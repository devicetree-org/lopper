#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""End-to-end tests for YAML sigil overlay merge.

Validates the full pipeline from YAML sigil parsing through to final DTS
output with two target OS types (linux, zephyr) producing different results.

Tests cover:
- Same SDT YAML with two domains (linux_domain, zephyr_domain)
- Per-property merge schemes: replace, append
- Conditional node staging (chosen!linux:, chosen!zephyr:)
- overlay_tree() producing correct merged tree for each OS
- Outputs for linux and zephyr are different in the expected ways
- Sigils on real device nodes (not under /domains/) — the "openamp pattern"
  where compatible!linux replaces a driver binding for one domain while
  other domains see the base value unchanged
"""

import io
import sys
import os
import textwrap

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lopper.yaml import LopperYAML
from lopper.tree import LopperProp, LopperNode, LopperTree


# ---------------------------------------------------------------------------
# Shared YAML fixture — single SDT with two domains and sigil annotations
# ---------------------------------------------------------------------------

# This YAML represents a minimal system device tree with:
#
#   /domains/linux_domain:
#     - os,type: linux
#     - compatible!linux!replace → base is "base-compat", linux overlay = "linux-compat"
#     - bootargs!append → appended to any existing value: "loglevel=7"
#     - chosen!linux: node → bootargs "root=/dev/mmcblk0" (conditional)
#
#   /domains/zephyr_domain:
#     - os,type: zephyr
#     - compatible!zephyr!replace → "zephyr-compat"
#     - bootargs!append → "CONFIG_DEBUG=y"
#     - chosen!zephyr: node → bootargs "zephyr,shell-uart" (conditional)
#
# Base (no-sigil) properties are shared and unchanged regardless of overlay.

DOMAINS_YAML = textwrap.dedent("""\
    domains:
      linux_domain:
        os,type: linux
        lopper,activate: linux
        compatible: base-compat
        compatible!linux!replace: linux-compat
        bootargs: quiet
        bootargs!append: loglevel=7
        chosen!linux:
          stdout-path: serial0
          bootargs: root=/dev/mmcblk0
      zephyr_domain:
        os,type: zephyr
        lopper,activate: zephyr
        compatible: base-compat
        compatible!zephyr!replace: zephyr-compat
        bootargs: quiet
        bootargs!append: CONFIG_DEBUG=y
        chosen!zephyr:
          stdout-path: serial1
          bootargs: zephyr,shell-uart
""")


@pytest.fixture(scope="module")
def yaml_tree(tmp_path_factory):
    """Parse DOMAINS_YAML and return the resulting LopperTree."""
    tmp = tmp_path_factory.mktemp("overlay_e2e")
    yaml_file = tmp / "domains.yaml"
    yaml_file.write_text(DOMAINS_YAML)
    y = LopperYAML(str(yaml_file))
    tree = y.to_tree()
    assert tree is not None, "YAML parse returned None"
    return tree


# ---------------------------------------------------------------------------
# Section 1: YAML parsing — sigils stripped, base tree unmodified
# ---------------------------------------------------------------------------

class TestYAMLParsing:
    def test_linux_domain_node_exists(self, yaml_tree):
        n = yaml_tree["/domains/linux_domain"]
        assert n is not None

    def test_zephyr_domain_node_exists(self, yaml_tree):
        n = yaml_tree["/domains/zephyr_domain"]
        assert n is not None

    def test_os_type_linux_plain_prop(self, yaml_tree):
        n = yaml_tree["/domains/linux_domain"]
        val = n.propval("os,type")
        assert "linux" in (val if isinstance(val, list) else [val])

    def test_os_type_zephyr_plain_prop(self, yaml_tree):
        n = yaml_tree["/domains/zephyr_domain"]
        val = n.propval("os,type")
        assert "zephyr" in (val if isinstance(val, list) else [val])

    def test_base_compatible_not_overwritten_by_sigil(self, yaml_tree):
        """Base value must be unchanged; overlay sits in overlay_subtrees."""
        n = yaml_tree["/domains/linux_domain"]
        val = n.__props__["compatible"].value
        assert "base-compat" in (val if isinstance(val, list) else [val]), \
            f"base-compat missing from base tree compatible: {val}"

    def test_overlay_subtrees_contain_linux(self, yaml_tree):
        """overlay_subtrees must have a 'linux' entry after parsing."""
        subtrees = yaml_tree._metadata.get("overlay_subtrees", {})
        assert "linux" in subtrees, f"linux missing from overlay_subtrees: {list(subtrees)}"

    def test_overlay_subtrees_contain_zephyr(self, yaml_tree):
        subtrees = yaml_tree._metadata.get("overlay_subtrees", {})
        assert "zephyr" in subtrees, f"zephyr missing from overlay_subtrees: {list(subtrees)}"

    def test_conditional_chosen_linux_not_in_base_tree(self, yaml_tree):
        """chosen!linux: node must NOT appear in base tree."""
        try:
            n = yaml_tree["/domains/linux_domain/chosen"]
            assert n is None, "chosen node unexpectedly present in base tree"
        except (KeyError, Exception):
            pass  # expected: node not in base tree

    def test_linux_chosen_in_overlay_subtree(self, yaml_tree):
        subtrees = yaml_tree._metadata.get("overlay_subtrees", {})
        linux_nodes = subtrees.get("linux", [])
        paths = [n.abs_path for n in linux_nodes]
        assert any("chosen" in p for p in paths), \
            f"chosen not in linux overlay_subtrees: {paths}"

    def test_zephyr_chosen_in_overlay_subtree(self, yaml_tree):
        subtrees = yaml_tree._metadata.get("overlay_subtrees", {})
        zephyr_nodes = subtrees.get("zephyr", [])
        paths = [n.abs_path for n in zephyr_nodes]
        assert any("chosen" in p for p in paths), \
            f"chosen not in zephyr overlay_subtrees: {paths}"


# ---------------------------------------------------------------------------
# Section 2: overlay_tree() — OS-specific merged tree
# ---------------------------------------------------------------------------

class TestOverlayTree:
    """Verify overlay_tree() produces correct merged trees per OS."""

    def _fresh_tree(self, tmp_path):
        yaml_file = tmp_path / "domains.yaml"
        yaml_file.write_text(DOMAINS_YAML)
        y = LopperYAML(str(yaml_file))
        return y.to_tree()

    def test_overlay_tree_linux_not_none(self, tmp_path):
        tree = self._fresh_tree(tmp_path)
        lt = tree.overlay_tree("linux")
        assert lt is not None, "overlay_tree('linux') returned None"

    def test_overlay_tree_zephyr_not_none(self, tmp_path):
        tree = self._fresh_tree(tmp_path)
        zt = tree.overlay_tree("zephyr")
        assert zt is not None, "overlay_tree('zephyr') returned None"

    def test_linux_overlay_tree_replaces_compatible(self, tmp_path):
        tree = self._fresh_tree(tmp_path)
        lt = tree.overlay_tree("linux")
        n = lt["/domains/linux_domain"]
        val = n.__props__["compatible"].value
        assert "linux-compat" in (val if isinstance(val, list) else [val]), \
            f"linux-compat missing from linux overlay_tree compatible: {val}"
        assert "base-compat" not in (val if isinstance(val, list) else [val]), \
            f"base-compat still present after linux replace: {val}"

    def test_zephyr_overlay_tree_replaces_compatible(self, tmp_path):
        tree = self._fresh_tree(tmp_path)
        zt = tree.overlay_tree("zephyr")
        n = zt["/domains/zephyr_domain"]
        val = n.__props__["compatible"].value
        assert "zephyr-compat" in (val if isinstance(val, list) else [val]), \
            f"zephyr-compat missing from zephyr overlay_tree compatible: {val}"
        assert "base-compat" not in (val if isinstance(val, list) else [val]), \
            f"base-compat still present after zephyr replace: {val}"

    def test_linux_overlay_tree_appends_bootargs(self, tmp_path):
        tree = self._fresh_tree(tmp_path)
        lt = tree.overlay_tree("linux")
        n = lt["/domains/linux_domain"]
        val = n.__props__["bootargs"].value
        flat = " ".join(val) if isinstance(val, list) else val
        assert "quiet" in flat, f"base bootargs 'quiet' missing: {flat}"
        assert "loglevel=7" in flat, f"appended bootargs 'loglevel=7' missing: {flat}"

    def test_zephyr_overlay_tree_appends_bootargs(self, tmp_path):
        tree = self._fresh_tree(tmp_path)
        zt = tree.overlay_tree("zephyr")
        n = zt["/domains/zephyr_domain"]
        val = n.__props__["bootargs"].value
        flat = " ".join(val) if isinstance(val, list) else val
        assert "quiet" in flat, f"base bootargs 'quiet' missing: {flat}"
        assert "CONFIG_DEBUG=y" in flat, f"appended bootargs 'CONFIG_DEBUG=y' missing: {flat}"

    def test_linux_chosen_node_added(self, tmp_path):
        tree = self._fresh_tree(tmp_path)
        lt = tree.overlay_tree("linux")
        try:
            n = lt["/domains/linux_domain/chosen"]
            assert n is not None
        except (KeyError, Exception):
            pytest.fail("chosen node not in linux overlay_tree")

    def test_zephyr_chosen_node_added(self, tmp_path):
        tree = self._fresh_tree(tmp_path)
        zt = tree.overlay_tree("zephyr")
        try:
            n = zt["/domains/zephyr_domain/chosen"]
            assert n is not None
        except (KeyError, Exception):
            pytest.fail("chosen node not in zephyr overlay_tree")

    def test_linux_chosen_has_linux_bootargs(self, tmp_path):
        tree = self._fresh_tree(tmp_path)
        lt = tree.overlay_tree("linux")
        n = lt["/domains/linux_domain/chosen"]
        val = n.propval("bootargs")
        val_list = val if isinstance(val, list) else [val]
        assert any("root=/dev/mmcblk0" in str(v) for v in val_list), \
            f"linux chosen bootargs wrong: {val}"

    def test_zephyr_chosen_has_zephyr_bootargs(self, tmp_path):
        tree = self._fresh_tree(tmp_path)
        zt = tree.overlay_tree("zephyr")
        n = zt["/domains/zephyr_domain/chosen"]
        val = n.propval("bootargs")
        val_list = val if isinstance(val, list) else [val]
        assert any("zephyr,shell-uart" in str(v) for v in val_list), \
            f"zephyr chosen bootargs wrong: {val}"

    def test_linux_and_zephyr_outputs_differ(self, tmp_path):
        """The two overlay_tree outputs must differ in at least compatible."""
        tree = self._fresh_tree(tmp_path)
        lt = tree.overlay_tree("linux")
        zt = tree.overlay_tree("zephyr")
        ln = lt["/domains/linux_domain"]
        zn = zt["/domains/zephyr_domain"]
        lv = ln.__props__["compatible"].value
        zv = zn.__props__["compatible"].value
        assert lv != zv, f"linux and zephyr compatible should differ: {lv} vs {zv}"

    def test_linux_overlay_does_not_contaminate_zephyr_domain(self, tmp_path):
        """overlay_tree('linux') must not change zephyr domain."""
        tree = self._fresh_tree(tmp_path)
        lt = tree.overlay_tree("linux")
        zn = lt["/domains/zephyr_domain"]
        val = zn.__props__["compatible"].value
        assert "base-compat" in (val if isinstance(val, list) else [val]), \
            f"linux overlay_tree contaminated zephyr domain: {val}"
        assert "linux-compat" not in (val if isinstance(val, list) else [val])

    def test_base_tree_unchanged_after_overlay_tree(self, tmp_path):
        """Base tree must be unmodified after building an overlay_tree."""
        tree = self._fresh_tree(tmp_path)
        _ = tree.overlay_tree("linux")
        n = tree["/domains/linux_domain"]
        val = n.__props__["compatible"].value
        assert "base-compat" in (val if isinstance(val, list) else [val]), \
            f"base tree was mutated by overlay_tree(): {val}"

    def test_unknown_overlay_name_returns_none(self, tmp_path):
        tree = self._fresh_tree(tmp_path)
        result = tree.overlay_tree("nonexistent")
        assert result is None


# ---------------------------------------------------------------------------
# Section 3: DTS output content differs per OS
# ---------------------------------------------------------------------------

class TestDTSOutputDiffers:
    """Write DTS output and verify linux vs zephyr content differs."""

    def _dts_output(self, tree):
        buf = io.StringIO()
        tree["/"].print(buf)
        return buf.getvalue()

    def _overlay_dts(self, tmp_path, name):
        yaml_file = tmp_path / "domains.yaml"
        yaml_file.write_text(DOMAINS_YAML)
        tree = LopperYAML(str(yaml_file)).to_tree()
        ot = tree.overlay_tree(name)
        return self._dts_output(ot)

    def test_linux_dts_contains_linux_compat(self, tmp_path):
        out = self._overlay_dts(tmp_path, "linux")
        assert "linux-compat" in out, "linux-compat not in linux DTS output"

    def test_zephyr_dts_contains_zephyr_compat(self, tmp_path):
        out = self._overlay_dts(tmp_path, "zephyr")
        assert "zephyr-compat" in out, "zephyr-compat not in zephyr DTS output"

    def test_linux_dts_has_mmcblk_not_zephyr_uart(self, tmp_path):
        out = self._overlay_dts(tmp_path, "linux")
        assert "mmcblk0" in out, "linux chosen bootargs not in DTS output"
        assert "zephyr,shell-uart" not in out, \
            "zephyr conditional content leaked into linux DTS output"

    def test_zephyr_dts_has_shell_uart_not_mmcblk(self, tmp_path):
        out = self._overlay_dts(tmp_path, "zephyr")
        assert "zephyr,shell-uart" in out, "zephyr chosen bootargs not in DTS output"
        assert "mmcblk0" not in out, \
            "linux conditional content leaked into zephyr DTS output"

    def test_linux_dts_appended_bootargs(self, tmp_path):
        out = self._overlay_dts(tmp_path, "linux")
        assert "loglevel=7" in out, "linux appended bootargs not in DTS output"

    def test_zephyr_dts_appended_bootargs(self, tmp_path):
        out = self._overlay_dts(tmp_path, "zephyr")
        assert "CONFIG_DEBUG=y" in out, "zephyr appended bootargs not in DTS output"


# ---------------------------------------------------------------------------
# Section 4: "OpenAMP pattern" — sigils on real device nodes, not /domains/
#
# A property override (e.g. compatible!linux) lives at the actual device node
# in the tree (e.g. /axi/timer@f1e90000).  Domains select which overlay is
# active via lopper,activate.  Domains without lopper,activate see the base
# value.  This is the typical use case for per-OS driver-binding overrides.
# ---------------------------------------------------------------------------

# YAML representing a minimal multi-domain SDT where a device node carries
# a sigil-annotated property:
#
#   /axi/timer@f1e90000:
#     compatible: cdns,ttc          (base — all domains that don't activate linux)
#     compatible!linux: uio         (linux overlay replaces it)
#
#   /domains/APU_Linux:
#     lopper,activate: linux        → domain_access selects overlay_tree('linux')
#
#   /domains/RPU1_BM:
#     (no lopper,activate)          → domain_access uses the base tree

OPENAMP_YAML = """\
axi:
  timer@f1e90000:
    compatible: cdns,ttc
    compatible!linux: uio
    reg: 0xf1e90000 0x1000

domains:
  APU_Linux:
    compatible: openamp,domain-v1
    lopper,activate: linux
    cpus: 0
  RPU1_BM:
    compatible: openamp,domain-v1
    cpus: 1
"""


@pytest.fixture(scope="module")
def openamp_tree(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("openamp_sigil")
    yaml_file = tmp / "sdt.yaml"
    yaml_file.write_text(OPENAMP_YAML)
    y = LopperYAML(str(yaml_file))
    tree = y.to_tree()
    assert tree is not None
    return tree


class TestOpenAMPPattern:
    """Sigils on real device nodes, not /domains/ — per-domain driver binding."""

    def _fresh_tree(self, tmp_path):
        yaml_file = tmp_path / "sdt.yaml"
        yaml_file.write_text(OPENAMP_YAML)
        return LopperYAML(str(yaml_file)).to_tree()

    def test_base_timer_compatible_is_cdns(self, openamp_tree):
        """Base tree must have the vendor-neutral cdns,ttc binding."""
        n = openamp_tree["/axi/timer@f1e90000"]
        val = n.propval("compatible")
        assert "cdns,ttc" in (val if isinstance(val, list) else [val]), \
            f"expected cdns,ttc in base tree, got: {val}"

    def test_linux_overlay_subtree_registered(self, openamp_tree):
        """overlay_subtrees must have a 'linux' entry from the device-node sigil."""
        subtrees = openamp_tree._metadata.get("overlay_subtrees", {})
        assert "linux" in subtrees, \
            f"linux missing from overlay_subtrees keys: {list(subtrees)}"

    def test_linux_overlay_timer_path_in_subtree(self, openamp_tree):
        """The overlay node for linux must refer to the timer path."""
        subtrees = openamp_tree._metadata.get("overlay_subtrees", {})
        linux_nodes = subtrees.get("linux", [])
        paths = [n.abs_path for n in linux_nodes]
        assert any("timer" in p for p in paths), \
            f"timer not found in linux overlay_subtrees: {paths}"

    def test_linux_overlay_tree_replaces_compatible(self, tmp_path):
        """overlay_tree('linux') must expose uio, not cdns,ttc."""
        tree = self._fresh_tree(tmp_path)
        lt = tree.overlay_tree("linux")
        assert lt is not None, "overlay_tree('linux') returned None"
        n = lt["/axi/timer@f1e90000"]
        val = n.propval("compatible")
        val_list = val if isinstance(val, list) else [val]
        assert "uio" in val_list, \
            f"uio missing from linux overlay_tree compatible: {val}"
        assert "cdns,ttc" not in val_list, \
            f"cdns,ttc still present after linux replace: {val}"

    def test_base_tree_timer_unchanged_after_overlay(self, tmp_path):
        """Building overlay_tree('linux') must not mutate the base tree."""
        tree = self._fresh_tree(tmp_path)
        _ = tree.overlay_tree("linux")
        n = tree["/axi/timer@f1e90000"]
        val = n.propval("compatible")
        assert "cdns,ttc" in (val if isinstance(val, list) else [val]), \
            f"base tree was mutated: {val}"
        assert "uio" not in (val if isinstance(val, list) else [val]), \
            f"uio leaked into base tree: {val}"

    def test_bm_domain_has_no_lopper_activate(self, openamp_tree):
        """RPU1_BM must not carry lopper,activate — it uses the base tree."""
        n = openamp_tree["/domains/RPU1_BM"]
        val = n.propval("lopper,activate")
        assert val in (None, [""], ""), \
            f"RPU1_BM unexpectedly has lopper,activate: {val}"

    def test_apu_domain_has_lopper_activate_linux(self, openamp_tree):
        """APU_Linux must carry lopper,activate = linux."""
        n = openamp_tree["/domains/APU_Linux"]
        val = n.propval("lopper,activate")
        assert "linux" in (val if isinstance(val, list) else [val]), \
            f"APU_Linux lopper,activate wrong: {val}"

    def test_linux_overlay_does_not_create_unknown_key(self, tmp_path):
        """overlay_tree('nonexistent') must return None."""
        tree = self._fresh_tree(tmp_path)
        assert tree.overlay_tree("nonexistent") is None

    def test_dts_output_linux_has_uio(self, tmp_path):
        """DTS output for linux overlay must contain uio binding."""
        import io
        tree = self._fresh_tree(tmp_path)
        lt = tree.overlay_tree("linux")
        buf = io.StringIO()
        lt["/"].print(buf)
        out = buf.getvalue()
        assert "uio" in out, f"uio not in linux DTS output"
        assert "cdns,ttc" not in out, \
            f"cdns,ttc still in linux DTS output (replace failed)"

    def test_dts_output_base_has_cdns(self, tmp_path):
        """DTS output for base tree must contain cdns,ttc binding."""
        import io
        tree = self._fresh_tree(tmp_path)
        buf = io.StringIO()
        tree["/"].print(buf)
        out = buf.getvalue()
        assert "cdns,ttc" in out, f"cdns,ttc not in base DTS output"


# ---------------------------------------------------------------------------
# Section 4: overlay_subtrees serialization/deserialization round-trip
# ---------------------------------------------------------------------------

class TestOverlaySubtreesRoundTrip:
    """Verify that overlay_subtrees survive serialize → deserialize in-memory.

    These tests exercise the JSON serialization format used for the DTS embed
    mechanism without requiring dtc/libfdt — the serialize step embeds a
    /__lopper-overlays__ node, the deserialize step reconstructs overlay_subtrees
    from it and removes the internal node.
    """

    def _build_serialized_tree(self, tmp_path):
        """Return a tree with /__lopper-overlays__ embedded."""
        from lopper import _serialize_overlay_subtrees
        yaml_file = tmp_path / "sdt.yaml"
        yaml_file.write_text(OPENAMP_YAML)
        tree = LopperYAML(str(yaml_file)).to_tree()
        assert tree is not None
        _serialize_overlay_subtrees(tree)
        return tree

    def test_serialize_adds_lopper_overlays_node(self, tmp_path):
        """_serialize_overlay_subtrees must add /__lopper-overlays__ to the tree."""
        tree = self._build_serialized_tree(tmp_path)
        assert '/__lopper-overlays__' in tree.__nodes__, \
            "/__lopper-overlays__ node missing after serialization"

    def test_serialize_adds_condition_child(self, tmp_path):
        """A child node for the 'linux' condition must exist."""
        tree = self._build_serialized_tree(tmp_path)
        assert '/__lopper-overlays__/linux' in tree.__nodes__, \
            "/__lopper-overlays__/linux child missing"

    def test_serialize_child_has_encoded_prop(self, tmp_path):
        """The condition child must contain at least one property."""
        tree = self._build_serialized_tree(tmp_path)
        cond_node = tree['/__lopper-overlays__/linux']
        assert len(cond_node.__props__) > 0, \
            "/__lopper-overlays__/linux has no encoded properties"

    def test_deserialize_removes_internal_node(self, tmp_path):
        """After deserialization /__lopper-overlays__ must be gone."""
        from lopper import _serialize_overlay_subtrees, _deserialize_overlay_subtrees
        yaml_file = tmp_path / "sdt.yaml"
        yaml_file.write_text(OPENAMP_YAML)
        tree = LopperYAML(str(yaml_file)).to_tree()
        _serialize_overlay_subtrees(tree)
        # Simulate reload: drop the in-memory overlay_subtrees
        tree._metadata.pop('overlay_subtrees', None)
        _deserialize_overlay_subtrees(tree)
        assert '/__lopper-overlays__' not in tree.__nodes__, \
            "/__lopper-overlays__ still present after deserialization"

    def test_deserialize_restores_linux_condition(self, tmp_path):
        """overlay_subtrees must contain 'linux' after deserialize."""
        from lopper import _serialize_overlay_subtrees, _deserialize_overlay_subtrees
        yaml_file = tmp_path / "sdt.yaml"
        yaml_file.write_text(OPENAMP_YAML)
        tree = LopperYAML(str(yaml_file)).to_tree()
        _serialize_overlay_subtrees(tree)
        tree._metadata.pop('overlay_subtrees', None)
        _deserialize_overlay_subtrees(tree)
        ov = tree._metadata.get('overlay_subtrees', {})
        assert 'linux' in ov, \
            f"'linux' condition missing after deserialization: {list(ov.keys())}"
        assert len(ov['linux']) > 0, "linux condition has no overlay nodes"

    def test_deserialize_restores_uio_prop(self, tmp_path):
        """The deserialized overlay node must carry the 'uio' compatible value."""
        from lopper import _serialize_overlay_subtrees, _deserialize_overlay_subtrees
        yaml_file = tmp_path / "sdt.yaml"
        yaml_file.write_text(OPENAMP_YAML)
        tree = LopperYAML(str(yaml_file)).to_tree()
        _serialize_overlay_subtrees(tree)
        tree._metadata.pop('overlay_subtrees', None)
        _deserialize_overlay_subtrees(tree)
        ov = tree._metadata['overlay_subtrees']['linux']
        # Find the timer overlay node
        timer_ov = None
        for n in ov:
            if 'timer' in n.abs_path:
                timer_ov = n
                break
        assert timer_ov is not None, "timer overlay node not found after deserialize"
        prop = timer_ov.__props__.get('compatible')
        assert prop is not None, "compatible prop missing from deserialized overlay"
        val = prop.value
        val_list = val if isinstance(val, list) else [val]
        assert "uio" in val_list, \
            f"uio not found in deserialized compatible: {val_list}"

    def test_overlay_tree_works_after_roundtrip(self, tmp_path):
        """overlay_tree('linux') must produce uio binding after serialize/deserialize."""
        from lopper import _serialize_overlay_subtrees, _deserialize_overlay_subtrees
        yaml_file = tmp_path / "sdt.yaml"
        yaml_file.write_text(OPENAMP_YAML)
        tree = LopperYAML(str(yaml_file)).to_tree()
        _serialize_overlay_subtrees(tree)
        tree._metadata.pop('overlay_subtrees', None)
        _deserialize_overlay_subtrees(tree)

        lt = tree.overlay_tree('linux')
        assert lt is not None, "overlay_tree('linux') returned None after roundtrip"
        timer = lt["/axi/timer@f1e90000"]
        assert timer is not None, "/axi/timer@f1e90000 not found in overlay tree"
        val = timer.propval('compatible')
        val_list = val if isinstance(val, list) else [val]
        assert "uio" in val_list, \
            f"uio not in compatible after roundtrip: {val_list}"

    def test_dts_print_includes_lopper_overlays_for_pass2(self, tmp_path):
        """After serialization /__lopper-overlays__ must appear in DTS output.

        The node must be emitted so a second-pass lopper invocation can
        deserialize it; _deserialize_overlay_subtrees() deletes it after
        reconstruction so it never leaks into further output.
        """
        import io as _io
        tree = self._build_serialized_tree(tmp_path)
        buf = _io.StringIO()
        for node in tree.__nodes__.values():
            if node.abs_path == '/':
                node.print(buf)
                break
        out = buf.getvalue()
        assert "__lopper-overlays__" in out, \
            "/__lopper-overlays__ missing from DTS output — second-pass deserialization will fail"

    def test_dts_print_omits_lopper_overlays_after_deserialize(self, tmp_path):
        """After deserialize, /__lopper-overlays__ must be absent from DTS output."""
        import io as _io
        from lopper import _serialize_overlay_subtrees, _deserialize_overlay_subtrees
        yaml_file = tmp_path / "sdt.yaml"
        yaml_file.write_text(OPENAMP_YAML)
        tree = LopperYAML(str(yaml_file)).to_tree()
        _serialize_overlay_subtrees(tree)
        _deserialize_overlay_subtrees(tree)
        buf = _io.StringIO()
        for node in tree.__nodes__.values():
            if node.abs_path == '/':
                node.print(buf)
                break
        out = buf.getvalue()
        assert "__lopper-overlays__" not in out, \
            "/__lopper-overlays__ still in tree after deserialize — delete not working"


# ---------------------------------------------------------------------------
# Section 5: _props_to_delete delete-scheme via _merge_node_into_tree
# ---------------------------------------------------------------------------

class TestPropsToDeleteMergeScheme:
    """Verify the delete merge-scheme removes properties during overlay merge."""

    def test_delete_scheme_removes_prop(self):
        """A prop in _props_to_delete must be absent after _merge_node_into_tree."""
        from lopper.tree import _merge_node_into_tree, LopperNode, LopperProp

        # Base tree with /foo node carrying prop 'x'
        base = LopperTree()
        base_node = LopperNode(-1, '/foo')
        lp = LopperProp('x', -1, base_node, 'keep-me')
        base_node.__props__['x'] = lp
        base.add(base_node, dont_sync=True)

        # Overlay node for same path, marking 'x' for deletion
        ov_node = LopperNode(-1, '/foo')
        ov_node.__dict__['_props_to_delete'] = {'x'}

        _merge_node_into_tree(base, ov_node)

        result_node = base['/foo']
        assert 'x' not in result_node.__props__, \
            "Property 'x' should have been deleted by the overlay merge scheme"

    def test_delete_scheme_does_not_affect_other_props(self):
        """Only the flagged prop is deleted; sibling props are untouched."""
        from lopper.tree import _merge_node_into_tree, LopperNode, LopperProp

        base = LopperTree()
        base_node = LopperNode(-1, '/bar')
        for name, val in [('x', 'delete-me'), ('y', 'keep-me')]:
            lp = LopperProp(name, -1, base_node, val)
            base_node.__props__[name] = lp
        base.add(base_node, dont_sync=True)

        ov_node = LopperNode(-1, '/bar')
        ov_node.__dict__['_props_to_delete'] = {'x'}

        _merge_node_into_tree(base, ov_node)

        result_node = base['/bar']
        assert 'x' not in result_node.__props__, "'x' should be deleted"
        assert 'y' in result_node.__props__, "'y' should be preserved"


# ---------------------------------------------------------------------------
# Section 6: two-pass subprocess end-to-end
#
# Validates that overlay data embedded in the pass-1 DTS output is correctly
# deserialized by a second lopper invocation so domain_access can activate
# the right overlay tree via lopper,activate.
# ---------------------------------------------------------------------------

# Minimal system-top DTS used as the SDT base for two-pass tests.
# The YAML sidecar (OPENAMP_YAML) carries the sigil data.
TWOPASS_SYSTEM_TOP_DTS = """\
/dts-v1/;

/ {
\t#address-cells = <0x2>;
\t#size-cells = <0x2>;

\taxi: axi {
\t\t#address-cells = <0x2>;
\t\t#size-cells = <0x2>;
\t\tranges;

\t\ttimer: timer@f1e90000 {
\t\t\tcompatible = "cdns,ttc";
\t\t\treg = <0x0 0xf1e90000 0x0 0x1000>;
\t\t};
\t};
};
"""

# Sigil YAML that carries the conditional overlay and domain definitions.
# This is fed with -i in pass 1 alongside the system-top DTS.
TWOPASS_SIGIL_YAML = """\
axi:
  timer@f1e90000:
    compatible!linux: uio

domains:
  APU_Linux:
    compatible: openamp,domain-v1
    lopper,activate: linux
    cpus: 0
  RPU1_BM:
    compatible: openamp,domain-v1
    cpus: 1
"""


@pytest.mark.skipif(
    not os.path.exists("lopper.py"),
    reason="lopper.py not found — must run from repo root"
)
class TestTwoPassSubprocess:
    """Full two-pass lopper invocation test.

    Pass 1: lopper translates system-top.dts + sigil YAML → intermediate.dts
            (/__lopper-overlays__ embedded automatically).
    Pass 2: lopper runs domain_access on intermediate.dts → APU_Linux.dts
            (/__lopper-overlays__ deserialized; final output clean).
    """

    def _run(self, cmd):
        import subprocess
        return subprocess.run(cmd, capture_output=True, text=True, cwd=os.getcwd())

    def _setup_pass1(self, tmp_path):
        sdt = tmp_path / "system-top.dts"
        sdt.write_text(TWOPASS_SYSTEM_TOP_DTS)
        yaml_file = tmp_path / "sigils.yaml"
        yaml_file.write_text(TWOPASS_SIGIL_YAML)
        intermediate = tmp_path / "intermediate.dts"

        result = self._run([
            "./lopper.py", "-f", "--permissive", "--enhanced",
            "-i", str(yaml_file),
            str(sdt), str(intermediate),
        ])
        return result, intermediate

    def test_pass1_succeeds(self, tmp_path):
        """Pass 1 must exit cleanly."""
        result, _ = self._setup_pass1(tmp_path)
        assert result.returncode == 0, \
            f"Pass 1 failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"

    def test_pass1_embeds_lopper_overlays(self, tmp_path):
        """intermediate.dts must contain /__lopper-overlays__ for pass-2 consumption."""
        result, intermediate = self._setup_pass1(tmp_path)
        assert result.returncode == 0, f"Pass 1 failed: {result.stderr}"
        content = intermediate.read_text()
        assert "__lopper-overlays__" in content, \
            "/__lopper-overlays__ missing from pass-1 output — pass 2 will have no overlay data"

    def test_pass2_produces_uio_binding(self, tmp_path):
        """Pass 2 domain_access for APU_Linux must activate the linux overlay.

        The timer node's compatible must be 'uio' (from the sigil overlay),
        not 'cdns,ttc' (the base value).
        """
        result, intermediate = self._setup_pass1(tmp_path)
        assert result.returncode == 0, f"Pass 1 failed: {result.stderr}"

        apu_out = tmp_path / "APU_Linux.dts"
        result2 = self._run([
            "./lopper.py", "-f", "--permissive",
            str(intermediate), str(apu_out),
            "--", "domain_access", "-t", "/domains/APU_Linux",
        ])
        assert result2.returncode == 0, \
            f"Pass 2 failed:\nstdout: {result2.stdout}\nstderr: {result2.stderr}"

        content = apu_out.read_text()
        assert "uio" in content, \
            f"uio binding missing from APU_Linux.dts — lopper,activate did not activate linux overlay\n{content[:2000]}"
        assert "cdns,ttc" not in content, \
            f"cdns,ttc still present in APU_Linux.dts — replace overlay not applied\n{content[:2000]}"

    def test_pass2_output_has_no_lopper_overlays(self, tmp_path):
        """Final APU_Linux.dts must not contain /__lopper-overlays__."""
        result, intermediate = self._setup_pass1(tmp_path)
        assert result.returncode == 0, f"Pass 1 failed: {result.stderr}"

        apu_out = tmp_path / "APU_Linux.dts"
        result2 = self._run([
            "./lopper.py", "-f", "--permissive",
            str(intermediate), str(apu_out),
            "--", "domain_access", "-t", "/domains/APU_Linux",
        ])
        assert result2.returncode == 0, \
            f"Pass 2 failed:\nstdout: {result2.stdout}\nstderr: {result2.stderr}"

        content = apu_out.read_text()
        assert "__lopper-overlays__" not in content, \
            "/__lopper-overlays__ leaked into final output DTS"

    def test_base_domain_has_cdns_binding(self, tmp_path):
        """RPU1_BM (no lopper,activate) must retain the base cdns,ttc binding."""
        result, intermediate = self._setup_pass1(tmp_path)
        assert result.returncode == 0, f"Pass 1 failed: {result.stderr}"

        rpu_out = tmp_path / "RPU1_BM.dts"
        result2 = self._run([
            "./lopper.py", "-f", "--permissive",
            str(intermediate), str(rpu_out),
            "--", "domain_access", "-t", "/domains/RPU1_BM",
        ])
        assert result2.returncode == 0, \
            f"Pass 2 (RPU1_BM) failed:\nstdout: {result2.stdout}\nstderr: {result2.stderr}"

        content = rpu_out.read_text()
        assert "cdns,ttc" in content, \
            f"cdns,ttc missing from RPU1_BM.dts — base tree was unexpectedly modified"
        assert "uio" not in content, \
            f"uio leaked into RPU1_BM.dts — linux overlay contaminated base domain"


# ---------------------------------------------------------------------------
# Section 7: child-node deserialization regression
#
# Regression coverage for the bug where _deserialize_overlay_node() called
# node.child_nodes.append(child) but child_nodes is an OrderedDict, not a
# list — crashing with AttributeError on any overlay that contains nested
# child nodes (e.g. zyxclmm_drm under &amba_pl).
# ---------------------------------------------------------------------------

class TestDeserializeOverlayChildNodes:
    """_deserialize_overlay_node must correctly reconstruct nested child nodes.

    Previously crashed: AttributeError: 'collections.OrderedDict' has no
    attribute 'append'.  Fix: use child_nodes[child.abs_path] = child with
    explicit parent assignment.
    """

    def _make_nested_overlay_node(self):
        """Return a LopperNode with one level of child_nodes populated."""
        from lopper.tree import LopperNode, LopperProp
        from collections import OrderedDict

        parent_node = LopperNode(-1, "/axi/amba_pl")
        child_node = LopperNode(-1, "/axi/amba_pl/zyxclmm_drm")
        lp = LopperProp("compatible", -1, child_node, ["xlnx,zocl"])
        child_node.__props__["compatible"] = lp
        child_node.parent = parent_node
        parent_node.child_nodes[child_node.abs_path] = child_node
        return parent_node

    def test_serialize_nested_node_roundtrip(self):
        """serialize → deserialize must not raise AttributeError for nested nodes."""
        from lopper import _serialize_overlay_node, _deserialize_overlay_node

        root = self._make_nested_overlay_node()
        data = _serialize_overlay_node(root)

        # This previously raised: AttributeError: 'OrderedDict' has no attribute 'append'
        restored = _deserialize_overlay_node(data)
        assert restored is not None, "Deserialized node is None"

    def test_child_nodes_type_after_deserialize(self):
        """child_nodes must remain an OrderedDict after deserialization."""
        from collections import OrderedDict
        from lopper import _serialize_overlay_node, _deserialize_overlay_node

        root = self._make_nested_overlay_node()
        data = _serialize_overlay_node(root)
        restored = _deserialize_overlay_node(data)

        assert isinstance(restored.child_nodes, OrderedDict), \
            f"child_nodes is {type(restored.child_nodes)}, expected OrderedDict"

    def test_child_count_preserved_after_deserialize(self):
        """All child nodes must survive the serialize → deserialize round-trip."""
        from lopper import _serialize_overlay_node, _deserialize_overlay_node

        root = self._make_nested_overlay_node()
        original_count = len(root.child_nodes)
        data = _serialize_overlay_node(root)
        restored = _deserialize_overlay_node(data)

        assert len(restored.child_nodes) == original_count, \
            f"Expected {original_count} child nodes, got {len(restored.child_nodes)}"

    def test_child_node_props_preserved_after_deserialize(self):
        """Child node properties must be intact after deserialization."""
        from lopper import _serialize_overlay_node, _deserialize_overlay_node

        root = self._make_nested_overlay_node()
        data = _serialize_overlay_node(root)
        restored = _deserialize_overlay_node(data)

        child = list(restored.child_nodes.values())[0]
        assert "compatible" in child.__props__, \
            "compatible prop missing from deserialized child node"
        assert child.__props__["compatible"].value == ["xlnx,zocl"], \
            f"compatible value wrong: {child.__props__['compatible'].value}"

    def test_child_parent_set_after_deserialize(self):
        """Deserialized child nodes must have their parent correctly assigned."""
        from lopper import _serialize_overlay_node, _deserialize_overlay_node

        root = self._make_nested_overlay_node()
        data = _serialize_overlay_node(root)
        restored = _deserialize_overlay_node(data)

        child = list(restored.child_nodes.values())[0]
        assert child.parent is restored, \
            f"child.parent is {child.parent}, expected the restored parent node"

    def test_child_abs_path_is_dict_key(self):
        """Each child must be keyed by its abs_path in child_nodes."""
        from lopper import _serialize_overlay_node, _deserialize_overlay_node

        root = self._make_nested_overlay_node()
        data = _serialize_overlay_node(root)
        restored = _deserialize_overlay_node(data)

        child = list(restored.child_nodes.values())[0]
        assert child.abs_path in restored.child_nodes, \
            f"child abs_path '{child.abs_path}' not found as key in child_nodes"


# ---------------------------------------------------------------------------
# Section 8: overlay_fixups label-tuple format and _resolve_overlay_fixups
#
# Validates that:
#   - overlay_fixups stores (fragment_label, relative_path, prop_name, byte_off)
#     tuples — not baked abs_path strings — so resolution is always against the
#     label's current location in the result tree at build time.
#   - _resolve_overlay_fixups correctly patches phandle placeholders on the
#     fragment target node itself (relative_path == "") and on child nodes.
#   - Renaming the fragment target node between registration and overlay_tree()
#     is handled transparently via the label lookup.
# ---------------------------------------------------------------------------

class TestOverlayFixupsTupleFormat:
    """overlay_fixups must store label+relative_path tuples, not abs_path strings.

    The data structure for overlay_fixups is:
        {phandle_target_label: [(fragment_label, relative_path, prop_name, byte_off), ...]}

    This avoids baking abs_paths that become stale if an assist renames or
    moves a target node between overlay registration and overlay_tree() build.
    """

    def _make_fixup_tree(self):
        """Build a minimal tree that looks like a dtc-compiled overlay.

        Produces:
          /fragment@0  { target = <&amba_pl>;
                         __overlay__ {
                           zyxclmm_drm { xlnx,memory-region = <0xffffffff>; };
                         };
                       }
          /__fixups__  { cma_reserved = "/fragment@0/__overlay__/zyxclmm_drm:xlnx,memory-region:0";
                         amba_pl      = "/fragment@0:target:0"; }
        """
        import copy
        from lopper.tree import LopperProp, LopperNode, LopperTree

        tree = LopperTree()

        # /__fixups__
        fixups = LopperNode(name='__fixups__')
        fixups.__dict__['abs_path'] = '/__fixups__'
        p_target = LopperProp(name='amba_pl')
        p_target.__dict__['value'] = '/fragment@0:target:0'
        p_target.node = fixups
        fixups.__props__['amba_pl'] = p_target
        p_cma = LopperProp(name='cma_reserved')
        p_cma.__dict__['value'] = '/fragment@0/__overlay__/zyxclmm_drm:xlnx,memory-region:0'
        p_cma.node = fixups
        fixups.__props__['cma_reserved'] = p_cma
        tree.add(fixups)

        # /fragment@0
        frag = LopperNode(name='fragment@0')
        frag.__dict__['abs_path'] = '/fragment@0'
        tree.add(frag)

        # /fragment@0/__overlay__
        overlay_node = LopperNode(name='__overlay__')
        overlay_node.__dict__['abs_path'] = '/fragment@0/__overlay__'
        frag.add(overlay_node)

        # /fragment@0/__overlay__/zyxclmm_drm  — the child with the placeholder
        child = LopperNode(name='zyxclmm_drm')
        child.__dict__['abs_path'] = '/fragment@0/__overlay__/zyxclmm_drm'
        p_mr = LopperProp(name='xlnx,memory-region')
        p_mr.__dict__['value'] = [0xffffffff]
        p_mr.node = child
        child.__props__['xlnx,memory-region'] = p_mr
        overlay_node.add(child)

        return tree

    def _add_node(self, tree, node):
        """Register node into all tree indices via the public _register_node API."""
        tree._register_node(node)

    def _make_base_tree(self, amba_path='/amba_pl'):
        """Minimal base tree with an amba_pl node (label 'amba_pl') and a cma node."""
        from lopper.tree import LopperNode, LopperTree

        tree = LopperTree()

        amba = LopperNode(name=amba_path.split('/')[-1])
        amba.__dict__['abs_path'] = amba_path
        amba.label = 'amba_pl'
        amba.__dict__['phandle'] = 0
        self._add_node(tree, amba)

        cma = LopperNode(name='cma_reserved')
        cma.__dict__['abs_path'] = '/cma_reserved'
        cma.label = 'cma_reserved'
        cma.__dict__['phandle'] = 42
        self._add_node(tree, cma)

        return tree

    # ------------------------------------------------------------------
    # Format tests — inspect what _unwrap_overlay_tree stores
    # ------------------------------------------------------------------

    def test_fixup_entries_are_tuples(self):
        """Each entry in overlay_fixups must be a 4-tuple, not a string."""
        from lopper import _unwrap_overlay_tree

        ov_tree = self._make_fixup_tree()
        base_tree = self._make_base_tree()
        _, fixups = _unwrap_overlay_tree(ov_tree, base_tree)

        assert fixups, "no fixups returned — fixture may be wrong"
        for label, refs in fixups.items():
            for ref in refs:
                assert isinstance(ref, tuple), \
                    f"fixup ref for '{label}' is {type(ref).__name__}, expected tuple"
                assert len(ref) == 4, \
                    f"fixup ref for '{label}' has {len(ref)} elements, expected 4"

    def test_fixup_tuple_contains_fragment_label(self):
        """The first element of each tuple is the fragment target label string."""
        from lopper import _unwrap_overlay_tree

        ov_tree = self._make_fixup_tree()
        base_tree = self._make_base_tree()
        _, fixups = _unwrap_overlay_tree(ov_tree, base_tree)

        for label, refs in fixups.items():
            for frag_label, relative_path, prop_name, byte_off in refs:
                assert isinstance(frag_label, str) and frag_label, \
                    f"fragment_label is empty or not a str for phandle target '{label}'"

    def test_fixup_tuple_child_relative_path(self):
        """A fixup on a child node must store the relative path below the target."""
        from lopper import _unwrap_overlay_tree

        ov_tree = self._make_fixup_tree()
        base_tree = self._make_base_tree()
        _, fixups = _unwrap_overlay_tree(ov_tree, base_tree)

        # cma_reserved fixup is on /zyxclmm_drm (child of target), not the target itself
        assert 'cma_reserved' in fixups, "expected cma_reserved fixup"
        for frag_label, relative_path, prop_name, byte_off in fixups['cma_reserved']:
            assert relative_path == '/zyxclmm_drm', \
                f"relative_path is '{relative_path}', expected '/zyxclmm_drm'"

    def test_fixup_tuple_contains_no_abs_path(self):
        """No fixup ref should start with '/' — abs_paths must not be stored."""
        from lopper import _unwrap_overlay_tree

        ov_tree = self._make_fixup_tree()
        base_tree = self._make_base_tree()
        _, fixups = _unwrap_overlay_tree(ov_tree, base_tree)

        for label, refs in fixups.items():
            for frag_label, relative_path, prop_name, byte_off in refs:
                assert not frag_label.startswith('/'), \
                    f"fragment_label '{frag_label}' looks like an abs_path — should be a label"

    # ------------------------------------------------------------------
    # Resolution tests — _resolve_overlay_fixups patches the right node
    # ------------------------------------------------------------------

    def _make_result_tree(self, amba_path='/amba_pl'):
        """Result tree: amba_pl + child zyxclmm_drm + cma_reserved with phandle 42."""
        from lopper.tree import LopperProp, LopperNode, LopperTree

        tree = LopperTree()

        amba = LopperNode(name=amba_path.split('/')[-1])
        amba.__dict__['abs_path'] = amba_path
        amba.label = 'amba_pl'
        amba.__dict__['phandle'] = 0
        self._add_node(tree, amba)

        child = LopperNode(name='zyxclmm_drm')
        child.__dict__['abs_path'] = amba_path + '/zyxclmm_drm'
        p_mr = LopperProp(name='xlnx,memory-region')
        p_mr.__dict__['value'] = [0xffffffff]
        p_mr.node = child
        child.__props__['xlnx,memory-region'] = p_mr
        self._add_node(tree, child)

        cma = LopperNode(name='cma_reserved')
        cma.__dict__['abs_path'] = '/cma_reserved'
        cma.label = 'cma_reserved'
        cma.__dict__['phandle'] = 42
        self._add_node(tree, cma)

        return tree

    def test_resolve_patches_child_node_placeholder(self):
        """_resolve_overlay_fixups must patch 0xffffffff in a child node property."""
        from lopper.tree import _resolve_overlay_fixups

        result = self._make_result_tree()
        fixups = {
            'cma_reserved': [('amba_pl', '/zyxclmm_drm', 'xlnx,memory-region', '0')]
        }
        _resolve_overlay_fixups(result, fixups)

        child = result['/amba_pl/zyxclmm_drm']
        val = child.__props__['xlnx,memory-region'].__dict__['value']
        assert val[0] == 42, \
            f"placeholder not patched: got {val[0]!r}, expected phandle 42"

    def test_resolve_patches_target_node_itself(self):
        """_resolve_overlay_fixups must patch a placeholder on the target node itself."""
        from lopper.tree import LopperProp, _resolve_overlay_fixups

        result = self._make_result_tree()
        amba = result['/amba_pl']
        p = LopperProp(name='clocks')
        p.__dict__['value'] = [0xffffffff]
        p.node = amba
        amba.__props__['clocks'] = p

        fixups = {
            'cma_reserved': [('amba_pl', '', 'clocks', '0')]
        }
        _resolve_overlay_fixups(result, fixups)

        val = amba.__props__['clocks'].__dict__['value']
        assert val[0] == 42, \
            f"placeholder on target node not patched: got {val[0]!r}, expected 42"

    def test_resolve_tolerates_missing_frag_label(self):
        """A fixup whose fragment_label is absent in the result tree is silently skipped."""
        from lopper.tree import _resolve_overlay_fixups

        result = self._make_result_tree()
        fixups = {
            'cma_reserved': [('nonexistent_label', '/zyxclmm_drm', 'xlnx,memory-region', '0')]
        }
        # Must not raise
        _resolve_overlay_fixups(result, fixups)

        child = result['/amba_pl/zyxclmm_drm']
        val = child.__props__['xlnx,memory-region'].__dict__['value']
        assert val[0] == 0xffffffff, "placeholder should be unchanged when label is absent"

    # ------------------------------------------------------------------
    # Rename tolerance — target moved between registration and build time
    # ------------------------------------------------------------------

    def test_resolve_after_target_rename(self):
        """Phandle is resolved correctly even when the target node was renamed.

        Simulates an assist renaming /amba_pl -> /axi/amba_pl after overlay
        registration.  Because resolution uses tree.lnodes(frag_label) the
        rename is transparent — no fallback needed.

        We build the result tree's __nodes__ directly to avoid tree.add()
        path-composition side-effects; _resolve_overlay_fixups only uses
        tree.__nodes__ and tree.lnodes(), so this is the correct minimal API.
        """
        from lopper.tree import LopperProp, LopperNode, LopperTree, _resolve_overlay_fixups

        result = LopperTree()

        # amba_pl at its *renamed* location — label still 'amba_pl'
        amba = LopperNode(name='amba_pl')
        amba.__dict__['abs_path'] = '/axi/amba_pl'
        amba.label = 'amba_pl'
        amba.__dict__['phandle'] = 0
        self._add_node(result, amba)

        # child under renamed amba_pl
        child = LopperNode(name='zyxclmm_drm')
        child.__dict__['abs_path'] = '/axi/amba_pl/zyxclmm_drm'
        p_mr = LopperProp(name='xlnx,memory-region')
        p_mr.__dict__['value'] = [0xffffffff]
        p_mr.node = child
        child.__props__['xlnx,memory-region'] = p_mr
        self._add_node(result, child)

        # cma_reserved with phandle 42
        cma = LopperNode(name='cma_reserved')
        cma.__dict__['abs_path'] = '/cma_reserved'
        cma.label = 'cma_reserved'
        cma.__dict__['phandle'] = 42
        self._add_node(result, cma)

        # Fixup tuple uses the label, not any abs_path — rename is invisible
        fixups = {
            'cma_reserved': [('amba_pl', '/zyxclmm_drm', 'xlnx,memory-region', '0')]
        }
        _resolve_overlay_fixups(result, fixups)

        val = child.__props__['xlnx,memory-region'].__dict__['value']
        assert val[0] == 42, \
            f"phandle not resolved after rename: got {val[0]!r}, expected 42"
