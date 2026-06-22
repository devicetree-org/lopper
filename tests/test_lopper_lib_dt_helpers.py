# Copyright (c) 2026 Advanced Micro Devices, Inc. All Rights Reserved.
# Author: Bruce Ashfield <bruce.ashfield@amd.com>
# SPDX-License-Identifier: BSD-3-Clause
"""
Unit tests for the shared devicetree cell / node helpers in lopper_lib.

These primitives (cells<->int, parent/node cell sizes, reg start/size,
arch label, cluster / identity-bus predicates, lopper-source tag) back
the sdt-from-linux assists. They were extracted from per-assist copies
into lopper_lib; this file tests them directly rather than only through
the assists' golden output, so a regression in the primitive is caught
at its source.
"""

import textwrap

import pytest

from lopper import LopperSDT
from lopper.assists import lopper_lib


# --- pure value helpers (no tree needed) --------------------------------

def test_int_to_cells_splits_big_endian():
    assert lopper_lib.int_to_cells(0x1_00000002, 2) == [0x1, 0x2]
    assert lopper_lib.int_to_cells(0x5, 1) == [0x5]
    assert lopper_lib.int_to_cells(0xdeadbeef, 1) == [0xdeadbeef]


def test_cells_to_int_folds():
    assert lopper_lib.cells_to_int([0x1, 0x2]) == 0x1_00000002
    # n + start slicing
    assert lopper_lib.cells_to_int([0xaa, 0xbb, 0xcc, 0xdd], 2, 2) == 0xcc_000000dd
    # short input: missing cells fold in as zero rather than raising
    assert lopper_lib.cells_to_int([0x1], 2, 0) == 0x1_00000000


@pytest.mark.parametrize("value,n", [(0, 2), (0x1234, 1), (0xffffffffff, 2),
                                     (0x1_00000002, 2)])
def test_cells_int_roundtrip(value, n):
    assert lopper_lib.cells_to_int(lopper_lib.int_to_cells(value, n), n) == value


@pytest.mark.parametrize("compat,expected", [
    ("arm,cortex-a72", "a72"),
    (["arm,cortex-r5f"], "r5"),
    ("arm,cortex-m4", "m4"),
    ("arm,armv8", "a72"),
    ("pmc-microblaze", "pmc_microblaze"),
    ("", "unknown"),
    (None, "unknown"),
])
def test_arch_label(compat, expected):
    assert lopper_lib.arch_label(compat) == expected


# --- node-based helpers (small in-memory tree) --------------------------

_TEST_DTS = textwrap.dedent("""\
    /dts-v1/;
    / {
        #address-cells = <2>;
        #size-cells = <2>;
        cpus {
            #address-cells = <1>;
            #size-cells = <0>;
            compatible = "cpus,cluster";
            cpu@0 { device_type = "cpu"; compatible = "arm,cortex-a72"; reg = <0>; };
        };
        cpus-r5@0 {
            compatible = "cpus,cluster";
            cpu@0 { device_type = "cpu"; compatible = "arm,cortex-r5f"; reg = <0>; };
        };
        bus@0 {
            compatible = "simple-bus";
            #address-cells = <2>;
            #size-cells = <2>;
            ranges;
            widget@a0000000 {
                reg = <0x0 0xa0000000 0x0 0x1000>;
                lopper-source = "soc-facts";
            };
        };
        plain@b0000000 {
            reg = <0x0 0xb0000000 0x0 0x2000>;
        };
    };
""")


@pytest.fixture
def tree(tmp_path):
    dts = tmp_path / "helpers.dts"
    dts.write_text(_TEST_DTS)
    try:
        import libfdt  # noqa: F401
        libfdt_available = True
    except ImportError:
        libfdt_available = False
    sdt = LopperSDT(str(dts))
    sdt.dryrun = False
    sdt.verbose = 0
    sdt.werror = False
    sdt.output_file = str(tmp_path / "out.dts")
    sdt.cleanup_flag = True
    sdt.save_temps = False
    sdt.enhanced = True
    sdt.outdir = str(tmp_path)
    sdt.libfdt = libfdt_available
    sdt.setup(str(dts), [], "", True, libfdt=libfdt_available)
    return sdt.tree


def test_node_property_cells_reads_own(tree):
    assert lopper_lib.node_property_cells(tree['/cpus']) == (1, 0)
    assert lopper_lib.node_property_cells(tree['/bus@0']) == (2, 2)


def test_node_property_cells_defaults_when_absent(tree):
    # /plain@b0000000 declares no cell sizes -> DT-spec defaults (2, 1)
    assert lopper_lib.node_property_cells(tree['/plain@b0000000']) == (2, 1)


def test_parent_cells_reads_parent(tree):
    widget = tree['/bus@0/widget@a0000000']
    assert lopper_lib.parent_cells(widget) == (2, 2)


def test_node_reg_start_size(tree):
    widget = tree['/bus@0/widget@a0000000']
    assert lopper_lib.node_reg_start_size(widget) == (0xa0000000, 0x1000)
    plain = tree['/plain@b0000000']
    assert lopper_lib.node_reg_start_size(plain) == (0xb0000000, 0x2000)


def test_is_identity_bus(tree):
    assert lopper_lib.is_identity_bus(tree['/bus@0']) is True
    # /cpus has no ranges property at all -> not an identity bus
    assert lopper_lib.is_identity_bus(tree['/cpus']) is False


def test_is_cpu_cluster(tree):
    assert lopper_lib.is_cpu_cluster(tree['/cpus']) is True          # name + compatible
    assert lopper_lib.is_cpu_cluster(tree['/cpus-r5@0']) is True     # compatible
    assert lopper_lib.is_cpu_cluster(tree['/bus@0']) is False
    assert lopper_lib.is_cpu_cluster(tree['/plain@b0000000']) is False


def test_cluster_arch(tree):
    assert lopper_lib.cluster_arch(tree['/cpus']) == 'a72'
    assert lopper_lib.cluster_arch(tree['/cpus-r5@0']) == 'r5'


def test_source_tag(tree):
    assert lopper_lib.source_tag(tree['/bus@0/widget@a0000000']) == 'soc-facts'
    assert lopper_lib.source_tag(tree['/plain@b0000000']) == ''
