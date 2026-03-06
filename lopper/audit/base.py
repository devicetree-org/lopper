#/*
# * Copyright (c) 2024,2025,2026 Xilinx Inc. All rights reserved.
# *
# * Author:
# *       Bruce Ashfield <bruce.ashfield@xilinx.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

"""
Lopper audit base module

This module provides the base classes and registry for the audit framework.
All validators should inherit from BaseValidator and register themselves.

Validation phases:
- EARLY: During tree load/resolve (basic property validation)
- POST_YAML: After YAML expansion (structural checks)
- POST_PROCESSING: After all processing complete (final consistency)
"""

from dataclasses import dataclass
from enum import Enum, auto
from typing import List, Optional, Dict, Type, Set
import lopper.log


class ValidationPhase(Enum):
    """Phases when validation checks should run."""
    EARLY = auto()           # During tree load/resolve
    POST_YAML = auto()       # After YAML expansion
    POST_PROCESSING = auto() # After all processing complete


@dataclass
class ValidationResult:
    """Result of a validation check.

    Attributes:
        check_name: Name of the check (e.g., 'memory_overlap')
        phase: Validation phase when this check ran
        passed: True if validation passed, False if issues found
        message: Human-readable description of the result
        source_path: Device tree path related to the issue (if applicable)
        details: Additional details about the issue
    """
    check_name: str
    phase: ValidationPhase
    passed: bool
    message: str
    source_path: Optional[str] = None
    details: Optional[dict] = None


class BaseValidator:
    """Base class for all audit validators.

    Subclasses should:
    1. Set CATEGORY class attribute (e.g., "memory", "interrupt", "clock")
    2. Set WARNING_FLAGS class attribute with list of -W flags handled
    3. Implement run_phase() method
    4. Call ValidatorRegistry.register() to register themselves
    """

    CATEGORY: str = "base"
    WARNING_FLAGS: List[str] = []
    META_FLAGS: Dict[str, List[str]] = {}  # e.g., {"memory_all": ["memory_cells", ...]}

    def __init__(self, warnings: Optional[List[str]] = None, werror: bool = False):
        """Initialize the validator.

        Args:
            warnings: List of warning flags enabled
            werror: If True, treat warnings as errors
        """
        self.warnings = set()
        self.werror = werror
        self.results: List[ValidationResult] = []

        # Expand warning flags
        if warnings:
            for flag in warnings:
                if flag in self.META_FLAGS:
                    self.warnings.update(self.META_FLAGS[flag])
                elif flag in self.WARNING_FLAGS or flag == 'all':
                    self.warnings.add(flag)

    def is_enabled(self) -> bool:
        """Check if this validator has any checks enabled.

        Returns:
            True if any warning flags for this validator are enabled
        """
        if 'all' in self.warnings:
            return True
        return bool(self.warnings.intersection(self.WARNING_FLAGS))

    def is_check_enabled(self, check_name: str) -> bool:
        """Check if a specific validation check is enabled.

        Args:
            check_name: Name of the check

        Returns:
            True if the check is enabled
        """
        return check_name in self.warnings or 'all' in self.warnings

    def run_phase(
        self,
        phase: ValidationPhase,
        tree,
        **kwargs
    ) -> List[ValidationResult]:
        """Run all enabled checks for a specific phase.

        Subclasses should override this method.

        Args:
            phase: The validation phase to run
            tree: LopperTree to validate
            **kwargs: Additional arguments (domain_node, etc.)

        Returns:
            List of ValidationResult objects from this phase
        """
        return []

    def report(self) -> int:
        """Report all validation results and return error count.

        Returns:
            Number of failed checks (errors)
        """
        error_count = 0

        for result in self.results:
            if not result.passed:
                error_count += 1
                msg = f"{result.check_name}: {result.message}"
                if self.werror:
                    lopper.log._error(msg)
                else:
                    lopper.log._warning(msg)

        return error_count

    def clear(self):
        """Clear all collected results."""
        self.results = []


class ValidatorRegistry:
    """Registry for audit validators.

    Validators register themselves and the registry handles
    running all enabled validators at each phase.
    """

    _validators: Dict[str, Type[BaseValidator]] = {}

    @classmethod
    def register(cls, validator_class: Type[BaseValidator]) -> Type[BaseValidator]:
        """Register a validator class.

        Can be used as a decorator:
            @ValidatorRegistry.register
            class MyValidator(BaseValidator):
                ...

        Args:
            validator_class: The validator class to register

        Returns:
            The validator class (for decorator use)
        """
        cls._validators[validator_class.CATEGORY] = validator_class
        return validator_class

    @classmethod
    def get_validator(cls, category: str) -> Optional[Type[BaseValidator]]:
        """Get a validator class by category.

        Args:
            category: The validator category

        Returns:
            The validator class, or None if not found
        """
        return cls._validators.get(category)

    @classmethod
    def get_all_validators(cls) -> Dict[str, Type[BaseValidator]]:
        """Get all registered validators.

        Returns:
            Dictionary of category -> validator class
        """
        return cls._validators.copy()

    @classmethod
    def get_all_warning_flags(cls) -> Set[str]:
        """Get all warning flags from all registered validators.

        Returns:
            Set of all warning flag strings
        """
        flags = set()
        for validator_class in cls._validators.values():
            flags.update(validator_class.WARNING_FLAGS)
            for meta_flags in validator_class.META_FLAGS.values():
                flags.update(meta_flags)
            flags.update(validator_class.META_FLAGS.keys())
        return flags

    @classmethod
    def run_phase(
        cls,
        phase: ValidationPhase,
        tree,
        warnings: List[str],
        werror: bool = False,
        **kwargs
    ) -> int:
        """Run all enabled validators for a specific phase.

        Args:
            phase: The validation phase to run
            tree: LopperTree to validate
            warnings: List of warning flags enabled
            werror: If True, treat warnings as errors
            **kwargs: Additional arguments passed to validators

        Returns:
            Total number of errors found across all validators
        """
        total_errors = 0

        for category, validator_class in cls._validators.items():
            validator = validator_class(warnings=warnings, werror=werror)

            if not validator.is_enabled():
                continue

            validator.run_phase(phase, tree, **kwargs)
            total_errors += validator.report()

        return total_errors


def run_audit_phase(
    phase: ValidationPhase,
    tree,
    warnings: List[str],
    werror: bool = False,
    **kwargs
) -> int:
    """Convenience function to run all validators for a phase.

    This is the main entry point for running audits from the pipeline.

    Args:
        phase: The validation phase to run
        tree: LopperTree to validate
        warnings: List of warning flags enabled
        werror: If True, treat warnings as errors
        **kwargs: Additional arguments (domain_node, etc.)

    Returns:
        Total number of errors found
    """
    return ValidatorRegistry.run_phase(phase, tree, warnings, werror, **kwargs)
