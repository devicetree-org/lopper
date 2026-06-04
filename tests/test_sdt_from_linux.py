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


def _flatten_block(block, board_name, outdir, label):
    """Run cpp + dtc on one side (linux or zephyr) of a board's source.yaml.

    Returns the path to the resulting flat .dts. Honours an optional
    `dtc_force: true` on the block (needed for the i.MX 8MM Zephyr
    side, whose unresolved pinctrl phandles point into a Zephyr west
    module we don't vendor).
    """
    input_path = REPO_ROOT / block['input']
    include_paths = [REPO_ROOT / p for p in block['include_paths']]
    assert input_path.is_file(), f"{label} DT input missing: {input_path}"

    pp_dts = outdir / f'{board_name}-{label}.pp.dts'
    flat_dts = outdir / f'{board_name}-{label}.flat.dts'

    cpp_cmd = ['cpp', '-nostdinc', '-undef', '-x', 'assembler-with-cpp']
    for ip in include_paths:
        cpp_cmd.extend(['-I', str(ip)])
    cpp_cmd.extend([str(input_path), '-o', str(pp_dts)])
    cpp_result = subprocess.run(cpp_cmd, capture_output=True, text=True)
    assert cpp_result.returncode == 0, (
        f"cpp failed for board {board_name} ({label}):\n{cpp_result.stderr}")

    dtc_cmd = ['dtc', '-I', 'dts', '-O', 'dts']
    if block.get('dtc_force'):
        dtc_cmd.append('-f')
    dtc_cmd.extend(['-o', str(flat_dts), str(pp_dts)])
    dtc_result = subprocess.run(dtc_cmd, capture_output=True, text=True)
    assert dtc_result.returncode == 0, (
        f"dtc failed for board {board_name} ({label}):\n{dtc_result.stderr}")

    return flat_dts


def _flatten_board(board_name, outdir, want_zephyr=False):
    """Run cpp + dtc on the Linux side (and optionally Zephyr) of a board.

    Returns the Linux flat path; if want_zephyr is True, returns a
    (linux_flat, zephyr_flat) tuple instead.
    """
    src_yaml = BOARDS_ROOT / board_name / 'source.yaml'
    assert src_yaml.is_file(), f"board source.yaml missing: {src_yaml}"
    source = yaml.safe_load(src_yaml.read_text())

    linux_flat = _flatten_block(source['linux'], board_name, outdir, 'linux')
    if not want_zephyr:
        return linux_flat

    assert 'zephyr' in source, (
        f"board {board_name}: source.yaml has no zephyr: block; cannot do merge run")
    zephyr_flat = _flatten_block(source['zephyr'], board_name, outdir, 'zephyr')
    return linux_flat, zephyr_flat


def _run_compose_devices(flat_dts, board_name, outdir, zephyr_flat=None):
    """Invoke lopper.py with the compose_devices assist on the flat DT.

    If zephyr_flat is given, also pass --zephyr-dt for the merged path.
    """
    out_name = 'devices-merged.yaml' if zephyr_flat else 'devices.yaml'
    out_yaml = outdir / out_name
    lop_main_out = outdir / f'{board_name}-lopout.dts'

    cmd = [sys.executable, str(LOPPER_PY), '-f',
           str(flat_dts), str(lop_main_out),
           '--', 'compose_devices',
           '--board', board_name,
           '-o', str(out_yaml)]
    if zephyr_flat:
        cmd.extend(['--zephyr-dt', str(zephyr_flat)])
    result = subprocess.run(cmd, cwd=str(REPO_ROOT),
                            capture_output=True, text=True)
    assert result.returncode == 0, (
        f"compose_devices failed for board {board_name}:\n"
        f"--- stdout ---\n{result.stdout}\n"
        f"--- stderr ---\n{result.stderr}\n")
    assert out_yaml.is_file(), f"compose_devices produced no output: {out_yaml}"
    return out_yaml


def _run_assemble_sdt(devices_yaml, board_name, outdir):
    """Invoke lopper.py with the assemble_sdt assist on a devices YAML.

    assemble_sdt ignores the main sdt input; we still have to give
    lopper.py *some* file to load (otherwise it errors out). Re-use the
    Linux flat DT we already have on disk for the board.
    """
    out_dts = outdir / 'system-top.dts'
    lop_main_out = outdir / f'{board_name}-assemble-lopout.dts'
    # Lopper needs a real input file to load (even if the assist
    # ignores it). Reuse the Linux flat we built earlier — the
    # assist's sdt param goes unused.
    dummy_sdt = outdir / f'{board_name}-linux.flat.dts'
    if not dummy_sdt.is_file():
        # we expect _flatten_board to have been called for this board
        # before us; fall back to whichever flat file is around.
        candidates = list(outdir.glob('*-linux.flat.dts'))
        assert candidates, f"no flat .dts in {outdir} to use as dummy lopper input"
        dummy_sdt = candidates[0]

    cmd = [sys.executable, str(LOPPER_PY), '-f',
           str(dummy_sdt), str(lop_main_out),
           '--', 'assemble_sdt',
           '--devices', str(devices_yaml),
           '-o', str(out_dts)]
    result = subprocess.run(cmd, cwd=str(REPO_ROOT),
                            capture_output=True, text=True)
    assert result.returncode == 0, (
        f"assemble_sdt failed for board {board_name}:\n"
        f"--- stdout ---\n{result.stdout}\n"
        f"--- stderr ---\n{result.stderr}\n")
    assert out_dts.is_file(), f"assemble_sdt produced no output: {out_dts}"
    return out_dts


def _dtc_parses(dts_path):
    """Return True if dtc can compile the DTS to DTB without errors."""
    dtb = dts_path.with_suffix('.dtb')
    result = subprocess.run(
        ['dtc', '-I', 'dts', '-O', 'dtb', '-o', str(dtb), str(dts_path)],
        capture_output=True, text=True)
    # dtc emits warnings about non-canonical unit-address etc. — those
    # are tolerable for an SDT intermediate. Only ERROR / FATAL block.
    fatal = any(tag in (result.stderr or '')
                for tag in ('Error:', 'FATAL ERROR:'))
    return result.returncode == 0 and not fatal


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


def test_compose_versal_vck190_merged(tmp_path):
    """End-to-end Linux + Zephyr merge for VCK190.

    Adds Zephyr-side mining on top of the Linux-only path: the merged
    inventory should contain an additional R5 cluster (cortex-r5f,
    source: zephyr) and the OCM region at 0xfffc0000 (source: zephyr)
    that Linux DT has no awareness of.
    """
    linux_flat, zephyr_flat = _flatten_board('versal-vck190', tmp_path,
                                             want_zephyr=True)
    out = _run_compose_devices(linux_flat, 'versal-vck190', tmp_path,
                               zephyr_flat=zephyr_flat)

    devices = yaml.safe_load(out.read_text())
    dom = devices['domains']['versal-vck190']

    # Linux's SoC identity remains authoritative
    assert dom['board'] == 'xlnx,versal-vck190-revA'
    assert dom['soc_family'] == 'xlnx,versal'

    # Both clusters now present: A72 from Linux, R5 from Zephyr.
    cpus = dom['cpus']
    if isinstance(cpus, dict):
        cpus = [cpus]
    compats = [c.get('compatible') for c in cpus]
    assert 'arm,cortex-a72' in compats, f"A72 cluster missing: {compats}"
    assert 'arm,cortex-r5f' in compats, f"R5 cluster missing: {compats}"
    r5 = next(c for c in cpus if c.get('compatible') == 'arm,cortex-r5f')
    assert r5.get('source') == 'zephyr', (
        f"R5 cluster should be tagged source: zephyr; got {r5!r}")

    # OCM @ 0xfffc0000 is the Zephyr-only memory region the gap
    # analysis flagged as missing from the Linux DT.
    memory = dom['memory']
    if isinstance(memory, dict):
        memory = [memory]
    ocm = [m for m in memory if m.get('start') == 0xfffc0000]
    assert ocm, f"OCM @ 0xfffc0000 missing from merged inventory; got: {[m.get('start') for m in memory]}"
    assert ocm[0].get('source') == 'zephyr', (
        f"OCM should be tagged source: zephyr; got {ocm[0]!r}")

    # M9: board augment overlay contributed the R5 firmware
    # carve-out (rpu0_reserved @ 0x3e000000).
    aug = [m for m in memory if m.get('dev') == 'rpu0_reserved']
    assert aug, f"rpu0_reserved missing; got dev names: {[m.get('dev') for m in memory]}"
    assert aug[0].get('source') == 'augment'
    assert aug[0].get('start') == 0x3e000000
    assert aug[0].get('no-map') is True

    # Byte-exact merged golden
    golden = BOARDS_ROOT / 'versal-vck190' / 'expected-devices-merged.yaml'
    assert golden.is_file(), (
        f"merged golden file missing: {golden}\n"
        f"  Generated output is at {out}; copy it as the golden once verified.")
    assert out.read_text() == golden.read_text(), (
        f"compose_devices merged output drifted from {golden}.\n"
        f"  If the drift is intentional, regenerate:\n"
        f"      cp {out} {golden}")


def test_compose_imx8mm_evk_merged(tmp_path):
    """End-to-end Linux + Zephyr merge for i.MX 8MM EVK.

    The killer-result case: Linux DT has zero awareness of the M4 (no
    remoteproc, no TCM, no M-side mailbox). The Zephyr DT carries
    exactly those facts. Merging gives the full chip view.
    """
    linux_flat, zephyr_flat = _flatten_board('imx8mm-evk', tmp_path,
                                             want_zephyr=True)
    out = _run_compose_devices(linux_flat, 'imx8mm-evk', tmp_path,
                               zephyr_flat=zephyr_flat)

    devices = yaml.safe_load(out.read_text())
    dom = devices['domains']['imx8mm-evk']

    # Linux identity preserved
    assert dom['board'] == 'fsl,imx8mm-evk'

    # Both clusters present
    cpus = dom['cpus']
    if isinstance(cpus, dict):
        cpus = [cpus]
    compats = [c.get('compatible') for c in cpus]
    assert 'arm,cortex-a53' in compats, f"A53 cluster missing: {compats}"
    assert 'arm,cortex-m4' in compats, f"M4 cluster missing: {compats}"
    m4 = next(c for c in cpus if c.get('compatible') == 'arm,cortex-m4')
    assert m4.get('source') == 'zephyr', \
        f"M4 cluster should be tagged source: zephyr; got {m4!r}"

    # M-side MU mailbox (MU_B @ 0x30ab0000) — partners the Linux DT's
    # MU_A @ 0x30aa0000 and only the Zephyr side declares it.
    access = dom['access']
    mu_b = [d for d in access if d.get('dev') == 'mailbox@30ab0000']
    assert mu_b, "MU_B mailbox @ 0x30ab0000 should be merged from Zephyr"
    assert mu_b[0].get('source') == 'zephyr', \
        f"MU_B should be tagged source: zephyr; got {mu_b[0]!r}"

    # M4 ITCM (code@1ffe0000) — Zephyr-only by definition
    itcm = [d for d in access if d.get('dev') == 'code@1ffe0000']
    assert itcm, "M4 ITCM (code@1ffe0000) should be present"
    assert itcm[0].get('source') == 'zephyr'

    # M9: board augment overlay contributed the M4 firmware reserve
    # and the rpmsg shared-memory region.
    memory = dom['memory']
    if isinstance(memory, dict):
        memory = [memory]
    m4_reserved = [m for m in memory if m.get('dev') == 'm4_reserved']
    assert m4_reserved, "m4_reserved missing from merged inventory"
    assert m4_reserved[0].get('source') == 'augment'
    assert m4_reserved[0].get('start') == 0x80000000
    rpmsg = [m for m in memory if m.get('dev') == 'rpmsg_shmem']
    assert rpmsg, "rpmsg_shmem missing from merged inventory"
    assert rpmsg[0].get('source') == 'augment'
    assert rpmsg[0].get('start') == 0xb8000000

    # Byte-exact merged golden
    golden = BOARDS_ROOT / 'imx8mm-evk' / 'expected-devices-merged.yaml'
    assert golden.is_file(), (
        f"merged golden file missing: {golden}\n"
        f"  Generated output is at {out}; copy it as the golden once verified.")
    assert out.read_text() == golden.read_text(), (
        f"compose_devices merged output drifted from {golden}.\n"
        f"  If the drift is intentional, regenerate:\n"
        f"      cp {out} {golden}")


def test_assemble_versal_vck190_sdt(tmp_path):
    """End-to-end: vendored upstream Versal Linux+Zephyr → system-top.dts.

    Chains compose_devices → assemble_sdt, then asserts the resulting
    SDT is dtc-clean, contains both CPU clusters wrapped as
    cpus,cluster, and matches the committed golden expected-sdt.dts.
    """
    linux_flat, zephyr_flat = _flatten_board('versal-vck190', tmp_path,
                                             want_zephyr=True)
    devices_yaml = _run_compose_devices(linux_flat, 'versal-vck190', tmp_path,
                                        zephyr_flat=zephyr_flat)
    sdt = _run_assemble_sdt(devices_yaml, 'versal-vck190', tmp_path)

    assert _dtc_parses(sdt), f"dtc failed to parse {sdt}"
    text = sdt.read_text()

    assert 'compatible = "xlnx,versal-vck190-revA", "xlnx,versal";' in text
    assert 'cpus_a72: cpus-a72@0' in text, "A72 cluster wrapper missing"
    assert 'cpus_r5: cpus-r5@1' in text, "R5 cluster wrapper missing"
    assert 'compatible = "cpus,cluster";' in text, "no cpus,cluster compatible"
    # Source-tag round-trip from compose_devices through assemble_sdt.
    assert 'lopper-source = "zephyr"' in text
    assert 'lopper-source = "augment"' in text

    golden = BOARDS_ROOT / 'versal-vck190' / 'expected-sdt.dts'
    assert golden.is_file(), (
        f"sdt golden missing: {golden}\n"
        f"  Generated output is at {sdt}; copy it as the golden once verified.")
    assert sdt.read_text() == golden.read_text(), (
        f"assemble_sdt output drifted from {golden}.\n"
        f"  If the drift is intentional, regenerate:\n"
        f"      cp {sdt} {golden}")


def test_assemble_imx8mm_evk_sdt(tmp_path):
    """End-to-end: vendored upstream NXP Linux+Zephyr → system-top.dts.

    The killer-result case: a system-top.dts that contains both the
    Linux-side A53 cluster AND the Zephyr-side M4 cluster, with the
    M-side TCM/OCRAM/mailbox merged into the inventory, all from
    public-only inputs. Diff against the committed golden.
    """
    linux_flat, zephyr_flat = _flatten_board('imx8mm-evk', tmp_path,
                                             want_zephyr=True)
    devices_yaml = _run_compose_devices(linux_flat, 'imx8mm-evk', tmp_path,
                                        zephyr_flat=zephyr_flat)
    sdt = _run_assemble_sdt(devices_yaml, 'imx8mm-evk', tmp_path)

    assert _dtc_parses(sdt), f"dtc failed to parse {sdt}"
    text = sdt.read_text()

    assert 'compatible = "fsl,imx8mm-evk", "fsl,imx8mm";' in text
    assert 'cpus_a53: cpus-a53@0' in text, "A53 cluster wrapper missing"
    assert 'cpus_m4: cpus-m4@1' in text, "M4 cluster wrapper missing"
    assert 'compatible = "cpus,cluster";' in text
    # Augment-derived M4 firmware carve-out and the rpmsg region
    # should both be reachable as memory entries in the SDT.
    assert 'm4_reserved' in text
    assert 'rpmsg_shmem' in text

    golden = BOARDS_ROOT / 'imx8mm-evk' / 'expected-sdt.dts'
    assert golden.is_file(), (
        f"sdt golden missing: {golden}\n"
        f"  Generated output is at {sdt}; copy it as the golden once verified.")
    assert sdt.read_text() == golden.read_text(), (
        f"assemble_sdt output drifted from {golden}.\n"
        f"  If the drift is intentional, regenerate:\n"
        f"      cp {sdt} {golden}")
