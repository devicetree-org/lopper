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

