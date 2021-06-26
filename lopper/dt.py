#/*
# * Copyright (c) 2021 Xilinx Inc. All rights reserved.
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
from importlib.machinery import SourceFileLoader
import tempfile
from enum import Enum
import atexit
import textwrap
from collections import UserDict
from collections import OrderedDict

from string import printable

from devicetree import dtlib
from devicetree import edtlib
from lopper.fmt import LopperFmt

import lopper.base

class LopperDT(lopper.base.lopper_base):
    """The Lopper Class contains static methods for manipulating DT
    """

    @staticmethod
    def dt_compile( dts_file, i_files ="", includes="", force_overwrite=False, outdir="./",
                    save_temps=False, verbose=0, enhanced = True ):


        preprocessed_name = LopperDT.dt_preprocess( dts_file, includes, outdir, verbose )

        # we don't really 'compile' with dtlib, but it read/parses the source
        # file and creates an object.
        dt = dtlib.DT( preprocessed_name )

        # if we get a schema in the future:
        # dt = edtlib.DT( dts_file )

        if verbose > 4:
            print( "[DBG+++]: dumping device tree:" )
            for node in dt.node_iter():
                print( node.name )
                for p in  node.props:
                    print( "   %s" % p )

        return dt

    @staticmethod
    def node_getname( fdt, node_number_or_path ):
        """Gets the FDT name of a node

        Args:
            fdt (fdt): flattened device tree object
            node_number_or_path: node number or path

        Returns:
            string: name of the node, or "" if node wasn't found
        """

        name = ""
        try:
            node = fdt.get_node( node_number_or_path )
            name = node.name
        except:
            pass

        return name

    @staticmethod
    def node_type( fdt, node_offset, verbose=0 ):
        """Utility function to get the "type" of a node

        A small wrapper around the compatible property, but we can use this
        instead of directly getting compatible, since if we switch formats or if
        we want to infer anything based on the name of a node, we can hide it in
        this routine

        Args:
            fdt (fdt): flattened device tree object
            node_offset (int): node number
            verbose (int): verbose output level

        Returns:
            string: compatible string of the node if successful, otherwise ''
        """
        rt = ""
        try:
            node = fdt.get_node( node_offset )
            rt = node.props["compatible"].to_string()
        except Exception as e:
            pass

        return rt

    @staticmethod
    def node_by_phandle( fdt, phandle, verbose=0 ):
        """Get a node offset by a phandle

        Thin wrapper around the libfdt routine. The wrapper provides
        consistent exception handling and verbosity level handling.

        Args:
            fdt (fdt): flattened device tree object
            phandle(int): phandle to use as lookup key
            verbose(bool,optional): verbosity level. Deafult is 0.

        Returns:
            int: if > 0, the node that was found. -1 if node was not found.
        """
        anode = None

        try:
            anode = fdt.phandle2node( phandle )
        except:
            pass

        return anode

    @staticmethod
    def node_find_by_name( fdt, node_name, starting_node = 0, multi_match=False ):
        """Finds a node by its name (not path)

        Searches for a node by its name, and returns the offset of that same node
        Note: use this when you don't know the full path of a node

        Args:
            fdt (fdt): flattened device tree object
            node_name (string): name of the node
            starting_node (int): node number to use as the search starting point
            multi_match (bool,optional): flag to indicate if more than one matching
                                         node should be found, default is False

        Returns:
            tuple: first matching node, list of matching nodes. -1 and [] if no match is found
        """

        matching_nodes = []
        matching_node = None

        search_active = False
        if starting_node == "/" or starting_node == 0:
            search_active = True

        for node in fdt.node_iter():
            if not search_active:
                if node.path == starting_node:
                    search_active = True

            if search_active:
                if node.name == node_name:
                    if not matching_nodes:
                        matching_node = node
                    matching_nodes.append( node )

        return matching_node, matching_nodes

    @staticmethod
    def export( dt, start_node_path = "/", verbose = False, strict = False ):
        """export a FDT to a description / nested dictionary

        This routine takes a FDT, a start node, and produces a nested dictionary
        that describes the nodes and properties in the tree.

        The dictionary contains a set of internal properties, as well as
        a list of standand properties to the node. Internal properties have
        a __ suffix and __ prefix.

        Child nodes are indexed by their absolute path. So any property that
        starts with "/" and is a dictionary, represents another node in the
        tree.

        In particular:
            - __path__ : is the absolute path fo the node, and is used to lookup
                         the target node
            - __fdt_name__ : is the name of the node and will be written to the
                             fdt name property
            - __fdt_phandle__ : is the phandle for the node

        All other "standard" properties are returned as entries in the dictionary.

        if strict is enabled, structural issues in the input tree will be
        flagged and an error triggered. Currently, this is duplicate nodes, but
        may be extended in the future

        Args:
            fdt (fdt): flattened device tree object
            start_node (string,optional): the starting node
            verbose (bool,optional): verbosity level
            strict (bool,optional): toggle validity checking

        Returns:
            OrderedDict describing the tree
        """
        # export a DT as a dictionary
        dct = OrderedDict()

        dct["__path__"] = start_node_path

        np = None
        p_dict = {}
        current_node = dt.get_node( start_node_path )
        if current_node:
            np = LopperDT.node_properties_as_dict( current_node )
            if np:
                dct.update(np)

        nodes = current_node.nodes

        nn = -1
        dct["__fdt_number__"] = nn
        if current_node == dt.root:
            dct["__fdt_name__"] = ""
        else:
            dct["__fdt_name__"] = current_node.name
        if "phandle" in current_node.props:
            bytes = current_node.props["phandle"].value
            dct["__fdt_phandle__"] = int.from_bytes(bytes, byteorder='big', signed=False)
        else:
            dct["__fdt_phandle__"] = -1

        if verbose:
            print( "[DBG]: lopper.dt export: " )
            print( "[DBG]:     nodes: %s" % (nodes) )
            print( "[DBG]:          props: %s" % np )

        for i,n in nodes.items():
            # Children are indexed by their path (/foo/bar), since properties
            # cannot start with '/'
            dct[n.path] = LopperDT.export( dt, n.path, verbose, strict )

        return dct

    @staticmethod
    def node_properties_as_dict( node, type_hints=True, verbose=0 ):
        """Create a dictionary populated with the nodes properties.

        Builds a dictionary that is propulated with a node's properties as
        the keys, and their values. Used as a utility routine to avoid
        multiple calls to check if a property exists, and then to fetch its
        value.

        Args:
            fdt (fdt): flattened device tree object
            node (int or string): either a node number or node path
            type_hints  (bool,optional): flag indicating if type hints should be returned
            verbose (int,optional): verbosity level. default is 0.

        Returns:
            dict: dictionary of the properties, if successfull, otherwise and empty dict
        """

        prop_dict = {}

        for p,v in node.props.items():
            property_val = LopperDT.property_value_decode( v.value, 0, LopperFmt.COMPOUND, LopperFmt.DEC )
            prop_dict[v.name] = property_val
            if type_hints:
                prop_dict['__{}_type__'.format(v.name)] = LopperDT.property_type_guess( v.value )

        return prop_dict

