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
from lopper_tree import *

sys.path.append(os.path.dirname(__file__))
import cdotypes

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

# nodetype mapping
cdo_basenodeid = 0xc4100000

# map a device tree "type" to a cdo nodeid/type list
cdo_nodetype = {
                 "root" : [ "0x10", "root" ],
                 "cpus,cluster" : [ "0x1b", "device" ],
                 "interrupt-controller.*" : [ "0x1c", "device" ],
                 "subclock" : [ "0x1e", "clock" ],
                 ".*clk" : [ "0x1f", "clock-controller" ],
                 ".*clock" : [ "0x1d", "clock" ]
               }

def cdo_parent_word( parent_count, width, shift ):
    # After the node ID, the CMD_PM_ADD* have a word of the following format:
    #      <Reserved> <Number of Parents> <Width> <Shift>
    reserved = 0

    word_one = "0x{:02d}{:02d}{:02d}{:02d}".format( reserved, parent_count, width, shift )

    return word_one

#
# generate a nodeid from a compatible string
#
# nodeid is of the format: <class:24-31><subclass:16-23>:<type:8-15><index 0:7>
#
# TODO: make this smarter and produce nodes with proper nodeid structure
#
def cdo_nodeid( nodeoffset, device_tree_type ):
    try:
        x = cdo_nodetype[device_tree_type]
        x = x[0]
    except:
        x = hex(0 | nodeoffset)
        for k in cdo_nodetype.keys():
            if re.search( k, str(device_tree_type) ):
                x = cdo_nodetype[k]
                x = x[0]

    #
    # note: the node offset can't be a longterm solution, but we'll use it
    # for now to make sure we are getting more unique nodeids, eventually this
    # is where the index goes in the nodeid.
    #
    idx = (int(x,16) << 8) | (cdo_basenodeid) | (nodeoffset & 0xff)
    return idx

# map a compatible string to a cdo type
def cdo_type( device_tree_type ):
    try:
        x = cdo_nodetype[device_tree_type]
        x = x[1]
    except:
        x = "unknown_type"
        for k in cdo_nodetype.keys():
            if re.search( k, str(device_tree_type) ):
                x = cdo_nodetype[k]
                x = x[1]
    return x

def cdo_write( node, sdt, outfile, options ):
    try:
        verbose = options['verbose']
    except:
        verbose = 0

    # todo: we could have a force flag and not overwrite this if it exists
    if outfile != sys.stdout:
        output = open( outfile, "w")

    if verbose > 1:
        print( "[INFO]: cdo write: {}".format(outfile) )

    # we are going to tag the tree with some attributes, so make a copy
    cdo_tree = LopperTree( sdt.FDT, True )

    print( "# Lopper CDO export", file=output )

    # ref-count clock nodes. this will be used below when putting out the CDO
    # clocks and subclocks
    cdo_tree.ref( 0 )
    for n in cdo_tree:
        node_type = n.type
        if node_type:
            cdo_t = cdo_type( node_type[0] )

        if re.match( "^clock-controller$", cdo_t ):
            sub_clocks = n["clock-names"].value
            # TODO: we have to make sure these don't get output again
            for c in sub_clocks:
                cn = sdt.tree["/.*" + c]
                if cn:
                    cn.ref = 1

    # for depth, nodeoffset, nodename in node_list:
    for n in cdo_tree:
        if not n.name:
            nodename = "root"
            node_type= "root"
        else:
            nodename = n.name
            # get the compatible string of the node, that's our "type", which we'll
            # map to the CDO class, subclass and type fields.
            node_type = n.type

        #
        # we need to track the node IDs at depth > 1
        #
        #   depth 1: the parent is the root node
        #   depth 2: the parent is whatever the most recent node 1 was
        #   depth 3: the parent is whatever the most recent node 2 was
        #   ... etc
        #
        if not node_type:
            # we need to be at depth 2 or more, or we are just going to find the root node
            # .. and that type isn't all that useful
            if n.parent and n.parent.parent:
                print( "# [DBG]+: trying to infer type from parent node)", file=output )
                # try and infer the type from the parents type
                ptype = n.parent.parent.type
                if ptype:
                    print("# parent type: %s" % ptype, file=output )
                    node_type = ptype

        nodeid = cdo_nodeid( n.number, str(node_type) )
        # track the nodeid in the current node so we can reference it in its child nodes
        n.cdo_nodeid = nodeid
        n.cdo_type = node_type
        n.cdo_nodename = nodename

        if node_type:
            print( "# node start: [%s]:%s depth: %s offset: %s type: %s (%s)" %
                   (hex(nodeid), nodename, n.depth, hex(n.number), str(node_type), type(node_type) ) , file=output)

            cdo_t = cdo_type( node_type )

            print( "# node cdo mapping: %s" % cdo_t, file=output )

            #
            # TODO: these will be in routines eventually .. but for now, its a
            # giant set of if statements
            #

            reserved = 0
            parent_count = 1
            width = 0
            shift = 0

            if re.match( "root", cdo_t ):
                pm_add_word_one = cdo_parent_word( parent_count, width, shift )
                # note: since we are the root node, "parent" is 0x0, which has already been
                #       initialized in the parent tracker
                print( "# root node", file=output )
                print( "pm_add_node {0} {1} {2}".format( hex(nodeid), pm_add_word_one, hex(0)), file=output )

            elif re.match( "device", cdo_t ):
                # if we see a cluster, add a power domain/island
                pm_add_word_one = cdo_parent_word( parent_count, width, shift )

                print( "# device add: pm_add_node <nodeid> <res parent width shift> <parent>", file=output )
                print( "# cpu cluster, depth: {0}".format(n.depth), file=output)
                print( "pm_add_node {0} {1} {2}".format( hex(nodeid), pm_add_word_one, hex(n.parent.cdo_nodeid)), file=output )

            elif re.match( "domain", cdo_t ):
                # note: we could also put the skipping in the gather routine, so this can be
                #       a simple iteration
                print( "# skipping domain node: %s" % nodename, file=output )
            elif re.match( "^clock$", cdo_t ):
                ref_count = n.ref
                if ref_count < 0:
                    # there are no other references to this clock, so we put it out as
                    # a standalone node

                    # these aren't correct for a clock add, but we leave this call as a reminder
                    pm_add_word_one = cdo_parent_word( parent_count, width, shift )

                    print( "# clock add: pm_add_node <nodeid> <control reg addr> <flags> <power domain ID> ..", file=output)
                    print( "bb pm_add_node {0} <control reg addr> <flags> <power domain ID> ".format( hex(nodeid)), file=output )

            elif re.match( "^clock-controller$", cdo_t ):
                # we actually now need to iterate and issue parent adds for any
                # clocks that are in the "clock-names"

                # print the parent clock controller, and then we check for sub clocks
                print( "# clock controller: pm_add_node <nodeid> <control reg addr> <flags> <power domain ID> ..", file=output )
                # TODO: maybe unify this with the clock add (unreferenced) above, i.e.
                #       move them into a function ...

                # these aren't correct for a clock add, but we leave this call as a reminder
                pm_add_word_one = cdo_parent_word( parent_count, width, shift )
                print( "pm_add_node {0} <control reg addr> <flags> <power domain ID> ".format( hex(nodeid)), file=output )

                # sub_clocks = Lopper.prop_get( fdt, nodeoffset, "clock-names" )
                sub_clocks = n["clock-names"].value
                for c in sub_clocks:
                    cn = sdt.tree["/.*" + c]
                    if cn:
                        # we put out a pm_add for each subclock
                        sub_node_type = "subclock"
                        sub_nodeid = cdo_nodeid( cn.number, sub_node_type )

                        sub_clocktype = cn.type[0]
                        sub_clock_mask = 0x0
                        if re.match( "fixed", sub_clocktype ):
                            sub_clock_mask = 0x1
                        else:
                            sub_clock_mask = 0x2

                        # these aren't correct for a clock add, but we leave this call as a reminder
                        pm_add_word_one = cdo_parent_word( parent_count, width, shift )
                        print( "# clock subnode: pm_add_node <parent clock id> <clock type> <control reg addr> <reserved> <flags>", file=output )
                        print( "pm_add_node {0} {1} 0x0 0x0 0x0".format( hex(nodeid), hex(sub_clock_mask)), file=output )
            else:
                pm_add_word_one = cdo_parent_word( parent_count, width, shift )
                print( "# unknown type, depth: {0}".format(n.depth), file=output)
                print( "pm_add_node {0} <flags> {1}".format( hex(nodeid), hex(n.parent.cdo_nodeid)), file=output )

            print( "# node end: [%s]:%s" % (hex(nodeid), nodename), file=output )
        else:
            # TODO: what do to about these ? For now, we just log them
            print( "# INFO: skipping node with no type: %s (depth %s)" % (nodename,n.depth), file=output)
            print( "#       parent was: %s" % n.parent, file=output )


    return True



