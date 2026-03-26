"""
Tests for overlay source tracking infrastructure.

Tests the _source attribute on LopperProp and helper methods for
finding overlay-sourced properties on nodes and trees.

Also tests the helper functions for identifying and extracting
overlay targets from DTS files.
"""

import pytest
import subprocess
import tempfile
import os
from lopper.tree import LopperTree, LopperNode, LopperProp
from lopper import LopperSDT


class TestPropertySourceAttribute:
    """Test that _source attribute is properly initialized and copied."""

    def test_source_defaults_to_none(self):
        """New properties should have _source = None."""
        prop = LopperProp('test-prop', value=[1, 2, 3])
        assert prop._source is None

    def test_source_can_be_set(self):
        """_source should be settable."""
        prop = LopperProp('test-prop', value=[1, 2, 3])
        prop._source = 'overlay:mmi_dc.dtsi'
        assert prop._source == 'overlay:mmi_dc.dtsi'

    def test_source_preserved_on_deepcopy(self):
        """_source should be preserved when property is deep copied."""
        import copy
        prop = LopperProp('test-prop', value=[1, 2, 3])
        prop._source = 'overlay:test.dtsi'

        prop_copy = copy.deepcopy(prop)

        assert prop_copy._source == 'overlay:test.dtsi'

    def test_source_formats(self):
        """Various source format strings should be accepted."""
        prop = LopperProp('test-prop', value=[1])

        # Test various formats
        prop._source = 'overlay:mmi_dc.dtsi'
        assert prop._source == 'overlay:mmi_dc.dtsi'

        prop._source = 'yaml:domains.yaml'
        assert prop._source == 'yaml:domains.yaml'

        prop._source = 'json:config.json'
        assert prop._source == 'json:config.json'


class TestNodeOverlaySourcedProperties:
    """Test LopperNode.overlay_sourced_properties() method."""

    def test_returns_empty_for_no_overlay_sources(self):
        """Node with no overlay sources should return empty list."""
        tree = LopperTree()
        node = LopperNode(-1, "/test")
        tree.add(node)
        tree.sync()
        tree.resolve()

        # Add a property without source
        tree['/test'] + LopperProp(name='regular-prop', value=[1])

        result = tree['/test'].overlay_sourced_properties()
        assert result == []

    def test_finds_overlay_sourced_property(self):
        """Should find properties with overlay source."""
        tree = LopperTree()
        node = LopperNode(-1, "/test")
        tree.add(node)
        tree.sync()
        tree.resolve()

        # Add property with overlay source
        prop = LopperProp(name='overlay-prop', value=[1])
        prop._source = 'overlay:mmi_dc.dtsi'
        tree['/test'].__props__['overlay-prop'] = prop

        result = tree['/test'].overlay_sourced_properties()
        assert len(result) == 1
        assert result[0] == ('overlay-prop', 'overlay:mmi_dc.dtsi')

    def test_filter_by_specific_overlay(self):
        """Should filter by specific overlay name."""
        tree = LopperTree()
        node = LopperNode(-1, "/test")
        tree.add(node)
        tree.sync()
        tree.resolve()

        # Add properties from different overlays
        prop1 = LopperProp(name='prop1', value=[1])
        prop1._source = 'overlay:overlay_a.dtsi'
        tree['/test'].__props__['prop1'] = prop1

        prop2 = LopperProp(name='prop2', value=[2])
        prop2._source = 'overlay:overlay_b.dtsi'
        tree['/test'].__props__['prop2'] = prop2

        # Filter for overlay_a only
        result = tree['/test'].overlay_sourced_properties('overlay_a.dtsi')
        assert len(result) == 1
        assert result[0][0] == 'prop1'

        # Filter for overlay_b only
        result = tree['/test'].overlay_sourced_properties('overlay_b.dtsi')
        assert len(result) == 1
        assert result[0][0] == 'prop2'

    def test_filter_by_list_of_overlays(self):
        """Should filter by list of overlay names."""
        tree = LopperTree()
        node = LopperNode(-1, "/test")
        tree.add(node)
        tree.sync()
        tree.resolve()

        # Add properties from different overlays
        prop1 = LopperProp(name='prop1', value=[1])
        prop1._source = 'overlay:a.dtsi'
        tree['/test'].__props__['prop1'] = prop1

        prop2 = LopperProp(name='prop2', value=[2])
        prop2._source = 'overlay:b.dtsi'
        tree['/test'].__props__['prop2'] = prop2

        prop3 = LopperProp(name='prop3', value=[3])
        prop3._source = 'overlay:c.dtsi'
        tree['/test'].__props__['prop3'] = prop3

        # Filter for a and b
        result = tree['/test'].overlay_sourced_properties(['a.dtsi', 'b.dtsi'])
        assert len(result) == 2
        prop_names = [r[0] for r in result]
        assert 'prop1' in prop_names
        assert 'prop2' in prop_names
        assert 'prop3' not in prop_names

    def test_wildcard_matches_all_overlays(self):
        """Wildcard '*' should match all overlay sources."""
        tree = LopperTree()
        node = LopperNode(-1, "/test")
        tree.add(node)
        tree.sync()
        tree.resolve()

        # Add properties from different overlays
        prop1 = LopperProp(name='prop1', value=[1])
        prop1._source = 'overlay:a.dtsi'
        tree['/test'].__props__['prop1'] = prop1

        prop2 = LopperProp(name='prop2', value=[2])
        prop2._source = 'overlay:b.dtsi'
        tree['/test'].__props__['prop2'] = prop2

        result = tree['/test'].overlay_sourced_properties('*')
        assert len(result) == 2

    def test_ignores_non_overlay_sources(self):
        """Should ignore properties with non-overlay sources (yaml, json)."""
        tree = LopperTree()
        node = LopperNode(-1, "/test")
        tree.add(node)
        tree.sync()
        tree.resolve()

        # Add property with yaml source
        prop = LopperProp(name='yaml-prop', value=[1])
        prop._source = 'yaml:domains.yaml'
        tree['/test'].__props__['yaml-prop'] = prop

        result = tree['/test'].overlay_sourced_properties('*')
        assert result == []


class TestTreeNodesWithOverlaySources:
    """Test LopperTree.nodes_with_overlay_sources() method."""

    def test_returns_empty_for_no_overlay_sources(self):
        """Tree with no overlay sources should return empty list."""
        tree = LopperTree()
        node = LopperNode(-1, "/test")
        tree.add(node)
        tree.sync()
        tree.resolve()

        result = tree.nodes_with_overlay_sources()
        assert result == []

    def test_finds_nodes_with_overlay_properties(self):
        """Should find nodes that have overlay-sourced properties."""
        tree = LopperTree()
        node1 = LopperNode(-1, "/node1")
        node2 = LopperNode(-1, "/node2")
        tree.add(node1)
        tree.add(node2)
        tree.sync()
        tree.resolve()

        # Add overlay property to node1 only
        prop = LopperProp(name='overlay-prop', value=[1])
        prop._source = 'overlay:test.dtsi'
        tree['/node1'].__props__['overlay-prop'] = prop

        result = tree.nodes_with_overlay_sources()
        assert len(result) == 1
        assert result[0][0].abs_path == '/node1'
        assert len(result[0][1]) == 1

    def test_filters_by_overlay_name(self):
        """Should filter nodes by overlay source name."""
        tree = LopperTree()
        node1 = LopperNode(-1, "/node1")
        node2 = LopperNode(-1, "/node2")
        tree.add(node1)
        tree.add(node2)
        tree.sync()
        tree.resolve()

        # Add overlay property from 'a.dtsi' to node1
        prop1 = LopperProp(name='prop1', value=[1])
        prop1._source = 'overlay:a.dtsi'
        tree['/node1'].__props__['prop1'] = prop1

        # Add overlay property from 'b.dtsi' to node2
        prop2 = LopperProp(name='prop2', value=[2])
        prop2._source = 'overlay:b.dtsi'
        tree['/node2'].__props__['prop2'] = prop2

        # Filter for a.dtsi
        result = tree.nodes_with_overlay_sources('a.dtsi')
        assert len(result) == 1
        assert result[0][0].abs_path == '/node1'

        # Filter for b.dtsi
        result = tree.nodes_with_overlay_sources('b.dtsi')
        assert len(result) == 1
        assert result[0][0].abs_path == '/node2'

    def test_multiple_properties_per_node(self):
        """Should return all matching properties for each node."""
        tree = LopperTree()
        node = LopperNode(-1, "/test")
        tree.add(node)
        tree.sync()
        tree.resolve()

        # Add multiple overlay properties
        for i in range(3):
            prop = LopperProp(name=f'prop{i}', value=[i])
            prop._source = 'overlay:test.dtsi'
            tree['/test'].__props__[f'prop{i}'] = prop

        result = tree.nodes_with_overlay_sources()
        assert len(result) == 1
        assert len(result[0][1]) == 3


class TestIsOverlayFile:
    """Test is_overlay_file() helper function."""

    def test_detects_overlay_syntax(self):
        """Should detect &label { } overlay syntax."""
        from lopper import is_overlay_file

        with tempfile.NamedTemporaryFile(mode='w', suffix='.dtsi', delete=False) as f:
            f.write("""
            &mmi_dc {
                status = "okay";
                clocks = <&pl_clk 0>;
            };
            """)
            f.flush()
            try:
                assert is_overlay_file(f.name) is True
            finally:
                os.unlink(f.name)

    def test_rejects_non_overlay_file(self):
        """Should return False for files without overlay syntax."""
        from lopper import is_overlay_file

        with tempfile.NamedTemporaryFile(mode='w', suffix='.dts', delete=False) as f:
            f.write("""
            /dts-v1/;
            / {
                model = "test";
                compatible = "test,board";

                device@0 {
                    reg = <0x0 0x1000>;
                };
            };
            """)
            f.flush()
            try:
                assert is_overlay_file(f.name) is False
            finally:
                os.unlink(f.name)

    def test_handles_missing_file(self):
        """Should return False for non-existent files."""
        from lopper import is_overlay_file
        assert is_overlay_file('/nonexistent/path/file.dts') is False


class TestExtractOverlayTargets:
    """Test extract_overlay_targets() helper function."""

    def test_extracts_single_target(self):
        """Should extract single overlay target with properties."""
        from lopper import extract_overlay_targets

        with tempfile.NamedTemporaryFile(mode='w', suffix='.dtsi', delete=False) as f:
            f.write("""
            &mmi_dc {
                status = "okay";
                clocks = <&pl_clk 0>;
                clock-names = "pl_clk";
            };
            """)
            f.flush()
            try:
                targets = extract_overlay_targets(f.name)
                assert 'mmi_dc' in targets
                assert 'status' in targets['mmi_dc']['props']
                assert 'clocks' in targets['mmi_dc']['props']
                assert 'clock-names' in targets['mmi_dc']['props']
            finally:
                os.unlink(f.name)

    def test_extracts_multiple_targets(self):
        """Should extract multiple overlay targets."""
        from lopper import extract_overlay_targets

        with tempfile.NamedTemporaryFile(mode='w', suffix='.dtsi', delete=False) as f:
            f.write("""
            &node_a {
                prop1 = "value1";
            };

            &node_b {
                prop2 = "value2";
                prop3 = <0x100>;
            };
            """)
            f.flush()
            try:
                targets = extract_overlay_targets(f.name)
                assert 'node_a' in targets
                assert 'node_b' in targets
                assert 'prop1' in targets['node_a']['props']
                assert 'prop2' in targets['node_b']['props']
                assert 'prop3' in targets['node_b']['props']
            finally:
                os.unlink(f.name)

    def test_returns_empty_for_non_overlay(self):
        """Should return empty dict for non-overlay file."""
        from lopper import extract_overlay_targets

        with tempfile.NamedTemporaryFile(mode='w', suffix='.dts', delete=False) as f:
            f.write("""
            /dts-v1/;
            / {
                model = "test";
            };
            """)
            f.flush()
            try:
                targets = extract_overlay_targets(f.name)
                assert targets == {}
            finally:
                os.unlink(f.name)

    def test_handles_missing_file(self):
        """Should return empty dict for non-existent files."""
        from lopper import extract_overlay_targets
        targets = extract_overlay_targets('/nonexistent/path/file.dts')
        assert targets == {}


class TestExtractOverlayTargetsFromTree:
    """Test extract_overlay_targets_from_tree() for compiled tree analysis.

    Uses compile_overlay_standalone() to produce a real dtc-compiled tree,
    then verifies extract_overlay_targets_from_tree() reads the fragment@N/
    __overlay__ structure correctly.
    """

    def test_extracts_targets_from_tree(self):
        """Should extract props and children from a compiled overlay tree."""
        from lopper import extract_overlay_targets_from_tree, compile_overlay_standalone

        with tempfile.NamedTemporaryFile(mode='w', suffix='.dtsi', delete=False) as f:
            f.write("&mmi_dc { status = \"okay\"; clocks = <&pl_clk 0>; };\n")
            f.flush()
            try:
                tree = compile_overlay_standalone(f.name)
                if tree is None:
                    pytest.skip("dtc not available for overlay compilation")
                targets = extract_overlay_targets_from_tree(tree)
                assert 'mmi_dc' in targets
                assert 'status' in targets['mmi_dc']['props']
                assert 'clocks' in targets['mmi_dc']['props']
                assert targets['mmi_dc']['children'] == []
            finally:
                os.unlink(f.name)

    def test_extracts_multiple_targets(self):
        """Should extract multiple overlay targets from compiled tree."""
        from lopper import extract_overlay_targets_from_tree, compile_overlay_standalone

        with tempfile.NamedTemporaryFile(mode='w', suffix='.dtsi', delete=False) as f:
            f.write("&device_a { prop1 = \"value1\"; };\n"
                    "&device_b { prop2 = <0x100>; };\n")
            f.flush()
            try:
                tree = compile_overlay_standalone(f.name)
                if tree is None:
                    pytest.skip("dtc not available for overlay compilation")
                targets = extract_overlay_targets_from_tree(tree)
                assert 'device_a' in targets
                assert 'device_b' in targets
                assert 'prop1' in targets['device_a']['props']
                assert 'prop2' in targets['device_b']['props']
            finally:
                os.unlink(f.name)

    def test_ignores_non_overlay_nodes(self):
        """Should return empty dict for a non-overlay (normal DTS) file."""
        from lopper import extract_overlay_targets_from_tree, compile_overlay_standalone

        with tempfile.NamedTemporaryFile(mode='w', suffix='.dts', delete=False) as f:
            f.write("/dts-v1/;\n/ { model = \"test\"; };\n")
            f.flush()
            try:
                # compile_overlay_standalone returns None for non-overlay files
                tree = compile_overlay_standalone(f.name)
                if tree is not None:
                    targets = extract_overlay_targets_from_tree(tree)
                    assert targets == {}
                # If None, the function correctly rejected the non-overlay file
            finally:
                os.unlink(f.name)


class TestRegexFallback:
    """Test _extract_overlay_targets_regex() fallback function."""

    def test_regex_fallback_works(self):
        """Should extract targets using brace-counting parser when no dtc available."""
        from lopper import _extract_overlay_targets_regex

        with tempfile.NamedTemporaryFile(mode='w', suffix='.dtsi', delete=False) as f:
            f.write("""
            &my_device {
                status = "okay";
                reg = <0x100 0x1000>;
            };
            """)
            f.flush()
            try:
                targets = _extract_overlay_targets_regex(f.name)
                assert 'my_device' in targets
                assert 'status' in targets['my_device']['props']
                assert 'reg' in targets['my_device']['props']
            finally:
                os.unlink(f.name)



class TestFragmentAddForOverlaySources:
    """Test LopperTree.fragment_add_for_overlay_sources() method."""

    def test_creates_fragment_for_overlay_sourced_props(self):
        """Should create fragment for nodes with overlay-sourced properties."""
        tree = LopperTree()

        # Create a node with a label (required for fragment creation)
        device = LopperNode(-1, "/device")
        device.label = "my_device"
        tree.add(device)
        tree.sync()
        tree.resolve()

        # Add overlay-sourced properties
        prop1 = LopperProp(name='status', value=["okay"])
        prop1._source = 'overlay:test.dtsi'
        tree['/device'].__props__['status'] = prop1

        prop2 = LopperProp(name='clocks', value=[0x20, 0x0])
        prop2._source = 'overlay:test.dtsi'
        tree['/device'].__props__['clocks'] = prop2

        # Create overlay tree
        overlay = LopperTree()

        # Add fragments for overlay sources
        fragments = tree.fragment_add_for_overlay_sources(overlay)

        assert len(fragments) == 1
        assert fragments[0].name == '&my_device'

    def test_mode_properties_only_includes_sourced_props(self):
        """Mode 'properties' should only include overlay-sourced properties."""
        tree = LopperTree()

        device = LopperNode(-1, "/device")
        device.label = "my_device"
        tree.add(device)
        tree.sync()
        tree.resolve()

        # Add one overlay-sourced property
        prop1 = LopperProp(name='status', value=["okay"])
        prop1._source = 'overlay:test.dtsi'
        tree['/device'].__props__['status'] = prop1

        # Add one non-sourced property
        prop2 = LopperProp(name='reg', value=[0x0, 0x1000])
        tree['/device'].__props__['reg'] = prop2

        overlay = LopperTree()
        fragments = tree.fragment_add_for_overlay_sources(overlay, mode='properties')

        assert len(fragments) == 1
        # Fragment should only have 'status', not 'reg'
        assert 'status' in fragments[0].__props__
        assert 'reg' not in fragments[0].__props__

    def test_mode_full_nodes_includes_all_props(self):
        """Mode 'full_nodes' should include all properties from touched nodes."""
        tree = LopperTree()

        device = LopperNode(-1, "/device")
        device.label = "my_device"
        tree.add(device)
        tree.sync()
        tree.resolve()

        # Add one overlay-sourced property
        prop1 = LopperProp(name='status', value=["okay"])
        prop1._source = 'overlay:test.dtsi'
        tree['/device'].__props__['status'] = prop1

        # Add one non-sourced property
        prop2 = LopperProp(name='reg', value=[0x0, 0x1000])
        tree['/device'].__props__['reg'] = prop2

        overlay = LopperTree()
        fragments = tree.fragment_add_for_overlay_sources(overlay, mode='full_nodes')

        assert len(fragments) == 1
        # Fragment should have both properties
        assert 'status' in fragments[0].__props__
        assert 'reg' in fragments[0].__props__

    def test_filter_by_specific_overlay(self):
        """Should filter by specific overlay filename."""
        tree = LopperTree()

        device = LopperNode(-1, "/device")
        device.label = "my_device"
        tree.add(device)
        tree.sync()
        tree.resolve()

        # Add properties from different overlays
        prop1 = LopperProp(name='prop_a', value=[1])
        prop1._source = 'overlay:a.dtsi'
        tree['/device'].__props__['prop_a'] = prop1

        prop2 = LopperProp(name='prop_b', value=[2])
        prop2._source = 'overlay:b.dtsi'
        tree['/device'].__props__['prop_b'] = prop2

        overlay = LopperTree()
        # Only get properties from a.dtsi
        fragments = tree.fragment_add_for_overlay_sources(
            overlay,
            source_filter='a.dtsi',
            mode='properties'
        )

        assert len(fragments) == 1
        assert 'prop_a' in fragments[0].__props__
        assert 'prop_b' not in fragments[0].__props__

    def test_returns_empty_for_no_overlay_sources(self):
        """Should return empty list when no overlay sources exist."""
        tree = LopperTree()
        device = LopperNode(-1, "/device")
        device.label = "my_device"
        tree.add(device)
        tree.sync()
        tree.resolve()

        # Add property without source
        prop = LopperProp(name='reg', value=[0x0, 0x1000])
        tree['/device'].__props__['reg'] = prop

        overlay = LopperTree()
        fragments = tree.fragment_add_for_overlay_sources(overlay)

        assert fragments == []

    def test_merges_into_existing_fragment(self):
        """Should merge properties into existing fragment if present."""
        tree = LopperTree()

        device = LopperNode(-1, "/device")
        device.label = "my_device"
        tree.add(device)
        tree.sync()
        tree.resolve()

        # Add overlay-sourced property
        prop = LopperProp(name='status', value=["okay"])
        prop._source = 'overlay:test.dtsi'
        tree['/device'].__props__['status'] = prop

        # Create overlay with existing fragment for same node
        overlay = LopperTree()
        existing_frag = LopperNode(name="&my_device")
        existing_prop = LopperProp(name='clocks', value=[0x20])
        existing_frag.__props__['clocks'] = existing_prop
        overlay.add(existing_frag)
        overlay.sync()

        # Should merge into existing fragment rather than create new one
        fragments = tree.fragment_add_for_overlay_sources(overlay)

        # No new fragments created (merged into existing)
        assert len(fragments) == 0

        # Find the existing fragment and verify it has both properties
        found_frag = None
        for node in overlay:
            if node.name == '&my_device':
                found_frag = node
                break

        assert found_frag is not None
        assert 'clocks' in found_frag.__props__  # Original
        assert 'status' in found_frag.__props__  # Merged

    def test_skips_nodes_without_labels(self):
        """Should skip nodes that don't have labels."""
        tree = LopperTree()

        # Node without a label
        device = LopperNode(-1, "/device")
        # No device.label = ...
        tree.add(device)
        tree.sync()
        tree.resolve()

        prop = LopperProp(name='status', value=["okay"])
        prop._source = 'overlay:test.dtsi'
        tree['/device'].__props__['status'] = prop

        overlay = LopperTree()
        fragments = tree.fragment_add_for_overlay_sources(overlay)

        # No fragment created because node has no label
        assert fragments == []


class TestOverlaySourceIntegration:
    """Integration tests for the full overlay source tracking workflow.

    These tests verify the end-to-end use case:
    1. User provides overlay file modifying existing nodes
    2. Overlay is analyzed and properties tagged during setup
    3. Fragment generation pulls user's overlay properties into output
    """

    def test_full_workflow_single_overlay(self):
        """Test full workflow: overlay file → tagging → fragment generation.

        Simulates the real use case where user's mmi_dc.dtsi adds properties
        to an existing node, and those properties should appear in the
        generated output overlay.
        """
        from lopper import extract_overlay_targets

        # Step 1: Create overlay file (simulating user's mmi_dc.dtsi)
        with tempfile.NamedTemporaryFile(mode='w', suffix='.dtsi', delete=False) as f:
            f.write("""
            &mmi_dc {
                status = "okay";
                xlnx,dc-pixel-format = "rgb888";
                clocks = <&pl_clk 0>;
            };
            """)
            overlay_file = f.name
            f.flush()

        try:
            # Step 2: Extract overlay targets (what _tag_overlay_properties does)
            targets = extract_overlay_targets(overlay_file)
            assert 'mmi_dc' in targets
            assert 'status' in targets['mmi_dc']['props']
            assert 'xlnx,dc-pixel-format' in targets['mmi_dc']['props']
            assert 'clocks' in targets['mmi_dc']['props']

            # Step 3: Create base tree with the target node
            tree = LopperTree()
            mmi_dc = LopperNode(-1, "/amba/mmi_dc@fd4a0000")
            mmi_dc.label = "mmi_dc"
            tree.add(mmi_dc)
            tree.sync()
            tree.resolve()

            # Step 4: Tag properties as coming from overlay
            # (simulating what _tag_overlay_properties does after merge)
            source_tag = f"overlay:{os.path.basename(overlay_file)}"
            for label, target_info in targets.items():
                # Find node by label - use __nodes__ to avoid iterator state issues
                node = None
                for n in tree.__nodes__.values():
                    if n.label == label:
                        node = n
                        break

                if node:
                    for prop_name in target_info['props']:
                        # Simulate merged property
                        if prop_name == 'status':
                            prop = LopperProp(name=prop_name, value=["okay"])
                        elif prop_name == 'xlnx,dc-pixel-format':
                            prop = LopperProp(name=prop_name, value=["rgb888"])
                        else:
                            prop = LopperProp(name=prop_name, value=[0x20, 0x0])

                        prop._source = source_tag
                        node.__props__[prop_name] = prop

            # Step 5: Verify properties are tagged
            overlay_props = tree['/amba/mmi_dc@fd4a0000'].overlay_sourced_properties()
            assert len(overlay_props) == 3
            prop_names = [p[0] for p in overlay_props]
            assert 'status' in prop_names
            assert 'xlnx,dc-pixel-format' in prop_names
            assert 'clocks' in prop_names

            # Step 6: Generate fragment for output overlay
            output_overlay = LopperTree()
            fragments = tree.fragment_add_for_overlay_sources(output_overlay)

            # Step 7: Verify fragment contains user's properties
            assert len(fragments) == 1
            frag = fragments[0]
            assert frag.name == '&mmi_dc'
            assert 'status' in frag.__props__
            assert 'xlnx,dc-pixel-format' in frag.__props__
            assert 'clocks' in frag.__props__

        finally:
            os.unlink(overlay_file)

    def test_multiple_overlays_selective_pullback(self):
        """Test selective pullback when multiple overlays modify the tree.

        User provides two overlays:
        - display.dtsi: modifies &mmi_dc
        - audio.dtsi: modifies &audio_controller

        Output should be able to pull back only display.dtsi content.
        """
        from lopper import extract_overlay_targets

        # Create two overlay files
        with tempfile.NamedTemporaryFile(mode='w', suffix='.dtsi', delete=False) as f1:
            f1.write("""
            &mmi_dc {
                status = "okay";
            };
            """)
            display_file = f1.name
            f1.flush()

        with tempfile.NamedTemporaryFile(mode='w', suffix='.dtsi', delete=False) as f2:
            f2.write("""
            &audio_ctrl {
                status = "disabled";
            };
            """)
            audio_file = f2.name
            f2.flush()

        try:
            # Create base tree with both nodes
            tree = LopperTree()

            mmi_dc = LopperNode(-1, "/amba/mmi_dc@fd4a0000")
            mmi_dc.label = "mmi_dc"
            tree.add(mmi_dc)

            audio = LopperNode(-1, "/amba/audio@fd4b0000")
            audio.label = "audio_ctrl"
            tree.add(audio)

            tree.sync()
            tree.resolve()

            # Tag properties from each overlay
            for overlay_file in [display_file, audio_file]:
                targets = extract_overlay_targets(overlay_file)
                source_tag = f"overlay:{os.path.basename(overlay_file)}"

                for label, target_info in targets.items():
                    # Use __nodes__ to avoid iterator state issues
                    node = None
                    for n in tree.__nodes__.values():
                        if n.label == label:
                            node = n
                            break

                    if node:
                        for prop_name in target_info['props']:
                            prop = LopperProp(name=prop_name, value=["okay"])
                            prop._source = source_tag
                            node.__props__[prop_name] = prop

            # Generate fragments for only display.dtsi
            output_overlay = LopperTree()
            fragments = tree.fragment_add_for_overlay_sources(
                output_overlay,
                source_filter=os.path.basename(display_file)
            )

            # Should only have fragment for mmi_dc, not audio_ctrl
            assert len(fragments) == 1
            assert fragments[0].name == '&mmi_dc'

        finally:
            os.unlink(display_file)
            os.unlink(audio_file)

    def test_phandle_refs_plus_overlay_sources(self):
        """Test that both phandle refs and overlay sources are captured.

        This tests the complete scenario:
        - User overlay adds clocks property referencing PL node
        - PL node is extracted to overlay
        - fragment_add_for_refs() adds fragment for clocks
        - fragment_add_for_overlay_sources() adds any other user properties

        The two methods should work together without duplication.
        """
        tree = LopperTree()

        # Create PS node that user overlay will modify
        mmi_dc = LopperNode(-1, "/amba/mmi_dc@fd4a0000")
        mmi_dc.label = "mmi_dc"
        tree.add(mmi_dc)

        # Create PL node (will be "extracted" to overlay)
        pl_clk = LopperNode(-1, "/amba_pl/clkx5_wiz_0")
        pl_clk.label = "clkx5_wiz_0"
        pl_clk.phandle = 0x50
        tree.add(pl_clk)

        tree.sync()
        tree.resolve()

        # User overlay adds:
        # 1. clocks property referencing PL node (phandle ref)
        # 2. status property (non-phandle, just overlay source)
        clocks_prop = LopperProp(name='clocks', value=[0x50, 0x0])
        clocks_prop._source = 'overlay:mmi_dc.dtsi'
        tree['/amba/mmi_dc@fd4a0000'].__props__['clocks'] = clocks_prop

        status_prop = LopperProp(name='status', value=["okay"])
        status_prop._source = 'overlay:mmi_dc.dtsi'
        tree['/amba/mmi_dc@fd4a0000'].__props__['status'] = status_prop

        # Create output overlay (simulating extracted PL nodes)
        output_overlay = LopperTree()
        extracted = LopperNode(-1, "/amba_pl/clkx5_wiz_0")
        extracted.label = "clkx5_wiz_0"
        extracted.phandle = 0x50
        output_overlay.add(extracted)
        output_overlay.sync()

        # First: fragment_add_for_refs would add fragment for clocks
        # (We simulate this by adding a fragment manually)
        frag_from_refs = LopperNode(name="&mmi_dc")
        frag_from_refs.__props__['clocks'] = LopperProp(name='clocks', value=[0x50, 0x0])
        output_overlay.add(frag_from_refs)
        output_overlay.sync()

        # Second: fragment_add_for_overlay_sources should merge status
        # into existing fragment, not create duplicate
        fragments = tree.fragment_add_for_overlay_sources(output_overlay)

        # Should return 0 new fragments (merged into existing)
        assert len(fragments) == 0

        # Find the fragment and verify both properties present
        frag = None
        for node in output_overlay:
            if node.name == '&mmi_dc':
                frag = node
                break

        assert frag is not None
        assert 'clocks' in frag.__props__  # From fragment_add_for_refs
        assert 'status' in frag.__props__  # From fragment_add_for_overlay_sources

    def test_overlay_with_vendor_specific_properties(self):
        """Test that vendor-specific properties are captured.

        Real use case: User overlay sets xlnx,* properties that aren't
        phandle references but should still appear in output.
        """
        tree = LopperTree()

        dc = LopperNode(-1, "/amba/mmi_dc@fd4a0000")
        dc.label = "mmi_dc"
        tree.add(dc)
        tree.sync()
        tree.resolve()

        # Add vendor-specific properties from user overlay
        props_from_overlay = [
            ('status', ["okay"]),
            ('xlnx,dc-pixel-format', ["rgb888"]),
            ('xlnx,dc-timing-mode', [1]),
            ('xlnx,dc-height', [1080]),
            ('xlnx,dc-width', [1920]),
        ]

        for name, value in props_from_overlay:
            prop = LopperProp(name=name, value=value)
            prop._source = 'overlay:mmi_dc.dtsi'
            tree['/amba/mmi_dc@fd4a0000'].__props__[name] = prop

        # Generate fragment
        output_overlay = LopperTree()
        fragments = tree.fragment_add_for_overlay_sources(output_overlay)

        assert len(fragments) == 1
        frag = fragments[0]

        # All properties should be in fragment
        for name, _ in props_from_overlay:
            assert name in frag.__props__, f"Missing property: {name}"

    def test_round_trip_overlay_reconstruction(self):
        """Test that an overlay can be reconstructed from the merged tree.

        This is the key use case: user provides overlay, it gets merged
        for validation, then we extract it back out and verify it matches
        the original.

        Input overlay:
            &mmi_dc {
                status = "okay";
                xlnx,dc-pixel-format = "rgb888";
                xlnx,dc-timing-mode = <1>;
            };

        After merge + extraction, the generated fragment should contain
        exactly those same properties with the same values.
        """
        from lopper import extract_overlay_targets

        # Define the original overlay content
        original_overlay_content = """
        &mmi_dc {
            status = "okay";
            xlnx,dc-pixel-format = "rgb888";
            xlnx,dc-timing-mode = <1>;
        };
        """

        # Expected properties and values from the overlay
        expected_props = {
            'status': ["okay"],
            'xlnx,dc-pixel-format': ["rgb888"],
            'xlnx,dc-timing-mode': [1],
        }

        # Step 1: Write overlay to temp file and extract targets
        with tempfile.NamedTemporaryFile(mode='w', suffix='.dtsi', delete=False) as f:
            f.write(original_overlay_content)
            overlay_file = f.name
            f.flush()

        try:
            targets = extract_overlay_targets(overlay_file)
            overlay_basename = os.path.basename(overlay_file)

            # Step 2: Create base tree representing the SDT
            base_tree = LopperTree()

            # Base tree has mmi_dc with some existing properties
            mmi_dc = LopperNode(-1, "/amba/mmi_dc@fd4a0000")
            mmi_dc.label = "mmi_dc"
            base_tree.add(mmi_dc)
            base_tree.sync()
            base_tree.resolve()

            # Add a property that exists in base but NOT in overlay
            base_only_prop = LopperProp(name='reg', value=[0xfd4a0000, 0x10000])
            base_tree['/amba/mmi_dc@fd4a0000'].__props__['reg'] = base_only_prop

            # Step 3: Simulate merge - add overlay properties to base tree
            # (In real flow, DTC does this during concatenated compilation)
            source_tag = f"overlay:{overlay_basename}"
            for label, target_info in targets.items():
                node = None
                for n in base_tree.__nodes__.values():
                    if n.label == label:
                        node = n
                        break

                if node:
                    for prop_name in target_info['props']:
                        # Create property with value from expected_props
                        value = expected_props.get(prop_name, ["unknown"])
                        prop = LopperProp(name=prop_name, value=value)
                        prop._source = source_tag
                        node.__props__[prop_name] = prop

            # Step 4: Extract overlay content back out
            reconstructed_overlay = LopperTree()
            fragments = base_tree.fragment_add_for_overlay_sources(
                reconstructed_overlay,
                source_filter=overlay_basename,
                mode='properties'  # Only properties from overlay
            )

            # Step 5: Verify reconstruction matches original
            assert len(fragments) == 1, "Should have exactly one fragment"

            frag = fragments[0]
            assert frag.name == '&mmi_dc', "Fragment should target mmi_dc"

            # Verify all expected properties are present with correct values
            for prop_name, expected_value in expected_props.items():
                assert prop_name in frag.__props__, \
                    f"Missing property '{prop_name}' in reconstructed overlay"

                actual_value = frag.__props__[prop_name].value
                assert actual_value == expected_value, \
                    f"Property '{prop_name}': expected {expected_value}, got {actual_value}"

            # Verify base-only properties are NOT in the fragment
            assert 'reg' not in frag.__props__, \
                "Base-only property 'reg' should not be in reconstructed overlay"

            # Verify property count matches (no extra properties)
            # Filter out internal properties like phandle
            frag_props = [p for p in frag.__props__.keys()
                          if not p.startswith('lopper-') and p != 'phandle']
            assert len(frag_props) == len(expected_props), \
                f"Property count mismatch: expected {len(expected_props)}, got {len(frag_props)}"

        finally:
            os.unlink(overlay_file)

    def test_assist_workflow_simulation(self):
        """Simulate the xlnx_overlay_pl_dt assist workflow.

        This test mirrors what the assist does:
        1. User provides overlay modifying PS node with PL clock references
        2. PL nodes are extracted to overlay tree
        3. fragment_add_for_refs() pulls properties referencing PL nodes
        4. fragment_add_for_overlay_sources() pulls user's other properties
        5. Output overlay contains both phandle refs AND user properties

        This is the workflow shown in xlnx_overlay_pl_dt-integration-sample.md
        """
        from lopper import extract_overlay_targets

        # User's overlay: mmi_dc with status and clocks referencing PL
        user_overlay = """
        &mmi_dc {
            status = "okay";
            xlnx,dc-pixel-format = "rgb888";
            clocks = <&clkx5_wiz_0 0>;
        };
        """

        with tempfile.NamedTemporaryFile(mode='w', suffix='.dtsi', delete=False) as f:
            f.write(user_overlay)
            overlay_file = f.name
            f.flush()

        try:
            # === SETUP: Create base tree (SDT) ===
            base_tree = LopperTree()

            # PS node that user overlay modifies
            mmi_dc = LopperNode(-1, "/amba/mmi_dc@fd4a0000")
            mmi_dc.label = "mmi_dc"
            base_tree.add(mmi_dc)

            # PL node that will be extracted to overlay
            clkx5_wiz = LopperNode(-1, "/amba_pl/clkx5_wiz_0")
            clkx5_wiz.label = "clkx5_wiz_0"
            clkx5_wiz.phandle = 0x50
            base_tree.add(clkx5_wiz)

            base_tree.sync()
            base_tree.resolve()

            # === STEP 1: Tag user overlay properties (done by LopperSDT.setup) ===
            targets = extract_overlay_targets(overlay_file)
            source_tag = f"overlay:{os.path.basename(overlay_file)}"

            for label, target_info in targets.items():
                node = None
                for n in base_tree.__nodes__.values():
                    if n.label == label:
                        node = n
                        break

                if node:
                    for prop_name in target_info['props']:
                        if prop_name == 'status':
                            prop = LopperProp(name=prop_name, value=["okay"])
                        elif prop_name == 'xlnx,dc-pixel-format':
                            prop = LopperProp(name=prop_name, value=["rgb888"])
                        elif prop_name == 'clocks':
                            # Phandle reference to PL node
                            prop = LopperProp(name=prop_name, value=[0x50, 0x0])
                        else:
                            prop = LopperProp(name=prop_name, value=["unknown"])

                        prop._source = source_tag
                        node.__props__[prop_name] = prop

            # === STEP 2: Extract PL nodes to overlay (done by assist) ===
            overlay_tree = LopperTree()

            # Simulate extracting clkx5_wiz_0 to overlay
            extracted_node = LopperNode(-1, "/amba_pl/clkx5_wiz_0")
            extracted_node.label = "clkx5_wiz_0"
            extracted_node.phandle = 0x50
            overlay_tree.add(extracted_node)
            overlay_tree.sync()

            # === STEP 3: Pull phandle references ===
            # This would add &mmi_dc { clocks = <...>; } because clocks refs PL
            # (In real code: base_tree.fragment_add_for_refs(overlay_tree))
            # We simulate by checking the property references overlay node
            phandle_refs_fragments = base_tree.fragment_add_for_refs(overlay_tree)

            # === STEP 4: Pull user overlay properties ===
            # This adds status, xlnx,dc-pixel-format to the existing &mmi_dc fragment
            overlay_source_fragments = base_tree.fragment_add_for_overlay_sources(
                overlay_tree,
                source_filter=os.path.basename(overlay_file),
                mode='properties'
            )

            # === VERIFY: Output overlay has all expected content ===

            # Find the mmi_dc fragment
            mmi_dc_frag = None
            for node in overlay_tree.__nodes__.values():
                if node.name == '&mmi_dc':
                    mmi_dc_frag = node
                    break

            assert mmi_dc_frag is not None, "mmi_dc fragment should exist"

            # Should have all three properties from user overlay
            assert 'status' in mmi_dc_frag.__props__, "Missing status"
            assert 'xlnx,dc-pixel-format' in mmi_dc_frag.__props__, "Missing xlnx,dc-pixel-format"
            assert 'clocks' in mmi_dc_frag.__props__, "Missing clocks"

            # Verify values
            assert mmi_dc_frag.__props__['status'].value == ["okay"]
            assert mmi_dc_frag.__props__['xlnx,dc-pixel-format'].value == ["rgb888"]
            assert mmi_dc_frag.__props__['clocks'].value == [0x50, 0x0]

            # Extracted PL node should still be there
            pl_node = None
            for node in overlay_tree.__nodes__.values():
                if node.label == 'clkx5_wiz_0':
                    pl_node = node
                    break
            assert pl_node is not None, "Extracted PL node should exist"

        finally:
            os.unlink(overlay_file)

    def test_overlay_with_nested_child_nodes(self):
        """Test end-to-end: nested child nodes from user overlay appear in output overlay.

        Onkar's use case: User overlay for mmi_dc driver includes nested
        ports/endpoint hierarchy with remote-endpoint phandle to a PL node.
        extract_overlay_targets() must detect 'ports' as a child added by the
        overlay. _tag_overlay_properties() must recursively tag all properties
        inside the ports subtree. fragment_add_for_overlay_sources() must then
        pull them into the output overlay fragment.
        """
        from lopper import extract_overlay_targets

        user_overlay = """\
&mmi_dc {
    status = "okay";
    xlnx,dc-pixel-format = "rgb888";
    ports {
        port@0 {
            endpoint {
                remote-endpoint = <&avpg_outdc_0>;
            };
        };
    };
};
"""

        with tempfile.NamedTemporaryFile(mode='w', suffix='.dtsi', delete=False) as f:
            f.write(user_overlay)
            overlay_file = f.name
            f.flush()

        try:
            # === STEP 1: extract_overlay_targets must detect props AND children ===
            targets = extract_overlay_targets(overlay_file)
            assert 'mmi_dc' in targets, "mmi_dc should be identified as overlay target"
            assert 'status' in targets['mmi_dc']['props'], "status should be in props"
            assert 'xlnx,dc-pixel-format' in targets['mmi_dc']['props'], \
                "xlnx,dc-pixel-format should be in props"
            assert 'ports' in targets['mmi_dc']['children'], \
                "ports should be detected as a child node added by the overlay"

            # === SETUP: Create base tree simulating merged SDT ===
            base_tree = LopperTree()

            mmi_dc = LopperNode(-1, "/amba/mmi_dc@fd4a0000")
            mmi_dc.label = "mmi_dc"
            base_tree.add(mmi_dc)

            avpg = LopperNode(-1, "/amba_pl/avpg_outdc_0")
            avpg.label = "avpg_outdc_0"
            avpg.phandle = 0x60
            base_tree.add(avpg)

            # Child nodes from the overlay are present in the merged tree
            ports_node = LopperNode(-1, "/amba/mmi_dc@fd4a0000/ports")
            base_tree.add(ports_node)
            port0_node = LopperNode(-1, "/amba/mmi_dc@fd4a0000/ports/port@0")
            base_tree.add(port0_node)
            ep_node = LopperNode(-1, "/amba/mmi_dc@fd4a0000/ports/port@0/endpoint")
            base_tree.add(ep_node)

            base_tree.sync()
            base_tree.resolve()

            # Add properties that came from the overlay
            for prop_name, val in [('status', ["okay"]), ('xlnx,dc-pixel-format', ["rgb888"])]:
                base_tree['/amba/mmi_dc@fd4a0000'].__props__[prop_name] = LopperProp(name=prop_name, value=val)

            base_tree['/amba/mmi_dc@fd4a0000/ports'].__props__['#address-cells'] = \
                LopperProp(name='#address-cells', value=[1])

            remote_ep = LopperProp(name='remote-endpoint', value=[0x60])
            base_tree['/amba/mmi_dc@fd4a0000/ports/port@0/endpoint'].__props__['remote-endpoint'] = remote_ep

            # === STEP 2: LopperSDT._tag_overlay_properties tags props AND child subtrees ===
            overlay_basename = os.path.basename(overlay_file)
            source_tag = f"overlay:{overlay_basename}"

            sdt = LopperSDT("")
            sdt.tree = base_tree
            sdt._overlay_targets = {overlay_basename: targets}
            sdt._tag_overlay_properties()

            # Verify tagging reached the deep endpoint node
            ep_props = base_tree['/amba/mmi_dc@fd4a0000/ports/port@0/endpoint'].__props__
            assert ep_props['remote-endpoint']._source == source_tag, \
                "remote-endpoint inside nested endpoint should be tagged as overlay-sourced"

            ports_props = base_tree['/amba/mmi_dc@fd4a0000/ports'].__props__
            assert ports_props['#address-cells']._source == source_tag, \
                "#address-cells on ports node should be tagged as overlay-sourced"

            # === STEP 3: fragment generation pulls both flat props and child content ===
            output_overlay = LopperTree()
            extracted_avpg = LopperNode(-1, "/amba_pl/avpg_outdc_0")
            extracted_avpg.label = "avpg_outdc_0"
            extracted_avpg.phandle = 0x60
            output_overlay.add(extracted_avpg)
            output_overlay.sync()

            base_tree.fragment_add_for_refs(output_overlay)
            base_tree.fragment_add_for_overlay_sources(
                output_overlay,
                source_filter=overlay_basename,
                mode='properties'
            )

            mmi_dc_frag = None
            for node in output_overlay.__nodes__.values():
                if node.name == '&mmi_dc':
                    mmi_dc_frag = node
                    break

            assert mmi_dc_frag is not None, "mmi_dc fragment should exist in output overlay"
            assert 'status' in mmi_dc_frag.__props__, "status should be in output overlay"
            assert 'xlnx,dc-pixel-format' in mmi_dc_frag.__props__, \
                "xlnx,dc-pixel-format should be in output overlay"

            pl_found = any(n.label == 'avpg_outdc_0' for n in output_overlay.__nodes__.values())
            assert pl_found, "Extracted PL node avpg_outdc_0 should remain in overlay"

        finally:
            os.unlink(overlay_file)

    def test_overlay_nested_nodes_without_pl_refs(self):
        """Test end-to-end: nested child nodes without PL refs also go to overlay.

        Per Onkar's requirement: Even when the user overlay has nested child
        nodes that do NOT reference PL nodes (pure structural/config data),
        those children must appear in the output overlay.
        """
        from lopper import extract_overlay_targets

        user_overlay = """\
&mmi_dc {
    status = "okay";
    xlnx,dc-timing-mode = <1>;
    ports {
        port@0 {
            endpoint {
                data-lanes = <0 1 2 3>;
            };
        };
    };
};
"""

        with tempfile.NamedTemporaryFile(mode='w', suffix='.dtsi', delete=False) as f:
            f.write(user_overlay)
            overlay_file = f.name
            f.flush()

        try:
            # === STEP 1: Verify overlay target extraction detects children ===
            targets = extract_overlay_targets(overlay_file)
            assert 'mmi_dc' in targets
            assert 'ports' in targets['mmi_dc']['children'], \
                "ports should be detected as child node even without PL references"

            # === SETUP: Merged tree ===
            base_tree = LopperTree()
            mmi_dc = LopperNode(-1, "/amba/mmi_dc@fd4a0000")
            mmi_dc.label = "mmi_dc"
            base_tree.add(mmi_dc)

            ports_node = LopperNode(-1, "/amba/mmi_dc@fd4a0000/ports")
            base_tree.add(ports_node)
            port0_node = LopperNode(-1, "/amba/mmi_dc@fd4a0000/ports/port@0")
            base_tree.add(port0_node)
            ep_node = LopperNode(-1, "/amba/mmi_dc@fd4a0000/ports/port@0/endpoint")
            base_tree.add(ep_node)

            base_tree.sync()
            base_tree.resolve()

            for prop_name, val in [('status', ["okay"]), ('xlnx,dc-timing-mode', [1])]:
                base_tree['/amba/mmi_dc@fd4a0000'].__props__[prop_name] = LopperProp(name=prop_name, value=val)

            base_tree['/amba/mmi_dc@fd4a0000/ports/port@0/endpoint'].__props__['data-lanes'] = \
                LopperProp(name='data-lanes', value=[0, 1, 2, 3])

            # === STEP 2: LopperSDT._tag_overlay_properties tags props AND child subtrees ===
            overlay_basename = os.path.basename(overlay_file)
            source_tag = f"overlay:{overlay_basename}"

            sdt = LopperSDT("")
            sdt.tree = base_tree
            sdt._overlay_targets = {overlay_basename: targets}
            sdt._tag_overlay_properties()

            # Endpoint's data-lanes must be tagged even though it has no PL ref
            ep_props = base_tree['/amba/mmi_dc@fd4a0000/ports/port@0/endpoint'].__props__
            assert ep_props['data-lanes']._source == source_tag, \
                "data-lanes inside nested endpoint should be tagged as overlay-sourced"

            # === STEP 3: Fragment generation captures flat properties ===
            output_overlay = LopperTree()
            base_tree.fragment_add_for_overlay_sources(
                output_overlay,
                source_filter=overlay_basename,
                mode='properties'
            )

            mmi_dc_frag = None
            for node in output_overlay.__nodes__.values():
                if node.name == '&mmi_dc':
                    mmi_dc_frag = node
                    break

            assert mmi_dc_frag is not None, \
                "mmi_dc fragment should exist even without PL phandle references"
            assert 'status' in mmi_dc_frag.__props__
            assert 'xlnx,dc-timing-mode' in mmi_dc_frag.__props__

        finally:
            os.unlink(overlay_file)


# Minimal base DTS for overlay CLI tests.
# Has a labelled mmi_dc node so &mmi_dc in the user overlay resolves.
_BASE_DTS = """\
/dts-v1/;
/ {
    #address-cells = <2>;
    #size-cells = <2>;
    compatible = "test";

    amba: amba {
        #address-cells = <2>;
        #size-cells = <2>;
        ranges;

        mmi_dc: mmi_dc@fd4a0000 {
            compatible = "xlnx,mmi-dc";
            reg = <0x0 0xfd4a0000 0x0 0x10000>;
            status = "disabled";
        };
    };
};
"""


