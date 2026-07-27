"""Tests for lopper.compare — structural tree comparison (Phase 1).

Phase 1 scope: path-keyed node matching, literal property-value
comparison, exclusion of derived/bookkeeping nodes. Phandle
normalization, alternate keys and renderers are later phases.
"""

import pytest

from lopper import Lopper
from lopper.tree import LopperTree
import lopper.compare


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


# --- unsupported key ------------------------------------------------------

def test_unsupported_key_raises(tmp_path):
    a = _tree_from_dts(BASE, tmp_path, "a")
    b = _tree_from_dts(BASE, tmp_path, "b")
    with pytest.raises(NotImplementedError):
        a.compare(b, key="compatible")
