#/*
# * Copyright (c) 2024-2026 Advanced Micro Devices, Inc. All Rights Reserved.
# *
# * Author:
# *       Bruce Ashfield <bruce.ashfield@amd.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

"""
Shared device-inventory extraction core.

This module owns the input-agnostic logic for scanning a Lopper tree and
emitting an `openamp,domain-v1,devices` YAML inventory. It is used by the
`sdt_devices` assist (which operates on a System Device Tree) and by
other extractors that work from a Linux device tree, a Zephyr device
tree, or a merge of multiple sources.

The class `DevicesCore` walks any LopperSDT-shaped object and produces
the same intermediate format regardless of the input shape. Source-
specific quirks (Linux-DT mining, Zephyr-DT mining, multi-source merge)
belong in dedicated extractors that subclass or wrap this core.

Underscore-prefixed module name signals "shared internal helper for
assists in this package"; not intended for direct use by tooling
outside lopper.
"""

import glob
import os
import re
from enum import Enum

from lopper.tree import LopperNode, LopperProp, LopperTree
from ruamel.yaml.scalarint import HexInt

import lopper
import lopper.log

# Per-SoC YAML data files live alongside the library. The loader matches
# the input tree's root `compatible` against each file's `soc.matches`
# list and returns the first match. New SoC support = drop a new file
# in here (see lopper/data/socs/README.md for the schema).
_SOC_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                             'data', 'socs')

lopper.log._init(__name__)


class DeviceCategory(Enum):
    """Categories of devices that can be discovered from the tree."""
    BUS = "bus"
    CPU = "cpu"
    MEMORY = "memory"
    FIRMWARE = "firmware"
    TOPLEVEL = "toplevel"

    @classmethod
    def from_string(cls, s):
        """Convert string to DeviceCategory."""
        try:
            return cls(s.lower().strip())
        except ValueError:
            return None

    @classmethod
    def all_categories(cls):
        """Return list of all category values."""
        return list(cls)

    @classmethod
    def parse_list(cls, s):
        """Parse comma-separated string into list of categories."""
        categories = []
        for item in s.split(','):
            cat = cls.from_string(item)
            if cat:
                categories.append(cat)
        return categories


class DevicesCore:
    """Walk a Lopper tree and produce an openamp,domain-v1,devices inventory.

    Input-agnostic: works equally on a System Device Tree, a Linux DT, a
    Zephyr DT, or a merged tree. Per-source quirks live in extractors
    that subclass this or assemble their input tree before handing it in.
    """

    # Default bus compatible strings to search for devices
    DEFAULT_BUS_TYPES = ['simple-bus']

    # Memory type mappings based on node name patterns
    MEMORY_TYPE_PATTERNS = {
        'sram': [r'tcm', r'ocm', r'sram', r'bram'],
        'memory': [r'memory@', r'ddr'],
    }

    # Infrastructure device categories - devices that typically can't be
    # independently assigned to domains or protected by XPPU/XMPU.
    # Organized into categories so specific categories can be included via
    # --include-infrastructure option.
    INFRASTRUCTURE_CATEGORIES = {
        # Core interrupt infrastructure - cannot be split between domains
        'interrupt': [
            r'interrupt-controller',
            r'arm,gic',
        ],
        # Bus nodes - structural, not actual devices
        'bus': [
            r'simple-bus',
        ],
        # IPI mailbox infrastructure - shared communication
        'ipi': [
            r'xlnx,zynqmp-ipi-mailbox',
            r'xlnx,versal-ipi-dest-mailbox',
        ],
        # SMMU/IOMMU - system-level address translation
        'smmu': [
            r'arm,smmu',
            r'iommu',
        ],
        # Power management and CPU states
        'power': [
            r'arm,idle-state',
            r'arm,psci',
            r'xlnx,zynqmp-power',
        ],
        # System controller registers
        'syscon': [
            r'syscon',
        ],
        # PHY providers - not standalone devices
        'phy': [
            r'phy-provider',
        ],
        # Reset controllers - shared infrastructure
        'reset': [
            r'reset-controller',
        ],
        # Pin control - shared multiplexing
        'pinctrl': [
            r'pinctrl',
        ],
        # Miscellaneous structural/virtual nodes
        'misc': [
            r'gpio-keys',
            r'chosen$',
            r'memory$',
        ],
        # SLCR and clock/reset control registers - not PMU controlled
        'slcr': [
            r'slcr',
            r'_crf_',
            r'_crf@',
            r'_crl_',
            r'_crl@',
        ],
        # Interconnect fabric - shared infrastructure
        'interconnect': [
            r'_gpv@',
            r'_gpv_',
            r'_cci_',
            r'_cci@',
            r'_afi_',
            r'_afi@',
        ],
        # Protection units - can't protect themselves
        'protection': [
            r'xmpu',
            r'xppu',
        ],
        # CPU cluster control registers (psu_apu@, ps_wizard_0_ps11_0_apu_0@, etc.)
        'cpu-ctrl': [
            r'_apu@',
            r'_apu_\d+@',
            r'_rpu@',
            r'_rpu_[a-z]@',
        ],
        # Platform/IO configuration
        'platform': [
            r'_siou@',
            r'iou.*scntr',
            r'iousecure',
            r'iouslcr',
        ],
    }

    # All infrastructure category names for validation
    INFRASTRUCTURE_CATEGORY_NAMES = list(INFRASTRUCTURE_CATEGORIES.keys())

    # Clock-related compatible patterns - separated so they can be optionally included
    # NOTE: Clock domain assignment is not currently implemented. When included,
    # clocks appear in the device list but no special handling is performed.
    # Future work could add exclusive clock assignment to prevent multiple domains
    # from controlling the same clock frequency.
    CLOCK_COMPAT_PATTERNS = [
        r'fixed-clock',           # Clock providers
        r'fixed-factor-clock',    # Clock dividers
        r'-clk$',                 # Clock nodes
        r'clock-controller',      # Clock controllers
    ]

    # Cache of (root_compatible_tuple → SoC-data dict) so we only load
    # and match each YAML once per process.
    _soc_data_cache = {}

    # The compatible-string suffix that identifies a SoC-facts block
    # inside a unified `openamp,domain-v1` YAML. See sdt-from-linux
    # design §6.1.
    _SOC_FACTS_COMPATIBLE = 'openamp,domain-v1,soc-facts'

    # Relative subpath, under each include/search directory, where the
    # loader looks for SoC-facts YAML. Mirrors the shipped layout
    # (`lopper/data/socs/`) so a user replicating the structure in
    # their own repo and passing it via `-I` is found without dropping
    # files into the lopper tree.
    _SOC_SUBPATH = os.path.join('data', 'socs')

    @classmethod
    def _soc_search_dirs(cls, extra_dirs=()):
        """Ordered list of directories to scan for SoC-facts YAML.

        Each `-I` / `--input-dirs` (and `LOPPER_INPUT_DIRS`) directory
        contributes `<dir>/data/socs` — the same relative layout as the
        shipped tree — and is searched *before* the built-in
        `lopper/data/socs/`, so a user file can override or extend the
        shipped set without editing the repo. Only existing directories
        are returned, de-duplicated, order preserved.
        """
        candidates = [os.path.join(d, cls._SOC_SUBPATH) for d in extra_dirs]
        candidates.append(_SOC_DATA_DIR)
        seen = set()
        dirs = []
        for d in candidates:
            if not d or not os.path.isdir(d):
                continue
            real = os.path.realpath(d)
            if real in seen:
                continue
            seen.add(real)
            dirs.append(d)
        return dirs

    @classmethod
    def _load_soc_data(cls, compatibles, extra_dirs=()):
        """Find a SoC-facts domain block whose `matches:` list contains
        any of `compatibles` (the input tree's root compatible list).

        Each YAML file under a SoC search directory follows the unified
        schema (§6.1) — a `domains:` map at the root, with named child
        blocks discriminated by `compatible:`. We pick the first block
        whose compatible is `openamp,domain-v1,soc-facts` AND whose
        `matches:` overlaps with the caller's compatibles, scanning the
        user's `-I` directories (at `<dir>/data/socs/`) before the
        built-in `lopper/data/socs/`.

        Args:
            compatibles (list[str]): Root-level compatible strings, in
                                     priority order (most-specific first).
            extra_dirs (iterable[str]): Include/search directories (from
                                     `-I` etc.) to look under, before the
                                     built-in location.

        Returns:
            dict: The matched soc-facts block (with its pm_devices etc.),
                  or {} if no file matches.
        """
        soc_dirs = cls._soc_search_dirs(tuple(extra_dirs))
        key = (tuple(compatibles), tuple(soc_dirs))
        if key in cls._soc_data_cache:
            return cls._soc_data_cache[key]

        result = {}
        if not soc_dirs:
            cls._soc_data_cache[key] = result
            return result

        try:
            from ruamel.yaml import YAML
            yaml_loader = YAML(typ='safe')
        except Exception:
            lopper.log._warning("ruamel.yaml unavailable; SoC data files not loaded")
            cls._soc_data_cache[key] = result
            return result

        for soc_dir in soc_dirs:
            for path in sorted(glob.glob(os.path.join(soc_dir, '*.yaml'))):
                try:
                    with open(path) as fh:
                        data = yaml_loader.load(fh) or {}
                except Exception as e:
                    lopper.log._warning(f"Failed to parse SoC data {path}: {e}")
                    continue

                domains = data.get('domains') or {}
                for block_name, block in domains.items():
                    if not isinstance(block, dict):
                        continue
                    if block.get('compatible') != cls._SOC_FACTS_COMPATIBLE:
                        continue
                    matches = block.get('matches') or []
                    if any(c in matches for c in compatibles):
                        hits = [c for c in compatibles if c in matches]
                        lopper.log._info(
                            f"SoC data: matched {path} "
                            f"domain '{block_name}' on {hits}")
                        result = block
                        break
                if result:
                    break
            if result:
                break

        cls._soc_data_cache[key] = result
        return result

    def __init__(self, sdt, include_clocks=False, include_infrastructure=None):
        """Initialize the device-inventory extractor.

        Args:
            sdt (LopperSDT): A Lopper system device tree-shaped object.
                             Works with any input (SDT, Linux DT, Zephyr DT,
                             merged tree) so long as `sdt.tree` is a
                             walkable LopperTree.
            include_clocks (bool): If True, include clock nodes in device list.
                                   Default is False (clocks excluded).
            include_infrastructure (list): List of infrastructure category names
                                          to include (not exclude). Use ['all'] to
                                          include all infrastructure devices.
                                          Default is None (all infra excluded).
        """
        self.sdt = sdt
        self.include_clocks = include_clocks
        self.include_infrastructure = include_infrastructure or []
        self.tree = LopperTree()
        self.tree.phandle_resolution = False

        # Build the active exclusion patterns based on which categories are NOT included
        self._build_active_patterns()

        # Resolve the SoC data file (if any) for this input tree's root
        # compatible list. Cached so repeated invocations are cheap.
        identity = self._detect_soc_identity()
        compatibles = []
        if 'board' in identity:
            compatibles.append(identity['board'])
        if 'soc_family' in identity and identity['soc_family'] not in compatibles:
            compatibles.append(identity['soc_family'])
        # Honor lopper's include/search dirs (-I / --input-dirs /
        # LOPPER_INPUT_DIRS) so a user can keep SoC-facts files in their
        # own repo under <dir>/data/socs/ rather than editing the tree.
        soc_search_dirs = getattr(self.sdt, 'load_paths', None) or []
        self._soc_data = (self._load_soc_data(compatibles, soc_search_dirs)
                          if compatibles else {})
        self._pm_devices = (self._soc_data.get('pm_devices') or {})

    def _build_active_patterns(self):
        """Build the active infrastructure exclusion patterns.

        Combines patterns from all infrastructure categories that are NOT
        in the include_infrastructure list. If 'all' is in include_infrastructure,
        no patterns are excluded.
        """
        self.active_infra_patterns = []

        # If 'all' specified, don't exclude any infrastructure
        if 'all' in self.include_infrastructure:
            lopper.log._info("Including all infrastructure devices")
            return

        # Build exclusion patterns from categories NOT in include list
        for category, patterns in self.INFRASTRUCTURE_CATEGORIES.items():
            if category not in self.include_infrastructure:
                self.active_infra_patterns.extend(patterns)
            else:
                lopper.log._info(f"Including infrastructure category: {category}")

    def _classify_memory_type(self, node_name):
        """Classify memory node into memory or sram category.

        Args:
            node_name (str): Name of the memory node

        Returns:
            str: 'sram' or 'memory'
        """
        name_lower = node_name.lower()
        for mem_type, patterns in self.MEMORY_TYPE_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, name_lower):
                    return mem_type
        return 'memory'

    def _parse_reg_ranges(self, node):
        """Parse reg property into ALL (start, size) ranges it declares.

        A single node's reg property may declare multiple ranges by
        concatenating tuples, e.g.:
            reg = <0x00 0x00 0x00 0x80000000>,    // 2 GiB at 0x0
                  <0x08 0x00 0x01 0x80000000>;    // 6 GiB at 0x8_0000_0000
        Linux DTs commonly use this for memory@0 to declare DDR-low and
        DDR-high in one node; the older single-range parser silently
        dropped the second one.

        Args:
            node: LopperNode with reg property

        Returns:
            list of (start_hex, size_hex) tuples. Empty list if the
            property is missing or unparseable. Tuple values are HexInt
            for YAML hex formatting, or None if not derivable.
        """
        reg = node.propval("reg")
        if not reg or len(reg) < 2:
            return []

        # Address/size cell counts come from the parent
        parent = node.parent
        addr_cells = 2  # default
        size_cells = 2  # default
        if parent:
            ac = parent.propval("#address-cells")
            if ac and len(ac) > 0 and isinstance(ac[0], int):
                addr_cells = ac[0]
            sc = parent.propval("#size-cells")
            if sc and len(sc) > 0 and isinstance(sc[0], int):
                size_cells = sc[0]

        tuple_size = addr_cells + size_cells
        if tuple_size <= 0:
            return []

        def _read_cells(start, count):
            """Combine `count` consecutive 32-bit cells into one integer."""
            val = 0
            for i in range(count):
                cell = reg[start + i]
                if not isinstance(cell, int):
                    return None
                val = (val << 32) | (cell & 0xFFFFFFFF)
            return val

        ranges = []
        try:
            offset = 0
            while offset + tuple_size <= len(reg):
                addr = _read_cells(offset, addr_cells) if addr_cells else 0
                size = _read_cells(offset + addr_cells, size_cells) if size_cells else 0
                start_hex = HexInt(addr) if addr else None
                size_hex = HexInt(size) if size else None
                ranges.append((start_hex, size_hex))
                offset += tuple_size
        except Exception:
            return ranges  # return whatever we got before the error

        return ranges

    def _parse_reg_property(self, node):
        """Parse reg property and return just the first (start, size) range.

        Kept for callers that only care about the primary range
        (firmware/IPI/etc. typically have a single reg entry). For memory
        nodes use _parse_reg_ranges() instead to capture multi-range
        declarations.

        Returns:
            tuple: (start_hex, size_hex) or (None, None) if not parseable.
        """
        ranges = self._parse_reg_ranges(node)
        if not ranges:
            return None, None
        return ranges[0]

    def _is_actual_device(self, node):
        """Check if node represents an actual device (vs structural/infrastructure node).

        Actual devices that can be assigned to domains have:
        1. A 'compatible' property identifying the device type
        2. A compatible string that's NOT infrastructure (clocks, interrupt controllers, etc.)
        3. A node name that's NOT infrastructure (slcr, xmpu, etc.)

        Structural nodes (port@*, endpoint@*) don't have compatible properties.
        Infrastructure nodes (clocks, GIC, SMMU) have compatible but can't be split.

        Args:
            node: LopperNode to check

        Returns:
            bool: True if node appears to be an assignable device
        """
        compat = node.propval("compatible")
        # Check for valid compatible - propval may return [''] for missing properties
        if not compat or len(compat) == 0:
            return False

        # Filter out empty strings
        valid_compat = [c for c in compat if c and str(c).strip()]
        if not valid_compat:
            return False

        # Check if node name matches infrastructure patterns
        # (for patterns like _apu@, _gpv@, xmpu, etc.)
        node_name = node.name
        for pattern in self.active_infra_patterns:
            if re.search(pattern, node_name, re.IGNORECASE):
                lopper.log._debug(f"  Skipping infrastructure device (name): {node_name}")
                return False

        # Check if any compatible string matches infrastructure patterns
        for compat_str in valid_compat:
            compat_str = str(compat_str)
            for pattern in self.active_infra_patterns:
                if re.search(pattern, compat_str, re.IGNORECASE):
                    lopper.log._debug(f"  Skipping infrastructure device: {node.name} ({compat_str})")
                    return False

            # Check clock patterns (excluded by default, can be included with --include-clocks)
            if not self.include_clocks:
                for pattern in self.CLOCK_COMPAT_PATTERNS:
                    if re.search(pattern, compat_str, re.IGNORECASE):
                        lopper.log._debug(f"  Skipping clock device: {node.name} ({compat_str})")
                        return False

        return True

    def _apply_pattern_filter(self, devices, include_pattern=None, exclude_pattern=None):
        """Apply include/exclude pattern filters to device list.

        Args:
            devices (list): List of device dictionaries
            include_pattern (str): Regex pattern - only include matching devices
            exclude_pattern (str): Regex pattern - exclude matching devices

        Returns:
            list: Filtered device list
        """
        if not include_pattern and not exclude_pattern:
            return devices

        filtered = []
        for dev in devices:
            dev_name = dev.get('dev', '')

            # Check include pattern
            if include_pattern:
                if not re.search(include_pattern, dev_name):
                    continue

            # Check exclude pattern
            if exclude_pattern:
                if re.search(exclude_pattern, dev_name):
                    continue

            filtered.append(dev)

        return filtered

    def discover_bus_devices(self, bus_types=None):
        """Find all devices under bus nodes in the tree.

        Searches for nodes with compatible strings matching the specified
        bus types, then collects all addressable device children.

        Args:
            bus_types (list): List of compatible strings to search for bus nodes.
                            Defaults to ['simple-bus'].

        Returns:
            list: List of device dictionaries with 'dev' and optionally 'label' keys
        """
        if bus_types is None:
            bus_types = self.DEFAULT_BUS_TYPES

        devices = []
        seen_devices = set()

        # Find all bus nodes matching the compatible strings
        bus_nodes = []
        for bus_type in bus_types:
            found = self.sdt.tree.cnodes(bus_type)
            lopper.log._debug(f"Found {len(found)} nodes with compatible '{bus_type}'")
            bus_nodes.extend(found)

        lopper.log._info(f"Found {len(bus_nodes)} bus nodes to scan for devices")

        # Collect devices from each bus
        for bus in bus_nodes:
            lopper.log._debug(f"Scanning bus node: {bus.abs_path}")

            # Use child_nodes.values() to get only direct children
            # (subnodes with children_only=True still returns all descendants)
            for node in bus.child_nodes.values():
                # Only include addressable devices (have @ in name)
                if '@' in node.name:
                    # Only include actual devices (must have compatible property)
                    # This filters out structural nodes like port@*, endpoint@*, etc.
                    if not self._is_actual_device(node):
                        lopper.log._debug(f"  Skipping node without compatible: {node.name}")
                        continue

                    if node.abs_path in seen_devices:
                        continue
                    seen_devices.add(node.abs_path)

                    entry = {'dev': node.name}
                    if node.label:
                        entry['label'] = node.label

                    # Add status if not "okay" (disabled devices)
                    status = node.propval("status")
                    if status and len(status) > 0 and status[0]:
                        status_str = str(status[0]).strip()
                        if status_str and status_str != "okay":
                            entry['status'] = status_str

                    self._augment_device_entry(entry, node)
                    devices.append(entry)
                    lopper.log._debug(f"  Found bus device: {node.name}")

        lopper.log._info(f"Discovered {len(devices)} bus devices")
        return devices

    def _parse_cpu_map(self, cluster_node):
        """Parse cpu-map node to get linear CPU enumeration.

        The cpu-map node describes the physical topology with clusters and cores.
        We use it to enumerate CPUs linearly (0, 1, 2, ...) rather than using
        MPIDR-based reg values which create sparse bitmasks.

        Args:
            cluster_node: The parent CPU cluster node (e.g., cpus-a78@0)

        Returns:
            dict: Mapping of CPU node path to linear index, or None if no cpu-map
        """
        cpu_map = None
        for child in cluster_node.child_nodes.values():
            if child.name == 'cpu-map':
                cpu_map = child
                break

        if not cpu_map:
            return None

        # Parse cpu-map: cluster0/core0/cpu, cluster0/core1/cpu, etc.
        cpu_index_map = {}
        linear_idx = 0

        # Get clusters in sorted order for consistent enumeration
        clusters = sorted([n for n in cpu_map.child_nodes.values()
                          if n.name.startswith('cluster')],
                         key=lambda n: n.name)

        for cluster in clusters:
            # Get cores in sorted order
            cores = sorted([n for n in cluster.child_nodes.values()
                           if n.name.startswith('core')],
                          key=lambda n: n.name)

            for core in cores:
                # The 'cpu' property is a phandle to the actual CPU node
                cpu_phandle = core.propval('cpu')
                if cpu_phandle and len(cpu_phandle) > 0:
                    # Resolve phandle to node path
                    phandle_val = cpu_phandle[0]
                    if isinstance(phandle_val, int):
                        cpu_node = self.sdt.tree.pnode(phandle_val)
                        if cpu_node:
                            cpu_index_map[cpu_node.abs_path] = linear_idx
                            linear_idx += 1

        return cpu_index_map if cpu_index_map else None

    def discover_cpus(self):
        """Find all CPU clusters and CPUs in the tree.

        Discovers CPU nodes by looking for nodes with device_type="cpu"
        and their parent clusters. Uses cpu-map for proper CPU enumeration
        when available, falling back to sequential enumeration.

        Returns:
            list: List of CPU entry dictionaries with cluster info including:
                - dev: cluster name/label
                - compatible: CPU compatible string
                - cpumask: hex bitmap of available CPUs
        """
        cpus = []
        cluster_info = {}  # cluster_path -> {cluster, compat, cpu_nodes}

        # First pass: collect all CPU nodes grouped by cluster
        for node in self.sdt.tree:
            device_type = node.propval("device_type")
            if device_type and "cpu" in device_type:
                cluster = node.parent
                if cluster and cluster.abs_path != "/":
                    if cluster.abs_path not in cluster_info:
                        cluster_info[cluster.abs_path] = {
                            'cluster': cluster,
                            'compat': None,
                            'cpu_nodes': [],
                            'cpu_map': None
                        }
                    cluster_info[cluster.abs_path]['cpu_nodes'].append(node)
                    # Get compatible from first CPU
                    if not cluster_info[cluster.abs_path]['compat']:
                        compat = node.propval("compatible")
                        if compat and len(compat) > 0:
                            cluster_info[cluster.abs_path]['compat'] = compat[0]

        # Parse cpu-map for each cluster if available
        for cluster_path, info in cluster_info.items():
            info['cpu_map'] = self._parse_cpu_map(info['cluster'])

        # Second pass: build one entry per cluster with combined cpumask
        for cluster_path, info in cluster_info.items():
            cluster = info['cluster']
            cluster_name = cluster.label if cluster.label else cluster.name
            cpu_map = info['cpu_map']

            # Build cpumask - use cpu-map indices if available, else enumerate
            cpumask = 0
            for idx, cpu_node in enumerate(info['cpu_nodes']):
                if cpu_map and cpu_node.abs_path in cpu_map:
                    # Use linear index from cpu-map
                    cpu_idx = cpu_map[cpu_node.abs_path]
                else:
                    # Fallback: sequential enumeration within cluster
                    cpu_idx = idx

                cpumask |= (1 << cpu_idx)

            entry = {'dev': cluster_name}
            entry['cpumask'] = HexInt(cpumask) if cpumask else HexInt(0)

            if info['compat']:
                entry['compatible'] = info['compat']

            cpus.append(entry)
            map_source = "cpu-map" if cpu_map else "sequential"
            lopper.log._debug(f"  Found CPU cluster: {cluster_name} (cpumask={hex(cpumask)}, {len(info['cpu_nodes'])} CPUs, {map_source})")

        # TODO: Future enhancements for smarter CPU grouping:
        # 1. Parse power-domains property to group R52 CPUs by RPU cluster
        #    (e.g., RPU_A: cortexr52_0+1, RPU_B: cortexr52_2+3, etc.)
        # 2. Use xlnx,lockstep-select to determine lockstep vs split mode
        # 3. Consider cpu-map cluster hierarchy for A78 physical topology
        # 4. Add execution-level information from cpu node properties
        # Currently we keep it simple: one entry per DTS cluster node with
        # combined cpumask for all CPUs in that node.

        lopper.log._info(f"Discovered {len(cpus)} CPU clusters")
        return cpus

    def discover_memory(self):
        """Find all memory nodes in the tree.

        Discovers:
        - Main memory nodes (memory@*) — splits multi-range reg tuples
          into one entry per range (e.g. Linux DT memory@0 declaring DDR
          low and DDR high in one node becomes two inventory entries)
        - Reserved memory regions
        - SRAM/TCM regions

        Returns:
            dict: Dictionary with 'memory' and 'sram' lists
        """
        memory_devices = {'memory': [], 'sram': []}
        seen = set()

        # Find memory@* nodes
        for node in self.sdt.tree:
            if node.name.startswith('memory@'):
                if node.abs_path in seen:
                    continue
                seen.add(node.abs_path)

                ranges = self._parse_reg_ranges(node)
                if not ranges:
                    # Node has no usable reg; emit the bare entry
                    entry = {'dev': node.name}
                    if node.label:
                        entry['label'] = node.label
                    self._augment_device_entry(entry, node)
                    memory_devices['memory'].append(entry)
                    lopper.log._debug(f"  Found memory: {node.name} (no reg)")
                    continue

                # Emit one inventory entry per range. The first range
                # keeps the original node name + label (so single-range
                # nodes look exactly the same as before); subsequent
                # ranges get a synthesised name derived from their start
                # address so they're unique.
                for idx, (start, size) in enumerate(ranges):
                    if idx == 0:
                        entry = {'dev': node.name}
                        if node.label:
                            entry['label'] = node.label
                    else:
                        synth_name = ("memory@%x" % int(start)) if start else f"{node.name}:{idx}"
                        entry = {'dev': synth_name}

                    if start:
                        entry['start'] = start
                    if size:
                        entry['size'] = size

                    self._augment_device_entry(entry, node)
                    memory_devices['memory'].append(entry)
                    lopper.log._debug(f"  Found memory: {entry['dev']} "
                                      f"(range {idx + 1}/{len(ranges)})")

        # Find reserved-memory children
        try:
            reserved_mem = self.sdt.tree["/reserved-memory"]
            for node in reserved_mem.child_nodes.values():
                if node.abs_path in seen:
                    continue
                seen.add(node.abs_path)

                entry = {'dev': node.name}
                if node.label:
                    entry['label'] = node.label

                # Extract start address and size from reg property
                start, size = self._parse_reg_property(node)
                if start:
                    entry['start'] = start
                if size:
                    entry['size'] = size

                # Add reserved-memory flags if present
                if node.propval("no-map") is not None:
                    # no-map is a boolean property (presence = true)
                    no_map = node.propval("no-map")
                    if no_map != ['']:
                        entry['no-map'] = True
                if node.propval("reusable") is not None:
                    reusable = node.propval("reusable")
                    if reusable != ['']:
                        entry['reusable'] = True

                self._augment_device_entry(entry, node)
                mem_type = self._classify_memory_type(node.name)
                memory_devices[mem_type].append(entry)
                lopper.log._debug(f"  Found reserved memory: {node.name} ({mem_type})")
        except:
            pass

        # Find SRAM/TCM nodes (often under bus nodes but may be elsewhere)
        for node in self.sdt.tree:
            name_lower = node.name.lower()
            if any(p in name_lower for p in ['tcm', 'ocm', 'sram', 'bram']):
                if '@' in node.name and node.abs_path not in seen:
                    # Check if this is actually a memory node, not infrastructure
                    # (e.g., ocm_xmpu is protection unit, not memory)
                    is_infrastructure = False
                    for pattern in self.active_infra_patterns:
                        if re.search(pattern, node.name, re.IGNORECASE):
                            lopper.log._debug(f"  Skipping infrastructure SRAM: {node.name}")
                            is_infrastructure = True
                            break
                    if is_infrastructure:
                        continue

                    seen.add(node.abs_path)

                    entry = {'dev': node.name}
                    if node.label:
                        entry['label'] = node.label

                    # Extract start address and size from reg property
                    start, size = self._parse_reg_property(node)
                    if start:
                        entry['start'] = start
                    if size:
                        entry['size'] = size

                    self._augment_device_entry(entry, node)
                    memory_devices['sram'].append(entry)
                    lopper.log._debug(f"  Found SRAM: {node.name}")

        lopper.log._info(f"Discovered {len(memory_devices['memory'])} memory, "
                        f"{len(memory_devices['sram'])} sram nodes")
        return memory_devices

    def discover_firmware(self):
        """Find firmware and system nodes in the tree.

        Discovers:
        - /firmware/* nodes
        - IPI mailbox nodes
        - Power management nodes

        Returns:
            list: List of firmware device dictionaries
        """
        firmware_devices = []
        seen = set()

        # Find /firmware children (direct children only, not all descendants)
        try:
            firmware_node = self.sdt.tree["/firmware"]
            for node in firmware_node.child_nodes.values():
                if node.abs_path in seen:
                    continue

                # Skip infrastructure nodes
                if not self._is_actual_device(node):
                    lopper.log._debug(f"  Skipping infrastructure firmware: {node.name}")
                    continue

                seen.add(node.abs_path)

                # Use label or name
                dev_name = node.label if node.label else node.name
                entry = {'dev': dev_name}
                if node.label and node.label != dev_name:
                    entry['label'] = node.label

                self._augment_device_entry(entry, node)
                firmware_devices.append(entry)
                lopper.log._debug(f"  Found firmware node: {dev_name}")
        except:
            pass

        # Find IPI controller nodes (but not mailbox children)
        # We only want addressable IPI controllers, not destination mailboxes
        for node in self.sdt.tree:
            # Skip non-addressable nodes
            if '@' not in node.name:
                continue

            # Skip if not an actual device (filters out dest-mailbox children)
            if not self._is_actual_device(node):
                continue

            compat = node.propval("compatible")
            if compat:
                compat_str = ' '.join(str(c) for c in compat)
                # Only include IPI controllers, not mailbox destinations
                if 'ipi' in compat_str.lower() and 'dest' not in compat_str.lower():
                    if node.abs_path not in seen:
                        seen.add(node.abs_path)

                        dev_name = node.label if node.label else node.name
                        entry = {'dev': dev_name}
                        if node.label:
                            entry['label'] = node.label

                        self._augment_device_entry(entry, node)
                        firmware_devices.append(entry)
                        lopper.log._debug(f"  Found IPI controller: {dev_name}")

        lopper.log._info(f"Discovered {len(firmware_devices)} firmware nodes")
        return firmware_devices

    def discover_toplevel(self):
        """Find non-bus devices directly under root.

        Discovers devices that are direct children of / but are not
        bus nodes, CPU clusters, or special nodes.

        Returns:
            list: List of toplevel device dictionaries
        """
        toplevel_devices = []
        seen = set()

        # Nodes to skip (buses, special nodes, etc.)
        skip_patterns = [
            r'^cpus',
            r'^memory@',
            r'^reserved-memory$',
            r'^firmware$',
            r'^chosen$',
            r'^aliases$',
            r'^__symbols__$',
            r'^__fixups__$',
            r'^__local_fixups__$',
        ]

        try:
            root = self.sdt.tree["/"]
            for node in root.child_nodes.values():
                # Skip special nodes
                skip = False
                for pattern in skip_patterns:
                    if re.search(pattern, node.name):
                        skip = True
                        break

                if skip:
                    continue

                # Skip bus nodes (they're handled by discover_bus_devices)
                compat = node.propval("compatible")
                if compat:
                    compat_str = ' '.join(str(c) for c in compat)
                    if 'simple-bus' in compat_str:
                        continue

                # Skip infrastructure nodes (clocks, etc.)
                if not self._is_actual_device(node):
                    lopper.log._debug(f"  Skipping infrastructure toplevel: {node.name}")
                    continue

                if node.abs_path in seen:
                    continue
                seen.add(node.abs_path)

                dev_name = node.label if node.label else node.name
                entry = {'dev': dev_name}
                if node.label:
                    entry['label'] = node.label

                self._augment_device_entry(entry, node)
                toplevel_devices.append(entry)
                lopper.log._debug(f"  Found toplevel: {dev_name}")

        except:
            pass

        lopper.log._info(f"Discovered {len(toplevel_devices)} toplevel nodes")
        return toplevel_devices

    # bootph-* properties (U-Boot boot-phase tags) the augment hook
    # surfaces on a device entry. Standard names from the dt-bindings.
    _BOOTPH_PROPS = ('bootph-all', 'bootph-pre-ram', 'bootph-pre-sram',
                     'bootph-some-ram', 'bootph-verify')

    def _augment_device_entry(self, entry, node):
        """Add extracted-but-not-required fields to a device inventory entry.

        Called from every site that builds a per-device entry. Keep
        additions cheap and side-effect-free; absence of a property
        means the field is simply omitted. This is also the hook
        future enhancements (PM-ID decode, etc.) will use.

        Args:
            entry (dict): The inventory entry being built. Mutated in place.
            node (LopperNode): Source tree node the entry was built from.
        """
        # bootph-* presence flags — preserve so downstream consumers can
        # honour the "this device must exist in early boot" intent. Emit
        # as `bootph: <suffix>` (string when single, list when many).
        # propval() returns [""] for both absent and boolean-present
        # properties, so check __props__ membership directly.
        phases = []
        props = getattr(node, '__props__', {}) or {}
        for prop in self._BOOTPH_PROPS:
            if prop in props:
                phases.append(prop[len('bootph-'):])

        if phases:
            entry['bootph'] = phases[0] if len(phases) == 1 else phases

        # PM device ID decode — when a node carries
        # `power-domains = <&provider ID …>`, look the IDs up against the
        # SoC data file's pm_devices table and emit canonical names. The
        # property may contain multiple <phandle id> tuples; we don't
        # care which provider is referenced (the provider's
        # #power-domain-cells differs by SoC, but on the Versal/ZynqMP
        # PM the ID slot is always the cell immediately after the
        # phandle, so we treat every second-cell-onward integer as a
        # candidate ID).
        if self._pm_devices:
            pd = node.propval("power-domains")
            if pd and pd != [""] and isinstance(pd, list):
                names = []
                # power-domains entries are flat lists of ints; phandles
                # alternate with ID(s). Be conservative: try each int as
                # a candidate ID and emit matches.
                for cell in pd:
                    if not isinstance(cell, int):
                        continue
                    name = self._pm_devices.get(cell) or self._pm_devices.get(hex(cell))
                    if name and name not in names:
                        names.append(name)
                if names:
                    entry['pm_node'] = names[0] if len(names) == 1 else names

    def discover_aliases(self):
        """Pass through the /aliases block from the source tree.

        /aliases declares the canonical short name for user-facing
        devices (serial0, ethernet0, mmc0, …) — the same identifiers a
        user expects to keep meaning the same thing across the Linux
        view and the assembled SDT. Preserving them lets downstream
        consumers carry the user's intent through the pipeline.

        Returns:
            dict: {alias_name: target_path} pairs, or {} if /aliases is
                  absent.
        """
        aliases = {}
        try:
            aliases_node = self.sdt.tree["/aliases"]
        except Exception:
            return aliases

        for prop in aliases_node.__props__.values():
            try:
                val = prop.value
                if isinstance(val, list) and val:
                    val = val[0]
                if val is None:
                    continue
                aliases[prop.name] = str(val).strip()
            except Exception:
                continue
        return aliases

    def _detect_soc_identity(self):
        """Read SoC family and board identity from the tree's root node.

        Root-level `compatible` is conventionally ordered most-specific
        (board) → least-specific (SoC family), e.g.:
            compatible = "xlnx,versal-vck190-revA", "xlnx,versal";
            compatible = "fsl,imx8mm-evk",          "fsl,imx8mm";

        Returns:
            dict: {'soc_family': str, 'board': str, 'model': str},
                  with absent fields omitted. Empty dict if root has no
                  compatible/model.
        """
        identity = {}
        try:
            root = self.sdt.tree["/"]
        except Exception:
            return identity

        compat = root.propval("compatible") or []
        compat = [str(c).strip() for c in compat if c and str(c).strip()]
        if compat:
            identity['board'] = compat[0]
            identity['soc_family'] = compat[-1]

        model = root.propval("model") or []
        if model and str(model[0]).strip():
            identity['model'] = str(model[0]).strip()

        return identity

    def discover_all(self, categories=None, bus_types=None,
                     include_pattern=None, exclude_pattern=None):
        """Orchestrate discovery based on selected categories.

        Args:
            categories (list): List of DeviceCategory to include (default: all)
            bus_types (list): Bus compatible strings for bus device discovery
            include_pattern (str): Regex pattern - only include matching devices
            exclude_pattern (str): Regex pattern - exclude matching devices

        Returns:
            dict: Dictionary with 'access', 'cpus', 'memory', 'sram' lists
        """
        if categories is None:
            categories = DeviceCategory.all_categories()

        if bus_types is None:
            bus_types = self.DEFAULT_BUS_TYPES

        devices = {
            'access': [],
            'cpus': [],
            'memory': [],
            'sram': []
        }

        # Discover by category
        if DeviceCategory.BUS in categories:
            bus_devices = self.discover_bus_devices(bus_types)
            bus_devices = self._apply_pattern_filter(bus_devices, include_pattern, exclude_pattern)
            devices['access'].extend(bus_devices)

        if DeviceCategory.CPU in categories:
            cpu_devices = self.discover_cpus()
            cpu_devices = self._apply_pattern_filter(cpu_devices, include_pattern, exclude_pattern)
            devices['cpus'].extend(cpu_devices)

        if DeviceCategory.MEMORY in categories:
            mem_devices = self.discover_memory()
            mem_devices['memory'] = self._apply_pattern_filter(
                mem_devices['memory'], include_pattern, exclude_pattern)
            mem_devices['sram'] = self._apply_pattern_filter(
                mem_devices['sram'], include_pattern, exclude_pattern)
            devices['memory'].extend(mem_devices['memory'])
            devices['sram'].extend(mem_devices['sram'])

        if DeviceCategory.FIRMWARE in categories:
            fw_devices = self.discover_firmware()
            fw_devices = self._apply_pattern_filter(fw_devices, include_pattern, exclude_pattern)
            devices['access'].extend(fw_devices)

        if DeviceCategory.TOPLEVEL in categories:
            top_devices = self.discover_toplevel()
            top_devices = self._apply_pattern_filter(top_devices, include_pattern, exclude_pattern)
            devices['access'].extend(top_devices)

        return devices

    def build_domain_tree(self, domain_name, devices,
                          identity=None, aliases=None):
        """Wrap a pre-built devices dict into an openamp,domain-v1,devices tree.

        Split out from generate_domain so callers that need to merge
        multiple sources (e.g. Linux + Zephyr in compose_devices) can
        discover each separately, combine the dicts, and call this once
        for the final emit.

        Args:
            domain_name (str): Name for the generated domain node
            devices (dict): Dict with 'cpus', 'memory', 'sram', 'access'
                            lists in inventory-entry form
            identity (dict, optional): {'soc_family', 'board', 'model'};
                                       any present keys become domain
                                       properties. If None, the calling
                                       instance's _detect_soc_identity()
                                       is used.
            aliases (dict, optional): {alias_name: target_path}; if None,
                                      the calling instance's
                                      discover_aliases() is used.

        Returns:
            LopperTree: Tree containing /domains/<domain_name> populated.
        """
        # Create fresh tree
        self.tree = LopperTree()
        self.tree.phandle_resolution = False

        # Create /domains container
        domains_node = LopperNode(abspath="/domains", name="domains")
        domains_node.phandle_resolution = False
        self.tree = self.tree + domains_node

        # Create the device domain
        domain = LopperNode(name=domain_name)
        domain.phandle_resolution = False
        domain["compatible"] = "openamp,domain-v1,devices"
        domain["id"] = 0
        domains_node + domain

        # Tag the domain with SoC family / board identity. Caller can
        # override (e.g. compose_devices uses the Linux side's identity
        # even when merging Zephyr into it).
        if identity is None:
            identity = self._detect_soc_identity()
        for key in ('soc_family', 'board', 'model'):
            if key in identity:
                domain[key] = identity[key]

        # Carry aliases through. Caller-provided wins (compose_devices
        # uses Linux's aliases authoritatively).
        if aliases is None:
            aliases = self.discover_aliases()
        if aliases:
            aliases_prop = LopperProp("aliases", -1, domain, dict(aliases))
            aliases_prop.phandle_resolution = False
            domain + aliases_prop
            lopper.log._info(f"Carried {len(aliases)} aliases through")

        # Emit each property block if non-empty.
        for prop_name in ('cpus', 'memory', 'sram', 'access'):
            entries = devices.get(prop_name) or []
            if not entries:
                continue
            prop = LopperProp(prop_name, -1, domain, [])
            prop.phandle_resolution = False
            domain + prop
            for entry in entries:
                prop.value.append(entry)
            lopper.log._info(f"Added {len(entries)} {prop_name} entries")

        total = sum(len(devices.get(k) or []) for k in
                    ('cpus', 'memory', 'sram', 'access'))
        lopper.log._info(f"Generated domain '{domain_name}' with {total} total entries")
        return self.tree

    def generate_domain(self, domain_name='sdt_all_devices', categories=None,
                       bus_types=None, include_pattern=None, exclude_pattern=None):
        """Discover devices on this instance's input and build the domain tree.

        Backward-compatible thin wrapper around discover_all +
        build_domain_tree for callers (sdt_devices) that operate on a
        single input source.
        """
        devices = self.discover_all(categories=categories,
                                    bus_types=bus_types,
                                    include_pattern=include_pattern,
                                    exclude_pattern=exclude_pattern)
        return self.build_domain_tree(domain_name, devices)
