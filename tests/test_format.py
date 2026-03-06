"""
Pytest migration of format_sanity_test() from lopper_sanity.py

This module contains tests for DTS format/output writing functionality.
Migrated from lopper_sanity.py lines 2328-2333.

Copyright (c) 2019,2020 Xilinx Inc. All rights reserved.
Copyright (C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.

SPDX-License-Identifier: BSD-3-Clause

Author:
    Bruce Ashfield <bruce.ashfield@amd.com>
"""

import os
import pytest
from lopper import LopperSDT


class TestDTSWrite:
    """Test DTS file writing with enhanced mode.

    Reference: lopper_sanity.py:2328-2333
    """

    def test_write_with_enhanced_mode(self, format_lopper_sdt):
        """Test writing DTS file with enhanced processing enabled."""
        # Write the device tree with enhanced mode
        format_lopper_sdt.write(enhanced=True)

        # Verify output file was created
        assert os.path.exists(format_lopper_sdt.output_file), \
            f"Output file not created: {format_lopper_sdt.output_file}"

        # Verify it's a valid DTS file
        with open(format_lopper_sdt.output_file) as f:
            content = f.read()
            assert "/dts-v1/" in content, "DTS file missing /dts-v1/ header"
            assert len(content) > 0, "Output file is empty"
