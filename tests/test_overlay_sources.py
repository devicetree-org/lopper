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


class TestOverlayNestedLabels:
    """Labels on nodes below an overlay's fragment target must survive.

    dtc records the label of every labelled overlay node under /__symbols__,
    and nowhere else: the fragment target itself is recovered from __fixups__,
    but anything deeper is only named there. Requesting symbols when compiling
    the overlay is therefore the difference between a labelled node keeping its
    name and being emitted anonymously.

    Graph bindings make this the common case rather than a corner: ports /
    port@N / endpoint puts the labelled node three levels below the target, so
    any capture or display pipeline description depends on it.
    """

    # A labelled endpoint three levels below the fragment target, which is the
    # shape of every device tree graph binding.
    _OVERLAY = """\
/dts-v1/;
/plugin/;

&mmi_dc {
    ports {
        port@0 {
            dc_in_0: endpoint {
            };
        };
    };
};
"""

    def _compile(self, tmp_path):
        """Compile the overlay against the base and return the tree metadata."""
        base_file = tmp_path / "system-top.dts"
        base_file.write_text(_BASE_DTS)

        overlay_file = tmp_path / "nested.dtso"
        overlay_file.write_text(self._OVERLAY)

        sdt = LopperSDT(str(base_file))
        sdt.dryrun = False
        sdt.verbose = 0
        sdt.werror = False
        sdt.output_file = str(tmp_path / "out.dts")
        sdt.cleanup_flag = True
        sdt.save_temps = False
        sdt.enhanced = True
        sdt.outdir = str(tmp_path)
        sdt.setup(sdt.dts, [], "", True, libfdt=True)

        sdt._compile_overlay_subtrees([str(overlay_file)], str(tmp_path))
        return sdt.tree._metadata

    def test_symbols_are_collected_from_the_overlay(self, tmp_path):
        """The overlay is compiled with symbols, so its labels are available.

        Compiled without dtc's -@ the overlay DTB has no /__symbols__ at all,
        and there is then nothing to recover the label from -- the failure is
        silent and total rather than partial.
        """
        md = self._compile(tmp_path)

        labels = md.get('overlay_symbol_labels', {})
        assert labels, \
            "no overlay symbol labels collected: the overlay was compiled " \
            "without symbols, so dtc emitted no __symbols__ node"

        collected = [lbl for pairs in labels.values() for _, lbl in pairs]
        assert 'dc_in_0' in collected, \
            f"the labelled endpoint was not among the collected labels: {collected}"

    def test_nested_label_is_set_on_the_overlay_node(self, tmp_path):
        """The label is applied to the node, not just returned alongside it.

        Consumers that merge the overlay pick labels up from the returned list,
        but those that copy the nodes instead -- fragment_add_for_refs() builds
        &label fragments that way -- never see it. Setting it on the node means
        both get it, since deepcopy carries the label.
        """
        md = self._compile(tmp_path)

        found = {}

        def walk(node):
            if node.label:
                found[node.label] = node.abs_path
            for child in node.child_nodes.values():
                walk(child)

        for nodes in md.get('overlay_subtrees', {}).values():
            for node in nodes:
                walk(node)

        assert 'dc_in_0' in found, \
            f"the endpoint node carries no label; labelled nodes found: {found}"

    def test_fragment_target_keeps_its_own_label(self, tmp_path):
        """Baseline: the target label still comes from __fixups__ as before.

        That path is independent of __symbols__, so it would keep working even
        with symbols unavailable. Asserted so a regression there is not
        mistaken for the nested-label failure.
        """
        md = self._compile(tmp_path)

        roots = [n for nodes in md.get('overlay_subtrees', {}).values()
                 for n in nodes]
        assert roots, "no overlay subtrees were registered"
        assert any(n.label == 'mmi_dc' for n in roots), \
            f"fragment target lost its label: {[(n.abs_path, n.label) for n in roots]}"
