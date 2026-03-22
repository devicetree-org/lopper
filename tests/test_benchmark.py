"""
Performance benchmark tests for Lopper.

These tests measure relative performance to catch regressions.
Instead of using absolute times (which vary by machine), we measure
operations relative to a baseline operation on the same machine.

The approach:
1. Run a calibration operation (baseline) to measure machine speed
2. Run the operation under test
3. Compare the ratio to expected bounds

This way, a fast machine and slow machine will both pass as long as
the relative performance is within expected bounds.
"""

import time
import pytest
from pathlib import Path

from lopper import Lopper, LopperSDT
from lopper.tree import LopperTree, LopperNode, LopperProp


class PerformanceBaseline:
    """
    Calibration helper to normalize performance across different machines.

    Runs a fixed workload and returns the time taken, which can be used
    to normalize other measurements.
    """

    @staticmethod
    def calibrate(iterations=1000):
        """
        Run a calibration workload.

        Returns the time in seconds for the calibration workload.
        This is used to normalize other measurements.
        """
        # Simple workload: dictionary operations (similar to what lopper does)
        start = time.perf_counter()
        d = {}
        for i in range(iterations):
            d[f"key_{i}"] = {"value": i, "nested": {"a": 1, "b": 2}}
            _ = d.get(f"key_{i // 2}")
            if i > 0:
                d[f"key_{i}"]["updated"] = True
        elapsed = time.perf_counter() - start
        return elapsed

    @staticmethod
    def get_machine_factor():
        """
        Returns a factor representing machine speed.

        Higher = slower machine, Lower = faster machine.
        Normalized so a "reference" machine returns ~1.0
        """
        # Reference time from a typical dev machine (adjust as needed)
        # This was measured on a reasonable desktop
        REFERENCE_TIME = 0.005  # 5ms for 1000 iterations

        actual_time = PerformanceBaseline.calibrate()
        return actual_time / REFERENCE_TIME


class TestPhandlePerformance:
    """
    Performance tests for phandle operations.

    These tests verify that phandle assignment doesn't regress.
    Without the fast path optimization, there was ~4x slowdown when
    phandle_set() was called on every assignment.
    """

    @pytest.fixture
    def machine_factor(self):
        """Get machine speed factor for this test run."""
        return PerformanceBaseline.get_machine_factor()

    def test_phandle_assignment_performance(self, machine_factor):
        """
        Test that bulk phandle assignments complete in reasonable time.

        This test measures ONLY the phandle assignment overhead, not node
        creation (which is slow due to LopperNode.__init__).

        This should catch regressions where the phandle
        fast path is accidentally removed or bypassed.

        Expected: With the fast path, phandle assignments are nearly free.
        Without it, they would take significant time.
        """
        tree = LopperTree()
        tree.warnings = []  # Ensure fast path is used

        num_nodes = 500

        # Pre-create nodes (not timed - node creation is slow)
        nodes = []
        for i in range(num_nodes):
            node = LopperNode(number=i, name=f"node{i}")
            node.tree = tree
            nodes.append(node)

        # Time ONLY the phandle assignments
        start = time.perf_counter()
        for i, node in enumerate(nodes):
            node.phandle = i + 1  # This triggers __setattr__
        elapsed = time.perf_counter() - start

        # Normalize by machine speed
        normalized_time = elapsed / machine_factor

        # Expected performance bounds (in normalized seconds)
        # With fast path: phandle assignment should be very fast (< 0.1s for 500)
        # Without fast path: would be much slower
        MAX_NORMALIZED_TIME = 0.5  # Allow margin for variance

        assert normalized_time < MAX_NORMALIZED_TIME, (
            f"Phandle assignment too slow: {elapsed:.3f}s actual, "
            f"{normalized_time:.3f}s normalized (machine_factor={machine_factor:.2f}). "
            f"Expected < {MAX_NORMALIZED_TIME}s normalized. "
            f"This may indicate a regression in the phandle fast path."
        )

    def test_phandle_with_warnings_similar_performance(self, machine_factor):
        """
        Verify that phandle assignment performance is similar with or without warnings.

        With lazy duplicate detection (checking at resolve() time instead of
        on every assignment), the warning path should have similar performance
        to the fast path. This test verifies that optimization is working.
        """
        num_nodes = 100

        # Pre-create nodes for path without warnings
        tree_no_warn = LopperTree()
        tree_no_warn.warnings = []
        no_warn_nodes = []
        for i in range(num_nodes):
            node = LopperNode(number=i, name=f"nowarn{i}")
            node.tree = tree_no_warn
            no_warn_nodes.append(node)

        # Pre-create nodes for path with warnings
        tree_with_warn = LopperTree()
        tree_with_warn.warnings = ["duplicate_phandle"]
        with_warn_nodes = []
        for i in range(num_nodes):
            node = LopperNode(number=i + num_nodes, name=f"warn{i}")
            node.tree = tree_with_warn
            with_warn_nodes.append(node)

        # Time path without warnings
        start = time.perf_counter()
        for i, node in enumerate(no_warn_nodes):
            node.phandle = i + 1
        no_warn_time = time.perf_counter() - start

        # Time path with warnings
        start = time.perf_counter()
        for i, node in enumerate(with_warn_nodes):
            node.phandle = i + num_nodes + 1
        with_warn_time = time.perf_counter() - start

        # Log the ratio for informational purposes
        if no_warn_time > 0.0001:  # Avoid division issues
            ratio = with_warn_time / no_warn_time
            print(f"\nPhandle performance ratio (with_warnings/no_warnings): {ratio:.2f}x")
            print(f"  No warnings:   {no_warn_time*1000:.3f}ms for {num_nodes} nodes")
            print(f"  With warnings: {with_warn_time*1000:.3f}ms for {num_nodes} nodes")

            # With lazy duplicate detection, both paths should be similar.
            # Allow up to 3x difference to account for measurement noise.
            # If ratio > 3, the lazy detection optimization may be broken.
            assert ratio < 3.0, (
                f"Warning path is much slower than no-warning path. "
                f"Ratio {ratio:.2f}x suggests lazy duplicate detection may not be working."
            )


class TestTreeLoadPerformance:
    """Performance tests for tree loading operations."""

    @pytest.fixture
    def machine_factor(self):
        return PerformanceBaseline.get_machine_factor()

    def test_tree_load_performance(self, compiled_fdt, machine_factor):
        """
        Test that tree loading completes in reasonable time.

        This is a baseline test for overall tree loading performance.
        """
        # Export once (not timed)
        export_data = Lopper.export(compiled_fdt)

        # Measure load time
        iterations = 5
        start = time.perf_counter()
        for _ in range(iterations):
            tree = LopperTree()
            tree.load(export_data)
        elapsed = time.perf_counter() - start

        avg_time = elapsed / iterations
        normalized_time = avg_time / machine_factor

        # Tree load should be fast
        MAX_NORMALIZED_TIME = 2.0  # Allow for complex trees

        assert normalized_time < MAX_NORMALIZED_TIME, (
            f"Tree load too slow: {avg_time:.3f}s avg actual, "
            f"{normalized_time:.3f}s normalized. "
            f"Expected < {MAX_NORMALIZED_TIME}s normalized."
        )


class TestNodeIterationPerformance:
    """Performance tests for node iteration."""

    @pytest.fixture
    def machine_factor(self):
        return PerformanceBaseline.get_machine_factor()

    def test_tree_iteration_performance(self, lopper_tree, machine_factor):
        """
        Test that iterating over all nodes is fast.
        """
        iterations = 10

        start = time.perf_counter()
        for _ in range(iterations):
            count = 0
            for node in lopper_tree:
                count += 1
                # Access some properties to simulate real work
                _ = node.name
                _ = node.phandle
                _ = node.abs_path
        elapsed = time.perf_counter() - start

        avg_time = elapsed / iterations
        normalized_time = avg_time / machine_factor

        MAX_NORMALIZED_TIME = 0.5

        assert normalized_time < MAX_NORMALIZED_TIME, (
            f"Tree iteration too slow: {avg_time:.3f}s avg actual, "
            f"{normalized_time:.3f}s normalized."
        )


# Performance baseline data for tracking over time
# This can be extended to store historical data
PERFORMANCE_BASELINES = {
    "phandle_assignment": {
        "description": "500 phandle assignments (fast path)",
        "max_normalized_seconds": 1.0,
        "notes": "Fast path optimization for phandle assignment"
    },
    "tree_load": {
        "description": "Load tree from export data",
        "max_normalized_seconds": 2.0,
        "notes": "Baseline tree loading"
    },
    "tree_iteration": {
        "description": "Full tree iteration with property access",
        "max_normalized_seconds": 0.5,
        "notes": "Basic iteration performance"
    }
}
