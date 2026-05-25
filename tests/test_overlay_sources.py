"""
Tests for overlay helper functions for identifying and extracting
overlay targets from DTS files.
"""

import pytest
import subprocess
import tempfile
import os
from lopper.tree import LopperTree, LopperNode, LopperProp
from lopper import LopperSDT


class TestIsOverlayFile:
    """Test is_overlay_file() helper function."""

    def test_detects_overlay_with_plugin_directive(self):
        """Should detect a true overlay: /plugin/; plus &label { } syntax."""
        from lopper import is_overlay_file

        with tempfile.NamedTemporaryFile(mode='w', suffix='.dts', delete=False) as f:
            f.write("""
            /dts-v1/;
            /plugin/;

            &mmi_dc {
                status = "okay";
                clocks = <&pl_clk 0>;
            };
            """)
            f.flush()
            try:
                assert is_overlay_file(f.name) is True
            finally:
                os.unlink(f.name)

    def test_detects_overlay_by_dtso_extension(self):
        """Should detect a true overlay via .dtso extension + &label { }."""
        from lopper import is_overlay_file

        with tempfile.NamedTemporaryFile(mode='w', suffix='.dtso', delete=False) as f:
            f.write("""
            &mmi_dc {
                status = "okay";
            };
            """)
            f.flush()
            try:
                assert is_overlay_file(f.name) is True
            finally:
                os.unlink(f.name)

    def test_rejects_dtsi_fragment_without_plugin_directive(self):
        """A .dtsi fragment with &label { } but no /plugin/; is an include,
        not an overlay — dtc resolves the labels when concatenated with the
        base tree, so it must merge into the base SDT."""
        from lopper import is_overlay_file

        with tempfile.NamedTemporaryFile(mode='w', suffix='.dtsi', delete=False) as f:
            f.write("""
            &amba_pl {
                zyxclmm_drm {
                    compatible = "xlnx,zocl-versal";
                };
            };
            """)
            f.flush()
            try:
                assert is_overlay_file(f.name) is False
            finally:
                os.unlink(f.name)

    def test_rejects_non_overlay_file(self):
        """Should return False for files without overlay syntax."""
        from lopper import is_overlay_file

        with tempfile.NamedTemporaryFile(mode='w', suffix='.dts', delete=False) as f:
            f.write("""
            /dts-v1/;
            / {
                model = "test";
                compatible = "test,board";

                device@0 {
                    reg = <0x0 0x1000>;
                };
            };
            """)
            f.flush()
            try:
                assert is_overlay_file(f.name) is False
            finally:
                os.unlink(f.name)

    def test_handles_missing_file(self):
        """Should return False for non-existent files."""
        from lopper import is_overlay_file
        assert is_overlay_file('/nonexistent/path/file.dts') is False


class TestExtractOverlayTargets:
    """Test extract_overlay_targets() helper function."""

    def test_extracts_single_target(self):
        """Should extract single overlay target with properties."""
        from lopper import extract_overlay_targets

        with tempfile.NamedTemporaryFile(mode='w', suffix='.dtsi', delete=False) as f:
            f.write("""
            &mmi_dc {
                status = "okay";
                clocks = <&pl_clk 0>;
                clock-names = "pl_clk";
            };
            """)
            f.flush()
            try:
                targets = extract_overlay_targets(f.name)
                assert 'mmi_dc' in targets
                assert 'status' in targets['mmi_dc']['props']
                assert 'clocks' in targets['mmi_dc']['props']
                assert 'clock-names' in targets['mmi_dc']['props']
            finally:
                os.unlink(f.name)

    def test_extracts_multiple_targets(self):
        """Should extract multiple overlay targets."""
        from lopper import extract_overlay_targets

        with tempfile.NamedTemporaryFile(mode='w', suffix='.dtsi', delete=False) as f:
            f.write("""
            &node_a {
                prop1 = "value1";
            };

            &node_b {
                prop2 = "value2";
                prop3 = <0x100>;
            };
            """)
            f.flush()
            try:
                targets = extract_overlay_targets(f.name)
                assert 'node_a' in targets
                assert 'node_b' in targets
                assert 'prop1' in targets['node_a']['props']
                assert 'prop2' in targets['node_b']['props']
                assert 'prop3' in targets['node_b']['props']
            finally:
                os.unlink(f.name)

    def test_returns_empty_for_non_overlay(self):
        """Should return empty dict for non-overlay file."""
        from lopper import extract_overlay_targets

        with tempfile.NamedTemporaryFile(mode='w', suffix='.dts', delete=False) as f:
            f.write("""
            /dts-v1/;
            / {
                model = "test";
            };
            """)
            f.flush()
            try:
                targets = extract_overlay_targets(f.name)
                assert targets == {}
            finally:
                os.unlink(f.name)

    def test_handles_missing_file(self):
        """Should return empty dict for non-existent files."""
        from lopper import extract_overlay_targets
        targets = extract_overlay_targets('/nonexistent/path/file.dts')
        assert targets == {}


class TestExtractOverlayTargetsFromTree:
    """Test extract_overlay_targets_from_tree() for compiled tree analysis.

    Uses compile_overlay_standalone() to produce a real dtc-compiled tree,
    then verifies extract_overlay_targets_from_tree() reads the fragment@N/
    __overlay__ structure correctly.
    """

    def test_extracts_targets_from_tree(self):
        """Should extract props and children from a compiled overlay tree."""
        from lopper import extract_overlay_targets_from_tree, compile_overlay_standalone

        with tempfile.NamedTemporaryFile(mode='w', suffix='.dtsi', delete=False) as f:
            f.write("&mmi_dc { status = \"okay\"; clocks = <&pl_clk 0>; };\n")
            f.flush()
            try:
                tree = compile_overlay_standalone(f.name)
                if tree is None:
                    pytest.skip("dtc not available for overlay compilation")
                targets = extract_overlay_targets_from_tree(tree)
                assert 'mmi_dc' in targets
                assert 'status' in targets['mmi_dc']['props']
                assert 'clocks' in targets['mmi_dc']['props']
                assert targets['mmi_dc']['children'] == []
            finally:
                os.unlink(f.name)

    def test_extracts_multiple_targets(self):
        """Should extract multiple overlay targets from compiled tree."""
        from lopper import extract_overlay_targets_from_tree, compile_overlay_standalone

        with tempfile.NamedTemporaryFile(mode='w', suffix='.dtsi', delete=False) as f:
            f.write("&device_a { prop1 = \"value1\"; };\n"
                    "&device_b { prop2 = <0x100>; };\n")
            f.flush()
            try:
                tree = compile_overlay_standalone(f.name)
                if tree is None:
                    pytest.skip("dtc not available for overlay compilation")
                targets = extract_overlay_targets_from_tree(tree)
                assert 'device_a' in targets
                assert 'device_b' in targets
                assert 'prop1' in targets['device_a']['props']
                assert 'prop2' in targets['device_b']['props']
            finally:
                os.unlink(f.name)

    def test_ignores_non_overlay_nodes(self):
        """Should return empty dict for a non-overlay (normal DTS) file."""
        from lopper import extract_overlay_targets_from_tree, compile_overlay_standalone

        with tempfile.NamedTemporaryFile(mode='w', suffix='.dts', delete=False) as f:
            f.write("/dts-v1/;\n/ { model = \"test\"; };\n")
            f.flush()
            try:
                # compile_overlay_standalone returns None for non-overlay files
                tree = compile_overlay_standalone(f.name)
                if tree is not None:
                    targets = extract_overlay_targets_from_tree(tree)
                    assert targets == {}
                # If None, the function correctly rejected the non-overlay file
            finally:
                os.unlink(f.name)


class TestRegexFallback:
    """Test _extract_overlay_targets_regex() fallback function."""

    def test_regex_fallback_works(self):
        """Should extract targets using brace-counting parser when no dtc available."""
        from lopper import _extract_overlay_targets_regex

        with tempfile.NamedTemporaryFile(mode='w', suffix='.dtsi', delete=False) as f:
            f.write("""
            &my_device {
                status = "okay";
                reg = <0x100 0x1000>;
            };
            """)
            f.flush()
            try:
                targets = _extract_overlay_targets_regex(f.name)
                assert 'my_device' in targets
                assert 'status' in targets['my_device']['props']
                assert 'reg' in targets['my_device']['props']
            finally:
                os.unlink(f.name)


# Minimal base DTS for overlay CLI tests.
# Has a labelled mmi_dc node so &mmi_dc in the user overlay resolves.
_BASE_DTS = """\
/dts-v1/;
/ {
    #address-cells = <2>;
    #size-cells = <2>;
    compatible = "test";

    amba: amba {
        #address-cells = <2>;
        #size-cells = <2>;
        ranges;

        mmi_dc: mmi_dc@fd4a0000 {
            compatible = "xlnx,mmi-dc";
            reg = <0x0 0xfd4a0000 0x0 0x10000>;
            status = "disabled";
        };
    };
};
"""


class TestOverlayE2E:
    """End-to-end CLI tests: lopper processes a user overlay DTSI alongside
    a base DTS, just as a user would invoke it on the command line."""

    def run_lopper(self, tmp_path, base_dts, overlay_dtsi, extra_args=None):
        """Run lopper with a base DTS and a user overlay DTSI.

        Mirrors the user command:
            lopper.py -f -i <user-overlay.dtsi> <system-top.dts> <output.dts>
        """
        base_file = tmp_path / "system-top.dts"
        base_file.write_text(base_dts)

        overlay_file = tmp_path / "user-overlay.dtsi"
        overlay_file.write_text(overlay_dtsi)

        output_file = tmp_path / "output.dts"

        cmd = ["./lopper.py", "-f"]
        if extra_args:
            cmd.extend(extra_args)
        cmd.extend(["-i", str(overlay_file),
                    str(base_file), str(output_file)])

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=os.getcwd()
        )
        return result, output_file

    def test_true_overlay_base_tree_unchanged(self, tmp_path):
        """A true /plugin/; overlay does NOT modify the default base output.

        True overlays (declared with /plugin/;) are kept separate; the default
        output reflects the base tree. Callers retrieve the merged view via
        overlay_tree(stem), not the default write path.
        """
        overlay = '/dts-v1/;\n/plugin/;\n\n&mmi_dc { status = "okay"; };\n'

        result, output_file = self.run_lopper(tmp_path, _BASE_DTS, overlay)

        assert result.returncode == 0, \
            f"lopper failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        assert output_file.exists(), "output DTS was not created"

        content = output_file.read_text()
        # Base tree retains its original value; overlay is kept separate
        assert 'status = "disabled"' in content, \
            f"Base tree status should remain 'disabled':\n{content}"
        assert 'status = "okay"' not in content, \
            f"Overlay property leaked into base tree output:\n{content}"

    def test_plain_dtsi_fragment_merges_into_base(self, tmp_path):
        """A plain .dtsi fragment with &label { } (no /plugin/;) is an
        include, not an overlay — its contents must be merged into the
        base tree so that downstream assists see the new node.
        """
        overlay = '&mmi_dc { status = "okay"; };\n'

        result, output_file = self.run_lopper(tmp_path, _BASE_DTS, overlay)

        assert result.returncode == 0, \
            f"lopper failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        assert output_file.exists(), "output DTS was not created"

        content = output_file.read_text()
        assert 'status = "okay"' in content, \
            f"Plain .dtsi fragment should be merged into base tree:\n{content}"
        assert '__lopper-overlays__' not in content, \
            f"Plain .dtsi fragment should not be parked under overlays:\n{content}"

    def test_overlay_with_nested_nodes_no_error(self, tmp_path):
        """User overlay with nested child nodes is processed without error.

        Onkar's use case: ports/endpoint hierarchy inside the user overlay.
        Lopper must handle this without crashing.
        """
        overlay = """\
&mmi_dc {
    status = "okay";
    ports {
        port@0 {
            endpoint {
                remote-endpoint = <&mmi_dc>;
            };
        };
    };
};
"""
        result, output_file = self.run_lopper(tmp_path, _BASE_DTS, overlay)

        assert result.returncode == 0, \
            f"lopper failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        assert output_file.exists(), "output DTS was not created"

    def test_overlay_listed_in_system_device_tree(self, tmp_path):
        """Lopper includes the user overlay DTSI in the system device tree list.

        The overlay file is concatenated with the base DTS before compilation.
        Verbose output lists all inputs under 'system device tree:' — the overlay
        must appear there, confirming lopper accepted it as an input.
        """
        overlay = '&mmi_dc { xlnx,dc-pixel-format = "rgb888"; };\n'

        result, _ = self.run_lopper(
            tmp_path, _BASE_DTS, overlay, extra_args=["-v"]
        )

        assert result.returncode == 0, \
            f"lopper failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"

        combined = result.stdout + result.stderr
        assert "user-overlay.dtsi" in combined, \
            f"Expected user-overlay.dtsi to appear in lopper output:\n{combined}"
