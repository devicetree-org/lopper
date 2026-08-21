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
    merge_board_overlay_from_sdt,
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


def _tree_text(tree):
    import tempfile

    with tempfile.NamedTemporaryFile(mode="w", suffix=".dts", delete=False) as fh:
        path = fh.name
    try:
        tree.resolve()
        tree.print(path)
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    finally:
        os.unlink(path)


def test_discover_board_and_user_by_naming(tmp_path):
    sdt_folder = tmp_path / "sdt"
    sdt_folder.mkdir()
    (sdt_folder / "system-top.dts").write_text('/ { board = "kcu105"; };', encoding="utf-8")
    (sdt_folder / "kcu105_zephyr.dtsi").write_text("/ { };", encoding="utf-8")
    (sdt_folder / "custom_zephyr.dtsi").write_text("/ { };", encoding="utf-8")

    files = discover_zephyr_board_files(str(sdt_folder), _make_tree("kcu105"))
    assert files["board_dtsi"].endswith("kcu105_zephyr.dtsi")
    assert files["user_zephyr_dtsi"].endswith("custom_zephyr.dtsi")


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


def test_discover_ignores_unrelated_dtsi(tmp_path):
    sdt_folder = tmp_path / "sdt"
    sdt_folder.mkdir()
    (sdt_folder / "system-top.dts").write_text("/ { };", encoding="utf-8")
    (sdt_folder / "vek385.dtsi").write_text("/ { };", encoding="utf-8")

    files = discover_zephyr_board_files(str(sdt_folder), _make_tree())
    assert files["board_dtsi"] is None
    assert files["user_zephyr_dtsi"] is None


def test_discover_user_zephyr_override_without_board_zephyr_file(tmp_path):
    sdt_folder = tmp_path / "sdt"
    sdt_folder.mkdir()
    (sdt_folder / "system-top.dts").write_text(
        '#include "pl.dtsi"\n/ { board = "kcu105"; };',
        encoding="utf-8",
    )
    (sdt_folder / "pl.dtsi").write_text("/ { };", encoding="utf-8")
    (sdt_folder / "custom_zephyr.dtsi").write_text("/ { };", encoding="utf-8")

    files = discover_zephyr_board_files(str(sdt_folder), _make_tree("kcu105"))
    assert files["board_dtsi"] is None
    assert files["user_zephyr_dtsi"].endswith("custom_zephyr.dtsi")


def test_discover_ignores_transitively_included_dtsi(tmp_path):
    sdt_folder = tmp_path / "sdt"
    sdt_folder.mkdir()
    (sdt_folder / "system-top.dts").write_text(
        '#include "board.dtsi"\n/ { };', encoding="utf-8"
    )
    (sdt_folder / "board.dtsi").write_text(
        '#include "soc_zephyr.dtsi"\n/ { };', encoding="utf-8"
    )
    (sdt_folder / "soc_zephyr.dtsi").write_text("/ { };", encoding="utf-8")

    files = discover_zephyr_board_files(str(sdt_folder), _make_tree())
    assert files["board_dtsi"] is None
    assert files["user_zephyr_dtsi"] is None


def test_discover_include_cycle(tmp_path):
    sdt_folder = tmp_path / "sdt"
    sdt_folder.mkdir()
    (sdt_folder / "system-top.dts").write_text(
        '#include "first.dtsi"\n/ { };', encoding="utf-8"
    )
    (sdt_folder / "first.dtsi").write_text(
        '#include "second_zephyr.dtsi"\n/ { };', encoding="utf-8"
    )
    (sdt_folder / "second_zephyr.dtsi").write_text(
        '#include "first.dtsi"\n/ { };', encoding="utf-8"
    )
    (sdt_folder / "user_zephyr.dtsi").write_text("/ { };", encoding="utf-8")

    files = discover_zephyr_board_files(str(sdt_folder), _make_tree())
    assert files["user_zephyr_dtsi"].endswith("user_zephyr.dtsi")


def test_resolve_sdt_folder_from_args(tmp_path):
    sdt_folder = tmp_path / "sdt"
    sdt_folder.mkdir()

    options = {"args": ["microblaze_riscv_0", "zephyr_dt", str(sdt_folder)]}
    sdt = SimpleNamespace(outdir=str(tmp_path / "out"))
    assert resolve_sdt_folder(options, sdt) == str(sdt_folder)


def test_merge_overlay_user_only(tmp_path):
    sdt_folder = tmp_path / "sdt"
    outdir = tmp_path / "out"
    sdt_folder.mkdir()
    outdir.mkdir()
    user_content = textwrap.dedent(
        """\
        / {
            chosen {
                zephyr,test-user-marker = "user-only-marker";
            };
        };
        """
    )
    (sdt_folder / "system-top.dts").write_text("/ { };", encoding="utf-8")
    (sdt_folder / "custom_zephyr.dtsi").write_text(user_content, encoding="utf-8")

    tree = _make_tree()
    sdt = SimpleNamespace(outdir=str(outdir), tree=tree, tmpdir=str(outdir))
    options = {"args": ["microblaze_riscv_0", "zephyr_dt", str(sdt_folder)]}

    assert merge_board_overlay_from_sdt(sdt, options) is True
    merged = _tree_text(sdt.tree)
    assert "user-only-marker" in merged


def test_merge_overlay_board_only(tmp_path):
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

    sdt = SimpleNamespace(outdir=str(outdir), tree=tree, tmpdir=str(outdir))
    options = {"args": ["microblaze_riscv_0", "zephyr_dt", str(sdt_folder)]}

    assert merge_board_overlay_from_sdt(sdt, options) is True
    merged = _tree_text(sdt.tree)
    assert "missing_uart" not in merged
    assert 'status = "okay"' in merged


def test_merge_overlay_board_and_user_merge(tmp_path):
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
                zephyr,test-user-marker = "board-plus-user";
            };
        };
        """
    )

    (sdt_folder / "system-top.dts").write_text('/ { board = "kcu105"; };', encoding="utf-8")
    (sdt_folder / "kcu105_zephyr.dtsi").write_text(board_content, encoding="utf-8")
    (sdt_folder / "custom_zephyr.dtsi").write_text(user_content, encoding="utf-8")

    tree = _make_tree("kcu105")
    existing = LopperNode()
    existing.abs_path = "/existing_node"
    existing.name = "existing_node"
    existing.label = "existing_node"
    tree + existing

    sdt = SimpleNamespace(outdir=str(outdir), tree=tree, tmpdir=str(outdir))
    options = {"args": ["microblaze_riscv_0", "zephyr_dt", str(sdt_folder)]}

    assert merge_board_overlay_from_sdt(sdt, options) is True
    merged = _tree_text(sdt.tree)
    assert "missing_uart" not in merged
    assert 'status = "okay"' in merged
    assert "board-plus-user" in merged
