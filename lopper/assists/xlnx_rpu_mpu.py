#/*
# * Copyright (c) 2026 Advanced Micro Devices, Inc. All rights reserved.
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

"""Generate the initial MPU table of a standalone or FreeRTOS RPU domain.

The standalone BSP for Cortex-R5 and Cortex-R52 defines a weak
``InitialMpu_Config`` table that ``Init_MPU()`` programs at boot. When the
memories of a ``baremetal`` or ``freertos`` domain carry ``mpu-policy``,
this assist writes ``amd_mpu_config.c`` with a strong ``InitialMpu_Config``:
the BSP's own default table for the SoC, plus the domain's policies and the
OpenAMP and libmetal carveouts that the RPU maps non-cacheable at run time.
Without ``mpu-policy`` nothing is written and the BSP default applies.

Invocation, on the tree assembled from the SDT and the domain YAML::

    lopper -f --enhanced --auto -i domain.yaml system-top.dts out.dts
    lopper -f --enhanced -O <app src> out.dts unused.dts -- xlnx_rpu_mpu \\
        --domain=/domains/RPU0 [--output=amd_mpu_config.c] \\
        [--free-regions=N] [--aie]
"""

import argparse
from dataclasses import dataclass, replace
import os
import re
import sys

from lopper.log import _error, _info

sys.path.append(os.path.dirname(__file__))

from baremetalconfig_xlnx import get_label
from lopper_lib import resolve_domain_cpus
from xlnx_rpu_tcm import rpu_cpu_type, tcm_bank_type
from zephyr_memory import (
    LayoutError, Memory, MemoryPolicy, _domain_memory_nodes, _memory_kind,
    _memory_policy, _memory_range, resolve_memory_node,
)
from zephyr_mpu import r5_mpu_aperture, validate_memory_overlaps

MAX_REGIONS = 16
"""int: MPU regions on Cortex-R5 and Cortex-R52 (MAX_POSSIBLE_MPU_REGS)."""

DEFAULT_FREE_REGIONS = {"cortexr5": 5, "cortexr52": 3}
"""dict[str, int]: Regions left free for run-time Xil_MemMap() by default.

Cortex-R5: libmetal maps every I/O region through Xil_MemMap(), which always
takes a new region. The OpenAMP RPMsg demos map 5 (IPI, resource table,
shared memory, two vrings) and the libmetal demo maps 5 (IPI, two
descriptor areas, data, TTC).

Cortex-R52: libmetal skips a mapping that one existing region already
covers with the same attributes, so the carveouts in this table cost
nothing at run time. A mapping inside a larger region, such as the resource
table inside the firmware's DDR, splits it and needs 3 free regions.
"""

OUTPUT_NAME = "amd_mpu_config.c"
ACTIVE_OS_TYPES = ("baremetal", "freertos")
R52_ALIGNMENT = 64

RW = "PRIV_RW_USER_RW"
R5_NORMAL_WB = f"NORM_NSHARED_WB_WA | {RW}"
R5_DEVICE = f"DEVICE_NONSHARED | {RW}"
R52_NORMAL_WT = f"NORM_NSHARED_WT_NWA | {RW}"
R52_DEVICE = f"DEVICE_NONSHARED | {RW}"
IPC_SHARED_NCACHE = f"NORM_SHARED_NCACHE | {RW}"
IPC_NSHARED_NCACHE = f"NORM_NSHARED_NCACHE | {RW}"

KB = 1 << 10
MB = 1 << 20
GB = 1 << 30


class MpuError(ValueError):
    """Report an MPU table that cannot be generated."""


@dataclass(frozen=True)
class Region:
    """One MPU region: base, size in bytes, attribute macros and origin."""

    base: int
    size: int
    attribute: str
    name: str
    origin: str = "baseline"

    @property
    def end(self):
        """int: First address after the region."""
        return self.base + self.size


def is_compat(node, compat_string_to_test):
    """Register the generator for the ``xlnx_rpu_mpu`` command name."""
    if re.search(r"module,xlnx_rpu_mpu$", compat_string_to_test):
        return generate_mpu_config
    return ""


def r5_size_macro(size):
    """Return the Cortex-R5 REGION_* macro for a power-of-two size."""
    if size < 32 or size > 4 * GB or size & (size - 1):
        raise MpuError(f"0x{size:x} is not a Cortex-R5 MPU region size")
    for unit, suffix in ((GB, "G"), (MB, "M"), (KB, "K"), (1, "B")):
        if size >= unit:
            return f"REGION_{size // unit}{suffix}"
    raise MpuError(f"0x{size:x} is not a Cortex-R5 MPU region size")


def _r5_lookup(size):
    """Round a DDR size up to a region size, as mpu_r5.c's lookup does."""
    return r5_mpu_aperture(0, max(size, 1))[1]


def _c_ddr_size(size, high):
    """Return a DDR size as mpu_r5.c computes it from 32-bit XPAR values.

    The XPAR addresses are 32-bit constants, so the size of a DDR ending
    at 0xffffffff wraps to 0, which the lookup maps to a 32-byte region.
    """
    return size & 0xffffffff if high <= 0xffffffff else size


def zynqmp_r5_baseline(ddr):
    """Return the table that ZynqMP mpu_r5.c Init_MPU() programs today.

    Args:
        ddr (tuple[int, int] | None): XPAR_PSU_DDR_0 base and high address,
            as written to xmem_config.h, or None for a DDR-less design.
    """
    base = 0
    create_tcm = False
    if ddr is None:
        # DDR-less: map the TCM only.
        size = 256 * KB
    else:
        low, high = ddr
        ddr_size = _c_ddr_size(high - low + 1, high)
        if ddr_size < 2 * GB:
            size = _r5_lookup(ddr_size)
            # DDR not at 0x100000: map it from its own base and give the TCM
            # at 0x0 its own region.
            if low != 0x100000:
                create_tcm = True
                base = low
        else:
            size = 2 * GB
    regions = []
    if create_tcm:
        regions.append(Region(0x0, 256 * KB, R5_NORMAL_WB, "TCM"))
    regions += [
        Region(base, size, R5_NORMAL_WB, "DDR" if ddr else "TCM"),
        Region(0x80000000, 1 * GB, f"STRONG_ORDERD_SHARED | {RW}", "PL"),
        Region(0xC0000000, 512 * MB, R5_DEVICE, "QSPI"),
        Region(0xE0000000, 256 * MB, R5_DEVICE, "PCIe low"),
        Region(0xF8000000, 16 * MB, R5_DEVICE, "STM CoreSight"),
        Region(0xF9000000, 1 * MB, R5_DEVICE, "RPU GIC"),
        Region(0xFD000000, 16 * MB, R5_DEVICE, "FPD slaves"),
        Region(0xFE000000, 16 * MB, R5_DEVICE, "Upper LPD slaves"),
        Region(0xFF000000, 16 * MB, R5_DEVICE,
               "Lower LPD slaves, CSU, PMU, TCM, OCM"),
        Region(0xFFFC0000, 256 * KB, R5_NORMAL_WB, "OCM"),
    ]
    return regions


def versal_r5_baseline(ddr, aie=False):
    """Return the table that Versal mpu_r5.c Init_MPU() programs today.

    Args:
        ddr (tuple[int, int] | None): XPAR_AXI_NOC_DDR_LOW_0 (or NoC2) base
            and high address, or None for a DDR-less design.
        aie (bool): XPAR_AIE_NUM_INSTANCES is defined.
    """
    if ddr is None:
        size = 256 * KB
    else:
        # mpu_r5.c maps DDR from 0x0 up to its high address.
        ddr_size = _c_ddr_size(ddr[1] + 1, ddr[1])
        if ddr_size < 2 * GB:
            size = _r5_lookup(ddr_size)
        else:
            size = 1 * GB if aie else 2 * GB
    regions = [
        Region(0x0, size, R5_NORMAL_WB, "DDR" if ddr else "TCM"),
        Region(0x80000000, 1 * GB, f"STRONG_ORDERD_SHARED | {RW}", "PL"),
        Region(0xC0000000, 512 * MB, R5_DEVICE, "QSPI"),
        Region(0xE0000000, 256 * MB, R5_DEVICE, "PCIe low"),
        Region(0xF0000000, 128 * MB, R5_DEVICE, "PMC"),
        Region(0xF8000000, 16 * MB, R5_DEVICE, "STM CoreSight"),
        Region(0xF9000000, 1 * MB, R5_DEVICE, "RPU GIC"),
        Region(0xFD000000, 16 * MB, R5_DEVICE, "FPD slaves"),
        Region(0xFE000000, 16 * MB, R5_DEVICE, "Upper LPD slaves"),
        Region(0xFF000000, 16 * MB, R5_DEVICE,
               "Lower LPD slaves, CSU, PMU, TCM, OCM"),
    ]
    if aie:
        regions.append(Region(0x40000000, 1 * GB, R5_DEVICE, "AIE"))
    regions.append(Region(0xFFFC0000, 256 * KB, R5_NORMAL_WB, "OCM"))
    return regions


def versal_net_r52_baseline():
    """Return the weak InitialMpu_Config of versal-net/mpu_r52.c."""
    return [
        Region(0x00000000, 0x80000000, R52_NORMAL_WT, "DDR"),
        Region(0x80000000, 0x20000000, R52_DEVICE, "LPD to PL AXI"),
        Region(0xA0000000, 0x18000000, R52_DEVICE, "PCIe, FPD to PL AXI"),
        Region(0xBBF00000, 0x00100000, R52_NORMAL_WT, "OCM"),
        Region(0xC0000000, 0x21000000, R52_DEVICE, "OSPI, CoreSight"),
        Region(0xE2000000, 0x00200000, R52_DEVICE, "RPU GIC"),
        Region(0xE4000000, 0x01000000, R52_DEVICE, "CPM"),
        Region(0xE6000000, 0x00800000, R52_DEVICE, "HNIC"),
        Region(0xEA000000, 0x03000000, R52_DEVICE, "FPD, LPD"),
        Region(0xF0000000, 0x08000000, R52_DEVICE, "PMC"),
        Region(0xF8000000, 0x04000000, R52_DEVICE, "FPD CMN"),
    ]


def versal_2ve_2vm_r52_baseline():
    """Return the weak InitialMpu_Config of versal_2ve_2vm/mpu_r52.c."""
    return [
        Region(0x00000000, 0x80000000, R52_NORMAL_WT, "DDR"),
        Region(0x80000000, 0x20000000, R52_DEVICE, "LPD to PL AXI"),
        Region(0xA0000000, 0x18000000, R52_DEVICE, "PCIe, FPD to PL AXI"),
        Region(0xBBE00000, 0x00200000, R52_NORMAL_WT, "OCM"),
        Region(0xC0000000, 0x21000000, R52_DEVICE, "xSPI, CoreSight"),
        Region(0xE2000000, 0x00200000, R52_DEVICE, "RPU GIC"),
        Region(0xE8000000, 0x00800000, R52_DEVICE, "VCU, ISP"),
        Region(0xEA000000, 0x04000000, R52_DEVICE, "FPD, LPD, MMI"),
        Region(0xF0000000, 0x0C000000, R52_DEVICE, "PMC, FPD CMN"),
    ]


SOCS = {
    ("ZynqMP", "cortexr5"): "zynqmp",
    ("Versal", "cortexr5"): "versal",
    ("VersalNet", "cortexr52"): "versal-net",
    ("Versal_2VE_2VM", "cortexr52"): "versal_2ve_2vm",
}
"""dict: (SDT family, CPU) pairs the standalone BSP has a table for."""


def baseline(soc, ddr=None, aie=False):
    """Return the BSP default MPU table for a SoC key of ``SOCS``."""
    if soc == "zynqmp":
        return zynqmp_r5_baseline(ddr)
    if soc == "versal":
        return versal_r5_baseline(ddr, aie)
    if soc == "versal-net":
        return versal_net_r52_baseline()
    if soc == "versal_2ve_2vm":
        return versal_2ve_2vm_r52_baseline()
    raise MpuError(f"no MPU baseline for SoC '{soc}'")


def policy_attribute(policy, processor):
    """Translate a parsed ``mpu-policy`` into standalone BSP attributes.

    Description:
        The standalone counterpart of zephyr_mpu's Zephyr attribute
        encoding. The policy is parsed and checked by zephyr_memory, so the
        same values and combinations are accepted for every OS.
        ``cacheable`` selects the cacheable type of the BSP's own DDR
        region: write-back write-allocate on Cortex-R5, write-through on
        Cortex-R52. ``shareable`` selects the shareable type. Standalone
        and FreeRTOS run privileged, so ``writable`` maps to the
        PRIV_RW_USER_RW access the BSP and the demos use everywhere, and a
        region without ``writable`` is PRIV_RO_USER_RO. ``executable`` and
        ``userspace`` do not change the attributes: the BSP sets no
        execute-never bits, and run-time mappings match only an identical
        attribute.

    Args:
        policy (MemoryPolicy): Parsed policy bits.
        processor (str): ``cortexr5`` or ``cortexr52``.

    Returns:
        str: Attribute expression such as ``NORM_SHARED_NCACHE | PRIV_RW_USER_RW``.
    """
    shared = "" if policy & MemoryPolicy.SHAREABLE else "N"
    if policy & MemoryPolicy.CACHEABLE:
        cache = "WB_WA" if processor == "cortexr5" else "WT_NWA"
    else:
        cache = "NCACHE"
    access = RW if policy & MemoryPolicy.WRITABLE else "PRIV_RO_USER_RO"
    return f"NORM_{shared}SHARED_{cache} | {access}"


def _relation_carveouts(tree, domain):
    """Return {path: kind} for memories named by the domain's relations.

    kind is ``vring``, ``buffer``, ``rsc_table``, ``libmetal`` or
    ``elfload``.
    """
    kinds = {}
    for node in domain.subnodes():
        compatible = " ".join(node.parent.propval("compatible", list)
                              if node.parent else [])
        for prop in ("carveouts", "elfload"):
            values = node.propval(prop, list)
            if not values or values == [""]:
                continue
            for value in values:
                try:
                    memory = (tree.pnode(value) if isinstance(value, int)
                              else resolve_memory_node(tree, value))
                except LayoutError:
                    memory = None
                if memory is None:
                    raise MpuError(
                        f"{node.abs_path}: cannot resolve {prop} entry "
                        f"{value}")
                name = memory.name.lower()
                if prop == "elfload":
                    kind = "elfload"
                elif "rsc" in name or "resource" in name:
                    kind = "rsc_table"
                elif "vring" in name:
                    kind = "vring"
                elif "libmetal" in compatible:
                    kind = "libmetal"
                else:
                    kind = "buffer"
                kinds.setdefault(memory.abs_path, kind)
    return kinds


IPC_DEFAULT_ATTRIBUTES = {
    "vring": IPC_SHARED_NCACHE,
    "buffer": IPC_SHARED_NCACHE,
    "libmetal": IPC_SHARED_NCACHE,
    "rsc_table": IPC_NSHARED_NCACHE,
}
"""dict[str, str]: Attributes of carveouts without mpu-policy.

These are the attributes the openamp-system-reference RPU demos give the
same memory at run time: remoteproc_mmap() default and libmetal shared
memory use NORM_SHARED_NCACHE, the resource table NORM_NSHARED_NCACHE.
"""


def domain_regions(tree, domain, processor):
    """Collect the regions a domain asks for.

    Description:
        Considers the memories in the domain's ``sram`` and
        ``reserved-memory`` and the carveouts and ``elfload`` memories of
        its relations. A memory with ``mpu-policy`` gets a region with that
        policy, except for ``static`` and TCM banks, which the BSP maps.
        An OpenAMP or libmetal carveout without ``mpu-policy`` gets the
        attributes its demo maps it with at run time.

    Returns:
        tuple[bool, list[Region]]: Whether any memory of the domain carries
        mpu-policy, and the regions to add to the baseline.
    """
    try:
        nodes = list(_domain_memory_nodes(tree, domain))
    except LayoutError:
        nodes = []
    carveouts = _relation_carveouts(tree, domain)
    for path in carveouts:
        if all(node.abs_path != path for node in nodes):
            nodes.append(tree[path])

    opted_in = False
    memories = []
    attributes = {}
    for node in nodes:
        has_policy = "mpu-policy" in node.__props__
        kind = carveouts.get(node.abs_path)
        if has_policy:
            opted_in = True
        if tcm_bank_type(node.name) or tcm_bank_type(node.label or ""):
            # The BSP maps TCM: R5 within its DDR or TCM region, R52
            # through _TCM_*_REGION.
            continue
        policy = MemoryPolicy.NONE
        if has_policy:
            if "static" in [str(value) for value in
                            node.propval("mpu-policy", list)]:
                continue
            policy = _memory_policy(node, _memory_kind(node))
            attribute = policy_attribute(policy, processor)
            origin = "mpu-policy"
        elif kind in IPC_DEFAULT_ATTRIBUTES:
            attribute = IPC_DEFAULT_ATTRIBUTES[kind]
            origin = f"{kind} default"
        else:
            continue
        base, size = _memory_range(node)
        if size <= 0:
            raise MpuError(f"{node.abs_path}: empty memory range")
        if base + size > 1 << 32:
            raise MpuError(
                f"{node.abs_path}: 0x{base:x}+0x{size:x} is outside the "
                "32-bit RPU address space")
        memory = Memory(node.name, node, base, size, policy,
                        kind or _memory_kind(node))
        memories.append(memory)
        attributes[memory.node.abs_path] = (attribute, origin)
    validate_memory_overlaps(memories)
    return opted_in, [
        Region(memory.origin, memory.length,
               attributes[memory.node.abs_path][0], memory.name,
               attributes[memory.node.abs_path][1])
        for memory in memories]


def _r5_representable(region):
    """Return True for a power-of-two sized, size-aligned R5 region."""
    try:
        return r5_mpu_aperture(region.base, region.size) == \
            (region.base, region.size)
    except LayoutError:
        return False


def _merge_adjacent(regions, processor):
    """Merge address-contiguous regions with equal attributes.

    On Cortex-R5 two regions are merged only if the result is still a
    power-of-two sized, size-aligned region.
    """
    merged = []
    for region in sorted(regions, key=lambda r: (r.base, r.size)):
        if merged and merged[-1].end == region.base and \
                merged[-1].attribute == region.attribute:
            candidate = Region(merged[-1].base,
                               merged[-1].size + region.size,
                               region.attribute,
                               f"{merged[-1].name} + {region.name}",
                               merged[-1].origin)
            if processor != "cortexr5" or _r5_representable(candidate):
                merged[-1] = candidate
                continue
        merged.append(region)
    return merged


def _check_additions(regions, processor):
    """Validate added regions against the CPU's MPU rules."""
    for region in regions:
        if processor == "cortexr5" and not _r5_representable(region):
            base, size = r5_mpu_aperture(region.base, region.size)
            raise MpuError(
                f"{region.name} [0x{region.base:x}, 0x{region.end:x}) is "
                "not a Cortex-R5 MPU region: the size must be a power of "
                "two and the base aligned to it; the smallest region "
                f"covering it is [0x{base:x}, 0x{base + size:x}). Align "
                "the carveout, or give it mpu-policy 'static' to leave it "
                "to run-time mapping")
        if processor == "cortexr52" and (
                region.base % R52_ALIGNMENT or region.size % R52_ALIGNMENT):
            raise MpuError(
                f"{region.name} [0x{region.base:x}, 0x{region.end:x}) is "
                "not a Cortex-R52 MPU region: base and size must be "
                f"multiples of {R52_ALIGNMENT} bytes")


def _covering_attribute_r5(table, region):
    """Return the attribute R5 applies to all of region, or None if mixed.

    The highest-numbered matching region wins on Cortex-R5.
    """
    for entry in reversed(table):
        if entry.base < region.end and region.base < entry.end:
            if entry.base <= region.base and region.end <= entry.end:
                return entry.attribute
            return None
    return None


def _carve(table, additions):
    """Remove additions from R52 baseline regions, splitting them."""
    result = []
    for entry in table:
        pieces = [(entry.base, entry.end)]
        for add in additions:
            next_pieces = []
            for start, end in pieces:
                if add.end <= start or end <= add.base:
                    next_pieces.append((start, end))
                    continue
                if start < add.base:
                    next_pieces.append((start, add.base))
                if add.end < end:
                    next_pieces.append((add.end, end))
            pieces = next_pieces
        for start, end in pieces:
            result.append(replace(entry, base=start, size=end - start))
    return result


def build_table(soc, processor, additions, ddr=None, aie=False):
    """Combine the SoC baseline with the domain's regions.

    Returns:
        list[Region]: Regions in programming order.
    """
    table = baseline(soc, ddr, aie)
    kept = []
    for region in additions:
        if processor == "cortexr5":
            same = _covering_attribute_r5(table, region) == region.attribute
        else:
            same = any(entry.base <= region.base and region.end <= entry.end
                       and entry.attribute == region.attribute
                       for entry in table)
        if same:
            _info(f"xlnx_rpu_mpu: {region.name} already has "
                  f"{region.attribute} in the default table")
            continue
        kept.append(region)
    kept = _merge_adjacent(kept, processor)
    _check_additions(kept, processor)
    if processor == "cortexr5":
        # Later regions take priority on Cortex-R5.
        return table + kept
    # Cortex-R52 regions must not overlap.
    return sorted(_carve(table, kept) + kept, key=lambda r: r.base)


def format_table(table):
    """Return a readable region list for messages."""
    lines = []
    for index, region in enumerate(table):
        lines.append(
            f"  {index:2d}: 0x{region.base:08x}-0x{region.end - 1:08x} "
            f"({region.size:#x}) {region.attribute}  "
            f"[{region.name}, {region.origin}]")
    return "\n".join(lines)


def check_fit(table, free_regions):
    """Fail when the table leaves fewer than free_regions regions."""
    if len(table) + free_regions > MAX_REGIONS:
        raise MpuError(
            f"the MPU table needs {len(table)} regions and {free_regions} "
            f"must stay free for run-time mappings, but there are only "
            f"{MAX_REGIONS}:\n{format_table(table)}\nRemove or merge "
            "regions, or lower --free-regions if the application maps "
            "fewer regions at run time")


def render(table, processor, soc, domain_path, cpu_name, free_regions):
    """Render amd_mpu_config.c."""
    lines = [
        "/*",
        " * Copyright (c) 2026 Advanced Micro Devices, Inc. All Rights "
        "Reserved.",
        " * SPDX-License-Identifier: MIT",
        " *",
        f" * Generated by Lopper xlnx_rpu_mpu for {domain_path}",
        f" * ({cpu_name}, {soc}). Do not edit; change mpu-policy in the "
        "domain YAML.",
        " *",
        " * Overrides the standalone BSP's weak InitialMpu_Config. "
        f"{len(table)} of {MAX_REGIONS}",
        f" * MPU regions are used and {MAX_REGIONS - len(table)} are left "
        f"for run-time Xil_MemMap() ({free_regions} required).",
        " */",
        "",
        "#include \"xil_types.h\"",
        "#include \"xreg_cortexr5.h\"",
        "#include \"xil_mpu.h\"",
        "",
        "XMpuConfig_Initial InitialMpu_Config "
        "__attribute__((section(\".bootdata\"))) = {",
    ]
    for region in table:
        if processor == "cortexr5":
            size = r5_size_macro(region.size)
        else:
            size = f"0x{region.size - 1:X}U"
        comment = region.name
        if region.origin != "baseline":
            comment += f" ({region.origin})"
        lines += [
            "\t{",
            f"\t\t/* {comment} */",
            f"\t\t0x{region.base:08X}U,",
            f"\t\t{size},",
            f"\t\t{region.attribute},",
            "\t},",
        ]
    if len(table) < MAX_REGIONS:
        lines += ["\t{", "\t\t0U", "\t},"]
    lines += ["};", ""]
    return "\n".join(lines)


def _domain_cpu(tree, domain):
    """Return the domain's first CPU; lockstep domains select both cores."""
    selection = resolve_domain_cpus(tree, domain)
    if not selection.cpus:
        raise MpuError(f"{domain.abs_path}: {selection.diagnostic or 'no CPU'}")
    cpu = selection.cpus[0]
    if not rpu_cpu_type(cpu):
        raise MpuError(
            f"{domain.abs_path}: {cpu.abs_path} is not a Cortex-R5 or "
            "Cortex-R52 CPU")
    return cpu


def _ddr_range(sdt, cpu_name, soc):
    """Return the DDR range the BSP's xmem_config.h gives mpu_r5.c.

    Uses the same memory scan as baremetal_bspconfig_xlnx, which writes
    XPAR_<memory>_BASEADDRESS and _HIGHADDRESS (start + size - 1).
    """
    from baremetallinker_xlnx import get_memranges
    mem_ranges, _ = get_memranges("/", sdt, {"args": [cpu_name, "", "xparam"]})
    keys = (("psu_ddr_0",) if soc == "zynqmp" else
            ("axi_noc_ddr_low_0", "axi_noc2_ddr_low_0"))
    # NoC keys carry an upper-case channel suffix, e.g. axi_noc_DDR_LOW_0.
    ranges = {key.lower(): value for key, value in (mem_ranges or {}).items()}
    for key in keys:
        if key in ranges:
            start, size = ranges[key]
            return start, start + size - 1
    return None


def _argument_parser():
    parser = argparse.ArgumentParser(
        prog="xlnx_rpu_mpu", description=__doc__.splitlines()[0])
    parser.add_argument("--domain", required=True,
                        help="absolute /domains/... path of the RPU domain")
    parser.add_argument("--output", default=OUTPUT_NAME,
                        help="output file, relative to the output directory")
    parser.add_argument("--free-regions", type=int,
                        help="MPU regions to leave free for run-time "
                        "mappings (default: Cortex-R5 5, Cortex-R52 3)")
    parser.add_argument("--aie", action="store_true",
                        help="Versal R5: the BSP defines "
                        "XPAR_AIE_NUM_INSTANCES")
    parser.add_argument("-v", "--verbose", action="count", default=0)
    return parser


def generate_mpu_config(root_node, sdt, options):
    """Generate the table, exiting nonzero on invalid supplied content."""
    if not _generate_mpu_config(root_node, sdt, options):
        _error("xlnx_rpu_mpu: MPU generation failed; see the errors above",
               also_exit=1)
    return True


def _generate_mpu_config(root_node, sdt, options):
    """Write amd_mpu_config.c for one standalone or FreeRTOS RPU domain.

    Returns:
        bool: True on success, including when the domain has no
        mpu-policy and nothing is written; False after reporting an error.
    """
    try:
        args = _argument_parser().parse_args(options.get("args", []))
    except SystemExit as exc:
        if exc.code == 0:
            return True
        _error("xlnx_rpu_mpu: invalid arguments")
        return False
    try:
        base_tree = sdt.tree
        try:
            domain = base_tree[args.domain]
        except Exception as exc:
            raise MpuError(f"domain '{args.domain}' was not found") from exc
        os_type = (domain.propval("os,type", list) or [""])[0]
        if os_type not in ACTIVE_OS_TYPES:
            raise MpuError(
                f"{domain.abs_path}: os,type is '{os_type}'; amd_mpu_config.c"
                " is for baremetal and freertos domains")
        activation = (domain.propval("lopper,activate", list) or [""])[0] \
            or os_type
        tree = base_tree.overlay_tree(activation) or base_tree
        domain = tree[args.domain]

        cpu = _domain_cpu(tree, domain)
        processor = rpu_cpu_type(cpu)
        family = (tree["/"].propval("family", list) or [""])[0]
        soc = SOCS.get((family, processor))
        try:
            cpu_name = get_label(sdt, base_tree["/__symbols__"], cpu)
        except KeyError:
            cpu_name = None
        cpu_name = cpu_name or cpu.label or cpu.name

        opted_in, additions = domain_regions(tree, domain, processor)
        if not opted_in:
            _info(f"xlnx_rpu_mpu: {domain.abs_path} has no mpu-policy; "
                  "the BSP default MPU table applies")
            return True
        if soc is None:
            raise MpuError(
                f"no standalone BSP MPU table for {processor} on "
                f"'{family}'")

        ddr = None
        if processor == "cortexr5":
            ddr = _ddr_range(sdt, cpu_name, soc)
        free_regions = (args.free_regions if args.free_regions is not None
                        else DEFAULT_FREE_REGIONS[processor])
        table = build_table(soc, processor, additions, ddr, args.aie)
        check_fit(table, free_regions)

        output = args.output
        if not os.path.isabs(output):
            output = os.path.join(getattr(sdt, "outdir", None) or ".",
                                  output)
        with open(output, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(render(table, processor, soc, domain.abs_path,
                                cpu_name, free_regions))
        _info(f"xlnx_rpu_mpu: wrote {output} with {len(table)} regions")
        return True
    except (ValueError, LayoutError, OSError) as exc:
        _error(f"xlnx_rpu_mpu: {exc}")
        return False
