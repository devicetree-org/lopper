"""
Pytest migration of lops_sanity_test() from lopper_sanity.py

This module contains tests for Lopper Operations (lops) - declarative transformation rules.
Tests node/property modifications, additions, deletions, and output operations.
Migrated from lopper_sanity.py lines 2172-2305.

Copyright (c) 2019,2020 Xilinx Inc. All rights reserved.
Copyright (C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.

SPDX-License-Identifier: BSD-3-Clause

Author:
    Bruce Ashfield <bruce.ashfield@amd.com>
"""

import os
import re
import pytest
from pathlib import Path


class TestLopsNodeDeletion:
    """Test lops node deletion operations.

    Reference: lopper_sanity.py:2188-2192
    """

    def test_node_deletion(self, lops_device_tree):
        """Test that lop can delete a node."""
        output_file = lops_device_tree.output_file

        with open(output_file) as f:
            content = f.read()
            # Node should be deleted (count should be 0)
            count = content.count("anode_to_delete")

        assert count == 0, f"Node 'anode_to_delete' should be deleted, found {count} occurrences"


class TestLopsNodeRename:
    """Test lops node rename operations.

    Reference: lopper_sanity.py:2194-2198
    """

    def test_node_rename(self, lops_device_tree):
        """Test that lop can rename a node."""
        output_file = lops_device_tree.output_file

        with open(output_file) as f:
            content = f.read()
            count = content.count("cpus_a72")

        assert count == 1, f"Node 'cpus_a72' should exist once, found {count} occurrences"


class TestLopsPropertyRemoval:
    """Test lops property removal operations.

    Reference: lopper_sanity.py:2200-2210
    """

    def test_property_remove(self, lops_device_tree):
        """Test that lop can remove a property."""
        output_file = lops_device_tree.output_file

        with open(output_file) as f:
            content = f.read()
            count = content.count("no-access")

        assert count == 0, f"Property 'no-access' should be removed, found {count} occurrences"

    def test_nested_node_deletion(self, lops_device_tree):
        """Test that lop can delete a nested node."""
        output_file = lops_device_tree.output_file

        with open(output_file) as f:
            content = f.read()
            count = content.count("nested-node")

        assert count == 0, f"Node 'nested-node' should be deleted, found {count} occurrences"


class TestLopsNodeAddition:
    """Test lops node addition operations.

    Reference: lopper_sanity.py:2212-2220
    """

    def test_node_add(self, lops_device_tree):
        """Test that lop can add a new node."""
        output_file = lops_device_tree.output_file

        with open(output_file) as f:
            content = f.read()
            zynqmp_rpu_count = content.count("zynqmp-rpu")
            cpu_count = content.count("__cpu__")

        assert zynqmp_rpu_count == 1, f"Node 'zynqmp-rpu' should exist once, found {zynqmp_rpu_count}"
        assert cpu_count == 1, f"Node '__cpu__' should exist once, found {cpu_count}"


class TestLopsPropertyModification:
    """Test lops property modification operations.

    Reference: lopper_sanity.py:2222-2232
    """

    def test_new_node_property_modify(self, lops_device_tree):
        """Test modifying a property on a newly added node."""
        output_file = lops_device_tree.output_file

        with open(output_file) as f:
            content = f.read()
            count = content.count("lopper-mboxes")

        assert count == 1, f"Property 'lopper-mboxes' should exist once, found {count}"

    def test_root_property_modify(self, lops_device_tree):
        """Test modifying a root node property."""
        output_file = lops_device_tree.output_file

        with open(output_file) as f:
            content = f.read()
            count = content.count('model = "this is a test"')

        assert count == 1, f"Modified model property should exist once, found {count}"


class TestLopsSelectiveOutput:
    """Test lops selective node output operations.

    Reference: lopper_sanity.py:2234-2244
    """

    def test_node_selective_output(self, lops_device_tree):
        """Test that lop can output specific nodes to a file."""
        output_file = "/tmp/openamp-test.dts"

        with open(output_file) as f:
            content = f.read()
            count = content.count("zynqmp-rpu")

        assert count == 1, f"Selective output should contain 'zynqmp-rpu' once, found {count}"

    def test_node_regex_output(self, lops_device_tree):
        """Test that lop can output nodes matching regex pattern."""
        output_file = "/tmp/linux-amba.dts"

        with open(output_file) as f:
            content = f.read()
            count = len(re.findall(r".*amba.*{", content))

        assert count == 2, f"Regex output should contain 2 amba nodes, found {count}"


class TestLopsPropertyAddition:
    """Test lops property addition operations.

    Reference: lopper_sanity.py:2246-2256
    """

    def test_property_add(self, lops_device_tree):
        """Test that lop can add a new property."""
        output_file = lops_device_tree.output_file

        with open(output_file) as f:
            content = f.read()
            count = content.count("pnode-id =")

        assert count == 1, f"Property 'pnode-id' should be added once, found {count}"

    def test_property_via_regex_add(self, lops_device_tree):
        """Test that lop can add properties via regex matching."""
        output_file = lops_device_tree.output_file

        with open(output_file) as f:
            content = f.read()
            count = content.count("lopper-id-regex")

        assert count == 2, f"Regex-added property should exist twice, found {count}"


class TestLopsSubtrees:
    """Test lops subtree operations.

    Reference: lopper_sanity.py:2258-2277
    """

    def test_subtree_write(self, lops_device_tree):
        """Test that subtree is written to file."""
        assert "openamp-test" in lops_device_tree.subtrees, \
            "Subtree 'openamp-test' should exist"

        sub_tree = lops_device_tree.subtrees["openamp-test"]
        sub_tree_output = Path("/tmp/openamp-test2.dts")
        sub_tree_file = sub_tree_output.resolve()

        assert sub_tree_file.exists(), f"Subtree file should exist: {sub_tree_file}"

    def test_subtree_property_modify(self, lops_device_tree):
        """Test modifying properties in a subtree."""
        sub_tree_file = "/tmp/openamp-test2.dts"

        with open(sub_tree_file) as f:
            content = f.read()
            count = content.count("#size-cells = <0x3>;")

        assert count == 1, f"Subtree property modification should exist, found {count}"

    def test_subtree_node_move(self, lops_device_tree):
        """Test moving nodes within a subtree."""
        sub_tree_file = "/tmp/openamp-test2.dts"

        with open(sub_tree_file) as f:
            content = f.read()
            # Check for specific indentation indicating node was moved
            count = content.count("                reserved-memory {")

        assert count == 1, f"Moved node should have specific indentation, found {count}"


class TestLopsListModification:
    """Test lops list property modification operations.

    Reference: lopper_sanity.py:2279-2302
    """

    def test_listval_modify(self, lops_device_tree):
        """Test modifying a list value property."""
        output_file = lops_device_tree.output_file

        with open(output_file) as f:
            content = f.read()
            count = content.count("listval = <0xf 0x5>")

        assert count == 1, f"Modified listval should exist, found {count}"

    def test_liststring_modify(self, lops_device_tree):
        """Test modifying a list string property."""
        output_file = lops_device_tree.output_file

        with open(output_file) as f:
            content = f.read()
            count = content.count('liststring = "four", "five"')

        assert count == 1, f"Modified liststring should exist, found {count}"

    def test_singlestring_modify(self, lops_device_tree):
        """Test modifying a single string property."""
        output_file = lops_device_tree.output_file

        with open(output_file) as f:
            content = f.read()
            count = content.count('singlestring = "newcpu"')

        assert count == 1, f"Modified singlestring should exist, found {count}"

    def test_singleval_modify(self, lops_device_tree):
        """Test modifying a single value property."""
        output_file = lops_device_tree.output_file

        with open(output_file) as f:
            content = f.read()
            count = content.count("singleval = <0x5>")

        assert count == 1, f"Modified singleval should exist, found {count}"
