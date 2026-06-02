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

The extraction logic lives in `_devices_core.DevicesCore`; this module is
the thin assist wrapper that handles CLI option parsing and YAML
output. Other extractors (Linux DT, Zephyr DT, multi-source merge) reuse
the same core.

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

import os
import getopt
import logging
import re

from lopper.yaml import LopperYAML
import lopper
import lopper.log

from lopper.assists._devices_core import DeviceCategory, DevicesCore

# Backward-compatibility alias: external callers (and the test suite) import
# `SDTDevices` from this module. The class body now lives in _devices_core
# as DevicesCore; SDTDevices is the SDT-input flavour of it (no
# SDT-specific behaviour yet, but the subclass keeps a hook for future
# SDT-only tweaks without changing the public surface).
class SDTDevices(DevicesCore):
    """SDT-input flavour of the shared device-inventory extractor."""
    pass

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
