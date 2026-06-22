# Copyright (c) 2026 Advanced Micro Devices, Inc. All Rights Reserved.
# Author: Bruce Ashfield <bruce.ashfield@amd.com>
# SPDX-License-Identifier: BSD-3-Clause
"""
Round-trip test for the sdt-from-linux pipeline.

Generate an SDT from upstream inputs, then partition it back into
per-OS device trees with the standard Lopper domain flow, and check
the slices are *semantically* what each OS should see — not byte-exact
against the original inputs, just: dtc-clean ("could boot"), the right
CPU cluster present, the wrong cluster / co-processor bus absent.

Flow:
    build-board-sdt.py            inputs      -> system-top.dts
    lopper --auto -i devices -i domains  SDT  -> expanded.dts   (resolve globs)
    lopper expanded -- domain_access -t <APU> -> linux.dts
    lopper expanded -- domain_access -t <RPU> -> rpu.dts

Requires cpp + dtc; skips otherwise.
"""

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LOPPER_PY = REPO_ROOT / 'lopper.py'
BUILD_SCRIPT = REPO_ROOT / 'scripts' / 'build-board-sdt.py'


def _have_tools():
    return (shutil.which('cpp') is not None
            and shutil.which('dtc') is not None)


pytestmark = pytest.mark.skipif(
    not _have_tools(),
    reason="round-trip tests require cpp and dtc")


def _build_sdt(board, outdir):
    r = subprocess.run(
        [sys.executable, str(BUILD_SCRIPT), '--board', board,
         '--output-dir', str(outdir)],
        capture_output=True, text=True)
    assert r.returncode == 0, f"build-board-sdt failed:\n{r.stdout}\n{r.stderr}"
    return {
        'sdt': outdir / f'{board}-system-top.dts',
        'devices': outdir / f'{board}-sdt-devices.yaml',
        'domains': outdir / f'{board}-sdt-domains.yaml',
    }


def _expand(art, outdir, board):
    """Step 1: resolve the partition (and its globs) into the SDT."""
    expanded = outdir / f'{board}-expanded.dts'
    r = subprocess.run(
        [sys.executable, str(LOPPER_PY), '-f', '--permissive', '--enhanced',
         '--auto', '-O', str(outdir),
         '-i', str(art['devices']), '-i', str(art['domains']),
         str(art['sdt']), str(expanded)],
        cwd=str(REPO_ROOT), capture_output=True, text=True)
    assert r.returncode == 0, f"expand failed:\n{r.stdout}\n{r.stderr}"
    assert expanded.is_file()
    return expanded


def _slice(expanded, outdir, board, domain_path, out_name):
    """Step 2: filter the expanded SDT down to one domain."""
    out = outdir / f'{board}-{out_name}.dts'
    r = subprocess.run(
        [sys.executable, str(LOPPER_PY), '-f', '--enhanced',
         '-O', str(outdir), str(expanded), str(out),
         '--', 'domain_access', '-t', domain_path],
        cwd=str(REPO_ROOT), capture_output=True, text=True)
    assert r.returncode == 0, f"slice {domain_path} failed:\n{r.stdout}\n{r.stderr}"
    assert out.is_file()
    return out


def _dtc_clean(dts):
    r = subprocess.run(
        ['dtc', '-I', 'dts', '-O', 'dtb', '-o', str(dts.with_suffix('.dtb')),
         str(dts)], capture_output=True, text=True)
    fatal = any(t in (r.stderr or '') for t in ('Error:', 'FATAL ERROR:'))
    return r.returncode == 0 and not fatal


def _domain_paths(expanded):
    """Map domain label -> node path from the expanded /domains block."""
    text = expanded.read_text()
    # e.g. "APU: domain@0 {" under /domains/default/
    paths = {}
    for m in re.finditer(r'(\w+):\s*(domain@\d+)\s*\{', text):
        paths[m.group(1)] = f'/domains/default/{m.group(2)}'
    return paths


def _has_node(text, node):
    """True if `node` is declared as an actual node (not just mentioned
    inside an access-json metadata string). domain_access retains the
    full /domains block in each slice by design, so the sibling domain's
    access-json lists the other OS's devices by name; matching on a real
    node declaration avoids those false positives."""
    return re.search(r'(^|\s)' + re.escape(node) + r'\s*\{', text) is not None


def _check_versal_roundtrip(board, tmp_path):
    """Build a Versal SDT, partition it into a Linux (APU) slice and an
    RPU slice, and check each is dtc-clean and contains the right cluster
    while excluding the other side's cluster/bus. Both Versal reference
    boards share the same A72/R5 topology and node names, so the
    assertions are identical."""
    art = _build_sdt(board, tmp_path)
    expanded = _expand(art, tmp_path, board)

    dpaths = _domain_paths(expanded)
    assert 'APU' in dpaths and 'RPU' in dpaths, f"domain paths: {dpaths}"

    linux = _slice(expanded, tmp_path, board, dpaths['APU'], 'linux')
    rpu = _slice(expanded, tmp_path, board, dpaths['RPU'], 'rpu')

    # Both slices must be compilable — the "could boot" bar.
    assert _dtc_clean(linux), f"APU/linux slice not dtc-clean: {linux}"
    assert _dtc_clean(rpu), f"RPU slice not dtc-clean: {rpu}"

    ltext = linux.read_text()
    rtext = rpu.read_text()

    # --- Linux (APU) slice: A72 in, R5 / co-processor bus out --------
    assert 'cpus_a72: cpus' in ltext, "APU slice lost the A72 cluster"
    assert _has_node(ltext, 'serial@ff000000'), "APU slice lost the console UART"
    assert not _has_node(ltext, 'cpus-r5@0'), \
        "APU slice still contains the R5 cluster (domain_access should prune it)"
    assert not _has_node(ltext, 'non_linux_soc'), \
        "APU slice still contains the co-processor bus (should be pruned)"

    # --- RPU slice: R5 in, A72 / Linux peripherals out ---------------
    assert 'cpus_r5: cpus-r5@0' in rtext, "RPU slice lost the R5 cluster"
    assert _has_node(rtext, 'rpu0_reserved@3e000000'), \
        "RPU slice lost its reserved-memory"
    assert 'cpus_a72: cpus' not in rtext, \
        "RPU slice still contains the A72 cluster (should be pruned)"
    assert not _has_node(rtext, 'ethernet@ff0c0000'), \
        "RPU slice still contains the Linux ethernet node (should be pruned)"


def test_roundtrip_versal_vck190(tmp_path):
    _check_versal_roundtrip('versal-vck190', tmp_path)


def test_roundtrip_versal_vek280(tmp_path):
    _check_versal_roundtrip('versal-vek280', tmp_path)
