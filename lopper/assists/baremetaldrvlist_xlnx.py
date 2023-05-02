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
import yaml
import glob

sys.path.append(os.path.dirname(__file__))

from baremetalconfig_xlnx import compat_list, get_mapped_nodes
import common_utils as utils

def is_compat(node, compat_string_to_test):
    if re.search( "module,baremetaldrvlist_xlnx", compat_string_to_test):
        return xlnx_generate_bm_drvlist
    return ""

# tgt_node: is the baremetal config top level domain node number
# sdt: is the system device-tree
def xlnx_generate_bm_drvlist(tgt_node, sdt, options):
    root_node = sdt.tree[tgt_node]
    root_sub_nodes = root_node.subnodes()
    compatible_dict = {}
    ip_dict = {}

    driver_list = ["common"]
    node_list = []
    # Traverse the tree and find the nodes having status=ok property
    # and create a compatible_list from these nodes.
    for node in root_sub_nodes:
        try:
            status = node["status"].value
            if "okay" in status:
                node_list.append(node)
        except:
           pass

    driver_ip_list = []
    mapped_ip_list = []
    mapped_nodelist = get_mapped_nodes(sdt, node_list, options)
    for node in mapped_nodelist:
        compatible_dict.update({node: node["compatible"].value})
        node_ip_name = node.propval('xlnx,ip-name')
        if node_ip_name != ['']:
            mapped_ip_list += node_ip_name

    yaml_file_list = []
    repo_path_data = options['args'][1]
    if utils.is_file(repo_path_data):
        repo_schema = utils.load_yaml(repo_path_data)
        drv_data = repo_schema['driver']
        for entries in drv_data.keys():
            drv_path = drv_data[entries]['vless']
            drv_name = os.path.basename(drv_path)
            # Incase of versioned driver strip the version info
            drv_name = re.sub(r"_v.*_.*$", "", drv_name)
            yaml_file_list += [os.path.join(drv_path, 'data', f"{drv_name}.yaml")]
    else:
        drv_dir = os.path.join(repo_path_data, "XilinxProcessorIPLib", "drivers")
        if utils.is_dir(drv_dir, silent_discard=False):
            yaml_file_list = glob.glob(drv_dir + '/**/data/*.yaml', recursive=True)

    for yaml_file in yaml_file_list:
        # Traverse each driver and find supported compatible list
        # match it aginst the compatible_dict created above, if there
        # is a match append the driver name to the driver list.
        schema = utils.load_yaml(yaml_file)
        driver_compatlist = compat_list(schema)
        for comp in driver_compatlist:
            for node,c in compatible_dict.items():
                match = [x for x in c if comp == x]
                if match:
                    drv_name = utils.get_base_name(yaml_file).replace('.yaml','')
                    driver_list += [drv_name]
                    if schema.get('depends',{}):
                        driver_list += list(schema['depends'].keys())
                    ip_name = node.propval('xlnx,ip-name')
                    driver_ip_list += ip_name
                    if ip_name != ['']:
                        ip_dict.update({ip_name[0]:drv_name})

    generic_driver_set = list(set(mapped_ip_list) - set(driver_ip_list))
    for entries in generic_driver_set:
        ip_dict.update({entries:"None"})

    ip_dict_keys = list(ip_dict.keys())
    ip_dict_keys.sort()
    ip_sorted_dict = {entries: ip_dict[entries] for entries in ip_dict_keys}

    driver_list = list(set(driver_list))
    driver_list.sort()

    driver_list_for_yocto = []
    yocto_pkg_config_entries = ""

    for drv in driver_list:
        yocto_drv_name = drv.replace("_", "-")
        driver_list_for_yocto += [yocto_drv_name]
        yocto_pkg_config_entries += f'''
PACKAGECONFIG[{yocto_drv_name}] = "${{RECIPE_SYSROOT}}/usr/lib/lib{drv}.a,,{yocto_drv_name},,"'''

    with open(os.path.join(sdt.outdir, 'distro.conf'), 'w') as fd:
        fd.write(f'DISTRO_FEATURES = "{" ".join(driver_list_for_yocto)}"')

    with open(os.path.join(sdt.outdir, 'ip_drv_map.yaml'), 'w') as fd:
        yaml.dump(ip_sorted_dict, fd, default_flow_style=False, sort_keys=False)

    with open(os.path.join(sdt.outdir, 'libxil.conf'), 'w') as fd:
        fd.write(yocto_pkg_config_entries)

    with open(os.path.join(sdt.outdir, 'DRVLISTConfig.cmake'), 'w') as cfd:
        cfd.write(f'set(DRIVER_LIST {";".join(driver_list)})\n')

    return driver_list
