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
# from enum import Enum
import atexit
import textwrap
from collections import UserDict
from collections import OrderedDict

from lopper.fmt import LopperFmt
import lopper.base
from lopper.tree import LopperTreePrinter

from string import printable

try:
    import libfdt
    from libfdt import Fdt, FdtException, QUIET_NOTFOUND, QUIET_ALL
except:
    import site
    python_version_dir = "python{}.{}".format( sys.version_info[0], sys.version_info[1] )
    site.addsitedir((Path(__file__).parent / 'vendor/lib/{}/site-packages'.format( python_version_dir )).resolve())

    import libfdt
    from libfdt import Fdt, FdtException, QUIET_NOTFOUND, QUIET_ALL

# general retry count
MAX_RETRIES = 10

class LopperFDT(lopper.base.lopper_base):
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
            node_path = LopperFDT.node_abspath( fdt, node_number_or_path )
        except ValueError:
            node_number = LopperFDT.node_find( fdt, node_number_or_path )
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
            node_path = LopperFDT.node_abspath( fdt, node_number_or_path )
        except ValueError:
            node_number = LopperFDT.node_find( fdt, node_number_or_path )
            node_path = node_number_or_path

        retval = False
        if node_number == -1:
            return retval

        for _ in range(MAX_RETRIES):
            try:
                fdt.set_name( node_number, newname )
                retval = True
            except Exception as e:
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
        rt = LopperFDT.property_get( fdt, node_offset, "compatible" )

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
                node_path = LopperFDT.node_abspath( fdt, nn )

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
            matching_node = LopperFDT.node_abspath( fdt, matching_node )
            matching_node_list = matching_nodes
            matching_nodes = []
            for m in matching_node_list:
                matching_nodes.append( LopperFDT.node_abspath( fdt, m ) )

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

        node = LopperFDT.node_find( fdt, node_name )
        if node == -1:
            node, nodes = LopperFDT.node_find_by_name( fdt, node_name )

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
        paths_to_check = [ node_full_path ]
        n_path = node_full_path

        # create an ascending list of parent path components to check for existence
        while n_path != "/":
            n_path = os.path.dirname(n_path)
            paths_to_check.insert( 0, n_path )

        # walk that list, create what is missing and use the node numbers as parent offsets
        node_parent = 0
        for p in paths_to_check:
            node_number = LopperFDT.node_find( fdt_dest, p )
            node_name = os.path.basename( p )
            if node_number == -1:
                # were we the last item in the paths to check ? if not, we have
                # to check to see if the parent create flag was set .. if not, return
                # -1 and exit. Otherwise, create all missing components
                if p != paths_to_check[-1]:
                    if not create_parents:
                        if verbose:
                            print( "[DBG]: LopperFDT: parent node %s doesn't exist, but create parents is not set, returning -1" % p )
                        return -1

                # add it
                for _ in range(MAX_RETRIES):
                    try:
                        node_parent = fdt_dest.add_subnode( node_parent, node_name )
                    except Exception as e:
                        fdt_dest.resize( fdt_dest.totalsize() + 1024 )
                        continue
                    else:
                        break
            else:
                # it exists
                node_parent = node_number

        return node_parent

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
        node = LopperFDT.node_number( fdt, node_number_or_path )
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

        node = LopperFDT.node_number( fdt, node_number_or_path )
        if node == -1:
            return node_list

        depth = 0
        while depth >= 0:
            if abs_paths:
                node_list.append( LopperFDT.node_abspath( fdt, node ) )
            else:
                node_list.append( LopperFDT.node_getname( fdt, node ) )

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
        node = LopperFDT.node_number( fdt, node_number_or_path )
        if node == -1:
            return node_list

        offset = fdt.first_subnode(node, QUIET_NOTFOUND)
        while offset > 0:
            if abs_paths:
                node_list.append( LopperFDT.node_abspath( fdt, offset ) )
            else:
                node_list.append( LopperFDT.node_getname( fdt, offset ) )

            offset = fdt.next_subnode( offset, QUIET_NOTFOUND )

        return node_list

    @staticmethod
    def node_parent( fdt, node_number_or_path ):
        """Get the parent offset / number of a node

        Args:
            fdt (fdt): flattened device tree object
            node_number_or_path: (string or int): node number or full path to
                                 the target node.

        Returns:
            int: the node number of the parent
        """
        parent = -1
        node_number = LopperFDT.node_number( fdt, node_number_or_path )
        if node_number > 0:
            parent = fdt.parent_offset( node_number, QUIET_NOTFOUND )

        return parent

    @staticmethod
    def node_sync( fdt, node_in, parent = None, verbose = False ):
        """Write a node description to a FDT

        This routine takes an input dictionary, and writes the details to
        the passed fdt.

        The dictionary contains a set of internal properties, as well as
        a list of standand properties to the node. Internal properties have
        a __ suffix and __ prefix.

        In particular:
            - __path__ : is the absolute path fo the node, and is used to lookup
                         the target node
            - __fdt_name__ : is the name of the node and will be written to the
                             fdt name property
            - __fdt_phandle__ : is the phandle for the node

        All other '/' leading, or '__' leading properties will be written to
        the FDT as node properties.

        If the node doesn't exist, it will be created. If the node exists, then
        the existing properties are read, and any that are no present in the
        passed dictionary are deleted.

        Args:
            fdt (fdt): flattened device tree object
            node_in: (dictionary): Node description dictionary
            parent (string,optional): path to the parent node
            verbose (bool,optional): verbosity level

        Returns:
            Nothing
        """
        if verbose:
            print( "[DBG]: lopper.fdt: node_sync: start: %s (%s)" % (node_in['__fdt_name__'],node_in['__path__'] ))

        nn = LopperFDT.node_find( fdt, node_in['__path__'] )
        if nn == -1:
            # -1 means the node wasn't found
            if verbose:
                print( "[DBG]:    lopper.fdt: adding node: %s" % node_in['__path__'] )

            nn = LopperFDT.node_add( fdt, node_in['__path__'], True )
            if nn == -1:
                print( "[ERROR]:    lopper.fdt: node could not be added, exiting" )
                sys.exit(1)

        nname = node_in['__fdt_name__']
        nflag = LopperFDT.node_setname( fdt, nn, nname )
        if not nflag:
            print( "[ERROR]: unable to set node %s name to: %s" % (nn,nname) )
            sys.exit(1)

        try:
            ph = node_in['__fdt_phandle__']
            if ph:
                LopperFDT.property_set( fdt, nn, "phandle", ph )
        except:
            pass

        props = LopperFDT.node_properties( fdt, nn )
        props_to_delete = []
        for p in props:
            if node_in['__fdt_phandle__'] and p.name == "phandle":
                # we just added this, it won't be in the node_in items under
                # the name name
                pass
            else:
                props_to_delete.append( p.name )

        for prop, prop_val in reversed(node_in.items()):
            if re.search( "^__", prop ) or prop.startswith( '/' ):
                if verbose:
                    print( "          lopper.fdt: node sync: skipping internal property: %s" % prop)
                continue
            else:
                if verbose:
                    print( "          lopper.fdt: node sync: prop: %s val: %s" % (prop,prop_val) )

                # We could supply a type hint via the __{}_type__ attribute
                LopperFDT.property_set( fdt, nn, prop, prop_val, LopperFmt.COMPOUND )
                if props_to_delete:
                    try:
                        props_to_delete.remove( prop )
                    except:
                        # if a node was added at the top of this routine, it
                        # won't have anything in the props_to_delete, and this
                        # would throw an exception. Since that's ok, we just
                        # catch it and move on.
                        pass

        for p in props_to_delete:
            if verbose:
                print( "[DBG]:    lopper.fdt: node sync, deleting property: %s" % p )
            LopperFDT.property_remove( fdt, nname, p )

    @staticmethod
    def sync( fdt, dct, verbose = False ):
        """sync (write) a tree dictionary to a fdt

        This routine takes an input dictionary, and writes the details to
        the passed fdt.

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

        All other non  '/' leading, or '__' leading properties will be written to
        the FDT as node properties.

        Passed nodes will be synced via the node_sync() function, and will
        be created if they don't exist. Existing nodes will have their properties
        deleted if they are not in the corresponding dictionary.

        All of the existing nodes in the FDT are read, if they aren not found
        in the passed dictionary, they will be deleted.

        Args:
            fdt (fdt): flattened device tree object
            node_in: (dictionary): Node description dictionary
            parent (dictionary,optional): parent node description dictionary
            verbose (bool,optional): verbosity level

        Returns:
            Nothing
        """
        # import a dictionary to a FDT

        if not fdt:
            return

        if verbose:
            print( "[DBG]: lopper.fdt sync: start" )

        # we have a list of: containing dict, value, parent
        dwalk = [ [dct,dct,None]  ]
        node_ordered_list = []
        while dwalk:
            firstitem = dwalk.pop()
            if type(firstitem[1]) is OrderedDict:
                node_ordered_list.append( [firstitem[1], firstitem[0]] )
                for item,value in reversed(firstitem[1].items()):
                    dwalk.append([firstitem[1],value,firstitem[0]])
            else:
                pass

        # this gets us a list of absolute paths. If we walk through the
        # dictionary passed in, and delete them from the list, we have the list
        # of nodes to delete with whatever is left over, and the nodes to add if
        # they aren't in the list.
        nodes_to_remove = LopperFDT.nodes( fdt, "/" )
        nodes_to_add = []
        for n_item in node_ordered_list:
            try:
                nodes_to_remove.remove( n_item[0]['__path__'] )
            except:
                nodes_to_add.append( n_item )

        for node in nodes_to_remove:
            nn = LopperFDT.node_find( fdt, node )
            if nn != -1:
                if verbose:
                    print( "[DBG]:    lopper.fdt: sync: removing: node %s" % node )
                LopperFDT.node_remove( fdt, nn )
            else:
                if verbose:
                    print( "[DBG]:    lopper.fdt: sync: node %s was not found, and could not be remove" % node )
                # child nodes are removed with their parent, and follow in the
                # list, so this isn't an error.
                pass

        # add the nodes
        for n in reversed(node_ordered_list):
            nn = LopperFDT.node_find( fdt, n[0]['__path__'] )
            if nn == -1:
                new_number = LopperFDT.node_add( fdt, n[0]['__path__'], True, verbose )
                if new_number == -1:
                    print( "[ERROR]:    lopper_fdt: node %s could not be added, exiting" % n[0]['__path__'] )
                    sys.exit(1)


        # sync the properties
        for n_item in reversed(node_ordered_list):
            node_in = n_item[0]
            node_in_parent = n_item[1]
            node_path = node_in['__path__']
            abs_path = node_path
            nn =  node_in['__fdt_number__']

            LopperFDT.node_sync( fdt, node_in, node_in_parent )

    @staticmethod
    def export( fdt, start_node = "/", verbose = False, strict = False ):
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
        # export a FDT as a dictionary
        dct = OrderedDict()

        nodes = LopperFDT.node_subnodes( fdt, start_node )

        if strict:
            if len(nodes) != len(set(nodes)):
                raise Exception( "lopper.fdt: duplicate node detected (%s)" % nodes )

        dct["__path__"] = start_node

        np = LopperFDT.node_properties_as_dict( fdt, start_node )
        if np:
            dct.update(np)

        nn = LopperFDT.node_number( fdt, start_node )
        dct["__fdt_number__"] = nn
        dct["__fdt_name__"] = LopperFDT.node_getname( fdt, start_node )
        dct["__fdt_phandle__"] = LopperFDT.node_getphandle( fdt, nn )

        if verbose:
            print( "[DBG]: lopper.fdt export: " )
            print( "[DBG]:     [startnode: %s]: subnodes: %s" % (start_node,nodes ))
            print( "[DBG]:          props: %s" % np )

        for i,n in enumerate(nodes):
            # Children are indexed by their path (/foo/bar), since properties
            # cannot start with '/'
            dct[n] = LopperFDT.export( fdt, n, verbose, strict )

        return dct

    @staticmethod
    def node_properties_as_dict( fdt, node, type_hints=True, verbose=0 ):
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

        # is the node a number ? or do we need to look it up ?
        node_number = -1
        node_path = ""
        try:
            node_number = int(node)
            node_path = LopperFDT.node_abspath( fdt, node )
        except ValueError:
            node_number = LopperFDT.node_find( fdt, node )
            node_path = node

        if node_number == -1:
            print( "[WARNING]: could not find node %s" % node_path )
            return prop_dict

        prop_list = LopperFDT.node_properties( fdt, node_path )
        for p in prop_list:
            # print( "                      export as dict: read: %s" % p.name )
            property_val = LopperFDT.property_get( fdt, node_number, p.name, LopperFmt.COMPOUND )
            prop_dict[p.name] = property_val
            if type_hints:
                prop_dict['__{}_type__'.format(p.name)] = LopperFDT.property_type_guess( p )

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

        node_to_copy = LopperFDT.node_find( fdt_source, node_source_path )
        node_dest_path = os.path.dirname( node_full_dest )
        node_dest_name = os.path.basename( node_full_dest )

        if node_dest_path == "/":
            node_dest_parent_offset = 0
        else:
            # non root dest
            node_dest_parent_offset = LopperFDT.node_find( fdt_dest, node_dest_path )
            if node_dest_parent_offset == -1:
                node_dest_parent_offset = LopperFDT.node_add( fdt_dest, node_dest_path )
                if node_dest_parent_offset <= 0:
                    print( "[ERROR]: could not create new node" )
                    sys.exit(1)

        if node_to_copy:
            return LopperFDT.node_copy( fdt_source, node_to_copy, fdt_dest, node_dest_parent_offset, verbose )

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
                    prop_val = LopperFDT.property_value_decode( prop, 0 )
                    if not prop_val:
                        prop_val = LopperFDT.property_value_decode( prop, 0, LopperFmt.COMPOUND )
                    print( "            prop decoded: %s" % prop_val )
                    print( "            prop type: %s" % type(prop_val))
                    print( "" )

                poffset = fdt_source.next_property_offset(poffset, QUIET_NOTFOUND)

            # loop through the gathered properties and copy them over. We are reversing
            # the order of the way we iterated them, due to the way that setprop inserts
            # at zero every time. If we don't flip the order the copied node will have
            # them in the opposite order!

            for poffset, prop in prop_list:
                prop_val = LopperFDT.property_get( fdt_source, nn, prop.name, LopperFmt.COMPOUND )
                LopperFDT.property_set( fdt_dest, prop_offset, prop.name, prop_val )


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
            node_number = LopperFDT.node_find( fdt, node )
            if node_number == -1:
                node_number, matching_nodes = LopperFDT.node_find_by_name( fdt, node )

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
            node, nodes = LopperFDT.node_find_by_name( fdt, start_path )
        else:
            node = 0

        if node < 0:
            print( "[WARNING]: could not find starting node: %s" % start_path )
            sys.exit(1)

        while depth >= 0:
            prop_val = LopperFDT.property_get( fdt, node, match_propname, LopperFmt.COMPOUND )
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
                printer = LopperTreePrinter( True, output_filename, verbose )
                printer.load( LopperFDT.export( fdt_to_write ) )
                printer.exec()
            else:
                # write the device tree to a temporary dtb
                fp = tempfile.NamedTemporaryFile()
                byte_array = fdt_to_write.as_bytearray()
                with open(fp.name, 'wb') as w:
                    w.write(byte_array)

                LopperFDT.dtb_dts_export( fp.name, output_filename )

                # close the temp file so it is removed
                fp.close()
        else:
            print( "[INFO]: unknown file type (%s) passed for writing, skipping" % output_filename )

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
        if result.returncode != 0:
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
            val = LopperFDT.property_value_decode( prop, 0, ftype, encode )
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

            if len(prop_val) > 1:
                val_to_sync = []
                iseq = iter(prop_val)
                first_type = type(next(iseq))
                # check for a mixed type, we get "false" if it is not all the same, or
                # the type otherwise
                the_same = first_type if all( (type(x) is first_type) for x in iseq ) else False
                if the_same == False:
                    # convert everything to strings
                    val_to_sync = []
                    for v in prop_val:
                        val_to_sync.append( str(v) )
                else:
                    val_to_sync = prop_val
            else:
                val_to_sync = prop_val

            prop_val = val_to_sync

            # list is a compound value, or an empty one!
            if len(prop_val) >= 0:
                try:
                    bval = LopperFDT.encode_byte_array_from_strings(prop_val)
                except:
                    bval = LopperFDT.encode_byte_array(prop_val)

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
                    print( "[WARNING]: lopper_fdt: unable to write property '%s' to fdt" % prop_name )
        else:
            print( "[WARNING]: %s: unknown type was used: %s" % (prop_name,type(prop_val)) )

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

        node = LopperFDT.node_find( fdt, node_name )
        if node == -1:
            node, nodes = LopperFDT.node_find_by_name( fdt, node_name )

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
    def dt_compile( dts_file, i_files, includes, force_overwrite=False, outdir="./",
                    save_temps=False, verbose=0, enhanced = True ):
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

        preprocessed_name = LopperFDT.dt_preprocess( dts_file, includes, outdir, verbose )

        if enhanced:
            fp = preprocessed_name

            # we need to ensure comments are maintained by converting them
            # into DTS attributes
            fp_enhanced = fp + ".enhanced"
            shutil.copyfile( fp, fp_enhanced )
            fp = fp_enhanced

            with open(fp_enhanced, 'r') as file:
                data = file.read()

            # drop /dts-v1/; from the file, we'll add it back at the top first. but
            # for now, we need it out of te way to look for any preamble to the main
            # device tree nodes

            dts_regex = re.compile( '\/dts-v1/;' )
            if re.search( dts_regex, data ):
                # delete the dts opening, since we are going to capture everything
                # from the start of the file, to the opening of the device tree
                # nodes.
                data = re.sub( dts_regex, '', data )

            # This captures everything at the start of the file (i.e. a comment block)
            # and puts it into a special pre-mble property in the root node. If we don't
            # do this, and let the comment substituion find it below, we have an invalid
            # device tree.
            #
            # When printing the tree layer, we'll pop it out and put it back as an
            # opening comment.
            #
            preamble_regex = re.compile( '(^.*?)(/ {)', re.MULTILINE | re.DOTALL )
            preamble = re.search( preamble_regex, data )
            if preamble:
                # is it a comment block ? if so, we want to mark it specially so
                # it can be put back at the header later.
                comment_regex = re.compile( '(/\*)(.*?)(\*/)', re.MULTILINE | re.DOTALL )
                comment = re.search( comment_regex, preamble.group(1) )
                if comment:
                    comment = comment.group(2)
                    if comment:
                        comment = re.sub( "^\n", '', comment )
                        comment = re.sub( "\n$", '', comment )
                        comment = "    lopper-preamble = \"{0}\";".format( comment )

                    data = re.sub( preamble_regex, '/ {' + '\n\n{0}'.format(comment), data, count = 1 )

            # put the dts start info back in
            data = re.sub( '^', '/dts-v1/;\n\n', data )

            # Comment and label substitution
            fp_comments_as_attributes = LopperFDT._comment_translate(data)
            fp_comments_and_labels_as_attributes = LopperFDT._label_translate(fp_comments_as_attributes)

            # now we need to potentially sort/drop some comments that are in bad places
            #   - comments must preceed nodes
            #   - comments cannot be in the middle of a sequence
            #
            # In the future we could always try and relocate them, but we've done our
            # best at this pont, so we'll just delete them

            lopper_comment_pattern = re.compile(r'lopper-comment-([0-9]+) = "(.*?)"', re.DOTALL | re.MULTILINE )
            lopper_comment_pattern_with_context = re.compile(r'{(.*?)lopper-comment-([0-9]+) = "(.*?)"', re.DOTALL | re.MULTILINE )
            lopper_comment_open_pattern = re.compile(r'lopper-comment-([0-9]+) = "(.*$)', re.DOTALL )

            comments_to_delete = []
            file_as_array = fp_comments_and_labels_as_attributes.splitlines()
            file_boundary_index = 0
            node_depth = 0
            subnode_at_depth = { 0: False }
            for i,f in enumerate(file_as_array):
                # take note when we pass an included file boundary, we'll use it to scan back
                # and look for structures.
                ml = re.search( "^\#line.*\"(.*?)\"", f )
                if ml:
                    if verbose > 2:
                        print( "[DBG++]: comment scan: include file boundary passed: %s" % ml.group(1) )
                    file_boundary_index = i
                    # clear the node tracking counts, we are into a new file
                    subnode_at_depth = { 0: False }

                mn = re.search( "^\s*(.*){", f )
                if mn:
                    node_depth += 1
                    if verbose > 2:
                        print( "[DBG++] comment scan: -> node depth inc: %s (%s)" % (node_depth,mn.group(1)) )
                    subnode_at_depth[node_depth-1] = True
                    subnode_at_depth[node_depth] = False

                mn = re.search( "^\s*};", f )
                if mn:
                    if verbose > 2:
                        print( "[DBG++] comment scan: -> node depth dec: %s" % node_depth )
                    node_depth -= 1

                m = re.search( lopper_comment_open_pattern, f )
                if m:
                    comment_number = m.group(1)
                    if verbose > 2:
                        print( "[DBG++]: comment scan: line %s has comment #%s [%s]" % (i,comment_number,m.group(2)) )

                    if subnode_at_depth[node_depth]:
                        if verbose > 1:
                            print( "[DBG+]: comment scan: comment found after first subnode, tagging to delete" )
                        comments_to_delete.append( comment_number )

                    if node_depth == 0:
                        if verbose > 1:
                            print( "[DBG+]: comment scan: comment before any nodes, tagging to delete" )
                        comments_to_delete.append( comment_number )

                    m2 = re.search( "^\s*lopper-comment", file_as_array[i] )
                    if not m2:
                        m3 = re.search( "[{;]\s*lopper-comment", file_as_array[i] )
                        if not m3:
                            if verbose > 1:
                                print( "[DBG+]: comment scan: comment embedded in property, tagging to delete" )
                            comments_to_delete.append( comment_number )

            # in case our comment fixups miss something, pull an env variable that is a list
            # of comment numbers to delete.
            extra_drops = os.environ.get('LOPPER_COMMENT_DROPLIST')
            if extra_drops:
                comments_to_delete.extend( extra_drops.split() )
                if verbose > 1:
                    print( "[DBG+]: comment scan: droplist: %s" % comments_to_delete )
            for cnum in comments_to_delete:
                lopper_comment_regex = re.compile( r'lopper-comment-{0} = ".*?";'.format(cnum), re.MULTILINE | re.DOTALL )
                fp_comments_and_labels_as_attributes = re.sub( lopper_comment_regex, "", fp_comments_and_labels_as_attributes )

            labeldict = {}
            lopper_label_pattern = re.compile(r'lopper-label-([0-9]+) = "(.*?)"')
            tree_block_pattern = re.compile( r'/ {' )

            file_as_array = fp_comments_and_labels_as_attributes.splitlines()
            for i,f in enumerate(file_as_array):
                m = re.search( tree_block_pattern, f )
                if m:
                    labeldict = {}

                labelnum = 0
                label = None
                m = re.search( lopper_label_pattern, f )
                if m:
                    labelnum = m.group(1)
                    label = m.group(2)

                if label:
                    try:
                        existing_label = labeldict[label]
                        print( "[ERROR]: duplicate label '%s' detected, processing cannot continue" % label )
                        if verbose:
                            print( "[DBG+]: Dumping label dictionary (as processed to error)" )
                            for l in labeldict:
                                print( "    %s" % l )

                            print( "\n[DBG+]: Offending label lines with context:" )
                            file_as_array = data.splitlines()
                            pattern = re.compile( r'^\s*?({})\s*?\:(.*?)$'.format(label), re.DOTALL | re.MULTILINE )
                            match_line = 0
                            for i,f in enumerate(file_as_array):
                                m = re.search( pattern, f )
                                if m:
                                    try:
                                        print( "    %s %s" % (i-2,file_as_array[i-2]) )
                                        print( "    %s %s" % (i-1,file_as_array[i-1]) )
                                        print( "    %s %s" % (i,file_as_array[i]) )
                                        print( "    %s %s" % (i+1,file_as_array[i+1]) )
                                        print( "    %s %s" % (i+2,file_as_array[i+2]) )
                                    except:
                                        print( "    %s %s" % (i,file_as_array[i]) )

                                    print( "\n" )
                        os._exit(1)

                    except:
                        labeldict[label] = label

            f = open( fp_enhanced, 'w' )
            f.write( fp_comments_and_labels_as_attributes )
            f.close()

            preprocessed_name = fp_enhanced

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
        if result.returncode != 0:
            # force the dtb, we need to do processing
            dtcargs += [ "-f" ]
            if verbose:
                print( "[INFO]: forcing dtb generation: %s" % dtcargs )

            result = subprocess.run(dtcargs, check = False, stderr=subprocess.PIPE )
            if result.returncode != 0:
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
