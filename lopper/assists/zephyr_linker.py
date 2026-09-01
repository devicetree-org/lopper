#/*
# * Copyright (c) 2026 Advanced Micro Devices, Inc. All rights reserved.
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

"""Generate a Zephyr primary linker script from execution-domain metadata."""

import os
import re

from lopper.log import _error, _info
from openamp_zephyr_resource import render_resource_table
from zephyr_memory import (
    LayoutError, parse_layout, zephyr_argument_parser,
)

TCM_REGION_ENABLE = 0x1


def is_compat(node, compat_string_to_test):
    """Identify the assist compatibility string.

    Description:
        Registers the generator for the zephyr_linker command name.

    Args:
        node (LopperNode): Assist trigger node; unused.
        compat_string_to_test (str): Dispatcher compatibility string.

    Returns:
        Callable | str: Generator callback on a match, otherwise an empty string.

    Raises:
        None.
    """
    if re.search(r"module,zephyr_linker$", compat_string_to_test):
        return generate_linker
    return ""


def _section_map(layout):
    """Index required logical sections.

    Description:
        Ensures that the versioned complete template receives one destination
        for every required logical section.

    Args:
        layout (Layout): Normalized linker layout.

    Returns:
        dict[str, Section]: Sections indexed by logical name.

    Raises:
        LayoutError: If a required logical section is absent.
    """
    sections = {section.name: section for section in layout.sections}
    required = {"vector_table", "text", "rodata", "data", "bss",
                "noinit", "heap", "stack"}
    missing = sorted(required - sections.keys())
    if missing:
        raise LayoutError("missing section assignments: " + ", ".join(missing))
    if sections["stack"].memory != sections["noinit"].memory:
        raise LayoutError(
            "Zephyr 4.3 places runtime stacks in the noinit ABI group; "
            "stack and noinit must select the same region")
    return sections


def _render_linker(layout, user_contents=None):
    """Render a Zephyr 4.3 primary linker script.

    Description:
        Prepends YAML-derived destinations to a packaged, versioned Cortex-R
        template and emits explicit resource-table placement.

    Args:
        layout (Layout): Normalized linker layout.
        user_contents (str | None): Optional resolved user linker fragment.

    Returns:
        str: Preprocessor-enabled GNU linker source.

    Raises:
        LayoutError: If section grouping is unsupported by the selected ABI.
    """
    sections = _section_map(layout)
    data_load_memory = sections["data"].memory
    if layout.profile == "r52-tcm":
        data_load_memory = sections["vector_table"].memory
    template_path = os.path.join(
        os.path.dirname(__file__), "templates", "zephyr",
        layout.zephyr_version, "cortex-r.ld.in")
    try:
        with open(template_path, "r", encoding="utf-8") as template_file:
            template = template_file.read()
        common_ram_path = os.path.join(
            os.path.dirname(template_path), "common-ram.ld.in")
        with open(common_ram_path, "r", encoding="utf-8") as common_ram_file:
            common_ram = common_ram_file.read()
    except OSError as exc:
        raise LayoutError(
            f"cannot load Zephyr {layout.zephyr_version} linker template: "
            f"{exc}") from exc
    template = template.replace(
        "#include <zephyr/linker/common-ram.ld>", common_ram)
    custom_lines = []
    if layout.profile == "r52-tcm":
        memories_by_kind = {memory.kind: memory for memory in layout.memories}
        atcm = memories_by_kind.get("ATCM")
        btcm = memories_by_kind.get("BTCM")
        ctcm = memories_by_kind.get("CTCM")
        if atcm is None:
            raise LayoutError("R52 TCM profile requires ATCM")
        custom_lines.extend((
            "    /* Cortex-R52 TCM configuration consumed before stack setup.",
            "     * Bit 0 enables a local-address mapping; a zero word leaves",
            "     * the bank's existing boot-firmware configuration unchanged.",
            "     */",
            "    .tcm_config :",
            "    {",
            "        . = ALIGN(4);",
            "        z_arm_tcm_a_region = .;",
            "        LONG(0x00000000)",
            "        z_arm_tcm_b_region = .;",
            f"        LONG(0x{((btcm.origin | TCM_REGION_ENABLE) if btcm else 0):08x})",
            "        z_arm_tcm_c_region = .;",
            f"        LONG(0x{((ctcm.origin | TCM_REGION_ENABLE) if ctcm else 0):08x})",
            f"    }} > {atcm.name}",
            "",
        ))
    for section in (item for item in layout.sections if item.custom):
        custom_address = ""
        if section.offset is not None:
            custom_address = (
                f" ORIGIN({section.memory}) + 0x{section.offset:x}")
        section_type = " (NOLOAD)" if section.noload else ""
        custom_lines.append(
            f"    .{section.name}{custom_address}{section_type} :")
        custom_lines.append("    {")
        if section.alignment is not None:
            custom_lines.append(f"        . = ALIGN({section.alignment});")
        for pattern in section.input_sections:
            expression = f"*({pattern})"
            if section.keep:
                expression = f"KEEP({expression})"
            custom_lines.append(f"        {expression}")
        custom_lines.append(f"    }} > {section.memory}")
        custom_lines.append("")
    placement_lines = []
    for section_name, macro_name in (("vector_table", "VECTOR_ADDRESS"),
                                     ("text", "TEXT_ADDRESS")):
        section = sections[section_name]
        if section.offset is not None:
            placement_lines.append(
                f"#define {macro_name} ORIGIN({section.memory}) + "
                f"0x{section.offset:x}")
    lines = [
        "/* Generated by the Lopper Zephyr linker generator; do not edit. */",
        f"/* Zephyr linker ABI: {layout.zephyr_version}; profile: {layout.profile} */",
        "#undef CONFIG_KERNEL_ENTRY",
        f"#define CONFIG_KERNEL_ENTRY {layout.entry}",
        f"#define VECTOR_REGION {sections['vector_table'].memory}",
        f"#define TEXT_REGION {sections['text'].memory}",
        f"#define RODATA_REGION {sections['rodata'].memory}",
        f"#define DATA_REGION {sections['data'].memory}",
        f"#define DATA_LOAD_REGION {data_load_memory}",
        f"#define BSS_REGION {sections['bss'].memory}",
        f"#define NOINIT_REGION {sections['noinit'].memory}",
        f"#define HEAP_REGION {sections['heap'].memory}",
        f"#define STACK_REGION {sections['stack'].memory}",
        *placement_lines,
        "",
        template,
        "",
        "SECTIONS",
        "{",
        *custom_lines,
    ]
    resource = sections.get("resource_table")
    if resource:
        lines.extend(render_resource_table(resource))
    lines.extend(("}", ""))
    if user_contents is not None:
        lines.extend((
            "/* User linker content follows; generated checks no longer apply. */",
            user_contents.rstrip("\n"),
            "",
        ))
    return "\n".join(lines)


def _read_user_content(path):
    """Read an optional user linker fragment from its resolved path.

    Description:
        Resolves relative paths against the current working directory and
        reports a direct not-found diagnostic without searching other paths.

    Args:
        path (str | None): User-supplied linker fragment path.

    Returns:
        str | None: Fragment contents, or None when no fragment was requested.

    Raises:
        LayoutError: If the requested path is not a regular file.
        OSError: If an existing file cannot be read.
    """
    if path is None:
        return None
    resolved = os.path.abspath(path)
    if not os.path.isfile(resolved):
        raise LayoutError(f"user linker content '{path}' was not found")
    with open(resolved, "r", encoding="utf-8") as user_file:
        return user_file.read()


def _output_path(layout):
    """Return the mandatory linker output from domain metadata.

    Description:
        Uses the selected domain as the single authoritative source for the
        generated linker filename.

    Args:
        layout (Layout): Parsed domain linker layout.
    Returns:
        str: Selected output path.

    Raises:
        LayoutError: If the domain does not provide an output filename.
    """
    if not layout.output_name:
        raise LayoutError(
            f"{layout.domain.abs_path}: missing required "
            "'linker_file_output_name' property")
    return layout.output_name


def _render_report(layout):
    """Render a human-readable resolved layout report.

    Description:
        Records inferred policy, physical ranges, normalized names, and section
        destinations for review without inspecting generated linker syntax.

    Args:
        layout (Layout): Normalized linker layout.

    Returns:
        str: Plain-text report.

    Raises:
        None.
    """
    lines = [f"processor: {layout.processor}", f"profile: {layout.profile}",
             f"zephyr-version: {layout.zephyr_version}",
             f"entry: {layout.entry}", "memories:"]
    if layout.user_content:
        lines.insert(4, f"user-content: {os.path.abspath(layout.user_content)}")
    for memory in layout.memories:
        lines.append(
            f"  {memory.name}: 0x{memory.origin:x}-"
            f"0x{memory.origin + memory.length:x} ({memory.flags}) "
            f"{memory.node.abs_path}")
    lines.append("sections:")
    for section in layout.sections:
        suffix = f" + 0x{section.offset:x}" if section.offset is not None else ""
        custom = " (custom)" if section.custom else ""
        lines.append(f"  {section.name}: {section.memory}{suffix}{custom}")
    return "\n".join(lines) + "\n"


def _write_file(path, contents):
    """Write one generated artifact.

    Description:
        Creates a text artifact using UTF-8 and a deterministic newline policy.

    Args:
        path (str): Destination path.
        contents (str): Complete file contents.

    Returns:
        None.

    Raises:
        OSError: If the destination cannot be written.
    """
    with open(path, "w", encoding="utf-8", newline="\n") as output:
        output.write(contents)


def generate_linker(root_node, sdt, options):
    """Generate linker and resolved-layout artifacts.

    Description:
        Parses command-line options, resolves YAML-derived DT metadata, infers
        the boot profile, and writes the requested primary linker source.

    Args:
        root_node (LopperNode): Assist trigger node; unused.
        sdt (LopperSDT): Input system device tree.
        options (dict): Lopper assist options containing the argument list.

    Returns:
        bool: True on success and False after reporting a user-facing error.

    Raises:
        None. Layout, argument, and file errors are reported through Lopper.
    """
    try:
        parser = zephyr_argument_parser(__doc__)
        parser.add_argument("--map-report")
        args = parser.parse_args(options.get("args", []))
        layout = parse_layout(sdt.tree, args.domain, args.zephyr_version)
        output = _output_path(layout)
        user_contents = _read_user_content(layout.user_content)
        if user_contents is not None:
            _info("zephyr_linker: appending user content; generated layout "
                  "guarantees may no longer apply")
        linker = _render_linker(layout, user_contents)
        report_path = args.map_report or output + ".layout.txt"
        _write_file(output, linker)
        _write_file(report_path, _render_report(layout))
        _info(f"zephyr_linker: inferred {layout.profile}")
        _info(f"zephyr_linker: wrote {output}")
        _info(f"zephyr_linker: wrote {report_path}")
        return True
    except (LayoutError, OSError, SystemExit) as exc:
        _error(f"zephyr_linker: {exc}")
        return False
