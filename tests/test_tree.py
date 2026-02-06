"""
Tests for lopper tree walking and manipulation.

Complete migration of tree_sanity_test() from lopper_sanity.py (lines 1260-1985).
"""

import re
import tempfile
import filecmp
from pathlib import Path

from lopper import Lopper, LopperSDT
from lopper.tree import LopperTree, LopperTreePrinter, LopperNode, LopperProp


class TestBasicTreeWalking:
    """Tests for basic tree walking functionality (test1 from lopper_sanity.py)."""

    def test_tree_walk_no_exceptions(self, lopper_tree):
        """
        Test that tree walking doesn't raise exceptions.

        Walk through all nodes and properties without errors.
        Corresponds to lopper_sanity.py:1271-1281.
        """
        node_count = 0

        for node in lopper_tree:
            node_count += 1
            # Access node properties - should not raise
            _ = node.name
            _ = node.number
            _ = node.phandle
            _ = node.parent
            _ = node.child_nodes
            _ = node.depth

            # Walk properties - should not raise
            for prop in node:
                _ = prop.name
                _ = prop.value
                _ = node[prop.name]

        # Should have walked some nodes
        assert node_count > 0, "Tree walk found no nodes"

    def test_tree_walk_node_count(self, lopper_tree):
        """
        Test that tree walking produces the expected number of nodes.

        Corresponds to lopper_sanity.py:1283-1295.
        """
        node_count = 0

        with tempfile.NamedTemporaryFile(mode='w+', delete=False) as fpw:
            for node in lopper_tree:
                fpw.write(f"node: {node.name}:{node.number} [{hex(node.phandle)}] "
                         f"parent: {node.parent} children: {node.child_nodes} "
                         f"depth: {node.depth}\n")

                for prop in node:
                    fpw.write(f"    property: {prop.name} {prop.value}\n")
                    fpw.write(f"    raw: {node[prop.name]}\n")

            fpw.seek(0)
            content = fpw.read()
            node_count = len([line for line in content.split('\n') if 'node:' in line])

        # The test device tree from lopper_sanity.py has 22 nodes
        assert node_count == 22, f"Expected 22 nodes, got {node_count}"


class TestTreePrint:
    """Tests for tree printing functionality (test2 from lopper_sanity.py)."""

    def test_tree_print_with_memreserve(self, compiled_fdt):
        """
        Test that tree.print() maintains /memreserve/ entries.

        Corresponds to lopper_sanity.py:1301-1332.
        """
        memres_tree = LopperTree()
        dct = Lopper.export(compiled_fdt)
        memres_tree.load(dct)

        with tempfile.NamedTemporaryFile(mode='w+', delete=False) as fpw:
            memres_tree.print(fpw)
            fpw.flush()

            with open(fpw.name) as fp:
                content = fp.read()
                memres_regex = re.compile(r'\/memreserve\/(.*)?;')
                memres_found = memres_regex.search(content)

        assert memres_found is not None, "/memreserve/ was not maintained through processing"

    def test_tree_print_node_count(self, lopper_tree):
        """
        Test that tree.print() outputs correct number of nodes.

        Corresponds to lopper_sanity.py:1334-1349.
        """
        with tempfile.NamedTemporaryFile(mode='w+', delete=False) as fpw:
            lopper_tree.print(fpw)
            fpw.flush()

            with open(fpw.name) as fp:
                content = fp.read()
                node_count = len([line for line in content.split('\n') if '{' in line])

        # Expected 21 nodes (one less than walk count due to root)
        assert node_count == 21, f"Expected 21 nodes in print output, got {node_count}"


class TestNodeAccess:
    """Tests for node access methods (test3 from lopper_sanity.py)."""

    def test_node_access_by_path(self, lopper_tree):
        """
        Test accessing nodes by their path.

        Corresponds to lopper_sanity.py:1353-1359.
        """
        node = lopper_tree['/amba']
        assert node is not None, "Failed to access /amba by path"

    def test_node_access_by_number(self, lopper_tree):
        """
        Test accessing nodes by their index number.

        Corresponds to lopper_sanity.py:1361-1367.
        """
        amba_node = lopper_tree['/amba']
        node_by_number = lopper_tree.nodes(amba_node.number)
        assert node_by_number is not None, f"Failed to access node by number {amba_node.number}"

    def test_node_reassignment(self, lopper_tree):
        """
        Test reassigning a node by path.

        Corresponds to lopper_sanity.py:1369-1374.
        """
        amba_node = lopper_tree['/amba']
        lopper_tree['/amba'] = amba_node  # Should not raise

    def test_property_access_by_name(self, lopper_tree):
        """
        Test accessing node properties by name.

        Corresponds to lopper_sanity.py:1376-1382.
        """
        amba_node = lopper_tree['/amba']
        compatible = amba_node['compatible']
        assert compatible is not None, "Failed to access 'compatible' property"
        assert compatible.name == "compatible"

    def test_node_access_via_phandle(self, lopper_tree):
        """
        Test accessing nodes via phandle.

        Corresponds to lopper_sanity.py:1384-1391.
        """
        amba_node = lopper_tree['/amba']
        node_by_phandle = lopper_tree.pnode(amba_node.phandle)
        assert node_by_phandle is not None, f"Failed to access node via phandle {hex(amba_node.phandle)}"


class TestCustomNodeLists:
    """Tests for custom node iteration (lopper_sanity.py:1395-1471)."""

    def test_custom_node_print(self, compiled_fdt):
        """
        Test custom node printing with restricted starting node.

        Corresponds to lopper_sanity.py:1395-1417.
        """
        with tempfile.NamedTemporaryFile(mode='w+', delete=False) as fpw:
            printer = LopperTreePrinter(True, fpw.name)
            printer.load(Lopper.export(compiled_fdt))
            printer.reset(fpw.name)

            printer.__new_iteration__ = True
            printer.__current_node__ = "/amba_apu"
            printer.exec()

            fpw.flush()
            with open(fpw.name) as fp:
                count = sum(1 for line in fp if '{' in line)

        assert count == 6, f"Expected 6 nodes from /amba_apu, got {count}"

    def test_full_walk_after_restricted(self, compiled_fdt):
        """
        Test full tree walk after a restricted walk.

        Corresponds to lopper_sanity.py:1419-1436.
        """
        with tempfile.NamedTemporaryFile(mode='w+', delete=False) as fpw:
            printer = LopperTreePrinter(True, fpw.name)
            printer.load(Lopper.export(compiled_fdt))
            printer.reset(fpw.name)

            # Do restricted walk first
            printer.__new_iteration__ = True
            printer.__current_node__ = "/amba_apu"
            printer.exec()

            # Reset for full walk
            printer.__new_iteration__ = False
            printer.__current_node__ = "/"

            # Now do full walk
            with open(fpw.name, 'w+') as fw:
                for p in printer:
                    fw.write(f"node: {p}\n")
                fw.flush()

            with open(fpw.name) as fp:
                count = sum(1 for line in fp if 'node:' in line)

        assert count == 22, f"Expected 22 nodes in full walk, got {count}"

    def test_subtree_walk(self, compiled_fdt):
        """
        Test walking only a subtree.

        Corresponds to lopper_sanity.py:1439-1452.
        """
        printer = LopperTreePrinter(True, "/tmp/test.dts")
        printer.load(Lopper.export(compiled_fdt))

        printer.__current_node__ = "/amba"
        count = sum(1 for _ in printer)

        assert count == 3, f"Expected 3 nodes in /amba subtree, got {count}"

    def test_start_node_to_end_walk(self, compiled_fdt):
        """
        Test walking from a starting node to end of tree.

        Corresponds to lopper_sanity.py:1454-1471.
        """
        printer = LopperTreePrinter(True, "/tmp/test.dts")
        printer.load(Lopper.export(compiled_fdt))
        printer.reset()

        printer.__start_node__ = "/amba"
        count = sum(1 for _ in printer)

        assert count == 16, f"Expected 16 nodes from /amba to end, got {count}"


class TestSubnodeCalls:
    """Tests for subnode access methods (lopper_sanity.py:1476-1529)."""

    def test_subnodes_method(self, compiled_fdt):
        """
        Test getting subnodes of a node.

        Corresponds to lopper_sanity.py:1476-1499.
        """
        printer = LopperTreePrinter(True, "/tmp/test.dts")
        printer.load(Lopper.export(compiled_fdt))

        kiddies = printer.subnodes(printer.__nodes__['/amba'])
        subnode_count = sum(1 for _ in kiddies)

        kiddies2 = printer['/amba'].subnodes()
        subnode_count2 = sum(1 for _ in kiddies2)

        assert subnode_count == subnode_count2, \
            f"Subnode counts don't match: {subnode_count} vs {subnode_count2}"

    def test_full_tree_subnodes(self, compiled_fdt):
        """
        Test getting all subnodes from root.

        Corresponds to lopper_sanity.py:1501-1513.
        """
        printer = LopperTreePrinter(True, "/tmp/test.dts")
        printer.load(Lopper.export(compiled_fdt))

        kiddies = printer.subnodes(printer['/'])
        subnodecount = sum(1 for _ in kiddies)

        assert subnodecount == 22, f"Expected 22 subnodes from root, got {subnodecount}"

    def test_regex_subnodes(self, compiled_fdt):
        """
        Test getting subnodes matching a regex.

        Corresponds to lopper_sanity.py:1515-1527.
        """
        printer = LopperTreePrinter(True, "/tmp/test.dts")
        printer.load(Lopper.export(compiled_fdt))

        kiddies = printer.subnodes(printer['/'], ".*amba.*")
        subnodecount = sum(1 for _ in kiddies)

        assert subnodecount == 9, f"Expected 9 nodes matching .*amba.*, got {subnodecount}"


class TestResolveReferences:
    """Tests for reference resolution (lopper_sanity.py:1531-1558)."""

    def test_resolve_all_refs(self, compiled_fdt):
        """
        Test resolving all references from a node.

        Corresponds to lopper_sanity.py:1531-1557.
        """
        printer = LopperTreePrinter(True, "/tmp/test.dts")
        printer.load(Lopper.export(compiled_fdt))

        all_refs = printer['/amba/interrupt-multiplex'].resolve_all_refs()
        refcount = len(all_refs)

        root_found = any(a.abs_path == "/" for a in all_refs)
        amba_found = any(a.abs_path == "/amba" for a in all_refs)

        assert refcount == 6, f"Expected 6 references, got {refcount}"
        assert root_found and amba_found, "Parent nodes not found in references"


class TestNodeStringRepresentation:
    """Tests for node __str__ method (lopper_sanity.py:1560-1597)."""

    def test_str_method(self, compiled_fdt):
        """Test node string representation."""
        printer = LopperTreePrinter(True, "/tmp/test.dts")
        printer.load(Lopper.export(compiled_fdt))
        printer.__dbg__ = 0

        assert "/amba" == str(printer.__nodes__['/amba']), "__str__ failed"

    def test_str_raw(self, compiled_fdt):
        """Test raw node string representation."""
        printer = LopperTreePrinter(True, "/tmp/test.dts")
        printer.load(Lopper.export(compiled_fdt))
        printer.__dbg__ = 3

        assert re.search(r"<lopper.tree.LopperNode.*", str(printer.__nodes__['/amba'])), \
            "__str__ raw failed"

    def test_instance_type(self, compiled_fdt):
        """Test node instance type."""
        printer = LopperTreePrinter(True, "/tmp/test.dts")
        printer.load(Lopper.export(compiled_fdt))

        assert isinstance(printer.__nodes__['/amba'], LopperNode), "Wrong instance type"

    def test_node_equality(self, compiled_fdt):
        """Test node equality comparison."""
        printer = LopperTreePrinter(True, "/tmp/test.dts")
        printer.load(Lopper.export(compiled_fdt))

        assert printer.__nodes__['/amba'] != printer.__nodes__['/amba_apu'], \
            "Different nodes should not be equal"


class TestNodeRegexFind:
    """Tests for regex-based node finding (lopper_sanity.py:1599-1648)."""

    def test_regex_node_match_children(self, compiled_fdt):
        """Test finding nodes matching regex (children only)."""
        printer = LopperTreePrinter(True, "/tmp/test.dts")
        printer.load(Lopper.export(compiled_fdt))

        matches = printer.nodes("/amba/.*")
        count = sum(1 for _ in matches)
        multiplex = any(m.name == "interrupt-multiplex" for m in matches)

        assert count == 2 and multiplex, f"Expected 2 matches with multiplex, got {count}"

    def test_regex_node_match_tree(self, compiled_fdt):
        """Test finding nodes matching regex (full tree)."""
        printer = LopperTreePrinter(True, "/tmp/test.dts")
        printer.load(Lopper.export(compiled_fdt))

        matches = printer.nodes("/amba.*")
        count = sum(1 for _ in matches)
        multiplex = any(m.name == "interrupt-multiplex" for m in matches)

        assert count == 9 and multiplex, f"Expected 9 matches with multiplex, got {count}"

    def test_exact_node_match(self, compiled_fdt):
        """Test exact node path matching."""
        printer = LopperTreePrinter(True, "/tmp/test.dts")
        printer.load(Lopper.export(compiled_fdt))

        matches = printer.nodes("/amba")
        amba = matches[0] if matches else None

        assert len(matches) == 1 and amba.abs_path == "/amba", "Exact match failed"


class TestPropertyRegexFind:
    """Tests for property regex matching (lopper_sanity.py:1650-1686)."""

    def test_prop_regex_match(self, compiled_fdt):
        """Test finding properties by regex."""
        printer = LopperTreePrinter(True, "/tmp/test.dts")
        printer.load(Lopper.export(compiled_fdt))

        amba = printer.nodes("/amba")[0]
        props = amba.props('compat.*')
        prop = props[0]

        assert isinstance(prop, LopperProp), "Wrong property type"
        assert prop.value[0] == "simple-bus", f"Wrong value: {prop.value[0]}"
        assert str(prop) == "compatible = \"simple-bus\";", "Wrong string representation"

    def test_prop_value_reassign(self, compiled_fdt):
        """Test property value reassignment."""
        printer = LopperTreePrinter(True, "/tmp/test.dts")
        printer.load(Lopper.export(compiled_fdt))

        amba = printer.nodes("/amba")[0]
        prop = amba.props('compat.*')[0]

        prop.value = "testing 1.2.3"

        assert prop.value[0] == "testing 1.2.3", "Property reassignment failed"
        assert str(prop) == "compatible = \"testing 1.2.3\";", "Property resolve failed"


class TestTreeManipulation:
    """Tests for tree manipulation (lopper_sanity.py:1688-1746)."""

    def test_node_creation_and_addition(self, compiled_fdt):
        """Test creating and adding nodes to tree."""
        printer = LopperTreePrinter(True, "/tmp/test.dts")
        printer.load(Lopper.export(compiled_fdt))

        new_node = LopperNode(-1, "/amba/bruce")
        new_node.ref = 2
        new_node.ref = 1

        assert new_node.ref == 3, f"Node ref count wrong: {new_node.ref} vs 3"

        new_property = LopperProp("foobar", -1, new_node, ["testingfoo"])
        new_node + new_property
        printer + new_node

        assert new_node.abs_path == "/amba/bruce" and new_node.ref == 3, \
            "Node addition failed"

    def test_node_ref_persistence(self, compiled_fdt):
        """Test node reference counting persistence."""
        printer = LopperTreePrinter(True, "/tmp/test.dts")
        printer.load(Lopper.export(compiled_fdt))

        new_node = LopperNode(-1, "/amba/bruce")
        new_node.ref = 2
        new_node.ref = 1
        new_property = LopperProp("foobar", -1, new_node, ["testingfoo"])
        new_node + new_property
        printer + new_node

        refd = printer.refd()
        assert len(refd) == 1 and refd[0].abs_path == "/amba/bruce", \
            "Referenced nodes not persisted"

        printer.ref(0)
        refd = printer.refd()
        assert len(refd) == 0, "Referenced nodes not cleared"


class TestTreeResolveAndExport:
    """Tests for tree resolve and export (lopper_sanity.py:1748-1843)."""

    def test_tree_reresolve(self, compiled_fdt):
        """Test tree re-resolve operation."""
        with tempfile.NamedTemporaryFile(mode='w+', delete=False) as fpw:
            printer = LopperTreePrinter(True, fpw.name)
            printer.load(Lopper.export(compiled_fdt))

            printer.__dbg__ = 0
            printer.__start_node__ = '/'
            printer.reset(fpw.name)
            printer.resolve()
            printer.print(open(fpw.name, "w"))
            # If we get here, test passed

    def test_tree_export_and_reload(self, compiled_fdt):
        """Test exporting and reloading tree."""
        with tempfile.NamedTemporaryFile(mode='w+', delete=False) as fpw:
            with tempfile.NamedTemporaryFile(mode='w+', delete=False) as fpw2:
                printer = LopperTreePrinter(True, fpw.name)
                printer.load(Lopper.export(compiled_fdt))
                printer.__dbg__ = 0
                printer.__start_node__ = '/'
                printer.reset(fpw.name)
                printer.resolve()
                printer.print(open(fpw.name, "w"))

                print2 = LopperTree()
                print2.load(printer.export())
                print2.resolve()
                print2.print(open(fpw2.name, "w"))

                assert filecmp.cmp(fpw.name, fpw2.name), \
                    "Exported and reloaded trees don't match"

    def test_node_persistence_state(self, compiled_fdt):
        """Test node state persistence."""
        printer = LopperTreePrinter(True, "/tmp/test.dts")
        printer.load(Lopper.export(compiled_fdt))

        new_node = LopperNode(-1, "/amba/bruce")
        new_node.ref = 2
        new_property = LopperProp("foobar", -1, new_node, ["testingfoo"])
        new_node + new_property
        printer + new_node
        printer.resolve()

        assert new_node.__nstate__ == "resolved", \
            f"Node state wrong: {new_node.__nstate__}"

    def test_property_sync_and_write(self, compiled_fdt, test_outdir):
        """Test property sync and write operations."""
        printer = LopperTreePrinter(True, "/tmp/test.dts")
        printer.load(Lopper.export(compiled_fdt))

        new_node = LopperNode(-1, "/amba/bruce")
        new_node.ref = 2
        new_property = LopperProp("foobar", -1, new_node, ["testingfoo"])
        new_node + new_property
        printer + new_node
        printer.resolve()

        new_property2 = LopperProp("number2", -1, new_node, ["i am 2"])
        new_node + new_property2
        printer.sync()

        output1 = f"{test_outdir}/tester-output.dts"
        output2 = f"{test_outdir}/tester-output2.dts"

        LopperSDT(None).write(printer, output1, True, True)

        new_node - new_property2
        printer.sync()
        LopperSDT(None).write(printer, output2, True, True)

        assert not filecmp.cmp(output1, output2, False), \
            "Files should differ after property removal"


class TestNodeDeepCopy:
    """Tests for node deep copy (lopper_sanity.py:1845-1897)."""

    def test_node_deep_copy(self, compiled_fdt, test_outdir):
        """Test deep copying nodes."""
        printer = LopperTreePrinter(True, "/tmp/test.dts")
        printer.load(Lopper.export(compiled_fdt))

        new_node = LopperNode(-1, "/amba/bruce")
        new_property = LopperProp("foobar", -1, new_node, ["testingfoo"])
        new_node + new_property
        printer + new_node

        tree2 = LopperTree()
        tree2.load(Lopper.export(compiled_fdt))
        new_node2 = new_node()  # Invokes deep copy

        assert new_node.abs_path == new_node2.abs_path, "Copied nodes should have same path"
        assert new_node.__props__ != new_node2.__props__, "Copied properties should be different"

        tree2 + new_node2
        LopperSDT(None).write(tree2, f"{test_outdir}/tester-output-tree2.dts", True, True)

    def test_node_remove(self, compiled_fdt, test_outdir):
        """Test removing nodes from tree."""
        printer = LopperTreePrinter(True, "/tmp/test.dts")
        printer.load(Lopper.export(compiled_fdt))

        new_node = LopperNode(-1, "/amba/bruce")
        new_property = LopperProp("foobar", -1, new_node, ["testingfoo"])
        new_node + new_property
        printer + new_node

        tree2 = LopperTree()
        tree2.load(Lopper.export(compiled_fdt))
        new_node2 = new_node()
        tree2 + new_node2

        output1 = f"{test_outdir}/tester-output-node-removed.dts"
        output2 = f"{test_outdir}/tester-output-tree2.dts"

        printer = printer - new_node
        LopperSDT(None).write(printer, output1, True, True)
        LopperSDT(None).write(tree2, output2, True, True)

        assert not filecmp.cmp(output1, output2, False), \
            "Files should differ after node removal"


class TestPropertyManipulation:
    """Tests for property manipulation (lopper_sanity.py:1898-1940)."""

    def test_property_add_to_existing_node(self, compiled_fdt):
        """Test adding property to existing node."""
        with tempfile.NamedTemporaryFile(mode='w+', delete=False) as fpw:
            printer = LopperTreePrinter(True, fpw.name)
            printer.load(Lopper.export(compiled_fdt))

            prop = LopperProp("newproperty_existingnode")
            existing_node = printer['/amba']
            existing_node + prop

            printer.print(open(fpw.name, "w"))

            with open(fpw.name) as fp:
                count = sum(1 for line in fp if 'newproperty_existingnode' in line)

            assert count == 1, f"Expected 1 occurrence of newproperty_existingnode, got {count}"

    def test_new_tree_creation(self, compiled_fdt):
        """Test creating a new tree with nodes and properties."""
        with tempfile.NamedTemporaryFile(mode='w+', delete=False) as fpw:
            printer = LopperTreePrinter(True, "/tmp/test.dts")
            printer.load(Lopper.export(compiled_fdt))

            new_node = LopperNode(-1, "/amba/bruce")
            new_tree = LopperTree()
            new_tree_new_node = new_node()

            prop = LopperProp("newproperty_existingnode")
            new_tree + new_tree_new_node
            new_tree_new_node + prop
            new_tree.print(open(fpw.name, "w"))

            with open(fpw.name) as fp:
                content = fp.read()
                amba_count = sum(1 for line in content.split('\n') if 'amba' in line)
                bruce_count = sum(1 for line in content.split('\n') if 'bruce' in line)
                prop_count = sum(1 for line in content.split('\n') if 'newproperty_existingnode' in line)

            assert amba_count >= 1, "amba not found in new tree"
            assert bruce_count >= 1, "bruce not found in new tree"
            assert prop_count >= 1, "newproperty_existingnode not found in new tree"


class TestPropertyAccess:
    """Tests for property access methods (lopper_sanity.py:1942-1973)."""

    def test_simple_property_index_access(self, compiled_fdt):
        """Test accessing property values by index."""
        prop_tree = LopperTree()
        prop_tree.load(Lopper.export(compiled_fdt))

        cpu_node = prop_tree["/cpus/cpu@0"]
        cpu_prop = cpu_node["compatible"]
        compat1 = cpu_prop[0]
        compat2 = cpu_prop[1]

        assert compat1 == "arm,cortex-a72" and compat2 == "arm,armv8", \
            f"Property index access failed: {compat1}, {compat2}"

    def test_property_dict_access(self, compiled_fdt):
        """Test converting property to dict."""
        prop_tree = LopperTree()
        prop_tree.load(Lopper.export(compiled_fdt))

        cpu_node = prop_tree["/cpus/cpu@0"]
        cpu_prop = cpu_node["compatible"]
        prop_dict = dict(cpu_prop)

        assert prop_dict['value'] == ['arm,cortex-a72', 'arm,armv8'], \
            f"Property dict access failed: {prop_dict['value']}"

    def test_property_length(self, compiled_fdt):
        """Test getting property length."""
        prop_tree = LopperTree()
        prop_tree.load(Lopper.export(compiled_fdt))

        cpu_node = prop_tree["/cpus/cpu@0"]
        cpu_prop = cpu_node["compatible"]

        assert len(cpu_prop) == 2, f"Property length wrong: {len(cpu_prop)}"

    def test_propval_dict_access(self, compiled_fdt):
        """Test propval dict access."""
        prop_tree = LopperTree()
        prop_tree.load(Lopper.export(compiled_fdt))

        cpu_node = prop_tree["/cpus/cpu@0"]
        pp = cpu_node.propval("compatible", dict)

        assert pp['value'] == ['arm,cortex-a72', 'arm,armv8'], \
            f"propval dict access failed: {pp['value']}"


class TestAliases:
    """Tests for alias lookups (lopper_sanity.py:1974-1984)."""

    def test_alias_lookup_valid(self, compiled_fdt):
        """Test looking up valid alias."""
        printer = LopperTreePrinter(True, "/tmp/test.dts")
        printer.load(Lopper.export(compiled_fdt))

        alias = printer.alias_node("imux")
        assert alias is not None, "Failed to find valid alias 'imux'"

    def test_alias_lookup_invalid(self, compiled_fdt):
        """Test looking up invalid alias."""
        printer = LopperTreePrinter(True, "/tmp/test.dts")
        printer.load(Lopper.export(compiled_fdt))

        alias = printer.alias_node("serial0-fake")
        assert alias is None, "Should not find invalid alias 'serial0-fake'"
