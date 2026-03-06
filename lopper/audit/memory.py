#/*
# * Copyright (c) 2024,2025,2026 Xilinx Inc. All rights reserved.
# *
# * Author:
# *       Bruce Ashfield <bruce.ashfield@xilinx.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

"""
Lopper audit memory module

This module provides memory region data structures, validation checks,
and overlap detection for device tree memory analysis.

Key components:
- MemoryRegion: Dataclass representing a memory region
- MemoryMap: Collection of regions with analysis capabilities
- Validation functions: check_* functions for different checks
- MemoryValidator: Orchestrator for running phased validations
"""

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import List, Optional, Tuple, Set, Dict
import lopper.log

# Import base classes - ValidationPhase and ValidationResult come from base
from .base import (
    ValidationPhase,
    ValidationResult,
    BaseValidator,
    ValidatorRegistry,
)


class MemoryRegionType(Enum):
    """Types of memory regions in a device tree."""
    PHYSICAL_MEMORY = auto()      # /memory@* nodes
    RESERVED_MEMORY = auto()      # /reserved-memory/* nodes
    DOMAIN_MEMORY = auto()        # Domain's memory property ranges
    CARVEOUT = auto()             # OpenAMP carveout regions
    DEVICE_MEMORY = auto()        # Device reg property ranges


@dataclass
class MemoryRegion:
    """Represents a memory region in the device tree.

    Attributes:
        start: Starting address of the region
        size: Size of the region in bytes
        region_type: Type of memory region (physical, reserved, etc.)
        source_path: Device tree path of the node defining this region
        domain: Name of the domain this region belongs to (if applicable)
        label: Optional human-readable label for the region
        compatible: Compatible string(s) from the node (for shared memory detection)
    """
    start: int
    size: int
    region_type: MemoryRegionType
    source_path: str
    domain: Optional[str] = None
    label: Optional[str] = None
    compatible: Optional[List[str]] = None

    @property
    def end(self) -> int:
        """Return the end address (exclusive) of this region."""
        return self.start + self.size

    def overlaps(self, other: 'MemoryRegion') -> bool:
        """Check if this region overlaps with another region.

        Two regions overlap if they share any address space.
        Adjacent regions (where one ends exactly where another starts)
        do NOT overlap.

        Args:
            other: Another MemoryRegion to check against

        Returns:
            True if the regions overlap, False otherwise
        """
        return self.start < other.end and other.start < self.end

    def contains(self, other: 'MemoryRegion') -> bool:
        """Check if this region fully contains another region.

        Args:
            other: Another MemoryRegion to check

        Returns:
            True if other is fully contained within this region
        """
        return self.start <= other.start and self.end >= other.end

    def overlap_size(self, other: 'MemoryRegion') -> int:
        """Calculate the size of the overlap between two regions.

        Args:
            other: Another MemoryRegion

        Returns:
            Size of overlap in bytes, or 0 if no overlap
        """
        if not self.overlaps(other):
            return 0
        overlap_start = max(self.start, other.start)
        overlap_end = min(self.end, other.end)
        return overlap_end - overlap_start

    def is_shared_memory(self) -> bool:
        """Check if this region is marked as shared memory.

        Shared memory regions (like shared-dma-pool) are allowed to overlap
        across domains.

        Returns:
            True if the region is a shared memory region
        """
        if not self.compatible:
            return False
        shared_compat = [
            'shared-dma-pool',
            'restricted-dma-pool',
        ]
        for compat in self.compatible:
            if compat in shared_compat:
                return True
        return False


@dataclass
class OverlapResult:
    """Result of an overlap detection between two memory regions.

    Attributes:
        region1: First overlapping region
        region2: Second overlapping region
        overlap_start: Start address of the overlap
        overlap_size: Size of the overlap in bytes
        is_intentional: True if this overlap is intentional (shared memory)
    """
    region1: MemoryRegion
    region2: MemoryRegion
    overlap_start: int
    overlap_size: int
    is_intentional: bool = False

    @property
    def overlap_end(self) -> int:
        """Return the end address of the overlap."""
        return self.overlap_start + self.overlap_size

# ValidationResult is imported from base module


class MemoryMap:
    """Collection of memory regions with analysis capabilities.

    This class collects memory regions from a device tree and provides
    methods for querying, filtering, and detecting overlaps.
    """

    def __init__(self):
        """Initialize an empty memory map."""
        self.regions: List[MemoryRegion] = []
        self._domains: Set[str] = set()

    def add_region(self, region: MemoryRegion) -> None:
        """Add a memory region to the map.

        Args:
            region: MemoryRegion to add
        """
        self.regions.append(region)
        if region.domain:
            self._domains.add(region.domain)

    def get_by_domain(self, domain: str) -> List[MemoryRegion]:
        """Get all regions belonging to a specific domain.

        Args:
            domain: Domain name to filter by

        Returns:
            List of MemoryRegion objects for the specified domain
        """
        return [r for r in self.regions if r.domain == domain]

    def get_by_type(self, region_type: MemoryRegionType) -> List[MemoryRegion]:
        """Get all regions of a specific type.

        Args:
            region_type: MemoryRegionType to filter by

        Returns:
            List of MemoryRegion objects of the specified type
        """
        return [r for r in self.regions if r.region_type == region_type]

    def get_domains(self) -> Set[str]:
        """Get the set of all domain names in this memory map.

        Returns:
            Set of domain name strings
        """
        return self._domains.copy()

    def find_overlaps(
        self,
        region_types: Optional[List[MemoryRegionType]] = None,
        within_domain: Optional[str] = None,
        cross_domain: bool = False,
        include_intentional: bool = False,
        same_type_only: bool = False
    ) -> List[OverlapResult]:
        """Find overlapping memory regions.

        Args:
            region_types: Optional list of region types to check. If None,
                         check all region types.
            within_domain: If specified, only check overlaps within this domain
            cross_domain: If True, check overlaps between different domains
            include_intentional: If True, include intentional overlaps (shared memory)
            same_type_only: If True, only check overlaps between regions of the
                           same type (e.g., two RESERVED_MEMORY regions)

        Returns:
            List of OverlapResult objects describing each overlap
        """
        overlaps = []

        # Filter regions based on criteria
        regions = self.regions
        if region_types:
            regions = [r for r in regions if r.region_type in region_types]
        if within_domain:
            regions = [r for r in regions if r.domain == within_domain]

        # Check all pairs
        for i, r1 in enumerate(regions):
            for r2 in regions[i + 1:]:
                # Skip if checking cross-domain and regions are in same domain
                if cross_domain and r1.domain == r2.domain:
                    continue
                # Skip if not checking cross-domain and regions are in different domains
                if not cross_domain and within_domain is None:
                    if r1.domain != r2.domain and r1.domain is not None and r2.domain is not None:
                        continue

                # Skip if same_type_only and regions are different types
                if same_type_only and r1.region_type != r2.region_type:
                    continue

                if r1.overlaps(r2):
                    is_intentional = r1.is_shared_memory() or r2.is_shared_memory()

                    # Also consider reserved memory inside physical memory as intentional
                    if not is_intentional:
                        types = {r1.region_type, r2.region_type}
                        if types == {MemoryRegionType.PHYSICAL_MEMORY, MemoryRegionType.RESERVED_MEMORY}:
                            is_intentional = True
                        elif types == {MemoryRegionType.PHYSICAL_MEMORY, MemoryRegionType.DOMAIN_MEMORY}:
                            is_intentional = True
                        elif types == {MemoryRegionType.DOMAIN_MEMORY, MemoryRegionType.RESERVED_MEMORY}:
                            is_intentional = True

                    if not include_intentional and is_intentional:
                        continue

                    overlap_start = max(r1.start, r2.start)
                    overlap_size = r1.overlap_size(r2)

                    overlaps.append(OverlapResult(
                        region1=r1,
                        region2=r2,
                        overlap_start=overlap_start,
                        overlap_size=overlap_size,
                        is_intentional=is_intentional
                    ))

        return overlaps

    def find_containing_region(
        self,
        address: int,
        region_type: Optional[MemoryRegionType] = None,
        domain: Optional[str] = None
    ) -> Optional[MemoryRegion]:
        """Find a region that contains the given address.

        Args:
            address: Memory address to look up
            region_type: Optional filter by region type
            domain: Optional filter by domain

        Returns:
            MemoryRegion containing the address, or None
        """
        for region in self.regions:
            if region_type and region.region_type != region_type:
                continue
            if domain and region.domain != domain:
                continue
            if region.start <= address < region.end:
                return region
        return None

    def __len__(self) -> int:
        """Return the number of regions in the map."""
        return len(self.regions)

    def __iter__(self):
        """Iterate over all regions."""
        return iter(self.regions)


def _cell_value_get(cells, cell_size, start_idx=0):
    """Extract a multi-cell value from a property.

    This is a local copy of the function from core.py to avoid
    circular imports.

    Args:
        cells: List of cell values
        cell_size: Number of cells to combine (1 or 2)
        start_idx: Starting index in cells list

    Returns:
        Tuple of (combined_value, used_cells_list)
    """
    used_cells = []
    if cell_size == 2:
        value = (cells[start_idx] << 32) | cells[start_idx + 1]
        used_cells.append(cells[start_idx])
        used_cells.append(cells[start_idx + 1])
    else:
        value = cells[start_idx]
        used_cells.append(cells[start_idx])

    return value, used_cells


def _get_node_compatible(node) -> Optional[List[str]]:
    """Get the compatible property from a node as a list.

    Args:
        node: LopperNode to get compatible from

    Returns:
        List of compatible strings, or None if not present
    """
    try:
        compat = node['compatible'].value
        if not compat or compat == ['']:
            return None
        if isinstance(compat, str):
            return [compat]
        return compat
    except:
        return None


def _get_cell_sizes(tree, node=None):
    """Get #address-cells and #size-cells for a node or root.

    Args:
        tree: LopperTree
        node: Optional node to get cell sizes from parent of. If None, use root.

    Returns:
        Tuple of (address_cells, size_cells)
    """
    try:
        root_ac = tree['/']['#address-cells'][0]
    except:
        root_ac = 2
    try:
        root_sc = tree['/']['#size-cells'][0]
    except:
        root_sc = 2

    if node is None:
        return root_ac, root_sc

    # Try to get from parent node
    try:
        parent = node.parent
        if parent:
            try:
                ac = parent['#address-cells'][0]
            except:
                ac = root_ac
            try:
                sc = parent['#size-cells'][0]
            except:
                sc = root_sc
            return ac, sc
    except:
        pass

    return root_ac, root_sc


def collect_memory_regions(tree, domain_node=None) -> MemoryMap:
    """Collect all memory regions from a device tree.

    This function walks the tree and collects memory regions from:
    - /memory@* nodes (physical memory)
    - /reserved-memory/* nodes (reserved memory)
    - Domain memory property (if domain_node is specified)

    Args:
        tree: LopperTree to collect from
        domain_node: Optional domain node to scope collection to

    Returns:
        MemoryMap containing all collected regions
    """
    memory_map = MemoryMap()
    root_ac, root_sc = _get_cell_sizes(tree)

    # Collect physical memory regions from /memory@* nodes
    for node in tree:
        if not node.abs_path.startswith('/memory'):
            continue
        # Check it's a top-level memory node
        if node.abs_path.count('/') > 1:
            continue

        try:
            reg_val = node['reg'].value
            if not reg_val or reg_val == ['']:
                continue
        except:
            continue

        ac, sc = _get_cell_sizes(tree, node)
        cell_size = ac + sc

        for i in range(0, len(reg_val), cell_size):
            chunk = reg_val[i:i + cell_size]
            if len(chunk) < cell_size:
                break
            start, _ = _cell_value_get(chunk, ac)
            size, _ = _cell_value_get(chunk, sc, ac)

            if size > 0:
                region = MemoryRegion(
                    start=start,
                    size=size,
                    region_type=MemoryRegionType.PHYSICAL_MEMORY,
                    source_path=node.abs_path,
                    label=node.name
                )
                memory_map.add_region(region)

    # Collect reserved-memory regions
    try:
        resmem_parent = tree['/reserved-memory']
    except:
        resmem_parent = None

    if resmem_parent:
        try:
            resmem_ac = resmem_parent['#address-cells'][0]
        except:
            resmem_ac = root_ac
        try:
            resmem_sc = resmem_parent['#size-cells'][0]
        except:
            resmem_sc = root_sc

        for node in resmem_parent.subnodes(children_only=True):
            try:
                reg_val = node['reg'].value
                if not reg_val or reg_val == ['']:
                    continue
            except:
                continue

            cell_size = resmem_ac + resmem_sc
            compatible = _get_node_compatible(node)

            for i in range(0, len(reg_val), cell_size):
                chunk = reg_val[i:i + cell_size]
                if len(chunk) < cell_size:
                    break
                start, _ = _cell_value_get(chunk, resmem_ac)
                size, _ = _cell_value_get(chunk, resmem_sc, resmem_ac)

                if size > 0:
                    region = MemoryRegion(
                        start=start,
                        size=size,
                        region_type=MemoryRegionType.RESERVED_MEMORY,
                        source_path=node.abs_path,
                        label=node.name,
                        compatible=compatible
                    )
                    memory_map.add_region(region)

    # Collect domain memory if specified
    if domain_node:
        domain_name = domain_node.name
        try:
            mem_prop = domain_node['memory'].value
            if mem_prop and mem_prop != ['']:
                cell_size = root_ac + root_sc
                for i in range(0, len(mem_prop), cell_size):
                    chunk = mem_prop[i:i + cell_size]
                    if len(chunk) < cell_size:
                        break
                    start, _ = _cell_value_get(chunk, root_ac)
                    size, _ = _cell_value_get(chunk, root_sc, root_ac)

                    if size > 0:
                        region = MemoryRegion(
                            start=start,
                            size=size,
                            region_type=MemoryRegionType.DOMAIN_MEMORY,
                            source_path=domain_node.abs_path,
                            domain=domain_name,
                            label=f"{domain_name}_mem"
                        )
                        memory_map.add_region(region)
        except:
            pass

    return memory_map


# =============================================================================
# Validation Check Functions
# =============================================================================

def check_cell_properties(tree) -> List[ValidationResult]:
    """Validate #address-cells and #size-cells properties.

    This EARLY phase check ensures that cell size properties are present
    and have valid values (typically 1 or 2).

    Args:
        tree: LopperTree to validate

    Returns:
        List of ValidationResult objects
    """
    results = []

    # Check root node
    try:
        root = tree['/']
    except:
        results.append(ValidationResult(
            check_name='memory_cells',
            phase=ValidationPhase.EARLY,
            passed=False,
            message="Cannot find root node",
        ))
        return results

    # Check #address-cells on root
    try:
        ac = root['#address-cells'][0]
        if ac not in [1, 2]:
            results.append(ValidationResult(
                check_name='memory_cells',
                phase=ValidationPhase.EARLY,
                passed=False,
                message=f"Unusual #address-cells value {ac} (expected 1 or 2)",
                source_path='/',
            ))
    except:
        results.append(ValidationResult(
            check_name='memory_cells',
            phase=ValidationPhase.EARLY,
            passed=False,
            message="Missing #address-cells on root node",
            source_path='/',
        ))

    # Check #size-cells on root
    try:
        sc = root['#size-cells'][0]
        if sc not in [1, 2]:
            results.append(ValidationResult(
                check_name='memory_cells',
                phase=ValidationPhase.EARLY,
                passed=False,
                message=f"Unusual #size-cells value {sc} (expected 1 or 2)",
                source_path='/',
            ))
    except:
        results.append(ValidationResult(
            check_name='memory_cells',
            phase=ValidationPhase.EARLY,
            passed=False,
            message="Missing #size-cells on root node",
            source_path='/',
        ))

    # Check reserved-memory node if present
    try:
        resmem = tree['/reserved-memory']
        try:
            _ = resmem['#address-cells'][0]
        except:
            results.append(ValidationResult(
                check_name='memory_cells',
                phase=ValidationPhase.EARLY,
                passed=False,
                message="Missing #address-cells on /reserved-memory",
                source_path='/reserved-memory',
            ))
        try:
            _ = resmem['#size-cells'][0]
        except:
            results.append(ValidationResult(
                check_name='memory_cells',
                phase=ValidationPhase.EARLY,
                passed=False,
                message="Missing #size-cells on /reserved-memory",
                source_path='/reserved-memory',
            ))
    except:
        # reserved-memory not present is fine
        pass

    if not results:
        results.append(ValidationResult(
            check_name='memory_cells',
            phase=ValidationPhase.EARLY,
            passed=True,
            message="Cell properties valid",
        ))

    return results


def check_reg_property_format(tree) -> List[ValidationResult]:
    """Validate reg property format on memory nodes.

    This EARLY phase check ensures that reg properties have correct
    format: proper cell count, non-zero sizes, valid addresses.

    Args:
        tree: LopperTree to validate

    Returns:
        List of ValidationResult objects
    """
    results = []
    root_ac, root_sc = _get_cell_sizes(tree)

    # Check /memory@* nodes
    for node in tree:
        if not node.abs_path.startswith('/memory'):
            continue
        if node.abs_path.count('/') > 1:
            continue

        try:
            reg_val = node['reg'].value
        except:
            results.append(ValidationResult(
                check_name='memory_reg',
                phase=ValidationPhase.EARLY,
                passed=False,
                message=f"Memory node missing reg property",
                source_path=node.abs_path,
            ))
            continue

        if not reg_val or reg_val == ['']:
            results.append(ValidationResult(
                check_name='memory_reg',
                phase=ValidationPhase.EARLY,
                passed=False,
                message=f"Memory node has empty reg property",
                source_path=node.abs_path,
            ))
            continue

        ac, sc = _get_cell_sizes(tree, node)
        cell_size = ac + sc

        if len(reg_val) % cell_size != 0:
            results.append(ValidationResult(
                check_name='memory_reg',
                phase=ValidationPhase.EARLY,
                passed=False,
                message=(f"reg property has {len(reg_val)} cells, "
                        f"expected multiple of {cell_size}"),
                source_path=node.abs_path,
            ))
            continue

        # Check each entry
        for i in range(0, len(reg_val), cell_size):
            chunk = reg_val[i:i + cell_size]
            start, _ = _cell_value_get(chunk, ac)
            size, _ = _cell_value_get(chunk, sc, ac)

            if size == 0:
                results.append(ValidationResult(
                    check_name='memory_reg',
                    phase=ValidationPhase.EARLY,
                    passed=False,
                    message=f"Memory region has zero size at address {hex(start)}",
                    source_path=node.abs_path,
                ))

    # Check reserved-memory nodes
    try:
        resmem_parent = tree['/reserved-memory']
    except:
        resmem_parent = None

    if resmem_parent:
        try:
            resmem_ac = resmem_parent['#address-cells'][0]
        except:
            resmem_ac = root_ac
        try:
            resmem_sc = resmem_parent['#size-cells'][0]
        except:
            resmem_sc = root_sc

        for node in resmem_parent.subnodes(children_only=True):
            try:
                reg_val = node['reg'].value
            except:
                # Some reserved-memory nodes use 'size' instead of 'reg'
                continue

            if not reg_val or reg_val == ['']:
                continue

            cell_size = resmem_ac + resmem_sc

            if len(reg_val) % cell_size != 0:
                results.append(ValidationResult(
                    check_name='memory_reg',
                    phase=ValidationPhase.EARLY,
                    passed=False,
                    message=(f"reg property has {len(reg_val)} cells, "
                            f"expected multiple of {cell_size}"),
                    source_path=node.abs_path,
                ))
                continue

            for i in range(0, len(reg_val), cell_size):
                chunk = reg_val[i:i + cell_size]
                start, _ = _cell_value_get(chunk, resmem_ac)
                size, _ = _cell_value_get(chunk, resmem_sc, resmem_ac)

                if size == 0:
                    results.append(ValidationResult(
                        check_name='memory_reg',
                        phase=ValidationPhase.EARLY,
                        passed=False,
                        message=f"Reserved-memory region has zero size at address {hex(start)}",
                        source_path=node.abs_path,
                    ))

    if not results:
        results.append(ValidationResult(
            check_name='memory_reg',
            phase=ValidationPhase.EARLY,
            passed=True,
            message="Reg properties valid",
        ))

    return results


def check_reserved_memory_overlaps(tree) -> List[ValidationResult]:
    """Detect overlapping reserved-memory regions.

    This POST_YAML phase check finds reserved-memory regions that overlap.
    Shared memory pools (with shared-dma-pool compatible) are allowed to
    overlap and are flagged as intentional.

    Args:
        tree: LopperTree to validate

    Returns:
        List of ValidationResult objects
    """
    results = []

    # Collect regions
    memory_map = collect_memory_regions(tree)
    reserved_regions = memory_map.get_by_type(MemoryRegionType.RESERVED_MEMORY)

    if len(reserved_regions) < 2:
        results.append(ValidationResult(
            check_name='memory_overlap',
            phase=ValidationPhase.POST_YAML,
            passed=True,
            message="Fewer than 2 reserved-memory regions, no overlap possible",
        ))
        return results

    # Find overlaps (excluding intentional)
    overlaps = memory_map.find_overlaps(
        region_types=[MemoryRegionType.RESERVED_MEMORY],
        include_intentional=False
    )

    for overlap in overlaps:
        results.append(ValidationResult(
            check_name='memory_overlap',
            phase=ValidationPhase.POST_YAML,
            passed=False,
            message=(f"Reserved-memory overlap: {overlap.region1.source_path} "
                    f"and {overlap.region2.source_path} overlap at "
                    f"{hex(overlap.overlap_start)} ({overlap.overlap_size} bytes)"),
            source_path=overlap.region1.source_path,
            details={
                'region1': overlap.region1.source_path,
                'region2': overlap.region2.source_path,
                'overlap_start': overlap.overlap_start,
                'overlap_size': overlap.overlap_size,
            }
        ))

    if not overlaps:
        results.append(ValidationResult(
            check_name='memory_overlap',
            phase=ValidationPhase.POST_YAML,
            passed=True,
            message="No reserved-memory overlaps detected",
        ))

    return results


def check_domain_memory_overlaps(tree, domain_node) -> List[ValidationResult]:
    """Detect overlapping memory regions within a single domain.

    This POST_PROCESSING phase check finds memory regions within a domain
    that overlap unexpectedly.

    Args:
        tree: LopperTree to validate
        domain_node: Domain node to check

    Returns:
        List of ValidationResult objects
    """
    results = []
    domain_name = domain_node.name

    # Collect regions for this domain
    memory_map = collect_memory_regions(tree, domain_node)

    # Get domain memory and reserved memory referenced by domain
    domain_regions = memory_map.get_by_domain(domain_name)

    if len(domain_regions) < 2:
        results.append(ValidationResult(
            check_name='domain_overlap',
            phase=ValidationPhase.POST_PROCESSING,
            passed=True,
            message=f"Domain {domain_name}: fewer than 2 memory regions",
        ))
        return results

    # Check for overlaps within domain memory ranges
    for i, r1 in enumerate(domain_regions):
        for r2 in domain_regions[i + 1:]:
            if r1.overlaps(r2):
                is_intentional = r1.is_shared_memory() or r2.is_shared_memory()
                if not is_intentional:
                    results.append(ValidationResult(
                        check_name='domain_overlap',
                        phase=ValidationPhase.POST_PROCESSING,
                        passed=False,
                        message=(f"Domain {domain_name}: memory overlap between "
                                f"{r1.source_path} and {r2.source_path}"),
                        source_path=domain_node.abs_path,
                        details={
                            'region1': r1.source_path,
                            'region2': r2.source_path,
                            'overlap_start': max(r1.start, r2.start),
                            'overlap_size': r1.overlap_size(r2),
                        }
                    ))

    if not any(not r.passed for r in results):
        results.append(ValidationResult(
            check_name='domain_overlap',
            phase=ValidationPhase.POST_PROCESSING,
            passed=True,
            message=f"Domain {domain_name}: no internal memory overlaps",
        ))

    return results


def check_cross_domain_memory_overlaps(tree, domain_nodes) -> List[ValidationResult]:
    """Detect overlapping memory regions between different domains.

    This POST_PROCESSING phase check finds memory regions that overlap
    across domain boundaries (unless they are shared memory pools).

    Args:
        tree: LopperTree to validate
        domain_nodes: List of domain nodes to check

    Returns:
        List of ValidationResult objects
    """
    results = []

    if len(domain_nodes) < 2:
        results.append(ValidationResult(
            check_name='cross_domain_overlap',
            phase=ValidationPhase.POST_PROCESSING,
            passed=True,
            message="Fewer than 2 domains, no cross-domain overlap possible",
        ))
        return results

    # Collect all domain memory regions
    all_memory_map = MemoryMap()

    for domain_node in domain_nodes:
        domain_map = collect_memory_regions(tree, domain_node)
        for region in domain_map.get_by_domain(domain_node.name):
            all_memory_map.add_region(region)

    # Check for cross-domain overlaps
    overlaps = all_memory_map.find_overlaps(cross_domain=True, include_intentional=False)

    for overlap in overlaps:
        results.append(ValidationResult(
            check_name='cross_domain_overlap',
            phase=ValidationPhase.POST_PROCESSING,
            passed=False,
            message=(f"Cross-domain memory overlap: {overlap.region1.domain} "
                    f"and {overlap.region2.domain} overlap at "
                    f"{hex(overlap.overlap_start)} ({overlap.overlap_size} bytes)"),
            details={
                'domain1': overlap.region1.domain,
                'domain2': overlap.region2.domain,
                'region1': overlap.region1.source_path,
                'region2': overlap.region2.source_path,
                'overlap_start': overlap.overlap_start,
                'overlap_size': overlap.overlap_size,
            }
        ))

    if not overlaps:
        results.append(ValidationResult(
            check_name='cross_domain_overlap',
            phase=ValidationPhase.POST_PROCESSING,
            passed=True,
            message="No cross-domain memory overlaps detected",
        ))

    return results


def check_carveout_in_reserved_memory(tree, carveout_nodes) -> List[ValidationResult]:
    """Validate that OpenAMP carveout regions are in reserved-memory.

    This POST_PROCESSING phase check ensures that OpenAMP carveouts are
    properly defined in /reserved-memory.

    Args:
        tree: LopperTree to validate
        carveout_nodes: List of carveout nodes to validate

    Returns:
        List of ValidationResult objects
    """
    results = []

    if not carveout_nodes:
        results.append(ValidationResult(
            check_name='carveout_bounds',
            phase=ValidationPhase.POST_PROCESSING,
            passed=True,
            message="No carveout nodes to validate",
        ))
        return results

    # Check if reserved-memory exists
    try:
        resmem = tree['/reserved-memory']
    except:
        # Check if any carveout expects to be in DDR (reserved-memory)
        expect_ddr = any('/reserved-memory/' in n.abs_path for n in carveout_nodes)
        if expect_ddr:
            results.append(ValidationResult(
                check_name='carveout_bounds',
                phase=ValidationPhase.POST_PROCESSING,
                passed=False,
                message="Carveouts should be in reserved-memory but /reserved-memory does not exist",
            ))
        return results

    # Collect reserved-memory regions
    memory_map = collect_memory_regions(tree)
    reserved_regions = memory_map.get_by_type(MemoryRegionType.RESERVED_MEMORY)

    # Get cell sizes for carveouts
    try:
        resmem_ac = resmem['#address-cells'][0]
    except:
        resmem_ac = 2
    try:
        resmem_sc = resmem['#size-cells'][0]
    except:
        resmem_sc = 2

    # Check each carveout
    for carveout in carveout_nodes:
        try:
            reg_val = carveout['reg'].value
            if not reg_val or reg_val == ['']:
                continue
        except:
            continue

        # Parse carveout region
        start, _ = _cell_value_get(reg_val, resmem_ac)
        size, _ = _cell_value_get(reg_val, resmem_sc, resmem_ac)

        carveout_region = MemoryRegion(
            start=start,
            size=size,
            region_type=MemoryRegionType.CARVEOUT,
            source_path=carveout.abs_path,
        )

        # Check for conflicts with other reserved-memory regions
        for resmem_region in reserved_regions:
            if resmem_region.source_path == carveout.abs_path:
                continue  # Don't compare with self

            if carveout_region.overlaps(resmem_region):
                is_intentional = resmem_region.is_shared_memory()
                if not is_intentional:
                    results.append(ValidationResult(
                        check_name='carveout_bounds',
                        phase=ValidationPhase.POST_PROCESSING,
                        passed=False,
                        message=(f"Carveout {carveout.abs_path} conflicts with "
                                f"reserved-memory {resmem_region.source_path}"),
                        source_path=carveout.abs_path,
                        details={
                            'carveout': carveout.abs_path,
                            'conflict': resmem_region.source_path,
                            'overlap_start': max(start, resmem_region.start),
                            'overlap_size': carveout_region.overlap_size(resmem_region),
                        }
                    ))

    if not any(not r.passed for r in results):
        results.append(ValidationResult(
            check_name='carveout_bounds',
            phase=ValidationPhase.POST_PROCESSING,
            passed=True,
            message="Carveout regions validated successfully",
        ))

    return results


# =============================================================================
# Memory Validator Class
# =============================================================================

@ValidatorRegistry.register
class MemoryValidator(BaseValidator):
    """Orchestrator for running memory validation checks.

    This class manages the execution of memory validation checks at
    appropriate pipeline phases and collects results. It inherits from
    BaseValidator and registers itself with the ValidatorRegistry.
    """

    CATEGORY = "memory"

    # Warning flags this validator handles
    WARNING_FLAGS = [
        'memory_cells',
        'memory_reg',
        'memory_overlap',
        'reserved_bounds',
        'domain_overlap',
        'cross_domain_overlap',
        'carveout_bounds',
    ]

    # Meta-flags that enable multiple checks
    META_FLAGS = {
        'memory_all': ['memory_cells', 'memory_reg', 'memory_overlap',
                      'reserved_bounds', 'domain_overlap', 'cross_domain_overlap',
                      'carveout_bounds'],
    }

    # Map of warning flags to check functions and their phases
    CHECK_REGISTRY = {
        'memory_cells': (ValidationPhase.EARLY, check_cell_properties),
        'memory_reg': (ValidationPhase.EARLY, check_reg_property_format),
        'memory_overlap': (ValidationPhase.POST_YAML, check_reserved_memory_overlaps),
        'reserved_bounds': (ValidationPhase.POST_PROCESSING, None),  # Uses core.check_reserved_memory_in_memory_ranges
        'domain_overlap': (ValidationPhase.POST_PROCESSING, check_domain_memory_overlaps),
        'cross_domain_overlap': (ValidationPhase.POST_PROCESSING, check_cross_domain_memory_overlaps),
        'carveout_bounds': (ValidationPhase.POST_PROCESSING, check_carveout_in_reserved_memory),
    }

    def run_phase(
        self,
        phase: ValidationPhase,
        tree,
        domain_node=None,
        domain_nodes=None,
        carveout_nodes=None,
        **kwargs
    ) -> List[ValidationResult]:
        """Run all enabled checks for a specific phase.

        Args:
            phase: The validation phase to run
            tree: LopperTree to validate
            domain_node: Optional single domain node (for POST_PROCESSING)
            domain_nodes: Optional list of domain nodes (for cross-domain checks)
            carveout_nodes: Optional list of carveout nodes
            **kwargs: Additional arguments (ignored)

        Returns:
            List of ValidationResult objects from this phase
        """
        phase_results = []

        for check_name, (check_phase, check_func) in self.CHECK_REGISTRY.items():
            if check_phase != phase:
                continue
            if not self.is_check_enabled(check_name):
                continue

            # Handle special cases
            if check_name == 'reserved_bounds':
                # This check is handled by core.check_reserved_memory_in_memory_ranges
                continue
            elif check_name == 'domain_overlap' and domain_node:
                results = check_func(tree, domain_node)
            elif check_name == 'cross_domain_overlap' and domain_nodes:
                results = check_func(tree, domain_nodes)
            elif check_name == 'carveout_bounds' and carveout_nodes:
                results = check_func(tree, carveout_nodes)
            elif check_func and check_phase in [ValidationPhase.EARLY, ValidationPhase.POST_YAML]:
                results = check_func(tree)
            else:
                continue

            phase_results.extend(results)

        self.results.extend(phase_results)
        return phase_results


def validate_memory(
    tree,
    phase: ValidationPhase,
    warnings: Optional[List[str]] = None,
    werror: bool = False,
    domain_node=None,
    domain_nodes=None,
    carveout_nodes=None
) -> int:
    """Convenience function to run memory validation for a specific phase.

    This is the main entry point for running memory validation from
    the pipeline integration points.

    Args:
        tree: LopperTree to validate
        phase: Validation phase to run
        warnings: List of warning flags to enable
        werror: If True, treat warnings as errors
        domain_node: Optional domain node (for POST_PROCESSING)
        domain_nodes: Optional list of domain nodes (for cross-domain)
        carveout_nodes: Optional list of carveout nodes

    Returns:
        Number of failed checks (errors)
    """
    validator = MemoryValidator(warnings=warnings, werror=werror)
    validator.run_phase(
        phase,
        tree,
        domain_node=domain_node,
        domain_nodes=domain_nodes,
        carveout_nodes=carveout_nodes
    )
    return validator.report()
