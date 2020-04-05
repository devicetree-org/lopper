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
import os
import re
import shutil
from pathlib import Path
from pathlib import PurePath
import tempfile
from enum import Enum
import textwrap
from collections import UserDict
from collections import OrderedDict
from collections import Counter
import copy

import libfdt
from libfdt import Fdt, FdtException, QUIET_NOTFOUND, QUIET_ALL

from lopper import *

# used in node_filter
class LopperAction(Enum):
    """Enum class to define the actions available in Lopper's node_filter function
    """
    DELETE = 1
    REPORT = 2
    WHITELIST = 3
    BLACKLIST = 4


class LopperProp():
    """Holds the state of a device tree property
    """
    def __init__(self, name, number, node, value = None, debug_lvl = 0 ):
        self.__modified__ = True
        self.__pstate__ = "init"
        self.__dbg__ = debug_lvl

        self.name = name
        self.node = node
        self.number = number
        if value == None:
            self.value = []
        else:
            # we want to avoid the overriden __setattr__ below
            self.__dict__["value"] = value

        self.string_val = "**unresolved**"
        self.type = ""

        self.abs_path = ""

    def __str__( self ):
        return self.string_val

    def int(self):
        ret_val = []
        for p in self.value:
            ret_val.append( p )

        return ret_val

    def hex(self):
        ret_val = []
        for p in self.value:
            ret_val.append( hex(p) )

        return ret_val

    # prop
    def __setattr__(self, name, value):
        # a little helper to make sure that we keep up our list-ness!
        if name == "value":

            try:
                old_value = self.__dict__[name]
            except:
                old_value = []

            if type(value) != list:
                self.__dict__[name] = [ value ]
            else:
                self.__dict__[name] = value

            if Counter(old_value) != Counter(self.__dict__[name]):
                # print( "property value changed!!!!!!!!!!!!!!!!!!!!!!!!!!!!" )
                self.__modified__ = True

            # NOTE: this will not update phandle references, you need to
            #       do a full resolve with a fdt for that.
            self.resolve( None )
        else:
            self.__dict__[name] = value


    def phandle_params( self ):
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
        if self.name in phandle_props.keys():
            property_description = phandle_props[self.name]
            property_fields = property_description[0].split()

            phandle_idx = 0
            phandle_field_count = 0
            for f in property_fields:
                if re.search( '#.*', f ):
                    try:
                        # Looking into the node, is the same as:
                        #      field_val = Lopper.prop_get( fdt, nodeoffset, f, LopperFmt.SIMPLE )
                        field_val = self.node.__props__[f].value[0]
                    except:
                        field_val = 0

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

    # property
    # return true if something has changed / been written, we'll have to loop and
    # check before we know that.
    def sync( self, fdt ):
        # we could do a read-and-set-if-different
        Lopper.prop_set( fdt, self.node.number, self.name, self.value, LopperFmt.COMPOUND )
        self.__modified__ = False
        self.__pstate__ = "syncd"

        return False

    # lopper prop
    def resolve_phandles( self, fdt ):
        """Resolve the targets of any phandles in a property

        Args:
            fdt (FDT): flattened device tree

        Returns:
            A list of all resolved phandle nodes, [] if no phandles are present
        """

        phandle_targets = []

        idx, pfields = self.phandle_params()
        if idx == 0:
            return phandle_targets

        # we need the values in hex. This could be a utility routine in the
        # future .. convert to hex.
        prop_val = []
        for f in self.value:
            prop_val.append( hex(f) )

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
                        # name isn't used, we could probably drop the call.
                        phandle_targets.append( tgn )
                    except:
                        pass

                element_count = element_count + 1
        else:
            return phandle_targets

        return phandle_targets

    # LopperProp
    def resolve( self, fdt ):
        outstring = "{0} = {1};".format( self.name, self.value )

        prop_val = self.value

        if self.__dbg__ > 1:
            print( "[DBG+]:         property resolve: %s" % self.name )

        self.abs_path = ""
        # we sometimes resolve against a zerod out fdt, i.e. when a
        # property was assigned.
        if fdt != None:
            if self.node.number != 0:
                self.abs_path = Lopper.node_abspath( fdt, self.node.number )

        self.abs_path = self.abs_path + "/" + self.name

        if re.search( "lopper-comment.*", self.name ):
            prop_type = "comment"
        elif re.search( "lopper-preamble", self.name ):
            prop_type = "preamble"
        else:
            prop_type = type(prop_val)

        self.type = prop_type

        phandle_idx, phandle_field_count = self.phandle_params()

        if prop_type == "comment":
            outstring = ""
            for s in prop_val:
                outstring += s

        elif prop_type == "preamble":
            outstring = "/*\n"

            # print everything but the last element, we'll print it with no newline
            for p in prop_val[:-1]:
                outstring += "{0}\n".format(p)

            outstring +=  "{0}*/\n".format(prop_val[-1])

        elif prop_type == int:
            outstring = "{0} = <{1}>;".format( self.name, hex(prop_val) )
        elif prop_type == list:
            # if the length is one, and the only element is empty '', then
            # we just put out the name
            if len(prop_val) == 1 and prop_val[0] == '':
                outstring = "{0};".format( self.name )
            else:
                # otherwise, we need to iterate and output the elements
                # as a comma separated list, except for the last item which
                # ends with a ;
                outstring = ""
                outstring_list = "{0} = ".format( self.name )

                # if the attribute was detected as potentially having a
                # phandle, phandle_idx will be non zero.
                #
                # To more easily pick out the elements that we should
                # check for phandle replacement, we generate a list of
                # element numbers that are potential phandles.
                #
                # We generate that list by generating all indexes from 1
                # to the number of elments in our list (+1), and then we
                # slice the list. The slice starts at the phandle index
                # - 1 (since our list starts at position 0), and grabs
                # every "nth" item, where "n" is the number of fields in
                # the element block (number of fields).
                if phandle_idx != 0:
                    phandle_idxs = list(range(1,len(prop_val) + 1))
                    phandle_idxs = phandle_idxs[phandle_idx - 1::phandle_field_count]

                # is this a list of ints, or string ? Test the first
                # item to know.
                list_of_nums = False
                if type(prop_val[0]) == str:
                    # is it really a number, hiding as a string ?
                    base = 10
                    if re.search( "0x", prop_val[0] ):
                        base = 16
                        try:
                            i = int(prop_val[0],base)
                            list_of_nums = True
                        except:
                            pass
                else:
                    list_of_nums = True

                if list_of_nums:
                    # we have to open with a '<', if this is a list of numbers
                    outstring_list += " <"

                element_count = 1
                element_total = len(prop_val)
                outstring_record = ""
                drop_record = False
                drop_all = False

                # print( "prop: %s prop val!!: %s" % (self.name, prop_val ))

                for i in prop_val:
                    if list_of_nums:
                        base = 10
                        if re.search( "0x", str(i) ):
                            base = 16
                        try:
                            i_as_int = int(i,base)
                            i = i_as_int
                        except:
                            pass

                    phandle_tgt_name = ""
                    if phandle_idx != 0:
                        # if we we are on the phandle field, within the number of fields
                        # per element, then we need to look for a phandle replacement
                        if element_count in phandle_idxs:
                            if fdt != None:
                                try:
                                    tgn = fdt.node_offset_by_phandle( i )
                                    phandle_tgt_name = Lopper.phandle_safe_name( fdt.get_name( tgn ) )

                                    if self.__dbg__ > 1:
                                        print( "[DBG+]: [%s:%s] phandle replacement of: %s with %s" % ( self.node.name, self.name, hex(i), phandle_tgt_name))
                                except:
                                    # we need to drop the entire record from the output, the phandle wasn't found
                                    if self.__dbg__ > 1:
                                        print( "[DBG+]: [%s:%s] phandle: %s not found, dropping %s fields" % ( self.node.name, self.name, hex(i), phandle_field_count))

                                    drop_record = True
                                    if len(prop_val) == phandle_field_count:
                                        drop_all = True

                        # if we are on a "record" boundry, latch what we have (unless the drop
                        # flag is set) and start gathering more
                        if element_count % phandle_field_count == 0 and element_count != 0:
                            if not drop_record:
                                outstring_list += outstring_record

                            # reset for the next set of fields
                            outstring_record = ""
                            drop_record = False

                    # is this the last item ?
                    if element_count == element_total:
                        # last item, semicolon to close
                        if list_of_nums:
                            if phandle_tgt_name:
                                outstring_record += "&{0}>;".format( phandle_tgt_name )
                            else:
                                outstring_record += "{0}>;".format( hex(i) )
                        else:
                            outstring_record += "\"{0}\";".format( i )
                    else:
                        # not the last item ..
                        if list_of_nums:
                            if phandle_tgt_name:
                                outstring_record += "&{0} ".format( phandle_tgt_name )
                            else:
                                outstring_record += "{0} ".format( hex(i) )
                        else:
                            outstring_record += "\"{0}\",".format( i )

                    element_count = element_count + 1

                # gather the last record
                if not drop_all:
                    outstring_list += outstring_record
                    # add the lists string output to the overall output string
                    outstring += outstring_list
        else:
            outstring = "{0} = \"{1}\";".format( self.name, prop_val )


        self.string_val = outstring
        self.__pstate__ = "resolved"

class LopperNode(object):
    """Holds the state of a device tree node
    """
    def __init__(self, number = -1, abspath="", tree = None, phandle = -1, name = "", children = None, debug=0 ):
        self.number = number
        self.name = name
        self.parent = None
        self.tree = tree
        self.depth = 0
        if children == None:
            self.children = []
        else:
            self.children = children

        self.phandle = phandle

        # 'type' is roughly equivalent to a compatible property in
        # the node if it exists.
        self.type = []

        self.abs_path = abspath

        self._ref = 0

        # ordered dict, since we want properties to come back out in
        # the order we put them in (when we iterate).
        self.__props__ = OrderedDict()
        self.__current_property__ = -1
        self.__props__pending__ = OrderedDict()

        self.__dbg__ = debug
        # states could be enumerated types
        self.__nstate__ = "init"
        self.__modified__ = False

    # supports NodeA( NodeB ) to copy state
    def __call__( self, othernode=None ):
        if othernode == None:
            nn = copy.deepcopy( self )

            for p in nn.__props__.values():
                p.__modified__ = True

            # invalidate a few things
            # self.children = []
            nn.__nstate__ = "init"
            nn.__modified__ = True

            return nn
        else:
            # we are updating ourself
            nn = copy.deepcopy( othernode )
            # copy everything
            self.__dict__.update(nn.__dict__)

            for p in self.__props__.values():
                p.__modified__ = True

            # invalidate a few things
            # self.children = []
            self.__nstate__ = "init"
            self.__modified__ = True

            return self

    def __setattr__(self, name, value):
        if name == "__dbg__":
            self.__dict__[name] = value
            for p in self.__props__.values():
                p.__dbg__ = value
        else:
            # we do it this way, otherwise the property "ref" breaks
            super().__setattr__(name, value)
            self.__dict__["__modified__"] = True

    # access a property like a structure member
    def __getattribute__(self, name):
        if name == "__setstate__" or name == "__deepcopy__" or name == "__getstate__":
            raise AttributeError(name)

        try:
            return object.__getattribute__(self, name)
        except:
            return self.__props__[name].value

    # when casting to an int, return our node number
    def __int__(self):
        return self.number

    # todo: how to dump the raw object at times ...
    def __str__(self):
        if self.__dbg__ > 1:
            # this will be a raw object print, useful for debug
            return super().__str__()
        else:
            return self.abs_path

    # we are the iterator
    def __iter__(self):
        return self

    def __eq__(self,other):
        if not isinstance( other, LopperNode ):
            return False
        else:
            if self.number == other.number:
                return True

            return False

    # node
    # when iterating a node, we iterate the properties
    def __next__(self):
        if not self.__props__:
            raise StopIteration

        # there's probably a better way to do this ..
        self.__current_property__ = self.__current_property__ + 1
        prop_list = list(self.__props__)

        if self.__current_property__ >= len( prop_list ):
            self.__current_property__ = -1
            raise StopIteration
        else:
            return self.__props__[prop_list[self.__current_property__]]

    # node: access like a dictionary
    def __getitem__(self, key):
        # let the KeyError exception from an invalid key bubble back to
        # the user.
        if type(key) == str:
            return self.__props__[key]

        if isinstance( key, LopperProp ):
            return self.__props__[key.name]

        return None

    # node
    def __setitem__(self, key, val):
        if isinstance(val, LopperProp ):
            # we can try to assign
            self.__props__[key] = val
        else:
            np = LopperProp( key, -1, self, val, self.__dbg__ )
            self.__props__[key] = np
            self.__props__[key].resolve( self.tree.fdt )

            # thrown an exception, since this is not a valid
            # thing to assign.
            #raise TypeError( "LopperProp was not passed as value" )

    @property
    def ref(self):
        return self._ref

    @ref.setter
    def ref(self,ref):
        if ref > 0:
            self._ref += ref
        else:
            self._ref = 0

    # node
    # pass property_mask = "*" to mask them all
    def resolve_all_refs( self, fdt=None, property_mask=[] ):
        """Return all references in a node

        Finds all the references starting from a given node. This includes:

           - The node itself
           - The parent nodes
           - Any phandle referenced nodes, and any nodes they reference, etc

        Args:
           node_name (string or int): The path to a node, or the node number
           property_mask (list of regex): Any properties to exclude from reference
                                          tracking

        Returns:
           A list of referenced nodes, or [] if no references are found

        """
        resolve_fdt = fdt
        if fdt == None:
            resolve_fdt = self.tree.fdt

        property_mask_check = property_mask
        if type(property_mask) != list:
            property_mask_check = [ property_mask ]

        # find all references in the tree, starting from node_name
        reference_list = []

        # is 'node' a name, or number ? Call this to make sure it is just a number
        #node_number = Lopper.node_number( self.RESOLVE_FDT, node )
        #prop_dict = self.node_properties_as_dict( node_number )

        # always add ourself!
        reference_list.append( self )

        # and our parents, but we don't chase all of their links, just their
        # node numbers
        node_parent = self.parent
        while node_parent != None:
            reference_list.append( node_parent )
            node_parent = node_parent.parent

        props_to_consider = []
        for p in self:
            skip = False
            for m in property_mask_check:
                if re.search( m, p.name ):
                    # we are masked
                    skip = True

            if not skip:
                # process the property
                phandle_nodes = p.resolve_phandles( resolve_fdt )
                for ph in phandle_nodes:
                    # don't call in for our own node, or we'll recurse forever
                    if ph != self.number:
                        try:
                            ph_node = self.tree.__nnodes__[ph]
                        except:
                            ph_node = None

                        refs = []
                        if ph_node:
                            refs = ph_node.resolve_all_refs( resolve_fdt, property_mask_check )
                            if refs:
                                reference_list.append( refs )

        # flatten the list
        flat_list = []
        for sublist in reference_list:
            if type(sublist) == list:
                for i in sublist:
                    # drop duplicates while we are at it
                    if i not in flat_list:
                        flat_list.append(i)
            else:
                if sublist not in flat_list:
                    flat_list.append(sublist)

        return flat_list

    # node
    def subnodes( self ):
        # gets you a list of all looper nodes under starting node
        all_kids = [ self ]
        for n in self.children:
            child_node = self.tree[n]
            all_kids = all_kids + child_node.subnodes()

        return all_kids

    # node
    # return True if a change was made
    def sync( self, fdt ):
        retval = False

        # is the node resolved ? if not, it may have been added since we read the
        # tree and created things.
        if self.__nstate__ != "resolved":
            print( "[WARNING]: node sync: unresolved node, not syncing" )
        else:
            if self.__dbg__ > 1:
                print( "[DBG+]: node sync: %s" % (self.abs_path) )

            # check the FDT number and our number, and update as required
            nn = Lopper.node_find( fdt, self.abs_path )
            if nn != self.number:
                if self.__dbg__ > 2:
                    print( "[DBG++]: node sync: fdt and node number differ, updating node '%s' from %s to %s" % (self.abs_path,self.number,nn) )
                self.number = nn

            fdt_name = fdt.get_name( self.number )
            if fdt_name != self.name:
                fdt.set_name( self.number, self.name )

            # sync any modified properties to a device tree
            for p in self.__props__.values():
                if self.__dbg__ > 2:
                    print( "[DBG++]:    node sync: syncing property: %s %s" % (p.name,p.__pstate__) )

                if p.__modified__:
                    if self.__dbg__ > 2:
                        print( "[DBG++]:    node sync: property %s is modified, writing back" % p.name )
                    p.sync( fdt )
                    retval = True
                if p.__pstate__ == "init":
                    if self.__dbg__ > 2:
                        print( "[DBG++]:    node sync: property %s is new, creating with value: %s" % (p.name,p.value) )
                    p.sync( fdt )
                    retval = True

            for p in list(self.__props__pending__.values()):
                if p.__pstate__ == "deleted":
                    if self.__dbg__ > 2:
                        print( "[DBG++]:    node sync: pending property %s is delete, writing back" % p.name )

                    Lopper.prop_remove( fdt, self.name, p.name )
                    del self.__props__pending__[p.name]
                    retval = True

            self.__modified__ = False

        return retval

    # node
    def delete( self, prop ):
        if self.__dbg__ > 1:
            print( "[DBG+]: deleting property %s from node %s" % (prop, self))

        prop_to_delete = prop
        if type(prop) == str:
            try:
                prop_to_delete = self.__props__[prop]
            except Exception as e:
                raise e
        if not isinstance( prop_to_delete, LopperProp ):
            print( "[WARNING]: invalid property passed to delete: %s" % prop )

        self.__modified__ = True
        try:
            prop_to_delete.__pstate__ = "deleted"
            self.__props__pending__[prop_to_delete.name] = prop_to_delete
            del self.__props__[prop_to_delete.name]
        except Excption as e:
            raise e

    # A direct way to get a LopperProp (versus going at the
    # member, or iterating )
    def props( self, name ):
        pmatches = []
        try:
            pmatches = [self.__props__[name]]
        except:
            # maybe it was a regex ?
            for p in self.__props__.keys():
                if re.search( name, p ):
                    pmatches.append( self.__props__[p] )

        return pmatches

    # a safe (i.e. no exception) way to fetch a propery value
    def propval( self, pname ):
        try:
            prop = self.__props__[pname]
            return prop.value
        except:
            return [""]

    def reset(self):
        self.__current_property__ = -1

    # in case someone wants to use "node" + "prop"
    def __add__( self, other ):
        if not isinstance( other, LopperProp ):
            return self

        self.add( other )

        return self

    # node. for "node" - "prop"
    def __sub__( self, other ):
        if not isinstance( other, LopperProp ):
            return self

        self.delete( other )

        return self

    # node: for "del <node>[prop]"
    def __delitem__(self, key):
        pass

    # node. this is adding a property
    def add( self, p ):
        if self.__dbg__ > 2:
            print( "[DBG++]: node %s adding property: %s" % (self.abs_path,p.name) )

        self.__props__[p.name] = p

        # indicates that we should be sync'd before being written
        self.__modified__ = True

        return self

    # node
    def resolve( self, fdt ):
        # resolve the rest of the references based on the passed device tree
        # self.number must be set before calling this routine.

        if self.__dbg__ > 2:
            print( "[DBG++]: node resolution start [%s]: %s" % (self,self.abs_path))

        if fdt:
            if self.number >= 0:
                self.name = fdt.get_name(self.number)
                self.phandle = Lopper.node_getphandle( fdt, self.number )

                if self.number > 0:
                    self.abs_path = Lopper.node_abspath( fdt, self.number )
                else:
                    self.abs_path = "/"

                if self.__dbg__ > 2:
                    print( "[DBG++]:    resolved: number: %s name: %s path: %s" % ( self.number, self.name, self.abs_path ) )

                # parent and depth
                if self.number > 0:
                    depth = 1

                    parent_node_num = fdt.parent_offset( self.number, QUIET_NOTFOUND )
                    parent_path = Lopper.node_abspath( fdt, parent_node_num )
                    if self.tree:
                        self.parent = self.tree[parent_path]

                    p = parent_node_num
                    while p > 0:
                        depth = depth + 1
                        p = fdt.parent_offset( p, QUIET_NOTFOUND )
                else:
                    depth = 0

                self.depth = depth

                self.children = []
                offset = fdt.first_subnode( self.number, QUIET_NOTFOUND )
                while offset > 0:
                    child_path = Lopper.node_abspath( fdt, offset )
                    self.children.append(child_path)
                    offset = fdt.next_subnode(offset, QUIET_NOTFOUND)

                # decode the properties
                self.type = []
                prop_list = []
                poffset = fdt.first_property_offset(self.number, QUIET_NOTFOUND)
                while poffset > 0:
                    prop = fdt.get_property_by_offset(poffset)
                    prop_val = Lopper.prop_get( fdt, self.number, prop.name, LopperFmt.COMPOUND )

                    # special handling for 'compatible', we bubble it up as the node "type"
                    if prop.name == "compatible":
                        self.type += prop_val

                    ## TODO: simlar to the tree resolve() we might not want to throw these
                    ##       away if the exist, and instead sync + update
                    # create property objects, and resolve them
                    self.__props__[prop.name] = LopperProp( prop.name, poffset, self, prop_val, self.__dbg__ )
                    self.__props__[prop.name].resolve( fdt )
                    self.__props__[prop.name].__modified__ = False

                    poffset = fdt.next_property_offset(poffset, QUIET_NOTFOUND)

            if not self.type:
                self.type = [ "" ]

            self.__nstate__ = "resolved"
            self.__modified__ = False

            if self.__dbg__ > 2:
                print( "[DGB++]: node resolution end: %s" % self)

class LopperTree:
    """Class for walking a device tree, and providing callbacks at defined points
    """
    def __init__(self, fdt, snapshot = False, depth_first=True ):
        # copy the tree, so we'll remain valid if operations happen
        # on the tree. This is unlike many of the other modes
        if snapshot:
            self.fdt = Fdt( fdt.as_bytearray() )
        else:
            self.fdt = fdt

        # nodes, indexed by abspath
        self.__nodes__ = OrderedDict()
        # nodes, indexed by node number
        self.__nnodes__ = OrderedDict()
        # nodes, indexed by phandle
        self.__pnodes__ = OrderedDict()

        # callbacks
        # these can even be lambdas. i.e lambda n, fdt: print( "start the tree!: %s" % n )
        self.start_tree_cb = ""
        self.start_node_cb = ""
        self.end_node_cb = ""
        self.end_tree_cb = ""
        self.property_cb = ""

        # state
        self.__dbg__ = 0
        self.__must_sync__ = False
        self.__current_node__ = 0
        self.__start_node__ = 0
        self.__current_depth__ = 0
        self.__current_property__ = 0
        self.__new_iteration__ = True
        self.node_iter = None

        # type
        self.depth_first = depth_first

        # resolve against the fdt
        self.resolve()

    # we are the iterator
    def __iter__(self):
        return self

    # tree
    def __next__(self):
        n = self.next()
        if n.number == -1:
            raise StopIteration

        return n

    # tree
    def __setattr__(self, name, value):
        if name == "__current_node__" or name == "__start_node__":
            if type(value) == int:
                self.__dict__[name] = value
            else:
                nn = Lopper.node_number( self.fdt, value )
                self.__dict__[name] = nn
                # it's always a new iteration when you start via a path
                self.__new_iteration__ = True
        elif name == "__dbg__":
            # set all the nodes to debug
            self.__dict__[name] = value
            for n in self.__nodes__.values():
                n.__dbg__ = value
        else:
            self.__dict__[name] = value

        # TODO: we could detect if fdt is assigned/writen and re-run a resolve()

    # tree
    def __getattribute__(self, name):
        # try first as an attribute of the object, then, as an
        # index into the nodes by name (but since most names are
        # not valid python member names, it isn't all that useful.
        # more useful are the __*item*__ routines.
        try:
            return object.__getattribute__(self, name)
        except:
            try:
                # a common mistake is to leave a trailing / on a node
                # path. Drop it to make life easier.
                access_name = name.rstrip( self.__nodes__[access_name] )
            except:
                return None

    def __getitem__(self, key):
        # let the KeyError exception from an invalid key bubble back to the
        # user.
        if type(key) == int:
            return self.__nnodes__[key]

        if isinstance( key, LopperNode ):
            return self.__nodes__[key.abs_path]

        try:
            # a common mistake is to leave a trailing / on a node
            # path. Drop it to make life easier.
            access_name = key.rstrip('/')
            return self.__nodes__[access_name]
        except Exception as e:
            # is it a regex ?
            # we tweak the key a bit, to make sure the regex is bounded.
            m = self.nodes( "^" + key + "$" )
            if m:
                # we get the first match, if you want multiple matches
                # call the "nodes()" method
                return m[0]

            raise e

    def __setitem__(self, key, val):
        if isinstance(val, LopperNode ):
            # we can try to assign
            if type(key) == int:
                self.__nnodes__[key] = val
                self.__nodes__[val.abspath] = val
                if val.phandle != 0:
                    self.__pnodes__[val.phandle] = val
            else:
                self.__nodes__[key] = val
                self.__nnodes__[val.number] = val
                if val.phandle != 0:
                    self.__pnodes__[val.phandle] = val
        else:
            # thrown an exception, since this is not a valid
            # thing to assign.
            raise TypeError( "LopperNode was not passed as value" )

    # tree
    # deleting a dictionary key
    def __delitem__(self, key):
        # not currently supported
        pass

    # tree
    def ref_all( self, starting_node, parent_nodes=False ):
        if parent_nodes:
            refd_nodes = starting_node.resolve_all_refs( self.fdt, [".*"] )

        # reference the starting node and all subnodes, we could extend
        subnodes_to_ref = starting_node.subnodes()

        nodes_to_ref = []
        for n in refd_nodes + subnodes_to_ref:
            if n not in nodes_to_ref:
                nodes_to_ref.append(n)

        for n in nodes_to_ref:
            n.ref = 1


    # tree wide ref set / clear
    def ref( self, value, node_regex = None ):
        if node_regex:
            nodes = self.nodes( node_regex )
        else:
            nodes = self.__nodes__.values()

        for n in nodes:
            n.ref = value

    # tree
    def refd( self, node_regex="" ):
        """Get a list of refererenced nodes

        When refcounting is enabled, this routine returns the list of nodes
        that have been referenced.

        We use the name, rather than the offset, since the offset can change if
        something is deleted from the tree. But we need to use the full path so
        we can find it later.

        Args:
           node_regex: limit returned nodes to those that match the regex, which
                       is applied to the path of the nodes.

        Returns:
           list (strings): list of referenced nodes, or [] if there are no referenced nodes

        """

        rnodes = []
        for n in self:
            if n.ref > 0:
                rnodes.append(n)

        if node_regex:
            ret_nodes = []
            for n in rnodes:
                if re.search( node_regex, n.abs_path ):
                    ret_nodes.append( n )
        else:
            ret_nodes = rnodes

        return ret_nodes

    # tree
    def sync( self, fdt = None ):
        sync_fdt = fdt
        if sync_fdt == None:
            sync_fdt = self.fdt

        if self.__dbg__ > 2:
            print( "[DBG++][%s]: tree sync start: %s" % (sync_fdt,self) )

        # hmm. either we have to walk our nodes, and sync them and do it
        # iteratively until no changes are detected, or do a 2nd loop to
        # update the node numbers. OR we could walk the FDT, get the names
        # of the nodes in order, lookup their node in our dict and do the
        # sync that way....this 2nd way works, since we will get them in
        # order and pickup any trickle down changes (as far as we know).

        # technique b)
        nn = 0
        depth = 0
        while depth >= 0:
            lname = Lopper.node_abspath( sync_fdt, nn )
            try:
                if self.__nodes__[lname]:
                    self.__nodes__[lname].sync( sync_fdt )
            except:
                pass

            nn, depth = sync_fdt.next_node( nn, depth, (libfdt.BADOFFSET,) )

        # technique a)
        # sync any modified properties to a device tree
        # for n in self.__nodes__.values():
        #     if self.__dbg__ > 2:
        #         print( "[DBG++][%s]: tree sync node: %s" % (sync_fdt,n.name) )

        #     n.sync( sync_fdt )

        if self.__dbg__ > 2:
            print( "[DBG++][%s]: tree sync end: %s" % (sync_fdt,self) )

        # resolve and details that may have changed from the sync
        self.resolve()
        self.__must_sync__ = False

    # in case someone wants to do "tree" - "node"
    def __sub__( self, other ):
        if not isinstance( other, LopperNode ):
            return self

        self.delete( other )

        return self

    # tree
    def delete( self, node ):
        n = node
        # not a great idea to delete by number, but we support it as
        # a transitional step
        if type(node) == int:
            # let any exceptions bubble back up
            n = self.__nnodes__[node]

        if n.__nstate__ == "resolved" and self.__must_sync__ == False:
            if self.__dbg__ > 1:
                print( "[DBG+]: %s deleting node %s" % (self,n))

            if Lopper.node_remove( self.fdt, n.number ):
                self.sync()
                return True

        return False

    # in case someone wants to use "tree" + "node"
    def __add__( self, other ):
        if not isinstance( other, LopperNode ):
            return self

        self.add( other )

        return self

    # tree
    def add( self, node ):
        # do we already have a node at this path ?
        try:
            existing_node = self.__nodes__[node.abs_path]
        except:
            existing_node = None

        if existing_node:
            if self.__dbg__ > 2:
                print( "[WARNING]: add: node: %s already exists" % node.abs_path )
            return self

        # node is a LopperNode
        node.number = Lopper.node_add( self.fdt, node.abs_path, True, 0 )

        node.__dbg__ = self.__dbg__

        # put the new node in the nodes dictionary and resolve it. This is
        # temporary, since it will be re-ordered and re-solved below, but they
        # key off the dictionary, so we need it in the dict to be processed
        node.resolve( self.fdt )

        self.__nodes__[node.abs_path] = node

        if self.__dbg__ > 1:
            print( "[DBG+][%s] node added: %s" % (self.fdt,node.abs_path) )
            if self.__dbg__ > 2:
                for p in node:
                    print( "[DBG++]      property: %s %s" % (p.name,p.value) )

        self.__must_sync__ = True
        self.sync()

        return self

    # tree
    def subnodes( self, start_node, node_regex = None ):
        # start_node is a LopperNode
        # gets you a list of all looper nodes under starting node
        all_kids = [ start_node ]
        for n in start_node.children:
            all_kids = all_kids + self.subnodes( self.__nodes__[n] )

        all_matching_kids = []
        if node_regex:
            # we are filtering on a regex, drop nodes that don't match
            for n in all_kids:
                if re.search( node_regex, n.abs_path ):
                    all_matching_kids.append( n )
        else:
            all_matching_kids = all_kids

        return all_matching_kids

    # tree
    # nodename can be a regex
    def nodes( self, nodename ):
        matches = []
        try:
            matches = [self.__nodes__[nodename]]
        except:
            # maybe it was a regex ?
            for n in self.__nodes__.keys():
                if re.search( nodename, n ):
                    matches.append( self.__nodes__[n] )

        return matches

    # tree
    # node by phandle!
    def pnode( self, phandle ):
        try:
            return self.__pnodes__[phandle]
        except:
            return None


    # tree
    def filter( self, node_prefix, action, test_cmd, fdt=None, verbose=0 ):
        """Filter nodes and perform an action

        Starting from the supplied path (node_prefix), this function walks
        the device tree and executes a block of python code to test each
        node.

        If the block of code (test_cmd) returns True, then the action is
        taken. If false, nothing is done.

        Currently defined actions:

           - delete: delete the node
           - report: (not currently implemented)
           - whitelist: (not currently implemented)
           - blacklist: (not currently implemented)

        The "test_cmd" python code, runs in a constructed/safe environment to
        ensure that the code won't cause harmful sideffects to the execution
        environment.

        The following functions and variables are currently available in the
        safe_dict:

            len
            print
            verbose

        When executing in the filter context (aka node walking), the following
        variables are available to the python code block.

            fdt  : the flattened device tree being processed
            node : the LopperNode being processed
            node_name : the name of the node (as defined by the dts/dtb)
            node_number : the number of the node being processed

        A standard python "return True" and "return False" should be used to
        indicate the result of the test.

        Args:
            node_prefix (string): starting node path
            action (LopperAction): action to take in the True condition
            test_cmd (string): block of python code to test against each node
            fdt (FDT,optional): flattended device tree for reference
            verbose (int,optional): verbosity level to use.

        Returns:
            Nothing

        """
        fdt = self.fdt
        if verbose:
            print( "[NOTE]: filtering nodes root: %s" % node_prefix )

        if not node_prefix:
            node_prefix = "/"

        try:
            start_node = self[node_prefix]
            node_list = start_node.subnodes()
        except:
            start_node = None
            node_list = []
            if verbose:
                print( "[ERROR]: no nodes found that match prefix %s" % node_prefix )
            sys.exit(1)


        # make a list of safe functions
        safe_list = ['Lopper.prop_get', 'Lopper.node_getphandle', 'verbose', 'print']

        # this should work, but isn't resolving the local vars, so we have to add them again in the
        # loop below.
        # references: https://stackoverflow.com/questions/701802/how-do-i-execute-a-string-containing-python-code-in-python
        #             http://code.activestate.com/recipes/52217-replace-embedded-python-code-in-a-string-with-the-/
        safe_dict = dict([ (k, locals().get(k, None)) for k in safe_list ])
        safe_dict['len'] = len
        safe_dict['print'] = print
        safe_dict['prop_get'] = Lopper.prop_get
        safe_dict['getphandle'] = Lopper.node_getphandle
        safe_dict['fdt'] = fdt
        safe_dict['verbose'] = verbose

        if verbose > 1:
            print( "[INFO]: filter: base safe dict: %s" % safe_dict )
            print( "[INFO]: filter: node list: ", end=" " )
            for nn in node_list:
                print( "%s" % nn.abs_path, end="  " )
            print( "" )

        for n in node_list:
            # build up the device tree node path
            # node_name = node_prefix + n
            node_name = n.abs_path
            # node = fdt.path_offset(node_name)
            node_number = n.number
            #print( "---------------------------------- node name: %s" % fdt.get_name( node ) )
            # prop_list = Lopper.property_list( fdt, node_name )
            prop_list = n.__props__
            #print( "---------------------------------- node props name: %s" % prop_list )

            # Add the current node (n) to the list of safe things
            # NOTE: might not be required
            # safe_list.append( 'n' )
            # safe_list.append( 'node_name' )

            # add any needed builtins back in
            safe_dict['node'] = n
            safe_dict['node_number'] = node_number
            safe_dict['node_name' ] = node_name

            # search and replace any template options in the cmd. yes, this is
            # only a proof of concept, you'd never do this like this in the end.
            tc = test_cmd

            # we wrap the test command to control the ins and outs
            __nret = False
            # indent everything, its going in a function
            tc_indented = textwrap.indent( tc, '    ' )
            # define the function, add the body, call the function and grab the return value
            tc_full_block = "def __node_test_block():" + tc_indented + "\n__nret = __node_test_block()"

            if verbose > 2:
               print( "[DBG+]: filter node cmd:\n%s" % tc_full_block )

            # compile the block, so we can evaluate it later
            b = compile( tc_full_block, '<string>', 'exec' )

            x = locals()
            y = globals()

            # we merge the locals and globals into a single dictionary, so that
            # the local variables of *this* function (i.e. node, node_name) that
            # change each loop, will be availble when calling the code block as
            # globals in that context.
            m = {**x, **y, **safe_dict}

            # TODO: we could restrict the locals and globals a bit more, but
            #       in this function context, the side effects are limited to
            #       the dictionary 'm'.
            #
            #       BUT we should ensure that modules like 'os' aren't available
            #       to be mis-used
            #          x = eval( b, {"__builtins__" : None }, locals() )
            #       or
            #          x = eval( b, {"__builtins__" : None }, safe_dict )
            try:
                eval( b, m, m )
            except Exception as e:
                print("[WARNING]: Something wrong with the filter code: %s" % e)
                sys.exit(1)

            if verbose > 2:
                print( "[DBG+] return code was: %s" % m['__nret'] )

            # did the block set the return variable to True ?
            if m['__nret']:
                if action == LopperAction.DELETE:
                    if verbose:
                        print( "[INFO]: deleting node %s" % node_name )
                    self.delete( n )
            else:
                pass

    def exec(self):
        if self.__dbg__ > 4:
            print( "[DBG++++]: LopperTree exec start" )

        last_children = []
        chain_close_dict = {}
        for n in self:
            if self.__dbg__ > 4:
                print( "[DBG++++]: node: %s:%s [%s] parent: %s children: %s" % (n.name, n.number, n.phandle, n.parent, n.children))
            if n.number == 0:
                if self.start_tree_cb:
                    self.start_tree_cb( n, self.fdt )

            if n.children:
                # add the last child in our list, we'll use it to know when to end a node.
                # we could remove these on the close, if memory becomes an issue
                if self.__dbg__ > 4:
                    print( "[DBG++++]: node %s (%s) has last child %s" % (n.number,n.abs_path,n.children[-1]))

                if not n.abs_path in last_children:
                    last_children.append( n.abs_path )

                last_children.append( n.children[-1] )
                chain_close_dict[n.children[-1]] = n
                if self.__dbg__ > 4:
                    print( "[DBG++++]: mapped chain close %s (%s) to %s" % (n.number,n.abs_path,n.children[-1]))

            if self.start_node_cb:
                self.start_node_cb( n, self.fdt )

            # node stuff
            # i.e. iterate the properties and print them
            for p in n:
                if self.property_cb:
                    self.property_cb( p, self.fdt )

            # check to see if we are closing the node.
            #if last_children and n.number == last_children[-1]:
            if last_children and n.abs_path == last_children[-1]:
                if self.__dbg__ > 4:
                    print( "[DBG++++]: %s is in %s" % (n.number, last_children ))

                # we are closing!
                if self.end_node_cb:
                    self.end_node_cb( n, self.fdt )

                # pop the last child
                del last_children[-1]

                cc_close = n.abs_path
                to_close = n.abs_path
                while cc_close in list(chain_close_dict.keys()):
                    if self.__dbg__ > 4:
                        print( "[DBG++++]: chain close" )

                    to_close = chain_close_dict[cc_close]

                    if self.__dbg__ > 4:
                        print( "[DBG++++]: would close %s %s" % (to_close.abs_path,to_close ))

                    if last_children[-1] == to_close.abs_path:
                        del last_children[-1]
                        del chain_close_dict[cc_close]
                    else:
                        print( "[WARNING]: INCONSISTENCY FOUND WALKING TREE" )

                    if self.end_node_cb:
                        self.end_node_cb( to_close, self.fdt )

                    cc_close = to_close.abs_path
            elif not n.children:
                # we are closing!
                if self.end_node_cb:
                    if self.__dbg__ > 4:
                        print( "[DBG++++]: no children, closing node" )
                    self.end_node_cb( n, self.fdt )

        if self.end_tree_cb:
            self.end_tree_cb( -1, self.fdt )

    def reset(self):
        self.__current_node__ = 0
        self.__current_depth__ = 0
        self.__current_property__ = 0
        self.__new_iteration__ = True

    # tree
    def resolve(self):
        if self.depth_first:
            nodes_saved = dict(self.__nodes__)

            # clear the old dictionaries, we want to track the order by this
            # resolution, since it may be a re-resolve

            # nodes, indexed by abspath
            self.__nodes__ = OrderedDict()
            # nodes, indexed by node number
            self.__nnodes__ = OrderedDict()
            # nodes, indexed by phandle
            self.__pnodes__ = OrderedDict()

            if self.__dbg__ > 2:
                print( "[DGB+]: tree resolution start: %s" % self )

            nn = 0
            depth = 0
            while depth >= 0:
                # create the node
                # node = LopperNode( nn )

                abs_path = Lopper.node_abspath( self.fdt, nn )
                try:
                    # we try and re-use the node if possible, since that keeps
                    # old references valid for adding more properties, etc
                    node = nodes_saved[abs_path]

                    # fix any numbering changes, etc.
                    # This should already have been done, it is commented out, since
                    # fundamentally it is a re-sync/re-resolve if anything changed
                    # after this node during a full tree sync, but the sync needs to
                    # take care of that, so we are leaving this out.
                    # node.sync( self.fdt )
                except:
                    # node didn't exist before, create it as something new
                    node = LopperNode( nn, "", self )

                node.__dbg__ = self.__dbg__

                # resolve the details against the fdt
                node.resolve( self.fdt )

                # we want to find these by name AND number (but note, number can
                # change after some tree ops, so make sure to check the state of
                # a tree/node before using the number
                self.__nodes__[node.abs_path] = node
                self.__nnodes__[node.number] = node
                if node.phandle > 0:
                    self.__pnodes__[node.phandle] = node

                nn, depth = self.fdt.next_node( nn, depth, (libfdt.BADOFFSET,) )

            for node_abs_path in nodes_saved:
                # invalidate nodes, in case someone is holding a reference
                # to them
                try:
                    state = self.__nodes__[node_abs_path]
                except:
                    # the node didn't get copied over, invalidate the state
                    nodes_saved[node_abs_path].__nstate__ = "*invalid*"
        else:
            # breadth first. not currently implemented
            pass


    def next(self):
        node = None

        if self.__new_iteration__:
            self.__new_iteration__ = False

            # by default, we'll just iterate the nodes as they went
            # into our dictionary
            self.node_iter = iter( self.__nodes__.values() )

            if self.__current_node__ == 0 and self.__start_node__ == 0:
                # just get the first node out of the default iterator
                node = next(self.node_iter)
            elif self.__start_node__ != 0:
                # this is a starting node, so we fast forward and then use
                # the default iterator
                node = next(self.node_iter)
                while node.number != self.__start_node__:
                    node = next(self.node_iter)
            else:
                # non-zero current_node, that means we'll do a custom iteration
                # of only the nodes that are underneath of the set current_node
                child_nodes = self.subnodes( self.__nnodes__[self.__current_node__] )
                self.node_iter = iter( child_nodes )
                node = next(self.node_iter)
        else:
            if self.depth_first:
                try:
                    node = next(self.node_iter)
                except StopIteration:
                    # reset for the next call
                    self.reset()
                    raise StopIteration
            else:
                # TODO (may not be required)
                # breadthfirst, we should iterate through a given depth
                pass

        return node

class LopperTreePrinter( LopperTree ):

    def __init__( self, fdt, snapshot = False, output=sys.stdout, debug=0 ):
        # init the base walker.
        super().__init__( fdt, snapshot )

        self.start_tree_cb = self.start
        self.start_node_cb = self.start_node
        self.end_node_cb   = self.end_node
        self.end_tree_cb   = self.end
        self.property_cb   = self.start_property

        self.output = output
        if output != sys.stdout:
            self.output = open( output, "w")

        self.__dbg__ = debug

    def start(self, n, fdt ):
        # peek ahead to handle the pre-amble
        for p in n:
            if p.type == "preamble":
                print( "%s" % p, file=self.output )

        print( "/dts-v1/;\n\n/ {", file=self.output )

    def start_node(self, n, fdt ):
        indent = n.depth * 8
        nodename = n.name
        if n.number != 0:
            if n.phandle != 0:
                outstring = Lopper.phandle_safe_name( nodename ) + ": " + nodename + " {"
            else:
                outstring = nodename + " {"

            print(outstring.rjust(len(outstring)+indent," " ), file=self.output )

    def end_node(self, n, fdt):
        indent = n.depth * 8
        outstring = "};\n"
        print(outstring.rjust(len(outstring)+indent," " ), file=self.output)

    def start_property(self, p, fdt):
        # do we really need this resolve here ? We are already tracking if they
        # are modified/dirty, and we have a global resync/resolve now. I think it
        # can go
        p.resolve(fdt)

        indent = (p.node.depth * 8) + 8
        outstring = str( p )

        if p.type == "comment":
            # we have to substitute \n for better indentation, since comments
            # are multiline
            dstring = ""
            dstring = dstring.rjust(len(dstring) + indent - 3, " " )
            outstring = re.sub( '\n', '\n' + dstring, outstring )

        if p.type == "preamble":
            # start tree peeked at this, so we do nothing
            outstring = ""

        if outstring:
            print(outstring.rjust(len(outstring)+indent," " ), file=self.output)

    def end(self, n,fdt):
        if self.output != sys.stdout:
            self.output.close()


