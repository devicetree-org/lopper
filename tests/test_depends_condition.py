"""Per-processor selection of component driver dependencies.

A flat 'depends' dictionary can carry a 'condition' that selects the drivers a
component needs on the target processor, for example the AXI Mailbox on
MicroBlaze and the IPI on the RPU.
"""

# Copyright (c) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

import pytest

from lopper.assists import common_utils as utils

CONDITION = """
if proc in ["microblaze", "microblaze_riscv"]:
    depends = ["mbox"]
else:
    depends = ["ipipsu"]
"""

DEPENDS = {"ipipsu": [], "mbox": ["reg"], "condition": CONDITION}


@pytest.mark.parametrize(
    "proc, expected",
    [
        ("microblaze_riscv", {"mbox": ["reg"]}),
        ("microblaze", {"mbox": ["reg"]}),
        ("psv_cortexr5", {"ipipsu": []}),
        ("cortexr52", {"ipipsu": []}),
    ],
)
def test_condition_selects_drivers(proc, expected):
    mode, reqs = utils.parse_depends_schema(DEPENDS, proc, "Versal", None)
    assert mode == "all"
    assert reqs == expected


def test_condition_needs_processor_and_family():
    # Without the processor or family the condition is not evaluated and
    # every listed driver is kept, as before.
    everything = {"ipipsu": [], "mbox": ["reg"]}
    assert utils.parse_depends_schema(DEPENDS) == ("all", everything)
    assert utils.parse_depends_schema(DEPENDS, "psv_cortexr5", "") == (
        "all", everything)


def test_condition_sees_platform_and_variant():
    depends = {
        "ospipsv": [],
        "condition": 'depends = ["ospipsv"] if platform == "Versal" '
                     'and variant == "x" else []',
    }
    assert utils.select_depends(depends, "psv_cortexr5", "Versal", "x") == {
        "ospipsv": []}
    assert utils.select_depends(depends, "psv_cortexr5", "Versal", "y") == {}


def test_flat_without_condition_unchanged():
    depends = {"ipipsu": [], "scugic": []}
    assert utils.parse_depends_schema(depends, "psv_cortexr5", "Versal") == (
        "all", depends)


def test_oneof_unchanged():
    depends = {"oneOf": [{"ipipsu": []}, {"mbox": []}]}
    assert utils.parse_depends_schema(depends, "microblaze_riscv", "Versal") == (
        "oneOf", {"ipipsu": [], "mbox": []})


def test_failing_condition_selects_nothing():
    depends = {"mbox": [], "condition": "depends = undefined_name"}
    assert utils.select_depends(depends, "microblaze_riscv", "Versal") == {}
