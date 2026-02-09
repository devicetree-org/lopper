"""
Pytest migration of schema_type_sanity_test() from lopper_sanity.py

This module contains tests for schema type detection and property format preservation.
Migrated from lopper_sanity.py lines 2335-2411.

Copyright (c) 2019,2020 Xilinx Inc. All rights reserved.
Copyright (C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.

SPDX-License-Identifier: BSD-3-Clause

Author:
    Bruce Ashfield <bruce.ashfield@amd.com>
"""

import os
import re
import pytest
from lopper import LopperSDT
import lopper.schema
from lopper.fmt import LopperFmt


class TestSchemaTypeDetection:
    """Test schema-based type detection for properties.

    Reference: lopper_sanity.py:2362-2391
    """

    def test_string_property_detection(self, schema_lopper_sdt):
        """Test detection of string property type."""
        resolver = lopper.schema.get_schema_manager().get_resolver()
        if not resolver:
            pytest.skip("Schema resolver not available")

        detected_fmt = resolver.get_property_type("string-prop", "/schema-types/test")
        assert detected_fmt == LopperFmt.STRING, \
            f"Expected STRING format, got {detected_fmt.name if isinstance(detected_fmt, LopperFmt) else detected_fmt}"

    def test_multi_string_property_detection(self, schema_lopper_sdt):
        """Test detection of multi-string property type."""
        resolver = lopper.schema.get_schema_manager().get_resolver()
        if not resolver:
            pytest.skip("Schema resolver not available")

        detected_fmt = resolver.get_property_type("names-list", "/schema-types/test")
        assert detected_fmt == LopperFmt.MULTI_STRING, \
            f"Expected MULTI_STRING format, got {detected_fmt.name if isinstance(detected_fmt, LopperFmt) else detected_fmt}"

    def test_empty_flag_property_detection(self, schema_lopper_sdt):
        """Test detection of empty/flag property type."""
        resolver = lopper.schema.get_schema_manager().get_resolver()
        if not resolver:
            pytest.skip("Schema resolver not available")

        detected_fmt = resolver.get_property_type("flag-prop", "/schema-types/test")
        assert detected_fmt == LopperFmt.EMPTY, \
            f"Expected EMPTY format, got {detected_fmt.name if isinstance(detected_fmt, LopperFmt) else detected_fmt}"

    def test_uint32_property_detection(self, schema_lopper_sdt):
        """Test detection of uint32 property type."""
        resolver = lopper.schema.get_schema_manager().get_resolver()
        if not resolver:
            pytest.skip("Schema resolver not available")

        detected_fmt = resolver.get_property_type("cells-prop", "/schema-types/test")
        assert detected_fmt == LopperFmt.UINT32, \
            f"Expected UINT32 format, got {detected_fmt.name if isinstance(detected_fmt, LopperFmt) else detected_fmt}"

    def test_uint64_property_detection(self, schema_lopper_sdt):
        """Test detection of uint64 property type."""
        resolver = lopper.schema.get_schema_manager().get_resolver()
        if not resolver:
            pytest.skip("Schema resolver not available")

        detected_fmt = resolver.get_property_type("range64", "/schema-types/test")
        assert detected_fmt == LopperFmt.UINT64, \
            f"Expected UINT64 format, got {detected_fmt.name if isinstance(detected_fmt, LopperFmt) else detected_fmt}"

    def test_uint16_property_detection(self, schema_lopper_sdt):
        """Test detection of uint16 property type."""
        resolver = lopper.schema.get_schema_manager().get_resolver()
        if not resolver:
            pytest.skip("Schema resolver not available")

        detected_fmt = resolver.get_property_type("width16", "/schema-types/test")
        assert detected_fmt == LopperFmt.UINT16, \
            f"Expected UINT16 format, got {detected_fmt.name if isinstance(detected_fmt, LopperFmt) else detected_fmt}"

    def test_uint8_property_detection(self, schema_lopper_sdt):
        """Test detection of uint8 property type."""
        resolver = lopper.schema.get_schema_manager().get_resolver()
        if not resolver:
            pytest.skip("Schema resolver not available")

        detected_fmt = resolver.get_property_type("mac-bytes", "/schema-types/test")
        assert detected_fmt == LopperFmt.UINT8, \
            f"Expected UINT8 format, got {detected_fmt.name if isinstance(detected_fmt, LopperFmt) else detected_fmt}"

    def test_phandle_property_detection(self, schema_lopper_sdt):
        """Test detection of phandle list property type."""
        resolver = lopper.schema.get_schema_manager().get_resolver()
        if not resolver:
            pytest.skip("Schema resolver not available")

        detected_fmt = resolver.get_property_type("phandle-list", "/schema-types/test")
        assert detected_fmt == LopperFmt.UINT32, \
            f"Expected UINT32 format for phandle, got {detected_fmt.name if isinstance(detected_fmt, LopperFmt) else detected_fmt}"


class TestPropertyFormatPreservation:
    """Test that property formats are preserved in output.

    Reference: lopper_sanity.py:2393-2407
    """

    def test_flag_property_preserved(self, schema_lopper_sdt):
        """Test that flag properties are preserved in output."""
        output_file = schema_lopper_sdt.output_file
        assert os.path.exists(output_file), "Output file not found"

        with open(output_file) as f:
            content = f.read()
            assert re.search(r"flag-prop;", content), \
                "Flag property not preserved in output"

    def test_multi_string_preserved(self, schema_lopper_sdt):
        """Test that multi-string properties are preserved."""
        output_file = schema_lopper_sdt.output_file
        with open(output_file) as f:
            content = f.read()
            assert re.search(r'names-list = "alpha", "beta"', content), \
                "Multi-string property not preserved in output"

    def test_64bit_literal_preserved(self, schema_lopper_sdt):
        """Test that 64-bit literals are preserved."""
        output_file = schema_lopper_sdt.output_file
        with open(output_file) as f:
            content = f.read()
            assert re.search(r"range64 = /bits/ 64", content), \
                "64-bit literal not preserved in output"

    def test_64bit_single_value_preserved(self, schema_lopper_sdt):
        """Test that single 64-bit values are preserved."""
        output_file = schema_lopper_sdt.output_file
        with open(output_file) as f:
            content = f.read()
            assert re.search(r"bootscr-address = /bits/ 64 <0x78000000>", content), \
                "64-bit single value not preserved in output"

    def test_16bit_literal_preserved(self, schema_lopper_sdt):
        """Test that 16-bit literals are preserved."""
        output_file = schema_lopper_sdt.output_file
        with open(output_file) as f:
            content = f.read()
            assert re.search(r"width16 = /bits/ 16", content), \
                "16-bit literal not preserved in output"

    def test_phandle_reference_preserved(self, schema_lopper_sdt):
        """Test that phandle references are preserved."""
        output_file = schema_lopper_sdt.output_file
        with open(output_file) as f:
            content = f.read()
            assert re.search(r"phandle-list = <&refnode", content), \
                "Phandle reference not preserved in output"
