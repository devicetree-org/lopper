#/*
# * Copyright (c) 2026 Advanced Micro Devices, Inc. All Rights Reserved.
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

"""Section placement for baremetal and FreeRTOS RPU domains.

A baremetal or FreeRTOS domain may carry a ``linker`` block in its YAML.
YAML expansion flattens it into ``linker-layout`` and ``linker-section-*``
domain properties; ``baremetallinker_xlnx`` then calls
:func:`resolve_placement` on the assembled tree to turn them into the linker
region of each section, which it writes as CMake variables for the ESW and
openamp-system-reference linker templates.

Without a ``linker`` block :func:`default_section_regions` gives the values
that render the templates exactly as before the templates had per-section
placeholders.
"""

from dataclasses import dataclass, field

from lopper.audit.memory import MemoryRegion, MemoryRegionType
from lopper.assists.sdt_domains import _carveout_owner
from lopper.tree import LopperNode

try:
    from .zephyr_memory import (
        LayoutError, linker_section_property, resolve_memory_node,
        _domain_memory_nodes, _memory_range)
    from .xlnx_rpu_tcm import (
        TCM_LOCAL_BANKS, r52_tcm_region_words, rpu_cluster_tcm_bases,
        rpu_core_number, rpu_cpu_type, rpu_tcm_bank_owner, tcm_bank_type)
    from .lopper_lib import resolve_domain_cpus
except ImportError:
    from zephyr_memory import (
        LayoutError, linker_section_property, resolve_memory_node,
        _domain_memory_nodes, _memory_range)
    from xlnx_rpu_tcm import (
        TCM_LOCAL_BANKS, r52_tcm_region_words, rpu_cluster_tcm_bases,
        rpu_core_number, rpu_cpu_type, rpu_tcm_bank_owner, tcm_bank_type)
    from lopper_lib import resolve_domain_cpus

LINKER_LAYOUTS = ("tcm", "tcm-data", "ddr")
"""tuple[str]: ``layout`` presets, from most to least TCM use."""

DDR_REGION = "ddr"
"""str: Section region keyword for the default DDR region of the target.

That is the region baremetallinker_xlnx uses for CODE and DATA without a
linker block: the lowest DDR, or the OpenAMP ELF-load carveout in OpenAMP
mode."""

BAREMETAL_LINKER_SECTIONS = (
    "vector_table", "text", "rodata", "data", "bss", "heap", "stack",
    "resource_table",
)
"""tuple[str]: Logical sections of a baremetal or FreeRTOS linker block."""

SECTION_CMAKE_VARIABLES = {
    "vector_table": "VECTORS",
    "text": "CODE",
    "rodata": "RODATA",
    "data": "DATA",
    "bss": "BSS",
    "heap": "HEAP",
    "stack": "STACK",
    "resource_table": "RSC_TABLE_REGION",
}
"""dict[str, str]: CMake variable holding each section's linker region.

The resource table uses ``RSC_TABLE_REGION`` because ``RSC_TABLE`` already
holds the ELF-load carveout address in OpenAMP mode."""

LINKER_OS_TYPES = ("baremetal", "freertos")
"""tuple[str]: ``os,type`` values whose domains baremetallinker_xlnx
places."""

# Section -> bank type, or DDR_REGION, for each preset. "HS" is the bank for
# heap and stack: BTCM on R5, CTCM on R52.
_LAYOUT_PRESETS = {
    "tcm": {
        "vector_table": "ATCM", "text": "ATCM", "rodata": "ATCM",
        "data": "BTCM", "bss": "BTCM", "heap": "HS", "stack": "HS",
        "resource_table": "BTCM",
    },
    "tcm-data": {
        "vector_table": "ATCM", "text": DDR_REGION, "rodata": DDR_REGION,
        "data": "BTCM", "bss": "BTCM", "heap": "HS", "stack": "HS",
        "resource_table": "BTCM",
    },
    "ddr": {
        "vector_table": "ATCM", "text": DDR_REGION, "rodata": DDR_REGION,
        "data": DDR_REGION, "bss": DDR_REGION, "heap": DDR_REGION,
        "stack": DDR_REGION, "resource_table": DDR_REGION,
    },
}


@dataclass(frozen=True)
class RpuRegions:
    """Linker region names a target's templates use for its TCM."""

    cpu_type: str
    vectors: str
    banks: dict


@dataclass
class Placement:
    """Resolved section placement for one baremetal or FreeRTOS domain."""

    domain: object
    sections: dict
    memory: dict = field(default_factory=dict)
    tcm_regions: dict = field(default_factory=dict)

    def cmake_variables(self):
        """Return the section CMake variables in output order."""
        return section_cmake_variables(self.sections)


def section_cmake_variables(sections):
    """Return (CMake variable, linker region) pairs in output order."""
    return [(SECTION_CMAKE_VARIABLES[name], sections[name])
            for name in BAREMETAL_LINKER_SECTIONS]


def linker_os_type(domain):
    """Return the OS name of a domain's ``os,type``, such as ``baremetal``
    for ``baremetal,newlib,3.3.0``, or an empty string."""
    os_type = domain.propval("os,type", list)
    if not os_type or os_type == [""]:
        return ""
    return str(os_type[0]).split(",", 1)[0].strip().lower()


def rpu_regions(machine):
    """Return the TCM linker regions of an RPU target, else None.

    The names are the ones baremetallinker_xlnx writes into MEMORY_SECTION
    and that the templates hard-coded for the vectors before they had
    placeholders.
    """
    if "psu_cortexr5" in machine:
        return RpuRegions("cortexr5", "psu_r5_0_atcm_MEM_0", {
            "ATCM": "psu_r5_0_atcm_MEM_0", "BTCM": "psu_r5_0_btcm_MEM_0"})
    if "psv_cortexr5" in machine:
        return RpuRegions("cortexr5", "psv_r5_0_atcm_MEM_0", {
            "ATCM": "psv_r5_0_atcm_MEM_0", "BTCM": "psv_r5_0_btcm_MEM_0"})
    if "cortexr52" in machine:
        prefix = "psx_" if "psx_cortexr52" in machine else ""
        return RpuRegions("cortexr52", f"{prefix}r52_tcm_alias", {
            "ATCM": f"{prefix}r52_atcm", "BTCM": f"{prefix}r52_btcm",
            "CTCM": f"{prefix}r52_ctcm"})
    return None


def default_section_regions(machine, code, data):
    """Return the section regions used without a linker block, else None.

    The vectors stay in the region the templates hard-coded, the text in
    CODE and every other section in DATA, so the templates render as they
    did before they had per-section placeholders.
    """
    regions = rpu_regions(machine)
    if regions is None:
        return None
    sections = {name: data for name in BAREMETAL_LINKER_SECTIONS}
    sections["vector_table"] = regions.vectors
    sections["text"] = code
    return sections


def has_linker_metadata(domain):
    """Return True when a domain carries an expanded linker block."""
    return any(name == "linker-layout" or name.startswith("linker-section-")
               for name in domain.__props__)


def _is_linker_reference(name):
    """Return True for a domain property holding linker memory references."""
    if name == "linker_memories":
        return True
    return name.startswith("linker-section-") and not name.endswith("-offset")


def linker_referenced_nodes(tree):
    """Return the paths of the memories that baremetal and FreeRTOS linker
    blocks reference through ``linker_memories`` and section regions.

    Per-CPU device tree pruning keeps these nodes so that
    baremetallinker_xlnx can still resolve and check the references.
    """
    try:
        domains = tree["/domains"].subnodes()
    except Exception:
        return set()
    paths = set()
    for domain in domains:
        if linker_os_type(domain) not in LINKER_OS_TYPES:
            continue
        for name, prop in domain.__props__.items():
            if not _is_linker_reference(name):
                continue
            for value in prop.value:
                node = tree.pnode(value) if isinstance(value, int) else None
                if node is not None:
                    paths.add(node.abs_path)
    return paths


def find_linker_domain(tree, cpu_node, linker_block=True):
    """Return the baremetal or FreeRTOS domain whose cpus select
    ``cpu_node``, or None.

    Args:
        linker_block (bool): Only consider domains with linker metadata.

    Raises:
        LayoutError: If more than one such domain selects the CPU.
    """
    try:
        domains = tree["/domains"]
    except Exception:
        return None
    matches = []
    for domain in domains.subnodes():
        if linker_os_type(domain) not in LINKER_OS_TYPES:
            continue
        if linker_block and not has_linker_metadata(domain):
            continue
        selection = resolve_domain_cpus(tree, domain)
        if any(cpu.abs_path == cpu_node.abs_path for cpu in selection.cpus):
            matches.append(domain)
    if len(matches) > 1:
        raise LayoutError(
            f"{cpu_node.abs_path}: more than one "
            f"{'domain with a linker block' if linker_block else 'domain'} "
            "selects this CPU: " + ", ".join(d.abs_path for d in matches))
    return matches[0] if matches else None


def elfload_tcm_sections(tcm_nodes, cpu_node):
    """Return the section placement implied by an ELF load in TCM only.

    Description:
        An OpenAMP or libmetal relation whose ``elfload`` lists only TCM
        banks loads the whole firmware into those banks: vectors and text
        in the ATCM; read-only data, data, BSS and the resource table in
        the BTCM, else the ATCM; heap and stack in the CTCM on R52, else
        the BTCM, else the ATCM. Of several banks of one type, the first
        listed is used.

        Unlike the ``tcm`` layout, the read-only data goes with the data:
        a 64 KB ATCM does not hold the libmetal demo's vectors, text and
        read-only data together on R52, and is nearly full on R5, while
        the BTCM has room.

    Args:
        tcm_nodes (list[LopperNode]): TCM bank nodes of the ELF load.
        cpu_node (LopperNode): Target CPU node.

    Returns:
        dict[str, LopperNode]: Bank node of each section, for the
        ``sections`` argument of :func:`resolve_placement`.

    Raises:
        LayoutError: If the banks include no ATCM, which holds the vectors.
    """
    banks = {}
    for node in tcm_nodes:
        bank = tcm_bank_type(node.name)
        if bank is None:
            raise LayoutError(f"{node.abs_path}: not a TCM bank")
        banks.setdefault(bank, node)
    if "ATCM" not in banks:
        raise LayoutError(
            f"{cpu_node.abs_path}: an ELF load in TCM only must include the "
            "ATCM, which holds the vector table; it lists " +
            ", ".join(node.abs_path for node in tcm_nodes))

    def first(*candidates):
        return next(banks[bank] for bank in candidates if bank in banks)

    heap_stack = (("CTCM", "BTCM", "ATCM")
                  if rpu_cpu_type(cpu_node) == "cortexr52"
                  else ("BTCM", "ATCM"))
    data = first("BTCM", "ATCM")
    return {
        "vector_table": banks["ATCM"], "text": banks["ATCM"],
        "rodata": data, "data": data, "bss": data,
        "heap": first(*heap_stack), "stack": first(*heap_stack),
        "resource_table": data,
    }


def domain_lockstep(domain):
    """Return True when the domain's cpus mode cell selects lockstep."""
    cpus = domain.propval("cpus", list)
    if len(cpus) >= 3 and isinstance(cpus[2], int):
        return bool(cpus[2] & (1 << 30))
    return domain.propval("cpu_config_str", list) == ["lockstep"]


class _BankOwners:
    """Tell which RPU core a TCM bank node belongs to, raising LayoutError
    when the SDT does not say."""

    def __init__(self, tree, cpu_type):
        self.cpu_type = cpu_type
        self.bases = rpu_cluster_tcm_bases(tree, cpu_type)

    def owner(self, node):
        """Return (core number, lockstep alias) for a TCM bank node."""
        owner = rpu_tcm_bank_owner(node, self.cpu_type, self.bases)
        if owner is None:
            raise LayoutError(
                f"{node.abs_path}: cannot tell which RPU core owns this "
                "TCM bank")
        return owner


def _check_bank(owners, node, core):
    """Check that a TCM bank node is usable by ``core`` in split mode."""
    owner, alias = owners.owner(node)
    if not alias and owner == core:
        return
    if alias:
        why = "it is a lockstep bank and the cluster is in split mode"
    else:
        why = f"it belongs to RPU core {owner}"
    raise LayoutError(
        f"{node.abs_path}: TCM bank is not usable by RPU core {core} in "
        f"split mode: {why}")


def _section_reference(domain, name):
    """Return a section's region reference, or None when not set."""
    value = domain.propval(linker_section_property(name), list)
    if not value or value == [""]:
        return None
    if len(value) != 1:
        raise LayoutError(
            f"{domain.abs_path}: {linker_section_property(name)} must "
            "contain one reference")
    return value[0]


def _ipc_carveouts(tree):
    """Return the IPC carveout nodes of the tree.

    These are the carveouts the OpenAMP and libmetal relations under
    /domains list in ``carveouts`` (vrings, buffers, shared memory), and
    the ``reserved-memory`` nodes, at the root or in a domain, that the SDT
    domain inventory classes as shared OpenAMP memory. Before the OpenAMP
    assist runs, a relation may still name its carveouts, and the
    carveouts are still children of the domain's ``reserved-memory``.
    """
    nodes = {}
    try:
        domains = tree["/domains"].subnodes()
    except Exception:
        domains = []
    for node in domains:
        for value in node.propval("carveouts", list):
            if isinstance(value, int):
                target = tree.pnode(value)
            else:
                try:
                    target = resolve_memory_node(tree, value)
                except LayoutError:
                    target = None
            if target is not None:
                nodes[target.abs_path] = target
    for parent in tree["/"].subnodes():
        if parent.name != "reserved-memory":
            continue
        for node in parent.subnodes(children_only=True):
            if _carveout_owner(node) == "shared":
                nodes.setdefault(node.abs_path, node)
    return list(nodes.values())


def _layout_targets(domain, cpu_type):
    """Return the preset bank or DDR_REGION of each section."""
    layout = domain.propval("linker-layout", list)
    layout = layout[0] if layout and layout != [""] else "ddr"
    if layout not in _LAYOUT_PRESETS:
        raise LayoutError(
            f"{domain.abs_path}: layout must be one of " +
            ", ".join(LINKER_LAYOUTS))
    heap_stack_bank = "CTCM" if cpu_type == "cortexr52" else "BTCM"
    return {name: heap_stack_bank if target == "HS" else target
            for name, target in _LAYOUT_PRESETS[layout].items()}


def resolve_placement(tree, domain, cpu_node, machine, ddr_region,
                      region_for_node=None, sections=None):
    """Resolve and check a domain's section placement.

    Description:
        Starts from the ``layout`` preset (``ddr`` when there is none),
        replaces it with the explicit sections, from ``sections`` or else
        from the domain, and checks that the cluster is in split mode, that
        the vector table is in the ATCM, that every TCM bank belongs to the
        core, and that no region a section uses overlaps an IPC carveout in
        ``/reserved-memory``.

    Args:
        tree (LopperTree): Assembled system device tree.
        domain (LopperNode): Baremetal or FreeRTOS domain with linker
            metadata.
        cpu_node (LopperNode): Target CPU node.
        machine (str): Target CPU label, as given to baremetallinker_xlnx.
        ddr_region (tuple[str, int, int]): Linker name, start and size of
            the default DDR region.
        region_for_node (Callable): Maps a non-TCM memory node to a
            (linker name, start, size) tuple, or None when the linker has
            no region for it.
        sections (dict): Section name to memory node or reference, or
            ``ddr``, in place of the domain's ``linker-section-*``
            properties, such as :func:`elfload_tcm_sections` returns.

    Returns:
        Placement: Region of each section, the core-local TCM MEMORY
        entries, and for R52 the TCM region register values.

    Raises:
        LayoutError: If the placement is invalid for the target.
    """
    regions = rpu_regions(machine)
    cpu_type = rpu_cpu_type(cpu_node)
    if regions is None or cpu_type is None or cpu_type != regions.cpu_type:
        raise LayoutError(
            f"{domain.abs_path}: linker placement supports Cortex-R5 and "
            f"Cortex-R52 RPU targets only, not '{machine}'")
    if domain_lockstep(domain):
        raise LayoutError(
            f"{domain.abs_path}: section placement does not support an "
            "RPU cluster in lockstep yet; use split mode")
    local_banks = TCM_LOCAL_BANKS[(cpu_type, "split")]
    core = rpu_core_number(cpu_node.parent, cpu_node)
    if not isinstance(core, int):
        raise LayoutError(
            f"{cpu_node.abs_path}: cannot tell the RPU core number")
    owners = _BankOwners(tree, cpu_type)

    # The domain's own TCM banks must suit the core and the mode.
    try:
        owned = _domain_memory_nodes(tree, domain)
    except LayoutError:
        owned = ()
    for node in owned:
        if node.abs_path.startswith("/reserved-memory"):
            continue
        if tcm_bank_type(node.name) in local_banks:
            _check_bank(owners, node, core)

    listed = None
    references = domain.propval("linker_memories", list)
    if references and references != [""]:
        listed = [resolve_memory_node(tree, ref) for ref in references]
        for node in listed:
            if tcm_bank_type(node.name) in local_banks:
                _check_bank(owners, node, core)
    listed_paths = {node.abs_path for node in listed or []}
    listed_banks = {tcm_bank_type(node.name) for node in listed or []}

    # Region of each section: a bank type, DDR_REGION or "node".
    preset = _layout_targets(domain, cpu_type)
    targets = {}
    for name in BAREMETAL_LINKER_SECTIONS:
        if sections is not None:
            reference = sections.get(name)
        else:
            reference = _section_reference(domain, name)
        if reference is None:
            targets[name] = (preset[name], None)
            continue
        if isinstance(reference, str) and reference == DDR_REGION:
            targets[name] = (DDR_REGION, None)
            continue
        node = (reference if isinstance(reference, LopperNode)
                else resolve_memory_node(tree, reference))
        if listed is not None and node.abs_path not in listed_paths:
            raise LayoutError(
                f"{domain.abs_path}: section '{name}' region "
                f"{node.abs_path} is not in linker_memories")
        bank = tcm_bank_type(node.name)
        if bank is not None and bank not in local_banks:
            raise LayoutError(
                f"{node.abs_path}: {bank} is not a TCM bank of a "
                f"{cpu_type} core")
        if bank is not None:
            _check_bank(owners, node, core)
            targets[name] = (bank, node)
        else:
            targets[name] = ("node", node)

    if targets["vector_table"][0] != "ATCM":
        raise LayoutError(
            f"{domain.abs_path}: vector_table must be placed in the ATCM, "
            "at offset 0")
    offset = domain.propval(
        linker_section_property("vector_table") + "-offset", list)
    if offset and offset not in ([""], [0]):
        raise LayoutError(
            f"{domain.abs_path}: vector_table offset must be 0")

    sections = {}
    used = {}
    for name, (target, node) in targets.items():
        if target == DDR_REGION:
            region, start, size = ddr_region
            ranges = [(start, size)]
        elif target == "node":
            resolved = region_for_node(node) if region_for_node else None
            if resolved is None:
                raise LayoutError(
                    f"{domain.abs_path}: section '{name}' region "
                    f"{node.abs_path} is not a TCM bank or a memory the "
                    f"linker maps for {machine}; use a TCM bank or "
                    f"'{DDR_REGION}'")
            region, start, size = resolved
            ranges = [(start, size)]
        else:
            if listed is not None and target not in listed_banks:
                raise LayoutError(
                    f"{domain.abs_path}: section '{name}' uses the "
                    f"{target}, which linker_memories does not list")
            region = regions.banks[target]
            ranges = _bank_global_ranges(
                tree, owners, core, target, node)
        sections[name] = region
        used.setdefault(region, [])
        used[region].extend(r for r in ranges if r not in used[region])

    _check_carveouts(tree, domain, used, ddr_region)

    memory = {regions.banks[bank]: origin_length
              for bank, origin_length in local_banks.items()}
    tcm_regions = {}
    if cpu_type == "cortexr52":
        used_banks = {target for target, _ in targets.values()}
        def used_origin(bank):
            return local_banks[bank][0] if bank in used_banks else None
        tcm_regions = r52_tcm_region_words(
            used_origin("BTCM"), used_origin("CTCM"))
    return Placement(domain, sections, memory, tcm_regions)


def _bank_global_ranges(tree, owners, core, bank, node):
    """Return the global ranges behind a TCM region, for overlap checks."""
    if node is not None:
        return [_memory_range(node)]
    ranges = []
    first_base = owners.bases[0] if owners.bases else None
    for candidate in tree["/"].subnodes():
        if tcm_bank_type(candidate.name) != bank:
            continue
        try:
            start, size = _memory_range(candidate)
        except LayoutError:
            continue
        # Core-local views, such as psu_r5_0_atcm@0, are not physical.
        if first_base is None or start < first_base:
            continue
        owned = rpu_tcm_bank_owner(candidate, owners.cpu_type, owners.bases)
        if owned is None:
            continue
        owner, alias = owned
        if owner == core and not alias:
            ranges.append((start, size))
    return ranges


def _check_carveouts(tree, domain, used, ddr_region):
    """Reject a section region that overlaps an IPC carveout.

    The default DDR region itself is skipped when it is a carveout: in
    OpenAMP mode it is the ELF-load carveout.
    """
    _, ddr_start, ddr_size = ddr_region
    for carveout in _ipc_carveouts(tree):
        try:
            c_start, c_size = _memory_range(carveout)
        except LayoutError:
            continue
        if (c_start, c_size) == (ddr_start, ddr_size):
            continue
        ipc = MemoryRegion(c_start, c_size, MemoryRegionType.CARVEOUT,
                           carveout.abs_path)
        for region, ranges in used.items():
            for start, size in ranges:
                if ipc.overlaps(MemoryRegion(
                        start, size, MemoryRegionType.PHYSICAL_MEMORY,
                        region)):
                    raise LayoutError(
                        f"{domain.abs_path}: linker region {region} "
                        f"[0x{start:x}, 0x{start + size:x}) overlaps IPC "
                        f"carveout {carveout.abs_path} [0x{c_start:x}, "
                        f"0x{c_start + c_size:x}); place these sections "
                        "in TCM, or build in OpenAMP mode so that DDR is "
                        "the ELF-load carveout")


def r52_tcm_regions_snippet(tcm_regions):
    """Return the linker script lines that set ``_TCM_x_REGION``.

    Standalone boot.S loads each ``_TCM_x_REGION`` word and, when its
    enable bit is set, writes it to that bank's IMP_xTCMREGIONR. The BSP
    defines the words as weak zeros in ``.bootdata``; a linker script
    assignment takes precedence over them. These are the words the Zephyr
    linker writes as ``z_arm_tcm_x_region``. The snippet refers to the
    TCM_x_REGION CMake variables, so the values stay in one place.
    """
    lines = []
    for bank in sorted(tcm_regions):
        lines.append(f"   _TCM_{bank}_REGION = .;")
        lines.append(f"   LONG(${{TCM_{bank}_REGION}})")
    return "\n" + "\n".join(lines)
