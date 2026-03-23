"""
Tests for lopper/audit/schema.py - schema-based property validation.

This module tests the schema validation framework:
- ConstraintType, PropertyConstraint, NodeConstraints: Data structures
- Node pattern matching: _node_matches_pattern, _get_matching_constraints
- Validation checks: check_forbidden_properties, check_required_properties, etc.
- SchemaValidator: Orchestration of phased validation

Tests validation rules for reserved-memory nodes where device_type="memory"
incorrectly applied can cause Xen boot failures.
"""

import pytest
from unittest.mock import MagicMock

from lopper.audit.schema import (
    ConstraintType,
    PropertyConstraint,
    NodeConstraints,
    NODE_PROPERTY_CONSTRAINTS,
    load_constraints_from_schemas,
    _node_matches_pattern,
    _get_matching_constraints,
    check_forbidden_properties,
    check_required_properties,
    check_property_values,
    check_mutex_properties,
    SchemaValidator,
    validate_schema,
)
from lopper.audit.base import (
    ValidationPhase,
    ValidationResult,
    ValidatorRegistry,
)


class TestDataStructures:
    """Tests for constraint data structures."""

    def test_constraint_type_values(self):
        """Test ConstraintType enum values."""
        assert ConstraintType.REQUIRED.value == "required"
        assert ConstraintType.FORBIDDEN.value == "forbidden"
        assert ConstraintType.CONST.value == "const"
        assert ConstraintType.ENUM.value == "enum"
        assert ConstraintType.MUTEX.value == "mutex"

    def test_property_constraint_creation(self):
        """Test PropertyConstraint dataclass."""
        constraint = PropertyConstraint(
            constraint_type=ConstraintType.FORBIDDEN,
            properties=['device_type'],
            message="device_type is forbidden"
        )
        assert constraint.constraint_type == ConstraintType.FORBIDDEN
        assert constraint.properties == ['device_type']
        assert constraint.message == "device_type is forbidden"
        assert constraint.expected_value is None

    def test_property_constraint_with_expected_value(self):
        """Test PropertyConstraint with expected_value."""
        constraint = PropertyConstraint(
            constraint_type=ConstraintType.CONST,
            properties=['device_type'],
            expected_value='memory'
        )
        assert constraint.expected_value == 'memory'

    def test_node_constraints_creation(self):
        """Test NodeConstraints dataclass."""
        constraints = NodeConstraints(
            node_pattern='/reserved-memory/*',
            constraints=[
                PropertyConstraint(ConstraintType.FORBIDDEN, ['device_type']),
            ],
            description="Test constraints"
        )
        assert constraints.node_pattern == '/reserved-memory/*'
        assert len(constraints.constraints) == 1
        assert constraints.description == "Test constraints"

    def test_constraints_loaded_from_schemas(self):
        """Test that constraints are loaded from dt-schema YAML files."""
        # Constraints should be loaded at module import time
        assert len(NODE_PROPERTY_CONSTRAINTS) > 0

        # Check for reserved-memory constraints (loaded from reserved-memory.yaml)
        resmem_found = False
        memory_found = False
        for name, nc in NODE_PROPERTY_CONSTRAINTS.items():
            if nc.node_pattern == '/reserved-memory/*':
                resmem_found = True
                # Should have at least forbidden and mutex constraints
                types = [c.constraint_type for c in nc.constraints]
                assert ConstraintType.FORBIDDEN in types
                assert ConstraintType.MUTEX in types
            if nc.node_pattern == '/memory@*':
                memory_found = True
                # Should have required and const constraints
                types = [c.constraint_type for c in nc.constraints]
                assert ConstraintType.REQUIRED in types
                assert ConstraintType.CONST in types

        assert resmem_found, "reserved-memory constraints not loaded"
        assert memory_found, "memory constraints not loaded"

    def test_load_constraints_function(self):
        """Test load_constraints_from_schemas function."""
        constraints = load_constraints_from_schemas()
        assert len(constraints) > 0
        # All values should be NodeConstraints
        for name, nc in constraints.items():
            assert isinstance(nc, NodeConstraints)
            assert nc.node_pattern is not None
            assert len(nc.constraints) > 0


class TestNodePatternMatching:
    """Tests for node pattern matching functions."""

    def test_exact_match(self):
        """Test exact path matching."""
        assert _node_matches_pattern('/reserved-memory', '/reserved-memory')
        assert not _node_matches_pattern('/reserved-memory', '/memory')

    def test_wildcard_child_match(self):
        """Test /parent/* pattern matching."""
        assert _node_matches_pattern('/reserved-memory/region1', '/reserved-memory/*')
        assert _node_matches_pattern('/reserved-memory/my-carveout', '/reserved-memory/*')
        assert not _node_matches_pattern('/reserved-memory', '/reserved-memory/*')
        assert not _node_matches_pattern('/other/region1', '/reserved-memory/*')

    def test_unit_address_wildcard(self):
        """Test /node@* pattern matching."""
        assert _node_matches_pattern('/memory@0', '/memory@*')
        assert _node_matches_pattern('/memory@80000000', '/memory@*')
        assert not _node_matches_pattern('/memory', '/memory@*')
        assert not _node_matches_pattern('/reserved-memory', '/memory@*')

    def test_get_matching_constraints(self):
        """Test getting constraints for a node path."""
        matches = _get_matching_constraints('/reserved-memory/my-region')
        assert len(matches) == 1
        assert matches[0].node_pattern == '/reserved-memory/*'

        matches = _get_matching_constraints('/memory@0')
        assert len(matches) == 1
        assert matches[0].node_pattern == '/memory@*'

        matches = _get_matching_constraints('/some/other/node')
        assert len(matches) == 0


class MockProp:
    """Mock property for testing."""
    def __init__(self, value):
        self.value = value


class MockNode:
    """Mock node for testing."""
    def __init__(self, abs_path, props=None):
        self.abs_path = abs_path
        self.name = abs_path.split('/')[-1]
        self._props = props or {}

    def __getitem__(self, key):
        if key in self._props:
            return MockProp(self._props[key])
        raise KeyError(key)


class MockTree:
    """Mock tree for testing."""
    def __init__(self, nodes):
        self._nodes = nodes

    def __iter__(self):
        return iter(self._nodes)

    def __getitem__(self, path):
        for node in self._nodes:
            if node.abs_path == path:
                return node
        raise KeyError(path)


class TestCheckForbiddenProperties:
    """Tests for check_forbidden_properties function."""

    def test_device_type_on_reserved_memory_child_fails(self):
        """device_type on reserved-memory child should fail (forbidden per dt-schema)."""
        tree = MockTree([
            MockNode('/reserved-memory/bad_region', {
                'device_type': 'memory',
                'reg': [0, 0x10000000, 0, 0x1000],
            }),
        ])

        results = check_forbidden_properties(tree)

        failed = [r for r in results if not r.passed]
        assert len(failed) == 1
        assert 'device_type' in failed[0].message
        assert failed[0].source_path == '/reserved-memory/bad_region'

    def test_no_device_type_on_reserved_memory_passes(self):
        """reserved-memory child without device_type should pass."""
        tree = MockTree([
            MockNode('/reserved-memory/good_region', {
                'reg': [0, 0x10000000, 0, 0x1000],
            }),
        ])

        results = check_forbidden_properties(tree)

        # Should only have the "passed" result
        assert all(r.passed for r in results)

    def test_multiple_forbidden_properties(self):
        """Test detecting multiple violations."""
        tree = MockTree([
            MockNode('/reserved-memory/region1', {
                'device_type': 'memory',
            }),
            MockNode('/reserved-memory/region2', {
                'device_type': 'memory',
            }),
        ])

        results = check_forbidden_properties(tree)

        failed = [r for r in results if not r.passed]
        assert len(failed) == 2


class TestCheckRequiredProperties:
    """Tests for check_required_properties function."""

    def test_memory_without_device_type_fails(self):
        """memory node without device_type should fail."""
        tree = MockTree([
            MockNode('/memory@0', {
                'reg': [0, 0x80000000, 0, 0x40000000],
                # Missing device_type
            }),
        ])

        results = check_required_properties(tree)

        failed = [r for r in results if not r.passed]
        assert len(failed) == 1
        assert 'device_type' in failed[0].message

    def test_memory_with_all_required_passes(self):
        """memory node with all required properties should pass."""
        tree = MockTree([
            MockNode('/memory@0', {
                'device_type': 'memory',
                'reg': [0, 0x80000000, 0, 0x40000000],
            }),
        ])

        results = check_required_properties(tree)

        assert all(r.passed for r in results)

    def test_memory_without_reg_fails(self):
        """memory node without reg should fail."""
        tree = MockTree([
            MockNode('/memory@0', {
                'device_type': 'memory',
                # Missing reg
            }),
        ])

        results = check_required_properties(tree)

        failed = [r for r in results if not r.passed]
        assert len(failed) == 1
        assert 'reg' in failed[0].message


class TestCheckPropertyValues:
    """Tests for check_property_values function."""

    def test_memory_device_type_wrong_value_fails(self):
        """memory node with wrong device_type value should fail."""
        tree = MockTree([
            MockNode('/memory@0', {
                'device_type': 'wrong',
                'reg': [0, 0x80000000],
            }),
        ])

        results = check_property_values(tree)

        failed = [r for r in results if not r.passed]
        assert len(failed) == 1
        assert 'memory' in failed[0].message or 'wrong' in failed[0].message

    def test_memory_device_type_correct_passes(self):
        """memory node with correct device_type value should pass."""
        tree = MockTree([
            MockNode('/memory@0', {
                'device_type': 'memory',
                'reg': [0, 0x80000000],
            }),
        ])

        results = check_property_values(tree)

        assert all(r.passed for r in results)

    def test_handles_list_values(self):
        """Test that list values like ['memory'] are handled."""
        tree = MockTree([
            MockNode('/memory@0', {
                'device_type': ['memory'],  # As a list
                'reg': [0, 0x80000000],
            }),
        ])

        results = check_property_values(tree)

        assert all(r.passed for r in results)


class TestCheckMutexProperties:
    """Tests for check_mutex_properties function."""

    def test_no_map_and_reusable_mutex_fails(self):
        """no-map and reusable together should fail."""
        tree = MockTree([
            MockNode('/reserved-memory/bad', {
                'no-map': True,
                'reusable': True,
                'reg': [0, 0x10000000, 0, 0x1000],
            }),
        ])

        results = check_mutex_properties(tree)

        failed = [r for r in results if not r.passed]
        assert len(failed) == 1
        assert 'no-map' in failed[0].message or 'reusable' in failed[0].message

    def test_no_map_alone_passes(self):
        """no-map alone should pass."""
        tree = MockTree([
            MockNode('/reserved-memory/good', {
                'no-map': True,
                'reg': [0, 0x10000000, 0, 0x1000],
            }),
        ])

        results = check_mutex_properties(tree)

        assert all(r.passed for r in results)

    def test_reusable_alone_passes(self):
        """reusable alone should pass."""
        tree = MockTree([
            MockNode('/reserved-memory/good', {
                'reusable': True,
                'reg': [0, 0x10000000, 0, 0x1000],
            }),
        ])

        results = check_mutex_properties(tree)

        assert all(r.passed for r in results)


class TestSchemaValidator:
    """Tests for SchemaValidator class."""

    def test_validator_registration(self):
        """Test that SchemaValidator is registered."""
        validator_class = ValidatorRegistry.get_validator('schema')
        assert validator_class is SchemaValidator

    def test_warning_flags_defined(self):
        """Test that warning flags are defined."""
        assert 'schema_forbidden_props' in SchemaValidator.WARNING_FLAGS
        assert 'schema_required_props' in SchemaValidator.WARNING_FLAGS
        assert 'schema_prop_values' in SchemaValidator.WARNING_FLAGS
        assert 'schema_mutex_props' in SchemaValidator.WARNING_FLAGS

    def test_meta_flags_defined(self):
        """Test that meta flags are defined."""
        assert 'schema_all' in SchemaValidator.META_FLAGS
        assert 'schema_reserved_memory' in SchemaValidator.META_FLAGS

        # schema_all should include all checks
        all_flags = SchemaValidator.META_FLAGS['schema_all']
        for flag in SchemaValidator.WARNING_FLAGS:
            assert flag in all_flags

    def test_check_registry_defined(self):
        """Test that CHECK_REGISTRY maps to functions."""
        for flag in SchemaValidator.WARNING_FLAGS:
            assert flag in SchemaValidator.CHECK_REGISTRY
            phase, func = SchemaValidator.CHECK_REGISTRY[flag]
            assert phase == ValidationPhase.POST_YAML
            assert callable(func)

    def test_is_enabled_with_specific_flag(self):
        """Test is_enabled with specific flag."""
        validator = SchemaValidator(warnings=['schema_forbidden_props'])
        assert validator.is_enabled()
        assert validator.is_check_enabled('schema_forbidden_props')
        assert not validator.is_check_enabled('schema_required_props')

    def test_is_enabled_with_meta_flag(self):
        """Test is_enabled with meta flag."""
        validator = SchemaValidator(warnings=['schema_all'])
        assert validator.is_enabled()
        assert validator.is_check_enabled('schema_forbidden_props')
        assert validator.is_check_enabled('schema_required_props')

    def test_run_phase_post_yaml(self):
        """Test running POST_YAML phase checks."""
        tree = MockTree([
            MockNode('/reserved-memory/bad', {
                'device_type': 'memory',  # Forbidden
            }),
        ])

        validator = SchemaValidator(warnings=['schema_forbidden_props'])
        results = validator.run_phase(ValidationPhase.POST_YAML, tree)

        assert len(results) > 0
        failed = [r for r in results if not r.passed]
        assert len(failed) == 1

    def test_run_phase_wrong_phase(self):
        """Test that wrong phase returns no results."""
        tree = MockTree([
            MockNode('/reserved-memory/bad', {
                'device_type': 'memory',
            }),
        ])

        validator = SchemaValidator(warnings=['schema_forbidden_props'])
        results = validator.run_phase(ValidationPhase.EARLY, tree)

        # POST_YAML checks should not run in EARLY phase
        assert len(results) == 0


class TestValidateSchemaFunction:
    """Tests for validate_schema convenience function."""

    def test_validate_schema_returns_error_count(self):
        """Test that validate_schema returns error count."""
        tree = MockTree([
            MockNode('/reserved-memory/bad', {
                'device_type': 'memory',
            }),
        ])

        # Mock lopper.log to prevent actual logging
        import lopper.log
        original_warning = lopper.log._warning

        warnings_logged = []
        lopper.log._warning = lambda msg: warnings_logged.append(msg)

        try:
            error_count = validate_schema(
                tree,
                ValidationPhase.POST_YAML,
                warnings=['schema_forbidden_props']
            )
            assert error_count == 1
            assert len(warnings_logged) == 1
        finally:
            lopper.log._warning = original_warning


class TestIntegrationScenarios:
    """Integration tests for realistic scenarios."""

    def test_reserved_memory_device_type_xen_failure(self):
        """Test the scenario where reserved-memory device_type caused Xen boot failure."""
        # This is the problematic tree structure from the bug report
        tree = MockTree([
            MockNode('/reserved-memory/openamp_carveout', {
                'device_type': 'memory',  # THIS IS THE BUG - should not be here
                'reg': [0, 0x78000000, 0, 0x8000000],
                'compatible': ['shared-dma-pool'],
            }),
        ])

        validator = SchemaValidator(warnings=['schema_reserved_memory'])
        results = validator.run_phase(ValidationPhase.POST_YAML, tree)

        failed = [r for r in results if not r.passed]
        assert len(failed) == 1
        assert 'device_type' in failed[0].message
        assert failed[0].source_path == '/reserved-memory/openamp_carveout'

    def test_valid_memory_and_reserved_memory(self):
        """Test a valid tree with correct memory and reserved-memory."""
        tree = MockTree([
            MockNode('/memory@0', {
                'device_type': 'memory',  # Required on memory nodes
                'reg': [0, 0x0, 0, 0x80000000],
            }),
            MockNode('/reserved-memory/carveout', {
                # No device_type - correct!
                'reg': [0, 0x78000000, 0, 0x1000000],
                'no-map': True,
            }),
        ])

        validator = SchemaValidator(warnings=['schema_all'])
        results = validator.run_phase(ValidationPhase.POST_YAML, tree)

        # All checks should pass
        assert all(r.passed for r in results)

    def test_mixed_violations(self):
        """Test tree with multiple different violations."""
        tree = MockTree([
            MockNode('/memory@0', {
                # Missing device_type - REQUIRED violation
                'reg': [0, 0x0, 0, 0x80000000],
            }),
            MockNode('/reserved-memory/bad1', {
                'device_type': 'memory',  # FORBIDDEN violation
            }),
            MockNode('/reserved-memory/bad2', {
                'no-map': True,
                'reusable': True,  # MUTEX violation
            }),
        ])

        validator = SchemaValidator(warnings=['schema_all'])
        results = validator.run_phase(ValidationPhase.POST_YAML, tree)

        failed = [r for r in results if not r.passed]
        # Should have at least 3 failures
        assert len(failed) >= 3

        # Check we got different types of failures
        check_names = {r.check_name for r in failed}
        assert 'schema_required_props' in check_names
        assert 'schema_forbidden_props' in check_names
        assert 'schema_mutex_props' in check_names
