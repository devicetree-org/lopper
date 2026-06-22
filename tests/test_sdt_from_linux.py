# Copyright (c) 2026 Advanced Micro Devices, Inc. All Rights Reserved.
# Author: Bruce Ashfield <bruce.ashfield@amd.com>
# SPDX-License-Identifier: BSD-3-Clause
"""
Integration test for the sdt-from-linux pipeline.

End-to-end: vendored upstream Linux DT (and Zephyr DT, when present)
under lopper/data/upstream/ are flattened per the board's source.yaml
declaration, fed through compose_non_linux to produce a rich-property
non-linux YAML, then through assemble_sdt to produce a system-top.dts
that uses the Linux DT as its base. Each stage is structurally
checked and diffed against committed goldens under
lopper/data/boards/<board>/expected-*.

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


def _run_pipeline(board_name, outdir, no_zephyr=False, no_template=False,
                  domains_overlay=None):
    """Drive the full pipeline for one board via scripts/build-board-sdt.py."""
    cmd = [sys.executable, str(BUILD_SCRIPT),
           '--board', board_name,
           '--output-dir', str(outdir)]
    if no_zephyr:
        cmd.append('--no-zephyr')
    if no_template:
        cmd.append('--no-template')
    if domains_overlay is not None:
        cmd.extend(['--domains', str(domains_overlay)])

    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0, (
        f"build-board-sdt.py failed for board {board_name}:\n"
        f"--- stdout ---\n{result.stdout}\n"
        f"--- stderr ---\n{result.stderr}\n")

    artifacts = {
        'system_top_dts': outdir / f'{board_name}-system-top.dts',
        'sdt_devices_yaml': outdir / f'{board_name}-sdt-devices.yaml',
        'sdt_domains_yaml': outdir / f'{board_name}-sdt-domains.yaml',
    }
    if not no_zephyr:
        artifacts['non_linux_yaml'] = outdir / f'{board_name}-non-linux.yaml'

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


# --- compose_non_linux ----------------------------------------------------

def test_compose_non_linux_versal_vck190(tmp_path):
    """compose_non_linux mines the Versal Zephyr DT for R5-side content
    not present in the Linux DT, merges the per-board domains.yaml integration overlay,
    and emits a rich-property non-linux YAML.
    """
    artifacts = _run_pipeline('versal-vck190', tmp_path)
    out = artifacts['non_linux_yaml']

    payload = yaml.safe_load(out.read_text())
    nl = payload['non_linux']

    assert nl['compatible'] == 'openamp,domain-v1,non-linux'
    assert nl['board'] == 'versal-vck190'

    # R5 cluster from the Zephyr side.
    clusters = nl['clusters']
    assert clusters, "no clusters extracted"
    r5_cluster = next(iter(clusters.values()))
    cpu_entries = r5_cluster.get('cpus') or {}
    cpu_compats = [c['properties']['compatible'] for c in cpu_entries.values()]
    assert any('cortex-r5' in c for c in cpu_compats), \
        f"R5 cpu compatible missing: {cpu_compats}"

    # OCM @ 0xfffc0000 (Zephyr-source) and the domains.yaml-derived
    # rpu0_reserved should both be present in the memory bucket, both
    # normalised to the {start, size} convention.
    memory = nl['memory']
    ocm = memory.get('memory@fffc0000')
    assert ocm and ocm['source'] == 'zephyr', \
        f"OCM @ 0xfffc0000 missing or mis-tagged: {ocm!r}"
    assert ocm['properties']['start'] == 0xfffc0000
    assert ocm['properties']['size'] == 0x40000

    rpu = memory.get('rpu0_reserved')
    assert rpu and rpu['source'] == 'domain', \
        f"rpu0_reserved missing from domains.yaml overlay: {rpu!r}"
    assert rpu['properties']['start'] == 0x3e000000
    assert rpu['properties']['no-map'] is True

    _assert_golden(out,
                   BOARDS_ROOT / 'versal-vck190' / 'expected-non-linux.yaml',
                   label='compose_non_linux')


def test_compose_non_linux_imx8mm_evk(tmp_path):
    """compose_non_linux for i.MX 8MM: M4 cluster, TCM/OCRAM regions,
    M-side mailbox, and the domains.yaml-derived M4 reserved + rpmsg
    shared regions all show up as rich entries.
    """
    artifacts = _run_pipeline('imx8mm-evk', tmp_path)
    out = artifacts['non_linux_yaml']

    payload = yaml.safe_load(out.read_text())
    nl = payload['non_linux']

    assert nl['compatible'] == 'openamp,domain-v1,non-linux'
    assert nl['board'] == 'imx8mm-evk'

    # M4 cluster.
    clusters = nl['clusters']
    m4_cluster = next(iter(clusters.values()))
    cpu_compats = [c['properties']['compatible']
                   for c in (m4_cluster.get('cpus') or {}).values()]
    assert any('cortex-m4' in c for c in cpu_compats), \
        f"M4 cpu compatible missing: {cpu_compats}"

    # M-side TCM/OCRAM regions normalised to start/size.
    memory = nl['memory']
    dtcm = memory.get('memory@20000000')
    assert dtcm and dtcm['properties']['start'] == 0x20000000
    assert dtcm['properties']['compatible'] == 'nxp,imx-dtcm'

    # Augment-derived M4 firmware + rpmsg carve-outs.
    m4_reserved = memory.get('m4_reserved')
    assert m4_reserved and m4_reserved['source'] == 'domain'
    assert m4_reserved['properties']['start'] == 0x80000000
    rpmsg = memory.get('rpmsg_shmem')
    assert rpmsg and rpmsg['source'] == 'domain'
    assert rpmsg['properties']['start'] == 0xb8000000

    # M-side MU mailbox @ 0x30ab0000 — Linux DT has MU_A @ 0x30aa0000;
    # the Zephyr side declares MU_B @ 0x30ab0000.
    devices = nl['devices']
    mu_b = devices.get('mailbox@30ab0000')
    assert mu_b and mu_b['properties']['compatible'] == 'nxp,imx-mu'

    _assert_golden(out,
                   BOARDS_ROOT / 'imx8mm-evk' / 'expected-non-linux.yaml',
                   label='compose_non_linux')


# --- assemble_sdt ---------------------------------------------------------

def test_assemble_versal_vck190_sdt(tmp_path):
    """End-to-end: vendored upstream Versal Linux+Zephyr → system-top.dts.

    The Linux DT becomes the SDT base; assemble_sdt wraps /cpus into
    the cpus,cluster convention, attaches the R5 cluster from the
    non-linux YAML, and parks M/R-side peripherals under
    /non_linux_soc. Result must be dtc-clean.
    """
    artifacts = _run_pipeline('versal-vck190', tmp_path)
    sdt = artifacts['system_top_dts']

    assert _dtc_parses(sdt), f"dtc failed to parse {sdt}"
    text = sdt.read_text()

    # Linux DT identity round-trips.
    assert 'compatible = "xlnx,versal-vck190-revA", "xlnx,versal";' in text
    # Linux /cpus block stays in place, gets a cpus_<arch> label and
    # the cpus,cluster compatible marker.
    assert 'cpus_a72: cpus' in text, "A72 cluster label missing"
    # Non-Linux R5 cluster added as a sibling.
    assert 'cpus_r5: cpus-r5@0' in text, "R5 cluster wrapper missing"
    assert 'compatible = "cpus,cluster";' in text, "no cpus,cluster compatible"
    # Source-tag round-trip from compose_non_linux through assemble_sdt.
    assert 'lopper-source = "zephyr"' in text
    assert 'lopper-source = "domain"' in text
    # Augment-derived OCM carveout lands under /reserved-memory.
    assert 'rpu0_reserved' in text

    _assert_golden(sdt,
                   BOARDS_ROOT / 'versal-vck190' / 'expected-sdt.dts',
                   label='assemble_sdt')


def test_assemble_versal_vek280_sdt(tmp_path):
    """VEK280 (Versal AI Edge) — the newer Versal reference. Same
    A72-APU + R5-RPU pipeline as VCK190 on a different board DTS,
    exercised end-to-end against xilinx-v2026.1 sources.
    """
    artifacts = _run_pipeline('versal-vek280', tmp_path)
    sdt = artifacts['system_top_dts']

    assert _dtc_parses(sdt), f"dtc failed to parse {sdt}"
    text = sdt.read_text()

    assert 'xlnx,versal-vek280-revB' in text
    assert 'cpus_a72: cpus' in text, "A72 cluster label missing"
    assert 'cpus_r5: cpus-r5@0' in text, "R5 cluster wrapper missing"
    assert 'compatible = "cpus,cluster";' in text
    assert 'rpu0_reserved' in text
    assert 'lopper-source = "zephyr"' in text
    # Fixed management clusters the Linux/Zephyr inputs don't carry are
    # synthesized from the versal.yaml cluster_templates (public TRM).
    assert 'pmc-microblaze' in text, "PMC cluster not injected from soc-facts"
    assert 'psm-microblaze' in text, "PSM cluster not injected from soc-facts"
    assert 'lopper-source = "soc-facts"' in text

    # Fixed silicon apertures the inputs don't carry are materialized
    # from the versal.yaml reference blocks: IPI buffer + the four RPU
    # TCM banks (global view). OCM already comes from Zephyr, so it is
    # referenced in place rather than duplicated.
    assert 'mailbox@ff3f0000' in text, "IPI aperture not injected from soc-facts"
    assert 'memory@ffe00000' in text, "TCM bank not injected from soc-facts"

    # Every cpus,cluster gets a baseline address-map inferred from the
    # nodes present, at 2/2 ranges cells, with phandle refs that lopper
    # auto-labelled (no &invalid_phandle, no raw &name@addr). All four
    # Versal clusters (APU, RPU, PMC, PSM) are treated uniformly — no
    # CPU-type special-casing.
    assert text.count('address-map = ') == 4, \
        "expected one address-map per cluster (APU + RPU + PMC + PSM)"
    assert '#ranges-address-cells = <0x2>;' in text
    assert '#ranges-size-cells = <0x2>;' in text
    assert '&invalid_phandle' not in text, "unresolved phandle slot in address-map"
    # The RPU cluster's map reaches its TCM; the APU map reaches DDR.
    assert '&memoryffe00000' in text, "TCM not referenced from a cluster address-map"

    _assert_golden(sdt,
                   BOARDS_ROOT / 'versal-vek280' / 'expected-sdt.dts',
                   label='assemble_sdt')


def test_compose_non_linux_versal_vek280(tmp_path):
    """VEK280 R5-side extraction mirrors VCK190 (shared Versal R5
    silicon): an R5 cluster, the OCM region, and the domains.yaml
    rpu0_reserved carve-out.
    """
    artifacts = _run_pipeline('versal-vek280', tmp_path)
    out = artifacts['non_linux_yaml']

    nl = yaml.safe_load(out.read_text())['non_linux']
    assert nl['board'] == 'versal-vek280'
    r5 = next(iter(nl['clusters'].values()))
    compats = [c['properties']['compatible']
               for c in (r5.get('cpus') or {}).values()]
    assert any('cortex-r5' in c for c in compats), compats
    rpu = nl['memory'].get('rpu0_reserved')
    assert rpu and rpu['source'] == 'domain'

    _assert_golden(out,
                   BOARDS_ROOT / 'versal-vek280' / 'expected-non-linux.yaml',
                   label='compose_non_linux')


def test_sdt_domains_versal_vek280(tmp_path):
    """VEK280 starter partition: APU gets the Linux CMA pool, RPU gets
    the R5 cluster + its reserved-memory (the linux,cma ownership fix
    applies here too).
    """
    artifacts = _run_pipeline('versal-vek280', tmp_path)
    out = artifacts['sdt_domains_yaml']

    root = yaml.safe_load(out.read_text())['domains']['default']['domains']
    assert 'APU' in root and 'RPU' in root
    # The PMC and PSM microblaze clusters are NOT special-cased out: every
    # cpus,cluster yields a starter domain for the user to keep or prune.
    assert any('pmc' in k for k in root), f"PMC cluster got no domain: {list(root)}"
    assert any('psm' in k for k in root), f"PSM cluster got no domain: {list(root)}"
    apu_mem = [m['dev'] for m in root['APU']['memory']]
    rpu_mem = [m['dev'] for m in root['RPU']['memory']]
    assert 'linux,cma' in apu_mem, f"linux,cma should be in APU: {apu_mem}"
    assert 'linux,cma' not in rpu_mem, f"linux,cma should not be in RPU: {rpu_mem}"
    assert any('rpu0_reserved' in n for n in rpu_mem)

    _assert_golden(out,
                   BOARDS_ROOT / 'versal-vek280' / 'expected-sdt-domains.yaml',
                   label='sdt_domains')


def test_assemble_imx8mm_evk_sdt(tmp_path):
    """End-to-end: vendored upstream NXP Linux+Zephyr → system-top.dts.

    The killer-result case: an SDT containing both the Linux-side A53
    cluster (preserved verbatim from the Linux DT base) AND the
    Zephyr-side M4 cluster with its TCM/OCRAM/mailbox merged in via
    the non-linux YAML, all from public-only inputs.
    """
    artifacts = _run_pipeline('imx8mm-evk', tmp_path)
    sdt = artifacts['system_top_dts']

    assert _dtc_parses(sdt), f"dtc failed to parse {sdt}"
    text = sdt.read_text()

    assert 'compatible = "fsl,imx8mm-evk", "fsl,imx8mm";' in text
    assert 'cpus_a53: cpus' in text, "A53 cluster label missing"
    assert 'cpus_m4: cpus-m4@0' in text, "M4 cluster wrapper missing"
    assert 'compatible = "cpus,cluster";' in text
    # Augment-derived M4 firmware carve-out and the rpmsg region land
    # under /reserved-memory.
    assert 'm4_reserved' in text
    assert 'rpmsg_shmem' in text
    # M-side mailbox + NVIC + code regions live under /non_linux_soc.
    assert 'non_linux_soc' in text
    assert 'mailbox@30ab0000' in text

    _assert_golden(sdt,
                   BOARDS_ROOT / 'imx8mm-evk' / 'expected-sdt.dts',
                   label='assemble_sdt')


## --- sdt_devices on the assembled SDT ----------------------------------

def test_sdt_devices_versal_vck190(tmp_path):
    """sdt_devices enumerates every device in the assembled SDT into a
    YAML vocabulary the downstream glob-driven domains.yaml workflow
    consumes as a parent.
    """
    artifacts = _run_pipeline('versal-vck190', tmp_path)
    out = artifacts['sdt_devices_yaml']

    data = yaml.safe_load(out.read_text())
    dom = data['domains']['sdt_all_devices']
    devs = [e.get('dev', '') for e in dom.get('access', [])]
    # Core peripherals from the Linux DT.
    assert any('serial@ff000000' in d for d in devs), \
        f"console UART missing from enumeration: {devs[:5]}…"
    assert any('ethernet@ff0c0000' in d for d in devs)


## --- sdt_domains -----------------------------------------------

def test_sdt_domains_versal_vck190(tmp_path):
    """sdt_domains emits one starter domain per cpus,cluster,
    partitioned by lopper-source: APU (untagged Linux side) and RPU
    (zephyr-tagged R5 cluster + domains.yaml carve-outs that name-match).
    """
    artifacts = _run_pipeline('versal-vck190', tmp_path)
    out = artifacts['sdt_domains_yaml']

    data = yaml.safe_load(out.read_text())
    root = data['domains']['default']['domains']

    assert 'APU' in root, f"APU domain missing; got {list(root)}"
    assert 'RPU' in root, f"RPU domain missing; got {list(root)}"

    apu = root['APU']
    assert apu['cpus'][0]['cluster'] == 'cpus_a72'
    assert apu['cpus'][0]['cpumask'] == '0x3'
    # Linux starter uses a single wildcard glob for access.
    assert apu['access'] == [{'dev': '*'}]
    # Linux memory is the Linux DT's /memory@0 (untagged), not the
    # Zephyr-tagged OCM at 0xfffc0000.
    apu_mem_names = [m['dev'] for m in apu['memory']]
    assert 'memory@0' in apu_mem_names
    assert 'memory@fffc0000' not in apu_mem_names

    rpu = root['RPU']
    assert rpu['cpus'][0]['cluster'] == 'cpus_r5'
    rpu_mem_names = [m['dev'] for m in rpu['memory']]
    # OCM (Zephyr-tagged root memory) lands in the R5 domain.
    assert 'memory@fffc0000' in rpu_mem_names
    # Augment rpu0_reserved heuristic-matched to the R5 cluster.
    assert any('rpu0_reserved' in n for n in rpu_mem_names)

    _assert_golden(out,
                   BOARDS_ROOT / 'versal-vck190' / 'expected-sdt-domains.yaml',
                   label='sdt_domains')


def test_sdt_domains_imx8mm_evk(tmp_path):
    """sdt_domains for i.MX 8MM: APU + MCU starter domains,
    M4 firmware carve-out and rpmsg shared region attached to MCU,
    M-side peripherals from /non_linux_soc attached as access.
    """
    artifacts = _run_pipeline('imx8mm-evk', tmp_path)
    out = artifacts['sdt_domains_yaml']

    data = yaml.safe_load(out.read_text())
    root = data['domains']['default']['domains']

    assert 'APU' in root
    assert 'MCU' in root

    mcu = root['MCU']
    assert mcu['cpus'][0]['cluster'] == 'cpus_m4'
    mem_names = [m['dev'] for m in mcu['memory']]
    # M-side TCM + domains.yaml carve-outs land in MCU.
    assert any('memory@20000000' in n for n in mem_names), f"DTCM missing: {mem_names}"
    assert any('m4_reserved' in n for n in mem_names)
    assert any('rpmsg_shmem' in n for n in mem_names)

    access_names = [a['dev'] for a in mcu['access']]
    assert any('mailbox@30ab0000' in a for a in access_names), \
        f"M4 mailbox missing from MCU access: {access_names}"

    _assert_golden(out,
                   BOARDS_ROOT / 'imx8mm-evk' / 'expected-sdt-domains.yaml',
                   label='sdt_domains')


def test_linux_only_sdt_versal(tmp_path):
    """With --no-zephyr the pipeline produces a Linux-only SDT: the
    Linux DT is wrapped with cpus,cluster but no non-Linux content is
    overlaid. Used to validate the pipeline degrades gracefully on
    boards without a Zephyr DT.
    """
    artifacts = _run_pipeline('versal-vck190', tmp_path, no_zephyr=True)
    sdt = artifacts['system_top_dts']

    assert _dtc_parses(sdt), f"dtc failed to parse {sdt}"
    text = sdt.read_text()

    assert 'cpus_a72: cpus' in text
    assert 'compatible = "cpus,cluster";' in text
    # No non-Linux content was overlaid.
    assert 'cpus-r5@0' not in text
    assert 'non_linux_soc' not in text


def test_domains_overlay_layers_on_top_of_template(tmp_path):
    """The shipped board template + a user --domains overlay deep-merge
    in compose_non_linux: the overlay's entries override the template's
    by `dev` key, and overlay-only entries get added on top. Verifies
    the no-copy / no-sync deployment pattern.
    """
    overlay = tmp_path / 'my-overrides.yaml'
    overlay.write_text(
        "domains:\n"
        "  default:\n"
        "    compatible: openamp,domain-v1\n"
        "    domains:\n"
        "      RPU:\n"
        "        compatible: openamp,domain-v1\n"
        "        memory:\n"
        "          # override: shrink the rpu0_reserved carveout\n"
        "          - dev: rpu0_reserved\n"
        "            start: 0x3e000000\n"
        "            size: 0x100000\n"
        "            no-map: true\n"
        "          # new: add a small ipc_share region\n"
        "          - dev: my_ipc_share\n"
        "            start: 0x3f000000\n"
        "            size: 0x10000\n"
        "            no-map: true\n"
    )
    artifacts = _run_pipeline('versal-vck190', tmp_path,
                              domains_overlay=overlay)
    sdt = artifacts['system_top_dts']
    text = sdt.read_text()

    # Override took effect — size is the overlay's 0x100000, not the
    # template's 0x4000000.
    import re
    m = re.search(r'rpu0_reserved@\w+ \{[^}]*reg = <([^>]+)>;', text)
    assert m, "rpu0_reserved node not found in assembled SDT"
    cells = m.group(1).split()
    assert cells[-1] == '0x100000', \
        f"overlay's rpu0_reserved size should have replaced the template's; got reg = <{m.group(1)}>"

    # Addition took effect — overlay-only entry shows up.
    assert 'my_ipc_share' in text, \
        "overlay-only my_ipc_share carveout missing from SDT"
