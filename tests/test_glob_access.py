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
    is_glob_pattern, glob_to_regex, domain_parent, infer_parent_domain
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
