#/*
# * Copyright (c) 2026 Advanced Micro Devices, Inc. All Rights Reserved.
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

"""RPU TCM layout shared by the Xilinx assists.

The system device tree gives each TCM bank's global address and size, but
not where an RPU core sees the bank: R52 cores set their TCM addresses at
boot through the TCM region registers, and only ZynqMP SDTs map the R5 banks
into the core's address space. The OpenAMP remoteproc and Zephyr assists use
the defaults below, so the firmware they describe and the Linux remoteproc
node agree on where each bank is.
"""

TCM_LOCAL_ORIGINS = {
    "cortexr5": {"ATCM": 0x0, "BTCM": 0x20000},
    "cortexr52": {"ATCM": 0x0, "BTCM": 0x10000, "CTCM": 0x18000},
}
"""dict[str, dict[str, int]]: Core-local address of each TCM bank type."""
