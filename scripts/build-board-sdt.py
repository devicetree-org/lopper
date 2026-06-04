#!/usr/bin/env python3
# Copyright (c) 2026 Advanced Micro Devices, Inc. All Rights Reserved.
# Author: Bruce Ashfield <bruce.ashfield@amd.com>
# SPDX-License-Identifier: BSD-3-Clause
"""
Drive the full sdt-from-linux pipeline for one shipped board:
  source.yaml → cpp + dtc → compose_devices → assemble_sdt
                                                     ↓
                                              system-top.dts

This is the canonical entry point for running the pipeline on a
board configured under lopper/data/boards/<board>/. It reads the
board's source.yaml, preprocesses the upstream Linux DT (and the
upstream Zephyr DT, when the board has a zephyr: block), invokes
the compose_devices Lopper assist to build the inventory YAML,
then invokes the assemble_sdt Lopper assist to produce the SDT.

Users should not need to invoke cpp or dtc themselves — this
script encodes the per-board cpp include paths, the dtc_force
flag for boards whose Zephyr side needs it, and the chained
Lopper invocations. The integration tests in
tests/test_sdt_from_linux.py also drive the pipeline through this
script so there is one canonical implementation of the
orchestration.

Usage:
    scripts/build-board-sdt.py --board <board-name> [options]

Options:
    -b, --board NAME       Board configured under
                           lopper/data/boards/<NAME>/. Required.
    -o, --output-dir DIR   Where to write artifacts. Default:
                           ./<board>-build/. Created if missing.
    --no-zephyr            Skip the Zephyr-side flatten and merge
                           even when the board's source.yaml has a
                           zephyr: block. Produces a Linux-only SDT.
    --no-augment           Pass --no-augment to compose_devices,
                           disabling the per-board augment overlay.
                           Mostly useful for diagnostic runs.
    -v, --verbose          Print each cpp/dtc/lopper invocation as
                           it runs.

Outputs (under <output-dir>):
    <board>-linux.flat.dts        preprocessed Linux DT
    <board>-zephyr.flat.dts       preprocessed Zephyr DT (if --no-zephyr not set)
    <board>-devices.yaml          compose_devices output
    <board>-system-top.dts        assemble_sdt output (the SDT)
    <board>-*.pp.dts              intermediate cpp output (kept for inspection)

Example:
    scripts/build-board-sdt.py --board versal-vck190 -o /tmp/vck190-build
    scripts/build-board-sdt.py --board imx8mm-evk    -o /tmp/imx8mm-build
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LOPPER_PY = REPO_ROOT / 'lopper.py'
BOARDS_ROOT = REPO_ROOT / 'lopper' / 'data' / 'boards'


class PipelineError(RuntimeError):
    """Raised when a stage of the pipeline fails."""


def _print_cmd(cmd, verbose):
    if verbose:
        print('  $', ' '.join(str(c) for c in cmd), file=sys.stderr)


def _load_source_yaml(board_dir):
    """Parse lopper/data/boards/<board>/source.yaml."""
    src = board_dir / 'source.yaml'
    if not src.is_file():
        raise PipelineError(f"board source.yaml missing: {src}")
    try:
        from ruamel.yaml import YAML
        yaml = YAML(typ='safe')
        with open(src) as fh:
            return yaml.load(fh) or {}
    except Exception as e:
        raise PipelineError(f"failed to parse {src}: {e}")


def _flatten(block, board_name, label, outdir, verbose):
    """Run cpp + dtc on one side (linux or zephyr) of the board.

    The board's source.yaml declares the input .dts file (relative
    to the repo root) and the cpp include search path. A
    `dtc_force: true` on the block adds `-f` to the dtc invocation —
    needed when the input tree has unresolved phandles that aren't
    in scope for inventory extraction (e.g. the i.MX 8MM Zephyr
    side's pinctrl phandles into the unvendored hal_nxp module).
    """
    input_path = REPO_ROOT / block['input']
    include_paths = [REPO_ROOT / p for p in block['include_paths']]
    if not input_path.is_file():
        raise PipelineError(f"{label} input DT missing: {input_path}")

    pp_dts = outdir / f'{board_name}-{label}.pp.dts'
    flat_dts = outdir / f'{board_name}-{label}.flat.dts'

    cpp_cmd = ['cpp', '-nostdinc', '-undef', '-x', 'assembler-with-cpp']
    for ip in include_paths:
        cpp_cmd.extend(['-I', str(ip)])
    cpp_cmd.extend([str(input_path), '-o', str(pp_dts)])
    _print_cmd(cpp_cmd, verbose)
    result = subprocess.run(cpp_cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise PipelineError(
            f"cpp failed on {label} side of board '{board_name}':\n"
            f"{result.stderr}")

    dtc_cmd = ['dtc', '-I', 'dts', '-O', 'dts']
    if block.get('dtc_force'):
        dtc_cmd.append('-f')
    dtc_cmd.extend(['-o', str(flat_dts), str(pp_dts)])
    _print_cmd(dtc_cmd, verbose)
    result = subprocess.run(dtc_cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise PipelineError(
            f"dtc failed on {label} side of board '{board_name}':\n"
            f"{result.stderr}")

    return flat_dts


def _run_compose(linux_flat, board_name, outdir, zephyr_flat, no_augment,
                 verbose):
    """Invoke lopper.py with the compose_devices assist."""
    devices_yaml = outdir / f'{board_name}-devices.yaml'
    lop_main_out = outdir / f'{board_name}-compose-lopout.dts'

    cmd = [sys.executable, str(LOPPER_PY), '-f',
           str(linux_flat), str(lop_main_out),
           '--', 'compose_devices',
           '--board', board_name,
           '-o', str(devices_yaml)]
    if zephyr_flat is not None:
        cmd.extend(['--zephyr-dt', str(zephyr_flat)])
    if no_augment:
        cmd.append('--no-augment')

    _print_cmd(cmd, verbose)
    result = subprocess.run(cmd, cwd=str(REPO_ROOT),
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise PipelineError(
            f"compose_devices failed for board '{board_name}':\n"
            f"--- stdout ---\n{result.stdout}\n"
            f"--- stderr ---\n{result.stderr}\n")
    if not devices_yaml.is_file():
        raise PipelineError(
            f"compose_devices reported success but produced no output: {devices_yaml}")
    return devices_yaml


def _run_assemble(devices_yaml, linux_flat, board_name, outdir, verbose):
    """Invoke lopper.py with the assemble_sdt assist.

    assemble_sdt ignores the main sdt input, but lopper.py still
    requires something to load. We pass the Linux flat .dts as a
    placeholder — it's already on disk and assemble_sdt won't read it.
    """
    sdt = outdir / f'{board_name}-system-top.dts'
    lop_main_out = outdir / f'{board_name}-assemble-lopout.dts'

    cmd = [sys.executable, str(LOPPER_PY), '-f',
           str(linux_flat), str(lop_main_out),
           '--', 'assemble_sdt',
           '--devices', str(devices_yaml),
           '-o', str(sdt)]
    _print_cmd(cmd, verbose)
    result = subprocess.run(cmd, cwd=str(REPO_ROOT),
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise PipelineError(
            f"assemble_sdt failed for board '{board_name}':\n"
            f"--- stdout ---\n{result.stdout}\n"
            f"--- stderr ---\n{result.stderr}\n")
    if not sdt.is_file():
        raise PipelineError(
            f"assemble_sdt reported success but produced no output: {sdt}")
    return sdt


def _have_tools():
    missing = [t for t in ('cpp', 'dtc') if shutil.which(t) is None]
    return missing


def build_board(board_name, output_dir, no_zephyr=False, no_augment=False,
                verbose=False):
    """Run the four-stage pipeline end-to-end for one board.

    Returns a dict of artifact paths the script produced.
    """
    missing = _have_tools()
    if missing:
        raise PipelineError(
            f"required toolchain not on PATH: {', '.join(missing)}")

    board_dir = BOARDS_ROOT / board_name
    if not board_dir.is_dir():
        raise PipelineError(
            f"board '{board_name}' not found under {BOARDS_ROOT}/")

    source = _load_source_yaml(board_dir)

    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    artifacts = {'output_dir': output_dir}

    # Stage 1: flatten Linux side.
    if 'linux' not in source:
        raise PipelineError(
            f"board '{board_name}' source.yaml has no 'linux' block")
    linux_flat = _flatten(source['linux'], board_name, 'linux',
                          output_dir, verbose)
    artifacts['linux_flat'] = linux_flat

    # Stage 2: flatten Zephyr side (optional).
    zephyr_flat = None
    if 'zephyr' in source and not no_zephyr:
        zephyr_flat = _flatten(source['zephyr'], board_name, 'zephyr',
                               output_dir, verbose)
        artifacts['zephyr_flat'] = zephyr_flat

    # Stage 3: compose_devices.
    devices_yaml = _run_compose(linux_flat, board_name, output_dir,
                                zephyr_flat=zephyr_flat,
                                no_augment=no_augment, verbose=verbose)
    artifacts['devices_yaml'] = devices_yaml

    # Stage 4: assemble_sdt.
    sdt = _run_assemble(devices_yaml, linux_flat, board_name, output_dir,
                        verbose=verbose)
    artifacts['system_top_dts'] = sdt

    return artifacts


def main():
    p = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('-b', '--board', required=True,
                   help="board configured under lopper/data/boards/<NAME>/")
    p.add_argument('-o', '--output-dir', default=None,
                   help="where to write artifacts (default: ./<board>-build/)")
    p.add_argument('--no-zephyr', action='store_true',
                   help="skip the Zephyr-side flatten and merge even when "
                        "the board's source.yaml has a zephyr: block")
    p.add_argument('--no-augment', action='store_true',
                   help="pass --no-augment to compose_devices (disables the "
                        "per-board augment overlay)")
    p.add_argument('-v', '--verbose', action='store_true',
                   help="print each cpp/dtc/lopper invocation")
    args = p.parse_args()

    output_dir = args.output_dir or (Path.cwd() / f'{args.board}-build')

    try:
        artifacts = build_board(args.board, output_dir,
                                no_zephyr=args.no_zephyr,
                                no_augment=args.no_augment,
                                verbose=args.verbose)
    except PipelineError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"board:        {args.board}")
    print(f"output dir:   {artifacts['output_dir']}")
    print(f"linux flat:   {artifacts['linux_flat'].name}")
    if 'zephyr_flat' in artifacts:
        print(f"zephyr flat:  {artifacts['zephyr_flat'].name}")
    print(f"devices yaml: {artifacts['devices_yaml'].name}")
    print(f"system-top:   {artifacts['system_top_dts'].name}")
    print()
    print(f"Generated SDT: {artifacts['system_top_dts']}")


if __name__ == '__main__':
    main()
