#/*
# * Copyright (c) 2021 Xilinx Inc. All rights reserved.
# * Copyright (c) 2023, Advanced Micro Devices, Inc. All rights reserved.
# *
# * Author:
# *       Appana Durga Kedareswara rao <appana.durga.rao@xilinx.com>
# *       Naga Sureshkumar Relli <naga.sureshkumar.relli@xilinx.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

import struct
import sys
import types
import os
import getopt
import re
import subprocess
from pathlib import Path
from pathlib import PurePath
from lopper import Lopper
from lopper import LopperFmt
import lopper
from re import *
import yaml
import glob
from collections import OrderedDict
from lopper.tree import LopperTree, LopperNode
from __init__ import LopperSDT
import copy

sys.path.append(os.path.dirname(__file__))
from baremetalconfig_xlnx import *

def is_compat( node, compat_string_to_test ):
    if re.search( "module,xlnx_overlay_dt", compat_string_to_test):
        return xlnx_generate_overlay_dt
    return ""

def get_label(sdt, symbol_node, node):
    prop_dict = Lopper.node_properties_as_dict(sdt.FDT, symbol_node.abs_path, False)
    match = [label for label,node_abs in prop_dict.items() if re.match(node_abs[0], node.abs_path) and len(node_abs[0]) == len(node.abs_path)]
    if match:
        return match[0]
    else:
        return None

def remove_node_ref(sdt, tgt_node, ref_node):
    prop_dict = ref_node.__props__.copy()
    match_label_list = []
    for node in sdt.tree[tgt_node].subnodes():
        matched_label = get_label(sdt, ref_node, node)
        if matched_label:
            match_label_list.append(matched_label)
    for prop,node1 in prop_dict.items():
        if prop not in match_label_list:
            sdt.tree['/' + ref_node.name].delete(prop)

match_list = ["v_tc", "v_smpte_uhdsdi_tx", "v_smpte_uhdsdi_rx", "v_hdmi_tx1", "v_hdmi_rx1",
              "v_hdmi_rx", "v_hdmi_tx", "v_dp_tx", "v_dp_rx", "v_dp_rx1", "v_dp_tx1"]

def add_status_disabled_after_ipname(filepath, match_list):
    """
    This API adds `status = "disabled";` after each line matching `xlnx,ip-name = "{name}";`
    from the provided match_list. Preserves indentation.

    Args:
        filepath (str): Path to the input DTS/DTSI file.
        match_list (List[str]): List of IP names to match.
    """
    with open(filepath, "r") as f:
        lines = f.readlines()

    modified_lines = []
    for line in lines:
        modified_lines.append(line)
        stripped_line = line.strip()

        for name in match_list:
            if stripped_line == f'xlnx,ip-name = "{name}";':
                indent = line[:len(line) - len(line.lstrip())]
                modified_lines.append(f'{indent}status = "disabled";\n')
                break

    with open(filepath, "w") as f:
        f.writelines(modified_lines)

def infer_platform_from_sdt(sdt):
    """
    Infer the platform from the system device tree by parsing the family property.

    Args:
        sdt: System device tree object

    Returns:
        str: Inferred processor name or None if unable to determine
    """
    try:
        # Map family to processor names
        family_to_processor = {
            'Zynq': 'cortexa9',
            'ZynqMP': 'cortexa53',
            'Versal': 'cortexa72',
            'VersalNet': 'cortexa78',
            'Versal_2VE_2VM': 'cortexa78'
        }

        # Try to infer from the 'family' property at the root node
        family = sdt.tree['/'].propval('family')[0]
        if family in family_to_processor:
            processor = family_to_processor[family]
            print(f"Automatically inferred processor '{processor}' from family property '{family}'")
            return processor

        # Fallback: infer from CPU compatible strings
        for cpu_node in sdt.tree.nodes('/cpu.*@.*'):
            compatible = cpu_node.propval('compatible')[0]
            if 'cortex-a9' in compatible:
                print("Automatically inferred processor 'cortexa9' from CPU compatible string")
                return 'cortexa9'
            elif 'cortex-a53' in compatible:
                print("Automatically inferred processor 'cortexa53' from CPU compatible string")
                return 'cortexa53'
            elif 'cortex-a72' in compatible:
                print("Automatically inferred processor 'cortexa72' from CPU compatible string")
                return 'cortexa72'
            elif 'cortex-a78' in compatible:
                print("Automatically inferred processor 'cortexa78' from CPU compatible string")
                return 'cortexa78'

    except Exception as e:
        print(f"Warning: Failed to infer platform from system device tree: {e}")

    return None

def usage():
    print('Usage: ./lopper.py <system device tree> -- xlnx_overlay_dt <machine name> <configuration>')
    print('  system device tree:   Path to the input system device tree file (.dts)')
    print('  machine name:         (Optional) cortexa9-zynq | cortexa53-zynqmp | cortexa72-versal | cortexa78-versalnet')
    print('                        (or short forms: cortexa9 | cortexa53 | cortexa72 | cortexa78)')
    print('                        If not provided, the platform will be inferred from the system device tree.')
    print('  configuration:        full | segmented | dfx | external-fpga-config')

def validate_and_parse_options(options, sdt):
    """
    Parse and validate platform and configuration options.
    Supports two invocation modes:
    - Mode 1 (2 args): <machine> <config>
    - Mode 2 (1 arg):  <config> (machine auto-inferred)

    Args:
        options: Dictionary containing 'args' list with platform and config parameters
        sdt: System device tree object for auto-inference

    Returns:
        tuple: (platform, config, zynq_platforms, versal_platforms)

    Raises:
        SystemExit: If validation fails for platform or config
    """
    # Define valid options
    valid_configs = ["full", "segmented", "dfx", "external-fpga-config"]
    valid_platforms = ["cortexa9-zynq", "cortexa9", "cortexa53-zynqmp", "cortexa53",
                       "cortexa72-versal", "cortexa72", "cortexa78-versalnet", "cortexa78",
                       "cortexa9_0", "psu_cortexa53_0", "cortexa53_0",
                       "psv_cortexa72_0", "cortexa72_0",
                       "psx_cortexa78_0", "cortexa78_0"]
    zynq_platforms = ["cortexa53-zynqmp", "cortexa53", "cortexa9-zynq", "cortexa9",
                      "cortexa9_0", "psu_cortexa53_0", "cortexa53_0"]
    versal_platforms = ["cortexa72-versal", "cortexa72", "cortexa78-versalnet", "cortexa78",
                        "psv_cortexa72_0", "cortexa72_0", "psx_cortexa78_0", "cortexa78_0"]

    # Parse arguments based on count
    try:
        num_args = len(options['args'])

        if num_args == 2:
            # Mode 1: <machine> <config>
            platform = options['args'][0]
            config = options['args'][1]

        elif num_args == 1:
            # Mode 2: <config> only (auto-infer machine)
            config = options['args'][0]

            # Auto-infer platform
            platform = infer_platform_from_sdt(sdt)
            if platform is None:
                print("Error: Unable to automatically infer platform from system device tree.")
                print("Please provide the machine argument explicitly.")
                usage()
                sys.exit(1)

        else:
            print(f"Error: Invalid number of arguments. Expected 1 or 2, got {num_args}")
            usage()
            sys.exit(1)

    except (KeyError, IndexError) as e:
        print(f"Error: Failed to parse arguments: {e}")
        usage()
        sys.exit(1)

    # Validate config argument
    if config not in valid_configs:
        print(f"Error: Invalid configuration '{config}'.")
        print(f"Supported configurations: {', '.join(valid_configs)}")
        usage()
        sys.exit(1)

    # Validate platform argument
    if platform not in valid_platforms:
        print(f"Error: Invalid platform '{platform}'.")
        print(f"Supported platforms: {', '.join(valid_platforms)}")
        usage()
        sys.exit(1)

    return platform, config, zynq_platforms, versal_platforms

def validate_amba_pl_node(amba_node):
    """
    Validate that amba_pl node has valid PL nodes with register properties.

    Args:
        amba_node: The /amba_pl node from the device tree

    Returns:
        bool: True if valid PL nodes exist, False otherwise
    """
    has_valid_pl = False
    for subnode in amba_node.subnodes():
        if subnode.propval('reg') != ['']:
            has_valid_pl = True
            break

    if not has_valid_pl:
        print("[WARNING]: No valid PL nodes found in amba_pl (no nodes with reg properties)")

    return has_valid_pl

def setup_phandle_properties():
    """
    Configure phandle possible properties for Lopper.

    Sets up the dictionary of properties that can contain phandle references,
    including custom properties for Xilinx IP connections.
    """
    Lopper.phandle_possible_prop_dict = Lopper.phandle_possible_properties()
    Lopper.phandle_possible_prop_dict["axistream-connected"] = ["phandle"]
    Lopper.phandle_possible_prop_dict["pcs-handle"] = ["phandle"]
    Lopper.phandle_possible_prop_dict["remote_endpoint"] = ["phandle"]

def prepare_amba_node(amba_node, sdt, tgt_node):
    """
    Prepare amba node for overlay by cloning, removing from source tree, and cleaning up.

    Args:
        amba_node: The /amba_pl node from the device tree
        sdt: The system device tree object
        tgt_node: The target node for reference removal

    Returns:
        LopperNode: The prepared amba node ready for overlay
    """
    # Clone the amba_pl node
    new_amba_node = amba_node()

    # Remove amba_pl node from source tree
    sdt.tree = sdt.tree - amba_node

    # Remove "amba_pl" references from __symbols__ and aliases
    try:
        remove_node_ref(sdt, tgt_node, sdt.tree['/__symbols__'])
    except KeyError:
        print("[INFO]: No __symbols__ node found in device tree")

    try:
        remove_node_ref(sdt, tgt_node, sdt.tree['/aliases'])
    except KeyError:
        print("[INFO]: No aliases node found in device tree")

    # Rename the new node in the overlay
    new_amba_node.name = "&amba"
    new_amba_node.label = ""

    # Delete structural properties that should only exist in the base device tree
    new_amba_node.delete("ranges")
    new_amba_node.delete("compatible")
    new_amba_node.delete("#address-cells")
    new_amba_node.delete("#size-cells")

    return new_amba_node

def create_fpga_node(platform, config, firmware_name, zynq_platforms, versal_platforms):
    """
    Create and configure FPGA node based on platform and configuration type.

    Args:
        platform: Target platform (e.g., cortexa53-zynqmp, cortexa72-versal)
        config: Configuration type (full, dfx, external-fpga-config)
        firmware_name: Property containing firmware name value
        zynq_platforms: List of Zynq/ZynqMP platform identifiers
        versal_platforms: List of Versal/VersalNet platform identifiers

    Returns:
        tuple: (fpga_node, fpga_node_name)

    Raises:
        SystemExit: If unsupported platform/config combination is detected
    """
    # Determine FPGA node name based on platform
    if platform in zynq_platforms:
        fpga_node_name = "&fpga_full"
    else:
        fpga_node_name = "&fpga"

    # Create FPGA node
    fpga_node = LopperNode(name=fpga_node_name)

    # Configure fpga node based on platform and configuration:
    # - "full" config: Uses firmware-name for all platforms
    # - "dfx" config: Uses firmware-name for ZynqMP, external-fpga-config for Versal
    # - "external-fpga-config": Always uses external-fpga-config property
    if config == "full":
        fpga_node["firmware-name"] = firmware_name.value
    elif config == "dfx":
        if platform in zynq_platforms:
            # ZynqMP/Zynq uses firmware-name for DFX
            fpga_node["firmware-name"] = firmware_name.value
        elif platform in versal_platforms:
            # Versal/VersalNet uses external-fpga-config for DFX
            fpga_node["external-fpga-config"] = None
        else:
            print(f'Unsupported platform for DFX: {platform}')
            sys.exit(1)
    elif config == "external-fpga-config":
        fpga_node["external-fpga-config"] = None
    else:
        print(f'Invalid configuration: {config}')
        sys.exit(1)

    return fpga_node, fpga_node_name

def collect_special_nodes(amba_node):
    """
    Collect special nodes from amba node that need to be moved to fpga node.

    Categorizes subnodes into:
    - misc_clk_*: Miscellaneous clock nodes
    - fpga_PR*/fpga-PR*: Partial reconfiguration regions
    - afi*: AFI configuration nodes
    - clocking*: Clock configuration nodes

    Args:
        amba_node: The amba node containing subnodes to categorize

    Returns:
        dict: Dictionary with keys 'misc_clk', 'fpga_pr', 'afi', 'clocking',
              each containing a list of matching nodes
    """
    misc_clk_nodes = []
    fpga_pr_nodes = []
    afi_nodes = []
    clocking_nodes = []

    for subnode in amba_node.subnodes():
        if subnode.name.startswith("misc_clk_"):
            misc_clk_nodes.append(subnode)
        elif subnode.name.startswith("fpga_PR") or subnode.name.startswith("fpga-PR"):
            fpga_pr_nodes.append(subnode)
        elif subnode.name.startswith("clocking"):
            clocking_nodes.append(subnode)
        elif subnode.name.startswith("afi"):
            afi_nodes.append(subnode)

    return {
        'misc_clk': misc_clk_nodes,
        'fpga_pr': fpga_pr_nodes,
        'afi': afi_nodes,
        'clocking': clocking_nodes
    }

def move_nodes_to_fpga(new_amba_node, fpga_node, node_collections, platform, config, zynq_platforms):
    """
    Move special nodes from amba node to fpga node based on platform and configuration.

    Always moves:
    - misc_clk nodes (all platforms)
    - fpga_pr nodes (all platforms)

    Conditionally moves (ZynqMP/Zynq platforms with "full" or "dfx" config only):
    - clocking nodes
    - afi nodes

    Args:
        new_amba_node: The amba node to remove nodes from
        fpga_node: The fpga node to add nodes to
        node_collections: Dictionary containing categorized node lists
        platform: Target platform identifier
        config: Configuration type (full, dfx, external-fpga-config)
        zynq_platforms: List of Zynq/ZynqMP platform identifiers

    Returns:
        tuple: (updated_amba_node, updated_fpga_node)
    """
    # Move misc_clk nodes (always)
    if node_collections['misc_clk']:
        for clk_node in node_collections['misc_clk']:
            new_amba_node = new_amba_node - clk_node
            fpga_node = fpga_node + clk_node

    # Move fpga_pr nodes (always)
    if node_collections['fpga_pr']:
        for pr_node in node_collections['fpga_pr']:
            new_amba_node = new_amba_node - pr_node
            fpga_node = fpga_node + pr_node

    # For ZynqMP/Zynq platforms with "full" or "dfx" config, move AFI and clocking to fpga node
    if (platform in zynq_platforms) and config != "external-fpga-config":
        if node_collections['clocking']:
            for clk_node in node_collections['clocking']:
                new_amba_node = new_amba_node - clk_node
                fpga_node = fpga_node + clk_node

        if node_collections['afi']:
            for afi_node in node_collections['afi']:
                new_amba_node = new_amba_node - afi_node
                fpga_node = fpga_node + afi_node

    return new_amba_node, fpga_node

def build_overlay_tree(new_amba_node, fpga_node, fpga_node_name, base_tree):
    """
    Build overlay tree from amba and fpga nodes, establishing overlay relationship.

    Creates a new overlay tree, adds nodes, establishes overlay relationship with
    base tree, reorders nodes, and resolves all references.

    Args:
        new_amba_node: The prepared amba node for overlay
        fpga_node: The configured fpga node
        fpga_node_name: The name of the fpga node (e.g., "&fpga" or "&fpga_full")
        base_tree: The base device tree to overlay against

    Returns:
        LopperTree: The constructed and resolved overlay tree

    Raises:
        SystemExit: If node reordering fails
    """
    # Create overlay tree
    overlay_tree = LopperTree()

    # Resolve the fpga_node before adding to overlay_tree
    fpga_node.resolve()

    # Add nodes to overlay_tree
    overlay_tree = overlay_tree + new_amba_node
    overlay_tree = overlay_tree + fpga_node

    # Establish overlay relationship to resolve phandle references against base tree
    overlay_tree.overlay_of(base_tree)

    # Reorder nodes: fpga node should come after amba node
    try:
        overlay_tree['/'].reorder_child("/&amba", "/" + fpga_node_name, after=True, debug=True)
    except Exception as e:
        print(f"ERROR: reordering nodes: {e}")
        os._exit(1)

    # Resolve all phandle references and finalize tree structure
    overlay_tree.resolve()

    return overlay_tree

def clean_overlay_properties(overlay_tree):
    """
    Remove phandle and status="okay" properties from all nodes in overlay tree.

    Phandles should not be in overlays as they are resolved against the base tree.
    Status="okay" should not be in overlays as devices are enabled by default.

    Args:
        overlay_tree: The overlay tree to clean properties from

    Note:
        Modifies overlay_tree in place
    """
    for path, node in overlay_tree.__nodes__.items():
        # Remove phandle properties
        if node.phandle and node.phandle > 0:
            # Set phandle to 0 to prevent it from being printed
            node.phandle = 0
            # Also delete the property if it exists
            try:
                node.delete("phandle")
            except:
                pass

        # Remove status = "okay" property
        try:
            status_prop = node.propval('status')
            if status_prop and status_prop[0] == 'okay':
                node.delete('status')
        except:
            pass

def write_output_files(overlay_tree, sdt, pl_file, sdt_file, dtso_file):
    """
    Write output files and perform post-processing.

    Writes overlay tree to pl.dtsi, system device tree to sdt.dts, performs
    post-processing (interrupt-parent replacement, status disabled for video IPs),
    and copies pl.dtsi to pl.dtso.

    Args:
        overlay_tree: The overlay tree to write
        sdt: The system device tree object
        pl_file: Path to output pl.dtsi file
        sdt_file: Path to output sdt.dts file
        dtso_file: Path to output pl.dtso file
    """
    # Write overlay tree and system device tree
    LopperSDT(None).write(overlay_tree, pl_file, True, True)
    sdt.write(sdt.tree, sdt_file)

    # Post-process the generated pl.dtsi file to replace interrupt-parent references
    with open(pl_file, "r") as f:
        content = f.read()

    # Replace interrupt-parent references from imux to gic
    content = content.replace('interrupt-parent = <&imux>;', 'interrupt-parent = <&gic>;')

    with open(pl_file, "w") as f:
        f.write(content)

    # Add status = "disabled" for specific video IPs
    add_status_disabled_after_ipname(pl_file, match_list)

    # Copy contents from pl.dtsi to pl.dtso in the same directory
    with open(pl_file, "r") as src, open(dtso_file, "w") as dst:
        dst.writelines(src.readlines())

"""
This assist generates an overlay dts file (pl.dtsi) from the /amba_pl node
in the system device tree. Uses LopperTree operations to extract and transform
the PL (Programmable Logic) nodes into a proper device tree overlay format.

Args:
    tgt_node: is the baremetal config top level domain node number
    sdt:      is the system device-tree
    options:  There are two valid options
              Machine name: cortexa9-zynq | cortexa53-zynqmp | cortexa72-versal | cortexa78-versalnet
              (or short forms: cortexa9 | cortexa53 | cortexa72 | cortexa78)
              Configuration: full | segmented | dfx | external-fpga-config (default: full)

Output:
    - pl.dtsi: Device tree overlay file with &amba and &fpga nodes
    - pl.dtso: Copy of pl.dtsi with .dtso extension
    - sdt.dts: Modified system device tree with /amba_pl node removed
"""
def xlnx_generate_overlay_dt(tgt_node, sdt, options):
    # Parse and validate options
    platform, config, zynq_platforms, versal_platforms = validate_and_parse_options(options, sdt)

    overlay_tree = LopperTree()

    print("Starting overlay generation...")

    # Extract the /amba_pl node from sdt.tree
    amba_node = sdt.tree["/amba_pl"]

    # Validate that amba_pl has valid PL nodes with register properties
    if not validate_amba_pl_node(amba_node):
        return True

    # Setup phandle configuration
    setup_phandle_properties()

    # Prepare amba node for overlay
    new_amba_node = prepare_amba_node(amba_node, sdt, tgt_node)

    # Extract firmware-name property from amba node and remove it from amba node
    firmware_name = new_amba_node["firmware-name"]
    new_amba_node = new_amba_node - firmware_name

    # Create and configure FPGA node
    fpga_node, fpga_node_name = create_fpga_node(
        platform, config, firmware_name, zynq_platforms, versal_platforms
    )

    # Collect special nodes from amba node
    node_collections = collect_special_nodes(new_amba_node)

    # Move special nodes to fpga node based on platform and configuration
    new_amba_node, fpga_node = move_nodes_to_fpga(
        new_amba_node, fpga_node, node_collections,
        platform, config, zynq_platforms
    )

    # Build overlay tree
    overlay_tree = build_overlay_tree(new_amba_node, fpga_node, fpga_node_name, sdt.tree)

    # Clean overlay properties
    clean_overlay_properties(overlay_tree)

    pl_file = f"{sdt.outdir}/pl.dtsi"
    sdt_file = f"{sdt.outdir}/sdt.dts"
    dtso_file = f"{sdt.outdir}/pl.dtso"

    print( f"[INFO]: pl: {pl_file} sdt: {sdt_file} dtso: {dtso_file}" )

    # Write output files and perform post-processing
    write_output_files(overlay_tree, sdt, pl_file, sdt_file, dtso_file)

    print("Overlay generation completed successfully!")
    return True