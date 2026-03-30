#/*
# * Copyright (c) 2026 AMD Inc. All rights reserved.
# *
# * Author:
# *       Bruce Ashfield <bruce.ashfield@amd.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

"""
Core schema data structures and registry.

This module provides:
- ConstraintType: Enum for constraint kinds (required, forbidden, etc.)
- Constraint: Individual constraint definition
- PropertySpec: Complete property specification (type + constraints)
- NodeSpec: Node-level constraints by path pattern
- SchemaRegistry: Unified registry for all schema information
"""

from dataclasses import dataclass, field
from enum import Enum
from fnmatch import fnmatch
from typing import List, Dict, Optional, Any

from .types import PropertyType, TypeDefinition, DT_SCHEMA_TYPES


class ConstraintType(Enum):
    """Constraint types for property validation.

    Uses JSON Schema / dt-schema vocabulary for familiarity.
    """
    REQUIRED = "required"      # Property must exist
    FORBIDDEN = "forbidden"    # Property must NOT exist
    CONST = "const"            # Property must equal specific value
    ENUM = "enum"              # Property must be one of allowed values
    MUTEX = "mutex"            # Properties are mutually exclusive
    RANGE = "range"            # Value must be in min/max range
    PATTERN = "pattern"        # String must match regex


@dataclass
class Constraint:
    """A single constraint on a property or set of properties.

    Attributes:
        constraint_type: Kind of constraint
        properties: Property names this constraint applies to
        expected_value: Value for CONST/ENUM constraints
        message: Human-readable error message
    """
    constraint_type: ConstraintType
    properties: List[str]
    expected_value: Optional[Any] = None
    message: Optional[str] = None


@dataclass
class PropertySpec:
    """Complete specification for a property.

    Unifies type information (from dt-schema or learned) with
    constraints (required, forbidden, etc.).

    Attributes:
        name: Property name
        type_def: Type definition with validation bounds
        constraints: List of constraints on this property
        confidence: 1.0 = authoritative (dt-schema), <1.0 = inferred
        source: Origin ("dt-schema", "learned", "heuristic")
        context: Compatible string or node pattern for context-specific specs
        observation_count: Number of times observed (for learned)
        type_frequencies: Type occurrence counts for ambiguous properties
        phandle_pattern: Detected phandle pattern (e.g., "phandle + 2 cells")
        context_lookups: Properties to look up for context (e.g., "#clock-cells")
    """
    name: str
    type_def: TypeDefinition
    constraints: List[Constraint] = field(default_factory=list)
    confidence: float = 1.0
    source: str = "unknown"
    context: Optional[str] = None
    observation_count: int = 0
    type_frequencies: Dict[str, int] = field(default_factory=dict)
    phandle_pattern: Optional[str] = None
    context_lookups: List[str] = field(default_factory=list)


@dataclass
class NodeSpec:
    """Specification for a class of nodes.

    Matches nodes by path pattern and defines property constraints.

    Attributes:
        node_pattern: Glob pattern (e.g., "/memory@*", "/reserved-memory/*")
        properties: Property specifications for this node type
        constraints: Node-level constraints (e.g., required properties)
        description: Human-readable description
        schema_file: Source schema file path
    """
    node_pattern: str
    properties: Dict[str, PropertySpec] = field(default_factory=dict)
    constraints: List[Constraint] = field(default_factory=list)
    description: Optional[str] = None
    schema_file: Optional[str] = None


class SchemaRegistry:
    """Unified registry for all schema information.

    Combines:
    - dt-schema authoritative specs (highest priority)
    - Learned property types from observation
    - Name-based heuristics (lowest priority)

    Resolution priority: dt-schema > learned > heuristics
    """

    def __init__(self):
        self._node_specs: Dict[str, NodeSpec] = {}
        self._property_specs: Dict[str, PropertySpec] = {}
        self._compatible_specs: Dict[str, Dict[str, PropertySpec]] = {}
        self._type_defs: Dict[str, TypeDefinition] = {}
        self._initialized = False

    def register_type(self, name: str, type_def: TypeDefinition):
        """Register a type definition.

        Args:
            name: Type name (e.g., "uint32", "phandle")
            type_def: Type definition with constraints
        """
        self._type_defs[name] = type_def

    def get_type(self, name: str) -> Optional[TypeDefinition]:
        """Get a registered type definition.

        Args:
            name: Type name

        Returns:
            TypeDefinition or None if not found
        """
        return self._type_defs.get(name)

    def register_node_spec(self, name: str, spec: NodeSpec):
        """Register a node specification.

        Args:
            name: Identifier for this spec (usually schema filename)
            spec: Node specification with pattern and constraints
        """
        self._node_specs[name] = spec

    def get_node_spec(self, name: str) -> Optional[NodeSpec]:
        """Get a registered node specification by name."""
        return self._node_specs.get(name)

    def register_property_spec(
        self,
        spec: PropertySpec,
        context: Optional[str] = None
    ):
        """Register a property specification.

        Args:
            spec: Property specification
            context: Optional compatible string for context-specific registration
        """
        if context:
            if context not in self._compatible_specs:
                self._compatible_specs[context] = {}
            self._compatible_specs[context][spec.name] = spec
        else:
            self._property_specs[spec.name] = spec

    def resolve_property_type(
        self,
        prop_name: str,
        node_path: Optional[str] = None,
        compatible: Optional[str] = None
    ) -> PropertySpec:
        """Resolve the type specification for a property.

        Resolution order (highest to lowest priority):
        1. Node-specific (by path pattern match)
        2. Compatible-specific
        3. Global property spec
        4. Name heuristics
        5. Unknown

        Args:
            prop_name: Property name to resolve
            node_path: Optional node path for context
            compatible: Optional compatible string for context

        Returns:
            PropertySpec with type and constraints
        """
        # 1. Check node-specific specs (highest priority for path matches)
        if node_path:
            for spec in self._node_specs.values():
                if self._matches_pattern(node_path, spec.node_pattern):
                    if prop_name in spec.properties:
                        return spec.properties[prop_name]

        # 2. Check compatible-specific specs
        if compatible and compatible in self._compatible_specs:
            if prop_name in self._compatible_specs[compatible]:
                return self._compatible_specs[compatible][prop_name]

        # 3. Check global property specs
        if prop_name in self._property_specs:
            return self._property_specs[prop_name]

        # 4. Apply name heuristics
        heuristic_spec = self._apply_heuristics(prop_name)
        if heuristic_spec:
            return heuristic_spec

        # 5. Unknown
        return PropertySpec(
            name=prop_name,
            type_def=TypeDefinition(PropertyType.UNKNOWN, source="unknown"),
            confidence=0.0,
            source="unknown"
        )

    def get_node_constraints(self, node_path: str) -> List[Constraint]:
        """Get all constraints that apply to a node path.

        Args:
            node_path: Absolute node path (e.g., "/reserved-memory/region1")

        Returns:
            List of Constraint objects from all matching node specs
        """
        constraints = []
        for spec in self._node_specs.values():
            if self._matches_pattern(node_path, spec.node_pattern):
                constraints.extend(spec.constraints)
        return constraints

    def get_matching_node_specs(self, node_path: str) -> List[NodeSpec]:
        """Get all node specs that match a path.

        Args:
            node_path: Absolute node path

        Returns:
            List of matching NodeSpec objects
        """
        return [
            spec for spec in self._node_specs.values()
            if self._matches_pattern(node_path, spec.node_pattern)
        ]

    def _matches_pattern(self, path: str, pattern: str) -> bool:
        """Check if a node path matches a constraint pattern.

        Supports:
        - Exact match: "/reserved-memory" matches "/reserved-memory"
        - Child wildcard: "/reserved-memory/*" matches "/reserved-memory/foo"
        - Unit address wildcard: "/memory@*" matches "/memory@0"

        Args:
            path: Node path to test
            pattern: Pattern to match against

        Returns:
            True if path matches pattern
        """
        if pattern == path:
            return True

        # Child wildcard: /parent/* matches /parent/child
        if pattern.endswith('/*'):
            parent_pattern = pattern[:-2]
            if '/' not in path.lstrip('/'):
                return False
            parent_path = path.rsplit('/', 1)[0]
            if not parent_path:
                parent_path = '/'
            return parent_path == parent_pattern

        # Unit address wildcard: /node@* matches /node@0, /node@80000000
        if '@*' in pattern:
            base = pattern.replace('@*', '@')
            return path.startswith(base)

        # General glob
        return fnmatch(path, pattern)

    def _apply_heuristics(self, prop_name: str) -> Optional[PropertySpec]:
        """Apply name-based heuristics to infer property type.

        Uses PROPERTY_NAME_HEURISTICS from learned.py for suffix/prefix rules.

        Args:
            prop_name: Property name

        Returns:
            PropertySpec with heuristic type, or None
        """
        try:
            from .learned import PROPERTY_NAME_HEURISTICS
        except ImportError:
            return None

        # Check exact matches first
        exact = PROPERTY_NAME_HEURISTICS.get('exact', {})
        if prop_name in exact:
            lopper_fmt = exact[prop_name]
            return PropertySpec(
                name=prop_name,
                type_def=TypeDefinition(
                    property_type=PropertyType.from_lopper_fmt(lopper_fmt),
                    source="heuristic"
                ),
                confidence=0.7,
                source="heuristic"
            )

        # Check suffix patterns
        suffixes = PROPERTY_NAME_HEURISTICS.get('suffixes', {})
        for suffix, lopper_fmt in suffixes.items():
            if prop_name.endswith(suffix):
                return PropertySpec(
                    name=prop_name,
                    type_def=TypeDefinition(
                        property_type=PropertyType.from_lopper_fmt(lopper_fmt),
                        source="heuristic"
                    ),
                    confidence=0.5,
                    source="heuristic"
                )

        return None

    def list_node_specs(self) -> Dict[str, NodeSpec]:
        """Get all registered node specs."""
        return self._node_specs.copy()

    def list_property_specs(self) -> Dict[str, PropertySpec]:
        """Get all registered global property specs."""
        return self._property_specs.copy()

    def list_types(self) -> Dict[str, TypeDefinition]:
        """Get all registered type definitions."""
        return self._type_defs.copy()


# Global registry instance
_registry: Optional[SchemaRegistry] = None


def get_registry() -> SchemaRegistry:
    """Get the global schema registry, initializing if needed.

    The registry is lazily initialized on first access and loads:
    1. Built-in dt-schema type definitions
    2. Schemas from the search path

    Returns:
        The global SchemaRegistry instance
    """
    global _registry
    if _registry is None:
        _registry = SchemaRegistry()
        # Register built-in types
        for name, type_def in DT_SCHEMA_TYPES.items():
            _registry.register_type(name, type_def)
        _registry._initialized = True
    return _registry


def reset_registry():
    """Reset the global registry (mainly for testing)."""
    global _registry
    _registry = None
