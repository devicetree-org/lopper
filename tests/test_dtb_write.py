# Copyright (c) 2026 Advanced Micro Devices, Inc. All Rights Reserved.
# Author: Bruce Ashfield <bruce.ashfield@amd.com>
# SPDX-License-Identifier: BSD-3-Clause
"""
dtb output must describe the same tree the dts output does.

The two writers took different routes: the .dts path resolved the tree
before printing it, the .dtb path exported straight to an fdt. Resolution
is where a node that moved during lop or assist processing picks up its
new path, and where the symbol table is rebuilt from node labels, so the
dtb was written from a tree that still described the pre-move layout.

That matters for symbols specifically. A dtb carrying a symbol that names
a path which no longer exists is worse than one carrying no symbols at
all: an overlay applied against it resolves its fixups onto the stale
path instead of failing, which is how a DFX reconfigurable region ends up
targeting nothing.

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


def _have_tools():
    return (shutil.which('cpp') is not None
            and shutil.which('dtc') is not None)


pytestmark = pytest.mark.skipif(
    not _have_tools(),
    reason="dtb write tests require cpp and dtc")


SDT = """\
/dts-v1/;
/ {
    #address-cells = <2>;
    #size-cells = <2>;
    compatible = "test,sdt";

    amba_pl: amba_pl@0 {
        compatible = "simple-bus";
        #address-cells = <2>;
        #size-cells = <2>;
        ranges;

        fpga_PRVitisRegion: fpga-PRVitisRegion {
            compatible = "fpga-region";
            #address-cells = <2>;
            #size-cells = <2>;
            ranges;
        };
    };
};
"""

# relocate the labelled region out of its bus and up to root, which is what
# the linux device-tree assists do to a reconfigurable region
LOP_MOVE = """\
/dts-v1/;
/ {
    compatible = "system-device-tree-v1,lop";
    lops {
        compatible = "system-device-tree-v1,lop";
        lop_move_region {
            compatible = "system-device-tree-v1,lop,modify";
            modify = "/amba_pl@0/fpga-PRVitisRegion::/fpga-PRVitisRegion";
        };
    };
};
"""


def _run_lopper(sdt, lop, out, outdir):
    r = subprocess.run(
        [sys.executable, str(LOPPER_PY), '-f', '--symbols', '--enhanced',
         '-O', str(outdir), '-i', str(lop), str(sdt), str(out)],
        cwd=str(REPO_ROOT), capture_output=True, text=True)
    assert r.returncode == 0, f"lopper failed:\n{r.stdout}\n{r.stderr}"
    assert out.is_file(), f"no output produced at {out}"
    return out


def _symbols(path):
    """Return the output's __symbols__ as a {label: path} dict."""
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


@pytest.fixture
def moved_outputs(tmp_path):
    """Same tree, same lop, written both ways."""
    sdt = tmp_path / 'sdt.dts'
    sdt.write_text(SDT)
    lop = tmp_path / 'lop-move.dts'
    lop.write_text(LOP_MOVE)

    return {
        'dts': _symbols(_run_lopper(sdt, lop, tmp_path / 'out.dts', tmp_path)),
        'dtb': _symbols(_run_lopper(sdt, lop, tmp_path / 'out.dtb', tmp_path)),
    }


class TestDtbSymbolsAfterMove:
    """A relocated node's symbol must name where the node actually ended up."""

    def test_dts_symbol_tracks_the_move(self, moved_outputs):
        """Baseline: the dts path has always resolved before printing."""
        assert moved_outputs['dts'].get('fpga_PRVitisRegion') == \
            '/fpga-PRVitisRegion'

    def test_dtb_symbol_tracks_the_move(self, moved_outputs):
        """The dtb must not keep pointing into the bus the node left."""
        got = moved_outputs['dtb'].get('fpga_PRVitisRegion')
        assert got == '/fpga-PRVitisRegion', \
            f"dtb symbol names a stale path: {got!r}"

    def test_dtb_symbol_path_exists(self, moved_outputs):
        """A symbol naming a path nothing lives at is the real failure."""
        assert '/amba_pl@0/fpga-PRVitisRegion' not in \
            moved_outputs['dtb'].values()

    def test_both_writers_agree(self, moved_outputs):
        """Neither format should be the odd one out, whatever the paths are."""
        assert moved_outputs['dtb'] == moved_outputs['dts']

    def test_untouched_symbol_survives(self, moved_outputs):
        """Resolving must not cost us symbols for nodes that never moved."""
        assert moved_outputs['dtb'].get('amba_pl') == '/amba_pl@0'
