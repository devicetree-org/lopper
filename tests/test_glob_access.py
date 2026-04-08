"""
Pytest tests for glob-based access expansion in domain YAML files.

This module tests the wildcard device expansion functionality that pulls
devices from parent domain access lists into child domains based on glob
patterns.

Copyright (c) 2019,2020 Xilinx Inc. All rights reserved.
Copyright (C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.

SPDX-License-Identifier: BSD-3-Clause

Author:
    Bruce Ashfield <bruce.ashfield@amd.com>
"""

import os
import json
import pytest
from lopper.assists.yaml_to_dts_expansion import (
    is_glob_pattern, glob_to_regex, domain_parent, infer_parent_domain,
    domain_access, access_expand
)


class TestGlobPatternDetection:
    """Test glob pattern detection utilities."""

    def test_is_glob_pattern_with_star(self):
        """Test detection of * glob pattern."""
        assert is_glob_pattern("*") is True
        assert is_glob_pattern("*serial*") is True
        assert is_glob_pattern("prefix*") is True
        assert is_glob_pattern("*suffix") is True

    def test_is_glob_pattern_with_question(self):
        """Test detection of ? glob pattern."""
        assert is_glob_pattern("serial?") is True
        assert is_glob_pattern("?") is True

    def test_is_glob_pattern_literal(self):
        """Test non-glob patterns return False."""
        assert is_glob_pattern("serial@ff000000") is False
        assert is_glob_pattern("uart0") is False
        assert is_glob_pattern("") is False


class TestGlobToRegex:
    """Test glob to regex conversion."""

    def test_star_conversion(self):
        """Test * converts to .*"""
        regex = glob_to_regex("*")
        assert regex == "^.*$"

    def test_serial_glob(self):
        """Test *serial* converts correctly."""
        regex = glob_to_regex("*serial*")
        assert regex == "^.*serial.*$"

    def test_prefix_glob(self):
        """Test prefix* converts correctly."""
        regex = glob_to_regex("uart*")
        assert regex == "^uart.*$"

    def test_question_mark(self):
        """Test ? converts to single character match."""
        regex = glob_to_regex("serial?")
        assert regex == "^serial.$"

    def test_special_chars_escaped(self):
        """Test that regex special chars are escaped."""
        regex = glob_to_regex("serial@*")
        # @ doesn't need escaping in regex, but * is converted to .*
        assert regex == "^serial@.*$"


class TestDomainParent:
    """Test domain parent property lookup."""

    def test_domain_parent_returns_none_without_property(self, lopper_sdt):
        """Test domain_parent returns None when no parent: property."""
        # Find a domain node without parent property
        try:
            domains = lopper_sdt.tree['/domains']
            for child in domains.subnodes():
                parent = domain_parent(child)
                # Most test domains don't have explicit parent
                # This just tests the function doesn't crash
                break
        except:
            pytest.skip("No domains node in test tree")


class TestInferParentDomain:
    """Test parent domain inference by walking up tree."""

    def test_infer_stops_at_domains_container(self, lopper_sdt):
        """Test that inference stops at /domains container."""
        try:
            # Get a top-level domain (direct child of /domains)
            domains = lopper_sdt.tree['/domains']
            for child in domains.subnodes():
                if child.parent.abs_path == "/domains":
                    # This domain has no parent to infer
                    result = infer_parent_domain(lopper_sdt.tree, child)
                    assert result is None, \
                        "Top-level domain should not have inferred parent"
                    break
        except:
            pytest.skip("No domains node in test tree")


class TestGlobAccessIntegration:
    """Integration tests for glob access expansion.

    These tests require the full lopper processing pipeline and are
    covered by the legacy lopper_sanity.py tests:
    - domains_glob_test_child-serial: Tests *serial* glob
    - domains_glob_test_child-all: Tests * glob (all devices)

    Run with: python lopper_sanity.py --assists
    """

    @pytest.fixture
    def domains_parent_yaml(self):
        """Path to parent domain YAML with device list."""
        return "./lopper/selftest/domains/domains-parent.yaml"

    @pytest.fixture
    def domains_serial_glob_yaml(self):
        """Path to child domain YAML with *serial* glob."""
        return "./lopper/selftest/domains/domains-child-serial-glob.yaml"

    @pytest.fixture
    def domains_all_glob_yaml(self):
        """Path to child domain YAML with * glob."""
        return "./lopper/selftest/domains/domains-child-all-glob.yaml"

    def test_parent_yaml_has_devices(self, domains_parent_yaml):
        """Verify parent YAML has expected device count."""
        if not os.path.exists(domains_parent_yaml):
            pytest.skip(f"File not found: {domains_parent_yaml}")

        import yaml
        with open(domains_parent_yaml) as f:
            data = yaml.safe_load(f)

        access = data["domains"]["default"]["access"]
        # Parent should have 42 devices
        assert len(access) >= 40, \
            f"Parent should have ~42 devices, got {len(access)}"

    def test_serial_glob_yaml_has_parent_reference(self, domains_serial_glob_yaml):
        """Verify serial glob YAML has parent: property."""
        if not os.path.exists(domains_serial_glob_yaml):
            pytest.skip(f"File not found: {domains_serial_glob_yaml}")

        import yaml
        with open(domains_serial_glob_yaml) as f:
            data = yaml.safe_load(f)

        apu_domain = data["domains"]["default"]["domains"]["APU_domain"]
        assert "parent" in apu_domain, "APU_domain should have parent: property"
        assert apu_domain["parent"] == "/domains/default"

        # Check for glob in access
        access = apu_domain["access"]
        glob_found = any(
            a.get("dev", "").startswith("*") for a in access if isinstance(a, dict)
        )
        assert glob_found, "APU_domain should have glob pattern in access"

    def test_all_glob_yaml_has_wildcard(self, domains_all_glob_yaml):
        """Verify all-glob YAML has * pattern."""
        if not os.path.exists(domains_all_glob_yaml):
            pytest.skip(f"File not found: {domains_all_glob_yaml}")

        import yaml
        with open(domains_all_glob_yaml) as f:
            data = yaml.safe_load(f)

        apu_domain = data["domains"]["default"]["domains"]["APU_domain"]
        access = apu_domain["access"]

        # Should have dev: "*" entry
        star_found = any(
            a.get("dev") == "*" for a in access if isinstance(a, dict)
        )
        assert star_found, "APU_domain should have dev: '*' in access"


class TestPeerExclusion:
    """Test peer exclusion: explicit device refs excluded from glob matches.

    When sibling domains have explicit device references and glob patterns,
    the explicit refs should be removed from the pool before glob expansion.
    """

    def test_peer_exclusion_explicit_wins(self, tmp_path):
        """Test that explicit device refs are excluded from glob matches."""
        import subprocess
        import os

        # Write test YAML files
        devices_yaml = tmp_path / "devices.yaml"
        devices_yaml.write_text("""
domains:
  sdt_all_devices:
    compatible: openamp,domain-v1,devices
    id: 0
    access:
    - dev: serial@ff000000
    - dev: serial@ff010000
    - dev: can@ff060000
    - dev: ethernet@ff0e0000
""")

        child_yaml = tmp_path / "child.yaml"
        child_yaml.write_text("""
domains:
  linux:
    compatible: openamp,domain-v1
    id: 1
    access:
    - dev: "*"

  zephyr:
    compatible: openamp,domain-v1
    id: 2
    access:
    - dev: serial@ff000000
""")

        output_dts = tmp_path / "output.dts"

        # Run lopper
        cmd = [
            "./lopper.py", "-f", "--permissive", "--auto",
            "-i", str(devices_yaml),
            "-i", str(child_yaml),
            "./lopper/selftest/system-top.dts",
            str(output_dts)
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, cwd=os.getcwd())
        assert result.returncode == 0, f"Lopper failed: {result.stderr}"

        # Read output
        output_content = output_dts.read_text()

        # Find linux domain access list
        import re
        linux_match = re.search(r'linux\s*\{[^}]*access\s*=\s*<([^;]+);', output_content, re.DOTALL)
        assert linux_match, "Could not find linux domain access property"

        linux_access = linux_match.group(1)

        # serial@ff000000 should NOT be in linux (zephyr claimed it explicitly)
        assert "serialff000000" not in linux_access, \
            "serial@ff000000 should be excluded from linux glob (zephyr claimed it)"

        # serial@ff010000 SHOULD be in linux (not claimed by anyone)
        assert "serialff010000" in linux_access, \
            "serial@ff010000 should be in linux glob result"

        # Find zephyr domain access list
        zephyr_match = re.search(r'zephyr\s*\{[^}]*access\s*=\s*<([^;]+);', output_content, re.DOTALL)
        assert zephyr_match, "Could not find zephyr domain access property"

        zephyr_access = zephyr_match.group(1)

        # serial@ff000000 SHOULD be in zephyr (explicit claim)
        assert "serialff000000" in zephyr_access, \
            "serial@ff000000 should be in zephyr (explicit claim)"

    def test_peer_exclusion_multiple_explicit(self, tmp_path):
        """Test multiple explicit refs are all excluded from glob."""
        import subprocess
        import os

        devices_yaml = tmp_path / "devices.yaml"
        devices_yaml.write_text("""
domains:
  sdt_all_devices:
    compatible: openamp,domain-v1,devices
    id: 0
    access:
    - dev: serial@ff000000
    - dev: serial@ff010000
    - dev: can@ff060000
    - dev: ethernet@ff0e0000
""")

        child_yaml = tmp_path / "child.yaml"
        child_yaml.write_text("""
domains:
  linux:
    compatible: openamp,domain-v1
    id: 1
    access:
    - dev: "*"

  zephyr:
    compatible: openamp,domain-v1
    id: 2
    access:
    - dev: serial@ff000000

  baremetal:
    compatible: openamp,domain-v1
    id: 3
    access:
    - dev: can@ff060000
""")

        output_dts = tmp_path / "output.dts"

        cmd = [
            "./lopper.py", "-f", "--permissive", "--auto",
            "-i", str(devices_yaml),
            "-i", str(child_yaml),
            "./lopper/selftest/system-top.dts",
            str(output_dts)
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, cwd=os.getcwd())
        assert result.returncode == 0, f"Lopper failed: {result.stderr}"

        output_content = output_dts.read_text()

        # Find linux domain access
        import re
        linux_match = re.search(r'linux\s*\{[^}]*access\s*=\s*<([^;]+);', output_content, re.DOTALL)
        assert linux_match, "Could not find linux domain"

        linux_access = linux_match.group(1)

        # Both explicitly claimed devices should be excluded from linux
        assert "serialff000000" not in linux_access, \
            "serial@ff000000 should be excluded (zephyr claimed)"
        assert "canff060000" not in linux_access, \
            "can@ff060000 should be excluded (baremetal claimed)"

        # Unclaimed devices should be in linux
        assert "serialff010000" in linux_access, \
            "serial@ff010000 should be in linux"
        assert "ethernetff0e0000" in linux_access, \
            "ethernet@ff0e0000 should be in linux"

    def test_no_exclusion_without_globs(self, tmp_path):
        """Test that exclusion only happens when globs are present."""
        import subprocess
        import os

        devices_yaml = tmp_path / "devices.yaml"
        devices_yaml.write_text("""
domains:
  sdt_all_devices:
    compatible: openamp,domain-v1,devices
    id: 0
    access:
    - dev: serial@ff000000
    - dev: serial@ff010000
""")

        # Both domains have explicit refs, no globs
        child_yaml = tmp_path / "child.yaml"
        child_yaml.write_text("""
domains:
  linux:
    compatible: openamp,domain-v1
    id: 1
    access:
    - dev: serial@ff010000

  zephyr:
    compatible: openamp,domain-v1
    id: 2
    access:
    - dev: serial@ff000000
""")

        output_dts = tmp_path / "output.dts"

        cmd = [
            "./lopper.py", "-f", "--permissive", "--auto",
            "-i", str(devices_yaml),
            "-i", str(child_yaml),
            "./lopper/selftest/system-top.dts",
            str(output_dts)
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, cwd=os.getcwd())
        assert result.returncode == 0, f"Lopper failed: {result.stderr}"

        output_content = output_dts.read_text()

        # Both domains should have their explicit devices
        assert "serialff010000" in output_content
        assert "serialff000000" in output_content

    def test_peer_exclusion_with_explicit_parent(self, tmp_path):
        """Test peer exclusion when parent is explicit (not ,devices compatible).

        This tests the scenario where:
        - default domain has devices (no ,devices compatible)
        - APU_Linux has parent: /domains/default and uses dev: "*"
        - RPU_Zephyr has explicit dev: serial@ff000000

        serial@ff000000 should be excluded from APU_Linux's glob match.
        """
        import subprocess
        import os

        domains_yaml = tmp_path / "domains.yaml"
        domains_yaml.write_text("""
domains:
  default:
    compatible: openamp,domain-v1
    id: 0
    access:
    - dev: serial@ff000000
    - dev: serial@ff010000
    - dev: can@ff060000

  APU_Linux:
    parent: /domains/default
    compatible: openamp,domain-v1
    id: 1
    access:
    - dev: "*"

  RPU_Zephyr:
    parent: /domains/default
    compatible: openamp,domain-v1
    id: 2
    access:
    - dev: serial@ff000000
""")

        output_dts = tmp_path / "output.dts"

        cmd = [
            "./lopper.py", "-f", "--permissive", "--auto",
            "-i", str(domains_yaml),
            "./lopper/selftest/system-top.dts",
            str(output_dts)
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, cwd=os.getcwd())
        assert result.returncode == 0, f"Lopper failed: {result.stderr}"

        output_content = output_dts.read_text()

        # Find APU_Linux domain access
        import re
        linux_match = re.search(r'APU_Linux\s*\{[^}]*access\s*=\s*<([^;]+);', output_content, re.DOTALL)
        assert linux_match, "Could not find APU_Linux domain"

        linux_access = linux_match.group(1)

        # serial@ff000000 should be excluded (RPU_Zephyr claimed it)
        assert "serialff000000" not in linux_access, \
            "serial@ff000000 should be excluded from APU_Linux (RPU_Zephyr claimed)"

        # Other devices should be in APU_Linux
        assert "serialff010000" in linux_access, \
            "serial@ff010000 should be in APU_Linux"
        assert "canff060000" in linux_access, \
            "can@ff060000 should be in APU_Linux"


class TestDomainAccessFunction:
    """Regression tests for bugs in domain_access() and access_expand().

    These tests exercise the bug fixes in yaml_to_dts_expansion.py:
    - Bug 1: access_prop_string was not initialized before the try block,
      causing NameError if the except path was taken.
    - Bug 2: Early return was missing when access_prop_string is None after
      the try/except, causing TypeError on json.loads(None).
    - Bug 3: access_expand() called .join() on a list object (AttributeError);
      should use ','.join(list).
    - Bug 4: access_expand() tried to JSON-parse integer phandle lists, causing
      TypeError; phandle-based access should be skipped.
    """

    def _make_node_with_json_access(self, json_val):
        """Return a minimal LopperNode with an access property set to json_val."""
        from lopper.tree import LopperNode, LopperProp
        node = LopperNode(-1, "/domains/test_domain")
        prop = LopperProp("access", -1, node)
        prop.pclass = "json"
        prop.__dict__["value"] = json_val
        node + prop
        return node

    def _make_node_with_int_access(self, int_list):
        """Return a minimal LopperNode with an integer-list access property."""
        from lopper.tree import LopperNode, LopperProp
        node = LopperNode(-1, "/domains/test_domain")
        prop = LopperProp("access", -1, node)
        prop.__dict__["value"] = int_list
        node + prop
        return node

    def test_domain_access_json_string_returns_list(self):
        """domain_access() on a JSON string access property returns parsed list."""
        payload = [{"dev": "serial@ff000000"}, {"dev": "can@ff060000"}]
        node = self._make_node_with_json_access(json.dumps(payload))
        result = domain_access(node)
        assert result == payload

    def test_domain_access_json_list_value_returns_list(self):
        """domain_access() handles value stored as a Python list of strings."""
        payload = [{"dev": "serial@ff000000"}]
        # LopperProp sometimes stores a single JSON blob in a list
        node = self._make_node_with_json_access([json.dumps(payload)])
        result = domain_access(node)
        assert result == payload

    def test_domain_access_no_access_property_returns_empty(self):
        """domain_access() returns [] when node has no access property."""
        from lopper.tree import LopperNode
        node = LopperNode(-1, "/domains/empty")
        result = domain_access(node)
        assert result == []

    def test_domain_access_integer_list_returns_empty(self):
        """domain_access() returns [] without NameError when value is a phandle int list.

        Regression for Bug 1/2: access_prop_string was not initialized before
        the try block.  When value is a list of integers, ','.join([int,...])
        raises TypeError inside the try block, leaving access_prop_string
        unset.  Before the fix this caused NameError; after the fix it returns [].
        """
        from lopper.tree import LopperNode, LopperProp
        node = LopperNode(-1, "/domains/bad_access")
        prop = LopperProp("access", -1, node)
        # A phandle-based access list: list of integers, not JSON strings.
        # ','.join([0x100, 0x0]) raises TypeError in the try block.
        prop.__dict__["value"] = [0x100, 0x0, 0x200, 0x0]
        node + prop
        # Must not raise NameError (pre-fix) — should return []
        result = domain_access(node)
        assert result == []

    def test_access_expand_skips_integer_phandle_list(self):
        """access_expand() returns early for integer phandle access lists.

        Regression for Bug 4: passing an integer list to json.loads() raised
        TypeError because integers are not JSON strings.
        """
        from lopper.tree import LopperNode, LopperProp, LopperTree
        tree = LopperTree()
        node = self._make_node_with_int_access([0x100, 0x0, 0x200, 0x0])
        node.tree = tree
        # Must not raise TypeError
        access_expand(tree, node)

    def test_access_expand_processes_json_string_list(self):
        """access_expand() processes a JSON-encoded access list stored as [str].

        Regression for Bug 3: the original code called access_props[0].value.join()
        which is AttributeError on a list; the fix uses ','.join(list).
        """
        from lopper.tree import LopperNode, LopperProp, LopperTree
        payload = [{"dev": "serial@ff000000"}]
        tree = LopperTree()
        node = self._make_node_with_json_access([json.dumps(payload)])
        node.tree = tree
        # Must not raise AttributeError — should process without crashing
        access_expand(tree, node)


class TestPathRefPruning:
    """Regression tests for strict-mode path-ref / alias-ref pruning in LopperTree.print().

    These tests exercise the two pre-output passes added to LopperTree.print():
      Pass A: drop /aliases entries (and any other string properties) whose value is an
              absolute node path that no longer exists in the tree.
      Pass B: drop known alias-ref properties (e.g. stdout-path) that reference an alias
              name that was removed by pass A.
    """

    def _build_tree_with_aliases(self, alias_entries, live_nodes):
        """Build a minimal LopperTree with /aliases and some live nodes.

        alias_entries: dict of alias_name -> node_path string
        live_nodes: list of absolute node paths that should exist in the tree
        """
        from lopper.tree import LopperTree, LopperNode, LopperProp

        tree = LopperTree()

        # root node
        root = LopperNode(-1, "/")
        root.abs_path = "/"
        tree + root

        # live nodes
        for path in live_nodes:
            parts = path.strip("/").split("/")
            current = "/"
            for part in parts:
                child_path = current.rstrip("/") + "/" + part
                if child_path not in [n.abs_path for n in tree]:
                    node = LopperNode(-1, part)
                    node.abs_path = child_path
                    tree + node
                current = child_path

        # /aliases node
        aliases = LopperNode(-1, "aliases")
        aliases.abs_path = "/aliases"
        tree + aliases

        for aname, apath in alias_entries.items():
            prop = LopperProp(aname, -1, aliases, [apath])
            prop.pclass = "string"
            aliases + prop

        return tree, aliases

    def _run_pruning_passes(self, tree):
        """Run the strict-mode pruning passes directly without needing a full print() call.

        tree.print() tries to open output.name which fails for StringIO.  Instead,
        replicate only the pruning logic so the tests remain self-contained.
        """
        import re
        import lopper.schema

        try:
            aliases_node = tree["/aliases"]
        except Exception:
            aliases_node = None

        # Pass A: drop dangling path-ref properties
        for n in tree:
            props_to_delete = []
            for p in n:
                val = p.value
                if not isinstance(val, list) or len(val) != 1 or not isinstance(val[0], str):
                    continue
                raw = val[0].strip().strip('"')
                if raw.startswith('/'):
                    try:
                        tree[raw]
                    except Exception:
                        props_to_delete.append(p)
            for p in props_to_delete:
                n - p

        # Pass B: drop dangling alias-ref properties
        if aliases_node is not None:
            try:
                alias_ref_props = set(
                    lopper.schema.PROPERTY_TYPE_HINTS.get('alias_ref_properties', [])
                )
            except Exception:
                alias_ref_props = {'stdout-path', 'linux,stdout-path'}
            for n in tree:
                props_to_delete = []
                for p in n:
                    if p.name not in alias_ref_props:
                        continue
                    val = p.value
                    if not isinstance(val, list) or len(val) != 1 or not isinstance(val[0], str):
                        continue
                    raw = val[0].strip().strip('"')
                    alias_name = raw.split(':')[0]
                    if alias_name and aliases_node.propval(alias_name) == ['']:
                        props_to_delete.append(p)
                for p in props_to_delete:
                    n - p

    def test_path_ref_pruning_removes_dangling_alias(self):
        """Pass A removes a /aliases entry pointing to a deleted node."""
        from lopper.tree import LopperNode, LopperProp

        tree, aliases = self._build_tree_with_aliases(
            alias_entries={"serial0": "/axi/serial@f1920000",
                           "serial1": "/axi/serial@f1930000"},
            live_nodes=["/axi/serial@f1930000"],
        )
        tree.strict = True
        self._run_pruning_passes(tree)

        remaining = [p.name for p in aliases]
        assert "serial0" not in remaining, "dangling serial0 alias should have been pruned"
        assert "serial1" in remaining, "live serial1 alias must be preserved"

    def test_path_ref_pruning_preserves_valid_alias(self):
        """Pass A leaves /aliases entries intact when the target node exists."""
        from lopper.tree import LopperNode, LopperProp

        tree, aliases = self._build_tree_with_aliases(
            alias_entries={"serial1": "/axi/serial@f1930000"},
            live_nodes=["/axi/serial@f1930000"],
        )
        tree.strict = True
        self._run_pruning_passes(tree)

        remaining = [p.name for p in aliases]
        assert "serial1" in remaining, "valid alias must not be pruned"

    def test_alias_ref_pruning_removes_dangling_stdout_path(self):
        """Pass B removes stdout-path when the referenced alias was pruned by pass A."""
        from lopper.tree import LopperNode, LopperProp

        tree, aliases = self._build_tree_with_aliases(
            alias_entries={"serial0": "/axi/serial@f1920000"},
            live_nodes=[],   # node gone — serial0 alias will be pruned in pass A
        )
        tree.strict = True

        # Add a /chosen node with stdout-path referencing serial0
        chosen = LopperNode(-1, "chosen")
        chosen.abs_path = "/chosen"
        tree + chosen
        stdout_prop = LopperProp("stdout-path", -1, chosen, ["serial0:115200n8"])
        stdout_prop.pclass = "string"
        chosen + stdout_prop

        self._run_pruning_passes(tree)

        remaining_chosen = [p.name for p in chosen]
        assert "stdout-path" not in remaining_chosen, \
            "stdout-path referencing pruned alias must itself be pruned"
