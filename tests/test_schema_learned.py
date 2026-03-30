"""
Tests for learned schema type resolution.

These tests capture the current behavior of DTSPropertyTypeResolver and
DTSSchemaGenerator to ensure Phase 2 migration doesn't change output types.

Copyright (c) 2026 AMD Inc. All rights reserved.

SPDX-License-Identifier: BSD-3-Clause

Author:
    Bruce Ashfield <bruce.ashfield@amd.com>
"""

import pytest
from lopper.fmt import LopperFmt
import lopper.schema
from lopper.schema import (
    PROPERTY_NAME_HEURISTICS,
    PROPERTY_TYPE_HINTS,
    DTSSchemaGenerator,
    DTSPropertyTypeResolver,
    SchemaManager,
    get_schema_manager,
    PropertyType,
    PropertySpec,
    TypeDefinition,
)


class TestPropertyNameHeuristics:
    """Test that PROPERTY_NAME_HEURISTICS produces correct types."""

    def test_exact_compatible_is_string(self):
        """compatible property should be STRING."""
        assert PROPERTY_NAME_HEURISTICS['exact']['compatible'] == LopperFmt.STRING

    def test_exact_status_is_string(self):
        """status property should be STRING."""
        assert PROPERTY_NAME_HEURISTICS['exact']['status'] == LopperFmt.STRING

    def test_exact_device_type_is_string(self):
        """device_type property should be STRING."""
        assert PROPERTY_NAME_HEURISTICS['exact']['device_type'] == LopperFmt.STRING

    def test_exact_phandle_is_uint32(self):
        """phandle property should be UINT32."""
        assert PROPERTY_NAME_HEURISTICS['exact']['phandle'] == LopperFmt.UINT32

    def test_exact_reg_is_uint32(self):
        """reg property should be UINT32."""
        assert PROPERTY_NAME_HEURISTICS['exact']['reg'] == LopperFmt.UINT32

    def test_exact_interrupts_is_uint32(self):
        """interrupts property should be UINT32."""
        assert PROPERTY_NAME_HEURISTICS['exact']['interrupts'] == LopperFmt.UINT32

    def test_exact_clocks_is_uint32(self):
        """clocks property should be UINT32."""
        assert PROPERTY_NAME_HEURISTICS['exact']['clocks'] == LopperFmt.UINT32

    def test_exact_no_map_is_empty(self):
        """no-map property should be EMPTY (boolean flag)."""
        assert PROPERTY_NAME_HEURISTICS['exact']['no-map'] == LopperFmt.EMPTY

    def test_exact_reusable_is_empty(self):
        """reusable property should be EMPTY (boolean flag)."""
        assert PROPERTY_NAME_HEURISTICS['exact']['reusable'] == LopperFmt.EMPTY

    def test_suffix_names_is_multi_string(self):
        """-names suffix should map to MULTI_STRING."""
        assert PROPERTY_NAME_HEURISTICS['suffixes']['-names'] == LopperFmt.MULTI_STRING

    def test_suffix_cells_is_uint32(self):
        """-cells suffix should map to UINT32."""
        assert PROPERTY_NAME_HEURISTICS['suffixes']['-cells'] == LopperFmt.UINT32

    def test_suffix_gpio_is_uint32(self):
        """-gpio suffix should map to UINT32."""
        assert PROPERTY_NAME_HEURISTICS['suffixes']['-gpio'] == LopperFmt.UINT32

    def test_suffix_gpios_is_uint32(self):
        """-gpios suffix should map to UINT32."""
        assert PROPERTY_NAME_HEURISTICS['suffixes']['-gpios'] == LopperFmt.UINT32


class TestPropertyTypeHints:
    """Test PROPERTY_TYPE_HINTS structure."""

    def test_phandle_array_properties_contains_clocks(self):
        """clocks should be in phandle_array_properties."""
        assert 'clocks' in PROPERTY_TYPE_HINTS['phandle_array_properties']

    def test_phandle_array_properties_contains_resets(self):
        """resets should be in phandle_array_properties."""
        assert 'resets' in PROPERTY_TYPE_HINTS['phandle_array_properties']

    def test_phandle_array_properties_contains_interrupt_map(self):
        """interrupt-map should be in phandle_array_properties."""
        assert 'interrupt-map' in PROPERTY_TYPE_HINTS['phandle_array_properties']

    def test_potential_64bit_contains_reg(self):
        """reg should be in potential_64bit_properties."""
        assert 'reg' in PROPERTY_TYPE_HINTS['potential_64bit_properties']

    def test_potential_64bit_contains_ranges(self):
        """ranges should be in potential_64bit_properties."""
        assert 'ranges' in PROPERTY_TYPE_HINTS['potential_64bit_properties']

    def test_cell_groupings_reg_is_2(self):
        """reg cell grouping should be 2."""
        assert PROPERTY_TYPE_HINTS['cell_groupings']['reg'] == 2

    def test_cell_groupings_ranges_is_3(self):
        """ranges cell grouping should be 3."""
        assert PROPERTY_TYPE_HINTS['cell_groupings']['ranges'] == 3

    def test_string_properties_contains_compatible(self):
        """compatible should be in string_properties."""
        assert 'compatible' in PROPERTY_TYPE_HINTS['string_properties']

    def test_string_properties_contains_status(self):
        """status should be in string_properties."""
        assert 'status' in PROPERTY_TYPE_HINTS['string_properties']

    def test_boolean_properties_contains_no_map(self):
        """no-map should be in boolean_properties."""
        assert 'no-map' in PROPERTY_TYPE_HINTS['boolean_properties']

    def test_boolean_properties_contains_reusable(self):
        """reusable should be in boolean_properties."""
        assert 'reusable' in PROPERTY_TYPE_HINTS['boolean_properties']


class TestDTSSchemaGeneratorTypeDetection:
    """Test DTSSchemaGenerator._determine_property_type method."""

    @pytest.fixture
    def generator(self):
        """Create a fresh DTSSchemaGenerator."""
        return DTSSchemaGenerator()

    def test_empty_value_is_boolean(self, generator):
        """Empty value should return boolean type."""
        result = generator._determine_property_type('test-prop', '')
        assert result == 'boolean'

    def test_single_cell_is_uint32(self, generator):
        """Single cell value should return uint32."""
        result = generator._determine_property_type('test-prop', '<0x1>')
        assert result == 'uint32'

    def test_phandle_reference_is_phandle_array(self, generator):
        """Value with & should return phandle-array."""
        result = generator._determine_property_type('test-prop', '<&clk>')
        assert result == 'phandle-array'

    def test_empty_cells_is_empty(self, generator):
        """Empty angle brackets should return empty."""
        result = generator._determine_property_type('test-prop', '<>')
        assert result == 'empty'

    def test_single_string_is_string(self, generator):
        """Quoted string should return string."""
        result = generator._determine_property_type('test-prop', '"hello"')
        assert result == 'string'

    def test_multi_string_is_string_array(self, generator):
        """Multiple quoted strings should return string-array."""
        result = generator._determine_property_type('test-prop', '"hello", "world"')
        assert result == 'string-array'

    def test_byte_array_is_uint8_array(self, generator):
        """Byte array syntax should return uint8-array."""
        result = generator._determine_property_type('test-prop', '[00 01 02 03]')
        assert result == 'uint8-array'

    def test_known_string_property_is_string(self, generator):
        """Known string property should return string."""
        result = generator._determine_property_type('compatible', '"foo,bar"')
        assert result == 'string'

    def test_known_boolean_property_is_boolean(self, generator):
        """Known boolean property should return boolean."""
        result = generator._determine_property_type('no-map', '')
        assert result == 'boolean'

    def test_two_cells_phandle_array_is_uint32_matrix(self, generator):
        """Two cells for phandle array property should be uint32-matrix-2."""
        result = generator._determine_property_type('clocks', '<0x1 0x2>')
        assert result == 'uint32-matrix-2'

    def test_multiple_cells_is_uint32_array(self, generator):
        """Multiple cells should return uint32-array."""
        result = generator._determine_property_type('test-prop', '<0x1 0x2 0x3>')
        assert result == 'uint32-array'


class TestDTSPropertyTypeResolverHeuristics:
    """Test DTSPropertyTypeResolver._apply_heuristics method."""

    @pytest.fixture
    def resolver(self):
        """Create a resolver with minimal schema."""
        schema = {'property_definitions': {}}
        return DTSPropertyTypeResolver(schema)

    def test_exact_match_compatible(self, resolver):
        """compatible should resolve via exact heuristic."""
        result = resolver._apply_heuristics('compatible')
        assert result == LopperFmt.STRING

    def test_exact_match_reg(self, resolver):
        """reg should resolve via exact heuristic."""
        result = resolver._apply_heuristics('reg')
        assert result == LopperFmt.UINT32

    def test_exact_match_no_map(self, resolver):
        """no-map should resolve via exact heuristic."""
        result = resolver._apply_heuristics('no-map')
        assert result == LopperFmt.EMPTY

    def test_suffix_clock_names(self, resolver):
        """clock-names should resolve via -names suffix."""
        result = resolver._apply_heuristics('clock-names')
        assert result == LopperFmt.MULTI_STRING

    def test_suffix_interrupt_names(self, resolver):
        """interrupt-names should resolve via -names suffix."""
        result = resolver._apply_heuristics('interrupt-names')
        assert result == LopperFmt.MULTI_STRING

    def test_suffix_gpio_cells(self, resolver):
        """#gpio-cells should resolve via -cells suffix."""
        result = resolver._apply_heuristics('#gpio-cells')
        assert result == LopperFmt.UINT32

    def test_suffix_clock_cells(self, resolver):
        """#clock-cells should resolve via -cells suffix."""
        result = resolver._apply_heuristics('#clock-cells')
        assert result == LopperFmt.UINT32

    def test_suffix_reset_gpios(self, resolver):
        """reset-gpios should resolve via -gpios suffix."""
        result = resolver._apply_heuristics('reset-gpios')
        assert result == LopperFmt.UINT32

    def test_unknown_property(self, resolver):
        """Unknown property should return UNKNOWN."""
        result = resolver._apply_heuristics('xlnx-totally-unknown-property')
        assert result == LopperFmt.UNKNOWN


class TestDTSPropertyTypeResolverLookup:
    """Test DTSPropertyTypeResolver.get_property_type with schema data."""

    @pytest.fixture
    def schema_with_definitions(self):
        """Create schema with property definitions."""
        return {
            'property_definitions': {
                'my-custom-prop': {
                    'type': 'uint32',
                },
                'my-string-prop': {
                    'type': 'string',
                },
                'my-64bit-prop': {
                    'type': 'string',
                    'format': 'uint64-bits',
                },
                'my-16bit-prop': {
                    'type': 'string',
                    'format': 'uint16-array',
                },
                'my-8bit-prop': {
                    'type': 'string',
                    'format': 'uint8-array',
                },
            },
            'compatible_mappings': {
                'vendor,device': {
                    'properties': {
                        'vendor-specific': {
                            'type': 'uint32',
                        }
                    }
                }
            },
            'path_overrides': {
                '/special/node': {
                    'properties': {
                        'special-prop': {
                            'type': 'uint64',
                        }
                    }
                }
            }
        }

    @pytest.fixture
    def resolver(self, schema_with_definitions):
        """Create resolver with test schema."""
        return DTSPropertyTypeResolver(schema_with_definitions)

    def test_global_uint32_property(self, resolver):
        """Global uint32 property should resolve correctly."""
        result = resolver.get_property_type('my-custom-prop')
        assert result == LopperFmt.UINT32

    def test_global_string_property(self, resolver):
        """Global string property should resolve correctly."""
        result = resolver.get_property_type('my-string-prop')
        assert result == LopperFmt.STRING

    def test_64bit_format_property(self, resolver):
        """Property with uint64-bits format should resolve to UINT64."""
        result = resolver.get_property_type('my-64bit-prop')
        assert result == LopperFmt.UINT64

    def test_16bit_format_property(self, resolver):
        """Property with uint16-array format should resolve to UINT16."""
        result = resolver.get_property_type('my-16bit-prop')
        assert result == LopperFmt.UINT16

    def test_8bit_format_property(self, resolver):
        """Property with uint8-array format should resolve to UINT8."""
        result = resolver.get_property_type('my-8bit-prop')
        assert result == LopperFmt.UINT8

    def test_compatible_specific_property(self, resolver):
        """Compatible-specific property should resolve correctly."""
        result = resolver.get_property_type('vendor-specific', compatible='vendor,device')
        assert result == LopperFmt.UINT32

    def test_path_override_property(self, resolver):
        """Path-specific property should resolve correctly."""
        result = resolver.get_property_type('special-prop', node_path='/special/node')
        assert result == LopperFmt.UINT64

    def test_fallback_to_heuristics(self, resolver):
        """Unknown property should fall back to heuristics."""
        result = resolver.get_property_type('clock-names')
        assert result == LopperFmt.MULTI_STRING


class TestSchemaManagerSingleton:
    """Test SchemaManager singleton behavior."""

    def test_singleton_returns_same_instance(self):
        """get_schema_manager should return same instance."""
        mgr1 = get_schema_manager()
        mgr2 = get_schema_manager()
        assert mgr1 is mgr2

    def test_update_schema_creates_resolver(self):
        """update_schema should create resolver."""
        mgr = get_schema_manager()
        test_schema = {
            'property_definitions': {
                'test-prop': {'type': 'uint32'}
            }
        }
        mgr.update_schema(test_schema)
        resolver = mgr.get_resolver()
        assert resolver is not None

    def test_resolver_uses_updated_schema(self):
        """Resolver should use updated schema data."""
        mgr = get_schema_manager()
        test_schema = {
            'property_definitions': {
                'unique-test-prop-12345': {'type': 'string'}
            }
        }
        mgr.update_schema(test_schema)
        resolver = mgr.get_resolver()
        result = resolver.get_property_type('unique-test-prop-12345')
        assert result == LopperFmt.STRING


class TestSchemaToLopperFmtConversion:
    """Test _schema_to_lopper_fmt conversion for all type formats."""

    @pytest.fixture
    def resolver(self):
        """Create resolver with empty schema."""
        return DTSPropertyTypeResolver({'property_definitions': {}})

    def test_uint32_type(self, resolver):
        """uint32 type should convert to UINT32."""
        result = resolver._schema_to_lopper_fmt('test', {'type': 'uint32'})
        assert result == LopperFmt.UINT32

    def test_uint64_type(self, resolver):
        """uint64 type should convert to UINT64."""
        result = resolver._schema_to_lopper_fmt('test', {'type': 'uint64'})
        assert result == LopperFmt.UINT64

    def test_uint8_type(self, resolver):
        """uint8 type should convert to UINT8."""
        result = resolver._schema_to_lopper_fmt('test', {'type': 'uint8'})
        assert result == LopperFmt.UINT8

    def test_string_type(self, resolver):
        """string type should convert to STRING."""
        result = resolver._schema_to_lopper_fmt('test', {'type': 'string'})
        assert result == LopperFmt.STRING

    def test_string_array_type(self, resolver):
        """string-array type should convert to MULTI_STRING."""
        result = resolver._schema_to_lopper_fmt('test', {'type': 'string-array'})
        assert result == LopperFmt.MULTI_STRING

    def test_boolean_type(self, resolver):
        """boolean type should convert to EMPTY."""
        result = resolver._schema_to_lopper_fmt('test', {'type': 'boolean'})
        assert result == LopperFmt.EMPTY

    def test_integer_type(self, resolver):
        """integer type should convert to UINT32."""
        result = resolver._schema_to_lopper_fmt('test', {'type': 'integer'})
        assert result == LopperFmt.UINT32

    def test_phandle_array_type(self, resolver):
        """phandle-array type should convert to UINT32."""
        result = resolver._schema_to_lopper_fmt('test', {'type': 'phandle-array'})
        assert result == LopperFmt.UINT32

    def test_array_of_integers(self, resolver):
        """Array of integers should convert to UINT32."""
        result = resolver._schema_to_lopper_fmt('test', {
            'type': 'array',
            'items': {'type': 'integer'}
        })
        assert result == LopperFmt.UINT32

    def test_array_of_strings(self, resolver):
        """Array of strings should convert to MULTI_STRING."""
        result = resolver._schema_to_lopper_fmt('test', {
            'type': 'array',
            'items': {'type': 'string'}
        })
        assert result == LopperFmt.MULTI_STRING

    def test_uint8_format_override(self, resolver):
        """Format uint8 should override type string."""
        result = resolver._schema_to_lopper_fmt('test', {
            'type': 'string',
            'format': 'uint8'
        })
        assert result == LopperFmt.UINT8

    def test_uint8_array_format_override(self, resolver):
        """Format uint8-array should override type string."""
        result = resolver._schema_to_lopper_fmt('test', {
            'type': 'string',
            'format': 'uint8-array'
        })
        assert result == LopperFmt.UINT8

    def test_uint16_array_format_override(self, resolver):
        """Format uint16-array should override type string."""
        result = resolver._schema_to_lopper_fmt('test', {
            'type': 'string',
            'format': 'uint16-array'
        })
        assert result == LopperFmt.UINT16

    def test_uint64_bits_format(self, resolver):
        """Format uint64-bits should return UINT64."""
        result = resolver._schema_to_lopper_fmt('test', {
            'type': 'string',
            'format': 'uint64-bits'
        })
        assert result == LopperFmt.UINT64

    def test_uint64_bits_array_format(self, resolver):
        """Format uint64-bits-array should return UINT64."""
        result = resolver._schema_to_lopper_fmt('test', {
            'type': 'string',
            'format': 'uint64-bits-array'
        })
        assert result == LopperFmt.UINT64

    def test_oneof_with_type_frequencies(self, resolver):
        """oneOf with _type_frequencies should use most common type."""
        result = resolver._schema_to_lopper_fmt('test', {
            'oneOf': [
                {'type': 'integer'},
                {'type': 'string'}
            ],
            '_type_frequencies': {
                'uint32': 10,
                'string': 2
            }
        })
        assert result == LopperFmt.UINT32

    def test_oneof_string_majority(self, resolver):
        """oneOf with string majority should return STRING."""
        result = resolver._schema_to_lopper_fmt('test', {
            'oneOf': [
                {'type': 'integer'},
                {'type': 'string'}
            ],
            '_type_frequencies': {
                'uint32': 2,
                'string': 10
            }
        })
        assert result == LopperFmt.STRING

    def test_empty_definition(self, resolver):
        """Empty definition should return UNKNOWN."""
        result = resolver._schema_to_lopper_fmt('test', {})
        assert result == LopperFmt.UNKNOWN

    def test_none_definition(self, resolver):
        """None definition should return UNKNOWN."""
        result = resolver._schema_to_lopper_fmt('test', None)
        assert result == LopperFmt.UNKNOWN


class TestDTSSchemaGeneratorCellGrouping:
    """Test cell grouping determination."""

    @pytest.fixture
    def generator(self):
        """Create a fresh generator."""
        return DTSSchemaGenerator()

    def test_reg_grouping_even_cells(self, generator):
        """reg with even cells should group by 2."""
        cells = ['0x0', '0x1000', '0x80000000', '0x2000']
        result = generator._determine_cell_grouping('reg', cells)
        assert result == 2

    def test_ranges_grouping_divisible_by_3(self, generator):
        """ranges divisible by 3 should group by 3."""
        cells = ['0x0', '0x0', '0x1000', '0x1000', '0x1000', '0x2000']
        result = generator._determine_cell_grouping('ranges', cells)
        assert result == 3

    def test_interrupts_grouping(self, generator):
        """interrupts divisible by 3 should group by 3."""
        cells = ['0x0', '0x1', '0x4', '0x0', '0x2', '0x4']
        result = generator._determine_cell_grouping('interrupts', cells)
        assert result == 3

    def test_clocks_grouping(self, generator):
        """clocks with even cells should group by 2."""
        cells = ['0x1', '0x0', '0x2', '0x1']
        result = generator._determine_cell_grouping('clocks', cells)
        assert result == 2

    def test_unknown_property_no_grouping(self, generator):
        """Unknown property should have no grouping."""
        cells = ['0x1', '0x2', '0x3', '0x4', '0x5']
        result = generator._determine_cell_grouping('unknown-prop', cells)
        assert result == 1


class TestPropertyTypeConsistency:
    """Test that property types are consistent across resolution paths."""

    @pytest.fixture
    def full_schema(self):
        """Create schema with various property definitions."""
        return {
            'property_definitions': {
                'compatible': {'type': 'string'},
                'reg': {'type': 'uint32'},
                'interrupts': {'type': 'uint32'},
                'clocks': {'type': 'uint32'},
                'status': {'type': 'string'},
                'no-map': {'type': 'boolean'},
            }
        }

    @pytest.fixture
    def resolver(self, full_schema):
        """Create resolver with full schema."""
        return DTSPropertyTypeResolver(full_schema)

    def test_compatible_consistent(self, resolver):
        """compatible should be STRING via all paths."""
        # Via schema
        schema_result = resolver.get_property_type('compatible')
        # Via heuristics
        heuristic_result = resolver._apply_heuristics('compatible')
        assert schema_result == LopperFmt.STRING
        assert heuristic_result == LopperFmt.STRING

    def test_reg_consistent(self, resolver):
        """reg should be UINT32 via all paths."""
        schema_result = resolver.get_property_type('reg')
        heuristic_result = resolver._apply_heuristics('reg')
        assert schema_result == LopperFmt.UINT32
        assert heuristic_result == LopperFmt.UINT32

    def test_no_map_consistent(self, resolver):
        """no-map should be EMPTY via all paths."""
        schema_result = resolver.get_property_type('no-map')
        heuristic_result = resolver._apply_heuristics('no-map')
        assert schema_result == LopperFmt.EMPTY
        assert heuristic_result == LopperFmt.EMPTY


class TestResolvePropertySpec:
    """Test DTSPropertyTypeResolver.resolve_property_spec method (Phase 2)."""

    @pytest.fixture
    def schema_with_definitions(self):
        """Create schema with various property definitions."""
        return {
            'property_definitions': {
                'my-uint32-prop': {
                    'type': 'uint32',
                    'description': 'A test uint32 property',
                },
                'my-string-prop': {
                    'type': 'string',
                },
                'mixed-type-prop': {
                    'oneOf': [
                        {'type': 'integer'},
                        {'type': 'string'}
                    ],
                    '_type_frequencies': {
                        'uint32': 10,
                        'string': 2
                    }
                },
                'phandle-prop': {
                    'type': 'phandle-array',
                    'phandle-pattern': 'phandle + 2 cells',
                    'context-lookups': ['#clock-cells'],
                },
            },
            'compatible_mappings': {
                'vendor,device': {
                    'properties': {
                        'vendor-prop': {'type': 'uint32'}
                    }
                }
            },
            'path_overrides': {
                '/special/node': {
                    'properties': {
                        'special-prop': {'type': 'uint64'}
                    }
                }
            }
        }

    @pytest.fixture
    def resolver(self, schema_with_definitions):
        """Create resolver with test schema."""
        return DTSPropertyTypeResolver(schema_with_definitions)

    def test_returns_property_spec(self, resolver):
        """resolve_property_spec should return PropertySpec."""
        result = resolver.resolve_property_spec('my-uint32-prop')
        assert isinstance(result, PropertySpec)

    def test_property_spec_has_name(self, resolver):
        """PropertySpec should have correct name."""
        result = resolver.resolve_property_spec('my-uint32-prop')
        assert result.name == 'my-uint32-prop'

    def test_property_spec_has_type_def(self, resolver):
        """PropertySpec should have TypeDefinition."""
        result = resolver.resolve_property_spec('my-uint32-prop')
        assert isinstance(result.type_def, TypeDefinition)

    def test_uint32_resolves_to_correct_type(self, resolver):
        """uint32 property should resolve to UINT32 PropertyType."""
        result = resolver.resolve_property_spec('my-uint32-prop')
        assert result.type_def.property_type == PropertyType.UINT32

    def test_string_resolves_to_correct_type(self, resolver):
        """string property should resolve to STRING PropertyType."""
        result = resolver.resolve_property_spec('my-string-prop')
        assert result.type_def.property_type == PropertyType.STRING

    def test_type_converts_to_lopper_fmt(self, resolver):
        """PropertyType should convert to correct LopperFmt."""
        result = resolver.resolve_property_spec('my-uint32-prop')
        lopper_fmt = result.type_def.property_type.to_lopper_fmt()
        assert lopper_fmt == LopperFmt.UINT32

    def test_source_is_learned(self, resolver):
        """Source should be 'learned' for schema-defined properties."""
        result = resolver.resolve_property_spec('my-uint32-prop')
        assert result.source == 'learned'

    def test_confidence_for_learned(self, resolver):
        """Confidence should be 0.8 for learned properties."""
        result = resolver.resolve_property_spec('my-uint32-prop')
        assert result.confidence == 0.8

    def test_type_frequencies_preserved(self, resolver):
        """Type frequencies should be preserved in PropertySpec."""
        result = resolver.resolve_property_spec('mixed-type-prop')
        assert result.type_frequencies == {'uint32': 10, 'string': 2}

    def test_phandle_pattern_preserved(self, resolver):
        """Phandle pattern should be preserved in PropertySpec."""
        result = resolver.resolve_property_spec('phandle-prop')
        assert result.phandle_pattern == 'phandle + 2 cells'

    def test_context_lookups_preserved(self, resolver):
        """Context lookups should be preserved in PropertySpec."""
        result = resolver.resolve_property_spec('phandle-prop')
        assert result.context_lookups == ['#clock-cells']

    def test_compatible_specific_source(self, resolver):
        """Compatible-specific property should have learned-compatible source."""
        result = resolver.resolve_property_spec('vendor-prop', compatible='vendor,device')
        assert result.source == 'learned-compatible'
        assert result.confidence == 0.85

    def test_path_specific_source(self, resolver):
        """Path-specific property should have learned-path source."""
        result = resolver.resolve_property_spec('special-prop', node_path='/special/node')
        assert result.source == 'learned-path'
        assert result.confidence == 0.9

    def test_heuristic_exact_match(self, resolver):
        """Heuristic exact match should have source 'heuristic'."""
        result = resolver.resolve_property_spec('compatible')
        assert result.source == 'heuristic'
        assert result.confidence == 0.7

    def test_heuristic_suffix_match(self, resolver):
        """Heuristic suffix match should have lower confidence."""
        result = resolver.resolve_property_spec('clock-names')
        assert result.source == 'heuristic'
        assert result.confidence == 0.5

    def test_unknown_property(self, resolver):
        """Unknown property should have source 'unknown'."""
        result = resolver.resolve_property_spec('totally-unknown-xyz')
        assert result.source == 'unknown'
        assert result.confidence == 0.0

    def test_consistency_with_get_property_type(self, resolver):
        """resolve_property_spec should be consistent with get_property_type."""
        # Test several properties
        for prop in ['my-uint32-prop', 'my-string-prop', 'compatible', 'clock-names']:
            spec = resolver.resolve_property_spec(prop)
            lopper_fmt = resolver.get_property_type(prop)
            # Convert PropertySpec type to LopperFmt
            spec_lopper_fmt = spec.type_def.property_type.to_lopper_fmt()
            assert spec_lopper_fmt == lopper_fmt, \
                f"{prop}: PropertySpec gives {spec_lopper_fmt}, get_property_type gives {lopper_fmt}"
