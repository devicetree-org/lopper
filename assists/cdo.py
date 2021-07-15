# /*
# * Copyright (c) 2019,2020 Xilinx Inc. All rights reserved.
# *
# * Author:
# *       Bruce Ashfield <bruce.ashfield@xilinx.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

import humanfriendly
import json
from collections import OrderedDict
from cdo_topology import *
from xlnx_versal_power import *
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
from lopper_tree import *
from xlnx_pm import subsystem

sys.path.append(os.path.dirname(__file__))


def props():
    return ["id", "file_ext"]


def id():
    return "xlnx,output,cdo"


def file_ext():
    return ".cdo"


def is_compat(node, compat_id):
    if re.search("xlnx,output,cdo", compat_id):
        return cdo_write
    return ""


def cdo_write(root_node, sdt, options):
    try:
        verbose = options['verbose']
    except BaseException:
        verbose = 0

    subs_data = []

    domain_node = None
    try:
        domain_node = root_node.tree["/domains"]
    except KeyError:
        if verbose > 0:
            print("[DBG++]: CDO plugin unable to find domains node")
        return True

    subsystems = subsystem.valid_subsystems(domain_node, sdt, options)

    for sub in subsystems:
        # collect device tree flags, nodes, xilpm IDs for each device linked to
        # a subsystem
        subsystem.process_subsystem(sub, sub.sub_node, sdt, options)
        subsystem.construct_flag_references(sub)

    # generate xilpm reqs for each device
    subsystem.construct_pm_reqs(subsystems)

    if (len(options["args"]) > 0):
        if re.match(options["args"][0], "regulator"):
            outfile = options["args"][1]
            gen_board_topology(domain_node, sdt, output)
        else:
            outfile = options["args"][0]
    else:
        outfile = "subsystem.cdo"

    output = open(outfile, "w")
    print("# Lopper CDO export", file=output)
    print("version 2.0", file=output)
    for sub in subsystems:
        # determine subsystem ID
        sub_id = sub.sub_node.propval("id")
        if isinstance(sub_id, list):
            sub_id = sub_id[0]
        cdo_sub_str = "subsystem_" + hex(sub_id)
        cdo_sub_id = 0x1c000000 | sub_id
        # add subsystem
        print("# " + cdo_sub_str, file=output)
        print("pm_add_subsystem " + hex(cdo_sub_id), file=output)

    # add CDO commands for permissions
    subsystem.sub_perms(subsystems, output)

    for sub in subsystems:
        # determine subsystem ID
        sub_id = sub.sub_node.propval("id")
        if isinstance(sub_id, list):
            sub_id = sub_id[0]
        cdo_sub_str = "subsystem_" + hex(sub_id)
        cdo_sub_id = 0x1c000000 | sub_id
        # add reqs
        for device in sub.dev_dict.values():
            if device.node_id not in xilinx_versal_device_names.keys():
                print("WARNING: ", hex(device.node_id),
                      ' not found in xilinx_versal_device_names')
                return

            req_description = "# " + cdo_sub_str + ' ' + \
                xilinx_versal_device_names[device.node_id]

            # form CDO flags in string for python
            req_str = 'pm_add_requirement ' + hex(cdo_sub_id) + ' ' + hex(
                device.node_id)
            for req in device.pm_reqs:
                req_str += ' ' + hex(req)

            # write CDO
            print(req_description, file=output)
            print(req_str, file=output)

    return True
