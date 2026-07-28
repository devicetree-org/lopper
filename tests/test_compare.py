"""Tests for lopper.tree_compare — structural tree comparison.

Phase 1 scope: path-keyed node matching, literal property-value
comparison, exclusion of derived/bookkeeping nodes. Phandle
normalization, alternate keys and renderers are later phases.
"""

import pytest

from lopper import Lopper, LopperSDT
from lopper.tree import LopperTree
import lopper.tree_compare
import lopper.assists.compare as compare_assist


# --- helpers --------------------------------------------------------------

def _tree_from_dts(dts_text, tmp_path, name):
    """Compile a DTS string and load it into a LopperTree."""
    dts = tmp_path / f"{name}.dts"
    dts.write_text(dts_text)
    compiled, _ = Lopper.dt_compile(str(dts), "", "", True, str(tmp_path))
    try:
        import libfdt  # noqa: F401
        fdt = Lopper.dt_to_fdt(compiled)
    except ImportError:
        fdt = compiled
    tree = LopperTree()
    tree.load(Lopper.export(fdt))
    return tree


BASE = """\
/dts-v1/;
/ {
    #address-cells = <1>;
    #size-cells = <1>;
    model = "test";
    serial@1000 {
        compatible = "ns16550";
        reg = <0x1000 0x100>;
        status = "disabled";
    };
    gpio@2000 {
        compatible = "generic-gpio";
        reg = <0x2000 0x100>;
    };
};
"""


def _changed_by_path(delta):
    return {nd.path: nd for nd in delta.changed_nodes}


# --- identical / equivalence ---------------------------------------------

def test_identical_trees_are_equivalent(tmp_path):
    a = _tree_from_dts(BASE, tmp_path, "a")
    b = _tree_from_dts(BASE, tmp_path, "b")
    delta = a.compare(b)
    assert not delta
    assert delta.equivalent()
    assert delta.added_nodes == []
    assert delta.removed_nodes == []
    assert delta.changed_nodes == []


# --- node add / remove ----------------------------------------------------

def test_added_node(tmp_path):
    # insert a new node just before the closing "};\n" of the root
    b_text = BASE[:-3] + (
        "    timer@3000 {\n"
        "        compatible = \"generic-timer\";\n"
        "        reg = <0x3000 0x100>;\n"
        "    };\n};\n"
    )
    a = _tree_from_dts(BASE, tmp_path, "a")
    b = _tree_from_dts(b_text, tmp_path, "b")
    delta = a.compare(b)
    assert bool(delta)
    added = [n.abs_path for n in delta.added_nodes]
    assert "/timer@3000" in added
    assert delta.removed_nodes == []


def test_removed_node(tmp_path):
    b_text = BASE.replace(
        "    gpio@2000 {\n"
        "        compatible = \"generic-gpio\";\n"
        "        reg = <0x2000 0x100>;\n"
        "    };\n",
        "",
    )
    a = _tree_from_dts(BASE, tmp_path, "a")
    b = _tree_from_dts(b_text, tmp_path, "b")
    delta = a.compare(b)
    removed = [n.abs_path for n in delta.removed_nodes]
    assert "/gpio@2000" in removed
    assert delta.added_nodes == []


# --- property add / remove / change --------------------------------------

def test_added_property(tmp_path):
    b_text = BASE.replace(
        "        status = \"disabled\";\n",
        "        status = \"disabled\";\n"
        "        clock-frequency = <100000000>;\n",
    )
    a = _tree_from_dts(BASE, tmp_path, "a")
    b = _tree_from_dts(b_text, tmp_path, "b")
    delta = a.compare(b)
    changed = _changed_by_path(delta)
    assert "/serial@1000" in changed
    added = [p.name for p in changed["/serial@1000"].added_props]
    assert "clock-frequency" in added


def test_removed_property(tmp_path):
    b_text = BASE.replace("        status = \"disabled\";\n", "")
    a = _tree_from_dts(BASE, tmp_path, "a")
    b = _tree_from_dts(b_text, tmp_path, "b")
    delta = a.compare(b)
    changed = _changed_by_path(delta)
    assert "/serial@1000" in changed
    removed = [p.name for p in changed["/serial@1000"].removed_props]
    assert "status" in removed


def test_changed_property(tmp_path):
    b_text = BASE.replace('status = "disabled";', 'status = "okay";')
    a = _tree_from_dts(BASE, tmp_path, "a")
    b = _tree_from_dts(b_text, tmp_path, "b")
    delta = a.compare(b)
    changed = _changed_by_path(delta)
    assert "/serial@1000" in changed
    names = [name for (name, _va, _vb) in changed["/serial@1000"].changed_props]
    assert "status" in names


# --- internal / bookkeeping node exclusion -------------------------------

def test_aliases_node_excluded(tmp_path):
    a_text = BASE[:-3] + (
        "    aliases {\n"
        "        serial0 = \"/serial@1000\";\n"
        "    };\n};\n"
    )
    b_text = BASE[:-3] + (
        "    aliases {\n"
        "        serial0 = \"/gpio@2000\";\n"  # different, but /aliases is excluded
        "    };\n};\n"
    )
    a = _tree_from_dts(a_text, tmp_path, "a")
    b = _tree_from_dts(b_text, tmp_path, "b")
    delta = a.compare(b)
    # the only difference is under /aliases, which is excluded → equivalent
    assert delta.equivalent(), repr(delta)


# --- phandle normalization (Phase 2) -------------------------------------

# Same topology in both trees; only the explicit phandle NUMBER on the
# interrupt controller differs, which shifts the raw cell value of every
# `interrupt-parent` referencing it. A correct diff normalizes phandle
# references by resolved target and ignores the bookkeeping `phandle`
# property, so these must compare equal.
_PH_A = """\
/dts-v1/;
/ {
    #address-cells = <1>;
    #size-cells = <1>;
    gic: intc@1000 {
        phandle = <0x1>;
        compatible = "arm,gic";
        interrupt-controller;
        #interrupt-cells = <1>;
        reg = <0x1000 0x100>;
    };
    dev@2000 {
        compatible = "vendor,dev";
        reg = <0x2000 0x10>;
        interrupt-parent = <&gic>;
    };
};
"""

_PH_B = _PH_A.replace("phandle = <0x1>;", "phandle = <0x5>;")


def test_phandle_number_churn_is_equivalent(tmp_path):
    a = _tree_from_dts(_PH_A, tmp_path, "a")
    b = _tree_from_dts(_PH_B, tmp_path, "b")
    delta = a.compare(b)
    assert delta.equivalent(), repr([(nd.path, nd.changed_props)
                                     for nd in delta.changed_nodes])


def test_genuine_phandle_retarget_is_a_change(tmp_path):
    # two controllers; move dev's interrupt-parent from gic0 to gic1
    two_ctrl = """\
/dts-v1/;
/ {
    #address-cells = <1>;
    #size-cells = <1>;
    gic0: intc@1000 {
        phandle = <0x1>;
        compatible = "arm,gic";
        interrupt-controller;
        #interrupt-cells = <1>;
        reg = <0x1000 0x100>;
    };
    gic1: intc@1100 {
        phandle = <0x2>;
        compatible = "arm,gic";
        interrupt-controller;
        #interrupt-cells = <1>;
        reg = <0x1100 0x100>;
    };
    dev@2000 {
        compatible = "vendor,dev";
        reg = <0x2000 0x10>;
        interrupt-parent = <&gic0>;
    };
};
"""
    a = _tree_from_dts(two_ctrl, tmp_path, "a")
    b = _tree_from_dts(two_ctrl.replace("interrupt-parent = <&gic0>;",
                                        "interrupt-parent = <&gic1>;"),
                       tmp_path, "b")
    delta = a.compare(b)
    changed = _changed_by_path(delta)
    assert "/dev@2000" in changed, repr(delta)
    names = [name for (name, _va, _vb) in changed["/dev@2000"].changed_props]
    assert "interrupt-parent" in names


def test_phandle_property_itself_excluded(tmp_path):
    # nodes identical except the explicit phandle number -> the `phandle`
    # bookkeeping property must never surface as a change
    a = _tree_from_dts(_PH_A, tmp_path, "a")
    b = _tree_from_dts(_PH_B, tmp_path, "b")
    delta = a.compare(b)
    for nd in delta.changed_nodes:
        names = ([p.name for p in nd.added_props]
                 + [p.name for p in nd.removed_props]
                 + [name for (name, _a, _b) in nd.changed_props])
        assert "phandle" not in names, f"{nd.path}: {names}"


# --- alternate match keys (Phase 3) --------------------------------------

# A labeled node whose unit-address (and thus path) changes between trees,
# with its properties otherwise identical. Under key="path" this is a
# remove+add; under key="label"/"address"... it is the same node, moved.
_MOVE_A = """\
/dts-v1/;
/ {
    #address-cells = <1>;
    #size-cells = <1>;
    uart: serial@1100 {
        compatible = "ns16550";
        reg = <0x1100 0x10>;
    };
};
"""
# same label "uart", different node name/address -> different path;
# reg kept identical so the ONLY difference is the move.
_MOVE_B = """\
/dts-v1/;
/ {
    #address-cells = <1>;
    #size-cells = <1>;
    uart: serial@2200 {
        compatible = "ns16550";
        reg = <0x1100 0x10>;
    };
};
"""


def test_path_key_sees_move_as_add_remove(tmp_path):
    a = _tree_from_dts(_MOVE_A, tmp_path, "a")
    b = _tree_from_dts(_MOVE_B, tmp_path, "b")
    delta = a.compare(b, key="path")
    removed = [n.abs_path for n in delta.removed_nodes]
    added = [n.abs_path for n in delta.added_nodes]
    assert "/serial@1100" in removed
    assert "/serial@2200" in added
    assert delta.changed_nodes == []


def test_label_key_sees_move_as_changed(tmp_path):
    a = _tree_from_dts(_MOVE_A, tmp_path, "a")
    b = _tree_from_dts(_MOVE_B, tmp_path, "b")
    delta = a.compare(b, key="label")
    assert delta.added_nodes == []
    assert delta.removed_nodes == []
    assert len(delta.changed_nodes) == 1
    nd = delta.changed_nodes[0]
    assert nd.moved == ("/serial@1100", "/serial@2200")
    # reg is identical, so no property changes -- the move is the difference
    assert nd.changed_props == []


def test_address_key_matches_moved_node(tmp_path):
    # keep the same address but change the label -> path stays the same
    # here, so use a differing-parent construction for address matching
    a_text = """\
/dts-v1/;
/ {
    #address-cells = <1>;
    #size-cells = <1>;
    old_label: serial@1100 { compatible = "ns16550"; reg = <0x1100 0x10>; };
};
"""
    b_text = """\
/dts-v1/;
/ {
    #address-cells = <1>;
    #size-cells = <1>;
    new_label: serial@1100 { compatible = "ns16550"; reg = <0x1100 0x10>; status = "okay"; };
};
"""
    a = _tree_from_dts(a_text, tmp_path, "a")
    b = _tree_from_dts(b_text, tmp_path, "b")
    # same path here, so even key="address" matches; verify the property
    # change is picked up (label is not a property, so not a change)
    delta = a.compare(b, key="address")
    changed = _changed_by_path(delta)
    assert "/serial@1100" in changed
    names = [p.name for p in changed["/serial@1100"].added_props]
    assert "status" in names


def test_ambiguous_key_falls_back_to_path(tmp_path):
    # two nodes share the same name-part ("serial") -> under key="name"
    # the value is ambiguous, so matching falls back to path and the trees
    # (identical) compare equal rather than mis-pairing the two serials.
    text = """\
/dts-v1/;
/ {
    #address-cells = <1>;
    #size-cells = <1>;
    serial@1000 { compatible = "ns16550"; reg = <0x1000 0x10>; };
    serial@2000 { compatible = "ns16550"; reg = <0x2000 0x10>; };
};
"""
    a = _tree_from_dts(text, tmp_path, "a")
    b = _tree_from_dts(text, tmp_path, "b")
    delta = a.compare(b, key="name")
    assert delta.equivalent(), repr(delta)


# --- emitters: equivalence + unified (Phase 4a/4b) -----------------------

def test_emit_equivalence(tmp_path):
    a = _tree_from_dts(BASE, tmp_path, "a")
    same = _tree_from_dts(BASE, tmp_path, "same")
    assert a.compare(same).emit("equivalence") == "equivalent\n"

    b_text = BASE.replace('status = "disabled";', 'status = "okay";')
    b = _tree_from_dts(b_text, tmp_path, "b")
    assert a.compare(b).emit("equivalence") == "differ\n"


def test_emit_unified_content(tmp_path):
    b_text = BASE.replace(
        '        status = "disabled";\n',
        '        status = "okay";\n        clock-frequency = <100000000>;\n',
    )
    # also drop gpio and add a timer to exercise removed/added node lines
    b_text = b_text.replace(
        "    gpio@2000 {\n"
        "        compatible = \"generic-gpio\";\n"
        "        reg = <0x2000 0x100>;\n"
        "    };\n",
        "",
    )
    b_text = b_text[:-3] + (
        "    timer@3000 { compatible = \"generic-timer\"; reg = <0x3000 0x100>; };\n};\n"
    )
    a = _tree_from_dts(BASE, tmp_path, "a")
    b = _tree_from_dts(b_text, tmp_path, "b")
    text = a.compare(b).emit("unified")

    assert "- /gpio@2000" in text          # removed node
    assert "+ /timer@3000" in text         # added node
    assert "  /serial@1000" in text        # changed node header
    assert "+ clock-frequency" in text     # added property
    assert "~ status:" in text             # changed property
    assert '"disabled"' in text and '"okay"' in text


def test_emit_unified_deterministic(tmp_path):
    b_text = BASE.replace('status = "disabled";', 'status = "okay";')
    a = _tree_from_dts(BASE, tmp_path, "a")
    b = _tree_from_dts(b_text, tmp_path, "b")
    delta = a.compare(b)
    assert delta.emit("unified") == delta.emit("unified")


def test_emit_unified_to_file(tmp_path):
    b_text = BASE.replace('status = "disabled";', 'status = "okay";')
    a = _tree_from_dts(BASE, tmp_path, "a")
    b = _tree_from_dts(b_text, tmp_path, "b")
    out = tmp_path / "diff.txt"
    ret = a.compare(b).emit("unified", output=str(out))
    assert ret == str(out)
    assert out.read_text().splitlines()[0] == "--- a"


def test_emit_unknown_format_raises(tmp_path):
    a = _tree_from_dts(BASE, tmp_path, "a")
    b = _tree_from_dts(BASE, tmp_path, "b")
    with pytest.raises(NotImplementedError):
        a.compare(b).emit("bogus")


# --- fragment renderer (Phase 4c) -----------------------------------------

# labeled base + target exercising all delta kinds:
#   uart: status changed, clock-frequency added, `extra` removed
#   gpio: removed entirely
#   timer: added
_LBASE = """\
/dts-v1/;
/ {
    #address-cells = <1>;
    #size-cells = <1>;
    uart: serial@1000 {
        compatible = "ns16550";
        reg = <0x1000 0x100>;
        status = "disabled";
        extra = "removeme";
    };
    gpio: gpio@2000 {
        compatible = "generic-gpio";
        reg = <0x2000 0x100>;
    };
};
"""

_LTARGET = """\
/dts-v1/;
/ {
    #address-cells = <1>;
    #size-cells = <1>;
    uart: serial@1000 {
        compatible = "ns16550";
        reg = <0x1000 0x100>;
        status = "okay";
        clock-frequency = <100000000>;
    };
    timer: timer@3000 {
        compatible = "generic-timer";
        reg = <0x3000 0x100>;
    };
};
"""


def test_fragment_contains_all_delta_kinds(tmp_path):
    a = _tree_from_dts(_LBASE, tmp_path, "a")
    b = _tree_from_dts(_LTARGET, tmp_path, "b")
    overlay = a.compare(b).emit("fragment")
    assert "&uart {" in overlay
    assert 'status = "okay";' in overlay
    assert "clock-frequency = <0x5f5e100>;" in overlay
    assert "/delete-property/ extra;" in overlay
    assert "/delete-node/ &gpio;" in overlay
    assert "timer@3000 {" in overlay
    # no /dts-v1/ or /plugin/ wrapper -- it is an include fragment
    assert "/dts-v1/" not in overlay
    assert "/plugin/" not in overlay


def test_fragment_round_trip_reconstructs_target(tmp_path):
    a = _tree_from_dts(_LBASE, tmp_path, "a")
    b = _tree_from_dts(_LTARGET, tmp_path, "b")
    overlay = a.compare(b).emit("fragment")

    # source + overlay, concatenated and recompiled, must equal target
    combined = _LBASE + "\n" + overlay
    recompiled = _tree_from_dts(combined, tmp_path, "combined")

    delta = b.compare(recompiled)
    assert delta.equivalent(), repr(delta.emit("unified"))


def test_fragment_phandle_rendered_as_label(tmp_path):
    base = """\
/dts-v1/;
/ {
    #address-cells = <1>;
    #size-cells = <1>;
    gic: intc@1000 {
        phandle = <0x1>;
        compatible = "arm,gic";
        interrupt-controller;
        #interrupt-cells = <1>;
        reg = <0x1000 0x100>;
    };
    dev@2000 { compatible = "vendor,dev"; reg = <0x2000 0x10>; };
};
"""
    target = base.replace(
        'dev@2000 { compatible = "vendor,dev"; reg = <0x2000 0x10>; };',
        'dev@2000 { compatible = "vendor,dev"; reg = <0x2000 0x10>; '
        'interrupt-parent = <&gic>; };',
    )
    a = _tree_from_dts(base, tmp_path, "a")
    b = _tree_from_dts(target, tmp_path, "b")
    overlay = a.compare(b).emit("fragment")
    # the added phandle property must render as &gic, not a raw number
    assert "interrupt-parent = <&gic>;" in overlay
    # and it must round-trip
    recompiled = _tree_from_dts(base + "\n" + overlay, tmp_path, "combined")
    assert b.compare(recompiled).equivalent()


# --- compare assist / CLI wiring (Phase 5) -------------------------------

def _sdt_from_dts(dts_text, tmp_path, name):
    """Build a real LopperSDT (with a loaded .tree) for the assist tests."""
    dts = tmp_path / f"{name}.dts"
    dts.write_text(dts_text)
    try:
        import libfdt  # noqa: F401
        libfdt_ok = True
    except ImportError:
        libfdt_ok = False
    sdt = LopperSDT(str(dts))
    sdt.dryrun = False
    sdt.save_temps = False
    sdt.outdir = str(tmp_path)
    sdt.setup(sdt.dts, [], "", True, libfdt=libfdt_ok)
    return sdt


def test_assist_unified_to_file(tmp_path):
    sdt = _sdt_from_dts(_LBASE, tmp_path, "base")
    target = tmp_path / "target.dts"
    target.write_text(_LTARGET)
    out = tmp_path / "diff.txt"
    compare_assist.compare(0, sdt, {"args": ["-o", "unified", str(target), str(out)]})
    text = out.read_text()
    assert "- /gpio@2000" in text
    assert "~ status:" in text


def test_assist_fragment_round_trips(tmp_path):
    sdt = _sdt_from_dts(_LBASE, tmp_path, "base")
    target_dts = tmp_path / "target.dts"
    target_dts.write_text(_LTARGET)
    out = tmp_path / "board.overlay"
    compare_assist.compare(0, sdt, {"args": ["-o", "fragment", str(target_dts), str(out)]})

    overlay = out.read_text()
    # gpio was removed; the ref form (&gpio vs &{/gpio@2000}) depends on
    # whether the source tree carries labels -- accept either.
    assert ("/delete-node/ &gpio;" in overlay
            or "/delete-node/ &{/gpio@2000};" in overlay)
    # the real check: source + emitted overlay recompiles to the target
    recompiled = _tree_from_dts(_LBASE + "\n" + overlay, tmp_path, "combined")
    target_tree = _tree_from_dts(_LTARGET, tmp_path, "target_tree")
    assert target_tree.compare(recompiled).equivalent()


def test_assist_equivalence_gate(tmp_path):
    sdt = _sdt_from_dts(_LBASE, tmp_path, "base")

    same = tmp_path / "same.dts"
    same.write_text(_LBASE)
    # equivalent -> returns normally
    assert compare_assist.compare(0, sdt, {"args": ["-o", "equivalence", str(same)]}) is True

    diff = tmp_path / "diff.dts"
    diff.write_text(_LTARGET)
    # differ -> non-zero exit (regression gate)
    with pytest.raises(SystemExit) as ei:
        compare_assist.compare(0, sdt, {"args": ["-o", "equivalence", str(diff)]})
    assert ei.value.code == 2


def test_assist_legacy_name_check_runs(tmp_path):
    # no -o: legacy name-existence comparison path still works
    sdt = _sdt_from_dts(_LBASE, tmp_path, "base")
    same = tmp_path / "same.dts"
    same.write_text(_LBASE)
    assert compare_assist.compare(0, sdt, {"args": ["-c", "name", str(same)]}) is True


# --- unsupported key ------------------------------------------------------

def test_unsupported_key_raises(tmp_path):
    a = _tree_from_dts(BASE, tmp_path, "a")
    b = _tree_from_dts(BASE, tmp_path, "b")
    with pytest.raises(NotImplementedError):
        a.compare(b, key="compatible")
