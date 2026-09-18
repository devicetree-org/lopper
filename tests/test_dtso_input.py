"""
Tests for .dtso input handling.

.dtso is the Linux overlay-source convention. Lopper already held overlays
rather than merging them (is_overlay_file() -> _compile_overlay_subtrees()
-> tree._metadata['overlay_subtrees']), but only reached that path for
files named .dts or .dtsi: the CLI gate in __main__.py rejected the .dtso
extension outright, so the .dtso clause inside is_overlay_file() was
unreachable from the command line.

These tests cover both directions of that:

  - an overlay passed with -i is accepted and held, and .dtso produces the
    same result as the identical content named .dts
  - an overlay passed as the *system device tree* is refused with an
    explanation, rather than falling through to the binary loader and
    surfacing FDT_ERR_BADMAGIC

Copyright (C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.

SPDX-License-Identifier: BSD-3-Clause

Author:
    Bruce Ashfield <bruce.ashfield@amd.com>
"""

import os
import pytest
from lopper import LopperSDT


BASE_DTS = """/dts-v1/;
/ {
    #address-cells = <1>;
    #size-cells = <1>;
    compatible = "test";

    amba_pl: amba_pl {
        compatible = "simple-bus";
        #address-cells = <1>;
        #size-cells = <1>;
        ranges;

        dev0: dev@a0000000 {
            compatible = "test,dev";
            reg = <0xa0000000 0x1000>;
        };
    };
};
"""

OVERLAY = """/dts-v1/;
/plugin/;

&amba_pl {
    newdev: newdev@b0000000 {
        compatible = "test,new";
        reg = <0xb0000000 0x1000>;
    };
};
"""


def _write(d, name, content):
    p = os.path.join(str(d), name)
    with open(p, "w") as f:
        f.write(content)
    return p


def _run(tmp_path, sdt_path, input_files, out_name):
    sdt = LopperSDT(sdt_path)
    sdt.dryrun = False
    sdt.verbose = 0
    sdt.werror = False
    sdt.output_file = os.path.join(str(tmp_path), out_name)
    sdt.cleanup_flag = True
    sdt.save_temps = False
    sdt.enhanced = True
    sdt.outdir = str(tmp_path)
    sdt.setup(sdt.dts, input_files, "", True, libfdt=True)
    return sdt


class TestDtsoAsInput:
    """An overlay passed with -i is accepted and held, not merged."""

    def test_dtso_is_held_not_merged(self, tmp_path):
        base = _write(tmp_path, "base.dts", BASE_DTS)
        ov = _write(tmp_path, "ov.dtso", OVERLAY)

        sdt = _run(tmp_path, base, [ov], "out.dts")

        held = sdt.tree._metadata.get("overlay_subtrees", {})
        assert "ov" in held, \
            ".dtso input was not held as an overlay subtree - the CLI " \
            "classifier is rejecting the extension again"

        # the base tree must be untouched: the overlay is held for later
        # retrieval, not applied
        amba = sdt.tree["/amba_pl"]
        names = {c.name for c in amba.subnodes(children_only=True)}
        assert "newdev@b0000000" not in names, \
            "the overlay was merged into the base tree; it should be held"
        sdt.cleanup()

    def test_dtso_matches_dts_with_same_content(self, tmp_path):
        """.dtso and .dts carrying the same overlay behave identically.

        The .dtso extension is only a naming convention; it must join the
        existing overlay path rather than creating a second one.
        """
        b1 = _write(tmp_path, "base1.dts", BASE_DTS)
        o1 = _write(tmp_path, "same.dtso", OVERLAY)
        s1 = _run(tmp_path, b1, [o1], "out1.dts")
        held1 = set(s1.tree._metadata.get("overlay_subtrees", {}).keys())
        s1.cleanup()

        b2 = _write(tmp_path, "base2.dts", BASE_DTS)
        o2 = _write(tmp_path, "same.dts", OVERLAY)
        s2 = _run(tmp_path, b2, [o2], "out2.dts")
        held2 = set(s2.tree._metadata.get("overlay_subtrees", {}).keys())
        s2.cleanup()

        assert held1 == held2 == {"same"}, \
            f".dtso and .dts diverged: {held1} vs {held2}"


class TestDtsoAsSystemDeviceTree:
    """An overlay is not a base tree, and saying so is the useful answer."""

    def test_dtso_as_sdt_is_refused(self, tmp_path):
        """Refused with an explanation, not FDT_ERR_BADMAGIC.

        Before this was caught explicitly, an unrecognised extension fell
        through to the terminal else, which assumes a dtb and hands the
        file to libfdt. The caller got a raw FdtException telling them
        nothing about what was wrong.
        """
        ov = _write(tmp_path, "solo.dtso", OVERLAY)

        sdt = LopperSDT(ov)
        sdt.dryrun = False
        sdt.verbose = 0
        sdt.werror = False
        sdt.output_file = os.path.join(str(tmp_path), "out.dts")
        sdt.cleanup_flag = True
        sdt.save_temps = False
        sdt.enhanced = True
        sdt.outdir = str(tmp_path)

        with pytest.raises(SystemExit) as excinfo:
            sdt.setup(sdt.dts, [], "", True, libfdt=True)

        assert excinfo.value.code == 1, \
            "an overlay passed as the system device tree should exit(1)"
