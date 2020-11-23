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
from importlib.machinery import SourceFileLoader
import tempfile
from enum import Enum
import atexit
import textwrap
from collections import UserDict
from collections import OrderedDict

from string import printable



import libfdt
from libfdt import Fdt, FdtException, QUIET_NOTFOUND, QUIET_ALL

# used in encode/decode routines
class LopperFmt(Enum):
    """Enum class to define the types and encodings of Lopper format routines
    """
    SIMPLE = 1
    COMPOUND = 2
    HEX = 3
    DEC = 4
    STRING = 5
    MULTI_STRING = 6
    UINT8 = 7
    UINT32 = 8
    UINT64 = 9
    EMPTY = 10
    UNKNOWN = 11

# general retry count
MAX_RETRIES = 3

class Lopper:
    """The Lopper Class contains static methods for manipulating device trees

    Use the lopper methods when manipulating device trees (in particular
    libfdt FDT objects) or SystemDeviceTree classes.

    """

    @staticmethod
    def fdt_copy( fdt ):
        """Copy a fdt

        Creats a new FDT that is a copy of the passed one.

        Args:
            fdt (FDT): reference FDT

        Returns:
            fdt: The newly created FDT
        """

        return Fdt( fdt.as_bytearray() )

    @staticmethod
    def fdt( size=None, other_fdt=None ):
        """Create a new FDT

        Creats a new FDT of a passed size. If other_fdt is passed, it
        is used as the start size of the fdt.

        If no size or other fdt is passed, 128 bytes is the default
        size

        Args:
            size (int,optional): size in bytes of the FDT
            other_fdt (FDT,optional): reference FDT for size

        Returns:
            fdt: The newly created FDT
        """

        fdt = None

        if other_fdt:
            size = other_fdt.totalsize()
        else:
            if not size:
                # size is in bytes
                size = 128

            fdt = libfdt.Fdt.create_empty_tree( size )

        return fdt

    @staticmethod
    def node_getname( fdt, node_number_or_path ):
        """Gets the FDT name of a node

        Args:
            fdt (fdt): flattened device tree object
            node_number_or_path: node number or path

        Returns:
            string: name of the node, or "" if node wasn't found
        """
        node_number = -1
        node_path = ""
        try:
            node_number = int(node_number_or_path)
            node_path = Lopper.node_abspath( fdt, node_number_or_path )
        except ValueError:
            node_number = Lopper.node_find( fdt, node_number_or_path )
            node_path = node_number_or_path
        try:
            name = fdt.get_name( node_number )
        except:
            name = ""

        return name

    @staticmethod
    def node_setname( fdt, node_number_or_path, newname ):
        """Sets the FDT name of a node

        Args:
            fdt (fdt): flattened device tree object
            node_number_or_path: node number or path
            newname (string): name of the node

        Returns:
            boolean: True if the name was set, False otherwise
        """
        node_number = -1
        node_path = ""
        try:
            node_number = int(node_number_or_path)
            node_path = Lopper.node_abspath( fdt, node_number_or_path )
        except ValueError:
            node_number = Lopper.node_find( fdt, node_number_or_path )
            node_path = node_number_or_path

        retval = False
        if node_number == -1:
            return retval

        for _ in range(MAX_RETRIES):
            try:
                fdt.set_name( node_number, newname )
                retval = True
            except:
                fdt.resize( fdt.totalsize() + 1024 )
                continue
            else:
                break

        return retval


    @staticmethod
    def node_find( fdt, node_prefix ):
        """Finds a node by its prefix

        Args:
            fdt (fdt): flattened device tree object
            node_prefix (string): device tree path

        Returns:
            int: node number if successful, otherwise -1
        """
        try:
            node = fdt.path_offset( node_prefix )
        except:
            node = -1

        return node

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
        rt = Lopper.property_get( fdt, node_offset, "compatible" )

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
        anode = -1
        try:
            anode = fdt.node_offset_by_phandle( phandle )
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

        nn = starting_node
        matching_nodes = []
        matching_node = -1
        # short circuit the search if they are looking for /
        if node_name == "/":
            depth = -1
        else:
            depth = 0

        while depth >= 0:
            nn_name = fdt.get_name(nn)
            if nn_name:
                # we search to support regex matching
                if re.search( node_name, nn_name ):
                    matching_nodes.append(nn)
                    if matching_node == -1:
                        # this is the first match, so we capture the number
                        matching_node = nn

                    if not multi_match:
                        depth = -1
                    else:
                        # match, but mult-match is on .. get the next node
                        nn, depth = fdt.next_node(nn, depth, (libfdt.BADOFFSET,))
                else:
                    # no match, get the next node
                    nn, depth = fdt.next_node(nn, depth, (libfdt.BADOFFSET,))
            else:
                # no name, get the next node
                nn, depth = fdt.next_node(nn, depth, (libfdt.BADOFFSET,))

        return matching_node, matching_nodes

    @staticmethod
    def node_find_by_regex( fdt, node_regex, starting_node = 0, multi_match=False, paths_not_numbers=False ):
        """Finds a node by a regex /path/<regex>/<name>

        Searches for nodes that match a regex (path + name).

        Note: if you pass the name of a node as the regex, you'll get a list of
              that node + children
        Note: if you pass no regex, you'll get all nodes from the starting point
              to the end of the tree.

        Args:
            fdt (fdt): flattened device tree object
            node_regex (string): regex to use for comparision
            starting_node (int): node number to use as the search starting point
            multi_match (bool,optional): flag to indicate if more than one matching
                                         node should be found, default is False
            paths_not_numbers (bool,optional): flag to request paths, not node numbers
                                               be returned

        Returns:
            tuple: first matching node, list of matching nodes. -1 and [] if no match is found
        """

        nn = starting_node
        matching_nodes = []
        matching_node = -1
        depth = 0

        while depth >= 0:
            nn_name = fdt.get_name(nn)
            node_path = ""
            if nn > 0:
                node_path = Lopper.node_abspath( fdt, nn )

            if nn_name:
                # we search to support regex matching
                if re.search( node_regex, node_path ):
                    matching_nodes.append(nn)
                    if matching_node == -1:
                        # this is the first match, so we capture the number
                        matching_node = nn

                    if not multi_match:
                        depth = -1
                    else:
                        # match, but mult-match is on .. get the next node
                        nn, depth = fdt.next_node(nn, depth, (libfdt.BADOFFSET,))
                else:
                    # no match, get the next node
                    nn, depth = fdt.next_node(nn, depth, (libfdt.BADOFFSET,))
            else:
                # no name, get the next node
                nn, depth = fdt.next_node(nn, depth, (libfdt.BADOFFSET,))

        # convert everything to paths if requested. This could have been in the
        # loop, but let's keep it simple :D
        if paths_not_numbers:
            matching_node = Lopper.node_abspath( fdt, matching_node )
            matching_node_list = matching_nodes
            matching_nodes = []
            for m in matching_node_list:
                matching_nodes.append( Lopper.node_abspath( fdt, m ) )

        return matching_node, matching_nodes

    @staticmethod
    def node_prop_check( fdt, node_name, property_name ):
        """Check if a node contains a property

        Boolean check to see if a node contains a property.

        The node name does not need to be a full path or path prefix, since
        the node will be searched starting at the root node, which means that
        a non-unique node name could match multiple nodes.

        Args:
            fdt (fdt): flattened device tree object
            node_name (string): name of the node
            property_name (string): name of the property to check

        Returns:
            bool: True if the node has the property, otherwise False
        """

        node = Lopper.node_find( fdt, node_name )
        if node == -1:
            node, nodes = Lopper.node_find_by_name( fdt, node_name )

        if node == -1:
            return False

        try:
            fdt.getprop( node, property_name )
        except:
            return False

        return True

    # A thin wrapper + consistent logging and error handling around FDT's
    # node delete
    @staticmethod
    def node_remove( fdt, target_node_offset, verbose = 0 ):
        """remove a node from the device tree

        Thin wrapper and consistent logging around libfdt's node delete.

        Args:
           fdt (fdt): flattended device tree
           target_node_offset (int): offset of the node to be deleted

        Returns:
           Boolean: True if node is removed, false otherwise

        """
        target_node_name = fdt.get_name( target_node_offset )

        if verbose > 1:
            print( "[NOTE]: deleting node: %s" % target_node_name )

        try:
            fdt.del_node( target_node_offset, True )
        except:
            return False

        return True


    @staticmethod
    def node_add( fdt_dest, node_full_path, create_parents = True, verbose = 0 ):
        """Add an empty node to a flattened device tree

        Creates a new node in a flattened devide tree at a given path. If
        desired a node structure (aka parents) will be created as part of
        adding the node at the specified path.

        Args:
            fdt_dest (fdt): flattened device tree object
            node_full_path (string): fully specified path (and name) of the node to create
            create_parents (bool,optional): Should parent nodes be created. Default is True.
                True: create parents as required, False: error if parents are missing
            verbose (int,optional): verbosity level. default is 0.

        Returns:
            int: The node offset of the created node, if successfull, otherwise -1
        """
        prev = 0
        for p in os.path.split( node_full_path ):
            n = Lopper.node_find( fdt_dest, p )
            if n < 0:
                if create_parents:
                    for _ in range(MAX_RETRIES):
                        try:
                            p = p.lstrip( '/' )
                            prev = fdt_dest.add_subnode( prev, p )
                        except Exception as e:
                            fdt_dest.resize( fdt_dest.totalsize() + 1024 )
                            continue
                        else:
                            break
            else:
                prev = n

        return prev

    @staticmethod
    def node_properties( fdt, node_number_or_path ):
        """Get the list of properties for a node

        Gather the list of FDT properties for a given node.

        Args:
            fdt (fdt): flattened device tree object
            node_number_or_path: (string or int): node number or full path to
                                 the target node.

        Returns:
            list (FDT prop): The properties of the node [] if no props
        """
        prop_list = []
        node = Lopper.node_number( fdt, node_number_or_path )
        if node == -1:
            return prop_list

        poffset = fdt.first_property_offset(node, QUIET_NOTFOUND)
        while poffset > 0:
            prop = fdt.get_property_by_offset(poffset)
            prop_list.append(prop)
            poffset = fdt.next_property_offset(poffset, QUIET_NOTFOUND)

        return prop_list

    @staticmethod
    def nodes( fdt, node_number_or_path, abs_paths = True ):
        """Get the nodes of a tree from a starting point

        Gather the list nodes in the tree from a particular starting point

        Args:
            fdt (fdt): flattened device tree object
            node_number_or_path: (string or int): node number or full path to
                                 the target node.
            abs_paths (boolean, optional): indicate if absolute paths should be returned

        Returns:
            list (strings): The nodes, [] if no nodes
        """
        node_list = []

        node = Lopper.node_number( fdt, node_number_or_path )
        if node == -1:
            return node_list

        depth = 0
        while depth >= 0:
            if abs_paths:
                node_list.append( Lopper.node_abspath( fdt, node ) )
            else:
                node_list.append( Lopper.node_getname( fdt, node ) )

            node, depth = fdt.next_node(node, depth, (libfdt.BADOFFSET,))

        return node_list

    @staticmethod
    def node_subnodes( fdt, node_number_or_path, abs_paths = True ):
        """Get the list of properties for a node

        Gather the list of FDT properties for a given node.

        Args:
            fdt (fdt): flattened device tree object
            node_number_or_path: (string or int): node number or full path to
                                 the target node.
            abs_paths (boolean, optional): indicate if absolute paths should be returned

        Returns:
            list (strings): The subnodes, [] if no subnodes
        """
        node_list = []
        node = Lopper.node_number( fdt, node_number_or_path )
        if node == -1:
            return node_list

        offset = fdt.first_subnode(node, QUIET_NOTFOUND)
        while offset > 0:
            if abs_paths:
                node_list.append( Lopper.node_abspath( fdt, offset ) )
            else:
                node_list.append( Lopper.node_getname( fdt, offset ) )

            offset = fdt.next_subnode( offset, QUIET_NOTFOUND )

        return node_list

    @staticmethod
    def node_parent( fdt, node_number_or_path ):
        parent = -1
        node_number = Lopper.node_number( fdt, node_number_or_path )
        if node_number > 0:
            parent = fdt.parent_offset( node_number, QUIET_NOTFOUND )

        return parent

    @staticmethod
    def node_properties_as_dict( fdt, node, verbose=0 ):
        """Create a dictionary populated with the nodes properties.

        Builds a dictionary that is propulated with a node's properties as
        the keys, and their values. Used as a utility routine to avoid
        multiple calls to check if a property exists, and then to fetch its
        value.

        Args:
            fdt (fdt): flattened device tree object
            node (int or string): either a node number or node path
            verbose (int,optional): verbosity level. default is 0.

        Returns:
            dict: dictionary of the properties, if successfull, otherwise and empty dict
        """

        prop_dict = {}

        # is the node a number ? or do we need to look it up ?
        node_number = -1
        node_path = ""
        try:
            node_number = int(node)
            node_path = Lopper.node_abspath( fdt, node )
        except ValueError:
            node_number = Lopper.node_find( fdt, node )
            node_path = node

        if node_number == -1:
            print( "[WARNING]: could not find node %s" % node_path )
            return prop_dict

        prop_list = Lopper.property_list( fdt, node_path )
        for p in prop_list:
            property_val = Lopper.property_get( fdt, node_number, p, LopperFmt.COMPOUND )
            prop_dict[p] = property_val

        return prop_dict

    @staticmethod
    def node_copy_from_path( fdt_source, node_source_path, fdt_dest, node_full_dest, verbose=0 ):
        """Copies a node from one FDT to another

        Copies a node between flattened device trees. The node (and
        properties) will be copied to the specified target device tree and
        path (ensure that a node does not already exist at the destination
        path).

        This routine is a wrapper around node_copy(), and will create a
        parent node structure in the destination fdt as required.

        Args:
            fdt_source (fdt): source flattened device tree object
            node_source_path: source device tree node path (fully specified)
            fdt_dest (fdt): destination flattened device tree object
            node_full_dest: destination device tree path for copied node (fully specified)
            verbose (int,optional): verbosity level. default is 0.

        Returns:
            bool: True if the node was copied, otherise, False
        """

        if verbose > 1:
            print( "[DBG ]: node_copy_from_path: %s -> %s" % (node_source_path, node_full_dest) )

        node_to_copy = Lopper.node_find( fdt_source, node_source_path )
        node_dest_path = os.path.dirname( node_full_dest )
        node_dest_name = os.path.basename( node_full_dest )

        if node_dest_path == "/":
            node_dest_parent_offset = 0
        else:
            # non root dest
            node_dest_parent_offset = Lopper.node_find( fdt_dest, node_dest_path )
            if node_dest_parent_offset == -1:
                node_dest_parent_offset = Lopper.node_add( fdt_dest, node_dest_path )
                if node_dest_parent_offset <= 0:
                    print( "[ERROR]: could not create new node" )
                    sys.exit(1)

        if node_to_copy:
            return Lopper.node_copy( fdt_source, node_to_copy, fdt_dest, node_dest_parent_offset, verbose )

        return False

    @staticmethod
    def node_copy( fdt_source, node_source_offset, fdt_dest, node_dest_parent_offset, verbose=0 ):
        """Copies a node from one FDT to another

        Copies a node between flattened device trees. The node (and
        properties) will be copied to the specified target device tree and
        path (ensure that a node does not already exist at the destination
        path).

        Note: the destination node parent must exist before calling this routine

        Properties are iterated, decoded and then copied (encoded) to the
        destination node. As such, the copies are limited by the
        decode/encode capabilities. If properties do not look correct in the
        copy, the decode/encode routines need to be checked.

        Args:
            fdt_source (fdt): source flattened device tree object
            node_source_offset: source device tree node offset
            fdt_dest (fdt): destination flattened device tree object
            node_dest_parent_offset: destination device parent node
            verbose (int,optional): verbosity level. default is 0.

        Returns:
            bool: True if the node was copied, otherise, False
        """

        old_depth = -1
        depth = 0
        nn = node_source_offset
        newoff = node_dest_parent_offset
        while depth >= 0:
            nn_name = fdt_source.get_name(nn)
            for _ in range(MAX_RETRIES):
                try:
                    copy_added_node_offset = fdt_dest.add_subnode( newoff, nn_name )
                except Exception as e:
                    fdt_dest.resize( fdt_dest.totalsize() + 1024 )
                    continue
                else:
                    break
            else:
                print( "[ERROR]: could not create subnode for node copy" )
                sys.exit(1)

            prop_offset = fdt_dest.subnode_offset( newoff, nn_name )

            if verbose > 2:
                print( "" )
                print( "[DBG+]: properties for: %s" % fdt_source.get_name(nn) )

            # TODO: Investigate whether or not we can just copy the properties
            #       byte array directly. Versus decode -> encode, which could
            #       introduce issues.
            prop_list = []
            poffset = fdt_source.first_property_offset(nn, QUIET_NOTFOUND)
            while poffset > 0:
                prop = fdt_source.get_property_by_offset(poffset)

                # we insert, not append. So we can flip the order of way we are
                # discovering the properties
                prop_list.insert( 0, [ poffset, prop ] )

                if verbose > 2:
                    print( "            prop name: %s" % prop.name )
                    print( "            prop raw: %s" % prop )

                if verbose > 2:
                    prop_val = Lopper.property_value_decode( prop, 0 )
                    if not prop_val:
                        prop_val = Lopper.property_value_decode( prop, 0, LopperFmt.COMPOUND )
                    print( "            prop decoded: %s" % prop_val )
                    print( "            prop type: %s" % type(prop_val))
                    print( "" )

                poffset = fdt_source.next_property_offset(poffset, QUIET_NOTFOUND)

            # loop through the gathered properties and copy them over. We are reversing
            # the order of the way we iterated them, due to the way that setprop inserts
            # at zero every time. If we don't flip the order the copied node will have
            # them in the opposite order!

            for poffset, prop in prop_list:
                prop_val = Lopper.property_get( fdt_source, nn, prop.name, LopperFmt.COMPOUND )
                Lopper.property_set( fdt_dest, prop_offset, prop.name, prop_val )


            old_depth = depth
            nn, depth = fdt_source.next_node(nn, depth, (libfdt.BADOFFSET,))

            # we need a new offset fo the next time through this loop (but only if our depth
            # changed)
            if depth >= 0 and old_depth != depth:
                newoff = fdt_dest.subnode_offset( newoff, nn_name )

        return True

    @staticmethod
    def node_abspath( fdt, nodeid ):
        """Get the absolute (fully specified) path of a nodes

        Args:
            fdt (fdt): flattened device tree object
            nodeid: device tree node offset

        Returns:
            string: node path, if successful, otherwise ""
        """

        if nodeid == 0:
            return "/"

        node_id_list = [nodeid]
        p = fdt.parent_offset(nodeid,QUIET_NOTFOUND)
        while p != 0:
            node_id_list.insert( 0, p )
            p = fdt.parent_offset(p,QUIET_NOTFOUND)

        retname = ""
        for id in node_id_list:
            retname = retname + "/" + fdt.get_name( id )

        return retname

    @staticmethod
    def node_number( fdt, node ):
        """Get the number for the passed node

        Return the node number of a node by its path, or just return
        its number if it is already a number. This is a normalization
        routine for node references

        Args:
            fdt (fdt): flattened device tree object
            node (string or ing): the name or node number to check

        Returns:
            string: node number, or -1 if the node doesn't exist
        """
        # is the node a number ? or do we need to look it up ?
        node_number = -1

        try:
            node_number = int(node)
        except ValueError:
            node_number = Lopper.node_find( fdt, node )
            if node_number == -1:
                node_number, matching_nodes = Lopper.node_find_by_name( fdt, node )

        if node_number == -1:
            print( "[WARNING]: could not find node %s" % node )

        return node_number

    @staticmethod
    def nodes_with_property( fdt, match_propname, match_regex="",
                             start_path="/", include_children=True, match_depth=0 ):
        """Get a list of nodes with a particular property

        Searches a device tree and returns a list of nodes that contain
        a given property.

        Matching is done by the existence of a property name in a node.

        If a match_regex is passed, then the value of the property is
        tested against the regex. If there's a match, then the node is
        added to the list.

        Args:
            fdt (fdt): source flattened device tree to search
            match_propname (string): target property name
            match_regex (string,optional): property value match regex. Default is ""
            start_path (string,optional): starting path in the device tree. Default is "/"
            include_children (bool,optional): should child nodes be searched. Default is True.
            match_depth (int): depth of the node, relative to the start path. Default is 0 (all nodes)

        Returns:
            list: list of matching nodes if successful, otherwise an empty list
        """

        # node_list = []
        depth = 0
        ret_nodes = []
        if start_path != "/":
            node, nodes = Lopper.node_find_by_name( fdt, start_path )
        else:
            node = 0

        if node < 0:
            print( "[WARNING]: could not find starting node: %s" % start_path )
            sys.exit(1)

        while depth >= 0:
            prop_val = Lopper.property_get( fdt, node, match_propname, LopperFmt.COMPOUND )
            if match_depth > 0:
                if match_depth != depth:
                    prop_val = None

            if prop_val:
                if match_regex:
                    for p in prop_val:
                        if re.search( match_regex, p ):
                            ret_nodes.append(node)
                else:
                    if match_propname == prop.name:
                        if not node in ret_nodes:
                            ret_nodes.append(node)

            node, depth = fdt.next_node(node, depth, (libfdt.BADOFFSET,))

        return ret_nodes

    @staticmethod
    def write_fdt( fdt_to_write, output_filename, overwrite=True, verbose=0, enhanced=False ):
        """Write a system device tree to a file

        Write a fdt (or system device tree) to an output file. This routine uses
        the output filename to determine if a module should be used to write the
        output.

        If the output format is .dts or .dtb, Lopper takes care of writing the
        output. If it is an unrecognized output type, the available assist
        modules are queried for compatibility. If there is a compatible assist,
        it is called to write the file, otherwise, a warning or error is raised.

        Args:
            fdt_to_write (fdt): source flattened device tree to write
            output_filename (string): name of the output file to create
            overwrite (bool,optional): Should existing files be overwritten. Default is True.
            verbose (int,optional): verbosity level to use.
            enhanced(bool,optional): whether enhanced printing should be performed. Default is False

        Returns:
            Nothing

        """
        if not output_filename:
            return

        if re.search( ".dtb", output_filename ):
            if verbose:
                print( "[INFO]: dtb output format detected, writing %s" % output_filename )

            byte_array = fdt_to_write.as_bytearray()

            if verbose:
                print( "[INFO]: writing output dtb: %s" % output_filename )

            o = Path(output_filename)
            if o.exists() and not overwrite:
                print( "[ERROR]: output file %s exists and force overwrite is not enabled" % output_filename )
                sys.exit(1)

            with open(output_filename, 'wb') as w:
                w.write(byte_array)

        elif re.search( ".dts", output_filename ):
            if verbose:
                print( "[INFO]: dts format detected, writing %s" % output_filename )

            o = Path(output_filename)
            if o.exists() and not overwrite:
                print( "[ERROR]: output file %s exists and force overwrite is not enabled" % output_filename )
                sys.exit(1)

            if enhanced:
                printer = LopperTreePrinter( fdt_to_write, True, output_filename, verbose )
                printer.exec()
            else:
                # write the device tree to a temporary dtb
                fp = tempfile.NamedTemporaryFile()
                byte_array = fdt_to_write.as_bytearray()
                with open(fp.name, 'wb') as w:
                    w.write(byte_array)

                Lopper.dtb_dts_export( fp.name, output_filename )

                # close the temp file so it is removed
                fp.close()
        else:
            print( "[INFO]: unknown file type (%s) passed for writing, skipping" % output_filename )

    @staticmethod
    def phandle_safe_name( phandle_name ):
        """Make the passed name safe to use as a phandle label/reference

        Args:
            phandle_name (string): the name to use for a phandle

        Returns:
            The modified phandle safe string
        """

        safe_name = phandle_name.replace( '@', '' )
        safe_name = safe_name.replace( '-', "_" )

        return safe_name

    # class variable for tracking phandle property formats
    phandle_possible_prop_dict = {}

    @staticmethod
    def phandle_possible_properties():
        """Get the diectionary of properties that can contain phandles

        dictionary of possible properties that can have phandles.
        To do the replacement, we map out the properties so we can locate any
        handles and do replacement on them with symbolic values. This format is
        internal only, and yes, could be the schema for the fields, but for now,
        this is easier.

        Each key (property name) maps to a list of: 'format', 'flag'
        flag is currently unused, and format is the following:

           - field starting with #: is a size value, we'll look it up and add 'x'
             number of fields based on it. If we can't find it, we'll just use '1'
           - phandle: this is the location of a phandle, size is '1'
           - anything else: is just a field we can ignore, size is '1'

        Args:
            None

        Returns:
            The phandle property dictionary
        """
        try:
            if Lopper.phandle_possible_prop_dict:
                return Lopper.phandle_possible_prop_dict
            else:
                return {
                    "DEFAULT" : [ 'this is the default provided phandle map' ],
                    "address-map" : [ '#ranges-address-cells phandle #ranges-address-cells #ranges-size-cells', 0 ],
                    "interrupt-parent" : [ 'phandle', 0 ],
                    "iommus" : [ 'phandle field' ],
                    "interrupt-map" : [ '#interrupt-cells phandle #interrupt-cells' ],
                    "access" : [ 'phandle flags' ],
                    "cpus" : [ 'phandle mask mode' ],
                    "clocks" : [ 'phandle:#clock-cells' ],
                }
        except:
            return {}

    @staticmethod
    def property_phandle_params( fdt, nodeoffset, property_name ):
        """Determines the phandle elements/params of a property

        Takes a property name and returns where to find a phandle in
        that property.

        Both the index of the phandle, and the number of fields in
        the property are returned.

        Args:
            fdt (FDT): flattened device tree
            nodeoffset (int): node number of the property
            property_name (string): the name of the property to fetch

        Returns:
            The the phandle index and number of fields, if the node can't
            be found 0, 0 are returned.
        """
        phandle_props = Lopper.phandle_possible_properties()
        if property_name in phandle_props.keys():
            property_description = phandle_props[property_name]
            property_fields = property_description[0].split()

            phandle_idx = 0
            phandle_field_count = 0
            for f in property_fields:
                if re.search( '#.*', f ):
                    field_val = Lopper.property_get( fdt, nodeoffset, f, LopperFmt.SIMPLE )
                    if not field_val:
                        field_val = 1

                    phandle_field_count = phandle_field_count + field_val
                elif re.search( 'phandle', f ):
                    phandle_field_count = phandle_field_count + 1
                    phandle_idx = phandle_field_count
                else:
                    # it's a placeholder field, count it as one
                    phandle_field_count = phandle_field_count + 1
        else:
            phandle_idx = 0
            phandle_field_count = 0

        return phandle_idx, phandle_field_count

    @staticmethod
    def property_resolve_phandles( fdt, nodeoffset, property_name ):
        """Resolve the targets of any phandles in a property

        Args:
            fdt (FDT): flattened device tree
            nodeoffset (int): node number of the property
            property_name (string): the name of the property to resolve

        Returns:
            A list of all resolved phandle nodes, [] if no phandles are present
        """

        phandle_targets = []

        idx, pfields = Lopper.property_phandle_params( fdt, nodeoffset, property_name )
        if idx == 0:
            return phandle_targets

        prop_val = Lopper.property_get( fdt, nodeoffset, property_name, LopperFmt.COMPOUND, LopperFmt.HEX )
        if not prop_val:
            return phandle_targets

        prop_type = type(prop_val)
        if prop_type == list:
            phandle_idxs = list(range(1,len(prop_val) + 1))
            phandle_idxs = phandle_idxs[idx - 1::pfields]

            element_count = 1
            element_total = len(prop_val)
            for i in prop_val:
                base = 10
                if re.search( "0x", i ):
                    base = 16
                try:
                    i_as_int = int(i,base)
                    i = i_as_int
                except:
                    pass

                if element_count in phandle_idxs:
                    try:
                        tgn = fdt.node_offset_by_phandle( i )
                        phandle_tgt_name = Lopper.phandle_safe_name( fdt.get_name( tgn ) )
                        phandle_targets.append( tgn )
                    except:
                        pass

                element_count = element_count + 1
        else:
            return phandle_targets

        return phandle_targets

    @staticmethod
    def node_remove_if_not_compatible( fdt, node_prefix, compat_string ):
        """Remove a node if incompatible with passed string

        Utility/cleanup function to remove all nodues under a node_prefix
        that are not compatible with a given string.

        Args:
            fdt (FDT): flattened device tree
            node_prefix (string): starting node path
            compat_strin (string): string for compat property comparison

        Returns:
            Nothing

        """

        if verbose:
            print( "[NOTE]: removing incompatible nodes: %s %s" % (node_prefix, compat_string) )

        node_list = []
        node_list = Lopper.get_subnodes( fdt, node_prefix )
        for n in node_list:
            # build up the device tree node path
            node_name = node_prefix + n
            node = fdt.path_offset(node_name)
            # print( "node name: %s" % fdt.get_name( node ) )
            prop_list = Lopper.property_list( fdt, node_name )
            # print( "prop list: %s" % prop_list )
            if "compatible" in prop_list:
                prop_value = fdt.getprop( node, 'compatible' )
                # split on null, since if there are multiple strings in the compat, we
                # need them to be separate
                vv = prop_value[:-1].decode('utf-8').split('\x00')
                if not compat_string in vv:
                    if verbose:
                        print( "[INFO]: deleting node %s" % node_name )
                    fdt.del_node( node, True )

    # source: libfdt tests
    @staticmethod
    def property_list( fdt, node_path ):
        """Read a list of properties from a node

        Args:
           node_path: Full path to node, e.g. '/subnode@1/subsubnode'

        Returns:
           List of property names for that node, e.g. ['compatible', 'reg']
        """
        prop_list = []
        node = fdt.path_offset(node_path)
        poffset = fdt.first_property_offset(node, QUIET_NOTFOUND)
        while poffset > 0:
            prop = fdt.get_property_by_offset(poffset)
            prop_list.append(prop.name)
            poffset = fdt.next_property_offset(poffset, QUIET_NOTFOUND)

        return prop_list

    @staticmethod
    def node_walk( fdt, verbose=0 ):
        """Walk nodes and gather a list

        Utility / reference routine for gathering a list of nodes.
        Always starts at node 0.

        Args:
            fdt (FDT): flattened device tree
            verbose (int,optional): verbosity level, default 0.

        Returns:
            List of nodes. Each containg the [node number, name, phandle, depth]

        """
        node_list = []
        node = 0
        depth = 0
        while depth >= 0:
            node_list.append([node, fdt.get_name(node), Lopper.node_getphandle(fdt,node), depth])
            node, depth = fdt.next_node(node, depth, (libfdt.BADOFFSET,))

        return node_list

    @staticmethod
    def dtb_dts_export( dtb, outfilename="", verbose=0 ):
        """writes a dtb to a file or to stdout as a dts

        Args:
           dtb: a compiled device tree
           outfilename (string): the output filename (stdout is used if empty)
           verbose (int,optional): extra debug info. default 0.

        Returns:
           The return value of executing dtc to dump the dtb to dts
        """
        dtcargs = (os.environ.get('LOPPER_DTC') or shutil.which("dtc")).split()
        dtcargs += (os.environ.get("LOPPER_DTC_FLAGS") or "").split()
        dtcargs += (os.environ.get("LOPPER_DTC_BFLAGS") or "").split()
        if outfilename:
            dtcargs += ["-o", "{0}".format(outfilename)]
        dtcargs += ["-I", "dtb", "-O", "dts", dtb]

        if verbose:
            print( "[INFO]: dumping dtb: %s" % dtcargs )

        result = subprocess.run(dtcargs, check = False, stderr=subprocess.PIPE )
        if result.returncode is not 0:
            print( "[ERROR]: unable to export a dts" )
            print( "\n%s" % textwrap.indent(result.stderr.decode(), '         ') )

        return result

    @staticmethod
    def dt_to_fdt( dtb, rmode='rb' ):
        """takes a dtb and returns a flattened device tree object

        Args:
           dtb: a compiled device tree
           rmode (string,optional): the read mode of the file, see libfdt for possible values
                                    default is 'rb'

        Returns:
           A flattended device tree object (as defined by libfdt)
        """
        fdt = libfdt.Fdt(open(dtb, mode=rmode).read())
        return fdt

    @staticmethod
    def node_getphandle( fdt, node_number ):
        """utility command to get a phandle (as a number) from a node

        Args:
           fdt (FDT): flattened device tree
           node_number (int): node number in the fdt

        Returns:
           int: the phandle of the node number, if successful, -1 if not
        """
        prop = fdt.get_phandle( node_number )
        return prop

    @staticmethod
    def property_get( fdt, node_number, prop_name, ftype=LopperFmt.SIMPLE, encode=LopperFmt.DEC ):
        """utility command to get a property (as a string) from a node

        A more robust way to get the value of a property in a node, when
        you aren't sure of the format of that property. This routine takes
        hints when getting the property in the form of a "format type" and
        an encoding.

        The format and encoding options are in the following enum type:

           class LopperFmt(Enum):
              SIMPLE = 1 (format)
              COMPOUND = 2 (format)
              HEX = 3 (encoding)
              DEC = 4 (encoding)
              STRING = 5 (encoding)
              MULTI_STRING = 5 (encoding)

        Args:
           fdt (FDT): flattened device tree
           node_number (int): node number in the fdt
           property (string): property name whose value to get
           ftype (LopperFmt,optional): format of the property. Default SIMPLE.
           encode (LopperFmt,optional); encoding of the property. Default DEC

        Returns:
           string: if format is SIMPLE: string value of the property, or "" if not found
           list: if format is COMPOUND: list of property values as strings, [] if not found
        """
        try:
            prop = fdt.getprop( node_number, prop_name )
            val = Lopper.property_value_decode( prop, 0, ftype, encode )
        except Exception as e:
            val = ""

        return val

    @staticmethod
    def property_set( fdt, node_number, prop_name, prop_val, ftype=LopperFmt.SIMPLE, verbose=False ):
        """utility command to set a property in a node

        A more robust way to set the value of a property in a node, This routine
        takes hints when getting the property in the form of a "format type"

        The format options are in the following enum type:

           class LopperFmt(Enum):
              SIMPLE = 1 (format)
              COMPOUND = 2 (format)

        Based on the format hint, and the passed value, the property is encoded
        into a byte array and stored into the flattened device tree node.

        Args:
           fdt_dst (FDT): flattened device tree
           node_number (int): node number in the fdt
           prop_name (string): property name whose value to set
           ftype (LopperFmt,optional): format of the property. Default SIMPLE.

        Returns:
           Nothing

        """

        # if it's a list, we dig in a bit to see if it is a single item list.
        # if so, we grab the value so it can be propery encoded. We also have
        # a special case if the '' string is the only element .. we explicity
        # set the empty list, so it will encode properly.
        if type(prop_val) == list:
            if len(prop_val) == 1 and prop_val[0] != '':
                prop_val = prop_val[0]
            elif len(prop_val) == 1 and prop_val[0] == '':
                pass

        try:
            prop_val_converted = int(prop_val,0)
            # if it works, that's our new prop_val. This covers the case where
            # a string is passed in, but it is really just a single number.
            # note: we may need to consult "ftype" in the future so the caller
            # can override this automatical conversion
            prop_val = prop_val_converted
        except:
            # do nothing. let propval go through as whatever it was
            pass

        # we have to re-encode based on the type of what we just decoded.
        if type(prop_val) == int:
            # this seems to break some operations, but a variant may be required
            # to prevent overflow situations
            # if sys.getsizeof(prop_val) >= 32:
            for _ in range(MAX_RETRIES):
                try:
                    if sys.getsizeof(prop_val) > 32:
                        fdt.setprop_u64( node_number, prop_name, prop_val )
                    else:
                        fdt.setprop_u32( node_number, prop_name, prop_val )

                    break
                except Exception as e:
                    fdt.resize( fdt.totalsize() + 1024 )
                    continue
                else:
                    break
            else:
                # it wasn't set all all, we could thrown an error
                pass
        elif type(prop_val) == str:
            for _ in range(MAX_RETRIES):
                try:
                    fdt.setprop_str( node_number, prop_name, prop_val )
                    break
                except Exception as e:
                    if verbose:
                        print( "[WARNING]: property set exception: %s" % e)
                    fdt.resize( fdt.totalsize() + 1024 )
                    continue
                else:
                    break
            else:
                # we totally failed!
                pass

        elif type(prop_val) == list:
            # list is a compound value, or an empty one!
            if len(prop_val) >= 0:
                try:
                    bval = Lopper.encode_byte_array_from_strings(prop_val)
                except:
                    bval = Lopper.encode_byte_array(prop_val)

                for _ in range(MAX_RETRIES):
                    try:
                        fdt.setprop( node_number, prop_name, bval)
                    except Exception as e:
                        fdt.resize( fdt.totalsize() + 1024 )
                        continue
                    else:
                        break
                else:
                    # fail!
                    pass
        else:
            print( "[WARNING]; unknown type was used: %s" % type(prop_val) )

    @staticmethod
    def property_remove( fdt, node_name, prop_name, verbose=0 ):
        """removes a property from a fdt

        Removes a property (if it exists) from a node (and optionally its children).

        Args:
            fdt (FDT): flattened device tree to modify
            node_name (int or string): the node number or name to process
            prop_name (string): name of property to remove

        Returns:
            Boolean: True if the property was deleted, False if it wasn't

        """

        node = Lopper.node_find( fdt, node_name )
        if node == -1:
            node, nodes = Lopper.node_find_by_name( fdt, node_name )

        if node == -1:
            return False

        prop_list = []
        poffset = fdt.first_property_offset(node, QUIET_NOTFOUND)
        while poffset > 0:
            # if we delete the only property of a node, all calls to the FDT
            # will throw an except. So if we get an exception, we set our poffset
            # to zero to escape the loop.
            try:
                prop = fdt.get_property_by_offset(poffset)
            except:
                poffset = 0
                continue

            prop_list.append(prop.name)
            poffset = fdt.next_property_offset(poffset, QUIET_NOTFOUND)

        if prop_name in prop_list:
            # node is an integer offset, prop_name is a string
            if verbose:
                print( "[INFO]: removing property %s from %s" % (prop_name, fdt.get_name(node)) )

            fdt.delprop(node, prop_name)
        else:
            return False

        return True


    @staticmethod
    def dt_preprocess( dts_file, includes, outdir="./", verbose=0 ):
        """Compile a dts file to a dtb

        This routine takes a dts input file, include search path and then
        uses standard tools (cpp, etc) to expand references.

        Environment variables can be used tweak the execution of the various
        tools and stages:

           LOPPER_CPP: set if a different cpp than the standard one should
                       be used, or if cpp is not on the path
           LOPPER_PPFLAGS: flags to be used when calling cpp

        Args:
           dts_file (string): path to the dts file to be preprocessed
           includes (list): list of include directories (translated into -i <foo>
                            for cpp calls)
           outdir (string): directory to place all output and temporary files
           verbose (bool,optional): verbosity level

        Returns:
           string: Name of the preprocessed dts

        """
        # TODO: might need to make 'dts_file' absolute for the cpp call below
        dts_filename = os.path.basename( dts_file )
        dts_filename_noext = os.path.splitext(dts_filename)[0]

        #
        # step 1: preprocess the file with CPP (if available)
        #

        # NOTE: we are putting the .pp file into the same directory as the
        #       system device tree. Without doing this, dtc cannot resolve
        #       labels from include files, and will throw an error. If we get
        #       into a mode where the system device tree's directory is not
        #       writeable, then we'll have to either copy everything or look
        #       into why dtc can't handle the split directories and include
        #       files.

        # if outdir is left as the default (current dir), then we can respect
        # the dts directory. Otherwise, we need to follow where outdir has been
        # pointed. This may trigger the issue mentioned in the prvious comment,
        # but we'll cross that bridge when we get to it
        dts_dirname = outdir
        if outdir == "./":
            dts_file_dir = os.path.dirname( dts_file )
            if dts_file_dir:
                dts_dirname = dts_file_dir
        preprocessed_name = "{0}/{1}.pp".format(dts_dirname,dts_filename)

        includes += dts_dirname
        includes += " "
        includes += os.getcwd()

        ppargs = (os.environ.get('LOPPER_CPP') or shutil.which("cpp")).split()
        # Note: might drop the -I include later
        ppargs += "-nostdinc -I include -undef -x assembler-with-cpp ".split()
        ppargs += (os.environ.get('LOPPER_PPFLAGS') or "").split()
        for i in includes.split():
            ppargs.append("-I{0}".format(i))
        ppargs += ["-o", preprocessed_name, dts_file]
        if verbose:
            print( "[INFO]: preprocessing dts_file: %s" % ppargs )

        result = subprocess.run( ppargs, check = True )
        if result.returncode is not 0:
            print( "[ERROR]: unable to preprocess dts file: %s" % ppargs )
            print( "\n%s" % textwrap.indent(result.stderr.decode(), '         ') )
            sys.exit(result.returncode)

        return preprocessed_name


    @staticmethod
    def dt_compile( dts_file, i_files, includes, force_overwrite=False, outdir="./", save_temps=False, verbose=0 ):
        """Compile a dts file to a dtb

        This routine takes a dts input file, other dts include files,
        include search path and then uses standard tools (cpp, dtc, etc).

        Environment variables can be used tweak the execution of the various
        tools and stages:

           LOPPER_CPP: set if a different cpp than the standard one should
                       be used, or if cpp is not on the path
           LOPPER_PPFLAGS: flags to be used when calling cpp
           LOPPER_DTC: set if a non standard dtc should be used, or if dtc
                       is not on the path
           LOPPER_DTC_FLAGS: flags to use when calling dtc
           LOPPER_DTC_OFLAGS: extra dtc flags if an overlay is being compiled
           LOPPER_DTC_BFLAGS: extra dtc args/flags

        Args:
           dts_file (string): path to the dts file to be compiled
           i_files (list): files to be included
           includes (list): list of include directories (translated into -i <foo>
                            for dtc calls)
           force_overwrite (bool,optional): should files be overwritten.
                                            Default is False
           save_temps (bool, optional): should temporary files be saved on failure
           verbose (bool,optional): verbosity level

        Returns:
           string: Name of the compiled dtb

        """
        output_dtb = ""

        # Note: i_files is not currently used. They are typically concatenated
        #       before calling this routine due to pecularities in include
        #       processing
        # TODO: might need to make 'dts_file' absolute for the cpp call below
        dts_filename = os.path.basename( dts_file )
        dts_filename_noext = os.path.splitext(dts_filename)[0]

        #
        # step 1: preprocess the file with CPP (if available)
        #

        # NOTE: we are putting the .pp file into the same directory as the
        #       system device tree. Without doing this, dtc cannot resolve
        #       labels from include files, and will throw an error. If we get
        #       into a mode where the system device tree's directory is not
        #       writeable, then we'll have to either copy everything or look
        #       into why dtc can't handle the split directories and include
        #       files.
        preprocessed_name = Lopper.dt_preprocess( dts_file, includes, outdir, verbose )

        # step 2: compile the dtb
        #         dtc -O dtb -o test_tree1.dtb test_tree1.dts
        isoverlay = False
        output_dtb = "{0}.{1}".format(dts_filename, "dtbo" if isoverlay else "dtb")

        # make sure the dtb is not on disk, since it won't be overwritten by
        # default.
        if os.path.exists( output_dtb ):
            if not force_overwrite:
                print( "[ERROR]: output dtb (%s) exists and -f was not passed" % output_dtb )
                sys.exit(1)
            os.remove( output_dtb )

        dtcargs = (os.environ.get('LOPPER_DTC') or shutil.which("dtc")).split()
        dtcargs += (os.environ.get( 'LOPPER_DTC_FLAGS') or "").split()
        if isoverlay:
            dtcargs += (os.environ.get("LOPPER_DTC_OFLAGS") or "").split()
        else:
            dtcargs += (os.environ.get("LOPPER_DTC_BFLAGS") or "").split()
        for i in includes.split():
            dtcargs += ["-i", i]

        dtcargs += ["-o", "{0}/{1}".format(outdir,output_dtb)]
        dtcargs += ["-I", "dts", "-O", "dtb", preprocessed_name ]
        if verbose:
            print( "[INFO]: compiling dtb: %s" % dtcargs )

        result = subprocess.run(dtcargs, check = False, stderr=subprocess.PIPE )
        if result is not 0:
            # force the dtb, we need to do processing
            dtcargs += [ "-f" ]
            if verbose:
                print( "[INFO]: forcing dtb generation: %s" % dtcargs )

            result = subprocess.run(dtcargs, check = False, stderr=subprocess.PIPE )
            if result.returncode is not 0:
                print( "[ERROR]: unable to (force) compile %s" % dtcargs )
                print( "\n%s" % textwrap.indent(result.stderr.decode(), '         ') )
                sys.exit(1)

        # cleanup: remove the .pp file
        if not save_temps:
            os.remove( preprocessed_name )

        # if we got here, and for some reason the output_dtb does not exist, we should
        # zero the name and return "" instead.
        output_file = Path(outdir + "/" + output_dtb)
        try:
            output_file_path = output_file.resolve()
        except FileNotFoundError:
            output_dtb = ""

        return str(output_file)

    @staticmethod
    def input_file_type(infile):
        """utility to return the "type" of a file, aka the extension

        Args:
           infile (string): path of the file

        Returns:
           string: the extension of the file

        """
        return PurePath(infile).suffix

    @staticmethod
    def encode_byte_array( values ):
        """utility to encode a list of values into a bytearray

        Args:
           values (list): integer (numeric) values to encode

        Returns:
           byte array: the encoded byte array

        """
        barray = b''
        for i in values:
            barray = barray + i.to_bytes(4,byteorder='big')
        return barray

    @staticmethod
    def encode_byte_array_from_strings( values ):
        """utility to encode a list of strings into a bytearray

        Args:
           values (list): string values to encode

        Returns:
           byte array: the encoded byte array

        """
        barray = b''
        if len(values) > 1:
            for i in values:
                barray = barray + i.encode() + b'\x00'
        else:
            barray = barray + values[0].encode()

        return barray

    @staticmethod
    def string_test( prop, allow_multiline = True ):
        """ Check if a property (byte array) is a string

        Args:
           prop: (libfdt property)

        Returns:
           boolean: True if the property looks like a string
        """
        if not len( prop ):
            return False

        if prop[-1] != 0:
            return False

        byte = 0
        while byte < len( prop ):
            bytei = byte
            while byte < len( prop ) and \
                  prop[byte] != 0 and \
                  prop[byte] in printable.encode() and \
                  prop[byte] not in (ord('\r'), ord('\n')):

                byte += 1

            if prop[byte] in (ord('\r'), ord('\n')):
                if allow_multiline:
                    byte += 1
                    continue

            # if we broke walking through the positions, and
            # we aren't on a null (multiple strings) or are
            # where we started, then this isn't a string.
            if prop[byte] != 0 or byte == bytei:
                if byte + 3 < len(prop):
                    if prop[byte:byte+3] == b'\xe2\x80\x9c' or prop[byte:byte+3] == b'\xe2\x80\x9d':
                        #print( "jumping ahead, looks like an escaped quote" )
                        byte += 3
                        continue

                return False

            byte += 1

        return True


    @staticmethod
    def property_type_guess( prop ):
        """utility routine to guess the type of a property

        Often the type of a property is not know, in particular if there isn't
        access to markers via libfdt.

        This routine looks at the data of a libFDT property and returns the best
        guess for the type. The logic behind the guesses is documented in the code
        itself

        Args:
           prop (libfdt property): the property to process

        Returns:
           LopperFmt description of the property. Default is UINT8 (binary)
                       LopperFmt.STRING: string
                       LopperFmt.UINT32 1: uint32
                       LopperFmt.UINT64 2: uint64
                       LopperFmt.UINT8 3: uint8 (binary)
                       LopperFmt.EMPTY 4: empty (just a name)
        """
        type_guess = LopperFmt.UINT8

        if len(prop) == 0:
            return LopperFmt.EMPTY

        first_byte = prop[0]
        last_byte = prop[-1]

        # byte array encoded strings, start with a non '\x00' byte (i.e. a character), so
        # we test on that for a hint. If it is not \x00, then we try it as a string.
        # Note: we may also test on the last byte for a string terminator.
        if first_byte != 0 and len(prop) > 1:
            if last_byte == 0:
                type_guess = LopperFmt.STRING
                try:
                    val = prop[:-1].decode('utf-8').split('\x00')
                    # and a 2nd opinion
                    if not Lopper.string_test( prop ):
                        # change our mind
                        type_guess = LopperFmt.UINT8

                except Exception as e:
                    # it didn't decode, fall back to numbers ..
                    type_guess = LopperFmt.UINT8
            else:
                type_guess = LopperFmt.UINT8

        if type_guess == LopperFmt.UINT8:
            num_bits = len(prop)
            num_divisible = num_bits % 4
            if num_divisible != 0:
                # If it isn't a string and isn't divisible by a uint32 size, then it
                # is binary formatted data. So we return uint8
                type_guess = LopperFmt.UINT8
            else:
                # we can't easily guess the difference between a uint64 and uint32
                # until we get access to the marker data. So we default to the smaller
                # sized number. We could possibly
                type_guess = LopperFmt.UINT32

        return type_guess

    @staticmethod
    def property_value_decode( prop, poffset, ftype=LopperFmt.SIMPLE, encode=LopperFmt.UNKNOWN, verbose=0 ):
        """Decodes a property

        Decode a property into a common data type (string, integer, list of
        strings, etc).

        This is a robust wrapper around the decode facilities provided via
        libfdt. This routine tries multiple encode formats and uses
        heuristics to determine the best format for the decoded property.

        The format type (ftype) and encod arguments can be used to help
        decode properly when the type of a property is known.

        The format and encoding options are in the following enum type:

           class LopperFmt(Enum):
              SIMPLE = 1 (format)
              COMPOUND = 2 (format)
              HEX = 3 (encoding)
              DEC = 4 (encoding)
              STRING = 5 (encoding)
              MULTI_STRING = 5 (encoding)

        Args:
           prop (libfdt property): property to decode
           poffset (int): offset of the property in the node (unused)
           ftype (LopperFmt,optional): format hint for the property. default is SIMPLE
           encode (LopperFmt,optional): encoding hint. default is DEC
           verbose (int,optional): verbosity level, default is 0

        Returns:
           (string): if SIMPLE. The property as a string
           (list): if COMPOUND. The property as a list of strings / values

        """
        if verbose > 3:
            print( "[DBG+]: '%s' decode start: %s %s" % (prop.name,prop,ftype))

        # Note: these could also be nested.
        # Note: this is temporary since the decoding
        #       is sometimes wrong. We need to look at libfdt and see how they are
        #       stored so they can be unpacked better.
        if ftype == LopperFmt.SIMPLE:
            encode_calculated = Lopper.property_type_guess( prop )

            val = ""
            if repr(encode_calculated) == repr(LopperFmt.STRING) or \
               repr(encode_calculated) == repr(LopperFmt.EMPTY ):
                if not val:
                    try:
                        val = prop.as_str()
                        decode_msg = "(string): {0}".format(val)
                    except:
                        pass

                if not val:
                    try:
                        # this is getting us some false positives on multi-string. Need
                        # a better test
                        val = prop[:-1].decode('utf-8').split('\x00')
                        #val = ""
                        decode_msg = "(multi-string): {0}".format(val)
                    except:
                        pass
            else:
                val = ""
                decode_msg = ""
                try:
                    val = prop.as_uint32()
                    decode_msg = "(uint32): {0}".format(val)
                except:
                    pass
                if not val and val != 0:
                    try:
                        val = prop.as_uint64()
                        decode_msg = "(uint64): {0}".format(val)
                    except:
                        pass

            if not val and val != 0:
                decode_msg = "** unable to decode value **"
        else:
            # compound format
            decode_msg = ""
            val = ['']
            encode_calculated = Lopper.property_type_guess( prop )

            if repr(encode_calculated) == repr(LopperFmt.EMPTY):
                return val

            first_byte = prop[0]
            last_byte = prop[-1]

            # TODO: we shouldn't need these repr() wrappers around the enums, but yet
            #       it doesn't seem to work on the calculated variable without them
            if repr(encode_calculated) == repr(LopperFmt.STRING):
                try:
                    val = prop[:-1].decode('utf-8').split('\x00')
                    decode_msg = "(multi-string): {0}".format(val)
                except:
                    encode_calculated = encode

            if repr(encode_calculated) == repr(LopperFmt.UINT32) or \
               repr(encode_calculated) == repr(LopperFmt.UINT64) or \
               repr(encode_calculated) == repr(LopperFmt.UINT8) :
                try:
                    decode_msg = "(multi number)"
                    num_bits = len(prop)
                    if encode_calculated == LopperFmt.UINT8:
                        binary_data = True
                        start_index = 0
                        end_index = 1
                        short_int_size = 1
                        num_nums = num_bits
                    else:
                        binary_data = False
                        num_nums = num_bits // 4
                        start_index = 0
                        end_index = 4
                        short_int_size = 4

                    val = []
                    while end_index <= (num_nums * short_int_size):
                        short_int = prop[start_index:end_index]
                        if repr(encode) == repr(LopperFmt.HEX):
                            converted_int = hex(int.from_bytes(short_int,'big',signed=False))
                        else:
                            converted_int = int.from_bytes(short_int,'big',signed=False)

                        start_index = start_index + short_int_size
                        end_index = end_index + short_int_size
                        val.append(converted_int)

                except Exception as e:
                    decode_msg = "** unable to decode value **"


        if verbose > 3:
            print( "[DBG+]: decoding prop: \"%s\" (%s) [%s] --> %s" % (prop, poffset, prop, decode_msg ) )

        return val

from lopper_tree import *
