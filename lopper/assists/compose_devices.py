#/*
# * Copyright (c) 2026 Advanced Micro Devices, Inc. All Rights Reserved.
# *
# * Author:
# *       Bruce Ashfield <bruce.ashfield@amd.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

"""
Compose a device inventory YAML from a Linux device tree plus
optional per-board augmentation.

This is the Linux-DT-side counterpart to sdt_devices. It accepts a
pre-flattened Linux device tree as input (the lopper assist framework
expects the input sdt up front; the cpp + dtc flattening step is the
caller's responsibility) and runs the same DevicesCore extraction
sdt_devices uses, then — once M9 lands — overlays the per-board
augmentation YAML that fills in what the Linux view can't carry
(additional CPU clusters, TCM/OCM, reserved-memory carve-outs, etc.).

For M4 (this iteration) the overlay step is a no-op; behaviour is
identical to running sdt_devices on the same flattened tree. The
distinct assist exists so user-facing tooling has a stable name, the
--board flag selects per-board configuration, and the overlay seam is
in place for M9 to wire into without API churn.

Usage:
    lopper <flat-linux.dts> - -- compose_devices \\
        --board <board-name> -o <output.yaml>

    # Ad-hoc (no board config, just extract from the given tree):
    lopper <flat-linux.dts> - -- compose_devices -o <output.yaml>

Options:
    -v, --verbose          Enable verbose output
    -b, --board NAME       Use lopper/data/boards/<NAME>/ as the board
                           reference; reads augment.yaml from there.
                           Optional — if omitted, behaves like
                           sdt_devices on the input tree.
    -o, --output PATH      Output YAML path
    --include-clocks       Include clock nodes in the inventory
    --include-infrastructure CSV
                           Comma-separated infrastructure categories to
                           include (see sdt_devices for the list)

Pipeline (when invoked with --board):
    1. Validate that the input tree's root compatible matches the
       board's expected_root_compatible (sanity check that the caller
       fed us the file source.yaml points to).
    2. Run DevicesCore extraction (memory split, PM-ID decode, aliases,
       bootph, SoC identity tag — same machinery as sdt_devices).
    3. (M9) Overlay augment.yaml from the board directory.
    4. Emit openamp,domain-v1,devices YAML.
"""

import getopt
import logging
import os
import re
import sys

from lopper.yaml import LopperYAML
import lopper
import lopper.log

from lopper.assists._devices_core import DeviceCategory, DevicesCore

lopper.log._init(__name__)


# Repo root is two levels up from this file (lopper/assists/ → repo/).
# Boards data lives under <repo>/lopper/data/boards/.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
_BOARDS_ROOT = os.path.join(_REPO_ROOT, 'lopper', 'data', 'boards')


def is_compat(node, compat_string_to_test):
    """Assist-framework dispatch for compose_devices."""
    if re.search("compose-devices,compose-v1", compat_string_to_test):
        return compose_devices
    if re.search("module,compose_devices", compat_string_to_test):
        return compose_devices
    return ""


def usage():
    print("""
   Usage: compose_devices [OPTION]

      -v, --verbose          Enable verbose output
      -b, --board NAME       Use lopper/data/boards/<NAME>/ as the
                             board reference; reads augment.yaml from
                             there. Optional.
      -o, --output PATH      Output YAML path
      --include-clocks       Include clock nodes in the inventory
      --include-infrastructure CSV
                             Comma-separated infrastructure categories
                             to include (interrupt, smmu, power, slcr,
                             …; see sdt_devices --list-infrastructure)

   Compose a device inventory YAML from a Linux device tree, with the
   same mining enhancements sdt_devices uses (PM-ID decode, multi-range
   memory split, aliases pass-through, bootph preservation, SoC family
   tagging). When --board is supplied, additionally overlays the
   per-board augment.yaml (M9; currently a no-op stub).

   Example:
      lopper flat-linux.dts - -- compose_devices \\
          --board versal-vck190 -o /tmp/devices.yaml
    """)


def _load_board_source(board_name):
    """Load lopper/data/boards/<name>/source.yaml, return its dict."""
    path = os.path.join(_BOARDS_ROOT, board_name, 'source.yaml')
    if not os.path.isfile(path):
        raise RuntimeError(f"board {board_name!r}: source.yaml not found at {path}")
    try:
        from ruamel.yaml import YAML
        yaml = YAML(typ='safe')
        with open(path) as fh:
            return yaml.load(fh) or {}
    except Exception as e:
        raise RuntimeError(f"board {board_name!r}: failed to parse {path}: {e}")


def _sanity_check_board(sdt, board_data, board_name):
    """Confirm the input tree's root compatible matches what the board
    config expects. Warns on mismatch but does not abort — the user may
    legitimately be running an evolved DT against a stable board config.
    """
    expected = ((board_data.get('board') or {})
                .get('expected_root_compatible') or [])
    if not expected:
        return

    root_compat = []
    try:
        root = sdt.tree["/"]
        rc = root.propval("compatible") or []
        root_compat = [str(c).strip() for c in rc if c and str(c).strip()]
    except Exception:
        pass

    overlap = [c for c in expected if c in root_compat]
    if overlap:
        lopper.log._info(
            f"compose_devices: board {board_name!r} root compatible match: {overlap}")
    else:
        lopper.log._warning(
            f"compose_devices: board {board_name!r} expected one of "
            f"{expected}; input tree's root compatible is {root_compat} "
            f"— proceeding, but verify you're feeding the right file")


def _apply_board_augment(tree, board_name):
    """M9 hook — merge lopper/data/boards/<name>/augment.yaml into the
    generated tree. Currently a no-op stub; the file is read only to
    surface a friendly log line, so the seam is observably present.
    """
    if not board_name:
        return
    augment_path = os.path.join(_BOARDS_ROOT, board_name, 'augment.yaml')
    if not os.path.isfile(augment_path):
        return
    lopper.log._info(
        f"compose_devices: board augment ({augment_path}) present; "
        f"overlay handler not yet implemented (M9) — ignored")


def compose_devices(tgt_node, sdt, options):
    """Entry point. Same shape as sdt_devices for assist-framework parity."""
    try:
        verbose = options['verbose']
    except KeyError:
        verbose = 0
    try:
        args = options['args']
    except KeyError:
        args = []

    try:
        opts, _ = getopt.getopt(
            args,
            "hvb:o:",
            ["help", "verbose", "board=", "output=",
             "include-clocks", "include-infrastructure="])
    except getopt.GetoptError as e:
        lopper.log._error(f"compose_devices: invalid option: {e}")
        usage()
        return False

    board_name = None
    output_file = None
    include_clocks = False
    include_infrastructure = []

    for o, a in opts:
        if o in ('-h', '--help'):
            usage()
            return True
        elif o in ('-v', '--verbose'):
            verbose += 1
        elif o in ('-b', '--board'):
            board_name = a
        elif o in ('-o', '--output'):
            output_file = a
        elif o == '--include-clocks':
            include_clocks = True
        elif o == '--include-infrastructure':
            include_infrastructure = [c.strip().lower() for c in a.split(',')]

    if verbose > 1:
        lopper.log._level(logging.DEBUG, __name__)
    elif verbose > 0:
        lopper.log._level(logging.INFO, __name__)

    board_data = {}
    if board_name:
        try:
            board_data = _load_board_source(board_name)
        except RuntimeError as e:
            lopper.log._error(str(e))
            return False
        _sanity_check_board(sdt, board_data, board_name)

    # Run extraction. Domain name reflects intent: this came from the
    # Linux DT compose path, not from an SDT.
    domain_name = (board_data.get('board') or {}).get('name', 'composed_devices')
    lopper.log._info(f"compose_devices: extracting inventory as domain '{domain_name}'")

    generator = DevicesCore(sdt,
                            include_clocks=include_clocks,
                            include_infrastructure=include_infrastructure)
    tree = generator.generate_domain(domain_name=domain_name)

    _apply_board_augment(tree, board_name)

    # Output file resolution mirrors sdt_devices.
    if not output_file:
        if hasattr(sdt, 'output_file') and sdt.output_file:
            output_file = sdt.output_file
        else:
            lopper.log._error("compose_devices: no output file specified (use -o)")
            usage()
            return False
    if not output_file.endswith('.yaml'):
        base, _ = os.path.splitext(output_file)
        output_file = base + '.yaml'

    out_dir = os.path.dirname(output_file)
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir, exist_ok=True)

    lopper.log._info(f"compose_devices: writing inventory to {output_file}")
    try:
        config = getattr(sdt, 'config', {})
        LopperYAML(None, tree, config=config).to_yaml(output_file)
    except Exception as e:
        lopper.log._error(f"compose_devices: failed to write {output_file}: {e}")
        return False

    lopper.log._info(f"compose_devices: success ({output_file})")
    return True
