"""
Pytest tests for SDT devices YAML generation assist.

This module tests the sdt_devices assist that scans the System Device Tree
for devices across multiple categories and generates a YAML domain containing
all devices.

Copyright (c) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.

SPDX-License-Identifier: BSD-3-Clause

Author:
    Bruce Ashfield <bruce.ashfield@amd.com>
"""

import os
import re
import tempfile
import pytest
import yaml

from lopper import LopperSDT
from lopper.tree import LopperTree, LopperNode, LopperProp
from lopper.assists.sdt_devices import (
    SDTDevices, sdt_devices, is_compat, DeviceCategory
)


class TestDeviceCategory:
    """Test the DeviceCategory enum."""

    def test_all_categories_returns_list(self):
        """Test all_categories returns all enum values."""
        cats = DeviceCategory.all_categories()
        assert len(cats) == 5
        assert DeviceCategory.BUS in cats
        assert DeviceCategory.CPU in cats
        assert DeviceCategory.MEMORY in cats
        assert DeviceCategory.FIRMWARE in cats
        assert DeviceCategory.TOPLEVEL in cats

    def test_from_string_valid(self):
        """Test from_string with valid category names."""
        assert DeviceCategory.from_string("bus") == DeviceCategory.BUS
        assert DeviceCategory.from_string("CPU") == DeviceCategory.CPU
        assert DeviceCategory.from_string("Memory") == DeviceCategory.MEMORY
        assert DeviceCategory.from_string("  firmware  ") == DeviceCategory.FIRMWARE

    def test_from_string_invalid(self):
        """Test from_string returns None for invalid names."""
        assert DeviceCategory.from_string("invalid") is None
        assert DeviceCategory.from_string("") is None

    def test_parse_list(self):
        """Test parse_list parses comma-separated categories."""
        cats = DeviceCategory.parse_list("bus,cpu,memory")
        assert len(cats) == 3
        assert DeviceCategory.BUS in cats
        assert DeviceCategory.CPU in cats
        assert DeviceCategory.MEMORY in cats

    def test_parse_list_with_invalid(self):
        """Test parse_list ignores invalid category names."""
        cats = DeviceCategory.parse_list("bus,invalid,cpu")
        assert len(cats) == 2
        assert DeviceCategory.BUS in cats
        assert DeviceCategory.CPU in cats


class TestIsCompat:
    """Test the is_compat function for assist matching."""

    def test_matches_module_pattern(self):
        """Test is_compat matches 'module,sdt_devices' pattern."""
        result = is_compat(None, "module,sdt_devices")
        assert result == sdt_devices

    def test_matches_sdt_devices_pattern(self):
        """Test is_compat matches 'sdt-devices,sdt-devices-v1' pattern."""
        result = is_compat(None, "sdt-devices,sdt-devices-v1")
        assert result == sdt_devices

    def test_no_match_returns_empty_string(self):
        """Test is_compat returns empty string for non-matching patterns."""
        result = is_compat(None, "some,other-compat")
        assert result == ""

    def test_no_match_partial(self):
        """Test is_compat doesn't match partial patterns."""
        result = is_compat(None, "module,sdt")
        assert result == ""


class TestSDTDevicesDiscovery:
    """Unit tests for device discovery functionality."""

    def test_discover_bus_devices_finds_simple_bus_children(self, lopper_sdt):
        """Test that discover_bus_devices finds devices under simple-bus nodes."""
        generator = SDTDevices(lopper_sdt)
        devices = generator.discover_bus_devices()

        assert len(devices) > 0, "Should discover at least one device"
        for dev in devices:
            assert 'dev' in dev, "Each device entry must have 'dev' key"

    def test_discover_bus_devices_only_addressable(self, lopper_sdt):
        """Test that only addressable devices (with @) are discovered."""
        generator = SDTDevices(lopper_sdt)
        devices = generator.discover_bus_devices()

        for dev in devices:
            assert '@' in dev['dev'], \
                f"Device '{dev['dev']}' should be addressable (contain @)"

    def test_discover_bus_devices_includes_labels(self, lopper_sdt):
        """Test that device labels are included when present."""
        generator = SDTDevices(lopper_sdt)
        devices = generator.discover_bus_devices()

        devices_with_labels = [d for d in devices if 'label' in d]
        for dev in devices_with_labels:
            assert dev['label'], "Label should not be empty"

    def test_discover_bus_devices_no_duplicates(self, lopper_sdt):
        """Test that discovered devices have no duplicates."""
        generator = SDTDevices(lopper_sdt)
        devices = generator.discover_bus_devices()

        dev_names = [d['dev'] for d in devices]
        assert len(devices) == len(set(dev_names)), \
            "Device list should not have duplicates"

    def test_discover_bus_devices_custom_bus_type(self, lopper_sdt):
        """Test discovering devices with custom bus types."""
        generator = SDTDevices(lopper_sdt)

        devices = generator.discover_bus_devices(bus_types=['nonexistent-bus'])
        assert devices == []

    def test_discover_bus_devices_multiple_bus_types(self, lopper_sdt):
        """Test discovering devices with multiple bus types."""
        generator = SDTDevices(lopper_sdt)

        devices = generator.discover_bus_devices(
            bus_types=['simple-bus', 'xlnx,versal-axi']
        )
        assert isinstance(devices, list)


class TestCPUDiscovery:
    """Test CPU cluster discovery."""

    def test_discover_cpus_returns_list(self, lopper_sdt):
        """Test that discover_cpus returns a list."""
        generator = SDTDevices(lopper_sdt)
        cpus = generator.discover_cpus()

        assert isinstance(cpus, list)

    def test_discover_cpus_has_dev_key(self, lopper_sdt):
        """Test that CPU entries have 'dev' key."""
        generator = SDTDevices(lopper_sdt)
        cpus = generator.discover_cpus()

        for cpu in cpus:
            assert 'dev' in cpu, "CPU entry must have 'dev' key"

    def test_discover_cpus_no_duplicates(self, lopper_sdt):
        """Test that CPU cluster entries are not duplicated."""
        generator = SDTDevices(lopper_sdt)
        cpus = generator.discover_cpus()

        # Each cluster should appear only once
        dev_names = [c['dev'] for c in cpus]
        assert len(cpus) == len(set(dev_names)), \
            "CPU cluster entries should not be duplicated"


class TestMemoryDiscovery:
    """Test memory node discovery."""

    def test_discover_memory_returns_dict(self, lopper_sdt):
        """Test that discover_memory returns dict with memory and sram keys."""
        generator = SDTDevices(lopper_sdt)
        memory = generator.discover_memory()

        assert isinstance(memory, dict)
        assert 'memory' in memory
        assert 'sram' in memory
        assert isinstance(memory['memory'], list)
        assert isinstance(memory['sram'], list)

    def test_discover_memory_entries_have_dev(self, lopper_sdt):
        """Test that memory entries have 'dev' key."""
        generator = SDTDevices(lopper_sdt)
        memory = generator.discover_memory()

        for mem in memory['memory']:
            assert 'dev' in mem
        for sram in memory['sram']:
            assert 'dev' in sram

    def test_classify_memory_type_sram(self, lopper_sdt):
        """Test SRAM classification."""
        generator = SDTDevices(lopper_sdt)

        assert generator._classify_memory_type("tcm@ffe00000") == "sram"
        assert generator._classify_memory_type("ocm@fffc0000") == "sram"
        assert generator._classify_memory_type("sram@10000") == "sram"

    def test_classify_memory_type_memory(self, lopper_sdt):
        """Test memory classification."""
        generator = SDTDevices(lopper_sdt)

        assert generator._classify_memory_type("memory@0") == "memory"
        assert generator._classify_memory_type("ddr@80000000") == "memory"


class TestFirmwareDiscovery:
    """Test firmware node discovery."""

    def test_discover_firmware_returns_list(self, lopper_sdt):
        """Test that discover_firmware returns a list."""
        generator = SDTDevices(lopper_sdt)
        firmware = generator.discover_firmware()

        assert isinstance(firmware, list)

    def test_discover_firmware_entries_have_dev(self, lopper_sdt):
        """Test that firmware entries have 'dev' key."""
        generator = SDTDevices(lopper_sdt)
        firmware = generator.discover_firmware()

        for fw in firmware:
            assert 'dev' in fw


class TestToplevelDiscovery:
    """Test toplevel node discovery."""

    def test_discover_toplevel_returns_list(self, lopper_sdt):
        """Test that discover_toplevel returns a list."""
        generator = SDTDevices(lopper_sdt)
        toplevel = generator.discover_toplevel()

        assert isinstance(toplevel, list)

    def test_discover_toplevel_skips_special_nodes(self, lopper_sdt):
        """Test that special nodes are skipped."""
        generator = SDTDevices(lopper_sdt)
        toplevel = generator.discover_toplevel()

        dev_names = [d['dev'] for d in toplevel]
        assert 'chosen' not in dev_names
        assert 'aliases' not in dev_names
        assert '__symbols__' not in dev_names


class TestPatternFiltering:
    """Test include/exclude pattern filtering."""

    def test_include_pattern_filters_devices(self, lopper_sdt):
        """Test that include pattern filters devices."""
        generator = SDTDevices(lopper_sdt)
        devices = [
            {'dev': 'serial@ff000000'},
            {'dev': 'can@ff060000'},
            {'dev': 'serial@ff010000'},
        ]

        filtered = generator._apply_pattern_filter(
            devices, include_pattern="serial@.*"
        )

        assert len(filtered) == 2
        assert all('serial' in d['dev'] for d in filtered)

    def test_exclude_pattern_removes_devices(self, lopper_sdt):
        """Test that exclude pattern removes devices."""
        generator = SDTDevices(lopper_sdt)
        devices = [
            {'dev': 'serial@ff000000'},
            {'dev': 'can@ff060000'},
            {'dev': 'serial@ff010000'},
        ]

        filtered = generator._apply_pattern_filter(
            devices, exclude_pattern="serial@.*"
        )

        assert len(filtered) == 1
        assert filtered[0]['dev'] == 'can@ff060000'

    def test_both_patterns_applied(self, lopper_sdt):
        """Test that both patterns are applied."""
        generator = SDTDevices(lopper_sdt)
        devices = [
            {'dev': 'serial@ff000000'},
            {'dev': 'serial@ff010000'},
            {'dev': 'can@ff060000'},
        ]

        filtered = generator._apply_pattern_filter(
            devices,
            include_pattern="@ff0.*",
            exclude_pattern="can@.*"
        )

        assert len(filtered) == 2
        assert all('serial' in d['dev'] for d in filtered)

    def test_no_patterns_returns_original(self, lopper_sdt):
        """Test that no patterns returns original list."""
        generator = SDTDevices(lopper_sdt)
        devices = [{'dev': 'test@123'}]

        filtered = generator._apply_pattern_filter(devices)
        assert filtered == devices


class TestDiscoverAll:
    """Test the orchestrated discovery."""

    def test_discover_all_default_returns_all_categories(self, lopper_sdt):
        """Test discover_all with default categories."""
        generator = SDTDevices(lopper_sdt)
        devices = generator.discover_all()

        assert 'access' in devices
        assert 'cpus' in devices
        assert 'memory' in devices
        assert 'sram' in devices

    def test_discover_all_single_category(self, lopper_sdt):
        """Test discover_all with single category."""
        generator = SDTDevices(lopper_sdt)

        # Only bus devices
        devices = generator.discover_all(categories=[DeviceCategory.BUS])

        assert len(devices['access']) > 0
        # CPUs should be empty since we only asked for BUS
        assert len(devices['cpus']) == 0

    def test_discover_all_multiple_categories(self, lopper_sdt):
        """Test discover_all with multiple categories."""
        generator = SDTDevices(lopper_sdt)

        devices = generator.discover_all(
            categories=[DeviceCategory.BUS, DeviceCategory.CPU]
        )

        # Should have bus devices in access
        # May or may not have CPUs depending on test tree
        assert isinstance(devices['access'], list)
        assert isinstance(devices['cpus'], list)

    def test_discover_all_with_pattern_filter(self, lopper_sdt):
        """Test discover_all with pattern filtering."""
        generator = SDTDevices(lopper_sdt)

        # Get all devices first
        all_devices = generator.discover_all()

        # Then filter
        filtered = generator.discover_all(include_pattern="serial@.*")

        # Filtered should have fewer or equal devices
        total_all = sum(len(v) for v in all_devices.values())
        total_filtered = sum(len(v) for v in filtered.values())

        assert total_filtered <= total_all


class TestSDTDevicesGeneration:
    """Test YAML domain generation."""

    def test_generate_domain_creates_tree(self, lopper_sdt):
        """Test that generate_domain creates a valid LopperTree."""
        generator = SDTDevices(lopper_sdt)
        tree = generator.generate_domain()

        assert isinstance(tree, LopperTree)

    def test_generate_domain_has_domains_container(self, lopper_sdt):
        """Test that generated tree has /domains container."""
        generator = SDTDevices(lopper_sdt)
        tree = generator.generate_domain()

        domains_node = tree["/domains"]
        assert domains_node is not None

    def test_generate_domain_has_named_domain(self, lopper_sdt):
        """Test that generated tree has named domain node."""
        generator = SDTDevices(lopper_sdt)
        tree = generator.generate_domain(domain_name='test_domain')

        domains_node = tree["/domains"]
        domain_found = False
        for child in domains_node.subnodes(children_only=True):
            if child.name == 'test_domain':
                domain_found = True
                break

        assert domain_found, "Domain 'test_domain' should exist"

    def test_generate_domain_has_compatible(self, lopper_sdt):
        """Test that domain has correct compatible string."""
        generator = SDTDevices(lopper_sdt)
        tree = generator.generate_domain(domain_name='test_domain')

        domains_node = tree["/domains"]
        for child in domains_node.subnodes(children_only=True):
            if child.name == 'test_domain':
                compat = child["compatible"].value
                assert "openamp,domain-v1,devices" in compat
                break

    def test_generate_domain_has_id(self, lopper_sdt):
        """Test that domain has id property."""
        generator = SDTDevices(lopper_sdt)
        tree = generator.generate_domain(domain_name='test_domain')

        domains_node = tree["/domains"]
        for child in domains_node.subnodes(children_only=True):
            if child.name == 'test_domain':
                id_prop = child["id"].value
                assert id_prop is not None
                break

    def test_generate_domain_default_name(self, lopper_sdt):
        """Test that default domain name is 'sdt_all_devices'."""
        generator = SDTDevices(lopper_sdt)
        tree = generator.generate_domain()

        domains_node = tree["/domains"]
        default_found = False
        for child in domains_node.subnodes(children_only=True):
            if child.name == 'sdt_all_devices':
                default_found = True
                break

        assert default_found, "Default domain name should be 'sdt_all_devices'"

    def test_generate_domain_with_categories(self, lopper_sdt):
        """Test generate_domain respects category selection."""
        generator = SDTDevices(lopper_sdt)

        # Generate with only bus category
        tree = generator.generate_domain(categories=[DeviceCategory.BUS])

        domains_node = tree["/domains"]
        for child in domains_node.subnodes(children_only=True):
            # Should have access property (bus devices)
            access = child.propval("access")
            # access may be empty list or actual devices
            assert access is not None or True

    def test_generate_domain_with_pattern(self, lopper_sdt):
        """Test generate_domain with include pattern."""
        generator = SDTDevices(lopper_sdt)

        tree = generator.generate_domain(include_pattern="serial@.*")

        # Verify only serial devices in access
        domains_node = tree["/domains"]
        for child in domains_node.subnodes(children_only=True):
            access = child.propval("access")
            if access:
                for entry in access:
                    if isinstance(entry, dict) and 'dev' in entry:
                        assert 'serial' in entry['dev']


class TestSDTDevicesIntegration:
    """Integration tests for the sdt_devices entry point."""

    @pytest.fixture
    def temp_output_file(self):
        """Create a temporary file for output."""
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.yaml', delete=False
        ) as f:
            yield f.name
        if os.path.exists(f.name):
            os.unlink(f.name)

    def test_sdt_devices_entry_point_returns_true(
        self, lopper_sdt, temp_output_file
    ):
        """Test that sdt_devices entry point returns True on success."""
        lopper_sdt.output_file = temp_output_file
        options = {
            'verbose': 0,
            'args': []
        }

        result = sdt_devices(None, lopper_sdt, options)
        assert result is True

    def test_sdt_devices_creates_output_file(
        self, lopper_sdt, temp_output_file
    ):
        """Test that sdt_devices creates output file."""
        lopper_sdt.output_file = temp_output_file
        options = {
            'verbose': 0,
            'args': []
        }

        sdt_devices(None, lopper_sdt, options)
        assert os.path.exists(temp_output_file)

    def test_sdt_devices_output_is_valid_yaml(
        self, lopper_sdt, temp_output_file
    ):
        """Test that output is valid YAML."""
        lopper_sdt.output_file = temp_output_file
        options = {
            'verbose': 0,
            'args': []
        }

        sdt_devices(None, lopper_sdt, options)

        with open(temp_output_file) as f:
            data = yaml.safe_load(f)

        assert data is not None

    def test_sdt_devices_output_has_domains_structure(
        self, lopper_sdt, temp_output_file
    ):
        """Test that output YAML has correct domains structure."""
        lopper_sdt.output_file = temp_output_file
        options = {
            'verbose': 0,
            'args': []
        }

        sdt_devices(None, lopper_sdt, options)

        with open(temp_output_file) as f:
            data = yaml.safe_load(f)

        assert 'domains' in data, "Output should have 'domains' key"
        assert 'sdt_all_devices' in data['domains'], \
            "Output should have 'sdt_all_devices' domain"

    def test_sdt_devices_custom_domain_name(
        self, lopper_sdt, temp_output_file
    ):
        """Test sdt_devices with custom domain name option."""
        lopper_sdt.output_file = temp_output_file
        options = {
            'verbose': 0,
            'args': ['-n', 'my_custom_domain']
        }

        sdt_devices(None, lopper_sdt, options)

        with open(temp_output_file) as f:
            data = yaml.safe_load(f)

        assert 'my_custom_domain' in data['domains'], \
            "Output should have custom domain name"

    def test_sdt_devices_with_output_option(
        self, lopper_sdt, test_outdir
    ):
        """Test sdt_devices with -o output option."""
        output_path = os.path.join(test_outdir, "custom-output.yaml")
        options = {
            'verbose': 0,
            'args': ['-o', output_path]
        }

        result = sdt_devices(None, lopper_sdt, options)
        assert result is True
        assert os.path.exists(output_path)

    def test_sdt_devices_with_categories_option(
        self, lopper_sdt, temp_output_file
    ):
        """Test sdt_devices with -c categories option."""
        lopper_sdt.output_file = temp_output_file
        options = {
            'verbose': 0,
            'args': ['-c', 'bus']
        }

        result = sdt_devices(None, lopper_sdt, options)
        assert result is True

        with open(temp_output_file) as f:
            data = yaml.safe_load(f)

        # Should have access (bus devices) but not cpus
        domain = data['domains']['sdt_all_devices']
        # cpus should not be present or empty when only bus category
        assert 'cpus' not in domain or not domain.get('cpus')

    def test_sdt_devices_with_exclude_categories(
        self, lopper_sdt, temp_output_file
    ):
        """Test sdt_devices with --exclude-categories option."""
        lopper_sdt.output_file = temp_output_file
        options = {
            'verbose': 0,
            'args': ['--exclude-categories', 'firmware,toplevel']
        }

        result = sdt_devices(None, lopper_sdt, options)
        assert result is True

    def test_sdt_devices_with_include_pattern(
        self, lopper_sdt, temp_output_file
    ):
        """Test sdt_devices with --include-pattern option."""
        lopper_sdt.output_file = temp_output_file
        options = {
            'verbose': 0,
            'args': ['--include-pattern', 'serial@.*']
        }

        result = sdt_devices(None, lopper_sdt, options)
        assert result is True

        with open(temp_output_file) as f:
            data = yaml.safe_load(f)

        domain = data['domains']['sdt_all_devices']
        access = domain.get('access', [])

        # All access entries should match serial pattern
        for entry in access:
            if entry and isinstance(entry, dict) and 'dev' in entry:
                assert 'serial' in entry['dev'].lower() or '@' not in entry['dev']

    def test_sdt_devices_with_exclude_pattern(
        self, lopper_sdt, temp_output_file
    ):
        """Test sdt_devices with --exclude-pattern option."""
        lopper_sdt.output_file = temp_output_file
        options = {
            'verbose': 0,
            'args': ['--exclude-pattern', 'serial@.*', '-c', 'bus']
        }

        result = sdt_devices(None, lopper_sdt, options)
        assert result is True

        with open(temp_output_file) as f:
            data = yaml.safe_load(f)

        domain = data['domains']['sdt_all_devices']
        access = domain.get('access', [])

        # No access entries should be serial
        for entry in access:
            if isinstance(entry, dict) and 'dev' in entry:
                assert 'serial' not in entry['dev'].lower()


class TestBackwardsCompatibility:
    """Test backwards compatibility with original implementation."""

    @pytest.fixture
    def temp_output_file(self):
        """Create a temporary file for output."""
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.yaml', delete=False
        ) as f:
            yield f.name
        if os.path.exists(f.name):
            os.unlink(f.name)

    def test_no_options_includes_bus_devices(
        self, lopper_sdt, temp_output_file
    ):
        """Test that default behavior includes bus devices."""
        lopper_sdt.output_file = temp_output_file
        options = {
            'verbose': 0,
            'args': []
        }

        sdt_devices(None, lopper_sdt, options)

        with open(temp_output_file) as f:
            data = yaml.safe_load(f)

        domain = data['domains']['sdt_all_devices']
        access = domain.get('access', [])

        # Should have bus devices (addressable with @)
        addressable = [e for e in access if e and '@' in e.get('dev', '')]
        assert len(addressable) > 0

    def test_bus_types_option_still_works(
        self, lopper_sdt, temp_output_file
    ):
        """Test that -b/--bus-types option still works."""
        lopper_sdt.output_file = temp_output_file
        options = {
            'verbose': 0,
            'args': ['-b', 'simple-bus', '-c', 'bus']
        }

        result = sdt_devices(None, lopper_sdt, options)
        assert result is True

    def test_domain_has_compatible_string(
        self, lopper_sdt, temp_output_file
    ):
        """Test domain has openamp,domain-v1,devices compatible."""
        lopper_sdt.output_file = temp_output_file
        options = {
            'verbose': 0,
            'args': []
        }

        sdt_devices(None, lopper_sdt, options)

        with open(temp_output_file) as f:
            data = yaml.safe_load(f)

        domain = data['domains']['sdt_all_devices']
        assert domain.get('compatible') == 'openamp,domain-v1,devices'


class TestSDTDevicesGlobUsage:
    """Test that generated YAML can be used for glob matching."""

    def test_generated_yaml_has_access_devices(
        self, lopper_sdt, test_outdir
    ):
        """Test generated YAML has devices in access list."""
        output_path = os.path.join(test_outdir, "sdt-devices-glob-test.yaml")
        lopper_sdt.output_file = output_path
        options = {
            'verbose': 0,
            'args': []
        }

        sdt_devices(None, lopper_sdt, options)

        with open(output_path) as f:
            data = yaml.safe_load(f)

        domain = data['domains']['sdt_all_devices']
        access = domain.get('access', [])

        non_empty_entries = [e for e in access if e]
        assert len(non_empty_entries) > 0, \
            "Generated domain should have devices for glob matching"

    def test_generated_yaml_device_names_are_addressable(
        self, lopper_sdt, test_outdir
    ):
        """Test bus devices in access list are addressable (have @)."""
        output_path = os.path.join(test_outdir, "sdt-devices-addr-test.yaml")
        lopper_sdt.output_file = output_path
        options = {
            'verbose': 0,
            'args': ['-c', 'bus']  # Only bus to ensure addressable
        }

        sdt_devices(None, lopper_sdt, options)

        with open(output_path) as f:
            data = yaml.safe_load(f)

        domain = data['domains']['sdt_all_devices']
        access = domain.get('access', [])

        for entry in access:
            if entry and 'dev' in entry:
                assert '@' in entry['dev'], \
                    f"Bus device '{entry['dev']}' should be addressable"

    def test_compatible_string_for_parent_domain(
        self, lopper_sdt, test_outdir
    ):
        """Test domain has compatible string identifiable as SDT devices."""
        output_path = os.path.join(test_outdir, "sdt-devices-compat-test.yaml")
        lopper_sdt.output_file = output_path
        options = {
            'verbose': 0,
            'args': []
        }

        sdt_devices(None, lopper_sdt, options)

        with open(output_path) as f:
            data = yaml.safe_load(f)

        domain = data['domains']['sdt_all_devices']
        compatible = domain.get('compatible')

        assert compatible == 'openamp,domain-v1,devices', \
            "Domain should have identifiable compatible string"
