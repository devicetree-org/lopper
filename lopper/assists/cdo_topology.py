#/*
# * Copyright (c) 2019,2020 Xilinx Inc. All rights reserved.
# *
# * Author:
# *       Bruce Ashfield <bruce.ashfield@xilinx.com>
# *       Naga Sureshkumar Relli <naga.sureshkumar.relli@xilinx.com>
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
import lopper
from lopper.tree import *

sys.path.append(os.path.dirname(__file__))
from topology_headr import *

def gen_board_topology( node, lt, output ):
    global regulator_count
    try:
        verbose = options['verbose']
    except:
        verbose = 0

    root_node = lt.tree[node]
    cdo_tree = root_node.subnodes()
    board_list = ["vck190"]
    root_compat = lt.tree['/']['compatible'].value
    board_name = [b for b in board_list for board in root_compat if b in board]
    if not board_name:
        print( "[ERROR]: No supported board provided: %s" % root_compat[0] )
        sys.exit(1)

    board = board_name[0]
    regulator_arr = []
    # This loop iterates twice, one for regulator cdo data and another for rail cdo data
    mux_addr = None
    regulator_count = 0
    mux_count = 0
    for n in cdo_tree:
        if not n.name:
            nodename = "root"
            node_type= "root"
        else:
            nodename = n.name
            # get the compatible string of the node, that's our "type", which we'll
            # map to the CDO class, subclass and type fields.
            node_type = n.type
            tmp = n.name.split("@")
            length = len(tmp)
            if length > 1:
                # get the mux_address, mux node starts with "i2c-mux"
                if re.match( "i2c-mux", tmp[0]):
                    mux_name = n.name
                    mux_type = n.type[0]
                    mux_addr = tmp[1]
                    mux_count += 1

        if node_type:
            cdo_t = cdo_type( node_type )
            # if cdo_t is not regulator, we will check for regulator-name property
            # in the node, as per regulator.txt and gpio-regulator.txt node can have
            # regulator-name property also.
            # This is to check whether the node is regulator or not
            try:
                regulator_name = n['regulator-name'].value
            except:
                regulator_name = ""

            reserved = 0
            parent_count = 1
            width = 0
            shift = 0
            if re.match( "regulator", cdo_t) or regulator_name != "":
               bus_type = is_pmbus( node_type )
               if bus_type == 1:
                   ctrl_mthd = CTRL_MTHD_PMBUS
               else:
                   #TODO gpio control method
                   ctrl_mthd = CTRL_MTHD_GPIO

               parent = str(n.parent)
               res = parent.rsplit('@', 1) #/amba/i2c@ff020000/i2c-mux@74/i2c@0, get the i2c channel address which is @0
               # This is valid only for i2c regulators, where the it is under i2c node
               # for GPIO regulators, it is just under /
               try:
                   mux_channel = int(res[1]) + 1
               except:
                   mux_channel = ""

               mux_bytes = get_mux_bytes(mux_type) #Number of i2c bytes to configure the mux channel

               # reg property is not valid for GPIO regulators
               try:
                   i2c_addr = n['reg'].value
               except:
                   i2c_addr = ""

               # Get the lable name from the node
               try:
                   label = n['label'].value
               except:
                   label = ""

               # label and regulator_name properties will be empty for PMBUS regulators
               # and regulator_name will be present for GPIO regulators
               # With this check, we say that it is Main regulator and generates regulator data
               if (label == "" and regulator_name == "") or regulator_name != "":
                   regulator_arr.append(n.name)
                   regulator_count += 1

               if (label == "" and regulator_name == "") or regulator_name != "":
                   if ctrl_mthd == CTRL_MTHD_PMBUS:
                       print("#Regulator: " + str(n.name) + " Mux: " + str(mux_name), file=output)
                       print("#             <i2c address of Regulator:" + hex(i2c_addr[0]) + "> <Numberof Muxes: " + str(mux_count) + "> <Controlling Method(PMBus(0x1)/GPIO(0x2): " + str(ctrl_mthd) + ">", file=output)
                       print("#             <NodeId of i2c controller:" + hex(PM_DEV_I2C_PM) + ">",  file=output)
                       print("#             <Mux Channel:" + hex(mux_channel) + "> <i2c bytes:" + hex(mux_bytes) + "> <mux i2c address:" + str(mux_addr) + ">", file=output )
                       print( "pm_add_node {0} {1}{2}{3} {4} {5}{6}{7}".format( f"0x442c00{regulator_count}", hex( i2c_addr[0] ), "%02x" % mux_count, "%02x" % ctrl_mthd, hex( PM_DEV_I2C_PM ), "0x%02x"  % mux_channel , "%02x" % mux_bytes, mux_addr), file=output )
                   else:
                       #GPIO control method
                       #TODO Will be updated once we have support in code base
                       print("#GPIO Regulator: " + str(n.name) , file=output)

               else:
                   node_id = rail_nodeid(label, board)
                   if re.match(str(node_id), "0"):
                           continue

                   try:
                       regulator_parent = n['parent-regulator'].value
                   except:
                       print("[ERROR]: No parent-regulator property found for %s" % label[0])
                       sys.exit(1)

                   reg_par = lt.FDT.node_offset_by_phandle(regulator_parent[0])
                   ind = lt.FDT.get_name(reg_par)
                   array_idx = regulator_arr.index(ind) + 1

                   pgood = is_pgood( node_type )

                   page_nr = n['page-number'].value #Rail line
                   page_nr = int(page_nr[0])

                   #0x442c00: XPM_NODECLASS_POWER
                   #          XPM_NODESUBCL_POWER_REGULATOR
                   #          XPM_NODETYPE_POWER_REGULATOR
                   #          XPM_NODEIDX_POWER_REGULATOR_0
                   parent_reg_id = f"0x442c00{array_idx}"

                   print("#Rail: " + str(label[0]) + " and Parent: " + str(ind), file=output)
                   print("#           <Pgood(0x2)/PMBus(0x1): " + hex( pgood ) + "> <Parent Regulator Id: " + str(parent_reg_id) + "> <Modes supported: " + hex(MODES_SUPPORTED) + ">", file=output)
                   print("#           <RailOFF data:          <i2c_cmnds:" + hex( MODE_OFF_I2C_CMNDS ) + "> <mode_rail_off: " + hex(MODE_OFF_COMMAND) + ">", file=output)
                   print("#                                   <cmnd_bytes:" + hex( NUMOF_I2C_CMND_BYTES ) + "> <Page number: " + hex(page_nr) + "> <Page command: " + hex(PAGE_COMMAND) + "> <cmnd_byts: " + hex(NUMOF_I2C_CMND_BYTES) + ">", file=output)
                   print("#                                   <Operation_OFF:" + hex( ON_OFF_OPERATION_COMMAND ) + "> <cmnd_bytes: " + hex(NUMOF_I2C_CMND_BYTES) + "> <Payload: " + hex(ON_OFF_CONFIG_DATA_BYTE) + "> <ON_OFF_cmd: " + hex(ON_OFF_CONFIG_COMMAND) + "> <Operation_off" +hex( OPERATION_OFF_COMMAND ) + ">", file=output)
                   print("#           <RailON data:           <i2c_cmnds:" + hex( MODE_ON_I2C_CMNDS ) + "> <mode_rail_off: " + str(MODE_ON_COMMAND) + ">", file=output)
                   print("#                                   <cmnd_bytes:" + hex( NUMOF_I2C_CMND_BYTES ) + "> <Page number: " + hex(page_nr) + "> <Page command: " + hex(PAGE_COMMAND) + "> <cmnd_bytes: " + hex(NUMOF_I2C_CMND_BYTES) + ">", file=output)
                   print("#                                   <Operation_OFF:" + hex( ON_OFF_OPERATION_COMMAND ) + "> <cmnd_bytes: " + hex(NUMOF_I2C_CMND_BYTES) + "> <Payload: " + hex(ON_OFF_CONFIG_DATA_BYTE) + "> <ON_OFF_cmd: " + hex(ON_OFF_CONFIG_COMMAND) + "> <Operation_off:" +hex( OPERATION_ON_COMMAND ) + ">", file=output)

                   print( "pm_add_node {0} {1} {2} {3} {4}{5} {6}{7}{8}{9} {10}{11}{12}{13} {14} {15}{16} {17}{18}{19}{20} {21}{22}{23}{24} {25}".format( node_id, hex( pgood ), parent_reg_id , hex( MODES_SUPPORTED), hex( MODE_OFF_I2C_CMNDS), "%02x" % MODE_OFF_COMMAND, "0x%02x" % NUMOF_I2C_CMND_BYTES, "%02x" % int(page_nr), "%02x" % PAGE_COMMAND, "%02x" % NUMOF_I2C_CMND_BYTES, "0x%02x"  % ON_OFF_OPERATION_COMMAND , "%02x" % NUMOF_I2C_CMND_BYTES, "%02x" % ON_OFF_CONFIG_DATA_BYTE, "%02x" % ON_OFF_CONFIG_COMMAND, hex( OPERATION_OFF_COMMAND ), "0x%02x" % MODE_ON_I2C_CMNDS, "%02x" % MODE_ON_COMMAND, "0x%02x" % NUMOF_I2C_CMND_BYTES, "%02x" % int(page_nr), "%02x" % PAGE_COMMAND, "%02x" % NUMOF_I2C_CMND_BYTES, "0x%02x" % ON_OFF_OPERATION_COMMAND, "%02x" % NUMOF_I2C_CMND_BYTES, "%02x" % ON_OFF_CONFIG_DATA_BYTE, "%02x" % ON_OFF_CONFIG_COMMAND, "0x%02x" % OPERATION_ON_COMMAND), file=output )

        else:
            # TODO: we don't handle other than regulators
            continue

    return True
