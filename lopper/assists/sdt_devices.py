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

    def __init__(self, sdt):
        """Initialize the SDT devices generator.

        Args:
            sdt (LopperSDT): The system device tree instance
        """
        self.sdt = sdt
        self.tree = LopperTree()
        self.tree.phandle_resolution = False

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

            for node in bus.subnodes(children_only=True):
                # Only include addressable devices (have @ in name)
                if '@' in node.name:
                    if node.abs_path in seen_devices:
                        continue
                    seen_devices.add(node.abs_path)

                    entry = {'dev': node.name}
                    if node.label:
                        entry['label'] = node.label

                    devices.append(entry)
                    lopper.log._debug(f"  Found bus device: {node.name}")

        lopper.log._info(f"Discovered {len(devices)} bus devices")
        return devices

    def discover_cpus(self):
        """Find all CPU clusters and CPUs in SDT.

        Discovers CPU nodes by looking for nodes with device_type="cpu"
        and their parent clusters.

        Returns:
            list: List of CPU entry dictionaries with cluster info
        """
        cpus = []
        seen_clusters = set()

        # Find all nodes with device_type = "cpu"
        for node in self.sdt.tree:
            device_type = node.propval("device_type")
            if device_type and "cpu" in device_type:
                # Get the cluster (parent node)
                cluster = node.parent
                if cluster and cluster.abs_path != "/":
                    cluster_name = cluster.label if cluster.label else cluster.name

                    # Add cluster entry if not seen
                    if cluster.abs_path not in seen_clusters:
                        seen_clusters.add(cluster.abs_path)

                        entry = {'dev': cluster_name}
                        if cluster.label:
                            entry['cluster'] = cluster.label

                        # Try to get compatible string for CPU type
                        compat = node.propval("compatible")
                        if compat and len(compat) > 0:
                            entry['compatible'] = compat[0]

                        cpus.append(entry)
                        lopper.log._debug(f"  Found CPU cluster: {cluster_name}")

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

                # Try to extract size/start from reg property
                reg = node.propval("reg")
                if reg and len(reg) >= 2:
                    # Simplified - actual parsing depends on #address-cells/#size-cells
                    entry['start'] = hex(reg[0]) if isinstance(reg[0], int) else str(reg[0])

                memory_devices['memory'].append(entry)
                lopper.log._debug(f"  Found memory: {node.name}")

        # Find reserved-memory children
        try:
            reserved_mem = self.sdt.tree["/reserved-memory"]
            for node in reserved_mem.subnodes(children_only=True):
                if node.abs_path in seen:
                    continue
                seen.add(node.abs_path)

                entry = {'dev': node.name}
                if node.label:
                    entry['label'] = node.label

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
                    seen.add(node.abs_path)

                    entry = {'dev': node.name}
                    if node.label:
                        entry['label'] = node.label

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

        # Find /firmware children
        try:
            firmware_node = self.sdt.tree["/firmware"]
            for node in firmware_node.subnodes():
                if node.abs_path in seen:
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

        # Find IPI nodes
        for node in self.sdt.tree:
            compat = node.propval("compatible")
            if compat:
                compat_str = ' '.join(str(c) for c in compat)
                if 'ipi' in compat_str.lower() or 'mailbox' in compat_str.lower():
                    if node.abs_path not in seen:
                        seen.add(node.abs_path)

                        dev_name = node.label if node.label else node.name
                        entry = {'dev': dev_name}
                        if node.label:
                            entry['label'] = node.label

                        firmware_devices.append(entry)
                        lopper.log._debug(f"  Found IPI/mailbox: {dev_name}")

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
            for node in root.subnodes(children_only=True):
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
        devices = self.discover_all(
            categories=categories,
            bus_types=bus_types,
            include_pattern=include_pattern,
            exclude_pattern=exclude_pattern
        )

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
             "include-pattern=", "exclude-pattern="]
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

    for o, a in opts:
        if o in ('-h', '--help'):
            usage()
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

    # Create the generator and build the domain tree
    generator = SDTDevices(sdt)
    tree = generator.generate_domain(
        domain_name=domain_name,
        categories=categories,
        bus_types=bus_types,
        include_pattern=include_pattern,
        exclude_pattern=exclude_pattern
    )

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
