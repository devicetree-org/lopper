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

try:
    from .lopper_lib import node_reg_start_size
except ImportError:
    from lopper_lib import node_reg_start_size

TCM_LOCAL_BANKS = {
    ("cortexr5", "split"): {
        "ATCM": (0x0, 0x10000), "BTCM": (0x20000, 0x10000)},
    ("cortexr52", "split"): {
        "ATCM": (0x0, 0x10000), "BTCM": (0x10000, 0x8000),
        "CTCM": (0x18000, 0x8000)},
}
"""dict[tuple[str, str], dict[str, tuple[int, int]]]: Core-local (origin,
length) of each TCM bank type, by CPU type and cluster mode. Only split mode
is described: section placement does not support a cluster in lockstep yet.
"""

TCM_LOCAL_ORIGINS = {
    cpu_type: {bank: origin for bank, (origin, _) in banks.items()}
    for (cpu_type, mode), banks in TCM_LOCAL_BANKS.items()
    if mode == "split"
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
_TCM_LOCAL_R5_CORE = re.compile(r"r5[_-](\d)[_-]", re.IGNORECASE)


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


R52_TCM_REGION_ENABLE = 0x1
"""int: IMP_xTCMREGIONR bit 0, which enables the bank at its base."""


def r52_tcm_region_words(btcm_origin=None, ctcm_origin=None):
    """Return the IMP_ATCMREGIONR, BTCMREGIONR and CTCMREGIONR words that
    Cortex-R52 startup code writes, keyed ``A``, ``B`` and ``C``.

    A zero word leaves a bank as boot firmware configured it. The ATCM word
    is always zero: the core runs its vectors from the ATCM. A BTCM or CTCM
    the firmware uses is enabled at its core-local origin.
    """
    def word(origin):
        return 0 if origin is None else origin | R52_TCM_REGION_ENABLE
    return {"A": 0, "B": word(btcm_origin), "C": word(ctcm_origin)}


def rpu_cluster_tcm_bases(tree, cpu_type):
    """Return the sorted TCM span bases of the SDT's RPU clusters.

    RPU clusters hold two cores each, in core order, and each cluster's TCM
    lies in one aligned span, so cluster N of the list holds cores 2N and
    2N + 1.
    """
    bases = set()
    for node in tree["/"].subnodes():
        if not is_global_tcm_bank(node.name):
            continue
        start, _ = node_reg_start_size(node)
        if isinstance(start, int):
            bases.add(rpu_cluster_tcm_base(start, cpu_type))
    return sorted(bases)


def rpu_tcm_bank_owner(node, cpu_type, bases):
    """Return (core number, lockstep alias) for a TCM bank node, else None.

    A bank at its global address belongs to the core whose half of its
    cluster's TCM span holds it; ``bases`` comes from
    :func:`rpu_cluster_tcm_bases`. The R5 lockstep banks, such as
    ``psu_r5_0_atcm_lockstep@ffe10000``, lie in core 0's half and are
    flagged as aliases. A bank at a core-local address, such as ZynqMP
    ``psu_r5_0_atcm@0``, belongs to the R5 core its name gives.
    """
    start, _ = node_reg_start_size(node)
    if not isinstance(start, int):
        return None
    alias = "lockstep" in node.name.lower()
    span = RPU_CLUSTER_TCM_SPAN[cpu_type]
    for index, base in enumerate(bases):
        if base <= start < base + span:
            return index * 2 + (start - base) // (span // 2), alias
    match = _TCM_LOCAL_R5_CORE.search(node.name)
    if match and cpu_type == "cortexr5":
        return int(match.group(1)), alias
    return None


def rpu_core_number(cluster_node, cpu_node):
    """Return an RPU core's number across all RPU clusters.

    SDTs give each RPU core its own ``cpus-r5@N`` or ``cpus-r52@N`` cluster
    with a single ``cpu@0``, so N is the core's number. Older SDTs put both
    R5 cores in one cluster; there the core's reg is its number.
    """
    cpus = [n for n in cluster_node.subnodes(children_only=True)
            if n.name.startswith("cpu@")]
    if len(cpus) == 1 and "@" in cluster_node.name:
        try:
            return int(cluster_node.name.split("@")[1], 16)
        except ValueError:
            pass
    reg = cpu_node.propval("reg")
    return reg[0] if reg != [''] else None
