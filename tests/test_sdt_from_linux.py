# Copyright (c) 2026 Advanced Micro Devices, Inc. All Rights Reserved.
# Author: Bruce Ashfield <bruce.ashfield@amd.com>
# SPDX-License-Identifier: BSD-3-Clause
"""
Integration test for the sdt-from-linux pipeline.

End-to-end: vendored upstream Linux DT under lopper/data/upstream/ is
flattened per the board's source.yaml declaration, fed through the
compose_devices assist, and the resulting openamp,domain-v1,devices
YAML is checked structurally and diffed against a committed golden
file at lopper/data/boards/<board>/expected-devices.yaml.

The pipeline depends on `cpp` and `dtc`. Tests skip when those aren't
available so that environments without a toolchain can still run the
rest of the suite.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
BOARDS_ROOT = REPO_ROOT / 'lopper' / 'data' / 'boards'
LOPPER_PY = REPO_ROOT / 'lopper.py'


def _have_tools():
    return (shutil.which('cpp') is not None
            and shutil.which('dtc') is not None)


pytestmark = pytest.mark.skipif(
    not _have_tools(),
    reason="sdt-from-linux integration tests require cpp and dtc")


def _flatten_board(board_name, outdir):
    """Run cpp + dtc on the Linux DT declared in the board's source.yaml.

    Returns the path to the resulting flat .dts.
    """
    src_yaml = BOARDS_ROOT / board_name / 'source.yaml'
    assert src_yaml.is_file(), f"board source.yaml missing: {src_yaml}"
    source = yaml.safe_load(src_yaml.read_text())

    linux = source['linux']
    input_path = REPO_ROOT / linux['input']
    include_paths = [REPO_ROOT / p for p in linux['include_paths']]
    assert input_path.is_file(), f"Linux DT input missing: {input_path}"

    pp_dts = outdir / f'{board_name}.pp.dts'
    flat_dts = outdir / f'{board_name}.flat.dts'

    cpp_cmd = ['cpp', '-nostdinc', '-undef', '-x', 'assembler-with-cpp']
    for ip in include_paths:
        cpp_cmd.extend(['-I', str(ip)])
    cpp_cmd.extend([str(input_path), '-o', str(pp_dts)])
    cpp_result = subprocess.run(cpp_cmd, capture_output=True, text=True)
    assert cpp_result.returncode == 0, (
        f"cpp failed for board {board_name}:\n{cpp_result.stderr}")

    dtc_cmd = ['dtc', '-I', 'dts', '-O', 'dts',
               '-o', str(flat_dts), str(pp_dts)]
    dtc_result = subprocess.run(dtc_cmd, capture_output=True, text=True)
    # dtc emits warnings on stderr even when it succeeds; only fail on
    # non-zero exit.
    assert dtc_result.returncode == 0, (
        f"dtc failed for board {board_name}:\n{dtc_result.stderr}")

    return flat_dts


def _run_compose_devices(flat_dts, board_name, outdir):
    """Invoke lopper.py with the compose_devices assist on the flat DT."""
    out_yaml = outdir / 'devices.yaml'
    lop_main_out = outdir / f'{board_name}-lopout.dts'

    cmd = [sys.executable, str(LOPPER_PY), '-f',
           str(flat_dts), str(lop_main_out),
           '--', 'compose_devices',
           '--board', board_name,
           '-o', str(out_yaml)]
    result = subprocess.run(cmd, cwd=str(REPO_ROOT),
                            capture_output=True, text=True)
    assert result.returncode == 0, (
        f"compose_devices failed for board {board_name}:\n"
        f"--- stdout ---\n{result.stdout}\n"
        f"--- stderr ---\n{result.stderr}\n")
    assert out_yaml.is_file(), f"compose_devices produced no output: {out_yaml}"
    return out_yaml


def test_compose_versal_vck190(tmp_path):
    """End-to-end: vendored upstream Versal Linux DT → devices.yaml.

    Verifies structurally that all five M2 mining enhancements
    propagate through compose_devices (memory split, SoC identity,
    aliases, bootph, PM-ID decode), then diffs against the committed
    golden file. Drift fails the test; regenerate the golden when the
    drift is intentional.
    """
    flat = _flatten_board('versal-vck190', tmp_path)
    out = _run_compose_devices(flat, 'versal-vck190', tmp_path)

    devices = yaml.safe_load(out.read_text())
    dom = devices['domains']['versal-vck190']

    # M2-3: SoC identity tagged from root compatible/model
    assert dom['compatible'] == 'openamp,domain-v1,devices'
    assert dom['soc_family'] == 'xlnx,versal'
    assert dom['board'] == 'xlnx,versal-vck190-revA'
    assert 'Versal' in dom['model']

    # CPU cluster
    cpus = dom['cpus']
    if isinstance(cpus, list):
        cpus = cpus[0]
    assert cpus['compatible'] == 'arm,cortex-a72'
    assert cpus['cpumask'] == 0x3

    # M2-1: multi-range memory split surfaces DDR-high
    starts = [m.get('start', 0) for m in dom['memory']]
    assert 0x800000000 in starts, (
        "DDR-high (memory@800000000) should be present via multi-range split; "
        f"got memory starts: {starts}")

    # M2-2: aliases passed through verbatim
    assert dom['aliases']['serial0'] == '/axi/serial@ff000000'

    # M2-5: PM-ID decode tags Versal devices canonically
    pm_named = [d for d in dom['access'] if 'pm_node' in d]
    assert len(pm_named) >= 30, (
        "PM-ID decode should tag ~33 devices via versal.yaml; "
        f"got {len(pm_named)}")
    assert any(d.get('pm_node') == 'PM_DEV_UART_0' for d in dom['access']), \
        "Console UART should be tagged PM_DEV_UART_0"

    # M2-4: bootph-all preserved on the early-boot device set
    bootph = [d for d in dom['access'] if d.get('bootph') == 'all']
    assert any('serial' in d['dev'] for d in bootph), \
        "Console UART should carry bootph:all"

    # Byte-exact golden comparison
    golden = BOARDS_ROOT / 'versal-vck190' / 'expected-devices.yaml'
    assert golden.is_file(), (
        f"golden file missing: {golden}\n"
        f"  Generated output is at {out}; copy it as the golden once verified.")
    assert out.read_text() == golden.read_text(), (
        f"compose_devices output drifted from {golden}.\n"
        f"  If the drift is intentional, regenerate the golden:\n"
        f"      cp {out} {golden}")


def test_compose_imx8mm_evk(tmp_path):
    """End-to-end: vendored upstream NXP i.MX 8MM EVK Linux DT → devices.yaml.

    Parallel to test_compose_versal_vck190 but for a SoC without a
    public PM-ID table — proves the pipeline is SoC-agnostic and that
    PM-ID decode gracefully skips when no per-SoC pm_devices entries
    are available (imx8mm.yaml ships with pm_devices: {} on purpose;
    NXP doesn't publish an equivalent of xlnx-versal-power.h).
    """
    flat = _flatten_board('imx8mm-evk', tmp_path)
    out = _run_compose_devices(flat, 'imx8mm-evk', tmp_path)

    devices = yaml.safe_load(out.read_text())
    dom = devices['domains']['imx8mm-evk']

    # SoC identity tagged from root compatible/model
    assert dom['compatible'] == 'openamp,domain-v1,devices'
    assert dom['soc_family'] == 'fsl,imx8mm'
    assert dom['board'] == 'fsl,imx8mm-evk'
    assert 'i.MX8MM EVK' in dom['model']

    # A53 quad cluster
    cpus = dom['cpus']
    if isinstance(cpus, list):
        cpus = cpus[0]
    assert cpus['compatible'] == 'arm,cortex-a53'
    assert cpus['cpumask'] == 0xf

    # 2 GiB DDR at 0x40000000 (single range on the upstream EVK).
    # LopperYAML collapses single-element lists to bare dicts; normalise.
    mem = dom['memory']
    if isinstance(mem, dict):
        mem = [mem]
    assert any(m.get('start') == 0x40000000 for m in mem), \
        f"expected memory@40000000; got starts: {[m.get('start') for m in mem]}"

    # Aliases preserved (the upstream SoC dtsi declares them automatically)
    assert 'ethernet0' in dom['aliases']
    assert 'gpio0' in dom['aliases']
    assert 'i2c0' in dom['aliases']

    # PM-ID decode is intentionally a no-op on i.MX (no public table) —
    # the pipeline should not have tagged any device with pm_node.
    pm_named = [d for d in dom['access'] if 'pm_node' in d]
    assert pm_named == [], (
        "i.MX 8MM has no public PM-ID table; imx8mm.yaml ships "
        f"pm_devices: {{}}; pm_node tags should not appear. Got: {pm_named}")

    # Byte-exact golden comparison
    golden = BOARDS_ROOT / 'imx8mm-evk' / 'expected-devices.yaml'
    assert golden.is_file(), (
        f"golden file missing: {golden}\n"
        f"  Generated output is at {out}; copy it as the golden once verified.")
    assert out.read_text() == golden.read_text(), (
        f"compose_devices output drifted from {golden}.\n"
        f"  If the drift is intentional, regenerate the golden:\n"
        f"      cp {out} {golden}")
