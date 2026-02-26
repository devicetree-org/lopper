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
