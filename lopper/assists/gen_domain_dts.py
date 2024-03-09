#/*
# * Copyright (C) 2023 Advanced Micro Devices, Inc. All Rights Reserved.
# *
# * Author:
# *     Appana Durga Kedareswara rao <appana.durga.kedareswara.rao@amd.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

import yaml
import sys
import os
import glob

sys.path.append(os.path.dirname(__file__))

from baremetalconfig_xlnx import compat_list, get_cpu_node, get_mapped_nodes, get_label
from common_utils import to_cmakelist
import common_utils as utils
from domain_access import update_mem_node

def delete_unused_props( node, driver_proplist ):
    child_list = list(node.child_nodes.keys())
    for child in child_list:
        child_node = node.child_nodes[child]
        delete_unused_props( child_node, driver_proplist)
        if not child_node.child_nodes.keys() and not child_node.__props__.keys():
            node.delete(child_node)
    prop_list = list(node.__props__.keys())
    for prop in prop_list:
        if prop not in driver_proplist:
            node.delete(prop)

def is_compat( node, compat_string_to_test ):
    if "module,gen_domain_dts" in compat_string_to_test:
        return xlnx_generate_domain_dts
    return ""

# tgt_node: is the top level domain node
# sdt: is the system device-tree
# options: User provided options (processor name)
def xlnx_generate_domain_dts(tgt_node, sdt, options):
    root_node = sdt.tree[tgt_node]
    root_sub_nodes = root_node.subnodes()
   
    """
    When user provided processor name and system device-tree as input this
    assist produces as a dts file which contains nodes mapped to that 
    processor instance.
    1. Keep the nodes which has status ok and mapped in the address-map property.
    2. Keep the memory nodes which are mapped in address-map and update them as per
    address-map range.
    3. Delete the other cpu cluster nodes.
    4. Rename the procsessor cpu cluster node to cpus.
    5. Remove delete node lables from symbol node.
    For Linux Device-tree it does the below updates as well
    6. Keeps the status disabled nodes in the final device-tree.
    7. Delete the nodes that have an xlnx,ip-name property value mentioned in the linux_ignore_ip_list.
    """
    machine = options['args'][0]
    symbol_node = sdt.tree['/__symbols__']
    # Get the cpu node for a given Processor
    match_cpunode = get_cpu_node(sdt, options)
    address_map = match_cpunode.parent["address-map"].value
    na = match_cpunode.parent["#ranges-address-cells"].value[0]
    ns = match_cpunode.parent["#ranges-size-cells"].value[0]

    linux_dt = None
    try:
        linux_dt = options['args'][1]
    except IndexError:
        pass

    # Delete other CPU Cluster nodes
    cpunode_list = sdt.tree.nodes('/cpu.*@.*')
    clustercpu_nodes = []
    for node in cpunode_list:
        if node.parent.phandle != match_cpunode.parent.phandle and node.phandle != match_cpunode.parent.phandle:
            clustercpu_nodes.append(node.parent)
    clustercpu_nodes = list(dict.fromkeys(clustercpu_nodes))

    for node in clustercpu_nodes:
        if node.name != '' and node in match_cpunode.parent.subnodes():
            continue
        if node.name != '' and node.name != "idle-states" and node.name != "amba_pl":
            sdt.tree.delete(node)

    cells = na + ns
    tmp = na
    all_phandles = []
    while tmp < len(address_map):
        all_phandles.append(address_map[tmp])
        tmp = tmp + cells + na + 1

    node_list = []
    for node in root_sub_nodes:
        if linux_dt:
            if node.name == "memory@fffc0000" or node.name == "memory@bbf00000":
                sdt.tree.delete(node)
            for entry in node.propval('compatible', list):
                if entry.startswith("xlnx,ddr4-"):
                    sdt.tree.delete(node)
                    break

        if node.propval('status') != ['']:
            if linux_dt and node.name == "smmu@fd800000" and machine == "psu_cortexa53_0":
                # It needs to be disabled only for ZynqMP
                if "okay" in node.propval('status', list)[0]:
                    node.propval('status', list)[0] = "disabled"
            if "okay" in node.propval('status', list)[0]:
                node_list.append(node)
        if node.propval('device_type') != ['']:
            if "memory" in node.propval('device_type', list)[0]:
                node_list.append(node)

    mapped_nodelist = get_mapped_nodes(sdt, node_list, options)
    mapped_nodelist.append(symbol_node)
    mapped_nodelist.append(sdt.tree['/aliases'])

    # Update memory nodes as per address-map cluster mapping
    memnode_list = sdt.tree.nodes('/memory@.*')
    invalid_memnode = []
    for node in memnode_list:
        # Check whether the memory node is mapped to cpu cluster or not
        mem_phandles = [handle for handle in all_phandles if handle == node.phandle]
        prop_val = []
        if mem_phandles:
            mem_phandles = list(dict.fromkeys(mem_phandles))
            # Get all indexes of the address-map for this node
            tmp = na
            indx_list = []
            handle = na
            while handle < len(address_map):
                phandle = address_map[handle]
                for val in mem_phandles:
                    if phandle == val:
                        indx_list.append(handle)
                handle = handle + cells + na + 1
            for inx in indx_list:
                start = [address_map[inx+i+1] for i in range(na)]
                if na == 2 and start[0] != 0:
                    reg = int(f"{hex(start[0])}{start[1]:08x}", base=16)
                    prop_val.append(reg)
                elif na == 2:
                    prop_val.append(start[1])
                else:
                    prop_val.append(start[0])

                size_cells = [address_map[inx+na+i+1] for i in range(ns)]
                size = hex(size_cells[-1])
                if ns > 1:
                    high_size_cell = hex(size_cells[-2])
                else:
                    high_size_cell = "0x0"
                if high_size_cell != "0x0" and ns > 1:
                    size = f"{hex(size_cells[0])}{size_cells[1]:08x}"
                prop_val.append(int(size, base=16))
        else:
            invalid_memnode.append(node)

        modify_val = update_mem_node(node, prop_val)
        node['reg'].value = modify_val

    if invalid_memnode:
        for node in invalid_memnode:
            sdt.tree.delete(node)

    linux_ignore_ip_list = ['xlconstant', 'proc_sys_reset', 'noc_mc_ddr4', 'psv_apu', 'psv_coresight_a720_dbg', 'psv_coresight_a720_etm',
                            'axi_noc', 'psv_coresight_a720_pmu', 'psv_coresight_a720_cti', 'psv_coresight_a721_dbg',
                            'psv_coresight_a721_etm', 'psv_coresight_a721_pmu', 'psv_coresight_a721_cti',
                            'psv_coresight_a721_pmu', 'psv_coresight_a721_cti', 'psv_coresight_apu_ela',
                            'psv_coresight_apu_etf', 'psv_coresight_apu_fun', 'psv_coresight_cpm_atm', 'psv_coresight_cpm_cti2a',
                            'psu_apu', 'psu_bbram_0', 'psu_cci_gpv', 'psu_crf_apb', 'psu_crl_apb',
                            'psu_csu_0', 'psu_ddr_phy', 'psu_ddr_qos_ctrl', 'psu_ddr_xmpu0_cfg', 'psu_ddr_xmpu1_cfg',
                            'psu_ddr_xmpu2_cfg', 'psu_ddr_xmpu3_cfg', 'psu_ddr_xmpu4_cfg', 'psu_ddr_xmpu5_cfg', 'psu_efuse',
                            'psu_fpd_gpv', 'psu_fpd_slcr', 'psu_fpd_slcr_secure', 'psu_fpd_xmpu_cfg', 'psu_fpd_xmpu_sink',
                            'psu_iou_scntr', 'psu_iou_scntrs', 'psu_iousecure_slcr', 'psu_iouslcr_0', 'psu_lpd_slcr',
                            'psu_lpd_slcr_secure', 'psu_lpd_xppu_sink', 'psu_mbistjtag', 'psu_message_buffers', 'psu_ocm_xmpu_cfg',
                            'psu_pcie_attrib_0', 'psu_pcie_dma', 'psu_pcie_high1', 'psu_pcie_high2', 'psu_pcie_low',
                            'psu_pmu_global_0', 'psu_qspi_linear_0', 'psu_rpu', 'psu_rsa', 'psu_siou', 'psu_ipi',
                            'psx_PSM_PPU', 'psx_ram_instr_cntlr', 'psx_rpu',
                            'psx_fpd_gpv', 'ddr4']

    if linux_dt:
        yaml_prune_list = ["xlnx,xdma-host.yaml", "xlnx,rfdc.yaml", "xlnx,sd-fec.yaml", "xlnx,clocking-wizard.yaml"]
        driver_compatlist = []
        # Shouldn't delete properties
        driver_proplist = ["#interrupt-cells", "#address-cells", "#size-cells", "device_type"]
        for yaml_prune in yaml_prune_list:
            yaml_file = os.path.join(os.path.dirname(os.path.realpath(__file__)), yaml_prune)
            schema = utils.load_yaml(yaml_file)
            driver_compatlist = driver_compatlist + compat_list(schema)
            driver_proplist = driver_proplist + schema.get('required',[])
    for node in root_sub_nodes:
        if linux_dt:
            if node.propval('xlnx,ip-name') != ['']:
                val = node.propval('xlnx,ip-name', list)[0]
                if val in linux_ignore_ip_list:
                    sdt.tree.delete(node)
                elif 'xlnx,zynqmp-ipi-mailbox' in node.propval('compatible'):
                    sdt.tree.delete(node)
            elif node.name == "rpu-bus":
                sdt.tree.delete(node)
        if node not in mapped_nodelist:
            if node.propval('device_type') != ['']:
                if "cpu" in node.propval('device_type', list)[0]:
                    continue
            if node.propval('status') != ['']:
                if 'disabled' in node.propval('status', list)[0] and linux_dt:
                    continue
                elif "tcm" in node.propval('compatible', list)[0]:
                    continue
                else:
                    sdt.tree.delete(node)
        elif node.propval('compatible') != [''] and linux_dt:
            is_prune_node = [compat for compat in driver_compatlist if compat in node.propval('compatible', list)]
            if is_prune_node:
                delete_unused_props( node, driver_proplist)

    # Remove symbol node referneces
    symbol_node = sdt.tree['/__symbols__']
    prop_list = list(symbol_node.__props__.keys())
    match_label_list = []
    for node in sdt.tree['/'].subnodes():
        matched_label = get_label(sdt, symbol_node, node)
        if matched_label:
            match_label_list.append(matched_label)
    for prop in prop_list:
        if prop not in match_label_list:
            sdt.tree['/__symbols__'].delete(prop)
        if prop == "gic_a53" or prop == "gic_a72" and linux_dt:
            val = sdt.tree['/__symbols__'].propval(prop, list)[0]
            val = val.replace("apu-bus", "axi")
            sdt.tree['/__symbols__'].propval(prop, list)[0] = val

    # Add new property which will be consumed by other assists
    if not linux_dt:
        sdt.tree['/']['pruned-sdt'] = 1
    else:
        match_cpunode.parent.name = "cpus"
        sdt.tree.sync()
        if sdt.tree['/cpus'].propval('address-map') != ['']:
            sdt.tree['/cpus'].delete('address-map')

    return True
