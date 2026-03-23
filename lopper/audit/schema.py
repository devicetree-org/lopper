#/*
# * Copyright (c) 2026 AMD Inc. All rights reserved.
# *
# * Author:
# *       Bruce Ashfield <bruce.ashfield@amd.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

"""
Lopper audit schema module

This module provides schema-based property validation for device trees,
loading constraints from vendored dt-schema YAML files.

Key components:
- ConstraintType: Enum of constraint types (required, forbidden, etc.)
- PropertyConstraint: Dataclass for individual property constraints
- NodeConstraints: Dataclass for node-level constraint groupings
- load_constraints_from_schema(): Loads constraints from dt-schema YAML
- Standalone check functions: check_forbidden_properties, etc.
- SchemaValidator: Orchestrator for running schema validation checks

The constraints are loaded dynamically from YAML schema files in
lopper/dt-schema/schemas/, making validation fully data-driven.
"""

from dataclasses import dataclass, field
from enum import Enum
from fnmatch import fnmatch
import glob
import os
import lopper.log

try:
    import ruamel.yaml as yaml
    from ruamel.yaml import YAML
    _yaml_loader = YAML()
    _yaml_loader.preserve_quotes = True
except ImportError:
    import yaml
    _yaml_loader = None

from .base import (
    ValidationPhase,
    ValidationResult,
    BaseValidator,
    ValidatorRegistry,
)


class ConstraintType(Enum):
    """
    Property constraint types using dt-schema/JSON Schema terminology.

    These are lopper's own constraint implementations, using vocabulary
    consistent with upstream dt-schema for familiarity.
    """
    REQUIRED = "required"    # Property must be present
    FORBIDDEN = "forbidden"  # Property must NOT be present
    CONST = "const"          # Property must have specific value
    ENUM = "enum"            # Property must be one of allowed values
    MUTEX = "mutex"          # Properties are mutually exclusive


@dataclass
class PropertyConstraint:
    """A single property constraint.

    Attributes:
        constraint_type: Type of constraint (required, forbidden, etc.)
        properties: List of property names this constraint applies to
        expected_value: Expected value for CONST/ENUM constraints
        message: Optional human-readable message for violations
    """
    constraint_type: ConstraintType
    properties: list
    expected_value: object = None
    message: str = None


@dataclass
class NodeConstraints:
    """Constraints for a class of nodes.

    Attributes:
        node_pattern: Glob pattern matching node paths (e.g., "/memory@*")
        constraints: List of PropertyConstraint objects
        description: Optional description of this constraint set
        schema_file: Source schema file path
    """
    node_pattern: str
    constraints: list
    description: str = None
    schema_file: str = None


# =============================================================================
# Schema Loading
# =============================================================================

def _get_schema_dir():
    """Get the path to vendored dt-schema files."""
    return os.path.join(os.path.dirname(__file__), '..', 'dt-schema', 'schemas')


def _parse_schema_file(schema_path):
    """Parse a single dt-schema YAML file and extract constraints.

    Args:
        schema_path: Path to the YAML schema file

    Returns:
        NodeConstraints object, or None if parsing fails
    """
    try:
        with open(schema_path, 'r') as f:
            if _yaml_loader:
                schema = _yaml_loader.load(f)
            else:
                schema = yaml.safe_load(f)
    except Exception as e:
        lopper.log._debug(f"schema: failed to load {schema_path}: {e}")
        return None

    if not schema:
        return None

    # Determine node pattern from $id or file path
    schema_id = schema.get('$id', '')
    node_pattern = _schema_id_to_pattern(schema_id, schema_path)
    if not node_pattern:
        return None

    constraints = []
    description = schema.get('description', schema.get('title', ''))

    # Parse 'required' array -> REQUIRED constraint
    required_props = schema.get('required', [])
    if required_props:
        constraints.append(PropertyConstraint(
            constraint_type=ConstraintType.REQUIRED,
            properties=required_props,
            message=f"required properties: {', '.join(required_props)}"
        ))

    # Parse 'not: required' -> FORBIDDEN constraint
    not_block = schema.get('not', {})
    if isinstance(not_block, dict):
        forbidden = not_block.get('required', [])
        if forbidden:
            constraints.append(PropertyConstraint(
                constraint_type=ConstraintType.FORBIDDEN,
                properties=forbidden,
                message=f"forbidden properties: {', '.join(forbidden)}"
            ))

    # Parse 'properties' for const/enum
    properties = schema.get('properties', {})
    for prop_name, prop_schema in properties.items():
        if isinstance(prop_schema, dict):
            # const constraint
            if 'const' in prop_schema:
                constraints.append(PropertyConstraint(
                    constraint_type=ConstraintType.CONST,
                    properties=[prop_name],
                    expected_value=prop_schema['const'],
                    message=f"{prop_name} must be '{prop_schema['const']}'"
                ))
            # enum constraint
            elif 'enum' in prop_schema:
                constraints.append(PropertyConstraint(
                    constraint_type=ConstraintType.ENUM,
                    properties=[prop_name],
                    expected_value=prop_schema['enum'],
                    message=f"{prop_name} must be one of {prop_schema['enum']}"
                ))

    # Parse 'dependentSchemas' for mutex
    dependent = schema.get('dependentSchemas', {})
    mutex_pairs = _extract_mutex_from_dependent(dependent)
    for mutex_props in mutex_pairs:
        constraints.append(PropertyConstraint(
            constraint_type=ConstraintType.MUTEX,
            properties=mutex_props,
            message=f"{' and '.join(mutex_props)} are mutually exclusive"
        ))

    if not constraints:
        return None

    return NodeConstraints(
        node_pattern=node_pattern,
        constraints=constraints,
        description=description,
        schema_file=schema_path
    )


def _schema_id_to_pattern(schema_id, schema_path):
    """Convert schema $id to node pattern.

    Args:
        schema_id: The $id field from the schema
        schema_path: Path to the schema file (fallback)

    Returns:
        Node pattern string (e.g., "/reserved-memory/*")
    """
    # Map known schema IDs to patterns
    id_to_pattern = {
        'reserved-memory.yaml': '/reserved-memory/*',
        'memory.yaml': '/memory@*',
    }

    # Check by filename
    basename = os.path.basename(schema_path)
    if basename in id_to_pattern:
        return id_to_pattern[basename]

    # Check by schema_id
    for key, pattern in id_to_pattern.items():
        if key in schema_id:
            return pattern

    return None


def _extract_mutex_from_dependent(dependent):
    """Extract mutex property pairs from dependentSchemas.

    In dt-schema, mutual exclusivity is expressed as:
        dependentSchemas:
          prop_a:
            not:
              required: [prop_b]

    Args:
        dependent: The dependentSchemas dict from schema

    Returns:
        List of mutex property lists (e.g., [['no-map', 'reusable']])
    """
    mutex_pairs = []
    seen = set()

    for prop_a, dep_schema in dependent.items():
        if not isinstance(dep_schema, dict):
            continue
        not_block = dep_schema.get('not', {})
        if not isinstance(not_block, dict):
            continue
        forbidden = not_block.get('required', [])
        for prop_b in forbidden:
            # Avoid duplicates (a,b) and (b,a)
            pair = tuple(sorted([prop_a, prop_b]))
            if pair not in seen:
                seen.add(pair)
                mutex_pairs.append(list(pair))

    return mutex_pairs


def load_constraints_from_schemas(schema_dir=None):
    """Load all constraints from dt-schema YAML files.

    Args:
        schema_dir: Directory containing schema files (default: vendored schemas)

    Returns:
        Dict mapping constraint names to NodeConstraints objects
    """
    schema_dir = schema_dir or _get_schema_dir()
    constraints = {}

    if not os.path.isdir(schema_dir):
        lopper.log._debug(f"schema: schema directory not found: {schema_dir}")
        return constraints

    # Find all YAML files recursively
    pattern = os.path.join(schema_dir, '**', '*.yaml')
    for schema_path in glob.glob(pattern, recursive=True):
        node_constraints = _parse_schema_file(schema_path)
        if node_constraints:
            # Use basename without extension as key
            name = os.path.splitext(os.path.basename(schema_path))[0]
            # Handle duplicates by adding directory context
            if name in constraints:
                parent = os.path.basename(os.path.dirname(schema_path))
                name = f"{parent}-{name}"
            constraints[name] = node_constraints
            lopper.log._debug(f"schema: loaded {name} -> {node_constraints.node_pattern}")

    return constraints


# Load constraints at module import time
NODE_PROPERTY_CONSTRAINTS = load_constraints_from_schemas()


# =============================================================================
# Node Pattern Matching
# =============================================================================

def _node_matches_pattern(node_path, pattern):
    """Check if a node path matches a constraint pattern.

    The pattern uses glob-style matching:
    - '*' matches any single path component
    - '**' would match multiple components (not currently used)

    Args:
        node_path: Absolute path of the node (e.g., "/reserved-memory/my-region")
        pattern: Pattern to match against (e.g., "/reserved-memory/*")

    Returns:
        True if the node path matches the pattern
    """
    # Handle exact matches
    if pattern == node_path:
        return True

    # Handle patterns ending in /*
    if pattern.endswith('/*'):
        parent_pattern = pattern[:-2]
        # Check if node is direct child of the parent
        if '/' not in node_path.lstrip('/'):
            return False
        parent_path = node_path.rsplit('/', 1)[0]
        if not parent_path:
            parent_path = '/'
        return parent_path == parent_pattern

    # Handle /memory@* pattern (matches /memory@0, /memory@80000000, etc.)
    if '@*' in pattern:
        base_pattern = pattern.replace('@*', '@')
        return node_path.startswith(base_pattern)

    # Use fnmatch for general glob patterns
    return fnmatch(node_path, pattern)


def _get_matching_constraints(node_path, constraints=None):
    """Get all constraint sets that match a node path.

    Args:
        node_path: Absolute path of the node
        constraints: Constraint dictionary to search (default: NODE_PROPERTY_CONSTRAINTS)

    Returns:
        List of NodeConstraints objects that match this node
    """
    constraints = constraints or NODE_PROPERTY_CONSTRAINTS
    matches = []

    for name, node_constraints in constraints.items():
        if _node_matches_pattern(node_path, node_constraints.node_pattern):
            matches.append(node_constraints)

    return matches


# =============================================================================
# Standalone Check Functions
# =============================================================================

def check_forbidden_properties(tree, constraints=None):
    """Check for forbidden properties on nodes.

    This function validates that nodes do not contain properties that
    are forbidden by the schema. For example, reserved-memory children
    must not have device_type property.

    Standalone function - can be wrapped by CheckHandler later.

    Args:
        tree: LopperTree to validate
        constraints: Optional constraint dictionary (default: NODE_PROPERTY_CONSTRAINTS)

    Returns:
        List of ValidationResult objects
    """
    results = []
    constraints = constraints or NODE_PROPERTY_CONSTRAINTS

    for node in tree:
        matching = _get_matching_constraints(node.abs_path, constraints)

        for node_constraints in matching:
            for prop_constraint in node_constraints.constraints:
                if prop_constraint.constraint_type != ConstraintType.FORBIDDEN:
                    continue

                for prop_name in prop_constraint.properties:
                    try:
                        prop_val = node[prop_name]
                        if prop_val is not None:
                            msg = prop_constraint.message or \
                                f"forbidden property '{prop_name}' present"
                            results.append(ValidationResult(
                                check_name='schema_forbidden_props',
                                phase=ValidationPhase.POST_YAML,
                                passed=False,
                                message=f"{node.abs_path}: {msg}",
                                source_path=node.abs_path,
                                details={
                                    'property': prop_name,
                                    'pattern': node_constraints.node_pattern,
                                    'value': str(prop_val.value) if hasattr(prop_val, 'value') else str(prop_val),
                                }
                            ))
                    except (KeyError, TypeError):
                        # Property not present - good
                        pass

    if not any(not r.passed for r in results):
        results.append(ValidationResult(
            check_name='schema_forbidden_props',
            phase=ValidationPhase.POST_YAML,
            passed=True,
            message="No forbidden properties detected",
        ))

    return results


def check_required_properties(tree, constraints=None):
    """Check for required properties on nodes.

    This function validates that nodes contain all required properties.
    For example, memory nodes must have device_type and reg.

    Standalone function - can be wrapped by CheckHandler later.

    Args:
        tree: LopperTree to validate
        constraints: Optional constraint dictionary (default: NODE_PROPERTY_CONSTRAINTS)

    Returns:
        List of ValidationResult objects
    """
    results = []
    constraints = constraints or NODE_PROPERTY_CONSTRAINTS

    for node in tree:
        matching = _get_matching_constraints(node.abs_path, constraints)

        for node_constraints in matching:
            for prop_constraint in node_constraints.constraints:
                if prop_constraint.constraint_type != ConstraintType.REQUIRED:
                    continue

                for prop_name in prop_constraint.properties:
                    try:
                        prop_val = node[prop_name]
                        if prop_val is None or (hasattr(prop_val, 'value') and prop_val.value == ['']):
                            raise KeyError(prop_name)
                    except (KeyError, TypeError):
                        msg = prop_constraint.message or \
                            f"required property '{prop_name}' missing"
                        results.append(ValidationResult(
                            check_name='schema_required_props',
                            phase=ValidationPhase.POST_YAML,
                            passed=False,
                            message=f"{node.abs_path}: {msg}",
                            source_path=node.abs_path,
                            details={
                                'property': prop_name,
                                'pattern': node_constraints.node_pattern,
                            }
                        ))

    if not any(not r.passed for r in results):
        results.append(ValidationResult(
            check_name='schema_required_props',
            phase=ValidationPhase.POST_YAML,
            passed=True,
            message="All required properties present",
        ))

    return results


def check_property_values(tree, constraints=None):
    """Check that properties have expected values.

    This function validates CONST and ENUM constraints - properties
    that must have specific values or be one of a set of values.

    Standalone function - can be wrapped by CheckHandler later.

    Args:
        tree: LopperTree to validate
        constraints: Optional constraint dictionary (default: NODE_PROPERTY_CONSTRAINTS)

    Returns:
        List of ValidationResult objects
    """
    results = []
    constraints = constraints or NODE_PROPERTY_CONSTRAINTS

    for node in tree:
        matching = _get_matching_constraints(node.abs_path, constraints)

        for node_constraints in matching:
            for prop_constraint in node_constraints.constraints:
                if prop_constraint.constraint_type not in (ConstraintType.CONST, ConstraintType.ENUM):
                    continue

                for prop_name in prop_constraint.properties:
                    try:
                        prop_val = node[prop_name]
                        if prop_val is None:
                            continue

                        actual_value = prop_val.value if hasattr(prop_val, 'value') else prop_val
                        # Handle list values (e.g., ['memory'])
                        if isinstance(actual_value, list) and len(actual_value) == 1:
                            actual_value = actual_value[0]

                        expected = prop_constraint.expected_value

                        if prop_constraint.constraint_type == ConstraintType.CONST:
                            if actual_value != expected:
                                msg = prop_constraint.message or \
                                    f"property '{prop_name}' must be '{expected}', got '{actual_value}'"
                                results.append(ValidationResult(
                                    check_name='schema_prop_values',
                                    phase=ValidationPhase.POST_YAML,
                                    passed=False,
                                    message=f"{node.abs_path}: {msg}",
                                    source_path=node.abs_path,
                                    details={
                                        'property': prop_name,
                                        'expected': expected,
                                        'actual': actual_value,
                                        'pattern': node_constraints.node_pattern,
                                    }
                                ))

                        elif prop_constraint.constraint_type == ConstraintType.ENUM:
                            if actual_value not in expected:
                                msg = prop_constraint.message or \
                                    f"property '{prop_name}' must be one of {expected}, got '{actual_value}'"
                                results.append(ValidationResult(
                                    check_name='schema_prop_values',
                                    phase=ValidationPhase.POST_YAML,
                                    passed=False,
                                    message=f"{node.abs_path}: {msg}",
                                    source_path=node.abs_path,
                                    details={
                                        'property': prop_name,
                                        'allowed': expected,
                                        'actual': actual_value,
                                        'pattern': node_constraints.node_pattern,
                                    }
                                ))

                    except (KeyError, TypeError):
                        # Property not present - skip value check
                        pass

    if not any(not r.passed for r in results):
        results.append(ValidationResult(
            check_name='schema_prop_values',
            phase=ValidationPhase.POST_YAML,
            passed=True,
            message="Property values valid",
        ))

    return results


def check_mutex_properties(tree, constraints=None):
    """Check for mutually exclusive properties.

    This function validates that mutually exclusive properties are not
    both present on the same node. For example, reserved-memory nodes
    cannot have both no-map and reusable.

    Standalone function - can be wrapped by CheckHandler later.

    Args:
        tree: LopperTree to validate
        constraints: Optional constraint dictionary (default: NODE_PROPERTY_CONSTRAINTS)

    Returns:
        List of ValidationResult objects
    """
    results = []
    constraints = constraints or NODE_PROPERTY_CONSTRAINTS

    for node in tree:
        matching = _get_matching_constraints(node.abs_path, constraints)

        for node_constraints in matching:
            for prop_constraint in node_constraints.constraints:
                if prop_constraint.constraint_type != ConstraintType.MUTEX:
                    continue

                # Check which mutex properties are present
                present = []
                for prop_name in prop_constraint.properties:
                    try:
                        prop_val = node[prop_name]
                        if prop_val is not None:
                            present.append(prop_name)
                    except (KeyError, TypeError):
                        pass

                if len(present) > 1:
                    msg = prop_constraint.message or \
                        f"mutually exclusive properties present: {', '.join(present)}"
                    results.append(ValidationResult(
                        check_name='schema_mutex_props',
                        phase=ValidationPhase.POST_YAML,
                        passed=False,
                        message=f"{node.abs_path}: {msg}",
                        source_path=node.abs_path,
                        details={
                            'mutex_properties': prop_constraint.properties,
                            'present': present,
                            'pattern': node_constraints.node_pattern,
                        }
                    ))

    if not any(not r.passed for r in results):
        results.append(ValidationResult(
            check_name='schema_mutex_props',
            phase=ValidationPhase.POST_YAML,
            passed=True,
            message="No mutex violations detected",
        ))

    return results


# =============================================================================
# Schema Validator Class
# =============================================================================

@ValidatorRegistry.register
class SchemaValidator(BaseValidator):
    """Orchestrator for running schema-based validation checks.

    This class manages the execution of schema validation checks at
    appropriate pipeline phases and collects results. It inherits from
    BaseValidator and registers itself with the ValidatorRegistry.

    Constraints are loaded dynamically from dt-schema YAML files,
    making validation fully data-driven.
    """

    CATEGORY = "schema"

    # Warning flags this validator handles
    WARNING_FLAGS = [
        'schema_forbidden_props',
        'schema_required_props',
        'schema_prop_values',
        'schema_mutex_props',
    ]

    # Meta-flags that enable multiple checks
    META_FLAGS = {
        'schema_all': [
            'schema_forbidden_props',
            'schema_required_props',
            'schema_prop_values',
            'schema_mutex_props',
        ],
        'schema_reserved_memory': [
            'schema_forbidden_props',
            'schema_mutex_props',
        ],
    }

    # Map of warning flags to check functions and their phases
    CHECK_REGISTRY = {
        'schema_forbidden_props': (ValidationPhase.POST_YAML, check_forbidden_properties),
        'schema_required_props': (ValidationPhase.POST_YAML, check_required_properties),
        'schema_prop_values': (ValidationPhase.POST_YAML, check_property_values),
        'schema_mutex_props': (ValidationPhase.POST_YAML, check_mutex_properties),
    }

    def run_phase(self, phase, tree, **kwargs):
        """Run all enabled checks for a specific phase.

        Args:
            phase: The validation phase to run
            tree: LopperTree to validate
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

            results = check_func(tree)
            phase_results.extend(results)

        self.results.extend(phase_results)
        return phase_results


def validate_schema(tree, phase, warnings=None, werror=False):
    """Convenience function to run schema validation for a specific phase.

    This is the main entry point for running schema validation from
    the pipeline integration points.

    Args:
        tree: LopperTree to validate
        phase: Validation phase to run
        warnings: List of warning flags to enable
        werror: If True, treat warnings as errors

    Returns:
        Number of failed checks (errors)
    """
    validator = SchemaValidator(warnings=warnings, werror=werror)
    validator.run_phase(phase, tree)
    return validator.report()
