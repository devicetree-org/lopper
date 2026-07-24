"""End-to-end tests for ZynqMP Libmetal domain generation."""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
LOPPER = REPO_ROOT / "lopper.py"
INPUTS = REPO_ROOT / "demos" / "openamp" / "inputs"
SDT = INPUTS / "openamp_zu.dts"
LIBMETAL_YAML = INPUTS / "libmetal-overlay-zynqmp.yaml"
DOMAIN_ACCESS_YAML = (
    INPUTS / "libmetal-overlay-zynqmp-domain-access.yaml"
)


pytestmark = pytest.mark.skipif(
    shutil.which("dtc") is None,
    reason="ZynqMP Libmetal generation requires dtc",
)


def _run(args):
    env = os.environ.copy()
    env["LOPPER_DTC_FLAGS"] = "-b 0 -@"
    result = subprocess.run(
        [sys.executable, str(LOPPER), *map(str, args)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "assist %" not in output, output
    assert "the assist returned false" not in output, output
    assert "[ERROR]" not in output, output
    return output


def _cmake_values(path):
    return dict(re.findall(
        r"set\((\w+)\s+\"?([^\"\s\)]+)\"?\)",
        path.read_text(),
    ))


def _assert_node_compatible(dts, unit_name, compatible):
    text = dts.read_text()
    match = re.search(
        rf"(?:\w+:\s+)?{re.escape(unit_name)}\s*\{{(.*?)\n\s*\}};",
        text,
        re.DOTALL,
    )
    assert match, f"node {unit_name} missing from {dts}"
    assert f'compatible = "{compatible}";' in match.group(1)


def test_zynqmp_libmetal_linux_and_baremetal_outputs(tmp_path):
    """Generate domain slices and Libmetal CMake data for both endpoints."""
    expanded = tmp_path / "libmetal-zynqmp-expanded.dts"
    _run([
        "-f", "--permissive", "--enhanced", "--auto",
        "-i", LIBMETAL_YAML,
        "-i", DOMAIN_ACCESS_YAML,
        SDT, expanded,
    ])
    expanded_text = expanded.read_text()
    assert "__lopper-overlays__" in expanded_text
    assert 'lopper,activate = "linux";' in expanded_text

    linux_dts = tmp_path / "libmetal-zynqmp-linux.dts"
    r5_dts = tmp_path / "libmetal-zynqmp-r5-1.dts"
    _run([
        "-f", "--permissive", expanded, linux_dts,
        "--", "domain_access", "-t", "/domains/APU_Linux",
    ])
    _run([
        "-f", "--permissive", expanded, r5_dts,
        "--", "domain_access", "-t", "/domains/R5_1_BAREMETAL",
    ])

    _assert_node_compatible(linux_dts, "timer@ff130000", "uio")
    _assert_node_compatible(linux_dts, "ipi@ff350000", "uio")
    _assert_node_compatible(r5_dts, "timer@ff130000", "cdns,ttc")
    _assert_node_compatible(
        r5_dts, "ipi@ff320000", "xlnx,zynqmp-ipi-mailbox")

    linux_cmake = tmp_path / "libmetal-zynqmp-linux.cmake"
    r5_cmake = tmp_path / "libmetal-zynqmp-r5-1.cmake"
    _run([
        "-f", linux_dts, tmp_path / "linux-openamp.dts",
        "--", "openamp", "--libmetal_output_file",
        "--compatible-string=libmetal,ipc-v1",
        "--processor=psu_cortexr5_1", "--os=linux_dt",
        f"--openamp_output_filename={linux_cmake}",
    ])
    _run([
        "-f", r5_dts, tmp_path / "r5-openamp.dts",
        "--", "openamp", "--libmetal_output_file",
        "--compatible-string=libmetal,ipc-v1",
        "--processor=psu_cortexr5_1", "--os=baremetal_dt",
        f"--openamp_output_filename={r5_cmake}",
    ])

    common = {
        "TTC_DEV_NAME": "ff130000.timer",
        "TTC_NODEID": "0x1a",
        "TTC_BASE_ADDR": "0xff130000",
        "SHM0_DESC_BASE": "0x99c8000",
        "SHM1_DESC_BASE": "0x99cc000",
        "SHM_IMAGE_BASE": "0x99d0000",
        "SHM_IMAGE_SIZE": "0x40000",
    }
    linux_expected = {
        **common,
        "IPI_DEV_NAME": "ff350000.ipi",
        "IPI_BASE_ADDR": "0xff350000",
        "IPI_MASK": "0x200",
        "IPI_IRQ_VECT_ID": "0",
        "BUS_NAME": "platform",
    }
    r5_expected = {
        **common,
        "IPI_DEV_NAME": "ff320000.ipi",
        "IPI_BASE_ADDR": "0xff320000",
        "IPI_MASK": "0x2000000",
        "IPI_IRQ_VECT_ID": "66",
        "BUS_NAME": "generic",
    }

    linux_values = _cmake_values(linux_cmake)
    r5_values = _cmake_values(r5_cmake)
    for name, value in linux_expected.items():
        assert linux_values[name] == value
    for name, value in r5_expected.items():
        assert r5_values[name] == value
