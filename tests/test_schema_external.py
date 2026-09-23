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
from lopper.schema.learned import DTSPropertyTypeResolver, lopper_fmt_from_type_name
from lopper.schema.loader import _json_schema_to_property_type
from lopper.schema.types import PropertyType
from lopper.__main__ import resolve_schema_arguments


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


class TestOverridePrecedence:
    """Explicit overrides are consulted ahead of learned and guessed types.

    An override is a statement about a type rather than an observation of
    one, so it has to win -- correcting a type that could not be decided
    from bytes is the whole reason for supplying one.
    """

    UINT32_DEF = {'oneOf': [{'type': 'integer', 'minimum': 0, 'maximum': 0xffffffff}]}
    NODE = "/axi/memory-controller@fd070000"

    def _resolver(self, overrides, **sections):
        schema = dict(sections)
        schema['overrides'] = overrides
        return DTSPropertyTypeResolver(schema)

    def test_override_beats_a_learned_type(self):
        r = self._resolver(
            {'properties': {'xlnx,ddr-freq': 'string'}},
            property_definitions={'xlnx,ddr-freq': self.UINT32_DEF},
        )
        assert r.get_property_type('xlnx,ddr-freq', self.NODE) == LopperFmt.STRING

    def test_path_scope_beats_node_pattern_and_global(self):
        r = self._resolver({
            'properties':    {'reg': 'uint32'},
            'node_patterns': {'memory-controller@*': {'reg': 'string'}},
            'paths':         {self.NODE: {'reg': 'uint64'}},
        })
        assert r.get_property_type('reg', self.NODE) == LopperFmt.UINT64

    def test_node_pattern_beats_global(self):
        r = self._resolver({
            'properties':    {'reg': 'uint32'},
            'node_patterns': {'memory-controller@*': {'reg': 'string'}},
        })
        assert r.get_property_type('reg', self.NODE) == LopperFmt.STRING

    def test_scoped_override_does_not_leak_to_other_nodes(self):
        r = self._resolver({'paths': {self.NODE: {'reg': 'uint64'}}})
        assert r.get_property_type('reg', "/axi/other@0") != LopperFmt.UINT64

    def test_unlisted_property_is_left_alone(self):
        """An override says nothing about properties it does not name."""
        r = self._resolver(
            {'properties': {'xlnx,ddr-freq': 'uint32'}},
            property_definitions={'other-prop': self.UINT32_DEF},
        )
        assert r.get_property_type('other-prop', self.NODE) == LopperFmt.UINT32

    def test_both_shorthand_and_schema_forms_are_accepted(self):
        """Overrides are hand written, so a bare type name works too."""
        r = self._resolver({'properties': {
            'shorthand': 'uint32',
            'longhand':  self.UINT32_DEF,
        }})
        assert r.get_property_type('shorthand', self.NODE) == LopperFmt.UINT32
        assert r.get_property_type('longhand', self.NODE) == LopperFmt.UINT32


class TestTypeNames:
    """The override type vocabulary is dt-schema's, via PropertyType."""

    @pytest.mark.parametrize("name,expected", [
        ("uint32",       LopperFmt.UINT32),
        ("uint64",       LopperFmt.UINT64),
        ("string",       LopperFmt.STRING),
        ("string-array", LopperFmt.MULTI_STRING),
        ("flag",         LopperFmt.EMPTY),
        ("phandle",      LopperFmt.UINT32),
    ])
    def test_known_names_resolve(self, name, expected):
        assert lopper_fmt_from_type_name(name) == expected

    def test_unknown_name_is_an_error_listing_the_valid_ones(self):
        """A typo must not be accepted and quietly do nothing."""
        with pytest.raises(ValueError) as excinfo:
            lopper_fmt_from_type_name("uint33")

        assert "uint33" in str(excinfo.value)
        assert "uint32" in str(excinfo.value)


def test_override_corrects_an_undecidable_type_on_dtb_input(ambiguous_dtb):
    """End to end: an override fixes the cell byte guessing cannot decide."""
    dtb, _ = ambiguous_dtb
    schema = {'overrides': {'properties': {'xlnx,ddr-freq': 'uint32'}}}

    lopper.schema.initialize_lopper_properties(schema)
    lopper.schema._schema_manager.update_schema(schema)

    sdt = _load_dtb(dtb, schema)

    assert _prop(sdt.tree, "xlnx,ddr-freq").value == [0x3f2e5100]


class TestSchemaFiles:
    """Schemas are read from a file or a directory, in either accepted form."""

    LOPPER_FORM = """
overrides:
  properties:
    xlnx,ddr-freq: uint32
"""

    DT_SCHEMA_FORM = """
$id: http://devicetree.org/schemas/xlnx,zynqmp-ddrc.yaml#
$schema: http://json-schema.org/draft-07/schema#
node_pattern: memory-controller@*
properties:
  xlnx,ddrc-clk-freq-hz:
    $ref: types.yaml#/definitions/uint32
"""

    def _run(self, dtb, schema_path):
        sdt = _load_dtb(dtb, str(schema_path))
        return sdt

    def test_lopper_form_file(self, ambiguous_dtb, tmp_path):
        dtb, _ = ambiguous_dtb
        path = tmp_path / "override.yaml"
        path.write_text(self.LOPPER_FORM)

        sdt = self._run(dtb, path)

        assert _prop(sdt.tree, "xlnx,ddr-freq").value == [0x3f2e5100]

    def test_dt_schema_form_file_resolves_a_type_reference(self, ambiguous_dtb, tmp_path):
        """A binding states its types by reference into types.yaml."""
        dtb, _ = ambiguous_dtb
        path = tmp_path / "binding.yaml"
        path.write_text(self.DT_SCHEMA_FORM)

        sdt = self._run(dtb, path)

        assert _prop(sdt.tree, "xlnx,ddrc-clk-freq-hz").value == [0x1f98a480]

    def test_directory_loads_both_forms_together(self, ambiguous_dtb, tmp_path):
        """The many-files case: a directory of mixed schemas is applied as one."""
        dtb, _ = ambiguous_dtb
        schemas = tmp_path / "schemas"
        schemas.mkdir()
        (schemas / "a-override.yaml").write_text(self.LOPPER_FORM)
        (schemas / "b-binding.yaml").write_text(self.DT_SCHEMA_FORM)

        sdt = self._run(dtb, schemas)

        assert _prop(sdt.tree, "xlnx,ddr-freq").value == [0x3f2e5100]
        assert _prop(sdt.tree, "xlnx,ddrc-clk-freq-hz").value == [0x1f98a480]

    def test_a_later_file_restates_an_earlier_type(self, ambiguous_dtb, tmp_path):
        """Files apply in sorted order, so the last word wins."""
        dtb, _ = ambiguous_dtb
        schemas = tmp_path / "schemas"
        schemas.mkdir()
        (schemas / "1-first.yaml").write_text(
            "overrides:\n  properties:\n    xlnx,ddr-freq: string\n")
        (schemas / "2-second.yaml").write_text(self.LOPPER_FORM)

        sdt = self._run(dtb, schemas)

        assert _prop(sdt.tree, "xlnx,ddr-freq").value == [0x3f2e5100]


class TestSchemaFilesRefused:
    """A schema that cannot be applied says so, rather than doing nothing.

    Silently ignoring a supplied schema is the behaviour these tests exist to
    keep from coming back: it is indistinguishable from the schema being wrong.
    """

    def _expect_refusal(self, dtb, path):
        with pytest.raises(SystemExit) as excinfo:
            _load_dtb(dtb, str(path))
        assert excinfo.value.code != 0

    def test_unscoped_binding_is_refused(self, ambiguous_dtb, tmp_path):
        """A binding must say which nodes it describes.

        Its properties include generic names such as reg, whose type depends
        on the node, so applying them everywhere would corrupt trees the
        binding says nothing about.
        """
        dtb, _ = ambiguous_dtb
        path = tmp_path / "unscoped.yaml"
        path.write_text(
            "$id: http://devicetree.org/schemas/some,device.yaml#\n"
            "properties:\n"
            "  reg:\n"
            "    $ref: types.yaml#/definitions/uint32\n")

        self._expect_refusal(dtb, path)

    def test_unknown_type_name_is_refused(self, ambiguous_dtb, tmp_path):
        dtb, _ = ambiguous_dtb
        path = tmp_path / "typo.yaml"
        path.write_text("overrides:\n  properties:\n    xlnx,ddr-freq: uint33\n")

        self._expect_refusal(dtb, path)

    def test_unrecognised_form_is_refused(self, ambiguous_dtb, tmp_path):
        dtb, _ = ambiguous_dtb
        path = tmp_path / "nonsense.yaml"
        path.write_text("some-random-key: 1\n")

        self._expect_refusal(dtb, path)

    def test_empty_directory_is_refused(self, ambiguous_dtb, tmp_path):
        dtb, _ = ambiguous_dtb
        empty = tmp_path / "empty"
        empty.mkdir()

        self._expect_refusal(dtb, empty)


class TestTypeReferences:
    """dt-schema states most types by reference rather than with a json type."""

    @pytest.mark.parametrize("ref,expected", [
        ("types.yaml#/definitions/uint32",       PropertyType.UINT32),
        ("types.yaml#/definitions/uint64",       PropertyType.UINT64),
        ("types.yaml#/definitions/string",       PropertyType.STRING),
        ("types.yaml#/definitions/phandle",      PropertyType.PHANDLE),
        ("#/definitions/uint32-array",           PropertyType.UINT32_ARRAY),
    ])
    def test_reference_resolves(self, ref, expected):
        assert _json_schema_to_property_type({'$ref': ref}, 'p') == expected

    def test_reference_nested_in_a_combiner_resolves(self):
        """Bindings commonly wrap a reference alongside a constraint."""
        spec = {'allOf': [{'$ref': 'types.yaml#/definitions/uint32'},
                          {'maximum': 100}]}
        assert _json_schema_to_property_type(spec, 'p') == PropertyType.UINT32

    def test_unresolvable_reference_is_not_guessed_at(self):
        spec = {'$ref': 'types.yaml#/definitions/not-a-type'}
        assert _json_schema_to_property_type(spec, 'p') == PropertyType.UNKNOWN


class TestInlineStatements:
    """A type can be stated on the command line, without writing a file.

    The separator is ';' rather than ',' because property names contain
    commas: "xlnx,ddr-freq" would otherwise be split in half.
    """

    def test_a_bare_name_states_the_type_wherever_it_appears(self, ambiguous_dtb):
        dtb, _ = ambiguous_dtb
        sdt = _load_dtb(dtb, ["type:xlnx,ddr-freq=uint32"])

        assert _prop(sdt.tree, "xlnx,ddr-freq").value == [0x3f2e5100]

    def test_a_node_pattern_scopes_the_statement(self, ambiguous_dtb):
        dtb, _ = ambiguous_dtb
        sdt = _load_dtb(dtb, ["type:memory-controller@*/xlnx,ddr-freq=uint32"])

        assert _prop(sdt.tree, "xlnx,ddr-freq").value == [0x3f2e5100]

    def test_a_leading_separator_makes_it_an_exact_path(self, ambiguous_dtb):
        dtb, _ = ambiguous_dtb
        sdt = _load_dtb(dtb, [f"type:{MC_PATH}/xlnx,ddr-freq=uint32"])

        assert _prop(sdt.tree, "xlnx,ddr-freq").value == [0x3f2e5100]

    def test_several_statements_in_one_argument(self, ambiguous_dtb):
        dtb, _ = ambiguous_dtb
        sdt = _load_dtb(dtb, ["type:xlnx,ddr-freq=uint32;xlnx,ip-name=string"])

        assert _prop(sdt.tree, "xlnx,ddr-freq").value == [0x3f2e5100]
        assert _prop(sdt.tree, "xlnx,ip-name").value == ['psu_ddrc']

    def test_a_comma_in_a_property_name_survives(self, ambiguous_dtb):
        """The separator must not split a vendor prefixed name."""
        dtb, _ = ambiguous_dtb
        sdt = _load_dtb(dtb, ["type:xlnx,ddr-freq=uint32;xlnx,ddrc-clk-freq-hz=uint32"])

        assert _prop(sdt.tree, "xlnx,ddr-freq").value == [0x3f2e5100]
        assert _prop(sdt.tree, "xlnx,ddrc-clk-freq-hz").value == [0x1f98a480]

    def test_sources_apply_in_the_order_given(self, ambiguous_dtb):
        dtb, _ = ambiguous_dtb
        sdt = _load_dtb(dtb, ["type:xlnx,ddr-freq=string",
                              "type:xlnx,ddr-freq=uint32"])

        assert _prop(sdt.tree, "xlnx,ddr-freq").value == [0x3f2e5100]

    @pytest.mark.parametrize("spec", [
        "type:broken",              # no type given
        "type:=uint32",             # no property named
        "type:xlnx,ddr-freq=",      # no type named
        "type:xlnx,ddr-freq=nope",  # not a type
        "type:scope/=uint32",       # scope, but no property
    ])
    def test_a_malformed_statement_is_refused(self, ambiguous_dtb, spec):
        dtb, _ = ambiguous_dtb
        with pytest.raises(SystemExit) as excinfo:
            _load_dtb(dtb, [spec])
        assert excinfo.value.code != 0


class TestPartialSchema:
    """Stating one type must not restate any others.

    Before the resolver stopped guessing from names, supplying a schema at
    all meant every property it did not mention was typed by the ".*,.*"
    heuristic, which calls any vendor prefixed name a string. Correcting one
    property therefore broke its neighbours, and a partial schema was worse
    than none -- which is the only kind of schema anyone writes by hand.
    """

    def test_neighbours_keep_the_type_they_had(self, ambiguous_dtb):
        dtb, _ = ambiguous_dtb
        sdt = _load_dtb(dtb, ["type:xlnx,ddr-freq=uint32"])

        assert _prop(sdt.tree, "xlnx,ddr-freq").value == [0x3f2e5100]
        # numeric, and not named by the schema
        assert _prop(sdt.tree, "xlnx,ddrc-clk-freq-hz").value == [0x1f98a480]
        # a string, and also not named by the schema
        assert _prop(sdt.tree, "xlnx,ip-name").value == ['psu_ddrc']

    def test_only_the_named_property_differs_from_an_unschemaed_run(self, ambiguous_dtb):
        """Everything the schema is silent about reads as it would with none."""
        dtb, _ = ambiguous_dtb

        bare = _load_dtb(dtb, None)
        bare_values = { p.name: p.value for p in bare.tree[MC_PATH] }

        sdt = _load_dtb(dtb, ["type:xlnx,ddr-freq=uint32"])
        values = { p.name: p.value for p in sdt.tree[MC_PATH] }

        differing = { name for name, value in values.items()
                      if bare_values.get(name) != value }

        assert differing == {"xlnx,ddr-freq"}

    def test_an_unknown_property_is_left_to_the_byte_guess(self):
        """The resolver says it has no opinion rather than guessing a name."""
        resolver = DTSPropertyTypeResolver({'overrides': {'properties': {'a': 'uint32'}}})

        assert resolver.get_property_type('xlnx,some-unlisted-thing',
                                          MC_PATH) == LopperFmt.UNKNOWN


class TestSchemaArgumentResolution:
    """--schema is repeatable, but the modes that are not schemas are not."""

    def test_no_argument_learns(self):
        assert resolve_schema_arguments([]) == "learn"

    def test_none_disables(self):
        assert resolve_schema_arguments(["none"]) is None

    def test_schemas_accumulate_in_order(self, tmp_path):
        first = tmp_path / "a.yaml"
        first.write_text("overrides:\n  properties:\n    a: uint32\n")

        resolved = resolve_schema_arguments([str(first), "type:b=string"])

        assert resolved == [str(first), "type:b=string"]

    @pytest.mark.parametrize("mode", ["none", "learn"])
    def test_a_mode_cannot_be_combined_with_a_schema(self, mode):
        """Mixing them asks for two different things at once."""
        with pytest.raises(SystemExit):
            resolve_schema_arguments([mode, "type:a=uint32"])

    def test_a_missing_schema_path_is_refused(self, tmp_path):
        with pytest.raises(SystemExit):
            resolve_schema_arguments([str(tmp_path / "absent.yaml")])


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
