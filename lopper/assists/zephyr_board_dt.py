#/*
# * Copyright (C) 2025 Advanced Micro Devices, Inc. All Rights Reserved.
# *
# * Author:
# *     Appana Durga Kedareswara rao <appana.durga.kedareswara.rao@amd.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

import copy
import glob
import os
import re
import sys
import tempfile

from lopper import Lopper, LopperSDT, compile_overlay_standalone, _unwrap_overlay_tree
from lopper.tree import _merge_node_into_tree, _resolve_overlay_fixups, _apply_overlay_symbol_labels

sys.path.append(os.path.dirname(__file__))

OVERLAY_REF_RE = re.compile(r"&\w+\s*{")
ZEPHYR_BOARD_DTSI_SUFFIX = "_zephyr.dtsi"
SDT_DTSI_GLOB = "*.dtsi"
SDT_TOP_DTS = "system-top.dts"
SDT_BOARD_PROP_RE = re.compile(r'board\s*=\s*"([^"]+)"')
SDT_INCLUDE_RE = re.compile(r'#include\s+"([^"]+)"')


def _include_paths(sdt_folder, sdt):
    paths = [p for p in (sdt_folder, getattr(sdt, "outdir", None)) if p and os.path.isdir(p)]
    return " ".join(paths)


def _work_dir(sdt):
    tmpdir = getattr(sdt, "tmpdir", None) if sdt else None
    if tmpdir and os.path.isdir(tmpdir):
        return tmpdir
    outdir = getattr(sdt, "outdir", None) if sdt else None
    return tempfile.mkdtemp(prefix="zephyr_board_", dir=outdir or None)


def _fragment_overlay_to_real(overlay_tree, main_tree):
    """Map plugin fragment __overlay__ prefixes to resolved target abs_paths."""
    fragment_to_label = {}
    fixups_nodes = overlay_tree.nodes("/__fixups__")
    fixups_node = fixups_nodes[0] if fixups_nodes else None
    if fixups_node:
        for prop in fixups_node.__props__.values():
            refs = prop.value if isinstance(prop.value, list) else [prop.value]
            for ref in refs:
                if not isinstance(ref, str) or ":target:" not in ref:
                    continue
                frag_path = ref.split(":")[0]
                if frag_path.count("/") == 1:
                    fragment_to_label[frag_path] = prop.name

    mapping = {}
    for frag_path, label in fragment_to_label.items():
        target_nodes = main_tree.lnodes(label, exact=True)
        if target_nodes:
            mapping[f"{frag_path}/__overlay__"] = target_nodes[0].abs_path
    return mapping


def _rewrite_fragment_path(path, fragment_overlay_to_real):
    if not isinstance(path, str) or not path:
        return None
    for frag_prefix, real_prefix in fragment_overlay_to_real.items():
        if path.startswith(frag_prefix):
            return real_prefix + path[len(frag_prefix):]
    return path


def _merge_root_plugin_nodes(overlay_tree, main_tree, fragment_overlay_to_real):
    """Merge root /aliases and /chosen from a plugin-compiled overlay tree."""
    for node_name in ("aliases", "chosen"):
        try:
            ov_node = overlay_tree[f"/{node_name}"]
        except (KeyError, Exception):
            continue

        try:
            base_node = main_tree[f"/{node_name}"]
        except (KeyError, Exception):
            _merge_node_into_tree(main_tree, ov_node)
            continue

        for prop_name, prop in ov_node.__props__.items():
            if node_name == "aliases":
                raw_values = prop.value if isinstance(prop.value, list) else [prop.value]
                rewritten = [
                    _rewrite_fragment_path(value, fragment_overlay_to_real)
                    for value in raw_values
                ]
                if not rewritten or not all(rewritten):
                    continue
                try:
                    main_tree[rewritten[0]]
                except (KeyError, Exception):
                    continue
                prop_value = rewritten
            else:
                prop_value = prop.value

            new_prop = copy.deepcopy(prop)
            new_prop.value = prop_value
            new_prop.node = base_node
            base_node.__props__[prop_name] = new_prop
            try:
                new_prop.resolve()
            except Exception:
                pass


def _merge_plugin_overlay(content, main_tree, sdt, sdt_folder, work_dir):
    """Merge &label { } board fragments via plugin compile + unwrap (optional HW safe)."""
    dtso_path = os.path.join(work_dir, "board_zephyr.dtso")
    with open(dtso_path, "w", encoding="utf-8") as fh:
        fh.write(content)

    overlay_tree = compile_overlay_standalone(
        dtso_path,
        include_paths=_include_paths(sdt_folder, sdt),
        tmpdir=work_dir,
        save_temps=True,
    )
    if overlay_tree is None:
        return False

    nodes, fixups, local_fixups, symbol_labels = _unwrap_overlay_tree(overlay_tree, main_tree)
    for node in nodes:
        _merge_node_into_tree(main_tree, node)
    if fixups or local_fixups:
        # One call resolves both global and in-overlay (__local_fixups__)
        # references, so this path can't silently drop the local ones.
        _resolve_overlay_fixups(main_tree, fixups, local_fixups)
    if symbol_labels:
        # Carry overlay node labels (/__symbols__) onto the merged nodes.
        _apply_overlay_symbol_labels(main_tree, symbol_labels)

    fragment_map = _fragment_overlay_to_real(overlay_tree, main_tree)
    _merge_root_plugin_nodes(overlay_tree, main_tree, fragment_map)

    labels = sorted({n.label for n in nodes if getattr(n, "label", None)})
    print(
        f"[INFO] Board overlay merged into domain tree; "
        f"kept {len(labels)} fragment(s): {', '.join(labels) or 'none'}."
    )
    return True


def _merge_include_fragment(content, main_tree, sdt, work_dir, include_paths):
    """Merge / { ... }; include-style fragments via domain + fragment dt_compile."""
    base_path = os.path.join(work_dir, "_domain_base.dts")
    combined_path = os.path.join(work_dir, "_combined.dts")

    main_tree.resolve()
    main_tree.print(base_path)

    with open(combined_path, "w", encoding="utf-8") as out:
        with open(base_path, encoding="utf-8") as base:
            out.write(base.read())
        out.write("\n")
        out.write(content)

    result = Lopper.dt_compile(
        combined_path,
        [],
        include_paths or work_dir,
        force_overwrite=True,
        outdir=work_dir,
        save_temps=True,
        permissive=False,
    )
    if not result[0]:
        return False

    # The fragment brings in properties the domain tree never had, and the
    # loader below will not learn them: a LopperSDT constructed here has no
    # schema set, and setup() only learns when it has been asked to. Without
    # this their type is guessed from the property name, which turns a
    # vendor prefixed boolean into an empty string. Compiling the combined
    # file has just produced a schema that covers them, so fold it in before
    # the tree is loaded and typed.
    import lopper.schema
    lopper.schema._schema_manager.merge_schema( result[1] )

    loader = LopperSDT(combined_path)
    loader.tmpdir = work_dir
    loader.setup(combined_path, [], include_paths or work_dir, force=True)
    if sdt is not None:
        sdt.tree = loader.tree
    else:
        main_tree.__dict__.update(loader.tree.__dict__)
    loader.tree.resolve()
    print("[INFO] Board include fragment merged into domain tree (combined DTS).")
    return True


def merge_board_overlay_content(overlay_content, main_tree, sdt=None, sdt_folder=None):
    """
    Merge board/user .dtsi into the domain tree — combined DTS, no overlay file.

    Uses lopper core only:
      - &label { } content: compile_overlay_standalone, _unwrap_overlay_tree,
        _merge_node_into_tree, _resolve_overlay_fixups
      - / { ... }; only content: domain + fragment concat via Lopper.dt_compile
    """
    if not overlay_content.strip():
        return True

    work_dir = _work_dir(sdt)
    includes = _include_paths(sdt_folder, sdt)

    if OVERLAY_REF_RE.search(overlay_content):
        if not _merge_plugin_overlay(overlay_content, main_tree, sdt, sdt_folder, work_dir):
            print("[ERROR] plugin overlay merge failed.")
            return False
        main_tree.resolve()
        return True

    if not _merge_include_fragment(overlay_content, main_tree, sdt, work_dir, includes):
        print("[ERROR] include-fragment merge failed.")
        return False
    return True


def get_board_name_from_sdt(main_tree, sdt_folder=None):
    try:
        board = main_tree["/"].propval("board")
        if isinstance(board, list):
            board = board[0] if board else ""
        if board:
            return board
    except Exception:
        pass

    if sdt_folder:
        system_top = os.path.join(sdt_folder, SDT_TOP_DTS)
        if os.path.isfile(system_top):
            with open(system_top, "r", encoding="utf-8") as fh:
                match = SDT_BOARD_PROP_RE.search(fh.read())
            if match:
                return match.group(1)
    return None


def _sdt_included_dtsi_basenames(sdt_folder):
    system_top = os.path.join(sdt_folder, SDT_TOP_DTS)
    if not os.path.isfile(system_top):
        return frozenset()
    included = set()
    with open(system_top, "r", encoding="utf-8") as fh:
        for line in fh:
            match = SDT_INCLUDE_RE.match(line.strip())
            if match:
                included.add(os.path.basename(match.group(1)))
    return frozenset(included)


def discover_zephyr_board_files(sdt_folder, main_tree):
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

    return {"board_dtsi": board_dtsi, "user_zephyr_dtsi": user_zephyr_dtsi}


def resolve_sdt_folder(options, sdt):
    try:
        candidate = options["args"][2]
    except (IndexError, KeyError, TypeError):
        return None
    if not candidate:
        return None
    path = os.path.abspath(candidate)
    return path if os.path.isdir(path) else None


def merge_board_overlay_from_sdt(sdt, options):
    sdt_folder = resolve_sdt_folder(options, sdt)
    if not sdt_folder:
        print("[WARNING] SDT folder not provided; skipping Zephyr board overlay merge.")
        return False

    files = discover_zephyr_board_files(sdt_folder, sdt.tree)
    board_dtsi = files.get("board_dtsi")
    user_zephyr_dtsi = files.get("user_zephyr_dtsi")

    if not board_dtsi and not user_zephyr_dtsi:
        print(f"[INFO] No Zephyr board DTSI in '{sdt_folder}'; skipping overlay merge.")
        return False

    for path in (p for p in (board_dtsi, user_zephyr_dtsi) if p):
        with open(path, encoding="utf-8") as fh:
            if not merge_board_overlay_content(fh.read(), sdt.tree, sdt=sdt, sdt_folder=sdt_folder):
                return False

    print(f"[INFO] Merged Zephyr board content into domain tree from SDT folder.")
    return True


generate_board_overlay_from_sdt = merge_board_overlay_from_sdt
process_board_overlay = merge_board_overlay_content
