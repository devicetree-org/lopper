"""Rendering of the RPU linker templates without a domain linker block.

The ESW and openamp-system-reference RPU linker templates have a placeholder
per section (@VECTORS@, @RODATA@, @BSS@, @HEAP@, @STACK@ and, for R52,
@TCM_REGIONS@). Without a linker block, baremetallinker_xlnx must give them
values that render each template byte for byte as the template did before it
had those placeholders.

The templates live in other repositories. Point the test at them with:

    LOPPER_TEST_ESW=<embeddedsw checkout>
    LOPPER_TEST_ESW_BASE=<git ref before the placeholders>
    LOPPER_TEST_OSR=<openamp-system-reference checkout>
    LOPPER_TEST_OSR_BASE=<git ref before the placeholders>

Each pair is skipped when it is not set.
"""

# Copyright (c) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

import os
import re
import subprocess

import pytest

from lopper.assists.xlnx_rpu_linker import (
    default_section_regions,
    section_cmake_variables,
)

ESW_TEMPLATES = "scripts/linker_files"
OSR_LIBMETAL = "examples/libmetal/machine/remote/amd_rpu/linker_files"
OSR_LEGACY = "examples/legacy_apps/machine/xlnx/linker_files"

# (repository, template, machine) for every template with placeholders.
TEMPLATES = [
    ("ESW", f"{ESW_TEMPLATES}/lscript_r5.ld.in", "psu_cortexr5_0"),
    ("ESW", f"{ESW_TEMPLATES}/lscript_versal_r5.ld.in", "psv_cortexr5_0"),
    ("ESW", f"{ESW_TEMPLATES}/lscript_r52_generic.ld.in", "cortexr52_0"),
    ("ESW", f"{ESW_TEMPLATES}/lscript_versal_net_r5.ld.in",
     "psx_cortexr52_0"),
    ("OSR", f"{OSR_LIBMETAL}/lscript_r5.ld.in", "psu_cortexr5_0"),
    ("OSR", f"{OSR_LIBMETAL}/lscript_versal_r5.ld.in", "psv_cortexr5_0"),
    ("OSR", f"{OSR_LIBMETAL}/lscript_r52_generic.ld.in", "cortexr52_0"),
    ("OSR", f"{OSR_LIBMETAL}/lscript_versal_net_r5.ld.in",
     "psx_cortexr52_0"),
    ("OSR", f"{OSR_LEGACY}/lscript_r5.ld.in", "psu_cortexr5_0"),
    ("OSR", f"{OSR_LEGACY}/lscript_versal_r5.ld.in", "psv_cortexr5_0"),
]

# What baremetallinker_xlnx wrote before the per-section variables.
BASE_VARIABLES = {
    "DDR": "DDR_0", "CODE": "DDR_0", "DATA": "DDR_0",
    "STACK_SIZE": "0x2000", "HEAP_SIZE": "0x2000",
    "MEMORY_SECTION": "MEMORY\n{\n\tDDR_0 : ORIGIN = 0x0, LENGTH = 0x1\n}",
    "RSC_TABLE": "0x9800000",
}


def configure_file(template, variables):
    """Substitute @VAR@ and ${VAR} as CMake configure_file does; an
    undefined variable becomes an empty string."""
    def value(match):
        return variables.get(match.group(1) or match.group(2), "")
    return re.sub(r"@([A-Za-z_][A-Za-z0-9_]*)@|\$\{([A-Za-z_][A-Za-z0-9_]*)\}",
                  value, template)


def _template(repository, path, base):
    """Return a template from the work tree, or at ``base`` when given."""
    root = os.environ.get(f"LOPPER_TEST_{repository}")
    ref = os.environ.get(f"LOPPER_TEST_{repository}_BASE")
    if not root or not ref:
        pytest.skip(f"set LOPPER_TEST_{repository} and "
                    f"LOPPER_TEST_{repository}_BASE")
    if base:
        return subprocess.run(
            ["git", "-C", root, "show", f"{ref}:{path}"], check=True,
            capture_output=True, text=True).stdout
    with open(os.path.join(root, path)) as template:
        return template.read()


@pytest.mark.parametrize("repository, path, machine", TEMPLATES)
def test_template_renders_unchanged_without_a_linker_block(
        repository, path, machine):
    before = configure_file(_template(repository, path, True),
                            BASE_VARIABLES)
    variables = dict(BASE_VARIABLES)
    variables.update(section_cmake_variables(default_section_regions(
        machine, BASE_VARIABLES["CODE"], BASE_VARIABLES["DATA"])))

    after = configure_file(_template(repository, path, False), variables)

    assert after == before
