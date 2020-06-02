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
    NONE = 5


class LopperProp():
    """Class representing a device tree property

    This class implements:
       - resolve(): to update information / state against a device tree
       - sync(): to write changes back to the device tree
       - utility routines for easy access and iteration of the values

    Attributes:
       - __modified__: Flag to indicate if the property has been changed
       - __pstate__: The state of the property. For internal use only.
                     Values can be: "init", "resolved", "syncd" or "deleted"
       - __dbg__: The debug/verbosity level of property operations. 0 is no
                  debug, and levels increase from there.

       - name: The property name
       - value: The property value (always as a list of values)
       - node: The node that contains this property
       - number: The property offset within the containing node (rarely used)
       - string_val: The enhanced printed string representation of a property
       - type: The type of a property, "comment", "preamble" or "list"
       - abs_path: The absolute device tree path to this property

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
        self.binary = False

        self.abs_path = ""

    def __str__( self ):
        """The string representation of the property

        Returns the enhanced printed property when str() is used to access
        an object.

        The string_val is composed in the resolv() function, and takes the
        format of:  <property name> = <property value>;

        Args:
           None

        Returns:
           string
        """
        return self.string_val

    def int(self):
        """Get the property value as a list of integers

        Args:
           None

        Returns:
           list: integer formatted property value
        """
        ret_val = []
        for p in self.value:
            ret_val.append( p )

        return ret_val

    def hex(self):
        """Get the property value as a list of hex formatted numbers

        Args:
           None

        Returns:
           list: hex formatted property value
        """
        ret_val = []
        for p in self.value:
            ret_val.append( hex(p) )

        return ret_val

    def __setattr__(self, name, value):
        """magic method to check the setting of a LopperProp attribute

        If the attribute being set is "value" (i.e. LopperProp.value), this
        method makes sure that it is stored as a list, that the property is
        marked as modified (for future write backs) and triggers a resolve()
        of the property value.

        Args:
           name: attribute name
           value: attribute value

        Returns:
           Nothing
        """
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
                self.__modified__ = True

            # NOTE: this will not update phandle references, you need to
            #       do a full resolve with a fdt for that.
            self.resolve( None )
        else:
            self.__dict__[name] = value

    def compare( self, other_prop ):
        """Compare one property to another

        Due to the complexity of property representations, this compare is
        not a strict 1:1 value equality. It looks through various elements
        of the source and comparision properties to decide if they have
        common components.

        The following metrics are used, where "single" means a property with
        a single value (string or number) and "list" is a property with
        multiple strings or integer properties.

          comparison types:
               single -> list:   single must be somewhere in the list
                                    - for strings, single may be a regex
               single -> single: single must be in or equal the other
                                    - for strings, single may be a regex
               list -> single:   any value in list must match single
                                    - for strings, list elements can be regexs
               list -> list:     all individual elements must match
                                    - NO regexs allowed

        Args:
           other_prop (LopperProp): comparison target
           value: attribute value

        Returns:
           boolean: True there is a match, false otherwise
        """
        if self.__dbg__ > 1:
            print( "[DBG++]: property compare compare (%s) vs (%s)" % (self,other_prop) )

        ret_val = False
        invert_check  = ""
        if len(self.value) == 1:
            # single comparison value
            if len( other_prop.value ) == 1:
                # single -> single: single must be in or equal the other
                lop_compare_value = self.value[0]
                tgt_node_compare_value = other_prop.value[0]

                if type(lop_compare_value) == str:
                    constructed_condition = "{0} re.search(\"{1}\",\"{2}\")".format(invert_check,lop_compare_value,tgt_node_compare_value)
                elif type(lop_compare_value) == int:
                    constructed_condition = "{0} {1} == {2}".format(invert_check,lop_compare_value,tgt_node_compare_value)

                if self.__dbg__ > 2:
                    print( "[DBG+++]:    single:single. Condition: %s" % (constructed_condition))

                constructed_check = eval(constructed_condition)
                if constructed_check:
                    ret_val = True
                else:
                    ret_val = False
            else:
                #  single -> list:  single must be somewhere in the list
                #                     - for strings, single may be a regex
                lop_compare_value = self.value[0]
                ret_val = False
                for tgt_node_compare_value in other_prop.value:
                    # if we have found a match, we are done and can stop comparing
                    if ret_val:
                        continue

                    # otherwise, run the compare
                    if type(lop_compare_value) == str:
                        constructed_condition = "{0} re.search(\"{1}\",\"{2}\")".format(invert_check,lop_compare_value,tgt_node_compare_value)

                    elif type(lop_compare_value) == int:
                        constructed_condition = "{0} {1} == {2}".format(invert_check,lop_compare_value,tgt_node_compare_value)

                    if self.__dbg__ > 2:
                        print( "[DBG+++]:    single:list. Condition: %s" % (constructed_condition))

                    constructed_check = eval(constructed_condition)
                    if constructed_check:
                        ret_val = True
                    else:
                        ret_val = False
        else:
            # list comparison value
            if len( other_prop.value ) == 1:
                # list -> single:  any value in list must match single
                #                     - for strings, list elements can be regexs

                tgt_node_compare_value = other_prop.value[0]
                for lop_compare_value in self.value:
                    # if we have found a match, we are done and can stop comparing
                    if ret_val:
                        continue

                    # otherwise, run the compare
                    if type(lop_compare_value) == str:
                        constructed_condition = "{0} re.search(\"{1}\",\"{2}\")".format(invert_check,lop_compare_value,tgt_node_compare_value)

                    elif type(lop_compare_value) == int:
                        constructed_condition = "{0} {1} == {2}".format(invert_check,lop_compare_value,tgt_node_compare_value)

                    if self.__dbg__ > 2:
                        print( "[DBG+++]:    list:single. Condition: %s" % (constructed_condition))

                    constructed_check = eval(constructed_condition)
                    if constructed_check:
                        ret_val = True
                    else:
                        ret_val = False
            else:
                lop_compare_value = self.value
                tgt_node_compare_value = other_prop.value

                # list -> list
                # regex are not supported (since we'd have to index iterate and run
                # different compares. So instead, we just compare the lists directly
                if self.__dbg__ > 2:
                    print( "[DBG+++]:    list:list. Condition: %s == %s" % (lop_compare_value,tgt_node_compare_value))

                if lop_compare_value == tgt_node_compare_value:
                    ret_val = True
                else:
                    ret_val = False

        if self.__dbg__ > 2:
            print( "[DBG+++]:        prop compare: %s" % (ret_val))

        return ret_val

    def phandle_params( self ):
        """Determines the phandle elements/params of a property

        Takes a property name and returns where to find a phandle in
        that property.

        Both the index of the phandle, and the number of fields in
        the property are returned.

        Args:
            None

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
                        #      field_val = Lopper.property_get( fdt, nodeoffset, f, LopperFmt.SIMPLE )
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

    def sync( self, fdt ):
        """sync the property to a backing FDT

        Writes the property value to the backing flattended device tree. After
        write, the state is set to  "syncd" and the modified flat is cleared.

        Args:
           fdt (FDT): flattened device tree to sync to

        Returns:
           boolean: True if the property was sync'd, otherwise False
        """
        # we could do a read-and-set-if-different
        Lopper.property_set( fdt, self.node.number, self.name, self.value, LopperFmt.COMPOUND )
        self.__modified__ = False
        self.__pstate__ = "syncd"

        return True

    def resolve_phandles( self, fdt ):
        """Resolve the targets of any phandles in a property

        Args:
            fdt (FDT): flattened device tree

        Returns:
            A list of all resolved phandle node numbers, [] if no phandles are present
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
                        # TODO: this should be a list of LopperNodes, not offsets
                        tgn = fdt.node_offset_by_phandle( i )
                        phandle_tgt_name = Lopper.phandle_safe_name( fdt.get_name( tgn ) )
                        # TODO: name isn't used, we could probably drop the call.
                        phandle_targets.append( tgn )
                    except:
                        pass

                element_count = element_count + 1
        else:
            return phandle_targets

        return phandle_targets

    def resolve( self, fdt ):
        """resolve (calculate) property details against a FDT

        Some attributes of a property are not known at initialization
        time, or may change due to tree operations.

        This method calculates those values using information in the
        property and in the passed FDT. If no FDT is passed only
        partial resolution is done.

        Fields resolved:
           - abs_path
           - type
           - string_val (with phandles resolved)
           - __pstate__

        Args:
           fdt (FDT): flattened device tree to sync to or None if no
                      tree is available

        Returns:
           Nothing
        """
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
        elif re.search( "lopper-label.*", self.name ):
            prop_type = "label"
        else:
            # we could make this smarter, and use the Lopper Guessed type
            prop_type = type(prop_val)

        self.type = prop_type

        phandle_idx, phandle_field_count = self.phandle_params()

        if prop_type == "comment":
            outstring = ""
            for s in prop_val:
                outstring += s

        elif prop_type == "label":
            outstring = ""

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
            if len(prop_val) == 0:
                outstring = ""
            elif len(prop_val) == 1 and prop_val[0] == '':
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
                    if self.binary:
                        outstring_list += "["
                    else:
                        # we have to open with a '<', if this is a list of numbers
                        outstring_list += "<"

                element_count = 1
                element_total = len(prop_val)
                outstring_record = ""
                drop_record = False
                drop_all = False

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
                                    try:
                                        tgn = self.node.tree.pnode( i )
                                    except Exception as e:
                                        tgn = 0

                                    try:
                                        phandle_tgt_name = tgn.label
                                    except:
                                        phandle_tgt_name = ""

                                    if not phandle_tgt_name:
                                        phandle_tgt_name = Lopper.phandle_safe_name( tgn.name )

                                    if self.__dbg__ > 1:
                                        print( "[DBG+]: [%s:%s] phandle replacement of: %s with %s" %
                                               ( self.node.name, self.name, hex(i), phandle_tgt_name))
                                except:
                                    # we need to drop the entire record from the output, the phandle wasn't found
                                    if self.__dbg__ > 1:
                                        print( "[DBG+]: [%s:%s] phandle: %s not found, dropping %s fields" %
                                               ( self.node.name, self.name, hex(i), phandle_field_count))

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
                                if self.binary:
                                    outstring_record += "{0:02X}];".format( i )
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
                                if self.binary:
                                    outstring_record += "{0:02X} ".format( i )
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
    """Class representing a device tree node

    This class implements:
       - a property iterator
       - dictionary access to properties
       - str(): string cast
       - equality check (==): for comparison
       - ref counting: set, get, clear
       - property add, modify, delete (via methods and '-', '+')
       - resolve(): to update/calculate properties against a FDT
       - sync(): sync modified node elements (and properties) to a FDT
       - deep node copy via LopperNode()

     Attributes:
       - number: the node number in the backing FDT
       - name: the node name in the backing FDT (this is not the node path)
       - parent: a link to the parent LopperNode object
       - tree: the tree which contains this node
       - depth: the nodes depth in the backing FDT (0 is root, 1 for first level children)
       - children: the list of child LopperNodes
       - phandle: the phandle in the backing FDT (optional)
       - type: the type of the node (based on 'compatible' property)
       - abs_path: the full/absolute path to this node in the backing FDT
       - _ref: the refcount for this node
       - __props__: ordered dictionary of LopperProp
       - __current_property__: place holder for property iterator
       - __dbg__: debug level for the node
       - __nstate__: the state of the node ("init", "resolved" )
       - __modified__: flag indicating if the node has been modified

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

        self.label = ""

        # 'type' is roughly equivalent to a compatible property in
        # the node if it exists.
        self.type = []

        self.abs_path = abspath

        self._ref = 0

        # ordered dict, since we want properties to come back out in
        # the order we put them in (when we iterate).
        self.__props__ = OrderedDict()
        self.__current_property__ = -1
        self.__props_pending_delete__ = OrderedDict()

        self.__dbg__ = debug

        # states could be enumerated types
        self.__nstate__ = "init"
        self.__modified__ = False

    # supports NodeA( NodeB ) to copy state
    def __call__( self, othernode=None ):
        """Callable implementation for the node class

        When used, this creates a deep copy of the current node, versus
        a reference. This allows a node to be cloned and used in a secondary
        tree, free from changes to the original node.

        Two modes are supported:
           A) <LopperNode Object>()
           B) <LopperNode Object>( <other node> )

        When no other node is passed (mode A) a copy of the existing node is
        made, including properties with the state is set to "init", this node
        should then be resolved to fill in missing information.

        When mode B is used, the current node is updated using copies of the
        values from the other node. This is used on a newly created node, to
        initalize it with values from an existing node.

        Args:
           othernode (LopperNode,optional): node to use for initalization values

        Returns:
           The copied node, or self (if updating).
        """
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
        """magic method to check the setting of a LopperNode attribute

        If the attribute being set is the debug level (__dbg__), this wrapper
        chains the setting to any LopperProps of the node.

        If the attribute is any other, we set the value and tag the node as
        modified, so it can be sync'd later.

        Args:
           name: attribute name
           value: attribute value

        Returns:
           Nothing
        """
        if name == "__dbg__":
            self.__dict__[name] = value
            for p in self.__props__.values():
                p.__dbg__ = value
        else:
            # we do it this way, otherwise the property "ref" breaks
            super().__setattr__(name, value)
            # we could restrict this to only some attributes in the future
            self.__dict__["__modified__"] = True

    def __getattribute__(self, name):
        """magic method around object attribute access

        This method first attempts to access the objects inherent attributes and
        returns the value if one exists matching the passed name.

        If one is not found, then the properties dictionary is checked, and that
        value returned.

        This allows access like:

            <LopperNode Object>.compatible

        To get the compatible LopperProperty value.

        In practice, this is only of limited use, since many property names are
        not valid python attribute names.

        Args:
           name: attribute name

        Returns:
           The attribute value, or AttributeError if it doesn't exist.
        """
        # this is needed to ensure that deep copy works, the exception is expected
        # by that process.
        if name == "__setstate__" or name == "__deepcopy__" or name == "__getstate__":
            raise AttributeError(name)

        try:
            return object.__getattribute__(self, name)
        except:
            try:
                return self.__props__[name].value
            except:
                raise AttributeError(name)


    def __int__(self):
        """magic method for int type conversion of LopperNode

        If a LopperNode is converted to an int, we use the node number

        Args:
            None

        Returns:
           int: the node number
        """
        # when casting to an int, return our node number
        return self.number

    def __str__(self):
        """magic method for string type conversion of LopperNode

        If a LopperNode is converted to a string, we use the absolute (full) path

        Args:
            None

        Returns:
           string: the abs path
        """
        if self.__dbg__ > 1:
            # this will be a raw object print, useful for debug
            return super().__str__()
        else:
            return self.abs_path

    def __iter__(self):
        """magic method to support iteration

        For iterating the properties of a LopperNode, we are the iterator.
        This is required by the iterator protocol.

        Args:
            None

        Returns:
           LopperNode object: self
        """
        return self

    def __eq__(self,other):
        """magic method for node comparision

        Support LopperNode comparisons: nodea == nodeb

        If the node numbers of two nodes match, we consider them equal.

        Args:
            other: LopperNode

        Returns:
           LopperNode object: self
        """
        if not isinstance( other, LopperNode ):
            return False
        else:
            if self.number == other.number:
                return True

            return False

    def __next__(self):
        """magic method for iteration on a node

        This routine uses the __current_property__ attribute to move
        through the properties of a node.

        If there are no properties, or we have iterated all properties,
        StopIteration is raised (as is required by the iterator protocol).

        Args:
            None

        Returns:
           LopperProp object or StopIteration exception
        """
        if not self.__props__:
            raise StopIteration

        # Ther are other ways to do this .., since we are making a list and just
        # indexing it. The __props__ is an ordered dictionary, so we could just
        # iterate the values() of it as well, but for now, we keep the control
        # of the indexing.
        self.__current_property__ = self.__current_property__ + 1
        prop_list = list(self.__props__)

        if self.__current_property__ >= len( prop_list ):
            self.__current_property__ = -1
            raise StopIteration
        else:
            return self.__props__[prop_list[self.__current_property__]]

    def __getitem__(self, key):
        """magic method for accessing LopperNode properties like a dictionary

        Allow accessing of properties as a dictionary:

            <Lopper Node Object>[<property name>]

        This abstracts the storage of the properties and allows direct access
        by name. Either the string name of the property may be used, or a
        LopperProp object itself.

        The standard KeyError exception is raised if the property is not valid for
        a node.

        For an exception free way of checking for a property, see the propval()
        method.

        Args:
            key: string or LopperProp

        Returns:
           LopperProp object or KeyError exception
        """
        # let the KeyError exception from an invalid key bubble back to the
        # user.
        if type(key) == str:
            access_key = key
        elif isinstance( key, LopperProp ):
            access_key = key.name
        else:
            raise KeyError(key)

        try:
            return self.__props__[access_key]
        except:
            # is it a regex ?
            # we tweak the key a bit, to make sure the regex is bounded.
            m = self.props( "^" + key + "$" )
            if m:
                # we get the first match, if you want multiple matches
                # call the "nodes()" method
                return m[0]

        raise KeyError(key)

    def __setitem__(self, key, val):
        """magic method for setting LopperNode properties like a dictionary

        Allow setting of properties as a dictionary:

            <Lopper Node Object>[<property name>] = <LopperProperty Object>

               or

            <Lopper Node Object>[<property name>] = [list of property values]


        This abstracts the storage of the properties and allows direct access
        by name.

        If a LopperProp is passed as 'val', it is directly assigned. If a list
        of values is passed, a LopperProp object is created, the values assigned
        and then placed in the property dictionary.

        Args:
            key: string
            val: LopperProp or string

        Returns:
           Nothing
        """
        if isinstance(val, LopperProp ):
            # we can try to assign
            self.__props__[key] = val
        else:
            np = LopperProp( key, -1, self, val, self.__dbg__ )
            self.__props__[key] = np
            self.__props__[key].resolve( self.tree.fdt )

            # throw an exception, since this is not a valid
            # thing to assign.
            # raise TypeError( "LopperProp was not passed as value" )

    @property
    def ref(self):
        """Node reference count getter

        Args:
           None

        Returns:
           int: The node refcount
        """
        return self._ref

    @ref.setter
    def ref(self,ref):
        """Node reference count setter

        Args:
           ref (int): > 0: the refcount increment, 0 to clear the refcount

        Returns:
           int: The node refcount
        """
        if ref > 0:
            self._ref += ref
        else:
            self._ref = 0

    def resolve_all_refs( self, fdt=None, property_mask=[] ):
        """Resolve and Return all references in a node

        Finds all the references starting from a given node. This includes:

           - The node itself
           - The parent nodes
           - Any phandle referenced nodes, and any nodes they reference, etc

        Args:
           fdt (FDT,optional): The flattended device tree to use for resolution
           property_mask (list of regex): Any properties to exclude from reference
                                          tracking, "*" to exclude all properties

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

    def subnodes( self ):
        """Return all the subnodes of this node

        Gathers and returns all the reachable subnodes of the current node
        (this includes nodes of children, etc).

        Args:
           None

        Returns:
           A list of child LopperNodes

        """
        all_kids = [ self ]
        for n in self.children:
            child_node = self.tree[n]
            all_kids = all_kids + child_node.subnodes()

        return all_kids

    def sync( self, fdt ):
        """sync a LopperNode to a backing FDT

        This routine looks for changes to the LopperNode and writes them back
        to the passed FDT.

        For the node itself, this is primarily a write back of a changed name.

        As part of the sync process, the node's number in the backing FDT is
        checked and the stored number changed to match as appropriate.

        We also check fo modified properties and sync them to the FDT.

        Removed properties are deleted from the FDT.

        And finally, the __modified__ flag is set to False.

        Args:
           fdt (FDT): device tree to sync against

        Returns:
           boolean: True if the node was sync'd, False otherwise

        """
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
                    print( "[DBG++]: node sync: fdt and node number differ, updating node '%s' from %s to %s" %
                           (self.abs_path,self.number,nn) )
                self.number = nn

            fdt_name = Lopper.node_getname( fdt, self.number )
            if fdt_name != self.name:
                try:
                    if self.__dbg__ > 2:
                        print( "[DBG++]:    node sync: syncing name from %s to %s" % (fdt_name,self.name))

                    Lopper.node_setname( fdt, self.number, self.name )
                except Exception as e:
                    print( "[WARNING]: could not set node name to %s (%s)" % (self.name,e))

            # sync any modified properties to a device tree
            for p in self.__props__.values():
                if self.__dbg__ > 2:
                    print( "[DBG++]:    node sync: syncing property: %s (state:%s)" % (p.name,p.__pstate__) )

                if p.__modified__:
                    if self.__dbg__ > 2:
                        print( "[DBG++]:    node sync: property %s is modified, writing back" % p.name )
                    p.sync( fdt )
                    retval = True
                if not p.__pstate__ == "syncd":
                    if self.__dbg__ > 2:
                        print( "[DBG++]:    node sync: property %s is not syncd, creating with value: %s" %
                               (p.name,p.value) )
                    p.sync( fdt )
                    retval = True

            for p in list(self.__props_pending_delete__.values()):
                if p.__pstate__ == "deleted":
                    if self.__dbg__ > 2:
                        print( "[DBG++]:    node sync: pending property %s is delete, writing back" % p.name )

                    Lopper.property_remove( fdt, self.name, p.name )
                    del self.__props_pending_delete__[p.name]
                    retval = True

            self.__modified__ = False

        return retval

    def delete( self, prop ):
        """delete a property from a node

        Queues a property for deletion on the next sync of a node.

        Takes a property name or LopperProp object as the parameter, and if
        it is a valid property, queues it for deletion.

        The node is marked as modified, so on the next sync, it will be remove.

        Args:
           prop (string or LopperProp): the property to delete

        Returns:
           Nothing. KeyError if property is not found

        """
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
            self.__props_pending_delete__[prop_to_delete.name] = prop_to_delete
            del self.__props__[prop_to_delete.name]
        except Exception as e:
            raise e

    def props( self, name ):
        """Access a property or list of properties described by a name/regex

        Looks through the properties of a node and returns any that match
        the name or regex passed to the routine.

        Args:
           name (string): property name or property regex

        Returns:
           list: list of LopperProp objects that match the name/regex, or [] if none match

        """
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
        """Access the value of a property

        This is a safe (no Exception) way to access the value of a named property,
        versus access it through the dictionary accessors.

        Args:
           name (string): property name

        Returns:
           list: list of values for the property, or [""] if the property name is invalid

        """
        try:
            prop = self.__props__[pname]
            return prop.value
        except:
            return [""]

    def reset(self):
        """reset the iterator of the node

        Sets the node iteration index to the starting value.

        Args:
           None

        Returns:
           None

        """
        self.__current_property__ = -1

    def __add__( self, other ):
        """magic method for adding a property to a node

        Supports adding a property to a node through "+"

            node + <LopperProp object>

        Args:
           other (LopperProp): property to add

        Returns:
           LopperNode: returns self, Exception on invalid input

        """
        if not isinstance( other, LopperProp ):
            raise Exception( "LopperProp was not passed" )

        self.add( other )

        return self

    def __sub__( self, other ):
        """magic method for removing a property from a node

        Supports removing a property from a node through "-"

            node - <LopperProp object>

        Args:
           other (LopperProp): property to remove

        Returns:
           LopperNode: returns self

        """
        if not isinstance( other, LopperProp ):
            return self

        self.delete( other )

        return self

    def __delitem__(self, key):
        """magic method for removing a property from a node dictionary style

        ** Not currently implemented **, overridden to prevent use

        Supports removing a property from a node through "del"

            del <node>[prop]

        Args:
           key (LopperProp): property/index to remove

        Returns:
           Nothing

        """
        pass

    def add( self, prop ):
        """Add a property to a node

        Supports adding a property to a node through

            node.add( prop )

        After adding the property, the node is tagged as modified to it
        can be sync'd in the future.

        Args:
           prop (LopperProp): property to add

        Returns:
           LopperNode: returns self, raises Exception on invalid parameter

        """
        if not isinstance( prop, LopperProp ):
            raise Exception( "LopperProp was not passed" )

        if self.__dbg__ > 2:
            print( "[DBG++]: node %s adding property: %s" % (self.abs_path,prop.name) )

        self.__props__[prop.name] = prop

        # indicates that we should be sync'd
        self.__modified__ = True

        return self

    def resolve( self, fdt ):
        """resolve (calculate) node details against a FDT

        Some attributes of a node are not known at initialization time, or may
        change due to tree operations.

        This method calculates those values using information in the node and in
        the passed FDT. If no FDT is passed only partial resolution is done.

        The only value that must be set in the node before resolve() is called
        is the node number. Which simply means it should have been added to the
        FDT first (see LopperTree.add()) and then resolved.

        Fields resolved (see class for descriptions)
           - name
           - abs_path
           - phandle
           - depth
           - children
           - type
           - __props__
           - __nstate__
           - __modified__

        Args:
           fdt (FDT): flattened device tree to sync to or None if no
                      tree is available

        Returns:
           Nothing

        """
        # resolve the rest of the references based on the passed device tree
        # self.number must be set before calling this routine.
        if self.__dbg__ > 2:
            print( "[DBG++]: node resolution start [fdt:%s][%s]: %s" % (fdt,self,self.abs_path))

        if fdt:
            if self.number >= 0:

                saved_props = self.__props__
                self.__props__ = OrderedDict()

                self.name = fdt.get_name(self.number)
                self.phandle = Lopper.node_getphandle( fdt, self.number )

                if self.number > 0:
                    self.abs_path = Lopper.node_abspath( fdt, self.number )
                else:
                    self.abs_path = "/"

                if self.__dbg__ > 2:
                    print( "[DBG++]:    node resolved: number: %s name: %s path: %s [fdt:%s]" %
                           ( self.number, self.name, self.abs_path, fdt ) )

                # parent and depth
                if self.number > 0:
                    depth = 1

                    parent_node_num = fdt.parent_offset( self.number, QUIET_NOTFOUND )
                    parent_path = Lopper.node_abspath( fdt, parent_node_num )
                    if self.tree:
                        self.parent = self.tree[parent_path]
                    depth = len(re.findall( '/', self.abs_path ))
                else:
                    depth = 0

                self.depth = depth

                self.children = []
                offset = fdt.first_subnode( self.number, QUIET_NOTFOUND )
                while offset > 0:
                    if self.abs_path != '/':
                        child_path2 = self.abs_path + '/' + Lopper.node_getname( fdt, offset )
                    else:
                        child_path2 = '/' + Lopper.node_getname( fdt, offset )

                    self.children.append(child_path2)
                    offset = fdt.next_subnode(offset, QUIET_NOTFOUND)

                # First pass: we look at the properties in the FDT. If they were in our
                # saved properties dictionary from above, we copy them back in. Re-resolving
                # and decoding unchanged properties is slow, so we avoid that step where
                # possible.
                self.type = []
                label_props = []
                poffset = fdt.first_property_offset(self.number, QUIET_NOTFOUND)
                while poffset > 0:
                    prop = fdt.get_property_by_offset(poffset)
                    prop_val = Lopper.property_get( fdt, self.number, prop.name, LopperFmt.COMPOUND )
                    dtype = Lopper.property_type_guess( prop )

                    # special handling for 'compatible', we bubble it up as the node "type"
                    if prop.name == "compatible":
                        self.type += prop_val

                    # create property objects, and resolve them
                    try:
                        existing_prop = saved_props[prop.name]
                    except Exception as e:
                        existing_prop = None

                    if existing_prop:
                        # same prop name, same parent node .. it is the same. If this
                        # somehow changes, we'll need to call resolve on this as well.
                        self.__props__[prop.name] = existing_prop
                    else:
                        self.__props__[prop.name] = LopperProp( prop.name, poffset, self, prop_val, self.__dbg__ )
                        if dtype == LopperFmt.UINT8:
                            self.__props__[prop.name].binary = True

                        self.__props__[prop.name].resolve( fdt )
                        self.__props__[prop.name].__modified__ = False

                        # if our node has a property of type label, we bubble it up to the node
                        # for future use when replacing phandles, etc.
                        if self.__props__[prop.name].type == "label":
                            self.label = self.__props__[prop.name].value[0]
                            label_props.append( self.__props__[prop.name] )

                    poffset = fdt.next_property_offset(poffset, QUIET_NOTFOUND)

                # second pass: re-resolve properties if we found some that had labels
                if label_props:
                    # we had labels, some output strings in the properities may need to be
                    # update to reflect the new targets
                    for p in self.__props__:
                        self.__props__[p].resolve( fdt )
                        self.__props__[p].__modified__ = False

                # 3rd pass: did we have any added, but not syn'd properites. They need
                #           to be brought back into the main property dictionary.
                for p in saved_props:
                    if saved_props[p].__pstate__ == "init":
                        self.__props__[p] = saved_props[p]

            if not self.type:
                self.type = [ "" ]

            self.__nstate__ = "resolved"
            self.__modified__ = False

            if self.__dbg__ > 2:
                print( "[DGB++]: node resolution end: %s" % self)

class LopperTree:
    """Class for walking a device tree, and providing callbacks at defined points

    This class implements:
       - a node iterator
       - dictionary access to nodes by path or node number
       - a tree walker / exec() that has callbacks for: tree start, node start,
                                                        property start, node end, tree end
       - debug level
       - tree wide reference tracking control: clear, get
       - sync(): to sync changes to a backing FDT
       - node manipulatins: add, delete, filter, subnodes
       - phandle access to nodes
       - node search by regex

    A LopperTree object is instantiated for an easier/structure interface to a backing
    device tree store (currently only a flattended device tree from libfdt). It provides
    the ability to add/delete/manipulate nodes on a tree wide basis and can sync those
    changes to the backing store.

    When initialized the tree is created as a snapshot or reference to a FDT. If the
    changes made by the object are to be indepdendent, then a snapshot is used. If the
    original FDT is to be updated, then a reference should be used. reference is the
    default mode.

    During the walking of a tree via exec(), callbacks are made (if set) at defined
    points in the process. This makes it easy to implement structured output of a
    tree, without the need to have deep encoding/understanding of the underlying
    structure.

    Callbacks are functions of the form: <fn>( <node or property>, FDT )

    Attributes:
       - __nodes__: The nodes of the tree, ordered by absolute path indexing
       - __nnodes__: The nodes of the tree, ordered by node number
       - __pnodes__: The nodes of the tree, ordered by phandle
       - __dbg__: treewide debug level
       - __must_sync__: flag, true when the tree must be syncd to the FDT
       - __current_node__: The current node in an iteration
       - __start_node__: The starting node for an iteration
       - __new_iteration__: Flag set to start a new iteration
       - __node_iter__: The current iterator
       - start_tree_cb, start_node_cb, end_node_cb, property_cb, end_tree_cb: callbacks
       - depth_first: not currently implemented

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
        # nodes, indexed by label
        self.__lnodes__ = OrderedDict()
        # nodes. selected. default/fallback for some operations
        self.__selected__ = []

        # callbacks
        # these can even be lambdas. i.e lambda n, fdt: print( "start the tree!: %s" % n )
        # TODO: the callbacks could return False if we want to abort the tree walk
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
        self.__new_iteration__ = True
        self.__node_iter__ = None

        # type
        self.depth_first = depth_first

        # resolve against the fdt
        self.resolve()

    def __iter__(self):
        """magic method to support iteration

        For iterating the nodes of a LopperTree, we are the iterator.
        This is required by the iterator protocol.

        Args:
            None

        Returns:
           LopperTree object: self
        """
        return self

    def __next__(self):
        """magic method for iteration on a tree

        This routine uses the next() method to move through the nodes of a
        tree.

        If there are no nodes, or we have iterated all nodes, StopIteration is
        raised (as is required by the iterator protocol).

        Args:
            None

        Returns:
           LopperNode object or StopIteration exception

        """
        n = self.next()
        if n.number == -1:
            raise StopIteration

        return n

    def __setattr__(self, name, value):
        """magic method to check the setting of a LopperTree attribute

        If the attribute being set is __current_node__ or __start_node__
        then the new iteration flag is set to trigger the start of a new
        iteration. When setting these attributes, value can either be a
        node number or a node name. When it is a name, it is internally
        converted to a number on behalf of the caller.

        If the attribute is __dbg__, then the debug setting is chained
        to contained nodes.

        Args:
           name: attribute name
           value: attribute value

        Returns:
           Nothing
        """
        # TODO: we could detect if fdt is assigned/writen and re-run a resolve()
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

    # tree
    def __getattribute__(self, name):
        """magic method around object attribute access

        This method first attempts to access the objects inherent attributes and
        returns the value if one exists matching the passed name.

        If one is not found, then the node dictionary is checked, and that
        value returned.

        This allows access like:

            <LopperTree Object>.path_to_node

        To get the LopperNode at that path

        In practice, this is only of limited use, since many node paths are
        not valid python attribute names.

        Args:
           name: attribute name

        Returns:
           The attribute value, or AttributeError if it doesn't exist.
        """
        # try first as an attribute of the object, then, as an index into the
        # nodes by name (but since most names are not valid python member names,
        # it isn't all that useful. more useful are the __*item*__ routines.
        try:
            return object.__getattribute__(self, name)
        except:
            try:
                # a common mistake is to leave a trailing / on a node
                # path. Drop it to make life easier.
                access_name = name.rstrip('/')
                return self.__nodes__[access_name]
            except:
                raise AttributeError(name)

    def __getitem__(self, key):
        """magic method for accessing LopperTree nodes like a dictionary

        Allow accessing of nodes as a dictionary:

            <Lopper Tree Object>[<node path>]

        This abstracts the storage of nodesand allows direct access by name,
        by number or by node regex.

        Either the string name of the node path, the node number, a LopperNode
        object, or a node path with a regex can be used to access a node.

        Note that on a regex search, the first match is returned. For multiple
        node returns, use the nodes() method.

        The standard KeyError exception is raised if the node is not valid for
        a tree

        Args:
            key: string, int or LopperNode

        Returns:
           LopperNode object or KeyError exception

        """
        # let the KeyError exception from an invalid key bubble back to the user.
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
            # is it a label :
            try:
                return self.__lnodes__[access_name]
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
        """magic method for setting LopperTree nodes like a dictionary

        Allow setting of properties as a dictionary:

            <Lopper Tree Object>[<node name>] = <LopperNode Object>

               or

            <Lopper Tree Object>[<node number>] =  <LopperNode Object>

        During assignment of the node, access is created by name, number and
        phandle as appropriate

        Args:
            key: string or int
            val: LopperNode

        Returns:
;           Nothing, raises TypeError on invalid parameters
        """

        if isinstance(val, LopperNode ):
            # we can try to assign
            if type(key) == int:
                self.__nnodes__[key] = val
                self.__nodes__[val.abspath] = val
                if val.phandle != 0:
                    self.__pnodes__[val.phandle] = val
                if val.label:
                    self.__lnodes__[val.label] = val
            else:
                self.__nodes__[key] = val
                self.__nnodes__[val.number] = val
                if val.phandle != 0:
                    self.__pnodes__[val.phandle] = val
                if val.label:
                    self.__lnodes__[val.label] = val
        else:
            # thrown an exception, since this is not a valid
            # thing to assign.
            raise TypeError( "LopperNode was not passed as value" )

    def __delitem__(self, key):
        """magic method for removing a property from a tree dictionary style

        ** Not currently implemented **, overridden to prevent use

        Supports removing a node from a tree through "del"

            del <tree>[node]

        Args:
           key (LopperNode): node/index to remove

        Returns:
           Nothing

        """
        # not currently supported
        pass

    def ref_all( self, starting_node, parent_nodes=False ):
        """Increment the refcount for a node and its subnodes (and optionally parents)

        Creates a reference to a node and its subnodes.

        If parent_nodes is set to True, parent nodes will be also referenced.

        Args:
           starting_node (LopperNode): node to reference
           parent_nodes (boolean,optional): flag to indicate if parent nodes
                                            should be referenced

        Returns:
           Nothing

        """
        if parent_nodes:
            refd_nodes = starting_node.resolve_all_refs( self.fdt, [".*"] )

        subnodes_to_ref = starting_node.subnodes()

        nodes_to_ref = []
        for n in refd_nodes + subnodes_to_ref:
            if n not in nodes_to_ref:
                nodes_to_ref.append(n)

        for n in nodes_to_ref:
            n.ref = 1


    def ref( self, value, node_regex = None ):
        """Tree wide setting of a refcount

        Sets a refcount for all nodes in the tree, or a regex contained set
        of nodes.

        Calling this routine with zero, is a treewide reset of all refcounts.

        If a regex is passed, only matching nodes will be set/cleared.

        Args:
           value (int): refcount value by which to increment
           node_regex (string,optional): node path regex to restrict scope of
                                         refcount operations

        Returns:
           Nothing

        """
        if node_regex:
            nodes = self.nodes( node_regex )
        else:
            nodes = self.__nodes__.values()

        for n in nodes:
            n.ref = value

    def refd( self, node_regex="" ):
        """Get a list of referenced nodes

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

    def sync( self, fdt = None ):
        """Sync a tree to a backing FDT

        This routine walks the FDT, and sync's changes from any LopperTree nodes
        into the backing store.

        Once complete, all nodes are resolved() to ensure their attributes reflect
        the FDT status.

        Args:
           fdt (FDT,optional): the flattended device tree to sync to. If it isn't
                               passed, the stored FDT is use for sync.

        Returns:
           Nothing

        """
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

        # technique a), left for reference
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
        """magic method for removing a node from a tree

        Supports removing a node from a tree through "-"

            tree - <LopperNode object>

        Args:
           other (LopperNode): Node to remove

        Returns:
           LopperTree: returns self

        """
        if not isinstance( other, LopperNode ):
            return self

        self.delete( other )

        return self

    def delete( self, node ):
        """delete a node from a tree

        If a node is resolved and syncd to the FDT, this routine deletes it
        from the FDT and the LopperTree structure.

        Args:
           node (int or LopperNode): the node to delete

        Returns:
           Boolean: True if deleted, False otherwise. KeyError if node is not found

        """
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

    def __add__( self, other ):
        """magic method for adding a node to a tree

        Supports adding a node to a tree through "+"

            tree + <LopperNode object>

        Args:
           other (LopperNode): node to add

        Returns:
           LopperTree: returns self, Exception on invalid input

        """
        if not isinstance( other, LopperNode ):
            raise Excepton( "LopperNode was not passed" )

        self.add( other )

        return self

    def add( self, node ):
        """Add a node to a tree

        Supports adding a node to a tree through:

            tree.add( <node> )

        The node is added to the FDT, resolved and syncd. It is then available
        for use in any tree operations.

        Args:
           node (LopperNode): node to add

        Returns:
           LopperTree: returns self, raises Exception on invalid parameter

        """
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
                    print( "[DBG++]      property: %s %s (state:%s)" % (p.name,p.value,p.__pstate__) )

        # we can probably drop this by making the individual node sync's smarter and
        # more efficient when something doesn't need to be written
        self.__must_sync__ = True
        self.sync()

        return self

    def subnodes( self, start_node, node_regex = None ):
        """return the subnodes of a node

        Returns a list of all subnodes from a given starting node.

        If a node regex is passed, those nodes that do not match the
        regex are removed from the returned value.

        Args:
           start_node (LopperNode): the starting node
           node_regex (string,optional): node mask

        Returns:
           list: returns a list of all subnodes (or matching subnodes)

        """
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


    def nodes( self, nodename ):
        """Get nodes that match a given name or regex

        Looks for a node at a name/path, or nodes that match a regex.

        Args:
           nodename (string): node name or regex

        Returns:
           list: a list all nodes that match the name or regex

        """
        matches = []
        try:
            matches = [self.__nodes__[nodename]]
        except:
            # maybe it was a regex ?
            for n in self.__nodes__.keys():
                if re.search( nodename, n ):
                    matches.append( self.__nodes__[n] )

        return matches

    def pnode( self, phandle ):
        """Find a node in a tree by phandle

        Safely (no exception raised) returns the node that can be found
        at a given phandle value.

        Args:
           phandle (int): node phandle to check

        Returns:
           LopperNode: the matching node if found, None otherwise

        """
        try:
            return self.__pnodes__[phandle]
        except:
            return None

    def exec_cmd( self, node, cmd, env = None ):
        """Execute a (limited) code block against a node

        Execute a python clode block with the 'node' context set to the
        value passed to this routine.

        The "cmd" python code, runs in a constructed/safe environment to ensure
        that the code won't cause harmful sideffects to the execution
        environment.

        The following functions and variables are currently available in the
        safe_dict:

            len
            print
            verbose

        When executing in the code context, the following variables are
        available to the python code block.

            tree : the LopperTree object containing the node
            node : the LopperNode being processed
            node_name : the name of the node (as defined by the dts/dtb)
            node_number : the number of the node being processed

        The return value of the block is sent to the caller, so it can act
        accordingly.

        Args:
            node (LopperNode or string): starting node
            cmd (string): block of python code to execute
            env (dictionary,optional): values to make available as
                                       variables to the code block

        Returns:
            Return value from the execution of the code block

        """

        n = node

        if node == None:
            return False

        if type(node) == str:
            n = self[node]

        # make a list of seed safe functions
        safe_list = []

        # this should work, but isn't resolving the local vars, so we have to add them again in the
        # loop below.
        # references: https://stackoverflow.com/questions/701802/how-do-i-execute-a-string-containing-python-code-in-python
        #             http://code.activestate.com/recipes/52217-replace-embedded-python-code-in-a-string-with-the-/
        safe_dict = dict([ (k, locals().get(k, None)) for k in safe_list ])
        safe_dict['len'] = len
        safe_dict['print'] = print
        safe_dict['prop_get'] = Lopper.property_get
        safe_dict['getphandle'] = Lopper.node_getphandle
        safe_dict['fdt'] = self.fdt
        safe_dict['verbose'] = self.__dbg__
        safe_dict['tree'] = self

        if self.__dbg__ > 1:
            print( "[INFO]: filter: base safe dict: %s" % safe_dict )
            print( "[INFO]: filter: node: %s" % node )

        # build up the device tree node path
        # node_name = node_prefix + n
        node_name = n.abs_path
        node_number = n.number
        prop_list = n.__props__

        # add any needed builtins back in
        safe_dict['node'] = n
        safe_dict['node_number'] = node_number
        safe_dict['node_name' ] = node_name

        if env:
            for e in env:
                safe_dict[e] = env[e]

        # search and replace any template options in the cmd. yes, this is
        # only a proof of concept, you'd never do this like this in the end.
        tc = cmd

        # we wrap the test command to control the ins and outs
        __nret = False
        # indent everything, its going in a function
        tc_indented = textwrap.indent( tc, '    ' )
        # define the function, add the body, call the function and grab the return value
        tc_full_block = "def __node_test_block():" + tc_indented + "\n__nret = __node_test_block()"

        if self.__dbg__ > 2:
           print( "[DBG+]: node exec cmd:\n%s" % tc_full_block )

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
            print("[WARNING]: Exception (%s) raised by code block: %s" % (e,tc_full_block))
            os._exit(1)

        if self.__dbg__ > 2:
            print( "[DBG+] return code was: %s" % m['__nret'] )

        if m['__nret']:
            return m['__nret']
        else:
            return False


    def filter( self, node_prefix, action, test_cmd, fdt=None, verbose=0 ):
        """Filter tree nodes and perform an action

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
        environment. See the exec_cmd method for details of the command
        execution.

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
        if fdt == None:
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

        if verbose > 1:
            print( "[INFO]: filter: node list: ", end=" " )
            for nn in node_list:
                print( "%s" % nn.abs_path, end="  " )
            print( "" )

        for n in node_list:
            if verbose > 2:
               print( "[DBG+]: filter node cmd:\n%s" % test_cmd )

            test_cmd_result = self.exec_cmd( n, test_cmd )

            if verbose > 2:
                print( "[DBG+] return code was: %s" % test_cmd_result )

            # did the block set the return variable to True ?
            if test_cmd_result:
                if action == LopperAction.DELETE:
                    if verbose:
                        print( "[INFO]: deleting node %s" % n.abs_path )
                    self.delete( n )
            else:
                pass

        return test_cmd_result

    def exec(self):
        """Start a tree walk execution, with callbacks executed as required

        Starts walking the tree, beginning at the preamble, and then through a depth
        first walking of the nodes.

        If the tree has registered callbacks, they are executed before the walk
        starts, at the start/end of each node, at each property and at the end of
        the tree.

        See the class description for details on the callbacks

        Args:
           None

        Returns:
           Nothing

        """
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
        """reset a tree

        Resets certain parts of the tree to their initial values. Specifically
        it resets the tree for a new iteration.

        Args:
           None

        Returns:
           Nothing

        """
        self.__current_node__ = 0
        self.__new_iteration__ = True

    def resolve(self):
        """resolve a tree

        Resolves the details around the nodes of a tree, and completes values
        that are not possible at initialization time.

        In particular, it updates the path, node and phandle ordered dictionaries
        to reflect the backing FDT. This is often done after a node is added to
        ensure that iterations will see the new node in tree order, versus added
        order.

        Args:
           None

        Returns:
           Nothing

        """
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
            # nodes, indexed by label
            self.__lnodes__ = OrderedDict()

            if self.__dbg__ > 2:
                print( "[DGB+]: tree resolution start: %s" % self )

            nn = 0
            depth = 0
            while depth >= 0:
                abs_path = Lopper.node_abspath( self.fdt, nn )
                try:
                    # we try and re-use the node if possible, since that keeps
                    # old references valid for adding more properties, etc
                    node = nodes_saved[abs_path]
                except:
                    # node didn't exist before, create it as something new
                    node = LopperNode( nn, "", self )

                node.__dbg__ = self.__dbg__

                # resolve the details against the fdt
                node.resolve( self.fdt )

                try:
                    node_check = self.__nodes__[node.abs_path]
                    if node_check:
                        print( "[ERROR]: tree inconsistency found, two nodes with the same path" )
                        # we need to exit the thread/backgound call AND the entire application, so
                        # hit is with a hammer.
                        os._exit(1)
                except:
                    pass
                # we want to find these by name AND number (but note, number can
                # change after some tree ops, so make sure to check the state of
                # a tree/node before using the number
                self.__nodes__[node.abs_path] = node
                self.__nnodes__[node.number] = node
                if node.phandle > 0:
                    self.__pnodes__[node.phandle] = node
                if node.label:
                    self.__lnodes__[node.label] = node

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
        """Returns the next node in a tree iteration

        This method maintains the iteration state of a tree and returns
        the next LopperNode in the iteration.

        Three types of iterations are common:

          - full iteration: a depth first walk of every node in the tree
          - subnode iteration: a depth first walk of all nodes under a given
                               starting point
          - startnode iteration: A depth first walk starting at a given node
                                 and continuing to the end of the tree

        Args:
           None

        Returns:
           LopperNode

        """
        node = None

        if self.__new_iteration__:
            self.__new_iteration__ = False

            # by default, we'll just iterate the nodes as they went
            # into our dictionary
            self.__node_iter__ = iter( self.__nodes__.values() )

            if self.__current_node__ == 0 and self.__start_node__ == 0:
                # just get the first node out of the default iterator
                node = next(self.__node_iter__)
            elif self.__start_node__ != 0:
                # this is a starting node, so we fast forward and then use
                # the default iterator
                node = next(self.__node_iter__)
                while node.number != self.__start_node__:
                    node = next(self.__node_iter__)
            else:
                # non-zero current_node, that means we'll do a custom iteration
                # of only the nodes that are underneath of the set current_node
                child_nodes = self.subnodes( self.__nnodes__[self.__current_node__] )
                self.__node_iter__ = iter( child_nodes )
                node = next(self.__node_iter__)
        else:
            if self.depth_first:
                try:
                    node = next(self.__node_iter__)
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
    """SubClass for enhanced printing a lopper tree

    This class implements:
       - routines to print the start of a tree, nodes, properties and end of a tree
         to DTS format.

    Enhanced printing is done by implementing callbacks that the base LopperTree
    class will call during a tree walk.

    Attributes:
       - output: output file name, if not passed stdout is used

    """
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

    def reset(self, output_file=sys.stdout ):
        """reset the output of a printer

        closes the existing output_file (if not stdout) and opens a new
        output_file (if not stdout)

        Args:
            output_file (string,optional): name of file to open for output, default is stdout

        Returns:
            Nothing
        """
        super().reset()

        if type(self.output) != str:
            output_name = self.output.name
        else:
            output_name = ""

        if self.output != sys.stdout and output_name != '<stdout>':
            self.output.close()

        if type(output_file) != str:
            output_name = output_file.name
        else:
            output_name = output_file

        if output_file != sys.stdout and output_name != '<stdout>':
            try:
                self.output = open( output_file, "w")
            except Exception as e:
                print( "[WARNING]: could not open %s as output: %s" % (output_file,e))

    def start(self, n, fdt ):
        """LopperTreePrinter start

        Prints the start / opening of a tree and handles the preamble.

        Args:
            n (LopperNode): the opening node of the tree
            fdt (FDT): the FDT backing the tree

        Returns:
            Nothing
        """
        # peek ahead to handle the preamble
        for p in n:
            if p.type == "preamble":
                print( "%s" % p, file=self.output )

        print( "/dts-v1/;\n\n/ {", file=self.output )

    def start_node(self, n, fdt ):
        """LopperTreePrinter node start

        Prints the start / opening of a node

        Args:
            n (LopperNode): the node being opened
            fdt (FDT): the FDT backing the tree

        Returns:
            Nothing
        """
        indent = n.depth * 8
        nodename = n.name
        if n.number != 0:
            if n.phandle != 0:
                plabel = ""
                try:
                    if n['lopper-label.*']:
                        plabel = n['lopper-label.*'].value[0]
                except:
                    pass

                if plabel:
                    outstring = plabel + ": " + nodename + " {"
                else:
                    outstring = Lopper.phandle_safe_name( nodename ) + ": " + nodename + " {"
            else:
                outstring = nodename + " {"

            print(outstring.rjust(len(outstring)+indent," " ), file=self.output )

    def end_node(self, n, fdt):
        """LopperTreePrinter node end

        Prints the end / closing of a node

        Args:
            n (LopperNode): the node being closed
            fdt (FDT): the FDT backing the tree

        Returns:
            Nothing
        """
        indent = n.depth * 8
        outstring = "};\n"
        print(outstring.rjust(len(outstring)+indent," " ), file=self.output)

    def start_property(self, p, fdt):
        """LopperTreePrinter property print

        Prints a property

        Args:
            p (LopperProperty): the property to print
            fdt (FDT): the FDT backing the tree

        Returns:
            Nothing
        """
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
            dstring = dstring.rjust(len(dstring) + indent + 1, " " )
            outstring = re.sub( '\n\s*', '\n' + dstring, outstring, 0, re.MULTILINE | re.DOTALL)

        if p.type == "preamble":
            # start tree peeked at this, so we do nothing
            outstring = ""

        if outstring:
            print(outstring.rjust(len(outstring)+indent," " ), file=self.output)

    def end(self, n,fdt):
        """LopperTreePrinter tree end

        Ends the walking of a tree

        Args:
            n (LopperNode): -1
            fdt (FDT): the FDT backing the tree

        Returns:
            Nothing
        """

        if self.output != sys.stdout:
            self.output.close()


