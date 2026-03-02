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

This assist scans the SDT for devices under bus nodes (e.g., simple-bus)
and generates a YAML file containing a domain with all devices in its
access list. This generated YAML can then be used as a parent domain
for glob-based device matching.

Usage:
    lopper system.dts output.yaml -- sdt_devices [options]

Options:
    -v, --verbose       Enable verbose output
    -b, --bus-types     Comma-separated bus compatible strings (default: simple-bus)
    -n, --domain-name   Name for generated domain (default: sdt_all_devices)
    -o                  Output file path (overrides positional output argument)

Example:
    # Generate SDT devices YAML (use '-' to skip main output, -o for assist output)
    lopper system-top.dts - -- sdt_devices -o /tmp/sdt-devices.yaml

    # Use as parent for glob matching
    lopper -f --permissive --enhanced \\
        -x '*.yaml' \\
        -i /tmp/sdt-devices.yaml \\
        -i my-domain.yaml \\
        system-top.dts output.dts

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
from pathlib import Path
from lopper.tree import LopperTree
from lopper.tree import LopperNode
from lopper.tree import LopperProp
from lopper.yaml import LopperYAML
import lopper
import lopper.log

lopper.log._init(__name__)


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

      -v, --verbose     Enable verbose output
      -b, --bus-types   Comma-separated bus compatible strings (default: simple-bus)
      -n, --domain-name Name for generated domain (default: sdt_all_devices)
      -o                Output file path

   Generate YAML domain containing all devices from the System Device Tree.
   The generated YAML can be used as a parent domain for glob-based device matching.

   Example:
      lopper system.dts - -- sdt_devices -o output.yaml
      lopper system.dts - -- sdt_devices -o output.yaml -b simple-bus,xlnx,versal-axi
      lopper system.dts - -- sdt_devices -o output.yaml -n my_devices
    """)


class SDTDevices:
    """Generates a YAML domain containing all devices from the SDT."""

    # Default bus compatible strings to search for devices
    DEFAULT_BUS_TYPES = ['simple-bus']

    def __init__(self, sdt):
        """Initialize the SDT devices generator.

        Args:
            sdt (LopperSDT): The system device tree instance
        """
        self.sdt = sdt
        self.tree = LopperTree()
        self.tree.phandle_resolution = False

    def discover_devices(self, bus_types=None):
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
        seen_devices = set()  # Track seen devices to avoid duplicates

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

            # Get direct children of the bus node
            for node in bus.subnodes(children_only=True):
                # Only include addressable devices (have @ in name)
                if '@' in node.name:
                    # Skip if already seen (can happen with overlapping bus types)
                    if node.abs_path in seen_devices:
                        continue
                    seen_devices.add(node.abs_path)

                    entry = {'dev': node.name}

                    # Add label if present
                    if node.label:
                        entry['label'] = node.label

                    devices.append(entry)
                    lopper.log._debug(f"  Found device: {node.name} (label: {node.label})")

        lopper.log._info(f"Discovered {len(devices)} devices from SDT")
        return devices

    def generate_domain(self, domain_name='sdt_all_devices', bus_types=None):
        """Generate a domain node containing all discovered devices.

        Creates a tree structure:
            /domains
                /<domain_name>
                    compatible = "lopper,sdt-devices-v1"
                    id = 0
                    access:
                    - dev: <device_name>
                      label: <device_label>
                    ...

        Args:
            domain_name (str): Name for the generated domain node
            bus_types (list): List of bus compatible strings to search

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

        # Discover devices and add to access property
        devices = self.discover_devices(bus_types)

        # Create access property with device list
        access = LopperProp("access", -1, domain, [])
        access.phandle_resolution = False
        domain + access

        # Add each device entry to the access list
        for dev in devices:
            access.value.append(dev)

        lopper.log._info(f"Generated domain '{domain_name}' with {len(devices)} devices")

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
            "hvb:n:o:",
            ["help", "verbose", "bus-types=", "domain-name="]
        )
    except getopt.GetoptError as e:
        lopper.log._error(f"Invalid option: {e}")
        usage()
        return False

    # Default values
    bus_types = SDTDevices.DEFAULT_BUS_TYPES
    domain_name = 'sdt_all_devices'
    output_file = None

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

    lopper.log._info(f"sdt_devices: generating device list for domain '{domain_name}'")
    lopper.log._info(f"sdt_devices: scanning for bus types: {bus_types}")

    # Create the generator and build the domain tree
    generator = SDTDevices(sdt)
    tree = generator.generate_domain(domain_name=domain_name, bus_types=bus_types)

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
