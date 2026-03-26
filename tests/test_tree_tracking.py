"""
Tests for unified tree tracking in Lopper core.

Tests the _metadata infrastructure on LopperTree that enables:
- Automatic capture of deleted nodes in extracted trees
- Registration of overlays with parent trees
- Backward compatibility with _external_trees
- Child tree access helpers
"""

import pytest
from lopper.tree import LopperTree, LopperNode


class TestMetadataInitialization:
    """Test that _metadata is properly initialized."""

    def test_metadata_exists_on_new_tree(self):
        """New trees should have _metadata dict initialized."""
        tree = LopperTree()
        assert hasattr(tree, '_metadata')
        assert isinstance(tree._metadata, dict)

    def test_metadata_has_required_keys(self):
        """_metadata should have all required keys."""
        tree = LopperTree()
        required_keys = ['type', 'name', 'source', 'parent', 'child_trees', 'external_trees']
        for key in required_keys:
            assert key in tree._metadata, f"Missing key: {key}"

    def test_metadata_default_type_is_primary(self):
        """New trees should default to type 'primary'."""
        tree = LopperTree()
        assert tree._metadata['type'] == 'primary'

    def test_metadata_child_trees_starts_empty(self):
        """child_trees should start as empty list."""
        tree = LopperTree()
        assert tree._metadata['child_trees'] == []

    def test_metadata_external_trees_starts_empty(self):
        """external_trees should start as empty list."""
        tree = LopperTree()
        assert tree._metadata['external_trees'] == []


class TestExternalTreesBackwardCompatibility:
    """Test backward compatibility of _external_trees property."""

    def test_external_trees_property_returns_metadata_list(self):
        """_external_trees property should delegate to metadata."""
        tree = LopperTree()
        # Add a tree to external_trees via metadata
        other_tree = LopperTree()
        tree._metadata['external_trees'].append(other_tree)

        # Property should return the same list
        assert other_tree in tree._external_trees

    def test_external_trees_setter_updates_metadata(self):
        """Setting _external_trees should update metadata."""
        tree = LopperTree()
        other_tree = LopperTree()

        tree._external_trees = [other_tree]

        assert tree._metadata['external_trees'] == [other_tree]

    def test_external_trees_append_works(self):
        """Appending to _external_trees should work."""
        tree = LopperTree()
        other_tree = LopperTree()

        tree._external_trees.append(other_tree)

        assert other_tree in tree._metadata['external_trees']


class TestChildTreeHelpers:
    """Test child_trees(), extracted_trees(), overlay_trees() helpers."""

    def test_child_trees_returns_all_children(self):
        """child_trees() without filter returns all children."""
        tree = LopperTree()
        child1 = LopperTree()
        child1._metadata['type'] = 'extracted'
        child2 = LopperTree()
        child2._metadata['type'] = 'overlay'

        tree._metadata['child_trees'] = [child1, child2]

        assert len(tree.child_trees()) == 2
        assert child1 in tree.child_trees()
        assert child2 in tree.child_trees()

    def test_child_trees_filters_by_type(self):
        """child_trees(type) should filter by type."""
        tree = LopperTree()
        child1 = LopperTree()
        child1._metadata['type'] = 'extracted'
        child2 = LopperTree()
        child2._metadata['type'] = 'overlay'

        tree._metadata['child_trees'] = [child1, child2]

        extracted = tree.child_trees('extracted')
        assert len(extracted) == 1
        assert child1 in extracted

        overlays = tree.child_trees('overlay')
        assert len(overlays) == 1
        assert child2 in overlays

    def test_extracted_trees_helper(self):
        """extracted_trees() should return only extracted type."""
        tree = LopperTree()
        child1 = LopperTree()
        child1._metadata['type'] = 'extracted'
        child2 = LopperTree()
        child2._metadata['type'] = 'overlay'

        tree._metadata['child_trees'] = [child1, child2]

        assert tree.extracted_trees() == [child1]

    def test_overlay_trees_helper(self):
        """overlay_trees() should return only overlay type."""
        tree = LopperTree()
        child1 = LopperTree()
        child1._metadata['type'] = 'extracted'
        child2 = LopperTree()
        child2._metadata['type'] = 'overlay'

        tree._metadata['child_trees'] = [child1, child2]

        assert tree.overlay_trees() == [child2]


class TestDeleteCapture:
    """Test that delete() captures nodes in extracted trees."""

    def test_delete_creates_extracted_tree(self):
        """Deleting a node should create an extracted tree."""
        tree = LopperTree()
        # Create a node and add it
        foo = LopperNode(-1, "/foo")
        tree.add(foo)
        tree.sync()
        tree.resolve()

        # Delete the node
        tree.delete(tree['/foo'])

        # Check that an extracted tree was created
        assert len(tree.extracted_trees()) == 1
        extracted = tree.extracted_trees()[0]
        assert extracted._metadata['name'] == 'extracted_foo'
        assert extracted._metadata['type'] == 'extracted'

    def test_delete_with_capture_false_no_extraction(self):
        """delete(capture=False) should not create extracted tree."""
        tree = LopperTree()
        foo = LopperNode(-1, "/foo")
        tree.add(foo)
        tree.sync()
        tree.resolve()

        tree.delete(tree['/foo'], capture=False)

        assert len(tree.extracted_trees()) == 0

    def test_extracted_tree_not_created_for_already_extracted(self):
        """Extracted trees should not create child extracted trees."""
        tree = LopperTree()
        tree._metadata['type'] = 'extracted'  # Mark as extracted

        foo = LopperNode(-1, "/foo")
        tree.add(foo)
        tree.sync()
        tree.resolve()

        tree.delete(tree['/foo'])

        # Should not create nested extracted tree
        assert len(tree.extracted_trees()) == 0

    def test_subtraction_operator_creates_extracted_tree(self):
        """tree - node should create extracted tree via capture."""
        tree = LopperTree()
        foo = LopperNode(-1, "/foo")
        tree.add(foo)
        tree.sync()
        tree.resolve()

        tree = tree - tree['/foo']

        assert len(tree.extracted_trees()) == 1


class TestOverlayRegistration:
    """Test that overlay_of() registers overlays with parent."""

    def test_overlay_of_registers_with_parent(self):
        """overlay_of() should register overlay in parent's child_trees."""
        base = LopperTree()
        overlay = LopperTree()

        overlay.overlay_of(base)

        assert overlay in base.child_trees()
        assert overlay in base.overlay_trees()

    def test_overlay_of_sets_metadata(self):
        """overlay_of() should set overlay metadata."""
        base = LopperTree()
        overlay = LopperTree()

        overlay.overlay_of(base)

        assert overlay._metadata['type'] == 'overlay'
        assert overlay._metadata['parent'] is base
        assert overlay._metadata['name'] == 'overlay_1'

    def test_overlay_of_with_custom_name(self):
        """overlay_of() should accept custom name."""
        base = LopperTree()
        overlay = LopperTree()

        overlay.overlay_of(base, name='pl_overlay')

        assert overlay._metadata['name'] == 'pl_overlay'

    def test_overlay_external_trees_for_resolution(self):
        """overlay should have base in _external_trees for resolution."""
        base = LopperTree()
        overlay = LopperTree()

        overlay.overlay_of(base)

        assert base in overlay._external_trees
        assert base in overlay._metadata['external_trees']

    def test_overlay_of_exclude_props(self):
        """overlay_of(exclude_props) should remove specified properties."""
        from lopper.tree import LopperProp

        base = LopperTree()
        overlay = LopperTree()

        # Add a node with properties to overlay
        foo = LopperNode(-1, "/foo")
        overlay.add(foo)
        overlay.sync()
        overlay.resolve()

        # Add properties directly to avoid phandle resolution issues
        overlay['/foo'].__props__['address-map'] = LopperProp(
            'address-map', -1, overlay['/foo'], [1, 2, 3]
        )
        overlay['/foo'].__props__['other-prop'] = LopperProp(
            'other-prop', -1, overlay['/foo'], "keep"
        )

        overlay.overlay_of(base, exclude_props=['address-map'])

        # address-map should be removed, other-prop should remain
        assert 'address-map' not in overlay['/foo'].__props__
        assert 'other-prop' in overlay['/foo'].__props__


class TestSubtreesSync:
    """Test LopperSDT.subtrees_sync() method."""

    def test_subtrees_sync_populates_from_child_trees(self, lopper_sdt):
        """subtrees_sync() should copy named child trees to subtrees dict."""
        # Create a child tree with a name
        child = LopperTree()
        child._metadata['name'] = 'test_child'
        child._metadata['type'] = 'extracted'
        lopper_sdt.tree._metadata['child_trees'].append(child)

        lopper_sdt.subtrees_sync()

        assert 'test_child' in lopper_sdt.subtrees
        assert lopper_sdt.subtrees['test_child'] is child

    def test_subtrees_sync_skips_unnamed_children(self, lopper_sdt):
        """subtrees_sync() should skip children without names."""
        child = LopperTree()
        child._metadata['name'] = None
        lopper_sdt.tree._metadata['child_trees'].append(child)

        initial_count = len(lopper_sdt.subtrees)
        lopper_sdt.subtrees_sync()

        assert len(lopper_sdt.subtrees) == initial_count

    def test_subtrees_sync_does_not_overwrite(self, lopper_sdt):
        """subtrees_sync() should not overwrite existing subtrees."""
        # Add a subtree manually
        existing = LopperTree()
        lopper_sdt.subtrees['test_child'] = existing

        # Add a child with same name
        child = LopperTree()
        child._metadata['name'] = 'test_child'
        lopper_sdt.tree._metadata['child_trees'].append(child)

        lopper_sdt.subtrees_sync()

        # Should keep the existing one
        assert lopper_sdt.subtrees['test_child'] is existing


class TestAutoFragments:
    """Test that overlay_of() generates auto-fragments for cross-tree references."""

    def test_overlay_of_generates_auto_fragments(self):
        """overlay_of() should generate fragments for base properties referencing overlay nodes."""
        from lopper.tree import LopperProp

        # Create base tree with a device that references a clock
        base = LopperTree()
        base_root = base['/']
        base_root + LopperProp(name='#clock-cells', value=[1])

        device = LopperNode(-1, "/device")
        device.phandle = 0x10
        device.label = "my_device"
        device + LopperProp(name='clocks', value=[0x20, 0x0])  # References phandle 0x20
        device + LopperProp(name='clock-names', value=["pl_clk"])
        base.add(device)
        base.sync()
        base.resolve()

        # Create overlay tree with the referenced clock node
        overlay = LopperTree()
        pl_clock = LopperNode(-1, "/pl_clock")
        pl_clock.phandle = 0x20  # This is what device references
        pl_clock.label = "pl_clk_0"
        pl_clock + LopperProp(name='#clock-cells', value=[1])
        overlay.add(pl_clock)
        overlay.sync()
        overlay.resolve()

        # Call overlay_of - should auto-generate fragments
        overlay.overlay_of(base)

        # Check that auto_fragments metadata is populated
        assert 'auto_fragments' in overlay._metadata
        assert isinstance(overlay._metadata['auto_fragments'], list)
        assert len(overlay._metadata['auto_fragments']) > 0

    def test_auto_fragments_contains_fragment_nodes(self):
        """auto_fragments metadata should contain the generated fragment nodes."""
        from lopper.tree import LopperProp

        base = LopperTree()
        base_root = base['/']
        base_root + LopperProp(name='#clock-cells', value=[1])

        device = LopperNode(-1, "/device")
        device.phandle = 0x10
        device.label = "my_device"
        device + LopperProp(name='clocks', value=[0x20, 0x0])
        base.add(device)
        base.sync()
        base.resolve()

        overlay = LopperTree()
        pl_clock = LopperNode(-1, "/pl_clock")
        pl_clock.phandle = 0x20
        pl_clock.label = "pl_clk_0"
        pl_clock + LopperProp(name='#clock-cells', value=[1])
        overlay.add(pl_clock)
        overlay.sync()
        overlay.resolve()

        overlay.overlay_of(base)

        # Fragment nodes should be in auto_fragments list
        fragments = overlay._metadata['auto_fragments']
        fragment_names = [f.name for f in fragments]
        assert "&my_device" in fragment_names

    def test_auto_fragments_empty_when_no_refs(self):
        """auto_fragments should be empty when no cross-tree references exist."""
        base = LopperTree()
        overlay = LopperTree()

        overlay.overlay_of(base)

        assert 'auto_fragments' in overlay._metadata
        assert overlay._metadata['auto_fragments'] == []
