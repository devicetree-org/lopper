#!/usr/bin/env python3
# Copyright (c) 2026 Advanced Micro Devices, Inc. All Rights Reserved.
# Author: Bruce Ashfield <bruce.ashfield@amd.com>
# SPDX-License-Identifier: BSD-3-Clause
"""
Drive the full sdt-from-linux pipeline for one shipped board:
  source.yaml → cpp + dtc → compose_non_linux → assemble_sdt
                                                       ↓
                                                system-top.dts

The Linux DT is the base of the SDT, so its devices come along
verbatim. compose_non_linux extracts the co-processor side from
the Zephyr DT and the per-board domains.yaml into a rich-property
non-linux YAML. assemble_sdt loads the Linux DT as the base tree
and overlays the non-linux content on top, producing the SDT.

This is the canonical entry point for running the pipeline on a
board configured under lopper/data/boards/<board>/. It reads the
board's source.yaml, preprocesses the upstream Linux DT (and the
upstream Zephyr DT, when the board has a zephyr: block), then
chains the two Lopper assists.

Usage:
    scripts/build-board-sdt.py --board <board-name> [options]

Options:
    -b, --board NAME       Board configured under
                           lopper/data/boards/<NAME>/. Required.
    -o, --output-dir DIR   Where to write artifacts. Default:
                           ./<board>-build/. Created if missing.
    --no-zephyr            Skip the Zephyr-side flatten and merge
                           even when the board's source.yaml has a
                           zephyr: block. Produces a Linux-only SDT
                           (assemble_sdt runs with no non-linux YAML
                           input — board domains.yaml content is dropped).
    --domains PATH         User's per-deployment domains.yaml overlay
                           (deep-merged on top of the shipped per-board
                           template at lopper/data/boards/<board>/
                           domains.yaml). The user maintains a small
                           overlay file with their specific edits;
                           git pull refreshes the template without
                           disturbing their copy.
    --no-template          Skip the shipped per-board template; use
                           only --domains (or nothing) as the source
                           of integration declarations. Diagnostic /
                           bring-your-own-template use.
    -I, --input-dirs DIR   Lopper include directory, forwarded to
                           every lopper invocation in the run. Used to
                           find SoC-facts YAML you keep in your own
                           repo at <DIR>/data/socs/ (searched before
                           the shipped lopper/data/socs/). Repeatable.
    -v, --verbose          Print each cpp/dtc/lopper invocation as
                           it runs.

Outputs (under <output-dir>):
    <board>-linux.flat.dts        preprocessed Linux DT
    <board>-zephyr.flat.dts       preprocessed Zephyr DT (if --no-zephyr not set)
    <board>-non-linux.yaml        compose_non_linux output (if Zephyr DT present)
    <board>-system-top.dts        assemble_sdt output (the SDT)
    <board>-sdt-devices.yaml      sdt_devices enumeration of every node in
                                  the assembled SDT — the "vocabulary" a
                                  user-written domains.yaml can glob against
    <board>-sdt-domains.yaml      sdt_domains starter — one domain per
                                  cpus,cluster, partitioned by
                                  lopper-source tag; edit-then-use
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


def _inc_args(include_dirs):
    """Expand a list of include dirs into repeated `-I <dir>` lopper args.

    These reach the assists as `sdt.load_paths`, so e.g. sdt_devices
    can find SoC-facts YAML the user keeps in their own repo under
    `<dir>/data/socs/`.
    """
    args = []
    for d in (include_dirs or []):
        args.extend(['-I', str(d)])
    return args


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


def _run_compose_non_linux(zephyr_flat, linux_flat, board_name, outdir,
                           overlay_path, no_template, include_dirs, verbose):
    """Invoke lopper.py with the compose_non_linux assist.

    Takes the Zephyr DT as the main lopper input; passes the Linux DT
    via --linux-dt for address dedup against the eventual base tree.
    The board's shipped domains.yaml template is auto-located via
    --board; an optional user-supplied overlay file gets layered on
    top via --domains.
    """
    non_linux_yaml = outdir / f'{board_name}-non-linux.yaml'
    lop_main_out = outdir / f'{board_name}-compose-non-linux-lopout.dts'

    cmd = [sys.executable, str(LOPPER_PY),
           '-O', str(outdir), '--permissive', '-f',
           *_inc_args(include_dirs),
           str(zephyr_flat), str(lop_main_out),
           '--', 'compose_non_linux',
           '--linux-dt', str(linux_flat),
           '--board', board_name,
           '-o', str(non_linux_yaml)]
    if overlay_path:
        cmd.extend(['--domains', str(overlay_path)])
    if no_template:
        cmd.append('--no-template')

    _print_cmd(cmd, verbose)
    result = subprocess.run(cmd, cwd=str(REPO_ROOT),
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise PipelineError(
            f"compose_non_linux failed for board '{board_name}':\n"
            f"--- stdout ---\n{result.stdout}\n"
            f"--- stderr ---\n{result.stderr}\n")
    if not non_linux_yaml.is_file():
        raise PipelineError(
            f"compose_non_linux reported success but produced no output: "
            f"{non_linux_yaml}")
    return non_linux_yaml


def _run_sdt_devices(sdt, board_name, outdir, include_dirs, verbose):
    """Enumerate every device in the assembled SDT into a YAML inventory.

    Output feeds the glob-driven domains workflow: a user writes a
    domains.yaml with `dev: "*pattern*"` access entries and loads
    sdt-devices.yaml as a parent so the patterns expand against the
    full enumeration.

    Include dirs (-I) reach the SoC-facts loader, so PM-ID decode can
    use a SoC YAML the user keeps in their own repo.
    """
    devices_yaml = outdir / f'{board_name}-sdt-devices.yaml'
    lop_main_out = outdir / f'{board_name}-sdt-devices-lopout.dts'

    cmd = [sys.executable, str(LOPPER_PY),
           '-O', str(outdir), '--permissive', '-f',
           *_inc_args(include_dirs),
           str(sdt), str(lop_main_out),
           '--', 'sdt_devices',
           '-o', str(devices_yaml)]
    _print_cmd(cmd, verbose)
    result = subprocess.run(cmd, cwd=str(REPO_ROOT),
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise PipelineError(
            f"sdt_devices failed for board '{board_name}':\n"
            f"--- stdout ---\n{result.stdout}\n"
            f"--- stderr ---\n{result.stderr}\n")
    if not devices_yaml.is_file():
        raise PipelineError(
            f"sdt_devices reported success but produced no output: {devices_yaml}")
    return devices_yaml


def _run_sdt_domains(sdt, board_name, outdir, include_dirs, verbose):
    """Produce a starter domains.yaml from the assembled SDT.

    Walks the SDT for cpus,cluster nodes and partitions devices /
    memory across one domain per cluster, using the lopper-source
    tags assemble_sdt attached during assembly. The result is a
    user-editable starting point — not a final partition.
    """
    domains_yaml = outdir / f'{board_name}-sdt-domains.yaml'
    lop_main_out = outdir / f'{board_name}-gen-domains-lopout.dts'

    cmd = [sys.executable, str(LOPPER_PY),
           '-O', str(outdir), '--permissive', '-f',
           *_inc_args(include_dirs),
           str(sdt), str(lop_main_out),
           '--', 'sdt_domains',
           '-o', str(domains_yaml)]
    _print_cmd(cmd, verbose)
    result = subprocess.run(cmd, cwd=str(REPO_ROOT),
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise PipelineError(
            f"sdt_domains failed for board '{board_name}':\n"
            f"--- stdout ---\n{result.stdout}\n"
            f"--- stderr ---\n{result.stderr}\n")
    if not domains_yaml.is_file():
        raise PipelineError(
            f"sdt_domains reported success but produced no output: "
            f"{domains_yaml}")
    return domains_yaml


def _run_assemble(linux_flat, non_linux_yaml, board_name, outdir,
                  include_dirs, verbose):
    """Invoke lopper.py with the assemble_sdt assist.

    assemble_sdt ignores the main sdt input, but lopper.py still
    requires something to load. We pass the Linux flat .dts as a
    placeholder — assemble_sdt re-loads it via --linux-dt.
    """
    sdt = outdir / f'{board_name}-system-top.dts'
    lop_main_out = outdir / f'{board_name}-assemble-lopout.dts'

    cmd = [sys.executable, str(LOPPER_PY),
           '-O', str(outdir), '--permissive', '-f',
           *_inc_args(include_dirs),
           str(linux_flat), str(lop_main_out),
           '--', 'assemble_sdt',
           '--linux-dt', str(linux_flat),
           '-o', str(sdt)]
    if non_linux_yaml is not None:
        cmd.extend(['--non-linux', str(non_linux_yaml)])

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


def build_board(board_name, output_dir, no_zephyr=False, no_template=False,
                domains_overlay=None, include_dirs=None, verbose=False):
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

    # Stage 3: compose_non_linux (only when there's a Zephyr DT to
    # extract from; otherwise the SDT is Linux-only and assemble_sdt
    # runs without a non-linux overlay).
    non_linux_yaml = None
    if zephyr_flat is not None:
        non_linux_yaml = _run_compose_non_linux(
            zephyr_flat, linux_flat, board_name, output_dir,
            overlay_path=domains_overlay, no_template=no_template,
            include_dirs=include_dirs, verbose=verbose)
        artifacts['non_linux_yaml'] = non_linux_yaml

    # Stage 4: assemble_sdt.
    sdt = _run_assemble(linux_flat, non_linux_yaml, board_name, output_dir,
                        include_dirs=include_dirs, verbose=verbose)
    artifacts['system_top_dts'] = sdt

    # Stage 5: post-SDT enumeration and partitioning starters. These
    # are independent of one another and only consume the assembled
    # SDT, but it's convenient to produce them in the same run.
    artifacts['sdt_devices_yaml'] = _run_sdt_devices(
        sdt, board_name, output_dir, include_dirs=include_dirs,
        verbose=verbose)
    artifacts['sdt_domains_yaml'] = _run_sdt_domains(
        sdt, board_name, output_dir, include_dirs=include_dirs,
        verbose=verbose)

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
    p.add_argument('--domains', default=None,
                   help="user's per-deployment domains.yaml overlay; "
                        "deep-merged on top of the shipped per-board "
                        "template at lopper/data/boards/<board>/"
                        "domains.yaml")
    p.add_argument('--no-template', action='store_true',
                   help="skip the shipped per-board template; use only "
                        "--domains (or nothing) as the source of "
                        "integration declarations")
    p.add_argument('-I', '--input-dirs', action='append', default=[],
                   metavar='DIR',
                   help="lopper include directory, forwarded to every "
                        "lopper invocation in the run. Used to find "
                        "SoC-facts YAML you keep in your own repo at "
                        "<DIR>/data/socs/ (searched before the shipped "
                        "lopper/data/socs/). Repeatable.")
    p.add_argument('-v', '--verbose', action='store_true',
                   help="print each cpp/dtc/lopper invocation")
    args = p.parse_args()

    output_dir = args.output_dir or (Path.cwd() / f'{args.board}-build')

    # Resolve the user's overlay against *their* cwd, not the repo root
    # the lopper subprocess runs in. This lets --domains point anywhere
    # on disk (relative or absolute) — the user is never forced to drop
    # files into the lopper directory tree.
    domains_overlay = (str(Path(args.domains).resolve())
                       if args.domains else None)

    # Likewise resolve include dirs against the user's cwd before they
    # reach the repo-root-cwd subprocess.
    include_dirs = [str(Path(d).resolve()) for d in args.input_dirs]

    try:
        artifacts = build_board(args.board, output_dir,
                                no_zephyr=args.no_zephyr,
                                no_template=args.no_template,
                                domains_overlay=domains_overlay,
                                include_dirs=include_dirs,
                                verbose=args.verbose)
    except PipelineError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"board:           {args.board}")
    print(f"output dir:      {artifacts['output_dir']}")
    print(f"linux flat:      {artifacts['linux_flat'].name}")
    if 'zephyr_flat' in artifacts:
        print(f"zephyr flat:     {artifacts['zephyr_flat'].name}")
    if 'non_linux_yaml' in artifacts:
        print(f"non-linux yaml:  {artifacts['non_linux_yaml'].name}")
    print(f"system-top:      {artifacts['system_top_dts'].name}")
    print(f"sdt-devices:     {artifacts['sdt_devices_yaml'].name}")
    print(f"sdt-domains:  {artifacts['sdt_domains_yaml'].name}")
    print()
    print(f"Generated SDT: {artifacts['system_top_dts']}")


if __name__ == '__main__':
    main()
