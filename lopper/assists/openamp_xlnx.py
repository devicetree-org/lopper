#/*
# * Copyright (c) 2019,2020 Xilinx Inc. All rights reserved.
# *
# * Author:
# *       Bruce Ashfield <bruce.ashfield@xilinx.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

import argparse
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
from lopper.tree import *
from re import *
from string import Template

sys.path.append(os.path.dirname(__file__))
from openamp_xlnx_common import *
from baremetalconfig_xlnx import get_cpu_node
from string import ascii_lowercase as alc

RPU_PATH = "/rpu@ff9a0000"
REMOTEPROC_D_TO_D = "openamp,remoteproc-v1"
REMOTEPROC_D_TO_D_v2 = "openamp,remoteproc-v2"
RPMSG_D_TO_D = "openamp,rpmsg-v1"

def is_compat( node, compat_string_to_test ):
    if re.search( "openamp,xlnx-rpu", compat_string_to_test):
        return xlnx_openamp_rpu
    return ""

def xlnx_handle_relations(sdt, machine, find_only = True, os = None, out_file_name = None):
    tree = sdt.tree

    # get_cpu_node expects dictionary where first arg first element is machine
    match_cpunode = get_cpu_node(sdt, {'args':[machine]})
    if not match_cpunode:
        print("xlnx_handle_relations: unable to find machine: ", machine)
        return False

    parse_routines = { REMOTEPROC_D_TO_D_v2: xlnx_remoteproc_parse, RPMSG_D_TO_D: xlnx_rpmsg_parse }

    # first collect all relevant openamp domains
    remoteproc_relations = []
    rpmsg_relations = []

    for n in sdt.tree["/domains"].subnodes():
        node_compat = n.propval("compatible")[0]
        if node_compat == '':
            continue

        if n.parent.parent.propval("cpus") == ['']:
            continue

        # ensure target domain matches
        if match_cpunode.parent == sdt.tree.pnode(n.parent.parent.propval("cpus")[0]):
            if find_only:
                 return n
            else: # do processing on found ndoes
                if node_compat in parse_routines.keys():
                    arr = remoteproc_relations if node_compat == REMOTEPROC_D_TO_D_v2 else rpmsg_relations
                    arr.append(n)

    # As the RPMsg relation will be appending nodes to a remoteproc node, link the rpmsg
    # relation to its corresponding remoteproc relation so the remoteproc relation can pass along
    # the remoteproc core node
    remoteproc_core_mapping_to_rpmsg_relation = {}

    # used to check if conflicts in ELFLOAD and IPC carveouts
    carveout_validation_arr = []

    for rel in remoteproc_relations:
        ret = xlnx_remoteproc_parse(tree, rel, carveout_validation_arr, 1)
        if not ret:
            return ret

        # save remoteproc core nodes for later use in rpmsg processing
        remoteproc_core_mapping_to_rpmsg_relation.update(ret)

    for rel in rpmsg_relations:
        if not xlnx_rpmsg_parse(tree, rel, machine, carveout_validation_arr, remoteproc_core_mapping_to_rpmsg_relation, os, out_file_name, 1):
            return False

    # check if conflicts in ELFLOAD and IPC carveouts
    if not xlnx_validate_carveouts(tree, carveout_validation_arr):
            return False

    # if here for find case, then return None as failure
    # if processing too and we are here, then this did not encounter error. So return True.
    # note that if the tree does not have openamp nodes True is also returned.
    return None if find_only else True

def xlnx_rpmsg_update_tree_linux(tree, node, ipi_node, core_node, rpmsg_carveouts, verbose = 0 ):
    print(" -> xlnx_rpmsg_update_tree_linux", node)
    vdev0buf_node = [n for n in rpmsg_carveouts if "vdev0buf" in n.name]
    if len(vdev0buf_node) == 1:
        vdev0buf_node[0] + LopperProp(name="compatible", value="shared-dma-pool")
    else:
        print("ERROR: missing or multiple vdev0buf nodes for linux rpmsg case")
        return False

    # vdev0buf should be first after the ELF load prop already in memory-region
    vdev0buf = [ index for index, rc in enumerate(rpmsg_carveouts) if "vdev0buffer" in rc.name ]
    if len(vdev0buf) != 1:
        print("ERROR: xlnx_rpmsg_update_tree_linux: expected 1 vdev0buffer node. got ", vdev0buf)
        return False

    vdev0buf = rpmsg_carveouts.pop(vdev0buf[0])
    rpmsg_carveouts.insert(0, vdev0buf)
    new_mem_region_prop_val = core_node.propval("memory-region")

    [ new_mem_region_prop_val.append(rc.phandle) for rc in rpmsg_carveouts ]

    # If DDRBOOT, ensure that it is after RPMSG carveouts
    # # save ddrboot node and add to end of list
    ddrboot_node_index = [ index for index, phandle in enumerate(new_mem_region_prop_val) if "ddrboot" in tree.pnode(phandle).name ]
    if ddrboot_node_index != []:
        new_mem_region_prop_val.append( new_mem_region_prop_val.pop(ddrboot_node_index[0]) )

    # update property with new values
    core_node["memory-region"].value = new_mem_region_prop_val
    core_node + LopperProp(name="mboxes", value = [ipi_node.phandle, 0, ipi_node.phandle, 1])
    core_node + LopperProp(name="mbox-names", value = ["tx", "rx"])
    return True

# Inputs: openamp-processed SDT, target processor
# If there exists a DDR carveout for ELF-Loading, return the start and size
# of the carveout
def xlnx_openamp_get_ddr_elf_load(machine, sdt):
    # get_cpu_node expects dictionary where first arg first element is machine
    match_cpunode = get_cpu_node(sdt, {'args':[machine]})
    if not match_cpunode:
        print("unable to find machine: ", machine)
        return False

    # map machine to CPU node and then to openamp domain with relation
    target_node = None
    for n in sdt.tree["/domains"].subnodes(children_only=True):
        node_compat = n.propval("compatible")[0]
        # domains must (a) have compatible and (b) relate to CPU
        if node_compat == '' or n.parent.parent.propval("cpus") == ['']:
            continue

        # ensure target domain matches
        if match_cpunode.parent == sdt.tree.pnode(n.parent.parent.propval("cpus")[0]):
             target_node = n
             break

    if target_node == None:
        print("OPENAMP: XLNX: ERROR: unable to map machine", machine, "to relation")
        return False

    # find node described in the domain that is for ELF LOAD

    # remote should only have one relevant host
    rpmsg_rels = target_node.subnodes(children_only=True)
    if len(rpmsg_rels) != 1:
        print("OPENAMP: XLNX: ERROR: expected 1 and only 1 rpmsg relation for ", target_node)
        return False

    rpmsg_rel = rpmsg_rels[0]
    if rpmsg_rel.propval("host") == [''] and len(rpmsg_rel.propval("host")) != 1:
        print("OPENAMP: XLNX: ERROR: expected host prop for", target_node)
        return False

    host = rpmsg_rel.propval("host")
    host_node = sdt.tree.pnode(rpmsg_rel.propval("host")[0])
    if not isinstance(host_node, LopperNode):
        print("OPENAMP: XLNX: ERROR: expected host node ref in host prop for", rpmsg_rel)
        return False

    # look through host for matching remoteproc relation. If found then return the relation's elfload property reg value
    for rel in host_node.subnodes(children_only=True):
        if rel.propval("remote") != [''] and ['openamp,remoteproc-v2'] == rel.parent.propval("compatible"):
            if rel.propval("remote") == ['']:
                print("OPENAMP: XLNX: ERROR: elfload needs remoteproc host to describe elfload region")
                return False

            #  check that the referenced remote matches
            referenced_remote_domain = sdt.tree.pnode(rel.propval("remote")[0])
            if referenced_remote_domain == None or referenced_remote_domain != target_node.parent.parent:
                print("OPENAMP: XLNX: ERROR: referenced remote is invalid for host", rel)
                return False

            elfload_nodes = [ sdt.tree.pnode(i) for i in rel.propval("elfload") ]
            relevant_elfload_nodes = [ i for i in elfload_nodes if i != None ]
            if relevant_elfload_nodes == []:
                print("OPENAMP: XLNX: ERROR: expected at least one ELFLOAD node for case of generating openamp linker script using DDR.")
                return False

            # return reg from match
            reg_val = relevant_elfload_nodes[0].propval("reg")
            if reg_val == ['']:
                print("OPENAMP: XLNX: ERROR: expected 'reg' property for elfload entry", relevant_elfload_nodes[0])
                return False

            return (reg_val[1], reg_val[3])

    print("OPENAMP: XLNX: ERROR: unable to find elf load carveout")
    return False

# Inputs: openamp-processed SDT, target processor, ipi, ipc node
def xlnx_rpmsg_update_tree_zephyr(machine, tree, ipi_node, ipc_nodes):

    if len(ipc_nodes) != 1:
        print("ERROR: zephyr: rpmsg: only length of 1 ipc node allowed. got: ", ipc_nodes)
        return False

    tree['/chosen']['zephyr,ipc_shm'] = ipc_nodes[0].abs_path

    mbox_consumer_node = LopperNode(-1, "/mbox-consumer")
    mbox_consumer_props = { "compatible" : 'vnd,mbox-consumer', "mboxes" : [ipi_node.phandle, 0, ipi_node.phandle, 1], "mbox-names" : ['tx', 'rx'] }
    [mbox_consumer_node + LopperProp(name=n, value=mbox_consumer_props[n]) for n in mbox_consumer_props.keys()]
    tree.add(mbox_consumer_node)

    # delete all other children of ipi parent
    [ tree - ipi_subnode for ipi_subnode in ipi_node.parent.subnodes(children_only=True) if ipi_subnode != ipi_node ]

    if tree['/chosen'].propval('zephyr,flash') != ['']:
        tree['/chosen'].delete(sdt.tree['/chosen']['zephyr,flash'])
    if tree['/chosen'].propval('zephyr,ocm') != ['']:
        tree['/chosen'].delete(sdt.tree['/chosen']['zephyr,ocm'])

    return True

def xlnx_openamp_gen_outputs_only(tree, machine, output_file, memory_region_nodes, host_ipi, verbose = 0 ):
    vrings = [n for n in memory_region_nodes if 'vring' in n.name]
    vring_total_sz = hex(sum([n.propval("reg")[3] for n in vrings]))
    shm_pa = hex(min([n.propval("reg")[1] for n in vrings]))
    shbuf_sz = hex([n.propval("reg")[1] for n in memory_region_nodes if 'vdev0buffer' in n.name][0])

    remote_ipi = host_ipi.parent

    remote_vect_id = remote_ipi.propval('xlnx,int-id')[0]
    ipi_irq_vect_id = hex(remote_vect_id)
    ipi_irq_vect_id_rtos = hex(remote_vect_id-32)
    remote_ipi_str = hex(remote_ipi.propval('reg')[1])
    host_bitmask = hex(host_ipi.propval('xlnx,ipi-bitmask')[0])

    try:
        inputs = {
        "POLL_BASE_ADDR": remote_ipi_str,
        "SHM_DEV_NAME": "\"x.shm\"",
        "DEV_BUS_NAME": "\"generic\"",
        "IPI_DEV_NAME":  "\"y.ipi\"",
        "IPI_IRQ_VECT_ID": ipi_irq_vect_id,
        "IPI_IRQ_VECT_ID_FREERTOS": ipi_irq_vect_id_rtos,
        "IPI_CHN_BITMASK": host_bitmask,
        "RING_TX": "FW_RSC_U32_ADDR_ANY",
        "RING_RX": "FW_RSC_U32_ADDR_ANY",
        "SHARED_MEM_PA": shm_pa,
        "SHARED_MEM_SIZE": shbuf_sz,
        "SHARED_BUF_OFFSET": vring_total_sz,
        "EXTRAS":"",
        }

        with open(output_file, "w") as f:
            output = Template(platform_info_header_r5_template)
            f.write(output.substitute(inputs))
    except Exception as e:
        print("OPENAMP: XLNX: ERROR: xlnx_openamp_gen_outputs_only: Error in generating template for RPU header.", e)
        return False

    return True

def xlnx_rpmsg_parse(tree, rpmsg_relation_node, machine, carveout_validation_arr, channel_to_core_dict = None, os = None, out_file_name = None, verbose = 0 ):
    print(" -> xlnx_rpmsg_parse", rpmsg_relation_node)

    platform = get_platform(tree, verbose)
    if platform == None:
        return False

    for node in rpmsg_relation_node.subnodes(children_only=True):
        pname = "remote" if os == "linux_dt" else "host"
        # check for remote property
        if node.props(pname) == []:
            print("ERROR: ", node, "is missing remote property")
            return False

        remote_node = tree.pnode(node.propval(pname)[0])
        if remote_node == None:
             print("ERROR: invalid rpmsg ", pname," for relation: ", rpmsg_relation_node)
             return False

        if os == "linux_dt" and remote_node.name not in channel_to_core_dict.keys():
            print("ERROR: missing needed remoteproc core node for rpmsg parse to occur.")
            return False

        core_node = channel_to_core_dict[remote_node.name] if os == "linux_dt" else None

        # first find host to remote IPI
        mbox_pval = node.propval("mbox")
        if mbox_pval == ['']:
            print("ERROR: ", node, " is missing mbox property")
            return False

        ipi_node = tree.pnode(mbox_pval[0])
        if ipi_node == None:
            print("ERROR: Unable to find ipi")
            return False

        carveouts_node = tree[node.parent.parent.parent.abs_path + "/domain-to-domain/rpmsg-relation"]
        carveout_prop = node.propval("carveouts")
        if carveout_prop == ['']:
            print("ERROR: ", node, " is missing carveouts property")
            return False

        rpmsg_carveouts = [ tree.pnode(phandle) for phandle in carveout_prop ]

        # validate later
        carveout_validation_arr.extend(rpmsg_carveouts)

        if os == "zephyr_dt" and not xlnx_rpmsg_update_tree_zephyr(machine, tree, ipi_node, rpmsg_carveouts):
            return False
        if os == "linux_dt"  and not xlnx_rpmsg_update_tree_linux(tree, node, ipi_node, core_node, rpmsg_carveouts, verbose):
            return False
        if out_file_name != None:
            return xlnx_openamp_gen_outputs_only(tree, machine, out_file_name, rpmsg_carveouts, ipi_node, verbose)

    return True

# tests for a bit that is set, going fro 31 -> 0 from MSB to LSB
def check_bit_set(n, k):
    if n & (1 << (k)):
        return True

    return False


def determine_cpus_config(remote_domain):
  print(" -> determine_cpus_config ", remote_domain, remote_domain.propval("cpu_config_str"), remote_domain.propval("cpus"))
  if remote_domain.propval("cpu_config_str") == ['']:
      print(" determine_cpus_config failed. could not find cpu_config_str property on remote domain", remote_domain)
      return -1

  if remote_domain.propval("cpu_config_str") not in [ ['split'], ['lockstep'] ]:
      print(" determine_cpus_config failed. invalid cpu_config_str: ", remote_domain.propval("cpu_config_str"))
      return -1

  return { "split": CPU_CONFIG.RPU_SPLIT, "lockstep": CPU_CONFIG.RPU_LOCKSTEP }[remote_domain.propval("cpu_config_str")[0]]

def determinte_rpu_core(tree, cpu_config, remote_node):
    print(" -> determinte_rpu_core", cpu_config, remote_node)
    if remote_node.propval("core_num") == ['']:
        print(" determinte_rpu_core failed. could not find core_num property no node: ", remote_node)
        return False

    core_index = int(remote_node.propval("core_num")[0])
    return RPU_CORE(core_index)

def xlnx_validate_carveouts(tree, carveouts):
    print(" -> xlnx_validate_carveouts")
    expect_ddr = any(["/reserved-memory/" in n.abs_path for n in carveouts])
    try:
        res_mem_node = tree["/reserved-memory"]
    except KeyError:
        if expect_ddr:
            print("ERROR: carveouts should be in reserved memory.")
            return False

        res_mem_node = LopperNode(-1, "/reserved-memory")
        res_mem_node + LopperProp(name="ranges",value=[])
        tree.add(res_mem_node)

    carveout_pairs = [ [ carveout.propval("reg")[1], carveout.propval("reg")[3] ] for carveout in carveouts ]

    # validate no overlaps or conflicts by generating 2d array of reg values from each reserved memory
    # this array contains reg values for such validation
    res_mem_regs = [ n.propval("reg") for n in res_mem_node.subnodes(children_only=True) if n.propval("reg") != [''] ]
    for i in range(len(res_mem_regs)):
        base1, size1 = res_mem_regs[i][1], res_mem_regs[i][3]
        for j in range(i + 1, len(res_mem_regs)):
            base2, size2 = res_mem_regs[j][1], res_mem_regs[j][3]
            if [ base1, size1 ] not in carveout_pairs: # only validate relevant carveouts
                continue
            if base1 < base2 + size2 and base2 < base1 + size1:
                print("ERROR: conflict between reserved memory nodes reg values: ", [ hex(i) for i in [ base1, size1, base2, size2 ] ])
                return False

    return True

def platform_validate(platform):
    if platform not in [ SOC_TYPE.ZYNQMP, SOC_TYPE.VERSAL, SOC_TYPE.VERSAL_NET, SOC_TYPE.VERSAL2 ]:
        print("ERROR: unsupported platform: ", platform)
        return False
    return True

def xlnx_remoteproc_v2_add_cluster(tree, platform, cpu_config, cluster_ranges_val, cluster_node_path):
    driver_compat_str  = {
      SOC_TYPE.ZYNQMP : "xlnx,zynqmp-r5fss",
      SOC_TYPE.VERSAL : "xlnx,versal-r5fss",
      SOC_TYPE.VERSAL_NET : "xlnx,versal-net-r52fss",
      SOC_TYPE.VERSAL2 : "xlnx,versal-net-r52fss",
    }

    cluster_modes = {
        CPU_CONFIG.RPU_SPLIT: 0,
        CPU_CONFIG.RPU_LOCKSTEP: 1,
    }

    cluster_node_props = {
      "compatible" : driver_compat_str[platform],
      "#address-cells": 0x2,
      "#size-cells": 0x2,
      "xlnx,cluster-mode": cluster_modes[cpu_config.value],
      "ranges": cluster_ranges_val,
    }

    # R5 cores also need tcm mode
    if platform in [ SOC_TYPE.ZYNQMP, SOC_TYPE.VERSAL ]:
        cluster_node_props["xlnx,tcm-mode"] = cluster_modes[cpu_config.value]

    try:
        cluster_node = tree[cluster_node_path]

        # here validate if cpu config does not match
        if cluster_modes[cpu_config.value] != cluster_node.propval("xlnx,cluster-mode"):
            print("ERROR: ", "split" if cpu_config == CPU_CONFIG.RPU_SPLIT else "lockstep", "for cpu value mismatches the cluster.")
            return False

        # only in split case, let range value be extended by both cores
        if cpu_config == CPU_CONFIG.RPU_SPLIT:
            cluster_node_props["ranges"].extend(cluster_ranges_val)

    except KeyError:
        cluster_node = LopperNode(-1, cluster_node_path)
        for key in cluster_node_props.keys():
            cluster_node + LopperProp(name=key, value = cluster_node_props[key])

        tree.add(cluster_node)

    return True

def xlnx_remoteproc_v2_add_core(tree, openamp_channel_info, power_domains, core_reg_val, core_reg_names, cluster_node_path, platform):
    print(" --> xlnx_remoteproc_v2_add_core")
    compatible_strs = { SOC_TYPE.VERSAL2:  "xlnx,versal2-r52f", SOC_TYPE.VERSAL_NET:  "xlnx,versal-net-r52f", SOC_TYPE.VERSAL: "xlnx,versal-r5f", SOC_TYPE.ZYNQMP: "xlnx,zynqmp-r5f" }
    core_names = { SOC_TYPE.VERSAL_NET: "r52f", SOC_TYPE.VERSAL: "r5f", SOC_TYPE.ZYNQMP: "r5f" }
    core_names[SOC_TYPE.VERSAL2] = core_names[SOC_TYPE.VERSAL_NET]

    core_node = LopperNode(-1, "{}/{}@{}".format( cluster_node_path, core_names[platform], int(openamp_channel_info["rpu_core"]) % 2))

    core_node_props = {
      "compatible" : compatible_strs[platform],
      "power-domains": power_domains,
      "reg": core_reg_val,
      "reg-names": core_reg_names,
      "memory-region": [ n.phandle for n in openamp_channel_info["new_ddr_nodes"] ]
    }

    if openamp_channel_info["new_ddr_nodes"] == []:
        core_node_props.pop("memory-region")

    for key in core_node_props.keys():
        core_node + LopperProp(name=key, value = core_node_props[key])

    tree.add(core_node)

    return core_node


def xlnx_remoteproc_v2_cluster_base_str(platform, rpu_core):
    base_addresses = {
        SOC_TYPE.VERSAL_NET: {
            RPU_CORE.RPU_0: "eba00000",
            RPU_CORE.RPU_1: "eba00000",
            RPU_CORE.RPU_2: "eba40000",
            RPU_CORE.RPU_3: "eba40000",
        },
        SOC_TYPE.VERSAL2: {
            RPU_CORE.RPU_0: "eba00000",
            RPU_CORE.RPU_1: "eba00000",
            RPU_CORE.RPU_2: "ebb00000",
            RPU_CORE.RPU_3: "ebb00000",
            RPU_CORE.RPU_4: "ebc00000",
            RPU_CORE.RPU_5: "ebc00000",
            RPU_CORE.RPU_6: "ebac0000",
            RPU_CORE.RPU_7: "ebac0000",
            RPU_CORE.RPU_8: "ebbc0000",
            RPU_CORE.RPU_9: "ebbc0000",
        },
        SOC_TYPE.ZYNQMP: {
            RPU_CORE.RPU_0: "ffe00000",
            RPU_CORE.RPU_1: "ffe00000",
        },
        SOC_TYPE.VERSAL: {
            RPU_CORE.RPU_0: "ffe00000",
            RPU_CORE.RPU_1: "ffe00000",
        },
    }

    return base_addresses[platform][rpu_core]

def xlnx_remoteproc_v2_construct_cluster(tree, openamp_channel_info, channel_elfload_nodes, verbose = 0):
    print(" -> xlnx_remoteproc_v2_construct_cluster")
 
    cpu_config = openamp_channel_info["cpu_config"]

    rpu_core = determinte_rpu_core(tree, cpu_config, openamp_channel_info["remote_node"] )
    platform = get_platform(tree, verbose)
    cluster_ranges_val = []
    core_reg_names = []
    power_domains = []
    core_reg_val = []

    global memory_nodes

    if not platform_validate(platform):
        return False

    power_domains = openamp_channel_info["rpu_core_pd_prop"].value

    # loop through TCM nodes
    for n in [ n for n in channel_elfload_nodes if n.propval("xlnx,ip-name") != [''] ]:
        pd = n.propval("power-domains")
        power_domains.extend(pd)
        core_reg_val.extend(memory_nodes[pd[1]]["rpu_view"])
        cluster_ranges_val.extend(memory_nodes[pd[1]]["system_view"])
        core_reg_names.append(n.name)

    # construct remoteproc cluster node
    cluster_node_path = "/remoteproc@" + xlnx_remoteproc_v2_cluster_base_str(platform, rpu_core)
    if not xlnx_remoteproc_v2_add_cluster(tree, platform, cpu_config, cluster_ranges_val, cluster_node_path):
        return False

    openamp_channel_info["new_ddr_nodes"] = [ n for n in channel_elfload_nodes if n.propval("xlnx,ip-name") == [''] ]

    # add individual core node in cluster node
    return xlnx_remoteproc_v2_add_core(tree, openamp_channel_info, power_domains,
                                       core_reg_val, core_reg_names, cluster_node_path, platform)

def xlnx_remoteproc_rpu_parse(tree, node, openamp_channel_info, elfload_nodes, verbose = 0):
    print(" -> xlnx_remoteproc_rpu_parse", node)

    remote_node = openamp_channel_info["remote_node"] 
    cpu_config = determine_cpus_config(remote_node)
    if cpu_config not in [ CPU_CONFIG.RPU_LOCKSTEP, CPU_CONFIG.RPU_SPLIT]:
        print("ERROR: cpu_config: ", cpu_config, " is not in ", [ CPU_CONFIG.RPU_LOCKSTEP, CPU_CONFIG.RPU_SPLIT])
        return False

    rpu_core = determinte_rpu_core(tree, cpu_config, remote_node )
    if rpu_core not in RPU_CORE:
        print("ERROR: Invalid rpu core: ", rpu_core)
        return False

    if remote_node.propval("rpu_pd_val") == ['']:
        print("ERROR: no RPU Power domain value found")
        return False

    openamp_channel_info["rpu_core_pd_prop"] = remote_node.props("rpu_pd_val")[0]
    openamp_channel_info["cpu_config"] = cpu_config
    openamp_channel_info["rpu_core"] = str(int(rpu_core))

    return True

banner_printed = False
def get_platform(tree, verbose = 0):
    # set platform
    global banner_printed
    platform = None
    root_node = tree["/"]

    inputs = root_node.propval("compatible") + root_node.propval("model")

    zynqmp = [ 'Xilinx ZynqMP',  "xlnx,zynqmp" ]
    versal = [ 'xlnx,versal', 'Xilinx Versal']
    versalnet = [ 'versal-net', 'Versal NET', "xlnx,versal-net", "Xilinx Versal NET" ]
    versal2 = [ 'xlnx,versal2', 'amd,versal2', 'amd versal vek385 reva' ]

    rpu_socs = [ versal2, zynqmp, versal, versalnet ]
    rpu_socs_enums = [ SOC_TYPE.VERSAL2, SOC_TYPE.ZYNQMP, SOC_TYPE.VERSAL, SOC_TYPE.VERSAL_NET ]

    if verbose > 0 and not banner_printed:
        print("[INFO]: ------> OPENAMP: XLNX: \t platform info: ", inputs)
        banner_printed = True

    for index, soc in enumerate(rpu_socs):
        for soc_str in soc:
            for i in inputs:
                if i == soc_str:
                    return rpu_socs_enums[index]

    if platform == None:
        print("Unable to find data for platform: ", root_model, root_compat)

    return platform

def xlnx_remoteproc_parse(tree, remoteproc_relation_node, carveout_validation_arr, verbose = 0 ):
    print(" -> xlnx_remoteproc_parse", remoteproc_relation_node)

    # Xilinx OpenAMP subroutine to collect Remoteproc information from relation node in tree
    if get_platform(tree, verbose) == None:
        print("Unsupported platform")
        return False

    channel_to_core_dict = {}

    for node in remoteproc_relation_node.subnodes(children_only=True):
        # check for remote property
        if node.propval("remote") == ['']:
            print("ERROR: ", node, "is missing remote property")
            return False

        remote_node = tree.pnode(node.propval("remote")[0])
        openamp_channel_info = { "remote_node": remote_node }

        # check for elfload prop
        if node.props("elfload") == []:
            print("ERROR: ", node, " is missing elfload property")
            return False

        channel_elfload_nodes = [ tree.pnode(current_elfload) for current_elfload in node.propval("elfload") ]
        # validate later
        carveout_validation_arr.extend(channel_elfload_nodes)

        if not xlnx_remoteproc_rpu_parse(tree, node, openamp_channel_info, channel_elfload_nodes, verbose):
            return False

        core_node = xlnx_remoteproc_v2_construct_cluster(tree, openamp_channel_info, channel_elfload_nodes, verbose = 0)
        if not core_node:
            return False

        channel_to_core_dict[remote_node.name] = core_node # save core node for later use by rpmsg processing

    return channel_to_core_dict

def xlnx_openamp_find_compat_domains(tree, delete_nodes = False):
    try:
        domain_node = tree["/domains"]
    except:
        return False

    for n in tree["/domains"].subnodes(children_only=True):
        node_compat = n.propval("compatible")
        if node_compat == ['']:
            continue
        if node_compat[0] in [ REMOTEPROC_D_TO_D_v2, RPMSG_D_TO_D ]:
            if delete_nodes:
                tree - n
            else:
                return True

    return False

def parse_openamp_args(arg_inputs):
    parser = argparse.ArgumentParser(description="OpenAMP argument parser")
    parser.add_argument("--openamp_output_filename", type=str, help="Output header file name")
    parser.add_argument("--openamp_remote", type=str, help="OpenAMP remote machine name")
    argv = [ arg for arg in arg_inputs if "--openamp_output_filename" in arg or "--openamp_remote" in arg ]
    args = parser.parse_args(argv)
    config = vars(args)

    # Check if zephyr_dt or linux_dt exist in the original input
    config["dt_type"] = "zephyr_dt" if "zephyr_dt" in arg_inputs else "linux_dt" if "linux_dt" in arg_inputs else None

    # set machine based on openamp plugin inputs or gen-domain plugin inputs
    config["machine"] = arg_inputs[0] if config["dt_type"] else config["openamp_remote"]

    # remove now unused record
    del config["openamp_remote"]

    return config

def xlnx_openamp_parse(sdt, options, verbose = 0 ):
    # Xilinx OpenAMP subroutine to parse OpenAMP Channel
    # information and generate Device Tree information.
    print(" -> xlnx_openamp_parse")
    openamp_args = parse_openamp_args(options['args'])
    tree = sdt.tree
    ret = -1
    machine = openamp_args["machine"]

    if not xlnx_openamp_find_compat_domains(tree):
        if verbose > 1:
            print("OPENAMP: XLNX: WARNING: no openamp domains found")
        return True

    if openamp_args["dt_type"] in ["zephyr_dt", "linux_dt"] or openamp_args["openamp_output_filename"]:
        # if find_only is False, then processing will also occur.
        if not xlnx_handle_relations(sdt, machine, False, openamp_args["dt_type"], openamp_args["openamp_output_filename"]):
            return False

        # remove TTC so they do not conflict with RPU - linux only case
        [ tree.delete(node) for node in tree["/"].subnodes() if "cdns,ttc" in node.propval('compatible') and openamp_args["dt_type"] == "linux_dt"]
    else:
        print("OPENAMP: XLNX: WARNING: not doing any processing given inputs", options)

    return True

def xlnx_openamp_rpmsg_expand(tree, subnode, verbose = 0 ):
    # Xilinx-specific YAML expansion of RPMsg description.
    if not resolve_host_remote( tree, subnode, verbose):
        return False
    if not resolve_carveouts(tree, subnode, "carveouts", verbose):
        return False

    return resolve_rpmsg_mbox( tree, subnode, verbose)

def xlnx_openamp_remoteproc_expand(tree, subnode, verbose = 0 ):
    # Xilinx-specific YAML expansion of Remoteproc description.
    if not resolve_host_remote( tree, subnode, verbose):
        return False

    return resolve_carveouts(tree, subnode, "elfload", verbose)
