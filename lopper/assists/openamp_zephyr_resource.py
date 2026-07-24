#/*
# * Copyright (c) 2026 Advanced Micro Devices, Inc. All rights reserved.
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

"""Optional OpenAMP resource-table extension for Zephyr linker policy."""

SECTION_NAMES = {"resource_table"}
FIXED_OFFSET_SECTIONS = SECTION_NAMES


def render_resource_table(section):
    """Render explicit OpenAMP resource-table placement."""
    address = f"ORIGIN({section.memory})"
    if section.offset is not None:
        address += f" + 0x{section.offset:x}"
    return (
        f"    .resource_table {address} : SUBALIGN(4)",
        "    {",
        "        __resource_table_start = .;",
        "        KEEP(*(.resource_table))",
        "        __resource_table_end = .;",
        f"    }} > {section.memory}",
    )
