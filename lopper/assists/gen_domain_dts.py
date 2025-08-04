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
import lopper
import re
from lopper.tree import LopperProp
from lopper.tree import LopperNode

sys.path.append(os.path.dirname(__file__))

from baremetalconfig_xlnx import compat_list, get_cpu_node, get_mapped_nodes, get_label
from common_utils import to_cmakelist
import common_utils as utils
from domain_access import update_mem_node
from openamp_xlnx import xlnx_openamp_find_channels, xlnx_openamp_parse
from openamp_xlnx_common import openamp_linux_hosts, openamp_roles
from openamp_xlnx import xlnx_openamp_zephyr_update_tree
from zephyr_board_dt import process_overlay_with_lopper_api

def delete_unused_props( node, driver_proplist , delete_child_nodes):
    if delete_child_nodes:
        child_list = list(node.child_nodes.keys())
        for child in child_list:
            child_node = node.child_nodes[child]
            delete_unused_props( child_node, driver_proplist, True)
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

def filter_ipi_nodes_for_cpu(sdt, machine):
    """
    Filter IPI nodes for A78 processors:
    Keep the IPI nodes with CPU name matching the expected A78 CPU name.
    """
    # Only process A78 machines, exit early for all others
    if "a78" not in machine.lower():
        return

    # Extract expected A78_* CPU name
    try:
        match = re.search(r'a78[_]?(\d+)', machine.lower())
        expected_cpu_name = f"A78_{match.group(1)}" if match else "A78_0"
    except Exception as e:
        print(f"[ERROR] Failed to extract CPU name from machine '{machine}': {e}")
        return

    try:
        # Direct access to AXI node instead of looping through all subnodes
        try:
            axi_node = sdt.tree['/axi']
        except KeyError:
            print(f"[WARNING] AXI node not found in device tree")
            return

        ipi_nodes_to_remove = []

        for node in axi_node.subnodes():
            # Check if this is an IPI parent node
            if (node.depth == 2 and  # Direct child of /axi/
                node.propval('compatible') != [''] and
                'xlnx,versal-ipi-mailbox' in node.propval('compatible', list) and
                node.propval('xlnx,cpu-name') != [''] and
                node.propval('xlnx,ip-name') != [''] and
                'ipi' in node.propval('xlnx,ip-name', list)[0]):

                ipi_cpu_name = node.propval('xlnx,cpu-name', list)[0]

                # Only keep A78 nodes that match our target
                if ipi_cpu_name.startswith('A78_'):
                    if ipi_cpu_name != expected_cpu_name:
                        ipi_nodes_to_remove.append(node)

        # Remove unwanted A78 IPI parent nodes (this removes parent and all children automatically)
        if ipi_nodes_to_remove:
            for ipi_node in ipi_nodes_to_remove:
                try:
                    sdt.tree.delete(ipi_node)  # This removes parent and all children
                except Exception as e:
                    print(f"[ERROR] Failed to delete IPI node '{ipi_node.name}' (path: {ipi_node.abs_path}): {e}")
                    pass

    except Exception as e:
        print(f"[ERROR] Failed to delete IPI node...")
        return


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
    zephyr_dt = None
    zynqmp_fsbl = None
    try:
        if options['args'][1] == "zephyr_dt":
            zephyr_dt = 1
        elif options['args'][1] == "linux_dt":
            linux_dt = 1
        elif options['args'][1] == "zynqmp_fsbl":
            zynqmp_fsbl = 1

    except IndexError:
        pass

    keep_tcms = None
    try:
        keep_tcms = options['args'][2]
    except IndexError:
        pass

    openamp_machine = None
    if machine not in openamp_linux_hosts and linux_dt != 1:
        openamp_machine = machine

    openamp_present = xlnx_openamp_find_channels(sdt, openamp_machine)
    openamp_host = machine in openamp_linux_hosts and linux_dt == 1
    openamp_remote = machine in openamp_roles.keys() and linux_dt != 1 and machine not in openamp_linux_hosts
    openamp_role = "host" if openamp_host else "remote"

    if openamp_present and (openamp_host or openamp_remote):
        xlnx_options = { "openamp_host":   openamp_roles[machine],
                         "openamp_remote": openamp_roles[machine],
                         "openamp_role":   openamp_role,
                         "zephyr_dt" : True if zephyr_dt == 1 else False,
                         "openamp_no_header": True if "--openamp_no_header" in options['args'] else False,
                         "machine" : machine,
                       }
        xlnx_openamp_parse(sdt, options, xlnx_options, 1)

    # Delete other CPU Cluster nodes
    cpunode_list = sdt.tree.nodes('/cpu.*@.*', strict=True)
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

    # Access the '/amba_pl' node for Linux device tree
    if linux_dt:
        try:
            amba_pl_node = sdt.tree['/amba_pl']
        except KeyError:
            amba_pl_node = None

        if amba_pl_node:
            # Iterate over each subnode of '/amba_pl' at depth 1 only
            nodes_to_move = []
            for subnode in amba_pl_node.subnodes():
                # Only process nodes at depth 1 (direct children of amba_pl)
                if subnode.depth == 2:
                    if len(subnode.subnodes()) > 1:
                        continue
                    has_reg = subnode.propval('reg') != ['']
                    has_ranges = subnode.propval('ranges') != ['']
                    has_at_symbol = '@' in subnode.name

                    # Move node to root if it doesn't have reg or ranges properties, or no @ in node name
                    if not has_reg and not has_ranges or not has_at_symbol:
                        nodes_to_move.append(subnode)

            # Move the identified nodes to root (top-level nodes only, preserving their subnodes)
            for subnode in nodes_to_move:
                # Simply move the node by updating its path and parent
                subnode.abs_path = subnode.abs_path.replace("/amba_pl/", "/")

                # Remove the original node from amba_pl and add the new node to root
                amba_pl_node.delete(subnode)
                sdt.tree.add(subnode)

    filter_ipi_nodes_for_cpu(sdt, machine)

    node_list = []
    cpu_ip = match_cpunode.propval('xlnx,ip-name', list)
    for node in root_sub_nodes:
        if linux_dt:
            is_microblaze = cpu_ip[0] in ('microblaze','microblaze_riscv')
            # Rename memory@ to sram@ for MicroBlaze/MicroBlaze RISC-V if the memory node IP is lmb_bram
            if (is_microblaze and 'lmb_bram' in node.propval('xlnx,ip-name', list)[0] 
                   and node.propval('device_type',list)[0] == "memory"):
                        node.name = node.name.replace("memory","sram")
            if node.name == "memory@fffc0000" or node.name == "memory@bbf00000":
                sdt.tree.delete(node)
            if (keep_tcms == None and not openamp_present) and 'tcm' in node.name:
                sdt.tree.delete(node)
            if node.propval('memory_type', list) == ['linear_flash']:
                sdt.tree.delete(node)
            for entry in node.propval('compatible', list):
                pl_memory_compatible_list = ["xlnx,axi-bram"] if is_microblaze \
                                           else ["xlnx,ddr4", "xlnx,mig-7series","xlnx,lmb-bram","xlnx,axi-bram"]
                if any(entry.startswith(compatible_prefix) for compatible_prefix in pl_memory_compatible_list):
                    sdt.tree.delete(node)
                    break
            if node.propval('xlnx,name') != ['']:
                if node.parent.propval('compatible') != ['']:
                    if not "xlnx,versal-sysmon" in node.parent.propval('compatible'):
                        node.delete('xlnx,name')
            if node.propval('xlnx,interconnect-s-axi-masters') != ['']:
                node.delete('xlnx,interconnect-s-axi-masters')
            if node.propval('xlnx,rable') != ['']:
                node.delete('xlnx,rable')

        if node.propval('status') != ['']:
            if linux_dt and node.name == "smmu@fd800000" and machine == "psu_cortexa53_0":
                # It needs to be disabled only for ZynqMP
                if "okay" in node.propval('status', list)[0]:
                    node.propval('status', list)[0] = "disabled"
            if linux_dt and node.name == "pcie@fd0e0000":
		# It needs to be disabled when pcie-mode is EndPoint
                mode = node.propval('xlnx,pcie-mode')
                if mode == ['Endpoint , Device']:
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
    # Iterate through memories where the device_type is set as "memory."
    memnode_list = [node for node in memnode_list if node.propval('device_type') == ["memory"]]
    invalid_memnode = []
    for node in memnode_list:
        # Check whether the memory node is mapped to cpu cluster or not
        mem_phandles = [handle for handle in all_phandles if handle == node.phandle]
        prop_val = []
        if zynqmp_fsbl and "psu_ddr_0_memory" in node.label:
            continue

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

    if linux_dt:
        for node in memnode_list:
            # Yocto project expects zynq DDR base addresses to start from 0x0 for linux to boot.
            # This has been a legacy expectation which is logically flawed. Adding the temporary
            # workaround to unblock them until the QEMU issue and the yocto integration issues
            # are resolved.
            if node.propval('xlnx,ip-name', list) == ["ps7_ddr"]:
                new_high_addr = node['reg'].value[0] + node['reg'].value[1]
                node['reg'].value = update_mem_node(node, [0, new_high_addr])
                node.name = "memory@0"
                # QEMU boot is not working with compatible property in DDR node for Zynq
                node.delete("compatible")

    if invalid_memnode:
        for node in invalid_memnode:
            sdt.tree.delete(node)

    linux_ignore_ip_list =  ['xlconstant', 'proc_sys_reset', 'psv_apu', 'psv_coresight_a720_dbg', 'psv_coresight_a720_etm',
                            'psv_coresight_a720_pmu', 'psv_coresight_a720_cti', 'psv_coresight_a721_dbg',
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
                            'psu_pmu_global_0', 'psu_qspi_linear_0', 'psu_rpu', 'psu_rsa', 'psu_siou',
                            'psx_PSM_PPU', 'psx_ram_instr_cntlr', 'psx_rpu', 'psx_fpd_gpv', 'ps7_ram', 'ps7_afi',
                            'ps7_pmu', 'ps7_ocmc', 'ps7_scuc', 'ps7_iop_bus_config', 'ps7_gpv', 'psu_ocm_ram_0', 'psv_ocm_ram_0',
                            'psx_ocm_ram', 'ocm_ram', 'psx_ocm_ram_0', 'ocm_ram_0', 'gt_quad_base', 'psv_coresight_apu_cti',
                            'psv_coresight_cpm_cti2d', 'psv_coresight_cpm_ela2a', 'psv_coresight_cpm_ela2b',
                            'psv_coresight_cpm_ela2c', 'psv_coresight_cpm_ela2d', 'psv_coresight_cpm_fun',
                            'psv_coresight_cpm_rom', 'psv_coresight_fpd_atm', 'psv_coresight_fpd_stm',
                            'psv_coresight_lpd_atm', 'psv_crf', 'psv_crl',  'psv_crp', 'psv_fpd_afi',
                            'psv_fpd_cci', 'psv_fpd_gpv', 'psv_fpd_slcr', 'psv_fpd_slcr_secure', 'psv_fpd_smmu',
                            'psv_lpd_afi', 'psv_lpd_iou_secure_slcr', 'psv_lpd_iou_slcr', 'psv_lpd_slcr',
                            'psv_lpd_slcr_secure', 'psv_noc_pcie_0', 'psv_noc_pcie_1', 'psv_noc_pcie_2',
                            'psv_ocm', 'psv_pmc_aes', 'psv_pmc_bbram_ctrl', 'psv_pmc_cfi_cframe', 'psv_pmc_cfu_apb',
                            'psv_pmc_efuse_cache', 'psv_pmc_efuse_ctrl', 'psv_pmc_global', 'psv_pmc_ppu1_mdm',
                            'psv_pmc_ram_npi', 'psv_pmc_rsa', 'psv_pmc_sha', 'psv_pmc_slave_boot', 'psv_scntrs',
                            'psv_pmc_slave_boot_stream', 'psv_pmc_trng', 'psv_psm_global_reg', 'psv_rpu', 'psv_scntr']

    versal_gen2_linux_ignore_ip_list = ['mmi_udh_pll', 'mmi_common', 'mmi_pipe_gem_slcr',
                            'mmi_udh_pll', 'mmi_udh_slcr', 'mmi_usb2phy', 'mmi_usb3phy_crpara', 'mmi_usb3phy_tca',
                            'pmc_rsa', 'pmc_aes', 'pmc_sha2', 'pmc_sha3', "rpu", "apu", "pmc_ppu1_mdm", "pmc_xppu_npi", "pmc_xppu",
                            "pmc_xmpu", "pmc_slave_boot_stream", "pmc_slave_boot", "pmc_ram_npi", "pmc_global", "ocm", "ocm_xmpu",
                            "lpd_xppu", "lpd_systmr_read", "lpd_systmr_ctrl", "lpd_slcr_secure", "lpd_slcr", "lpd_iou_slcr",
                            "lpd_iou_secure_slcr", "lpd_afi", "fpd_systmr_read", "fpd_systmr_ctrl", "fpd_slv_asild_xmpu",
                            "fpd_slv_asilb_xmpu", "fpd_slcr_secure", "fpd_slcr", "fpd_cmn", "fpd_afi", "pmc_efuse_ctrl",
                            "pmc_efuse_cache", "crp", "crf", "crl", "coresight_lpd_atm", "coresight_fpd_stm", "pmc_bbram_ctrl",
                            "pmc_cfi_cframe", "pmc_cfu_apb"]

    linux_ignore_ip_list += versal_gen2_linux_ignore_ip_list

    if linux_dt:
        binding_dir = os.path.join(os.path.dirname(os.path.realpath(__file__)), "yaml_bindings")
        yaml_prune_list = utils.find_files("*.yaml", binding_dir)
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
                if 'xlnx,is-hierarchy' in node.__props__ and 'tcm' not in node.name:
                    sdt.tree.delete(node)
            elif node.name == "rpu-bus":
                sdt.tree.delete(node)
        if node not in mapped_nodelist:
            if node.propval('device_type') != ['']:
                if "cpu" in node.propval('device_type', list)[0]:
                    continue
            if node.propval('status') != ['']:
                if linux_dt and ('disabled' in node.propval('status', list)[0] or "@" not in node.name):
                    delete_pl_node_in_linux_dt = ["xlnx,afi-fpga", "xlnx,fclk"]
                    if any(entry in node.propval('compatible', list) for entry in delete_pl_node_in_linux_dt):
                        sdt.tree.delete(node)
                    else:
                        continue
                elif "tcm" in node.propval('compatible', list)[0]:
                    continue
                elif linux_dt and "xlnx,versal-ddrmc" in node.propval('compatible', list):
                    # ddr controller is not mapped to APU and there is a special handling in SDT to make its status okay.
                    continue
                else:
                    sdt.tree.delete(node)
        elif node.propval('compatible') != [''] and linux_dt:
            is_prune_node = [compat for compat in driver_compatlist if compat in node.propval('compatible', list)]
            delete_child_nodes = False
            if 'xlnx,usp-rf-data-converter-2.6' in node.propval('compatible'):
                delete_child_nodes = True
            if is_prune_node:
                if linux_dt and "qdma" in node.label:
                    mode = node.propval('xlnx,device_port_type')
                delete_unused_props( node, driver_proplist, delete_child_nodes)

                if linux_dt and "qdma" in node.label:
                    if mode == ['PCI_Express_Endpoint_device']:
                        if "okay" in node.propval('status', list)[0]:
                            node.propval('status', list)[0] = "disabled"

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
        if prop == "gic_a53" or prop == "gic_a72" or prop == "gic_its":
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

    if zephyr_dt:
        if "r52" in machine or "a78" in machine:
            xlnx_generate_zephyr_domain_dts_arm(tgt_node, sdt, options, machine)
            if "a78" in machine:
                new_dst_node = LopperNode()
                new_dst_node['compatible'] = "arm,psci-1.1"
                new_dst_node['method'] = "smc"
                new_dst_node.abs_path = "/psci "
                new_dst_node.name = "psci "
                sdt.tree + new_dst_node
        else:
            xlnx_generate_zephyr_domain_dts(tgt_node, sdt, options)
            zephyr_supported_schema_file = os.path.join(os.path.dirname(os.path.realpath(__file__)), "zephyr_supported_comp.yaml")
            if utils.is_file(zephyr_supported_schema_file):
                match_cpunode = get_cpu_node(sdt, options)
                schema = utils.load_yaml(zephyr_supported_schema_file)
                proplist = schema["amd,mbv32"]["required"]
                delete_unused_props( match_cpunode, proplist, False)
                match_cpunode.parent.name = "cpus"
        zephyr_board_dt = None
        try:
            zephyr_board_dt = options['args'][2]
        except IndexError:
            pass
        if zephyr_board_dt and os.path.exists(zephyr_board_dt):
            try:
                # Read the overlay file
                with open(zephyr_board_dt, 'r') as f:
                    overlay_content = f.read()
                cleaned_content = process_overlay_with_lopper_api(overlay_content, sdt.tree)
                with open(os.path.join(sdt.outdir, "board.overlay"), 'w') as f:
                    f.write(cleaned_content)
            except Exception as e:
                print(f"[ERROR] Failed to process overlay file: {e}")
                import traceback
                traceback.print_exc()

    return True

def xlnx_generate_zephyr_domain_dts_arm(tgt_node, sdt, options, machine):
    root_node = sdt.tree['/']
    root_sub_nodes = root_node.subnodes()

    if "amd,versal2" in root_node['compatible'].value:
        root_node["model"] = "AMD Versal Gen 2"
        root_node["compatible"] = "xlnx,versal2"

    for node in root_sub_nodes:
        if node.depth == 1:
            if "cpus" not in node.name and "amba" not in node.name and "memory" not in node.name and "chosen" not in node.name and "bus" not in node.name and "axi" not in node.name and "timer" not in node.name and "alias" not in node.name and "consumer" not in node.name:
                sdt.tree.delete(node)
        elif node.name == "cpu-map" or node.name == "idle-states":
            sdt.tree.delete(node)

        if node.propval("compatible") != ['']:
            if node.propval('xlnx,ip-name') != ['']:
                val = node.propval('xlnx,ip-name', list)[0]
                if "r52" in machine and (val == "psx_rcpu_gic" or val == "rcpu_gic"):
                    name  = node.name
                    sdt.tree.delete(node)
                    sdt.tree.delete(node.parent)
                    new_dst_node = node()
                    new_dst_node['#interrupt-cells'] = 4
                    new_dst_node.abs_path = "/axi/interrupt-controller@e2000000 "
                    new_dst_node.name = "interrupt-controller@e2000000 "
                    sdt.tree + new_dst_node
                    sdt.tree.sync()
                elif "a78" in machine and (val == "psx_acpu_gic" or val == "acpu_gic"):
                    name  = node.name
                    sdt.tree.delete(node)
                    new_dst_node = node()
                    new_dst_node['#interrupt-cells'] = 4
                    new_dst_node.abs_path = "/axi/interrupt-controller@e2000000 "
                    new_dst_node.name = "interrupt-controller@e2000000"
                    new_dst_node['compatible'].value = ["arm,gic-v3", "arm,gic"]
                    sdt.tree + new_dst_node
                    sdt.tree.sync()

            compatible = node.propval('compatible', list)[0]
            if compatible == "arm,armv8-timer":
                node["interrupts"].value = [0x1, 0xd, 0x4, 0xa4, 0x1, 0xe, 0x4, 0xa4, 0x1, 0xb, 0x4, 0xa4, 0x1, 0xa, 0x4, 0xa4]

            elif node.propval('interrupts') != ['']:
                intr_list = node["interrupts"].value
                intr_list.append("0xa0")            

            if compatible == "cpus,cluster":
                node.name = "cpus" 


    xlnx_remove_unsupported_nodes(tgt_node, sdt)

    for node in root_sub_nodes:
        if node.propval("compatible") != ['']:
            if node.propval("compatible") == "indirect-bus":
                sdt.tree.delete(node)

    return True

def xlnx_remove_unsupported_nodes(tgt_node, sdt):
    root_node = sdt.tree['/']
    root_sub_nodes = root_node.subnodes()
    valid_alias_proplist = []

    zephyr_supported_schema_file = os.path.join(os.path.dirname(os.path.realpath(__file__)), "zephyr_supported_comp.yaml")
    memnode_list = sdt.tree.nodes('/memory@.*')
    if utils.is_file(zephyr_supported_schema_file):
        schema = utils.load_yaml(zephyr_supported_schema_file)
        for node in root_sub_nodes:
            if node.parent:
                if node.propval("compatible") != ['']:
                    if any(version in node["compatible"].value for version in ("arm,cortex-r52", "arm,cortex-a78")):
                        if node.propval('xlnx,timestamp-clk-freq') != ['']:
                            node["clock-frequency"] = node['xlnx,timestamp-clk-freq'].value
                    if node.propval('xlnx,ip-name') != ['']:
                        val = node.propval('xlnx,ip-name', list)[0]
                        if val == "axi_intc":
                            num_intr = node.propval('xlnx,num-intr-inputs', list)[0]
                            num_intr += 12

                    is_supported_periph = [value for key,value in schema.items() if key in node["compatible"].value]
                    if "xlnx,xps-timer-1.00.a" in node["compatible"].value:
                        node["compatible"].value = ["amd,xps-timer-1.00.a"]
                    # UARTNS550
                    if "xlnx,axi-uart16550-2.0" in node["compatible"].value:
                        node["compatible"].value = ["ns16550"]
                        if node.propval('clock-frequency') == [''] and node.propval('xlnx,clock-freq') != ['']:
                            node["clock-frequency"] = LopperProp("clock-frequency")
                            node["clock-frequency"].value = node["xlnx,clock-freq"].value
                        if node.propval('reg-shift') != ['2']:
                            node["reg-shift"] = LopperProp("reg-shift")
                            node["reg-shift"].value = 2
                    # MDM RISCV DEBUG UARTLITE
                    if "xlnx,mdm-riscv-1.0" in node["compatible"].value:
                        node["compatible"].value = ["xlnx,xps-uartlite-1.00a"]
                    # UARTPS
                    if any(version in node["compatible"].value for version in ("xlnx,zynqmp-uart", "xlnx,xuartps")):
                        node["compatible"].value = ["xlnx,xuartps"]
                        if node.propval('clock-frequency') == [''] and node.propval('xlnx,clock-freq') != ['']:
                            node["clock-frequency"] = LopperProp("clock-frequency")
                            node["clock-frequency"].value = node["xlnx,clock-freq"].value
                        if node.propval('current-speed') == [''] and node.propval('xlnx,baudrate') != ['']:
                            node["current-speed"] = LopperProp("current-speed")
                            node["current-speed"].value = node["xlnx,baudrate"].value
                    # UARTPSV
                    if any(version in node["compatible"].value for version in ("arm,pl011", "arm,sbsa-uart")):
                        node["compatible"].value = ["arm,sbsa-uart"]
                        if node.propval('interrupt-names') == ['']:
                            node["interrupt-names"] = LopperProp("interrupt-names")
                            node["interrupt-names"].value = node.label
                            node.add(node["interrupt-names"])
                    # AXI-IIC
                    if "xlnx,axi-iic-2.1" in node["compatible"].value:
                        node["compatible"].value = ["xlnx,xps-iic-2.1"]
                    if any(version in node["compatible"].value for version in ("xlnx,xps-iic-2.00.a", "xlnx,xps-iic-2.1")):
                        if node.propval('#address-cells') != ['1']:
                            node["#address-cells"] = LopperProp("#address-cells")
                            node["#address-cells"].value = 1
                        if node.propval('#size-cells') != ['0']:
                            node["#size-cells"] = LopperProp("#size-cells")
                            node["#size-cells"].value = 0
                    # Mailbox
                    if any(version in node["compatible"].value for version in ("vnd,mbox-consumer", "xlnx,mbox-versal-ipi-mailbox", "xlnx,mbox-versal-ipi-dest-mailbox")):
                        continue
                    if "xlnx,versal-ipi-mailbox" in node["compatible"].value:
                        node["compatible"].value = ["xlnx,mbox-versal-ipi-mailbox"]
                    elif "xlnx,versal-ipi-dest-mailbox" in node["compatible"].value:
                        node["compatible"].value = ["xlnx,mbox-versal-ipi-dest-mailbox"]
                        node.name = f"child@{hex(node.propval('reg')[1])[2:]}"
                    # PS-IIC
                    if "cdns,i2c-r1p14" in node["compatible"].value:
                        node["compatible"].value = ["cdns,i2c"]
                        if node.propval('clock-frequency') == ['']:
                            node["clock-frequency"] = LopperProp("clock-frequency")
                            node["clock-frequency"].value = 100000
                            node.add(node["clock-frequency"])
                        if node.propval('fifo-depth') == ['']:
                            node["fifo-depth"] = LopperProp("fifo-depth")
                            node["fifo-depth"].value = 16
                            node.add(node["fifo-depth"])
                        if node.propval('#address-cells') != [1]:
                            node["#address-cells"] = LopperProp("#address-cells")
                            node["#address-cells"].value = 1
                            node.add(node["#address-cells"])
                        if node.propval('#size-cells') != [0]:
                            node["#size-cells"] = LopperProp("#size-cells")
                            node["#size-cells"].value = 0
                            node.add(node["#size-cells"])
                    #AXI-GPIO
                    if "xlnx,xps-gpio-1.00.a" in node["compatible"].value:
                        node["compatible"].value = ["xlnx,xps-gpio-1.00.a"]
                        if node.propval('xlnx,is-dual') != ['']:
                            val = node.propval('xlnx,is-dual')[0]
                            if val == 1:
                                new_node = LopperNode()
                                new_node['compatible'] = "xlnx,xps-gpio-1.00.a-gpio2"
                                new_node.name = "gpio2"
                                new_prop = LopperProp( "gpio-controller" )
                                new_node + new_prop
                                new_node['#gpio-cells'] = 2
                                new_node.label_set(node.label)
                                node.add(new_node)
                    # SDHC
                    if any(version in node["compatible"].value for version in ("xlnx,versal-8.9a", "xlnx,versal-net-emmc")):
                        version = lambda x: x in node["compatible"].value
                        new_node = LopperNode()
                        if version("xlnx,versal-net-emmc"):
                            new_node.name = "mmc"
                            new_node['compatible'] = "zephyr,mmc-disk"
                            new_node['bus-width'] = node["xlnx,bus-width"].value
                        else:
                            new_node.name = "sdmmc"
                            new_node['compatible'] = "zephyr,sdmmc-disk"
                            node['power-delay-ms'] = 10
                        node.add(new_node)
                        node["compatible"] = "xlnx,versal-8.9a"
                    # GPIOPS
                    if any(version in node["compatible"].value for version in ("xlnx,pmc-gpio-1.0", "xlnx,versal-gpio-1.0")):
                        version = lambda x: x in node["compatible"].value
                        platform = sdt.tree['/']['family'].value
                        if version("xlnx,pmc-gpio-1.0"):
                            num_banks = [(0,26),(1,26),(3,32),(4,32)]
                            if platform != ['VersalNet']:
                                num_banks.extend([(2,26),(5,32)])
                        else:
                            num_banks = [(0,26),(3,32)]
                            if platform != ['VersalNet']:
                                num_banks.append((4,32))
                        for bank in num_banks:
                            new_node = LopperNode()
                            new_node["compatible"] = "xlnx,ps-gpio-bank"
                            new_node['reg'] = bank[0]
                            new_node['#gpio-cells'] = 2
                            new_prop = LopperProp( "gpio-controller" )
                            new_node + new_prop
                            new_node['ngpios'] = bank[1]
                            new_node.name = f"{node.label}_bank@{bank[0]}"
                            new_node.label_set(f"{node.label}_bank{bank[0]}")
                            node.add(new_node)
                        node['#address-cells'] = 1
                        node['#size-cells'] = 0
                        node['compatible'] = "xlnx,ps-gpio"
                    if is_supported_periph:
                        required_prop = is_supported_periph[0]["required"]
                        prop_list = list(node.__props__.keys())
                        valid_alias_proplist.append(node.name)
                        # Create fixed clock nodes
                        if 'clocks' in required_prop:
                            if any(clock_prop == (re.search(r'xlnx,.*-clk-freq-hz$', prop)) for prop in prop_list):
                                clk_freq = node[clock_prop.group()].value
                            else:
                                # If there is no clk-freq property use 0MHZ as default this prevent
                                # build failure if any of the ip does not have this property.
                                clk_freq = 0
                            new_ref_clk = True
                            # Check clock node with requested clk-freq is already available or not,
                            # if yes use the existing clk node else create new ref clock node.
                            for clk_node in sdt.tree.nodes(r'.*ref_clock$'):
                                if clk_freq == clk_node['clock-frequency'].value:
                                    if node.props('clocks') != []:
                                        node.delete('clocks')
                                    clock_prop = f"clocks = <&{clk_node.name}>"
                                    node + LopperProp(clock_prop)
                                    new_ref_clk = False
                            if new_ref_clk:
                                new_node = LopperNode()
                                new_node.abs_path = "/clocks"
                                new_node.name = node.label + "_ref_clock"
                                new_node['compatible'] = ["fixed-clock"]
                                new_node['#clock-cells'] = 0
                                new_node['clock-frequency'] = clk_freq
                                new_node.label_set(new_node.name)
                                sdt.tree.add(new_node)
                                if node.props('clocks') != []:
                                    node.delete('clocks')
                                clock_prop = f"clocks = <&{new_node.name}>"
                                node + LopperProp(clock_prop)
                        for prop in prop_list:
                            if prop not in required_prop:
                                node.delete(prop)
                    else:
                        if node.name not in ("axi", "soc") and node not in memnode_list:
                            sdt.tree.delete(node)

    alias_node = sdt.tree['/aliases']
    alias_prop_list = list(alias_node.__props__.keys())
    for prop in alias_prop_list:
        val = sdt.tree['/aliases'].propval(prop, list)[0]
        pl_node_ref = None
        if "amba_pl" in val:
            pl_node_ref = True
        val = val.rsplit('/', 1)[-1]
        if val not in valid_alias_proplist or pl_node_ref:
            sdt.tree['/aliases'].delete(prop)

    max_mem_size = 0
    sram_node = 0
    for node in root_sub_nodes:
        if node.propval('device_type') != ['']:
            val = node.propval('device_type', list)[0]
            if val == "memory":
                mem_size = node.propval('reg', list)[3]
                if mem_size > max_mem_size:
                    sram_node = node.abs_path
                    max_mem_size = mem_size

        if node.name == "chosen":
                var = sdt.tree[node].propval('stdout-path', list)[0]
                dev_node = var.split(':')[0]

                if sdt.tree['/chosen'].propval('zephyr,console') == ['']:
                   sdt.tree[node]['zephyr,console'] = dev_node
                   sdt.tree[node]['zephyr,shell-uart'] = dev_node

    if sdt.tree['/chosen'].propval('zephyr,sram') == ['']:
        sdt.tree['/chosen'] + LopperProp(name="zephyr,sram", value = sram_node)

    return True

def xlnx_generate_zephyr_domain_dts(tgt_node, sdt, options):
    root_node = sdt.tree[tgt_node]
    root_sub_nodes = root_node.subnodes()
    symbol_node = sdt.tree['/__symbols__']
    valid_alias_proplist = []

    """
    DRC Checks
    1) Interrupt controller is present or not
	If not:
		error: Zephyr required at least one interrupt controller IP to be present in the design
	if present and fast interrupt is enabled
		error: Fast interrupt is not supported please disable fast interrupt configuration from the design
    2) Check if timer is present or not
	if not:
		error: Zephyr expects at least one timer IP to be present for tick funcationailty
	if present and interrupt not connected
		error: For timer IP interrupt is not connected please connect the same.
    """
    is_axi_intc_present = None
    is_axi_timer_present = None
    for node in root_sub_nodes:
        if node.propval('xlnx,ip-name') != ['']:
            val = node.propval('xlnx,ip-name', list)[0]
            if val == "axi_intc":
                is_axi_intc_present = node
            elif val == "axi_timer":
                is_axi_timer_present = node

    err_no_intc = "\nERROR: Zephyr OS requires the presence of at least one interrupt controller. Please ensure that the axi_intc is included in the design, with fast interrupts disabled.\r"
    err_no_timer = "\nERROR: Zephyr OS requires at least one timer controller with interrupts enabled for its scheduler. Please include the axi_timer in your hardware design and ensure its interrupts are properly connected.\r"
    warn_intc_has_fast = "\nWARNING: Zephyr does not support fast interrupts; they will be handled as standard interrupts. Therefore, enabling FAST interrupts in the AXI INTC core will not improve interrupt latency. Additionally, fast interrupts are not supported in QEMU.\r"
    err_timer_nointr = "\nERROR: Zephyr OS requires at least one timer with interrupts enabled to manage its scheduler effectively. Please ensure that the interrupt pins for the timer are correctly connected in your hardware design and rebuild with the updated configuration.\r"
    if not is_axi_intc_present and not is_axi_timer_present:
        print(err_no_intc)
        print(err_no_timer)
        sys.exit(1)
    elif not is_axi_intc_present:
        print(err_no_intc)
        sys.exit(1)
    elif is_axi_intc_present:
        if is_axi_intc_present.propval('xlnx,has-fast') != ['']:
            val = is_axi_intc_present.propval('xlnx,has-fast', list)[0]
            if val != 0 or val != 0x0:
                print(warn_intc_has_fast)
    if not is_axi_timer_present:
        print(err_no_timer)
        sys.exit(1)
    elif is_axi_timer_present and is_axi_timer_present.propval('interrupts') == ['']:
        print(err_timer_nointr)
        sys.exit(1)

    license_content = '''#
# Copyright (c) 2024 Advanced Micro Devices, Inc.
#
# SPDX-License-Identifier: Apache-2.0
#

'''
    fix_part= '''
  imply ARCH_CPU_IDLE_CUSTOM
  select CLOCK_CONTROL
  select CLOCK_CONTROL_FIXED_RATE_CLOCK
  select CONSOLE
  select SERIAL
  select UART_CONSOLE if (UART_NS16550 || UART_XLNX_UARTLITE)
  select UART_INTERRUPT_DRIVEN if (UART_NS16550 || UART_XLNX_UARTLITE)
  imply UART_NS16550 if DT_HAS_NS16550_ENABLED
  imply UART_XLNX_UARTLITE if DT_HAS_UARTLITE_ENABLED
  imply GPIO if DT_HAS_XLNX_XPS_GPIO_1_00_A_ENABLED
  imply GPIO_XLNX_AXI if DT_HAS_XLNX_XPS_GPIO_1_00_A_ENABLED
  imply AMD_TMRCTR if DT_HAS_AMD_XPS_TIMER_1_00_A_ENABLED
  imply XLNX_INTC if DT_HAS_XLNX_XPS_INTC_1_00_A_ENABLED
  select XLNX_INTC_USE_IPR if XLNX_INTC
  select XLNX_INTC_USE_SIE if XLNX_INTC
  select XLNX_INTC_USE_CIE if XLNX_INTC
  select XLNX_INTC_USE_IVR if XLNX_INTC
    '''

    max_mem_size = 0
    num_intr = None
    for node in root_sub_nodes:
        if node.propval('xlnx,ip-name') != ['']:
            val = node.propval('xlnx,ip-name', list)[0]
            if val == "microblaze_riscv":
                compatlist = ['amd,mbv32', 'riscv']
                node['compatible'] = compatlist
                new_node = LopperNode()
                new_node.name = "interrupt-controller"
                new_node['compatible'] = "riscv,cpu-intc"
                new_prop = LopperProp( "interrupt-controller" )
                new_node + new_prop
                new_node['#interrupt-cells'] = 1
                new_node.label_set("cpu_intc")
                node.add(new_node)
                phandle_val = new_node.phandle_or_create()
                new_node + LopperProp(name="phandle", value=phandle_val)

    zephyr_supported_schema_file = os.path.join(os.path.dirname(os.path.realpath(__file__)), "zephyr_supported_comp.yaml")
    if utils.is_file(zephyr_supported_schema_file):
        schema = utils.load_yaml(zephyr_supported_schema_file)
        for node in root_sub_nodes:
            if node.parent:
                if "amba_pl" in node.parent.name:
                    if node.propval("compatible") != ['']:
                        if node.propval('xlnx,ip-name') != ['']:
                            val = node.propval('xlnx,ip-name', list)[0]
                            if val == "axi_intc":
                                num_intr = node.propval('xlnx,num-intr-inputs', list)[0]
                                num_intr += 12
                        is_supported_periph = [value for key,value in schema.items() if key in node["compatible"].value]
                        if "xlnx,xps-timer-1.00.a" in node["compatible"].value:
                            node["compatible"].value = ["amd,xps-timer-1.00.a"]
                        # UARTNS550
                        if "xlnx,axi-uart16550-2.0" in node["compatible"].value:
                            node["compatible"].value = ["ns16550"]
                            if node.propval('clock-frequency') == [''] and node.propval('xlnx,clock-freq') != ['']:
                                node["clock-frequency"] = LopperProp("clock-frequency")
                                node["clock-frequency"].value = node["xlnx,clock-freq"].value
                            if node.propval('reg-shift') != ['2']:
                               node["reg-shift"] = LopperProp("reg-shift")
                               node["reg-shift"].value = 2
                        # MDM RISCV DEBUG UARTLITE
                        if "xlnx,mdm-riscv-1.0" in node["compatible"].value:
                            node["compatible"].value = ["xlnx,xps-uartlite-1.00a"]
                        # UARTPS
                        if any(version in node["compatible"].value for version in ("xlnx,zynqmp-uart", "xlnx,xuartps")):
                            node["compatible"].value = ["xlnx,xuartps"]
                            if node.propval('clock-frequency') == [''] and node.propval('xlnx,clock-freq') != ['']:
                                node["clock-frequency"] = LopperProp("clock-frequency")
                                node["clock-frequency"].value = node["xlnx,clock-freq"].value
                            if node.propval('current-speed') == [''] and node.propval('xlnx,baudrate') != ['']:
                                node["current-speed"] = LopperProp("current-speed")
                                node["current-speed"].value = node["xlnx,baudrate"].value
                        # UARTPSV
                        if any(version in node["compatible"].value for version in ("arm,pl011", "arm,sbsa-uart")):
                            node["compatible"].value = ["arm,sbsa-uart"]
                        # AXI-IIC
                        if "xlnx,axi-iic-2.1" in node["compatible"].value:
                            node["compatible"].value = ["xlnx,xps-iic-2.1"]
                        if any(version in node["compatible"].value for version in ("xlnx,xps-iic-2.00.a", "xlnx,xps-iic-2.1")):
                            if node.propval('#address-cells') != ['1']:
                                node["#address-cells"] = LopperProp("#address-cells")
                                node["#address-cells"].value = 1
                            if node.propval('#size-cells') != ['0']:
                                node["#size-cells"] = LopperProp("#size-cells")
                                node["#size-cells"].value = 0
                        #AXI-GPIO
                        if "xlnx,xps-gpio-1.00.a" in node["compatible"].value:
                            node["compatible"].value = ["xlnx,xps-gpio-1.00.a"]
                            if node.propval('xlnx,is-dual') != ['']:
                                val = node.propval('xlnx,is-dual')[0]
                                if val == 1:
                                    new_node = LopperNode()
                                    new_node['compatible'] = "xlnx,xps-gpio-1.00.a-gpio2"
                                    new_node.name = "gpio2"
                                    new_prop = LopperProp( "gpio-controller" )
                                    new_node + new_prop
                                    new_node['#gpio-cells'] = 2
                                    new_node.label_set(node.label)
                                    node.add(new_node)
                        #AXI-SPI
                        if any(version in node["compatible"].value for version in ("xlnx,xps-spi-2.00.a", "xlnx,axi-quad-spi-3.2")):
                            if node.propval('#address-cells') != ['1']:
                                node['#address-cells'] = 1
                            if node.propval('#size-cells') != ['0']:
                                node['#size-cells'] = 0
                            node["compatible"] = "xlnx,xps-spi-2.00.a"
                        if is_supported_periph:
                            required_prop = is_supported_periph[0]["required"]
                            prop_list = list(node.__props__.keys())
                            valid_alias_proplist.append(node.name)
                            for prop in prop_list:
                                if prop not in required_prop:
                                    node.delete(prop)
                        else:
                            sdt.tree.delete(node)

    alias_node = sdt.tree['/aliases']
    alias_prop_list = list(alias_node.__props__.keys())
    for prop in alias_prop_list:
        val = sdt.tree['/aliases'].propval(prop, list)[0]
        val = val.rsplit('/', 1)[-1]
        if val not in valid_alias_proplist:
            sdt.tree['/aliases'].delete(prop)

    # Delete reg property from clocks node
    clock_node = sdt.tree['/clocks']
    clock_subnodes = clock_node.subnodes()
    for node in clock_subnodes:
        if node.propval('reg') != ['']:
            sdt.tree[node].delete('reg')
        if node.propval('clock-output-names') != ['']:
            sdt.tree[node].delete('clock-output-names')
        node.name = node.name.split('@')[0]

    match_cpunode = get_cpu_node(sdt, options)
    match_cpunode.parent.delete("address-map")
    for node in root_sub_nodes:

        soc_kconfig_file = os.path.join(sdt.outdir, "Kconfig")
        soc_kconfig = open(soc_kconfig_file, 'a')

        soc_defconfig_file = os.path.join(sdt.outdir, "Kconfig.defconfig")
        defconfig_kconfig = open(soc_defconfig_file, 'a')

        if node.name == "chosen":
                var = sdt.tree[node].propval('stdout-path', list)[0]
                dev_node = var.split(':')[0]

                if sdt.tree['/chosen'].propval('zephyr,console') == ['']:
                    sdt.tree[node]['zephyr,console'] = dev_node
                    sdt.tree[node]['zephyr,shell-uart'] = dev_node
 
        if node.name == "amba_pl":
                sdt.tree.delete(node)
                new_dst_node = node()
                new_dst_node.abs_path = "/soc"
                new_dst_node.name = "soc"
                sdt.tree + new_dst_node
                sdt.tree.sync()

                symbol_node = sdt.tree['/__symbols__']
                prop_list = list(symbol_node.__props__.keys())
                for prop in prop_list:
                    val = sdt.tree['/__symbols__'].propval(prop, list)[0]
                    val = val.replace("amba_pl", "soc")
                    sdt.tree['/__symbols__'].propval(prop, list)[0] = val

                symbol_node = sdt.tree['/aliases']
                prop_list = list(symbol_node.__props__.keys())
                for prop in prop_list:
                    val = sdt.tree['/aliases'].propval(prop, list)[0]
                    val = val.replace("amba_pl", "soc")
                    sdt.tree['/aliases'].propval(prop, list)[0] = val

        if node.propval('device_type') != ['']:
            val = node.propval('device_type', list)[0]
            if val == "memory":
                mem_size = node.propval('reg', list)[1]
                if mem_size > max_mem_size:
                    sram_node = node.abs_path
                    max_mem_size = mem_size

        if node.propval('xlnx,ip-name') != ['']:
            val = node.propval('xlnx,ip-name', list)[0]
            if val == "microblaze_riscv":
                cflags_file = os.path.join(sdt.outdir, "cflags.yaml")
                try:
                    stream = open(cflags_file, 'r')
                except FileNotFoundError:
                    print("ERROR:cflags.yaml not found. Lops file lop-microblaze-riscv.dts need to be run for generating cflags.yaml.")
                else:
                    data = yaml.load(stream,  Loader=yaml.Loader)
                    var = data.get('cflags')
                    match = re.search(r"(?<=\=).+?(?=\ )",var)
                    sdt.tree[node]['riscv,isa'] = match.group()
                    isa = match.group()

                    ''' Parse isa string and generate Kconfig.soc
                        and Kconfig.defconfig based on that 
                    ''' 

                    soc_kconfig.write(str(license_content))
                    soc_kconfig.write("config SOC_MBV32\n")

                    soc_kconfig.write("  select RISCV\n")
                    soc_kconfig.write("  select ATOMIC_OPERATIONS_C\n")
                    soc_kconfig.write("  select INCLUDE_RESET_VECTOR\n")

                    data_dict={'_zicsr':"  select RISCV_ISA_EXT_ZICSR\n",
                               '_zifencei':"  select RISCV_ISA_EXT_ZIFENCEI\n",
                               '_zba':"  select RISCV_ISA_EXT_ZBA\n",
                               '_zbb':"  select RISCV_ISA_EXT_ZBB\n",
                               '_zbc':"  select RISCV_ISA_EXT_ZBC\n",
                               '_zbs':"  select RISCV_ISA_EXT_ZBS\n",
                    }
                    for key, value in data_dict.items():
                        if isa.find(key) != -1:
                            soc_kconfig.write(value)

                    isa = isa.split('_')[0]

                    data_dict={'rv32i':"  select RISCV_ISA_RV32I\n",
                        'm':"  select RISCV_ISA_EXT_M\n",
                        'a':"  select RISCV_ISA_EXT_A\n",
                        'c':"  select RISCV_ISA_EXT_C\n",
                        'f':"  select RISCV_ISA_EXT_F\n",
                        'd':"  select RISCV_ISA_EXT_D\n",
                    }
                    for key, value in data_dict.items():
                        if isa.find(key) != -1:
                            soc_kconfig.write(value)

                    soc_kconfig.write(str(fix_part))

                    soc_kconfig.close()

                    soc_defconfig_file = os.path.join(sdt.outdir, "Kconfig.defconfig")
                    defconfig_kconfig = open(soc_defconfig_file, 'a')
                    
                    defconfig_kconfig.write(str(license_content))
                    defconfig_kconfig.write("\nif SOC_MBV32\n")
                    defconfig_kconfig.write("\nconfig MBV_CSR_DATA_WIDTH\n")
                    defconfig_kconfig.write("  int \"Select Control/Status register width\"\n")
                    defconfig_kconfig.write("  default 32\n")

                    val = node.propval('clock-frequency', list)[0]
                    defconfig_kconfig.write("\nconfig SYS_CLOCK_HW_CYCLES_PER_SEC\n")
                    defconfig_kconfig.write("  default $(dt_node_int_prop_int,/cpus/cpu@0,clock-frequency)")

                    val = node.propval('xlnx,pmp-entries', list)[0]
                    if val % 8 == 0 and val != 0:
                        soc_kconfig = open(soc_kconfig_file, 'a')
                        soc_kconfig.write("  select RISCV_PMP\n")
                        soc_kconfig.close()

                        defconfig_kconfig.write("\nconfig PMP_SLOTS\n")
                        defconfig_kconfig.write("  default %s\n" % str(val))

                        val = node.propval('xlnx,pmp-granularity', list)[0]
                        defconfig_kconfig.write("\nconfig PMP_GRANULARITY\n")
                        val = pow(val + 2, 2)
                        defconfig_kconfig.write("  default %s\n" % str(val))

                    defconfig_kconfig.close()


    defconfig_kconfig = open(soc_defconfig_file, 'a')
    defconfig_kconfig.write("\nconfig NUM_IRQS\n")
    if num_intr:
        defconfig_kconfig.write("  default %s\n" % str(num_intr))
    defconfig_kconfig.write("\nendif\n")
    defconfig_kconfig.close()

    if sdt.tree['/chosen'].propval('zephyr,sram') == ['']:
        sdt.tree['/chosen'] + LopperProp(name="zephyr,sram", value = sram_node)

    # Update memory nodes
    # For DDR keep only device_type and remove compatible
    # For LMB ram change the compatible to mmio-sram
    memnode_list = sdt.tree.nodes('/memory@.*')
    for mem_node in memnode_list:
        if mem_node.propval('xlnx,ip-name') != ['']:
            if 'ddr' in mem_node['xlnx,ip-name'].value[0]:
                sdt.tree[mem_node].delete('compatible')
            if 'lmb_bram' in mem_node['xlnx,ip-name'].value[0]:
                sdt.tree[mem_node]['compatible'].value = ['mmio-sram']
                #mem_node.delete('device_type')
            mem_node.delete('memory_type')
            mem_node.delete('xlnx,ip-name')

    return True

