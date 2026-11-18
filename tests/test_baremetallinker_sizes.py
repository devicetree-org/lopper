"""Stack and heap sizes from a baremetal or FreeRTOS domain linker block.

A domain may set ``linker: { stack_size, heap_size }`` for any processor.
YAML expansion stores them on the domain, and baremetallinker_xlnx lets them
win over the processor defaults and the app's linker_constraints.
"""

# Copyright (c) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

import io
import os
import sys

import pytest

# baremetallinker_xlnx imports its sibling assists by plain module name.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "lopper", "assists"))

from lopper.assists import baremetallinker_xlnx
from lopper.assists.xlnx_rpu_linker import has_linker_metadata
from lopper.assists.yaml_to_dts_expansion import zephyr_linker_expand
from lopper.assists.zephyr_memory import LayoutError
from lopper.tree import LopperNode, LopperTree


def _node(tree, path, properties=None):
    node = LopperNode(-1, path)
    tree + node
    for name, value in (properties or {}).items():
        node[name] = value
    return node


def _domain_with_linker(properties, os_type="baremetal"):
    tree = LopperTree()
    domain = _node(tree, "/domains/MBV", {"os,type": [os_type]})
    _node(tree, "/domains/MBV/linker", properties)
    tree.sync()
    return tree, domain


def test_sizes_only_linker_block_is_expanded():
    """A block with only sizes is valid and is not section placement."""
    tree, domain = _domain_with_linker(
        {"stack_size": [0x4000], "heap_size": [0x8000]})

    assert zephyr_linker_expand(tree, domain)

    assert domain.propval("linker-stack-size", list) == [0x4000]
    assert domain.propval("linker-heap-size", list) == [0x8000]
    assert not has_linker_metadata(domain)
    with pytest.raises(KeyError):
        tree[f"{domain.abs_path}/linker"]


@pytest.mark.parametrize("value", [[0], [0x400, 0x800], ["big"]])
def test_invalid_size_is_rejected(value):
    tree, domain = _domain_with_linker({"stack_size": value})

    with pytest.raises(LayoutError, match="stack_size must be one positive"):
        zephyr_linker_expand(tree, domain)


def test_empty_linker_block_is_still_rejected():
    tree, domain = _domain_with_linker({})

    with pytest.raises(LayoutError, match="stack_size or heap_size"):
        zephyr_linker_expand(tree, domain)


def _sizes(cpu_ip_name, domain_sizes=None, yaml_file="/nonexistent.yaml"):
    """Run generate_linker_script on one DDR region and return the stack
    and heap sizes it writes."""
    out = io.StringIO()
    baremetallinker_xlnx.generate_linker_script(
        {"ddr": [0x0, 0x100000]}, yaml_file, "app", out, cpu_ip_name,
        f"{cpu_ip_name}_0", None, False, False, False, True,
        {"ddr": "ddr_0"}, None, None, domain_sizes)
    text = out.getvalue()
    return {name: line.split()[1].rstrip(")")
            for line in text.splitlines()
            for name in ("STACK_SIZE", "HEAP_SIZE")
            if line.startswith(f"set({name} ")}


def test_without_domain_sizes_the_defaults_are_unchanged():
    assert _sizes("microblaze_riscv") == {
        "STACK_SIZE": "0x400", "HEAP_SIZE": "0x800"}
    assert _sizes("cortexr52") == {
        "STACK_SIZE": "0x2000", "HEAP_SIZE": "0x2000"}


def test_domain_sizes_override_the_defaults():
    assert _sizes("microblaze_riscv", {"stack": 0x4000, "heap": 0x4000}) == {
        "STACK_SIZE": "0x4000", "HEAP_SIZE": "0x4000"}
    assert _sizes("microblaze_riscv", {"heap": 0x4000}) == {
        "STACK_SIZE": "0x400", "HEAP_SIZE": "0x4000"}


def test_domain_sizes_override_app_linker_constraints(tmp_path):
    app_yaml = tmp_path / "app.yaml"
    app_yaml.write_text("linker_constraints:\n  stack: 0x1000\n  heap: 0x1000\n")

    assert _sizes("cortexr52", yaml_file=str(app_yaml)) == {
        "STACK_SIZE": "0x1000", "HEAP_SIZE": "0x1000"}
    assert _sizes("cortexr52", {"stack": 0x3000}, str(app_yaml)) == {
        "STACK_SIZE": "0x3000", "HEAP_SIZE": "0x1000"}


def test_domain_linker_sizes_reads_the_cpu_domain():
    """The sizes come from the baremetal domain whose cpus select the CPU."""
    tree = LopperTree()
    cluster = _node(tree, "/cpus_microblaze_riscv_0")
    cpu = _node(tree, "/cpus_microblaze_riscv_0/cpu@0")
    cluster.label = "cpus_microblaze_riscv_0"
    domain = _node(tree, "/domains/MBV", {
        "os,type": ["baremetal"],
        "cpus": [cluster.phandle_or_create(), 0x1, 0x0],
        "linker-stack-size": [0x4000]})
    tree.sync()

    class _Sdt:
        pass
    sdt = _Sdt()
    sdt.tree = tree

    assert baremetallinker_xlnx.domain_linker_sizes(sdt, cpu) == {
        "stack": 0x4000}
    tree - domain
    assert baremetallinker_xlnx.domain_linker_sizes(sdt, cpu) == {}
