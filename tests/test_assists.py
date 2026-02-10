"""
Pytest migration of assists_sanity_test() from lopper_sanity.py

This module contains tests for assist (transformation module) functionality.
Tests both built-in assists and external assist loading.
Migrated from lopper_sanity.py lines 2306-2327 and 2742-2803.

Copyright (c) 2019,2020 Xilinx Inc. All rights reserved.
Copyright (C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.

SPDX-License-Identifier: BSD-3-Clause

Author:
    Bruce Ashfield <bruce.ashfield@amd.com>
"""

import os
import pytest
from lopper import LopperSDT


class TestBuiltInAssists:
    """Test built-in assist loading and execution.

    Reference: lopper_sanity.py:2760
    """

    def test_builtin_assist_with_lop(self, test_outdir):
        """Test loading built-in domain_access assist via lop."""
        # Check if libfdt is available
        libfdt_available = False
        try:
            import libfdt
            libfdt_available = True
        except ImportError:
            pytest.skip("libfdt not available")

        # Setup system device tree and lop file
        import lopper_sanity
        dt = lopper_sanity.setup_system_device_tree(test_outdir)
        lop_file = lopper_sanity.setup_assist_lops(test_outdir)

        sdt = LopperSDT(dt)
        sdt.dryrun = False
        sdt.verbose = 0
        sdt.werror = False
        sdt.output_file = test_outdir + "/assist-output.dts"
        sdt.cleanup_flag = True
        sdt.save_temps = False
        sdt.enhanced = True
        sdt.outdir = test_outdir
        sdt.use_libfdt = libfdt_available

        # Setup with lop file
        sdt.setup(sdt.dts, [lop_file], "", True, libfdt=libfdt_available)
        sdt.assists_setup(["lopper/assists/domain_access.py"])

        # Perform lops - should not raise exception
        sdt.perform_lops()

        # Write output
        if sdt.output_file:
            sdt.write(enhanced=True)

        # Verify output was created
        assert os.path.exists(sdt.output_file), "Assist output file not created"

        sdt.cleanup()


class TestExternalAssists:
    """Test external assist loading and execution.

    Reference: lopper_sanity.py:2763, 2770
    """

    def test_external_assist_domain_access(self, test_outdir):
        """Test loading and running external assist-sanity.py."""
        # Check if libfdt is available
        libfdt_available = False
        try:
            import libfdt
            libfdt_available = True
        except ImportError:
            pytest.skip("libfdt not available")

        # Setup system device tree
        import lopper_sanity
        dt = lopper_sanity.setup_system_device_tree(test_outdir)

        sdt = LopperSDT(dt)
        sdt.dryrun = False
        sdt.verbose = 0
        sdt.werror = False
        sdt.output_file = test_outdir + "/assist-external-output.dts"
        sdt.cleanup_flag = True
        sdt.save_temps = False
        sdt.enhanced = True
        sdt.outdir = test_outdir
        sdt.use_libfdt = libfdt_available

        # Setup without lop file
        sdt.setup(sdt.dts, [], "", True, libfdt=libfdt_available)

        # Add selftest to load paths
        sdt.load_paths.append("lopper/selftest/")

        # Load external assist
        sdt.assists_setup(["assist-sanity.py"])

        # Setup autorun
        sdt.assist_autorun_setup("assist-sanity", ["domain_access_test"])

        # Perform lops - should not raise exception
        sdt.perform_lops()

        # Write output
        if sdt.output_file:
            sdt.write(enhanced=True)

        # Verify output was created
        assert os.path.exists(sdt.output_file), "External assist output file not created"

        sdt.cleanup()

    def test_external_assist_overlay(self, test_outdir):
        """Test external assist with overlay operations."""
        # Check if libfdt is available and if overlay test file exists
        libfdt_available = False
        try:
            import libfdt
            libfdt_available = True
        except ImportError:
            pytest.skip("libfdt not available")

        if not os.path.exists("./lopper/selftest/system-top.dts"):
            pytest.skip("Test file system-top.dts not available")

        sdt = LopperSDT("./lopper/selftest/system-top.dts")
        sdt.dryrun = False
        sdt.verbose = 0
        sdt.werror = False
        sdt.output_file = test_outdir + "/assist-overlay-output.dts"
        sdt.cleanup_flag = True
        sdt.save_temps = False
        sdt.enhanced = True
        sdt.outdir = test_outdir
        sdt.use_libfdt = libfdt_available

        # Setup
        sdt.setup(sdt.dts, [], "", True, libfdt=libfdt_available)
        sdt.load_paths.append("lopper/selftest/")
        sdt.assists_setup(["assist-sanity.py"])
        sdt.assist_autorun_setup("assist-sanity", ["overlay_test"])

        # Execute
        sdt.perform_lops()

        if sdt.output_file:
            sdt.write(enhanced=True)

        sdt.cleanup()
