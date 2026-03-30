#/*
# * Copyright (c) 2026 AMD Inc. All rights reserved.
# *
# * Author:
# *       Bruce Ashfield <bruce.ashfield@amd.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

"""
Schema loader - loads schema definitions from multiple sources.

Search path (lowest to highest priority):
1. Built-in dt-schema (lopper/schema/dt-schema/schemas/)
2. User schemas (~/.config/lopper/schemas/)
3. Environment variable (LOPPER_SCHEMA_PATH, colon-separated)
4. Command-line override (--schema-dir)

Later sources override earlier ones, allowing vendor/user customization.
"""

import os
import glob as glob_module
from typing import Dict, List, Optional

import lopper.log

try:
    from ruamel.yaml import YAML
    _yaml = YAML()
    _yaml.preserve_quotes = True
    _use_ruamel = True
except ImportError:
    import yaml as pyyaml
    _yaml = None
    _use_ruamel = False

from .types import PropertyType, TypeDefinition, DT_SCHEMA_TYPES
from .core import (
    SchemaRegistry,
    NodeSpec,
    PropertySpec,
    Constraint,
    ConstraintType,
)


def _get_builtin_schema_dir() -> str:
    """Get path to built-in dt-schema files."""
    return os.path.join(os.path.dirname(__file__), 'dt-schema', 'schemas')


def get_schema_search_path(extra_dirs: List[str] = None) -> List[str]:
    """Get ordered list of schema directories to search.

    Returns directories in priority order (lowest to highest).
    Later directories can override schemas from earlier ones.

    Args:
        extra_dirs: Additional directories (e.g., from --schema-dir)

    Returns:
        List of directory paths
    """
    paths = []

    # 1. Built-in dt-schema (lowest priority)
    builtin = _get_builtin_schema_dir()
    if os.path.isdir(builtin):
        paths.append(builtin)

    # 2. XDG user config directory
    xdg_config = os.environ.get('XDG_CONFIG_HOME', os.path.expanduser('~/.config'))
    user_schemas = os.path.join(xdg_config, 'lopper', 'schemas')
    if os.path.isdir(user_schemas):
        paths.append(user_schemas)

    # 3. Environment variable (colon-separated)
    env_path = os.environ.get('LOPPER_SCHEMA_PATH', '')
    if env_path:
        for p in env_path.split(':'):
            p = p.strip()
            if p and os.path.isdir(p):
                paths.append(p)

    # 4. Extra directories from caller (e.g., --schema-dir)
    if extra_dirs:
        for p in extra_dirs:
            if os.path.isdir(p):
                paths.append(p)

    return paths


def load_all_schemas(
    registry: SchemaRegistry,
    extra_dirs: List[str] = None
) -> int:
    """Load schemas from all directories in search path.

    Later directories override earlier ones, allowing user/vendor
    customization of built-in schemas.

    Args:
        registry: SchemaRegistry to populate
        extra_dirs: Additional directories (e.g., from --schema-dir)

    Returns:
        Number of schemas loaded
    """
    count = 0

    # Always register built-in types first
    for name, type_def in DT_SCHEMA_TYPES.items():
        registry.register_type(name, type_def)

    # Load from each directory in order
    for schema_dir in get_schema_search_path(extra_dirs):
        count += _load_schemas_from_dir(registry, schema_dir)

    return count


def _load_schemas_from_dir(registry: SchemaRegistry, schema_dir: str) -> int:
    """Load all schema YAML files from a directory.

    Args:
        registry: SchemaRegistry to populate
        schema_dir: Directory to scan

    Returns:
        Number of schemas loaded
    """
    count = 0
    pattern = os.path.join(schema_dir, '**', '*.yaml')

    for schema_path in glob_module.glob(pattern, recursive=True):
        basename = os.path.basename(schema_path)

        if basename == 'types.yaml':
            # Types file - load type definitions
            if _load_types_yaml(registry, schema_path):
                count += 1
        else:
            # Constraint schema - load node constraints
            if _load_constraint_schema(registry, schema_path):
                count += 1

    return count


def _load_yaml_file(path: str) -> Optional[dict]:
    """Load a YAML file.

    Args:
        path: Path to YAML file

    Returns:
        Parsed YAML as dict, or None on error
    """
    try:
        with open(path, 'r') as f:
            if _use_ruamel and _yaml:
                return _yaml.load(f)
            else:
                return pyyaml.safe_load(f)
    except Exception as e:
        lopper.log._debug(f"schema: failed to load {path}: {e}")
        return None


def _load_types_yaml(registry: SchemaRegistry, path: str) -> bool:
    """Parse types.yaml and register type definitions.

    Args:
        registry: SchemaRegistry to populate
        path: Path to types.yaml

    Returns:
        True if successfully loaded
    """
    data = _load_yaml_file(path)
    if not data:
        return False

    definitions = data.get('definitions', {})
    for name, spec in definitions.items():
        type_def = _parse_type_definition(name, spec)
        if type_def:
            registry.register_type(name, type_def)
            lopper.log._debug(f"schema: loaded type {name} from {path}")

    return True


def _load_constraint_schema(registry: SchemaRegistry, path: str) -> bool:
    """Parse a dt-schema YAML and register node constraints.

    Args:
        registry: SchemaRegistry to populate
        path: Path to schema YAML file

    Returns:
        True if successfully loaded
    """
    data = _load_yaml_file(path)
    if not data:
        return False

    # Determine node pattern from file
    node_pattern = _schema_to_node_pattern(data, path)
    if not node_pattern:
        return False

    # Extract constraints
    constraints = []
    properties = {}

    # Required properties
    required = data.get('required', [])
    if required:
        constraints.append(Constraint(
            constraint_type=ConstraintType.REQUIRED,
            properties=required,
            message=f"required: {', '.join(required)}"
        ))

    # Forbidden properties (from 'not: required')
    not_block = data.get('not', {})
    if isinstance(not_block, dict):
        forbidden = not_block.get('required', [])
        if forbidden:
            constraints.append(Constraint(
                constraint_type=ConstraintType.FORBIDDEN,
                properties=forbidden,
                message=f"forbidden: {', '.join(forbidden)}"
            ))

    # Property type definitions and const/enum constraints
    for prop_name, prop_schema in data.get('properties', {}).items():
        prop_spec = _parse_property_schema(prop_name, prop_schema)
        if prop_spec:
            properties[prop_name] = prop_spec

        # Extract const/enum constraints
        if isinstance(prop_schema, dict):
            if 'const' in prop_schema:
                constraints.append(Constraint(
                    constraint_type=ConstraintType.CONST,
                    properties=[prop_name],
                    expected_value=prop_schema['const'],
                    message=f"{prop_name} must be '{prop_schema['const']}'"
                ))
            elif 'enum' in prop_schema:
                constraints.append(Constraint(
                    constraint_type=ConstraintType.ENUM,
                    properties=[prop_name],
                    expected_value=prop_schema['enum'],
                    message=f"{prop_name} must be one of {prop_schema['enum']}"
                ))

    # Mutex constraints from dependentSchemas
    mutex_pairs = _extract_mutex_constraints(data.get('dependentSchemas', {}))
    for mutex_props in mutex_pairs:
        constraints.append(Constraint(
            constraint_type=ConstraintType.MUTEX,
            properties=mutex_props,
            message=f"mutually exclusive: {', '.join(mutex_props)}"
        ))

    if not constraints and not properties:
        return False

    # Register node spec
    node_spec = NodeSpec(
        node_pattern=node_pattern,
        properties=properties,
        constraints=constraints,
        description=data.get('description', data.get('title', '')),
        schema_file=path
    )

    # Use basename (without parent dirs that might conflict)
    name = os.path.splitext(os.path.basename(path))[0]

    # Handle name conflicts by adding parent directory
    existing = registry.get_node_spec(name)
    if existing and existing.schema_file != path:
        parent = os.path.basename(os.path.dirname(path))
        name = f"{parent}-{name}"

    registry.register_node_spec(name, node_spec)
    lopper.log._debug(f"schema: loaded {name} -> {node_pattern} from {path}")

    return True


def _schema_to_node_pattern(data: dict, path: str) -> Optional[str]:
    """Convert schema to node pattern.

    Uses filename and/or $id to determine which nodes this schema applies to.

    Args:
        data: Parsed schema data
        path: Path to schema file

    Returns:
        Node pattern string, or None if cannot determine
    """
    # Map known filenames to patterns
    patterns = {
        'reserved-memory.yaml': '/reserved-memory/*',
        'memory.yaml': '/memory@*',
    }

    basename = os.path.basename(path)
    if basename in patterns:
        return patterns[basename]

    # Check $id field
    schema_id = data.get('$id', '')
    for filename, pattern in patterns.items():
        if filename in schema_id:
            return pattern

    # Check for explicit node_pattern in schema (extension)
    if 'node_pattern' in data:
        return data['node_pattern']

    return None


def _parse_type_definition(name: str, spec: dict) -> Optional[TypeDefinition]:
    """Parse a type definition from dt-schema types.yaml.

    Args:
        name: Type name
        spec: Type specification dict

    Returns:
        TypeDefinition or None
    """
    if not isinstance(spec, dict):
        return None

    prop_type = _json_schema_to_property_type(spec, name)

    return TypeDefinition(
        property_type=prop_type,
        min_value=spec.get('minimum'),
        max_value=spec.get('maximum'),
        min_items=spec.get('minItems'),
        max_items=spec.get('maxItems'),
        source="dt-schema",
        description=spec.get('description')
    )


def _parse_property_schema(name: str, spec: dict) -> Optional[PropertySpec]:
    """Parse a property schema from dt-schema.

    Args:
        name: Property name
        spec: Property schema dict

    Returns:
        PropertySpec or None
    """
    if not isinstance(spec, dict):
        return None

    prop_type = _json_schema_to_property_type(spec, name)
    type_def = TypeDefinition(
        property_type=prop_type,
        min_value=spec.get('minimum'),
        max_value=spec.get('maximum'),
        source="dt-schema",
        description=spec.get('description')
    )

    return PropertySpec(
        name=name,
        type_def=type_def,
        confidence=1.0,
        source="dt-schema"
    )


def _json_schema_to_property_type(spec: dict, name: str) -> PropertyType:
    """Convert JSON schema type to PropertyType.

    Args:
        spec: JSON schema dict
        name: Property/type name for context

    Returns:
        PropertyType enum value
    """
    schema_type = spec.get('type', 'unknown')

    if schema_type == 'integer':
        max_val = spec.get('maximum', 0xffffffff)
        min_val = spec.get('minimum', 0)

        # Check for signed
        if min_val < 0:
            if max_val <= 127:
                return PropertyType.INT8
            elif max_val <= 32767:
                return PropertyType.INT16
            elif max_val <= 2147483647:
                return PropertyType.INT32
            else:
                return PropertyType.INT64

        # Unsigned
        if max_val <= 255:
            return PropertyType.UINT8
        elif max_val <= 65535:
            return PropertyType.UINT16
        elif max_val <= 0xffffffff:
            return PropertyType.UINT32
        else:
            return PropertyType.UINT64

    elif schema_type == 'string':
        return PropertyType.STRING

    elif schema_type == 'boolean':
        return PropertyType.FLAG

    elif schema_type == 'array':
        items = spec.get('items', {})
        items_type = items.get('type', 'unknown') if isinstance(items, dict) else 'unknown'

        if items_type == 'string':
            return PropertyType.STRING_ARRAY
        elif items_type == 'integer':
            # Determine array element size
            max_val = items.get('maximum', 0xffffffff) if isinstance(items, dict) else 0xffffffff
            if max_val <= 255:
                return PropertyType.UINT8_ARRAY
            elif max_val <= 65535:
                return PropertyType.UINT16_ARRAY
            elif max_val <= 0xffffffff:
                return PropertyType.UINT32_ARRAY
            else:
                return PropertyType.UINT64_ARRAY
        else:
            return PropertyType.UINT32_ARRAY  # Default for unknown arrays

    return PropertyType.UNKNOWN


def _extract_mutex_constraints(dependent: dict) -> List[List[str]]:
    """Extract mutex property pairs from dependentSchemas.

    In dt-schema, mutual exclusivity is expressed as:
        dependentSchemas:
          prop_a:
            not:
              required: [prop_b]

    Args:
        dependent: The dependentSchemas dict

    Returns:
        List of mutex property lists
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
