"""
Regression tests for domain_access step 2d: mutually linked peer retention.

A device whose only inbound link is a phandle held in a subnode of an
accessed node is not refcounted by steps 1/1a, and the step 5 filter
deletes it.  The graph binding is the common case: a capture wrapper
reachable only through <accessed>/ports/port@N/endpoint's remote-endpoint.

Step 2d retains such a node when the reference is reciprocated.  These
tests cover the retention, the negative control (a one way reference of the
same shape must NOT retain), and the peer's subtree.

Fixture: lopper/selftest/domain-graph-peer-sdt.dts

Copyright (C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.

SPDX-License-Identifier: BSD-3-Clause

Author:
    Bruce Ashfield <bruce.ashfield@amd.com>
"""

import os
import pytest
import lopper.base
from lopper import LopperSDT, Lopper


class TestMutuallyLinkedPeerRetention:
    """domain_access step 2d: reciprocated references retain the peer.

    The SDT fixture contains, all under /amba_pl:

      visp_ss@b1300000    in APU_Linux's access list
      vcap_visp_ss_00     mutually linked to visp_ss  -> must survive
      oneway_sink         referenced by visp_ss only  -> must be deleted
      unrelated@b2000000  referenced by nothing       -> must be deleted

    Note that remote-endpoint is NOT declared as a phandle property here.
    It is not in the built in table; the typing comes from schema learning,
    which is how it is typed in production too.  These tests therefore also
    guard that path: if learning stops recognising remote-endpoint, the
    reciprocal link becomes invisible and the retention tests below fail.
    """

    SDT = "lopper/selftest/domain-graph-peer-sdt.dts"

    def _run_pipeline(self, test_outdir, target="/domains/APU_Linux",
                      output_name="da-peer-output.dts"):
        if not os.path.exists(self.SDT):
            pytest.skip(f"Test fixture not found: {self.SDT}")

        device_tree = LopperSDT(self.SDT)
        device_tree.dryrun = False
        device_tree.verbose = 0
        device_tree.werror = False
        device_tree.output_file = os.path.join(test_outdir, output_name)
        device_tree.cleanup_flag = True
        device_tree.save_temps = False
        device_tree.enhanced = True
        device_tree.outdir = test_outdir

        # phandle_possible_prop_dict is a class variable and persists for the
        # whole pytest session.  It is declared on lopper_base; Lopper
        # (LopperFDT) is a subclass, so other suites that pin it by assigning
        # Lopper.phandle_possible_prop_dict install a *shadowing* attribute on
        # the subclass.  Reads resolve to that shadow, which hides anything
        # schema learning subsequently writes to the base class -- including
        # the remote-endpoint typing these tests depend on.
        #
        # Drop the shadow and clear the base so the built in table is the
        # starting point and learning supplies remote-endpoint, as it does in
        # production.  Without this the tests below pass alone and fail after
        # a suite that pinned the dictionary.
        if 'phandle_possible_prop_dict' in Lopper.__dict__:
            delattr( Lopper, 'phandle_possible_prop_dict' )
        lopper.base.lopper_base.phandle_possible_prop_dict = {}

        device_tree.setup(device_tree.dts, [], "", True, libfdt=True)
        device_tree.target = target
        device_tree.assists_setup(["lopper/assists/domain_access.py"])
        device_tree.assist_autorun_setup("lopper/assists/domain_access", ["-t", target])
        device_tree.perform_lops()

        return device_tree

    def _pl_children(self, tree):
        """Return the set of node names directly under /amba_pl."""
        try:
            pl = tree["/amba_pl"]
        except Exception:
            return set()
        return {child.name for child in pl.subnodes(children_only=True)}

    # ------------------------------------------------------------------
    # the fix
    # ------------------------------------------------------------------

    def test_mutually_linked_peer_survives(self, test_outdir):
        """A peer that reciprocates the reference is retained."""
        dt = self._run_pipeline(test_outdir, output_name="da-peer-keep.dts")
        children = self._pl_children(dt.tree)
        assert "vcap_visp_ss_00" in children, \
            "vcap_visp_ss_00 was deleted - a reciprocated reference from an " \
            "accessed node's subtree must retain the peer"
        dt.cleanup()

    def test_peer_subtree_survives(self, test_outdir):
        """The peer is retained whole, not just the path to the endpoint.

        The step 5 filter walks every descendant of the bus, so a peer whose
        siblings were left unreferenced would survive with its ports gone.
        """
        dt = self._run_pipeline(test_outdir, output_name="da-peer-subtree.dts")
        try:
            ports = dt.tree["/amba_pl/vcap_visp_ss_00/ports"]
        except Exception:
            ports = None
        assert ports is not None, "the peer's ports node was deleted"

        port_names = {c.name for c in ports.subnodes(children_only=True)}
        assert "port@0" in port_names, "the reciprocating port was deleted"
        assert "port@1" in port_names, \
            "a port with no reciprocal link was deleted - step 2d must ref " \
            "the peer's whole subtree, not only the matched path"
        dt.cleanup()

    # ------------------------------------------------------------------
    # the gate: this is what keeps the rule from marking the whole tree
    # ------------------------------------------------------------------

    def test_one_way_reference_does_not_retain(self, test_outdir):
        """A reference of the same shape that is NOT reciprocated is ignored.

        oneway_sink is referenced from visp_ss's endpoint exactly as
        vcap_visp_ss_00 is.  The only difference is that it does not name
        visp_ss back.  If this survives, step 2d is following subnode
        phandles generally rather than testing reciprocation.
        """
        dt = self._run_pipeline(test_outdir, output_name="da-peer-oneway.dts")
        children = self._pl_children(dt.tree)
        assert "oneway_sink" not in children, \
            "oneway_sink survived - step 2d is not gating on reciprocation"
        dt.cleanup()

    def test_unreferenced_node_still_pruned(self, test_outdir):
        """Baseline: an unreferenced node is still deleted."""
        dt = self._run_pipeline(test_outdir, output_name="da-peer-unref.dts")
        children = self._pl_children(dt.tree)
        assert "unrelated@b2000000" not in children, \
            "unrelated@b2000000 survived - pruning is not running at all"
        dt.cleanup()

    # ------------------------------------------------------------------
    # the accessed node itself
    # ------------------------------------------------------------------

    def test_accessed_node_survives(self, test_outdir):
        """The directly accessed node survives, as it always did."""
        dt = self._run_pipeline(test_outdir, output_name="da-peer-accessed.dts")
        children = self._pl_children(dt.tree)
        assert "visp_ss@b1300000" in children, \
            "the accessed node was deleted"
        dt.cleanup()

    # ------------------------------------------------------------------
    # self references
    # ------------------------------------------------------------------

    def test_self_reference_is_skipped(self, test_outdir):
        """A node inside the accessed subtree may reference itself.

        visp_intc is its own interrupt-parent, which is how interrupt
        controllers are normally written.  Step 2d walks the accessed
        subtree and resolves each property's phandles, so it meets that
        reference and has to recognise the target as the node it is already
        looking at.

        A node is not its own peer, so there is nothing to retain, but the
        reason to skip it before the reciprocation check is stronger than
        that: a node offers one walk of its properties at a time, carried
        to completion, and checking reciprocation starts a second walk.
        When the target is the node step 2d is already walking, the two
        have nothing to tell them apart -- the inner one finishing returns
        the node to its starting state, and the outer walk begins again.

        If this test does not complete, the skip has been removed.
        """
        dt = self._run_pipeline(test_outdir, output_name="da-peer-selfref.dts")

        intc = "/amba_pl/visp_ss@b1300000/interrupt-controller@b1310000"
        try:
            node = dt.tree[intc]
        except Exception:
            node = None
        assert node is not None, \
            "the self referencing node was deleted - it is inside the " \
            "accessed subtree and must be retained with it"

        parent = node.propval("interrupt-parent")
        assert parent and parent != [''], \
            "interrupt-parent was stripped from the self referencing node"
        dt.cleanup()
