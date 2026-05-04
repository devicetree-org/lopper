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
from zephyr_board_dt import process_overlay_with_lopper_api
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


def check_console_uart_accessibility(sdt, options):
    """
    Check if the console UART (from /chosen/stdout-path) is accessible to MicroBlaze RISC-V.

    Logic:
    1. Read /chosen/stdout-path to get configured console UART alias (e.g., serial0)
    2. Resolve alias to get actual UART device node
    3. Check if UART is accessible using accessible_by() API
    4. If accessible: enable PS UART (set status="okay")
    5. If NOT accessible: find PL UART that is accessible and update /chosen/stdout-path

    This ensures Vitis BSP and Zephyr use the same console UART.

    Args:
        sdt: System device tree
        options: User options with processor name
    """
    try:
        # Only process for MicroBlaze RISC-V
        match_cpunode = get_cpu_node(sdt, options)
        cpu_ip = match_cpunode.propval('xlnx,ip-name', list)
        if not cpu_ip or cpu_ip[0] not in ('microblaze', 'microblaze_riscv'):
            return

        chosen_node = sdt.tree['/chosen']
        if chosen_node.propval('stdout-path') == ['']:
            return

        stdout_path = chosen_node.propval('stdout-path', list)[0]
        alias_name = stdout_path.split(':')[0]

        alias_node = sdt.tree['/aliases']
        if alias_node.propval(alias_name) == ['']:
            return

        uart_path = alias_node.propval(alias_name, list)[0]

        try:
            uart_node = sdt.tree[uart_path]
        except KeyError:
            return

        # Check if UART is accessible by any CPU cluster using accessible_by() API
        is_accessible = bool(sdt.tree.accessible_by(uart_node))

        if is_accessible:
            current_status = uart_node.propval('status', list)
            if current_status and current_status[0] == 'disabled':
                uart_node['status'] = 'okay'
        else:
            pl_uart_compatibles = [
                'xlnx,axi-uart16550', 'xlnx,xps-uart16550',
                'xlnx,xps-uartlite', 'xlnx,mdm-riscv',
                'ns16550'
            ]

            accessible_pl_uart = None
            for node in sdt.tree.nodes('/.*'):
                if node.propval('compatible') != ['']:
                    node_compatibles = node.propval('compatible', list)
                    if any(compat in node_compatibles for compat in pl_uart_compatibles):
                        # Check if this PL UART is accessible using accessible_by() API
                        if sdt.tree.accessible_by(node):
                            accessible_pl_uart = node
                            break

            if accessible_pl_uart:
                pl_uart_label = accessible_pl_uart.label if accessible_pl_uart.label else "serial_pl"
                alias_node[pl_uart_label] = accessible_pl_uart.abs_path
                baud_rate = ":" + stdout_path.split(':')[1] if ':' in stdout_path else ""
                chosen_node['stdout-path'] = pl_uart_label + baud_rate
    except Exception:
        pass


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

    # Check console UART accessibility for MicroBlaze Zephyr DTS
    # This ensures Vitis BSP and Zephyr use the same accessible console UART
    if zephyr_dt:
        check_console_uart_accessibility(sdt, options)

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
                elif xlnx_openamp_keep_node(linux_dt, zephyr_dt, node, sdt.tree):
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
            delete_unused_props( sdt.tree[match_cpunode] , driver_proplist, False)

    if zephyr_dt:
        if "r52" in machine or "a78" in machine or "a72" in machine or "r5" in machine:
            xlnx_generate_zephyr_domain_dts_arm(tgt_node, sdt, options, machine)
            if "a78" in machine or "a72" in machine:
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
    wwdt_nodes = []
    ufs_nodes = []
    rtc_nodes = []

    if "amd,versal2" in root_node['compatible'].value:
        root_node["model"] = "AMD Versal Gen 2"
        root_node["compatible"] = "xlnx,versal2"

    for node in root_sub_nodes:
        if node.depth == 1:
            if "cpus" not in node.name and "amba" not in node.name and "memory" not in node.name and "chosen" not in node.name and "bus" not in node.name and "axi" not in node.name and "timer" not in node.name and "alias" not in node.name and not xlnx_openamp_keep_node(False, True, node, sdt.tree):
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
                elif "a72" in machine and (val == "psv_acpu_gic"):
                    name  = node.name
                    sdt.tree.delete(node)
                    new_dst_node = node()
                    new_dst_node['#interrupt-cells'] = 4
                    new_dst_node.abs_path = "/axi/interrupt-controller@f9000000 "
                    new_dst_node.name = "interrupt-controller@f9000000"
                    new_dst_node['compatible'].value = ["arm,gic-v3", "arm,gic"]
                    sdt.tree + new_dst_node
                    sdt.tree.sync()
                elif "r5" in machine and (val == "psv_rcpu_gic"):
                    name  = node.name
                    sdt.tree.delete(node)
                    sdt.tree.delete(node.parent)
                    new_dst_node = node()
                    new_dst_node['#interrupt-cells'] = 4
                    new_dst_node.abs_path = "/axi/interrupt-controller@f9000000 "
                    new_dst_node.name = "interrupt-controller@f9000000 "
                    new_dst_node['compatible'].value = ["arm,gic-v1", "arm,gic"]
                    sdt.tree + new_dst_node
                    sdt.tree.sync()

            compatible = node.propval('compatible', list)[0]
            if compatible == "arm,armv8-timer":
                if "a72" in machine:
                    node["interrupts"].value = [0x1, 0xd, 0x2, 0xa0, 0x1, 0xe, 0x2, 0xa0, 0x1, 0xb, 0x2, 0xa0, 0x1, 0xa, 0x2, 0xa0]
                else:
                    node["interrupts"].value = [0x1, 0xd, 0x4, 0xa4, 0x1, 0xe, 0x4, 0xa4, 0x1, 0xb, 0x4, 0xa4, 0x1, 0xa, 0x4, 0xa4]


            elif compatible == "xlnx,zynqmp-rtc":
                # RTC: Convert 3-cell interrupts to 4-cell GICv3 format by adding 0xa0 priority
                intr_list = node["interrupts"].value
                node["interrupts"].value = [cell for i in range(0, len(intr_list), 3)
                                for cell in intr_list[i:i+3] + [0xa0]]
                # Set clock frequency for RTC
                # Revert this snippet once the clock support is added in sdtgen
                if node.propval('clock-frequency') == ['']:
                    node["clock-frequency"] = 32767
            elif compatible == "cdns,ttc":
                # TTC: Convert 3-cell interrupts to 4-cell GIC format by adding 0xa0 priority
                intr_list = node["interrupts"].value
                node["interrupts"].value = [cell for i in range(0, len(intr_list), 3)
                                for cell in intr_list[i:i+3] + [0xa0]]
            elif compatible == "xlnx,versal-gem":
                # GEM: Convert 3-cell interrupts to 4-cell GICv3 format
                intr_list = node["interrupts"].value
                node["interrupts"].value = [cell for i in range(0, len(intr_list), 3)
                                for cell in intr_list[i:i+3] + [0xa0]]
            elif node.propval('interrupts') != ['']:
                intr_list = node["interrupts"].value
                intr_parent_cells = None
                try:
                    ip_val = node.propval('interrupt-parent')
                    if ip_val != ['']:
                        ip_node = sdt.tree.pnode(ip_val[0])
                        if ip_node is None:
                            # Dangling phandle (e.g. imux deleted) - repoint to GIC
                            for gic_candidate in sdt.tree['/'].subnodes():
                                if gic_candidate.propval('compatible') != ['']:
                                    if any('arm,gic' in c for c in gic_candidate.propval('compatible', list)):
                                        node['interrupt-parent'].value = [gic_candidate.phandle]
                                        break
                        elif ip_node.propval('#interrupt-cells') != ['']:
                            intr_parent_cells = ip_node.propval('#interrupt-cells', list)[0]
                except Exception:
                    pass
                if intr_parent_cells is not None and intr_parent_cells <= 2:
                    pass
                elif len(intr_list) >= 3 and len(intr_list) % 3 == 0:
                    node["interrupts"].value = [cell for i in range(0, len(intr_list), 3)
                                    for cell in intr_list[i:i+3] + [0xa0]]

            if compatible == "cpus,cluster":
                node.name = "cpus" 

            # Collect all the wwdt nodes (PS WWDT and PL timebase WDT)
            if any(version in node["compatible"].value for version in ("xlnx,versal-wwdt-1.0", "xlnx,versal-wwdt")):
                wwdt_nodes.append(node)
            if any(version in node["compatible"].value for version in ("xlnx,axi-timebase-wdt-3.0", "xlnx,xps-timebase-wdt-1.00.a")):
                wwdt_nodes.append(node)
            if "amd,versal2-ufs" in node["compatible"].value:
                ufs_nodes.append(node)
            if "xlnx,zynqmp-rtc" in node["compatible"].value:
                rtc_nodes.append(node)

    xlnx_remove_unsupported_nodes(tgt_node, sdt, machine)

    # Zephyr Watchdog samples/tests expects watchdog0 alias.
    # Add watchdog0 alias by referring it to the first occurence of the wwdt node
    if wwdt_nodes:
        wwdt_node = sdt.tree.pnode(wwdt_nodes[0].phandle)
        sdt.tree['/aliases'] + LopperProp(name="watchdog0", value = wwdt_node.abs_path)
    # Zephyr UFS tests expects ufs0 alias, referring to first ufs node
    if ufs_nodes:
        ufs_node = sdt.tree.pnode(ufs_nodes[0].phandle)
        sdt.tree['/aliases'] + LopperProp(name="ufs0", value = ufs_node.abs_path)
    if rtc_nodes:
        rtc_node = sdt.tree.pnode(rtc_nodes[0].phandle)
        sdt.tree['/aliases'] + LopperProp(name="rtc", value = rtc_node.abs_path)

    for node in root_sub_nodes:
        if node.propval("compatible") != ['']:
            if node.propval("compatible") == "indirect-bus":
                sdt.tree.delete(node)
            compatible = node.propval('compatible', list)[0]
            if compatible == "arm,armv8-timer" and 'psv_cortexr5' in machine:
                sdt.tree.delete(node)
        if node.name == 'reserved-memory' and 'r52' in machine:
            node.delete('ranges')
            node + LopperProp(name='ranges')

    return True

def _apply_pl_peripheral_transforms(node, schema, rename_timer=None, stdout_baud=None):
    """Apply PL-specific peripheral transformations shared between PS+PL and pure-PL paths.

    Args:
        rename_timer: If set, rename AXI Timer compatible to this value (MicroBlaze only).
        stdout_baud: Fallback baud rate for UARTNS550 current-speed.

    Returns:
        Updated is_supported_periph list if compatible changed, else None.
    """
    compatible_changed = False

    if rename_timer and "xlnx,xps-timer-1.00.a" in node["compatible"].value:
        node["compatible"].value = [rename_timer]

    if "xlnx,eth-dma" in node["compatible"].value:
        node["compatible"].value = ["xlnx,eth-dma"]
        if node.props("dma-channels") == []:
            node + LopperProp("dma-channels")
            node["dma-channels"].value = [2]

    if any(v in node["compatible"].value for v in ("xlnx,xps-spi-2.00.a", "xlnx,axi-quad-spi-3.2")):
        if node.propval('#address-cells') != ['1']:
            node['#address-cells'] = 1
        if node.propval('#size-cells') != ['0']:
            node['#size-cells'] = 0
        node["compatible"] = "xlnx,xps-spi-2.00.a"
        compatible_changed = True

    if any(v in node["compatible"].value for v in ("ns16550", "xlnx,axi-uart16550-2.0")):
        if node.propval('current-speed') == ['']:
            node["current-speed"] = LopperProp("current-speed")
            if node.propval('xlnx,baudrate') != ['']:
                node["current-speed"].value = node["xlnx,baudrate"].value
            elif stdout_baud:
                node["current-speed"].value = stdout_baud
            else:
                node["current-speed"].value = 9600

    if "xlnx,axi-ethernet-1.00.a" in node["compatible"].value:
        node["compatible"].value = ["xlnx,axi-ethernet-1.00.a"]
        subnodes = node.subnodes()
        for subnode in subnodes:
            node.delete(subnode)
        emacnode = LopperNode()
        eth_schema = [value for key, value in schema.items() if key == "xlnx,axi-ethernet-1.00.a"]
        if eth_schema:
            eth_required = list(eth_schema[0]["required"])
            eth_required.reverse()
            for prop in eth_required:
                if prop == "compatible":
                    emacnode[prop] = ["xlnx,axi-ethernet-1.00.a"]
                elif prop in ("reg", "status"):
                    continue
                else:
                    if node.propval(prop) != ['']:
                        emacnode[prop] = node[prop]
            emacnode.name = "ethernet-mac"
            emacnode.label_set("axi_ethernet")
            node.add(emacnode)
            for prop in eth_required:
                if prop not in ["compatible", "reg", "status"] and node.props(prop) != []:
                    node.delete(prop)
            node["compatible"].value = ["xlnx,axi-ethernet-subsystem-7.2"]
            node.label_set("axi_enet")
            name = node.name
            parts = name.split("@")
            if len(parts) == 2:
                node.name = f"axi-ethernet-subsystem@{parts[1]}"
        compatible_changed = True

    if compatible_changed:
        return [value for key, value in schema.items() if key in node["compatible"].value]
    return None


def xlnx_remove_unsupported_nodes(tgt_node, sdt, machine):
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
                    if any(version in node["compatible"].value for version in ("arm,cortex-r52", "arm,cortex-r5")):
                        if node.name != ['']:
                            node.name = "cpu@0"
                    if node.propval('xlnx,ip-name') != ['']:
                        val = node.propval('xlnx,ip-name', list)[0]
                        if val == "axi_intc":
                            num_intr = node.propval('xlnx,num-intr-inputs', list)[0]
                            num_intr += 12

                    is_supported_periph = [value for key,value in schema.items() if key in node["compatible"].value]
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
                    # PS-I3C
                    if "snps,dw-i3c-master-1.00a" in node["compatible"].value:
                        node["compatible"].value = ["snps,designware-i3c"]
                        if node.propval('#address-cells') != [3]:
                            node["#address-cells"] = LopperProp("#address-cells")
                            node["#address-cells"].value = 3
                            node.add(node["#address-cells"])
                        if node.propval('#size-cells') != [0]:
                            node["#size-cells"] = LopperProp("#size-cells")
                            node["#size-cells"].value = 0
                            node.add(node["#size-cells"])
                    #UFS
                    if "amd,versal2-ufs" in node["compatible"].value:
                        new_node = LopperNode()
                        new_node.name = "ufsdisk0"
                        new_node['compatible'] = "zephyr,ufs-disk"
                        new_node['disk-name'] = "UFS"
                        node.add(new_node)
                        ufs_reg_val = node["reg"].value;
                        platform = sdt.tree['/']['family'].value
                        if platform == ['Versal_2VE_2VM']:
                            ufs_slcr_reg = [0, 0xf1060000, 0, 0x2000]
                            ufs_reg_val = ufs_reg_val + ufs_slcr_reg
                        for node_efuse in root_sub_nodes:
                            if node_efuse.propval("compatible") != ['']:
                                if any("xlnx,pmc-efuse-cache" in comp for comp in node_efuse["compatible"].value):
                                    ufs_efuse_reg = node_efuse["reg"].value
                                    ufs_reg_val = ufs_reg_val + ufs_efuse_reg
                                    break
                        for node_crp in root_sub_nodes:
                            if node_crp.propval("compatible") != ['']:
                                if any("xlnx,crp" in comp for comp in node_crp["compatible"].value):
                                    ufs_crp_reg = node_crp["reg"].value
                                    ufs_reg_val = ufs_reg_val + ufs_crp_reg
                                    break
                        node["reg"].value = ufs_reg_val
                        if node.props('clock-names') != []:
                            desired_clock_names = ["core", "ref_clk"]
                            clk_names = node.propval("clock-names")
                            if clk_names == []:
                                clk_names_list = []
                            else:
                                clk_names_list = [str(x) for x in clk_names]
                            if clk_names_list != desired_clock_names:
                                node["clock-names"].value = desired_clock_names
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
                            new_node['disk-name'] = "EMMC"
                        else:
                            new_node.name = "sdmmc"
                            new_node['compatible'] = "zephyr,sdmmc-disk"
                            new_node['disk-name'] = "SD"
                            node['power-delay-ms'] = 10
                        node.add(new_node)
                        node["compatible"] = "xlnx,versal-8.9a"
                    # TTCPS
                    if "cdns,ttc" in node["compatible"].value:
                        if 'psv_cortexr5' in machine:
                            node["compatible"] = "xlnx,ttcps"
                            if node.propval('interrupt-names') == ['']:
                                ttc_irq_names = ["irq_0", "irq_1", "irq_2"]
                                node["interrupt-names"] = LopperProp("interrupt-names")
                                node["interrupt-names"].value = ttc_irq_names
                                node.add(node["interrupt-names"])
                            clk_freq = node.propval('xlnx,clock-freq')
                            # Round to nearest MHz (optional but clean)
                            rounded_clk = int(round(clk_freq[0] / 1000000.0)) * 1000000
                            node["clock-frequency"] = LopperProp("clock-frequency")
                            node["clock-frequency"].value = rounded_clk
                            node.add(node["clock-frequency"])
                        else:
                            for i in range(3):
                                new_node = LopperNode()
                                new_node["compatible"] = "xlnx,ttc-counter"
                                new_node["clock-frequency"] = LopperProp("clock-frequency")
                                new_node["clock-frequency"].value = node["xlnx,clock-freq"].value
                                new_node.add(new_node["clock-frequency"])
                                new_node["reg"] = node["reg"]
                                new_node.label_set(f"{node.label}_timer{i}")
                                new_node["interrupt-parent"] = node["interrupt-parent"]
                                new_node.name = f"counter{i}@{node['reg'][1]:x}"
                                new_node["timer-id"] = LopperProp("timer-id")
                                new_node["timer-id"].value = i
                                new_node["interrupts"] = LopperProp("interrupts")
                                new_node["interrupts"].value = node["interrupts"].value[i * 4 : (i + 1) * 4]
                                new_node["timer-width"] = node["timer-width"]
                                new_node.parent = node.parent
                                node.parent.add(new_node)
                            sdt.tree.delete(node)
                    # CANFD
                    if "xlnx,canfd-2.0" in node["compatible"].value:
                        node["compatible"] = "xlnx,canfd-2.0"
                    # OSPI
                    if "xlnx,versal-ospi-1.0" in node["compatible"].value:
                        node["compatible"].value = ["xlnx,versal-ospi-1.0"]
                        if node.propval('#address-cells') != [1]:
                            node["#address-cells"] = LopperProp("#address-cells")
                            node["#address-cells"].value = 1
                            node.add(node["#address-cells"])
                        if node.propval('#size-cells') != [0]:
                            node["#size-cells"] = LopperProp("#size-cells")
                            node["#size-cells"].value = 0
                            node.add(node["#size-cells"])
                    # SPIPS
                    if "cdns,spi-r1p6" in node["compatible"].value:
                        node["compatible"] = "cdns,spi"
                        if node.propval('#address-cells') != [1]:
                            node["#address-cells"] = LopperProp("#address-cells")
                            node["#address-cells"].value = 1
                            node.add(node["#address-cells"])
                        if node.propval('#size-cells') != [0]:
                            node["#size-cells"] = LopperProp("#size-cells")
                            node["#size-cells"].value = 0
                            node.add(node["#size-cells"])
                        clk_freq = node.propval('xlnx,spi-clk-freq-hz')
                        node["clock-frequency"] = LopperProp("clock-frequency")
                        node["clock-frequency"].value = clk_freq
                        node.add(node["clock-frequency"])
                        node["tx-fifo-depth"] = LopperProp("tx-fifo-depth")
                        node["tx-fifo-depth"].value = 128
                        node.add(node["tx-fifo-depth"])
                        node["rx-fifo-depth"] = LopperProp("rx-fifo-depth")
                        node["rx-fifo-depth"].value = 128
                        node.add(node["rx-fifo-depth"])
                        node["fifo-width"] = LopperProp("fifo-width")
                        node["fifo-width"].value = 8
                        node.add(node["fifo-width"])
                    #ADMA
                    if any(version in node["compatible"].value for version in ("xlnx,zynqmp-dma-1.0", "amd,versal2-dma-1.0")):
                        if node.props("clocks") != [] and node.propval("clocks") != []:
                            node["clocks"].value = []
                        if node.props('clock-names') != []:
                            desired_clock_names = ["clk_main", "clk_apb"]
                            clk_names = node.propval("clock-names")
                            if clk_names == []:
                                clk_names_list = []
                            else:
                                clk_names_list = [str(x) for x in clk_names]
                            if clk_names_list != desired_clock_names:
                                node["clock-names"].value = desired_clock_names
                        if node.props("#dma-cells") != [] and node.propval("#dma-cells") != [1]:
                            node['#dma-cells'].value = [1]
                        if node.props("xlnx,bus-width") != [] and node.propval("xlnx,bus-width") != [64]:
                            node["xlnx,bus-width"].value = [64]
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
                    # GEM (Gigabit Ethernet MAC) — xlnx,versal-gem / cdns,gem
                    if "xlnx,versal-gem" in node["compatible"].value:
                        # Remove any pre-existing sub-nodes
                        for subnode in node.subnodes():
                            node.delete(subnode)
                        # --- Build ethernet_mac child node ---
                        mac_node = LopperNode()
                        mac_node["compatible"] = ["xlnx,gem"]
                        mac_node["status"] = "okay"
                        # clock-frequency: read from xlnx,enet-clk-freq-hz (set by pcw.dtsi)
                        if node.propval('xlnx,enet-clk-freq-hz') != ['']:
                            mac_node["clock-frequency"] = node["xlnx,enet-clk-freq-hz"].value
                        # Copy interrupts (already converted to 4-cell GICv3 format)
                        if node.propval('interrupts') != ['']:
                            mac_node["interrupts"] = node["interrupts"].value
                        # Fixed MAC tuning parameters
                        mac_node["amba-ahb-burst-length"] = 16
                        mac_node["hw-rx-buffer-size"] = 3
                        mac_node["hw-rx-buffer-offset"] = 0
                        mac_node + LopperProp("hw-tx-buffer-size-full")
                        mac_node["rx-buffer-descriptors"] = 32
                        mac_node["tx-buffer-descriptors"] = 32
                        mac_node["rx-buffer-size"] = 1536
                        mac_node["tx-buffer-size"] = 1536
                        mac_node + LopperProp("discard-rx-fcs")
                        mac_node + LopperProp("unicast-hash")
                        mac_addr_prop = LopperProp(name="local-mac-address")
                        mac_addr_prop.value = [0x00, 0x0a, 0x35, 0x00, 0x01, 0x02]
                        mac_addr_prop.binary = True
                        mac_node + mac_addr_prop
                        mac_node.name = "ethernet_mac"
                        mac_node.label_set(f"{node.label}_mac")
                        node.add(mac_node)
                        node["compatible"].value = ["xlnx,gem-controller"]
                        is_supported_periph = [value for key,value in schema.items() if key in node["compatible"].value]
                    if node.parent and 'amba_pl' in node.parent.name:
                        result = _apply_pl_peripheral_transforms(node, schema)
                        if result is not None:
                            is_supported_periph = result
                    if is_supported_periph:
                        required_prop = is_supported_periph[0]["required"]
                        prop_list = list(node.__props__.keys())
                        valid_alias_proplist.append(node.name)
                        pl_node = node.parent and node.parent.propval('interrupt-parent') == ['']
                        if pl_node:
                            for preserve_prop in ('interrupts', 'interrupt-parent'):
                                if preserve_prop not in required_prop and node.propval(preserve_prop) != ['']:
                                    required_prop = required_prop + [preserve_prop]
                        is_timer = "xlnx,xps-timer-1.00.a" in node["compatible"].value
                        if is_timer:
                            if 'clocks' in required_prop:
                                required_prop.remove('clocks')
                            for p in ('clock-frequency', 'xlnx,count-width'):
                                if p not in required_prop and node.propval(p) != ['']:
                                    required_prop = required_prop + [p]
                        # Create fixed clock nodes
                        if 'clocks' in required_prop:
                            if any(clock_prop := (re.search(r'xlnx,.*-clk-freq-hz$', prop)) for prop in prop_list):
                                clk_freq = node[clock_prop.group()].value
                            elif node.propval('clock-frequency') != ['']:
                                clk_freq = node.propval('clock-frequency')[0]
                            elif node.propval('clocks') != ['']:
                                clk_freq = 0
                                try:
                                    first_clk_ph = node.propval('clocks')[0]
                                    clk_node = sdt.tree.pnode(first_clk_ph)
                                    if clk_node and clk_node.propval('clock-frequency') != ['']:
                                        clk_freq = clk_node.propval('clock-frequency')[0]
                                except Exception:
                                    pass
                            else:
                                # If there is no clk-freq property use 0MHZ as default this prevent
                                # build failure if any of the ip does not have this property.
                                clk_freq = 0
                            # Always create individual clock node for each peripheral
                            new_node = LopperNode()
                            new_node.abs_path = "/clocks"
                            if node["compatible"].value == ["xlnx,zynqmp-dma-1.0"] or node["compatible"].value == ["amd,versal2-dma-1.0"]:
                                new_node.name = "adma_ref_clk"
                            else:
                                new_node.name = node.label + "_ref_clock"
                            new_node['compatible'] = ["fixed-clock"]
                            new_node['#clock-cells'] = 0
                            if node["compatible"].value == ["xlnx,zynqmp-dma-1.0"] or node["compatible"].value == ["amd,versal2-dma-1.0"]:
                                clk_freq = 450000000
                            new_node['clock-frequency'] = clk_freq
                            new_node.label_set(new_node.name)
                            sdt.tree.add(new_node)
                            if node.props('clocks') != []:
                                node.delete('clocks')
                            if node["compatible"].value == ["xlnx,zynqmp-dma-1.0"] or \
                                node["compatible"].value == ["amd,versal2-dma-1.0"] or \
                                "amd,versal2-ufs" in node["compatible"].value:
                                clock_prop = f"clocks = <&{new_node.name}>, <&{new_node.name}>"
                            else:
                                clock_prop = f"clocks = <&{new_node.name}>"
                            node + LopperProp(clock_prop)
                        for prop in prop_list:
                            if prop not in required_prop:
                                node.delete(prop)
                    else:
                        if node.name not in ("axi", "soc", "amba_pl") and node not in memnode_list and not xlnx_openamp_keep_node(False, True, node, sdt.tree):
                            sdt.tree.delete(node)

    for node in memnode_list:
        if node.propval('ranges') != ['']:
            node.delete('ranges')

    for node in root_sub_nodes:
        if node.name == "amba_pl":
            has_periph = any(
                child.propval('reg') != [''] and 'interrupt-controller' not in child.__props__
                for child in node.subnodes()
            )
            if not has_periph:
                sdt.tree.delete(node)
            break

    alias_node = sdt.tree['/aliases']
    alias_prop_list = list(alias_node.__props__.keys())
    for prop in alias_prop_list:
        val = sdt.tree['/aliases'].propval(prop, list)[0]
        val = val.rsplit('/', 1)[-1]
        if val not in valid_alias_proplist:
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

                   # Find CANFD nodes for zephyr,canbus
                   can_nodes = []
                   for root_node in root_sub_nodes:
                       if root_node.propval('compatible') != ['']:
                           compatible_list = root_node.propval('compatible', list)
                           if any('xlnx,canfd-2.0' in compat for compat in compatible_list):
                               can_nodes.append(root_node)

                   # Set zephyr,canbus to first CANFD node if available
                   if can_nodes:
                       sdt.tree[node]['zephyr,canbus'] = can_nodes[0].abs_path

    if sdt.tree['/chosen'].propval('zephyr,sram') == ['']:
        sdt.tree['/chosen'] + LopperProp(name="zephyr,sram", value = sram_node)

    return True

def generate_board_kconfig_defconfig(isa_string, cpu_node, intc_node, num_interrupts):
    """
    Generate board-level Kconfig.defconfig content based on hardware configuration.
    Similar to boards/amd/mbv32/Kconfig.defconfig structure.

    Args:
        isa_string: The RISC-V ISA string from cflags.yaml
        cpu_node: The CPU node from device tree
        intc_node: The interrupt controller node
        num_interrupts: Number of interrupts detected

    Returns:
        str: Complete board-level Kconfig.defconfig content
    """
    license_content = '''#
# Copyright (c) 2024 - 2025 Advanced Micro Devices, Inc.
#
# SPDX-License-Identifier: Apache-2.0
#

'''

    content = license_content
    content += f"if BOARD_MBV32\n"

    # Track added extensions to avoid duplicates
    added_extensions = set()

    # Base ISA configurations - always add RV32I for MicroBlaze RISC-V
    content += "config RISCV_ISA_RV32I\n"
    content += "\tdefault y\n\n"
    added_extensions.add('RISCV_ISA_RV32I')  # Track it to avoid duplicates

    # Process ISA string to generate configs in the exact order: RV32I, M, A, C, ZICSR, ZIFENCEI
    if isa_string:
        # Define the exact order matching the existing board defconfig
        ordered_extensions = [
            ('m', 'RISCV_ISA_EXT_M'),
            ('a', 'RISCV_ISA_EXT_A'),
            ('c', 'RISCV_ISA_EXT_C'),
            ('_zicsr', 'RISCV_ISA_EXT_ZICSR'),
            ('_zifencei', 'RISCV_ISA_EXT_ZIFENCEI'),
        ]

        # Process base ISA part for M, A, C detection
        isa_base = isa_string.split('_')[0]  # Same as SOC level

        # Process each extension in the defined order
        for key, config_name in ordered_extensions:
            if config_name not in added_extensions:
                # For M, A, C - check in base ISA part
                if key in ['m', 'a', 'c']:
                    if isa_base.find(key) != -1:
                        content += f"config {config_name}\n"
                        content += "\tdefault y\n\n"
                        added_extensions.add(config_name)
                # For ZICSR, ZIFENCEI - check in full ISA string
                elif key.startswith('_'):
                    if isa_string.find(key) != -1:
                        content += f"config {config_name}\n"
                        content += "\tdefault y\n\n"
                        added_extensions.add(config_name)

        # Handle any additional extensions (F, D, ZBA, ZBB, etc.) after the core ones
        additional_z_extensions = [
            ('_zba', 'RISCV_ISA_EXT_ZBA'),
            ('_zbb', 'RISCV_ISA_EXT_ZBB'),
            ('_zbc', 'RISCV_ISA_EXT_ZBC'),
            ('_zbs', 'RISCV_ISA_EXT_ZBS'),
        ]

        additional_base_extensions = [
            ('f', 'RISCV_ISA_EXT_F'),
            ('d', 'RISCV_ISA_EXT_D'),
        ]

        # Add F, D extensions if present
        for key, config_name in additional_base_extensions:
            if isa_base.find(key) != -1 and config_name not in added_extensions:
                content += f"config {config_name}\n"
                content += "\tdefault y\n\n"
                added_extensions.add(config_name)

        # Add additional Z-extensions if present
        for key, config_name in additional_z_extensions:
            if isa_string.find(key) != -1 and config_name not in added_extensions:
                content += f"config {config_name}\n"
                content += "\tdefault y\n\n"
                added_extensions.add(config_name)

    content += "endif\n"
    return content

def xlnx_generate_zephyr_domain_dts(tgt_node, sdt, options):
    root_node = sdt.tree[tgt_node]
    root_sub_nodes = root_node.subnodes()
    symbol_node = sdt.tree['/__symbols__']
    valid_alias_proplist = []
    stdout_baud = None
    try:
        chosen_node = sdt.tree['/chosen']
        if chosen_node.propval('stdout-path') != ['']:
            stdout_path = chosen_node.propval('stdout-path', list)[0]
            match = re.search(r':(\d+)', str(stdout_path))
            if match:
                stdout_baud = int(match.group(1))
    except Exception:
        stdout_baud = None
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
    is_iomodule_present = None
    for node in root_sub_nodes:
        if node.propval('xlnx,ip-name') != ['']:
            val = node.propval('xlnx,ip-name', list)[0]
            if val == "axi_intc":
                is_axi_intc_present = node
            elif val == "axi_timer":
                is_axi_timer_present = node
            elif val == "iomodule":
                is_iomodule_present = node

    err_no_intc = "\nERROR: Zephyr OS requires the presence of at least one interrupt controller. Please ensure that the axi_intc is included in the design, with fast interrupts disabled.\r"
    err_no_timer = "\nERROR: Zephyr OS requires at least one timer controller with interrupts enabled for its scheduler. Please include the axi_timer in your hardware design and ensure its interrupts are properly connected.\r"
    warn_intc_has_fast = "\nWARNING: Zephyr does not support fast interrupts; they will be handled as standard interrupts. Therefore, enabling FAST interrupts in the AXI INTC core will not improve interrupt latency. Additionally, fast interrupts are not supported in QEMU.\r"
    err_timer_nointr = "\nERROR: Zephyr OS requires at least one timer with interrupts enabled to manage its scheduler effectively. Please ensure that the interrupt pins for the timer are correctly connected in your hardware design and rebuild with the updated configuration.\r"
    if is_iomodule_present:
        if is_iomodule_present.propval('xlnx,intc-has-fast') != ['']:
            val = is_iomodule_present.propval('xlnx,intc-has-fast', list)[0]
            if val != 0 or val != 0x0:
                print(warn_intc_has_fast)
    else:
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

    memnode_list = sdt.tree.nodes('/memory@.*')
    for mem_node in memnode_list:
        if mem_node.propval('ranges') != ['']:
            mem_node.delete('ranges')

    # MicroBlaze Zephyr DTS should only keep PL peripherals plus core nodes
    # when the design includes both PS and PL (MicroBlaze) domains.
    has_pl_mb = False
    try:
        match_cpunode = get_cpu_node(sdt, options)
        cpu_ip = match_cpunode.propval('xlnx,ip-name', list)
        has_pl_mb = bool(cpu_ip and cpu_ip[0] == "microblaze_riscv")
    except Exception:
        has_pl_mb = any(
            node.propval('xlnx,ip-name') != [''] and node.propval('xlnx,ip-name', list)[0] == "microblaze_riscv"
            for node in root_sub_nodes
        )

    has_ps_axi = False
    ps_serial_data_to_recreate = []
    ps_ipi_data_to_recreate = []

    try:
        axi_node = sdt.tree['/axi']
        has_ps_axi = True

        if has_pl_mb:
            ps_uart_compatibles = ['arm,pl011', 'arm,sbsa-uart', 'arm,primecell']
            ps_ipi_compatibles = ['xlnx,versal-ipi-mailbox']
            for node in list(axi_node.subnodes()):
                if node.propval('compatible') != ['']:
                    node_compatibles = node.propval('compatible', list)
                    if any(compat in node_compatibles for compat in ps_uart_compatibles):
                        if 'serial' in node.name.lower() and node.propval('status', list) == ['okay']:
                            node_data = {
                                'name': node.name,
                                'label': node.label,
                                'properties': {}
                            }
                            for prop_name, prop_obj in node.__props__.items():
                                node_data['properties'][prop_name] = prop_obj.value
                            ps_serial_data_to_recreate.append(node_data)
                    elif any(compat in node_compatibles for compat in ps_ipi_compatibles):
                        if node.propval('status', list) == ['okay']:
                            node_data = {
                                'name': node.name,
                                'label': node.label,
                                'properties': {},
                                'children': []
                            }
                            for prop_name, prop_obj in node.__props__.items():
                                node_data['properties'][prop_name] = prop_obj.value
                            for child in node.child_nodes.values():
                                child_data = {
                                    'name': child.name,
                                    'label': child.label,
                                    'properties': {}
                                }
                                for prop_name, prop_obj in child.__props__.items():
                                    child_data['properties'][prop_name] = prop_obj.value
                                node_data['children'].append(child_data)
                            ps_ipi_data_to_recreate.append(node_data)
    except Exception:
        pass

    if has_pl_mb and has_ps_axi:
        allowed_top_nodes = {
            "amba_pl",
            "soc",
            "chosen",
            "aliases",
            "clocks",
            "__symbols__",
        }
        for node in list(root_sub_nodes):
            if node.depth != 1:
                continue
            if node.name in allowed_top_nodes:
                continue
            if node.name.startswith("memory@"):
                continue
            if node.name.startswith("cpus"):
                continue
            sdt.tree.delete(node)
        root_sub_nodes = root_node.subnodes()

        if ps_serial_data_to_recreate or ps_ipi_data_to_recreate:
            target_bus = None
            try:
                target_bus = sdt.tree['/soc']
            except KeyError:
                try:
                    target_bus = sdt.tree['/amba_pl']
                except KeyError:
                    pass

            if target_bus:
                for idx, serial_data in enumerate(ps_serial_data_to_recreate):
                    try:
                        new_serial_path = f"{target_bus.abs_path}/{serial_data['name']}"
                        serial_label = f"serial{idx}"

                        new_serial = LopperNode(-1, new_serial_path)
                        new_serial.tree = sdt.tree
                        new_serial.label = serial_label

                        new_serial + LopperProp(name="compatible", value=["arm,sbsa-uart"])
                        new_serial + LopperProp(name="status", value=["okay"])

                        if 'reg' in serial_data['properties']:
                            new_serial + LopperProp(name="reg", value=serial_data['properties']['reg'])

                        baudrate = serial_data['properties']['xlnx,baudrate'][0] if 'xlnx,baudrate' in serial_data['properties'] else 115200
                        new_serial + LopperProp(name="current-speed", value=[baudrate])

                        sdt.tree.add(new_serial)

                        alias_path = new_serial.abs_path.replace("/amba_pl/", "/soc/")
                        alias_node = sdt.tree['/aliases']
                        alias_node + LopperProp(name=serial_label, value=alias_path)

                        valid_alias_proplist.append(serial_data['name'])
                    except Exception:
                        pass
                if ps_ipi_data_to_recreate:
                    _schema_file = os.path.join(os.path.dirname(os.path.realpath(__file__)), "zephyr_supported_comp.yaml")
                    _schema = utils.load_yaml(_schema_file) if utils.is_file(_schema_file) else {}
                    ipi_parent_required = _schema.get('xlnx,versal-ipi-mailbox', {}).get('required', [])
                    ipi_child_required = _schema.get('xlnx,versal-ipi-dest-mailbox', {}).get('required', [])

                    for ipi_data in ps_ipi_data_to_recreate:
                        try:
                            new_ipi_path = f"{target_bus.abs_path}/{ipi_data['name']}"
                            ipi_label = ipi_data['label']

                            new_ipi = LopperNode(-1, new_ipi_path)
                            new_ipi.tree = sdt.tree
                            new_ipi.label = ipi_label

                            for prop_name, prop_val in ipi_data['properties'].items():
                                if ipi_parent_required and prop_name not in ipi_parent_required:
                                    continue
                                if prop_name == 'compatible':
                                    new_ipi + LopperProp(name="compatible", value=["xlnx,mbox-versal-ipi-mailbox"])
                                else:
                                    new_ipi + LopperProp(name=prop_name, value=prop_val)

                            sdt.tree.add(new_ipi)

                            for child_data in ipi_data.get('children', []):
                                new_child = LopperNode()
                                # Match ARM path naming: child@<reg_addr_hex>
                                child_reg = child_data['properties'].get('reg', [])
                                if len(child_reg) > 1:
                                    new_child.name = f"child@{hex(child_reg[1])[2:]}"
                                else:
                                    new_child.name = child_data['name']
                                new_child.label = child_data['label']
                                for prop_name, prop_val in child_data['properties'].items():
                                    if ipi_child_required and prop_name not in ipi_child_required:
                                        continue
                                    if prop_name == 'compatible':
                                        new_child + LopperProp(name="compatible", value=["xlnx,mbox-versal-ipi-dest-mailbox"])
                                    else:
                                        new_child + LopperProp(name=prop_name, value=prop_val)
                                new_ipi.add(new_child)

                        except Exception:
                            pass

        try:
            chosen_node = sdt.tree['/chosen']
            memnode_list = sdt.tree.nodes('/memory@.*')
            ddr_memory = None
            bram_memory = None
            ocm_memory = None
            for mem_node in memnode_list:
                mem_path = mem_node.abs_path.lower()
                mem_name = mem_node.name.lower()

                if 'ddr' in mem_path or 'ddr' in mem_name or any(addr in mem_name for addr in ['@40000000', '@80000000', '@8000', '@4000', 'axi_noc', 'noc']):
                    if not ddr_memory:
                        ddr_memory = mem_node
                elif any(keyword in mem_path or keyword in mem_name for keyword in ['bram', 'axi_bram', 'axi-bram', 'blockram', 'dlmb', 'ilmb', 'lmb']):
                    if not bram_memory:
                        bram_memory = mem_node
                elif any(keyword in mem_path or keyword in mem_name for keyword in ['ocm', 'tcm']) or (mem_name.startswith('memory@') and 'sram' in mem_path):
                    if not ocm_memory:
                        ocm_memory = mem_node

            selected_sram = ddr_memory or bram_memory or ocm_memory

            if selected_sram:
                chosen_node['zephyr,sram'] = LopperProp(name="zephyr,sram", value=selected_sram.abs_path)
        except Exception:
            pass

    license_content = '''#
# Copyright (c) 2024 - 2025 Advanced Micro Devices, Inc.
#
# SPDX-License-Identifier: Apache-2.0
#

'''
    fix_part= '''
	select CLOCK_CONTROL
	select CLOCK_CONTROL_FIXED_RATE_CLOCK
	select CONSOLE
	select SERIAL
	select UART_CONSOLE if (UART_NS16550 || UART_XLNX_UARTLITE  || UART_XLNX_IOMODULE)
	select UART_INTERRUPT_DRIVEN if (UART_NS16550 || UART_XLNX_UARTLITE  || UART_XLNX_IOMODULE)
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
        imply MFD if DT_HAS_XLNX_IOMODULE_ENABLED
        imply XLNX_IOMODULE_INTC if DT_HAS_XLNX_IOMODULE_INTC_ENABLED
        imply XLNX_IOMODULE_PIT if DT_HAS_XLNX_IOMODULE_PIT_ENABLED
'''

    max_mem_size = 0
    num_intr = None
    for node in root_sub_nodes:
        if node.propval('xlnx,ip-name') != ['']:
            val = node.propval('xlnx,ip-name', list)[0]
            if val == "microblaze_riscv":
                compatlist = ['amd,mbv32', 'riscv']
                node['compatible'] = compatlist

                # Find existing interrupt-controller child or create a new one
                intc_node = None
                for child in node.child_nodes.values():
                    if child.name == "interrupt-controller":
                        intc_node = child
                        break
                if not intc_node:
                    intc_node = LopperNode()
                    intc_node.name = "interrupt-controller"
                    node.add(intc_node)

                intc_node.label_set("cpu_intc")
                intc_node['compatible'] = "riscv,cpu-intc"
                intc_node['#interrupt-cells'] = 1
                if 'interrupt-controller' not in intc_node.__props__:
                    intc_node + LopperProp("interrupt-controller")
                phandle_val = intc_node.phandle_or_create()
                intc_node + LopperProp(name="phandle", value=phandle_val)

    zephyr_supported_schema_file = os.path.join(os.path.dirname(os.path.realpath(__file__)), "zephyr_supported_comp.yaml")
    if utils.is_file(zephyr_supported_schema_file):
        schema = utils.load_yaml(zephyr_supported_schema_file)
        axi_wdt_nodes = []
        for node in root_sub_nodes:
            if node.parent:
                if "amba_pl" in node.parent.name:
                    if node.propval("compatible") != ['']:
                        if node.propval('xlnx,ip-name') != ['']:
                            val = node.propval('xlnx,ip-name', list)[0]
                            if val == "axi_intc":
                                num_intr = node.propval('xlnx,num-intr-inputs', list)[0]
                                num_intr += 12
                            else:
                                num_intr = 32
                        is_supported_periph = [value for key,value in schema.items() if key in node["compatible"].value]
                        result = _apply_pl_peripheral_transforms(node, schema,
                            rename_timer="amd,xps-timer-1.00.a", stdout_baud=stdout_baud)
                        if result is not None:
                            is_supported_periph = result
                        if "xlnx,iomodule-3.1" in node["compatible"].value:
                            node["compatible"].value = ["xlnx,iomodule"]
                            node['reg'] = node['reg']
                            # PIT: validate xlnx,pit-prescaler (missing, empty, or no zero cell).
                            pit_prescaler = (
                                [int(x) for x in node.propval("xlnx,pit-prescaler", list)]
                                if node.props("xlnx,pit-prescaler") != []
                                else []
                            )
                            if not pit_prescaler or 0 not in pit_prescaler:
                                print(
                                    "\nERROR: IOModule: xlnx,pit-prescaler missing, empty, or has no 0 cell "
                                    "(Zephyr requires at least one PIT with prescaler 0)\n"
                                )
                                sys.exit(1)

                            pit_size = (
                                [int(x) for x in node.propval("xlnx,pit-size", list)]
                                if node.props("xlnx,pit-size") != []
                                else []
                            )
                            pit_readable = (
                                [int(x) for x in node.propval("xlnx,pit-readable", list)]
                                if node.props("xlnx,pit-readable") != []
                                else []
                            )

                            match_cpu = get_cpu_node(sdt, options)
                            cpu_ic = next(
                                (c for c in match_cpu.child_nodes.values() if c.name == "interrupt-controller"),
                                None,
                            )
                            if cpu_ic is None:
                                for c in match_cpu.child_nodes.values():
                                    icc = c.propval("compatible", list) or []
                                    if any(isinstance(x, str) and "riscv,cpu-intc" in x for x in icc):
                                        cpu_ic = c
                                        break
                            if cpu_ic:
                                iph = cpu_ic.phandle_or_create()
                                if node.props("interrupt-parent") != []:
                                    node["interrupt-parent"].value = [iph]
                                else:
                                    node + LopperProp(name="interrupt-parent", value=[iph])
                                if node.props("interrupts") != []:
                                    node["interrupts"].value = [11]
                                else:
                                    node + LopperProp(name="interrupts", value=[11])

                            iomodule_intc_node = LopperNode()
                            iomodule_intc_node + LopperProp(name="compatible", value=["xlnx,iomodule-intc"])
                            iomodule_intc_node + LopperProp("interrupt-controller")
                            iomodule_intc_node + LopperProp(name="#interrupt-cells", value=[2])
                            if node.props("xlnx,intc-level-edge") != []:
                                iomodule_intc_node + LopperProp(
                                    name="xlnx,intc-level-edge",
                                    value=node["xlnx,intc-level-edge"].value,
                                )
                            if node.props("xlnx,intc-positive") != []:
                                iomodule_intc_node + LopperProp(
                                    name="xlnx,intc-positive",
                                    value=node["xlnx,intc-positive"].value,
                                )
                            iomodule_intc_node.name = "iomodule-interrupt-controller"
                            iomodule_intc_node.label_set("iomodule_intc")
                            node.add(iomodule_intc_node)

                            uart_node = LopperNode()
                            uart_node.name = "serial"
                            uart_node + LopperProp(name="compatible", value=["xlnx,iomodule-uart"])
                            # IOModule IP C_UART_BAUDRATE VHDL default is 9600; override from DT when present.
                            baud = 9600
                            if node.props("xlnx,uart-baudrate") != []:
                                baud = node.propval("xlnx,uart-baudrate", list)[0]
                            uart_node + LopperProp(name="current-speed", value=[baud])
                            uart_node + LopperProp(name="xlnx,uart-baudrate", value=[baud])
                            data_bits = 8
                            if node.props("xlnx,uart-data-bits") != []:
                                data_bits = node.propval("xlnx,uart-data-bits", list)[0]
                            uart_node + LopperProp(name="xlnx,uart-data-bits", value=[data_bits])
                            uart_node.label_set("iomodule_uart")
                            uart_node + LopperProp(name="status", value=["okay"])
                            node.add(uart_node)

                            # ---- PIT: create timer nodes (prescaler == 0 only; first enabled) ----
                            # IOModule C_FREQ VHDL default is 100000000 Hz; DT xlnx,clock-freq or CPU clock may override.
                            clk_hz = 100000000
                            if node.props("xlnx,clock-freq") != []:
                                clk_hz = int(node.propval("xlnx,clock-freq", list)[0])
                            else:
                                try:
                                    _cpu = get_cpu_node(sdt, options)
                                    if _cpu.props("clock-frequency") != []:
                                        clk_hz = int(_cpu.propval("clock-frequency", list)[0])
                                except Exception:
                                    pass

                            zero_prescaler_indices = [i for i, p in enumerate(pit_prescaler) if p == 0]
                            pit_enabled_assigned = False
                            for idx in zero_prescaler_indices:
                                pit = LopperNode()
                                pit.name = f"timer{idx}"
                                pit.label_set(f"iomodule_pit{idx}")
                                pit + LopperProp(name="compatible", value=["xlnx,iomodule-pit"])
                                pit + LopperProp(name="xlnx,pit-timer-id", value=[idx])
                                size_val = pit_size[idx] if idx < len(pit_size) else 32
                                pit + LopperProp(name="xlnx,pit-size", value=[size_val])
                                pit + LopperProp(name="xlnx,pit-prescaler", value=[pit_prescaler[idx]])
                                readable_val = pit_readable[idx] if idx < len(pit_readable) else 1
                                if readable_val:
                                    pit + LopperProp("xlnx,pit-readable")
                                pit + LopperProp(name="xlnx,clock-freq", value=[clk_hz])
                                if pit_enabled_assigned:
                                    pit + LopperProp(name="status", value=["disabled"])
                                else:
                                    pit_enabled_assigned = True
                                node.add(pit)
                                _php = pit.phandle_or_create()
                                if pit.props("phandle") == []:
                                    pit + LopperProp(name="phandle", value=_php)

                            # ---- /aliases: serial0 -> UART ----
                            try:
                                sdt.tree.sync()
                            except Exception:
                                pass

                            uart_path = getattr(uart_node, "abs_path", None)
                            if not uart_path:
                                uart_path = f"{node.abs_path}/serial"

                            serial_alias = "serial0"
                            alias_node = sdt.tree["/aliases"]
                            if alias_node.props(serial_alias) != []:
                                alias_node[serial_alias].value = [uart_path]
                            else:
                                alias_node + LopperProp(name=serial_alias, value=[uart_path])
                            valid_alias_proplist.append("serial")

                            # ---- /chosen ----
                            chosen_node = sdt.tree["/chosen"]
                            _use_p = (
                                int(node.propval("xlnx,uart-use-parity", list)[0])
                                if node.props("xlnx,uart-use-parity") != []
                                else 0
                            )
                            if _use_p == 0:
                                parity_c = "n"
                            else:
                                _odd = (
                                    int(node.propval("xlnx,uart-odd-parity", list)[0])
                                    if node.props("xlnx,uart-odd-parity") != []
                                    else 0
                                )
                                parity_c = "o" if _odd else "e"
                            stdout_s = f"{serial_alias}:{baud}{parity_c}{int(data_bits)}"
                            if chosen_node.props("stdout-path") != []:
                                chosen_node["stdout-path"].value = [stdout_s]
                            else:
                                chosen_node + LopperProp(name="stdout-path", value=[stdout_s])
                            for _cn in ("zephyr,console", "zephyr,shell-uart"):
                                if chosen_node.props(_cn) != []:
                                    chosen_node[_cn].value = [serial_alias]
                                else:
                                    chosen_node + LopperProp(name=_cn, value=[serial_alias])
                        #AXI-ETHERNET-LITE
                        if any(v in node["compatible"].value for v in ("xlnx,axi-ethernetlite-3.0", "xlnx,xps-ethernetlite-1.00.a")):
                            node["compatible"].value = ["xlnx,xps-ethernetlite-3.00.a"]
                            for subnode in node.children():
                                node.delete(subnode)
                            emacnode = LopperNode()
                            emacnode["compatible"] = ["xlnx,xps-ethernetlite-3.00.a-mac"]
                            if node.propval('interrupt-parent') != ['']:
                                emacnode["interrupt-parent"] = node["interrupt-parent"]
                            if node.propval('interrupts') != ['']:
                                emacnode["interrupts"] = node["interrupts"].value
                            mac_prop = LopperProp(name="local-mac-address")
                            mac_prop.value = node["local-mac-address"].value
                            mac_prop.binary = True
                            emacnode + mac_prop
                            if node.propval('xlnx,rx-ping-pong') != [''] and node.propval('xlnx,rx-ping-pong', list)[0] == 1:
                                emacnode + LopperProp("xlnx,rx-ping-pong")
                            if node.propval('xlnx,tx-ping-pong') != [''] and node.propval('xlnx,tx-ping-pong', list)[0] == 1:
                                emacnode + LopperProp("xlnx,tx-ping-pong")
                            emacnode["status"] = "okay"
                            emacnode.name = "axi-ethernet-lite-mac"
                            emacnode.label_set(f"{node.label}_mac")
                            node.add(emacnode)
                        # UARTNS550: compatible rename + clock-frequency + reg-shift
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
                        # Collect all the axi-timebase-wdt nodes
                        if any(version in node["compatible"].value for version in ("xlnx,axi-timebase-wdt-3.0", "xlnx,xps-timebase-wdt-1.00.a")):
                            axi_wdt_nodes.append(node)
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
                    original_isa_string = isa  # Store original ISA string before modification

                    ''' Parse isa string and generate Kconfig.soc
                        and Kconfig.defconfig based on that 
                    ''' 

                    soc_kconfig.write(str(license_content))
                    soc_kconfig.write("config SOC_MBV32\n")

                    soc_kconfig.write("\tselect RISCV\n")
                    soc_kconfig.write("\tselect ATOMIC_OPERATIONS_C\n")
                    soc_kconfig.write("\tselect INCLUDE_RESET_VECTOR\n")

                    data_dict={'_zicsr':"\tselect RISCV_ISA_EXT_ZICSR\n",
                               '_zifencei':"\tselect RISCV_ISA_EXT_ZIFENCEI\n",
                               '_zba':"\tselect RISCV_ISA_EXT_ZBA\n",
                               '_zbb':"\tselect RISCV_ISA_EXT_ZBB\n",
                               '_zbc':"\tselect RISCV_ISA_EXT_ZBC\n",
                               '_zbs':"\tselect RISCV_ISA_EXT_ZBS\n",
                    }
                    for key, value in data_dict.items():
                        if isa.find(key) != -1:
                            soc_kconfig.write(value)

                    isa = isa.split('_')[0]

                    data_dict={'rv32i':"\tselect RISCV_ISA_RV32I\n",
                        'm':"\tselect RISCV_ISA_EXT_M\n",
                        'a':"\tselect RISCV_ISA_EXT_A\n",
                        'c':"\tselect RISCV_ISA_EXT_C\n",
                        'f':"\tselect RISCV_ISA_EXT_F\n",
                        'd':"\tselect RISCV_ISA_EXT_D\n",
                    }
                    for key, value in data_dict.items():
                        if isa.find(key) != -1:
                            soc_kconfig.write(value)

                    soc_kconfig.write(str(fix_part))

                    soc_kconfig.close()

                    soc_defconfig_file = os.path.join(sdt.outdir, "Kconfig.defconfig")
                    defconfig_kconfig = open(soc_defconfig_file, 'a')
                    
                    defconfig_kconfig.write(str(license_content))
                    defconfig_kconfig.write("if SOC_MBV32\n\n")
                    defconfig_kconfig.write("config MBV_CSR_DATA_WIDTH\n")
                    defconfig_kconfig.write("\tint \"Select Control/Status register width\"\n")
                    defconfig_kconfig.write("\tdefault 32\n\n")

                    val = node.propval('xlnx,pmp-entries', list)[0]
                    if val % 8 == 0 and val != 0:
                        soc_kconfig = open(soc_kconfig_file, 'a')
                        soc_kconfig.write("\tselect RISCV_PMP\n")
                        soc_kconfig.close()

                        defconfig_kconfig.write("config PMP_SLOTS\n")
                        defconfig_kconfig.write("\tdefault %s\n\n" % str(val))

                        val = node.propval('xlnx,pmp-granularity', list)[0]
                        defconfig_kconfig.write("config PMP_GRANULARITY\n")
                        val = pow(val + 2, 2)
                        defconfig_kconfig.write("\tdefault %s\n\n" % str(val))

                    # Add NUM_IRQS configuration
                    if num_intr:
                        defconfig_kconfig.write("config NUM_IRQS\n")
                        defconfig_kconfig.write("\tdefault %s\n\n" % str(num_intr))

                    # Add custom CPU idle capability
                    defconfig_kconfig.write("config ARCH_HAS_CUSTOM_CPU_IDLE\n")
                    defconfig_kconfig.write("\tdefault y\n\n")
                    # Add custom atomic CPU idle capability
                    defconfig_kconfig.write("config ARCH_HAS_CUSTOM_CPU_ATOMIC_IDLE\n")
                    defconfig_kconfig.write("\tdefault y\n\n")

                    # Add SYS_CLOCK_HW_CYCLES_PER_SEC at the end
                    val = node.propval('clock-frequency', list)[0]
                    defconfig_kconfig.write("config SYS_CLOCK_HW_CYCLES_PER_SEC\n")
                    defconfig_kconfig.write("\tdefault $(dt_node_int_prop_int,/cpus/cpu@0,clock-frequency)\n\n")

                    defconfig_kconfig.write("endif # SOC_MBV32\n")

                    defconfig_kconfig.close()
                    board_defconfig_content = generate_board_kconfig_defconfig(original_isa_string, node, is_axi_intc_present, num_intr)
                    board_defconfig_file = os.path.join(sdt.outdir, f"board_Kconfig.defconfig")
                    with open(board_defconfig_file, 'w') as board_defconfig:
                        board_defconfig.write(board_defconfig_content)

    if sdt.tree['/chosen'].propval('zephyr,sram') == ['']:
        sdt.tree['/chosen'] + LopperProp(name="zephyr,sram", value = sram_node)

    # Zephyr Watchdog samples/tests expects watchdog0 alias.
    # Add watchdog0 alias by referring it to the first occurence of the axi-timebase-wdt node
    if axi_wdt_nodes:
        axi_wdt_node = sdt.tree.pnode(axi_wdt_nodes[0].phandle)
        sdt.tree['/aliases'] + LopperProp(name="watchdog0", value = axi_wdt_node.abs_path)

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
