"""Tests for Cortex-R52 TCM configuration in generated Zephyr linkers."""

# Copyright (c) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

import os
import re
import sys

import pytest

# Lopper loads assists as top-level modules. Mirror that here so the classes
# the generator consumes and raises are the ones this test compares against.
sys.path.append(
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "lopper", "assists"))

from zephyr_linker import _render_linker  # noqa: E402
from zephyr_memory import (  # noqa: E402
    Layout,
    LayoutError,
    Memory,
    MemoryPolicy,
    Section,
)

TCM_POLICY = (MemoryPolicy.READABLE | MemoryPolicy.WRITABLE |
              MemoryPolicy.EXECUTABLE)
CODE_SECTIONS = ("vector_table", "text", "rodata")
DATA_SECTIONS = ("data", "bss", "noinit", "heap", "stack")


def _memory(kind, origin, length=0x8000):
    """Build one normalized linker memory of the given bank kind."""
    return Memory(kind, None, origin, length, TCM_POLICY, kind)


def _layout(*memories, profile="r52-tcm", code="ATCM", data="BTCM"):
    """Build a minimal R52 layout that renders a complete linker script."""
    sections = tuple(
        [Section(name, code) for name in CODE_SECTIONS] +
        [Section(name, data) for name in DATA_SECTIONS])
    return Layout("cortexr52", profile, "_vector_table", "4.3",
                  tuple(memories), sections, None, "RPU_ZEPHYR.ld")


def _tcm_words(script):
    """Return the ordered symbol and word pairs of the .tcm_config block."""
    return re.findall(
        r"(z_arm_tcm_[abc]_region) = \.;\s*\n\s*LONG\((0x[0-9a-f]{8})\)",
        script)


def test_tcm_config_declares_the_zephyr_startup_symbols():
    """Zephyr reads these exact symbols before its stack is usable.

    The names are a cross-repository ABI: renaming one side alone makes
    Zephyr silently fall back to its weak defaults instead of failing.
    """
    script = _render_linker(_layout(
        _memory("ATCM", 0x0, 0x10000),
        _memory("BTCM", 0x10000),
        _memory("CTCM", 0x18000)))

    assert _tcm_words(script) == [
        ("z_arm_tcm_a_region", "0x00000000"),
        ("z_arm_tcm_b_region", "0x00010001"),
        ("z_arm_tcm_c_region", "0x00018001"),
    ]


def test_tcm_config_is_placed_in_the_vector_bank():
    """The words are read at reset, so they load with the vector image."""
    script = _render_linker(_layout(
        _memory("ATCM", 0x0, 0x10000),
        _memory("BTCM", 0x10000),
        _memory("CTCM", 0x18000)))

    start = script.index(".tcm_config")
    assert script[script.index("} >", start):].startswith("} > ATCM")


def test_tcm_config_words_follow_the_selected_bank_origins():
    """Each word carries its bank's local base, not a hard-coded address."""
    script = _render_linker(_layout(
        _memory("ATCM", 0x0, 0x10000),
        _memory("BTCM", 0x20000),
        _memory("CTCM", 0x30000)))

    assert _tcm_words(script) == [
        ("z_arm_tcm_a_region", "0x00000000"),
        ("z_arm_tcm_b_region", "0x00020001"),
        ("z_arm_tcm_c_region", "0x00030001"),
    ]


def test_tcm_config_leaves_an_unselected_bank_unchanged():
    """A domain without CTCM emits a zero word, clearing the enable bit."""
    script = _render_linker(_layout(
        _memory("ATCM", 0x0, 0x10000),
        _memory("BTCM", 0x10000)))

    assert _tcm_words(script) == [
        ("z_arm_tcm_a_region", "0x00000000"),
        ("z_arm_tcm_b_region", "0x00010001"),
        ("z_arm_tcm_c_region", "0x00000000"),
    ]


def test_ddr_profile_emits_no_tcm_config():
    """DDR-booted R52 images do not remap their local banks."""
    script = _render_linker(_layout(
        _memory("DDR", 0x9800100, 0x5de00),
        profile="r52-ddr", code="DDR", data="DDR"))

    assert ".tcm_config" not in script
    assert "z_arm_tcm_" not in script


def test_tcm_profile_requires_atcm():
    """Without ATCM there is no reset-reachable home for the words."""
    with pytest.raises(LayoutError, match="requires ATCM"):
        _render_linker(_layout(_memory("BTCM", 0x10000)))
