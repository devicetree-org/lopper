#/*
# * Copyright (c) 2024,2025,2026 Xilinx Inc. All rights reserved.
# *
# * Author:
# *       Bruce Ashfield <bruce.ashfield@xilinx.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

"""
Lopper audit memory visualization module

This module provides ASCII memory map visualization for debugging
and verification of device tree memory layouts.

Key components:
- MemoryVisualizer: Main class for rendering memory maps
- render_memory_map: Convenience function for quick visualization
"""

from typing import List, Optional, Set, Tuple
from .memory import (
    MemoryRegion,
    MemoryRegionType,
    MemoryMap,
    OverlapResult,
    collect_memory_regions,
)


class MemoryVisualizer:
    """ASCII memory map visualizer.

    This class renders memory maps as ASCII art for debugging and
    verification purposes. It supports multiple display modes and
    can highlight overlapping regions.
    """

    # Characters for different region types
    CHAR_PHYSICAL = '='
    CHAR_RESERVED = '#'
    CHAR_DOMAIN = '-'
    CHAR_OVERLAP = 'X'
    CHAR_EMPTY = '.'
    CHAR_CARVEOUT = '@'

    # Region type to character mapping
    TYPE_CHARS = {
        MemoryRegionType.PHYSICAL_MEMORY: CHAR_PHYSICAL,
        MemoryRegionType.RESERVED_MEMORY: CHAR_RESERVED,
        MemoryRegionType.DOMAIN_MEMORY: CHAR_DOMAIN,
        MemoryRegionType.CARVEOUT: CHAR_CARVEOUT,
        MemoryRegionType.DEVICE_MEMORY: '+',
    }

    def __init__(self, width: int = 50, show_addresses: bool = True):
        """Initialize the visualizer.

        Args:
            width: Width of the memory bar in characters (default 50)
            show_addresses: Whether to show start/end addresses
        """
        self.width = width
        self.show_addresses = show_addresses

    def _format_address(self, addr: int, width: int = 18) -> str:
        """Format an address as a hex string with consistent width.

        Args:
            addr: Address to format
            width: Total width of the formatted string

        Returns:
            Formatted hex address string
        """
        return f"0x{addr:016x}"[:width]

    def _format_size(self, size: int) -> str:
        """Format a size value with appropriate units.

        Args:
            size: Size in bytes

        Returns:
            Human-readable size string (e.g., "256MB")
        """
        if size >= 1024 * 1024 * 1024:
            return f"{size / (1024 * 1024 * 1024):.1f}GB"
        elif size >= 1024 * 1024:
            return f"{size / (1024 * 1024):.1f}MB"
        elif size >= 1024:
            return f"{size / 1024:.1f}KB"
        else:
            return f"{size}B"

    def _compute_bar(
        self,
        region: MemoryRegion,
        view_start: int,
        view_end: int,
        overlapping_ranges: Optional[Set[Tuple[int, int]]] = None
    ) -> str:
        """Compute the ASCII bar for a single region.

        Args:
            region: MemoryRegion to render
            view_start: Start address of the view window
            view_end: End address of the view window
            overlapping_ranges: Set of (start, end) tuples for overlapping areas

        Returns:
            String of width characters representing the region
        """
        view_size = view_end - view_start
        if view_size <= 0:
            return self.CHAR_EMPTY * self.width

        char = self.TYPE_CHARS.get(region.region_type, '?')
        bar = [self.CHAR_EMPTY] * self.width

        # Calculate the region's position in the bar
        if region.start >= view_end or region.end <= view_start:
            return ''.join(bar)

        # Clamp region to view window
        vis_start = max(region.start, view_start)
        vis_end = min(region.end, view_end)

        # Convert to bar positions
        start_pos = int((vis_start - view_start) / view_size * self.width)
        end_pos = int((vis_end - view_start) / view_size * self.width)

        # Ensure at least one character if region is in view
        if end_pos == start_pos and vis_start < vis_end:
            end_pos = start_pos + 1

        # Fill the bar
        for i in range(max(0, start_pos), min(self.width, end_pos)):
            bar[i] = char

        # Mark overlapping areas
        if overlapping_ranges:
            for overlap_start, overlap_end in overlapping_ranges:
                if overlap_start >= view_end or overlap_end <= view_start:
                    continue

                # Clamp overlap to view window
                vis_overlap_start = max(overlap_start, view_start)
                vis_overlap_end = min(overlap_end, view_end)

                # Convert to bar positions
                o_start_pos = int((vis_overlap_start - view_start) / view_size * self.width)
                o_end_pos = int((vis_overlap_end - view_start) / view_size * self.width)

                # Check if this overlap affects this region
                if region.start < overlap_end and overlap_start < region.end:
                    for i in range(max(0, o_start_pos), min(self.width, o_end_pos)):
                        if bar[i] != self.CHAR_EMPTY:
                            bar[i] = self.CHAR_OVERLAP

        return ''.join(bar)

    def _get_overlapping_ranges(
        self,
        memory_map: MemoryMap,
        include_intentional: bool = False
    ) -> Set[Tuple[int, int]]:
        """Find all overlapping address ranges.

        Args:
            memory_map: MemoryMap to analyze
            include_intentional: If True, include intentional overlaps
                               (shared memory, reserved inside physical)

        Returns:
            Set of (start, end) tuples for overlapping ranges
        """
        overlapping = set()
        overlaps = memory_map.find_overlaps(include_intentional=include_intentional)

        for overlap in overlaps:
            overlapping.add((overlap.overlap_start, overlap.overlap_end))

        return overlapping

    def render_memory_map(
        self,
        memory_map: MemoryMap,
        title: Optional[str] = None,
        highlight_overlaps: bool = True,
        sort_by_address: bool = True
    ) -> str:
        """Render a complete memory map visualization.

        Args:
            memory_map: MemoryMap to render
            title: Optional title for the visualization
            highlight_overlaps: Whether to highlight overlapping regions
            sort_by_address: Whether to sort regions by start address

        Returns:
            Multi-line string containing the ASCII visualization
        """
        lines = []

        # Title
        header_width = 80
        if title:
            lines.append("=" * header_width)
            centered_title = f"Memory Map: {title}".center(header_width)
            lines.append(centered_title)
            lines.append("=" * header_width)
            lines.append("")

        if not memory_map.regions:
            lines.append("(no memory regions)")
            return '\n'.join(lines)

        # Calculate view window (union of all regions with padding)
        all_starts = [r.start for r in memory_map.regions]
        all_ends = [r.end for r in memory_map.regions]
        view_start = min(all_starts)
        view_end = max(all_ends)

        # Add 5% padding
        view_range = view_end - view_start
        if view_range > 0:
            padding = view_range // 20
            view_start = max(0, view_start - padding)
            view_end = view_end + padding

        # Get overlapping ranges
        overlapping_ranges = None
        if highlight_overlaps:
            overlapping_ranges = self._get_overlapping_ranges(memory_map)

        # Sort regions
        regions = memory_map.regions
        if sort_by_address:
            regions = sorted(regions, key=lambda r: r.start)

        # Render each region
        for region in regions:
            bar = self._compute_bar(region, view_start, view_end, overlapping_ranges)

            # Format the line
            start_addr = self._format_address(region.start)
            end_addr = self._format_address(region.end)
            label = region.label or region.source_path.split('/')[-1]

            # Truncate label if too long
            if len(label) > 25:
                label = label[:22] + "..."

            if self.show_addresses:
                line = f"{start_addr}  [{bar}]  {end_addr}  {label}"
            else:
                line = f"[{bar}]  {label}"

            lines.append(line)

        # Add legend
        lines.append("")
        lines.append("Legend:")
        lines.append(f"  {self.CHAR_PHYSICAL} Physical Memory    "
                    f"{self.CHAR_RESERVED} Reserved Memory    "
                    f"{self.CHAR_DOMAIN} Domain Memory")
        lines.append(f"  {self.CHAR_CARVEOUT} Carveout           "
                    f"{self.CHAR_OVERLAP} Overlap (ERROR)    "
                    f"{self.CHAR_EMPTY} Empty")

        # Add overlap report if any
        if overlapping_ranges and highlight_overlaps:
            overlaps = memory_map.find_overlaps(include_intentional=False)
            if overlaps:
                lines.append("")
                lines.append("OVERLAPS DETECTED:")
                for i, overlap in enumerate(overlaps, 1):
                    size_str = self._format_size(overlap.overlap_size)
                    r1_label = overlap.region1.label or overlap.region1.source_path.split('/')[-1]
                    r2_label = overlap.region2.label or overlap.region2.source_path.split('/')[-1]
                    lines.append(
                        f"  {i}. [ERROR] {r1_label} overlaps {r2_label} "
                        f"at {hex(overlap.overlap_start)} ({size_str})"
                    )

        return '\n'.join(lines)

    def render_cpu_view(
        self,
        memory_map: MemoryMap,
        cpu_cluster_path: str,
        tree
    ) -> str:
        """Render memory map from CPU's perspective (using address-map).

        This renders the memory as seen by a specific CPU cluster,
        translating addresses through the address-map property.

        Args:
            memory_map: MemoryMap to render
            cpu_cluster_path: Path to the CPU cluster node
            tree: LopperTree containing the nodes

        Returns:
            Multi-line string containing the CPU-view visualization
        """
        lines = []
        header_width = 80

        lines.append("=" * header_width)
        lines.append(f"CPU Memory View: {cpu_cluster_path}".center(header_width))
        lines.append("=" * header_width)
        lines.append("")

        # Try to get address-map from the CPU cluster
        try:
            cpu_node = tree[cpu_cluster_path]
        except:
            lines.append(f"(cannot find CPU cluster node: {cpu_cluster_path})")
            return '\n'.join(lines)

        try:
            addr_map = cpu_node['address-map'].value
            if addr_map and addr_map != ['']:
                lines.append(f"Address map found with {len(addr_map)} cells")
                lines.append("(Address translation visualization not yet implemented)")
            else:
                lines.append("(no address-map property, showing physical view)")
        except:
            lines.append("(no address-map property, showing physical view)")

        # For now, just render the physical view
        lines.append("")
        lines.append(self.render_memory_map(
            memory_map,
            title=f"{cpu_cluster_path} (physical)",
            highlight_overlaps=True
        ))

        return '\n'.join(lines)

    def render_summary(self, memory_map: MemoryMap) -> str:
        """Render a summary of the memory map.

        Args:
            memory_map: MemoryMap to summarize

        Returns:
            Multi-line summary string
        """
        lines = []

        # Count by type
        type_counts = {}
        type_sizes = {}
        for region in memory_map.regions:
            rtype = region.region_type.name
            type_counts[rtype] = type_counts.get(rtype, 0) + 1
            type_sizes[rtype] = type_sizes.get(rtype, 0) + region.size

        lines.append("Memory Map Summary")
        lines.append("-" * 40)
        lines.append(f"Total regions: {len(memory_map.regions)}")
        lines.append("")

        for rtype in sorted(type_counts.keys()):
            count = type_counts[rtype]
            size = type_sizes[rtype]
            lines.append(f"  {rtype}: {count} regions, {self._format_size(size)}")

        # Domains
        domains = memory_map.get_domains()
        if domains:
            lines.append("")
            lines.append(f"Domains: {len(domains)}")
            for domain in sorted(domains):
                domain_regions = memory_map.get_by_domain(domain)
                total_size = sum(r.size for r in domain_regions)
                lines.append(f"  {domain}: {len(domain_regions)} regions, "
                           f"{self._format_size(total_size)}")

        # Overlaps
        overlaps = memory_map.find_overlaps(include_intentional=False)
        if overlaps:
            lines.append("")
            lines.append(f"WARNINGS: {len(overlaps)} overlap(s) detected")

        return '\n'.join(lines)


def render_memory_map(
    tree,
    domain_node=None,
    title: Optional[str] = None,
    output_file: Optional[str] = None,
    width: int = 50
) -> str:
    """Convenience function to render a memory map.

    This is the main entry point for memory visualization from
    the command line or pipeline.

    Args:
        tree: LopperTree to visualize
        domain_node: Optional domain node to scope the visualization
        title: Optional title for the visualization
        output_file: Optional file path to write output
        width: Width of the memory bar (default 50)

    Returns:
        The rendered memory map as a string
    """
    # Collect memory regions
    memory_map = collect_memory_regions(tree, domain_node)

    # Determine title
    if title is None:
        if domain_node:
            title = domain_node.name
        else:
            title = "System"

    # Create visualizer and render
    viz = MemoryVisualizer(width=width)
    result = viz.render_memory_map(memory_map, title=title, highlight_overlaps=True)

    # Add summary
    result += "\n\n"
    result += viz.render_summary(memory_map)

    # Write to file if requested
    if output_file:
        with open(output_file, 'w') as f:
            f.write(result)

    return result
