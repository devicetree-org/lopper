#/*
# * Copyright (c) 2026 AMD Inc. All rights reserved.
# *
# * Author:
# *       Bruce Ashfield <bruce.ashfield@amd.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

"""
Lopper Schema Package

Unified schema system for device tree property types and constraints.

This package provides:
- Type definitions from dt-schema (uint32, phandle, string, etc.)
- Constraint definitions (required, forbidden, const, enum, mutex)
- Schema registry for type resolution
- Schema loader with search path for user/vendor extensions

For backwards compatibility, all exports from the old lopper/schema.py
are re-exported here. New code should use the unified types.

Schema Search Path (lowest to highest priority):
1. Built-in dt-schema (lopper/schema/dt-schema/schemas/)
2. User schemas (~/.config/lopper/schemas/)
3. LOPPER_SCHEMA_PATH environment variable
4. --schema-dir command-line option

Deprecation Notes:
- get_schema_manager() is deprecated, use get_registry() for new code
- SchemaManager is deprecated, use SchemaRegistry for new code
- DTSPropertyTypeResolver.get_property_type() is deprecated,
  use resolve_property_spec() which returns a PropertySpec
"""

import warnings

# New unified types
from .types import (
    PropertyType,
    TypeDefinition,
    DT_SCHEMA_TYPES,
)

# Core data structures
from .core import (
    ConstraintType,
    Constraint,
    PropertySpec,
    NodeSpec,
    SchemaRegistry,
    get_registry,
    reset_registry,
)

# Loader functions
from .loader import (
    get_schema_search_path,
    load_all_schemas,
)

# Backwards compatibility: re-export everything from learned.py
# This ensures `from lopper.schema import ...` continues to work
from .learned import (
    # Constants
    PROPERTY_DEBUG_LIST,
    PROPERTY_DEBUG_SET,
    PROPERTY_NAME_HEURISTICS,
    PROPERTY_TYPE_HINTS,
    # Schema manager (legacy API - see deprecation wrappers below)
    SchemaManager as _SchemaManager,
    get_schema_manager as _get_schema_manager,
    _schema_manager,  # Private singleton accessed by lopper/__init__.py
    # Schema generator
    DTSSchemaGenerator,
    # Type resolver
    DTSPropertyTypeResolver,
    # Type checker
    DTSTypeChecker,
    # Validator
    DTSValidator,
    # Helper functions
    add_property_type_hint,
    add_property_heuristic,
    create_all_from_schema,
    update_schema,
    update_schema_with_property,
    update_schema_with_node_pattern,
    initialize_lopper_properties,
    schema_has_definition,
    property_exists_in_schema,
    get_property_info,
    create_property_resolver,
    generate_schema_from_dts,
    schema_add_runtime_property,
    schema_get_resolver,
)


# =============================================================================
# Deprecation Wrappers
# =============================================================================

# Control deprecation warnings - can be disabled for testing
_DEPRECATION_WARNINGS_ENABLED = True


def _deprecation_warning(message):
    """Issue a deprecation warning if enabled."""
    if _DEPRECATION_WARNINGS_ENABLED:
        warnings.warn(message, DeprecationWarning, stacklevel=3)


def get_schema_manager():
    """Get the global schema manager instance.

    .. deprecated::
        Use :func:`get_registry` for new code. The SchemaRegistry provides
        a unified interface for type resolution and constraint checking.

    Returns:
        SchemaManager instance (legacy API)
    """
    _deprecation_warning(
        "get_schema_manager() is deprecated. "
        "Use lopper.schema.get_registry() for new code. "
        "The SchemaRegistry provides unified type resolution."
    )
    return _get_schema_manager()


class SchemaManager(_SchemaManager):
    """Legacy schema manager for learned property types.

    .. deprecated::
        Use :class:`SchemaRegistry` for new code. The SchemaRegistry provides
        a unified interface combining dt-schema types, learned types, and
        constraint validation.

    This class is maintained for backwards compatibility. New code should use:
    - SchemaRegistry for type resolution
    - PropertySpec for complete property specifications
    - get_registry() to access the global registry
    """

    def __new__(cls):
        # Issue deprecation warning before singleton creation
        _deprecation_warning(
            "SchemaManager is deprecated. "
            "Use lopper.schema.SchemaRegistry for new code."
        )
        return super().__new__(cls)

__all__ = [
    # New unified types
    'PropertyType',
    'TypeDefinition',
    'DT_SCHEMA_TYPES',
    # Core data structures
    'ConstraintType',
    'Constraint',
    'PropertySpec',
    'NodeSpec',
    'SchemaRegistry',
    'get_registry',
    'reset_registry',
    # Loader
    'get_schema_search_path',
    'load_all_schemas',
    # Legacy (from learned.py)
    'PROPERTY_DEBUG_LIST',
    'PROPERTY_DEBUG_SET',
    'PROPERTY_NAME_HEURISTICS',
    'PROPERTY_TYPE_HINTS',
    'SchemaManager',
    'get_schema_manager',
    'DTSSchemaGenerator',
    'DTSPropertyTypeResolver',
    'DTSTypeChecker',
    'DTSValidator',
    'add_property_type_hint',
    'add_property_heuristic',
    'create_all_from_schema',
    'update_schema',
    'update_schema_with_property',
    'update_schema_with_node_pattern',
    'initialize_lopper_properties',
    'schema_has_definition',
    'property_exists_in_schema',
    'get_property_info',
    'create_property_resolver',
    'generate_schema_from_dts',
    'schema_add_runtime_property',
    'schema_get_resolver',
]
