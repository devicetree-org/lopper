#/*
# * Copyright (c) 2022 AMD Inc. All rights reserved.
# *
# * Author:
# *       Bruce Ashfield <bruce.ashfield@amd.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

import sys
import types
import os
import getopt
import re
import copy
import subprocess
import textwrap

def is_compat( node, compat_string_to_test ):
    if re.search( "module,image-builder", compat_string_to_test):
        return image_builder
    return ""

def usage():
    print( """
   Usage: image-builder [--uboot] -o <output dir> --imagebuilder <path to imagebuilder>

    wrapper around imagebuilder (https://gitlab.com/xen-project/imagebuilder)      

      --uboot  execute imagebuilder's "uboot-script-gen", with the
               options: -t tftp -c ./config, and the supplied output directory
      -i       path to imagebuilder clone
      -v       enable verbose debug/processing
      -o       output directory for files

    """)


def image_builder( tgt_node, sdt, options ):
    try:
        verbose = options['verbose']
    except:
        verbose = 0

    try:
        args = options['args']
    except:
        args = []

    try:
        xen_tree = sdt.subtrees["extracted"]
    except:
        print( "[ERROR]: no extracted tree detected, returning" )
        return False

    opts,args2 = getopt.getopt( args, "vpt:o:i:", [ "uboot", "verbose", "imagebuilder=" ] )

    image_type="uboot"
    output=None
    for o,a in opts:
        # print( "o: %s a: %s" % (o,a))
        if o in ('-o'):
            output=a
        elif o in ('-v', "--verbose"):
            verbose = verbose + 1
        elif o in ("--uboot"):
            image_type="uboot"
        elif o in ("-i", "--imagebuilder"):
            image_builder=a

    if not image_builder:
        print( "[ERROR][imagebuilder]: path to image builder not passed" )
        sys.exit(1)

    if image_type == "uboot":
        print( "[INFO][imagebuilder]: generating uboot" )

        if not output:
            print( "[ERROR]: path to imagebuilder missing" )
            sys.exit(1)

        #  ~/git/imagebuilder/scripts/uboot-script-gen -t tftp -d . -c ./config
        result = subprocess.run(['%s/scripts/uboot-script-gen' % image_builder, '-t', 'tftp', '-d', '%s' % output, '-c', '%s/config' % output ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=3)
        if result.returncode != 0:
            print( "[ERROR]: unable to generate uboot scripts" )
            print( "\n%s" % textwrap.indent(result.stderr.decode(), '         ') )
            sys.exit(result.returncode)

    return True
