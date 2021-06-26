#/*
# * Copyright (c) 2020 Xilinx Inc. All rights reserved.
# *
# * Author:
# *       Appana Durga Kedareswara rao <appana.durga.rao@xilinx.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */
import sys
import types
import os
import re
from pathlib import Path
from pathlib import PurePath
from io import StringIO
import contextlib
import importlib
from lopper import Lopper
from lopper import LopperFmt
import lopper
from lopper.tree import *
from re import *
import yaml

sys.path.append(os.path.dirname(__file__))
from baremetalconfig_xlnx import *

def is_compat(node, compat_string_to_test):
    if re.search( "module,baremetaldrvlist_xlnx", compat_string_to_test):
        return xlnx_generate_bm_drvlist
    return ""

# tgt_node: is the baremetal config top level domain node number
# sdt: is the system device-tree
def xlnx_generate_bm_drvlist(tgt_node, sdt, options):
    root_node = sdt.tree[tgt_node]
    root_sub_nodes = root_node.subnodes()
    compatible_list = []
    driver_list = []
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

    mapped_nodelist = get_mapped_nodes(sdt, node_list, options)
    for node in mapped_nodelist:
        compatible_list.append(node["compatible"].value)

    tmpdir = os.getcwd()
    src_dir = options['args'][1]
    os.chdir(src_dir)
    os.chdir("XilinxProcessorIPLib/drivers/")
    cwd = os.getcwd()
    files = os.listdir(cwd)
    depdrv_list = []
    for name in files:
        os.chdir(cwd)
        if os.path.isdir(name):
            os.chdir(name)
            if os.path.isdir("data"):
                os.chdir("data")
                yamlfile = name + str(".yaml")
                try:
                    # Traverse each driver and find supported compatible list
                    # match it aginst the compatible_list created above, if there
                    # is a match append the driver name to the driver list.
                    with open(yamlfile, 'r') as stream:
                        schema = yaml.safe_load(stream)
                        driver_compatlist = compat_list(schema)
                        for comp in driver_compatlist:
                            for c in compatible_list:
                                match = [x for x in c if comp == x]
                                if match:
                                    driver_list.append(name)
                                    try:
                                        if schema['depends']:
                                            depdrv_list.append(schema['depends'])
                                    except:
                                        pass
                except FileNotFoundError:
                    pass

    for depdrv in depdrv_list:
        if isinstance(depdrv, list):
            for dep in depdrv:
                driver_list.append(dep)
        else:
            driver_list.append(depdrv)
    driver_list = list(dict.fromkeys(driver_list))
    # common driver needs to be present always
    driver_list.append("common")
    driver_list.sort()
    os.chdir(tmpdir)

    with open('distro.conf', 'w') as fd:
        tmpdrv_list = [drv.replace("_", "-") for drv in driver_list]
        tmp_str =  ' '.join(tmpdrv_list)
        tmp_str = '"{}"'.format(tmp_str)
        fd.write("DISTRO_FEATURES = %s" % tmp_str)
    with open('libxil.conf', 'w') as fd:
        for drv in driver_list:
            drv1 = drv.replace("_", "-")
            tmp_str1 = str("${RECIPE_SYSROOT}")
            tmp_str = tmp_str1 + "/usr/lib/lib{}.a,,{},,".format(drv, drv1)
            tmp_str = '"{}"'.format(tmp_str)
            fd.write("\nPACKAGECONFIG[%s] = %s" % (drv1, tmp_str))

    return driver_list
