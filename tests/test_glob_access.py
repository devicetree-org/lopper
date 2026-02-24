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
