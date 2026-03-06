"""
Pytest migration of lops_code_test() from lopper_sanity.py

This module contains tests for lops code blocks, conditional operations,
and selection mechanisms.
Migrated from lopper_sanity.py lines 1987-2092.

Copyright (c) 2019,2020 Xilinx Inc. All rights reserved.
Copyright (C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.

SPDX-License-Identifier: BSD-3-Clause

Author:
    Bruce Ashfield <bruce.ashfield@amd.com>
"""

import re
import pytest


class TestLopsCodeBlocks:
    """Test lops code block execution.

    Reference: lopper_sanity.py:1999-2002
    """

    def test_code_block_exec(self, lops_code_output):
        """Test that code blocks execute and find a72."""
        test_output = lops_code_output

        assert re.search(r"a72 found, tagging", test_output), \
            "Code block should find and tag a72 processors"


class TestLopsConditionals:
    """Test lops conditional operations (true/false blocks).

    Reference: lopper_sanity.py:2004-2018
    """

    def test_enable_method_true_block(self, lops_code_output):
        """Test conditional true block execution."""
        test_output = lops_code_output

        assert re.search(r'\[FOUND\] enable-method', test_output), \
            "True block should execute and find enable-method"

    def test_enable_method_chained_true_block(self, lops_code_output):
        """Test chained conditional true blocks."""
        test_output = lops_code_output

        assert re.search(r'\[FOUND 2\] enable-method', test_output), \
            "Chained true block should execute"

    def test_compatible_false_block(self, lops_code_output):
        """Test conditional false block execution."""
        test_output = lops_code_output

        count = len(re.findall(r"[^']\[FOUND\] cpu that does not match invalid a72", test_output))
        assert count == 3, f"False block should execute 3 times, found {count}"


class TestLopsDoubleConditionals:
    """Test lops with multiple condition checks.

    Reference: lopper_sanity.py:2020-2042
    """

    def test_double_condition_true(self, lops_code_output):
        """Test double condition matching."""
        test_output = lops_code_output

        count = len(re.findall(r"[^']\[INFO\]: double condition a72 found", test_output))
        assert count == 2, f"Double condition should match 2 times, found {count}"

    def test_double_condition_false(self, lops_code_output):
        """Test double condition not matching."""
        test_output = lops_code_output

        count = len(re.findall(r"[^']\[INFO\]: double condition a72 not found", test_output))
        assert count == 2, f"Double condition false should execute 2 times, found {count}"

    def test_double_condition_inverted(self, lops_code_output):
        """Test inverted condition matching."""
        test_output = lops_code_output

        count = len(re.findall(r"[^']\[INFO\]: double condition inverted a72 found", test_output))
        assert count == 2, f"Inverted condition should match 2 times, found {count}"

    def test_double_condition_list(self, lops_code_output):
        """Test double condition with list values."""
        test_output = lops_code_output

        count = len(re.findall(r"[^']\[INFO\]: double condition list a72 found", test_output))
        assert count == 1, f"List condition should match once, found {count}"


class TestLopsDataPersistence:
    """Test data persistence across lop operations.

    Reference: lopper_sanity.py:2044-2054
    """

    def test_data_persistence(self, lops_code_output):
        """Test that data set in one lop persists to next."""
        test_output = lops_code_output

        count = len(re.findall(r"[^']\[INFO\]: node tag:", test_output))
        assert count == 3, f"Node tags should persist, found {count} times (expected 3)"

    def test_data_persistence_magic_clock(self, lops_code_output):
        """Test that magic-clock property persists."""
        test_output = lops_code_output

        count = len(re.findall(r"[^']\[INFO\]: clock magic", test_output))
        assert count == 1, f"Magic clock should be found once, found {count}"


class TestLopsExecLibrary:
    """Test lops exec and library routine execution.

    Reference: lopper_sanity.py:2056-2060
    """

    def test_exec_library_routine(self, lops_code_output):
        """Test that exec can call library routines."""
        test_output = lops_code_output

        count = len(re.findall(r"[^']track: lopper library routine", test_output))
        assert count == 1, f"Library routine should execute once, found {count}"


class TestLopsPrint:
    """Test lops print operations.

    Reference: lopper_sanity.py:2062-2071
    """

    def test_print_operations(self, lops_code_output):
        """Test that lop print operations work."""
        test_output = lops_code_output

        count = len(re.findall(r"[^']print_test", test_output))
        assert count == 2, f"Print test should appear twice, found {count}"

    def test_node_print(self, lops_code_output):
        """Test node printing in lops."""
        test_output = lops_code_output

        assert re.search(r"arm,idle-state", test_output), \
            "Node print should show 'arm,idle-state'"


class TestLopsSelection:
    """Test lops selection mechanisms.

    Reference: lopper_sanity.py:2073-2090
    """

    def test_selection_and_operation(self, lops_code_output):
        """Test selection with AND logic."""
        test_output = lops_code_output

        if re.search(r"[^']selected: /cpus/cpu@2", test_output):
            count = len(re.findall(r"testprop: testvalue", test_output))
            assert count == 1, f"Selected node should have testprop, found {count}"

    def test_selection_or_operation(self, lops_code_output):
        """Test selection with OR logic."""
        test_output = lops_code_output

        count = len(re.findall(r"[^']selected2:", test_output))
        assert count == 4, f"OR selection should find 4 nodes, found {count}"

    def test_selection_phandle(self, lops_code_output):
        """Test selection by phandle reference."""
        test_output = lops_code_output

        count = len(re.findall(r"[^']selected3:", test_output))
        assert count == 3, f"Phandle selection should find 3 nodes, found {count}"
