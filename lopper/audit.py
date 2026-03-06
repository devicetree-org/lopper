#/*
# * Copyright (c) 2024,2025 Xilinx Inc. All rights reserved.
# *
# * Author:
# *       Bruce Ashfield <bruce.ashfield@xilinx.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

#
# Lopper audit module
#
# This module contains tree validation and consistency checking routines.
# These are designed to be called at various points in the pipeline,
# typically during resolve() operations.
#
# Future work will expand this into a data-driven audit framework with
# configurable assertions, relationships, and semantic checks.
#

import re
import lopper.base
import lopper.log


def _cell_value_get(cells, cell_size, start_idx=0):
    """Extract a multi-cell value from a property.

    Args:
        cells: List of cell values
        cell_size: Number of cells to combine (1 or 2)
        start_idx: Starting index in cells list

    Returns:
        Tuple of (combined_value, used_cells_list)
    """
    used_cells = []
    if cell_size == 2:
        value = (cells[start_idx] << 32) | cells[start_idx + 1]
        used_cells.append(cells[start_idx])
        used_cells.append(cells[start_idx + 1])
    else:
        value = cells[start_idx]
        used_cells.append(cells[start_idx])

    return value, used_cells

def check_invalid_phandles(tree, warn_only_modified=True):
    """Check for invalid phandle references in phandle-bearing properties

    This function walks all nodes and checks phandle-bearing properties
    for phandle values that don't resolve to any node.

    This catches:
    - dtc's 0xffffffff sentinel for unresolved <&label> references
    - Any other dangling numeric phandle references

    Args:
        tree (LopperTree): The tree to check
        warn_only_modified (bool): If True, only check nodes that have been
                                   modified (node.__modified__ == True) or
                                   are not yet resolved (node.__nstate__ != "resolved").
                                   Set to False for full tree check.

    Returns:
        list: List of (node_path, property_name) tuples for invalid references
    """
    invalid_refs = []

    # Get the list of properties that can contain phandles
    phandle_props = lopper.base.lopper_base.phandle_possible_properties()
    exclude_patterns = phandle_props.get("__phandle_exclude__", [])

    for n in tree:
        # Optimization: skip nodes that haven't been modified if requested
        if warn_only_modified:
            try:
                # Skip if node is resolved and not modified
                if n.__nstate__ == "resolved" and not n.__modified__:
                    continue
            except AttributeError:
                # Node doesn't have these attributes, check it anyway
                pass

        # Check if node path matches any exclude pattern
        excluded = False
        for pattern in exclude_patterns:
            if re.search(pattern, n.abs_path):
                excluded = True
                break
        if excluded:
            continue

        for p in n:
            if p.name not in phandle_props:
                continue

            # Optimization: skip properties that haven't been modified
            if warn_only_modified:
                try:
                    if p.__pstate__ == "syncd" and not p.__modified__:
                        continue
                except AttributeError:
                    pass

            # Use resolve_phandles with tag_invalid=True to properly identify
            # which values are phandles (vs addresses or other fields)
            try:
                resolved = p.resolve_phandles(tag_invalid=True)
            except:
                continue

            # Check for any "#invalid" markers
            for target in resolved:
                if target == "#invalid":
                    invalid_refs.append((n.abs_path, p.name))
                    # Only record once per property
                    break

    return invalid_refs


def report_invalid_phandles(tree, werror=False, warn_only_modified=True):
    """Check and report invalid phandle references

    Convenience wrapper that checks for invalid phandles and logs warnings/errors.

    Args:
        tree (LopperTree): The tree to check
        werror (bool): If True, treat warnings as errors and exit
        warn_only_modified (bool): If True, only check modified nodes

    Returns:
        int: Number of invalid references found
    """
    invalid_refs = check_invalid_phandles(tree, warn_only_modified)

    for node_path, prop_name in invalid_refs:
        msg = (f"invalid_phandle: property {prop_name} in {node_path} "
               f"contains a phandle reference that does not resolve to any node")
        if werror:
            lopper.log._error(msg, also_exit=1)
        else:
            lopper.log._warning(msg)

    return len(invalid_refs)


def check_reserved_memory_in_memory_ranges(tree, domain_node):
    """Check that reserved-memory regions fall within domain memory ranges.

    This function validates that all reserved-memory regions referenced by
    a domain node's 'reserved-memory' property are contained within the
    domain's memory ranges (from its 'memory' property).

    Args:
        tree (LopperTree): The tree containing the nodes
        domain_node (LopperNode): The domain node to validate

    Returns:
        list: List of (resmem_path, res_start, res_end) tuples for regions
              that fall outside domain memory ranges. Empty list if all valid.
    """
    invalid_regions = []

    # Get domain memory ranges
    try:
        mem_prop = domain_node['memory'].value
        if not mem_prop or mem_prop == ['']:
            return invalid_regions
    except:
        return invalid_regions

    # Get reserved-memory phandles
    try:
        resmem_prop = domain_node['reserved-memory'].value
        if not resmem_prop or resmem_prop == ['']:
            return invalid_regions
    except:
        return invalid_regions

    # Get cell sizes from root
    try:
        root_ac = tree['/']['#address-cells'][0]
    except:
        root_ac = 2
    try:
        root_sc = tree['/']['#size-cells'][0]
    except:
        root_sc = 2

    # Parse domain memory ranges
    memory_ranges = []
    cell_size = root_ac + root_sc
    for i in range(0, len(mem_prop), cell_size):
        chunk = mem_prop[i:i+cell_size]
        if len(chunk) < cell_size:
            break
        mem_start, _ = _cell_value_get(chunk, root_ac)
        mem_size, _ = _cell_value_get(chunk, root_sc, root_ac)
        mem_end = mem_start + mem_size
        memory_ranges.append((mem_start, mem_end))

    if not memory_ranges:
        return invalid_regions

    # Check each reserved-memory region
    for ph in resmem_prop:
        if not isinstance(ph, int):
            continue

        resmem_node = tree.pnode(ph)
        if not resmem_node:
            lopper.log._warning(f"check_reserved_memory: could not find node for phandle {ph}")
            continue

        # Get reg property
        try:
            reg_val = resmem_node['reg'].value
            if not reg_val or reg_val == ['']:
                continue
        except:
            continue

        # Get cell sizes for reserved-memory
        try:
            resmem_ac = resmem_node.parent['#address-cells'][0]
        except:
            resmem_ac = root_ac
        try:
            resmem_sc = resmem_node.parent['#size-cells'][0]
        except:
            resmem_sc = root_sc

        # Parse reserved-memory region
        res_start, _ = _cell_value_get(reg_val, resmem_ac)
        res_size, _ = _cell_value_get(reg_val, resmem_sc, resmem_ac)
        res_end = res_start + res_size

        # Check if reserved-memory falls within any domain memory range
        found_valid_range = False
        for mem_start, mem_end in memory_ranges:
            if res_start >= mem_start and res_end <= mem_end:
                found_valid_range = True
                break

        if not found_valid_range:
            invalid_regions.append((resmem_node.abs_path, res_start, res_end))

    return invalid_regions


def validate_reserved_memory_in_memory_ranges(tree, domain_node, werror=False):
    """Validate reserved-memory regions and report errors.

    Convenience wrapper that checks reserved-memory regions and logs errors
    for any that fall outside domain memory ranges.

    Args:
        tree (LopperTree): The tree containing the nodes
        domain_node (LopperNode): The domain node to validate
        werror (bool): If True, treat errors as fatal and exit

    Returns:
        int: Number of invalid regions found

    Raises:
        SystemExit: If werror=True and invalid regions found
    """
    invalid_regions = check_reserved_memory_in_memory_ranges(tree, domain_node)

    for resmem_path, res_start, res_end in invalid_regions:
        msg = (f"reserved-memory region {resmem_path} "
               f"({hex(res_start)}-{hex(res_end)}) is outside domain memory ranges")
        if werror:
            lopper.log._error(msg, also_exit=1)
        else:
            lopper.log._error(msg)

    return len(invalid_regions)
