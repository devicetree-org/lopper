"""
Tests for lopper/audit/memviz.py - memory map visualization.

This module tests the ASCII memory map visualization:
- MemoryVisualizer: Main visualization class
- render_memory_map: Convenience function
"""

import pytest
import tempfile
import os

from lopper.tree import LopperTree, LopperNode, LopperProp
from lopper.audit.memory import (
    MemoryRegion,
    MemoryRegionType,
    MemoryMap,
    collect_memory_regions,
)
from lopper.audit.memviz import (
    MemoryVisualizer,
    render_memory_map,
)


class TestMemoryVisualizer:
    """Tests for MemoryVisualizer class."""

    def test_basic_initialization(self):
        """Test basic initialization with defaults."""
        viz = MemoryVisualizer()
        assert viz.width == 50
        assert viz.show_addresses is True

    def test_custom_width(self):
        """Test initialization with custom width."""
        viz = MemoryVisualizer(width=80)
        assert viz.width == 80

    def test_format_address(self):
        """Test address formatting."""
        viz = MemoryVisualizer()
        addr = viz._format_address(0x10000000)
        assert "0x" in addr
        assert "10000000" in addr

    def test_format_size_bytes(self):
        """Test size formatting for bytes."""
        viz = MemoryVisualizer()
        assert viz._format_size(512) == "512B"

    def test_format_size_kb(self):
        """Test size formatting for kilobytes."""
        viz = MemoryVisualizer()
        assert viz._format_size(4096) == "4.0KB"

    def test_format_size_mb(self):
        """Test size formatting for megabytes."""
        viz = MemoryVisualizer()
        result = viz._format_size(16 * 1024 * 1024)
        assert "16" in result
        assert "MB" in result

    def test_format_size_gb(self):
        """Test size formatting for gigabytes."""
        viz = MemoryVisualizer()
        result = viz._format_size(2 * 1024 * 1024 * 1024)
        assert "2" in result
        assert "GB" in result

    def test_render_empty_map(self):
        """Test rendering an empty memory map."""
        viz = MemoryVisualizer()
        mm = MemoryMap()
        result = viz.render_memory_map(mm, title="Empty")

        assert "Memory Map: Empty" in result
        assert "no memory regions" in result

    def test_render_single_region(self):
        """Test rendering a single memory region."""
        viz = MemoryVisualizer()
        mm = MemoryMap()
        mm.add_region(MemoryRegion(
            start=0x0,
            size=0x40000000,
            region_type=MemoryRegionType.PHYSICAL_MEMORY,
            source_path="/memory@0",
            label="memory@0"
        ))

        result = viz.render_memory_map(mm, title="Single")

        assert "Memory Map: Single" in result
        assert "memory@0" in result
        assert "=" in result  # Physical memory character

    def test_render_multiple_regions(self):
        """Test rendering multiple memory regions."""
        viz = MemoryVisualizer()
        mm = MemoryMap()
        mm.add_region(MemoryRegion(
            start=0x0,
            size=0x40000000,
            region_type=MemoryRegionType.PHYSICAL_MEMORY,
            source_path="/memory@0",
            label="memory@0"
        ))
        mm.add_region(MemoryRegion(
            start=0x10000000,
            size=0x1000000,
            region_type=MemoryRegionType.RESERVED_MEMORY,
            source_path="/reserved-memory/buffer",
            label="buffer"
        ))

        result = viz.render_memory_map(mm, title="Multiple")

        assert "memory@0" in result
        assert "buffer" in result
        assert "=" in result  # Physical memory
        assert "#" in result  # Reserved memory

    def test_render_with_overlaps(self):
        """Test rendering overlapping regions."""
        viz = MemoryVisualizer()
        mm = MemoryMap()
        mm.add_region(MemoryRegion(
            start=0x10000000,
            size=0x10000000,
            region_type=MemoryRegionType.RESERVED_MEMORY,
            source_path="/reserved-memory/r1",
            label="r1"
        ))
        mm.add_region(MemoryRegion(
            start=0x18000000,
            size=0x10000000,
            region_type=MemoryRegionType.RESERVED_MEMORY,
            source_path="/reserved-memory/r2",
            label="r2"
        ))

        result = viz.render_memory_map(mm, title="Overlapping", highlight_overlaps=True)

        assert "OVERLAPS DETECTED" in result
        assert "ERROR" in result

    def test_render_legend(self):
        """Test that legend is included."""
        viz = MemoryVisualizer()
        mm = MemoryMap()
        mm.add_region(MemoryRegion(
            start=0x0,
            size=0x40000000,
            region_type=MemoryRegionType.PHYSICAL_MEMORY,
            source_path="/memory@0"
        ))

        result = viz.render_memory_map(mm)

        assert "Legend:" in result
        assert "Physical Memory" in result
        assert "Reserved Memory" in result

    def test_render_summary(self):
        """Test rendering summary."""
        viz = MemoryVisualizer()
        mm = MemoryMap()
        mm.add_region(MemoryRegion(
            start=0x0,
            size=0x40000000,
            region_type=MemoryRegionType.PHYSICAL_MEMORY,
            source_path="/memory@0"
        ))
        mm.add_region(MemoryRegion(
            start=0x10000000,
            size=0x1000000,
            region_type=MemoryRegionType.RESERVED_MEMORY,
            source_path="/reserved-memory/r1"
        ))

        result = viz.render_summary(mm)

        assert "Memory Map Summary" in result
        assert "Total regions: 2" in result
        assert "PHYSICAL_MEMORY" in result
        assert "RESERVED_MEMORY" in result

    def test_render_summary_with_domains(self):
        """Test summary includes domain information."""
        viz = MemoryVisualizer()
        mm = MemoryMap()
        mm.add_region(MemoryRegion(
            start=0x0,
            size=0x40000000,
            region_type=MemoryRegionType.DOMAIN_MEMORY,
            source_path="/domains/linux",
            domain="linux"
        ))
        mm.add_region(MemoryRegion(
            start=0x80000000,
            size=0x10000000,
            region_type=MemoryRegionType.DOMAIN_MEMORY,
            source_path="/domains/baremetal",
            domain="baremetal"
        ))

        result = viz.render_summary(mm)

        assert "Domains: 2" in result
        assert "linux" in result
        assert "baremetal" in result


class TestRenderMemoryMap:
    """Tests for render_memory_map convenience function."""

    def _create_basic_tree(self):
        """Create a basic tree with memory nodes."""
        tree = LopperTree()
        root = tree['/']
        root + LopperProp(name='#address-cells', value=2)
        root + LopperProp(name='#size-cells', value=2)

        mem = LopperNode(-1, "/memory@0")
        mem + LopperProp(name='reg', value=[0x0, 0x0, 0x0, 0x80000000])
        tree.add(mem)

        resmem = LopperNode(-1, "/reserved-memory")
        resmem + LopperProp(name='#address-cells', value=2)
        resmem + LopperProp(name='#size-cells', value=2)
        tree.add(resmem)

        res = LopperNode(-1, "/reserved-memory/buffer@10000000")
        res + LopperProp(name='reg', value=[0x0, 0x10000000, 0x0, 0x1000000])
        tree.add(res)

        tree.sync()
        return tree

    def test_render_from_tree(self):
        """Test rendering memory map from a tree."""
        tree = self._create_basic_tree()
        result = render_memory_map(tree)

        assert "Memory Map: System" in result
        assert "memory@0" in result

    def test_render_with_title(self):
        """Test rendering with custom title."""
        tree = self._create_basic_tree()
        result = render_memory_map(tree, title="Custom Title")

        assert "Memory Map: Custom Title" in result

    def test_render_with_domain(self):
        """Test rendering with domain node."""
        tree = self._create_basic_tree()

        domain = LopperNode(-1, "/domains/test_domain")
        domain + LopperProp(name='memory', value=[0x0, 0x0, 0x0, 0x40000000])
        tree.add(domain)
        tree.sync()

        result = render_memory_map(tree, domain_node=domain)

        assert "test_domain" in result

    def test_render_to_file(self):
        """Test rendering to output file."""
        tree = self._create_basic_tree()

        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            output_path = f.name

        try:
            result = render_memory_map(tree, output_file=output_path)

            assert os.path.exists(output_path)
            with open(output_path, 'r') as f:
                file_content = f.read()
            assert file_content == result
        finally:
            if os.path.exists(output_path):
                os.unlink(output_path)

    def test_includes_summary(self):
        """Test that summary is included in output."""
        tree = self._create_basic_tree()
        result = render_memory_map(tree)

        assert "Memory Map Summary" in result
        assert "Total regions:" in result


class TestVisualizationIntegration:
    """Integration tests for visualization with real tree structures."""

    def test_visualize_with_overlapping_reserved_memory(self):
        """Test visualization with overlapping reserved-memory regions."""
        tree = LopperTree()
        root = tree['/']
        root + LopperProp(name='#address-cells', value=2)
        root + LopperProp(name='#size-cells', value=2)

        resmem = LopperNode(-1, "/reserved-memory")
        resmem + LopperProp(name='#address-cells', value=2)
        resmem + LopperProp(name='#size-cells', value=2)
        tree.add(resmem)

        # Overlapping regions
        r1 = LopperNode(-1, "/reserved-memory/region1")
        r1 + LopperProp(name='reg', value=[0x0, 0x10000000, 0x0, 0x10000000])
        tree.add(r1)

        r2 = LopperNode(-1, "/reserved-memory/region2")
        r2 + LopperProp(name='reg', value=[0x0, 0x18000000, 0x0, 0x10000000])
        tree.add(r2)

        tree.sync()

        result = render_memory_map(tree, title="Overlap Test")

        # Should highlight the overlap
        assert "OVERLAPS DETECTED" in result
        assert "region1" in result
        assert "region2" in result

    def test_visualize_domain_memory(self):
        """Test visualization of domain memory allocation."""
        tree = LopperTree()
        root = tree['/']
        root + LopperProp(name='#address-cells', value=2)
        root + LopperProp(name='#size-cells', value=2)

        # Physical memory
        mem = LopperNode(-1, "/memory@0")
        mem + LopperProp(name='reg', value=[0x0, 0x0, 0x1, 0x0])  # 4GB
        tree.add(mem)

        # Domains
        domains = LopperNode(-1, "/domains")
        tree.add(domains)

        d1 = LopperNode(-1, "/domains/linux")
        d1 + LopperProp(name='memory', value=[0x0, 0x0, 0x0, 0x80000000])  # First 2GB
        tree.add(d1)

        d2 = LopperNode(-1, "/domains/baremetal")
        d2 + LopperProp(name='memory', value=[0x0, 0x80000000, 0x0, 0x40000000])  # Next 1GB
        tree.add(d2)

        tree.sync()

        # Visualize Linux domain
        result = render_memory_map(tree, domain_node=d1, title="Linux Domain")
        assert "linux" in result.lower()

        # Visualize baremetal domain
        result = render_memory_map(tree, domain_node=d2, title="Baremetal Domain")
        assert "baremetal" in result.lower()


class TestVisualizerCharacters:
    """Tests for visualization character mapping."""

    def test_physical_memory_character(self):
        """Test physical memory uses '=' character."""
        viz = MemoryVisualizer()
        assert viz.TYPE_CHARS[MemoryRegionType.PHYSICAL_MEMORY] == '='

    def test_reserved_memory_character(self):
        """Test reserved memory uses '#' character."""
        viz = MemoryVisualizer()
        assert viz.TYPE_CHARS[MemoryRegionType.RESERVED_MEMORY] == '#'

    def test_domain_memory_character(self):
        """Test domain memory uses '-' character."""
        viz = MemoryVisualizer()
        assert viz.TYPE_CHARS[MemoryRegionType.DOMAIN_MEMORY] == '-'

    def test_overlap_character(self):
        """Test overlap uses 'X' character."""
        viz = MemoryVisualizer()
        assert viz.CHAR_OVERLAP == 'X'

    def test_carveout_character(self):
        """Test carveout uses '@' character."""
        viz = MemoryVisualizer()
        assert viz.TYPE_CHARS[MemoryRegionType.CARVEOUT] == '@'
