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

from baremetalconfig_xlnx import compat_list, get_cpu_node, get_mapped_nodes, get_label, scan_reg_size
from common_utils import to_cmakelist
import common_utils as utils
from domain_access import update_mem_node
from openamp_xlnx import xlnx_openamp_keep_node

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

    Zephyr device-tree generation uses assist zephyr_domain_dts (run after domain DTS).
    """
    machine = options['args'][0]
    symbol_node = sdt.tree['/__symbols__']
    # Get the cpu node for a given Processor
    match_cpunode = get_cpu_node(sdt, options)
    address_map = match_cpunode.parent["address-map"].value
    na = match_cpunode.parent["#ranges-address-cells"].value[0]
    ns = match_cpunode.parent["#ranges-size-cells"].value[0]

    linux_dt = None
    zynqmp_fsbl = None
    try:
        if options['args'][1] == "linux_dt":
            linux_dt = 1
        elif options['args'][1] == "zynqmp_fsbl":
            zynqmp_fsbl = 1

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
                        node.delete('device_type')
            if node.name == "memory@fffc0000" or node.name == "memory@bbf00000":
                sdt.tree.delete(node)
            if node.propval('memory_type', list) == ['linear_flash']:
                sdt.tree.delete(node)
            for entry in node.propval('compatible', list):
                pl_memory_compatible_list = ["xlnx,axi-bram"] if is_microblaze \
                                           else ["xlnx,ddr4","xlnx,mig-7series","xlnx,lmb-bram","xlnx,axi-bram"]
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
                            'psv_pmc_slave_boot_stream', 'psv_pmc_trng', 'psv_psm_global_reg', 'psv_rpu', 'psv_scntr',
                            'psu_r5_tcm_ram', 'psu_r5_0_btcm', 'psu_r5_0_btcm_global', 'psu_r5_0_atcm_global', 'psu_r5_0_atcm', 'psu_r5_0_atcm_lockstep',
                            'psu_r5_tcm_ram', 'psu_r5_0_btcm', 'psu_r5_0_btcm_global', 'psu_r5_0_atcm_global', 'psu_r5_0_atcm',
                            'xlnx,tcm', 'r52_atcm_global', 'r52_btcm_global', 'r52_ctcm_global', 'psx_tcm_global',
                            'psv_r5_tcm', 'psv_tcm_global', 'psv_r5_0_atcm_lockstep', 'psv_r5_0_btcm_lockstep']

    versal_gen2_linux_ignore_ip_list = ['mmi_udh_pll', 'mmi_common', 'mmi_pipe_gem_slcr',
                            'mmi_udh_pll', 'mmi_udh_slcr', 'mmi_usb2phy',
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
        ipi_schema = None
        for yaml_prune in yaml_prune_list:
            yaml_file = os.path.join(os.path.dirname(os.path.realpath(__file__)), yaml_prune)
            schema = utils.load_yaml(yaml_file)
            driver_compatlist = driver_compatlist + compat_list(schema)
            driver_proplist = driver_proplist + schema.get('required',[])
            if "xlnx,zynqmp-ipi-mailbox.yaml" in yaml_prune:
                ipi_schema = schema

    mapped_children_nodes = []
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
                elif linux_dt and "xlnx,versal-ddrmc" in node.propval('compatible', list):
                    # ddr controller is not mapped to APU and there is a special handling in SDT to make its status okay.
                    continue
                elif linux_dt and (node.parent is not None and ((node.parent in mapped_nodelist) or (node.parent in mapped_children_nodes))):
                    # Add the unmapped nodes which are children of mapped nodes to the final device-tree. This is required to keep the hierarchy
                    # of the device-tree intact and also to keep the nodes which are required for the mapped nodes to function properly.
                    mapped_children_nodes.append(node)
                    continue
                elif xlnx_openamp_keep_node(linux_dt, False, node, sdt.tree):
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

                # Prune IPI child node properties according to the YAML schema
                if linux_dt and ipi_schema:
                    ipi_parent_compat = ipi_schema.get("properties", {}).get("compatible", {}).get("enum", [])
                    pattern_props = ipi_schema.get("patternProperties", {})
                    # Only process if this node is an IPI parent node
                    if any(c in ipi_parent_compat for c in node.propval('compatible', list)):
                        for pattern, child_schema in pattern_props.items():
                            # Get the required property list for IPI child nodes from YAML
                            ipi_child_required = child_schema.get("required", [])
                            # Get the list of valid child compatibles from YAML (if present)
                            child_compat_enum = child_schema.get("properties", {}).get("compatible", {}).get("enum", [])
                            for child in node.subnodes():
                                child_compat = child.propval('compatible', list)
                                # If YAML lists child compatibles, match them; else, prune all children
                                if not child_compat_enum or any(c in child_compat_enum for c in child_compat):
                                    delete_unused_props(child, ipi_child_required, False)
                            break  # Only process the first pattern (as in the YAML)

                if linux_dt and "qdma" in node.label:
                    if mode == ['PCI_Express_Endpoint_device']:
                        if "okay" in node.propval('status', list)[0]:
                            node.propval('status', list)[0] = "disabled"

    # For MicroBlaze/MicroBlaze RISC-V: remap PS peripheral interrupts to AXI INTC.
    # PS peripherals like sysmon have GIC-format interrupts (3-cell) but on MicroBlaze
    # they are routed via ps_pl_irq through xlconcat to the AXI INTC. The SDT does not
    # capture this cross-domain routing, so we detect and remap here.
    cpu_ip_name = match_cpunode.propval('xlnx,ip-name', list)[0]
    if cpu_ip_name in ('microblaze', 'microblaze_riscv'):
        axi_intc_node = None
        for node in sdt.tree['/'].subnodes():
            if node.propval('xlnx,ip-name', list) == ['axi_intc']:
                axi_intc_node = node
                break

        if axi_intc_node is not None:
            intc_phandle = axi_intc_node.phandle
            num_intr = axi_intc_node.propval('xlnx,num-intr-inputs', list)[0]

            for node in sdt.tree['/'].subnodes():
                if node.propval('interrupts') == [''] or node.propval('compatible') == ['']:
                    continue

                intr_val = node.propval('interrupts')
                if len(intr_val) < 3:
                    continue

                # Use property_find() to walk parent chain for interrupt-parent
                prop, _ = node.property_find('interrupt-parent')
                if not prop:
                    continue

                # Use deref() to resolve the phandle to a node
                intr_parent = sdt.tree.deref(prop.value[0])
                if not intr_parent:
                    continue

                inc = intr_parent.propval('#interrupt-cells', list)[0]
                if inc != 3:
                    continue

                # Remap known PS peripherals routed via ps_pl_irq to AXI INTC
                compat = node.propval('compatible', list)
                if 'xlnx,versal-sysmon' in compat:
                    # Determine the AXI INTC input by finding the next input
                    # after all PL peripherals already connected to the INTC
                    used_inputs = set()
                    for n in sdt.tree['/'].subnodes():
                        if n.propval('interrupts') == ['']:
                            continue
                        n_prop, _ = n.property_find('interrupt-parent')
                        if not n_prop:
                            continue
                        if n_prop.value[0] == intc_phandle and n != node:
                            n_intr = n.propval('interrupts')
                            if len(n_intr) >= 2:
                                used_inputs.add(n_intr[0])

                    sysmon_intr_id = None
                    if used_inputs:
                        next_input = max(used_inputs) + 1
                        if next_input < num_intr:
                            sysmon_intr_id = next_input
                    else:
                        sysmon_intr_id = 0

                    if sysmon_intr_id is not None:
                        from lopper.tree import LopperProp
                        node['interrupt-parent'] = LopperProp(
                            name='interrupt-parent', value=[intc_phandle])
                        node.add(node['interrupt-parent'])
                        node['interrupts'].value = [sysmon_intr_id, 0x2]
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

        # Move timebase-frequency from cpu@0 node to cpus node for riscv designs
        if match_cpunode.propval('xlnx,ip-name', list)[0] == 'microblaze_riscv':
            try:
                cpu_prop = match_cpunode.propval('timebase-frequency')
                if cpu_prop != ['']:
                    sdt.tree[match_cpunode.parent]['timebase-frequency'] = cpu_prop
                    sdt.tree[match_cpunode].delete('timebase-frequency')
            except KeyError:
                pass

            # Add cpu0_intc (riscv,cpu-intc) as a child of the CPU node,
            # and add the riscv,timer node at root wired to cpu0_intc at IRQ 5.
            # Also wire axi_intc to cpu0_intc at IRQ 9 (IRQ_S_EXT).
            intc_node = LopperNode()
            intc_node.name = "interrupt-controller"
            intc_node.label = "cpu0_intc"
            intc_node['compatible'] = "riscv,cpu-intc"
            intc_node['#interrupt-cells'] = 1
            intc_node + LopperProp("interrupt-controller")
            match_cpunode.add(intc_node)

            timer_node = LopperNode()
            timer_node.abs_path = "/timer"
            timer_node.name = "timer"
            timer_node.label = "int_timer"
            timer_node["compatible"] = "riscv,timer"
            timer_node + LopperProp("bootph-all")
            timer_node + LopperProp("interrupts-extended = <&cpu0_intc 5>")
            sdt.tree + timer_node

            for node in sdt.tree['/'].subnodes():
                if node.propval('xlnx,ip-name', list) == ['axi_intc']:
                    node + LopperProp("interrupts-extended = <&cpu0_intc 9>")
                    break

            delete_unused_props( sdt.tree[match_cpunode] , driver_proplist, False)

    return True
