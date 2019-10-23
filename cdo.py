#!/usr/bin/python3

#/*
# * Copyright (c) 2019,2020 Xilinx Inc. All rights reserved.
# *
# * Author:
# *       Bruce Ashfield <bruce.ashfield@xilinx.com>
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
from libfdt import Fdt, FdtSw, FdtException, QUIET_NOTFOUND, QUIET_ALL
import libfdt

def props():
    return ["id", "file_ext"]

def id():
    return "xlnx,output,cdo"

def file_ext():
    return ".cdo"

def is_compat( node, compat_id ):
    if re.search( "xlnx,output,cdo", compat_id):
        return cdo_write
    return ""

def cdo_write( node, sdt, outfile, verbose=0 ):
    print( "[INFO]: I'd print some CDO to %s" % outfile )
    return True



