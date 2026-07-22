#/*
# * Copyright (C) 2025 Advanced Micro Devices, Inc. All Rights Reserved.
# *
# * Author:
# *     Appana Durga Kedareswara rao <appana.durga.kedareswara.rao@amd.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

import yaml
import sys
import os
import glob
import lopper
import re
from lopper.tree import LopperProp
from lopper.tree import LopperNode

sys.path.append(os.path.dirname(__file__))

import sys
import os
import re


def process_overlay_with_lopper_api(overlay_content, main_tree):
    """
    Process overlay content using official lopper tree API methods
    
    Args:
        overlay_content (str): The overlay file content
        main_tree: The LopperTree object (sdt.tree)
    
    Returns:
        str: Cleaned overlay content
    """
    
    # Extract all overlay references (&reference)
    overlay_refs = set(re.findall(r'&(\w+)', overlay_content))
    
    # Extract internal labels (locally defined in the overlay)
    internal_labels = set(re.findall(r'(\w+):\s*[\w@-]+\s*{', overlay_content))
    
    #print(f"[INFO] Found overlay references: {sorted(list(overlay_refs))}")
    #print(f"[INFO] Found internal labels: {sorted(list(internal_labels))}")
    
    # Check which references exist using lopper tree API
    valid_refs = []
    invalid_refs = []
    
    for ref in overlay_refs:
        found_in_system = False
        found_internally = ref in internal_labels
        
        #print(f"[INFO] Checking reference '{ref}' (internal: {found_internally})")
        
        if not found_internally:
            # Use lopper tree API calls to check if reference exists
            found_in_system = check_reference_with_lopper_api(main_tree, ref)
        
        if found_in_system or found_internally:
            valid_refs.append(ref)
        else:
            invalid_refs.append(ref)
    
    # Process overlay content to remove invalid sections
    if not invalid_refs:
        #print("[INFO] All references are valid. Applying final cleanup only.")
        return apply_final_cleanup(overlay_content)
    
    return clean_overlay_content(overlay_content, valid_refs, invalid_refs, internal_labels)


def check_reference_with_lopper_api(tree, ref_name):
    """
    Check if a reference exists using official lopper tree API methods
    Fixed version with accurate debug output
    
    Args:
        tree: The LopperTree object
        ref_name: The reference name to search for
    
    Returns:
        bool: True if reference is found, False otherwise
    """
    
    # Method 1: Check aliases using tree['path'] access
    try:
        aliases_node = tree['/aliases']
        if aliases_node:
            # Check if this reference is an alias
            try:
                alias_prop = aliases_node[ref_name]
                if alias_prop:
                    #print(f"[DEBUG] ✓ Found alias '{ref_name}' = '{alias_prop.value}' in tree['/aliases']")
                    return True
            except:
                pass
        #print(f"[DEBUG] ✗ No alias '{ref_name}' found in tree['/aliases']")
    except Exception as e:
        #print(f"[DEBUG] ✗ Aliases check failed for '{ref_name}': {e}")
        pass
    
    # Method 2: Check symbols using tree['path'] access
    try:
        symbols_node = tree['/__symbols__']
        if symbols_node:
            try:
                prop_dict = symbols_node.__props__
                symbol_exist = [label for label,node_abs in prop_dict.items() if label == ref_name]
                if symbol_exist:
                    #print(f"[DEBUG] ✓ Found symbol '{ref_name}' -> '{symbol_prop.value}' in tree['/__symbols__']")
                    return True
            except:
                pass
        #print(f"[DEBUG] ✗ No symbol '{ref_name}' found in tree['/__symbols__']")
    except Exception as e:
        #print(f"[DEBUG] ✗ Symbols check failed for '{ref_name}': {e}")
        pass
    
    # Method 3: Use subnodes to traverse the tree
    try:
        root_node = tree['/']
        if root_node:
            # Get direct children first
            subnodes_list = tree.subnodes(root_node)
            for node in subnodes_list:
                # Check node label
                if hasattr(node, 'label') and node.label == ref_name:
                    #print(f"[DEBUG] ✓ Found node with label '{ref_name}' at: {node.abs_path}")
                    return True
                
                # Check node name
                if hasattr(node, 'name'):
                    node_name = node.name
                    # Match exact or with address suffix
                    if node_name == ref_name or node_name.startswith(f"{ref_name}@"):
                        #print(f"[DEBUG] ✓ Found node '{ref_name}' by name: {node_name} at {node.abs_path}")
                        return True
                
                # Recursively check subnodes
                if _check_subnodes_recursive(tree, node, ref_name):
                    return True
    except Exception as e:
        #print(f"[DEBUG] ✗ Subnodes traversal failed for '{ref_name}': {e}")
        pass
    
    #print(f"[DEBUG] ✗ Reference '{ref_name}' not found using any lopper tree API method")
    return False

 
def apply_final_cleanup(content):
    """
    Apply final formatting cleanup to remove artifacts
    """
    # Remove multiple consecutive empty lines
    content = re.sub(r'\n\s*\n\s*\n+', '\n\n', content)
    
    # Remove empty alias blocks
    content = re.sub(r'aliases\s*{\s*}\s*;?', '', content)
    
    # Remove orphaned semicolons on their own lines
    content = re.sub(r'^\s*;\s*$', '', content, flags=re.MULTILINE)
    
    # Clean up spacing around braces
    content = re.sub(r'}\s*;\s*\n', '};\n', content)
    
    # Remove trailing whitespace from lines
    content = re.sub(r'[ \t]+$', '', content, flags=re.MULTILINE)
    
    # Ensure single newline at end
    content = content.rstrip() + '\n'
    
    # One more pass to clean up multiple newlines that might have been created
    content = re.sub(r'\n\s*\n\s*\n+', '\n\n', content)
    
    return content


def clean_overlay_content(overlay_content, valid_refs, invalid_refs, internal_labels):
    """
    Clean the overlay content by removing invalid sections
    """
    #print(f"[INFO] Cleaning overlay - keeping {len(valid_refs)} valid refs, removing {len(invalid_refs)} invalid refs")
    
    # First, identify which internal labels will be removed when we remove invalid sections
    removed_internal_labels = set()
    
    # Find all overlay sections and identify which internal labels are in removed sections
    for match in re.finditer(r'^(&(\w+))\s*{', overlay_content, re.MULTILINE):
        ref_name = match.group(2)  # Extract reference name without &
        
        if ref_name in invalid_refs:
            # This section will be removed, so find all internal labels defined within it
            section_start = match.start()
            
            # Find the end of this section by counting braces
            brace_count = 1  # We start with the opening brace
            section_end = match.end()
            for i, char in enumerate(overlay_content[match.end():]):
                if char == '{':
                    brace_count += 1
                elif char == '}':
                    brace_count -= 1
                    if brace_count <= 0:  # This closes the overlay section
                        section_end = match.end() + i + 1
                        break
            
            # Extract the section content and find internal labels
            section_content = overlay_content[section_start:section_end]
            labels_in_section = re.findall(r'(\w+):\s*[\w@-]+\s*{', section_content)
            
            for label in labels_in_section:
                removed_internal_labels.add(label)
                #print(f"[INFO] Internal label '{label}' will be removed with section &{ref_name}")
    
    #print(f"[INFO] Internal labels to be removed: {sorted(list(removed_internal_labels))}")
    
    lines = overlay_content.split('\n')
    result_lines = []
    
    # Track overlay sections to remove
    in_removed_section = False
    removed_section_name = None
    brace_count = 0
    
    for line_num, line in enumerate(lines):
        # Check if this line starts an overlay section (&reference {)
        overlay_section_match = re.match(r'^(&(\w+))\s*{', line.strip())
        
        if overlay_section_match:
            ref_name = overlay_section_match.group(2)  # Extract reference name without &
            
            if ref_name in invalid_refs:
                # Start of a section to remove
                in_removed_section = True
                removed_section_name = ref_name
                brace_count = 1  # We just saw the opening brace
                #print(f"[INFO] Removing overlay section &{ref_name}")
                continue
        
        # If we're in a section being removed, track braces
        if in_removed_section:
            # Count braces to find the end of the section
            brace_count += line.count('{')
            brace_count -= line.count('}')
            
            if brace_count <= 0:
                # End of removed section
                in_removed_section = False
                removed_section_name = None
                brace_count = 0
            
            # Skip this line (it's part of the removed section)
            continue
        
        # Check if this line is an alias pointing to a removed reference
        alias_match = re.match(r'^\s*([^=\s]+)\s*=\s*&(\w+);\s*$', line.strip())
        if alias_match:
            alias_name = alias_match.group(1)
            alias_target = alias_match.group(2)
            
            # Remove aliases pointing to invalid references OR removed internal labels
            if alias_target in invalid_refs or alias_target in removed_internal_labels:
                #print(f"[INFO] Removing alias '{alias_name}' pointing to removed reference '{alias_target}'")
                continue
        
        # Keep this line
        result_lines.append(line)
    
    # Join lines back together
    result_content = '\n'.join(result_lines)
    
    # Apply final cleanup
    result_content = apply_final_cleanup(result_content)
    
    return result_content

def _check_subnodes_recursive(tree, node, ref_name, max_depth=5, current_depth=0):
    """
    Recursively check subnodes for the reference
    """
    if current_depth >= max_depth:
        return False
    
    try:
        subnodes_list = tree.subnodes(node)
        for subnode in subnodes_list:
            # Check subnode label
            if hasattr(subnode, 'label') and subnode.label == ref_name:
                #print(f"[DEBUG] ✓ Found node with label '{ref_name}' at: {subnode.abs_path}")
                return True
            
            # Check subnode name
            if hasattr(subnode, 'name'):
                subnode_name = subnode.name
                if subnode_name == ref_name or subnode_name.startswith(f"{ref_name}@"):
                    #print(f"[DEBUG] ✓ Found node '{ref_name}' by name: {subnode_name} at {subnode.abs_path}")
                    return True
            
            # Recursively check deeper
            if _check_subnodes_recursive(tree, subnode, ref_name, max_depth, current_depth + 1):
                return True
    except:
        pass
    
    return False


ZEPHYR_BOARD_DTSI_SUFFIX = "_zephyr.dtsi"
SDT_DTSI_GLOB = "*.dtsi"
SDT_TOP_DTS = "system-top.dts"
SDT_BOARD_PROP_RE = re.compile(r'board\s*=\s*"([^"]+)"')
SDT_INCLUDE_RE = re.compile(r'#include\s+"([^"]+)"')


def _sdt_included_dtsi_basenames(sdt_folder):
    """Return basenames of .dtsi files #included from system-top.dts."""
    system_top = os.path.join(sdt_folder, SDT_TOP_DTS)
    if not os.path.isfile(system_top):
        return frozenset()

    included = set()
    with open(system_top, "r", encoding="utf-8") as f:
        for line in f:
            match = SDT_INCLUDE_RE.match(line.strip())
            if match:
                included.add(os.path.basename(match.group(1)))
    return frozenset(included)


def _read_text_file(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _write_text_file(path, content):
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def get_board_name_from_sdt(main_tree, sdt_folder=None):
    try:
        root = main_tree["/"]
        board = root.propval("board")
        if isinstance(board, list):
            board = board[0] if board else ""
        if board:
            return board
    except Exception:
        pass

    if sdt_folder:
        system_top = os.path.join(sdt_folder, SDT_TOP_DTS)
        if os.path.isfile(system_top):
            with open(system_top, "r", encoding="utf-8") as f:
                match = SDT_BOARD_PROP_RE.search(f.read())
            if match:
                return match.group(1)

    return None


def discover_zephyr_board_files(sdt_folder, main_tree):
    """
    Discover Zephyr board and user DTS files in the SDT folder.

    SDTGEN (gen_zephyr_board_info) copies files as follows:
      - board (CONFIG.BOARD): {board}.dtsi -> {board}_zephyr.dtsi
      - user (-user_zephyr_dts): copied with its original file name

    Board DTSI is identified as {board}_zephyr.dtsi when the SDT root has a
    matching board property. User Zephyr DTSI is any other .dtsi in the SDT
    folder that is not #included from system-top.dts (for example pl.dtsi or
    pcw.dtsi). SDTGEN rejects a user file whose basename equals
    {board}_zephyr.dtsi, so board and user never share that name.
    """
    board_dtsi = None
    board_name = get_board_name_from_sdt(main_tree, sdt_folder)
    if board_name:
        candidate = os.path.join(sdt_folder, f"{board_name}{ZEPHYR_BOARD_DTSI_SUFFIX}")
        if os.path.isfile(candidate):
            board_dtsi = os.path.abspath(candidate)

    user_zephyr_dtsi = None
    sdt_includes = _sdt_included_dtsi_basenames(sdt_folder)
    for path in sorted(glob.glob(os.path.join(sdt_folder, SDT_DTSI_GLOB))):
        abs_path = os.path.abspath(path)
        if board_dtsi is not None and abs_path == board_dtsi:
            continue
        if os.path.basename(abs_path) in sdt_includes:
            continue
        user_zephyr_dtsi = abs_path
        break

    return {
        "board_dtsi": board_dtsi,
        "user_zephyr_dtsi": user_zephyr_dtsi,
    }


def resolve_sdt_folder(options, sdt):
    """
    Resolve SDT folder from gen_domain_dts assist args[2].

    args[0] = processor
    args[1] = zephyr_dt
    args[2] = SDT folder path (SDTGEN output directory)
    """
    try:
        candidate = options["args"][2]
    except (IndexError, KeyError, TypeError):
        return None

    if not candidate:
        return None

    path = os.path.abspath(candidate)
    if os.path.isdir(path):
        return path

    return None


def generate_board_overlay_from_sdt(sdt, options):
    """
    Generate board.overlay from Zephyr board DTSI files in the SDT folder.

    Called by gen_domain_dts when args[2] is a directory. Legacy callers that
    pass a single board .dts/.dtsi file are handled directly in gen_domain_dts.

    SDT-folder scenarios:
      1. board DTS only -> validate/filter against SDT tree
      2. board + user DTS -> filter board, append user unchanged
      3. user DTS only -> copy user content unchanged
    """
    sdt_folder = resolve_sdt_folder(options, sdt)
    if not sdt_folder:
        print("[WARNING] SDT folder path was not provided to gen_domain_dts. Skipping Zephyr board overlay generation.")
        return False

    if not os.path.isdir(sdt_folder):
        print(f"[WARNING] SDT folder '{sdt_folder}' was not found. Skipping Zephyr board overlay generation.")
        return False

    files = discover_zephyr_board_files(sdt_folder, sdt.tree)
    board_dtsi = files.get("board_dtsi")
    user_zephyr_dtsi = files.get("user_zephyr_dtsi")

    if not board_dtsi and not user_zephyr_dtsi:
        print(f"[INFO] No Zephyr board DTSI found in SDT folder '{sdt_folder}'. Skipping board overlay generation.")
        return False

    overlay_parts = []

    if board_dtsi and user_zephyr_dtsi:
        filtered_board = process_overlay_with_lopper_api(
            _read_text_file(board_dtsi), sdt.tree
        ).rstrip()
        if filtered_board:
            overlay_parts.append(filtered_board)
        overlay_parts.append(_read_text_file(user_zephyr_dtsi).rstrip())
        print(f"[INFO] Generated board.overlay from filtered '{os.path.basename(board_dtsi)}' and user '{os.path.basename(user_zephyr_dtsi)}'.")
    elif board_dtsi:
        overlay_parts.append(
            process_overlay_with_lopper_api(
                _read_text_file(board_dtsi), sdt.tree
            ).rstrip()
        )
        print(f"[INFO] Generated board.overlay from filtered '{os.path.basename(board_dtsi)}'.")
    else:
        overlay_parts.append(_read_text_file(user_zephyr_dtsi).rstrip())
        print(f"[INFO] Generated board.overlay from user DTSI '{os.path.basename(user_zephyr_dtsi)}'.")

    output = "\n\n".join(part for part in overlay_parts if part.strip()) + "\n"
    _write_text_file(os.path.join(sdt.outdir, "board.overlay"), output)
    return True
