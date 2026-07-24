#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause

import os
import sys
import textwrap
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lopper", "assists"))

from zephyr_board_dt import (  # noqa: E402
    discover_zephyr_board_files,
    generate_board_overlay_from_sdt,
    resolve_sdt_folder,
)
from lopper.tree import LopperNode, LopperProp, LopperTree  # noqa: E402


def _make_tree(board_name=None):
    tree = LopperTree()
    root = LopperNode()
    root.abs_path = "/"
    root.name = ""
    if board_name:
        root + LopperProp(name="board", value=board_name)
    tree + root
    return tree


def test_discover_board_and_user_by_naming(tmp_path):
    sdt_folder = tmp_path / "sdt"
    sdt_folder.mkdir()
    (sdt_folder / "system-top.dts").write_text('/ { board = "kcu105"; };', encoding="utf-8")
    (sdt_folder / "kcu105_zephyr.dtsi").write_text("/ { };", encoding="utf-8")
    (sdt_folder / "custom.dtsi").write_text("/ { };", encoding="utf-8")

    files = discover_zephyr_board_files(str(sdt_folder), _make_tree("kcu105"))
    assert files["board_dtsi"].endswith("kcu105_zephyr.dtsi")
    assert files["user_zephyr_dtsi"].endswith("custom.dtsi")


def test_discover_ignores_sdt_included_dtsi(tmp_path):
    sdt_folder = tmp_path / "sdt"
    sdt_folder.mkdir()
    (sdt_folder / "system-top.dts").write_text(
        '#include "pl.dtsi"\n/ { board = "kcu105"; };',
        encoding="utf-8",
    )
    (sdt_folder / "kcu105_zephyr.dtsi").write_text("/ { };", encoding="utf-8")
    (sdt_folder / "pl.dtsi").write_text("/ { cpus { }; };", encoding="utf-8")

    files = discover_zephyr_board_files(str(sdt_folder), _make_tree("kcu105"))
    assert files["board_dtsi"].endswith("kcu105_zephyr.dtsi")
    assert files["user_zephyr_dtsi"] is None


def test_discover_user_only(tmp_path):
    sdt_folder = tmp_path / "sdt"
    sdt_folder.mkdir()
    (sdt_folder / "system-top.dts").write_text("/ { };", encoding="utf-8")
    (sdt_folder / "vek385.dtsi").write_text("/ { };", encoding="utf-8")

    files = discover_zephyr_board_files(str(sdt_folder), _make_tree())
    assert files["board_dtsi"] is None
    assert files["user_zephyr_dtsi"].endswith("vek385.dtsi")


def test_discover_user_override_without_board_zephyr_file(tmp_path):
    sdt_folder = tmp_path / "sdt"
    sdt_folder.mkdir()
    (sdt_folder / "system-top.dts").write_text(
        '#include "pl.dtsi"\n/ { board = "kcu105"; };',
        encoding="utf-8",
    )
    (sdt_folder / "pl.dtsi").write_text("/ { };", encoding="utf-8")
    (sdt_folder / "kcu105.dtsi").write_text("/ { };", encoding="utf-8")

    files = discover_zephyr_board_files(str(sdt_folder), _make_tree("kcu105"))
    assert files["board_dtsi"] is None
    assert files["user_zephyr_dtsi"].endswith("kcu105.dtsi")


def test_resolve_sdt_folder_from_args(tmp_path):
    sdt_folder = tmp_path / "sdt"
    sdt_folder.mkdir()

    options = {"args": ["microblaze_riscv_0", "zephyr_dt", str(sdt_folder)]}
    sdt = SimpleNamespace(outdir=str(tmp_path / "out"))
    assert resolve_sdt_folder(options, sdt) == str(sdt_folder)


def test_generate_overlay_user_only(tmp_path):
    sdt_folder = tmp_path / "sdt"
    outdir = tmp_path / "out"
    sdt_folder.mkdir()
    outdir.mkdir()
    user_content = textwrap.dedent(
        """\
        / {
            chosen {
                zephyr,console = &uart0;
            };
        };
        """
    )
    (sdt_folder / "system-top.dts").write_text("/ { };", encoding="utf-8")
    (sdt_folder / "custom.dtsi").write_text(user_content, encoding="utf-8")

    sdt = SimpleNamespace(outdir=str(outdir), tree=_make_tree())
    options = {"args": ["microblaze_riscv_0", "zephyr_dt", str(sdt_folder)]}

    assert generate_board_overlay_from_sdt(sdt, options) is True
    overlay = (outdir / "board.overlay").read_text(encoding="utf-8")
    assert "zephyr,console = &uart0;" in overlay


def test_generate_overlay_board_only(tmp_path):
    sdt_folder = tmp_path / "sdt"
    outdir = tmp_path / "out"
    sdt_folder.mkdir()
    outdir.mkdir()

    board_content = textwrap.dedent(
        """\
        / {
            aliases {
                serial0 = &missing_uart;
            };
        };
        &existing_node {
            status = "okay";
        };
        """
    )
    (sdt_folder / "kcu105_zephyr.dtsi").write_text(board_content, encoding="utf-8")
    (sdt_folder / "system-top.dts").write_text('/ { board = "kcu105"; };', encoding="utf-8")

    tree = _make_tree("kcu105")
    existing = LopperNode()
    existing.abs_path = "/existing_node"
    existing.name = "existing_node"
    existing.label = "existing_node"
    tree + existing

    sdt = SimpleNamespace(outdir=str(outdir), tree=tree)
    options = {"args": ["microblaze_riscv_0", "zephyr_dt", str(sdt_folder)]}

    assert generate_board_overlay_from_sdt(sdt, options) is True
    overlay = (outdir / "board.overlay").read_text(encoding="utf-8")
    assert "missing_uart" not in overlay
    assert "&existing_node" in overlay


def test_generate_overlay_board_and_user_merge(tmp_path):
    sdt_folder = tmp_path / "sdt"
    outdir = tmp_path / "out"
    sdt_folder.mkdir()
    outdir.mkdir()

    board_content = textwrap.dedent(
        """\
        / {
            aliases {
                serial0 = &missing_uart;
            };
        };
        &existing_node {
            status = "okay";
        };
        """
    )
    user_content = textwrap.dedent(
        """\
        / {
            chosen {
                zephyr,sram = &sram0;
            };
        };
        """
    )

    (sdt_folder / "system-top.dts").write_text('/ { board = "kcu105"; };', encoding="utf-8")
    (sdt_folder / "kcu105_zephyr.dtsi").write_text(board_content, encoding="utf-8")
    (sdt_folder / "custom.dtsi").write_text(user_content, encoding="utf-8")

    tree = _make_tree("kcu105")
    existing = LopperNode()
    existing.abs_path = "/existing_node"
    existing.name = "existing_node"
    existing.label = "existing_node"
    tree + existing

    sdt = SimpleNamespace(outdir=str(outdir), tree=tree)
    options = {"args": ["microblaze_riscv_0", "zephyr_dt", str(sdt_folder)]}

    assert generate_board_overlay_from_sdt(sdt, options) is True
    overlay = (outdir / "board.overlay").read_text(encoding="utf-8")
    assert "missing_uart" not in overlay
    assert "&existing_node" in overlay
    assert "zephyr,sram = &sram0;" in overlay
