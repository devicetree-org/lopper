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
from lopper import LopperFmt
from lopper.tree import LopperAction
import lopper

sys.path.append(os.path.dirname(__file__))
from openamp_xlnx import xlnx_openamp_parse

def is_compat( node, compat_string_to_test ):
    """Identify whether this assist handles the provided compatibility string.

    Args:
        node (LopperNode): Device tree node being evaluated. Present to satisfy the
            dispatcher interface; not used for the decision.
        compat_string_to_test (str): Compatibility string extracted from the node.

    Returns:
        Callable | str: ``openamp_parse`` when a supported OpenAMP module is
        detected, otherwise an empty string indicating no match.

    Algorithm:
        Performs a regular-expression search for the supported OpenAMP compatible
        strings and returns the registered handler on success.
    """
    if re.search( "openamp,domain-processing", compat_string_to_test):
        return openamp_parse
    if re.search( "module,openamp", compat_string_to_test):
        return openamp_parse
    return ""

def openamp_parse(root_node, tree, options ):
    """Entry point for the OpenAMP assist dispatcher.

    Args:
        root_node (LopperNode): Domain node describing the OpenAMP configuration.
        tree (LopperTree): Parsed device tree representation.
        options (dict[str, Any]): Assist invocation parameters supplied by lopper.

    Returns:
        Any: Result of the Xilinx OpenAMP parser, or False when the assist does
        not support the detected vendor strings.
    """
    try:
        verbose = options['verbose']
    except:
        verbose = 0

    for i in root_node["compatible"].value:
        for j in ['amd','xlnx']:
            if j in i:
                return xlnx_openamp_parse(tree, options, verbose)

    return False
