"""
Tests for enhanced reserved-memory handling in Lopper.

This module tests:
1. Boolean property expansion (reusable, linux,cma-default, linux,dma-default)
2. Validation that reserved-memory regions fall within domain memory ranges
3. Explicit refcounting for reserved-memory nodes
4. End-to-end testing of reserved-memory addition and pruning
5. Semantic validation of reserved-memory consistency with domain memory

Copyright (C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.

SPDX-License-Identifier: BSD-3-Clause

Author:
    Bruce Ashfield <bruce.ashfield@amd.com>
"""

import os
import pytest
import tempfile
from lopper.tree import LopperTree, LopperNode, LopperProp
from lopper.yaml import LopperYAML
from lopper import LopperSDT


class TestReservedMemoryBooleanProperties:
    """Test boolean property expansion for reserved-memory nodes."""

    def test_boolean_properties_in_schema(self):
        """Test that reserved-memory boolean properties are in schema."""
        from lopper.schema import PROPERTY_NAME_HEURISTICS, PROPERTY_TYPE_HINTS
        from lopper import LopperFmt

        # Check PROPERTY_NAME_HEURISTICS['exact']
        exact = PROPERTY_NAME_HEURISTICS['exact']
        assert 'reusable' in exact, "reusable not in exact property names"
        assert 'linux,cma-default' in exact, "linux,cma-default not in exact property names"
        assert 'linux,dma-default' in exact, "linux,dma-default not in exact property names"

        assert exact['reusable'] == LopperFmt.EMPTY, "reusable should be EMPTY type"
        assert exact['linux,cma-default'] == LopperFmt.EMPTY, "linux,cma-default should be EMPTY type"
        assert exact['linux,dma-default'] == LopperFmt.EMPTY, "linux,dma-default should be EMPTY type"

        # Check PROPERTY_TYPE_HINTS['boolean_properties']
        boolean_props = PROPERTY_TYPE_HINTS['boolean_properties']
        assert 'reusable' in boolean_props, "reusable not in boolean_properties"
        assert 'linux,cma-default' in boolean_props, "linux,cma-default not in boolean_properties"
        assert 'linux,dma-default' in boolean_props, "linux,dma-default not in boolean_properties"

    def test_boolean_props_list_in_yaml_expansion(self):
        """Test that reserved_memory_expand handles all expected boolean properties.

        Verify the function handles the DT-spec boolean properties by checking
        the source code, since the constant is local to the function.
        """
        import inspect
        import lopper.assists.yaml_to_dts_expansion as yaml_exp

        # Get the source code of reserved_memory_expand
        source = inspect.getsource(yaml_exp.reserved_memory_expand)

        # Verify all expected boolean properties are handled
        expected_props = ['no-map', 'reusable', 'linux,cma-default', 'linux,dma-default']
        for prop in expected_props:
            assert f"'{prop}'" in source, \
                f"'{prop}' not found in reserved_memory_expand source"


class TestReservedMemoryYAMLExpansion:
    """Test YAML-to-DTS expansion for reserved-memory nodes."""

    def test_reusable_boolean_expansion(self, test_outdir):
        """Test that reusable: true is converted to empty property."""
        yaml_content = """
reserved-memory:
  "#address-cells": 2
  "#size-cells": 2
  ranges: true

  cma_reserved@10000000:
    label: cma_1
    compatible: shared-dma-pool
    reusable: true
    start: 0x10000000
    size: 0x1000000
"""
        yaml_file = os.path.join(test_outdir, "test_reusable.yaml")
        with open(yaml_file, 'w') as f:
            f.write(yaml_content)

        yaml_obj = LopperYAML(yaml_file)
        tree = yaml_obj.to_tree()

        # Find the reserved-memory child node
        resmem_node = tree["/reserved-memory/cma_reserved@10000000"]
        assert resmem_node is not None, "cma_reserved node not found"

        # The reusable property should exist
        reusable_prop = resmem_node.props("reusable")
        assert reusable_prop, "reusable property not found"

    def test_cma_default_boolean_expansion(self, test_outdir):
        """Test that linux,cma-default: true is handled correctly."""
        yaml_content = """
reserved-memory:
  "#address-cells": 2
  "#size-cells": 2
  ranges: true

  cma_default@20000000:
    label: cma_default
    compatible: shared-dma-pool
    linux,cma-default: true
    start: 0x20000000
    size: 0x2000000
"""
        yaml_file = os.path.join(test_outdir, "test_cma_default.yaml")
        with open(yaml_file, 'w') as f:
            f.write(yaml_content)

        yaml_obj = LopperYAML(yaml_file)
        tree = yaml_obj.to_tree()

        # Find the reserved-memory child node
        resmem_node = tree["/reserved-memory/cma_default@20000000"]
        assert resmem_node is not None, "cma_default node not found"

        # The linux,cma-default property should exist
        cma_default_prop = resmem_node.props("linux,cma-default")
        assert cma_default_prop, "linux,cma-default property not found"


class TestReservedMemoryValidation:
    """Test validation of reserved-memory regions within domain memory."""

    def test_validate_function_exists(self):
        """Test that validate_reserved_memory_in_memory_ranges function exists."""
        from lopper.assists.domain_access import validate_reserved_memory_in_memory_ranges
        assert callable(validate_reserved_memory_in_memory_ranges)

    def test_validation_skips_when_no_memory(self, test_outdir):
        """Test that validation is skipped when domain has no memory property."""
        from lopper.assists.domain_access import validate_reserved_memory_in_memory_ranges

        # Create a minimal tree with a domain that has no memory property
        tree = LopperTree()
        root = tree['/']

        domain_node = LopperNode(-1, "/domains/test_domain")
        tree.add(domain_node)

        # Create a mock SDT-like object
        class MockSDT:
            def __init__(self, tree):
                self.tree = tree

        sdt = MockSDT(tree)

        # Should not raise - just returns early
        validate_reserved_memory_in_memory_ranges(sdt, domain_node, verbose=0)

    def test_validation_skips_when_no_reserved_memory(self, test_outdir):
        """Test that validation is skipped when domain has no reserved-memory property."""
        from lopper.assists.domain_access import validate_reserved_memory_in_memory_ranges

        # Create a minimal tree with a domain that has memory but no reserved-memory
        tree = LopperTree()
        root = tree['/']
        root + LopperProp(name='#address-cells', value=2)
        root + LopperProp(name='#size-cells', value=2)

        domain_node = LopperNode(-1, "/domains/test_domain")
        domain_node + LopperProp(name='memory', value=[0, 0, 0, 0x80000000])
        tree.add(domain_node)

        # Create a mock SDT-like object
        class MockSDT:
            def __init__(self, tree):
                self.tree = tree

        sdt = MockSDT(tree)

        # Should not raise - just returns early
        validate_reserved_memory_in_memory_ranges(sdt, domain_node, verbose=0)


class TestReservedMemoryRefcounting:
    """Test explicit refcounting for reserved-memory nodes."""

    def test_refcount_code_exists(self):
        """Test that reserved-memory refcounting code is present in domain_access."""
        import inspect
        from lopper.assists import domain_access

        source = inspect.getsource(domain_access.core_domain_access)

        # Check for the refcounting code markers
        assert "reserved-memory node references" in source, \
            "Reserved-memory refcounting section not found in core_domain_access"
        assert "keep_all_domain_reserved_memory" in source, \
            "Reserved-memory conditional flag not found"


class TestPhandleRefTest:
    """Test phandle reference resolution for memory-region property.

    This tests the existing phandle-ref-test.yaml fixture which validates
    that memory-region phandles are correctly resolved.
    """

    def test_memory_region_phandle_resolution(self, test_outdir):
        """Test that memory-region with &label syntax resolves to phandles."""
        yaml_file = "lopper/selftest/domains/phandle-ref-test.yaml"

        if not os.path.exists(yaml_file):
            pytest.skip("phandle-ref-test.yaml not found")

        yaml_obj = LopperYAML(yaml_file)
        tree = yaml_obj.to_tree()

        # Check that reserved-memory nodes exist
        test_reserved = tree.lnodes("test_reserved")
        assert test_reserved, "test_reserved label not found"

        another_reserved = tree.lnodes("another_reserved")
        assert another_reserved, "another_reserved label not found"

        # Check that test-node exists and has memory-region property
        test_node = tree["/test-node"]
        assert test_node is not None, "test-node not found"

        mem_region = test_node.propval("memory-region")
        assert mem_region != [''], "memory-region property not found or empty"


class TestReservedMemoryBooleanIntegration:
    """Integration test for reserved-memory boolean property expansion."""

    def test_all_boolean_properties_from_yaml(self, test_outdir):
        """Test that all boolean properties are loaded from YAML test file."""
        yaml_file = "lopper/selftest/domains/reserved-memory-boolean-test.yaml"

        if not os.path.exists(yaml_file):
            pytest.skip("reserved-memory-boolean-test.yaml not found")

        yaml_obj = LopperYAML(yaml_file)
        tree = yaml_obj.to_tree()

        # Check that all reserved-memory nodes exist
        nomap_region = tree.lnodes("nomap_region")
        assert nomap_region, "nomap_region label not found"

        cma_pool = tree.lnodes("cma_pool")
        assert cma_pool, "cma_pool label not found"

        cma_default = tree.lnodes("cma_default_region")
        assert cma_default, "cma_default_region label not found"

        dma_default = tree.lnodes("dma_default_region")
        assert dma_default, "dma_default_region label not found"

        multi_flags = tree.lnodes("multi_flags_region")
        assert multi_flags, "multi_flags_region label not found"

        # Verify no-map property exists on nomap_region
        nomap_props = nomap_region[0].props("no-map")
        assert nomap_props, "no-map property not found on nomap_region"

        # Verify reusable property exists on cma_pool
        reusable_props = cma_pool[0].props("reusable")
        assert reusable_props, "reusable property not found on cma_pool"

        # Verify linux,cma-default property exists on cma_default_region
        cma_default_props = cma_default[0].props("linux,cma-default")
        assert cma_default_props, "linux,cma-default property not found on cma_default_region"

        # Verify linux,dma-default property exists on dma_default_region
        dma_default_props = dma_default[0].props("linux,dma-default")
        assert dma_default_props, "linux,dma-default property not found on dma_default_region"

        # Verify multiple properties exist on multi_flags_region
        multi_reusable = multi_flags[0].props("reusable")
        multi_cma = multi_flags[0].props("linux,cma-default")
        assert multi_reusable, "reusable property not found on multi_flags_region"
        assert multi_cma, "linux,cma-default property not found on multi_flags_region"

    def test_memory_region_phandle_with_boolean_props(self, test_outdir):
        """Test that memory-region phandles work with boolean property nodes."""
        yaml_file = "lopper/selftest/domains/reserved-memory-boolean-test.yaml"

        if not os.path.exists(yaml_file):
            pytest.skip("reserved-memory-boolean-test.yaml not found")

        yaml_obj = LopperYAML(yaml_file)
        tree = yaml_obj.to_tree()

        # Check that test-device exists and has memory-region property
        test_device = tree["/test-device"]
        assert test_device is not None, "test-device not found"

        mem_region = test_device.propval("memory-region")
        assert mem_region != [''], "memory-region property not found or empty"


class TestReservedMemoryEndToEnd:
    """End-to-end tests for reserved-memory processing pipeline.

    These tests verify the complete flow from YAML loading through
    domain_access processing, including:
    - Referenced reserved-memory nodes surviving filtering
    - Unreferenced reserved-memory nodes being pruned
    - Boolean property expansion during yaml_to_dts_expansion
    """

    def test_reserved_memory_nodes_loaded_from_yaml(self, test_outdir):
        """Test that reserved-memory nodes are correctly loaded from YAML."""
        yaml_file = "lopper/selftest/domains/reserved-memory-e2e-test.yaml"

        if not os.path.exists(yaml_file):
            pytest.skip("reserved-memory-e2e-test.yaml not found")

        yaml_obj = LopperYAML(yaml_file)
        tree = yaml_obj.to_tree()

        # All three reserved-memory nodes should exist after YAML loading
        referenced = tree.lnodes("referenced_region")
        assert referenced, "referenced_region label not found"

        another = tree.lnodes("another_referenced")
        assert another, "another_referenced label not found"

        unreferenced = tree.lnodes("unreferenced_region")
        assert unreferenced, "unreferenced_region label not found"

    def test_boolean_props_exist_after_yaml_load(self, test_outdir):
        """Test that boolean props from YAML exist after loading."""
        yaml_file = "lopper/selftest/domains/reserved-memory-e2e-test.yaml"

        if not os.path.exists(yaml_file):
            pytest.skip("reserved-memory-e2e-test.yaml not found")

        yaml_obj = LopperYAML(yaml_file)
        tree = yaml_obj.to_tree()

        # Check reusable property on referenced_region
        referenced = tree.lnodes("referenced_region")
        assert referenced, "referenced_region not found"

        # After YAML loading, boolean True may be converted to:
        # - Empty list [] for empty property
        # - 1 or True or [1] or [True] depending on YAML loader behavior
        reusable_props = referenced[0].props("reusable")
        assert reusable_props, "reusable property not found on referenced_region"

    def test_start_size_to_reg_conversion(self, test_outdir):
        """Test that start/size are converted to reg property."""
        yaml_file = "lopper/selftest/domains/reserved-memory-e2e-test.yaml"

        if not os.path.exists(yaml_file):
            pytest.skip("reserved-memory-e2e-test.yaml not found")

        yaml_obj = LopperYAML(yaml_file)
        tree = yaml_obj.to_tree()

        referenced = tree.lnodes("referenced_region")
        assert referenced, "referenced_region not found"

        # After YAML load, should have start/size (before expansion)
        # or reg (if expansion already happened)
        start_val = referenced[0].propval("start")
        reg_val = referenced[0].propval("reg")

        # Either start exists (pre-expansion) or reg exists (post-expansion)
        assert start_val != [''] or reg_val != [''], \
            "Neither start nor reg property found on reserved-memory node"


class TestReservedMemorySemanticValidation:
    """Test semantic validation of reserved-memory consistency.

    These tests verify that the validate_reserved_memory_in_memory_ranges()
    function correctly identifies reserved-memory regions that fall outside
    domain memory ranges.
    """

    def test_validation_catches_outside_region(self, test_outdir):
        """Test that validation raises error for reserved-memory outside domain memory."""
        from lopper.assists.domain_access import validate_reserved_memory_in_memory_ranges

        # Create a tree where reserved-memory is outside domain memory
        tree = LopperTree()
        root = tree['/']
        root + LopperProp(name='#address-cells', value=2)
        root + LopperProp(name='#size-cells', value=2)

        # Create reserved-memory parent
        resmem_parent = LopperNode(-1, "/reserved-memory")
        resmem_parent + LopperProp(name='#address-cells', value=2)
        resmem_parent + LopperProp(name='#size-cells', value=2)
        tree.add(resmem_parent)

        # Create reserved-memory node at 0x80000000 (2GB)
        resmem_node = LopperNode(-1, "/reserved-memory/outside@80000000")
        # reg: address cells=2, size cells=2: <0x0 0x80000000 0x0 0x10000000>
        resmem_node + LopperProp(name='reg', value=[0x0, 0x80000000, 0x0, 0x10000000])
        tree.add(resmem_node)

        # Sync tree to ensure phandles are registered
        tree.sync()

        # Now create phandle after sync
        phandle = resmem_node.phandle_or_create()

        # Create domain with memory only up to 0x40000000 (1GB)
        domain_node = LopperNode(-1, "/domains/test_domain")
        # memory: address cells=2, size cells=2: <0x0 0x0 0x0 0x40000000>
        domain_node + LopperProp(name='memory', value=[0x0, 0x0, 0x0, 0x40000000])
        # Reference the reserved-memory node by phandle
        domain_node + LopperProp(name='reserved-memory', value=[phandle])
        tree.add(domain_node)

        # Sync again to register domain node
        tree.sync()

        # Create mock SDT
        class MockSDT:
            def __init__(self, tree):
                self.tree = tree

        sdt = MockSDT(tree)

        # Validation should raise an error (via SystemExit from _error)
        with pytest.raises(SystemExit):
            validate_reserved_memory_in_memory_ranges(sdt, domain_node, verbose=0)

    def test_validation_passes_for_inside_region(self, test_outdir):
        """Test that validation passes for reserved-memory inside domain memory."""
        from lopper.assists.domain_access import validate_reserved_memory_in_memory_ranges

        # Create a tree where reserved-memory is inside domain memory
        tree = LopperTree()
        root = tree['/']
        root + LopperProp(name='#address-cells', value=2)
        root + LopperProp(name='#size-cells', value=2)

        # Create reserved-memory parent
        resmem_parent = LopperNode(-1, "/reserved-memory")
        resmem_parent + LopperProp(name='#address-cells', value=2)
        resmem_parent + LopperProp(name='#size-cells', value=2)
        tree.add(resmem_parent)

        # Create reserved-memory node at 0x10000000 (256MB) - inside domain memory
        resmem_node = LopperNode(-1, "/reserved-memory/inside@10000000")
        # reg: <0x0 0x10000000 0x0 0x1000000> (16MB at 256MB)
        resmem_node + LopperProp(name='reg', value=[0x0, 0x10000000, 0x0, 0x1000000])
        tree.add(resmem_node)

        # Sync tree to register nodes
        tree.sync()

        # Create phandle after sync
        phandle = resmem_node.phandle_or_create()

        # Create domain with memory up to 0x40000000 (1GB)
        domain_node = LopperNode(-1, "/domains/test_domain")
        # memory: <0x0 0x0 0x0 0x40000000>
        domain_node + LopperProp(name='memory', value=[0x0, 0x0, 0x0, 0x40000000])
        # Reference the reserved-memory node
        domain_node + LopperProp(name='reserved-memory', value=[phandle])
        tree.add(domain_node)

        # Sync again
        tree.sync()

        # Create mock SDT
        class MockSDT:
            def __init__(self, tree):
                self.tree = tree

        sdt = MockSDT(tree)

        # Validation should pass (no exception)
        validate_reserved_memory_in_memory_ranges(sdt, domain_node, verbose=0)

    def test_validation_with_multiple_memory_ranges(self, test_outdir):
        """Test validation works with multiple disjoint memory ranges."""
        from lopper.assists.domain_access import validate_reserved_memory_in_memory_ranges

        tree = LopperTree()
        root = tree['/']
        root + LopperProp(name='#address-cells', value=2)
        root + LopperProp(name='#size-cells', value=2)

        # Create reserved-memory parent
        resmem_parent = LopperNode(-1, "/reserved-memory")
        resmem_parent + LopperProp(name='#address-cells', value=2)
        resmem_parent + LopperProp(name='#size-cells', value=2)
        tree.add(resmem_parent)

        # Reserved-memory at 0x80000000 - in second memory range
        resmem_node = LopperNode(-1, "/reserved-memory/highaddr@80000000")
        resmem_node + LopperProp(name='reg', value=[0x0, 0x80000000, 0x0, 0x1000000])
        tree.add(resmem_node)

        # Sync tree
        tree.sync()

        # Create phandle after sync
        phandle = resmem_node.phandle_or_create()

        # Domain with TWO memory ranges:
        # Range 1: 0x0-0x40000000 (1GB)
        # Range 2: 0x80000000-0xC0000000 (1GB at 2GB)
        domain_node = LopperNode(-1, "/domains/test_domain")
        # Two ranges concatenated: <0x0 0x0 0x0 0x40000000  0x0 0x80000000 0x0 0x40000000>
        domain_node + LopperProp(name='memory', value=[
            0x0, 0x0, 0x0, 0x40000000,        # First range
            0x0, 0x80000000, 0x0, 0x40000000  # Second range
        ])
        domain_node + LopperProp(name='reserved-memory', value=[phandle])
        tree.add(domain_node)

        # Sync again
        tree.sync()

        class MockSDT:
            def __init__(self, tree):
                self.tree = tree

        sdt = MockSDT(tree)

        # Should pass - reserved-memory is in second memory range
        validate_reserved_memory_in_memory_ranges(sdt, domain_node, verbose=0)


class TestReservedMemoryFullPipeline:
    """Full end-to-end tests running the complete lopper pipeline.

    These tests use the actual lopper infrastructure to verify:
    - YAML loading and tree merge
    - YAML expansion (boolean props, start/size -> reg)
    - domain_access filtering (referenced nodes survive, unreferenced pruned)
    - phandle indexing in __pnodes__
    """

    def test_full_pipeline_reserved_memory_pruning(self, test_outdir):
        """Test that unreferenced reserved-memory nodes are pruned through full pipeline."""
        sdt_file = "lopper/selftest/reserved-memory-test-sdt.dts"
        yaml_file = "lopper/selftest/domains/reserved-memory-e2e-domain.yaml"

        if not os.path.exists(sdt_file) or not os.path.exists(yaml_file):
            pytest.skip("Test files not found")

        # Create lopper SDT
        device_tree = LopperSDT(sdt_file)
        device_tree.dryrun = False
        device_tree.verbose = 0
        device_tree.werror = False
        device_tree.output_file = os.path.join(test_outdir, "e2e-output.dts")
        device_tree.cleanup_flag = True
        device_tree.save_temps = False
        device_tree.enhanced = True
        device_tree.outdir = test_outdir

        input_files = [yaml_file]

        # Find auto lops (like %.yaml.lop) for the input YAML files
        auto_assists = device_tree.find_any_matching_assists(input_files)
        input_files_with_auto = input_files + auto_assists

        # Setup with input files
        device_tree.setup(device_tree.dts, input_files_with_auto, "", True, libfdt=True)
        device_tree.target = "/domains/APU_Linux"

        # Load domain_access assist
        device_tree.assists_setup(["lopper/assists/domain_access.py"])

        # Run domain_access on the target domain
        device_tree.assist_autorun_setup("lopper/assists/domain_access", ["-t", "/domains/APU_Linux"])

        # Run the lops
        device_tree.perform_lops()

        # Verify the results
        tree = device_tree.tree

        # Check /reserved-memory exists
        try:
            resmem = tree['/reserved-memory']
        except:
            pytest.fail("/reserved-memory node not found")

        # Check referenced nodes exist
        referenced_names = ['cma_pool@10000000', 'nomap_region@30000000', 'yaml_cma@60000000']
        for name in referenced_names:
            found = False
            for child in resmem.subnodes(children_only=True):
                if child.name == name:
                    found = True
                    # Verify phandle is indexed in __pnodes__
                    if child.phandle > 0:
                        pnode_lookup = tree.pnode(child.phandle)
                        assert pnode_lookup == child, \
                            f"{name} phandle {child.phandle} not found via pnode() lookup"
                    break
            assert found, f"Referenced node {name} not found (should survive filtering)"

        # Check unreferenced node was pruned
        for child in resmem.subnodes(children_only=True):
            assert 'unused@50000000' not in child.name, \
                f"Unreferenced node {child.name} should have been pruned"
            assert 'unreferenced' not in child.name, \
                f"Unreferenced node {child.name} should have been pruned"

        device_tree.cleanup()

    def test_full_pipeline_boolean_property_expansion(self, test_outdir):
        """Test that boolean properties are expanded through full pipeline."""
        sdt_file = "lopper/selftest/reserved-memory-test-sdt.dts"
        yaml_file = "lopper/selftest/domains/reserved-memory-e2e-domain.yaml"

        if not os.path.exists(sdt_file) or not os.path.exists(yaml_file):
            pytest.skip("Test files not found")

        device_tree = LopperSDT(sdt_file)
        device_tree.dryrun = False
        device_tree.verbose = 0
        device_tree.werror = False
        device_tree.output_file = os.path.join(test_outdir, "e2e-boolean-output.dts")
        device_tree.cleanup_flag = True
        device_tree.save_temps = False
        device_tree.enhanced = True
        device_tree.outdir = test_outdir

        input_files = [yaml_file]
        auto_assists = device_tree.find_any_matching_assists(input_files)
        input_files_with_auto = input_files + auto_assists

        device_tree.setup(device_tree.dts, input_files_with_auto, "", True, libfdt=True)
        device_tree.target = "/domains/APU_Linux"
        device_tree.assists_setup(["lopper/assists/domain_access.py"])
        device_tree.assist_autorun_setup("lopper/assists/domain_access", ["-t", "/domains/APU_Linux"])
        device_tree.perform_lops()

        tree = device_tree.tree

        # Find yaml_cma node
        yaml_cma = None
        try:
            resmem = tree['/reserved-memory']
            for child in resmem.subnodes(children_only=True):
                if 'yaml_cma' in child.name:
                    yaml_cma = child
                    break
        except:
            pytest.fail("/reserved-memory not found")

        assert yaml_cma is not None, "yaml_cma node not found"

        # Check reusable property exists
        reusable = yaml_cma.props('reusable')
        assert reusable, "reusable property not found on yaml_cma"

        # Check linux,cma-default property exists
        cma_default = yaml_cma.props('linux,cma-default')
        assert cma_default, "linux,cma-default property not found on yaml_cma"

        # Check reg property exists (start/size converted)
        reg = yaml_cma.propval('reg')
        assert reg and reg != [''], "reg property not found on yaml_cma"

        device_tree.cleanup()

    def test_full_pipeline_pnode_index_consistency(self, test_outdir):
        """Test that pnode() lookups work correctly after phandle_or_create()."""
        sdt_file = "lopper/selftest/reserved-memory-test-sdt.dts"
        yaml_file = "lopper/selftest/domains/reserved-memory-e2e-domain.yaml"

        if not os.path.exists(sdt_file) or not os.path.exists(yaml_file):
            pytest.skip("Test files not found")

        device_tree = LopperSDT(sdt_file)
        device_tree.dryrun = False
        device_tree.verbose = 0
        device_tree.werror = False
        device_tree.output_file = os.path.join(test_outdir, "e2e-pnode-output.dts")
        device_tree.cleanup_flag = True
        device_tree.save_temps = False
        device_tree.enhanced = True
        device_tree.outdir = test_outdir

        input_files = [yaml_file]
        auto_assists = device_tree.find_any_matching_assists(input_files)
        input_files_with_auto = input_files + auto_assists

        device_tree.setup(device_tree.dts, input_files_with_auto, "", True, libfdt=True)
        device_tree.target = "/domains/APU_Linux"
        device_tree.assists_setup(["lopper/assists/domain_access.py"])
        device_tree.assist_autorun_setup("lopper/assists/domain_access", ["-t", "/domains/APU_Linux"])
        device_tree.perform_lops()

        tree = device_tree.tree

        # Check domain's reserved-memory property contains valid phandles
        try:
            domain = tree['/domains/APU_Linux']
            resmem_prop = domain.propval('reserved-memory')
        except:
            pytest.skip("Domain node not found after processing")

        if resmem_prop and resmem_prop != ['']:
            for ph in resmem_prop:
                if isinstance(ph, int) and ph > 0:
                    node = tree.pnode(ph)
                    assert node is not None, \
                        f"pnode({ph}) returned None - __pnodes__ not properly updated"
                    assert node.phandle == ph, \
                        f"pnode({ph}) returned node with different phandle {node.phandle}"

        device_tree.cleanup()
