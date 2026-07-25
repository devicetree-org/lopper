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


# --- unsupported key ------------------------------------------------------

def test_nonpath_key_not_yet_supported(tmp_path):
    a = _tree_from_dts(BASE, tmp_path, "a")
    b = _tree_from_dts(BASE, tmp_path, "b")
    with pytest.raises(NotImplementedError):
        a.compare(b, key="label")
