"""The ZynqMP OpenAMP demo overlays generate remoteproc on the in-tree SDT.

demos/openamp/inputs/openamp-overlay-zynqmp*.yaml are written against
demos/openamp/inputs/openamp_zu.dts. Each is expanded and run through the
OpenAMP assist for Linux on A53-0, and the remoteproc cluster checked.
"""

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


pytestmark = pytest.mark.skipif(
    shutil.which("dtc") is None,
    reason="OpenAMP demo overlay generation requires dtc",
)


def _run(args):
    env = os.environ.copy()
    env["LOPPER_DTC_FLAGS"] = "-b 0 -@"
    result = subprocess.run(
        [sys.executable, str(LOPPER), *map(str, args)],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True)
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "[ERROR]" not in output, output


def _node(dts, name):
    """The body of the first node called name, up to its closing brace."""
    match = re.search(rf"\n([ \t]+){re.escape(name)} \{{\n(.*?)\n\1\}};",
                      dts, re.S)
    assert match, f"{name} not found"
    return match.group(2)


@pytest.mark.parametrize("overlay, cores", [
    ("openamp-overlay-zynqmp.yaml", {
        "r5f@0": ('"atcm0", "btcm0"', "rproc09800000", "psu_ipi_7_1"),
        "r5f@1": ('"atcm1", "btcm1"', "rproc19e00000", "psu_ipi_7_2"),
    }),
    ("openamp-overlay-zynqmp-lockstep.yaml", {
        "r5f@0": ('"atcm0", "btcm0", "atcm1", "btcm1"', "rproc03ed00000",
                  "psu_ipi_7_1"),
    }),
])
def test_zynqmp_demo_overlay_generates_remoteproc(tmp_path, overlay, cores):
    expanded = tmp_path / "expanded.dts"
    linux = tmp_path / "linux.dts"
    _run(["-f", "--enhanced", "--auto", "-i", INPUTS / overlay, SDT,
          expanded])
    _run(["-f", "--enhanced", expanded, linux,
          "--", "openamp", "psu_cortexa53_0", "linux_dt"])

    cluster = _node(linux.read_text(), "remoteproc@ffe00000")
    lockstep = "<0x1>" if len(cores) == 1 else "<0x0>"
    assert f"xlnx,cluster-mode = {lockstep};" in cluster
    assert sorted(re.findall(r"\s(r5f@\d) \{", cluster)) == sorted(cores)
    for core, (reg_names, region, mbox) in cores.items():
        body = _node(cluster, core)
        assert f"reg-names = {reg_names};" in body
        assert f"memory-region = <&{region}>" in body
        assert f"mboxes = <&{mbox} 0x0>" in body
