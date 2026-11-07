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


def osr_legacy_variables(path, variables):
    """Return the OAMP_* values the openamp-system-reference legacy_apps
    CMake derives from Lopper's variables (machine/xlnx/CMakeLists.txt).

    Without RPU_SECTION_PLACEMENT these give the fixed legacy layout:
    resource table metadata, resource table and text at fixed offsets in
    the DDR ELF-load carveout, BSS in DDR and the other sections in BTCM.
    """
    if not path.startswith(OSR_LEGACY):
        return {}
    if variables.get("RPU_SECTION_PLACEMENT"):
        values = {f"OAMP_{name}": variables[name] for name in (
            "CODE", "RODATA", "DATA", "BSS", "HEAP", "STACK")}
        values.update({
            "OAMP_RSC_TABLE": variables["RSC_TABLE_REGION"],
            "OAMP_RSC_TABLE_ADDR": "", "OAMP_TEXT_ADDR": ""})
        if variables.get("RSC_TABLE"):
            values.update({"OAMP_RSC_META": variables["DDR"],
                           "OAMP_RSC_META_ADDR": variables["RSC_TABLE"]})
        else:
            values.update({"OAMP_RSC_META": variables["RSC_TABLE_REGION"],
                           "OAMP_RSC_META_ADDR": ""})
        return values
    btcm = ("psv_r5_0_btcm_MEM_0" if "versal" in path
            else "psu_r5_0_btcm_MEM_0")
    ddr, rsc_table = variables["DDR"], variables["RSC_TABLE"]
    return {
        "OAMP_CODE": ddr, "OAMP_RODATA": btcm, "OAMP_DATA": btcm,
        "OAMP_BSS": ddr, "OAMP_HEAP": btcm, "OAMP_STACK": btcm,
        "OAMP_RSC_TABLE": ddr, "OAMP_RSC_META": ddr,
        "OAMP_RSC_META_ADDR": rsc_table,
        "OAMP_RSC_TABLE_ADDR": f"{rsc_table} + 0x100",
        "OAMP_TEXT_ADDR": f"{rsc_table} + 0x200",
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
    variables.update(osr_legacy_variables(path, variables))

    after = configure_file(_template(repository, path, False), variables)

    assert after == before


@pytest.mark.parametrize("rsc_table, metadata", [
    ("0x9800000", ".resource_table_metadata 0x9800000 : {"),
    ("", ".resource_table_metadata  : {"),
])
@pytest.mark.parametrize("path, machine", [
    (f"{OSR_LEGACY}/lscript_r5.ld.in", "psu_cortexr5_0"),
    (f"{OSR_LEGACY}/lscript_versal_r5.ld.in", "psv_cortexr5_0"),
])
def test_osr_legacy_template_follows_a_tcm_placement(
        path, machine, rsc_table, metadata):
    """With a placement, the legacy apps template puts nothing at a fixed
    DDR address except the resource table metadata, which starts the DDR
    carveout when the YAML gives one."""
    regions = default_section_regions(machine, "ATCM", "BTCM")
    variables = dict(BASE_VARIABLES, RSC_TABLE=rsc_table,
                     RPU_SECTION_PLACEMENT="ON")
    variables.update(section_cmake_variables(regions))
    variables.update(osr_legacy_variables(path, variables))

    script = configure_file(_template("OSR", path, False), variables)

    assert metadata in script
    assert ".resource_table  : {" in script
    assert ".text  : {" in script
    assert "+ 0x" not in script
    assert script.count("> DDR_0") == (1 if rsc_table else 0)
