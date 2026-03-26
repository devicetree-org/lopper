"""
Tests for overlay generation functionality.

Tests the node_refs(), tree_refs(), fragment_create(), and fragment_add_for_refs()
methods used for generating overlay fragments when base tree properties reference
overlay nodes.
"""

import pytest
import tempfile
from pathlib import Path

from lopper.tree import LopperTree, LopperNode, LopperProp
import lopper.base


class TestNodeRefs:
    """Tests for LopperNode.node_refs() method."""

    def test_node_refs_finds_phandle_reference(self, lopper_tree):
        """
        Test that node_refs finds properties that reference a node's phandle.
        """
        # Find a node that has a phandle and is referenced by another property
        # In the test tree, interrupt-parent properties reference interrupt controllers
        target_node = None
        for node in lopper_tree:
            if node.phandle and node.phandle > 0:
                # Check if any node references this phandle
                refs = node.node_refs(lopper_tree)
                if refs:
                    target_node = node
                    break

        if target_node:
            refs = target_node.node_refs(lopper_tree)
            assert len(refs) > 0, "Expected to find references"
            # Each ref is (node, prop_name, companion)
            for ref_node, prop_name, companion in refs:
                assert ref_node is not None
                assert prop_name is not None

    def test_node_refs_no_false_positives_from_integers(self):
        """
        Test that node_refs doesn't match arbitrary integers as phandles.

        This tests the fix for Bug 2: reset-gpios = <&gpio0 0x19 0x1> should
        not match a node with phandle 0x19 because 0x19 is a GPIO pin number,
        not a phandle reference.
        """
        # Create a minimal tree
        tree = LopperTree()
        root = tree['/']
        root + LopperProp(name='#gpio-cells', value=[2])

        # GPIO controller with phandle 0x96
        gpio0 = LopperNode(-1, "/gpio0")
        gpio0.phandle = 0x96
        gpio0 + LopperProp(name='#gpio-cells', value=[2])
        tree.add(gpio0)

        # A node with phandle 0x19 (same as GPIO pin number in reset-gpios)
        other_node = LopperNode(-1, "/other_node")
        other_node.phandle = 0x19
        tree.add(other_node)

        # PHY node with reset-gpios referencing gpio0, with pin 0x19
        phy0 = LopperNode(-1, "/phy0")
        phy0.phandle = 0x10
        # reset-gpios = <&gpio0 0x19 0x1> -> [0x96, 0x19, 0x1]
        phy0 + LopperProp(name='reset-gpios', value=[0x96, 0x19, 0x1])
        tree.add(phy0)

        # Sync and resolve the tree
        tree.sync()
        tree.resolve()

        # Now check: other_node (phandle 0x19) should NOT be found as referenced
        # by phy0's reset-gpios, because 0x19 is the GPIO pin, not a phandle
        refs = other_node.node_refs(tree)

        # Should find NO references - 0x19 is a GPIO pin number, not a phandle
        assert len(refs) == 0, (
            f"False positive: found {len(refs)} references to node with phandle 0x19, "
            "but 0x19 appears as a GPIO pin number, not a phandle reference"
        )


class TestCompanionPropertySync:
    """Tests for companion property synchronization in strict mode."""

    def test_companion_sync_on_phandle_drop(self):
        """
        Test that when strict mode drops invalid phandle records,
        the companion property is also updated.

        This tests the fix for Bug 1: clock-names should be synced
        when clocks entries are dropped due to invalid phandles.
        """
        # Create a tree with clocks and clock-names properties
        tree = LopperTree()
        tree.strict = True
        root = tree['/']
        root + LopperProp(name='#clock-cells', value=[1])

        # Valid clock controller
        clk_ctrl = LopperNode(-1, "/clock-controller")
        clk_ctrl.phandle = 0xAD
        clk_ctrl + LopperProp(name='#clock-cells', value=[1])
        tree.add(clk_ctrl)

        # Device node with clocks and clock-names
        device = LopperNode(-1, "/device")
        device.phandle = 0x10

        # clocks = <&clk_ctrl 0x1>, <&invalid 0x2>, <&clk_ctrl 0x3>
        # where &invalid (0xFF) doesn't exist in the tree
        device + LopperProp(name='clocks', value=[0xAD, 0x1, 0xFF, 0x2, 0xAD, 0x3])
        device + LopperProp(name='clock-names', value=["clk_a", "clk_b", "clk_c"])
        tree.add(device)

        tree.sync()

        # Resolve the clocks property with strict mode and companion sync
        clocks_prop = device['clocks']
        clocks_prop.resolve(strict=True, sync_companions=True)

        # After strict mode drops the invalid record, clock-names should have 2 entries
        clock_names_prop = device['clock-names']
        assert len(clock_names_prop.value) == 2, (
            f"Expected clock-names to have 2 entries after dropping invalid phandle, "
            f"got {len(clock_names_prop.value)}: {clock_names_prop.value}"
        )
        assert clock_names_prop.value == ["clk_a", "clk_c"], (
            f"Expected clock-names to be ['clk_a', 'clk_c'], got {clock_names_prop.value}"
        )


class TestTreeRefs:
    """Tests for LopperTree.tree_refs() method."""

    def test_tree_refs_finds_cross_tree_references(self):
        """
        Test that tree_refs finds properties in base tree that reference
        nodes in a target (overlay) tree.
        """
        # Create base tree
        base_tree = LopperTree()
        base_root = base_tree['/']
        base_root + LopperProp(name='#clock-cells', value=[1])

        # Device in base tree that references a clock
        device = LopperNode(-1, "/device")
        device.phandle = 0x10
        device.label = "my_device"
        device + LopperProp(name='clocks', value=[0x20, 0x0])  # References phandle 0x20
        device + LopperProp(name='clock-names', value=["pl_clk"])
        base_tree.add(device)
        base_tree.sync()
        base_tree.resolve()

        # Create overlay tree with the referenced clock node
        overlay_tree = LopperTree()
        overlay_root = overlay_tree['/']

        pl_clock = LopperNode(-1, "/pl_clock")
        pl_clock.phandle = 0x20  # This is what device references
        pl_clock.label = "pl_clk_0"
        pl_clock + LopperProp(name='#clock-cells', value=[1])
        overlay_tree.add(pl_clock)
        overlay_tree.sync()
        overlay_tree.resolve()

        # Find references from base_tree to overlay_tree
        refs = base_tree.tree_refs(overlay_tree)

        # Should find device's clocks property references pl_clock
        assert len(refs) > 0, "Expected to find references from base to overlay"

        found_device_ref = False
        for ref_node, prop_name, companion in refs:
            if ref_node.name == "device" and prop_name == "clocks":
                found_device_ref = True
                assert companion == "clock-names", f"Expected companion 'clock-names', got {companion}"

        assert found_device_ref, "Expected to find device's clocks property in refs"


class TestFragmentCreate:
    """Tests for LopperTree.fragment_create() method."""

    def test_fragment_create_copies_specified_properties(self):
        """
        Test that fragment_create creates a node with only specified properties.
        """
        tree = LopperTree()
        root = tree['/']

        # Source node with multiple properties
        source = LopperNode(-1, "/source_node")
        source.phandle = 0x10
        source.label = "my_source"
        source + LopperProp(name='prop_a', value=[1, 2, 3])
        source + LopperProp(name='prop_b', value=["hello"])
        source + LopperProp(name='prop_c', value=[0xFF])
        tree.add(source)
        tree.sync()

        # Create fragment with only prop_a and prop_b
        fragment = tree.fragment_create(source, ["prop_a", "prop_b"])

        assert fragment is not None, "fragment_create should return a node"
        assert fragment.name == "&my_source", f"Expected name '&my_source', got {fragment.name}"
        assert "prop_a" in fragment.__props__, "Fragment should have prop_a"
        assert "prop_b" in fragment.__props__, "Fragment should have prop_b"
        assert "prop_c" not in fragment.__props__, "Fragment should NOT have prop_c"

    def test_fragment_create_requires_label(self):
        """
        Test that fragment_create returns None if source node has no label.
        """
        tree = LopperTree()
        root = tree['/']

        # Source node WITHOUT label
        source = LopperNode(-1, "/source_node")
        source.phandle = 0x10
        # source.label is not set
        source + LopperProp(name='prop_a', value=[1, 2, 3])
        tree.add(source)
        tree.sync()

        # Create fragment - should return None because no label
        fragment = tree.fragment_create(source, ["prop_a"])

        assert fragment is None, "fragment_create should return None when source has no label"


class TestFragmentAddForRefs:
    """Tests for LopperTree.fragment_add_for_refs() method."""

    def test_fragment_add_for_refs_creates_overlay_fragments(self):
        """
        Test that fragment_add_for_refs creates overlay fragments for
        properties that reference overlay nodes.
        """
        # Create base tree
        base_tree = LopperTree()
        base_root = base_tree['/']
        base_root + LopperProp(name='#clock-cells', value=[1])

        # Device that references an overlay clock
        device = LopperNode(-1, "/device")
        device.phandle = 0x10
        device.label = "my_device"
        device + LopperProp(name='clocks', value=[0x20, 0x0])  # References phandle 0x20
        device + LopperProp(name='clock-names', value=["pl_clk"])
        base_tree.add(device)
        base_tree.sync()
        base_tree.resolve()

        # Create overlay tree
        overlay_tree = LopperTree()
        overlay_root = overlay_tree['/']

        pl_clock = LopperNode(-1, "/pl_clock")
        pl_clock.phandle = 0x20
        pl_clock.label = "pl_clk_0"
        pl_clock + LopperProp(name='#clock-cells', value=[1])
        overlay_tree.add(pl_clock)
        overlay_tree.sync()
        overlay_tree.resolve()

        # Count nodes before
        overlay_count_before = sum(1 for _ in overlay_tree)

        # Add fragments for refs
        fragments_added = base_tree.fragment_add_for_refs(overlay_tree)

        # Count nodes after
        overlay_count_after = sum(1 for _ in overlay_tree)

        assert fragments_added > 0, "Expected at least one fragment to be added"
        assert overlay_count_after > overlay_count_before, (
            "Overlay tree should have more nodes after adding fragments"
        )

        # Find the fragment node
        fragment_found = False
        for node in overlay_tree:
            if node.name == "&my_device":
                fragment_found = True
                assert "clocks" in node.__props__, "Fragment should have clocks property"
                break

        assert fragment_found, "Expected to find &my_device fragment in overlay tree"
