#!/usr/bin/python3

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

def get_compatible_strings():
    print( "openamp,domain-v1" )

def is_compat( compat_string_to_test ):
    if re.search( "openamp,domain-v1", compat_string_to_test):
        return True
    return False

def process_domain( tgt_domain, sdt, verbose=0 ):
    tgt_node = Lopper.node_find( sdt.FDT, tgt_domain )
    cpu_prop_values = Lopper.get_prop( sdt.FDT, tgt_node, "cpus", "compound" )

    if cpu_prop_values == "":
        sys.exit(1)

    # the cpu handle is element 0
    cpu_prop = cpu_prop_values[0]
    cpu_node = sdt.FDT.node_offset_by_phandle( cpu_prop )

    if verbose:
        print( "[INFO]: cpu prop phandle: %s" % cpu_prop )
        print( "[INFO]: cpu node: %s" % cpu_node )

    ## We  need to delete any other nodes that have "compatible = cpus,cluster"
    ## and are Not the ones we just found in the chosen node. All we have is a phandle
    ##  so we need to:
    ##   1) find the nodes that are compatible with the cpus,cluster
    ##   2) check their phandle
    ##   3) delete if it isn't the one we just got
    xform_path = "/"
    prop = "cpus,cluster"
    code = """
p = get_prop( %%FDT%%, %%NODE%%, \"compatible\" )
if p and "%%%prop%%%" in p:
    ph = getphandle( %%FDT%%, %%NODE%% )
    if ph != %%%phandle%%%:
        %%TRUE%%
    else:
        %%FALSE%%
else:
    %%FALSE%%
"""
    code = code.replace( "%%%prop%%%", prop )
    code = code.replace( "%%%phandle%%%", str( cpu_prop ) )

    if verbose:
        print( "[INFO]: filtering on:\n------%s-------\n" % code )

    # the action will be taken if the code block returns 'true'
    Lopper.filter_node( sdt.FDT, xform_path, "delete", code, verbose )

    # we must re-find the domain node, since its numbering may have
    # changed due to the filter_node deleting things
    tgt_node = Lopper.node_find( sdt.FDT, tgt_domain )

    # "access" is a list of tuples: phandles + flags
    access_list = Lopper.get_prop( sdt.FDT, tgt_node, "access", "compound" )
    if not access_list:
        if verbose:
            print( "[INFO]: no access list found, skipping ..." )

        pass
    else:
        #print( "[INFO]: converted access list: %s" % access_list )

        # although the access list is decoded as a list, it is actually tuples, so we need
        # to get every other entry as a phandle, not every one.
        for ph in access_list[::2]:
            #ph = int(ph_hex, 16)
            #print( "processing %s" % ph )
            anode = sdt.FDT.node_offset_by_phandle( ph )
            node_type = Lopper.get_prop( sdt.FDT, anode, "compatible" )
            node_name = sdt.FDT.get_name( anode )
            node_parent = sdt.FDT.parent_offset(anode,QUIET_NOTFOUND)
            if re.search( "simple-bus", node_type ):
                if verbose > 1:
                    print( "[INFO]: access is a simple-bus (%s), leaving all nodes" % node_name)
            else:
                # The node is *not* a simple bus, so we must do more processing

                # a) If the node parent is something other than zero, the node is nested, so
                #    we have to do more processing. Note: this should be recursive eventually, but
                #    for now, we keep it simple
                #print( "node name: %s node parent: %s" % (node_name, node_parent) )
                if node_parent:
                    parent_node_type = Lopper.get_prop( sdt.FDT, node_parent, "compatible" )
                    parent_node_name = sdt.FDT.get_name( node_parent )
                    node_grand_parent = sdt.FDT.parent_offset(node_parent,QUIET_NOTFOUND)
                    if not parent_node_type:
                        # is it a special name ?
                        if re.search( "reserved-memory", parent_node_name ):
                            parent_node_type = "reserved-memory"
                        else:
                            # if there's no type and no special name, we need to bail
                            continue

                    if re.search( "simple-bus", parent_node_type ):
                        if verbose > 1:
                            print( "[INFO]: node parent is a simple-bus (%s), dropping sibling nodes" % parent_node_name)
                        # TODO: this node path must be constructed better than this ...
                        parent_subnodes = Lopper.get_subnodes( sdt.FDT, "/" + parent_node_name )
                        for n in parent_subnodes:
                            if re.search( node_name, n ):
                                pass # do nothing for now
                            else:
                                # we must delete this node
                                tgt_node_path = "/" + parent_node_name + "/" + n
                                try:
                                    tgt_node_id = sdt.FDT.path_offset( tgt_node_path )
                                except:
                                    tgt_node_id = 0
                                if tgt_node_id:
                                    sdt.node_remove( tgt_node_id )
                    elif re.search( "reserved-memory", parent_node_type ):
                        print( "AAAAAAAAAA reserved memory!!: %s" % node_name)
                        # delete all other memory nodes ?? ...nope. but at some point we need to delete the ones that we don't have access to. is it the flags ? should we refcount it ?


    # we must re-find the domain node, since its numbering may have
    # changed due to the filter_node deleting things
    tgt_node = Lopper.node_find( sdt.FDT, tgt_domain )

    memory_hex = Lopper.get_prop( sdt.FDT, tgt_node, "memory", "compound:hex" )
    memory_int = Lopper.get_prop( sdt.FDT, tgt_node, "memory", "compound" )

    # This may be moved to the top of the domain process and then
    # when we are processing cpus and bus nodes, we can apply the
    # memory to ranges <>, etc, and modify them accordingly.
    if verbose > 1:
        print( "[INFO]: memory property: %s" % memory_hex )

        # 1) find if there's a top level memory node
        memory_node = Lopper.node_find( sdt.FDT, "/memory" )
        if memory_node:
            if verbose:
                print( "[INFO]: memory node found (%s), modifying to match domain memory" % memory_node )

            # 2) modify that memory property to match the node we have here
            # memprop_old = sdt.FDT.getprop(memory_node, 'reg' )
            # num_bits = len(memprop_old)
            # a = 0
            # b = 1
            # c = 0
            # d = 1
            # val = a.to_bytes(4,byteorder='big') + b.to_bytes(4,byteorder='big') + c.to_bytes(4,byteorder='big') + d.to_bytes(4,byteorder='big')

            sdt.FDT.setprop(memory_node, 'reg', Lopper.encode_byte_array(memory_int))
