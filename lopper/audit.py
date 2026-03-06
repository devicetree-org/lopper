#/*
# * Copyright (c) 2024,2025,2026 Xilinx Inc. All rights reserved.
# *
# * Author:
# *       Bruce Ashfield <bruce.ashfield@xilinx.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

"""
Lopper audit module - backwards compatibility shim.

This module has been restructured into a package (lopper/audit/).
This file provides backwards compatibility for code that imports
lopper.audit directly.

The actual implementation is in:
- lopper/audit/core.py: Phandle validation, basic tree checks
- lopper/audit/memory.py: Memory region validation
- lopper/audit/memviz.py: Memory map visualization
"""

# Import everything from the audit package
from lopper.audit import *
