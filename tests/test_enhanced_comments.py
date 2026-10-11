"""
Tests for comment handling in enhanced processing.

dtc discards comments, so --enhanced carries them through the compile by
rewriting each one into a numbered property and turning it back into a comment
on the way out. A property is not legal everywhere a comment is, though, so a
comment that lands somewhere a property cannot go -- before any node, after the
first subnode at its depth, inside another property -- is dropped rather than
emitted as something dtc would reject.

Dropping it has to be complete. The comment body is a dts string and the quotes
inside it are escaped when the property is built, so the removal has to honour
that escaping to find where the property actually ends. A comment carrying its
own quoted text would otherwise be cut at the first quote in the body, leaving
the remainder in the preprocessed file as bare text and failing the compile at a
line that looks unrelated to the comment.

Copyright (C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.

SPDX-License-Identifier: BSD-3-Clause

Author:
    Bruce Ashfield <bruce.ashfield@amd.com>
"""

import os
import shutil

import pytest

from lopper import Lopper


# Comments only reach lopper if the preprocessor passes them through. pcpp does,
# via --passthru-comments; the system cpp strips them before lopper ever sees
# them, which makes every test in this file vacuous rather than failing. Skip
# instead of passing for nothing. This is the same lookup base.py does.
_preprocessor = (os.environ.get('LOPPER_CPP') or shutil.which("pcpp")
                 or shutil.which("pcpp-python") or "")

pytestmark = pytest.mark.skipif(
    "pcpp" not in os.path.basename(_preprocessor),
    reason="needs pcpp: the system cpp strips comments before lopper sees them")


# A comment at the top level is in a position a property cannot occupy, so it
# gets dropped. This one carries a quoted string, which is what makes the
# removal non-trivial.
#
# It has to come after the root node rather than before it: a comment ahead of
# any node is taken as the preamble and kept as lopper-preamble, which is a
# legal property and never reaches the deletion path.
TOP_LEVEL_QUOTED_COMMENT = """/dts-v1/;

/ {
    #address-cells = <1>;
    #size-cells = <1>;

    anode {
        compatible = "test,anode";
    };
};

/*
&some_label {
    phy-mode = "10gbase-r";
    fixed-link {
        speed = <10000>;
    };
};
*/
"""

# The same quoted text, but inside a node where a property is legal, so it is
# kept rather than dropped.
IN_NODE_QUOTED_COMMENT = """/dts-v1/;

/ {
    #address-cells = <1>;
    #size-cells = <1>;

    anode {
        /* phy-mode = "10gbase-r"; */
        compatible = "test,anode";
    };
};
"""


def _compile(tmp_path, source, name, save_temps=False):
    dts = tmp_path / f"{name}.dts"
    dts.write_text(source)

    dtb, _schema = Lopper.dt_compile(str(dts), "", "", True, str(tmp_path),
                                     save_temps, enhanced=True)
    return dtb


def test_dropped_comment_carrying_quotes_compiles(tmp_path):
    """A dropped comment takes its whole body with it.

    dt_compile() exits non-zero when dtc rejects its input, so reaching the
    assertion at all is most of the test: an incomplete removal leaves the tail
    of the comment behind and the compile fails.
    """
    dtb = _compile(tmp_path, TOP_LEVEL_QUOTED_COMMENT, "top-level")

    assert dtb


def test_dropped_comment_leaves_nothing_behind(tmp_path):
    """Nothing of the comment survives into the preprocessed source."""
    _compile(tmp_path, TOP_LEVEL_QUOTED_COMMENT, "residue", save_temps=True)

    preprocessed = tmp_path / "residue.dts.pp.enhanced"
    assert preprocessed.exists(), "save_temps should retain the preprocessed source"

    text = preprocessed.read_text()
    assert "lopper-comment" not in text
    assert "10gbase-r" not in text
    assert "fixed-link" not in text


def test_comment_in_a_legal_position_is_kept(tmp_path):
    """The quotes are not the problem -- the position is.

    The same text inside a node is somewhere a property can live, so it is
    never tagged for removal and survives the round trip.
    """
    dtb = _compile(tmp_path, IN_NODE_QUOTED_COMMENT, "in-node")

    assert dtb
