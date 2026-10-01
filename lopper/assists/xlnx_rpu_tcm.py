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

import re

TCM_LOCAL_ORIGINS = {
    "cortexr5": {"ATCM": 0x0, "BTCM": 0x20000},
    "cortexr52": {"ATCM": 0x0, "BTCM": 0x10000, "CTCM": 0x18000},
}
"""dict[str, dict[str, int]]: Core-local address of each TCM bank type."""

RPU_CLUSTER_TCM_SPAN = {
    "cortexr5": 0x100000,
    "cortexr52": 0x80000,
}
"""dict[str, int]: Global address span of one RPU cluster's TCM.

Each RPU cluster's banks lie in one naturally aligned span, whose base is the
cluster's core 0 ATCM: 0xffe00000 for the R5 cluster, and 0xeba00000,
0xeba80000, 0xebb00000, ... for the R52 clusters.
"""

_TCM_BANK = re.compile(r"([abc])tcm", re.IGNORECASE)
_TCM_GLOBAL_BANK = re.compile(r"[abc]tcm[_-]global", re.IGNORECASE)


def rpu_cpu_type(cpu_node):
    """Return ``cortexr5`` or ``cortexr52`` for an RPU CPU node, else None."""
    compatible = cpu_node.propval("compatible", list)
    if "arm,cortex-r52" in compatible:
        return "cortexr52"
    if "arm,cortex-r5" in compatible:
        return "cortexr5"
    return None


def tcm_bank_type(name):
    """Return ATCM, BTCM or CTCM for a TCM bank node name, else None."""
    match = _TCM_BANK.search(name)
    return f"{match.group(1).upper()}TCM" if match else None


def is_global_tcm_bank(name):
    """Return True for a TCM bank at its global address, such as
    ``r52_0a_atcm_global`` or ``psu_r5_0_atcm_global``."""
    return bool(_TCM_GLOBAL_BANK.search(name))


def rpu_cluster_tcm_base(address, cpu_type):
    """Return the base of the RPU cluster TCM span holding ``address``."""
    return address & ~(RPU_CLUSTER_TCM_SPAN[cpu_type] - 1)
