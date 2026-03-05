#/*
# * Copyright (c) 2026 Advanced Micro Devices, Inc. All Rights Reserved.
# *
# * Author:
# *       Ricky Sun <ricky.sun@amd.com>
# *       Francisco Iglesias <francisco.iglesias@amd.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */
import sys
import os
import re
import common_utils as utils
from baremetalconfig_xlnx import scan_reg_size

sys.path.append(os.path.dirname(__file__))

def is_compat(node, compat_string_to_test):
    if re.search(r"module,.*gen_qemu_mem_cfg", compat_string_to_test):
        return gen_qemu_mem_cfg_all
    return ""


def gen_qemu_mem_cfg_all(tgt_node, sdt, options):
    """
    Wrapper that generates both the QEMU and matching Vitis NoC SystemC model
    memory config outputs.

    Args:
        tgt_node: Target node number (typically root)
        sdt: System device tree object
        options: Dictionary containing command-line options and args
    """
    gen_qemu_mem_cfg(tgt_node, sdt, options)
    noc_memory_config(tgt_node, sdt, options)
    return True


def extract_noc_memory_regions(tgt_node, sdt):
    """
    Extract NOC memory regions from the SDT.

    Traverses the device tree, filters for NOC memory nodes, and extracts
    address/size pairs from their reg properties.

    Args:
        tgt_node: Target node number (typically root)
        sdt: System device tree object

    Returns:
        List of (address, size) tuples, sorted by address.
        Empty list if no NOC memory found.
    """
    root_node = sdt.tree[tgt_node]
    root_sub_nodes = root_node.subnodes()

    memory_regions = []

    for node in root_sub_nodes:
        try:
            device_type = node.propval("device_type")
            if device_type != ["memory"]:
                continue

            if not any("xlnx,axi-noc" in t for t in node.type):
                continue

            reg_value = node.propval("reg")
            if not reg_value:
                continue

            na = node.parent.propval("#address-cells")[0]
            if not na:
                na = 2
            ns = node.parent.propval("#size-cells")[0]
            if not ns:
                ns = 2

            cells_per_region = na + ns
            num_regions = len(reg_value) // cells_per_region

            for idx in range(num_regions):
                try:
                    address, size = scan_reg_size(node, reg_value, idx)

                    if size == 0:
                        continue

                    memory_regions.append((address, size))

                except Exception as e:
                    continue

        except Exception as e:
            continue

    memory_regions.sort(key=lambda x: x[0])
    return memory_regions


def gen_qemu_mem_cfg(tgt_node, sdt, options):
    """
    Generate memory.qemuboot.conf with QEMU dynamic memory instantiation args.

    Scans the SDT for NOC memory regions and produces a qemuboot.conf file
    with -device amd-ddr-memory arguments for each region above 2GB.
    Regions below 2GB (0x80000000) are skipped as QEMU handles this region.

    Output format:
        [config_bsp]
        qb_mem = -machine dynamic-mem=on -device amd-ddr-memory,address=0x...,size=0x...

    Args:
        tgt_node: Target node number (typically root)
        sdt: System device tree object
        options: Dictionary containing command-line options and args
    """
    if not options.get('outdir', {}):
        raise ValueError("providing an outdir is required")

    outdir = options['outdir']
    memory_regions = extract_noc_memory_regions(tgt_node, sdt)

    if not memory_regions:
        return

    device_args = []
    for address, size in memory_regions:
        if address < 0x80000000:
            continue
        arg = f"-device amd-ddr-memory,address={hex(address)},size={hex(size)}"
        device_args.append(arg)

    if not device_args:
        return

    qb_mem = "-machine dynamic-mem=on " + " ".join(device_args)

    output_file = os.path.join(outdir, "memory.qemuboot.conf")

    if os.path.isfile(output_file) and "-f" not in options.get('args', []):
        print(f"[WARNING] {output_file} already exists, use -f to overwrite")
        return

    with open(output_file, 'w') as fd:
        fd.write("[config_bsp]\n")
        fd.write(f"qb_mem = {qb_mem}\n")

    return True


def noc_memory_config(tgt_node, sdt, options):
    """
    Generate noc_memory_config.txt matching the QEMU memory configuration.

    Extracts NOC memory regions from the SDT and writes them in the format:
        qemu-memory-_ddr@<hex_addr>,<decimal_address>,<decimal_size>

    Args:
        tgt_node: Target node number (typically root)
        sdt: System device tree object
        options: Dictionary containing command-line options and args
    """
    if not options.get('outdir', {}):
        raise ValueError("providing an outdir is required")
    outdir = options['outdir']

    memory_regions = extract_noc_memory_regions(tgt_node, sdt)

    if not memory_regions:
        return

    output_file = os.path.join(outdir, "noc_memory_config.txt")

    if os.path.isfile(output_file) and "-f" not in options.get('args', []):
        print(f"[WARNING] {output_file} already exists, use -f to overwrite")
        return

    with open(output_file, 'w') as fd:
        for address, size in memory_regions:
            addr_str = f"0x{address:08x}" if address == 0 else f"0x{address:x}"
            line = f"qemu-memory-_ddr@{addr_str},{address},{size}\n"
            fd.write(line)

    return True
