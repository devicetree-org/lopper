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

from lopper import Lopper, LopperSDT


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


def _enhanced_output(tmp_path, source, name):
    """Compile with enhanced processing and return the written dts."""
    dts = tmp_path / f"{name}.dts"
    dts.write_text(source)
    out = tmp_path / f"{name}-out.dts"

    sdt = LopperSDT(str(dts))
    sdt.dryrun = False
    sdt.enhanced = True
    sdt.setup(str(dts), [], "", force=True)
    sdt.write(output_filename=str(out), enhanced=True)

    return out.read_text()


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


def test_a_kept_comment_reaches_the_output(tmp_path):
    """Preserving a comment through the compile is only half of it.

    A block comment's text opens with '/*', and strict mode prunes properties
    whose value looks like a path to a node that is not there. Every comment
    the whole mechanism exists to carry is held in such a property, so reading
    them as paths threw all of them away on the way out -- in the default mode,
    whatever position the comment was in.
    """
    source = """/dts-v1/;

/ {
    /* a comment in a position a property can occupy */
    akeeper = <1>;

    anode {
        compatible = "test,anode";
    };
};
"""
    dts = tmp_path / "kept.dts"
    dts.write_text(source)
    out = tmp_path / "kept-out.dts"

    sdt = LopperSDT(str(dts))
    sdt.dryrun = False
    sdt.enhanced = True
    sdt.setup(str(dts), [], "", force=True)
    sdt.write(output_filename=str(out), enhanced=True)

    assert "a comment in a position a property can occupy" in out.read_text()


def test_comment_after_a_subnode_keeps_its_place(tmp_path):
    """A comment following a subnode survives, where it was written.

    dts wants a node's properties before its subnodes, so the comment cannot
    stay a property here. A node is legal after a node and holds its place
    among the siblings, so the comment is carried in one and unwrapped on the
    way out -- still between the two nodes it was written between.
    """
    source = """/dts-v1/;

/ {
    before = <1>;

    anode {
        p = <1>;
    };
    /* between anode and bnode, where it was written */
    bnode {
        q = <2>;
    };
};
"""
    dts = tmp_path / "between.dts"
    dts.write_text(source)
    out = tmp_path / "between-out.dts"

    sdt = LopperSDT(str(dts))
    sdt.dryrun = False
    sdt.enhanced = True
    sdt.setup(str(dts), [], "", force=True)
    sdt.write(output_filename=str(out), enhanced=True)

    text = out.read_text()
    assert "between anode and bnode" in text

    lines = [l.strip() for l in text.splitlines() if l.strip()]
    anode = next(i for i, l in enumerate(lines) if l.startswith("anode"))
    comment = next(i for i, l in enumerate(lines) if "between anode and bnode" in l)
    bnode = next(i for i, l in enumerate(lines) if l.startswith("bnode"))
    assert anode < comment < bnode

    # the node that carried it is an artifact of the compile, not of the output
    assert "lopper-comment-container" not in text


class TestPositionIsFoundWithoutReadingContents:
    """Where a comment sits is decided without reading what it says.

    The scan counts braces to track depth, and by the time it runs a comment
    is a property holding whatever the author wrote. Reading those lines as
    structure lets a comment's own braces move the depth, and every comment
    after it is then judged against a depth the tree never reached.
    """

    def test_node_written_on_one_line(self, tmp_path):
        """"a { };" opens and closes on the same line.

        The closing brace is not at the start of the line, so a close check
        anchored there never sees it and the depth climbs and stays. The
        comment that follows is then not recognised as being after a subnode,
        is left in place, and dtc rejects it.
        """
        source = """/dts-v1/;

/ {
    anode { };
    /* after a subnode, so this cannot stay */
};
"""
        assert _compile(tmp_path, source, "one-line")

    def test_commented_out_node_does_not_move_the_depth(self, tmp_path):
        """A commented out node is text, not structure."""
        source = """/dts-v1/;

/ {
    before = <1>;
    /*
    &some_label {
        phy-mode = "10gbase-r";
    };
    */
    after = <2>;
};
"""
        assert _compile(tmp_path, source, "commented-node")

    def test_unbalanced_brace_does_not_strand_later_comments(self, tmp_path):
        """An unbalanced brace in a comment used to skew everything after it.

        The two comments get different treatment, and both verdicts are only
        right if the first one's brace was not counted as structure. The first
        is somewhere a property can live and is left as one. The second follows
        a subnode, where a property is not allowed, so it is carried in a node.
        """
        source = """/dts-v1/;

/ {
    before = <1>;
    /* FIRST: a stray brace, as in commented out code: if (x) { do(); */
    anode { };
    /* SECOND: after a subnode */
};
"""
        _compile(tmp_path, source, "unbalanced", save_temps=True)

        text = (tmp_path / "unbalanced.dts.pp.enhanced").read_text()
        first = next(l for l in text.splitlines() if "FIRST" in l)
        second = next(l for l in text.splitlines() if "SECOND" in l)

        assert "lopper-comment-container" not in first, \
            "a comment a property position accepts should stay a property"
        assert "lopper-comment-container" in second, \
            "a comment after a subnode should be carried in a node"


class TestCommentsNeverBreakTheBuild:
    """dtc decides what is legal, and a wrong guess costs a comment.

    Whether a carried comment may stay where it is gets decided by reading the
    preprocessed text rather than parsing it, so it can be wrong. When it is,
    dtc rejects the source. Rather than failing, the comments are dropped and
    the compile is retried: the tree is what was asked for, a comment is not.
    """

    def test_dropping_comments_leaves_valid_dts(self, tmp_path):
        """The retreat removes the property and nothing else."""
        source = ('/dts-v1/;\n/ {\n\tanode {\n\t};\n'
                  '\tlopper-comment-1 = "/* somewhere dts does not allow */";\n};\n')
        f = tmp_path / "carried.dts"
        f.write_text(source)

        removed = Lopper.drop_comments_from(str(f))

        assert removed == 1
        text = f.read_text()
        assert "lopper-comment" not in text
        assert "anode" in text, "the tree must survive the retreat untouched"

    def test_a_comment_body_is_not_cut_short(self, tmp_path):
        """A comment carrying its own quotes is removed whole.

        The body is a dts string with its quotes escaped, so a plain
        non-greedy match ends at the first quote inside the text and leaves
        the rest behind -- which is what made the compile fail in the first
        place.
        """
        source = ('/dts-v1/;\n/ {\n'
                  '\tlopper-comment-1 = "/* phy-mode = \\"10gbase-r\\"; */";\n};\n')
        f = tmp_path / "quoted.dts"
        f.write_text(source)

        assert Lopper.drop_comments_from(str(f)) == 1

        text = f.read_text()
        assert "10gbase-r" not in text
        assert "*/" not in text, "no tail of the comment may be left behind"

    def test_nothing_to_drop_is_reported_as_such(self, tmp_path):
        """A source with no carried comments is left alone."""
        source = '/dts-v1/;\n/ {\n\tanode {\n\t};\n};\n'
        f = tmp_path / "plain.dts"
        f.write_text(source)
        before = f.read_text()

        assert Lopper.drop_comments_from(str(f)) == 0
        assert f.read_text() == before


class TestCommentTextSurvivesVerbatim:
    """What the author wrote is what comes back.

    The comment becomes a dts string, so the characters dts gives meaning to
    have to be escaped going in. Quotes were; backslashes were not, and a
    backslash is both an escape introducer and, immediately before the closing
    quote, a way to swallow it.
    """

    def test_trailing_backslash_is_not_eaten(self, tmp_path):
        """A backslash before the closing delimiter used to escape it."""
        source = '/dts-v1/;\n\n/ {\n    /* ends with a backslash \\ */\n    p = <1>;\n};\n'
        out = _enhanced_output(tmp_path, source, "trailing-bs")

        assert "ends with a backslash \\ */" in out

    def test_escape_sequences_are_not_interpreted(self, tmp_path):
        r"""'C:\temp\new' is a path, not a tab and a newline."""
        source = '/dts-v1/;\n\n/ {\n    /* a path C:\\temp\\new */\n    p = <1>;\n};\n'
        out = _enhanced_output(tmp_path, source, "escapes")

        assert "C:\\temp\\new" in out
        assert "C:\temp" not in out, "the \\t must not become a tab"

    def test_a_quote_and_a_backslash_together(self, tmp_path):
        """Escaping one must not corrupt the other: backslashes go first."""
        source = '/dts-v1/;\n\n/ {\n    /* mode = "x" under C:\\y */\n    p = <1>;\n};\n'
        out = _enhanced_output(tmp_path, source, "both")

        assert 'mode = "x" under C:\\y' in out
