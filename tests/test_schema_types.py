"""
Tests for unified schema types (PropertyType, TypeDefinition).

These tests ensure the new unified type system converts correctly to/from
LopperFmt and maintains consistency with learned schema output.

Copyright (c) 2026 AMD Inc. All rights reserved.

SPDX-License-Identifier: BSD-3-Clause

Author:
    Bruce Ashfield <bruce.ashfield@amd.com>
"""

import pytest
from lopper.fmt import LopperFmt
from lopper.schema import (
    PropertyType,
    TypeDefinition,
    DT_SCHEMA_TYPES,
)


class TestPropertyTypeToLopperFmt:
    """Test PropertyType.to_lopper_fmt() conversion."""

    def test_uint8_to_lopper(self):
        """UINT8 should convert to LopperFmt.UINT8."""
        assert PropertyType.UINT8.to_lopper_fmt() == LopperFmt.UINT8

    def test_uint16_to_lopper(self):
        """UINT16 should convert to LopperFmt.UINT16."""
        assert PropertyType.UINT16.to_lopper_fmt() == LopperFmt.UINT16

    def test_uint32_to_lopper(self):
        """UINT32 should convert to LopperFmt.UINT32."""
        assert PropertyType.UINT32.to_lopper_fmt() == LopperFmt.UINT32

    def test_uint64_to_lopper(self):
        """UINT64 should convert to LopperFmt.UINT64."""
        assert PropertyType.UINT64.to_lopper_fmt() == LopperFmt.UINT64

    def test_string_to_lopper(self):
        """STRING should convert to LopperFmt.STRING."""
        assert PropertyType.STRING.to_lopper_fmt() == LopperFmt.STRING

    def test_string_array_to_lopper(self):
        """STRING_ARRAY should convert to LopperFmt.MULTI_STRING."""
        assert PropertyType.STRING_ARRAY.to_lopper_fmt() == LopperFmt.MULTI_STRING

    def test_flag_to_lopper(self):
        """FLAG should convert to LopperFmt.EMPTY."""
        assert PropertyType.FLAG.to_lopper_fmt() == LopperFmt.EMPTY

    def test_empty_to_lopper(self):
        """EMPTY should convert to LopperFmt.EMPTY."""
        assert PropertyType.EMPTY.to_lopper_fmt() == LopperFmt.EMPTY

    def test_phandle_to_lopper(self):
        """PHANDLE should convert to LopperFmt.UINT32."""
        assert PropertyType.PHANDLE.to_lopper_fmt() == LopperFmt.UINT32

    def test_phandle_array_to_lopper(self):
        """PHANDLE_ARRAY should convert to LopperFmt.UINT32."""
        assert PropertyType.PHANDLE_ARRAY.to_lopper_fmt() == LopperFmt.UINT32

    def test_uint8_array_to_lopper(self):
        """UINT8_ARRAY should convert to LopperFmt.UINT8."""
        assert PropertyType.UINT8_ARRAY.to_lopper_fmt() == LopperFmt.UINT8

    def test_uint16_array_to_lopper(self):
        """UINT16_ARRAY should convert to LopperFmt.UINT16."""
        assert PropertyType.UINT16_ARRAY.to_lopper_fmt() == LopperFmt.UINT16

    def test_uint32_array_to_lopper(self):
        """UINT32_ARRAY should convert to LopperFmt.UINT32."""
        assert PropertyType.UINT32_ARRAY.to_lopper_fmt() == LopperFmt.UINT32

    def test_uint64_array_to_lopper(self):
        """UINT64_ARRAY should convert to LopperFmt.UINT64."""
        assert PropertyType.UINT64_ARRAY.to_lopper_fmt() == LopperFmt.UINT64

    def test_unknown_to_lopper(self):
        """UNKNOWN should convert to LopperFmt.UNKNOWN."""
        assert PropertyType.UNKNOWN.to_lopper_fmt() == LopperFmt.UNKNOWN

    def test_signed_int8_to_lopper(self):
        """INT8 should convert to LopperFmt.UINT8 (no signed in LopperFmt)."""
        assert PropertyType.INT8.to_lopper_fmt() == LopperFmt.UINT8

    def test_signed_int16_to_lopper(self):
        """INT16 should convert to LopperFmt.UINT16 (no signed in LopperFmt)."""
        assert PropertyType.INT16.to_lopper_fmt() == LopperFmt.UINT16

    def test_signed_int32_to_lopper(self):
        """INT32 should convert to LopperFmt.UINT32 (no signed in LopperFmt)."""
        assert PropertyType.INT32.to_lopper_fmt() == LopperFmt.UINT32

    def test_signed_int64_to_lopper(self):
        """INT64 should convert to LopperFmt.UINT64 (no signed in LopperFmt)."""
        assert PropertyType.INT64.to_lopper_fmt() == LopperFmt.UINT64


class TestPropertyTypeFromLopperFmt:
    """Test PropertyType.from_lopper_fmt() conversion."""

    def test_from_uint8(self):
        """LopperFmt.UINT8 should convert to UINT8."""
        assert PropertyType.from_lopper_fmt(LopperFmt.UINT8) == PropertyType.UINT8

    def test_from_uint16(self):
        """LopperFmt.UINT16 should convert to UINT16."""
        assert PropertyType.from_lopper_fmt(LopperFmt.UINT16) == PropertyType.UINT16

    def test_from_uint32(self):
        """LopperFmt.UINT32 should convert to UINT32."""
        assert PropertyType.from_lopper_fmt(LopperFmt.UINT32) == PropertyType.UINT32

    def test_from_uint64(self):
        """LopperFmt.UINT64 should convert to UINT64."""
        assert PropertyType.from_lopper_fmt(LopperFmt.UINT64) == PropertyType.UINT64

    def test_from_string(self):
        """LopperFmt.STRING should convert to STRING."""
        assert PropertyType.from_lopper_fmt(LopperFmt.STRING) == PropertyType.STRING

    def test_from_multi_string(self):
        """LopperFmt.MULTI_STRING should convert to STRING_ARRAY."""
        assert PropertyType.from_lopper_fmt(LopperFmt.MULTI_STRING) == PropertyType.STRING_ARRAY

    def test_from_empty(self):
        """LopperFmt.EMPTY should convert to FLAG."""
        assert PropertyType.from_lopper_fmt(LopperFmt.EMPTY) == PropertyType.FLAG

    def test_from_unknown(self):
        """LopperFmt.UNKNOWN should convert to UNKNOWN."""
        assert PropertyType.from_lopper_fmt(LopperFmt.UNKNOWN) == PropertyType.UNKNOWN


class TestPropertyTypeRoundTrip:
    """Test round-trip conversion PropertyType -> LopperFmt -> PropertyType."""

    def test_uint8_roundtrip(self):
        """UINT8 should round-trip correctly."""
        original = PropertyType.UINT8
        lopper_fmt = original.to_lopper_fmt()
        result = PropertyType.from_lopper_fmt(lopper_fmt)
        assert result == original

    def test_uint16_roundtrip(self):
        """UINT16 should round-trip correctly."""
        original = PropertyType.UINT16
        lopper_fmt = original.to_lopper_fmt()
        result = PropertyType.from_lopper_fmt(lopper_fmt)
        assert result == original

    def test_uint32_roundtrip(self):
        """UINT32 should round-trip correctly."""
        original = PropertyType.UINT32
        lopper_fmt = original.to_lopper_fmt()
        result = PropertyType.from_lopper_fmt(lopper_fmt)
        assert result == original

    def test_uint64_roundtrip(self):
        """UINT64 should round-trip correctly."""
        original = PropertyType.UINT64
        lopper_fmt = original.to_lopper_fmt()
        result = PropertyType.from_lopper_fmt(lopper_fmt)
        assert result == original

    def test_string_roundtrip(self):
        """STRING should round-trip correctly."""
        original = PropertyType.STRING
        lopper_fmt = original.to_lopper_fmt()
        result = PropertyType.from_lopper_fmt(lopper_fmt)
        assert result == original

    def test_string_array_roundtrip(self):
        """STRING_ARRAY should round-trip correctly."""
        original = PropertyType.STRING_ARRAY
        lopper_fmt = original.to_lopper_fmt()
        result = PropertyType.from_lopper_fmt(lopper_fmt)
        assert result == original

    def test_flag_to_empty_roundtrip(self):
        """FLAG -> EMPTY -> FLAG (via from_lopper_fmt)."""
        original = PropertyType.FLAG
        lopper_fmt = original.to_lopper_fmt()
        assert lopper_fmt == LopperFmt.EMPTY
        result = PropertyType.from_lopper_fmt(lopper_fmt)
        assert result == PropertyType.FLAG


class TestTypeDefinition:
    """Test TypeDefinition dataclass."""

    def test_basic_creation(self):
        """TypeDefinition should be creatable with just property_type."""
        td = TypeDefinition(PropertyType.UINT32)
        assert td.property_type == PropertyType.UINT32
        assert td.min_value is None
        assert td.max_value is None
        assert td.source == "unknown"

    def test_with_range(self):
        """TypeDefinition should accept min/max values."""
        td = TypeDefinition(
            PropertyType.UINT8,
            min_value=0,
            max_value=255
        )
        assert td.min_value == 0
        assert td.max_value == 255

    def test_with_source(self):
        """TypeDefinition should accept source."""
        td = TypeDefinition(
            PropertyType.STRING,
            source="dt-schema"
        )
        assert td.source == "dt-schema"

    def test_with_description(self):
        """TypeDefinition should accept description."""
        td = TypeDefinition(
            PropertyType.PHANDLE,
            description="Reference to another node"
        )
        assert td.description == "Reference to another node"

    def test_with_enum_values(self):
        """TypeDefinition should accept enum_values."""
        td = TypeDefinition(
            PropertyType.STRING,
            enum_values=["okay", "disabled", "fail"]
        )
        assert td.enum_values == ["okay", "disabled", "fail"]

    def test_with_array_bounds(self):
        """TypeDefinition should accept min_items/max_items."""
        td = TypeDefinition(
            PropertyType.UINT32_ARRAY,
            min_items=1,
            max_items=10
        )
        assert td.min_items == 1
        assert td.max_items == 10


class TestDTSchemaTypes:
    """Test pre-built DT_SCHEMA_TYPES dictionary."""

    def test_uint8_exists(self):
        """uint8 type should exist in DT_SCHEMA_TYPES."""
        assert 'uint8' in DT_SCHEMA_TYPES
        td = DT_SCHEMA_TYPES['uint8']
        assert td.property_type == PropertyType.UINT8
        assert td.min_value == 0
        assert td.max_value == 255

    def test_uint16_exists(self):
        """uint16 type should exist in DT_SCHEMA_TYPES."""
        assert 'uint16' in DT_SCHEMA_TYPES
        td = DT_SCHEMA_TYPES['uint16']
        assert td.property_type == PropertyType.UINT16
        assert td.min_value == 0
        assert td.max_value == 65535

    def test_uint32_exists(self):
        """uint32 type should exist in DT_SCHEMA_TYPES."""
        assert 'uint32' in DT_SCHEMA_TYPES
        td = DT_SCHEMA_TYPES['uint32']
        assert td.property_type == PropertyType.UINT32
        assert td.min_value == 0
        assert td.max_value == 0xffffffff

    def test_uint64_exists(self):
        """uint64 type should exist in DT_SCHEMA_TYPES."""
        assert 'uint64' in DT_SCHEMA_TYPES
        td = DT_SCHEMA_TYPES['uint64']
        assert td.property_type == PropertyType.UINT64

    def test_phandle_exists(self):
        """phandle type should exist in DT_SCHEMA_TYPES."""
        assert 'phandle' in DT_SCHEMA_TYPES
        td = DT_SCHEMA_TYPES['phandle']
        assert td.property_type == PropertyType.PHANDLE
        assert td.min_value == 1  # phandle 0 is invalid

    def test_string_exists(self):
        """string type should exist in DT_SCHEMA_TYPES."""
        assert 'string' in DT_SCHEMA_TYPES
        td = DT_SCHEMA_TYPES['string']
        assert td.property_type == PropertyType.STRING

    def test_flag_exists(self):
        """flag type should exist in DT_SCHEMA_TYPES."""
        assert 'flag' in DT_SCHEMA_TYPES
        td = DT_SCHEMA_TYPES['flag']
        assert td.property_type == PropertyType.FLAG

    def test_all_have_dt_schema_source(self):
        """All DT_SCHEMA_TYPES should have source='dt-schema'."""
        for name, td in DT_SCHEMA_TYPES.items():
            assert td.source == "dt-schema", f"{name} has wrong source: {td.source}"


class TestPropertyTypeValues:
    """Test PropertyType enum values match expected strings."""

    def test_uint8_value(self):
        assert PropertyType.UINT8.value == "uint8"

    def test_uint16_value(self):
        assert PropertyType.UINT16.value == "uint16"

    def test_uint32_value(self):
        assert PropertyType.UINT32.value == "uint32"

    def test_uint64_value(self):
        assert PropertyType.UINT64.value == "uint64"

    def test_string_value(self):
        assert PropertyType.STRING.value == "string"

    def test_string_array_value(self):
        assert PropertyType.STRING_ARRAY.value == "string-array"

    def test_phandle_value(self):
        assert PropertyType.PHANDLE.value == "phandle"

    def test_phandle_array_value(self):
        assert PropertyType.PHANDLE_ARRAY.value == "phandle-array"

    def test_flag_value(self):
        assert PropertyType.FLAG.value == "flag"

    def test_empty_value(self):
        assert PropertyType.EMPTY.value == "empty"

    def test_unknown_value(self):
        assert PropertyType.UNKNOWN.value == "unknown"


class TestTypeDefinitionConversionToLopperFmt:
    """Test that TypeDefinition can convert its property_type to LopperFmt."""

    def test_uint32_typedef_to_lopper(self):
        """TypeDefinition with UINT32 should convert to LopperFmt.UINT32."""
        td = TypeDefinition(PropertyType.UINT32)
        assert td.property_type.to_lopper_fmt() == LopperFmt.UINT32

    def test_string_typedef_to_lopper(self):
        """TypeDefinition with STRING should convert to LopperFmt.STRING."""
        td = TypeDefinition(PropertyType.STRING)
        assert td.property_type.to_lopper_fmt() == LopperFmt.STRING

    def test_flag_typedef_to_lopper(self):
        """TypeDefinition with FLAG should convert to LopperFmt.EMPTY."""
        td = TypeDefinition(PropertyType.FLAG)
        assert td.property_type.to_lopper_fmt() == LopperFmt.EMPTY

    def test_dt_schema_uint32_to_lopper(self):
        """DT_SCHEMA_TYPES['uint32'] should convert to LopperFmt.UINT32."""
        td = DT_SCHEMA_TYPES['uint32']
        assert td.property_type.to_lopper_fmt() == LopperFmt.UINT32

    def test_dt_schema_string_to_lopper(self):
        """DT_SCHEMA_TYPES['string'] should convert to LopperFmt.STRING."""
        td = DT_SCHEMA_TYPES['string']
        assert td.property_type.to_lopper_fmt() == LopperFmt.STRING

    def test_dt_schema_phandle_to_lopper(self):
        """DT_SCHEMA_TYPES['phandle'] should convert to LopperFmt.UINT32."""
        td = DT_SCHEMA_TYPES['phandle']
        assert td.property_type.to_lopper_fmt() == LopperFmt.UINT32


class TestDeprecationWarnings:
    """Test deprecation warnings for legacy APIs."""

    def test_get_schema_manager_warns(self):
        """get_schema_manager() should emit deprecation warning."""
        import warnings
        import lopper.schema

        # Enable deprecation warnings for this test
        lopper.schema._DEPRECATION_WARNINGS_ENABLED = True

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            manager = lopper.schema.get_schema_manager()

            # Check a deprecation warning was issued
            assert len(w) >= 1
            assert any(issubclass(warning.category, DeprecationWarning) for warning in w)
            assert any("get_schema_manager" in str(warning.message) for warning in w)

    def test_schema_manager_class_warns(self):
        """SchemaManager() should emit deprecation warning."""
        import warnings
        import lopper.schema

        # Enable deprecation warnings for this test
        lopper.schema._DEPRECATION_WARNINGS_ENABLED = True

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            # Creating SchemaManager directly should warn
            manager = lopper.schema.SchemaManager()

            assert len(w) >= 1
            assert any(issubclass(warning.category, DeprecationWarning) for warning in w)
            assert any("SchemaManager" in str(warning.message) for warning in w)

    def test_deprecation_warnings_can_be_disabled(self):
        """Deprecation warnings should be suppressible."""
        import warnings
        import lopper.schema

        # Disable deprecation warnings
        lopper.schema._DEPRECATION_WARNINGS_ENABLED = False

        try:
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                manager = lopper.schema.get_schema_manager()

                # No deprecation warnings should be issued
                deprecation_warnings = [
                    warning for warning in w
                    if issubclass(warning.category, DeprecationWarning)
                    and "get_schema_manager" in str(warning.message)
                ]
                assert len(deprecation_warnings) == 0
        finally:
            # Re-enable for other tests
            lopper.schema._DEPRECATION_WARNINGS_ENABLED = True

    def test_new_api_does_not_warn(self):
        """get_registry() should not emit deprecation warning."""
        import warnings
        import lopper.schema

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            registry = lopper.schema.get_registry()

            # No deprecation warnings for new API
            deprecation_warnings = [
                warning for warning in w
                if issubclass(warning.category, DeprecationWarning)
            ]
            assert len(deprecation_warnings) == 0
