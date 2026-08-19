"""Tests for the LopperTree alias API.

Covers walking /aliases, pruning entries whose target is gone, and binding an
alias to a node so that it follows the node rather than a path.
"""

import pytest

from lopper import Lopper
from lopper.tree import LopperTree, LopperNode


ALIAS_DTS = """
/dts-v1/;
/ {
    #address-cells = <1>;
    #size-cells = <1>;

    aliases {
        serial0 = &uart0;
        i2c0    = &i2c0;
        dead0   = "/axi/nope@0";
    };

    axi {
        #address-cells = <1>;
        #size-cells = <1>;

        uart0: serial@f1920000 { reg = <0xf1920000 0x1000>; };
        i2c0:  i2c@f1940000    { reg = <0xf1940000 0x1000>; };
        spi0:  spi@f1010000    { reg = <0xf1010000 0x1000>; };
    };

    newbus {
        #address-cells = <1>;
        #size-cells = <1>;
    };
};
"""

SERIAL = "/axi/serial@f1920000"
I2C = "/axi/i2c@f1940000"
SPI = "/axi/spi@f1010000"


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


@pytest.fixture
def tree(tmp_path):
    return _tree_from_dts(ALIAS_DTS, tmp_path, "aliases")


# --- aliases() ------------------------------------------------------------

def test_aliases_walks_every_entry(tree):
    assert {n for n, _ in tree.aliases()} == {"serial0", "i2c0", "dead0"}


def test_aliases_returns_raw_values(tree):
    assert dict(tree.aliases())["serial0"] == SERIAL


def test_aliases_resolve_yields_nodes(tree):
    resolved = dict(tree.aliases(resolve=True))
    assert resolved["serial0"].abs_path == SERIAL
    assert resolved["i2c0"].abs_path == I2C


def test_aliases_resolve_yields_none_for_dangling(tree):
    assert dict(tree.aliases(resolve=True))["dead0"] is None


def test_aliases_empty_without_an_aliases_node():
    assert LopperTree().aliases() == []


# --- alias_prune() --------------------------------------------------------

def test_alias_prune_dry_run_reports_without_deleting(tree):
    assert [n for n, _ in tree.alias_prune(dry_run=True)] == ["dead0"]
    assert "dead0" in {n for n, _ in tree.aliases()}


def test_alias_prune_drops_only_the_dangling_entry(tree):
    assert [n for n, _ in tree.alias_prune()] == ["dead0"]
    assert {n for n, _ in tree.aliases()} == {"serial0", "i2c0"}


# --- alias_set() name validation -----------------------------------------

@pytest.mark.parametrize("name", ["Serial0", "watchdog_0", "thing.x", "", "UPPER"])
def test_alias_set_rejects_invalid_names(tree, name):
    with pytest.raises(ValueError):
        tree.alias_set(name, tree[SPI])


@pytest.mark.parametrize("name", ["spi0", "my-thing", "rtc", "watchdog0"])
def test_alias_set_accepts_valid_names(tree, name):
    tree.alias_set(name, tree[SPI])
    assert dict(tree.aliases())[name] == SPI


def test_alias_set_rejects_a_missing_node(tree):
    with pytest.raises(ValueError):
        tree.alias_set("nonode", None)


# --- alias_set() binding --------------------------------------------------

def test_alias_set_overwrites_an_existing_alias(tree):
    tree.alias_set("spi0", tree[SPI])
    tree.alias_set("spi0", tree[I2C])
    assert dict(tree.aliases())["spi0"] == I2C


def test_bound_alias_follows_a_rename(tree):
    tree.alias_set("spi0", tree[SPI])
    tree.rename(tree[SPI], "spi@deadbeef")
    tree.resolve()
    assert dict(tree.aliases())["spi0"] == "/axi/spi@deadbeef"


def test_bound_alias_follows_a_move(tree):
    node = tree[SPI]
    tree.alias_set("spi0", node)
    tree.move(node, SPI, "/newbus/spi@f1010000")
    tree.resolve()
    assert dict(tree.aliases())["spi0"] == "/newbus/spi@f1010000"


def test_bound_alias_is_dropped_when_its_node_is_deleted(tree):
    tree.alias_set("spi0", tree[SPI])
    tree.delete(tree[SPI])
    tree.resolve()
    assert "spi0" not in {n for n, _ in tree.aliases()}


def test_authored_alias_value_is_not_rewritten(tree):
    before = dict(tree.aliases())["serial0"]
    tree.resolve()
    tree.resolve()
    assert dict(tree.aliases())["serial0"] == before


# --- alias_node() staying in step with the tree ---------------------------

def test_alias_node_drops_a_deleted_node(tree):
    assert tree.alias_node("i2c0") is not None
    tree.delete(tree[I2C])
    tree.resolve()
    assert tree.alias_node("i2c0") is None


def test_alias_node_sees_an_alias_added_after_load(tree):
    tree.alias_set("spi0", tree[SPI])
    tree.resolve()
    assert tree.alias_node("spi0").abs_path == SPI
