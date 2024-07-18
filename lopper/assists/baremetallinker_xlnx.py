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
from openamp_xlnx import xlnx_openamp_get_ddr_elf_load

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

    #Maintain a static memory IP list this is needed inorder to capture proper ip name in the linker script
    xlnx_memipname = {
        "axi_bram": 0, "ps7_ddr": 0, "psu_ddr": 0,
        "psv_ddr": 0, "mig": 0, "lmb_bram": 0,
        "axi_noc2": 0, "axi_noc": 0,"psu_ocm": 0,
        "psv_ocm": 0, "psx_ocm": 0, "ddr4": 0,
        "ddr5": 0, "mig_7series": 0, "ps7_ram": 0,
        "axi_emc": 0, "psu_qspi_linear": 0, "ps7_qspi_linear": 0
    }
    for node in root_sub_nodes:
        try:
            device_type = node["device_type"].value
            if "memory" in device_type:
                mem_nodes.append(node)
        except:
           pass

    # Ensure that the region addresses are always in descending order of addresses
    # This order is necessary to employ the comparison while mapping the available regions.
    versal_noc_region_ranges =  {
        "0x70000000000": "DDR_CH_3", "0x60000000000": "DDR_CH_2", "0x50000000000": "DDR_CH_1",
        "0x10000000000": "DDR_LOW_3", "0xC000000000": "DDR_LOW_2",
        "0x47C0000000": "HBM15_PC1", "0x4780000000": "HBM15_PC0", "0x4740000000": "HBM14_PC1", "0x4700000000": "HBM14_PC0",
        "0x46C0000000": "HBM13_PC1", "0x4680000000": "HBM13_PC0", "0x4640000000": "HBM12_PC1", "0x4600000000": "HBM12_PC0",
        "0x45C0000000": "HBM11_PC1", "0x4580000000": "HBM11_PC0", "0x4540000000": "HBM10_PC1", "0x4500000000": "HBM10_PC0",
        "0x44C0000000": "HBM9_PC1", "0x4480000000": "HBM9_PC0", "0x4440000000": "HBM8_PC1", "0x4400000000": "HBM8_PC0",
        "0x43C0000000": "HBM7_PC1", "0x4380000000": "HBM7_PC0", "0x4340000000": "HBM6_PC1", "0x4300000000": "HBM6_PC0",
        "0x42C0000000": "HBM5_PC1", "0x4280000000": "HBM5_PC0", "0x4240000000": "HBM4_PC1", "0x4200000000": "HBM4_PC0",
        "0x41C0000000": "HBM3_PC1", "0x4180000000": "HBM3_PC0", "0x4140000000": "HBM2_PC1", "0x4100000000": "HBM2_PC0",
        "0x40C0000000": "HBM1_PC1", "0x4080000000": "HBM1_PC0", "0x4040000000": "HBM0_PC1", "0x4000000000": "HBM0_PC0",
        "0x800000000": "DDR_LOW_1",
        "0x0": "DDR_LOW_0"
    }

    # Ensure that the region addresses are always in descending order of addresses
    # This order is necessary to employ the comparison while mapping the available regions.
    versal_net_noc2_region_ranges = {
        "0x198000000000" : "DDR_CH_4",
        "0x190000000000" : "DDR_CH_3A",
        "0x188000000000" : "DDR_CH_3",
        "0x180000000000" : "DDR_CH_2A",
        "0x70000000000" : "DDR_CH_2",
        "0x60000000000" : "DDR_CH_1A",
        "0x50000000000" : "DDR_CH_1",
        "0x10000000000" : "DDR_LOW_3",
        "0xC000000000" : "DDR_LOW_2",
        "0x800000000" : "DDR_LOW_1",
        "0x0" : "DDR_LOW_0"
    }

    noc_regions = versal_noc_region_ranges

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
               size_list.append(address_map[inx+2*na])
               if na == 2 and start[0] != 0:
                   reg = int(f"{hex(start[0])}{start[1]:08x}", base=16)
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
                        if "axi_noc2" in key:
                            noc_regions = versal_net_noc2_region_ranges
                        for region_addr_range in noc_regions.keys():
                            if int(region_addr_range, base=16) <= int(hex(valid_range[0]), base=16):
                                is_valid_noc_ch = noc_regions[region_addr_range]
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
    openamp_config = None
    machine = options['args'][0]

    try:
        extra_config = options['args'][2]
        if extra_config == "memtest":
            memtest_config = True
        if extra_config == "openamp":
            openamp_config = True

    except IndexError:
        pass

    src_dir = options['args'][1]
    app_path = utils.get_dir_path(src_dir.rstrip(os.sep))
    appname = utils.get_base_name(app_path)
    yaml_file = os.path.join(app_path, "data", f"{appname}.yaml")
    match_cpunode = get_cpu_node(sdt, options)
    cpu_ip_name = None
    stack_size = None
    heap_size = None
    if match_cpunode.propval('xlnx,ip-name') != ['']:
        cpu_ip_name = match_cpunode.propval('xlnx,ip-name', list)[0]

    if cpu_ip_name is not None:
        if "microblaze" in cpu_ip_name:
            stack_size = 0x400
            heap_size = 0x800
        else:
            stack_size = 0x2000
            heap_size = 0x2000

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

    openamp_elfload_start = False
    openamp_elfload_sz = False
    if openamp_config:
        elfload_tuple = xlnx_openamp_get_ddr_elf_load(machine, sdt, options)
        if elfload_tuple != None:
            openamp_elfload_start = elfload_tuple[0]
            openamp_elfload_sz = elfload_tuple[1]

    for key, value in sorted(mem_ranges.items(), key=lambda e: e[1][1], reverse=traverse):
        if default_ddr is None:
            default_ddr = key
        start,size = value[0], value[1]
        """
        Initial 80 bytes is being used by the linker vectors section in case of Microblaze.
        Adjust the size and start address accordingly.
        """
        if cpu_ip_name == "microblaze" and start < 80:
            start = 80
            size -= start
            if size <= 0x2000:
                heap_size = 0x400
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
        # Use OpenAMP DDR carveout in lieu of system DDR space if DDR OpenAMP carveout is present
        if openamp_elfload_sz and ("psu_ddr" in key or "axi_noc" in key):
                start = openamp_elfload_start
                size = openamp_elfload_sz
                cfd.write("set(RSC_TABLE %s)\n" % hex(openamp_elfload_start))

        mem_sec += f'\n\t{key} : ORIGIN = {hex(start)}, LENGTH = {hex(size)}'

    ## To inline with existing tools point default ddr for linker to lower DDR
    lower_ddrs = ["axi_noc", "psu_ddr_0", "ps7_ddr_0"]
    has_ddr = [x for x in mem_ranges.keys() for ddr in lower_ddrs if re.search(ddr, x)]
    if has_ddr and not memtest_config:
        default_ddr = has_ddr[0]
    has_ocm = None
    has_ram = None
    has_bram = None
    ## For memory tests configuration default memory should be ocm if available
    if memtest_config:
        has_ocm = [x for x in mem_ranges.keys() if "ocm" in x]
        has_ram = [x for x in mem_ranges.keys() if "_ram" in x]
        has_bram = [x for x in mem_ranges.keys() if "_bram" in x]
        if has_ocm:
            default_ddr = has_ocm[0]
        elif has_ram:
            default_ddr = has_ram[0]
        elif has_bram:
            if len(has_bram) > 1:
                size = 0
                for key, value in mem_ranges.items():
                    if "_bram" in key:
                        if size < value[1]:
                            size = value[1]
                            default_ddr = key
            else:
                default_ddr = has_bram[0]

    cfd.write("set(DDR %s)\n" % default_ddr)
    memip_list = []
    for key, value in sorted(mem_ranges.items(), key=lambda e: e[1][1], reverse=traverse):
        # Here skip adding DDR system mem to config if OpenAMP module is set
        if "psu_qspi_linear" in key or "ps7_qspi_linear" in key or "axi_emc" in key or openamp_elfload_sz:
            continue
        start,size = value[0], value[1]
        """
        LMB BRAM initial 80 bytes being used by the linker vectors section in case of Microblaze
        Adjust the size and start address accordingly.
        """
        if "lmb_bram" in key and not "microblaze_riscv" in machine and not "cortex" in machine:
            start += 80
            size -= start
        memip_list.append(key)
        cfd.write("set(%s %s)\n" % (key, to_cmakelist([hex(start), hex(size)])))
    if memtest_config:
        if has_ocm:
            memip_list.insert(0, memip_list.pop(memip_list.index(has_ocm[0])))
        elif has_ram:
            memip_list.insert(0, memip_list.pop(memip_list.index(has_ram[0])))
        elif has_bram:
            memip_list.insert(0, memip_list.pop(memip_list.index(default_ddr)))
    cfd.write("set(TOTAL_MEM_CONTROLLERS %s)\n" % to_cmakelist(memip_list))
    cfd.write(f'set(MEMORY_SECTION "MEMORY\n{{{mem_sec}\n}}")\n')
    if stack_size is not None:
        cfd.write(f'set(STACK_SIZE {hex(stack_size)})\n')
    if heap_size is not None:
        cfd.write(f'set(HEAP_SIZE {hex(heap_size)})\n')

    return True
