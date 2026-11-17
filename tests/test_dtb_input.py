# Copyright (c) 2026 Advanced Micro Devices, Inc. All Rights Reserved.
# Author: Bruce Ashfield <bruce.ashfield@amd.com>
# SPDX-License-Identifier: BSD-3-Clause
"""
A dtb handed to lopper as the system device tree.

dtc keeps labels out of the nodes -- they are compile time identifiers --
and records them in /__symbols__ when asked with -@. So a tree loaded from
a dtb arrives with its labels in that node and nowhere else, where a tree
compiled from source has them on the nodes as well.

Anything that rebuilds /__symbols__ from node labels therefore has nothing
to rebuild from on this path, and drops the entries. Downstream that is not
a cosmetic loss: the baremetal assists find a processor by looking its label
up in /__symbols__, so an emptied table reports no processors at all and the
configure step that consumes it produces nothing.

This file exists because the dtb input path had no coverage. Every other
.dtb in the suite is an output.

Requires dtc; skips otherwise.
"""

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LOPPER_PY = REPO_ROOT / 'lopper.py'


def _have_tools():
    return shutil.which('dtc') is not None


pytestmark = pytest.mark.skipif(
    not _have_tools(), reason="dtb input tests require dtc")


# A labelled cpu under a labelled cluster, which is the shape the baremetal
# assists look for: they resolve a processor name like "pmc_0" through
# /__symbols__ to the node that carries it.
#
# Deliberately no xlnx,name on the cpu. get_label() falls back to it when the
# symbol lookup finds nothing, so a node carrying one can be found even with
# the table emptied -- and the test would pass whether or not the entries
# survived. The processors in the reported tree carry no xlnx,name, which is
# why the lookup had nothing to fall back to there.
SDT = """\
/dts-v1/;
/ {
    #address-cells = <1>;
    #size-cells = <1>;
    compatible = "test,sdt";

    cpus_microblaze_0: cpus_microblaze@0 {
        #address-cells = <1>;
        #size-cells = <0>;
        compatible = "cpus,cluster";

        pmc_0: cpu@0 {
            compatible = "pmc-microblaze";
            device_type = "cpu";
            reg = <0>;
        };
    };
};
"""


@pytest.fixture
def sdt_dtb(tmp_path):
    """The SDT above, compiled with -@ so the labels reach /__symbols__."""
    src = tmp_path / 'sdt.dts'
    src.write_text(SDT)
    dtb = tmp_path / 'sdt.dtb'

    r = subprocess.run(['dtc', '-@', '-I', 'dts', '-O', 'dtb',
                        '-o', str(dtb), str(src)],
                       capture_output=True, text=True)
    assert r.returncode == 0, f"dtc failed:\n{r.stderr}"

    # the premise of the whole file: the labels are in __symbols__ and the
    # nodes themselves carry none
    assert 'pmc_0' in _symbols(dtb), "dtc did not record the label"
    return dtb


def _symbols(path):
    """Return a dtb or dts file's __symbols__ as a {label: path} dict."""
    if path.suffix == '.dtb':
        r = subprocess.run(['dtc', '-I', 'dtb', '-O', 'dts', str(path)],
                           capture_output=True, text=True)
        assert r.returncode == 0, f"dtc decompile failed:\n{r.stderr}"
        text = r.stdout
    else:
        text = path.read_text()

    block = re.search(r'__symbols__\s*\{(.*?)\}', text, re.S)
    if not block:
        return {}
    return dict(re.findall(r'([\w-]+)\s*=\s*"([^"]*)"', block.group(1)))


def _run_lopper(sdt, out, outdir, *extra):
    r = subprocess.run(
        [sys.executable, str(LOPPER_PY), '-f', '--permissive',
         '-O', str(outdir), str(sdt), str(out), *extra],
        cwd=str(REPO_ROOT), capture_output=True, text=True)
    assert r.returncode == 0, f"lopper failed:\n{r.stdout}\n{r.stderr}"
    assert out.is_file(), f"no output produced at {out}"
    return out


class TestDtbInputKeepsItsSymbols:
    """A dtb's labels have to survive the trip through lopper."""

    def test_symbols_survive(self, sdt_dtb, tmp_path):
        """The entries are still there, naming the same nodes."""
        out = _run_lopper(sdt_dtb, tmp_path / 'out.dts', tmp_path)
        symbols = _symbols(out)

        assert symbols.get('pmc_0') == '/cpus_microblaze@0/cpu@0', \
            f"the cpu's symbol did not survive the load: {symbols}"
        assert symbols.get('cpus_microblaze_0') == '/cpus_microblaze@0', \
            f"the cluster's symbol did not survive the load: {symbols}"

    def test_a_processor_can_still_be_found_by_label(self, sdt_dtb, tmp_path):
        """What the emptied table actually broke.

        The baremetal assists resolve a processor name through /__symbols__.
        With the entries gone they report no processors at all, which reads
        as a tree with no cpus rather than as a lookup that failed.

        Only the lookup is asserted. The assist goes on to want an
        address-map this tree does not carry, so it fails either way -- what
        changes is whether it got far enough to fail there.
        """
        r = subprocess.run(
            [sys.executable, str(LOPPER_PY), '-f', '--permissive',
             '-O', str(tmp_path), str(sdt_dtb),
             '--', 'baremetal_bspconfig_xlnx', 'pmc_0'],
            cwd=str(REPO_ROOT), capture_output=True, text=True)

        combined = r.stdout + r.stderr
        assert 'valid Processors for a given SDT are []' not in combined, \
            "the processor list came back empty; the symbol lookup failed"
