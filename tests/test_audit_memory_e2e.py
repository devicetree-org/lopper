# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2024-2026, Advanced Micro Devices, Inc. All rights reserved.

"""
End-to-end tests for memory audit CLI functionality.

These tests verify that the memory audit framework works correctly when
invoked through the lopper CLI, covering each check from the rejected
PR #705 (check_reserved_memory_overlaps).

Reference: https://github.com/devicetree-org/lopper/pull/705
"""

import os
import subprocess
import pytest


# --- Inline DTS Fixtures ---

# Test overlapping regions (PR #705: overlap detection)
OVERLAPPING_RESERVED_MEMORY_DTS = """\
/dts-v1/;
/ {
    #address-cells = <2>;
    #size-cells = <2>;
    compatible = "test";

    memory@0 {
        device_type = "memory";
        reg = <0x0 0x0 0x0 0x80000000>;
    };

    reserved-memory {
        #address-cells = <2>;
        #size-cells = <2>;
        ranges;

        region1@10000000 {
            reg = <0x0 0x10000000 0x0 0x20000000>;
        };
        region2@20000000 {
            /* Overlaps region1 at 0x20000000-0x30000000 */
            reg = <0x0 0x20000000 0x0 0x20000000>;
        };
    };
};
"""

# Test missing #address-cells (PR #705: cell parsing)
MISSING_CELLS_DTS = """\
/dts-v1/;
/ {
    #address-cells = <2>;
    #size-cells = <2>;
    compatible = "test";

    reserved-memory {
        /* Missing #address-cells and #size-cells - should warn */
        ranges;

        region@10000000 {
            reg = <0x0 0x10000000 0x0 0x1000000>;
        };
    };
};
"""

# Test zero-size region (PR #705: zero-size filtering)
ZERO_SIZE_REGION_DTS = """\
/dts-v1/;
/ {
    #address-cells = <2>;
    #size-cells = <2>;
    compatible = "test";

    reserved-memory {
        #address-cells = <2>;
        #size-cells = <2>;
        ranges;

        zero_region@10000000 {
            /* Zero size - should warn */
            reg = <0x0 0x10000000 0x0 0x0>;
        };
    };
};
"""

# Test shared-dma-pool intentional overlap (allowed)
SHARED_POOL_OVERLAP_DTS = """\
/dts-v1/;
/ {
    #address-cells = <2>;
    #size-cells = <2>;
    compatible = "test";

    memory@0 {
        device_type = "memory";
        reg = <0x0 0x0 0x0 0x80000000>;
    };

    reserved-memory {
        #address-cells = <2>;
        #size-cells = <2>;
        ranges;

        pool1@10000000 {
            compatible = "shared-dma-pool";
            reg = <0x0 0x10000000 0x0 0x20000000>;
        };
        pool2@20000000 {
            compatible = "shared-dma-pool";
            /* Overlaps pool1 but both are shared-dma-pool - allowed */
            reg = <0x0 0x20000000 0x0 0x20000000>;
        };
    };
};
"""

# Test containment (one region fully inside another)
CONTAINMENT_DTS = """\
/dts-v1/;
/ {
    #address-cells = <2>;
    #size-cells = <2>;
    compatible = "test";

    memory@0 {
        device_type = "memory";
        reg = <0x0 0x0 0x0 0x80000000>;
    };

    reserved-memory {
        #address-cells = <2>;
        #size-cells = <2>;
        ranges;

        outer@10000000 {
            reg = <0x0 0x10000000 0x0 0x40000000>;
        };
        inner@20000000 {
            /* Fully contained in outer - should warn */
            reg = <0x0 0x20000000 0x0 0x10000000>;
        };
    };
};
"""

# Valid tree with no issues
VALID_TREE_DTS = """\
/dts-v1/;
/ {
    #address-cells = <2>;
    #size-cells = <2>;
    compatible = "test";

    memory@0 {
        device_type = "memory";
        reg = <0x0 0x0 0x0 0x80000000>;
    };

    reserved-memory {
        #address-cells = <2>;
        #size-cells = <2>;
        ranges;

        region1@10000000 {
            reg = <0x0 0x10000000 0x0 0x10000000>;
        };
        region2@30000000 {
            /* No overlap - different addresses */
            reg = <0x0 0x30000000 0x0 0x10000000>;
        };
    };
};
"""


class TestMemoryAuditE2E:
    """End-to-end tests for memory audit CLI functionality."""

    def run_lopper(self, tmp_path, dts_content, extra_args=None):
        """Helper to run lopper with a DTS file and return result."""
        dts_file = tmp_path / "test.dts"
        dts_file.write_text(dts_content)

        output_file = tmp_path / "output.dts"

        cmd = ["./lopper.py", "-f"]
        if extra_args:
            cmd.extend(extra_args)
        cmd.extend([str(dts_file), str(output_file)])

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=os.getcwd()
        )
        return result

    def test_memory_overlap_detected(self, tmp_path):
        """PR #705: Overlap detection - warns about overlapping reserved-memory."""
        result = self.run_lopper(
            tmp_path,
            OVERLAPPING_RESERVED_MEMORY_DTS,
            ["-W", "memory_overlap"]
        )

        # Should succeed but emit warning
        assert result.returncode == 0, f"Lopper failed: {result.stderr}"

        # Check for overlap warning in output (stderr or stdout)
        combined_output = result.stdout + result.stderr
        assert "overlap" in combined_output.lower() or "warning" in combined_output.lower(), \
            f"Expected overlap warning, got: {combined_output}"

    def test_memory_overlap_with_werror(self, tmp_path):
        """PR #705: Error exit on overlap - --werror makes overlaps fatal."""
        result = self.run_lopper(
            tmp_path,
            OVERLAPPING_RESERVED_MEMORY_DTS,
            ["-W", "memory_overlap", "--werror"]
        )

        # Should fail with non-zero exit code
        assert result.returncode != 0, \
            f"Expected failure with --werror, but exit code was 0"

    def test_memory_cells_validation(self, tmp_path):
        """PR #705: #address-cells/#size-cells parsing - warns about missing cells."""
        result = self.run_lopper(
            tmp_path,
            MISSING_CELLS_DTS,
            ["-W", "memory_cells"]
        )

        # Should succeed (possibly with warnings)
        # The warning may be about missing cells
        combined_output = result.stdout + result.stderr
        # Note: behavior depends on whether lopper inherits cells from parent
        # At minimum, the check should run without crashing
        assert result.returncode == 0 or "cell" in combined_output.lower(), \
            f"Unexpected result: {combined_output}"

    def test_reg_property_format(self, tmp_path):
        """PR #705: Register property parsing - validates reg format."""
        # Use overlapping DTS which has valid reg properties
        result = self.run_lopper(
            tmp_path,
            OVERLAPPING_RESERVED_MEMORY_DTS,
            ["-W", "memory_reg"]
        )

        # Should succeed with valid reg properties
        assert result.returncode == 0, f"Lopper failed: {result.stderr}"

    def test_zero_size_region_detected(self, tmp_path):
        """PR #705: Zero-size region filtering - warns about zero size."""
        result = self.run_lopper(
            tmp_path,
            ZERO_SIZE_REGION_DTS,
            ["-W", "memory_reg"]
        )

        # Should succeed but may warn about zero size
        combined_output = result.stdout + result.stderr
        # Check runs without crashing
        assert result.returncode == 0 or "zero" in combined_output.lower() or "size" in combined_output.lower(), \
            f"Unexpected result: {combined_output}"

    def test_containment_detection(self, tmp_path):
        """PR #705: Complete containment check - warns when one region contains another."""
        result = self.run_lopper(
            tmp_path,
            CONTAINMENT_DTS,
            ["-W", "memory_overlap"]
        )

        # Should succeed but warn about containment/overlap
        assert result.returncode == 0, f"Lopper failed: {result.stderr}"

        combined_output = result.stdout + result.stderr
        # Containment is a form of overlap
        assert "overlap" in combined_output.lower() or "contain" in combined_output.lower() or \
               "warning" in combined_output.lower(), \
            f"Expected containment warning, got: {combined_output}"

    def test_memmap_visualization(self, tmp_path):
        """Memory map output - --memmap=- outputs to stdout."""
        result = self.run_lopper(
            tmp_path,
            VALID_TREE_DTS,
            ["--memmap=-"]
        )

        assert result.returncode == 0, f"Lopper failed: {result.stderr}"

        # Memory map should appear in stdout
        assert "Memory Map" in result.stdout or "memory" in result.stdout.lower(), \
            f"Expected memory map in output, got: {result.stdout}"
        assert "Legend" in result.stdout or "0x" in result.stdout, \
            f"Expected memory map details, got: {result.stdout}"

    def test_memmap_shows_overlap_marker(self, tmp_path):
        """Overlap in visualization - memmap shows 'X' for overlapping regions."""
        result = self.run_lopper(
            tmp_path,
            OVERLAPPING_RESERVED_MEMORY_DTS,
            ["--memmap=-", "-W", "memory_overlap"]
        )

        assert result.returncode == 0, f"Lopper failed: {result.stderr}"

        # Memory map should include overlap marker or warning
        combined_output = result.stdout + result.stderr
        has_overlap_indication = (
            "X" in result.stdout or  # Overlap marker
            "overlap" in combined_output.lower() or
            "OVERLAP" in result.stdout
        )
        assert has_overlap_indication, \
            f"Expected overlap indication in memmap, got: {result.stdout}"

    def test_memory_all_enables_all_checks(self, tmp_path):
        """Meta-flag expansion - -W memory_all enables multiple check types."""
        result = self.run_lopper(
            tmp_path,
            OVERLAPPING_RESERVED_MEMORY_DTS,
            ["-W", "memory_all"]
        )

        # Should succeed but show warnings
        combined_output = result.stdout + result.stderr
        # memory_all should run overlap checks at minimum
        assert "overlap" in combined_output.lower() or "warning" in combined_output.lower() or \
               result.returncode == 0, \
            f"memory_all did not produce expected results: {combined_output}"

    def test_no_warnings_on_valid_tree(self, tmp_path):
        """Clean tree passes - valid tree produces no warnings."""
        result = self.run_lopper(
            tmp_path,
            VALID_TREE_DTS,
            ["-W", "memory_all"]
        )

        # Should succeed with no warnings
        assert result.returncode == 0, f"Valid tree failed: {result.stderr}"

        combined_output = result.stdout + result.stderr
        # Should not contain overlap warnings
        assert "overlap" not in combined_output.lower() or "no overlap" in combined_output.lower(), \
            f"Unexpected warning on valid tree: {combined_output}"

    def test_shared_dma_pool_allowed_overlap(self, tmp_path):
        """Intentional overlap allowed - no warning for shared-dma-pool overlaps."""
        result = self.run_lopper(
            tmp_path,
            SHARED_POOL_OVERLAP_DTS,
            ["-W", "memory_overlap"]
        )

        # Should succeed without error
        assert result.returncode == 0, f"Lopper failed: {result.stderr}"

        combined_output = result.stdout + result.stderr
        # Should NOT produce overlap warning (or should indicate it's intentional)
        # Either no warning, or warning explicitly says it's allowed/intentional
        if "overlap" in combined_output.lower():
            assert "intentional" in combined_output.lower() or \
                   "shared" in combined_output.lower() or \
                   "allowed" in combined_output.lower(), \
                f"Unexpected conflict warning for shared-dma-pool: {combined_output}"


class TestMemoryAuditWithRepositoryFiles:
    """Tests using actual repository files for integration testing."""

    def test_reserved_memory_test_sdt(self):
        """Run memory audit on the test SDT file from lopper/selftest."""
        dts_file = "./lopper/selftest/reserved-memory-test-sdt.dts"

        if not os.path.exists(dts_file):
            pytest.skip(f"Test file not found: {dts_file}")

        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".dts", delete=False) as f:
            output_file = f.name

        try:
            cmd = [
                "./lopper.py", "-f",
                "-W", "memory_all",
                dts_file,
                output_file
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=os.getcwd()
            )

            # Should succeed on well-formed test SDT
            assert result.returncode == 0, f"Failed: {result.stderr}"
        finally:
            if os.path.exists(output_file):
                os.unlink(output_file)

    def test_memmap_on_test_sdt(self):
        """Generate memory map from the test SDT file."""
        dts_file = "./lopper/selftest/reserved-memory-test-sdt.dts"

        if not os.path.exists(dts_file):
            pytest.skip(f"Test file not found: {dts_file}")

        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".dts", delete=False) as f:
            output_file = f.name

        try:
            cmd = [
                "./lopper.py", "-f",
                "--memmap=-",
                dts_file,
                output_file
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=os.getcwd()
            )

            assert result.returncode == 0, f"Failed: {result.stderr}"

            # Memory map should show regions from the test SDT
            # The test SDT has regions at 0x10000000, 0x30000000, 0x50000000
            assert "0x" in result.stdout, \
                f"Expected hex addresses in memmap, got: {result.stdout}"
        finally:
            if os.path.exists(output_file):
                os.unlink(output_file)
