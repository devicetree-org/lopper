# Copyright (c) 2026 Advanced Micro Devices, Inc. All Rights Reserved.
# Author: Bruce Ashfield <bruce.ashfield@amd.com>
# SPDX-License-Identifier: BSD-3-Clause
"""
Integration test for the sdt-from-linux pipeline.

End-to-end: vendored upstream Linux DT under lopper/data/upstream/ is
flattened per the board's source.yaml declaration, fed through
compose_devices to produce an openamp,domain-v1,devices YAML, then
fed through assemble_sdt to produce a system-top.dts. Each stage's
output is structurally checked and diffed against a committed golden
file at lopper/data/boards/<board>/expected-*.

The pipeline orchestration (cpp + dtc + lopper invocations) lives in
scripts/build-board-sdt.py, which the tests invoke as a subprocess.
This keeps the orchestration logic in one place — users running the
pipeline drive it through the same script the tests use.

The pipeline depends on `cpp` and `dtc`. Tests skip when those aren't
available so that environments without a toolchain can still run the
rest of the suite.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
BOARDS_ROOT = REPO_ROOT / 'lopper' / 'data' / 'boards'
BUILD_SCRIPT = REPO_ROOT / 'scripts' / 'build-board-sdt.py'


def _have_tools():
    return (shutil.which('cpp') is not None
            and shutil.which('dtc') is not None)


pytestmark = pytest.mark.skipif(
    not _have_tools(),
    reason="sdt-from-linux integration tests require cpp and dtc")


def _run_pipeline(board_name, outdir, no_zephyr=False):
    """Drive the full pipeline for one board via scripts/build-board-sdt.py.

    Returns a dict of output artifact paths. The script knows the
    per-board cpp include paths, the dtc_force flag, the chained
    Lopper invocations — the test just calls it and checks the
    outputs.
    """
    cmd = [sys.executable, str(BUILD_SCRIPT),
           '--board', board_name,
           '--output-dir', str(outdir)]
    if no_zephyr:
        cmd.append('--no-zephyr')

    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0, (
        f"build-board-sdt.py failed for board {board_name}:\n"
        f"--- stdout ---\n{result.stdout}\n"
        f"--- stderr ---\n{result.stderr}\n")

    artifacts = {
        'devices_yaml': outdir / f'{board_name}-devices.yaml',
        'system_top_dts': outdir / f'{board_name}-system-top.dts',
    }
    for name, path in artifacts.items():
        assert path.is_file(), (
            f"build-board-sdt.py reported success but {name} is missing: {path}")
    return artifacts


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


def _assert_golden(generated, golden, label):
    """Byte-exact comparison; helpful error pointing at the regen command."""
    assert golden.is_file(), (
        f"{label} golden missing: {golden}\n"
        f"  Generated output is at {generated}; copy it as the golden "
        f"once verified.")
    assert generated.read_text() == golden.read_text(), (
        f"{label} drifted from {golden}.\n"
        f"  If the drift is intentional, regenerate the golden:\n"
        f"      cp {generated} {golden}")


def test_compose_versal_vck190(tmp_path):
    """End-to-end (Linux-only): vendored upstream Versal Linux DT → devices.yaml.

    Verifies structurally that all five M2 mining enhancements
    propagate through compose_devices (memory split, SoC identity,
    aliases, bootph, PM-ID decode), then diffs against the committed
    golden file. Drift fails the test; regenerate the golden when the
    drift is intentional.
    """
    artifacts = _run_pipeline('versal-vck190', tmp_path, no_zephyr=True)
    out = artifacts['devices_yaml']

    devices = yaml.safe_load(out.read_text())
    dom = devices['domains']['versal-vck190']

    # SoC identity tagged from root compatible/model
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

    # Multi-range memory split surfaces DDR-high
    starts = [m.get('start', 0) for m in dom['memory']]
    assert 0x800000000 in starts, (
        "DDR-high (memory@800000000) should be present via multi-range split; "
        f"got memory starts: {starts}")

    # Aliases passed through verbatim
    assert dom['aliases']['serial0'] == '/axi/serial@ff000000'

    # PM-ID decode tags Versal devices canonically
    pm_named = [d for d in dom['access'] if 'pm_node' in d]
    assert len(pm_named) >= 30, (
        "PM-ID decode should tag ~33 devices via versal.yaml; "
        f"got {len(pm_named)}")
    assert any(d.get('pm_node') == 'PM_DEV_UART_0' for d in dom['access']), \
        "Console UART should be tagged PM_DEV_UART_0"

    # bootph-all preserved on the early-boot device set
    bootph = [d for d in dom['access'] if d.get('bootph') == 'all']
    assert any('serial' in d['dev'] for d in bootph), \
        "Console UART should carry bootph:all"

    _assert_golden(out,
                   BOARDS_ROOT / 'versal-vck190' / 'expected-devices.yaml',
                   label='compose_devices Linux-only')


def test_compose_imx8mm_evk(tmp_path):
    """End-to-end (Linux-only): vendored upstream NXP i.MX 8MM EVK Linux DT → devices.yaml.

    Parallel to test_compose_versal_vck190 but for a SoC without a
    public PM-ID table — proves the pipeline is SoC-agnostic and that
    PM-ID decode gracefully skips when no per-SoC pm_devices entries
    are available (imx8mm.yaml ships with pm_devices: {} on purpose;
    NXP doesn't publish an equivalent of xlnx-versal-power.h).
    """
    artifacts = _run_pipeline('imx8mm-evk', tmp_path, no_zephyr=True)
    out = artifacts['devices_yaml']

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

    _assert_golden(out,
                   BOARDS_ROOT / 'imx8mm-evk' / 'expected-devices.yaml',
                   label='compose_devices Linux-only')


def test_compose_versal_vck190_merged(tmp_path):
    """End-to-end Linux + Zephyr merge for VCK190.

    Adds Zephyr-side mining on top of the Linux-only path: the merged
    inventory should contain an additional R5 cluster (cortex-r5f,
    source: zephyr) and the OCM region at 0xfffc0000 (source: zephyr)
    that Linux DT has no awareness of.
    """
    artifacts = _run_pipeline('versal-vck190', tmp_path)
    out = artifacts['devices_yaml']

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

    # Board augment overlay contributed the R5 firmware
    # carve-out (rpu0_reserved @ 0x3e000000).
    aug = [m for m in memory if m.get('dev') == 'rpu0_reserved']
    assert aug, f"rpu0_reserved missing; got dev names: {[m.get('dev') for m in memory]}"
    assert aug[0].get('source') == 'augment'
    assert aug[0].get('start') == 0x3e000000
    assert aug[0].get('no-map') is True

    _assert_golden(out,
                   BOARDS_ROOT / 'versal-vck190' / 'expected-devices-merged.yaml',
                   label='compose_devices merged')


def test_compose_imx8mm_evk_merged(tmp_path):
    """End-to-end Linux + Zephyr merge for i.MX 8MM EVK.

    The killer-result case: Linux DT has zero awareness of the M4 (no
    remoteproc, no TCM, no M-side mailbox). The Zephyr DT carries
    exactly those facts. Merging gives the full chip view.
    """
    artifacts = _run_pipeline('imx8mm-evk', tmp_path)
    out = artifacts['devices_yaml']

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

    # Board augment overlay contributed the M4 firmware reserve
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

    _assert_golden(out,
                   BOARDS_ROOT / 'imx8mm-evk' / 'expected-devices-merged.yaml',
                   label='compose_devices merged')


def test_assemble_versal_vck190_sdt(tmp_path):
    """End-to-end: vendored upstream Versal Linux+Zephyr → system-top.dts.

    Chains compose_devices → assemble_sdt via the build-board script,
    then asserts the resulting SDT is dtc-clean, contains both CPU
    clusters wrapped as cpus,cluster, and matches the committed golden
    expected-sdt.dts.
    """
    artifacts = _run_pipeline('versal-vck190', tmp_path)
    sdt = artifacts['system_top_dts']

    assert _dtc_parses(sdt), f"dtc failed to parse {sdt}"
    text = sdt.read_text()

    assert 'compatible = "xlnx,versal-vck190-revA", "xlnx,versal";' in text
    assert 'cpus_a72: cpus-a72@0' in text, "A72 cluster wrapper missing"
    assert 'cpus_r5: cpus-r5@1' in text, "R5 cluster wrapper missing"
    assert 'compatible = "cpus,cluster";' in text, "no cpus,cluster compatible"
    # Source-tag round-trip from compose_devices through assemble_sdt.
    assert 'lopper-source = "zephyr"' in text
    assert 'lopper-source = "augment"' in text

    _assert_golden(sdt,
                   BOARDS_ROOT / 'versal-vck190' / 'expected-sdt.dts',
                   label='assemble_sdt')


def test_assemble_imx8mm_evk_sdt(tmp_path):
    """End-to-end: vendored upstream NXP Linux+Zephyr → system-top.dts.

    The killer-result case: a system-top.dts that contains both the
    Linux-side A53 cluster AND the Zephyr-side M4 cluster, with the
    M-side TCM/OCRAM/mailbox merged into the inventory, all from
    public-only inputs. Diff against the committed golden.
    """
    artifacts = _run_pipeline('imx8mm-evk', tmp_path)
    sdt = artifacts['system_top_dts']

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

    _assert_golden(sdt,
                   BOARDS_ROOT / 'imx8mm-evk' / 'expected-sdt.dts',
                   label='assemble_sdt')
