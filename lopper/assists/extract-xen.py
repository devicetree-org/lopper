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
from pathlib import Path
from pathlib import PurePath
from lopper import Lopper
from lopper import LopperFmt
from lopper.tree import LopperAction
from lopper.tree import LopperTree
from lopper.tree import LopperNode
from lopper.tree import LopperProp
import lopper

def is_compat( node, compat_string_to_test ):
    if re.search( "module,extract-xen", compat_string_to_test):
        return extract_xen
    return ""

def usage():
    print( """
   Usage: extract-xen -t <target node> [OPTION]

      -t       target node (full path)
      -p       permissive matching on target node (regex)
      -v       enable verbose debug/processing
      -o       output file for extracted device tree

    """)


def extract_xen( tgt_node, sdt, options ):
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
        print( "[ERROR]: no extracted tree detcted, returning" )
        return False

    opts,args2 = getopt.getopt( args, "vpt:o:", [ "verbose", "permissive" ] )

    permissive=False
    target_node_name=None
    output=None
    for o,a in opts:
        # print( "o: %s a: %s" % (o,a))
        if o in ('-o'):
            output=a
        elif o in ('-t'):
            target_node_name=a
        elif o in ('-v', "--verbose"):
            verbose = verbose + 1
        elif o in ('-p', "--permissive"):
            permissive = True

    ## current TODO:
    ##  - create a zynqmp-firmware parent node to the clock-controller
    ##      - this is actually the parent node in the input device tree, but
    ##        not the only parent. How do we want to indicate that we only
    ##        should extract and pull in one level of parent ?? Maybe pull them
    ##        all in, and have this delete the one it doesn't need, since that is
    ##        xen specific ?
    ##  - add the xen,<> arguments to the serial@ff01000 node
    ##      xen,reg: generated from the reg property, with some hardcoded 2nd/3rd groupings
    ##      xen,path: path of the node we got the extracted target from in the sdt .. what is
    ##                the best way to pass this through ? maybe do a generic extract,path and
    ##                just update it to xen,path here [done (generic extract,path route)]
    ##  - change interrupt-parent = <0xfde8> in serial@ff0100 node, or if it isn't present
    ##    (i.e. ignored by extract) it must be added.
    ##  - remove the interrupt-controller node
    ##  - renamed "/extracted" to "/passthrough" [done]

    try:
        extracted_node = xen_tree["/extracted"]
    except:
        print( "[ERROR]: no extracted tree detected" )
        return False

    # rename the containing node from /extracted to /passthrough
    extracted_node.name = "passthrough"

    # walk the nodes in the tree, and look for the property "extracted,path"
    # and update it to "xen,path"
    for n in xen_tree:
        try:
            p = n["extracted,path"]
            p.name = "xen,path"
        except:
            pass

        try:
            ip = n["interrupt-parent"]
            n["interrupt-parent"].value = 0xfde8
            if verbose:
                print( "[INFO]: %s interrupt parent found, updating" % n.name  )
            # for p in n:
            #     print( "p: %s %s" % (p.name,p.value))

            # this is a known non-existent phandle, we need to inhibit
            # phandle resolution and just have the number used
            ip.phandle_resolution = False
            ip.resolve( strict = False )
            # n.print()
        except:
            pass


    if target_node_name:
        for n in xen_tree:
            if n.name == target_node_name:
                # print( "target node found" )
                np = LopperProp( "xen,force-assign-without-iommu" )
                np.value = 1
                n + np

                # check for the reg property
                try:
                    reg = n["reg"]
                    if verbose:
                        print( "[INFO]: reg found: %s copying and extending to xen,reg" % reg )
                    # make a xen,reg from it
                    xen_reg = LopperProp( "xen,reg" )
                    xen_reg.value = copy.deepcopy( reg.value )

                    # xen,reg has one additional [start size]. which are the first two
                    # entries in the reg
                    address = reg.value[0]
                    size = reg.value[1]
                    xen_reg.value.append( address )
                    xen_reg.value.append( size )

                    # magic. these need to be generated in the future
                    # xen_reg.value.extend( [0x0, 0xff110000, 0x0, 0x1000, 0x0, 0xff110000] )
                    # xen_reg.value.extend( [0x0, 0xff120000, 0x0, 0x1000, 0x0, 0xff120000] )
                    # xen_reg.value.extend( [0x0, 0xff130000, 0x0, 0x1000, 0x0, 0xff130000] )
                    # xen_reg.value.extend( [0x0, 0xff140000, 0x0, 0x1000, 0x0, 0xff140000] )

                    n = n + xen_reg
                except Exception as e:
                    if verbose > 3:
                        print( "[ERROR]: %s" % e )


    # resolve() isn't strictly required, but better to be safe
    xen_tree.strict = False
    xen_tree.resolve()

    if output:
        xen_tree.output = open( output, "w")
        xen_tree.print()
    else:
        if verbose:
            xen_tree.output = None
            xen_tree.print()

    return True
