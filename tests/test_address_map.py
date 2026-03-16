"""
Tests for address-map parsing and accessibility API.

Tests the LopperAddressMapEntry dataclass, parse_address_map() utility function,
and the tree-level accessible_by() method.
"""

import pytest
from lopper import Lopper, LopperSDT
from lopper.tree import LopperTree, LopperNode, LopperProp
from lopper.assists.lopper_lib import (
    LopperAddressMapEntry,
    parse_address_map,
    get_accessible_phandles,
    find_address_in_map,
)


class TestLopperAddressMapEntry:
    """Tests for the LopperAddressMapEntry dataclass."""

    def test_entry_creation(self):
        """Test creating an address-map entry."""
        entry = LopperAddressMapEntry(
            child_addr=0xf0000000,
            phandle=42,
            parent_addr=0xf0000000,
            size=0x10000000
        )
        assert entry.child_addr == 0xf0000000
        assert entry.phandle == 42
        assert entry.parent_addr == 0xf0000000
        assert entry.size == 0x10000000

    def test_contains_address_inside(self):
        """Test contains_address returns True for addresses inside the range."""
        entry = LopperAddressMapEntry(
            child_addr=0x1000,
            phandle=1,
            parent_addr=0x1000,
            size=0x1000
        )
        # Start of range
        assert entry.contains_address(0x1000) is True
        # Middle of range
        assert entry.contains_address(0x1500) is True
        # Just before end
        assert entry.contains_address(0x1FFF) is True

    def test_contains_address_outside(self):
        """Test contains_address returns False for addresses outside the range."""
        entry = LopperAddressMapEntry(
            child_addr=0x1000,
            phandle=1,
            parent_addr=0x1000,
            size=0x1000
        )
        # Before range
        assert entry.contains_address(0x0FFF) is False
        # At end (exclusive)
        assert entry.contains_address(0x2000) is False
        # Well after
        assert entry.contains_address(0x5000) is False

    def test_contains_address_64bit(self):
        """Test contains_address works with 64-bit addresses."""
        entry = LopperAddressMapEntry(
            child_addr=0x800000000,
            phandle=1,
            parent_addr=0x800000000,
            size=0x80000000
        )
        assert entry.contains_address(0x800000000) is True
        assert entry.contains_address(0x850000000) is True
        assert entry.contains_address(0x7FFFFFFFF) is False

    def test_repr(self):
        """Test the string representation."""
        entry = LopperAddressMapEntry(
            child_addr=0xf0000000,
            phandle=42,
            parent_addr=0xf0000000,
            size=0x10000000
        )
        repr_str = repr(entry)
        assert "LopperAddressMapEntry" in repr_str
        assert "0xf0000000" in repr_str
        assert "phandle=42" in repr_str


class TestParseAddressMap:
    """Tests for the parse_address_map function."""

    def test_parse_empty_map(self):
        """Test parsing an empty address-map."""
        entries = parse_address_map([], 2, 2)
        assert entries == []

    def test_parse_empty_list_marker(self):
        """Test parsing address-map with empty list marker."""
        entries = parse_address_map([''], 2, 2)
        assert entries == []

    def test_parse_single_entry_32bit(self):
        """Test parsing a single entry with 32-bit cells (na=1, ns=1)."""
        # Format: child_addr(1) + phandle(1) + parent_addr(1) + size(1)
        address_map = [
            0xf0000000,  # child_addr
            42,          # phandle
            0xf0000000,  # parent_addr
            0x10000000,  # size
        ]
        entries = parse_address_map(address_map, address_cells=1, size_cells=1)

        assert len(entries) == 1
        assert entries[0].child_addr == 0xf0000000
        assert entries[0].phandle == 42
        assert entries[0].parent_addr == 0xf0000000
        assert entries[0].size == 0x10000000

    def test_parse_single_entry_64bit(self):
        """Test parsing a single entry with 64-bit cells (na=2, ns=2)."""
        # Format: child_addr(2) + phandle(1) + parent_addr(2) + size(2)
        address_map = [
            0x0, 0xf0000000,  # child_addr (64-bit: high, low)
            42,               # phandle
            0x0, 0xf0000000,  # parent_addr (64-bit)
            0x0, 0x10000000,  # size (64-bit)
        ]
        entries = parse_address_map(address_map, address_cells=2, size_cells=2)

        assert len(entries) == 1
        assert entries[0].child_addr == 0xf0000000
        assert entries[0].phandle == 42
        assert entries[0].parent_addr == 0xf0000000
        assert entries[0].size == 0x10000000

    def test_parse_multiple_entries_32bit(self):
        """Test parsing multiple entries with 32-bit cells."""
        # Three entries
        address_map = [
            0xf0000000, 10, 0xf0000000, 0x10000000,  # entry 1
            0xf9000000, 20, 0xf9000000, 0x80000,     # entry 2
            0xff300000, 30, 0xff300000, 0x10000,     # entry 3
        ]
        entries = parse_address_map(address_map, address_cells=1, size_cells=1)

        assert len(entries) == 3

        assert entries[0].child_addr == 0xf0000000
        assert entries[0].phandle == 10
        assert entries[0].size == 0x10000000

        assert entries[1].child_addr == 0xf9000000
        assert entries[1].phandle == 20
        assert entries[1].size == 0x80000

        assert entries[2].child_addr == 0xff300000
        assert entries[2].phandle == 30
        assert entries[2].size == 0x10000

    def test_parse_multiple_entries_64bit(self):
        """Test parsing multiple entries with 64-bit cells."""
        # Two entries with 64-bit addressing
        address_map = [
            0x0, 0xf0000000, 42, 0x0, 0xf0000000, 0x0, 0x10000000,  # entry 1
            0x8, 0x00000000, 43, 0x8, 0x00000000, 0x0, 0x80000000,  # entry 2 (high addr)
        ]
        entries = parse_address_map(address_map, address_cells=2, size_cells=2)

        assert len(entries) == 2

        assert entries[0].child_addr == 0xf0000000
        assert entries[0].phandle == 42

        # 64-bit address: 0x8_00000000
        assert entries[1].child_addr == 0x800000000
        assert entries[1].phandle == 43

    def test_parse_incomplete_entry_ignored(self):
        """Test that incomplete entries at the end are ignored."""
        # Complete entry + partial entry
        address_map = [
            0xf0000000, 42, 0xf0000000, 0x10000000,  # complete
            0xff000000, 43,  # incomplete (missing parent_addr and size)
        ]
        entries = parse_address_map(address_map, address_cells=1, size_cells=1)

        # Only the complete entry should be parsed
        assert len(entries) == 1
        assert entries[0].phandle == 42


class TestGetAccessiblePhandles:
    """Tests for the get_accessible_phandles function."""

    def test_get_phandles_32bit(self):
        """Test extracting phandles from 32-bit address-map."""
        address_map = [
            0xf0000000, 10, 0xf0000000, 0x10000000,
            0xf9000000, 20, 0xf9000000, 0x80000,
            0xff300000, 30, 0xff300000, 0x10000,
        ]
        phandles = get_accessible_phandles(address_map, address_cells=1, size_cells=1)

        assert phandles == [10, 20, 30]

    def test_get_phandles_64bit(self):
        """Test extracting phandles from 64-bit address-map."""
        address_map = [
            0x0, 0xf0000000, 42, 0x0, 0xf0000000, 0x0, 0x10000000,
            0x8, 0x00000000, 43, 0x8, 0x00000000, 0x0, 0x80000000,
        ]
        phandles = get_accessible_phandles(address_map, address_cells=2, size_cells=2)

        assert phandles == [42, 43]

    def test_get_phandles_empty(self):
        """Test extracting phandles from empty address-map."""
        phandles = get_accessible_phandles([], address_cells=1, size_cells=1)
        assert phandles == []


class TestFindAddressInMap:
    """Tests for the find_address_in_map function."""

    def test_find_address_in_first_entry(self):
        """Test finding an address in the first entry."""
        entries = [
            LopperAddressMapEntry(0x1000, 1, 0x1000, 0x1000),
            LopperAddressMapEntry(0x2000, 2, 0x2000, 0x1000),
            LopperAddressMapEntry(0x3000, 3, 0x3000, 0x1000),
        ]
        result = find_address_in_map(entries, 0x1500)

        assert result is not None
        assert result.phandle == 1

    def test_find_address_in_middle_entry(self):
        """Test finding an address in a middle entry."""
        entries = [
            LopperAddressMapEntry(0x1000, 1, 0x1000, 0x1000),
            LopperAddressMapEntry(0x2000, 2, 0x2000, 0x1000),
            LopperAddressMapEntry(0x3000, 3, 0x3000, 0x1000),
        ]
        result = find_address_in_map(entries, 0x2500)

        assert result is not None
        assert result.phandle == 2

    def test_find_address_not_mapped(self):
        """Test that unmapped address returns None."""
        entries = [
            LopperAddressMapEntry(0x1000, 1, 0x1000, 0x1000),
            LopperAddressMapEntry(0x3000, 3, 0x3000, 0x1000),
        ]
        # Gap between entries
        result = find_address_in_map(entries, 0x2500)

        assert result is None

    def test_find_address_empty_entries(self):
        """Test finding address in empty entries list."""
        result = find_address_in_map([], 0x1000)
        assert result is None


class TestAccessibleByWithSystemTree:
    """Integration tests for accessible_by using the system device tree."""

    def test_accessible_by_returns_list(self, lopper_sdt):
        """Test accessible_by returns a list."""
        tree = lopper_sdt.tree

        # Try to find any node to check
        try:
            root = tree['/']
        except KeyError:
            pytest.skip("No root node")

        # Should return a list (possibly empty)
        result = tree.accessible_by(root)
        assert isinstance(result, list)

    def test_accessible_by_node_target(self, lopper_sdt):
        """Test accessible_by with a node target."""
        tree = lopper_sdt.tree

        # Find a node that might be in an address-map
        try:
            # Look for any node with address-map
            cluster = None
            for node in tree:
                if 'address-map' in node.__props__:
                    cluster = node
                    break

            if cluster is None:
                pytest.skip("No CPU cluster with address-map found")

            # Get the address-map and find a phandle
            address_map = cluster['address-map'].value
            na = cluster['#ranges-address-cells'].value[0]
            ns = cluster['#ranges-size-cells'].value[0]

            entries = parse_address_map(address_map, na, ns)
            if not entries:
                pytest.skip("Empty address-map")

            # Find the node for first phandle
            target = tree.pnode(entries[0].phandle)
            if target is None:
                pytest.skip("Could not resolve phandle to node")

            # This node should be accessible by at least one cluster
            result = tree.accessible_by(target)
            assert len(result) >= 1
            assert cluster in result

        except (KeyError, IndexError, TypeError):
            pytest.skip("Could not set up test")

    def test_accessible_by_path_string(self, lopper_sdt):
        """Test accessible_by with path string target."""
        tree = lopper_sdt.tree

        # Non-existent path should return empty list
        result = tree.accessible_by('/nonexistent/path')
        assert result == []

    def test_accessible_by_uses_deref(self, lopper_sdt):
        """Test that accessible_by uses deref for string resolution."""
        tree = lopper_sdt.tree

        # deref handles path, label, and alias resolution
        # Non-existent label/alias should return empty list
        result = tree.accessible_by('nonexistent_label')
        assert result == []

    def test_accessible_by_address_integer(self, lopper_sdt):
        """Test accessible_by with integer address."""
        tree = lopper_sdt.tree

        # Check for any cluster that maps 0xf0000000 (common address)
        result = tree.accessible_by(0xf0000000)
        assert isinstance(result, list)
        # May or may not have matches depending on tree

    def test_accessible_by_returns_cluster_nodes(self, lopper_sdt):
        """Test that accessible_by returns nodes with address-map property."""
        tree = lopper_sdt.tree

        # Find a target that's definitely in some address-map
        for node in tree:
            if 'address-map' in node.__props__:
                try:
                    address_map = node['address-map'].value
                    na = node['#ranges-address-cells'].value[0]
                    ns = node['#ranges-size-cells'].value[0]
                    entries = parse_address_map(address_map, na, ns)
                    if entries:
                        target = tree.pnode(entries[0].phandle)
                        if target:
                            result = tree.accessible_by(target)
                            # All returned nodes should have address-map
                            for cluster in result:
                                assert 'address-map' in cluster.__props__
                            return
                except (KeyError, IndexError, TypeError):
                    continue

        pytest.skip("No suitable test data found")


class TestAccessibleByMultipleClusters:
    """Test accessible_by when multiple clusters can access a device."""

    def test_multiple_clusters_same_device(self, lopper_sdt):
        """Test that accessible_by returns all clusters that can access a device."""
        tree = lopper_sdt.tree

        # Find a phandle that appears in multiple address-maps
        phandle_to_clusters = {}

        for node in tree:
            if 'address-map' not in node.__props__:
                continue
            try:
                address_map = node['address-map'].value
                na = node['#ranges-address-cells'].value[0]
                ns = node['#ranges-size-cells'].value[0]
                entries = parse_address_map(address_map, na, ns)
                for entry in entries:
                    if entry.phandle not in phandle_to_clusters:
                        phandle_to_clusters[entry.phandle] = []
                    phandle_to_clusters[entry.phandle].append(node)
            except (KeyError, IndexError, TypeError):
                continue

        # Find a phandle that's in multiple clusters
        shared_phandle = None
        expected_clusters = None
        for phandle, clusters in phandle_to_clusters.items():
            if len(clusters) > 1:
                shared_phandle = phandle
                expected_clusters = clusters
                break

        if shared_phandle is None:
            pytest.skip("No device shared by multiple clusters")

        target = tree.pnode(shared_phandle)
        if target is None:
            pytest.skip("Could not resolve shared phandle")

        result = tree.accessible_by(target)
        assert len(result) == len(expected_clusters)
        for cluster in expected_clusters:
            assert cluster in result


class TestAddressMapParsingMatchesLegacy:
    """Test that new parsing matches the legacy while-loop implementation."""

    def test_phandle_extraction_matches_legacy_32bit(self):
        """Verify get_accessible_phandles matches legacy extraction for 32-bit."""
        # This is the pattern used throughout the assists
        address_map = [
            0xf0000000, 10, 0xf0000000, 0x10000000,
            0xf9000000, 20, 0xf9000000, 0x80000,
            0xff300000, 30, 0xff300000, 0x10000,
        ]
        na = 1
        ns = 1

        # Legacy extraction pattern (from gen_domain_dts.py)
        cells = na + ns
        tmp = na
        legacy_phandles = []
        while tmp < len(address_map):
            legacy_phandles.append(address_map[tmp])
            tmp = tmp + cells + na + 1

        # New API
        new_phandles = get_accessible_phandles(address_map, na, ns)

        assert new_phandles == legacy_phandles

    def test_phandle_extraction_matches_legacy_64bit(self):
        """Verify get_accessible_phandles matches legacy extraction for 64-bit."""
        # 64-bit address-map (na=2, ns=2)
        address_map = [
            0x0, 0xf0000000, 42, 0x0, 0xf0000000, 0x0, 0x10000000,
            0x0, 0xf9000000, 43, 0x0, 0xf9000000, 0x0, 0x80000,
        ]
        na = 2
        ns = 2

        # Legacy extraction pattern
        cells = na + ns
        tmp = na
        legacy_phandles = []
        while tmp < len(address_map):
            legacy_phandles.append(address_map[tmp])
            tmp = tmp + cells + na + 1

        # New API
        new_phandles = get_accessible_phandles(address_map, na, ns)

        assert new_phandles == legacy_phandles
