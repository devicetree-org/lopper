"""
Tests for lopper/audit.py - tree validation and consistency checking.

This module tests the audit framework functions:
- _cell_value_get(): Multi-cell value extraction helper
- check_invalid_phandles(): Detect dangling phandle references
- report_invalid_phandles(): Log warnings/errors for invalid phandles
- check_reserved_memory_in_memory_ranges(): Validate reserved-memory bounds
- validate_reserved_memory_in_memory_ranges(): Log errors for invalid regions
"""

import pytest
from unittest.mock import patch, MagicMock

from lopper.tree import LopperTree, LopperNode, LopperProp
import lopper.audit


class TestCellValueGet:
    """Tests for the _cell_value_get() helper function."""

    def test_single_cell_value(self):
        """Test extracting a single cell (32-bit) value."""
        cells = [0x12345678, 0xABCDEF00]
        value, used = lopper.audit._cell_value_get(cells, cell_size=1, start_idx=0)

        assert value == 0x12345678
        assert used == [0x12345678]

    def test_single_cell_value_with_offset(self):
        """Test extracting single cell value at an offset."""
        cells = [0x12345678, 0xABCDEF00, 0x11111111]
        value, used = lopper.audit._cell_value_get(cells, cell_size=1, start_idx=1)

        assert value == 0xABCDEF00
        assert used == [0xABCDEF00]

    def test_dual_cell_value(self):
        """Test extracting a dual cell (64-bit) value."""
        # 0x00000001_80000000 = 0x180000000 (6GB)
        cells = [0x00000001, 0x80000000]
        value, used = lopper.audit._cell_value_get(cells, cell_size=2, start_idx=0)

        assert value == 0x180000000
        assert used == [0x00000001, 0x80000000]

    def test_dual_cell_value_with_offset(self):
        """Test extracting dual cell value at an offset."""
        # First cell is padding, then 0x00000002_00000000 = 0x200000000 (8GB)
        cells = [0x99999999, 0x00000002, 0x00000000]
        value, used = lopper.audit._cell_value_get(cells, cell_size=2, start_idx=1)

        assert value == 0x200000000
        assert used == [0x00000002, 0x00000000]

    def test_dual_cell_zero(self):
        """Test extracting zero as a dual cell value."""
        cells = [0x00000000, 0x00000000]
        value, used = lopper.audit._cell_value_get(cells, cell_size=2, start_idx=0)

        assert value == 0
        assert used == [0x00000000, 0x00000000]

    def test_dual_cell_max_32bit_low(self):
        """Test extracting max 32-bit value in low cell."""
        cells = [0x00000000, 0xFFFFFFFF]
        value, used = lopper.audit._cell_value_get(cells, cell_size=2, start_idx=0)

        assert value == 0xFFFFFFFF
        assert used == [0x00000000, 0xFFFFFFFF]


class TestCheckInvalidPhandles:
    """Tests for check_invalid_phandles() function."""

    def test_no_invalid_phandles_in_empty_tree(self):
        """Test that an empty tree has no invalid phandles."""
        tree = LopperTree()
        tree.sync()

        invalid = lopper.audit.check_invalid_phandles(tree, warn_only_modified=False)
        assert invalid == []

    def test_detects_0xffffffff_sentinel(self):
        """Test detection of dtc's 0xffffffff sentinel for unresolved references."""
        tree = LopperTree()
        root = tree['/']

        # Create a node with an interrupt-parent pointing to 0xffffffff
        node = LopperNode(-1, "/test_node")
        node + LopperProp(name='interrupt-parent', value=[0xFFFFFFFF])
        tree.add(node)
        tree.sync()

        invalid = lopper.audit.check_invalid_phandles(tree, warn_only_modified=False)

        # Should detect the invalid phandle
        assert len(invalid) == 1
        assert invalid[0][0] == "/test_node"
        assert invalid[0][1] == "interrupt-parent"

    def test_detects_dangling_numeric_phandle(self):
        """Test detection of a phandle that doesn't resolve to any node."""
        tree = LopperTree()
        root = tree['/']

        # Create a node with clocks property pointing to non-existent phandle
        node = LopperNode(-1, "/test_node")
        # 0x12345 is not a valid phandle in this tree
        node + LopperProp(name='clocks', value=[0x12345])
        tree.add(node)
        tree.sync()

        invalid = lopper.audit.check_invalid_phandles(tree, warn_only_modified=False)

        assert len(invalid) == 1
        assert invalid[0][0] == "/test_node"
        assert invalid[0][1] == "clocks"

    def test_valid_phandle_not_flagged(self):
        """Test that valid phandle references are not flagged."""
        tree = LopperTree()
        root = tree['/']

        # Create a target node with a phandle
        target = LopperNode(-1, "/target_node")
        tree.add(target)
        tree.sync()
        phandle = target.phandle_or_create()

        # Create a node referencing the target
        node = LopperNode(-1, "/test_node")
        node + LopperProp(name='interrupt-parent', value=[phandle])
        tree.add(node)
        tree.sync()

        invalid = lopper.audit.check_invalid_phandles(tree, warn_only_modified=False)

        assert invalid == []

    def test_phandle_zero_is_flagged(self):
        """Test that phandle value 0 is flagged as invalid reference.

        In device tree, phandle 0 means "no phandle assigned" but when used
        as a reference in interrupt-parent, it's still an invalid reference
        since no node has phandle 0.
        """
        tree = LopperTree()
        root = tree['/']

        # Create a node with phandle 0 - this is an invalid reference
        node = LopperNode(-1, "/test_node")
        node + LopperProp(name='interrupt-parent', value=[0])
        tree.add(node)
        tree.sync()

        invalid = lopper.audit.check_invalid_phandles(tree, warn_only_modified=False)

        # phandle 0 is considered invalid since no node has that phandle
        assert len(invalid) == 1
        assert invalid[0][0] == "/test_node"

    def test_multiple_invalid_phandles(self):
        """Test detection of multiple invalid phandles in different nodes."""
        tree = LopperTree()
        root = tree['/']

        # Create first node with invalid phandle
        node1 = LopperNode(-1, "/node1")
        node1 + LopperProp(name='interrupt-parent', value=[0xFFFFFFFF])
        tree.add(node1)

        # Create second node with different invalid phandle
        node2 = LopperNode(-1, "/node2")
        node2 + LopperProp(name='clocks', value=[0xDEADBEEF])
        tree.add(node2)

        tree.sync()

        invalid = lopper.audit.check_invalid_phandles(tree, warn_only_modified=False)

        assert len(invalid) == 2
        paths = [item[0] for item in invalid]
        assert "/node1" in paths
        assert "/node2" in paths

    def test_warn_only_modified_skips_unmodified(self):
        """Test that warn_only_modified=True skips unmodified nodes and properties."""
        tree = LopperTree()
        root = tree['/']

        # Create node with invalid phandle
        node = LopperNode(-1, "/test_node")
        node + LopperProp(name='interrupt-parent', value=[0xFFFFFFFF])
        tree.add(node)
        tree.sync()

        # Mark node as resolved and not modified
        node.__nstate__ = "resolved"
        node.__modified__ = False

        # Also mark the property as syncd and not modified
        # The optimization checks BOTH node and property state
        prop = node['interrupt-parent']
        prop.__pstate__ = "syncd"
        prop.__modified__ = False

        invalid = lopper.audit.check_invalid_phandles(tree, warn_only_modified=True)

        # Should skip because both node AND property are resolved/syncd and not modified
        assert invalid == []

    def test_warn_only_modified_checks_modified_nodes(self):
        """Test that warn_only_modified=True checks modified nodes."""
        tree = LopperTree()
        root = tree['/']

        # Create node with invalid phandle
        node = LopperNode(-1, "/test_node")
        node + LopperProp(name='interrupt-parent', value=[0xFFFFFFFF])
        tree.add(node)
        tree.sync()

        # Mark node as resolved but modified
        node.__nstate__ = "resolved"
        node.__modified__ = True

        invalid = lopper.audit.check_invalid_phandles(tree, warn_only_modified=True)

        # Should check the node because it's modified
        assert len(invalid) == 1


class TestReportInvalidPhandles:
    """Tests for report_invalid_phandles() function."""

    def test_reports_warnings_for_invalid_phandles(self):
        """Test that warnings are logged for invalid phandles."""
        tree = LopperTree()
        root = tree['/']

        node = LopperNode(-1, "/test_node")
        node + LopperProp(name='interrupt-parent', value=[0xFFFFFFFF])
        tree.add(node)
        tree.sync()

        with patch('lopper.log._warning') as mock_warning:
            count = lopper.audit.report_invalid_phandles(tree, werror=False,
                                                         warn_only_modified=False)

            assert count == 1
            mock_warning.assert_called_once()
            # Check the warning message contains relevant info
            call_args = mock_warning.call_args[0][0]
            assert "interrupt-parent" in call_args
            assert "/test_node" in call_args

    def test_reports_errors_with_werror(self):
        """Test that errors are raised with werror=True."""
        tree = LopperTree()
        root = tree['/']

        node = LopperNode(-1, "/test_node")
        node + LopperProp(name='interrupt-parent', value=[0xFFFFFFFF])
        tree.add(node)
        tree.sync()

        with patch('lopper.log._error') as mock_error:
            # Mock _error to not actually exit
            mock_error.side_effect = SystemExit(1)

            with pytest.raises(SystemExit):
                lopper.audit.report_invalid_phandles(tree, werror=True,
                                                     warn_only_modified=False)

            mock_error.assert_called_once()
            # Check also_exit=1 was passed
            assert mock_error.call_args[1].get('also_exit') == 1

    def test_returns_zero_for_valid_tree(self):
        """Test that zero is returned for a tree with no invalid phandles."""
        tree = LopperTree()
        tree.sync()

        count = lopper.audit.report_invalid_phandles(tree, werror=False,
                                                     warn_only_modified=False)
        assert count == 0


class TestCheckReservedMemoryInMemoryRanges:
    """Tests for check_reserved_memory_in_memory_ranges() function."""

    def _create_tree_with_reserved_memory(self, domain_memory, resmem_reg):
        """Helper to create a tree with domain memory and reserved-memory node.

        Args:
            domain_memory: List of values for domain's memory property
            resmem_reg: List of values for reserved-memory node's reg property

        Returns:
            Tuple of (tree, domain_node, resmem_phandle)
        """
        tree = LopperTree()
        root = tree['/']
        root + LopperProp(name='#address-cells', value=2)
        root + LopperProp(name='#size-cells', value=2)

        # Create reserved-memory parent
        resmem_parent = LopperNode(-1, "/reserved-memory")
        resmem_parent + LopperProp(name='#address-cells', value=2)
        resmem_parent + LopperProp(name='#size-cells', value=2)
        tree.add(resmem_parent)

        # Create reserved-memory node
        resmem_node = LopperNode(-1, "/reserved-memory/test_region")
        resmem_node + LopperProp(name='reg', value=resmem_reg)
        tree.add(resmem_node)
        tree.sync()

        phandle = resmem_node.phandle_or_create()

        # Create domain node
        domain_node = LopperNode(-1, "/domains/test_domain")
        domain_node + LopperProp(name='memory', value=domain_memory)
        domain_node + LopperProp(name='reserved-memory', value=[phandle])
        tree.add(domain_node)
        tree.sync()

        return tree, domain_node, phandle

    def test_valid_region_inside_memory(self):
        """Test that a valid reserved-memory region inside domain memory passes."""
        # Domain memory: 0x0 - 0x40000000 (1GB)
        # Reserved-memory: 0x10000000 - 0x11000000 (16MB at 256MB)
        tree, domain_node, _ = self._create_tree_with_reserved_memory(
            domain_memory=[0x0, 0x0, 0x0, 0x40000000],
            resmem_reg=[0x0, 0x10000000, 0x0, 0x1000000]
        )

        invalid = lopper.audit.check_reserved_memory_in_memory_ranges(tree, domain_node)
        assert invalid == []

    def test_invalid_region_outside_memory(self):
        """Test that reserved-memory outside domain memory is detected."""
        # Domain memory: 0x0 - 0x40000000 (1GB)
        # Reserved-memory: 0x80000000 - 0x90000000 (256MB at 2GB) - outside!
        tree, domain_node, _ = self._create_tree_with_reserved_memory(
            domain_memory=[0x0, 0x0, 0x0, 0x40000000],
            resmem_reg=[0x0, 0x80000000, 0x0, 0x10000000]
        )

        invalid = lopper.audit.check_reserved_memory_in_memory_ranges(tree, domain_node)

        assert len(invalid) == 1
        assert invalid[0][0] == "/reserved-memory/test_region"
        assert invalid[0][1] == 0x80000000  # start address
        assert invalid[0][2] == 0x90000000  # end address

    def test_region_partially_outside(self):
        """Test that a region extending past memory end is detected."""
        # Domain memory: 0x0 - 0x40000000 (1GB)
        # Reserved-memory: 0x30000000 - 0x50000000 (512MB at 768MB) - extends past 1GB
        tree, domain_node, _ = self._create_tree_with_reserved_memory(
            domain_memory=[0x0, 0x0, 0x0, 0x40000000],
            resmem_reg=[0x0, 0x30000000, 0x0, 0x20000000]
        )

        invalid = lopper.audit.check_reserved_memory_in_memory_ranges(tree, domain_node)

        assert len(invalid) == 1

    def test_no_memory_property_skips_validation(self):
        """Test that missing memory property skips validation gracefully."""
        tree = LopperTree()
        root = tree['/']
        root + LopperProp(name='#address-cells', value=2)
        root + LopperProp(name='#size-cells', value=2)

        # Domain without memory property
        domain_node = LopperNode(-1, "/domains/test_domain")
        tree.add(domain_node)
        tree.sync()

        invalid = lopper.audit.check_reserved_memory_in_memory_ranges(tree, domain_node)
        assert invalid == []

    def test_no_reserved_memory_property_skips_validation(self):
        """Test that missing reserved-memory property skips validation gracefully."""
        tree = LopperTree()
        root = tree['/']
        root + LopperProp(name='#address-cells', value=2)
        root + LopperProp(name='#size-cells', value=2)

        # Domain with memory but no reserved-memory
        domain_node = LopperNode(-1, "/domains/test_domain")
        domain_node + LopperProp(name='memory', value=[0x0, 0x0, 0x0, 0x40000000])
        tree.add(domain_node)
        tree.sync()

        invalid = lopper.audit.check_reserved_memory_in_memory_ranges(tree, domain_node)
        assert invalid == []

    def test_multiple_memory_ranges_valid(self):
        """Test validation with multiple disjoint memory ranges."""
        tree = LopperTree()
        root = tree['/']
        root + LopperProp(name='#address-cells', value=2)
        root + LopperProp(name='#size-cells', value=2)

        # Create reserved-memory parent and node
        resmem_parent = LopperNode(-1, "/reserved-memory")
        resmem_parent + LopperProp(name='#address-cells', value=2)
        resmem_parent + LopperProp(name='#size-cells', value=2)
        tree.add(resmem_parent)

        # Reserved-memory at 0x80010000 - in second memory range
        resmem_node = LopperNode(-1, "/reserved-memory/test_region")
        resmem_node + LopperProp(name='reg', value=[0x0, 0x80010000, 0x0, 0x10000])
        tree.add(resmem_node)
        tree.sync()

        phandle = resmem_node.phandle_or_create()

        # Domain with two memory ranges: 0-0x40000000 and 0x80000000-0xC0000000
        domain_node = LopperNode(-1, "/domains/test_domain")
        domain_node + LopperProp(name='memory', value=[
            0x0, 0x0, 0x0, 0x40000000,           # 0 - 1GB
            0x0, 0x80000000, 0x0, 0x40000000     # 2GB - 3GB
        ])
        domain_node + LopperProp(name='reserved-memory', value=[phandle])
        tree.add(domain_node)
        tree.sync()

        invalid = lopper.audit.check_reserved_memory_in_memory_ranges(tree, domain_node)
        assert invalid == []


class TestValidateReservedMemoryInMemoryRanges:
    """Tests for validate_reserved_memory_in_memory_ranges() function."""

    def test_logs_error_for_invalid_region(self):
        """Test that errors are logged for invalid regions."""
        tree = LopperTree()
        root = tree['/']
        root + LopperProp(name='#address-cells', value=2)
        root + LopperProp(name='#size-cells', value=2)

        # Create reserved-memory outside domain memory
        resmem_parent = LopperNode(-1, "/reserved-memory")
        resmem_parent + LopperProp(name='#address-cells', value=2)
        resmem_parent + LopperProp(name='#size-cells', value=2)
        tree.add(resmem_parent)

        resmem_node = LopperNode(-1, "/reserved-memory/outside")
        resmem_node + LopperProp(name='reg', value=[0x0, 0x80000000, 0x0, 0x10000000])
        tree.add(resmem_node)
        tree.sync()

        phandle = resmem_node.phandle_or_create()

        domain_node = LopperNode(-1, "/domains/test_domain")
        domain_node + LopperProp(name='memory', value=[0x0, 0x0, 0x0, 0x40000000])
        domain_node + LopperProp(name='reserved-memory', value=[phandle])
        tree.add(domain_node)
        tree.sync()

        with patch('lopper.log._error') as mock_error:
            count = lopper.audit.validate_reserved_memory_in_memory_ranges(
                tree, domain_node, werror=False
            )

            assert count == 1
            mock_error.assert_called_once()
            call_args = mock_error.call_args[0][0]
            assert "outside" in call_args
            assert "0x80000000" in call_args

    def test_exits_with_werror(self):
        """Test that SystemExit is raised with werror=True."""
        tree = LopperTree()
        root = tree['/']
        root + LopperProp(name='#address-cells', value=2)
        root + LopperProp(name='#size-cells', value=2)

        resmem_parent = LopperNode(-1, "/reserved-memory")
        resmem_parent + LopperProp(name='#address-cells', value=2)
        resmem_parent + LopperProp(name='#size-cells', value=2)
        tree.add(resmem_parent)

        resmem_node = LopperNode(-1, "/reserved-memory/outside")
        resmem_node + LopperProp(name='reg', value=[0x0, 0x80000000, 0x0, 0x10000000])
        tree.add(resmem_node)
        tree.sync()

        phandle = resmem_node.phandle_or_create()

        domain_node = LopperNode(-1, "/domains/test_domain")
        domain_node + LopperProp(name='memory', value=[0x0, 0x0, 0x0, 0x40000000])
        domain_node + LopperProp(name='reserved-memory', value=[phandle])
        tree.add(domain_node)
        tree.sync()

        with patch('lopper.log._error') as mock_error:
            mock_error.side_effect = SystemExit(1)

            with pytest.raises(SystemExit):
                lopper.audit.validate_reserved_memory_in_memory_ranges(
                    tree, domain_node, werror=True
                )

            assert mock_error.call_args[1].get('also_exit') == 1

    def test_returns_zero_for_valid_regions(self):
        """Test that zero is returned when all regions are valid."""
        tree = LopperTree()
        root = tree['/']
        root + LopperProp(name='#address-cells', value=2)
        root + LopperProp(name='#size-cells', value=2)

        resmem_parent = LopperNode(-1, "/reserved-memory")
        resmem_parent + LopperProp(name='#address-cells', value=2)
        resmem_parent + LopperProp(name='#size-cells', value=2)
        tree.add(resmem_parent)

        # Reserved-memory inside domain memory
        resmem_node = LopperNode(-1, "/reserved-memory/inside")
        resmem_node + LopperProp(name='reg', value=[0x0, 0x10000000, 0x0, 0x1000000])
        tree.add(resmem_node)
        tree.sync()

        phandle = resmem_node.phandle_or_create()

        domain_node = LopperNode(-1, "/domains/test_domain")
        domain_node + LopperProp(name='memory', value=[0x0, 0x0, 0x0, 0x40000000])
        domain_node + LopperProp(name='reserved-memory', value=[phandle])
        tree.add(domain_node)
        tree.sync()

        count = lopper.audit.validate_reserved_memory_in_memory_ranges(
            tree, domain_node, werror=False
        )
        assert count == 0


class TestAuditIntegrationWithSanityTrees:
    """Integration tests using lopper_sanity device trees.

    These tests run the audit functions against real device trees from
    lopper_sanity.py to verify they work with production-like data.
    """

    def test_check_invalid_phandles_on_clean_tree(self, lopper_tree):
        """Test that a well-formed tree from lopper_sanity has no invalid phandles."""
        # The lopper_sanity tester.dts is a valid device tree with proper phandle refs
        invalid = lopper.audit.check_invalid_phandles(lopper_tree, warn_only_modified=False)

        # A clean tree should have no invalid phandle references
        assert invalid == [], f"Found unexpected invalid phandles: {invalid}"

    def test_check_invalid_phandles_on_sdt(self, lopper_sdt):
        """Test audit on full LopperSDT with system device tree."""
        # Run the audit on the SDT's tree
        invalid = lopper.audit.check_invalid_phandles(lopper_sdt.tree, warn_only_modified=False)

        # The lopper_sanity tree should be clean
        assert invalid == [], f"Found unexpected invalid phandles: {invalid}"

    def test_report_invalid_phandles_returns_zero_on_clean_tree(self, lopper_tree):
        """Test that report function returns 0 on a clean tree."""
        count = lopper.audit.report_invalid_phandles(
            lopper_tree, werror=False, warn_only_modified=False
        )
        assert count == 0

    def test_injected_invalid_phandle_detected(self, lopper_tree):
        """Test that we can detect an invalid phandle injected into a real tree."""
        # Inject a node with an invalid phandle reference
        bad_node = LopperNode(-1, "/injected_bad_node")
        bad_node + LopperProp(name='interrupt-parent', value=[0xDEADBEEF])
        lopper_tree.add(bad_node)
        lopper_tree.sync()

        invalid = lopper.audit.check_invalid_phandles(lopper_tree, warn_only_modified=False)

        # Should detect at least our injected bad reference
        assert len(invalid) >= 1
        paths = [item[0] for item in invalid]
        assert "/injected_bad_node" in paths

    def test_injected_0xffffffff_sentinel_detected(self, lopper_tree):
        """Test detection of dtc's 0xffffffff sentinel in a real tree."""
        # Simulate what happens when dtc forces output with unresolved ref
        bad_node = LopperNode(-1, "/unresolved_ref_node")
        bad_node + LopperProp(name='clocks', value=[0xFFFFFFFF])
        lopper_tree.add(bad_node)
        lopper_tree.sync()

        invalid = lopper.audit.check_invalid_phandles(lopper_tree, warn_only_modified=False)

        paths = [item[0] for item in invalid]
        assert "/unresolved_ref_node" in paths

    def test_valid_phandle_in_real_tree_not_flagged(self, lopper_tree):
        """Test that valid phandle references in the tree aren't flagged."""
        # Find a node with a phandle in the tree
        target_node = None
        for node in lopper_tree:
            if node.phandle > 0:
                target_node = node
                break

        if target_node is None:
            pytest.skip("No node with phandle found in test tree")

        # Add a new node that references this valid phandle
        good_node = LopperNode(-1, "/good_reference_node")
        good_node + LopperProp(name='interrupt-parent', value=[target_node.phandle])
        lopper_tree.add(good_node)
        lopper_tree.sync()

        invalid = lopper.audit.check_invalid_phandles(lopper_tree, warn_only_modified=False)

        # Our new node should NOT be in the invalid list
        paths = [item[0] for item in invalid]
        assert "/good_reference_node" not in paths


class TestAuditIntegrationReservedMemory:
    """Integration tests for reserved-memory validation with real tree structures."""

    def test_reserved_memory_with_injected_domain(self, lopper_tree):
        """Test reserved-memory validation with domain injected into real tree."""
        # Add #address-cells and #size-cells to root if not present
        root = lopper_tree['/']
        try:
            _ = root['#address-cells']
        except:
            root + LopperProp(name='#address-cells', value=2)
        try:
            _ = root['#size-cells']
        except:
            root + LopperProp(name='#size-cells', value=2)

        # Create reserved-memory structure
        resmem_parent = LopperNode(-1, "/reserved-memory")
        resmem_parent + LopperProp(name='#address-cells', value=2)
        resmem_parent + LopperProp(name='#size-cells', value=2)
        resmem_parent + LopperProp(name='ranges', value=[])
        lopper_tree.add(resmem_parent)

        # Create a reserved-memory region at 0x10000000
        resmem_node = LopperNode(-1, "/reserved-memory/test_buffer@10000000")
        resmem_node + LopperProp(name='reg', value=[0x0, 0x10000000, 0x0, 0x1000000])
        resmem_node + LopperProp(name='compatible', value="shared-dma-pool")
        lopper_tree.add(resmem_node)
        lopper_tree.sync()

        phandle = resmem_node.phandle_or_create()

        # Create domain with memory that INCLUDES the reserved region
        domain_node = LopperNode(-1, "/domains/test_domain")
        # Memory from 0x0 to 0x80000000 (2GB) - includes reserved-memory
        domain_node + LopperProp(name='memory', value=[0x0, 0x0, 0x0, 0x80000000])
        domain_node + LopperProp(name='reserved-memory', value=[phandle])
        lopper_tree.add(domain_node)
        lopper_tree.sync()

        # Validation should pass
        invalid = lopper.audit.check_reserved_memory_in_memory_ranges(
            lopper_tree, domain_node
        )
        assert invalid == []

    def test_reserved_memory_outside_domain_detected(self, lopper_tree):
        """Test that reserved-memory outside domain memory is detected in real tree."""
        root = lopper_tree['/']
        try:
            _ = root['#address-cells']
        except:
            root + LopperProp(name='#address-cells', value=2)
        try:
            _ = root['#size-cells']
        except:
            root + LopperProp(name='#size-cells', value=2)

        # Create reserved-memory structure
        resmem_parent = LopperNode(-1, "/reserved-memory")
        resmem_parent + LopperProp(name='#address-cells', value=2)
        resmem_parent + LopperProp(name='#size-cells', value=2)
        lopper_tree.add(resmem_parent)

        # Create a reserved-memory region at 0x100000000 (4GB) - way outside domain
        resmem_node = LopperNode(-1, "/reserved-memory/outside_buffer@100000000")
        resmem_node + LopperProp(name='reg', value=[0x1, 0x0, 0x0, 0x1000000])
        lopper_tree.add(resmem_node)
        lopper_tree.sync()

        phandle = resmem_node.phandle_or_create()

        # Create domain with memory only from 0x0 to 0x40000000 (1GB)
        domain_node = LopperNode(-1, "/domains/test_domain")
        domain_node + LopperProp(name='memory', value=[0x0, 0x0, 0x0, 0x40000000])
        domain_node + LopperProp(name='reserved-memory', value=[phandle])
        lopper_tree.add(domain_node)
        lopper_tree.sync()

        # Validation should detect the out-of-bounds region
        invalid = lopper.audit.check_reserved_memory_in_memory_ranges(
            lopper_tree, domain_node
        )
        assert len(invalid) == 1
        assert "outside_buffer" in invalid[0][0]


class TestAuditWithModifiedNodes:
    """Test the warn_only_modified optimization with real tree structures."""

    def test_optimization_skips_unchanged_nodes(self, lopper_tree):
        """Test that resolved, unmodified nodes are skipped when optimizing."""
        # First, inject a bad node
        bad_node = LopperNode(-1, "/bad_but_resolved")
        bad_node + LopperProp(name='interrupt-parent', value=[0xBADBAD])
        lopper_tree.add(bad_node)
        lopper_tree.sync()

        # Mark the node and property as resolved/syncd and not modified
        bad_node.__nstate__ = "resolved"
        bad_node.__modified__ = False
        prop = bad_node['interrupt-parent']
        prop.__pstate__ = "syncd"
        prop.__modified__ = False

        # With optimization, this should be skipped
        invalid_optimized = lopper.audit.check_invalid_phandles(
            lopper_tree, warn_only_modified=True
        )

        # Without optimization, it should be caught
        invalid_full = lopper.audit.check_invalid_phandles(
            lopper_tree, warn_only_modified=False
        )

        # The optimized check should NOT find our "resolved" bad node
        opt_paths = [item[0] for item in invalid_optimized]
        assert "/bad_but_resolved" not in opt_paths

        # The full check SHOULD find it
        full_paths = [item[0] for item in invalid_full]
        assert "/bad_but_resolved" in full_paths

    def test_optimization_checks_modified_nodes(self, lopper_tree):
        """Test that modified nodes are checked even with optimization enabled."""
        # Inject a bad node that IS modified
        bad_node = LopperNode(-1, "/bad_and_modified")
        bad_node + LopperProp(name='clocks', value=[0xDEADC0DE])
        lopper_tree.add(bad_node)
        lopper_tree.sync()

        # Mark node as resolved but MODIFIED
        bad_node.__nstate__ = "resolved"
        bad_node.__modified__ = True

        # With optimization, this modified node should still be checked
        invalid = lopper.audit.check_invalid_phandles(
            lopper_tree, warn_only_modified=True
        )

        paths = [item[0] for item in invalid]
        assert "/bad_and_modified" in paths
