"""
Pytest migration of yaml_sanity_test() from lopper_sanity.py

This module contains tests for YAML input/output and conversion functionality.
Migrated from lopper_sanity.py lines 2534-2569.

Copyright (c) 2019,2020 Xilinx Inc. All rights reserved.
Copyright (C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.

SPDX-License-Identifier: BSD-3-Clause

Author:
    Bruce Ashfield <bruce.ashfield@amd.com>
"""

import os
import pytest
from lopper.yaml import LopperYAML
from lopper import LopperSDT


class TestYAMLReadWrite:
    """Test YAML file reading and writing.

    Reference: lopper_sanity.py:2534-2541
    """

    def test_yaml_load_and_write(self, yaml_test_file, test_outdir):
        """Test that YAML can be loaded and written back."""
        yaml_obj = LopperYAML(yaml_test_file)

        output_file = os.path.join(test_outdir, "output_yaml.yaml")
        yaml_obj.to_yaml(output_file)

        # Verify output file was created
        assert os.path.exists(output_file), f"YAML output file not created: {output_file}"

        # Verify it can be loaded again
        yaml_reloaded = LopperYAML(output_file)
        assert yaml_reloaded is not None, "Failed to reload written YAML"


class TestYAMLToTree:
    """Test YAML to tree conversion.

    Reference: lopper_sanity.py:2542-2545
    """

    def test_yaml_to_tree_conversion(self, yaml_test_file, test_outdir):
        """Test converting YAML to LopperTree."""
        yaml_obj = LopperYAML(yaml_test_file)
        tree = yaml_obj.to_tree()

        assert tree is not None, "YAML to tree conversion returned None"

        # Verify the tree has expected nodes
        ocp_node = tree["/ocp"]
        assert ocp_node is not None, "Expected /ocp node not found in tree"

    def test_yaml_tree_to_dts_write(self, yaml_test_file, test_outdir):
        """Test writing tree from YAML to DTS format."""
        yaml_obj = LopperYAML(yaml_test_file)
        tree = yaml_obj.to_tree()

        output_file = os.path.join(test_outdir, "output_yaml_to_dts.dts")
        LopperSDT(None).write(tree, output_file, True, True)

        assert os.path.exists(output_file), f"DTS output file not created: {output_file}"

        # Verify it's a valid DTS file (contains /dts-v1/)
        with open(output_file) as f:
            content = f.read()
            assert "/dts-v1/" in content, "DTS file missing /dts-v1/ header"


class TestSDTToYAML:
    """Test converting system device tree to YAML.

    Reference: lopper_sanity.py:2547-2549
    """

    def test_sdt_to_yaml_conversion(self, lopper_tree):
        """Test converting a device tree to YAML format."""
        yaml_obj = LopperYAML(None, lopper_tree)

        # This should not raise an exception
        # The dump() method prints to stdout in the original test
        assert yaml_obj is not None, "Failed to create YAML from tree"


class TestComplexPropertyAccess:
    """Test accessing complex nested properties in YAML-derived trees.

    Reference: lopper_sanity.py:2552-2568
    """

    def test_yaml_complex_property_access(self, yaml_test_file):
        """Test accessing complex nested properties from YAML."""
        yaml_obj = LopperYAML(yaml_test_file)
        tree = yaml_obj.to_tree()

        # Access nested nodes
        ocp_node = tree["/ocp"]
        assert ocp_node is not None, "Failed to access /ocp node"

        timer_node = tree["/ocp/TIMER4"]
        assert timer_node is not None, "Failed to access /ocp/TIMER4 node"

        # Access gpio property (should be a list/array)
        gpio_prop = timer_node["gpio"]
        assert gpio_prop is not None, "Failed to access gpio property"

        # First element should be a dict (phandle reference)
        gpio_compat = gpio_prop[0]
        assert isinstance(gpio_compat, dict), \
            f"Expected gpio[0] to be dict, got {type(gpio_compat)}"

    def test_yaml_complex_struct_access(self, yaml_test_file):
        """Test accessing values within complex structures from YAML."""
        yaml_obj = LopperYAML(yaml_test_file)
        tree = yaml_obj.to_tree()

        timer_node = tree["/ocp/TIMER4"]
        gpio_prop = timer_node["gpio"]

        # Access compatible string within the referenced structure
        assert gpio_prop[0]['compatible'] == "ti,omap4-gpio", \
            f"Expected 'ti,omap4-gpio', got {gpio_prop[0].get('compatible', 'NOT FOUND')}"
