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
        print( "[ERROR][extract-xen]: no extracted tree detcted, returning" )
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
        print( "[ERROR][xen-extract]: no extracted node detected" )
        return False

    # rename the containing node from /extracted to /passthrough
    extracted_node.name = "passthrough"

    # copy sdt root compatibles into extracted node
    root_compat = sdt.tree["/"]["compatible"].value
    root_compat.append(extracted_node["compatible"].value)
    extracted_node["compatible"].value = root_compat

    # walk the nodes in the tree, and look for the property "extracted,path"
    # and update it to "xen,path" (when conditions are met)
    for n in xen_tree:
        try:
            p = n["extracted,path"]
            # if there's an iommu in the node, we convert to xen,path, otherwise
            # do nothing
            iommu = n["iommus"]
            # we'll have thrown an exception if the property wasn't there, so this
            # only runs in the sucess case
            p.name = "xen,path"
        except:
            # TODO: we may want to check for nodes that have "reg" and use that
            #       as a secondary trigger to convert to xen,path .. but that still
            #       may be too broad
            pass

        try:
            ip = n["interrupt-parent"]
            n["interrupt-parent"].value = "0xfde8"
            if verbose:
                print( "[INFO][extract-xen]: %s interrupt parent found, updating" % n.name  )

            # this is a known non-existent phandle, we need to inhibit
            # phandle resolution and just have the number used
            ip.phandle_resolution = False
            ip.resolve( strict = False )
        except:
            pass

    if target_node_name:
        nodes_to_delete = []
        for n in xen_tree:
            if n.name == target_node_name:
                # the target node may not have had a iommus property, but we do
                # always want it to have a xen,path property, so we force it here
                p = n["extracted,path"]
                p.name = "xen,path"

                # is there an iommu property ? if so, that tells us what to do about the
                # without iommu
                need_force_assign = False
                try:
                    iommus_prop = n["iommus"]

                    # remove the property and all the other nodes it may have brought in
                    refs = iommus_prop.resolve_phandles()
                    n - iommus_prop

                    for r in refs:
                        nodes_to_delete.append( r )
                except:
                    need_force_assign = True


                if need_force_assign:
                    np = LopperProp( "xen,force-assign-without-iommu" )
                    np.value = 1
                    n + np

                # reach into the SDT and add "xen,passthrough" to the device
                sdt_device_path = n["extracted,path"].value
                if sdt_device_path:
                    print( "[INFO][extract-xen]: updating sdt with passthrough property" )
                    x_pass = LopperProp( "xen,passthrough" )
                    x_pass.value = ""
                    sdt.tree[sdt_device_path] + x_pass

                # check for the reg property
                try:
                    reg = n["reg"]
                    if verbose:
                        print( "[INFO][extract-xen]: reg found: %s copying and extending to xen,reg" % reg )
                    # make a xen,reg from it
                    xen_reg = LopperProp( "xen,reg" )

                    # split reg.value into chunks (memory regions) of 4 items (2 for address, 2 for size)
                    reg_chunks  = [reg.value[x:x+4] for x in range(0, len(reg.value), 4)]

                    # xen,reg is an array of <phys_addr size guest_addr> and we always
                    # set guest_addr to phys_addr. Iterate over splitted memory regions
                    # and fill in xen,reg according to the format mentioned above
                    for i in range(len(reg_chunks)):
                        addr = [reg_chunks[i][0], reg_chunks[i][1]]
                        size = [reg_chunks[i][2], reg_chunks[i][3]]
                        xen_reg.value.extend(addr)
                        xen_reg.value.extend(size)
                        xen_reg.value.extend(addr)

                    # magic. these need to be generated in the future
                    # xen_reg.value.extend( [0x0, 0xff110000, 0x0, 0x1000, 0x0, 0xff110000] )
                    # xen_reg.value.extend( [0x0, 0xff120000, 0x0, 0x1000, 0x0, 0xff120000] )
                    # xen_reg.value.extend( [0x0, 0xff130000, 0x0, 0x1000, 0x0, 0xff130000] )
                    # xen_reg.value.extend( [0x0, 0xff140000, 0x0, 0x1000, 0x0, 0xff140000] )

                    n = n + xen_reg
                except Exception as e:
                    if verbose > 3:
                        print( "[ERROR]]extract-xen]: %s" % e )

        if nodes_to_delete:
            for n in nodes_to_delete:
                if verbose:
                    print( "[INFO][extract-xen]: deleting node (referencing node was removed): %s" % n.abs_path )
                xen_tree - n

    # resolve() isn't strictly required, but better to be safe
    xen_tree.strict = False
    xen_tree.resolve()

    if output:
        sdt.write( xen_tree, output, True, True )
    else:
        if verbose:
            xen_tree.output = None
            xen_tree.print( sys.stdout )

    return True
