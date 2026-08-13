#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""
Unit tests for the carrier-board peripheral Kconfig auto-detection added to
gen_domain_dts.py: board_symbol_for_machine, detect_board_i2c_mux_prio,
detect_dt_peripherals, _append_kconfig_entries_before_endif, and the
_reset_board_kconfig_scratch guard against stale scratch-file bleed across
unrelated west lopper-command runs.
"""

from lopper.assists import gen_domain_dts
from lopper.tree import LopperNode, LopperTree


def _tree_with_nodes(*node_specs):
    """Build a LopperTree with one node per (compatible, status) spec."""
    tree = LopperTree()
    for index, (compatible, status) in enumerate(node_specs):
        node = LopperNode(-1, f"/axi/dev@{index}")
        node["compatible"] = [compatible] if isinstance(compatible, str) else list(compatible)
        if status is not None:
            node["status"] = status
        tree + node
    return tree


# --- board_symbol_for_machine -----------------------------------------------

def test_board_symbol_known_processors():
    assert gen_domain_dts.board_symbol_for_machine("psu_cortexa53_0") == "BOARD_ZYNQMP_APU"
    assert gen_domain_dts.board_symbol_for_machine("psv_cortexa72_0") == "BOARD_VERSAL_APU"
    assert gen_domain_dts.board_symbol_for_machine("psx_cortexa78_0") == "BOARD_VERSALNET_APU"
    assert gen_domain_dts.board_symbol_for_machine("cortexa78_0") == "BOARD_VERSAL2_APU"
    assert gen_domain_dts.board_symbol_for_machine("psx_cortexr52_0") == "BOARD_VERSALNET_RPU"
    assert gen_domain_dts.board_symbol_for_machine("cortexr52_0") == "BOARD_VERSAL2_RPU"


def test_board_symbol_unknown_or_empty():
    assert gen_domain_dts.board_symbol_for_machine("some_unknown_proc") is None
    assert gen_domain_dts.board_symbol_for_machine("") is None
    assert gen_domain_dts.board_symbol_for_machine(None) is None


# --- detect_board_i2c_mux_prio ------------------------------------------------

def test_i2c_mux_prio_matches_known_boards():
    for compat in ("xlnx,kcu105-riscv", "xlnx,scu200-riscv", "xlnx,scu35-riscv"):
        entries = gen_domain_dts.detect_board_i2c_mux_prio([compat, "qemu,mbv"])
        assert entries == [
            ("I2C_TCA954X_ROOT_INIT_PRIO", "61"),
            ("I2C_TCA954X_CHANNEL_INIT_PRIO", "62"),
        ]


def test_i2c_mux_prio_no_match_for_unlisted_board():
    # zcu102 has no I2C mux hardware behind it -- must NOT get these entries.
    assert gen_domain_dts.detect_board_i2c_mux_prio(["xlnx,zcu102"]) == []
    assert gen_domain_dts.detect_board_i2c_mux_prio([]) == []
    assert gen_domain_dts.detect_board_i2c_mux_prio(None) == []


# --- detect_dt_peripherals ---------------------------------------------------

def test_detect_dt_peripherals_i2c_eeprom_and_ufs():
    tree = _tree_with_nodes(
        ("cdns,i2c-r1p14", None),
        (["atmel,24c08", "atmel,at24"], "okay"),  # real EEPROM nodes carry both compats
        ("amd,versal2-ufs", "okay"),
    )
    entries = gen_domain_dts.detect_dt_peripherals(tree)
    assert entries == [
        ("I2C", "y"),
        ("EEPROM", "y"),
        ("UFSHC", "y"),
        ("DISK_ACCESS", "y"),
        ("HEAP_MEM_POOL_SIZE", "16384"),
    ]


def test_detect_dt_peripherals_ignores_disabled_nodes():
    tree = _tree_with_nodes(
        ("amd,versal2-ufs", "disabled"),
    )
    assert gen_domain_dts.detect_dt_peripherals(tree) == []


def test_detect_dt_peripherals_no_status_counts_as_enabled():
    tree = _tree_with_nodes(
        ("atmel,at24", None),
    )
    assert gen_domain_dts.detect_dt_peripherals(tree) == [("EEPROM", "y")]


def test_detect_dt_peripherals_dedup_across_matching_nodes():
    tree = _tree_with_nodes(
        ("xlnx,axi-iic-2.1", "okay"),
        ("cdns,i2c-r1p14", "okay"),
    )
    assert gen_domain_dts.detect_dt_peripherals(tree) == [("I2C", "y")]


def test_detect_dt_peripherals_no_match():
    tree = _tree_with_nodes(("xlnx,axi-uartlite-2.0", "okay"))
    assert gen_domain_dts.detect_dt_peripherals(tree) == []


# --- _append_kconfig_entries_before_endif -------------------------------------

def test_append_creates_fresh_file(tmp_path):
    target = tmp_path / "board_Kconfig.defconfig"
    gen_domain_dts._append_kconfig_entries_before_endif(
        str(target), "BOARD_ZCU102_RPU", [("EEPROM", "y")]
    )
    text = target.read_text()
    assert "if BOARD_ZCU102_RPU" in text
    assert "config EEPROM" in text
    assert text.index("config EEPROM") < text.index("endif")


def test_append_onto_existing_same_board_preserves_content(tmp_path):
    target = tmp_path / "board_Kconfig.defconfig"
    target.write_text(
        "if BOARD_ZYNQMP_APU\n\nconfig BUILD_OUTPUT_BIN\n\tdefault y\n\nendif # BOARD_ZYNQMP_APU\n"
    )
    gen_domain_dts._append_kconfig_entries_before_endif(
        str(target), "BOARD_ZYNQMP_APU", [("I2C", "y")]
    )
    text = target.read_text()
    assert "config BUILD_OUTPUT_BIN" in text
    assert "config I2C" in text
    assert text.index("config I2C") < text.rindex("endif")


def test_append_skips_entries_already_present(tmp_path):
    target = tmp_path / "board_Kconfig.defconfig"
    target.write_text(
        "if BOARD_VERSAL2_RPU\n\nconfig I2C\n\tdefault n\n\nendif # BOARD_VERSAL2_RPU\n"
    )
    gen_domain_dts._append_kconfig_entries_before_endif(
        str(target), "BOARD_VERSAL2_RPU", [("I2C", "y")]
    )
    text = target.read_text()
    assert text.count("config I2C") == 1
    assert "default n" in text  # existing entry is left untouched, not overwritten


def test_append_onto_different_board_guard_starts_fresh(tmp_path):
    # A stale file left over from an earlier, unrelated board's run must not
    # be treated as belonging to the current board.
    target = tmp_path / "board_Kconfig.defconfig"
    target.write_text(
        "if BOARD_MBV32\n\nconfig RISCV_ISA_RV32I\n\tdefault y\n\nendif\n"
    )
    gen_domain_dts._append_kconfig_entries_before_endif(
        str(target), "BOARD_VERSAL2_RPU", [("I2C", "y")]
    )
    text = target.read_text()
    assert "BOARD_MBV32" not in text
    assert "RISCV_ISA_RV32I" not in text
    assert "if BOARD_VERSAL2_RPU" in text
    assert "config I2C" in text


# --- _reset_board_kconfig_scratch --------------------------------------------

def test_reset_removes_existing_scratch_file(tmp_path):
    scratch = tmp_path / "board_Kconfig.defconfig"
    scratch.write_text("if BOARD_MBV32\n\nendif\n")
    gen_domain_dts._reset_board_kconfig_scratch(str(tmp_path))
    assert not scratch.exists()


def test_reset_is_noop_when_nothing_to_remove(tmp_path):
    # Must not raise even though the file was never created.
    gen_domain_dts._reset_board_kconfig_scratch(str(tmp_path))


def test_reset_prevents_same_board_cross_run_bleed(tmp_path):
    """
    Regression test for the exact scenario reported in PR review: without the
    reset, a board not in _ARM_BOARD_DEFAULT_KCONFIG (e.g. versal2_apu) that
    picks up an EEPROM entry on one run could silently keep that entry on a
    later run of the *same* board that has nothing to contribute, because
    both write paths for board_Kconfig.defconfig are conditional and skip
    entirely when there's nothing new to add.
    """
    outdir = str(tmp_path)
    scratch = tmp_path / "board_Kconfig.defconfig"

    # Run 1: this design has an EEPROM behind it.
    gen_domain_dts._reset_board_kconfig_scratch(outdir)
    gen_domain_dts._append_kconfig_entries_before_endif(
        str(scratch), "BOARD_VERSAL2_APU", [("EEPROM", "y")]
    )
    assert "config EEPROM" in scratch.read_text()

    # Run 2: same board_symbol, but this design has nothing to contribute --
    # detect_dt_peripherals/detect_board_i2c_mux_prio both return no entries,
    # so _append_dt_peripheral_kconfig would return early without touching
    # the file at all. The reset must have already cleared Run 1's content.
    gen_domain_dts._reset_board_kconfig_scratch(outdir)
    entries_run2 = []
    if entries_run2:
        gen_domain_dts._append_kconfig_entries_before_endif(
            str(scratch), "BOARD_VERSAL2_APU", entries_run2
        )

    assert not scratch.exists(), (
        "Run 2 must not inherit Run 1's EEPROM entry for the same board"
    )
