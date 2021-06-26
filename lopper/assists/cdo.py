# /*
# * Copyright (c) 2019,2020,2021 Xilinx Inc. All rights reserved.
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
from xlnx import subsystem

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

    domain_node = None
    try:
        domain_node = root_node.tree["/domains"]
    except KeyError:
        if verbose > 0:
            print("[DBG++]: CDO plugin unable to find domains node")
        return True

    if (len(options["args"]) > 0):
        if re.match(options["args"][0], "regulator"):
            outfile = options["args"][1]
            gen_board_topology(domain_node, sdt, outfile)
        elif re.match(options["args"][0], "subsystem"):
            outfile = options["args"][1]
            subsystem.generate_cdo(root_node, domain_node,
                                   sdt, outfile, verbose, options)

    return True
