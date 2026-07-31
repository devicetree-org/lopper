"""
End-to-end tests for the schema audit as invoked from the command line.

Unlike test_audit_schema.py (which exercises the check functions directly),
these drive the real CLI the way a user / build flow does:

    ./lopper.py -f -W schema_forbidden_props <fixture>.dts out.dts

using a committed fixture, and assert on lopper's exit code and output.

Covers CR-1261898: a reserved-memory node with device_type = "memory"
(forbidden) must be flagged inline during normal processing, and --werror
must make it fatal.
"""

import os
import subprocess
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"
BAD_FIXTURE = "audit-schema-reserved-memory.dts"
CLEAN_FIXTURE = "audit-schema-clean.dts"


class TestAuditSchemaE2E:
    """Drive the schema audit through lopper's command line."""

    def run_lopper(self, tmp_path, fixture, extra_args=None):
        """Run ./lopper.py against a committed fixture, return the result."""
        output_file = tmp_path / "output.dts"
        cmd = ["./lopper.py", "-f"]
        if extra_args:
            cmd.extend(extra_args)
        cmd.extend([str(FIXTURES / fixture), str(output_file)])
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=os.getcwd(),
        )

    def test_forbidden_device_type_flagged(self, tmp_path):
        """-W schema_forbidden_props flags device_type on a reserved-memory
        child (report-only: warning, exit 0)."""
        result = self.run_lopper(
            tmp_path, BAD_FIXTURE, ["-W", "schema_forbidden_props"]
        )
        assert result.returncode == 0, f"lopper failed: {result.stderr}"

        out = (result.stdout + result.stderr).lower()
        assert "device_type" in out and "forbidden" in out, \
            f"expected a forbidden device_type finding, got:\n{result.stdout}{result.stderr}"
        assert "bad_region" in out, "the offending node should be named"
        # the clean sibling must not be reported
        assert "good_region" not in out, "clean reserved-memory child was wrongly flagged"

    def test_werror_makes_it_fatal(self, tmp_path):
        """--werror turns the forbidden-property finding into a build failure."""
        result = self.run_lopper(
            tmp_path, BAD_FIXTURE, ["-W", "schema_forbidden_props", "--werror"]
        )
        assert result.returncode != 0, \
            "expected non-zero exit under --werror, got 0"

    def test_schema_all_meta_flag(self, tmp_path):
        """The schema_all meta-flag enables the forbidden-property check too."""
        result = self.run_lopper(
            tmp_path, BAD_FIXTURE, ["-W", "schema_all"]
        )
        out = (result.stdout + result.stderr).lower()
        assert "device_type" in out and "forbidden" in out, \
            f"schema_all did not run the forbidden check:\n{result.stdout}{result.stderr}"

    def test_clean_tree_has_no_forbidden_finding(self, tmp_path):
        """A correct tree produces no forbidden-property finding, even with
        --werror (exit 0)."""
        result = self.run_lopper(
            tmp_path, CLEAN_FIXTURE, ["-W", "schema_forbidden_props", "--werror"]
        )
        assert result.returncode == 0, \
            f"clean tree should pass, got: {result.stdout}{result.stderr}"
        out = (result.stdout + result.stderr).lower()
        assert "forbidden properties" not in out, \
            f"clean tree unexpectedly flagged: {result.stdout}{result.stderr}"
