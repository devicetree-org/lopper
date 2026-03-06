"""
Tests for lopper/audit/memory.py - memory region data structures and validation.

This module tests the memory validation framework:
- MemoryRegion: Memory region dataclass and overlap detection
- MemoryMap: Collection class with filtering and overlap analysis
- collect_memory_regions(): Tree traversal and region collection
- Validation checks: check_cell_properties, check_reg_property_format, etc.
- MemoryValidator: Orchestration of phased validation
"""

import pytest
from unittest.mock import patch, MagicMock

from lopper.tree import LopperTree, LopperNode, LopperProp
from lopper.audit.memory import (
    MemoryRegion,
    MemoryRegionType,
    MemoryMap,
    OverlapResult,
    ValidationResult,
    ValidationPhase,
    collect_memory_regions,
    check_cell_properties,
    check_reg_property_format,
    check_reserved_memory_overlaps,
    check_domain_memory_overlaps,
    check_cross_domain_memory_overlaps,
    MemoryValidator,
    validate_memory,
)


class TestMemoryRegion:
    """Tests for MemoryRegion dataclass."""

    def test_basic_creation(self):
        """Test creating a basic MemoryRegion."""
        region = MemoryRegion(
            start=0x10000000,
            size=0x1000000,
            region_type=MemoryRegionType.PHYSICAL_MEMORY,
            source_path="/memory@0"
        )
        assert region.start == 0x10000000
        assert region.size == 0x1000000
        assert region.end == 0x11000000
        assert region.region_type == MemoryRegionType.PHYSICAL_MEMORY

    def test_end_property(self):
        """Test that end property correctly computes end address."""
        region = MemoryRegion(
            start=0x0,
            size=0x40000000,  # 1GB
            region_type=MemoryRegionType.PHYSICAL_MEMORY,
            source_path="/memory@0"
        )
        assert region.end == 0x40000000

    def test_overlaps_true(self):
        """Test overlap detection when regions overlap."""
        region1 = MemoryRegion(
            start=0x10000000,
            size=0x10000000,  # 0x10000000 - 0x20000000
            region_type=MemoryRegionType.RESERVED_MEMORY,
            source_path="/reserved-memory/region1"
        )
        region2 = MemoryRegion(
            start=0x18000000,  # Starts in the middle of region1
            size=0x10000000,  # 0x18000000 - 0x28000000
            region_type=MemoryRegionType.RESERVED_MEMORY,
            source_path="/reserved-memory/region2"
        )
        assert region1.overlaps(region2)
        assert region2.overlaps(region1)

    def test_overlaps_false_adjacent(self):
        """Test that adjacent regions do not overlap."""
        region1 = MemoryRegion(
            start=0x10000000,
            size=0x10000000,  # 0x10000000 - 0x20000000
            region_type=MemoryRegionType.RESERVED_MEMORY,
            source_path="/reserved-memory/region1"
        )
        region2 = MemoryRegion(
            start=0x20000000,  # Starts exactly where region1 ends
            size=0x10000000,  # 0x20000000 - 0x30000000
            region_type=MemoryRegionType.RESERVED_MEMORY,
            source_path="/reserved-memory/region2"
        )
        assert not region1.overlaps(region2)
        assert not region2.overlaps(region1)

    def test_overlaps_false_disjoint(self):
        """Test that disjoint regions do not overlap."""
        region1 = MemoryRegion(
            start=0x10000000,
            size=0x10000000,
            region_type=MemoryRegionType.RESERVED_MEMORY,
            source_path="/reserved-memory/region1"
        )
        region2 = MemoryRegion(
            start=0x50000000,
            size=0x10000000,
            region_type=MemoryRegionType.RESERVED_MEMORY,
            source_path="/reserved-memory/region2"
        )
        assert not region1.overlaps(region2)

    def test_contains(self):
        """Test containment check."""
        outer = MemoryRegion(
            start=0x0,
            size=0x80000000,  # 2GB
            region_type=MemoryRegionType.PHYSICAL_MEMORY,
            source_path="/memory@0"
        )
        inner = MemoryRegion(
            start=0x10000000,
            size=0x1000000,  # 16MB inside the 2GB region
            region_type=MemoryRegionType.RESERVED_MEMORY,
            source_path="/reserved-memory/inner"
        )
        assert outer.contains(inner)
        assert not inner.contains(outer)

    def test_overlap_size(self):
        """Test overlap size calculation."""
        region1 = MemoryRegion(
            start=0x10000000,
            size=0x10000000,  # 256MB
            region_type=MemoryRegionType.RESERVED_MEMORY,
            source_path="/reserved-memory/region1"
        )
        region2 = MemoryRegion(
            start=0x18000000,
            size=0x10000000,  # 256MB, overlaps 128MB
            region_type=MemoryRegionType.RESERVED_MEMORY,
            source_path="/reserved-memory/region2"
        )
        # Overlap from 0x18000000 to 0x20000000 = 0x8000000 (128MB)
        assert region1.overlap_size(region2) == 0x8000000
        assert region2.overlap_size(region1) == 0x8000000

    def test_overlap_size_no_overlap(self):
        """Test overlap size returns 0 for non-overlapping regions."""
        region1 = MemoryRegion(
            start=0x10000000,
            size=0x10000000,
            region_type=MemoryRegionType.RESERVED_MEMORY,
            source_path="/reserved-memory/region1"
        )
        region2 = MemoryRegion(
            start=0x50000000,
            size=0x10000000,
            region_type=MemoryRegionType.RESERVED_MEMORY,
            source_path="/reserved-memory/region2"
        )
        assert region1.overlap_size(region2) == 0

    def test_is_shared_memory_true(self):
        """Test shared memory detection with shared-dma-pool."""
        region = MemoryRegion(
            start=0x10000000,
            size=0x1000000,
            region_type=MemoryRegionType.RESERVED_MEMORY,
            source_path="/reserved-memory/shared",
            compatible=['shared-dma-pool']
        )
        assert region.is_shared_memory()

    def test_is_shared_memory_false(self):
        """Test shared memory detection with non-shared compatible."""
        region = MemoryRegion(
            start=0x10000000,
            size=0x1000000,
            region_type=MemoryRegionType.RESERVED_MEMORY,
            source_path="/reserved-memory/private",
            compatible=['xlnx,reserved-memory']
        )
        assert not region.is_shared_memory()

    def test_is_shared_memory_no_compatible(self):
        """Test shared memory detection with no compatible property."""
        region = MemoryRegion(
            start=0x10000000,
            size=0x1000000,
            region_type=MemoryRegionType.RESERVED_MEMORY,
            source_path="/reserved-memory/nocompat"
        )
        assert not region.is_shared_memory()


class TestMemoryMap:
    """Tests for MemoryMap collection class."""

    def test_empty_map(self):
        """Test empty memory map."""
        mm = MemoryMap()
        assert len(mm) == 0
        assert list(mm) == []

    def test_add_region(self):
        """Test adding regions to map."""
        mm = MemoryMap()
        region = MemoryRegion(
            start=0x0,
            size=0x40000000,
            region_type=MemoryRegionType.PHYSICAL_MEMORY,
            source_path="/memory@0"
        )
        mm.add_region(region)
        assert len(mm) == 1
        assert region in list(mm)

    def test_get_by_type(self):
        """Test filtering by region type."""
        mm = MemoryMap()
        phys = MemoryRegion(
            start=0x0, size=0x40000000,
            region_type=MemoryRegionType.PHYSICAL_MEMORY,
            source_path="/memory@0"
        )
        res = MemoryRegion(
            start=0x10000000, size=0x1000000,
            region_type=MemoryRegionType.RESERVED_MEMORY,
            source_path="/reserved-memory/region"
        )
        mm.add_region(phys)
        mm.add_region(res)

        physical_regions = mm.get_by_type(MemoryRegionType.PHYSICAL_MEMORY)
        assert len(physical_regions) == 1
        assert physical_regions[0] == phys

        reserved_regions = mm.get_by_type(MemoryRegionType.RESERVED_MEMORY)
        assert len(reserved_regions) == 1
        assert reserved_regions[0] == res

    def test_get_by_domain(self):
        """Test filtering by domain."""
        mm = MemoryMap()
        region1 = MemoryRegion(
            start=0x0, size=0x40000000,
            region_type=MemoryRegionType.DOMAIN_MEMORY,
            source_path="/domains/linux",
            domain="linux"
        )
        region2 = MemoryRegion(
            start=0x80000000, size=0x10000000,
            region_type=MemoryRegionType.DOMAIN_MEMORY,
            source_path="/domains/baremetal",
            domain="baremetal"
        )
        mm.add_region(region1)
        mm.add_region(region2)

        linux_regions = mm.get_by_domain("linux")
        assert len(linux_regions) == 1
        assert linux_regions[0].domain == "linux"

    def test_get_domains(self):
        """Test getting set of all domains."""
        mm = MemoryMap()
        mm.add_region(MemoryRegion(
            start=0x0, size=0x40000000,
            region_type=MemoryRegionType.DOMAIN_MEMORY,
            source_path="/domains/a", domain="domain_a"
        ))
        mm.add_region(MemoryRegion(
            start=0x80000000, size=0x10000000,
            region_type=MemoryRegionType.DOMAIN_MEMORY,
            source_path="/domains/b", domain="domain_b"
        ))

        domains = mm.get_domains()
        assert domains == {"domain_a", "domain_b"}

    def test_find_overlaps(self):
        """Test finding overlapping regions."""
        mm = MemoryMap()
        region1 = MemoryRegion(
            start=0x10000000, size=0x10000000,
            region_type=MemoryRegionType.RESERVED_MEMORY,
            source_path="/reserved-memory/r1"
        )
        region2 = MemoryRegion(
            start=0x18000000, size=0x10000000,
            region_type=MemoryRegionType.RESERVED_MEMORY,
            source_path="/reserved-memory/r2"
        )
        region3 = MemoryRegion(
            start=0x50000000, size=0x10000000,
            region_type=MemoryRegionType.RESERVED_MEMORY,
            source_path="/reserved-memory/r3"
        )
        mm.add_region(region1)
        mm.add_region(region2)
        mm.add_region(region3)

        overlaps = mm.find_overlaps()
        assert len(overlaps) == 1
        assert overlaps[0].region1 == region1
        assert overlaps[0].region2 == region2

    def test_find_overlaps_excludes_intentional(self):
        """Test that shared memory overlaps are excluded by default."""
        mm = MemoryMap()
        region1 = MemoryRegion(
            start=0x10000000, size=0x10000000,
            region_type=MemoryRegionType.RESERVED_MEMORY,
            source_path="/reserved-memory/r1",
            compatible=['shared-dma-pool']
        )
        region2 = MemoryRegion(
            start=0x18000000, size=0x10000000,
            region_type=MemoryRegionType.RESERVED_MEMORY,
            source_path="/reserved-memory/r2"
        )
        mm.add_region(region1)
        mm.add_region(region2)

        overlaps = mm.find_overlaps(include_intentional=False)
        assert len(overlaps) == 0

        overlaps_all = mm.find_overlaps(include_intentional=True)
        assert len(overlaps_all) == 1

    def test_find_containing_region(self):
        """Test finding region containing an address."""
        mm = MemoryMap()
        mm.add_region(MemoryRegion(
            start=0x0, size=0x40000000,
            region_type=MemoryRegionType.PHYSICAL_MEMORY,
            source_path="/memory@0"
        ))
        mm.add_region(MemoryRegion(
            start=0x80000000, size=0x40000000,
            region_type=MemoryRegionType.PHYSICAL_MEMORY,
            source_path="/memory@80000000"
        ))

        region = mm.find_containing_region(0x10000000)
        assert region is not None
        assert region.source_path == "/memory@0"

        region2 = mm.find_containing_region(0x90000000)
        assert region2 is not None
        assert region2.source_path == "/memory@80000000"

        region3 = mm.find_containing_region(0x50000000)
        assert region3 is None


class TestCollectMemoryRegions:
    """Tests for collect_memory_regions function."""

    def _create_basic_tree(self):
        """Create a basic tree with memory and reserved-memory nodes."""
        tree = LopperTree()
        root = tree['/']
        root + LopperProp(name='#address-cells', value=2)
        root + LopperProp(name='#size-cells', value=2)

        # Add physical memory node
        mem = LopperNode(-1, "/memory@0")
        mem + LopperProp(name='device_type', value="memory")
        mem + LopperProp(name='reg', value=[0x0, 0x0, 0x0, 0x80000000])  # 2GB
        tree.add(mem)

        # Add reserved-memory parent
        resmem = LopperNode(-1, "/reserved-memory")
        resmem + LopperProp(name='#address-cells', value=2)
        resmem + LopperProp(name='#size-cells', value=2)
        resmem + LopperProp(name='ranges', value=[])
        tree.add(resmem)

        # Add reserved-memory region
        res_region = LopperNode(-1, "/reserved-memory/buffer@10000000")
        res_region + LopperProp(name='reg', value=[0x0, 0x10000000, 0x0, 0x1000000])
        tree.add(res_region)

        tree.sync()
        return tree

    def test_collects_physical_memory(self):
        """Test that physical memory regions are collected."""
        tree = self._create_basic_tree()
        mm = collect_memory_regions(tree)

        phys_regions = mm.get_by_type(MemoryRegionType.PHYSICAL_MEMORY)
        assert len(phys_regions) == 1
        assert phys_regions[0].start == 0x0
        assert phys_regions[0].size == 0x80000000

    def test_collects_reserved_memory(self):
        """Test that reserved-memory regions are collected."""
        tree = self._create_basic_tree()
        mm = collect_memory_regions(tree)

        res_regions = mm.get_by_type(MemoryRegionType.RESERVED_MEMORY)
        assert len(res_regions) == 1
        assert res_regions[0].start == 0x10000000
        assert res_regions[0].size == 0x1000000

    def test_collects_domain_memory(self):
        """Test that domain memory is collected when domain_node is provided."""
        tree = self._create_basic_tree()

        # Add domain node
        domain = LopperNode(-1, "/domains/test_domain")
        domain + LopperProp(name='memory', value=[0x0, 0x0, 0x0, 0x40000000])
        tree.add(domain)
        tree.sync()

        mm = collect_memory_regions(tree, domain_node=domain)

        domain_regions = mm.get_by_type(MemoryRegionType.DOMAIN_MEMORY)
        assert len(domain_regions) == 1
        assert domain_regions[0].domain == "test_domain"
        assert domain_regions[0].size == 0x40000000


class TestCheckCellProperties:
    """Tests for check_cell_properties validation."""

    def test_valid_root_cells(self):
        """Test that valid cell properties pass."""
        tree = LopperTree()
        root = tree['/']
        root + LopperProp(name='#address-cells', value=2)
        root + LopperProp(name='#size-cells', value=2)
        tree.sync()

        results = check_cell_properties(tree)
        assert all(r.passed for r in results)

    def test_missing_address_cells(self):
        """Test detection of missing #address-cells."""
        tree = LopperTree()
        root = tree['/']
        root + LopperProp(name='#size-cells', value=2)
        tree.sync()

        results = check_cell_properties(tree)
        failed = [r for r in results if not r.passed]
        assert len(failed) == 1
        assert "address-cells" in failed[0].message

    def test_missing_size_cells(self):
        """Test detection of missing #size-cells."""
        tree = LopperTree()
        root = tree['/']
        root + LopperProp(name='#address-cells', value=2)
        tree.sync()

        results = check_cell_properties(tree)
        failed = [r for r in results if not r.passed]
        assert len(failed) == 1
        assert "size-cells" in failed[0].message


class TestCheckRegPropertyFormat:
    """Tests for check_reg_property_format validation."""

    def test_valid_reg_property(self):
        """Test that valid reg property passes."""
        tree = LopperTree()
        root = tree['/']
        root + LopperProp(name='#address-cells', value=2)
        root + LopperProp(name='#size-cells', value=2)

        mem = LopperNode(-1, "/memory@0")
        mem + LopperProp(name='reg', value=[0x0, 0x0, 0x0, 0x80000000])
        tree.add(mem)
        tree.sync()

        results = check_reg_property_format(tree)
        assert all(r.passed for r in results)

    def test_zero_size_region(self):
        """Test detection of zero-size memory region."""
        tree = LopperTree()
        root = tree['/']
        root + LopperProp(name='#address-cells', value=2)
        root + LopperProp(name='#size-cells', value=2)

        mem = LopperNode(-1, "/memory@0")
        mem + LopperProp(name='reg', value=[0x0, 0x0, 0x0, 0x0])  # Zero size
        tree.add(mem)
        tree.sync()

        results = check_reg_property_format(tree)
        failed = [r for r in results if not r.passed]
        assert len(failed) == 1
        assert "zero size" in failed[0].message


class TestCheckReservedMemoryOverlaps:
    """Tests for check_reserved_memory_overlaps validation."""

    def test_no_overlaps(self):
        """Test that non-overlapping regions pass."""
        tree = LopperTree()
        root = tree['/']
        root + LopperProp(name='#address-cells', value=2)
        root + LopperProp(name='#size-cells', value=2)

        resmem = LopperNode(-1, "/reserved-memory")
        resmem + LopperProp(name='#address-cells', value=2)
        resmem + LopperProp(name='#size-cells', value=2)
        tree.add(resmem)

        r1 = LopperNode(-1, "/reserved-memory/r1")
        r1 + LopperProp(name='reg', value=[0x0, 0x10000000, 0x0, 0x1000000])
        tree.add(r1)

        r2 = LopperNode(-1, "/reserved-memory/r2")
        r2 + LopperProp(name='reg', value=[0x0, 0x20000000, 0x0, 0x1000000])
        tree.add(r2)

        tree.sync()

        results = check_reserved_memory_overlaps(tree)
        assert all(r.passed for r in results)

    def test_overlapping_regions(self):
        """Test detection of overlapping reserved-memory regions."""
        tree = LopperTree()
        root = tree['/']
        root + LopperProp(name='#address-cells', value=2)
        root + LopperProp(name='#size-cells', value=2)

        resmem = LopperNode(-1, "/reserved-memory")
        resmem + LopperProp(name='#address-cells', value=2)
        resmem + LopperProp(name='#size-cells', value=2)
        tree.add(resmem)

        # Two overlapping regions
        r1 = LopperNode(-1, "/reserved-memory/r1")
        r1 + LopperProp(name='reg', value=[0x0, 0x10000000, 0x0, 0x10000000])
        tree.add(r1)

        r2 = LopperNode(-1, "/reserved-memory/r2")
        r2 + LopperProp(name='reg', value=[0x0, 0x18000000, 0x0, 0x10000000])
        tree.add(r2)

        tree.sync()

        results = check_reserved_memory_overlaps(tree)
        failed = [r for r in results if not r.passed]
        assert len(failed) == 1
        assert "overlap" in failed[0].message.lower()


class TestMemoryValidator:
    """Tests for MemoryValidator class."""

    def test_check_enabled(self):
        """Test warning flag checking."""
        validator = MemoryValidator(warnings=['memory_cells', 'memory_overlap'])
        assert validator.is_check_enabled('memory_cells')
        assert validator.is_check_enabled('memory_overlap')
        assert not validator.is_check_enabled('domain_overlap')

    def test_meta_flag_expansion(self):
        """Test that meta-flags expand to individual checks."""
        validator = MemoryValidator(warnings=['memory_all'])
        assert validator.is_check_enabled('memory_cells')
        assert validator.is_check_enabled('memory_reg')
        assert validator.is_check_enabled('memory_overlap')
        assert validator.is_check_enabled('domain_overlap')

    def test_run_early_phase(self):
        """Test running EARLY phase checks."""
        tree = LopperTree()
        root = tree['/']
        root + LopperProp(name='#address-cells', value=2)
        root + LopperProp(name='#size-cells', value=2)
        tree.sync()

        validator = MemoryValidator(warnings=['memory_cells'])
        results = validator.run_phase(ValidationPhase.EARLY, tree)

        assert len(results) > 0
        assert all(r.phase == ValidationPhase.EARLY for r in results)


class TestValidateMemory:
    """Tests for validate_memory convenience function."""

    def test_returns_error_count(self):
        """Test that validate_memory returns error count."""
        tree = LopperTree()
        root = tree['/']
        root + LopperProp(name='#address-cells', value=2)
        root + LopperProp(name='#size-cells', value=2)
        tree.sync()

        with patch('lopper.log._warning'):
            error_count = validate_memory(
                tree,
                phase=ValidationPhase.EARLY,
                warnings=['memory_cells']
            )
            assert error_count == 0

    def test_reports_with_werror(self):
        """Test that werror flag is respected."""
        tree = LopperTree()
        root = tree['/']
        # Missing #size-cells
        root + LopperProp(name='#address-cells', value=2)
        tree.sync()

        with patch('lopper.log._error') as mock_error:
            error_count = validate_memory(
                tree,
                phase=ValidationPhase.EARLY,
                warnings=['memory_cells'],
                werror=True
            )
            assert error_count > 0
            mock_error.assert_called()


class TestOverlapResult:
    """Tests for OverlapResult dataclass."""

    def test_overlap_end_property(self):
        """Test overlap_end property calculation."""
        r1 = MemoryRegion(
            start=0x10000000, size=0x10000000,
            region_type=MemoryRegionType.RESERVED_MEMORY,
            source_path="/r1"
        )
        r2 = MemoryRegion(
            start=0x18000000, size=0x10000000,
            region_type=MemoryRegionType.RESERVED_MEMORY,
            source_path="/r2"
        )
        result = OverlapResult(
            region1=r1,
            region2=r2,
            overlap_start=0x18000000,
            overlap_size=0x8000000
        )
        assert result.overlap_end == 0x20000000


class TestValidationResult:
    """Tests for ValidationResult dataclass."""

    def test_basic_creation(self):
        """Test creating a ValidationResult."""
        result = ValidationResult(
            check_name='memory_cells',
            phase=ValidationPhase.EARLY,
            passed=True,
            message="Cell properties valid"
        )
        assert result.check_name == 'memory_cells'
        assert result.phase == ValidationPhase.EARLY
        assert result.passed is True

    def test_with_details(self):
        """Test ValidationResult with details."""
        result = ValidationResult(
            check_name='memory_overlap',
            phase=ValidationPhase.POST_YAML,
            passed=False,
            message="Overlap detected",
            source_path="/reserved-memory/r1",
            details={'overlap_size': 0x1000000}
        )
        assert result.details['overlap_size'] == 0x1000000
