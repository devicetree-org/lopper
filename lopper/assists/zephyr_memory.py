#/*
# * Copyright (c) 2026 Advanced Micro Devices, Inc. All rights reserved.
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

"""Shared memory model for Zephyr linker and MPU Lopper assists."""

import argparse
from dataclasses import dataclass, replace
from enum import IntFlag
import os
import re
import sys

sys.path.append(os.path.dirname(__file__))

from baremetalconfig_xlnx import scan_reg_size

try:
    from .openamp_zephyr_resource import (
        FIXED_OFFSET_SECTIONS as OPENAMP_FIXED_OFFSET_SECTIONS,
        SECTION_NAMES as OPENAMP_EXTENSION_SECTIONS,
    )
except ImportError:
    from openamp_zephyr_resource import (
        FIXED_OFFSET_SECTIONS as OPENAMP_FIXED_OFFSET_SECTIONS,
        SECTION_NAMES as OPENAMP_EXTENSION_SECTIONS,
    )

SUPPORTED_ZEPHYR_VERSIONS = ("4.3",)
ZEPHYR_SECTIONS = {
    "vector_table", "text", "rodata", "data", "bss", "noinit",
    "heap", "stack",
}
SUPPORTED_SECTIONS = ZEPHYR_SECTIONS | OPENAMP_EXTENSION_SECTIONS
SUPPORTED_FIXED_OFFSETS = {"vector_table", "text"} | \
    OPENAMP_FIXED_OFFSET_SECTIONS
LINKER_SCALAR_PROPERTIES = {
    "linker_file_output_name": "linker_file_output_name",
    "entry": "linker-entry",
    "user_content": "linker-user-content",
}
MPU_POLICY_PROPERTIES = {
    "readable", "writable", "executable", "cacheable", "shareable",
    "userspace",
}


class LayoutError(ValueError):
    """Report invalid or ambiguous Zephyr linker metadata."""


class MemoryPolicy(IntFlag):
    """Encode normalized Zephyr execution-domain memory characteristics."""

    NONE = 0
    READABLE = 1 << 0
    WRITABLE = 1 << 1
    EXECUTABLE = 1 << 2
    CACHEABLE = 1 << 3
    SHAREABLE = 1 << 4
    USERSPACE = 1 << 5


def zephyr_argument_parser(description):
    """Create the common Zephyr policy-assist argument parser.

    Description:
        Defines the domain and version contract shared by MPU and linker
        generation while allowing each assist to add its own options.

    Args:
        description (str): Command description displayed by argparse.

    Returns:
        argparse.ArgumentParser: Parser containing common required options.

    Raises:
        None.
    """
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--domain", required=True)
    parser.add_argument("--zephyr-version", required=True)
    return parser


@dataclass(frozen=True)
class Memory:
    """Describe one normalized linker memory."""

    name: str
    node: object
    origin: int
    length: int
    policy: MemoryPolicy
    kind: str

    def has_policy(self, policy):
        """Report whether all requested memory policy bits are enabled.

        Description:
            Tests one or more normalized access characteristics without
            exposing the bitmap representation to policy consumers.

        Args:
            policy (MemoryPolicy): Policy bits that must all be present.

        Returns:
            bool: True when every requested bit is enabled.

        Raises:
            None.
        """
        return self.policy & policy == policy

    @property
    def flags(self):
        """Return GNU linker flags derived from memory policy.

        Description:
            Formats readable, writable, and executable policy as the GNU ld
            MEMORY attribute string without storing redundant state.

        Args:
            None.

        Returns:
            str: GNU ld memory flags in rwx order.

        Raises:
            None.
        """
        flag_policy = (("r", MemoryPolicy.READABLE),
                       ("w", MemoryPolicy.WRITABLE),
                       ("x", MemoryPolicy.EXECUTABLE))
        return "".join(flag for flag, policy in flag_policy
                       if self.has_policy(policy))


@dataclass(frozen=True)
class Section:
    """Describe one logical output-section placement."""

    name: str
    memory: str
    offset: int = None
    input_sections: tuple = ()
    noload: bool = False
    alignment: int = None
    keep: bool = False

    @property
    def custom(self):
        """Report whether this is a user-defined output section.

        Description:
            Built-in Zephyr ABI groups have no explicit input-section list;
            custom output sections always have at least one input pattern.

        Args:
            None.

        Returns:
            bool: True for a user-defined section.

        Raises:
            None.
        """
        return bool(self.input_sections)


@dataclass(frozen=True)
class Layout:
    """Contain a normalized and validated Zephyr linker layout."""

    processor: str
    profile: str
    entry: str
    zephyr_version: str
    memories: tuple
    sections: tuple
    domain: object
    output_name: str
    user_content: str = None


def _property_value(node, property_name, required=True):
    """Return a scalar property value.

    Description:
        Reads a property that must contain at most one value and provides a
        common missing-property diagnostic.

    Args:
        node (LopperNode): Node containing the property.
        property_name (str): Property to read.
        required (bool): Whether absence is an error.

    Returns:
        object | None: Scalar property value, or None when optional and absent.

    Raises:
        LayoutError: If a required property is absent or is not scalar.
    """
    value = node.propval(property_name, list)
    if not value or value == [""]:
        if required:
            raise LayoutError(
                f"{node.abs_path}: missing required '{property_name}' property")
        return None
    if len(value) != 1:
        raise LayoutError(
            f"{node.abs_path}: '{property_name}' must contain one value")
    return value[0]


def resolve_memory_node(tree, reference):
    """Resolve a memory reference to exactly one DT node.

    Description:
        Accepts a phandle, absolute path, node name, label, or Xilinx IP name.

    Args:
        tree (LopperTree): Input device tree.
        reference (object): Memory reference from linker metadata.

    Returns:
        LopperNode: Unambiguously resolved memory node.

    Raises:
        LayoutError: If the reference is missing or ambiguous.
    """
    if isinstance(reference, int):
        node = tree.pnode(reference)
        if node:
            return node
    reference = str(reference)
    if reference.startswith("/"):
        try:
            return tree[reference]
        except Exception:
            pass
    matches = list(tree.lnodes(re.escape(reference)))
    alias = tree.alias_node(reference)
    if alias and alias not in matches:
        matches.append(alias)
    for node in tree:
        aliases = {node.name, node.name.split("@", 1)[0]}
        for property_name in ("label", "xlnx,ip-name"):
            value = node.propval(property_name, list)
            if value and value != [""]:
                aliases.add(str(value[0]))
        if reference in aliases and node not in matches:
            matches.append(node)
    if not matches:
        raise LayoutError(f"memory reference '{reference}' was not found")
    if len(matches) > 1:
        paths = ", ".join(node.abs_path for node in matches)
        raise LayoutError(f"memory reference '{reference}' is ambiguous: {paths}")
    return matches[0]


def linker_section_property(section_name):
    """Return the flattened property name for a logical section.

    Description:
        Keeps YAML expansion and linker parsing on one stable metadata ABI.

    Args:
        section_name (str): Logical Zephyr section-group name.

    Returns:
        str: Domain property containing the section's memory reference.

    Raises:
        LayoutError: If the logical section is unsupported.
    """
    if section_name not in SUPPORTED_SECTIONS:
        raise LayoutError(f"unsupported logical section '{section_name}'")
    return f"linker-section-{section_name.replace('_', '-')}"


def _memory_range(node):
    """Read the first address range of a memory node.

    Description:
        Decodes the first reg tuple using the node's address and size cells.

    Args:
        node (LopperNode): Memory node with a reg property.

    Returns:
        tuple[int, int]: Origin and length.

    Raises:
        LayoutError: If reg is absent or cannot be decoded.
    """
    try:
        return scan_reg_size(node, node["reg"].value, 0)
    except Exception as exc:
        raise LayoutError(
            f"{node.abs_path}: cannot resolve first reg entry: {exc}") from exc


def _boolean_property(node, property_name, default=False):
    """Read a boolean policy property.

    Description:
        Accepts an empty DT boolean, integer boolean, or YAML-expanded string.

    Args:
        node (LopperNode): Policy memory node.
        property_name (str): Boolean property name.
        default (bool): Value used when the property is absent.

    Returns:
        bool: Normalized property value.

    Raises:
        LayoutError: If an explicit value cannot be interpreted as boolean.
    """
    if property_name not in node.__props__:
        return default
    value = node.propval(property_name, list)
    if value in ([], [""], [None]):
        return True
    scalar = value[0]
    if scalar in (True, 1, "true", "True", "yes", "on"):
        return True
    if scalar in (False, 0, "false", "False", "no", "off"):
        return False
    raise LayoutError(
        f"{node.abs_path}: '{property_name}' is not a boolean value")


def _domain_node(tree, domain_path):
    """Resolve and validate one Zephyr domain by absolute path.

    Description:
        Makes direct assist invocation and the selected domain the policy
        discriminator instead of relying on a transformation-only compatible.

    Args:
        tree (LopperTree): Input device tree.
        domain_path (str): Absolute path of the target domain.

    Returns:
        LopperNode: Selected Zephyr domain.

    Raises:
        LayoutError: If the path is absent, non-absolute, unresolved, or does
            not describe a Zephyr domain.
    """
    if not domain_path or not str(domain_path).startswith("/domains/"):
        raise LayoutError("--domain must be an absolute /domains/... path")
    try:
        domain = tree[str(domain_path)]
    except Exception as exc:
        raise LayoutError(f"domain '{domain_path}' was not found") from exc
    os_type = domain.propval("os,type", list)
    if os_type != ["zephyr"]:
        value = os_type[0] if os_type and os_type != [""] else "missing"
        raise LayoutError(
            f"{domain.abs_path}: os,type must be 'zephyr', found '{value}'")
    return domain


def _processor_from_domain(tree, domain):
    """Infer the R-profile processor from a domain CPU reference.

    Description:
        Resolves the first phandle in the domain cpus property and recognizes
        Cortex-R5 or Cortex-R52 from the CPU and its parent cluster metadata.

    Args:
        tree (LopperTree): Input device tree.
        domain (LopperNode): Selected Zephyr domain.

    Returns:
        str: Canonical processor class, either cortexr5 or cortexr52.

    Raises:
        LayoutError: If the CPU reference is absent, unresolved, ambiguous, or
            does not identify a supported processor.
    """
    cpus = domain.propval("cpus", list)
    if not cpus or cpus == [""]:
        raise LayoutError(f"{domain.abs_path}: missing required 'cpus' property")
    cpu = tree.pnode(cpus[0]) if isinstance(cpus[0], int) else None
    if cpu is None:
        raise LayoutError(f"{domain.abs_path}: cannot resolve cpus reference")
    candidates = [cpu]
    if cpu.parent:
        candidates.append(cpu.parent)
    identity = " ".join(
        str(value).lower()
        for node in candidates
        for value in ([node.name, node.label or ""] +
                      node.propval("compatible", list) +
                      node.propval("xlnx,ip-name", list))
        if value not in (None, "")
    )
    if "cortex-r52" in identity or "cortexr52" in identity or "r52" in identity:
        return "cortexr52"
    if "cortex-r5" in identity or "cortexr5" in identity or "r5" in identity:
        return "cortexr5"
    raise LayoutError(
        f"{domain.abs_path}: CPU '{cpu.abs_path}' is not Cortex-R5 or Cortex-R52")


def _memory_kind(node):
    """Classify a physical memory independently of its linker name.

    Description:
        Uses node identity and compatible strings to retain TCM and DDR boot
        semantics when GNU linker names come from arbitrary labels.

    Args:
        node (LopperNode): Physical memory node.

    Returns:
        str: ATCM, BTCM, CTCM, DDR, IPC_SHM, or MEMORY.

    Raises:
        None.
    """
    identity = " ".join(
        [node.name, node.label or ""] +
        [str(value) for value in node.propval("compatible", list)] +
        [str(value) for value in node.propval("label", list)] +
        [str(value) for value in node.propval("xlnx,ip-name", list)]
    ).lower()
    for kind in ("atcm", "btcm", "ctcm"):
        if kind in identity:
            return kind.upper()
    if "ipc" in identity and ("shm" in identity or "mmio-sram" in identity):
        return "IPC_SHM"
    if (node.parent and node.parent.name.startswith("reserved-memory")) or \
            "ddr" in identity or node.name.startswith("memory@"):
        return "DDR"
    return "MEMORY"


def _linker_name(node):
    """Build a stable GNU linker memory name from a physical node.

    Description:
        Prefers an explicit label or IP name, falls back to the node base name,
        and sanitizes the result for GNU linker syntax. Duplicate fallback
        names are disambiguated later using their processor-visible address.

    Args:
        node (LopperNode): Physical memory node.

    Returns:
        str: Uppercase GNU linker memory identifier.

    Raises:
        LayoutError: If no valid identifier can be produced.
    """
    label_values = node.propval("label", list)
    source = (str(label_values[0])
              if label_values and label_values != [""] else "")
    source = source or str(node.label or "")
    ip_names = node.propval("xlnx,ip-name", list)
    if not source and ip_names and ip_names != [""]:
        source = str(ip_names[0])
    source = source or node.name.split("@", 1)[0]
    name = re.sub(r"[^A-Za-z0-9_]", "_", source).upper()
    if not name or not re.match(r"[A-Z_]", name):
        name = "MEM_" + name
    if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", name):
        raise LayoutError(
            f"{node.abs_path}: cannot derive a GNU linker memory name")
    return name


def _memory_policy(node, kind):
    """Parse and validate one physical memory MPU policy.

    Description:
        Reads a string-list property on the physical memory, defaults every
        omitted characteristic to false, and accepts only combinations
        supported by the Zephyr Cortex-R5/R52 policy decoder.

    Args:
        node (LopperNode): Physical domain-owned memory node.
        kind (str): Physical memory classification.

    Returns:
        MemoryPolicy: Bitmap of normalized memory characteristics.

    Raises:
        LayoutError: If properties or the resulting policy are unsupported.
    """
    values = node.propval("mpu-policy", list)
    if (not values or values == [""]) and kind == "IPC_SHM":
        return (MemoryPolicy.READABLE | MemoryPolicy.WRITABLE |
                MemoryPolicy.SHAREABLE | MemoryPolicy.USERSPACE)
    if not values or values == [""]:
        raise LayoutError(
            f"{node.abs_path}: missing required 'mpu-policy' property")
    values = [str(value) for value in values]
    unknown = set(values) - MPU_POLICY_PROPERTIES
    if unknown:
        raise LayoutError(
            f"{node.abs_path}: unsupported MPU policy options: " +
            ", ".join(sorted(unknown)))
    fields = (
        (MemoryPolicy.READABLE, "readable"),
        (MemoryPolicy.WRITABLE, "writable"),
        (MemoryPolicy.EXECUTABLE, "executable"),
        (MemoryPolicy.CACHEABLE, "cacheable"),
        (MemoryPolicy.SHAREABLE, "shareable"),
        (MemoryPolicy.USERSPACE, "userspace"),
    )
    policy = MemoryPolicy.NONE
    for policy_bit, property_name in fields:
        if property_name in values:
            policy |= policy_bit
    supported = {
        MemoryPolicy.READABLE | MemoryPolicy.WRITABLE | MemoryPolicy.CACHEABLE,
        MemoryPolicy.READABLE | MemoryPolicy.CACHEABLE,
        MemoryPolicy.READABLE | MemoryPolicy.EXECUTABLE | MemoryPolicy.CACHEABLE,
        MemoryPolicy.READABLE | MemoryPolicy.WRITABLE |
        MemoryPolicy.EXECUTABLE | MemoryPolicy.CACHEABLE,
        MemoryPolicy.READABLE | MemoryPolicy.WRITABLE |
        MemoryPolicy.SHAREABLE | MemoryPolicy.USERSPACE,
    }
    if policy not in supported:
        raise LayoutError(
            f"{node.abs_path}: unsupported Cortex-R MPU policy "
            f"0x{int(policy):x}")
    return policy


def _infer_profile(processor, memories, sections, entry):
    """Infer the hardware boot profile from the linker layout.

    Description:
        Uses processor type and vector-table placement to distinguish R5 TCM,
        R52 TCM, and R52 DDR boot. The vector memory must be executable and the
        entry must identify the vector table for this initial ABI.

    Args:
        processor (str): Target processor name.
        memories (tuple[Memory]): Normalized linker memories.
        sections (tuple[Section]): Logical section assignments.
        entry (str): ELF entry symbol.

    Returns:
        str: Inferred profile name.

    Raises:
        LayoutError: If boot placement is missing or invalid for the processor.
    """
    memory_map = {memory.name: memory for memory in memories}
    section_map = {section.name: section for section in sections}
    vector = section_map.get("vector_table")
    if not vector:
        raise LayoutError("profile inference requires vector_table placement")
    if entry != "_vector_table":
        raise LayoutError(
            "entry must be _vector_table until entry-section resolution is available")
    vector_memory = memory_map[vector.memory]
    if "x" not in vector_memory.flags:
        raise LayoutError("vector_table memory must be executable")
    is_r52 = "r52" in processor.lower()
    is_r5 = "r5" in processor.lower() and not is_r52
    if not is_r5 and not is_r52:
        raise LayoutError(f"unsupported RPU processor '{processor}'")
    if vector_memory.kind == "ATCM":
        vector_offset = vector.offset or 0
        if vector_memory.origin != 0:
            raise LayoutError("ATCM must use local address 0x0")
        if is_r5 and vector_offset != 0:
            raise LayoutError("Cortex-R5 ATCM vector_table offset must be 0")
        if is_r52 and vector_offset % 32:
            raise LayoutError(
                "Cortex-R52 vector_table offset must be 32-byte aligned")
        expected = ({"BTCM": 0x10000, "CTCM": 0x20000} if is_r52
                    else {"BTCM": 0x20000})
        for kind, origin in expected.items():
            memory = next((item for item in memories if item.kind == kind), None)
            if memory and memory.origin != origin:
                raise LayoutError(
                    f"{kind} must use local address 0x{origin:x} for "
                    f"processor '{processor}'")
        return "r52-tcm" if is_r52 else "r5-tcm"
    if vector_memory.kind == "DDR" and is_r52:
        return "r52-ddr"
    if vector_memory.kind == "DDR":
        raise LayoutError("R5 DDR boot is not supported")
    raise LayoutError("vector_table must be placed in ATCM or R52 DDR")


def _validate_section_policy(memories, sections):
    """Validate logical sections against memory permissions and bounds.

    Description:
        Rejects executable or writable placement that contradicts the shared
        MPU policy and verifies that fixed offsets begin inside their memory.

    Args:
        memories (tuple[Memory]): Normalized memory policies.
        sections (tuple[Section]): Logical linker placements.

    Returns:
        None.

    Raises:
        LayoutError: If placement conflicts with permissions or memory bounds.
    """
    memory_map = {memory.name: memory for memory in memories}
    executable = {"vector_table", "text"}
    writable = {"data", "bss", "noinit", "heap", "stack"}
    readable = {"vector_table", "text", "rodata", "resource_table"}
    for section in sections:
        memory = memory_map[section.memory]
        if section.name in executable and not memory.has_policy(
                MemoryPolicy.EXECUTABLE):
            raise LayoutError(
                f"section '{section.name}' selects non-executable "
                f"memory '{memory.name}'")
        if section.name in writable and not memory.has_policy(
                MemoryPolicy.WRITABLE):
            raise LayoutError(
                f"section '{section.name}' selects non-writable "
                f"memory '{memory.name}'")
        if section.name in readable and not memory.has_policy(
                MemoryPolicy.READABLE):
            raise LayoutError(
                f"section '{section.name}' selects non-readable "
                f"memory '{memory.name}'")
        if memory.name == "IPC_SHM" and section.name != "resource_table":
            raise LayoutError(
                f"ordinary section '{section.name}' cannot use IPC_SHM")
        if section.offset is not None and not 0 <= section.offset < memory.length:
            raise LayoutError(
                f"section '{section.name}' offset 0x{section.offset:x} is "
                f"outside memory '{memory.name}'")
        if (section.offset is not None and not section.custom and
                section.name not in SUPPORTED_FIXED_OFFSETS):
            raise LayoutError(
                f"section '{section.name}' does not support a fixed offset")


def _parse_custom_sections(domain, memory_by_reference):
    """Parse safe standalone output-section declarations.

    Description:
        Converts flat domain properties into normalized Section objects. Raw
        linker expressions are deliberately not accepted.

    Args:
        domain (LopperNode): Selected Zephyr domain.
        memory_by_reference (Callable): Resolve a section region reference.

    Returns:
        list[Section]: Parsed user-defined section placements.

    Raises:
        LayoutError: If a name, input pattern, alignment, or region is invalid.
    """
    sections = []
    safe_pattern = re.compile(r"\.[A-Za-z0-9_.$*?+\-]+$")
    reserved_prefixes = (
        ".text", ".rodata", ".data", ".bss", ".noinit", ".vectors",
        ".exc_vector_table", ".irq_vector_table", ".resource_table",
        ".user_stacks", ".priv_stacks", ".k_heap",
    )
    names = domain.propval("linker-custom-sections", list)
    if not names or names == [""]:
        return sections
    for raw_name in names:
        name = str(raw_name)
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", name):
            raise LayoutError(f"invalid custom section name '{name}'")
        if name in SUPPORTED_SECTIONS:
            raise LayoutError(
                f"custom section '{name}' overrides a Zephyr ABI group")
        prefix = f"linker-custom-section-{name}"
        memory = memory_by_reference(
            _property_value(domain, f"{prefix}-region")).name
        patterns = tuple(str(value) for value in
                         domain.propval(f"{prefix}-input-sections", list)
                         if value not in (None, ""))
        if not patterns:
            raise LayoutError(
                f"custom section '{name}' requires input-sections")
        for pattern in patterns:
            if not safe_pattern.fullmatch(pattern):
                raise LayoutError(
                    f"custom section '{name}' has unsafe input pattern "
                    f"'{pattern}'")
            if pattern.startswith(reserved_prefixes):
                raise LayoutError(
                    f"custom section '{name}' pattern '{pattern}' "
                    "would consume a Zephyr ABI section")
        offset = _property_value(domain, f"{prefix}-offset", required=False)
        alignment = _property_value(
            domain, f"{prefix}-alignment", required=False)
        if alignment is not None:
            alignment = int(alignment)
            if alignment <= 0 or alignment & (alignment - 1):
                raise LayoutError(
                    f"custom section '{name}' alignment must be a "
                    "positive power of two")
        sections.append(Section(
            name, memory, int(offset) if offset is not None else None,
            patterns, _boolean_property(domain, f"{prefix}-noload"),
            alignment, _boolean_property(domain, f"{prefix}-keep")))
    return sections


def _normalized_memory(node, processor):
    """Create one normalized memory from a physical DT node.

    Description:
        Derives its linker name and kind, decodes its range, applies the
        processor-local TCM view, and parses the mandatory MPU policy.

    Args:
        node (LopperNode): Physical memory node.
        processor (str): Canonical processor class.

    Returns:
        Memory: Normalized memory description.

    Raises:
        LayoutError: If the range, name, or MPU policy is invalid.
    """
    name = _linker_name(node)
    kind = _memory_kind(node)
    origin, length = _memory_range(node)
    if processor == "cortexr52":
        origin = {"ATCM": 0x0, "BTCM": 0x10000,
                  "CTCM": 0x20000}.get(kind, origin)
    elif processor == "cortexr5":
        origin = {"ATCM": 0x0, "BTCM": 0x20000}.get(kind, origin)
    return Memory(name, node, origin, length, _memory_policy(node, kind), kind)


def _normalized_memories(nodes, processor):
    """Normalize memories and disambiguate only colliding linker names.

    Existing unique semantic names remain stable. When labels are unavailable
    and multiple nodes share a base name, append the processor-visible origin
    derived from ``reg``.
    """
    memories = tuple(_normalized_memory(node, processor) for node in nodes)
    counts = {}
    for memory in memories:
        counts[memory.name] = counts.get(memory.name, 0) + 1
    return tuple(
        replace(memory, name=f"{memory.name}_{memory.origin:X}")
        if counts[memory.name] > 1 else memory
        for memory in memories
    )


def _domain_memory_nodes(tree, domain):
    """Resolve the union of domain SRAM and reserved-memory references.

    Description:
        Collects domain-owned MPU candidates in declaration order and removes
        duplicates by physical node identity.

    Args:
        tree (LopperTree): Input device tree.
        domain (LopperNode): Selected Zephyr domain.

    Returns:
        tuple[LopperNode]: Unique domain-owned physical memories.

    Raises:
        LayoutError: If either property is absent or a reference is invalid.
    """
    nodes = []
    sram = domain.propval("sram", list)
    if sram and sram != [""]:
        phandle_nodes = [tree.pnode(value) for value in sram
                         if isinstance(value, int)]
        if len(phandle_nodes) == len(sram) and all(phandle_nodes):
            nodes.extend(phandle_nodes)
        else:
            if len(sram) % 4:
                raise LayoutError(
                    f"{domain.abs_path}: sram must contain phandles or "
                    "four-cell address/size tuples")
            for index in range(0, len(sram), 4):
                origin = (int(sram[index]) << 32) | int(sram[index + 1])
                length = (int(sram[index + 2]) << 32) | int(sram[index + 3])
                candidates = []
                for path in ("/axi", "/reserved-memory"):
                    try:
                        parent = tree[path]
                    except KeyError:
                        continue
                    candidates.extend(parent.subnodes())
                matches = []
                for node in candidates:
                    if node.props("reg"):
                        try:
                            if _memory_range(node) == (origin, length):
                                matches.append(node)
                        except LayoutError:
                            continue
                tcm_matches = [node for node in matches
                               if node.abs_path.startswith("/axi/") and
                               _memory_kind(node) in ("ATCM", "BTCM", "CTCM")]
                if tcm_matches:
                    matches = tcm_matches
                if len(matches) != 1:
                    paths = ", ".join(node.abs_path for node in matches)
                    raise LayoutError(
                        f"{domain.abs_path}: SRAM range 0x{origin:x}+"
                        f"0x{length:x} resolved to {len(matches)} nodes"
                        f"{': ' + paths if paths else ''}")
                nodes.append(matches[0])
    reserved = domain.propval("reserved-memory", list)
    if reserved and reserved != [""]:
        nodes.extend(resolve_memory_node(tree, reference)
                     for reference in reserved)
    if not nodes:
        raise LayoutError(
            f"{domain.abs_path}: expected memory references in 'sram' or "
            "'reserved-memory'")
    unique_nodes = []
    paths = set()
    for node in nodes:
        if node.abs_path not in paths:
            unique_nodes.append(node)
            paths.add(node.abs_path)
    return tuple(unique_nodes)


def parse_mpu_memories(tree, domain_path):
    """Parse domain-owned memories for Zephyr MPU generation.

    Description:
        Resolves a Zephyr domain, infers its processor, and normalizes every
        memory named by its sram and reserved-memory properties.

    Args:
        tree (LopperTree): Input system device tree.
        domain_path (str): Absolute target domain path.

    Returns:
        tuple[LopperNode, str, tuple[Memory]]: Domain, processor, and memories.

    Raises:
        LayoutError: If domain, CPU, memory, or MPU policy metadata is invalid.
    """
    domain = _domain_node(tree, domain_path)
    processor = _processor_from_domain(tree, domain)
    memories = _normalized_memories(
        _domain_memory_nodes(tree, domain), processor)
    if len({memory.name for memory in memories}) != len(memories):
        raise LayoutError(
            f"{domain.abs_path}: duplicate normalized linker memory name")
    return domain, processor, memories


def parse_layout(tree, domain_path, zephyr_version):
    """Parse and validate a Zephyr linker layout.

    Description:
        Resolves a Zephyr domain and its CPU, reads domain-local linker
        metadata, validates memory ownership and section assignments, and
        infers the RPU boot profile.

    Args:
        tree (LopperTree): Input system device tree.
        domain_path (str): Absolute path of the target Zephyr domain.
        zephyr_version (str): Required packaged Zephyr linker ABI version.

    Returns:
        Layout: Normalized, validated linker layout.

    Raises:
        LayoutError: If the version or layout metadata is invalid or ambiguous.
    """
    if zephyr_version not in SUPPORTED_ZEPHYR_VERSIONS:
        supported = ", ".join(SUPPORTED_ZEPHYR_VERSIONS)
        raise LayoutError(
            f"Zephyr linker template {zephyr_version} is not supported; "
            f"supported versions: {supported}")
    domain = _domain_node(tree, domain_path)
    processor = _processor_from_domain(tree, domain)
    entry = (_property_value(domain, "linker-entry", required=False) or
             "_vector_table")
    linker_refs = domain.propval("linker_memories", list)
    if not linker_refs or linker_refs == [""]:
        raise LayoutError(
            f"{domain.abs_path}: missing required 'linker_memories' property")
    owned = {node.abs_path: node for node in _domain_memory_nodes(tree, domain)}
    memories = []
    for reference in linker_refs:
        target = resolve_memory_node(tree, reference)
        if target.abs_path not in owned:
            raise LayoutError(
                f"{target.abs_path}: linker memory is not owned by domain "
                f"'{domain.abs_path}'")
        memories.append(target)
    memories = list(_normalized_memories(memories, processor))
    if len({memory.name for memory in memories}) != len(memories):
        raise LayoutError("duplicate linker memory name")
    by_path = {memory.node.abs_path: memory for memory in memories}

    def memory_by_reference(reference):
        """Resolve a section region to one selected linker memory.

        Description:
            Restricts section targets to the physical nodes explicitly listed
            by the domain's linker_memories property.

        Args:
            reference (object): Phandle, path, label, or node reference.

        Returns:
            Memory: Selected normalized linker memory.

        Raises:
            LayoutError: If the reference is unresolved or not selected.
        """
        node = resolve_memory_node(tree, reference)
        if node.abs_path not in by_path:
            raise LayoutError(
                f"section region '{reference}' is not in linker_memories")
        return by_path[node.abs_path]

    sections = []
    for name in sorted(SUPPORTED_SECTIONS):
        region_property = linker_section_property(name)
        reference = _property_value(domain, region_property, required=False)
        if reference is None:
            continue
        memory = memory_by_reference(reference).name
        offset = _property_value(
            domain, f"{region_property}-offset", required=False)
        sections.append(Section(name, memory,
                                int(offset) if offset is not None else None))
    sections.extend(_parse_custom_sections(domain, memory_by_reference))
    if len({section.name for section in sections}) != len(sections):
        raise LayoutError("a logical section was assigned more than once")
    _validate_section_policy(tuple(memories), tuple(sections))
    profile = _infer_profile(processor, tuple(memories), tuple(sections), entry)
    output_name = _property_value(
        domain, "linker_file_output_name", required=True)
    user_content = _property_value(
        domain, "linker-user-content", required=False)
    return Layout(processor, profile, entry, zephyr_version,
                  tuple(memories), tuple(sections), domain, str(output_name),
                  str(user_content) if user_content is not None else None)
