#/*
# * Copyright (c) 2026 Advanced Micro Devices, Inc. All rights reserved.
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

"""Generate conventional Zephyr MPU metadata for a Cortex-R domain."""

import re

from lopper.log import _error, _info
from lopper.tree import LopperNode
from zephyr_memory import (
    LayoutError, MemoryPolicy, parse_layout, parse_mpu_memories,
    zephyr_argument_parser,
)


DT_MEM_ARM_MPU_RAM_NOCACHE = (1 << 1) << 20


def is_compat(node, compat_string_to_test):
    """Identify the assist compatibility string.

    Description:
        Registers the generator for the zephyr_mpu command name.

    Args:
        node (LopperNode): Assist trigger node; unused.
        compat_string_to_test (str): Dispatcher compatibility string.

    Returns:
        Callable | str: Generator callback on a match, otherwise an empty string.

    Raises:
        None.
    """
    if re.search(r"module,zephyr_mpu$", compat_string_to_test):
        return generate_mpu_tree
    return ""


def _memory_attributes(memory):
    """Encode shared-memory policy using Zephyr's legacy ARM attribute.

    Description:
        Uses the existing R-profile non-cacheable RAM type. Static linker-owned
        memories do not call this helper.

    Args:
        memory (Memory): Normalized memory policy.

    Returns:
        int: Value for the conventional zephyr,memory-attr property.

    Raises:
        LayoutError: If a dynamic memory policy cannot be represented by the
            legacy privileged, non-cacheable, execute-never ARM RAM type.
    """
    required = MemoryPolicy.READABLE | MemoryPolicy.WRITABLE
    unsupported = (MemoryPolicy.EXECUTABLE | MemoryPolicy.CACHEABLE |
                   MemoryPolicy.SHAREABLE | MemoryPolicy.USERSPACE)
    if not memory.has_policy(required) or memory.policy & unsupported:
        raise LayoutError(
            f"{memory.node.abs_path}: non-static R-profile memory must be "
            "privileged read/write, non-cacheable, and execute-never")
    return DT_MEM_ARM_MPU_RAM_NOCACHE


def _apply_memory(memory, emit_mpu=True):
    """Expose one physical node as a Zephyr linker and optional MPU region.

    Description:
        Adds only conventional Zephyr properties while retaining the physical
        compatible strings and processor-local reg produced by gen_domain_dts.

    Args:
        memory (Memory): Normalized memory and source DT node.

    Returns:
        None.

    Raises:
        LayoutError: If a linker region name would conflict with an existing
            different name on the physical node.
    """
    node = memory.node
    existing = node.propval("zephyr,memory-region", list)
    if existing not in ([], [""]) and existing != [memory.name]:
        raise LayoutError(
            f"{node.abs_path}: memory-region '{existing[0]}' conflicts with "
            f"policy name '{memory.name}'")
    compatible = node.propval("compatible", list)
    compatible = [] if compatible == [""] else compatible
    node["compatible"] = list(dict.fromkeys(
        ["zephyr,memory-region"] + compatible))
    reg = node.propval("reg", list)
    if len(reg) >= 4:
        reg[0:2] = [0, memory.origin]
        node["reg"] = reg
    elif len(reg) >= 2:
        reg[0] = memory.origin
        node["reg"] = reg
    node["zephyr,memory-region"] = memory.name
    if emit_mpu:
        node["zephyr,memory-attr"] = _memory_attributes(memory)


def _uses_static_mpu(memory):
    """Return whether linker-derived static MPU regions own this memory."""
    return "static" in [str(value) for value in
                        memory.node.propval("mpu-policy", list)]


def _r5_mpu_aperture(memory, local_memories):
    """Calculate a representable ARMv7-R MPU aperture.

    Description:
        Expands the policy range to the smallest naturally aligned
        power-of-two window that contains it. Rejects an expansion that would
        cover a configured local TCM range.

    Args:
        memory (Memory): DDR policy memory to cover.
        local_memories (tuple[Memory]): Configured ATCM, BTCM, and CTCM
            memories that must remain outside the DDR policy window.

    Returns:
        tuple[int, int]: Representable aperture origin and length.

    Raises:
        LayoutError: If the aperture is outside 32-bit R5 address space or
            overlaps a configured local memory.
    """
    end = memory.origin + memory.length
    if memory.length <= 0:
        raise LayoutError("an MPU memory range must have a positive size")
    # ARMv7-R MPU regions require power-of-two sizes. This bit-length form
    # rounds the requested length up to the smallest representable size.
    length = 1 << (memory.length - 1).bit_length()
    origin = memory.origin & ~(length - 1)
    while origin + length < end:
        length <<= 1
        origin = memory.origin & ~(length - 1)
    if origin + length > 1 << 32:
        raise LayoutError(
            f"memory '{memory.name}' cannot be represented in the "
            "32-bit R5 MPU address space")
    for local in local_memories:
        local_end = local.origin + local.length
        if origin < local_end and local.origin < origin + length:
            raise LayoutError(
                f"R5 MPU aperture 0x{origin:x}-"
                f"0x{origin + length:x} overlaps {local.name} at "
                f"0x{local.origin:x}-0x{local_end:x}")
    return origin, length


def _set_memory_range(memory, origin, length):
    """Update the first reg tuple of a normalized memory node.

    Description:
        Replaces only the address and size cells used by the Zephyr memory
        region while preserving any subsequent device ranges.

    Args:
        memory (Memory): Normalized memory whose physical node is updated.
        origin (int): Processor-visible start address.
        length (int): Processor-visible byte length.

    Returns:
        None.

    Raises:
        LayoutError: If the node has no supported reg tuple.
    """
    reg = memory.node.propval("reg", list)
    if len(reg) >= 4:
        reg[0:4] = [origin >> 32, origin & 0xffffffff,
                    length >> 32, length & 0xffffffff]
    elif len(reg) >= 2:
        reg[0:2] = [origin, length]
    else:
        raise LayoutError(
            f"{memory.node.abs_path}: cannot update missing reg range")
    memory.node["reg"] = reg


def _prepare_r5_mpu_ranges(processor, memories, dynamic_memories):
    """Make R5 DT policy ranges representable by the ARMv7-R MPU.

    Description:
        Rounds each dynamic DDR or consolidated IPC policy node before
        conventional Zephyr MPU metadata is emitted. R52 ranges are unchanged
        because ARMv8-R uses base/limit regions.

    Args:
        processor (str): Canonical processor class.
        memories (tuple[Memory]): All normalized domain-owned memories.
        dynamic_memories (tuple[Memory]): Memories requiring DT MPU regions.

    Returns:
        None.

    Raises:
        LayoutError: If a required R5 DDR aperture cannot be represented
            without covering local TCM.
    """
    if processor != "cortexr5":
        return
    local = tuple(memory for memory in memories
                  if memory.kind in ("ATCM", "BTCM", "CTCM"))
    for memory in dynamic_memories:
        if memory.kind not in ("DDR", "IPC_SHM"):
            continue
        origin, length = _r5_mpu_aperture(memory, local)
        if (memory.kind == "IPC_SHM" and
                (origin != memory.origin or length != memory.length)):
            raise LayoutError(
                f"{memory.node.abs_path}: consolidated R5 IPC range must "
                "already be naturally aligned and power-of-two sized")
        _set_memory_range(memory, origin, length)
        _info(
            f"zephyr_mpu: {memory.name} R5 MPU aperture "
            f"0x{origin:x}-0x{origin + length:x}")


def _validate_memory_overlaps(memories):
    """Reject overlapping MPU memory ranges.

    Description:
        Sorts normalized half-open ranges and rejects configurations that
        would require MPU region priority to resolve conflicting entries.

    Args:
        memories (tuple[Memory]): Normalized MPU memories to validate.

    Returns:
        None.

    Raises:
        LayoutError: If two positive-sized MPU ranges overlap.
    """
    ordered = sorted(memories, key=lambda memory: (
        memory.origin, memory.origin + memory.length, memory.name))
    for previous, current in zip(ordered, ordered[1:]):
        previous_end = previous.origin + previous.length
        current_end = current.origin + current.length
        if current.origin < previous_end:
            overlap_end = min(previous_end, current_end)
            raise LayoutError(
                "MPU regions overlap: "
                f"{previous.node.abs_path} "
                f"[0x{previous.origin:x}, 0x{previous_end:x}) and "
                f"{current.node.abs_path} "
                f"[0x{current.origin:x}, 0x{current_end:x}); overlap "
                f"[0x{current.origin:x}, 0x{overlap_end:x})")


def _boot_memory(layout):
    """Resolve the memory containing the vector table.

    Description:
        Uses the already validated vector placement that drove profile
        inference; no independent boot policy is introduced by this assist.

    Args:
        layout (Layout): Normalized and validated policy.

    Returns:
        Memory: Boot memory selected for zephyr,sram.

    Raises:
        LayoutError: If the normalized layout lacks the vector assignment.
    """
    vector = next((section for section in layout.sections
                   if section.name == "vector_table"), None)
    if not vector:
        raise LayoutError("cannot select zephyr,sram without vector_table")
    return next(memory for memory in layout.memories
                if memory.name == vector.memory)


def generate_mpu_tree(root_node, sdt, options):
    """Transform an intermediate RPU DT into a Zephyr MPU-aware DT.

    Description:
        Consumes the execution-domain memory policy, exposes conventional Zephyr
        memory-region and memory-attr properties, selects the boot SRAM, and
        removes transformation-only policy metadata from the output tree.

    Args:
        root_node (LopperNode): Assist trigger node; unused.
        sdt (LopperSDT): Intermediate processor-domain device tree.
        options (dict): Lopper assist options containing the argument list.

    Returns:
        bool: True on success and False after reporting a user-facing error.

    Raises:
        None. Layout and argument errors are reported through Lopper.
    """
    try:
        args = zephyr_argument_parser(__doc__).parse_args(
            options.get("args", []))
        layout = parse_layout(sdt.tree, args.domain, args.zephyr_version)
        _, processor, memories = parse_mpu_memories(sdt.tree, args.domain)
        dynamic_memories = tuple(memory for memory in memories
                                 if not _uses_static_mpu(memory))
        _prepare_r5_mpu_ranges(processor, memories, dynamic_memories)
        _validate_memory_overlaps(dynamic_memories)
        for memory in memories:
            _apply_memory(memory, emit_mpu=not _uses_static_mpu(memory))
        try:
            chosen = sdt.tree["/chosen"]
        except KeyError:
            chosen = LopperNode(-1, "/chosen")
            sdt.tree.add(chosen)
        boot_node = _boot_memory(layout).node
        chosen["zephyr,sram"] = boot_node.abs_path
        ipc = next((memory for memory in memories
                    if memory.kind == "IPC_SHM"), None)
        if ipc:
            chosen["zephyr,ipc_shm"] = ipc.node.abs_path
        for node in sdt.tree:
            if "mpu-policy" in node.__props__:
                node.delete("mpu-policy")
        _info(f"zephyr_mpu: inferred {layout.profile}")
        _info("zephyr_mpu: emitted conventional Zephyr memory metadata")
        return True
    except (LayoutError, StopIteration, SystemExit) as exc:
        _error(f"zephyr_mpu: {exc}")
        return False
