#/*
# * Copyright (c) 2023 - 2025 Advanced Micro Devices, Inc. All Rights Reserved.
# *
# * Author:
# *       Onkar Harsh <onkar.harsh@amd.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

import os
import sys
import re
import glob
import yaml
from typing import Any, List, Optional, Dict, Union
import shutil
import logging
from lopper.log import _init, _warning

_init(__name__)

def to_cmakelist(pylist):
    cmake_list = ';'.join(pylist)
    cmake_list = '"{}"'.format(cmake_list)

    return cmake_list

def is_file(filepath: str, silent_discard: bool = True) -> bool:
    """Return True if the file exists Else returns False and raises Not Found Error Message.
    
    Args:
        filepath: File Path.
    Raises:
        FileNotFoundError: Raises exception if file not found.
    Returns:
        bool: True, if file is found Or False, if file is not found.
    """
   
    if os.path.isfile(filepath):
        return True
    elif not silent_discard:
        err_msg = f"No such file exists: {filepath}"
        raise FileNotFoundError(err_msg) from None
    else:
        return False

def is_dir(dirpath: str, silent_discard: bool = True) -> bool:
    """Checks if directory exists.
    
    Args:
        dirpath: Directory Path.
    Raises:
        ValueError (Exception): Raises exception if directory not found.
    Returns:
        bool: True, if directory is found Or False, if directory is not found.
    """

    if os.path.isdir(dirpath):
        return True
    elif not silent_discard:
        err_msg = f"No such directory exists: {dirpath}"
        raise ValueError(err_msg) from None
    else:
        return False

def get_base_name(fpath):
    """
    This api takes rel path or full path and returns base name
    Args:
        fpath: Path to get the base name from.
    Returns:
        string: Base name of the path
    """
    return os.path.basename(fpath.rstrip(os.path.sep))

def get_dir_path(fpath):
    """
    This api takes file path and returns it's directory path
    
    Args:
        fpath: Path to get the directory path from.
    Returns:
        string: Full Directory path of the passed path
    """
    return os.path.dirname(fpath.rstrip(os.path.sep))

def get_abs_path(fpath):
    """
    This api takes file path and returns it's absolute path
    Args:
        fpath: Path to get the absolute path from.
    Returns:
        string: Absolute location of the passed path
    """
    return os.path.abspath(fpath)

def load_yaml(filepath: str) -> Optional[dict]:
    """Read yaml file data and returns data in a dict format.
    
    Args:
        filepath: Path of the yaml file.
    Returns:
        dict: Return Python dict if the file reading is successful.
    """

    if is_file(filepath):
        try:
            with open(filepath) as f:
                data = yaml.safe_load(f)
            return data
        except Exception as e:
            _warning(f"{filepath} file reading failed: {e}")
            return {}
    else:
        return {}

def copy_file(src: str, dest: str, follow_symlinks: bool = False, silent_discard: bool = True) -> None:
    """
    copies the file from source to destination.
    Args:
        | src: source file path
        | dest: destination file path
        | follow_symlinks: maintain the symlink while copying
        | silent_discard: Dont raise exception if the source file doesnt exist 
    """
    is_file(src, silent_discard)
    shutil.copy2(src, dest, follow_symlinks=follow_symlinks)
    os.chmod(dest, 0o644)

def find_files(search_pattern, search_path):
    """
    This api find the files matching regex directories and returns absolute
    path of files, if file exists

    Args:
        | search_pattern: The regex pattern to be searched in file names
        | search_path: The directory that needs to be searched
    Returns:
        string: All the file paths that matches the pattern in the searched path.

    """

    return glob.glob(f"{search_path}{os.path.sep}{search_pattern}")
    
def log_setup(options):
  
    """
    Sets up the log level based on the verbosity given by the user.
    
    Args:
        options (dict): Dictionary containing command-line arguments.
    
    Returns:
        int: Logging level.
    """
    verbose = [i for i in options.get("args", []) if i.startswith('-v')]
    verbose_level = 1 if verbose else 0 
    # Adjust logging level based on verbose level
    level = logging.DEBUG if verbose_level >= 1 else logging.WARNING
    #print(f"[LOG_SETUP] Verbose level = {verbose_level}, Final level = {level}")
    return level

def run_exec(yaml_condition, proc_ip_name, family, variant=None, return_list="examples", yaml_file=""):
    """
    Executes a Python condition string in a restricted local scope and returns a specified list.

    Args:
        yaml_condition (str): Python code to execute, typically from a YAML file.
        proc_ip_name (str): Name of the processor IP available in the local scope as 'proc'.
        family (str): Platform family available in the local scope as 'platform'.
        variant (str, optional): Variant available in the local scope as 'variant'.
        return_list (str, optional): Name of the list to return from the local scope. Defaults to "examples".
        yaml_file (str, optional): YAML file name for error reporting.

    Returns:
        list: The list named by `return_list` from the local scope after executing the condition.

    Notes:
        Any exceptions during execution are caught and logged as warnings.
    """
    local_scope = {
        "proc": proc_ip_name,
        "platform": family,
        "variant": variant,
        return_list: []
    }
    try:
        exec(yaml_condition, {"__builtins__": {}}, local_scope)
    except Exception as e:
        _warning(f"The condition in the {yaml_file} file has failed. -> {e}")
    finally:
        return local_scope[return_list]

def cells_to_int(cells):
    """Convert a list of cells to a single integer value.

    Args:
        cells (list or int): List of cell values to convert, or single integer

    Returns:
        int: Combined integer value
    """
    # Handle case where cells is already an integer
    if isinstance(cells, int):
        return cells

    # Handle case where cells is a list
    if isinstance(cells, (list, tuple)):
        val = 0
        for c in cells:
            val = (val << 32) + c
        return val

    # Fallback - try to convert directly
    try:
        return int(cells)
    except (TypeError, ValueError):
        print(f"ERROR: cells_to_int received unexpected type: {type(cells)}, value: {cells}")
        return 0


def check_reserved_memory_overlaps(tree, carveouts=None, exit_on_error=False):
    """Check for overlapping memory regions in reserved-memory section.

    This function combines the functionality of validating all reserved memory regions
    and specific carveout nodes. It can be used for both general overlap detection
    and specific carveout validation scenarios.

    Args:
        tree: LopperTree object containing the device tree
        carveouts (list, optional): Specific carveout nodes to validate. If None,
                                   validates all reserved memory regions.
        exit_on_error (bool, optional): Whether to exit the program if overlaps are found.
                                        Defaults to False.

    Returns:
        bool: True if overlaps found or validation failed, False if no overlaps detected.

    Examples:
        # Check all reserved memory regions for Linux DT
        has_overlaps = check_reserved_memory_overlaps(tree)

        # Validate specific carveouts
        has_overlaps = check_reserved_memory_overlaps(tree, carveouts=my_carveouts)
    """

    # Check if we expect DDR carveouts when validating specific carveouts
    expect_ddr = False
    if carveouts:
        expect_ddr = any(["/reserved-memory/" in n.abs_path for n in carveouts])
        print(f" -> check_reserved_memory_overlaps: validating {len(carveouts)} carveouts")

    try:
        reserved_memory_node = tree['/reserved-memory']
    except KeyError:
        if carveouts and expect_ddr:
            print("ERROR: carveouts should be in reserved memory.")
            if exit_on_error:
                sys.exit(1)
            return True  # Return True to indicate error/overlap condition
        else:
            return False  # No reserved-memory section found

    # Get address and size cells for reserved-memory
    address_cells = reserved_memory_node.propval('#address-cells')
    size_cells = reserved_memory_node.propval('#size-cells')

    # Ensure we have proper values and they're in list form
    if address_cells == [''] or address_cells is None:
        addr_cells = 2
    else:
        addr_cells = address_cells[0] if isinstance(address_cells, list) else address_cells

    if size_cells == [''] or size_cells is None:
        sz_cells = 2
    else:
        sz_cells = size_cells[0] if isinstance(size_cells, list) else size_cells

    # Collect all memory regions with their ranges
    memory_regions = []

    # Create carveout pairs for validation if carveouts are provided
    carveout_pairs = []
    if carveouts:
        for carveout in carveouts:
            try:
                reg_prop = carveout.propval("reg")
                if reg_prop and reg_prop != ['']:
                    # Ensure reg_values is a list
                    reg_values = reg_prop if isinstance(reg_prop, list) else [reg_prop]
                    if len(reg_values) >= 4:  # Need at least 4 values: addr_high, addr_low, size_high, size_low
                        # Extract base address and size using the same logic as xlnx_validate_carveouts
                        base = reg_values[1]  # Second element is base address
                        size = reg_values[3]  # Fourth element is size
                        carveout_pairs.append([base, size])
            except Exception as e:
                continue

    try:
        for node in reserved_memory_node.subnodes():
            try:
                reg_prop = node.propval('reg')
                if reg_prop and reg_prop != ['']:
                    # Ensure reg_values is a list
                    reg_values = reg_prop if isinstance(reg_prop, list) else [reg_prop]

                    # Calculate minimum required values
                    min_values = addr_cells + sz_cells

                    if len(reg_values) >= min_values:
                        # Parse reg values in groups of (addr_cells + sz_cells)
                        entry_size = addr_cells + sz_cells

                        for i in range(0, len(reg_values), entry_size):
                            if i + entry_size <= len(reg_values):
                                try:
                                    # Use cells_to_int for address and size calculation
                                    addr_cells_data = reg_values[i:i+addr_cells]
                                    size_cells_data = reg_values[i+addr_cells:i+addr_cells+sz_cells]
                                    start_addr = cells_to_int(addr_cells_data)
                                    size = cells_to_int(size_cells_data)

                                    # Calculate end address (exclusive)
                                    end_addr = start_addr + size

                                    # Skip zero-size regions
                                    if size > 0:
                                        memory_regions.append({
                                            'node': node,
                                            'start': start_addr,
                                            'end': end_addr,
                                            'size': size,
                                            'name': node.name,
                                            'is_carveout': carveouts and [start_addr, size] in carveout_pairs
                                        })

                                except Exception as e:
                                    continue
            except Exception as e:
                continue

        # Check for overlaps
        overlaps_found = []

        for i, region1 in enumerate(memory_regions):
            for j, region2 in enumerate(memory_regions[i+1:], i+1):
                # If validating specific carveouts, only check overlaps involving carveouts
                if carveouts and not (region1.get('is_carveout', False) or region2.get('is_carveout', False)):
                    continue

                # Check if regions overlap
                # Two regions overlap if: start1 < end2 AND start2 < end1
                if region1['start'] < region2['end'] and region2['start'] < region1['end']:
                    overlap_start = max(region1['start'], region2['start'])
                    overlap_end = min(region1['end'], region2['end'])
                    overlap_size = overlap_end - overlap_start

                    overlaps_found.append({
                        'region1': region1,
                        'region2': region2,
                        'overlap_start': overlap_start,
                        'overlap_end': overlap_end,
                        'overlap_size': overlap_size
                    })

        # Print warnings for overlaps
        if overlaps_found:
            # Build overlap details for the message
            context = "carveout validation" if carveouts else "reserved-memory section"
            overlap_details = []

            for i, overlap in enumerate(overlaps_found, 1):
                region1 = overlap['region1']
                region2 = overlap['region2']
                carveout1_flag = " (carveout)" if region1.get('is_carveout', False) else ""
                carveout2_flag = " (carveout)" if region2.get('is_carveout', False) else ""

                # Determine overlap type
                if (region1['start'] <= region2['start'] and region1['end'] >= region2['end']):
                    overlap_type = f"{region2['name']} is completely contained within {region1['name']}"
                elif (region2['start'] <= region1['start'] and region2['end'] >= region1['end']):
                    overlap_type = f"{region1['name']} is completely contained within {region2['name']}"
                else:
                    overlap_type = "Partial overlap"

                overlap_details.append(f"""
Overlap #{i}:
  Region 1: {region1['name']}{carveout1_flag}
    Range: 0x{region1['start']:x} - 0x{region1['end']:x} (size: 0x{region1['size']:x})
  Region 2: {region2['name']}{carveout2_flag}
    Range: 0x{region2['start']:x} - 0x{region2['end']:x} (size: 0x{region2['size']:x})
  Overlap: 0x{overlap['overlap_start']:x} - 0x{overlap['overlap_end']:x} (size: 0x{overlap['overlap_size']:x})
    Type: {overlap_type}""")

            # Create the complete message
            message = f"""
{'=' * 80}
{'ERROR' if exit_on_error else 'WARNING'}: Memory overlap(s) detected in {context}!
{'=' * 80}{''.join(overlap_details)}

{'-' * 80}
These overlaps may cause memory management issues and should be reviewed.
Consider adjusting memory region sizes or addresses to eliminate overlaps.
{'=' * 80}
"""
            print(message)

            if exit_on_error:
                sys.exit(1)

            return True  # Overlaps found
        else:
            return False  # No overlaps

    except Exception as e:
        print(f"ERROR: while checking reserved memory overlaps: {e}")
        import traceback
        traceback.print_exc()
        return False