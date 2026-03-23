#/*
# * Copyright (c) 2024,2025,2026 Xilinx Inc. All rights reserved.
# *
# * Author:
# *       Bruce Ashfield <bruce.ashfield@xilinx.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

"""
Lopper Audit Package

This package provides tree validation and consistency checking routines
for device trees. It includes:

- core: Phandle validation and basic tree checks
- memory: Memory region data structures, validation, and overlap detection
- memviz: ASCII memory map visualization

For backwards compatibility, all public functions are re-exported here.
Code using `import lopper.audit` will continue to work.
"""

# Re-export base classes for the audit framework
from .base import (
    ValidationPhase,
    ValidationResult,
    BaseValidator,
    ValidatorRegistry,
    run_audit_phase,
)

# Re-export all core functions for backwards compatibility
from .core import (
    _cell_value_get,
    check_invalid_phandles,
    report_invalid_phandles,
    check_duplicate_phandles,
    report_duplicate_phandles,
    check_reserved_memory_in_memory_ranges,
    validate_reserved_memory_in_memory_ranges,
)

# Re-export memory validation functions and classes
from .memory import (
    # Enums
    MemoryRegionType,
    # Data classes
    MemoryRegion,
    OverlapResult,
    # Collection class
    MemoryMap,
    # Collection functions
    collect_memory_regions,
    # Validation functions
    check_cell_properties,
    check_reg_property_format,
    check_reserved_memory_overlaps,
    check_domain_memory_overlaps,
    check_cross_domain_memory_overlaps,
    check_carveout_in_reserved_memory,
    # Validator class
    MemoryValidator,
    # Convenience function
    validate_memory,
)

# Re-export visualization
from .memviz import (
    MemoryVisualizer,
    render_memory_map,
)

# Re-export schema validation functions and classes
from .schema import (
    # Enums
    ConstraintType,
    # Data classes
    PropertyConstraint,
    NodeConstraints,
    # Constraint definitions
    NODE_PROPERTY_CONSTRAINTS,
    # Validation functions
    check_forbidden_properties,
    check_required_properties,
    check_property_values,
    check_mutex_properties,
    # Validator class
    SchemaValidator,
    # Convenience function
    validate_schema,
)

# Define what is exported when using `from lopper.audit import *`
__all__ = [
    # Base framework classes
    'ValidationPhase',
    'ValidationResult',
    'BaseValidator',
    'ValidatorRegistry',
    'run_audit_phase',
    # Core functions
    '_cell_value_get',
    'check_invalid_phandles',
    'report_invalid_phandles',
    'check_duplicate_phandles',
    'report_duplicate_phandles',
    'check_reserved_memory_in_memory_ranges',
    'validate_reserved_memory_in_memory_ranges',
    # Memory enums
    'MemoryRegionType',
    # Memory data classes
    'MemoryRegion',
    'OverlapResult',
    # Memory collection
    'MemoryMap',
    'collect_memory_regions',
    # Memory validation functions
    'check_cell_properties',
    'check_reg_property_format',
    'check_reserved_memory_overlaps',
    'check_domain_memory_overlaps',
    'check_cross_domain_memory_overlaps',
    'check_carveout_in_reserved_memory',
    # Memory validator
    'MemoryValidator',
    'validate_memory',
    # Visualization
    'MemoryVisualizer',
    'render_memory_map',
    # Schema enums
    'ConstraintType',
    # Schema data classes
    'PropertyConstraint',
    'NodeConstraints',
    # Schema constraints
    'NODE_PROPERTY_CONSTRAINTS',
    # Schema validation functions
    'check_forbidden_properties',
    'check_required_properties',
    'check_property_values',
    'check_mutex_properties',
    # Schema validator
    'SchemaValidator',
    'validate_schema',
]
