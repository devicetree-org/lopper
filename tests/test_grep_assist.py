"""
Pytest tests for the grep assist module.

This module tests the grep assist functionality for searching device tree
nodes and properties.

Copyright (c) 2019,2020 Xilinx Inc. All rights reserved.
Copyright (C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.

SPDX-License-Identifier: BSD-3-Clause

Author:
    Bruce Ashfield <bruce.ashfield@amd.com>
"""

import os
import pytest
import sys
from io import StringIO
from lopper import LopperSDT
from lopper.assists import grep


class TestGrepAssist:
    """Test grep assist module functionality."""

    def test_grep_is_compat(self):
        """Test that grep assist is recognized by compatibility string."""
        result = grep.is_compat(None, "module,grep")
        assert result == grep.grep, "grep assist should be recognized"

    def test_grep_not_compat(self):
        """Test that non-grep compat strings don't match."""
        result = grep.is_compat(None, "some,other,compat")
        assert result == "", "Non-grep compat should return empty string"

    def test_grep_find_property(self, lopper_sdt, capsys):
        """Test grep can find properties in nodes."""
        # Search for compatible property in all nodes
        options = {
            'verbose': 0,
            'args': ['compatible']  # Search for 'compatible' property
        }

        result = grep.grep(None, lopper_sdt, options)

        assert result is True, "grep should return True on success"

        # Capture output
        captured = capsys.readouterr()

        # Should have found compatible properties and printed them
        assert len(captured.out) > 0, "grep should produce output"
        assert "compatible" in captured.out or "not found" in captured.out, \
            "grep output should contain property name or 'not found'"

    def test_grep_find_property_in_specific_nodes(self, lopper_sdt, capsys):
        """Test grep can search specific nodes with regex."""
        # Search for compatible in nodes matching /cpus/*
        options = {
            'verbose': 0,
            'args': ['compatible', '/cpus.*']  # Property, node regex
        }

        result = grep.grep(None, lopper_sdt, options)

        assert result is True, "grep should return True on success"

        captured = capsys.readouterr()

        # Should have output
        assert len(captured.out) > 0, "grep should produce output"

    def test_grep_property_not_found(self, lopper_sdt, capsys):
        """Test grep output when property not found."""
        # Search for a property that doesn't exist
        options = {
            'verbose': 0,
            'args': ['nonexistent-property-xyz']
        }

        result = grep.grep(None, lopper_sdt, options)

        assert result is True, "grep should return True even when nothing found"

        captured = capsys.readouterr()

        # Should print "not found" message
        assert "not found" in captured.out, "grep should indicate property not found"

    def test_grep_with_verbose(self, lopper_sdt, capsys):
        """Test grep verbose output via logging."""
        # Search with verbose enabled
        options = {
            'verbose': 1,
            'args': ['compatible']
        }

        result = grep.grep(None, lopper_sdt, options)

        assert result is True, "grep should return True on success"

        # The debug message should go to logging, not stdout
        # User-facing output should still be in stdout
        captured = capsys.readouterr()
        assert len(captured.out) > 0, "grep should produce user-facing output"


class TestGrepIntegration:
    """Integration tests using real device tree files.

    Based on real-world usage:
    ./lopper.py -f --enhanced device-trees/system-device-tree.dts -- grep compatible "/.*bus.*"
    """

    def test_grep_compatible_in_bus_nodes(self, capsys):
        """Test grep searching for 'compatible' in nodes matching /.*bus.* pattern."""
        # Check if the device tree file exists
        dt_file = "device-trees/system-device-tree.dts"
        if not os.path.exists(dt_file):
            pytest.skip(f"Device tree file not found: {dt_file}")

        # Check if libfdt is available
        libfdt_available = False
        try:
            import libfdt
            libfdt_available = True
        except ImportError:
            pytest.skip("libfdt not available")

        # Load the device tree
        sdt = LopperSDT(dt_file)
        sdt.setup(dt_file, [], "", True, libfdt=libfdt_available)

        # Run grep: search for 'compatible' property in nodes matching /.*bus.*/
        options = {
            'verbose': 0,
            'args': ['compatible', '/.*bus.*']
        }

        result = grep.grep(None, sdt, options)

        assert result is True, "grep should return True"

        # Capture output
        captured = capsys.readouterr()

        # Verify expected output format (should have bus nodes with compatible properties)
        assert len(captured.out) > 0, "grep should produce output"

        # Check for expected patterns from the sample output
        # Should find bus nodes with compatible properties
        assert "compatible =" in captured.out, "Output should contain 'compatible ='"

        # Should find at least one bus node
        assert "bus" in captured.out.lower(), "Output should contain bus nodes"

    def test_grep_on_nonexistent_nodes(self, capsys):
        """Test grep on nodes that don't exist."""
        dt_file = "device-trees/system-device-tree.dts"
        if not os.path.exists(dt_file):
            pytest.skip(f"Device tree file not found: {dt_file}")

        libfdt_available = False
        try:
            import libfdt
            libfdt_available = True
        except ImportError:
            pytest.skip("libfdt not available")

        sdt = LopperSDT(dt_file)
        sdt.setup(dt_file, [], "", True, libfdt=libfdt_available)

        # Search for property in nodes that don't exist
        options = {
            'verbose': 0,
            'args': ['compatible', '/nonexistent-node.*']
        }

        result = grep.grep(None, sdt, options)

        assert result is True, "grep should return True even with no matches"

        captured = capsys.readouterr()

        # Should indicate not found
        assert "not found" in captured.out, "Should indicate property not found"
