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

sys.path.append(os.path.dirname(__file__))

from baremetalconfig_xlnx import compat_list, get_cpu_node, get_mapped_nodes, get_label
from common_utils import to_cmakelist
import common_utils as utils
from domain_access import update_mem_node

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
    try:
        if options['args'][1] == "zephyr_dt":
            zephyr_dt = 1
        elif options['args'][1] == "linux_dt":
            linux_dt = 1

    except IndexError:
        pass

    keep_tcms = None
    try:
        keep_tcms = options['args'][2]
    except IndexError:
        pass

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

    node_list = []
    for node in root_sub_nodes:
        if linux_dt:
            if node.name == "memory@fffc0000" or node.name == "memory@bbf00000":
                sdt.tree.delete(node)
            if keep_tcms == None and 'tcm' in node.name:
                sdt.tree.delete(node)
            if node.propval('memory_type', list) == ['linear_flash']:
                sdt.tree.delete(node)
            for entry in node.propval('compatible', list):
                pl_memory_compatible_list = ["xlnx,ddr4","xlnx,mig-7series","xlnx,lmb-bram","xlnx,axi-bram"]
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
                            'psu_pmu_global_0', 'psu_qspi_linear_0', 'psu_rpu', 'psu_rsa', 'psu_siou', 'psu_ipi',
                            'psx_PSM_PPU', 'psx_ram_instr_cntlr', 'psx_rpu', 'psx_fpd_gpv', 'ddr4', 'ps7_ram', 'ps7_afi',
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
                            'psv_pmc_slave_boot_stream', 'psv_pmc_trng', 'psv_psm_global_reg', 'psv_rpu', 'psv_scntr', 'v_tc',
                            'mipi_dphy', 'mipi_csi2_rx_ctrl', 'mipi_dsi_tx_ctrl', 'v_hscaler', 'v_vscaler', 'v_csc', 'v_hdmi_tx']

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
            delete_child_nodes = False
            if 'xlnx,usp-rf-data-converter-2.6' in node.propval('compatible'):
                delete_child_nodes = True
            if is_prune_node:
                delete_unused_props( node, driver_proplist, delete_child_nodes)

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
        xlnx_generate_zephyr_domain_dts(tgt_node, sdt)
    return True

def xlnx_generate_zephyr_domain_dts(tgt_node, sdt):
    root_node = sdt.tree[tgt_node]
    root_sub_nodes = root_node.subnodes()

    max_mem_size = 0

    for node in root_sub_nodes:

        soc_kconfig_file = os.path.join(sdt.outdir, "Kconfig.soc")
        soc_kconfig = open(soc_kconfig_file, 'a')

        soc_defconfig_file = os.path.join(sdt.outdir, "Kconfig.defconfig")
        defconfig_kconfig = open(soc_defconfig_file, 'a')

        if node.name == "chosen":
                var = sdt.tree[node].propval('stdout-path', list)[0]
                dev_node = var.split(':')[0]
                
                sdt.tree[node]['zephyr,console'] = dev_node
 
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

                    soc_kconfig.write("config SOC_MBV32\n")
                    soc_kconfig.write("  bool \"MBV32 system implementation\" \n")

                    soc_kconfig.write("  select RISCV\n")
                    soc_kconfig.write("  select ATOMIC_OPERATIONS_C\n")
                    soc_kconfig.write("  select INCLUDE_RESET_VECTOR\n")

                    if isa.find('_zicsr') != -1:
                        soc_kconfig.write("  select RISCV_ISA_EXT_ZICSR\n")

                    if isa.find('_zifencei') != -1:
                        soc_kconfig.write("  select RISCV_ISA_EXT_ZIFENCEI\n")
                    
                    if isa.find('_zba') != -1:
                        soc_kconfig.write("  select RISCV_ISA_EXT_ZBA\n")

                    if isa.find('_zbb') != -1:
                        soc_kconfig.write("  select RISCV_ISA_EXT_ZBB\n")

                    if isa.find('_zbc') != -1:
                        soc_kconfig.write("  select RISCV_ISA_EXT_ZBC\n")

                    if isa.find('_zbs') != -1:
                        soc_kconfig.write("  select RISCV_ISA_EXT_ZBS\n")

                    isa = isa.split('_')[0]

                    if isa.find('rv32i') != -1:
                        soc_kconfig.write("  select RISCV_ISA_RV32I\n")

                    if isa.find('m') != -1:
                        soc_kconfig.write("  select RISCV_ISA_EXT_M\n")

                    if isa.find('a') != -1:
                        soc_kconfig.write("  select RISCV_ISA_EXT_A\n")

                    if isa.find('c') != -1:
                        soc_kconfig.write("  select RISCV_ISA_EXT_C\n")

                    if isa.find('f') != -1:
                        soc_kconfig.write("  select RISCV_ISA_EXT_F\n")

                    if isa.find('d') != -1:
                        soc_kconfig.write("  select RISCV_ISA_EXT_D\n")

                    soc_kconfig.close()

                    soc_defconfig_file = os.path.join(sdt.outdir, "Kconfig.defconfig")
                    defconfig_kconfig = open(soc_defconfig_file, 'a')
                    
                    defconfig_kconfig.write("if SOC_MBV32\n")
                    defconfig_kconfig.write("config SOC\n")
                    defconfig_kconfig.write("  default \"mbv32\"\n")

                    val = node.propval('clock-frequency', list)[0]
                    defconfig_kconfig.write("config SYS_CLOCK_HW_CYCLES_PER_SEC\n")
                    defconfig_kconfig.write("  default %s\n" % str(val))

                    defconfig_kconfig.write("config RISCV_RESERVED_IRQ_ISR_TABLES_OFFSET\n")
                    defconfig_kconfig.write("  default 12\n")

                    defconfig_kconfig.close()

        if node.propval('xlnx,ip-name') != ['']:
            val = node.propval('xlnx,ip-name', list)[0]
            if val == "axi_intc":
                num_intr = node.propval('xlnx,num-intr-inputs', list)[0]
                num_intr += 12

    defconfig_kconfig = open(soc_defconfig_file, 'a')
    defconfig_kconfig.write("config NUM_IRQS\n")
    defconfig_kconfig.write("  default %s\n" % str(num_intr))
    defconfig_kconfig.write("endif\n")
    defconfig_kconfig.close()

    sdt.tree['/chosen']['zephyr,sram'] = sram_node
    return True

