#/*
# * Copyright (c) 2021 Xilinx Inc. All rights reserved.
# * Copyright (C) 2024 Advanced Micro Devices, Inc.  All rights reserved.
# *
# * Author:
# *       Appana Durga Kedareswara rao <appana.durga.rao@xilinx.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

import sys
import os
import re
import shutil
import glob

sys.path.append(os.path.dirname(__file__))
from baremetalconfig_xlnx import get_mapped_nodes, get_cpu_node, get_label, compat_list, scan_reg_size, get_stdin
import common_utils as utils
import baremetalconfig_xlnx as bm_config

def is_compat( node, compat_string_to_test ):
    if re.search( "module,baremetal_gentestapp_xlnx", compat_string_to_test):
        return xlnx_generate_testapp
    return ""

# tgt_node: is the baremetal config top level domain node number
# sdt: is the system device-tree
# options: baremetal application source path
def xlnx_generate_testapp(tgt_node, sdt, options):
    ttc_node_list = []
    dma_node_list = []
    root_node = sdt.tree[tgt_node]
    compatible_dict = {}
    root_sub_nodes = root_node.subnodes()
    node_list = []
    # Traverse the tree and find the nodes having status=ok property
    # and create a compatible_list from these nodes.
    symbol_node = ""
    chosen_node = ""
    driver_name = ""
    stdin = None
    stdin_node = None
    try:
        stdin = options['args'][2]
    except IndexError:
        pass
    for node in root_sub_nodes:
        try:
            if node.name == "chosen":
                chosen_node = node
            if node.name == "__symbols__":
                symbol_node = node
            status = node["status"].value
            if "okay" in status:
                node_list.append(node)
        except:
           pass

    if sdt.tree[tgt_node].propval('pruned-sdt') == ['']:
        node_list = get_mapped_nodes(sdt, node_list, options)
    for node in node_list:
        if "cdns,ttc" in node["compatible"].value:
            ttc_node_list += [node]
        compatible_dict.update({node: node["compatible"].value})
        if stdin:
            if node.propval('xlnx,name') != ['']:
                node_name = node.propval('xlnx,name', list)[0]
                if node_name == stdin:
                    stdin_node = node
    match_cpunode = get_cpu_node(sdt, options)
    proc_ip_name = match_cpunode['xlnx,ip-name'].value
    if proc_ip_name[0] in ["psu_cortexr5", "psv_cortexr5", "psx_cortexr52"] and ttc_node_list:
        node_list.remove(ttc_node_list[-1])
        compatible_dict.pop(ttc_node_list[-1])

    test_file_string = f'''
#include <stdio.h>
#include "xparameters.h"
#include "xil_printf.h"
'''
    periph_file_list = []

    repo_path_data = options['args'][1]
    yaml_file_list = []

    if utils.is_file(repo_path_data):
        repo_schema = utils.load_yaml(repo_path_data)
        drv_data = repo_schema['driver']
        for entries in drv_data.keys():
            try:
                drv_path_in_yaml = drv_data[entries]['vless']
            except KeyError:
                drv_path_in_yaml = drv_data[entries]['path'][0]
            drv_name_in_yaml = os.path.basename(drv_path_in_yaml)
            # Incase of versioned component strip the version info
            drv_name_in_yaml = re.split("_v(\d+)_(\d+)", drv_name_in_yaml)[0]
            yaml_file_list += [os.path.join(drv_path_in_yaml, 'data', f"{drv_name_in_yaml}.yaml")]
    else:
        drv_dir = os.path.join(repo_path_data, "XilinxProcessorIPLib", "drivers")
        if utils.is_dir(drv_dir):
            yaml_file_list = glob.glob(drv_dir + '/**/data/*.yaml', recursive=True)

    testapp_data = {}
    testapp_name = {}
    # Ensure that the interrupt controller example is the first example run in the peripheral tests sequence by updating the yaml_file_list.
    intc_index = [index for index,yaml_file in enumerate(yaml_file_list) if re.sub(r"_v.*_.*$", "", os.path.basename(yaml_file)) == "intc.yaml"]
    if intc_index:
        yaml_file_list.insert(0, yaml_file_list.pop(intc_index[0]))
    gic_index = [index for index,yaml_file in enumerate(yaml_file_list) if re.sub(r"_v.*_.*$", "", os.path.basename(yaml_file)) == "scugic.yaml"]
    if gic_index:
        yaml_file_list.insert(0, yaml_file_list.pop(gic_index[0]))
    for yaml_file in yaml_file_list:
        schema = utils.load_yaml(yaml_file)
        driver_compatlist = compat_list(schema)
        driver_nodes = []
        drv_name = utils.get_base_name(yaml_file).replace('.yaml','')
        drv_data_path = utils.get_dir_path(yaml_file)
        drv_path = utils.get_dir_path(drv_data_path)
        drv_is_active = 0
        for comp in driver_compatlist:
            for node,c in compatible_dict.items():
                match = [x for x in c if comp == x]
                if match:
                    drv_is_active = 1
        if not drv_is_active:
            continue
        try:
            drv_config_name = schema['config']
            drv_config_name = drv_config_name[0].rsplit("_", 1)[-2]
        except KeyError:
            drv_config_name = drv_name

        if drv_config_name == 'XAxiEthernet':
           driver_name = drv_config_name
           for node in node_list:
               if "xlnx,eth-dma" in node["compatible"].value:
                   dma_node_list.append(node)
                   dma_label = get_label(sdt, symbol_node, node)
                   testapp_name.update({dma_label: 'XAxiDma'})
               if "xlnx,eth-mcdma" in node["compatible"].value:
                   dma_node_list.append(node)
                   dma_label = get_label(sdt, symbol_node, node)
                   testapp_name.update({dma_label: 'XMcdma'})

        stdin_addr = None
        if stdin_node:
            val, size = scan_reg_size(stdin_node, stdin_node['reg'].value, 0)
            stdin_addr = val
        elif chosen_node:
            stdin_node = get_stdin(sdt, chosen_node, node_list)
            if stdin_node:
                val, size = scan_reg_size(stdin_node, stdin_node['reg'].value, 0)
                stdin_addr = val
        try:
            testapp_schema = schema['tapp']
            tapp_drv_header_file_name = f"{drv_name}_header.h"
            tapp_drv_header_src_file = os.path.join(drv_data_path, tapp_drv_header_file_name)
            tapp_drv_header_dst_file = os.path.join(sdt.outdir, tapp_drv_header_file_name)
            periph_file_list += [tapp_drv_header_dst_file]
            utils.copy_file(tapp_drv_header_src_file, tapp_drv_header_dst_file)

            test_file_string += f'''
#include "x{drv_name}.h"
#include "{tapp_drv_header_file_name}"
'''
            with open(tapp_drv_header_dst_file, 'r+') as fd:
                content = fd.readlines()
                content.insert(0, "#define TESTAPP_GEN\n")
                fd.seek(0, 0)
                fd.writelines(content)

            for compat in driver_compatlist:
                for node in node_list:
                    if sdt.tree[node].propval('reg') != ['']:
                        val, size = scan_reg_size(node, node['reg'].value, 0)
                        if stdin_addr == val:
                            continue
                    compat_string = node['compatible'].value
                    label_name = get_label(sdt, symbol_node, node)
                    drvconfig_name = None
                    if compat in compat_string:
                        driver_nodes.append(node)
                        dec = []
                        for app,prop in testapp_schema.items():
                            example_dir = os.path.join(drv_path, "examples")
                            example_file_src_path = os.path.join(example_dir, app)
                            example_file_dst_path = os.path.join(sdt.outdir, app)
                            list_of_hw_props = testapp_schema[app].get('hwproperties',[])
                            list_of_dep_files = testapp_schema[app].get('dependency_files',[])
                            valid_ex = 0
                            match_list = []
                            for prop_name in list_of_hw_props:
                                try:
                                    if "interrupts" in testapp_schema[app]['hwproperties']:
                                        intr_parent_phandle = node["interrupt-parent"].value
                                        intr_parent_node = [node for node in sdt.tree['/'].subnodes() if node.phandle == intr_parent_phandle[0]]
                                        # Ideally, the processor IP and the intr-parent combination should be checked for this case.
                                        # But, that is a cumbersome process and this condition also works given the way gen-domain-dts
                                        # behaves.
                                        if intr_parent_node and intr_parent_node[0]["compatible"].value[0] != "interrupt-multiplex":
                                            match_list.append(True)
                                        else:
                                            match_list.append(False)
                                    if isinstance(prop_name, dict):
                                        for e,prop_val in prop_name.items():
                                            val=node[e].value
                                            if prop_val == val:
                                                match_list.append(True)
                                            elif isinstance(val, list):
                                                if prop_val == val[0]:
                                                    match_list.append(True)
                                                else:
                                                    match_list.append(False)
                                            else:
                                                match_list.append(False)
                                    else:
                                        try:
                                            valid_ex = node[prop_name].value
                                            if valid_ex:
                                                match_list.append(True)
                                        except KeyError:
                                            match_list.append(False)

                                except KeyError:
                                    match_list.append(False)

                            for p_key, p_value in prop.items():
                                if p_key == "dependency_files":
                                    continue
                                if isinstance(p_value, int):
                                   val = node[p_key].value
                                   if p_key == "axistream-connected":
                                      reg = bm_config.get_phandle_regprop(sdt, p_key, val)
                                      val = reg & 0xF
                                   if val == p_value:
                                      match_list.append(True)
                                   else:
                                      match_list.append(False)

                            if False in match_list:
                                valid_ex = 0
                            else:
                                valid_ex = 1

                            if valid_ex and utils.is_file(example_file_src_path):
                                periph_file_list += [example_file_dst_path]
                                utils.copy_file(example_file_src_path, example_file_dst_path)
                                with open(example_file_dst_path, 'r+') as fd:
                                    content = fd.readlines()
                                    content.insert(0, "#define TESTAPP_GEN\n")
                                    fd.seek(0, 0)
                                    fd.writelines(content)
                                for dep_file in list_of_dep_files:
                                    dep_file_src_path = os.path.join(example_dir, dep_file)
                                    dep_file_dst_path = os.path.join(sdt.outdir, dep_file)
                                    if utils.is_file(dep_file_src_path):
                                       periph_file_list += [dep_file_dst_path]
                                       utils.copy_file(dep_file_src_path, dep_file_dst_path)
                                       with open(dep_file_dst_path, 'r+') as fd:
                                           content = fd.readlines()
                                           content.insert(0, "#define TESTAPP_GEN\n")
                                           fd.seek(0, 0)
                                           fd.writelines(content)
                                dec.append(testapp_schema[app]['declaration'])
                                if 'selftest' not in app.lower() and 'selftest' not in testapp_schema[app]['declaration'].lower():
                                    drvconfig_name = True
                        testapp_data.update({label_name:dec})
                        if drvconfig_name:
                            testapp_name.update({label_name:drv_config_name})
        except KeyError:
            testapp_schema = {}

    test_file_string += f'''int main ()
{{
'''
    for node,drv_config_name in testapp_name.items():
        test_file_string += f'''
    static {drv_config_name} {node};'''

    test_file_string += f'''

    print("---Entering main---\\n\\r");
'''
    for node, testapp in testapp_data.items():
        xpar_def = f"XPAR_{node.upper()}_BASEADDR"

        for app in testapp:
            if 'SelfTest' in app or 'selftest' in app:
                status_assignment = f"status = {app}({xpar_def});"
            elif driver_name == "XAxiEthernet":
                 driver_name = ""
                 if dma_label:
                    status_assignment = f"status = {app}(&{node}, &{dma_label}, {xpar_def});"
                 else:
                    print(f"In Valid DMA label \n")
            else:
                status_assignment = f"status = {app}(&{node}, {xpar_def});"

            test_file_string += f'''
    {{
        int status;
        print("\\r\\nRunning {app} for {node} ... \\r\\n");
        {status_assignment}
        if (status == 0) {{
            print("{app} PASSED \\r\\n");
        }} else {{
            print("{app} FAILED \\r\\n");
        }}
    }}
'''

    test_file_string += f'''
    print("---Exiting main---");
    return 0;
}}
'''

    testperiph_file = os.path.join(sdt.outdir, 'testperiph.c')
    with open(testperiph_file, 'w') as file_handle:
        file_handle.writelines(test_file_string)
        periph_file_list += [testperiph_file]

    with open(os.path.join(sdt.outdir, 'file_list.txt'), 'w') as file_handle:
        periph_file_list = set(periph_file_list)
        for entry in periph_file_list:
            file_handle.writelines(f'{entry}\n')

    return True
