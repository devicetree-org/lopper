"""
Tests for LopperTree.extract_nodes().

Verifies that nodes introduced by an overlay (tracked via _origin_layer)
are correctly moved from the base tree into the overlay tree, leaving
property-only modifications behind for fragment_add_for_overlay_sources().
"""

import pytest
import copy
from lopper.tree import LopperTree, LopperNode, LopperProp


def _make_tree_with_overlay_nodes():
    """
    Build a base tree that looks like the result of applying an overlay:

    / {
        cpus {                     <- existing node, no overlay origin
            cpu@0 { ... };
        };
        amba_pl {                  <- entire node introduced by overlay 'pl'
            uart0: uart@ff000000 { ... };
        };
    };

    Also tag cpus/cpu@0 with a modified 'status' property from the overlay.
    """
    tree = LopperTree()

    cpus = LopperNode(-1, "/cpus")
    cpus.label = "cpus"
    tree.add(cpus)

    cpu0 = LopperNode(-1, "/cpus/cpu@0")
    cpu0.label = "cpu0"
    tree.add(cpu0)

    # Property on existing node modified by overlay
    status = LopperProp(name='status', value=["okay"])
    status._set_layer_value('pl', ["okay"], priority=500)
    tree['/cpus/cpu@0'].__props__['status'] = status

    # Whole node introduced by overlay
    amba_pl = LopperNode(-1, "/amba_pl")
    amba_pl.label = "amba_pl"
    amba_pl._origin_layer = 'pl'
    tree.add(amba_pl)

    uart = LopperNode(-1, "/amba_pl/uart@ff000000")
    uart.label = "uart0"
    uart._origin_layer = 'pl'
    tree.add(uart)

    reg = LopperProp(name='reg', value=[0xff000000, 0x1000])
    reg._set_layer_value('pl', [0xff000000, 0x1000], priority=500)
    tree['/amba_pl/uart@ff000000'].__props__['reg'] = reg

    tree.sync()
    tree.resolve()
    return tree


class TestExtractOverlayNodes:

    def test_introduced_node_moves_to_overlay_tree(self):
        """Overlay-introduced nodes should be moved into overlay_tree."""
        base = _make_tree_with_overlay_nodes()
        overlay = LopperTree()

        moved = base.extract_nodes(overlay, layer_filter='pl')

        assert len(moved) == 1
        # The top-level introduced node amba_pl was moved
        abs_paths = [n.abs_path for n in moved]
        assert '/amba_pl' in abs_paths

    def test_introduced_node_removed_from_base(self):
        """After extraction the node must not remain in the base tree."""
        base = _make_tree_with_overlay_nodes()
        overlay = LopperTree()

        base.extract_nodes(overlay, layer_filter='pl')

        assert '/amba_pl' not in base.__nodes__

    def test_child_of_introduced_node_not_double_moved(self):
        """Children of a moved node should not appear as separate top-level moves."""
        base = _make_tree_with_overlay_nodes()
        overlay = LopperTree()

        moved = base.extract_nodes(overlay, layer_filter='pl')

        # Only one top-level node (amba_pl), not also its child uart@ff000000
        assert len(moved) == 1

    def test_existing_node_not_moved(self):
        """Nodes without _origin_layer should stay in base tree."""
        base = _make_tree_with_overlay_nodes()
        overlay = LopperTree()

        base.extract_nodes(overlay, layer_filter='pl')

        assert '/cpus' in base.__nodes__
        assert '/cpus/cpu@0' in base.__nodes__

    def test_wildcard_filter_matches_any_origin_layer(self):
        """layer_filter='*' should match all overlay-introduced nodes."""
        base = _make_tree_with_overlay_nodes()
        overlay = LopperTree()

        moved = base.extract_nodes(overlay, layer_filter='*')

        assert len(moved) == 1
        assert '/amba_pl' not in base.__nodes__

    def test_non_matching_filter_moves_nothing(self):
        """A filter that matches no layer should move nothing."""
        base = _make_tree_with_overlay_nodes()
        overlay = LopperTree()

        moved = base.extract_nodes(overlay, layer_filter='other_overlay')

        assert moved == []
        assert '/amba_pl' in base.__nodes__

    def test_property_modified_nodes_untouched(self):
        """Nodes with overlay-sourced properties but no _origin_layer stay in base."""
        base = _make_tree_with_overlay_nodes()
        overlay = LopperTree()

        base.extract_nodes(overlay, layer_filter='pl')

        # cpu@0 has a 'status' property from the 'pl' layer but _origin_layer is None
        assert '/cpus/cpu@0' in base.__nodes__

    def test_round_trip_combined_with_fragment_add(self):
        """
        Full round-trip: extract_nodes + fragment_add_for_overlay_sources
        gives overlay tree containing both whole nodes and property fragments.
        """
        base = _make_tree_with_overlay_nodes()
        overlay = LopperTree()

        # Step 1: move whole overlay-introduced nodes
        moved = base.extract_nodes(overlay, layer_filter='pl')
        assert len(moved) == 1

        # Step 2: add fragments for overlay-modified properties on existing nodes
        fragments = base.fragment_add_for_overlay_sources(overlay, source_filter='pl')
        # cpu@0 has status modified by pl layer → should produce a fragment
        assert len(fragments) >= 1

        # Verify the overlay tree has the extracted node
        assert any(n.abs_path == '/amba_pl' for n in overlay.__nodes__.values()
                   if hasattr(n, 'abs_path'))
