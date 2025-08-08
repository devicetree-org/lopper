#/*
# * Copyright (c) 2025 Advanced Micro Devices, Inc. All rights reserved.
# *
# * Author:
# *       Bruce Ashfield <bruce.ashfield@amd.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

import yaml
import re
from collections import defaultdict
from typing import Dict, Any, List, Set, Optional, Union, Tuple
from lopper import LopperFmt
from lopper.base import lopper_base
import os

# Add properties to debug as needed
PROPERTY_DEBUG_LIST = [
    # 'xlnx,buffer-base',
    # 'xlnx,buffer-index',
    # 'xlnx,csr-slcr',
    # 'ranges',
]
PROPERTY_DEBUG_SET = set(PROPERTY_DEBUG_LIST)

# Property name heuristics for type detection
PROPERTY_NAME_HEURISTICS = {
    # Suffix patterns (checked with endswith)
    'suffixes': {
        '-names': LopperFmt.MULTI_STRING,
        '-cells': LopperFmt.UINT32,
        '-gpio': LopperFmt.UINT32,
        '-gpios': LopperFmt.UINT32,
        '-supply': LopperFmt.UINT32,
        '-phy': LopperFmt.UINT32,
        '-phys': LopperFmt.UINT32,
        '-map': LopperFmt.UINT32,
        '-mask': LopperFmt.UINT32,
        '-ranges': LopperFmt.UINT32,
    },

    # Prefix patterns (checked with startswith)
    'prefixes': {
        '#': {  # Properties starting with #
            '-cells': LopperFmt.UINT32,  # Combined with suffix check
        }
    },

    # Exact property names
    'exact': {
        # Standard DT properties
        'compatible': LopperFmt.STRING,
        'status': LopperFmt.STRING,
        'device_type': LopperFmt.STRING,
        'model': LopperFmt.STRING,
        'label': LopperFmt.STRING,

        # Phandle properties
        'phandle': LopperFmt.UINT32,
        'linux,phandle': LopperFmt.UINT32,

        # Address/size properties
        'reg': LopperFmt.UINT32,
        'ranges': LopperFmt.UINT32,
        'dma-ranges': LopperFmt.UINT32,
        '#address-cells': LopperFmt.UINT32,
        '#size-cells': LopperFmt.UINT32,

        # Interrupt properties
        'interrupts': LopperFmt.UINT32,
        'interrupt-parent': LopperFmt.UINT32,
        '#interrupt-cells': LopperFmt.UINT32,

        'interrupt-map': LopperFmt.UINT32,  # It's a phandle+cells array
        'interrupt-map-mask': LopperFmt.UINT32,
        'interrupt-map-pass-thru': LopperFmt.UINT32,

        # Clock properties
        'clocks': LopperFmt.UINT32,
        'clock-frequency': LopperFmt.UINT32,
        'clock-output-names': LopperFmt.MULTI_STRING,

        # Memory properties
        'memory-region': LopperFmt.UINT32,
        'iommus': LopperFmt.UINT32,

        # Boolean properties
        'no-map': LopperFmt.EMPTY,
        'read-only': LopperFmt.EMPTY,
        'disabled': LopperFmt.EMPTY,
        'okay': LopperFmt.EMPTY,
        'fail': LopperFmt.EMPTY,
        'fail-sss': LopperFmt.EMPTY,
    },

    # Regex patterns (more complex than suffix/prefix)
    'patterns': {
        r'.*,.*': LopperFmt.STRING,  # Vendor prefixed properties often strings
    }
}


# Property type hints for DTS scanning
# These help the scanner correctly identify property types during initial parsing
PROPERTY_TYPE_HINTS = {
    # Properties that should never be treated as 64-bit values
    # even if they have exactly 2 cells
    'phandle_array_properties': [
        'clocks',
        'resets',
        'power-domains',
        'phys',
        'mboxes',
        'dmas',
        'interrupt-map',
        'interrupts-extended',
        'iommus',
        'thermal-sensors',
        'sound-dai',
        'nvmem-cells',
        'interconnects',
        'operating-points-v2',
        'cpu-idle-states',
    ],

    # Properties that may contain 64-bit values (addresses/sizes)
    'potential_64bit_properties': [
        'reg',
        'ranges',
        'dma-ranges',
        'memory-region',
    ],

    # Properties with specific cell groupings
    'cell_groupings': {
        'reg': 2,          # address, size pairs
        'ranges': 3,       # child-address, parent-address, size
        'dma-ranges': 3,   # child-address, parent-address, size
        'interrupts': 3,   # typically 3 cells per interrupt
        'clocks': 2,       # phandle + clock-specifier
        'resets': 2,       # phandle + reset-specifier
        'power-domains': 2, # phandle + power-domain-specifier
    },

    # Properties that are always strings (never cells)
    'string_properties': [
        'compatible',
        'model',
        'status',
        'device_type',
        'label',
        'bootargs',
        'stdout-path',
        'phy-mode',
        'dr_mode',
        'maximum-speed',
        'enable-method',
        'entry-method',
    ],

    # Properties that are always boolean (empty)
    'boolean_properties': [
        'no-map',
        'disabled',
        'okay',
        'fail',
        'interrupt-controller',
        'gpio-controller',
        'msi-controller',
        'dma-coherent',
        'cts-override',
        'dis-u2-susphy-quirk',
        'dis-u3-susphy-quirk',
    ],
}

def _get_schema_hash(schema_dict):
    """Generate a hash of the schema for cache invalidation"""
    import hashlib
    import json
    # Convert schema to a stable string representation
    schema_str = json.dumps(schema_dict, sort_keys=True)
    return hashlib.md5(schema_str.encode()).hexdigest()

class SchemaManager:
    """Singleton manager for schema and related tools"""
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.schema = None
            cls._instance.resolver = None
            cls._instance.checker = None
            cls._instance.validator = None
            cls._instance.schema_hash = None
        return cls._instance

    def update_schema(self, schema_dict):
        """Update schema and recreate tools if needed"""
        new_hash = _get_schema_hash(schema_dict)

        if self.schema_hash != new_hash:
            self.schema = schema_dict
            self.schema_hash = new_hash
            self.resolver = DTSPropertyTypeResolver(schema_dict)
            self.checker = DTSTypeChecker(schema_dict)
            self.validator = DTSValidator(schema_dict) if 'DTSValidator' in globals() else None

    def get_resolver(self):
        """Get resolver, creating if needed"""
        if self.resolver is None and self.schema is not None:
            self.resolver = DTSPropertyTypeResolver(self.schema)
        return self.resolver

    def get_tools(self):
        """Get all tools as a tuple"""
        return (self.resolver, self.checker, self.validator)

# Global instance
_schema_manager = SchemaManager()

def get_schema_manager():
    """Get the global schema manager instance"""
    return _schema_manager

class DTSSchemaGenerator:
    def __init__(self):
        self.properties = defaultdict(list)
        self.node_patterns = defaultdict(set)
        self.compatible_properties = defaultdict(set)
        self.path_properties = defaultdict(set)
        self.phandle_refs = set()
        self.nodes = []

        # Load type hints
        self.type_hints = PROPERTY_TYPE_HINTS

    def scan_dts_file(self, dts_content):
        """Parse DTS content and extract property information"""
        # Reset state
        self.__init__()

        analyzed_patterns, phandle_map = lopper_base.analyze_phandle_patterns(dts_content)
        newly_learned = lopper_base.update_phandle_property_descriptions(analyzed_patterns)

        if newly_learned:
            for prop, pattern in newly_learned.items():
                if prop in PROPERTY_DEBUG_SET:
                    print(f"DEBUG: Learned phandle pattern for {prop}: {pattern}")

        # Simple state machine for parsing
        current_path = []
        current_compatible = None

        lines = dts_content.split('\n')
        i = 0

        while i < len(lines):
            line = lines[i].strip()

            # Skip comments and empty lines
            if not line or line.startswith('//') or line.startswith('/*'):
                i += 1
                continue

            # Node opening
            node_match = re.match(r'([\w-]+)(@[\w,.-]+)?\s*{', line)
            if node_match:
                node_name = node_match.group(1)
                node_addr = node_match.group(2) or ''
                current_path.append(node_name + node_addr)

                # Track node patterns
                if '@' in node_name + node_addr:
                    pattern = re.sub(r'@[\w,.-]+', '@*', node_name + node_addr)
                    self.node_patterns[pattern].add('/'.join(current_path))

                self.nodes.append({
                    'path': '/'.join(current_path),
                    'name': node_name,
                    'properties': {},
                    'compatible': None
                })
                i += 1
                continue

            # Node closing
            if line == '};':
                if current_path:
                    current_path.pop()
                current_compatible = None
                i += 1
                continue

            # Property parsing - handle multi-line properties
            prop_match = re.match(r'([#\w,.-]+)\s*=?\s*(.*?)$', line)
            if prop_match:
                prop_name = prop_match.group(1)
                prop_value = prop_match.group(2).strip()

                # Check if this is a boolean property (no = sign and value is just semicolon)
                if '=' not in line and prop_value == ';':
                    # Boolean property
                    prop_value = ''  # Empty value for boolean
                else:
                    # Check if the property is complete (ends with semicolon)
                    is_complete = prop_value.endswith(';')

                    # Remove trailing semicolon if present
                    if is_complete:
                        prop_value = prop_value[:-1].strip()

                    # Only check for multi-line if the property didn't end with semicolon
                    if not is_complete and '=' in line:
                        # Multi-line property - collect all lines until we find the semicolon
                        value_lines = [prop_value]
                        j = i + 1
                        while j < len(lines):
                            next_line = lines[j].strip()
                            if next_line:
                                value_lines.append(next_line)
                                if next_line.endswith(';'):
                                    # Remove semicolon from last line
                                    value_lines[-1] = value_lines[-1][:-1].strip()
                                    break
                            j += 1
                        prop_value = ' '.join(value_lines)
                        i = j  # Skip the lines we just processed

                # Check if this is a property assignment
                if '=' in line or prop_value == '':  # Empty value indicates boolean
                    # Determine property type
                    prop_type = self._determine_property_type(prop_name, prop_value)

                    # Store property info
                    self.properties[prop_name].append({
                        'type': prop_type,
                        'value': prop_value,
                        'original_value': prop_value,  # Keep original for safety
                        'path': '/'.join(current_path),
                        'compatible': current_compatible
                    })

                    # Track compatible string
                    if prop_name == 'compatible':
                        current_compatible = self._extract_compatible(prop_value)
                        if self.nodes:
                            self.nodes[-1]['compatible'] = current_compatible

                    # Track phandle references
                    if '&' in prop_value:
                        self.phandle_refs.update(re.findall(r'&(\w+)', prop_value))

                    # Update node properties
                    if self.nodes and current_path:
                        self.nodes[-1]['properties'][prop_name] = prop_type

                    # Track path-specific properties
                    full_path = '/'.join(current_path)
                    self.path_properties[full_path].add((prop_name, prop_type))

            i += 1

        return analyzed_patterns, phandle_map

    def _extract_context_lookups(self, pattern_desc):
        """Extract property lookups from phandle pattern"""
        lookups = []

        # Find :#property patterns
        for match in re.finditer(r'(phandle|^)?:#([\w-]+)', pattern_desc):
            target = 'target' if match.group(1) == 'phandle' else 'self'
            if match.group(1) == '^':
                target = 'parent'

            lookups.append({
                'target': target,
                'property': match.group(2)
            })

        return lookups if lookups else None

    def _determine_phandle_type(self, pattern_desc, repeat_flag):
        """Determine specific phandle type from pattern description"""

        # Debug output
        if any(prop in PROPERTY_DEBUG_SET for prop in ['_phandle_types']):
            print(f"DEBUG _determine_phandle_type: pattern='{pattern_desc}', repeat={repeat_flag}")

        # Check for variable size patterns (contain size lookups)
        if ':#' in pattern_desc:
            # Has size lookups - can't determine fixed grouping
            return 'phandle-array-variable'

        # Parse pattern to count cells per group
        parts = pattern_desc.split()
        cells_per_group = 0

        for part in parts:
            if part == 'phandle':
                cells_per_group += 1
            elif part == 'field':
                cells_per_group += 1
            elif part.startswith('#'):
                # Local property lookup
                cells_per_group += 1
            elif part.startswith('^:#'):
                # Parent property lookup
                cells_per_group += 1

        # Determine type based on grouping and repeat
        if cells_per_group == 1 and not repeat_flag:
            return 'phandle'  # Single phandle reference
        elif repeat_flag:
            return 'phandle-array'  # Repeating pattern
        elif cells_per_group > 1:
            # Fixed size group
            return f'phandle-array-{cells_per_group}'
        else:
            # Default to generic array
            return 'phandle-array'

    def _determine_property_type(self, name, value):
        """Determine the type of a property from its value"""

        # Check explicit type hints first
        if name in self.type_hints.get('string_properties', []):
            return 'string' if '"' in value else 'unknown'

        if name in self.type_hints.get('boolean_properties', []):
            return 'boolean'

        # Generic debug for tracked properties
        if name in PROPERTY_DEBUG_SET:
            print(f"DEBUG _determine_property_type: {name} = '{value}'")

        if not value:  # Boolean property
            return 'boolean'
        elif value.startswith('<') and value.endswith('>'):
            # Cell or cell array
            cells = value[1:-1].strip().split()

            if name in PROPERTY_DEBUG_SET:
                print(f"  Cells: {cells}")
                print(f"  Cell count: {len(cells)}")
                print(f"  Determined type: uint32-array" if len(cells) > 1 else "  Determined type: uint32")

            if not cells:
                return 'empty'
            elif '&' in value:
                return 'phandle-array'
            elif len(cells) == 1:
                return 'uint32'
            elif len(cells) == 2:
                # Check if this is a phandle array property
                if name in self.type_hints.get('phandle_array_properties', []):
                    # This is a phandle+specifier, not a 64-bit value
                    grouping = self._determine_cell_grouping(name, cells)
                    if grouping > 1:
                        return f'uint32-matrix-{grouping}'
                    return 'uint32-array'
                # Check if this could be a 64-bit value
                elif name in self.type_hints.get('potential_64bit_properties', []) and self._is_64bit_value(cells):
                    return 'uint64'
                else:
                    # Default to array for 2 cells
                    return 'uint32-array'
            else:
                # Multiple cells
                grouping = self._determine_cell_grouping(name, cells)
                if grouping > 1:
                    return f'uint32-matrix-{grouping}'
                return 'uint32-array'
        elif value.startswith('[') and value.endswith(']'):
            return 'uint8-array'
        elif '"' in value:
            # Has quotes, so it's a string type
            if '", "' in value or '","' in value:
                return 'string-array'
            else:
                return 'string'
        else:
            return 'unknown'

    def _determine_cell_grouping(self, prop_name: str, cells):
        """Determine if cells should be grouped"""
        # First check the type hints
        groupings = self.type_hints.get('cell_groupings', {})
        if prop_name in groupings:
            expected_grouping = groupings[prop_name]
            # Verify the cell count matches expected grouping
            if len(cells) % expected_grouping == 0:
                return expected_grouping

        # Fallback to original logic
        cell_count = len(cells)

        if prop_name in ['reg', 'ranges']:
            if prop_name == 'reg' and cell_count % 2 == 0:
                return 2
            elif prop_name == 'ranges' and cell_count % 3 == 0:
                return 3
        elif prop_name in ['interrupts'] and cell_count % 3 == 0:
            return 3
        elif prop_name in ['clocks', 'resets'] and cell_count % 2 == 0:
            return 2

        return 1

    def _is_64bit_value(self, cells):
        """Check if two cells represent a 64-bit value"""
        if len(cells) != 2:
            return False
        # Common pattern for 64-bit values
        return all(c.startswith('0x') for c in cells)

    def _extract_compatible(self, value):
        """Extract first compatible string from property value"""
        match = re.search(r'"([^"]+)"', value)
        return match.group(1) if match else None

    def _determine_cell_grouping(self, prop_name, cells):
        """Determine if cells should be grouped (e.g., reg = <addr size>)"""
        # Common patterns for grouped cells
        if prop_name in ['reg', 'ranges']:
            if prop_name == 'reg' and len(cells) % 2 == 0:
                return 2
            elif prop_name == 'ranges' and len(cells) % 3 == 0:
                return 3
        elif prop_name in ['interrupts'] and len(cells) % 3 == 0:
            return 3
        elif prop_name in ['clocks', 'resets'] and len(cells) % 2 == 0:
            return 2

        # Default: no grouping
        return 1

    def generate_schema(self):
        """Generate complete YAML schema from scanned data"""
        schema = {
            '$schema': 'http://json-schema.org/draft-07/schema#',
            'title': 'Device Tree Schema',
            'type': 'object',
            'property_definitions': self._build_property_definitions(),
            'node_patterns': self._build_node_patterns(),
            'compatible_mappings': self._build_compatible_mappings(),
            'path_overrides': self._build_path_overrides(),
            'properties': self._generate_root_properties()
        }

        return schema

    def _build_property_definitions(self):
        """Build global property type definitions"""
        definitions = {}
        phandle_dict = lopper_base.phandle_possible_properties()

        for prop_name, occurrences in self.properties.items():
            type_counts = defaultdict(int)
            for occ in occurrences:
                type_counts[occ['type']] += 1

            if prop_name in PROPERTY_DEBUG_SET:
                print(f"\nDEBUG _build_property_definitions: {prop_name}")
                print(f"  Type counts: {dict(type_counts)}")
                print(f"  Occurrences: {len(occurrences)}")

            # Special handling for properties that can be uint32 or "NIL"
            if 'uint32' in type_counts and 'string' in type_counts:
                # Check if all string occurrences are "NIL"
                string_values = [occ['value'] for occ in occurrences if occ['type'] == 'string']
                if all(val == '"NIL"' for val in string_values):
                    # This is a uint32 that can also be "NIL"
                    definitions[prop_name] = {
                        'oneOf': [
                            self._get_property_schema_def('uint32'),
                            {'type': 'string', 'enum': ['NIL']}
                        ]
                    }
                    if prop_name in PROPERTY_DEBUG_SET:
                        print(f"  Created union type: uint32 | 'NIL'")
                    continue

            # Normalize compatible types
            if 'uint32' in type_counts and 'uint32-array' in type_counts:
                if prop_name in PROPERTY_DEBUG_SET:
                    print(f"  Normalizing uint32 + uint32-array → uint32-array")
                type_counts['uint32-array'] += type_counts['uint32']
                del type_counts['uint32']

            unique_types = len(type_counts)

            if unique_types > 1:
                if prop_name in PROPERTY_DEBUG_SET:
                    print(f"  SKIPPING due to multiple types!")
                continue

            # Get the (possibly normalized) type
            most_common_type = max(type_counts, key=type_counts.get)
            definitions[prop_name] = self._get_property_schema_def(most_common_type)

        # Enrich definitions with phandle pattern information
        for prop_name in definitions:
            if prop_name in phandle_dict:
                pattern_desc = phandle_dict[prop_name][0]

                # Add phandle pattern metadata
                if 'phandle' in pattern_desc:
                    definitions[prop_name]['phandle-pattern'] = pattern_desc

                    # Extract context lookups
                    lookups = self._extract_context_lookups(pattern_desc)
                    if lookups:
                        definitions[prop_name]['context-lookups'] = lookups

                    if prop_name in PROPERTY_DEBUG_SET:
                        print(f"  Added phandle metadata: pattern='{pattern_desc}'")

        return definitions

    def _build_node_patterns(self):
        """Build node pattern definitions"""
        patterns = {}

        for pattern, paths in self.node_patterns.items():
            # Collect properties for this node pattern
            pattern_props = defaultdict(set)

            for node in self.nodes:
                if self._matches_pattern(node['path'], pattern):
                    for prop_name, prop_type in node['properties'].items():
                        pattern_props[prop_name].add(prop_type)

            # Build schema for pattern
            patterns[pattern] = {
                'type': 'object',
                'properties': {
                    prop: self._get_property_schema_def(list(types)[0])
                    for prop, types in pattern_props.items()
                    if len(types) == 1
                }
            }

        return patterns

    def _build_compatible_mappings(self):
        """Build compatible string to property mappings"""
        mappings = {}

        # Group properties by compatible string
        compatible_props = defaultdict(lambda: defaultdict(set))

        for prop_name, occurrences in self.properties.items():
            for occ in occurrences:
                if occ['compatible']:
                    compatible_props[occ['compatible']][prop_name].add(occ['type'])

        # Build schema for each compatible
        for compatible, props in compatible_props.items():
            mappings[compatible] = {
                'type': 'object',
                'properties': {
                    prop: self._get_property_schema_def(list(types)[0])
                    for prop, types in props.items()
                    if len(types) == 1
                }
            }

        return mappings

    def _build_path_overrides(self):
        """Build path-specific property overrides"""
        overrides = {}

        for path, props in self.path_properties.items():
            if props:
                overrides[path] = {
                    'type': 'object',
                    'properties': {
                        prop_name: self._get_property_schema_def(prop_type)
                        for prop_name, prop_type in props
                    }
                }

        return overrides

    def _generate_root_properties(self):
        """Generate root-level properties schema"""
        root_props = {}

        # Find properties at root level (empty path)
        for prop_name, occurrences in self.properties.items():
            root_occs = [occ for occ in occurrences if not occ['path']]
            if root_occs:
                # Use the type from root occurrences
                root_props[prop_name] = self._get_property_schema_def(root_occs[0]['type'])

        return root_props

    def _get_property_schema_def(self, prop_type):
        """Get JSON schema definition for a property type"""
        if prop_type == 'uint32':
            return self._get_cell_schema()
        elif prop_type == 'uint64':
            return {'type': 'string', 'pattern': '^<0x[0-9a-fA-F]+ 0x[0-9a-fA-F]+>$'}
        elif prop_type == 'uint32-array':
            return self._get_cell_array_schema()
        elif prop_type.startswith('uint32-matrix-'):
            grouping = int(prop_type.split('-')[-1])
            return {
                'type': 'array',
                'items': {
                    'type': 'array',
                    'items': {'type': 'integer'},
                    'minItems': grouping,
                    'maxItems': grouping
                }
            }
        elif prop_type == 'uint8-array':
            return {
                'type': 'string',
                'pattern': r'^\[[0-9a-fA-F\s]+\]$',
                'format': 'uint8-array'  # Add a format hint
            }
        elif prop_type == 'string':
            return {'type': 'string'}
        elif prop_type == 'string-array':
            return {'type': 'array', 'items': {'type': 'string'}}
        elif prop_type == 'boolean':
            return {'type': 'boolean'}
        elif prop_type == 'phandle-array':
            return {'type': 'string', 'pattern': '^<.*&.*>$'}
        elif prop_type == 'empty':
            return {'type': 'null'}
        else:
            return {'type': 'string'}  # Default fallback

    def _get_cell_schema(self):
        """Get schema for single cell value"""
        return {
            'oneOf': [
                {'type': 'integer', 'minimum': 0, 'maximum': 0xFFFFFFFF},
                {'type': 'string', 'pattern': '^<(0x[0-9a-fA-F]+|[0-9]+)>$'}
            ]
        }

    def _get_cell_array_schema(self):
        """Get schema for cell array"""
        return {
            'oneOf': [
                {'type': 'array', 'items': {'type': 'integer'}},
                {'type': 'string', 'pattern': '^<([0-9a-fA-Fx\s]+)>$'}
            ]
        }

    def _matches_pattern(self, path, pattern):
        """Check if a path matches a node pattern"""
        # Convert pattern to regex
        regex_pattern = pattern.replace('*', '[^/]+')
        regex_pattern = f".*/{regex_pattern}$"
        return bool(re.match(regex_pattern, path))

    def _get_compatible_specific_properties(self, compatible):
        """Get properties specific to a compatible string"""
        if compatible in self.compatible_properties:
            return {
                prop: self._get_property_schema_def(prop_type)
                for prop, prop_type in self.compatible_properties[compatible]
            }
        return {}

# Function to extend type hints at runtime
def add_property_type_hint(hint_category, property_name, value=None):
    """
    Add a property type hint.

    Args:
        hint_category: One of 'phandle_array_properties', 'potential_64bit_properties',
                      'cell_groupings', 'string_properties', 'boolean_properties'
        property_name: The property name to add
        value: For 'cell_groupings', the grouping size
    """
    if hint_category in PROPERTY_TYPE_HINTS:
        if hint_category == 'cell_groupings':
            PROPERTY_TYPE_HINTS[hint_category][property_name] = value
        else:
            if property_name not in PROPERTY_TYPE_HINTS[hint_category]:
                PROPERTY_TYPE_HINTS[hint_category].append(property_name)

class DTSTypeChecker:
    """Type checker using generated schema"""

    def __init__(self, schema):
        self.schema = schema

    def get_property_type(self, prop_name, node_path, compatible = None):
        """Get expected type for a property"""
        # Priority order: path override > compatible mapping > node pattern > global definition

        # Check path overrides
        if node_path in self.schema.get('path_overrides', {}):
            path_schema = self.schema['path_overrides'][node_path]
            if prop_name in path_schema.get('properties', {}):
                return path_schema['properties'][prop_name]

        # Check compatible mappings
        if compatible and compatible in self.schema.get('compatible_mappings', {}):
            compat_schema = self.schema['compatible_mappings'][compatible]
            if prop_name in compat_schema.get('properties', {}):
                return compat_schema['properties'][prop_name]

        # Check node patterns
        for pattern, pattern_schema in self.schema.get('node_patterns', {}).items():
            if self._matches_pattern(node_path, pattern):
                if prop_name in pattern_schema.get('properties', {}):
                    return pattern_schema['properties'][prop_name]

        # Check global definitions
        if prop_name in self.schema.get('property_definitions', {}):
            return self.schema['property_definitions'][prop_name]

        return None

    def validate_property(self, prop_name, value, context):
        """Validate a property value against schema"""
        node_path = context.get('path', '')
        compatible = context.get('compatible')

        prop_schema = self.get_property_type(prop_name, node_path, compatible)
        if not prop_schema:
            return True, None  # No schema = no validation

        # Since we're validating against the raw DTS string representation,
        # we just need to ensure the format matches what we determined during scanning
        # The actual type checking was already done in _determine_property_type

        # For now, return True since the type was already validated during scanning
        # More sophisticated validation could be added here if needed
        return True, None

    def _matches_pattern(self, path, pattern):
        """Check if a path matches a node pattern"""
        regex_pattern = pattern.replace('*', '[^/]+')
        regex_pattern = f".*/{regex_pattern}$"
        return bool(re.match(regex_pattern, path))



class DTSPropertyTypeResolver:
    """
    Fast property type resolver for DTB processing.
    Maps DTS property types to LopperFmt types.
    """

    def __init__(self, schema):
        self.schema = schema

        # Build optimized lookup tables for fast access
        self._property_types = {}
        self._compatible_properties = {}
        self._pattern_properties = {}
        self._path_properties = schema.get('path_overrides', {})

        # Compile property name patterns with context (THIS WAS MISSING!)
        self._property_patterns = []
        for pattern_key, pattern_def in schema.get('property_patterns', {}).items():
            regex_str = pattern_def['regex']

            # Fix double-escaped backslashes if present
            if '\\\\' in regex_str:
                regex_str = regex_str.replace('\\\\', '\\')

            pattern_info = {
                'regex': re.compile(regex_str),
                'type': self._schema_to_lopper_fmt('', pattern_def.get('schema', {})),
                'context': pattern_def.get('context', {})
            }
            self._property_patterns.append(pattern_info)

        # Pre-process property definitions for O(1) lookup
        for prop_name, prop_def in schema.get('property_definitions', {}).items():
            self._property_types[prop_name] = self._schema_to_lopper_fmt(prop_name, prop_def)

        # Pre-process compatible mappings
        for compatible, compat_schema in schema.get('compatible_mappings', {}).items():
            self._compatible_properties[compatible] = {}
            for prop_name, prop_def in compat_schema.get('properties', {}).items():
                self._compatible_properties[compatible][prop_name] = self._schema_to_lopper_fmt(prop_name, prop_def)

        # Pre-compile node pattern regexes
        for pattern, pattern_schema in schema.get('node_patterns', {}).items():
            regex = pattern.replace('*', '[^/]+')
            self._pattern_properties[pattern] = {
                'regex': re.compile(f".*/{regex}$"),
                'properties': {
                    prop: self._schema_to_lopper_fmt(prop, prop_def)
                    for prop, prop_def in pattern_schema.get('properties', {}).items()
                }
            }

        # Compile heuristic patterns
        self._heuristic_patterns = []
        for pattern_str, fmt_type in PROPERTY_NAME_HEURISTICS.get('patterns', {}).items():
            self._heuristic_patterns.append({
                'regex': re.compile(pattern_str),
                'type': fmt_type
            })


    def _schema_to_lopper_fmt(self, prop_name, prop_def):
        """Convert schema property definition to LopperFmt type"""

        # Handle None or empty definitions
        if not prop_def:
            return LopperFmt.UNKNOWN

        # Get the type from the property definition
        prop_type = prop_def.get('type', 'unknown')

        # Handle oneOf schemas first
        if 'oneOf' in prop_def:
            # Look at the first option to determine type
            first_option = prop_def['oneOf'][0] if prop_def['oneOf'] else {}

            # Check for array type first
            if first_option.get('type') == 'array':
                items = first_option.get('items', {})
                if items.get('type') == 'integer':
                    return LopperFmt.UINT32  # Array of integers
                elif items.get('type') == 'string':
                    return LopperFmt.MULTI_STRING  # Array of strings
                elif items.get('type') == 'array':
                    # Nested array (matrix)
                    return LopperFmt.UINT32
                else:
                    return LopperFmt.UINT32  # Default for arrays

            elif first_option.get('type') == 'integer':
                # Single integer value
                return LopperFmt.UINT32

            elif first_option.get('type') == 'string':
                # Check for format hint first
                if prop_def.get('format') == 'uint8-array':
                    return LopperFmt.UINT8

                # Check if it has a pattern that indicates it's actually cell data
                pattern = prop_def.get('pattern', '')
                if pattern and '<' in pattern:
                    return LopperFmt.UINT32
                return LopperFmt.STRING

        # Handle direct type definitions
        elif prop_type == 'string':
            # Check for format hint FIRST
            if prop_def.get('format') == 'uint8-array':
                return LopperFmt.UINT8

            # Check if it has a pattern that indicates it's actually cell data
            pattern = prop_def.get('pattern', '')
            if pattern and '<' in pattern:
                return LopperFmt.UINT32
            return LopperFmt.STRING

        elif prop_type == 'array':
            items = prop_def.get('items', {})
            if items.get('type') == 'integer':
                return LopperFmt.UINT32
            elif items.get('type') == 'string':
                return LopperFmt.MULTI_STRING
            elif items.get('type') == 'array':
                # Nested array (matrix) - this is the clocks case!
                return LopperFmt.UINT32
            else:
                return LopperFmt.UINT32  # Default for arrays

        elif prop_type == 'integer':
            return LopperFmt.UINT32
        elif prop_type == 'boolean':
            return LopperFmt.EMPTY
        elif prop_type in ['uint32', 'uint32-array']:
            return LopperFmt.UINT32
        elif prop_type == 'uint64':
            return LopperFmt.UINT64
        elif prop_type == 'uint8-array':
            return LopperFmt.UINT8
        elif prop_type == 'string-array':
            return LopperFmt.MULTI_STRING
        elif prop_type == 'phandle-array':
            return LopperFmt.UINT32

        # Fallback to property name heuristics
        if prop_name in PROPERTY_NAME_HEURISTICS.get('exact', {}):
            return PROPERTY_NAME_HEURISTICS['exact'][prop_name]

        # Default fallback
        return LopperFmt.UNKNOWN

    def get_property_type(self, prop_name, node_path=None, compatible=None):
        """
        Get LopperFmt type for a property.

        Args:
            prop_name: Property name
            node_path: Full path to node (e.g., "/soc/uart@ff000000")
            compatible: Compatible string(s) for the node

        Returns:
            LopperFmt enum value
        """

        # Generic debug for tracked properties
        if prop_name in PROPERTY_DEBUG_SET:
            print(f"\nDEBUG: Looking up {prop_name}")
            print(f"  Node path: {node_path}")
            print(f"  Compatible: {compatible}")

            if prop_name in self._property_types:
                print(f"  Found in _property_types: {self._property_types[prop_name]}")

            if prop_name in self.schema.get('property_definitions', {}):
                prop_def = self.schema['property_definitions'][prop_name]
                print(f"  Schema def: {prop_def}")
                fmt = self._schema_to_lopper_fmt(prop_name, prop_def)
                print(f"  Converted to: {fmt}")

        # Priority 1: Path-specific override
        if node_path and node_path in self._path_properties:
            path_props = self._path_properties[node_path].get('properties', {})
            if prop_name in path_props:
                return self._schema_to_lopper_fmt(prop_name, path_props[prop_name])

        # Priority 2: Compatible-specific property
        if compatible:
            # Handle both string and list of compatible strings
            compat_list = [compatible] if isinstance(compatible, str) else compatible
            for compat in compat_list:
                if compat in self._compatible_properties:
                    if prop_name in self._compatible_properties[compat]:
                        return self._compatible_properties[compat][prop_name]

        # Priority 3: Node pattern match
        if node_path:
            for pattern, pattern_info in self._pattern_properties.items():
                if pattern_info['regex'].match(node_path):
                    if prop_name in pattern_info['properties']:
                        return pattern_info['properties'][prop_name]

        # Priority 4: Global property definition
        if prop_name in self._property_types:
            return self._property_types[prop_name]

        # Priority 5: Property name patterns
        for pattern_info in self._property_patterns:
            if pattern_info['regex'].match(prop_name):
                # Check if pattern has context requirements
                pattern_context = pattern_info['context']

                if not pattern_context:
                    # No context requirement - pattern matches anywhere
                    return pattern_info['type']

                # Check context requirements
                if 'compatible' in pattern_context and compatible:
                    if pattern_context['compatible'] == compatible:
                        return pattern_info['type']
                elif 'path' in pattern_context and node_path:
                    if pattern_context['path'] == node_path:
                        return pattern_info['type']
                elif 'pattern' in pattern_context and node_path:
                    node_pattern = pattern_context['pattern']
                    if self._matches_pattern(node_path, node_pattern):
                        return pattern_info['type']
                elif not node_path and not compatible:
                    # If we have no context to check against, accept the pattern
                    return pattern_info['type']

        # Priority 6: Name-based heuristics (now data-driven!)
        return self._apply_heuristics(prop_name)

        # Default: Unknown
        return LopperFmt.UNKNOWN

    def _apply_heuristics(self, prop_name):
        """Apply name-based heuristics to determine property type"""

        # Check exact matches first
        if prop_name in PROPERTY_NAME_HEURISTICS['exact']:
            return PROPERTY_NAME_HEURISTICS['exact'][prop_name]

        # Check suffix patterns
        for suffix, fmt_type in PROPERTY_NAME_HEURISTICS['suffixes'].items():
            if prop_name.endswith(suffix):
                return fmt_type

        # Check prefix patterns
        for prefix, prefix_rules in PROPERTY_NAME_HEURISTICS['prefixes'].items():
            if prop_name.startswith(prefix):
                # Check if there are suffix requirements for this prefix
                if isinstance(prefix_rules, dict):
                    for suffix, fmt_type in prefix_rules.items():
                        if prop_name.endswith(suffix):
                            return fmt_type
                else:
                    return prefix_rules

        # Check regex patterns
        for pattern_info in self._heuristic_patterns:
            if pattern_info['regex'].match(prop_name):
                return pattern_info['type']

        # Default: Unknown
        return LopperFmt.UNKNOWN

    def _matches_pattern(self, path, pattern):
        """Check if a path matches a node pattern"""
        regex_pattern = pattern.replace('*', '[^/]+')
        regex_pattern = f".*/{regex_pattern}$"
        return bool(re.match(regex_pattern, path))


    def get_common_properties(self):
        """Get a dictionary of all common property types for quick reference"""
        return self._property_types.copy()


def add_property_heuristic(heuristic_type, pattern, fmt_type):
    """
    Add a new heuristic rule at runtime.

    Args:
        heuristic_type: 'exact', 'suffixes', 'prefixes', or 'patterns'
        pattern: The pattern to match
        fmt_type: LopperFmt type to return
    """
    if heuristic_type in PROPERTY_NAME_HEURISTICS:
        if heuristic_type == 'prefixes' and isinstance(PROPERTY_NAME_HEURISTICS[heuristic_type], dict):
            # Handle prefix with potential suffix rules
            PROPERTY_NAME_HEURISTICS[heuristic_type][pattern] = fmt_type
        elif heuristic_type in ['exact', 'suffixes', 'patterns']:
            PROPERTY_NAME_HEURISTICS[heuristic_type][pattern] = fmt_type
        else:
            raise ValueError(f"Invalid heuristic type: {heuristic_type}")

def create_all_from_schema(schema_file=None, schema_dict=None):
    """
    Create all tools from a saved schema.

    Returns:
        tuple: (property_resolver, type_checker, validator)
    """
    if schema_file:
        with open(schema_file, 'r') as f:
            schema = yaml.safe_load(f)
    elif schema_dict:
        schema = schema_dict
    else:
        raise ValueError("Either schema_file or schema_dict must be provided")

    # Create all tools from the schema
    property_resolver = DTSPropertyTypeResolver(schema)
    type_checker = DTSTypeChecker(schema)
    validator = DTSValidator(schema)

    return property_resolver, type_checker, validator


class DTSValidator:
    """
    Full DTS/DTB validator using schema.
    Validates structure, required properties, and type correctness.
    """

    def __init__(self, schema):
        self.schema = schema
        self.type_checker = DTSTypeChecker(schema)
        self.property_resolver = DTSPropertyTypeResolver(schema)

        # Build validation rules from schema
        self._required_properties = self._extract_required_properties()
        self._node_requirements = self._extract_node_requirements()

    def _extract_required_properties(self):
        """Extract which properties are required for which contexts"""
        required = {}

        # Common required properties by node type
        required['cpu@*'] = ['compatible', 'device_type', 'reg']
        required['memory@*'] = ['device_type', 'reg']
        required['uart@*'] = ['compatible', 'reg']

        # Add from compatible mappings
        for compatible, compat_def in self.schema.get('compatible_mappings', {}).items():
            # Could mark required properties in schema with 'required': true
            pass

        return required

    def _extract_node_requirements(self):
        """Extract which nodes are required in a valid DTS"""
        # Basic requirements for a valid DTS
        return {
            'root': ['compatible'],  # Root must have compatible
            'cpus': ['#address-cells', '#size-cells'],  # CPUs node requirements
        }

    def validate_dts(self, dts_content):
        """Validate a complete DTS file"""
        errors = []
        warnings = []

        # Parse the DTS
        generator = DTSSchemaGenerator()
        generator.scan_dts_file(dts_content)

        # Validate each node
        for node in generator.nodes:
            node_errors, node_warnings = self._validate_node(node, generator)
            errors.extend(node_errors)
            warnings.extend(node_warnings)

        return {
            'valid': len(errors) == 0,
            'errors': errors,
            'warnings': warnings
        }

    def validate_dtb_node(self, node_path, properties):
        """
        Validate a node from DTB processing.

        Args:
            node_path: Path to the node
            properties: Dict of property names to values

        Returns:
            Dict with validation results
        """
        errors = []
        warnings = []

        # Check required properties
        for pattern, required_props in self._required_properties.items():
            if self._matches_pattern(node_path, pattern):
                for req_prop in required_props:
                    if req_prop not in properties:
                        errors.append(f"{node_path}: Missing required property '{req_prop}'")

        # Validate property types
        for prop_name, prop_value in properties.items():
            # Get expected type
            expected_fmt = self.property_resolver.get_property_type(prop_name, node_path)

            if expected_fmt == LopperFmt.UNKNOWN:
                warnings.append(f"{node_path}: Unknown property type for '{prop_name}'")

        return {
            'valid': len(errors) == 0,
            'errors': errors,
            'warnings': warnings
        }

    def _validate_node(self, node, generator):
        """Validate a single node"""
        errors = []
        warnings = []

        node_path = node['path']
        compatible = node.get('compatible')

        # Check required properties for this node type
        node_name = node['name']
        for pattern, required_props in self._required_properties.items():
            if self._matches_pattern(node_name, pattern):
                for req_prop in required_props:
                    if req_prop not in node['properties']:
                        errors.append(f"{node_path}: Missing required property '{req_prop}' for {pattern}")

        # Validate each property
        for prop_name, prop_type in node['properties'].items():
            # Find the actual property value
            prop_occurrences = [p for p in generator.properties[prop_name]
                               if p['path'] == node_path]

            if prop_occurrences:
                prop_value = prop_occurrences[0]['value']
                context = {'path': node_path, 'compatible': compatible}

                # Type validation
                is_valid, error = self.type_checker.validate_property(prop_name, prop_value, context)
                if not is_valid:
                    errors.append(f"{node_path}: {prop_name} - {error}")

                # Check if property is appropriate for this node
                expected_type = self.type_checker.get_property_type(prop_name, node_path, compatible)
                if not expected_type:
                    warnings.append(f"{node_path}: Unexpected property '{prop_name}'")

        return errors, warnings

    def _matches_pattern(self, path, pattern):
        """Check if a path matches a pattern"""
        regex_pattern = pattern.replace('*', '[^/]+')
        regex_pattern = f".*/{regex_pattern}$"
        return bool(re.match(regex_pattern, path))

### -----------------------------------------------------------------------------------------------
### ---------------------------------- update helper section --------------------------------------
### -----------------------------------------------------------------------------------------------


# # 1. Simple property
# update_schema(tree.schema, 'status', 'property', LopperFmt.STRING)

# # 2. Property pattern
# update_schema(tree.schema, r'lopper-comment-\d+', 'property', LopperFmt.STRING)

# # 3. Node pattern with mixed properties
# update_schema(tree.schema, 'custom@*', 'node', {
#     'compatible': LopperFmt.STRING,
#     'reg': LopperFmt.UINT32,
#     r'custom-prop-\d+': LopperFmt.UINT32,  # Pattern within node!
#     r'.*-config$': LopperFmt.STRING,        # Another pattern
# })

# # 4. Context-specific property pattern
# update_schema(tree.schema, r'.*-mask$', 'property', LopperFmt.UINT32,
#               context={'pattern': 'interrupt-controller@*'})

# # Check if property would be recognized
# if schema_has_definition(tree.schema, 'lopper-comment-42', 'property'):
#     print("Property pattern matched!")

# # The wrappers still work for compatibility
# update_schema_with_property(tree.schema, 'new-prop', LopperFmt.STRING)
# update_schema_with_node_pattern(tree.schema, 'widget@*', {'value': LopperFmt.UINT32})

def update_schema(schema_dict, name, schema_type, type_or_props=None, context=None):
    """
    Universal schema update function for properties and node patterns.

    Args:
        schema_dict: The schema dictionary to update (modified in-place)
        name: Property name/pattern OR node pattern (e.g., 'status', r'lopper-comment-\d+', 'uart@*')
        schema_type: 'property' or 'node'
        type_or_props: For properties: LopperFmt or type string
                      For nodes: dict of {property_name: LopperFmt/type}
        context: Optional dict with 'path', 'compatible', or 'pattern'

    Returns:
        The updated schema_dict
    """
    if schema_type == 'property':
        return _update_property(schema_dict, name, type_or_props, context)
    elif schema_type == 'node':
        return _update_node_pattern(schema_dict, name, type_or_props)
    else:
        raise ValueError(f"Invalid schema_type: {schema_type}")


def _update_property(schema_dict, prop_name, prop_type, context=None):
    """Internal function for property updates"""
    # Convert prop_type to schema definition
    if isinstance(prop_type, LopperFmt):
        schema_def = _lopper_fmt_to_schema_def(prop_type)
    else:
        schema_def = _type_string_to_schema_def(prop_type)

    # Check if prop_name is a pattern
    is_pattern = bool(re.search(r'[.*+?[\]{}()^$\\|]', prop_name))

    if is_pattern:
        # Handle as a property name pattern
        if 'property_patterns' not in schema_dict:
            schema_dict['property_patterns'] = {}

        # Store the pattern
        pattern_key = prop_name
        if context:
            # Create a compound key for context-specific patterns
            if 'compatible' in context:
                pattern_key = f"{prop_name}|compatible:{context['compatible']}"
            elif 'path' in context:
                pattern_key = f"{prop_name}|path:{context['path']}"
            elif 'pattern' in context:
                pattern_key = f"{prop_name}|node:{context['pattern']}"

        schema_dict['property_patterns'][pattern_key] = {
            'regex': prop_name,
            'schema': schema_def,
            'context': context or {}
        }
    else:
        # Handle as exact property name
        if 'property_definitions' not in schema_dict:
            schema_dict['property_definitions'] = {}
        schema_dict['property_definitions'][prop_name] = schema_def

        # Add to context-specific locations if provided
        if context:
            if 'path' in context:
                path = context['path']
                if 'path_overrides' not in schema_dict:
                    schema_dict['path_overrides'] = {}
                if path not in schema_dict['path_overrides']:
                    schema_dict['path_overrides'][path] = {
                        'type': 'object',
                        'properties': {}
                    }
                schema_dict['path_overrides'][path]['properties'][prop_name] = schema_def

            elif 'compatible' in context:
                compatible = context['compatible']
                if 'compatible_mappings' not in schema_dict:
                    schema_dict['compatible_mappings'] = {}
                if compatible not in schema_dict['compatible_mappings']:
                    schema_dict['compatible_mappings'][compatible] = {
                        'type': 'object',
                        'properties': {}
                    }
                schema_dict['compatible_mappings'][compatible]['properties'][prop_name] = schema_def

            elif 'pattern' in context:
                pattern = context['pattern']
                if 'node_patterns' not in schema_dict:
                    schema_dict['node_patterns'] = {}
                if pattern not in schema_dict['node_patterns']:
                    schema_dict['node_patterns'][pattern] = {
                        'type': 'object',
                        'properties': {}
                    }
                schema_dict['node_patterns'][pattern]['properties'][prop_name] = schema_def

    return schema_dict


def _update_node_pattern(schema_dict, pattern, properties=None):
    """Internal function for node pattern updates"""
    if 'node_patterns' not in schema_dict:
        schema_dict['node_patterns'] = {}

    if pattern not in schema_dict['node_patterns']:
        schema_dict['node_patterns'][pattern] = {
            'type': 'object',
            'properties': {}
        }

    if properties:
        for prop_name, prop_type in properties.items():
            # Check if property name is a pattern
            is_prop_pattern = bool(re.search(r'[.*+?[\]{}()^$\\|]', prop_name))

            if is_prop_pattern:
                # Store as a pattern specific to this node type
                if 'property_patterns' not in schema_dict:
                    schema_dict['property_patterns'] = {}

                pattern_key = f"{prop_name}|node:{pattern}"
                if isinstance(prop_type, LopperFmt):
                    schema_def = _lopper_fmt_to_schema_def(prop_type)
                else:
                    schema_def = _type_string_to_schema_def(prop_type)

                schema_dict['property_patterns'][pattern_key] = {
                    'regex': prop_name,
                    'schema': schema_def,
                    'context': {'pattern': pattern}
                }
            else:
                # Regular property for this node pattern
                if isinstance(prop_type, LopperFmt):
                    schema_def = _lopper_fmt_to_schema_def(prop_type)
                else:
                    schema_def = _type_string_to_schema_def(prop_type)

                schema_dict['node_patterns'][pattern]['properties'][prop_name] = schema_def

                # Also add to global property_definitions if not present
                if 'property_definitions' not in schema_dict:
                    schema_dict['property_definitions'] = {}
                if prop_name not in schema_dict['property_definitions']:
                    schema_dict['property_definitions'][prop_name] = schema_def

    return schema_dict


# Convenience wrappers for backward compatibility and clarity
def update_schema_with_property(schema_dict, prop_name, prop_type, context=None):
    """Wrapper for property updates"""
    return update_schema(schema_dict, prop_name, 'property', prop_type, context)


def update_schema_with_node_pattern(schema_dict, pattern, properties=None):
    """Wrapper for node pattern updates"""
    return update_schema(schema_dict, pattern, 'node', properties)


# Updated initialization using patterns
def initialize_lopper_properties(schema_dict):
    """
    Initialize a schema dictionary with common lopper properties and patterns.
    Now uses the pattern-based approach for numbered/variable properties.
    """
    # Step 1: Add exact global properties (non-pattern)
    exact_props = {
        'lopper-priority': LopperFmt.UINT32,
        'lopper-generated': LopperFmt.EMPTY,
        'lopper-modified': LopperFmt.EMPTY,
        'lopper-timestamp': LopperFmt.STRING,
        'lopper-version': LopperFmt.STRING,
        'lopper-assist': LopperFmt.STRING,
        'lopper-preamble': LopperFmt.STRING,
    }

    for prop_name, prop_type in exact_props.items():
        update_schema(schema_dict, prop_name, 'property', prop_type)

    # Step 2: Add property patterns for variable names
    property_patterns = {
        # Lopper patterns
        r'lopper-comment-\d+': LopperFmt.STRING,
        r'lopper-comment-\w+': LopperFmt.STRING,
        r'lopper-domain-\w+': LopperFmt.STRING,
        r'lopper-xlate-\d+': LopperFmt.UINT32,

        # Common device tree patterns
        r'.*-supply$': LopperFmt.UINT32,      # Power supplies
        r'.*-gpios?$': LopperFmt.UINT32,      # GPIO references
        r'.*-ph(y|ys)$': LopperFmt.UINT32,    # PHY references
        r'.*-names$': LopperFmt.MULTI_STRING,  # Name arrays
        r'.*-cells$': LopperFmt.UINT32,       # Cell counts
        r'#.*-cells$': LopperFmt.UINT32,      # Cell counts (# prefix)
        r'.*-map$': LopperFmt.UINT32,         # Mapping tables
        r'.*-map-mask$': LopperFmt.UINT32,    # Mapping masks
        r'.*-ranges$': LopperFmt.UINT32,      # Range specifications

        # Numbered/indexed patterns
        r'reg-\d+': LopperFmt.UINT32,
        r'ranges-\d+': LopperFmt.UINT32,
        r'interrupts-\d+': LopperFmt.UINT32,
        r'clocks-\d+': LopperFmt.UINT32,
        r'clock-names-\d+': LopperFmt.STRING,
        r'resets-\d+': LopperFmt.UINT32,
        r'reset-names-\d+': LopperFmt.STRING,
    }

    for pattern, prop_type in property_patterns.items():
        update_schema(schema_dict, pattern, 'property', prop_type)

    # Step 3: Add node patterns with their expected properties
    node_patterns = {
        'domain@*': {
            'compatible': LopperFmt.STRING,
            'lopper-domain-id': LopperFmt.UINT32,
            'cpus': LopperFmt.UINT32,              # phandle array
            'access': LopperFmt.UINT32,            # phandle array
            r'lopper-.*': LopperFmt.STRING,        # Pattern within node pattern!
        },

        'lopper@*': {
            'compatible': LopperFmt.STRING,
            'lopper-version': LopperFmt.STRING,
            r'lopper-.*': LopperFmt.STRING,
        },

        '__symbols__': {
            r'.*': LopperFmt.STRING,  # All properties in symbols are strings
        },

        'chosen': {
            'bootargs': LopperFmt.STRING,
            'stdout-path': LopperFmt.STRING,
            r'linux,.*': LopperFmt.STRING,
        },

        'memory@*': {
            'device_type': LopperFmt.STRING,
            'reg': LopperFmt.UINT32,
        },

        'cpu@*': {
            'device_type': LopperFmt.STRING,
            'compatible': LopperFmt.STRING,
            'reg': LopperFmt.UINT32,
            'clock-frequency': LopperFmt.UINT32,
            'timebase-frequency': LopperFmt.UINT32,
            r'.*-supply$': LopperFmt.UINT32,
        },
    }

    for pattern, properties in node_patterns.items():
        update_schema(schema_dict, pattern, 'node', properties)

    # Step 4: Context-specific patterns
    # Example: In GPIO controllers, all unrecognized props might be GPIO definitions
    update_schema(schema_dict, r'.*-hog$', 'property', LopperFmt.EMPTY,
                  context={'pattern': 'gpio@*'})

    return schema_dict


# Helper to check if something is in the schema
def schema_has_definition(schema_dict, name, schema_type='property', context=None):
    """
    Check if a property or node pattern exists in the schema.

    Args:
        schema_dict: Schema to check
        name: Property/node name or pattern
        schema_type: 'property' or 'node'
        context: Optional context to check

    Returns:
        bool: True if found
    """
    if schema_type == 'property':
        # Check exact match first
        if name in schema_dict.get('property_definitions', {}):
            return True

        # Check patterns
        for pattern_key, pattern_def in schema_dict.get('property_patterns', {}).items():
            if re.match(pattern_def['regex'], name):
                # Check context if specified
                if context:
                    pattern_context = pattern_def.get('context', {})
                    if not pattern_context:  # No context requirement
                        return True
                    # Check if contexts match
                    for key in context:
                        if key in pattern_context and pattern_context[key] == context[key]:
                            return True
                else:
                    return True

    elif schema_type == 'node':
        return name in schema_dict.get('node_patterns', {})

    return False


def _lopper_fmt_to_schema_def(lopper_fmt):
    """Convert LopperFmt enum to schema definition"""
    if lopper_fmt == LopperFmt.STRING:
        return {'type': 'string'}
    elif lopper_fmt == LopperFmt.MULTI_STRING:
        return {'type': 'array', 'items': {'type': 'string'}}
    elif lopper_fmt == LopperFmt.UINT32:
        return {
            'oneOf': [
                {'type': 'integer', 'minimum': 0, 'maximum': 0xFFFFFFFF},
                {'type': 'string', 'pattern': '^<(0x[0-9a-fA-F]+|[0-9]+)>$'}
            ]
        }
    elif lopper_fmt == LopperFmt.UINT64:
        return {'type': 'string', 'pattern': '^<0x[0-9a-fA-F]+ 0x[0-9a-fA-F]+>$'}
    elif lopper_fmt == LopperFmt.UINT8:
        return {'type': 'string', 'pattern': r'^\[[0-9a-fA-F\s]+\]$'}
    elif lopper_fmt == LopperFmt.EMPTY:
        return {'type': 'boolean'}
    else:
        return {'type': ['string', 'integer', 'array', 'boolean']}


def _type_string_to_schema_def(type_string):
    """Convert type string to schema definition"""
    type_map = {
        'string': {'type': 'string'},
        'string-array': {'type': 'array', 'items': {'type': 'string'}},
        'uint32': {
            'oneOf': [
                {'type': 'integer', 'minimum': 0, 'maximum': 0xFFFFFFFF},
                {'type': 'string', 'pattern': '^<(0x[0-9a-fA-F]+|[0-9]+)>$'}
            ]
        },
        'uint32-array': {'type': 'array', 'items': {'type': 'integer'}},
        'uint64': {'type': 'string', 'pattern': '^<0x[0-9a-fA-F]+ 0x[0-9a-fA-F]+>$'},
        'uint8-array': {'type': 'string', 'pattern': r'^\[[0-9a-fA-F\s]+\]$'},
        'boolean': {'type': 'boolean'},
        'empty': {'type': 'boolean'},
        'phandle-array': {'type': 'array', 'items': {'type': 'integer'}}
    }

    return type_map.get(type_string, {'type': ['string', 'integer', 'array', 'boolean']})

# # Check if a property exists
# if property_exists_in_schema(tree.schema, 'lopper-comment-42'):
#     print("Property exists (matched pattern)")

# # Check with context
# if property_exists_in_schema(tree.schema, 'irq-mask',
#                             context={'pattern': 'interrupt-controller@*'}):
#     print("Property exists in interrupt controller context")

# # Get information about how a property is defined
# info = get_property_info(tree.schema, 'lopper-comment-99')
# if info:
#     print(f"Property matched {info['type']}: {info.get('pattern', 'exact match')}")

# # Before adding a new property
# prop_name = 'lopper-generated-value'
# if not property_exists_in_schema(tree.schema, prop_name):
#     update_schema(tree.schema, prop_name, 'property', LopperFmt.UINT32)

def property_exists_in_schema(schema_dict, prop_name, context=None):
    """
    Check if a property already exists in the schema (exact match or pattern).

    Args:
        schema_dict: Schema dictionary to check
        prop_name: Property name to look for
        context: Optional context dict with 'path', 'compatible', or 'pattern'

    Returns:
        bool: True if property exists in the specified context
    """
    # 1. Check exact matches first
    if prop_name in schema_dict.get('property_definitions', {}):
        # If no specific context requested, property exists globally
        if not context:
            return True

    # 2. Check context-specific exact matches
    if context:
        if 'path' in context:
            path = context['path']
            path_props = schema_dict.get('path_overrides', {}).get(path, {}).get('properties', {})
            if prop_name in path_props:
                return True

        elif 'compatible' in context:
            compatible = context['compatible']
            compat_props = schema_dict.get('compatible_mappings', {}).get(compatible, {}).get('properties', {})
            if prop_name in compat_props:
                return True

        elif 'pattern' in context:
            pattern = context['pattern']
            pattern_props = schema_dict.get('node_patterns', {}).get(pattern, {}).get('properties', {})
            if prop_name in pattern_props:
                return True

    # 3. Check property patterns (NEW!)
    for pattern_key, pattern_def in schema_dict.get('property_patterns', {}).items():
        regex = pattern_def.get('regex', '')
        try:
            if re.match(regex, prop_name):
                # Pattern matches, check context if needed
                pattern_context = pattern_def.get('context', {})

                if not context and not pattern_context:
                    # No context on either side - match
                    return True
                elif not context and pattern_context:
                    # We have no context but pattern requires one - no match
                    continue
                elif context and not pattern_context:
                    # We have context but pattern doesn't require it - match
                    return True
                elif context and pattern_context:
                    # Both have context - check if they match
                    if 'compatible' in context and 'compatible' in pattern_context:
                        if context['compatible'] == pattern_context['compatible']:
                            return True
                    elif 'path' in context and 'path' in pattern_context:
                        if context['path'] == pattern_context['path']:
                            return True
                    elif 'pattern' in context and 'pattern' in pattern_context:
                        if context['pattern'] == pattern_context['pattern']:
                            return True
        except re.error:
            # Invalid regex, skip
            continue

    return False


def get_property_info(schema_dict, prop_name, context=None):
    """
    Get information about how a property is defined in the schema.
    Useful for debugging and understanding where a property comes from.

    Returns:
        dict: Information about the property definition, or None if not found
    """
    # Check exact matches first
    if prop_name in schema_dict.get('property_definitions', {}):
        info = {
            'source': 'property_definitions',
            'type': 'exact',
            'definition': schema_dict['property_definitions'][prop_name]
        }

        # Check if also in context-specific location
        if context:
            if 'path' in context:
                path = context['path']
                if path in schema_dict.get('path_overrides', {}):
                    if prop_name in schema_dict['path_overrides'][path].get('properties', {}):
                        info['context_override'] = {
                            'type': 'path',
                            'value': path,
                            'definition': schema_dict['path_overrides'][path]['properties'][prop_name]
                        }
            # Similar checks for compatible and pattern contexts...

        return info

    # Check patterns
    for pattern_key, pattern_def in schema_dict.get('property_patterns', {}).items():
        regex = pattern_def.get('regex', '')
        try:
            if re.match(regex, prop_name):
                return {
                    'source': 'property_patterns',
                    'type': 'pattern',
                    'pattern': regex,
                    'pattern_key': pattern_key,
                    'definition': pattern_def.get('schema', {}),
                    'context': pattern_def.get('context', {})
                }
        except re.error:
            continue

    return None

# Helper function for DTB processing
def create_property_resolver(schema_file=None, schema_dict=None):
    """
    Create a property type resolver from schema file or dict.

    Args:
        schema_file: Path to YAML schema file
        schema_dict: Schema dictionary (if already loaded)

    Returns:
        DTSPropertyTypeResolver instance
    """
    if schema_file:
        with open(schema_file, 'r') as f:
            schema = yaml.safe_load(f)
    elif schema_dict:
        schema = schema_dict
    else:
        raise ValueError("Either schema_file or schema_dict must be provided")

    return DTSPropertyTypeResolver(schema)


# Helper function for integration
def generate_schema_from_dts(dts_content: str):
    """Generate schema from DTS content"""
    generator = DTSSchemaGenerator()
    generator.scan_dts_file(dts_content)
    return generator.generate_schema()

def schema_add_runtime_property(tree, node, prop_name, prop_value):
    """Example of adding a property and updating the schema"""

    # Determine the property type from the value
    if isinstance(prop_value, str):
        prop_type = LopperFmt.STRING
    elif isinstance(prop_value, list) and all(isinstance(v, str) for v in prop_value):
        prop_type = LopperFmt.MULTI_STRING
    elif isinstance(prop_value, int):
        prop_type = LopperFmt.UINT32
    elif isinstance(prop_value, bool) or prop_value is None:
        prop_type = LopperFmt.EMPTY
    else:
        prop_type = LopperFmt.UNKNOWN

    # Check if property already exists in schema
    context = {'path': node.abs_path} if hasattr(node, 'abs_path') else None

    if not property_exists_in_schema(tree.schema, prop_name, context):
        # Update the schema with the new property
        update_schema_with_property(tree.schema, prop_name, prop_type, context)

        # Recreate resolver/checker if needed
        # (you might want to cache and only recreate periodically)
        tree._resolver = None  # Clear cached resolver to force recreation

# When you need the resolver again
def schema_get_resolver(tree):
    """Get or create resolver from current schema"""
    if not hasattr(tree, '_resolver') or tree._resolver is None:
        tree._resolver = create_property_resolver(schema_dict=tree.schema)

    return tree._resolver
