#/*
# * Copyright (c) 2019,2020 Xilinx Inc. All rights reserved.
# *
# * Author:
# *       Bruce Ashfield <bruce.ashfield@xilinx.com>
# *       Naga Sureshkumar Relli <naga.sureshkumar.relli@xilinx.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

from pathlib import Path
from pathlib import PurePath
import lopper
from lopper.tree import *

sys.path.append(os.path.dirname(__file__))
import cdotypes

#Power Nodes
PM_DEV_I2C_PM = 0x1822402d
PM_POWER_VCCINT_PMC = 0x4328029
PM_POWER_VCCAUX_PMC = 0x432802a
PM_POWER_VCCINT_PSLP = 0x432802b
PM_POWER_VCCINT_PSFP = 0x432802c
PM_POWER_VCCINT_SOC = 0x432802d
PM_POWER_VCCINT_RAM = 0x432802e
PM_POWER_VCCAUX = 0x432802f
PM_POWER_VCCINT_PL = 0x4328030

#Power Rail modes supported(ON or OFF)
MODES_SUPPORTED = 0x2
MODE_OFF_COMMAND = 0x0
MODE_ON_COMMAND = 0x1
MODE_OFF_I2C_CMNDS = 0x3
MODE_ON_I2C_CMNDS = 0x3

#PMBUS commands
PAGE_COMMAND = 0x0
ON_OFF_CONFIG_COMMAND = 0x2
ON_OFF_OPERATION_COMMAND = 0x1
OPERATION_OFF_COMMAND = 0x0
OPERATION_ON_COMMAND = 0x80

#Number of I2C command bytes
NUMOF_I2C_CMND_BYTES = 0x2

#Data to pass for ON_OFF_CONFIG Command
ON_OFF_CONFIG_DATA_BYTE = 0x1a

#Regulator control method
CTRL_MTHD_PMBUS = 0x1
CTRL_MTHD_GPIO = 0x2

# map a device tree "type" to a cdo nodeid/type list
cdo_nodetype = {
                 "ti,ina226" : "regulator",
                 "infineon,irps5401" : "regulator",
                 "infineon,ir38164" : "regulator"
               }

# These are the regulator devices which supports pgood option
pgood_devices = { "TPS65400" }

#These are the PMBUS supported regulators
pmbus_regulators = {"infineon,irps5401",
                     "infineon,ir38164"}

#Global regulator count, which is used to generate regulator index
regulator_count = 0

#Number Mux bytes
mux_bytes = {"nxp,pca9548": 1}

# vck190 power rail macros as mentioned in xpm_node.h
# {board: {rail: id}
rail_to_nodeid = { 'vck190':
                            {'vcc-pslp': '0x432802b',
                             'vcc-psfp': '0x432802c',
                             'vccaux': '0x432802f',
                             'vcc-ram': '0x432802e',
                             'vcc-soc': '0x432802d',
                             'vccint': '0x4328030',
                             'vcc-pmc': '0x4328029',
                             'vccaux-pmc': '0x432802a'}
                  }

def get_mux_bytes( mux_type ):
    for key in mux_bytes:
       if re.match(key, mux_type):
           return mux_bytes[key]

    # If no supported Mux found, the default to 1 bytes
    return 1

def is_pmbus( node_type ):
    bus_type = 0
    for type in pmbus_regulators:
        if re.match(type, node_type[0]):
            return 1

    return bus_type

def rail_nodeid( label, board):
    value = 0x0
    for p_id, p_info in rail_to_nodeid.items():
        if re.match(p_id, board):
            for key in p_info:
                val = label[0].split("-",1)[-1]
                if val in key:
                    value = p_info[key]
                    return value

    return value

def is_pgood(node_type):
    for type in pgood_devices:
        if re.match(type, node_type[0]):
            pgood = 2
        else:
            pgood = 1

    return pgood

# map a compatible string to a cdo type
def cdo_type( device_tree_type ):
    try:
        x = cdo_nodetype[device_tree_type]
    except:
        x = "unknown_type"
        for k in cdo_nodetype.keys():
            if re.search( k, str(device_tree_type) ):
                x = cdo_nodetype[k]

    return x
