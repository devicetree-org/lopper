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


_FIXUP_BASE_DTS = """\
/dts-v1/;
/ {
    #address-cells = <1>;
    #size-cells = <1>;
    compatible = "test";

    amba: amba {
        compatible = "simple-bus";
        #address-cells = <1>;
        #size-cells = <1>;
        ranges;

        tgt: widget@a0000000 {
            compatible = "test,widget";
            reg = <0xa0000000 0x1000>;
        };

        sink: sink@b0000000 {
            compatible = "test,sink";
            reg = <0xb0000000 0x1000>;

            sink_ep: endpoint {
            };
        };
    };
};
"""

# References sink_ep, which nothing in the base tree references. dtc therefore
# never assigns it a phandle, which is the case the resolution has to cope
# with rather than the exception.
_FIXUP_OVERLAY = """\
/dts-v1/;
/plugin/;

&tgt {
    ports {
        port@0 {
            tgt_ep: endpoint {
                remote-endpoint = <&sink_ep>;
            };
        };
    };
};
"""


class TestOverlayFixupResolution:
    """A reference leaving an overlay must survive to the output.

    dtc compiles an overlay without a base to resolve against, so a reference
    out of it becomes 0xffffffff with the wanted label recorded in __fixups__.
    Something has to bind that placeholder before the result is written, or the
    property refers to nothing and is dropped.

    The two consumers reach that point differently -- one merges the overlay
    into a tree, the other transcribes it into a &label fragment -- so both are
    covered here.
    """

    def _prepare(self, tmp_path):
        base_file = tmp_path / "system-top.dts"
        base_file.write_text(_FIXUP_BASE_DTS)

        overlay_file = tmp_path / "fixups.dtso"
        overlay_file.write_text(_FIXUP_OVERLAY)

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
        return sdt

    def test_target_without_a_phandle_still_resolves_on_merge(self, tmp_path):
        """A referenced node that dtc gave no phandle gets one.

        dtc assigns a phandle only to a node something already references. A
        node referenced solely by the overlay therefore has none, and skipping
        it would abandon exactly the case this resolution exists for.
        """
        sdt = self._prepare(tmp_path)
        merged = sdt.tree.overlay_tree("fixups")

        holder = merged.__nodes__.get("/amba/widget@a0000000/ports/port@0/endpoint")
        assert holder is not None, \
            "the overlay's endpoint node is not in the merged tree"

        prop = holder.__props__.get("remote-endpoint")
        assert prop is not None, "remote-endpoint was dropped during the merge"

        sink = merged.lnodes("sink_ep", exact=True)
        assert sink, "the reference target is missing from the merged tree"
        assert sink[0].phandle, \
            "the target was left without a phandle, so nothing could be bound"
        assert prop.value and prop.value[0] == sink[0].phandle, \
            f"placeholder was not bound: {prop.value} != {sink[0].phandle}"

    def test_transcribed_fragment_keeps_the_reference(self, tmp_path):
        """The fragment path binds the placeholder too.

        Nothing resolves fixups when an overlay is transcribed into a &label
        fragment rather than merged -- that is the shape the PL overlay assists
        emit -- so the placeholder reached the output and the property went
        with it.
        """
        from lopper.tree import LopperTree

        sdt = self._prepare(tmp_path)

        out = LopperTree()
        out.overlay_of(sdt.tree)        # drives fragment_add_for_refs()

        endpoints = [n for p, n in out.__nodes__.items()
                     if p.endswith("/ports/port@0/endpoint")]
        assert endpoints, \
            f"no fragment was emitted for the overlay: {sorted(out.__nodes__)}"

        prop = endpoints[0].__props__.get("remote-endpoint")
        assert prop is not None, \
            "remote-endpoint was dropped from the emitted fragment"
        assert prop.value and prop.value[0] != 0xffffffff, \
            "the dtc placeholder reached the fragment unbound"

    def test_reference_is_emitted_by_label(self, tmp_path):
        """The written form is the label, not the number it resolved to.

        A fragment is written to a source file and compiled later, against a
        tree whose phandle numbering has no reason to match this one. Only the
        label is meaningful outside the process that produced it.
        """
        from lopper.tree import LopperTree

        sdt = self._prepare(tmp_path)

        out = LopperTree()
        out.overlay_of(sdt.tree)
        out.resolve()

        endpoints = [n for p, n in out.__nodes__.items()
                     if p.endswith("/ports/port@0/endpoint")]
        assert endpoints, "no fragment emitted"

        prop = endpoints[0].__props__["remote-endpoint"]
        rendered = getattr(prop, "string_val", "") or ""
        assert "&sink_ep" in rendered, \
            f"reference was not written by label: {rendered!r}"


# Two targets, identical internal shape below each. The fixups for both are
# held together under the one overlay name, and each records its path relative
# to its own fragment root, so both read "/ports/port@0/endpoint".
_TWO_TARGET_BASE = """\
/dts-v1/;
/ {
    #address-cells = <1>;
    #size-cells = <1>;
    compatible = "test";

    amba: amba {
        compatible = "simple-bus";
        #address-cells = <1>;
        #size-cells = <1>;
        ranges;

        dc0: dc@a0000000 {
            compatible = "test,dc";
            reg = <0xa0000000 0x1000>;
        };

        dc1: dc@b0000000 {
            compatible = "test,dc";
            reg = <0xb0000000 0x1000>;
        };

        sink_a: sink@c0000000 {
            compatible = "test,sink";
            reg = <0xc0000000 0x1000>;
        };

        sink_b: sink@d0000000 {
            compatible = "test,sink";
            reg = <0xd0000000 0x1000>;
        };
    };
};
"""

_TWO_TARGET_OVERLAY = """\
/dts-v1/;
/plugin/;

&dc0 {
    ports { port@0 { ep_a: endpoint { remote-endpoint = <&sink_a>; }; }; };
};

&dc1 {
    ports { port@0 { ep_b: endpoint { remote-endpoint = <&sink_b>; }; }; };
};
"""

# Two blocks naming the same target. dtc accepts this and emits two fragments,
# both labelled dc0, so the label cannot separate them -- nor should it, since
# they describe one node and the later definition wins.
_SAME_TARGET_OVERLAY = """\
/dts-v1/;
/plugin/;

&dc0 {
    first-block;
    ports { port@0 { ep_a: endpoint { remote-endpoint = <&sink_a>; }; }; };
};

&dc0 {
    second-block;
    ports { port@0 { ep_b: endpoint { remote-endpoint = <&sink_b>; }; }; };
};
"""

_SPLIT_OVERLAY_A = """\
/dts-v1/;
/plugin/;
&dc0 { ports { port@0 { ep_a: endpoint { remote-endpoint = <&sink_a>; }; }; }; };
"""

_SPLIT_OVERLAY_B = """\
/dts-v1/;
/plugin/;
&dc1 { ports { port@0 { ep_b: endpoint { remote-endpoint = <&sink_b>; }; }; }; };
"""


class TestOverlayFixupsPerFragment:
    """A fixup must only be written into the fragment it was recorded against.

    An overlay's fixups are held together under one name, and each path is
    stored relative to its own fragment root so a later rename of the target is
    picked up. Two fragments of the same shape therefore collide on that path
    alone, and only the fragment label recorded beside it tells them apart.
    """

    def _sdt(self, tmp_path, overlays):
        base_file = tmp_path / "system-top.dts"
        base_file.write_text(_TWO_TARGET_BASE)

        files = []
        for name, text in overlays:
            f = tmp_path / name
            f.write_text(text)
            files.append(str(f))

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
        sdt._compile_overlay_subtrees(files, str(tmp_path))
        return sdt

    def _resolved(self, sdt):
        """{fragment name: label its remote-endpoint resolved to}."""
        from lopper.tree import LopperTree

        out = LopperTree()
        fragments = sdt.tree.fragment_add_for_refs(out)

        # Built after the fragments are bound, not before: a sink referenced
        # only by the overlay has no phandle until binding mints one, so a map
        # taken earlier has every target sitting on 0.
        by_phandle = {}
        for label in ("sink_a", "sink_b"):
            found = sdt.tree.lnodes(label, exact=True)
            if found:
                by_phandle[found[0].phandle] = label

        resolved = []
        for frag in fragments:
            for node in frag.subnodes():
                prop = node.__props__.get("remote-endpoint")
                if prop is None:
                    continue
                value = prop.value if isinstance(prop.value, list) else [prop.value]
                resolved.append((frag.name, by_phandle.get(value[0])))
        return resolved

    def test_each_fragment_keeps_its_own_target(self, tmp_path):
        """Two instances of one IP, each wired to its own sink."""
        sdt = self._sdt(tmp_path, [("twotarget.dtso", _TWO_TARGET_OVERLAY)])
        resolved = dict(self._resolved(sdt))

        assert resolved.get("&dc0") == "sink_a", \
            f"&dc0 took another fragment's target: {resolved.get('&dc0')}"
        assert resolved.get("&dc1") == "sink_b", \
            f"&dc1 took another fragment's target: {resolved.get('&dc1')}"

    def test_no_fragment_is_left_unresolved(self, tmp_path):
        """Filtering must not discard a fixup that does belong here."""
        sdt = self._sdt(tmp_path, [("twotarget.dtso", _TWO_TARGET_OVERLAY)])
        resolved = self._resolved(sdt)

        assert len(resolved) == 2, f"expected two bound references, got {resolved}"
        assert all(label is not None for _, label in resolved), \
            f"a reference was left unbound: {resolved}"

    def test_repeated_target_still_takes_the_later_block(self, tmp_path):
        """Two blocks on one target describe one node, so the later wins.

        Both fragments carry the same label, so the filter cannot separate
        them and must not try to. Guards against over-filtering.
        """
        sdt = self._sdt(tmp_path, [("sametarget.dtso", _SAME_TARGET_OVERLAY)])
        resolved = self._resolved(sdt)

        assert len(resolved) == 2, f"expected both blocks emitted, got {resolved}"
        assert {label for _, label in resolved} == {"sink_b"}, \
            f"the later block should win for both, got {resolved}"

    def test_separate_overlays_do_not_interfere(self, tmp_path):
        """The same shape in two files stays isolated by overlay name."""
        sdt = self._sdt(tmp_path, [("ovA.dtso", _SPLIT_OVERLAY_A),
                                   ("ovB.dtso", _SPLIT_OVERLAY_B)])
        resolved = dict(self._resolved(sdt))

        assert resolved.get("&dc0") == "sink_a"
        assert resolved.get("&dc1") == "sink_b"


# A base tree that references one of its own nodes by phandle, plus an
# overlay whose internally-assigned phandles start from 1 as every standalone
# compile does. The two numberings are independent, so the overlay arrives
# holding numbers the base is already using.
_COLLIDE_BASE = """\
/dts-v1/;
/ {
    #address-cells = <1>;
    #size-cells = <1>;
    compatible = "test";

    amba: amba {
        compatible = "simple-bus";
        #address-cells = <1>;
        #size-cells = <1>;
        ranges;

        intc: interrupt-controller@a0000000 {
            compatible = "test,intc";
            reg = <0xa0000000 0x1000>;
            interrupt-controller;
            #interrupt-cells = <3>;
        };

        victim: victim@b0000000 {
            compatible = "test,victim";
            reg = <0xb0000000 0x1000>;
            interrupt-parent = <&intc>;
        };

        tgt: widget@c0000000 {
            compatible = "test,widget";
            reg = <0xc0000000 0x1000>;
        };
    };
};
"""

_COLLIDE_OVERLAY = """\
/dts-v1/;
/plugin/;

&tgt {
    ports {
        port@0 {
            ov_ep_a: endpoint { };
        };
        port@1 {
            ov_ep_b: endpoint { };
        };
    };
};
"""


class TestOverlayPhandlesDoNotDisturbTheBase:
    """An overlay's own phandle numbering must not reach into the base.

    A standalone overlay compile numbers from 1, exactly as the base did, so
    the two have assigned the same numbers to different nodes. Transcribing
    the overlay into the tree without renumbering leaves base properties
    holding integers that now answer to an overlay node, and they render as a
    reference to it -- an interrupt parent becoming a video endpoint.
    """

    def _prepare(self, tmp_path):
        base_file = tmp_path / "system-top.dts"
        base_file.write_text(_COLLIDE_BASE)
        ov_file = tmp_path / "collide.dtso"
        ov_file.write_text(_COLLIDE_OVERLAY)

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
        sdt._compile_overlay_subtrees([str(ov_file)], str(tmp_path))
        return sdt

    def test_a_base_reference_still_names_its_own_target(self, tmp_path):
        """victim's interrupt-parent must still be the interrupt controller.

        The PL assist extracts base content into the same tree it adds the
        overlay fragments to, so a base-derived property is rendered with the
        overlay's nodes in scope. That is where a shared number is resolved to
        the wrong node -- checking the base tree alone never sees it, since
        nothing there answers to the overlay's numbering.
        """
        import copy
        from lopper.tree import LopperTree

        sdt = self._prepare(tmp_path)

        out = LopperTree()
        victim = sdt.tree.lnodes("victim", exact=True)
        assert victim, "victim node missing from the base"
        out.add(copy.deepcopy(victim[0]))

        out.overlay_of(sdt.tree)
        out.resolve()

        moved = out.lnodes("victim", exact=True)
        assert moved, "victim did not survive into the emitted tree"
        prop = moved[0].__props__.get("interrupt-parent")
        assert prop is not None, "interrupt-parent disappeared"

        rendered = getattr(prop, "string_val", "") or ""
        assert "ov_ep" not in rendered, \
            f"base reference was captured by an overlay node: {rendered!r}"

    def test_overlay_phandles_are_distinct_from_base_phandles(self, tmp_path):
        """No overlay node may carry a number the base is already using."""
        from lopper.tree import LopperTree

        sdt = self._prepare(tmp_path)
        out = LopperTree()
        out.overlay_of(sdt.tree)
        out.resolve()

        base_phandles = set(sdt.tree.__pnodes__.keys())
        overlay_phandles = set()
        for frag in out.__nodes__.values():
            for node in frag.subnodes():
                if node.phandle and node.phandle > 0:
                    overlay_phandles.add(node.phandle)

        clash = base_phandles & overlay_phandles
        assert not clash, f"overlay reused base phandle numbers: {sorted(clash)}"


# A base whose endpoints are declared but empty -- the shape a graph binding
# takes before anything is wired -- and an overlay that wires both ends. The
# overlay's own two fragments reference each other, which dtc records as a
# local fixup since both ends are inside the one overlay.
_WIRED_BASE = """\
/dts-v1/;
/ {
    #address-cells = <1>;
    #size-cells = <1>;
    compatible = "test";

    amba: amba {
        compatible = "simple-bus";
        #address-cells = <1>;
        #size-cells = <1>;
        ranges;

        sink: sink@a0000000 {
            compatible = "test,sink";
            reg = <0xa0000000 0x1000>;
        };

        src: src@b0000000 {
            compatible = "test,src";
            reg = <0xb0000000 0x1000>;
            src_port: port {
                src_out: endpoint {
                };
            };
        };
    };
};
"""

_WIRED_OVERLAY = """\
/dts-v1/;
/plugin/;

&sink {
    ports {
        port@0 {
            sink_in: endpoint {
                remote-endpoint = <&src_out>;
            };
        };
    };
};

&src_out {
    remote-endpoint = <&sink_in>;
};
"""


class TestOverlayWiresBothEnds:
    """An overlay that completes a link in both directions.

    Three things have to hold at once: the nested label the author wrote has
    to survive, a reference out to the base has to bind, and the reference
    back -- which crosses from one fragment of the overlay to another, and so
    is a local fixup -- has to bind to the node rather than to whatever number
    dtc happened to give it.
    """

    def _emit(self, tmp_path):
        from lopper.tree import LopperTree

        base_file = tmp_path / "system-top.dts"
        base_file.write_text(_WIRED_BASE)
        ov_file = tmp_path / "wired.dtso"
        ov_file.write_text(_WIRED_OVERLAY)

        sdt = LopperSDT(str(base_file))
        sdt.dryrun = False
        sdt.verbose = 0
        sdt.werror = False
        sdt.output_file = str(tmp_path / "out.dts")
        sdt.cleanup_flag = True
        sdt.save_temps = False
        sdt.enhanced = True
        sdt.symbols = True
        sdt.outdir = str(tmp_path)
        sdt.setup(sdt.dts, [], "", True, libfdt=True)
        sdt._compile_overlay_subtrees([str(ov_file)], str(tmp_path))

        out = LopperTree()
        out.overlay_of(sdt.tree)
        out.resolve()
        return sdt, out

    def _rendered(self, tree, suffix):
        for path, node in tree.__nodes__.items():
            if path.endswith(suffix):
                prop = node.__props__.get("remote-endpoint")
                if prop is not None:
                    return getattr(prop, "string_val", "") or ""
        return None

    def test_the_authored_label_survives(self, tmp_path):
        """sink_in, not a label derived from the node's own name."""
        sdt, out = self._emit(tmp_path)
        assert out.lnodes("sink_in", exact=True), \
            "the overlay's nested label was lost"

    def test_the_reference_out_to_the_base_binds(self, tmp_path):
        sdt, out = self._emit(tmp_path)
        rendered = self._rendered(out, "/ports/port@0/endpoint")
        assert rendered, "remote-endpoint was dropped from the overlay fragment"
        assert "src_out" in rendered, \
            f"reference out to the base did not bind: {rendered!r}"

    def test_the_reference_back_binds_across_fragments(self, tmp_path):
        """&src_out references sink_in, which lives in the other fragment of
        the same overlay -- a local fixup, resolved by path."""
        sdt, out = self._emit(tmp_path)
        rendered = self._rendered(out, "&src_out")
        assert rendered, "remote-endpoint was dropped from the &src_out fragment"
        assert "sink_in" in rendered, \
            f"local fixup did not bind to the node: {rendered!r}"


class TestLabelsFromSymbols:
    """A label on a node with no properties survives loading.

    dtc keeps labels out of nodes and records them in __symbols__. Lopper
    otherwise infers a label from a label-typed property, which an empty node
    does not have, so the label reaches the tree index but never the node --
    and is left behind the moment the node is copied elsewhere.
    """

    def test_an_empty_node_keeps_its_label(self, tmp_path):
        base_file = tmp_path / "system-top.dts"
        base_file.write_text(_WIRED_BASE)

        sdt = LopperSDT(str(base_file))
        sdt.dryrun = False
        sdt.verbose = 0
        sdt.werror = False
        sdt.output_file = str(tmp_path / "out.dts")
        sdt.cleanup_flag = True
        sdt.save_temps = False
        sdt.enhanced = True
        sdt.symbols = True
        sdt.outdir = str(tmp_path)
        sdt.setup(sdt.dts, [], "", True, libfdt=True)

        node = sdt.tree.__nodes__.get("/amba/src@b0000000/port/endpoint")
        assert node is not None, "the empty endpoint node did not survive load"
        assert node.label == "src_out", \
            f"label was not attached from __symbols__: {node.label!r}"

    def test_the_label_travels_with_a_copy(self, tmp_path):
        """The point of attaching it: a copied node takes its label along."""
        import copy

        base_file = tmp_path / "system-top.dts"
        base_file.write_text(_WIRED_BASE)

        sdt = LopperSDT(str(base_file))
        sdt.dryrun = False
        sdt.verbose = 0
        sdt.werror = False
        sdt.output_file = str(tmp_path / "out.dts")
        sdt.cleanup_flag = True
        sdt.save_temps = False
        sdt.enhanced = True
        sdt.symbols = True
        sdt.outdir = str(tmp_path)
        sdt.setup(sdt.dts, [], "", True, libfdt=True)

        node = sdt.tree.__nodes__.get("/amba/src@b0000000/port/endpoint")
        assert copy.deepcopy(node).label == "src_out", \
            "the label did not travel with the copied node"
