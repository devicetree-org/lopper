"""
Regression tests for domain_access step 2c: domain subnode phandle refcounting.

Tests the fix for a bug where nodes under /axi referenced by domain-to-domain
subnode properties (carveouts, mbox, timer, elfload) were deleted by the
simple-bus filter because:

  1. The subnodes() scan was too shallow (children_only=True) — relation0 is
     3 levels below the domain node and was never reached.
  2. The /axi parent chain was not marked after setting ref_node.ref = 1,
     so the simple-bus filter saw ref=0 on /axi and deleted it along with
     every child regardless of individual refcounts.

Fixture: lopper/selftest/domain-to-domain-axi-sdt.dts

Copyright (C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.

SPDX-License-Identifier: BSD-3-Clause

Author:
    Bruce Ashfield <bruce.ashfield@amd.com>
"""

import os
import pytest
from lopper import LopperSDT, Lopper


class TestDomainSubnodePhandleRefcounting:
    """Regression tests for domain_access step 2c subnode phandle scan.

    The SDT fixture contains:
      /axi/libmetal_uio_desc0@99c8000  -- referenced by APU_Linux relation0.carveouts
      /axi/libmetal_uio_data@99d0000   -- referenced by APU_Linux relation0.carveouts
      /axi/timer@f1e90000              -- referenced by APU_Linux relation0.timer
      /axi/mailbox@eb360000            -- referenced by APU_Linux relation0.mbox
      /axi/serial@f1920000             -- referenced by APU_Linux access= (step 1a)

    All five must survive after domain_access runs on /domains/APU_Linux.
    """

    SDT = "lopper/selftest/domain-to-domain-axi-sdt.dts"

    def _run_pipeline(self, test_outdir, target="/domains/APU_Linux",
                      output_name="da-phandle-output.dts"):
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

        # Register OpenAMP phandle property descriptors so resolve_phandles()
        # recognises carveouts/mbox/timer/elfload slots.  In production these
        # come from the phandle-desc-v1 block in %.yaml.lop; here we inject
        # them directly so the test is self-contained and order-independent.
        base = Lopper.phandle_possible_properties()
        base.pop("DEFAULT", None)
        for prop, spec in {
            "carveouts": "phandle",
            "elfload":   "phandle",
            "mbox":      "phandle",
            "timer":     "phandle",
            "host":      "phandle",
            "remote":    "phandle",
        }.items():
            base[prop] = [spec]
        Lopper.phandle_possible_prop_dict = base

        device_tree.setup(device_tree.dts, [], "", True, libfdt=True)
        device_tree.target = target
        device_tree.assists_setup(["lopper/assists/domain_access.py"])
        device_tree.assist_autorun_setup("lopper/assists/domain_access", ["-t", target])
        device_tree.perform_lops()

        return device_tree

    def _axi_children(self, tree):
        """Return a set of node names directly under /axi."""
        try:
            axi = tree["/axi"]
        except Exception:
            return set()
        return {child.name for child in axi.subnodes(children_only=True)}

    # ------------------------------------------------------------------
    # Nodes referenced by domain-to-domain phandles must survive
    # ------------------------------------------------------------------

    def test_carveout_nodes_survive(self, test_outdir):
        """Nodes referenced by relation0.carveouts survive after domain_access."""
        dt = self._run_pipeline(test_outdir, output_name="da-carveout.dts")
        children = self._axi_children(dt.tree)
        assert "libmetal_uio_desc0@99c8000" in children, \
            "libmetal_uio_desc0 (carveouts phandle) was deleted — step 2c scan too shallow"
        assert "libmetal_uio_data@99d0000" in children, \
            "libmetal_uio_data (carveouts phandle) was deleted — step 2c scan too shallow"
        dt.cleanup()

    def test_timer_node_survives(self, test_outdir):
        """Node referenced by relation0.timer survives after domain_access."""
        dt = self._run_pipeline(test_outdir, output_name="da-timer.dts")
        children = self._axi_children(dt.tree)
        assert "timer@f1e90000" in children, \
            "timer@f1e90000 (timer phandle) was deleted — step 2c scan too shallow"
        dt.cleanup()

    def test_mbox_node_survives(self, test_outdir):
        """Node referenced by relation0.mbox survives after domain_access."""
        dt = self._run_pipeline(test_outdir, output_name="da-mbox.dts")
        children = self._axi_children(dt.tree)
        assert "mailbox@eb360000" in children, \
            "mailbox@eb360000 (mbox phandle) was deleted — step 2c scan too shallow"
        dt.cleanup()

    def test_axi_bus_parent_survives(self, test_outdir):
        """The /axi simple-bus parent node survives when children are refcounted."""
        dt = self._run_pipeline(test_outdir, output_name="da-axi-parent.dts")
        try:
            axi = dt.tree["/axi"]
        except Exception:
            axi = None
        assert axi is not None, \
            "/axi was deleted — parent chain not marked after step 2c ref_node.ref=1"
        dt.cleanup()

    def test_access_listed_node_survives(self, test_outdir):
        """Node in the domain access= list (step 1a) still survives."""
        dt = self._run_pipeline(test_outdir, output_name="da-access.dts")
        children = self._axi_children(dt.tree)
        assert "serial@f1920000" in children, \
            "serial@f1920000 (access list) was deleted — step 1a regression"
        dt.cleanup()

    def test_elfload_node_survives_in_reserved_memory(self, test_outdir):
        """Node referenced by RPU relation0.elfload survives in /reserved-memory."""
        dt = self._run_pipeline(test_outdir, output_name="da-elfload.dts")
        try:
            resmem = dt.tree["/reserved-memory"]
            names = {child.name for child in resmem.subnodes(children_only=True)}
        except Exception:
            names = set()
        assert "libmetal_elf@9968000" in names, \
            "libmetal_elf@9968000 (elfload phandle) was deleted from /reserved-memory"
        dt.cleanup()
