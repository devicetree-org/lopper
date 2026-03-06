"""
Tests for lopper/audit/base.py - base classes for the audit framework.

This module tests the generic audit framework:
- ValidationPhase: Enum for validation phases
- ValidationResult: Dataclass for validation results
- BaseValidator: Base class for all validators
- ValidatorRegistry: Registry for validator classes
- run_audit_phase(): Convenience function for running validators
"""

import pytest
from unittest.mock import patch, MagicMock

from lopper.audit.base import (
    ValidationPhase,
    ValidationResult,
    BaseValidator,
    ValidatorRegistry,
    run_audit_phase,
)


class TestValidationPhase:
    """Tests for ValidationPhase enum."""

    def test_early_phase_exists(self):
        """Test EARLY phase is defined."""
        assert hasattr(ValidationPhase, 'EARLY')
        assert ValidationPhase.EARLY is not None

    def test_post_yaml_phase_exists(self):
        """Test POST_YAML phase is defined."""
        assert hasattr(ValidationPhase, 'POST_YAML')
        assert ValidationPhase.POST_YAML is not None

    def test_post_processing_phase_exists(self):
        """Test POST_PROCESSING phase is defined."""
        assert hasattr(ValidationPhase, 'POST_PROCESSING')
        assert ValidationPhase.POST_PROCESSING is not None

    def test_phases_are_distinct(self):
        """Test that all phases are distinct values."""
        phases = [ValidationPhase.EARLY, ValidationPhase.POST_YAML, ValidationPhase.POST_PROCESSING]
        assert len(phases) == len(set(phases))


class TestValidationResult:
    """Tests for ValidationResult dataclass."""

    def test_basic_creation(self):
        """Test creating a basic ValidationResult."""
        result = ValidationResult(
            check_name="test_check",
            phase=ValidationPhase.EARLY,
            passed=True,
            message="Test passed"
        )
        assert result.check_name == "test_check"
        assert result.phase == ValidationPhase.EARLY
        assert result.passed is True
        assert result.message == "Test passed"
        assert result.source_path is None
        assert result.details is None

    def test_with_source_path(self):
        """Test creating ValidationResult with source path."""
        result = ValidationResult(
            check_name="test_check",
            phase=ValidationPhase.POST_YAML,
            passed=False,
            message="Test failed",
            source_path="/some/node"
        )
        assert result.source_path == "/some/node"

    def test_with_details(self):
        """Test creating ValidationResult with details."""
        details = {"overlap_start": 0x1000, "overlap_size": 0x500}
        result = ValidationResult(
            check_name="memory_overlap",
            phase=ValidationPhase.POST_YAML,
            passed=False,
            message="Overlap detected",
            details=details
        )
        assert result.details == details
        assert result.details["overlap_start"] == 0x1000


class TestBaseValidator:
    """Tests for BaseValidator base class."""

    def test_default_category(self):
        """Test default category is 'base'."""
        validator = BaseValidator()
        assert validator.CATEGORY == "base"

    def test_default_warning_flags(self):
        """Test default warning flags is empty list."""
        assert BaseValidator.WARNING_FLAGS == []

    def test_default_meta_flags(self):
        """Test default meta flags is empty dict."""
        assert BaseValidator.META_FLAGS == {}

    def test_initialization_with_warnings(self):
        """Test initialization with warning flags."""

        class TestValidator(BaseValidator):
            WARNING_FLAGS = ['test_warn1', 'test_warn2']

        validator = TestValidator(warnings=['test_warn1'])
        assert 'test_warn1' in validator.warnings
        assert 'test_warn2' not in validator.warnings

    def test_meta_flag_expansion(self):
        """Test meta-flag expansion to individual warnings."""

        class TestValidator(BaseValidator):
            WARNING_FLAGS = ['check1', 'check2', 'check3']
            META_FLAGS = {'all_checks': ['check1', 'check2', 'check3']}

        validator = TestValidator(warnings=['all_checks'])
        assert 'check1' in validator.warnings
        assert 'check2' in validator.warnings
        assert 'check3' in validator.warnings

    def test_all_flag_enabled(self):
        """Test that 'all' flag enables everything."""

        class TestValidator(BaseValidator):
            WARNING_FLAGS = ['check1', 'check2']

        validator = TestValidator(warnings=['all'])
        assert validator.is_enabled()
        assert validator.is_check_enabled('check1')
        assert validator.is_check_enabled('check2')
        assert validator.is_check_enabled('any_check')  # 'all' enables everything

    def test_is_enabled_false_when_no_warnings(self):
        """Test is_enabled returns False when no warnings enabled."""

        class TestValidator(BaseValidator):
            WARNING_FLAGS = ['check1', 'check2']

        validator = TestValidator(warnings=[])
        assert not validator.is_enabled()

    def test_is_enabled_true_when_warning_set(self):
        """Test is_enabled returns True when warnings are set."""

        class TestValidator(BaseValidator):
            WARNING_FLAGS = ['check1', 'check2']

        validator = TestValidator(warnings=['check1'])
        assert validator.is_enabled()

    def test_werror_flag(self):
        """Test werror flag is stored correctly."""
        validator = BaseValidator(werror=True)
        assert validator.werror is True

        validator2 = BaseValidator(werror=False)
        assert validator2.werror is False

    def test_run_phase_returns_empty_list(self):
        """Test default run_phase returns empty list."""
        validator = BaseValidator()
        result = validator.run_phase(ValidationPhase.EARLY, None)
        assert result == []

    def test_clear_results(self):
        """Test clear method empties results."""
        validator = BaseValidator()
        validator.results = [
            ValidationResult("test", ValidationPhase.EARLY, True, "msg")
        ]
        validator.clear()
        assert validator.results == []


class TestValidatorRegistry:
    """Tests for ValidatorRegistry."""

    def test_register_decorator(self):
        """Test that register can be used as decorator."""
        # Clear any existing validators with same category
        original_validators = ValidatorRegistry._validators.copy()

        try:
            @ValidatorRegistry.register
            class TestValidator(BaseValidator):
                CATEGORY = "test_category_unique"
                WARNING_FLAGS = ['test_warn']

            assert "test_category_unique" in ValidatorRegistry._validators
            assert ValidatorRegistry._validators["test_category_unique"] == TestValidator
        finally:
            # Restore original state
            ValidatorRegistry._validators = original_validators

    def test_get_validator(self):
        """Test get_validator returns correct validator."""
        original_validators = ValidatorRegistry._validators.copy()

        try:
            @ValidatorRegistry.register
            class TestValidator2(BaseValidator):
                CATEGORY = "test_get"
                WARNING_FLAGS = ['warn1']

            validator_class = ValidatorRegistry.get_validator("test_get")
            assert validator_class == TestValidator2
        finally:
            ValidatorRegistry._validators = original_validators

    def test_get_validator_not_found(self):
        """Test get_validator returns None for unknown category."""
        result = ValidatorRegistry.get_validator("nonexistent_category_xyz")
        assert result is None

    def test_get_all_validators(self):
        """Test get_all_validators returns copy of registry."""
        validators = ValidatorRegistry.get_all_validators()
        # Should contain at least the memory validator
        assert isinstance(validators, dict)

    def test_get_all_warning_flags(self):
        """Test get_all_warning_flags collects all flags."""
        flags = ValidatorRegistry.get_all_warning_flags()
        assert isinstance(flags, set)
        # Should contain memory validator flags
        assert 'memory_cells' in flags
        assert 'memory_overlap' in flags

    def test_memory_validator_is_registered(self):
        """Test that MemoryValidator is registered with 'memory' category."""
        validator_class = ValidatorRegistry.get_validator("memory")
        assert validator_class is not None
        assert validator_class.CATEGORY == "memory"


class TestRunAuditPhase:
    """Tests for run_audit_phase convenience function."""

    def test_returns_zero_with_no_warnings(self):
        """Test returns 0 when no warnings enabled."""
        tree = MagicMock()
        result = run_audit_phase(ValidationPhase.EARLY, tree, [], werror=False)
        assert result == 0

    def test_runs_enabled_validators(self):
        """Test that enabled validators are run."""
        # Create a minimal tree mock
        tree = MagicMock()
        tree.__iter__ = MagicMock(return_value=iter([]))
        tree.__getitem__ = MagicMock(side_effect=KeyError)

        # Run with memory_cells warning
        result = run_audit_phase(
            ValidationPhase.EARLY,
            tree,
            ['memory_cells'],
            werror=False
        )
        # Should return error count (could be > 0 if missing #address-cells)
        assert isinstance(result, int)

    def test_phase_filtering(self):
        """Test that only validators for the specified phase run."""
        tree = MagicMock()
        tree.__iter__ = MagicMock(return_value=iter([]))
        tree.__getitem__ = MagicMock(side_effect=KeyError)

        # POST_YAML phase should run memory_overlap check
        result = run_audit_phase(
            ValidationPhase.POST_YAML,
            tree,
            ['memory_overlap'],
            werror=False
        )
        assert isinstance(result, int)


class TestBaseValidatorReport:
    """Tests for BaseValidator.report() method."""

    def test_report_returns_zero_for_passing_results(self):
        """Test report returns 0 when all results pass."""
        validator = BaseValidator()
        validator.results = [
            ValidationResult("check1", ValidationPhase.EARLY, True, "Passed"),
            ValidationResult("check2", ValidationPhase.EARLY, True, "Passed"),
        ]
        assert validator.report() == 0

    def test_report_counts_failures(self):
        """Test report counts failed results."""
        validator = BaseValidator()
        validator.results = [
            ValidationResult("check1", ValidationPhase.EARLY, True, "Passed"),
            ValidationResult("check2", ValidationPhase.EARLY, False, "Failed"),
            ValidationResult("check3", ValidationPhase.EARLY, False, "Failed"),
        ]
        assert validator.report() == 2

    @patch('lopper.log._warning')
    def test_report_logs_warnings(self, mock_warning):
        """Test report logs warnings for failed checks."""
        validator = BaseValidator(werror=False)
        validator.results = [
            ValidationResult("test_check", ValidationPhase.EARLY, False, "Test failure"),
        ]
        validator.report()
        mock_warning.assert_called_once()
        assert "test_check" in str(mock_warning.call_args)

    @patch('lopper.log._error')
    def test_report_logs_errors_with_werror(self, mock_error):
        """Test report logs errors when werror=True."""
        validator = BaseValidator(werror=True)
        validator.results = [
            ValidationResult("test_check", ValidationPhase.EARLY, False, "Test failure"),
        ]
        validator.report()
        mock_error.assert_called_once()
        assert "test_check" in str(mock_error.call_args)
