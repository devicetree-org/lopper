#/*
# * Copyright (c) 2020 Xilinx Inc. All rights reserved.
# *
# * Author:
# *       Appana Durga Kedareswara rao <appana.durga.rao@xilinx.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

import sys
import os
import re
import lopper_lib

sys.path.append(os.path.dirname(__file__))
from baremetalconfig_xlnx import scan_reg_size, get_cpu_node
import common_utils as utils
from common_utils import to_cmakelist
from domain_access import domain_get_subnodes

def is_compat( node, compat_string_to_test ):
    if re.search( "module,baremetallinker_xlnx", compat_string_to_test):
        return xlnx_generate_bm_linker
    return ""


# tgt_node: is the baremetal config top level domain node number
# sdt: is the system device-tree
# options: baremetal application source path
def get_memranges(tgt_node, sdt, options):
    root_node = sdt.tree[tgt_node]
    root_sub_nodes = root_node.subnodes()
    mem_nodes = []

    domain_nodes = domain_get_subnodes(sdt.tree)
    # Get the Baremetal Domain
    for domain_node in domain_nodes:
        bm_domain = []
        if domain_node.propval('os,type') != ['']:
            if re.search('baremetal', domain_node.propval('os,type', list)[0]):
                    bm_domain.append(domain_node)
        elif domain_node.propval('cpus') != ['']:
                print('ERROR: os,type property is missing in the domain', domain_node.name)

        match_cpunode = get_cpu_node(sdt, options)

        # Check whether domain is mapped for current processor or not
        if bm_domain:
            shared_mem = []
            if not domain_node['cpus'][0] == match_cpunode.parent.phandle:
                continue
            else:
               if domain_node.propval('include') != ['']:
                   rsc_domain = lopper_lib.includes( sdt.tree, domain_node['include'] )
                   for rscdomain in rsc_domain:
                       if rscdomain.propval('memory') != ['']:
                           shared_mem.extend(rscdomain.propval('memory'))

            mem_nodes = sdt.tree.nodes('/memory@.*')
            for node in mem_nodes:
                update_shared_mem = []
                reg, size = scan_reg_size(node, node['reg'].value, 0)
                if shared_mem:
                    start_addr = shared_mem[0]
                    if start_addr in range(reg, reg+size):
                        update_shared_mem.extend(shared_mem)

                if domain_node.propval('memory') != ['']:
                    start_addr = domain_node['memory'].value[0]
                    if start_addr in range(reg, reg+size):
                        if update_shared_mem:
                            update_shared_mem.extend(domain_node['memory'].value)
                            modify_val = update_mem_node(node, update_shared_mem)
                        else:
                            modify_val = update_mem_node(node, domain_node['memory'].value)
                        node['reg'].value = modify_val
                else:
                    if update_shared_mem:
                        modify_val = update_mem_node(node, update_shared_mem)
                        node['reg'].value = modify_val

    #Maintain a static memory IP list this is needed inorder to capture proper ip name in the linker script
    xlnx_memipname = {"axi_bram": 0, "ps7_ddr": 0, "psu_ddr": 0, "psv_ddr": 0, "mig": 0, "lmb_bram": 0, "axi_noc2": 0, "axi_noc": 0,"psu_ocm": 0,  "psv_ocm": 0, "psx_ocm": 0, "ddr4": 0, "ddr5": 0, "mig_7series": 0, "ps7_ram": 0}
    for node in root_sub_nodes:
        try:
            device_type = node["device_type"].value
            if "memory" in device_type:
                mem_nodes.append(node)
        except:
           pass

    versal_noc_ch_ranges =  {"DDR_CH_1": "0x50000000000", "DDR_CH_2": "0x60000000000", "DDR_CH_3": "0x70000000000"}

    # Yocto Machine to CPU compat mapping
    match_cpunode = get_cpu_node(sdt, options)
    if not match_cpunode:
        return
    address_map = match_cpunode.parent["address-map"].value
    all_phandles = []
    ns = match_cpunode.parent["#ranges-size-cells"].value[0]
    na = match_cpunode.parent["#ranges-address-cells"].value[0]
    cells = na + ns
    tmp = na
    while tmp < len(address_map):
        all_phandles.append(address_map[tmp])
        tmp = tmp + cells + na + 1

    mem_ranges = {}
    # Remove Duplicate memory node referenecs
    mem_nodes = list(dict.fromkeys(mem_nodes))
    for node in mem_nodes:
        # Check whether the memory node is mapped to cpu cluster or not
        mem_phandles = [handle for handle in all_phandles if handle == node.phandle]
        addr_list = []
        size_list = []
        if mem_phandles:
           # Remove Duplicate phandle referenecs
           mem_phandles = list(dict.fromkeys(mem_phandles))
           indx_list = [index for index,handle in enumerate(address_map) for val in mem_phandles if handle == val]
           for inx in indx_list:
               start = [address_map[inx+i+1] for i in range(na)]
               size_list.append(address_map[inx+2*na])
               if na == 2 and start[0] != 0:
                   val = str(start[1])
                   pad = 8 - len(val)
                   val = val.ljust(pad + len(val), '0')
                   reg = int((str(hex(start[0])) + val), base=16)
                   addr_list.append(reg)
               elif na == 2:
                   addr_list.append(start[1])
               else:
                   addr_list.append(start[0])

        nac = node.parent["#address-cells"].value[0]
        nsc = node.parent["#size-cells"].value[0]
        val = node['reg'].value
        total_nodes = int(len(val)/(nac+nsc))
        name_list = [name.replace("_", "-") for name in list(xlnx_memipname.keys())]
        try:
            compat = node['compatible'].value[0]
            match = [mem for mem in name_list if mem in compat]
            for i in range(total_nodes):
                reg, size = scan_reg_size(node, val, i)
                valid_range = [addr for addr in addr_list if reg == addr or addr in range(reg, size-reg)]
                if not valid_range:
                    valid_range = [reg for index, addr in enumerate(addr_list) if reg in range(addr, size_list[index]-addr)]
                if valid_range:
                    key = match[0].replace("-", "_")
                    is_valid_noc_ch = 0
                    if "axi_noc" in key:
                        for ch_name, ran in sorted(versal_noc_ch_ranges.items(), reverse=True):
                            if int(ran, base=16) <= int(hex(valid_range[0]), base=16):
                                is_valid_noc_ch = ch_name
                                break

                    if is_valid_noc_ch:
                        linker_secname = key + str("_") + is_valid_noc_ch
                    else:
                        linker_secname = key + str("_") + str(xlnx_memipname[key])
                        xlnx_memipname[key] += 1
                    mem_ranges.update({linker_secname: [valid_range[0], size]})
        except KeyError:
            pass

    return mem_ranges

# tgt_node: is the baremetal config top level domain node number
# sdt: is the system device-tree
# options: baremetal application source path
def xlnx_generate_bm_linker(tgt_node, sdt, options):
    mem_ranges = get_memranges(tgt_node, sdt, options)
    default_ddr = None
    memtest_config = None
    machine = options['args'][0]

    try:
        memtest_config = options['args'][2]
    except IndexError:
        pass

    src_dir = options['args'][1]
    app_path = utils.get_dir_path(src_dir.rstrip(os.sep))
    appname = utils.get_base_name(app_path)
    yaml_file = os.path.join(app_path, "data", f"{appname}.yaml")
    match_cpunode = get_cpu_node(sdt, options)
    if match_cpunode.propval('xlnx,ip-name') != ['']:
        ip_name = match_cpunode.propval('xlnx,ip-name', list)[0]
        if "microblaze" in ip_name:
            stack_size = 0x400
            heap_size = 0x400
        else:
            stack_size = 0x2000
            heap_size = 0x2000
    else:
        stack_size = None
        heap_size = None
    if not utils.is_file(yaml_file):
        print(f"{appname} doesn't have yaml file")
    else:
        schema = utils.load_yaml(yaml_file)
        if schema.get("linker_constraints"):
            linker_constraint_opts = list(schema["linker_constraints"].keys())
            if "stack" in linker_constraint_opts:
                stack_size = schema["linker_constraints"]["stack"]
            if "heap" in linker_constraint_opts:
                heap_size = schema["linker_constraints"]["heap"]

    cmake_file = os.path.join(sdt.outdir, f"{appname.capitalize()}Example.cmake")
    cfd = open(cmake_file, 'a')
    if memtest_config:
        traverse = False
    else:
        traverse = True

    mem_sec = ""
    """
    For cortexr5 processor in ZynqMP and Versal SOC TCM memory map is fixed
    update the memory section for the same.
    """
    if "psu_cortexr5" in machine:
        mem_sec += '\n\tpsu_r5_0_atcm_MEM_0 : ORIGIN = 0x0, LENGTH = 0x10000'
        mem_sec += '\n\tpsu_r5_0_btcm_MEM_0 : ORIGIN = 0x20000, LENGTH = 0x10000'
        mem_sec += '\n\tpsu_r5_tcm_ram_0_MEM_0 : ORIGIN = 0x0, LENGTH = 0x40000'
    if "psv_cortexr5" in machine:
        mem_sec += '\n\tpsv_r5_0_atcm_MEM_0 : ORIGIN = 0x0, LENGTH = 0x10000'
        mem_sec += '\n\tpsv_r5_0_atcm_lockstep_MEM_0 : ORIGIN = 0xFFE10000, LENGTH = 0x10000'
        mem_sec += '\n\tpsv_r5_0_btcm_MEM_0 : ORIGIN = 0x20000, LENGTH = 0x10000'
        mem_sec += '\n\tpsv_r5_0_btcm_lockstep_MEM_0 : ORIGIN = 0xFFE30000, LENGTH = 0x10000'
        mem_sec += '\n\tpsv_pmc_ram_psv_pmc_ram : ORIGIN = 0xF2000000, LENGTH = 0x20000'
        mem_sec += '\n\tpsv_r5_0_data_cache_MEM_0 : ORIGIN = 0xFFE50000, LENGTH = 0x10000'
        mem_sec += '\n\tpsv_r5_0_instruction_cache_MEM_0 : ORIGIN = 0xFFE40000, LENGTH = 0x10000'
        mem_sec += '\n\tpsv_r5_1_data_cache_MEM_0 : ORIGIN = 0xFFED0000, LENGTH = 0x10000'
        mem_sec += '\n\tpsv_r5_1_instruction_cache_MEM_0 : ORIGIN = 0xFFEC0000, LENGTH = 0x10000'
        mem_sec += '\n\tpsv_r5_0_atcm_global_MEM_0 : ORIGIN = 0xFFE00000, LENGTH = 0x10000'
        mem_sec += '\n\tpsv_r5_1_atcm_global_MEM_0 : ORIGIN = 0xFFE90000, LENGTH = 0x10000'
        mem_sec += '\n\tpsv_r5_0_btcm_global_MEM_0 : ORIGIN = 0xFFE20000, LENGTH = 0x10000'
        mem_sec += '\n\tpsv_r5_1_btcm_global_MEM_0 : ORIGIN = 0xFFEB0000, LENGTH = 0x10000'
    if "psv_cortexa72" in machine:
        mem_sec += '\n\tpsv_pmc_ram_psv_pmc_ram : ORIGIN = 0xF2000000, LENGTH = 0x20000'
        mem_sec += '\n\tpsv_r5_0_atcm_global_MEM_0 : ORIGIN = 0xFFE00000, LENGTH = 0x40000'
        mem_sec += '\n\tpsv_r5_1_atcm_global_MEM_0 : ORIGIN = 0xFFE90000, LENGTH = 0x10000'
        mem_sec += '\n\tpsv_r5_1_btcm_global_MEM_0 : ORIGIN = 0xFFEB0000, LENGTH = 0x10000'
    if "psx_cortexa78" in machine:
        mem_sec += '\n\tpsx_pmc_ram : ORIGIN = 0xF2000000, LENGTH = 0x20000'
        mem_sec += '\n\tpsx_r52_0a_atcm_global : ORIGIN = 0xEBA00000, LENGTH = 0x10000'
        mem_sec += '\n\tpsx_r52_0a_btcm_global : ORIGIN = 0xEBA10000, LENGTH = 0x8000'
        mem_sec += '\n\tpsx_r52_0a_ctcm_global : ORIGIN = 0xEBA20000, LENGTH = 0x8000'
        mem_sec += '\n\tpsx_r52_1a_atcm_global : ORIGIN = 0xEBA40000, LENGTH = 0x10000'
        mem_sec += '\n\tpsx_r52_1a_btcm_global : ORIGIN = 0xEBA50000, LENGTH = 0x8000'
        mem_sec += '\n\tpsx_r52_1a_ctcm_global : ORIGIN = 0xEBA60000, LENGTH = 0x8000'
        mem_sec += '\n\tpsx_r52_0b_atcm_global : ORIGIN = 0xEBA80000, LENGTH = 0x10000'
        mem_sec += '\n\tpsx_r52_0b_btcm_global : ORIGIN = 0xEBA90000, LENGTH = 0x8000'
        mem_sec += '\n\tpsx_r52_0b_ctcm_global : ORIGIN = 0xEBAA0000, LENGTH = 0x8000'
        mem_sec += '\n\tpsx_r52_1b_atcm_global : ORIGIN = 0xEBAC0000, LENGTH = 0x10000'
        mem_sec += '\n\tpsx_r52_1b_btcm_global : ORIGIN = 0xEBAD0000, LENGTH = 0x8000'
        mem_sec += '\n\tpsx_r52_1b_ctcm_global : ORIGIN = 0xEBAE0000, LENGTH = 0x8000'
    if "psx_cortexr52" in machine:
        mem_sec += '\n\tpsx_pmc_ram : ORIGIN = 0xF2000000, LENGTH = 0x20000'
        mem_sec += '\n\tpsx_r52_tcm_alias : ORIGIN = 0x0, LENGTH = 0x20000'

    for key, value in sorted(mem_ranges.items(), key=lambda e: e[1][1], reverse=traverse):
        if default_ddr is None:
            default_ddr = key
        start,size = value[0], value[1]
        """
        LMB BRAM initial 80 bytes being used by the linker vectors section
        Adjust the size and start address accordingly.
        """
        if "lmb_bram" in key:
            start = 80
            size -= start
        """
        For R5 PSU DDR initial 1MB is reserved for tcm
        Adjust the size and start address accordingly.
        """
        if "psu_ddr" in key and machine == "cortexr5-zynqmp" and start == 0:
            start = 1048576
            size -= start
        if "axi_noc" in key and machine == "cortexr5-versal" and start == 0:
            start = 1048576
            size -= start
        mem_sec += f'\n\t{key} : ORIGIN = {hex(start)}, LENGTH = {hex(size)}'

    ## To inline with existing tools point default ddr for linker to lower DDR
    lower_ddrs = ["axi_noc_0", "psu_ddr_0", "ps7_ddr_0"]
    has_ddr = [x for x in mem_ranges.keys() for ddr in lower_ddrs if re.search(ddr, x)]
    if has_ddr and not memtest_config:
        default_ddr = has_ddr[0]
    has_ocm = None
    has_ram = None
    ## For memory tests configuration default memory should be ocm if available
    if memtest_config:
        has_ocm = [x for x in mem_ranges.keys() if "ocm" in x]
        has_ram = [x for x in mem_ranges.keys() if "ram" in x]
        if has_ocm:
            default_ddr = has_ocm[0]
        elif has_ram:
            default_ddr = has_ram[0]

    cfd.write("set(DDR %s)\n" % default_ddr)
    memip_list = []
    for key, value in sorted(mem_ranges.items(), key=lambda e: e[1][1], reverse=traverse):
        start,size = value[0], value[1]
        """
        LMB BRAM initial 80 bytes being used by the linker vectors section
        Adjust the size and start address accordingly.
        """
        if "lmb_bram" in key:
            start = 80
            size -= start
        memip_list.append(key)
        cfd.write("set(%s %s)\n" % (key, to_cmakelist([hex(start), hex(size)])))
    if memtest_config:
        if has_ocm:
            memip_list.insert(0, memip_list.pop(memip_list.index(has_ocm[0])))
        elif has_ram:
            memip_list.insert(0, memip_list.pop(memip_list.index(has_ram[0])))
    cfd.write("set(TOTAL_MEM_CONTROLLERS %s)\n" % to_cmakelist(memip_list))
    cfd.write(f'set(MEMORY_SECTION "MEMORY\n{{{mem_sec}\n}}")\n')
    if stack_size is not None:
        cfd.write(f'set(STACK_SIZE {hex(stack_size)})\n')
    if heap_size is not None:
        cfd.write(f'set(HEAP_SIZE {hex(heap_size)})\n')

    return True
