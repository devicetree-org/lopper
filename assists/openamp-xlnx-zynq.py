#/*
# * Copyright (c) 2019,2020 Xilinx Inc. All rights reserved.
# *
# * Author:
# *       Bruce Ashfield <bruce.ashfield@xilinx.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

import copy
import struct
import sys
import types
import unittest
import os
import getopt
import re
import subprocess
import shutil
from pathlib import Path
from pathlib import PurePath
from io import StringIO
import contextlib
import importlib
from lopper import Lopper
from lopper import LopperFmt
import lopper
from lopper_tree import *
from re import *

def write_one_carveout(f, prefix, addr_prop, range_prop):
    f.write("#define ")
    f.write(prefix+"ADDR\t"+addr_prop+"U\n")
    f.write("#define ")
    f.write(prefix+"RANGE\t"+range_prop+"U\n")

def write_openamp_virtio_rpmsg_info(f, carveout_list, options):
    symbol_name = "CHANNEL_0_MEM_"
    current_channel_count = 0 # if == 4 then got complete channel range
    vring_mems = []
    rsc_mem_pa = -1
    shared_mem_size = -1
    for i in carveout_list:

        if "vdev0buffer" in i[0]:
            current_channel_count += 1
            f.write("#define "+symbol_name+"SHARED_MEM_SIZE\t"+i[1][1]+"\n")
            shared_mem_size = int(i[1][1],16)
        elif "vdev0vring0" in i[0]:
            current_channel_count += 1
            f.write("#define "+symbol_name+"SHARED_MEM_PA\t"+i[1][1]+"\n")
            f.write("#define "+symbol_name+"RING_TX\tFW_RSC_U32_ADDR_ANY\n")
            vring_mems.append(i[1][1])
        elif "vdev0vring1" in i[0]:
            vring_mems.append(i[1][1])
            current_channel_count += 1
            f.write("#define "+symbol_name+"RING_RX\tFW_RSC_U32_ADDR_ANY\n")
        elif "elfload" in i[0]:
            current_channel_count += 1

        if current_channel_count == 4:
            current_channel_count = 0
            vring_mems_size_total = 0
            for i in vring_mems:
                vring_mems_size_total += int(i,16)
            f.write("#define "+symbol_name+"SHARED_BUF_OFFSET\t"+hex(vring_mems_size_total)+"\n")
            f.write("#define "+symbol_name+"VRING_MEM_SIZE\t"+hex(vring_mems_size_total)+"\n")
            vring_mem_size = 0
            f.write("#define "+symbol_name+"RSC_MEM_SIZE\t0x2000UL\n")
            f.write("#define "+symbol_name+"NUM_VRINGS\t2\n")
            f.write("#define "+symbol_name+"VRING_ALIGN\t0x1000\n")
            f.write("#define "+symbol_name+"VRING_SIZE\t256\n")
            f.write("#define "+symbol_name+"NUM_TABLE_ENTRIES\t1\n")
            f.write("#define REMOTE_BUS_NAME\t\"generic\"\n")
            f.write("#define REMOTE_SCUGIC_DEV_NAME\t\"scugic_dev\"\n")
            f.write("#define SCUGIC_PERIPH_BASE\t0xF8F00000\n")
            f.write("#define SCUGIC_DIST_BASE\t(SCUGIC_PERIPH_BASE + 0x00001000)\n")
            f.write("#define ZYNQ_CPU_ID_MASK\t0x1UL\n")
            f.write("/* SGIs */\n")
            f.write("#define SGI_TO_NOTIFY           15 /* SGI to notify the remote */\n")
            f.write("#define SGI_NOTIFICATION        14 /* SGI from the remote */\n")
            f.write("#define NORM_NONCACHE 0x11DE2   /* Normal Non-cacheable */\n")
            f.write("#define STRONG_ORDERED 0xC02    /* Strongly ordered */\n")
            f.write("#define DEVICE_MEMORY 0xC06     /* Device memory */\n")
            f.write("#define RESERVED 0x0            /* reserved memory */\n")

    return [rsc_mem_pa, shared_mem_size]

def write_mem_carveouts(f, carveout_list, options):
    symbol_name = "CHANNEL_0_MEM_"
    current_channel_number = 0
    channel_range = 0
    current_channel_count = 0 # if == 4 then got complete channel range

    for i in carveout_list:

        if "vdev0buffer" in i[0]:
            write_one_carveout(f, symbol_name+"VDEV0BUFFER_", i[1][0], i[1][1])
            channel_range += int(i[1][1],16)
            current_channel_count += 1
        elif "vdev0vring0" in i[0]:
            write_one_carveout(f, symbol_name+"VDEV0VRING0_", i[1][0], i[1][1])
            channel_range += int(i[1][1],16)
            current_channel_count += 1
        elif "vdev0vring1" in i[0]:
            write_one_carveout(f, symbol_name+"VDEV0VRING1_", i[1][0], i[1][1])
            channel_range += int(i[1][1],16)
            current_channel_count += 1
        elif "elfload" in i[0]:
            write_one_carveout(f, symbol_name+"ELFLOAD_", i[1][0], i[1][1])
            channel_range += int(i[1][1],16)
            current_channel_count += 1

        if current_channel_count == 4:
            current_channel_count = 0
            f.write("#define ")
            f.write(symbol_name+"RANGE\t"+str(hex(channel_range))+U"\n\n")
            channel_range = 0

# given write interrupt base addresses and adequate register width to header file
def generate_openamp_file( carveout_list, options):
    if (len(options["args"])) > 0:
        f_name = options["args"][0]
    else:
        f_name = "openamp_lopper_info.h"
    try:
        verbose = options['verbose']
    except:
        verbose = 0

    f = open(f_name, "w")
    f.write("#ifndef OPENAMP_LOPPER_INFO_H_\n")
    f.write("#define OPENAMP_LOPPER_INFO_H_\n\n")

    f.write("\n")
    write_mem_carveouts(f, carveout_list, options)
    [ rsc_mem_pa, shared_mem_size ] = write_openamp_virtio_rpmsg_info(f, carveout_list, options)
    f.write("\n\n#endif /* OPENAMP_LOPPER_INFO_H_ */\n")
    f.close()
    return [rsc_mem_pa,shared_mem_size]

def parse_memory_carevouts_for_zynq(sdt, options):
    try:
        verbose = options['verbose']
    except:
        verbose = 0
    mem_node = sdt.tree["/reserved-memory"]
    remoteproc_node = sdt.tree["/remoteproc0"]
    carveout_list = [] # string representation of mem carveout nodes
    dt_carveout_list = [] # values used for later being put into output DT's
    phandle_list = []

    for node in mem_node.subnodes():
            if len(node.props("compatible")) > 0 and "openamp,xlnx-mem-carveout" in node["compatible"].value:
                phandle_list.append(node.phandle)
                carveout_list.append( ( (str(node), str(node['reg']).replace("reg = <","").replace(">;","").split(" ")) ))
    remoteproc_node["memory-region"].value = phandle_list
    remoteproc_node.sync ( sdt.FDT )
    return carveout_list

def is_compat( node, compat_string_to_test ):
    if re.search( "openamp,xlnx-zynq-a9", compat_string_to_test):
        return xlnx_openamp_zynq
    return ""

# tests for a bit that is set, going fro 31 -> 0 from MSB to LSB
def check_bit_set(n, k):
    if n & (1 << (k)):
        return True

    return False

# tgt_node: is the openamp domain node number
# sdt: is the system device tree
# TODO: this routine needs to be factored and made smaller
def xlnx_openamp_zynq( tgt_node, sdt, options ):
    try:
        verbose = options['verbose']
    except:
        verbose = 0

    if verbose:
        print( "[INFO]: cb: xlnx_openamp_zynq( %s, %s, %s )" % (tgt_node, sdt, verbose))

    domain_node = sdt.tree[tgt_node]

    root_node = sdt.tree["/"]
    try:
        memory_node = sdt.tree[ "/reserved-memory" ]
    except:
        return False
    mem_carveouts = parse_memory_carevouts_for_zynq(sdt, options)
    generate_openamp_file( mem_carveouts, options )
    return True
