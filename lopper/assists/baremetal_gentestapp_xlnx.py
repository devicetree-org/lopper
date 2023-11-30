#/*
# * Copyright (c) 2021 Xilinx Inc. All rights reserved.
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
from baremetalconfig_xlnx import get_mapped_nodes, get_label, compat_list
import common_utils as utils

def is_compat( node, compat_string_to_test ):
    if re.search( "module,baremetal_gentestapp_xlnx", compat_string_to_test):
        return xlnx_generate_testapp
    return ""

# tgt_node: is the baremetal config top level domain node number
# sdt: is the system device-tree
# options: baremetal application source path
def xlnx_generate_testapp(tgt_node, sdt, options):
    root_node = sdt.tree[tgt_node]
    compatible_dict = {}
    root_sub_nodes = root_node.subnodes()
    node_list = []
    # Traverse the tree and find the nodes having status=ok property
    # and create a compatible_list from these nodes.
    symbol_node = ""
    for node in root_sub_nodes:
        try:
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
        compatible_dict.update({node: node["compatible"].value})

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
            drv_name_in_yaml = re.sub(r"_v.*_.*$", "", drv_name_in_yaml)
            yaml_file_list += [os.path.join(drv_path_in_yaml, 'data', f"{drv_name_in_yaml}.yaml")]
    else:
        drv_dir = os.path.join(repo_path_data, "XilinxProcessorIPLib", "drivers")
        if utils.is_dir(drv_dir):
            yaml_file_list = glob.glob(drv_dir + '/**/data/*.yaml', recursive=True)

    testapp_data = {}
    testapp_name = {}
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
                    compat_string = node['compatible'].value
                    label_name = get_label(sdt, symbol_node, node)
                    if compat in compat_string:
                        driver_nodes.append(node)
                        dec = []
                        for app,prop in testapp_schema.items():
                            example_dir = os.path.join(drv_path, "examples")
                            example_file_src_path = os.path.join(example_dir, app)
                            example_file_dst_path = os.path.join(sdt.outdir, app)
                            try:
                                has_hwdep = testapp_schema[app]['hwproperties'][0]
                                try:
                                    val = node[has_hwdep].value
                                    has_hwdep = 0
                                except KeyError:
                                    has_hwdep = 1
                            except KeyError:
                                has_hwdep = 0

                            if not has_hwdep and utils.is_file(example_file_src_path):
                                periph_file_list += [example_file_dst_path]
                                utils.copy_file(example_file_src_path, example_file_dst_path)
                                with open(example_file_dst_path, 'r+') as fd:
                                    content = fd.readlines()
                                    content.insert(0, "#define TESTAPP_GEN\n")
                                    fd.seek(0, 0)
                                    fd.writelines(content)
                                dec.append(testapp_schema[app]['declaration'])
                        testapp_data.update({label_name:dec})
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