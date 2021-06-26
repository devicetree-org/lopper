#/*
# * Copyright (c) 2021 Xilinx Inc. All rights reserved.
# *
# * Author:
# *       Appana Durga Kedareswara rao <appana.durga.rao@xilinx.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

import struct
import sys
import types
import unittest
import os
import getopt
import re
import subprocess
import shutil
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
from baremetallinker_xlnx import *
from baremetal_xparameters_xlnx import get_label
from bmcmake_metadata_xlnx import to_cmakelist

def is_compat( node, compat_string_to_test ):
    if re.search( "module,baremetal_gentestapp_xlnx", compat_string_to_test):
        return xlnx_generate_testapp
    return ""

# tgt_node: is the baremetal config top level domain node number
# sdt: is the system device-tree
# options: baremetal application source path
def xlnx_generate_testapp(tgt_node, sdt, options):
    root_node = sdt.tree[tgt_node]
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

    node_list = get_mapped_nodes(sdt, node_list, options)
    tmpdir = os.getcwd()
    file_fd = open('file_list.txt', 'w')
    file_fd.write('testperiph.c')
    file_fd.write("\n")
    plat = DtbtoCStruct('testperiph.c')
    src_dir = options['args'][1]
    os.chdir(src_dir)
    os.chdir("XilinxProcessorIPLib/drivers/")
    cwd = os.getcwd()
    files = os.listdir(cwd)
    plat.buf('#include <stdio.h>\n')
    plat.buf('#include "xparameters.h"\n')
    plat.buf('#include "xil_printf.h"\n')
    testapp_data = {}
    testapp_name = {}
    for name in files:
        os.chdir(cwd)
        if os.path.isdir(name):
            os.chdir(name)
            if os.path.isdir("data"):
                os.chdir("data")
                yamlfile = name + str(".yaml")
                try:
                    with open(yamlfile, 'r') as stream:
                        schema = yaml.safe_load(stream)
                        driver_compatlist = compat_list(schema)
                        driver_nodes = []
                        try:
                            drvname = schema['config']
                            drvname = drvname[0].rsplit("_", 1)[-2]
                        except KeyError:
                            drvname = name
                        try:
                            testapp_schema = schema['tapp']
                            tmp_str = str("x") + str(name) + str(".h") 
                            plat.buf("#include %s" % '"{}"\n'.format(tmp_str))
                            tmp_str = str(name) + str("_header.h") 
                            plat.buf("#include %s" % '"{}"\n'.format(tmp_str))
                            headerfile = os.getcwd() + str("/") + tmp_str
                            hdr_file = tmpdir + str("/") + tmp_str
                            shutil.copyfile(headerfile, hdr_file)
                            file_fd.write(hdr_file)
                            file_fd.write("\n")
                            with open(hdr_file, 'r+') as fd:
                                content = fd.readlines()
                                content.insert(0, "#define TESTAPP_GEN\n")
                                fd.seek(0, 0)
                                fd.writelines(content)

                            for compat in driver_compatlist:
                                for node in node_list:
                                    compat_string = node['compatible'].value[0]
                                    label_name = get_label(sdt, symbol_node, node)
                                    if compat in compat_string:
                                        driver_nodes.append(node)
                                        dec = []
                                        for app,prop in testapp_schema.items():
                                            filename = os.getcwd() + str("/../examples/") + app
                                            destination = tmpdir + str("/") + app
                                            try:
                                                has_hwdep = testapp_schema[app]['hwproperties'][0]
                                                try:
                                                    val = node[has_hwdep].value
                                                    has_hwdep = 0
                                                except KeyError:
                                                    has_hwdep = 1
                                            except KeyError:
                                                has_hwdep = 0

                                            if not has_hwdep:
                                                shutil.copyfile(filename, destination)
                                                file_fd.write(destination)
                                                file_fd.write("\n")
                                                with open(destination, 'r+') as fd:
                                                    content = fd.readlines()
                                                    content.insert(0, "#define TESTAPP_GEN\n")
                                                    fd.seek(0, 0)
                                                    fd.writelines(content)
                                                dec.append(testapp_schema[app]['declaration'])
                                        testapp_data.update({label_name:dec})
                                        testapp_name.update({label_name:drvname})
                        except KeyError:
                            testapp_schema = {}
                except FileNotFoundError:
                    pass

    file_fd.close()
    plat.buf('\nint main ()\n{\n')
    for node,drvname in testapp_name.items():
        plat.buf('   static %s %s;\n' % (drvname, node))

    plat.buf('\n\tprint("---Entering main---%s");' % r"\n\r")
    plat.buf('\n')
    for node,testapp in testapp_data.items():
        xpar_def = str("XPAR_") + node.upper() + str("_BASEADDR")
        for app in testapp:
            plat.buf("\n\t{\n")
            plat.buf("\t\tint status;\n\n")
            tmp_str = r"\r\n" + str("Running ") + app + str(" for ") + node + str("...") + r"\r\n"
            plat.buf('\t\tprint("%s");' % tmp_str)
            is_selftest = re.search('SelfTest', app)
            if is_selftest:
                plat.buf('\n\t\tstatus = %s(%s);\n' % (app, xpar_def))
            else:
                plat.buf('\n\t\tstatus = %s(&%s, %s);\n' % (app, node, xpar_def))
            plat.buf('\t\tif (status == 0) {\n')
            plat.buf('\t\t\t print("%s PASSED%s");\n' % (app, r"\r\n"))
            plat.buf('\t\t} else {\n')
            plat.buf('\t\t\t print("%s FAILED%s");\n' % (app, r"\r\n"))
            plat.buf('\t\t}\n')
            plat.buf("\t}\n")
    plat.buf('\n\tprint("---Exiting main---");\n')
    plat.buf('\treturn 0;\n')
    plat.buf('}')
    os.chdir(tmpdir)
    # Remove duplicate lines
    with open('file_list.txt', 'r') as f:
        unique_lines = set(f.readlines())
    with open('file_list.txt', 'w') as f:
        f.writelines(unique_lines)
    plat.out(''.join(plat.get_buf()))

    return True
