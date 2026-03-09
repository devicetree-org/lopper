#/*
# * Copyright (c) 2024-2026 Advanced Micro Devices, Inc. All Rights Reserved.
# *
# * Author:
# *       Bruce Ashfield <bruce.ashfield@amd.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

"""
Generate YAML domain with all devices from System Device Tree.

This assist scans the SDT for devices across multiple categories (bus devices,
CPUs, memory, firmware, etc.) and generates a YAML file containing a domain
with all devices. This generated YAML can then be used as a parent domain
for glob-based device matching.

Usage:
    lopper system.dts output.yaml -- sdt_devices [options]

Options:
    -v, --verbose           Enable verbose output
    -b, --bus-types         Comma-separated bus compatible strings (default: simple-bus)
    -n, --domain-name       Name for generated domain (default: sdt_all_devices)
    -o                      Output file path (overrides positional output argument)
    -c, --categories        Comma-separated device categories to include
                            Categories: bus,cpu,memory,firmware,toplevel (default: all)
    --exclude-categories    Comma-separated categories to exclude
    --include-pattern       Regex pattern for node names to include
    --exclude-pattern       Regex pattern for node names to exclude
    --include-clocks        Include clock nodes in device list (default: excluded)
    --include-infrastructure  Comma-separated infrastructure category names to include
                            Use --list-infrastructure to see available categories
                            Use 'all' to include all infrastructure devices
    --list-infrastructure   List available infrastructure categories and exit

Example:
    # Generate SDT devices YAML (use '-' to skip main output, -o for assist output)
    lopper system-top.dts - -- sdt_devices -o /tmp/sdt-devices.yaml

    # Only CPUs and memory
    lopper system.dts - -- sdt_devices -c cpu,memory -o /tmp/cpu-mem.yaml

    # Everything except firmware
    lopper system.dts - -- sdt_devices --exclude-categories firmware -o output.yaml

    # Only serial devices
    lopper system.dts - -- sdt_devices --include-pattern "serial@.*" -o serial.yaml

    # All-in-one command (generate and use in single pipeline)
    lopper system-top.dts - -- sdt_devices -o /tmp/sdt-devices.yaml && \\
    lopper -f --permissive --enhanced \\
        -x '*.yaml' \\
        -i /tmp/sdt-devices.yaml \\
        -i my-domain.yaml \\
        system-top.dts output.dts
"""

import sys
import os
import getopt
import re
import logging
from enum import Enum
from pathlib import Path
from lopper.tree import LopperTree
from lopper.tree import LopperNode
from lopper.tree import LopperProp
from lopper.yaml import LopperYAML
from ruamel.yaml.scalarint import HexInt
import lopper
import lopper.log

lopper.log._init(__name__)


class DeviceCategory(Enum):
    """Categories of devices that can be discovered from the SDT."""
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


def is_compat(node, compat_string_to_test):
    """Identify whether this assist handles the provided compatibility string.

    Args:
        node (LopperNode): Device tree node being evaluated
        compat_string_to_test (str): Compatibility string to test

    Returns:
        Callable | str: Reference to entry point function on match, empty string otherwise
    """
    if re.search("sdt-devices,sdt-devices-v1", compat_string_to_test):
        return sdt_devices
    if re.search("module,sdt_devices", compat_string_to_test):
        return sdt_devices
    return ""


def usage():
    print("""
   Usage: sdt_devices [OPTION]

      -v, --verbose           Enable verbose output
      -b, --bus-types         Comma-separated bus compatible strings (default: simple-bus)
      -n, --domain-name       Name for generated domain (default: sdt_all_devices)
      -o                      Output file path
      -c, --categories        Comma-separated device categories to include
                              Categories: bus,cpu,memory,firmware,toplevel
                              (default: all categories)
      --exclude-categories    Comma-separated categories to exclude
      --include-pattern       Regex pattern for node names to include
      --exclude-pattern       Regex pattern for node names to exclude
      --include-clocks        Include clock nodes in device list (default: excluded)
      --include-infrastructure  Comma-separated infrastructure category names to include
                              (devices normally excluded as non-assignable)
                              Use --list-infrastructure to see available categories
                              Use 'all' to include all infrastructure devices
      --list-infrastructure   List available infrastructure categories and exit

   Generate YAML domain containing devices from the System Device Tree.
   The generated YAML can be used as a parent domain for glob-based device matching.

   Device Categories:
      bus       - Devices under simple-bus or other bus nodes
      cpu       - CPU clusters and individual CPU nodes
      memory    - Memory nodes, reserved-memory, SRAM/TCM
      firmware  - Firmware nodes, IPI, power management
      toplevel  - Non-bus devices directly under root

   Example:
      lopper system.dts - -- sdt_devices -o output.yaml
      lopper system.dts - -- sdt_devices -o output.yaml -c bus,cpu
      lopper system.dts - -- sdt_devices -o output.yaml --exclude-categories firmware
      lopper system.dts - -- sdt_devices -o output.yaml --include-pattern "serial@.*"
      lopper system.dts - -- sdt_devices -o output.yaml --include-infrastructure protection
    """)


def list_infrastructure():
    """Print available infrastructure categories and their descriptions."""
    print("""
   Infrastructure Categories (excluded by default):

   These devices cannot be independently assigned to domains or protected
   by XPPU/XMPU. Use --include-infrastructure <category> to include them.

   Category        Description                              Example patterns
   --------        -----------                              ----------------
   interrupt       Interrupt controllers (shared)           arm,gic, interrupt-controller
   bus             Bus nodes (structural, not devices)      simple-bus
   ipi             IPI mailbox infrastructure               zynqmp-ipi-mailbox
   smmu            SMMU/IOMMU address translation           arm,smmu, iommu
   power           Power management and CPU states          arm,psci, arm,idle-state
   syscon          System controller registers              syscon
   phy             PHY providers (not standalone)           phy-provider
   reset           Reset controllers (shared)               reset-controller
   pinctrl         Pin control/muxing (shared)              pinctrl
   misc            Miscellaneous structural nodes           gpio-keys, chosen
   slcr            SLCR and clock/reset control             *slcr*, *_crf_*, *_crl_*
   interconnect    Interconnect fabric (shared)             *_gpv@*, *_cci_*, *_afi_*
   protection      Protection units (can't protect self)    *xmpu*, *xppu*
   cpu-ctrl        CPU cluster control registers            *_apu_*, *_rpu_*
   platform        Platform/IO configuration                *_siou@*, *iouslcr*

   Use 'all' to include all infrastructure devices:
      lopper system.dts - -- sdt_devices --include-infrastructure all -o output.yaml

   Include multiple categories:
      lopper system.dts - -- sdt_devices --include-infrastructure protection,slcr -o output.yaml
    """)


class SDTDevices:
    """Generates a YAML domain containing all devices from the SDT."""

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

    def __init__(self, sdt, include_clocks=False, include_infrastructure=None):
        """Initialize the SDT devices generator.

        Args:
            sdt (LopperSDT): The system device tree instance
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

    def _parse_reg_property(self, node):
        """Parse reg property to extract start address and size.

        The reg property format depends on parent's #address-cells and #size-cells.
        Common formats:
        - 4 cells: <addr_hi addr_lo size_hi size_lo>
        - 2 cells: <addr size>

        Args:
            node: LopperNode with reg property

        Returns:
            tuple: (start_hex, size_hex) or (None, None) if not parseable
        """
        reg = node.propval("reg")
        if not reg or len(reg) < 2:
            return None, None

        # Try to get #address-cells and #size-cells from parent
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

        try:
            # Parse based on cells
            if addr_cells == 2 and size_cells == 2 and len(reg) >= 4:
                # 64-bit address and size
                start = (reg[0] << 32) | reg[1] if isinstance(reg[0], int) else 0
                size = (reg[2] << 32) | reg[3] if isinstance(reg[2], int) else 0
            elif addr_cells == 1 and size_cells == 1 and len(reg) >= 2:
                # 32-bit address and size
                start = reg[0] if isinstance(reg[0], int) else 0
                size = reg[1] if isinstance(reg[1], int) else 0
            elif addr_cells == 2 and size_cells == 1 and len(reg) >= 3:
                # 64-bit address, 32-bit size
                start = (reg[0] << 32) | reg[1] if isinstance(reg[0], int) else 0
                size = reg[2] if isinstance(reg[2], int) else 0
            else:
                # Fallback: assume first value is address, second is size
                start = reg[0] if isinstance(reg[0], int) else 0
                size = reg[1] if isinstance(reg[1], int) else 0

            # Return HexInt for proper YAML hex integer output
            start_hex = HexInt(start) if start else None
            size_hex = HexInt(size) if size else None
            return start_hex, size_hex
        except:
            return None, None

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
        """Find all devices under bus nodes in SDT.

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
        """Find all CPU clusters and CPUs in SDT.

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
        """Find all memory nodes in SDT.

        Discovers:
        - Main memory nodes (memory@*)
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

                entry = {'dev': node.name}
                if node.label:
                    entry['label'] = node.label

                # Extract start address and size from reg property
                start, size = self._parse_reg_property(node)
                if start:
                    entry['start'] = start
                if size:
                    entry['size'] = size

                memory_devices['memory'].append(entry)
                lopper.log._debug(f"  Found memory: {node.name}")

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

                    memory_devices['sram'].append(entry)
                    lopper.log._debug(f"  Found SRAM: {node.name}")

        lopper.log._info(f"Discovered {len(memory_devices['memory'])} memory, "
                        f"{len(memory_devices['sram'])} sram nodes")
        return memory_devices

    def discover_firmware(self):
        """Find firmware and system nodes in SDT.

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

                toplevel_devices.append(entry)
                lopper.log._debug(f"  Found toplevel: {dev_name}")

        except:
            pass

        lopper.log._info(f"Discovered {len(toplevel_devices)} toplevel nodes")
        return toplevel_devices

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

    def generate_domain(self, domain_name='sdt_all_devices', categories=None,
                       bus_types=None, include_pattern=None, exclude_pattern=None):
        """Generate a domain node containing discovered devices.

        Creates a tree structure with separate properties for different
        device types (matching isospec format):
            /domains
                /<domain_name>
                    compatible = "lopper,sdt-devices-v1"
                    id = 0
                    cpus: [...]
                    memory: [...]
                    sram: [...]
                    access: [...]

        Args:
            domain_name (str): Name for the generated domain node
            categories (list): List of DeviceCategory to include
            bus_types (list): List of bus compatible strings to search
            include_pattern (str): Regex pattern for including devices
            exclude_pattern (str): Regex pattern for excluding devices

        Returns:
            LopperTree: Tree containing the generated domain
        """
        # Create fresh tree
        self.tree = LopperTree()
        self.tree.phandle_resolution = False

        # Create /domains container
        domains = LopperNode(abspath="/domains", name="domains")
        domains.phandle_resolution = False
        self.tree = self.tree + domains

        # Create the device domain
        domain = LopperNode(name=domain_name)
        domain.phandle_resolution = False
        domain["compatible"] = "lopper,sdt-devices-v1"
        domain["id"] = 0
        domains + domain

        # Discover all devices based on categories
        devices = self.discover_all( categories=categories,
                                     bus_types=bus_types,
                                     include_pattern=include_pattern,
                                     exclude_pattern=exclude_pattern )

        # Add cpus property if we have CPUs
        if devices['cpus']:
            cpus_prop = LopperProp("cpus", -1, domain, [])
            cpus_prop.phandle_resolution = False
            domain + cpus_prop
            for cpu in devices['cpus']:
                cpus_prop.value.append(cpu)
            lopper.log._info(f"Added {len(devices['cpus'])} CPU entries")

        # Add memory property if we have memory
        if devices['memory']:
            memory_prop = LopperProp("memory", -1, domain, [])
            memory_prop.phandle_resolution = False
            domain + memory_prop
            for mem in devices['memory']:
                memory_prop.value.append(mem)
            lopper.log._info(f"Added {len(devices['memory'])} memory entries")

        # Add sram property if we have SRAM
        if devices['sram']:
            sram_prop = LopperProp("sram", -1, domain, [])
            sram_prop.phandle_resolution = False
            domain + sram_prop
            for sram in devices['sram']:
                sram_prop.value.append(sram)
            lopper.log._info(f"Added {len(devices['sram'])} SRAM entries")

        # Add access property for bus/firmware/toplevel devices
        if devices['access']:
            access_prop = LopperProp("access", -1, domain, [])
            access_prop.phandle_resolution = False
            domain + access_prop
            for dev in devices['access']:
                access_prop.value.append(dev)
            lopper.log._info(f"Added {len(devices['access'])} access entries")

        total = (len(devices['cpus']) + len(devices['memory']) +
                len(devices['sram']) + len(devices['access']))
        lopper.log._info(f"Generated domain '{domain_name}' with {total} total entries")

        return self.tree


def sdt_devices(tgt_node, sdt, options):
    """Generate YAML domain with all SDT devices.

    This is the main entry point called by the lopper assist framework.

    Args:
        tgt_node (LopperNode): Target node (typically root /)
        sdt (LopperSDT): System device tree instance
        options (dict): Options dictionary with 'verbose' and 'args' keys

    Returns:
        bool: True on success, False on failure
    """
    try:
        verbose = options['verbose']
    except:
        verbose = 0

    try:
        args = options['args']
    except:
        args = []

    # Parse command-line options
    try:
        opts, args2 = getopt.getopt(
            args,
            "hvb:n:o:c:",
            ["help", "verbose", "bus-types=", "domain-name=",
             "categories=", "exclude-categories=",
             "include-pattern=", "exclude-pattern=",
             "include-clocks", "include-infrastructure=", "list-infrastructure"]
        )
    except getopt.GetoptError as e:
        lopper.log._error(f"Invalid option: {e}")
        usage()
        return False

    # Default values
    bus_types = SDTDevices.DEFAULT_BUS_TYPES
    domain_name = 'sdt_all_devices'
    output_file = None
    categories = None  # None means all categories
    exclude_categories = []
    include_pattern = None
    exclude_pattern = None
    include_clocks = False
    include_infrastructure = []

    for o, a in opts:
        if o in ('-h', '--help'):
            usage()
            return True
        elif o in ('--list-infrastructure',):
            list_infrastructure()
            return True
        elif o in ('-v', '--verbose'):
            verbose = verbose + 1
        elif o in ('-b', '--bus-types'):
            bus_types = [t.strip() for t in a.split(',')]
        elif o in ('-n', '--domain-name'):
            domain_name = a
        elif o in ('-o'):
            output_file = a
        elif o in ('-c', '--categories'):
            categories = DeviceCategory.parse_list(a)
        elif o in ('--exclude-categories',):
            exclude_categories = DeviceCategory.parse_list(a)
        elif o in ('--include-pattern',):
            include_pattern = a
        elif o in ('--exclude-pattern',):
            exclude_pattern = a
        elif o in ('--include-clocks',):
            include_clocks = True
        elif o in ('--include-infrastructure',):
            # Parse comma-separated infrastructure categories
            infra_cats = [c.strip().lower() for c in a.split(',')]
            for cat in infra_cats:
                if cat == 'all':
                    include_infrastructure = ['all']
                    break
                elif cat in SDTDevices.INFRASTRUCTURE_CATEGORY_NAMES:
                    include_infrastructure.append(cat)
                else:
                    lopper.log._warning(f"Unknown infrastructure category: {cat}")
                    lopper.log._warning(f"Valid categories: {', '.join(SDTDevices.INFRASTRUCTURE_CATEGORY_NAMES)}")

    # Handle category exclusions
    if categories is None:
        categories = DeviceCategory.all_categories()
    if exclude_categories:
        categories = [c for c in categories if c not in exclude_categories]

    # Set logging level based on verbosity
    if verbose > 3:
        desired_level = lopper.log.TRACE2
    elif verbose > 2:
        desired_level = lopper.log.TRACE
    elif verbose > 1:
        desired_level = logging.DEBUG
    elif verbose > 0:
        desired_level = logging.INFO
    else:
        desired_level = None

    if desired_level is not None:
        lopper.log._level(desired_level, __name__)

    cat_names = [c.value for c in categories]
    lopper.log._info(f"sdt_devices: generating device list for domain '{domain_name}'")
    lopper.log._info(f"sdt_devices: categories: {cat_names}")
    lopper.log._info(f"sdt_devices: bus types: {bus_types}")
    if include_pattern:
        lopper.log._info(f"sdt_devices: include pattern: {include_pattern}")
    if exclude_pattern:
        lopper.log._info(f"sdt_devices: exclude pattern: {exclude_pattern}")
    if include_clocks:
        lopper.log._info(f"sdt_devices: including clock nodes")
    if include_infrastructure:
        lopper.log._info(f"sdt_devices: including infrastructure: {include_infrastructure}")

    # Create the generator and build the domain tree
    generator = SDTDevices(sdt, include_clocks=include_clocks,
                           include_infrastructure=include_infrastructure)
    tree = generator.generate_domain( domain_name=domain_name,
                                      categories=categories,
                                      bus_types=bus_types,
                                      include_pattern=include_pattern,
                                      exclude_pattern=exclude_pattern )

    # Determine output file
    if not output_file:
        # Try to get output from sdt
        if hasattr(sdt, 'output_file') and sdt.output_file:
            output_file = sdt.output_file
        else:
            lopper.log._error("No output file specified")
            usage()
            return False

    # Ensure output file has .yaml extension for proper formatting
    if not output_file.endswith('.yaml'):
        base, _ = os.path.splitext(output_file)
        output_file = base + '.yaml'
        lopper.log._info(f"Output file changed to: {output_file}")

    # Ensure parent directory exists
    output_dir = os.path.dirname(output_file)
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir, exist_ok=True)

    # Write the output using LopperYAML directly
    # This is more robust than sdt.write() as it doesn't require sdt.config
    lopper.log._info(f"Writing SDT devices to: {output_file}")
    try:
        # Get config from sdt if available, otherwise use empty dict
        config = getattr(sdt, 'config', {})
        yaml_writer = LopperYAML(None, tree, config=config)
        yaml_writer.to_yaml(output_file)
    except Exception as e:
        lopper.log._error(f"Failed to write output: {e}")
        return False

    lopper.log._info(f"Successfully generated {output_file}")
    return True
