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


class TestAssistFailureDiagnostics:
    """What a failing assist tells you.

    An assist that raises does not stop the run: --werror is how that gets
    promoted to an exit, and a third party assist blowing up the pipeline
    would leave the caller no way to recover. So the message is the whole
    of what a reader gets, and it has to identify the assist and where the
    exception came from.
    """

    def test_assist_is_named_not_repr(self):
        """The assist is named, rather than shown as an object.

        A callable's repr carries a heap address: it identifies nothing a
        reader can act on and differs between runs of the same failure.
        """
        from lopper import _assist_name

        def xlnx_generate_domain_dts(tgt_node, sdt, options):
            pass

        name = _assist_name(xlnx_generate_domain_dts)
        assert "xlnx_generate_domain_dts" in name
        assert "0x" not in name, f"an address leaked into the name: {name}"
        assert "<function" not in name, f"the repr was used: {name}"

    def test_source_loaded_assist_is_not_qualified_with_py(self):
        """An assist loaded from a file carries a filename as its module.

        SourceFileLoader sets __module__ to something like
        'gen_domain_dts.py', so reducing a dotted path to its last component
        without dropping the suffix first yields 'py' as the qualifier.
        """
        from lopper import _assist_name

        def some_assist():
            pass
        some_assist.__module__ = "gen_domain_dts.py"

        name = _assist_name(some_assist)
        assert not name.startswith("py."), \
            f"the file suffix was taken as the module: {name}"
        assert "gen_domain_dts" in name

    def test_origin_is_where_it_raised_not_where_it_was_caught(self):
        """The reported location is the raise site.

        sys.exc_info() hands back a traceback whose tb_frame is the frame
        doing the catching, so reporting from it names the except clause --
        the same answer for every failure caught there. The traceback chains
        outwards through tb_next, so the origin is the innermost frame.
        """
        from lopper import _exception_origin

        def raises_here():
            raise KeyError("/__symbols__")

        try:
            raises_here()
        except Exception as e:
            described = _exception_origin(e)

        assert described, "nothing was described"
        innermost = described[-1]
        assert "raises_here" in innermost, \
            f"the raise site was not reported: {described}"
        assert "test_assists.py" in innermost, \
            f"the origin file was not reported: {described}"

    def test_origin_includes_the_path_into_the_failure(self):
        """More than the innermost frame, so the caller is visible too.

        An assist that raises inside lopper core surfaces at a core line
        where the assist's own name does not appear, and knowing only that
        is not enough to find which assist to look at.
        """
        from lopper import _exception_origin

        def inner():
            raise ValueError("boom")

        def outer():
            inner()

        try:
            outer()
        except Exception as e:
            described = _exception_origin(e)

        joined = " ".join(described)
        assert "inner" in joined and "outer" in joined, \
            f"the path into the failure was lost: {described}"

    def test_no_traceback_is_not_an_error(self):
        """An exception with no traceback describes nothing, quietly.

        This runs inside an exception handler, so it must not raise a second
        one while reporting the first.
        """
        from lopper import _exception_origin

        assert _exception_origin(ValueError("never raised")) == []
