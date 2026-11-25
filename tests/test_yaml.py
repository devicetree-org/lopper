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


class TestYAMLTranslationCompatibility:
    """Test that %.yaml.lop and lop-xlate-yaml.dts produce identical output.

    This is a transitional test to verify backwards compatibility during
    the migration from lop-xlate-yaml.dts to %.yaml.lop. Can be removed
    once all external tools have migrated to the new approach.
    """

    def test_yaml_translation_output_identical(self, test_outdir):
        """Verify %.yaml.lop and lop-xlate-yaml.dts produce identical DTS output."""
        import subprocess
        import difflib

        # Use a simple domain YAML that exercises the translation
        yaml_file = "lopper/selftest/domains/domains-parent.yaml"
        sdt_file = "lopper/selftest/system-top.dts"

        # Skip if test files don't exist
        if not os.path.exists(yaml_file) or not os.path.exists(sdt_file):
            pytest.skip("Test input files not found")

        output_new = os.path.join(test_outdir, "output_new_yaml_lop.dts")
        output_legacy = os.path.join(test_outdir, "output_legacy_xlate.dts")

        # Run with new %.yaml.lop (auto-matched via -x yaml)
        cmd_new = [
            "python3", "-m", "lopper",
            "-f", "--permissive",
            "-x", "yaml",
            "-i", yaml_file,
            sdt_file,
            output_new
        ]

        # Run with legacy lop-xlate-yaml.dts (explicitly passed)
        cmd_legacy = [
            "python3", "-m", "lopper",
            "-f", "--permissive",
            "-i", "lopper/lops/lop-xlate-yaml.dts",
            "-i", yaml_file,
            sdt_file,
            output_legacy
        ]

        # Execute both commands
        result_new = subprocess.run(cmd_new, capture_output=True, text=True)
        if result_new.returncode != 0:
            pytest.fail(f"New approach failed: {result_new.stderr}")

        result_legacy = subprocess.run(cmd_legacy, capture_output=True, text=True)
        if result_legacy.returncode != 0:
            pytest.fail(f"Legacy approach failed: {result_legacy.stderr}")

        # Read and compare outputs
        with open(output_new) as f:
            content_new = f.read()
        with open(output_legacy) as f:
            content_legacy = f.read()

        # The outputs should be identical
        if content_new != content_legacy:
            # Generate diff for debugging
            diff = list(difflib.unified_diff(
                content_legacy.splitlines(keepends=True),
                content_new.splitlines(keepends=True),
                fromfile='legacy (lop-xlate-yaml.dts)',
                tofile='new (%.yaml.lop)',
                lineterm=''
            ))
            diff_str = ''.join(diff[:50])  # First 50 lines of diff
            pytest.fail(
                f"Output differs between %.yaml.lop and lop-xlate-yaml.dts:\n{diff_str}"
            )


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


class TestNodeLabels:
    """How a YAML node gets the label that dtc emits in front of it.

    A label is declared with the '<<label' directive. dtc writes one on the
    declaration line; YAML has no equivalent position, since the mapping key
    is the only thing that declares a node, so it is given inside the node.
    """

    def _tree(self, tmp_path, text):
        f = tmp_path / "t.yaml"
        f.write_text(text)
        return LopperYAML(str(f)).to_tree()

    def test_label_directive_names_the_node(self, tmp_path):
        tree = self._tree(tmp_path,
            "cma@20000000:\n"
            "  <<label: cma_pool\n"
            "  compatible: shared-dma-pool\n")

        assert tree["/cma@20000000"].label == "cma_pool"

    def test_the_directive_is_not_emitted_as_a_property(self, tmp_path):
        """It is a directive, so it names the node and leaves no trace."""
        tree = self._tree(tmp_path,
            "cma@20000000:\n"
            "  <<label: cma_pool\n"
            "  compatible: shared-dma-pool\n")

        assert "<<label" not in tree["/cma@20000000"].__props__

    def test_a_label_is_applied_even_when_nothing_references_it(self, tmp_path):
        """Declaring it is the whole trigger.

        The label has to reach the DTS whether or not anything in the YAML
        points at it -- a reference is one reason to want one, not the reason
        it exists.
        """
        tree = self._tree(tmp_path,
            "lonely@0:\n"
            "  <<label: nobody_points_here\n"
            "  compatible: x\n")

        assert tree["/lonely@0"].label == "nobody_points_here"

    def test_a_bare_label_property_is_left_alone(self, tmp_path):
        """The reported case: a flash partition's name must not become a label.

        'label' is a standard binding property -- fixed-partitions, leds, iio
        and others define it as a human readable name. Promoting it emitted
        'Image Selector: partition@0 {', which dtc refuses.
        """
        tree = self._tree(tmp_path,
            "partition@0:\n"
            '  label: "Image Selector"\n'
            "  compatible: x\n")

        node = tree["/partition@0"]
        assert node.propval("label") == "Image Selector"
        assert not node.label

    def test_a_valid_looking_label_property_is_also_left_alone(self, tmp_path):
        """Not a rule about spaces.

        'boot' would survive promotion and compile, which is why the old
        behaviour went unnoticed for so long. It is still the binding's
        property rather than a label, so it is still left alone.
        """
        tree = self._tree(tmp_path,
            "partition@0:\n"
            "  label: boot\n"
            "  compatible: x\n")

        assert not tree["/partition@0"].label

    def test_lopper_label_is_still_promoted(self, tmp_path):
        """lopper's own encoding of a source label, written by _label_translate
        when a dts is read and by assists that inject one deliberately. Being
        namespaced it means a label and nothing else.
        """
        tree = self._tree(tmp_path,
            "n@0:\n"
            "  lopper-label-1: from_dts\n"
            "  compatible: x\n")

        assert tree["/n@0"].label == "from_dts"

    @pytest.mark.parametrize("value,why", [
        ('"not an identifier"', "a space"),
        ("0bad",                "leading digit"),
        ("cma-pool",            "a dash"),
        ('""',                  "empty"),
        ("[a, b]",              "more than one name"),
    ])
    def test_an_unusable_label_is_refused(self, tmp_path, value, why):
        """A label the user asked for is validated rather than silently dropped.

        This checks a value that was explicitly declared as a label. It is not
        a rule deciding *whether* to make one from arbitrary data.
        """
        with pytest.raises(SystemExit):
            self._tree(tmp_path,
                "n@0:\n"
                f"  <<label: {value}\n"
                "  compatible: x\n")

    @pytest.mark.parametrize("key", ["<<label!linux", "<<label!append"])
    def test_sigils_on_the_directive_are_refused(self, tmp_path, key):
        """A label belongs to the node, so it is neither conditional nor merged."""
        with pytest.raises(SystemExit):
            self._tree(tmp_path,
                "n@0:\n"
                f"  {key}: cma_pool\n"
                "  compatible: x\n")
