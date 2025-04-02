#/*
# * Copyright (c) 2020 Xilinx Inc. All rights reserved.
# * Copyright (c) 2024 - 2025 Advanced Micro Devices, Inc.  All rights reserved.
# *
# * Author:
# *       Appana Durga Kedareswara rao <appana.durga.kedareswara.rao@amd.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

import sys
import os
import re
import glob
import logging
import common_utils as utils
import baremetalconfig_xlnx as bm_config

from baremetaldrvlist_xlnx import xlnx_generate_bm_drvlist
from baremetallinker_xlnx import get_memranges
from lopper.log import _init, _warning, _info, _error, _debug, _level, __logger__

sys.path.append(os.path.dirname(__file__))

_init(__name__)

def is_compat( node, compat_string_to_test ):
    if re.search( "module,baremetal_xparameters_xlnx", compat_string_to_test):
        return xlnx_generate_xparams
    return ""

def add_multi_buf(plat,match_cpunode,data_dict,else_ignore_data=[]):
    """To add multi buf data to the plat
    Args:
        plat(obj) : object of a plat
        match_cpunode(obj) : object of a cpu node
        data_dict(dict) : data to be added to buf
        else_ignore_data(list) : ignore the else part
    Returns:
        None
    """
    for key,value in data_dict.items():
        if match_cpunode.propval(key) != ['']:
            data = match_cpunode.propval(key, list)[0]
            plat.buf(f'\n#define {value} {data}\n')
        else:
            if key not in else_ignore_data:
                _warning(f'Property "{key}" not found in CPU node. Defaulting {value} to 0.')
                plat.buf(f'\n#define {value} 0\n')

def xlnx_generate_xparams(tgt_node, sdt, options):
    _level(utils.log_setup(options), __name__)
    root_node = sdt.tree[tgt_node]
    root_sub_nodes = root_node.subnodes()
    if options.get('outdir', {}):
        sdt.outdir = options['outdir']

    node_dict = {}
    node_list = []
    # Traverse the tree and find the nodes having status=ok property
    symbol_node = ""
    chosen_node = ""
    for node in root_sub_nodes:
        try:
            if node.name == "chosen":
                chosen_node = node
            if node.name == "__symbols__":
                symbol_node = node
            status = node["status"].value
            if "okay" in status:
                node_list.append(node)
                compat = node['compatible'].value
                node_dict.update({node.abs_path:compat})
        except:
           pass

    repo_path_data = options['args'][1]
    is_fpd_coherent = None
    is_lpd_coherent = None
    is_pl_coherent = None
    fpd_master_list = ["psu_sata","psu_pcie"]
    lpd_master_list = ["psu_adma","psu_qspi","psu_nand","psu_sd","psu_ethernet","psu_cortexr5","psu_usb"]
    for node in node_list:
        try:
            prop_val = node['dma-coherent'].value
            if '' in prop_val:
                cci_en = True
        except:
            cci_en = None
        if cci_en:
            ip_name = node.propval('xlnx,ip-name', list)[0]
            if ip_name in fpd_master_list:
                is_fpd_coherent = True
            if ip_name in lpd_master_list:
                is_lpd_coherent = True
            if re.search("amba_pl", node.parent.name):
                is_pl_coherent = True
    drvlist = xlnx_generate_bm_drvlist(tgt_node, sdt, options)
    xparams = os.path.join(sdt.outdir, f"xparameters.h")
    plat = bm_config.DtbtoCStruct(xparams)
    plat.buf('#ifndef XPARAMETERS_H   /* prevent circular inclusions */\n')
    plat.buf('#define XPARAMETERS_H   /* by using protection macros */\n')
    total_nodes = node_list
    for drv in drvlist:
        if utils.is_file(repo_path_data):
            repo_schema = utils.load_yaml(repo_path_data)
            drv_data = repo_schema['driver']
            drv_dir = drv_data.get(drv,{}).get('vless','')
            if not drv_dir and drv_data.get(drv,{}).get('path',''):
                drv_dir = drv_data.get(drv,{}).get('path','')[0]
        else:
            _warning(f"YAML file not found at path {repo_path_data}, falling back to default path.")
            drv_dir = os.path.join(repo_path_data, "XilinxProcessorIPLib", "drivers", drv)

        if not drv_dir:
            has_drivers = [dir_name for dir_name in os.listdir(utils.get_dir_path(sdt.dts)) if "drivers" in dir_name]
            if has_drivers:
                has_drivers = os.path.join(utils.get_dir_path(sdt.dts), "drivers")
                yaml_list = glob.glob(has_drivers + '/**/data/*.yaml', recursive=True)
                yaml_file_abs = [yaml for yaml in yaml_list if f"{drv}.yaml" in yaml]
                if yaml_file_abs:
                    yaml_file_abs = yaml_file_abs[0]
        else:
            yaml_file_abs = os.path.join(drv_dir, "data", f"{drv}.yaml")

        if utils.is_file(yaml_file_abs):
            schema = utils.load_yaml(yaml_file_abs)
            driver_compatlist = bm_config.compat_list(schema)
            driver_proplist = schema.get('required',{})
            if schema.get('additionalProperties', {}):
                if driver_proplist:
                    driver_proplist.extend(schema.get('additionalProperties',{}))
                else:
                    driver_proplist = schema.get('additionalProperties',{})
            match_nodes = []
            for node,compatible_list in node_dict.items():
                for comp in driver_compatlist:
                   match = [x for x in compatible_list if comp == x]
                   if match:
                       node1 = [x for x in node_list if (x.abs_path == node)]
                       node_list = [x for x in node_list if not(x.abs_path == node)]
                       if node1:
                           match_nodes.append(node1[0])
            if sdt.tree[tgt_node].propval('pruned-sdt') == ['']:
                match_nodes = bm_config.get_mapped_nodes(sdt, match_nodes, options)
            canonical_name = f"X{drv.upper()}"
            if schema.get('xparam_prefix',""):
                canonical_name = schema["xparam_prefix"].upper()

            for index, node in enumerate(match_nodes):
                label_name = bm_config.get_label(sdt, symbol_node, node)
                label_name = label_name.upper()
                canondef_dict = {}
                if index == 0:
                    plat.buf(f'\n#define XPAR_{canonical_name}_NUM_INSTANCES {len(match_nodes)}\n')
                for i, prop in enumerate(driver_proplist):
                    pad = 0
                    phandle_prop = 0
                    subnode_gen = 0
                    if isinstance(prop, dict):
                        pad = list(prop.values())[0]
                        prop = list(prop.keys())[0]
                        if isinstance(pad, dict):
                            subnode_prop = prop
                            prop = list(pad.keys())[0]
                            subnode_prop_list = list(pad.values())[0]
                            if prop == "subnode_phandle":
                                subnode_gen = 1
                                pad = 0
                                phandle_prop = 0
                        if pad == "phandle":
                            phandle_prop = 1

                    if i == 0:
                        plat.buf(f'\n/* Definitions for peripheral {label_name} */')

                    if prop == "reg":
                        try:
                            val, size = bm_config.scan_reg_size(node, node[prop].value, 0)
                            plat.buf(f'\n#define XPAR_{label_name}_BASEADDR {hex(val)}')
                            plat.buf(f'\n#define XPAR_{label_name}_HIGHADDR {hex(val + size -1)}')
                            canondef_dict.update({"BASEADDR":hex(val)})
                            canondef_dict.update({"HIGHADDR":hex(val + size - 1)})
                            """
                            Generate defines for all the available baseaddresses
                            """
                            na = node.parent["#address-cells"].value[0]
                            ns = node.parent["#size-cells"].value[0]
                            cells = na + ns
                            num_of_addr = int(len(node[prop].value)/cells)
                            if num_of_addr > 1:
                                for j in range(1, num_of_addr):
                                    try:
                                        val, size = bm_config.scan_reg_size(node, node[prop].value, j)
                                        plat.buf(f'\n#define XPAR_{label_name}_BASEADDR_{j} {hex(val)}')
                                    except IndexError:
                                        pass
                        except KeyError:
                            pass
                    elif prop == "compatible":
                        plat.buf(f'\n#define XPAR_{label_name}_{prop.upper()} "{node[prop].value[0]}"')
                        canondef_dict.update({prop:f'"{node[prop].value[0]}"'})
                    elif prop == "interrupts":
                        try:
                            intr = bm_config.get_interrupt_prop(sdt, node, node[prop].value)
                            plat.buf(f'\n#define XPAR_{label_name}_{prop.upper()} {intr[0]}')
                            canondef_dict.update({prop:intr[0]})
                        except KeyError:
                            _warning(f"Get interrupt prop is failed {node.name}, adding default value as {hex(0xFFFF)}")
                            intr = [0xFFFF]

                        """
                        Generate interrupt defines for all the interrupts
                        """
                        try:
                            inp =  node['interrupt-parent'].value[0]
                            intr_parent = [node for node in sdt.tree['/'].subnodes() if node.phandle == inp]
                            inc = intr_parent[0]["#interrupt-cells"].value[0]
                            num_of_intrs = int(len(node[prop].value)/inc)
                        except KeyError:
                            _warning(f"Get interrupt parent is failed {node.name}, adding default value as {0}")
                            num_of_intrs = 0
                        if num_of_intrs > 1:
                            for j in range(1, num_of_intrs):
                                try:
                                    plat.buf(f'\n#define XPAR_{label_name}_{prop.upper()}_{j} {intr[j]}')
                                except IndexError:
                                    pass

                        try:
                            intr_id = bm_config.get_interrupt_id(sdt, node, node[prop].value)
                            if node.parent.name != "":
                                if node.parent.name == "amba_pl":
                                    plat.buf(f'\n#define XPAR_FABRIC_{label_name}_INTR {intr_id[0]}')
                                    canondef_dict.update({"FABRIC":intr_id[0]})
                                    if num_of_intrs > 1:
                                        for j in range(1, num_of_intrs):
                                            plat.buf(f'\n#define XPAR_FABRIC_{label_name}_INTR_{j} {intr_id[j]}')
                                else:
                                    if node.propval('xlnx,ip-name') != ['']:
                                        ip_name = node.propval('xlnx,ip-name', list)[0]
                                        if drv == "ipipsu":
                                            plat.buf(f'\n#define XPAR_{label_name}_INTR {hex(intr_id[0]+32)}')
                                            canondef_dict.update({"INTR":hex(intr_id[0]+32)})
                        except KeyError:
                            _warning(f"Get interrupt id is failed {node.name}, adding default value as {[0xFFFF]}")
                            intr_id = [0xFFFF]
                            continue

                    elif prop == "interrupt-parent":
                        try:
                            intr_parent = bm_config.get_intrerrupt_parent(sdt, node[prop].value)
                            prop = prop.replace("-", "_")
                            plat.buf(f'\n#define XPAR_{label_name}_{prop.upper()} {hex(intr_parent)}')
                            canondef_dict.update({prop:hex(intr_parent)})
                        except KeyError:
                            pass
                    elif prop == "clocks":
                        clkprop_val = bm_config.get_clock_prop(sdt, node[prop].value)
                        plat.buf(f'\n#define XPAR_{label_name}_{prop.upper()} {hex(clkprop_val)}')
                        canondef_dict.update({prop:hex(clkprop_val)})
                    elif prop == "child,required":
                        ipi_target_list = []
                        for j,child in enumerate(list(node.child_nodes.items())):
                            ipi_child_dict = {}
                            for k,p in enumerate(pad):
                                if type(p) is dict:
                                    break
                                if p == "nosub":
                                    continue
                                try:
                                    val = hex(child[1][p].value[0])
                                except KeyError:
                                    _warning(f"Get ipi child value is failed {node.name}, adding default value as {0xFFFF}")
                                    val = 0xFFFF
                                p = p.replace("-", "_")
                                p = p.replace("xlnx,", "")
                                plat.buf(f'\n#define XPAR_{label_name}_{j}_{p.upper()} {val}')
                                if drv == "ipipsu":
                                    ipi_child_dict[p.upper()]=val
                            if ipi_child_dict:
                                ipi_target_list.append(ipi_child_dict)
                    elif phandle_prop:
                        try:
                            prop_val = bm_config.get_phandle_regprop(sdt, prop, node[prop].value)
                            prop = prop.replace("-", "_")
                            plat.buf(f'\n#define XPAR_{label_name}_{prop.upper()} {hex(prop_val)}')
                            canondef_dict.update({prop:hex(prop_val)})
                        except KeyError:
                            pass
                    elif subnode_gen:
                        try:
                            phandle_value = node[subnode_prop].value[0]
                            prop_val = 1
                            subnode_prop = subnode_prop.replace("-", "_")
                            plat.buf(f'\n#define XPAR_{label_name}_{subnode_prop.upper()} {hex(prop_val)}')
                            canondef_dict.update({subnode_prop:hex(prop_val)})
                        except KeyError:
                            pass
                    elif prop == "ranges":
                        try:
                            device_type = node['device_type'].value[0]
                            if device_type == "pci":
                                device_ispci = 1
                        except KeyError:
                            _warning(f"Get device type is failed {node.name}, adding default value as {0}")
                            device_ispci = 0
                        if device_ispci:
                            prop_vallist = bm_config.get_pci_ranges(node, node[prop].value, pad)
                            i = 0
                            for j, prop_val in enumerate(prop_vallist):
                                if j % 2:
                                    plat.buf(f'\n#define XPAR_{label_name}_{prop.upper()}_HIGHADDR_{i} {prop_val}')
                                    cannon_prop = prop + str("_") + str("HIGHADDR") + str("_") + str(i)
                                    canondef_dict.update({cannon_prop:prop_val})
                                    i += 1
                                else:
                                    plat.buf(f'\n#define XPAR_{label_name}_{prop.upper()}_BASEADDR_{i} {prop_val}')
                                    cannon_prop = prop + str("_") + str("BASEADDR") + str("_") + str(i)
                                    canondef_dict.update({cannon_prop:prop_val})
                        # Add special handling for Zynq NAND
                        elif "arm,pl353-smc-r2p1" in node.propval('compatible'):
                            val = node[prop].value[2]
                            size = node[prop].value[3]
                            plat.buf(f'\n#define XPAR_{label_name}_BASEADDR {hex(val)}')
                            plat.buf(f'\n#define XPAR_{label_name}_HIGHADDR {hex(val + size -1)}')
                            canondef_dict.update({"BASEADDR":hex(val)})
                            canondef_dict.update({"HIGHADDR":hex(val + size - 1)})
                    else:
                        try:
                            prop_val = node[prop].value
                            # For boolean property if present LopperProp will return
                            # empty string convert it to baremetal config struct expected value
                            if '' in prop_val:
                                prop_val = [1]
                        except KeyError:
                            _warning(f"Get property value is failed for {prop} and node is {node.name}, adding default value as {0}")
                            prop_val = [0]
                            continue

                        if pad:
                            address_prop = ""
                            for pad_num in range(0,pad):
                                if pad_num == 0:
                                    address_prop = hex(node[prop].value[pad_num])
                                elif pad_num < len(node[prop].value):
                                    address_prop += f"{node[prop].value[pad_num]:08x}"
                            prop = prop.replace("-", "_")
                            prop = prop.replace("xlnx,", "")
                            if address_prop:
                                canondef_dict.update({prop:address_prop})
                                plat.buf(f'\n#define XPAR_{label_name}_{prop.upper()} {address_prop}')
                        else:
                            if ('/bits/' in prop_val):
                                prop_val = [int(prop_val[-1][3:-1], base=16)]

                            prop = prop.replace("-", "_")
                            prop = prop.replace("xlnx,", "")

                            if isinstance(prop_val[0], str):
                                if prop_val[0].replace('.','',1).isdigit():
                                    canondef_dict.update({prop:f'{prop_val[0]}'})
                                    plat.buf(f'\n#define XPAR_{label_name}_{prop.upper()} {prop_val[0]}')
                                else:
                                    canondef_dict.update({prop:f'"{prop_val[0]}"'})
                                    plat.buf(f'\n#define XPAR_{label_name}_{prop.upper()} "{prop_val[0]}"')
                            elif len(prop_val) > 1:
                                for k,item in enumerate(prop_val):
                                    cannon_prop = prop + str("_") + str(k)
                                    canondef_dict.update({cannon_prop:item})
                                    plat.buf(f'\n#define XPAR_{label_name}_{prop.upper()}_{k} {item}')
                            else:
                                canondef_dict.update({prop:hex(prop_val[0])})
                                plat.buf(f'\n#define XPAR_{label_name}_{prop.upper()} {hex(prop_val[0])}')

                plat.buf(f'\n\n/* Canonical definitions for peripheral {label_name} */')
                for prop,val in sorted(canondef_dict.items(), key=lambda e: e[0][0], reverse=False):
                    if prop == "FABRIC":
                        plat.buf(f'\n#define XPAR_FABRIC_{canonical_name}_{index}_INTR {val}')
                    else:
                        plat.buf(f'\n#define XPAR_{canonical_name}_{index}_{prop.upper()} {val}')

                #Generate Canonicals for IPI
                if drv=="ipipsu":
                    for target_channel, ipi_target_info in enumerate(ipi_target_list):
                        for key, value in ipi_target_info.items():
                            plat.buf(f'\n#define XPAR_{canonical_name}_{index}_CH{target_channel}_{key.upper()} {value}')
                plat.buf('\n')
                                    
    # Generate Defines for Generic Nodes
    if sdt.tree[tgt_node].propval('pruned-sdt') == ['']:
        node_list = bm_config.get_mapped_nodes(sdt, node_list, options)

    node_ip_count_dict = {}
    for node in node_list:
        try:
            label_name = bm_config.get_label(sdt, symbol_node, node)
            if label_name is not None:
                label_name = label_name.upper()
            val = bm_config.scan_reg_size(node, node['reg'].value, 0)
            plat.buf(f'\n/* Definitions for peripheral {label_name} */')
            plat.buf(f'\n#define XPAR_{label_name}_BASEADDR {hex(val[0])}\n')
            plat.buf(f'#define XPAR_{label_name}_HIGHADDR {hex(val[0] + val[1] - 1)}\n')

            node_ip_name = node['xlnx,ip-name'].value[0]
            if node_ip_name not in node_ip_count_dict.keys():
                node_ip_count_dict[node_ip_name] = 0
            else:
                node_ip_count_dict[node_ip_name] += 1

            canonical_name = node_ip_name.upper().replace("-", "_")

            # Check if canonical_name matches any label_name in node_list
            label_names_in_list = [
                bm_config.get_label(sdt, symbol_node, n).upper()
                for n in node_list
                if bm_config.get_label(sdt, symbol_node, n) is not None
            ]

            canonical_def_name = f'{canonical_name}_{node_ip_count_dict[node_ip_name]}'
            if canonical_def_name not in label_names_in_list:
                plat.buf(f'\n/* Canonical definitions for peripheral {label_name} */')
                plat.buf(f'\n#define XPAR_{canonical_name}_{node_ip_count_dict[node_ip_name]}_BASEADDR {hex(val[0])}\n')
                plat.buf(f'#define XPAR_{canonical_name}_{node_ip_count_dict[node_ip_name]}_HIGHADDR {hex(val[0] + val[1] - 1)}\n')
        except KeyError:
            pass

    # Define for Board
    if sdt.tree[tgt_node].propval('board') != ['']:
        board = sdt.tree[tgt_node].propval('board', list)[0]
        plat.buf(f"\n/*  BOARD definition */")
        plat.buf(f"\n#define XPS_BOARD_{board.upper()}\n")
    
    # Memory Node related defines
    mem_ranges, _ = get_memranges(tgt_node, sdt, options)
    for key, value in sorted(mem_ranges.items(), key=lambda e: e[1][1], reverse=True):
        start,size = value[0], value[1]
        suffix = "ADDRESS"
        if "axi_noc" in key:
            suffix = "ADDR"
        plat.buf(f"\n#define XPAR_{key.upper()}_BASE{suffix} {hex(start)}")
        plat.buf(f"\n#define XPAR_{key.upper()}_HIGH{suffix} {hex(start + size - 1)}")

    #CPU parameters related defines
    match_cpunode = bm_config.get_cpu_node(sdt, options)
    if re.search("microblaze-riscv", match_cpunode['compatible'].value[0]):
        plat.buf(f"\n\n/*  CPU parameters definition */\n")
        cpu_parameters={
        'xlnx,freq':"XPAR_CPU_CORE_CLOCK_FREQ_HZ",
        'xlnx,use-dcache':'XPAR_MICROBLAZE_RISCV_USE_DCACHE',
        'xlnx,dcache-line-len':'XPAR_MICROBLAZE_RISCV_DCACHE_LINE_LEN',
        'xlnx,dcache-byte-size':'XPAR_MICROBLAZE_RISCV_DCACHE_BYTE_SIZE',
        'xlnx,use-icache':'XPAR_MICROBLAZE_RISCV_USE_ICACHE',
        'xlnx,icache-line-len':'XPAR_MICROBLAZE_RISCV_ICACHE_LINE_LEN',
        'xlnx,icache-byte-size':'XPAR_MICROBLAZE_RISCV_ICACHE_BYTE_SIZE',
        'xlnx,use-fpu':'XPAR_MICROBLAZE_RISCV_USE_FPU',
        'xlnx,use-mmu':'XPAR_MICROBLAZE_RISCV_USE_MMU',
        'xlnx,use-sleep':'XPAR_MICROBLAZE_RISCV_USE_SLEEP',
        'xlnx,fault-tolerant':'XPAR_MICROBLAZE_RISCV_FAULT_TOLERANT',
        'xlnx,d-lmb':'XPAR_MICROBLAZE_RISCV_D_LMB',
        'xlnx,use-branch-target-cache':'XPAR_MICROBLAZE_RISCV_USE_BRANCH_TARGET_CACHE',
        'xlnx,branch-target-cache-size':'XPAR_MICROBLAZE_RISCV_BRANCH_TARGET_CACHE_SIZE',
        }
        ignore_else_part_lis = ['xlnx,freq','xlnx,dcache-byte-size','xlnx,icache-line-len',
                                'xlnx,icache-byte-size','xlnx,use-fpu',]
        add_multi_buf(plat,match_cpunode,cpu_parameters,ignore_else_part_lis)

    elif re.search("microblaze", match_cpunode['compatible'].value[0]):
        mic_cpu = {'xlnx,freq':'XPAR_CPU_CORE_CLOCK_FREQ_HZ',
                   'xlnx,ddr-reserve-sa':'XPAR_MICROBLAZE_DDR_RESERVE_SA',
                   'xlnx,addr-size':'XPAR_MICROBLAZE_ADDR_SIZE'}
        add_multi_buf(plat,match_cpunode,mic_cpu)

    else:
        extra_cpu_param = {"xlnx,cpu-clk-freq-hz":"XPAR_CPU_CORE_CLOCK_FREQ_HZ",
                           "xlnx,timestamp-clk-freq":"XPAR_CPU_TIMESTAMP_CLK_FREQ",}
        add_multi_buf(plat,match_cpunode,extra_cpu_param)

    #PSS REF clocks define
    if match_cpunode.propval('xlnx,pss-ref-clk-freq') != ['']:
        pss_ref = match_cpunode.propval('xlnx,pss-ref-clk-freq', list)[0]
        plat.buf(f'#define XPAR_PSU_PSS_REF_CLK_FREQ_HZ {pss_ref}\n')
    #Define for NUMBER_OF_SLRS
    if sdt.tree[tgt_node].propval('slrcount') != ['']:
        val = sdt.tree[tgt_node].propval('slrcount', list)[0]
        plat.buf(f"\n/* Number of SLRs */")
        plat.buf(f"\n#define NUMBER_OF_SLRS {hex(val)}\n")

    #Define for DEVICE_ID
    if sdt.tree[tgt_node].propval('device_id') != ['']:
        val = sdt.tree[tgt_node].propval('device_id', list)[0]
        plat.buf(f"\n/* Device ID */")
        plat.buf(f'\n#define XPAR_DEVICE_ID "{val}"\n')

    #Define for XSEM_CFRSCAN_EN
    if sdt.tree[tgt_node].propval('semmem-scan') != ['']:
        val = sdt.tree[tgt_node].propval('semmem-scan', list)[0]
        plat.buf(f'\n#define XSEM_CFRSCAN_EN {val}\n')

    #Define for XSEM_NPISCAN_EN
    if sdt.tree[tgt_node].propval('semnpi-scan') != ['']:
        val = sdt.tree[tgt_node].propval('semnpi-scan', list)[0]
        plat.buf(f'\n#define XSEM_NPISCAN_EN {val}\n')

    if is_fpd_coherent:
        plat.buf("\n#define XPAR_FPD_IS_CACHE_COHERENT \n")
    if is_lpd_coherent:
        plat.buf("\n#define XPAR_LPD_IS_CACHE_COHERENT \n")
    if is_pl_coherent:
        plat.buf("\n#define XPAR_PL_IS_CACHE_COHERENT \n")

    plat.buf('\n#endif  /* end of protection macro */')
    plat.out(''.join(plat.get_buf()))

    return True
