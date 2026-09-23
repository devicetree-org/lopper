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


# Sections that mark a file as being in lopper's own schema form, which is
# what "--schema learn:<file>" writes, so a learned schema can be fed back in.
LOPPER_SCHEMA_SECTIONS = (
    'overrides',
    'property_definitions',
    'node_patterns',
    'path_overrides',
    'compatible_mappings',
    'property_patterns',
)

# Sections carried through unchanged when a lopper form schema is loaded.
_LEARNED_SECTIONS = LOPPER_SCHEMA_SECTIONS[1:]


# An inline type statement, given on the command line instead of in a file.
INLINE_SCHEMA_PREFIX = 'type:'


def load_external_schema(sources) -> dict:
    """Load the schemas supplied to lopper

    A source is a schema file, a directory of them, or an inline statement
    prefixed with "type:". Sources are applied in the order given, so a later
    one can restate a type an earlier one set.

    Two file forms are accepted, told apart by content rather than by
    filename: lopper's own form, carrying an 'overrides' section and/or the
    sections that "--schema learn:<file>" writes, so a learned schema round
    trips; and dt-schema, as published for devicetree bindings, whose
    'properties' are read for their types.

    Args:
        sources (str or list): schema files, directories, or inline statements

    Returns:
        dict: a schema whose 'overrides' section states the types found
    """
    if isinstance(sources, str):
        sources = [sources]

    merged = { 'overrides': { 'properties': {}, 'node_patterns': {}, 'paths': {} } }

    total = 0
    for source in sources:
        if source.startswith( INLINE_SCHEMA_PREFIX ):
            count = _merge_inline_schema( merged, source[len(INLINE_SCHEMA_PREFIX):], source )
            lopper.log._info( f"schema: {source}: {count} type statement(s)" )
            total += count
            continue

        files = _external_schema_files(source)
        if not files:
            lopper.log._error( f"schema: no yaml files found under {source}", also_exit=1 )

        for schema_file in files:
            data = _load_yaml_file(schema_file)
            if data is None:
                lopper.log._error( f"schema: {schema_file} could not be read as yaml", also_exit=1 )

            kind = _external_schema_kind( data, schema_file )
            if kind == 'lopper':
                count = _merge_lopper_schema( merged, data, schema_file )
            else:
                count = _merge_dt_schema( merged, data, schema_file )

            lopper.log._info( f"schema: {schema_file}: {count} type definition(s)" )
            total += count

    if not total:
        # Supplying a schema and getting nothing out of it is the failure this
        # whole path exists to avoid, so it is called out rather than left to
        # be discovered in the output.
        lopper.log._warning( f"schema: {', '.join(sources)} supplied no property types" )

    return merged


def _merge_inline_schema(merged: dict, spec: str, source: str) -> int:
    """Merge an inline type statement

    The statement is one or more "name=type" pairs separated by ';'. A comma
    cannot be the separator: property names contain them, as in
    "xlnx,ddr-freq".

    A name may carry a scope ahead of it, split on the last '/'. A leading '/'
    makes it an exact node path, anything else a node pattern, and no scope at
    all states the type wherever the property appears:

        xlnx,ddr-freq=uint32
        memory-controller@*/xlnx,ddr-freq=uint32
        /axi/memory-controller@fd070000/reg=uint64
    """
    count = 0
    for entry in spec.split(';'):
        entry = entry.strip()
        if not entry:
            continue

        name, assigned, type_name = entry.partition('=')
        if not assigned or not name or not type_name:
            lopper.log._error( f"schema: {source}: '{entry}' is not a type statement. "
                               f"Expected [scope/]<property>=<type>, for example "
                               f"'xlnx,ddr-freq=uint32'", also_exit=1 )

        _validate_type_name( type_name, name, source )

        if '/' in name:
            scope, _, prop_name = name.rpartition('/')
            if not prop_name:
                lopper.log._error( f"schema: {source}: '{entry}' names no property "
                                   f"after its scope", also_exit=1 )

            if scope.startswith('/'):
                bucket = merged['overrides']['paths'].setdefault( scope, {} )
            else:
                bucket = merged['overrides']['node_patterns'].setdefault( scope, {} )
        else:
            bucket = merged['overrides']['properties']
            prop_name = name

        bucket[prop_name] = type_name
        count += 1

    return count


def _external_schema_files(path: str) -> List[str]:
    """Collect the schema files named by a path, recursing into a directory."""
    if os.path.isdir(path):
        found = []
        for extension in ('yaml', 'yml'):
            found += glob_module.glob( os.path.join(path, '**', f'*.{extension}'),
                                       recursive=True )
        return sorted(found)

    return [path]


def _external_schema_kind(data, path: str) -> str:
    """Decide which form a loaded schema file is in."""
    if not isinstance(data, dict):
        lopper.log._error( f"schema: {path} is not a yaml mapping", also_exit=1 )

    if any( section in data for section in LOPPER_SCHEMA_SECTIONS ):
        return 'lopper'

    if ('$schema' in data or '$id' in data) and 'properties' in data:
        return 'dt-schema'

    lopper.log._error( f"schema: {path} is in no recognised form. Expected either a lopper "
                       f"schema, with one of: {', '.join(LOPPER_SCHEMA_SECTIONS)}; or a "
                       f"dt-schema binding, with $id or $schema and a properties section",
                       also_exit=1 )


def _validate_type_name(type_name, prop_name: str, path: str):
    """Refuse an unknown type name at load time, naming where it came from."""
    try:
        PropertyType(type_name)
    except ValueError:
        from .learned import property_type_names
        lopper.log._error( f"schema: {path}: property '{prop_name}' has unknown type "
                           f"'{type_name}'. valid types: {property_type_names()}",
                           also_exit=1 )


def _merge_override_scope(target: dict, incoming, path: str) -> int:
    """Merge one scope of an overrides section, validating type names."""
    count = 0
    for prop_name, type_name in (incoming or {}).items():
        if isinstance(type_name, str):
            _validate_type_name( type_name, prop_name, path )
        target[prop_name] = type_name
        count += 1

    return count


def _merge_lopper_schema(merged: dict, data: dict, path: str) -> int:
    """Merge a schema written in lopper's own form."""
    overrides = data.get('overrides') or {}

    count = _merge_override_scope( merged['overrides']['properties'],
                                   overrides.get('properties'), path )

    for scope in ('node_patterns', 'paths'):
        for key, props in (overrides.get(scope) or {}).items():
            bucket = merged['overrides'][scope].setdefault(key, {})
            count += _merge_override_scope( bucket, props, path )

    # A learned schema carries its observations in these sections. They are
    # kept as they are: they rank below the overrides above, which is what
    # lets a stated type correct something that was learned wrongly.
    for section in _LEARNED_SECTIONS:
        if section in data:
            merged.setdefault(section, {}).update( data[section] or {} )
            count += len( data[section] or {} )

    return count


def _merge_dt_schema(merged: dict, data: dict, path: str) -> int:
    """Merge a dt-schema binding, reading its properties for their types.

    A binding's properties include generic names such as reg and status, whose
    meaning depends on the node they appear in. Applying those to every node
    would corrupt trees the binding says nothing about, so a binding has to
    say which nodes it describes, and one that does not is refused.
    """
    scope = _schema_to_node_pattern( data, path )
    if not scope:
        lopper.log._error( f"schema: {path} does not say which nodes it describes, and its "
                           f"properties include names whose type depends on the node. Add a "
                           f"'node_pattern' key naming the nodes it applies to, for example "
                           f"'node_pattern: memory-controller@*'", also_exit=1 )

    # Node patterns are matched against a path's trailing components, so a
    # leading separator would never match.
    bucket = merged['overrides']['node_patterns'].setdefault( scope.lstrip('/'), {} )

    count = 0
    for prop_name, prop_schema in (data.get('properties') or {}).items():
        if not isinstance(prop_schema, dict):
            continue

        prop_type = _json_schema_to_property_type( prop_schema, prop_name )
        if prop_type == PropertyType.UNKNOWN:
            lopper.log._debug( f"schema: {path}: no type for '{prop_name}', leaving it alone" )
            continue

        bucket[prop_name] = prop_type.value
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


def _property_type_from_ref(spec: dict) -> Optional[PropertyType]:
    """Resolve a dt-schema type reference to a PropertyType.

    The fragment of a types.yaml reference is the type name, and those names
    are PropertyType's values, so "types.yaml#/definitions/uint32" resolves
    directly. References nested inside allOf/oneOf/anyOf are followed, since
    bindings commonly wrap a reference alongside a constraint.

    Args:
        spec: JSON schema dict

    Returns:
        PropertyType, or None if the spec carries no resolvable reference
    """
    if not isinstance(spec, dict):
        return None

    ref = spec.get('$ref')
    if not ref:
        for combiner in ('allOf', 'oneOf', 'anyOf'):
            for entry in spec.get(combiner) or []:
                found = _property_type_from_ref(entry)
                if found is not None:
                    return found
        return None

    fragment = ref.split('/')[-1]
    try:
        return PropertyType(fragment)
    except ValueError:
        lopper.log._debug(f"schema: unrecognised type reference '{ref}'")
        return None


def _json_schema_to_property_type(spec: dict, name: str) -> PropertyType:
    """Convert JSON schema type to PropertyType.

    Args:
        spec: JSON schema dict
        name: Property/type name for context

    Returns:
        PropertyType enum value
    """
    # dt-schema states most property types by reference into types.yaml
    # rather than with a json 'type', so a binding that says
    #   $ref: types.yaml#/definitions/uint32
    # has no 'type' key at all and would otherwise resolve to UNKNOWN.
    ref_type = _property_type_from_ref(spec)
    if ref_type is not None:
        return ref_type

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
