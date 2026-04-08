#/*
# * Copyright (c) 2026 AMD Inc. All rights reserved.
# *
# * Author:
# *       Bruce Ashfield <bruce.ashfield@amd.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

"""
Unified type definitions for device tree schema validation.

This module defines the core type system used across:
- dt-schema based validation (authoritative specs)
- Learned schema inference (observed usage)
- Audit constraint checking

Types are based on dt-schema/JSON Schema vocabulary for familiarity.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List, Any, Dict


class PropertyType(Enum):
    """Unified property types from dt-schema + lopper extensions.

    These map to both dt-schema types.yaml definitions and LopperFmt
    for backwards compatibility.
    """
    # Scalar integers (from dt-schema types.yaml)
    UINT8 = "uint8"
    INT8 = "int8"
    UINT16 = "uint16"
    INT16 = "int16"
    UINT32 = "uint32"
    INT32 = "int32"
    UINT64 = "uint64"
    INT64 = "int64"

    # References
    PHANDLE = "phandle"
    PHANDLE_ARRAY = "phandle-array"
    PATH_REF = "path-ref"    # string value is an absolute node path ("/axi/foo@0")
    ALIAS_REF = "alias-ref"  # string value is an alias name, optionally ":options"

    # Strings
    STRING = "string"
    STRING_ARRAY = "string-array"

    # Boolean/flag
    FLAG = "flag"
    EMPTY = "empty"  # Lopper's EMPTY maps to FLAG

    # Arrays (element type determined by context)
    UINT8_ARRAY = "uint8-array"
    UINT16_ARRAY = "uint16-array"
    UINT32_ARRAY = "uint32-array"
    UINT64_ARRAY = "uint64-array"

    # Special
    UNKNOWN = "unknown"

    def to_lopper_fmt(self):
        """Convert to LopperFmt for backwards compatibility."""
        from lopper import LopperFmt
        mapping = {
            PropertyType.UINT8: LopperFmt.UINT8,
            PropertyType.UINT16: LopperFmt.UINT16,
            PropertyType.UINT32: LopperFmt.UINT32,
            PropertyType.UINT64: LopperFmt.UINT64,
            PropertyType.INT8: LopperFmt.UINT8,   # No signed in LopperFmt
            PropertyType.INT16: LopperFmt.UINT16,
            PropertyType.INT32: LopperFmt.UINT32,
            PropertyType.INT64: LopperFmt.UINT64,
            PropertyType.STRING: LopperFmt.STRING,
            PropertyType.STRING_ARRAY: LopperFmt.MULTI_STRING,
            PropertyType.FLAG: LopperFmt.EMPTY,
            PropertyType.EMPTY: LopperFmt.EMPTY,
            PropertyType.PHANDLE: LopperFmt.UINT32,
            PropertyType.PHANDLE_ARRAY: LopperFmt.UINT32,
            PropertyType.PATH_REF: LopperFmt.STRING,
            PropertyType.ALIAS_REF: LopperFmt.STRING,
            PropertyType.UINT8_ARRAY: LopperFmt.UINT8,
            PropertyType.UINT16_ARRAY: LopperFmt.UINT16,
            PropertyType.UINT32_ARRAY: LopperFmt.UINT32,
            PropertyType.UINT64_ARRAY: LopperFmt.UINT64,
            PropertyType.UNKNOWN: LopperFmt.UNKNOWN,
        }
        return mapping.get(self, LopperFmt.UNKNOWN)

    @classmethod
    def from_lopper_fmt(cls, lopper_fmt):
        """Convert from LopperFmt to PropertyType."""
        from lopper import LopperFmt
        mapping = {
            LopperFmt.UINT8: cls.UINT8,
            LopperFmt.UINT16: cls.UINT16,
            LopperFmt.UINT32: cls.UINT32,
            LopperFmt.UINT64: cls.UINT64,
            LopperFmt.STRING: cls.STRING,
            LopperFmt.MULTI_STRING: cls.STRING_ARRAY,
            LopperFmt.EMPTY: cls.FLAG,
            LopperFmt.UNKNOWN: cls.UNKNOWN,
        }
        return mapping.get(lopper_fmt, cls.UNKNOWN)


@dataclass
class TypeDefinition:
    """A property type with validation constraints.

    Loaded from dt-schema types.yaml or inferred from observation.

    Attributes:
        property_type: The PropertyType enum value
        min_value: Minimum allowed value (for integers)
        max_value: Maximum allowed value (for integers)
        min_items: Minimum array length
        max_items: Maximum array length
        pattern: Regex pattern for string validation
        enum_values: List of allowed values
        source: Origin of this definition ("dt-schema", "learned", "heuristic")
        description: Human-readable description
    """
    property_type: PropertyType
    min_value: Optional[int] = None
    max_value: Optional[int] = None
    min_items: Optional[int] = None
    max_items: Optional[int] = None
    pattern: Optional[str] = None
    enum_values: Optional[List[Any]] = None
    source: str = "unknown"
    description: Optional[str] = None


# Pre-built type definitions from dt-schema types.yaml
DT_SCHEMA_TYPES: Dict[str, TypeDefinition] = {
    'uint8': TypeDefinition(
        PropertyType.UINT8,
        min_value=0, max_value=255,
        source="dt-schema",
        description="8-bit unsigned integer"
    ),
    'int8': TypeDefinition(
        PropertyType.INT8,
        min_value=-128, max_value=127,
        source="dt-schema",
        description="8-bit signed integer"
    ),
    'uint16': TypeDefinition(
        PropertyType.UINT16,
        min_value=0, max_value=65535,
        source="dt-schema",
        description="16-bit unsigned integer"
    ),
    'int16': TypeDefinition(
        PropertyType.INT16,
        min_value=-32768, max_value=32767,
        source="dt-schema",
        description="16-bit signed integer"
    ),
    'uint32': TypeDefinition(
        PropertyType.UINT32,
        min_value=0, max_value=0xffffffff,
        source="dt-schema",
        description="32-bit unsigned integer"
    ),
    'int32': TypeDefinition(
        PropertyType.INT32,
        min_value=-2147483648, max_value=2147483647,
        source="dt-schema",
        description="32-bit signed integer"
    ),
    'uint64': TypeDefinition(
        PropertyType.UINT64,
        min_value=0, max_value=0xffffffffffffffff,
        source="dt-schema",
        description="64-bit unsigned integer"
    ),
    'int64': TypeDefinition(
        PropertyType.INT64,
        min_value=-9223372036854775808, max_value=9223372036854775807,
        source="dt-schema",
        description="64-bit signed integer"
    ),
    'phandle': TypeDefinition(
        PropertyType.PHANDLE,
        min_value=1, max_value=0xffffffff,  # phandle 0 is invalid
        source="dt-schema",
        description="Reference to another node"
    ),
    'string': TypeDefinition(
        PropertyType.STRING,
        source="dt-schema",
        description="Single string value"
    ),
    'string-array': TypeDefinition(
        PropertyType.STRING_ARRAY,
        min_items=1,
        source="dt-schema",
        description="Array of strings"
    ),
    'flag': TypeDefinition(
        PropertyType.FLAG,
        source="dt-schema",
        description="Boolean flag (presence = true)"
    ),
    'uint32-array': TypeDefinition(
        PropertyType.UINT32_ARRAY,
        min_items=1,
        source="dt-schema",
        description="Array of 32-bit unsigned integers"
    ),
    'uint64-array': TypeDefinition(
        PropertyType.UINT64_ARRAY,
        min_items=1,
        source="dt-schema",
        description="Array of 64-bit unsigned integers"
    ),
    'phandle-array': TypeDefinition(
        PropertyType.PHANDLE_ARRAY,
        min_items=1,
        source="dt-schema",
        description="Array of phandles with optional specifier cells"
    ),
    'path-ref': TypeDefinition(
        PropertyType.PATH_REF,
        source="dt-schema",
        description="Absolute device-tree node path string"
    ),
    'alias-ref': TypeDefinition(
        PropertyType.ALIAS_REF,
        source="dt-schema",
        description="Alias name string, optionally suffixed with :options"
    ),
}
