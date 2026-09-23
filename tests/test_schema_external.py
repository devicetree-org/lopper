"""
Tests for schemas supplied to lopper, rather than learned from dts source.

A schema is learned while compiling dts source. A dtb has no source to learn
from, so its property types fall back to byte level guessing -- and that guess
is not always decidable. A cell whose bytes happen to be printable ascii
followed by NUL is byte-identical to a short null terminated string:

    0x3f2e5100  ->  3f 2e 51 00  ->  "?.Q\\0"

No improvement to the guesser can separate those two readings. The type has to
come from a schema, which means a schema has to be able to reach the decode
path for dtb input.

It did not. The schema handling block lives in the dts-compile branch of
LopperSDT.setup(), while a dtb takes the other branch and called
Lopper.export() with no schema at all, so a schema was accepted and then
silently ignored.

Copyright (C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.

SPDX-License-Identifier: BSD-3-Clause

Author:
    Bruce Ashfield <bruce.ashfield@amd.com>
"""

import pytest

import lopper
import lopper.schema
from lopper import Lopper, LopperSDT
from lopper.fmt import LopperFmt


# 0x3f2e5100 decodes as the string "?.Q" if guessed from bytes alone.
# 0x1f98a480 has a non-printable byte, so byte guessing reads it correctly.
AMBIGUOUS_DTS = """
/dts-v1/;

/ {
    #address-cells = <2>;
    #size-cells = <2>;

    axi {
        #address-cells = <2>;
        #size-cells = <2>;

        memory-controller@fd070000 {
            compatible = "xlnx,zynqmp-ddrc-2.40a";
            xlnx,ddr-freq = <0x3f2e5100>;
            xlnx,ddrc-clk-freq-hz = <0x1f98a480>;
            xlnx,ip-name = "psu_ddrc";
        };
    };
};
"""

MC_PATH = "/axi/memory-controller@fd070000"


@pytest.fixture
def ambiguous_dtb(tmp_path):
    """Compile the ambiguous tree, returning (dtb path, schema learned from it)."""
    dts = tmp_path / "ambiguous.dts"
    dts.write_text(AMBIGUOUS_DTS)

    dtb, schema = Lopper.dt_compile(str(dts), "", "", True, str(tmp_path))
    return str(dtb), schema


@pytest.fixture(autouse=True)
def clean_schema_manager():
    """Keep the global schema manager from leaking between tests.

    The resolver used by the decode path is reached through the module level
    manager rather than through anything passed in, so a schema installed by
    one test would otherwise still be active in the next.

    The fields are saved and restored directly rather than through
    update_schema(), which builds a resolver from whatever it is handed and so
    cannot be used to restore an unset schema.
    """
    mgr = lopper.schema._schema_manager
    saved = (mgr.schema, mgr.resolver, mgr.checker, mgr.validator, mgr.schema_hash)
    yield
    (mgr.schema, mgr.resolver, mgr.checker,
     mgr.validator, mgr.schema_hash) = saved


def _prop(tree, name):
    return tree[MC_PATH].props(f"^{name}$")[0]


def _load_dtb(dtb, schema):
    sdt = LopperSDT(dtb)
    sdt.dryrun = False
    sdt.schema = schema
    sdt.setup(dtb, [], "", force=True)
    return sdt


def test_byte_guess_misreads_printable_cell(ambiguous_dtb):
    """Without a schema the ambiguous cell is read as a string.

    This is the reported behaviour, pinned so that the schema cases below are
    demonstrably fixing something rather than asserting what already happened.
    """
    dtb, _ = ambiguous_dtb
    sdt = _load_dtb(dtb, None)

    assert _prop(sdt.tree, "xlnx,ddr-freq").value == ['?.Q']


def test_schema_reaches_dtb_decode(ambiguous_dtb):
    """A schema supplied for dtb input types the ambiguous cell correctly."""
    dtb, schema = ambiguous_dtb

    lopper.schema.initialize_lopper_properties(schema)
    lopper.schema._schema_manager.update_schema(schema)

    sdt = _load_dtb(dtb, schema)

    freq = _prop(sdt.tree, "xlnx,ddr-freq")
    assert freq.value == [0x3f2e5100]
    assert freq.ptype == LopperFmt.UINT32


def test_schema_is_recorded_on_the_tree(ambiguous_dtb):
    """The dtb path records the schema on the tree, as the dts path does."""
    dtb, schema = ambiguous_dtb

    lopper.schema.initialize_lopper_properties(schema)
    lopper.schema._schema_manager.update_schema(schema)

    sdt = _load_dtb(dtb, schema)

    assert sdt.tree.schema is schema


def test_unambiguous_cell_is_unaffected(ambiguous_dtb):
    """A value byte guessing already read correctly stays correct.

    Guards the decode path against a schema changing properties it has no
    opinion about.
    """
    dtb, schema = ambiguous_dtb

    lopper.schema.initialize_lopper_properties(schema)
    lopper.schema._schema_manager.update_schema(schema)

    sdt = _load_dtb(dtb, schema)

    assert _prop(sdt.tree, "xlnx,ddrc-clk-freq-hz").value == [0x1f98a480]
    assert _prop(sdt.tree, "xlnx,ip-name").value == ['psu_ddrc']


def test_learn_request_on_dtb_does_not_write_a_schema(ambiguous_dtb, tmp_path, caplog):
    """Asking to learn from a dtb warns instead of silently writing nothing.

    There is no source to learn from, so the requested output file was never
    produced and nothing said so.
    """
    dtb, _ = ambiguous_dtb
    out = tmp_path / "learned.yaml"

    sdt = _load_dtb(dtb, ("learn_dump", str(out)))

    assert not out.exists()
    assert sdt.schema is None
    assert "no source to learn from" in caplog.text
