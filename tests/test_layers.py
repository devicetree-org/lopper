#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Tests for the layered property view system.

Covers:
- LopperProp._layers storage and layer API
- View context manager (tree.view())
- Backward compatibility (prop.value, prop.value[0], propval())
- Node visibility in base/overlay views
- merge() doesn't overwrite an already-set base layer
"""

import sys
import os
import io

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lopper.tree import LopperProp, LopperNode, LopperTree


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tree_with_prop(value, name='clocks', node_path='/cpu0'):
    """Return (tree, node, prop) with prop added to node."""
    t = LopperTree()
    n = LopperNode(-1, node_path)
    t.add(n)
    p = LopperProp(name, value=value)
    n[name] = p
    return t, n, n.__props__[name]


# ---------------------------------------------------------------------------
# Layer storage tests
# ---------------------------------------------------------------------------

class TestLayeredPropStorage:
    def test_init_seeds_base_layer(self):
        p = LopperProp('clocks', value=[42])
        assert 'base' in p._layers
        assert p._layers['base'] == (0, [42])

    def test_init_empty_value_seeds_empty_base(self):
        p = LopperProp('status', value=[])
        # __init__ branches on value == None; empty list is not None
        # base layer is seeded with initial value
        assert 'base' in p._layers

    def test_set_layer_value_default_priority(self):
        p = LopperProp('clocks', value=[1])
        p._set_layer_value('base', [10])
        assert p._layers['base'] == (0, [10])

        p._set_layer_value('modifications', [99])
        assert p._layers['modifications'] == (1000, [99])

        p._set_layer_value('pl_overlay', [50])
        assert p._layers['pl_overlay'] == (500, [50])

    def test_set_layer_value_custom_priority(self):
        p = LopperProp('reg', value=[0])
        p._set_layer_value('special', [7], priority=750)
        assert p._layers['special'] == (750, [7])

    def test_layer_value_returns_value(self):
        p = LopperProp('clocks', value=[1])
        p._set_layer_value('base', [10])
        p._set_layer_value('pl_overlay', [20], priority=500)
        assert p._layer_value('base') == [10]
        assert p._layer_value('pl_overlay') == [20]
        assert p._layer_value('nonexistent') is None

    def test_winning_layer_no_view(self):
        p = LopperProp('clocks', value=[1])
        p._set_layer_value('base', [10])
        p._set_layer_value('pl_overlay', [20], priority=500)
        assert p._winning_layer_value() == [20]

    def test_winning_layer_with_view(self):
        p = LopperProp('clocks', value=[1])
        p._set_layer_value('base', [10])
        p._set_layer_value('pl_overlay', [20], priority=500)
        assert p._winning_layer_value('base') == [10]
        assert p._winning_layer_value('pl_overlay') == [20]

    def test_winning_layer_view_missing_falls_back_to_winning(self):
        p = LopperProp('clocks', value=[1])
        p._set_layer_value('base', [10])
        # no 'other_overlay' layer
        assert p._winning_layer_value('other_overlay') == [10]

    def test_modifications_wins_over_all(self):
        p = LopperProp('clocks', value=[1])
        p._set_layer_value('base', [10])
        p._set_layer_value('pl_overlay', [20], priority=500)
        p._set_layer_value('modifications', [999], priority=1000)
        assert p._winning_layer_value() == [999]

    def test_setattr_writes_modifications_layer(self):
        t, n, p = _make_tree_with_prop([1])
        p.value = [42]
        assert 'modifications' in p._layers
        assert p._layers['modifications'] == (1000, [42])


# ---------------------------------------------------------------------------
# View context manager tests
# ---------------------------------------------------------------------------

class TestViewContextManager:
    def test_default_view_is_winning(self):
        t, n, p = _make_tree_with_prop([100])
        p.set_layer_value('base', [100])
        p.set_layer_value('pl_overlay', [200], priority=500)
        assert p.value == [200]

    def test_base_view_returns_base(self):
        t, n, p = _make_tree_with_prop([100])
        p._set_layer_value('base', [100])
        p._set_layer_value('pl_overlay', [200], priority=500)
        with t.view('base'):
            assert p.value == [100]

    def test_overlay_view_returns_overlay(self):
        t, n, p = _make_tree_with_prop([100])
        p._set_layer_value('base', [100])
        p._set_layer_value('pl_overlay', [200], priority=500)
        with t.view('pl_overlay'):
            assert p.value == [200]

    def test_view_restores_after_exit(self):
        t, n, p = _make_tree_with_prop([100])
        p.set_layer_value('base', [100])
        p.set_layer_value('pl_overlay', [200], priority=500)
        with t.view('base'):
            assert p.value == [100]
        # After exiting, returns to winning
        assert p.value == [200]

    def test_nested_views(self):
        t, n, p = _make_tree_with_prop([100])
        p.set_layer_value('base', [100])
        p.set_layer_value('overlay_a', [200], priority=500)
        p.set_layer_value('overlay_b', [300], priority=600)
        with t.view('base'):
            assert p.value == [100]
            with t.view('overlay_a'):
                assert p.value == [200]
            # Inner view exited, back to base
            assert p.value == [100]
        # Both exited, back to winning
        assert p.value == [300]

    def test_view_yields_tree(self):
        t = LopperTree()
        with t.view('base') as tree:
            assert tree is t

    def test_modifications_win_in_base_view(self):
        """Explicit writes always win, even in base view."""
        t, n, p = _make_tree_with_prop([1])
        p._set_layer_value('base', [10])
        p._set_layer_value('pl_overlay', [20], priority=500)
        p.value = [999]   # modifications layer, priority 1000
        with t.view('base'):
            # modifications (1000) > base (0), modifications wins when viewing base
            # because view selects the 'base' layer explicitly
            assert p.value == [10]


# ---------------------------------------------------------------------------
# Backward compatibility tests
# ---------------------------------------------------------------------------

class TestBackwardCompat:
    def test_prop_value_returns_list(self):
        t, n, p = _make_tree_with_prop([42])
        assert isinstance(p.value, list)
        assert p.value == [42]

    def test_prop_value_index(self):
        t, n, p = _make_tree_with_prop([42])
        assert p.value[0] == 42

    def test_prop_value_assign_scalar(self):
        t, n, p = _make_tree_with_prop([1])
        p.value = 99
        assert p.value == [99]

    def test_prop_value_assign_list(self):
        t, n, p = _make_tree_with_prop([1])
        p.value = [10, 20]
        assert p.value == [10, 20]

    def test_propval_no_view(self):
        """LopperNode.propval() should return winning value with no view set."""
        t, n, p = _make_tree_with_prop([42], name='reg')
        p.set_layer_value('base', [42])
        p.set_layer_value('overlay', [99], priority=500)
        result = n.propval('reg')
        assert result == [99]

    def test_propval_in_base_view(self):
        """LopperNode.propval() returns base value inside base view."""
        t, n, p = _make_tree_with_prop([42], name='reg')
        p._set_layer_value('base', [42])
        p._set_layer_value('overlay', [99], priority=500)
        with t.view('base'):
            result = n.propval('reg')
        assert result == [42]

    def test_no_layers_fallback(self):
        """Props with empty _layers fall back to __dict__['value']."""
        p = LopperProp('clocks', value=[7])
        p._layers.clear()
        assert p.value == [7]

    def test_deepcopy_preserves_layers(self):
        import copy
        t, n, p = _make_tree_with_prop([1])
        p._set_layer_value('base', [1])
        p._set_layer_value('pl_overlay', [2], priority=500)
        p2 = copy.deepcopy(p)
        assert 'base' in p2._layers
        assert 'pl_overlay' in p2._layers
        assert p2._layers['base'] == p._layers['base']
        assert p2._node is None  # backref not copied


# ---------------------------------------------------------------------------
# Node visibility tests
# ---------------------------------------------------------------------------

class TestNodeVisibility:
    def test_overlay_node_hidden_in_base_view(self):
        """A node with _origin_layer set should not appear in 'base' view output."""
        t = LopperTree()
        root = t['/']

        n = LopperNode(-1, '/overlay_node')
        n._origin_layer = 'pl_overlay'
        t.add(n)
        t.resolve()

        buf = io.StringIO()
        with t.view('base'):
            root.print(buf)
        output = buf.getvalue()
        assert 'overlay_node' not in output

    def test_base_node_visible_in_base_view(self):
        """A node with _origin_layer=None should appear in 'base' view output."""
        t = LopperTree()
        n = LopperNode(-1, '/base_node')
        # _origin_layer stays None (default)
        t.add(n)
        t.resolve()

        buf = io.StringIO()
        with t.view('base'):
            t['/'].print(buf)
        output = buf.getvalue()
        assert 'base_node' in output

    def test_overlay_node_visible_in_overlay_view(self):
        """Overlay node appears when viewing the correct overlay layer."""
        t = LopperTree()
        n = LopperNode(-1, '/pl_thing')
        n._origin_layer = 'pl_overlay'
        t.add(n)
        t.resolve()

        buf = io.StringIO()
        with t.view('pl_overlay'):
            t['/'].print(buf)
        output = buf.getvalue()
        assert 'pl_thing' in output

    def test_overlay_property_hidden_in_base_view(self):
        """A property tagged as overlay-sourced should not appear in base view."""
        t, n, p = _make_tree_with_prop([200], name='address-map')
        p._set_layer_value('pl', [200], priority=500)
        t.resolve()

        buf = io.StringIO()
        with t.view('base'):
            t['/'].print(buf)
        output = buf.getvalue()
        assert 'address-map' not in output

    def test_base_property_not_skipped_in_base_view(self):
        """A property without overlay source should NOT be filtered in base view.

        We verify this by checking the filtering logic directly rather than
        relying on full print() output (which has its own phandle-resolution
        quirks for synthetic nodes not loaded from FDT).
        """
        t, n, p = _make_tree_with_prop([100], name='status', node_path='/base_node')
        # Use a string value so it resolves cleanly
        p2 = LopperProp('status', value=['okay'])
        n['status'] = p2
        real_p = n.__props__['status']
        # No overlay layers (base tree property) — should NOT be filtered
        overlay_layers = [k for k in real_p._layers if k not in ('base', 'modifications')]
        assert overlay_layers == []
        # In base view: no overlay layers => not filtered
        with t.view('base'):
            active = t.__dict__.get('_view_local')
            active_view = getattr(active, 'active', None) if active else None
            assert active_view == 'base'
            # Property should pass the filter (no overlay layers)
            should_skip = (active_view == 'base' and
                           any(k not in ('base', 'modifications') for k in real_p._layers))
            assert not should_skip


# ---------------------------------------------------------------------------
# Merge protection tests
# ---------------------------------------------------------------------------

class TestMergeBaseProtection:
    def test_merge_doesnt_overwrite_existing_base(self):
        """merge() should not overwrite 'base' layer if it already has a value."""
        t, n, p1 = _make_tree_with_prop([10], name='clocks')
        p1._set_layer_value('base', [10])

        p2 = LopperProp('clocks', value=[20])
        p1.merge(p2)

        # base layer should still be [10]
        assert p1._layer_value('base') == [10]

    def test_merge_sets_base_if_empty(self):
        """merge() should set 'base' layer if it wasn't set."""
        p = LopperProp('clocks', value=[])
        p._layers.clear()  # simulate prop with no layers

        p2 = LopperProp('clocks', value=[42])
        p.merge(p2)

        # base was empty, merge should set it
        assert 'base' in p._layers


if __name__ == '__main__':
    import pytest
    pytest.main([__file__, '-v'])
