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
from io import StringIO
import copy
import json

import lopper.base
from lopper.fmt import LopperFmt

from lopper.log import _warning, _info, _error, _debug
import logging

lopper.log._init( __name__ )
lopper.log._init( "tree.py" )

# must be set to the Lopper class to call
global Lopper

# utility function to return true or false if a number
# is 32 bit, or not.
def check_32_bit(n):
    return (n & 0xFFFFFFFF00000000) == 0

def chunks(l, n):
    # For item i in a range that is a length of l,
    for i in range(0, len(l), n):
        # Create an index range for l of n items:
        yield l[i:i+n]

def chunks_variable(lst, chunk_sizes):
    """
    Splits a Python list into variable sized records of different sizes

    Args:
        lst: the list to be chunked
        chunk_sizes: a list of integers representing the sizes of the chunks

    Yields:
        A list of variable length, where each list is a chunk
    """
    if sum(chunk_sizes) != len(lst):
        raise ValueError( f"The sum of chunk sizes: {sum(chunk_sizes)} ({chunk_sizes}) must be "
                          f"equal to the length of the list ({len(lst)})" )

    start = 0

    for size in chunk_sizes:
        end = start + size
        chunk = lst[start:end]
        yield chunk
        start = end

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
    def __init__(self, name, number = -1, node = None, value = None, debug_lvl = 0 ):
        self.__modified__ = True
        self.__pstate__ = "init"
        self.__dbg__ = debug_lvl

        self.name = name
        self.node = node
        self.number = number

        self.string_val = "**unresolved**"
        self.pclass = ""
        self.ptype = ""
        self.binary = False

        self.phandle_resolution = True

        self.abs_path = ""

        if value == None:
            self.value = []
        else:
            # we want to avoid the overriden __setattr__ below
            self.__dict__["value"] = value
            # set a default ptype, since before the property is
            # resolved, it may be used in some sort of test that
            # needs a type
            self.ptype = self.property_type_guess()


    def __deepcopy__(self, memodict={}):
        """ Create a deep copy of a property

        Properties have links to nodes, so we need to ensure that they are
        cleared as part of a deep copy.

        """
        if self.__dbg__ > 1:
            lopper.log._debug( f"property '{self.name}' deepcopy start: {[self]}" )
            lopper.log._debug( f"         value type: {type(self.value)} value: {self.value}" )

        new_instance = LopperProp(self.name)

        # if we blindly want everything, we'd do this update. But it
        # is easier to pick out the properties that we do want, versus
        # copying and undoing.
        #      new_instance.__dict__.update(self.__dict__)
        new_instance.__dbg__ = copy.deepcopy( self.number, memodict )
        # we use __dict__ for the value assignemnt to avoid any object level
        # wrapping of the assignement (i.e. making a list, etc)
        new_instance.__dict__["value"] = copy.deepcopy( self.value, memodict )

        try:
            new_instance.__dict__["struct_value"] = copy.deepcopy( self.struct_value, memodict )
        except:
            pass
        try:
            new_instance.__dict__["list_value"] = copy.deepcopy( self.list_value, memodict )
        except:
            pass

        new_instance.__pstate__ = "init"
        new_instance.node = None

        new_instance.pclass = self.pclass
        new_instance.ptype = self.ptype
        new_instance.binary = self.binary

        if self.__dbg__ > 1:
            lopper.log._debug( f"property deep copy done: {[self]} ({type(new_instance.value)})({new_instance.value})" )

        return new_instance

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

    def __getitem__(self, key):
        """Access a property's value by key

        Allows the property's value to be access by 'index', since
        properties are normally lists of value.

        If the property is a special type, i.e. a json pclass, then
        the value is expanded and indexed. Otherwise, the value list
        is simply indexed

        Non-integer keys return None. Unless "value" is used as a key
        and you get the raw/entire value list.

        Normal list exceptions are raised if you index outside of the
        range of the value

        Args:
          Key (int or "value")

        Returns:
          The item at the specified index

        """
        if type(key) == int:
            if self.pclass == 'json':
                loaded_j = json.loads( self.value )
                return loaded_j[key]
            else:
                if type(self.value) == list:
                    return self.value[key]
                else:
                    return self.value
        else:
            if key == "value":
                return self.value

            return None

    def __len__(self):
        """Get the length of a property

        When using the __getitem__ access to property values, knowing
        the length is important.

        if the property is a special class (i.e. json), the lenght of
        the loaded list is returned.

        if the values are a list, the lenght of that list is returned

        if the value is a single item, 0 is returned

        Args:
           None

        Returns:
           Int: The lenght of the list

        """
        if self.pclass == 'json':
            loaded_j = json.loads( self.value )
            return len(loaded_j)
        else:
            if type(self.value) == list:
                return len(self.value)
            else:
                return 1

    def __iter__(self):
        """magic method to support iteration

        This allows the values of a property to be iterated, which
        isn't very useful. But it is useful that functions like dict()
        take this iterator and create a usable dictionary for the
        caller.

        If the property is special, like json, then you get an
        keyed return of 'value' and the loaded value

        if the property is standard, you get a keyed return of
        'value' and the value list

        Args:
            None

        Returns:
           iterator for use in dict()
        """
        if self.pclass == 'json':
            loaded_j = json.loads( self.value )
            for chunk in loaded_j:
                yield chunk
            #yield 'value', loaded_j
        else:
            if type( self.value ) == list:
                yield 'value', self.value
            else:
                yield 'value', [self.value]

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

            try:
                if Counter(old_value) != Counter(self.__dict__[name]):
                    self.__modified__ = True
            except:
                self.__modified__ = True

            self.resolve()
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
            lopper.log._debug( f"property compare ({self}) vs ({other_prop})" )

        ret_val = False
        invert_check  = ""
        if len(self.value) == 1:
            # single comparison value
            lop_compare_value = self.value[0]

            if len( other_prop.value ) == 1:
                # check if this is actually a phandle property
                # TODO: this may need to be converted to the newer phandle_map(), but
                #       we aren't checking ALL phandles currently, so this is good
                #       enough
                idx, pfields = self.phandle_params()
                # idx2, pfields2 = other_prop.phandle_params()
                if pfields > 0:
                    if self.ptype == LopperFmt.STRING:
                        # check for "&" to designate that it is a phandle, if it isn't
                        # there, throw an error. If it is there, remove it, since we
                        # don't use it for the lookup.
                        if re.search( r'&', lop_compare_value ):
                            lop_compare_value = re.sub( r'&', '', lop_compare_value )

                            # this is a phandle, but is currently a string, we need to
                            # resolve the value.
                            nodes = other_prop.node.tree.nodes( lop_compare_value )
                            if not nodes:
                                nodes = other_prop.node.tree.lnodes( lop_compare_value )

                            if nodes:
                                phandle = nodes[0].phandle
                            else:
                                phandle = 0

                            # update our value so the rest of the code can stay the same
                            self.ptype = LopperFmt.UINT32
                            self.value[0] = phandle

                        else:
                            pass
                            #print( "[ERROR]: phandle is being compared, and target node does not start with & (%s)" % lop_compare_value )
                            #sys.exit(1)

                # single -> single: single must be in or equal the other
                lop_compare_value = self.value[0]
                tgt_node_compare_value = other_prop.value[0]

                if other_prop.ptype == LopperFmt.STRING or \
                             self.ptype == LopperFmt.STRING: # type(lop_compare_value) == str:
                    constructed_condition = f"{invert_check} re.search(r\"{lop_compare_value}\",'{tgt_node_compare_value}')"
                elif other_prop.ptype == LopperFmt.UINT32: # type(lop_compare_value) == int:
                    constructed_condition = f"{invert_check} {lop_compare_value} == {tgt_node_compare_value}"
                else:
                    lopper.log._warning( f"comparison property [{other_prop.name}] {other_prop.node.abs_path}"
                                         f" has an invalid type: {other_prop.ptype}, skipping test" )
                    return False

                if self.__dbg__ > 2:
                    lopper.log._debug( f"    single:single. Condition: {constructed_condition}" )

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
                    if self.ptype == LopperFmt.STRING: # type(lop_compare_value) == str:
                        constructed_condition = f"{invert_check} re.search(r\"{lop_compare_value}\",\"{tgt_node_compare_value}\")"

                    elif self.ptype == LopperFmt.UINT32: # type(lop_compare_value) == int:
                        constructed_condition = f"{invert_check} {lop_compare_value} == {tgt_node_compare_value}"

                    if self.__dbg__ > 2:
                        lopper.log._debug( f"    single:list. Condition: {constructed_condition}" )

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
                    if self.ptype == LopperFmt.STRING: # type(lop_compare_value) == str:
                        constructed_condition = f"{invert_check} re.search(r\"{lop_compare_value}\",\"{tgt_node_compare_value}\")"

                    elif self.ptype == LopperFmt.UINT32: # type(lop_compare_value) == int:
                        constructed_condition = f"{invert_check} {lop_compare_value} == {tgt_node_compare_value}"

                    if self.__dbg__ > 2:
                        lopper.log._debug( f"    list:single. Condition: {constructed_condition}" )

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
                    lopper.log._debug( f"    list:list. Condition: {lop_compare_value} == {tgt_node_compare_value}" )

                if lop_compare_value == tgt_node_compare_value:
                    ret_val = True
                else:
                    ret_val = False

        if self.__dbg__ > 2:
            lopper.log._debug( f"        prop compare: {ret_val}" )

        return ret_val

    def phandle_map( self, tag_invalid = True ):
        """Determines the phandle elements/params of a property

        Takes a property name and returns a list of lists, where phandles are
        indicated by the dereferenced node, and non phandle fields are "0"

        Args:
            tag_invalid (bool): default True. Whether or not invalid phandles should be indicated with "invald"

        Returns:
            A list / map of values. Where 0 in the list means no phandle, and
            a LopperNode in the list means phandle. If there are no phandles, an empty list
            is returned.
        """
        phandle_map = []
        phandle_sub_list = []
        phandle_props = Lopper.phandle_possible_properties()

        field_offset_pattern = r'([+-]?)(\d+):(.*)'
        field_offset_regex = re.compile(field_offset_pattern)

        if not self.name in phandle_props.keys():
            return phandle_map

        # if this is a json type, we can't possibly find anything useful
        # by iterating, so just return the empty map
        if self.pclass == "json":
            return phandle_map

        debug = False
        # this is too verbose, kept for reference
        # debug = self.__dbg__
        dname = self.name

        if self.node:
            try:
                compat = self.node["compatible"]
                is_lop = False
                for c in compat.value:
                    if re.search( r"system-device-tree-v1,lop", c ):
                        is_lop = True

                # lops can't resolve, so just return early
                if is_lop:
                    return []
            except:
                pass

        # We are iterating two things:
        #   - the values of the property
        #   - the fields of the property description

        # In the fields of the property, we are looking for values that
        # might be phandles. We then look them up, and store them in
        # a return list that has nodes and 0s.

        # For the property description, we walk it field by field, looking
        # up values to figure out where the phandles might be, and the
        # size of each sublist in the return (these are records in the
        # property)

        property_description = phandle_props[self.name]

        # index 0 is always the description, other elements are flags, etc.
        property_fields = property_description[0].split()

        group_size = 0
        group_sizes = []
        property_global_index = 0
        phandle_index_list = []

        # We do one pass through the property field description. During
        # this pass, we dereference nodes (if required) and count fields.
        #
        # Note: this means that if a property has variable sized records,
        #       we could have issues. That hasn't been seen yet, but if
        #       it does happen, we need to walk ALL the values in the
        #       property to calculate the length of each record individually
        #       (and then have to change the chunking up of output throughout
        #       lopper).
        try:
            pval = self.value[property_global_index]
            property_iteration_flag = True
        except:
            property_iteration_flag = False

        while property_iteration_flag:
            for phandle_desc in property_fields:
                if re.search( r'^#.*', phandle_desc ):
                    try:
                        field_val = self.node.__props__[phandle_desc].value[0]
                    except Exception as e:
                        if self.node and self.node.tree and self.node.tree.strict:
                            # lopper.log._warning( f"({self.node.abs_path}) deref exception: {e}" )
                            return phandle_map

                        field_val = 1

                    group_size = group_size + field_val
                    property_global_index = property_global_index + field_val

                elif re.search( r'^\^.*', phandle_desc ):
                    try:
                        parent_node = None
                        if self.node:
                            parent_node = self.node.parent

                        derefs = phandle_desc.split(':')
                        if len(derefs) == 2:
                            # if parent_node is none, the exception will fire
                            field_val = parent_node.__props__[derefs[1]].value[0]
                        else:
                            field_val = 1
                    except Exception as e:
                        if self.node and self.node.tree and self.node.tree.strict:
                            # lopper.log._warning( f"({self.node.abs_path}) deref exception: {e}" )
                            return phandle_map

                        field_val = 1

                    group_size = group_size + field_val
                    property_global_index = property_global_index + field_val

                elif re.search( r'^phandle', phandle_desc ):
                    derefs = phandle_desc.split(':')
                    phandle_index_list.append( property_global_index )

                    try:
                        val = self.value[property_global_index]
                    except Exception as e:
                        # if the property isn't assigned to a node, don't warn/debug
                        # just continue
                        if not self.node:
                            continue

                        lopper.log._debug( f"index out of bounds for {self.name}"
                                           f"index: {property_global_index}, len: {len(self.value)}" )

                        if property_global_index >= len(self.value):
                            property_iteration_flag = False
                            break

                        continue

                    # We've been instructed to look up a property in the phandle.
                    # that tells us how many elements to jump before we look for
                    # the next phandle.

                    if len(derefs) >= 2:
                        # step 1) lookup the node
                        if self.node and self.node.tree:
                            node_deref = self.node.tree.deref( val )
                        else:
                            # if we aren't in a tree, we really can't continue since
                            # whatever we do will be wrong, just return an empty
                            # map
                            return []

                        try:
                            # step 2) look for the property in the
                            #         dereferenced node. If the node
                            #         wasn't found, we'll trigger an
                            #         exception, and just set a
                            #         default value of 1.
                            cell_count = node_deref[derefs[1]].value[0]

                        except Exception as e:
                            if self.node and self.node.tree and self.node.tree.strict:
                                return phandle_map
                            cell_count = 1

                        # step 3)
                        # if the length is 3, that means there was an expression added
                        # to the definition to adjust the value we found. We pull it out
                        # and evaluate it to get the answer.
                        if len(derefs) == 3:
                            expression = str(cell_count) + derefs[2]
                            expression = eval( expression )
                            cell_count = expression

                        # the +1 is for the phandle itself.
                        group_size = group_size + 1 + cell_count
                        property_global_index = property_global_index + 1 + cell_count
                    else:
                        # just add the phandle to the field count
                        group_size = group_size + 1
                        property_global_index = property_global_index + 1

                elif m := re.match( field_offset_regex, phandle_desc ):
                    # This is a "lookback" to a phandle that is at another field
                    # of the property (versus the current one).
                    offset = m.group(1)
                    field_pos = m.group(2)
                    target_prop = m.group(3)

                    # positive or negative offset into the phandle_sub_list (we currently
                    # onl2y handle lookbacks)
                    if offset != "-":
                        lopper.log._warning( f"only negative offset lookbacks are currently supported: {phandle_desc}" )
                        lookback = field_pos
                    else:
                        lookback = eval(offset+field_pos)

                    node_deref = phandle_sub_list[lookback]
                    if node_deref != 0:
                        # get the property ..
                        try:
                            # look for the property in the deferenced node. If the
                            # node wasn't found, we'll trigger an exception, and just
                            # set a default value of 1.
                            cell_count = node_deref[target_prop].value[0]
                        except:
                            cell_count = 1
                    else:
                        cell_count = 1

                    # this is the next index to check for a phandle
                    property_global_index = property_global_index + 1 + cell_count
                else:
                    # non-lookup field, value is one!
                    group_size = group_size + 1
                    property_global_index = property_global_index + 1


            group_sizes.append(group_size)
            try:
                # if we roll over the end, catch the exception and exit
                pval = self.value[property_global_index]
                group_size = 0
            except:
                property_iteration_flag = False

        # if we don't have a group size, we have nothing to chunk up and
        # the property was likely empty. return the empty map.
        if not group_size:
            return phandle_map

        if not group_sizes:
            return phandle_map

        if debug:
            lopper.log._warning( f"  {self.name} ({self.node.abs_path}):" )
            lopper.log._warning( f"    ... property value: {self.value}" )
            lopper.log._warning( f'    ... as hex: {["0x" + hex(num)[2:].zfill(2) for num in self.value]}' )
            lopper.log._warning( f"    ... {group_sizes}" )

        # We now know the group size. We chunk up the property into
        # these groups and pick out the phandles.
        # property_value_chunks  = chunks(self.value,group_size)
        try:
            property_value_chunks = chunks_variable(self.value,group_sizes)
            # this double call is on purpose. the iterator will throw a
            # value exception if there's an issue with the chunk sizes and
            # the length of the list. We can't it and handle it gracefully
            # here, versus in the bigger processing block below.
            # We then have to reset our list for the actual iteration and
            # processing.
            for property_val_group in property_value_chunks:
                pass
            property_value_chunks = chunks_variable(self.value,group_sizes)
        except Exception as e:
            if self.node:
                # note: this can't always be a warning, since sometimes we
                #       read two inputs and before merging the cells, not
                #       all lookups are valid, so we'll get an invalid group
                #       size. We need some sort of "final resolution" flag
                #       so we can warn.
                lopper.log._debug( f"Could not fully process the cells of: "
                                   f"{self.name} ({self.node.abs_path})" )
                lopper.log._debug( f"  {e}" )

            # just do a fixed record size chunking to continue processing
            property_value_chunks = chunks(self.value,group_size)

        property_chunk_idx = 0
        property_sub_list = []
        for property_val_group in property_value_chunks:
            for p in property_val_group:
                if property_chunk_idx in phandle_index_list:
                    if self.node and self.node.tree:
                        node_deref = self.node.tree.deref( p )
                    else:
                        # can we use "None" to represent something that IS
                        # a phandle, but that we couldn't look up. That matches
                        # what deref() returns .. hmm.
                        node_deref = None
                else:
                    node_deref = 0

                phandle_sub_list.append( node_deref )

                property_chunk_idx += 1

            phandle_map.append( phandle_sub_list )
            phandle_sub_list = []

        # Not currently used, but kept for reference. returning "#invalid"
        # here breaks callers that are looking for zeros. It is up to the
        # caller to indicate invalid phandles in any further processing
        if tag_invalid:
            # Replace "None" with "#invalid"
            for i in range(len(phandle_map)):
                for j in range(len(phandle_map[i])):
                    if phandle_map[i][j] == None:
                        phandle_map[i][j] = "#invalid"

        return phandle_map

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

        phandle_map = self.phandle_map()
        phandle_flat = [x for xs in phandle_map for x in xs]
        phandle_idx = 0
        phandle_field_count = 0
        field_index = 0
        if phandle_map:
            # looking for the first non-zero entry in the map, and we
            # return that as the index, and the number of entries in the
            # record as the number of fields (note: this is not completely
            # accurate, as variable sized lists of phandles are possible)
            for rnum,record in enumerate(phandle_map):
                for rindex,r in enumerate(record):
                    field_index += 1
                    if r:
                        phandle_idx = field_index
                        phandle_field_count = len(record)

                        return phandle_idx, phandle_field_count

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

        lopper.log._debug( f"property sync: node: {[self.node]} [{self.node.number}], name: {self.name} value: {self.value}" )

        # TODO (possibly). This should just return the value as a dictionary entry,
        #                  which is actually sync'd by some controller caller OR
        #                  It really does sync by calling lopper with an exported
        #                  dictionary entry.
        #
        # For now, it is here for compatibility with older assists, and simply updates
        # the state of the property.
        #

        self.__modified__ = False
        self.__pstate__ = "syncd"

        return True

    def resolve_phandles( self, tag_invalid = False, ctx_record = False ):
        """Resolve the targets of any phandles in a property

        Args:
            tag_invalid (bool,optional): if an exception or error occurs use
                                         #invalid as the phandle return
            ctx_record (bool,optional): return context fields when resolving
                                        phandles.

        Returns:
            A list of all resolved phandle node numbers, [] if no phandles are present
        """
        phandle_targets = []
        phandle_map = self.phandle_map()
        phandle_flat = [x for xs in phandle_map for x in xs]

        if not phandle_map:
            return phandle_targets

        # we need the values in hex. This could be a utility routine in the
        # future .. convert to hex.
        prop_val = []
        for f in self.value:
            if type(f) == str:
                prop_val.append( f )
            else:
                prop_val.append( hex(f) )

        if not prop_val:
            return phandle_targets

        ctx_fields = []

        element_map_idx = 0
        element_count = 1
        element_total = len(prop_val)

        record_list = []
        for i in prop_val:
            base = 10
            if re.search( r"0x", i ):
                base = 16
            try:
                i_as_int = int(i,base)
                i = i_as_int
            except:
                pass

            phandle_check = phandle_flat[element_map_idx]
            record_list.append( i )
            if phandle_check:
                ctx_fields.append( record_list )
                record_list = []

            element_map_idx = element_map_idx + 1
            if phandle_check:
                node_deref = None
                if self.node:
                    node_deref = self.node.tree.deref( i )
                if node_deref:
                    phandle_targets.append( node_deref )
                else:
                    if tag_invalid:
                        phandle_targets.append( "#invalid" )

            element_count = element_count + 1

        if ctx_record:
            return phandle_targets, ctx_fields
        else:
            return phandle_targets

    def print( self, output ):
        """print a property

        Print the resolved value of a property to the passed output
        stream.

        The property will be indented to match the depth of a node
        in a tree.

        Args:
           output (output stream).

        Returns:
           Nothing

        """
        if not self.node:
            return

        try:
            if self.node.tree._type == "dts":
                depth = self.node.depth
            elif self.node.tree._type == "dts_overlay":
                depth = self.node.depth - 1
            else:
                depth = self.node.depth
        except:
            depth = self.node.depth

        if depth < 0:
            depth = 0

        if self.node.indent_char == ' ':
            indent = (depth * 8) + 8
        else:
            indent = (depth) + 1

        outstring = self.string_val
        only_align_comments = False

        if self.pclass == "preamble":
            # start tree peeked at this, so we do nothing
            outstring = ""
        else:
            # p.pclass == "comment"
            # we have to substitute \n for better indentation, since comments
            # are multiline

            do_indent = True
            if only_align_comments:
                if self.pclass != "comment":
                    do_indent = False

            if do_indent:
                dstring = ""
                dstring = dstring.rjust(len(dstring) + indent + 1, self.node.indent_char)
                outstring = re.sub( r'\n\s*', '\n' + dstring, outstring, 0, re.MULTILINE | re.DOTALL)

        if outstring:
            print(outstring.rjust(len(outstring)+indent, self.node.indent_char), file=output, flush=True)

    def property_type_guess( self, force = False ):
        """'guess' the type of a property

        For properties that aren't created from a fdt, we can either
        explicitly set the type (if we know it), or we can run this routine
        to look at the values and give us the best guess.

        This routine does NOT update the property type, that is the
        responsibility of the caller.

        Args:
           force: if the property already has a type, ignore it and guess anyway

        Returns:
           type of the propery (LopperFmt)

        """
        # this is used for properties that we aren't sure of the value during
        # the creation process.
        if not force:
            if self.ptype:
                return self.ptype

        # force was passed, or we didn't have a ptype already assigned
        ptype = None

        # one good way to know the type, is to check if we've defined this
        # as a phandle containing property, then it must be a UINT32
        phandle_tgts = self.resolve_phandles()
        if phandle_tgts:
            ptype = LopperFmt.UINT32

        # we still don't know! Another easy thing to check is, if the binary
        # flag it set, this is UINT8.
        if not ptype:
            if self.binary:
                ptype = LopperFmt.UINT8

        # still nothing. let's poke at the value itself
        if not ptype:
            python_type = type(self.value)
            if python_type == list:
                # we need to look a the elements
                list_ptype = None
                mixed_types = False
                for p in self.value:
                    if mixed_types:
                        continue

                    if type(p) == str:
                        # search of 0x in the string, since it is likely
                        # a number hiding as a string.
                        base = 10
                        if re.search( r"0x", p ):
                            base = 16
                        try:
                            i = int(p, base)
                            list_element_ptype = LopperFmt.UINT32
                        except:
                            list_element_ptype = LopperFmt.STRING
                    else:
                        list_element_ptype = LopperFmt.UINT32

                    if list_ptype:
                        if list_element_ptype != list_ptype:
                            mixed_types = True
                        # if mixed, it is a string formatted list
                        list_ptype = LopperFmt.STRING
                    else:
                        list_ptype = list_element_ptype

                ptype = list_ptype

            elif python_type == int:
                ptype = LopperFmt.UINT32
            elif python_type == str:
                ptype = LopperFmt.STRING
                # search of 0x in the string, since it is likely
                # a number hiding as a string.
                base = 10
                if re.search( r"0x", self.value ):
                    base = 16
                try:
                    i = int(self.value, base)
                    ptype = LopperFmt.UINT32
                except:
                    pass

        return ptype

    def resolve( self, strict = True ):
        """resolve (calculate) property details

        Some attributes of a property are not known at initialization
        time, or may change due to tree operations.

        This method calculates those values using information in the
        property and in the tree

        Fields resolved:
           - abs_path
           - type
           - string_val (with phandles resolved)
           - __pstate__

        Args:
           strict: (boolean, optional): indicate whether correctness
                                        should be stictly enforced

        Returns:
           Nothing
        """
        outstring = f"{self.name} = {self.value};"

        prop_val = self.value

        if self.node:
            self.abs_path = self.node.abs_path + "/" + self.name
        else:
            self.abs_path = self.name

        if re.search( r"lopper-comment.*", self.name ):
            prop_type = "comment"
        elif re.search( r"lopper-preamble", self.name ):
            prop_type = "preamble"
        elif re.search( r"lopper-label.*", self.name ):
            prop_type = "label"
        else:
            # we could make this smarter, and use the Lopper Guessed type
            # if the class was json, only change the type if the value is
            # no longer a string .. since if it is still a string, is is
            # json encoded and should be left alone.
            if self.pclass == "json":
                prop_type = self.pclass
                if type(self.value) != str:
                    prop_type = type(prop_val)
            else:
                prop_type = type(prop_val)

        lopper.log._debug( f"strict: {strict} property [{prop_type}] resolve: {self.name} val: {self.value}" )

        self.pclass = prop_type

        if self.phandle_resolution:
            phandle_map = self.phandle_map()
        else:
            phandle_map = []

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
                outstring += f"{p}\n"

            outstring +=  f"{prop_val[-1]}*/\n"

        elif prop_type == int:
            outstring = f"{self.name} = <{hex(prop_val)}>;"
        elif prop_type == "json":
            # if this is a json property type, we need to escape any nested double
            # quotes. Otherwise, dtc won't be able to compile the dts.
            outstring = "{0} = \"{1}\";".format( self.name, self.value.replace('"', '\\"') )
        elif prop_type == list:
            # if the length is one, and the only element is empty '', then
            # we just put out the name

            process_list = True
            if len(prop_val) == 0:
                # outstring = ""
                outstring = f"{self.name};"
                process_list = False
            elif len(prop_val) == 1 and prop_val[0] == '':
                # this is an empty string, property = ""; in the input
                # if we detect that, allow it to be processed as a
                # string below, for non-string types, just assign it
                # to the property name
                if not self.ptype == LopperFmt.STRING:
                    outstring = f"{self.name};"
                    process_list = False
            elif len(prop_val) == 1 and prop_val[0] == ' ':
                outstring = f"{self.name};"
                process_list = False

            if process_list:
                # otherwise, we need to iterate and output the elements
                # as a comma separated list, except for the last item which
                # ends with a ;
                outstring = ""
                outstring_list = f"{self.name} = "

                # if the attribute was detected as potentially having a
                # phandle, phandle_map will be non zero.
                if phandle_map:
                    # we should consider a test to see if the type is string AND
                    # we have a non-zero phandle index.
                    # test if the string can be converted to a number, if not,
                    # don't change the list type here.
                    list_of_nums = True
                else:
                    list_of_nums = False

                if type(prop_val[0]) == str:
                    # is it really a number, hiding as a string ?
                    base = 10
                    if re.search( r"0x", prop_val[0] ):
                        base = 16
                    try:
                        i = int(prop_val[0],base)
                        # non fdt properties were relying on this.
                        # we need to track down, and fix their typing, since
                        # the ptype changes below based on this.
                        # list_of_nums = True
                    except:
                        list_of_nums = False
                else:
                    list_of_nums = True

                # if list_of_nums:
                #     # we shouldn't be changing this here, it should be done on the
                #     # load and never touched again.
                #     self.ptype = LopperFmt.UINT32
                # else:
                #     # and we also shouldn't be changing this here.
                #     self.ptype = LopperFmt.STRING

                element_count = 1
                element_total = len(prop_val)
                outstring_record = ""

                formatted_records = []
                phandle_record = []
                if phandle_map:
                    pval_index = 0
                    # each entry in the phandle map list, is a "record" in the phandle
                    # list.
                    for rnum,record in enumerate(phandle_map):
                        # each entry is a list of value that is a phandle or not, so we
                        # walk this list
                        drop_sub_record = False
                        phandle_sub_record = []

                        if self.binary:
                            phandle_sub_record.append( "[" )
                        else:
                            # we have to open with a '<', if this is a list of numbers
                            phandle_sub_record.append( "<" )

                        # now we walk the fields in the individual record, they will be values
                        # or phandles
                        for rindex,r in enumerate(record):
                            if rindex == 0:
                                # first item, we don't want a leading space
                                pass
                            else:
                                phandle_sub_record.append( " " )

                            if r:
                                if type(r) == str and r == "#invalid":
                                    # drop the record, if strict
                                    if not strict:
                                        if type(prop_val[pval_index]) == str:
                                            phandle_tgt_name = prop_val[pval_index]
                                        else:
                                            phandle_tgt_name = "&invalid_phandle"
                                    else:
                                        if self.node.tree:
                                            self.node.tree.warn( [ "invalid_phandle" ],
                                                                 f"property: {self.name} ({self.node.abs_path})",
                                                                 self.node )
                                        # strict and an invalid phandle, jump to the next record
                                        # were we the last record ? That means we could have an incorrectly
                                        # continued list with ","
                                        if rnum == len(phandle_map) - 1:
                                            try:
                                                formatted_records[-1] = ";"
                                                #phandle_record[-1] = ";"
                                            except:
                                                pass

                                        drop_sub_record = True
                                        pval_index = pval_index + 1
                                        continue
                                else:
                                    if r.label:
                                        phandle_tgt_name = "&" + r.label
                                    else:
                                        # the node has no label, we should label it, so we can reference it.
                                        # phandle_tgt_name = Lopper.phandle_safe_name( phandle_resolution.name )
                                        r.label_set( Lopper.phandle_safe_name( r.name ) )
                                        phandle_tgt_name = "&" + r.label

                                phandle_sub_record.append( f"{phandle_tgt_name}" )

                            else:
                                # r is not set, so this was a "0" in the phandle map, which just
                                # means "not a phandle". So it is a value we have to encode for output
                                element = prop_val[pval_index]
                                if self.binary:
                                    phandle_sub_record.append( f"{element:02X}" )
                                else:
                                    try:
                                        if check_32_bit(element):
                                            hex_string = f'0x{element:x}'
                                        else:
                                            upper = element >> 32
                                            lower = element & 0x00000000FFFFFFFF
                                            hex_string = f'0x{upper:08x}' + f' 0x{lower:08x}'
                                    except Exception as e:
                                        hex_string = f'{element}'

                                    phandle_sub_record.append( hex_string )

                            pval_index = pval_index + 1

                        if not drop_sub_record:
                            if phandle_sub_record:
                                formatted_records.extend( phandle_sub_record )

                                if self.binary:
                                    formatted_records.append( "]" )
                                else:
                                    formatted_records.append( ">" )

                                # if we aren't the last item, we continue with a ,
                                if rnum != len(phandle_map) - 1:
                                    formatted_records.append( ",\n" )
                                else:
                                    formatted_records.append( ";" )
                else:
                    # no phandles
                    if list_of_nums:
                        if self.binary:
                            formatted_records.append( "[" )
                        else:
                            # we have to open with a '<', if this is a list of numbers
                            formatted_records.append( "<" )

                    for n,i in enumerate(prop_val):
                        if n == 0:
                            # first item, we don't want a leading anything
                            pass
                        else:
                            if list_of_nums:
                                formatted_records.append( " " )
                            else:
                                formatted_records.append( ", " )
                            
                        if list_of_nums:
                            if self.binary:
                                formatted_records.append( f"{i:02X}" )
                            else:
                                try:
                                    if check_32_bit(i):
                                        hex_string = f'0x{i:x}'
                                    else:
                                        upper = i >> 32
                                        lower = i & 0x00000000FFFFFFFF
                                        hex_string = f'0x{upper:08x}' + f' 0x{lower:08x}'
                                except Exception as e:
                                    hex_string = f'{i}' 

                                formatted_records.append( hex_string )
                        else:
                            formatted_records.append( f"\"{i}\"" )

                    if list_of_nums:
                        if self.binary:
                            formatted_records.append( "];" )
                        else:
                            formatted_records.append( ">;" );
                    else:
                        formatted_records.append( ";" )

                if formatted_records:
                    for i,r in enumerate(formatted_records):
                        outstring_list += r
                else:
                    # all records were dropped, drop the property completely
                    outstring_list = ""

                outstring = outstring_list

        else:
            outstring = f"{self.name} = \"{prop_val}\";"

        if not self.ptype:
            self.ptype = self.property_type_guess()

            lopper.log._debug( f"guessing type for: {self.name}s [{self.ptype}]" )

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
       - resolve(): to update/calculate properties against the tree
       - sync(): sync modified node elements (and properties)
       - deep node copy via LopperNode()

     Attributes:
       - number: the node number in the backing structure
       - name: the node name in the backing structure (this is not the node path)
       - parent: a link to the parent LopperNode object
       - tree: the tree which contains this node
       - depth: the nodes depth in the backing structure (0 is root, 1 for first level children)
       - child_nodes: the list of child LopperNodes
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
    def __init__(self, number = -1, abspath="", tree = None, phandle = -1, name = "", debug=0 ):
        self.number = number
        self.name = name
        self.parent = None
        self.tree = tree
        self.depth = 0

        self.child_nodes = OrderedDict()

        self.phandle = phandle

        self.label = ""

        # 'type' is roughly equivalent to a compatible property in
        # the node if it exists.
        self.type = []

        if abspath:
            self.abs_path = abspath
        else:
            if name:
                self.abs_path = f"/{name}"
            else:
                self.abs_path = ""

        self._ref = 0

        # currently this can be: "dts", "yaml" or "none"
        self._source = "dts"

        # ordered dict, since we want properties to come back out in
        # the order we put them in (when we iterate).
        self.__props__ = OrderedDict()
        self.__current_property__ = -1
        self.__props_pending_delete__ = OrderedDict()

        self.__dbg__ = debug

        # output/print information
        self.indent_char = ' '

        # states could be enumerated types
        self.__nstate__ = "init"
        self.__modified__ = False

    def __deepcopy__(self, memodict={}):
        """ Create a deep copy of a node

        Only certain parts of a node need to be copied, we also have to
        trigger deep copies of properties, since they have references
        to nodes.

        We leave most values as the defaults on the new node instance,
        since the copied node needs to be added to a tree, where they'll
        be filled in.
        """

        lopper.log._debug( f"node deepcopy start: {self.abs_path}" )

        new_instance = LopperNode()

        # if we blindly want everything, we'd do this update. But it
        # is easier to pick out the properties that we do want, versus
        # copying and undoing.
        #      new_instance.__dict__.update(self.__dict__)

        # we loop instead of the copy below, since we want to preserve the order
        #      new_instance.__props__ = copy.deepcopy( self.__props__, memodict )
        new_instance.__props__ = OrderedDict()
        for p in reversed(self.__props__):
            lopper.log._debug( f"    property deepcopy start: {p} {self.__props__[p].value}" )
            new_instance[p] = copy.deepcopy( self.__props__[p], memodict )
            new_instance[p].node = new_instance
            lopper.log._debug( f"    property deepcopy has returned: {new_instance.__props__[p]} {new_instance.__props__[p].value}" )

        new_instance.number = -1 # copy.deepcopy( self.number, memodict )
        new_instance.depth = copy.deepcopy( self.depth, memodict )
        new_instance.label = copy.deepcopy( self.label, memodict )
        new_instance.type = copy.deepcopy( self.type, memodict )

        # consider appending a ".copied" to the name if the path
        # manipulations to /copied/ below are not enough (when
        # added via a flag)
        new_instance.name = copy.deepcopy( self.name, memodict )

        # It is up to the caller to adjust the copied node's path
        # to avoid duplicates if added to a tree that contained
        # the original node. We could consider a flag to the node
        # copy that would do this automatically (i.e. prepend a
        # /copied to the path, but for now, we leave it to the caller.
        new_instance.abs_path = copy.deepcopy( self.abs_path, memodict )
        new_instance.indent_char = self.indent_char

        new_instance._source = self._source

        # this may cause duplicate phandles, be careful when assiging to
        # a tree ... but doing this means that there's a chance copied
        # phandle references will continue to work.
        new_instance.phandle = self.phandle

        new_instance.tree = None

        # Note: the parent is not copied, this can cause issues
        #       with deleting nodes, since you can't go up the tree
        #       to remove it from subnodes(). but it is updated in
        #       the copied children, to point at the new parent node

        new_instance.child_nodes = OrderedDict()
        for c in reversed(self.child_nodes.values()):
            new_instance.child_nodes[c.abs_path] = copy.deepcopy( c, memodict )
            new_instance.child_nodes[c.abs_path].number = -1
            new_instance.child_nodes[c.abs_path].parent = new_instance

        lopper.log._debug( f"deep copy done: {[self]}" )

        return new_instance


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
                p.__pstate__ = "init"
                p.node = nn

            # invalidate a few things
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

            if name == "phandle":
                # someone is assigning a phandle, the tree's pnodes need to
                # be updated
                if self.tree:
                    # only non-zero phandles need update
                    if value > 0:
                        # this really should be interal to the tree, and will
                        # be in the future. We need some sort of Node "update"
                        # since the pnode assignemnt is only done in the add()
                        # (same with label updates).
                        #
                        # if strict is set, we could check to see if a phandle
                        # is already mapped and warn/error.
                        #
                        self.tree.__pnodes__[value] = self

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

    def items( self ):
        """method to support items() iteration

        If the pure Iterators aren't used (__iter__, etc), and instead a dictionary
        style items() is requested for the Node. We can just return the items() from
        __props__ to support that style of access.

        Args:
            None

        Returns:
           LopperNode object: self
        """
        return self.__props__.items()

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
            if self.abs_path == other.abs_path:
                return True

            return False

    def __hash__(self):
        """magic method for hasing a node

        Used when searching for a node in a list (among other things). We return
        the hash of a nodes absolute path as the identity value.

        Args:
            None

        Returns:
           Integer hash for the node

        """
        return hash((self.abs_path))

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
            self.__props__[key].resolve()

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

    def path(self,relative_to=None,sanity_check=False):
        """dynamically calculate the path of a node

        Args:
           relative_to (String): return the path relative to the
                                 passed path (not currently implemented)
           sanity_check (Boolean): default Faklse. Perform path sanity
                                   checking on the node.

        Returns:
           String: The absolute path of the node
        """
        npath = "/" + self.name

        node = self
        while node.parent:
            node = node.parent
            if node.name:
                npath = "/" + node.name + npath

        if sanity_check:
            if self.tree:
                try:
                    scheck_node = self.tree[npath]
                except Exception as e:
                    raise e

        return npath

    def phandle_set(self,value):
        old_phandle = self.phandle

        self.phandle = value

        # is there a phandle property ? That is only used
        # in printing, but it should be updated to match
        try:
            phandle_prop = self.__props__["phandle"]
            self.__props__["phandle"].value = self.phandle
        except:
            True

        # TOOD: consider if we should update the tree, if we are assigned to one ?

    def label_set(self,value):
        # someone is labelling a node, the tree's lnodes need to be
        # updated
        if self.tree:
            if value:
                # is there an existing labelled node ?
                label_val = None
                count = 1
                while not label_val:
                    try:
                        existing_label = self.tree.__lnodes__[value]
                        # label exists, we need to be unique
                        value = value + "_" + str(count)
                        count = count + 1
                    except:
                        label_val = value

                self.tree.__lnodes__[value] = self
                self.label = value
        else:
            # there's no associated tree, so the __lnodes__ update
            # will have too come when the node is added.
            self.label = value


    def resolve_all_refs( self, property_mask=[], parents=True ):
        """Resolve and Return all references in a node

        Finds all the references starting from a given node. This includes:

           - The node itself
           - The parent nodes
           - Any phandle referenced nodes, and any nodes they reference, etc

        Args:
           property_mask (list of regex): Any properties to exclude from reference
                                          tracking, "*" to exclude all properties
           parents (bool): flag indicating if parent nodes should be returned as
                           references. Default is True.

        Returns:
           A list of referenced nodes, or [] if no references are found

        """
        property_mask_check = property_mask
        if type(property_mask) != list:
            property_mask_check = [ property_mask ]

        # find all references in the tree, starting from node_name
        reference_list = []

        # always add ourself!
        reference_list.append( self )

        if parents:
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
                phandle_nodes = p.resolve_phandles()
                for ph_node in phandle_nodes:
                    # don't call in for our own node, or we'll recurse forever
                    if ph_node.abs_path != self.abs_path:
                        refs = ph_node.resolve_all_refs( property_mask_check, parents )
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

    def children( self ):
        """Return the immediate children of this node

        Args:
           None

        Returns:
           A list of child LopperNodes

        """
        # we are just a wrapper around the generic subnodes method
        return self.subnodes(max_depth=1,children_only=True)

    def subnodes( self, depth=0, max_depth=None, children_only = False ):
        """Return all the subnodes of this node

        Gathers and returns all the reachable subnodes of the current node
        (this includes nodes of children, etc).

        Args:
           None

        Returns:
           A list of child LopperNodes

        """
        if children_only:
            all_kids = []
        else:
            all_kids = [ self ]


        if depth and max_depth == depth:
            return all_kids

        for child_node in self.child_nodes.values():
            all_kids = all_kids + child_node.subnodes( depth + 1, max_depth  )

        return all_kids

    def is_child( self, potential_child_node ):
        """test if a node is a child

        Returns true if the passed node is a child of this node,
        false otherwise.

        Args:
            potential_child_node (LopperNode) : node to test as descendant

        Returns:
             bool: returns True if the node is a chile, false otherwise
        """
        possible_children = self.subnodes()
        if potential_child_node in possible_children:
            return True

        return False

    def print( self, output=None, strict=None, as_string=False ):
        """print a node

        Print a node to the passed output stream. If it isn't passed, then
        the containg tree's output is used. If the tree has no output, stdout
        is the final fallback.

        The node  will be indented to match the depth of a node
        in a tree.

        Args:
           output (optional, output stream).
           strict (optional, default None) : resolve properties when printing
           as_string (optional, default False) : return output as a string

        Returns:
           Nothing or string if "as_string" is set

        """

        if as_string:
            sys.stdout = mystdout = StringIO()
            output = sys.stdout
        else:
            if not output:
                try:
                    output = self.tree.output
                except:
                    output = sys.stdout

        try:
            if self.tree._type == "dts":
                depth = self.depth
            elif self.tree._type == "dts_overlay":
                depth = self.depth - 1
            else:
                depth = self.depth
        except:
            depth = self.depth

        if depth < 0:
            depth = 0

        if self.indent_char == ' ':
            indent = depth * 8
        else:
            indent = depth

        nodename = self.name

        # we test for None, not "if strict", since we don't want an
        # explicitly passed "False" to not take us into the check.
        resolve_props = False
        if strict != None:
            if self.tree and self.tree.strict != strict:
                resolve_props = True

        if self.abs_path != "/":
            plabel = ""
            try:
                if n['lopper-label.*']:
                    plabel = n['lopper-label.*'].value[0]
            except:
                label_all_nodes = False
                if not self.label:
                    if label_all_nodes:
                        self.label_set( Lopper.phandle_safe_name( nodename ) )
                else:
                    pass

                plabel = self.label

            if self.phandle != 0:
                if plabel:
                    outstring = plabel + ": " + nodename + " {"
                else:
                    # this is creating duplicate labels if the node names collide
                    # which they may
                    # outstring = Lopper.phandle_safe_name( nodename ) + ": " + nodename + " {"
                    outstring = nodename + " {"
            else:
                if plabel:
                    outstring = plabel + ": " + nodename + " {"
                else:
                    outstring = nodename + " {"

            print( "", file=output, flush=True )
            print(outstring.rjust(len(outstring)+indent, self.indent_char), file=output, flush=True )
        else:
            # root node
            # peek ahead to handle the preamble
            for p in self:
                if p.pclass == "preamble":
                    print( f"{p}", file=output, flush=True )

            print( "/dts-v1/;", file=output, flush=True )

            tree_type = "dts"
            try:
                tree_type = self.tree._type
            except:
                pass
            if tree_type == "dts":
                if self.tree and self.tree.__memreserve__:
                    mem_res_addr = hex(self.tree.__memreserve__[0] )
                    mem_res_len = hex(self.tree.__memreserve__[1] )
                    print( f"/memreserve/ {mem_res_addr} {mem_res_len};\n", file=output, flush=True )

                print( "/ {", file=output, flush=True )
            elif tree_type == "dts_overlay":
                print( "/plugin/;", file=output, flush=True )

        # now the properties
        for p in self:
            if resolve_props:
                p.resolve( strict )

            p.print( output )

        # child nodes
        for cn in self.child_nodes.values():
            cn.print( output )

        # end the node
        outstring = ""
        if self.abs_path == "/":
            # root nodes, on non-dts output (i.e. overlays do not have
            # a node opening bracket, so we don't need to close it here)
            tree_type = "dts"
            try:
                tree_type = self.tree._type
            except:
                pass

            if tree_type == "dts":
                outstring = "};"
        else:
            outstring = "};"

        print(outstring.rjust(len(outstring)+indent, self.indent_char), file=output , flush=True)

        if as_string:
            sys.stdout = sys.__stdout__
            return mystdout.getvalue()


    def phandle_or_create( self ):
        """Access (and generate) a phandle for this node

        Invoked the containing tree (if available), ad creates a unique phandle
        for a node. This is basic tracking and is used since
        fdt_find_max_phandle is not fully exposed, and removes a binding to
        libfdt.

        Args:
           None

        Returns:
           phandle number

        """
        if not self.tree:
            return 0

        if self.phandle > 0:
            return self.phandle

        new_ph = self.tree.phandle_gen()
        self.phandle = new_ph

        newprop = LopperProp(name='phandle',value=new_ph)
        self + newprop

        return new_ph

    def export( self ):
        """Export node details as a dictionary

        Export the details of a node in a dictionary. The format of the dictionary
        is suitable for loading() into a LopperTree, or syncing() to a flattened
        device tree by lopper.fdt.

        Internal / FDT properties are prefixed/suffixed with __.

        As part of exporting a node, if paths are detected as changed (a moved
        node, a renamed node, etc), then the are adjusted in the tree and
        exported in the dictionary.

        Note: This is not recursive, so child nodes are not exported

        Args:
           None

        Returns:
           Ordered Dict Describing a node

        """
        # node export to a dictionary
        dct = OrderedDict()

        # is the node resolved ? if not, it may have been added since we read the
        # tree and created things.
        if self.__nstate__ != "resolved":
            lopper.log._warning( f"node export: unresolved node, not syncing" )
        else:
            dct['__fdt_number__'] = self.number
            dct['__fdt_name__'] = self.name
            dct['__fdt_phandle__'] = self.phandle

            last_chunk_of_path = os.path.basename( self.abs_path )
            if last_chunk_of_path != self.name:
                lopper.log._debug( f"node export: name change detected, adjusting path" )
                self.abs_path = os.path.dirname( self.abs_path ) + "/" + self.name

            if self.parent:
                parent_chunk_of_path = os.path.dirname( self.abs_path )
                if parent_chunk_of_path != self.parent.abs_path:
                    lopper.log._debug( f"node export: path component change detected, adjusting path" )
                    self.abs_path = self.parent.abs_path + "/" + self.name

            self.abs_path = self.abs_path.replace( "//", "/" )
            lopper.log._debug( f"node export: start: [{self.number}][{self.abs_path}]" )

            dct['__path__'] = self.abs_path
            dct['__nodesrc__'] = self._source

            # property export
            for p in self.__props__.values():
                dct[p.name] = p.value
                if p.binary:
                    dct[f'__{p.name}_type__'] = LopperFmt.UINT8
                else:
                    dct[f'__{p.name}_type__'] = p.ptype

                dct[f'__{p.name}_pclass__'] = p.pclass

                lopper.log._debug( f"       node export: [{p.ptype}] property: {p.name} value: {p.value} (state:{p.__pstate__})(type:{dct[f'__{p.name}_type__']})" )

            if self.label:
                # there can only be one label per-node. The node may already have
                # a label that was created during processing, or read from a dtb.
                # if so, we overwrite that one with any label updates that may have
                # happened during processing. Otherwise, we create a special lopper
                # label attribute
                keys_to_delete = []
                existing_label = ""
                label_name = "lopper-label-0"
                for lp in dct.keys():
                    if re.match( r'lopper-label.*', lp ):
                        if existing_label:
                            keys_to_delete.append( lp )
                        else:
                            existing_label = lp
                            label_name = lp


                for k in keys_to_delete:
                    del dct[k]

                dct[label_name] = [ self.label ]


            self.__modified__ = False

        return dct


    def sync( self, fdt = None ):
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
        retval = True

        # TODO: this will either export a single node dictionary OR it will
        #       export and call lopper to sync it directly. For now, it is
        #       just marking everything sync'd, and is kept for compatibility

        # is the node resolved ? if not, it may have been added since we read the
        # tree and created things.
        if self.__nstate__ != "resolved":
            lopper.log._warning( f"node sync: unresolved node, not syncing" )
        else:
            lopper.log._debug( f"node sync start: [{self.number}][{self.abs_path}]" )

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
        if isinstance( prop, LopperProp ) or type(prop) == str:
            lopper.log._debug( f"deleting property {prop} from node {self}" )

            prop_to_delete = prop
            if type(prop) == str:
                try:
                    prop_to_delete = self.__props__[prop]
                except Exception as e:
                    raise e

            if not isinstance( prop_to_delete, LopperProp ):
                lopper.log._warning( f"invalid property passed to delete: {prop}" )

            self.__modified__ = True
            try:
                prop_to_delete.__pstate__ = "deleted"
                self.__props_pending_delete__[prop_to_delete.name] = prop_to_delete
                del self.__props__[prop_to_delete.name]
            except Exception as e:
                raise e
        elif isinstance( prop, LopperNode):
            try:
                del self.child_nodes[prop.abs_path]
                self.__modified__ = True
            except:
                lopper.log._debug( f"node {prop.abs_path} not found, and could not be deleted" )

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
    def propval( self, pname, ptype=None ):
        """Access the value of a property

        This is a safe (no Exception) way to access the value of a named property,
        versus access it through the dictionary accessors.

        Args:
           name (string): property name
           ptype(Optional): the format of the returned value

        Returns:
           list: list of values for the property, or [""] if the property name is invalid

        """
        if not ptype:
            try:
                prop = self.__props__[pname]
                return prop.value
            except:
                return [""]
        else:
            try:
                # we are doing a type cast
                if ptype == dict:
                    return dict(self.__props__[pname])
                elif ptype == list:
                    if type(self.__props__[pname].value) == list:
                        return self.__props__[pname].value
                    else:
                        return [ self.__props__[pname].value ]
                else:
                    return self.__props__[pname].value
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
        if not isinstance( other, LopperProp ) and not isinstance( other, LopperNode ):
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
        """Add a property or subnode to a node

        Supports adding a property or node to a node through

            node.add( prop )

        After adding the new elelent, the node is tagged as modified to it
        can be sync'd in the future.

        Args:
           prop (LopperProp or LopperNode): element to add

        Returns:
           LopperNode: returns self, raises Exception on invalid parameter

        """
        if isinstance( prop, LopperProp ):
            lopper.log._debug( f"node {self.abs_path} adding property: {prop.name}" )

            self.__props__[prop.name] = prop
            prop.node = self

            # indicates that we should be sync'd
            self.__modified__ = True
        elif isinstance( prop, LopperNode):
            node = prop
            # this isn't ideal. We don't have a path, but are getting
            # subnodes added. So we assume that our path is /<name> and
            # will adjust it later if that is wrong
            if not self.abs_path:
                self.abs_path = "/" + self.name

            node.abs_path = self.abs_path + "/" + node.name
            node.parent = self
            node.tree = self.tree

            self.child_nodes[node.abs_path] = node

            # this gets the node fully into the tree's tracking dictionaries
            if self.tree:
                self.tree.add( node )

            lopper.log._debug( f"node {self.abs_path} added Node: {node.name}" )

        return self

    def merge( self, other_node ):
        """merge a secondary node into the target

        This routine updates the target node with the properties of secondary.

        It is additive/modification only, no properties are removed as part of
        the processing.

        Args:
           other_node (LopperNode): The other to merge

        Returns:
           Nothing

        """
        # export the dictionary (properties)
        o_export = other_node.export()

        # load them into the node, keep children intact, this is a single
        # node operation
        self.load( o_export, clear_children = False, update_props = True )

    def load( self, dct, parent_path = None, clear_children = True, update_props = False):
        """load (calculate) node details against a property dictionary

        Some attributes of a node are not known at initialization time, or may
        change due to tree operations.

        This method calculates those values using information in the node and in
        the passed property dictionary

        If clear_children is set to True (the default), children nodes will be
        dropped with the expectation that they will be re-added when the children
        themselves are loaded. When set to False, the children are not modified,
        and this is used when updating a node from a dictionary.

        If update_props is set to True (the default is False), then existing
        properties will be updated with the contents of the passed dictionary.
        This is set to true when a dictionary should override all values in
        a node.

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
           Property dictionary: Dictionary with the node details/properties
           parent_path (Optional,string)
           clear_children (Optional,boolean): default is True
           update_props (Optional,boolean): default is False

        Returns:
           Nothing

        """
        # resolve the rest of the references based on the passed device tree
        # self.number must be set before calling this routine.

        self.dct = dct

        #
        # tree add currently takes care of this, but it might be better if
        # done here, since that way it is properly recursive and self contained.
        # keeping this here as a reference / placeholder
        #
        # if self.child_nodes:
        #     for p,c in list(self.child_nodes.items()):
        #         print( "loading child node %s" % c.name )
        #         # TODO: To be complete, we could add the properites of the node
        #         #       into the dictionary when calling load, that way we don't
        #         #       count on the current behaviour to not drop the properties.
        #         c.load( { '__path__' : self.abs_path + c.name,
        #                   '__fdt_name__' : c.name,
        #                   '__fdt_phandle__' : 0 },
        #                 self.abs_path )

        if dct:
            strict = self.tree.strict

            # we may not need to save this, temp.
            self.dct = dct

            self.abs_path = dct['__path__']

            if clear_children:
                # children will find their way back during the load process, so clear
                # the existing ones
                self.child_nodes = OrderedDict()

            lopper.log._debug( f"node load start [{self}][{self.number}]: {self.abs_path}" )

            saved_props = self.__props__
            self.__props__ = OrderedDict()

            self.name = dct['__fdt_name__']

            self.phandle = dct['__fdt_phandle__']

            last_chunk_of_path = os.path.basename( self.abs_path )
            if last_chunk_of_path != self.name:
                self.abs_path = os.path.dirname( self.abs_path ) + "/" + self.name

            # parent and depth
            if self.number != 0:
                if self.tree:
                    try:
                        self.parent = self.tree[parent_path]
                    except:
                        self.parent = None
                    if self.parent:
                        # the child dictionary is ordered. So we delete a
                        # key and re-assign it to make sure ordering is up
                        # to date
                        try:
                            if self.parent.child_nodes[self.abs_path]:
                                del self.parent.child_nodes[self.abs_path]
                        except:
                            pass

                        self.parent.child_nodes[self.abs_path] = self

                depth = len(re.findall( r'/', self.abs_path ))
            else:
                depth = 0

            self.depth = depth

            # First pass: we look at the properties in the FDT. If they were in our
            # saved properties dictionary from above, we copy them back in. Re-resolving
            # and decoding unchanged properties is slow, so we avoid that step where
            # possible.
            self.type = []
            label_props = []

            for prop, prop_val in dct.items():
                if re.search( r"^__", prop ) or prop.startswith( r'/' ):
                    # internal property, skip
                    continue

                dtype = LopperFmt.UINT8
                try:
                    # see if we got a type hint as part of the input dictionary
                    dtype = dct[f'__{prop}_type__']
                except Exception as e:
                    pass

                lopper.log._debug( f"node [{self}] load: [{dtype}] prop: {prop} val: {prop_val}" )

                try:
                    # see if we got a property class as part of the input dictionary
                    pclass = dct[f'__{prop}_pclass__']
                except Exception as e:
                    if re.search( r'lopper-comment-.*', prop ):
                        pclass = "comment"
                    elif re.search( r'lopper-label-.*', prop ):
                        pclass = "label"
                    else:
                        pclass = ""

                try:
                    node_source = dct['__nodesrc__']
                except:
                    node_source = None

                # special handling for 'compatible', we bubble it up as the node "type"
                if prop == "compatible":
                    self.type += prop_val
                    for p in prop_val:
                        if re.search( r"phandle-desc.*", p ):
                            strict = False

                # create property objects, and resolve them
                try:
                    existing_prop = saved_props[prop]
                except Exception as e:
                    existing_prop = None

                if existing_prop:
                    # same prop name, same parent node .. it is the same. If this
                    # somehow changes, we'll need to call resolve on this as well.
                    self.__props__[prop] = existing_prop
                    if update_props:
                        if self.__props__[prop].value != prop_val:
                            lopper.log._debug( f"existing prop detected ({self.__props__[prop].name}), updating value: {self.__props__[prop].value} -> {prop_val}" )
                            self.__props__[prop].value = prop_val

                else:
                    self.__props__[prop] = LopperProp( prop, -1, self,
                                                       prop_val, self.__dbg__ )
                    if dtype == LopperFmt.UINT8:
                        self.__props__[prop].binary = True

                    self.__props__[prop].ptype = dtype
                    self.__props__[prop].pclass = pclass

                    if node_source:
                        self._source = node_source

                    self.__props__[prop].resolve( strict )
                    self.__props__[prop].__modified__ = False

                    # if our node has a property of type label, we bubble it up to the node
                    # for future use when replacing phandles, etc.
                    if self.__props__[prop].pclass == "label":
                        self.label = self.__props__[prop].value[0]
                        label_props.append( self.__props__[prop] )


            # second pass: re-resolve properties if we found some that had labels
            if label_props:
                # we had labels, some output strings in the properities may need to be
                # update to reflect the new targets
                for p in self.__props__:
                    self.__props__[p].resolve( strict )
                    self.__props__[p].__modified__ = False

                # now delete the lopper-prop-* property, we'll just run with
                # the node.label property during our tree processing routines.
                for p in label_props:
                    try:
                        del self.__props__[p.name]
                    except Exception as e:
                        lopper.log._debug( f"{e}")

            # 3rd pass: did we have any added, but not sync'd properites. They need
            #           to be brought back into the main property dictionary.
            for p in saved_props:
                if saved_props[p].__pstate__ != "deleted":
                    self.__props__[p] = saved_props[p]
                    self.__props__[p].node = self

            if not self.type:
                self.type = [ "" ]

            # this ensures any phandle properties are in sync
            # with the phande assigned to the node
            self.phandle_set( self.phandle )

            self.__nstate__ = "resolved"
            self.__modified__ = False

        lopper.log._debug( f"node load end: {self}" )

    def resolve( self, fdt = None, resolve_children=True ):
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
           resolve_children (Boolean): default True. When resolving the
                                       node, also resolve any child nodes.

        Returns:
           Nothing

        """
        # resolve the rest of the references based on the passed device tree
        # self.number must be set before calling this routine.
        lopper.log._debug( f"node resolution start [{self}][{self.number}]: {self.abs_path}" )

        ## This may be converted to a dictionary export -> call to lopper fdt
        ## to do a partial sync. But for now, it is just changing the state as
        ## the new load() function takes care of these details.

        ## We also may use this as recursive subnode resolve() call, so we
        ## can apply changes to a nodes properties and all subnode properties

        ## Note:  if the node is not in a tree, the path will change to
        ##        /<name>, and will likely need to be adjusted later
        ## Note2: While keeping the interface simpler for adding nodes,
        ##        this can break node move detection, and causes more
        ##        issues. So until we resolve them, we comment out this
        ##        helper.
        # self.abs_path = self.path()

        if self.abs_path == "/":
            self.depth = 0
        else:
            self.depth = len(re.findall( r'/', self.abs_path ))

        lopper.log._debug( f"node resolve: calculating depth {self.abs_path} for: {self.depth}" )

        self.__nstate__ = "resolved"
        self.__modified__ = False

        if resolve_children:
            lopper.log._debug( f"node resolve: resolving child nodes: {self.child_nodes}" )

            for cn in self.child_nodes.values():
                cn.resolve( fdt, resolve_children )

        for p in self.__props__.values():
            p.resolve()

        if self.tree and self.tree.__symbols__:
            try:
                symbol_node = self.tree['/__symbols__']
                if self.label:
                    symbol_node[self.label] = self.abs_path
            except:
                pass

        lopper.log._debug( f"node resolution end: {self}" )

    def address(self, child_addr=None, nest_count=1):
        """Get the translated Address of the node.

        Returns the unit address of the node as translated by the ranges
        of the device tree.

        Args:
           child_addr (int): current translated address
           nest_count (int,optional): recursion count

        Returns:
            translated node address (int): translated address, or None
            if no translation is possible
        """

        lopper.log._debug( f"{chr(0x20)*nest_count}address translation for: {self.abs_path} ({self.name})" )

        unit_address = child_addr
        if not child_addr:
            try:
                unit_address = int(self.name.split('@')[1],16)
                lopper.log._debug( f"{chr(0x20)*nest_count}unit address: {hex(unit_address)}" )
            except Exception as e:
                lopper.log._debug( f"node {self.name} has no unit address: {unit_address}" )
                # No @ or it isn't a hex, so we have nothing to translate
                return None

        # translate the address

        # Do we have a parent node with a ranges property ? If not, then
        # the translation is just the unit address
        if self.parent:
            pranges = self.parent.props("ranges")
            if not pranges:
                lopper.log._debug( f"{chr(0x20)*nest_count}no parent ranges, "
                                   f"returning address: {hex(unit_address)}" )
                return unit_address

            lopper.log._debug( f"{chr(0x20)*nest_count}parent ranges: {pranges[0]}" )

            pranges_values = pranges[0].value

            # if the node had just "ranges;", we continue up to the parent
            # since this means the child and parent are 1:1 mapping
            if len(pranges[0]) == 1:
                lopper.log._debug( f"{chr(0x20)*nest_count}'ranges;' found, "
                                   f"recursing to parent: {self.parent.abs_path}" )
                return self.parent.address( unit_address, nest_count + 4 )
        else:
            # no parent
            return unit_address

        # do the translation

        address_cells = self.propval( "#address-cells" )[0]
        if not address_cells:
            address_cells = 2

        parent_address_cells = self.parent.propval( "#address-cells" )[0]
        if not parent_address_cells:
            parent_address_cells = 2

        size_cells = self.propval( "#size-cells" )[0]
        if not size_cells:
            size_cells = 1

        lopper.log._debug( f"{chr(0x20)*nest_count}address cells in: {self.abs_path} and {self.parent.abs_path}"
                           f"       {chr(0x20)*nest_count}child address cells: {address_cells}"
                           f"       {chr(0x20)*nest_count}parent address cells: {parent_address_cells}"
                           f"       {chr(0x20)*nest_count}child size cells: {size_cells}" )

        # Break things into chunks and translate

        # https://elinux.org/Device_Tree_Usage#Ranges_.28Address_Translation.29

        # Each entry in the ranges table is a tuple containing the child address,
        # the parent address, and the size of the region in the child address space

        # The size of each field is determined by taking the child's #address-cells value,
        # the parent's #address-cells value, and the child's #size-cells value

        item_size = int(size_cells) + int(address_cells) + int(parent_address_cells)
        address_chunks = chunks(pranges_values,item_size)

        for address_entry in address_chunks:
            lopper.log._debug( f"{chr(0x20)*nest_count}address entry: {address_entry}" )

            child_address = address_entry[0:address_cells]
            child_address = lopper.base.lopper_base.encode_byte_array( child_address )
            child_address = int.from_bytes(child_address,"big")

            parent_address = address_entry[address_cells:address_cells + parent_address_cells]
            parent_address = lopper.base.lopper_base.encode_byte_array( parent_address )
            parent_address = int.from_bytes(parent_address,"big")

            region_size = address_entry[-size_cells:]
            region_size = lopper.base.lopper_base.encode_byte_array( region_size )
            region_size = int.from_bytes(region_size,"big")

            lopper.log._debug( f"       {chr(0x20)*nest_count}child address: {child_address}"
                               f"       {chr(0x20)*nest_count}parent_address: {hex(parent_address)}"
                               f"       {chr(0x20)*nest_count}region_size: {hex(region_size)}" )

            if child_address <= unit_address <= child_address + region_size:
                lopper.log._debug( f"{chr(0x20)*nest_count}unit address {unit_address} is "
                                   "within a translation range, recursively translating" )
                return self.parent.address( parent_address + unit_address - child_address, nest_count + 4 )

        return unit_address

    def children_by_path( self ):
        """
        Get the children of the node sorted by path

        Args:
           None
        Returns:
           OrderedDict: A dictionary of child nodes sorted by path
        """
        return dict(sorted(self.child_nodes.items(), key=lambda item: item[0].split('/')[1]))

    def reorder_child(self, path_to_move, path_to_move_next_to, after=True):
        """
        (re)order a specified child node next to another specified child

        Args:
           path_to_move(String): the path of the child to move / order
           path_to_move_next_to (String): the path next to which the specified child path will be moved
           after (boolean):  if True (default), move after path_to_move_next_to; if False, move before path_to_move_next_to

        Returns:
           OrderedDict - the modified ordered dictionary
        """

        od = self.child_nodes

        if path_to_move not in od or path_to_move_next_to not in od:
            raise KeyError("Both keys must be present in the OrderedDict")

        items = list(od.items())
        item_to_move = (path_to_move, od[path_to_move])

        # Remove the item to move
        items = [item for item in items if item[0] != path_to_move]

        # Find the position to insert
        pos = next(i for i, item in enumerate(items) if item[0] == path_to_move_next_to)
        if after:
            pos += 1

        # Insert the item at the new position
        items.insert(pos, item_to_move)

        self.child_nodes = OrderedDict(items)

        # Create a new OrderedDict from the reordered items
        return OrderedDict(items)

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
       - export(): to export the tree description as a dictionary
       - node manipulatins: add, delete, filter, subnodes
       - phandle access to nodes
       - label access to nodes
       - node search by regex

    A LopperTree object is instantiated for an easier/structure interface to a backing
    device tree store (currently only a flattened device tree from libfdt). It provides
    the ability to add/delete/manipulate nodes on a tree wide basis and can sync those
    changes to the backing store.

    When initialized, the tree is created from an exported description of the
    FDT. If the changes made by the tree  are to be indepdendent, then the FDT
    should not be re-exported and loaded by the tree. But if other components are
    changing the FDT, it can be reloaded to synchronize the tree and the backing
    store.

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
       - strict: Flag indicating if strict property resolution should be enforced

    """
    ## TODO: Should this take a dictionary as an argument, and call  "load"
    ##       at the end ??
    def __init__(self, snapshot = False, depth_first=True ):
        # nodes, indexed by abspath
        self.__nodes__ = OrderedDict()
        # nodes, indexed by node number
        self.__nnodes__ = OrderedDict()
        # nodes, indexed by phandle
        self.__pnodes__ = OrderedDict()
        # nodes, indexed by label
        self.__lnodes__ = OrderedDict()
        # nodes. indexed by aliases
        self.__aliases__ = OrderedDict()
        # nodes. selected. default/fallback for some operations
        self.__selected__ = []

        # memreserve section
        self.__memreserve__ = []

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
        self.__current_node__ = "/"
        self.__start_node__ = "/"
        self.__new_iteration__ = True
        self.__node_iter__ = None

        self.__symbols__ = False

        self.dct = None

        # type:
        #   - dts
        #   - dts_overlay
        self._type = "dts"
        self.depth_first = depth_first

        self._external_trees = []

        self.strict = True
        self.warnings = []
        self.warnings_issued = {}
        self.werror = []
        self.__check__ = False

        # output/print information
        self.indent_char = ' '

        # ensure that we have a root node available immediately
        i_dct = {  '__path__' : '/',
                   '__fdt_name__' : "",
                   '__fdt_number__' : 0,
                   '__fdt_phandle__' : -1 }
        self.load( i_dct )

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
        #if n.number == -1:
        #    raise StopIteration

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
        # TODO: we could detect if dct is assigned/writen and re-run a load
        if name == "__current_node__" or name == "__start_node__":
            if type(value) == int:
                try:
                    node = self.__nnodes__[value]
                    self.__dict__[name] = node.abs_path
                except:
                    self.__dict__[name] = "/"
            else:
                # try:
                #     nn = self.tree[value].number
                # except:
                #     nn = -1
                # self.__dict__[name] = nn
                self.__dict__[name] = value
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
            x = object.__getattribute__(self, name)
            return x
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
            access_name = key
            if access_name != "/":
                # but if we rstrip just "/", we have nothing!
                access_name = key.rstrip('/')

            return self.__nodes__[access_name]
        except Exception as e:
            # is it a label :
            try:
                return self.__lnodes__[access_name]
            except Exception as e:
                # is it a regex ?
                # we tweak the key a bit, to make sure the regex is bounded.
                # avoid looking for "^/$" accross all nodes. It's a common
                # search and can't match anything but the root node
                regex = "^" + key + "$"
                if not regex == "^/$":
                    m = self.nodes( regex )
                    if m:
                        # we get the first match, if you want multiple matches
                        # call the "nodes()" method
                        return m[0]

                # nothing, let the exception bubble up
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
            Nothing, raises TypeError on invalid parameters
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

    def warn( self, warnings, context_str = "", extra_info = None ):
        """issue a warning against the tree

        This method issues a warning (or error) against the tree as
        indicated by the caller.

        Before issuing the warning it is checked if the warning is
        enabled.

        "werror" is also checked to promote a warning to an error

        Args:
          warnings (list)      : a list of warning types to issue
          context_str (string) : extra context to add to the warning
                                 message

        Returns:
          Nothing
        """
        # was checking enabled ?
        if not self.__check__:
            return

        if self.warnings:
            for w in warnings:
                if w in self.warnings or "all" in self.warnings:
                    outstring = f"{w}: " + context_str
                    try:
                        count = self.warnings_issued[outstring]
                        self.warnings_issued[outstring] += 1
                    except:
                        self.warnings_issued[outstring] = 1
                        count = 0

                    if not count:
                        if self.werror:
                            lopper.log._error( outstring, also_exit=1 )
                        else:
                            lopper.log._warning( outstring )

                        if extra_info:
                            if self.__dbg__ > 1:
                                node_string=extra_info.print(as_string=True)
                                node_string="node:" + node_string
                                lopper.log._warning( node_string )



    def overlay_of( self, parent_tree ):
        # we are becoming an overlay_of the passed tree
        self._type = "dts_overlay"

        # remove all phandle properties that might be printed
        phandles_to_delete = []
        for n in self:
            for p in n:
                if p.name == "phandle":
                    phandles_to_delete.append( n )

        for n in phandles_to_delete:
            del n.__props__['phandle']

        # store the parent tree, this is used for resolving
        # lables and phandles before printing
        self._external_trees.append(parent_tree)

    def phandles( self ):
        """Utility function to get the active phandles in the tree

        Args:
           None

        Returns:
           list (numbers): list of in use phandles in the tree

        """
        return list(self.__pnodes__.keys())

    def phandle_gen( self ):
        """Generate a phandle for use in a node

        Creates a unique phandle for a node. This is basic tracking and is
        used since fdt_find_max_phandle is not fully exposed, and removes
        a binding to libfdt.

        Args:
           None

        Returns:
           phandle number

        """
        if self.__pnodes__:
            sorted_phandles = sorted(list(self.__pnodes__.keys()))
            highest_phandle = sorted_phandles[-1]
        else:
            # no phandles at all yet!
            highest_phandle = 0

        # self.__pnodes__[highest_phandle + 1] = None

        return highest_phandle + 1

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
            refd_nodes = starting_node.resolve_all_refs( [".*"] )
        else:
            refd_nodes = []

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


    def export(self, start_path = "/" ):
        """Export a tree to a dictionary

        This routine takes a LopperTree, and exports the nodes and properties
        to a dictionary.

        Args:
            start_path (String,optional): the starting path for export

        Returns:
             dictionary
        """
        lopper.log._debug( f"tree export start: {start_path}" )

        try:
            # tree export to a nested dictionary!
            start_node = self[start_path]
        except:
            return {}

        subnodes = start_node.subnodes( 0, 1 )

        # we'll loop forever if we leave ourself in the subnodes
        subnodes.remove( start_node )

        dct = start_node.export()

        for i,n in enumerate(subnodes):
            # node_dct = self.export(n)
            # dct[node_dct['__path__']] = node_dct
            nd = self.export(n.abs_path)
            if nd:
                dct[n.abs_path] = nd
            else:
                # keep me. This was causing an exception
                lopper.log._warning( f"node with no properties (tree corruption?): {n.abs_path}" )

        if start_path == "/":
            if self.__memreserve__:
                lopper.log._debug( f"tree export: memreserve for tree: {self}" )
                dct["/memreserve"] = { '__fdt_number__' : -1,
                                       '__fdt_name__' : "memreserve",
                                       '__fdt_phandle__' : -1,
                                       '__path__' : "/memreserve",
                                       '__memreserve__' : self.__memreserve__
                                     }

        return dct

    def print(self, output = None):
        """print the contents of a tree

        Outputs the tree to the passed output stream, if not passed the tree's
        output stream is used. If the tree has no output stream, stdout is the
        final fallback.

        Args:
           output (optional,output stream).

        Returns:
           Nothing

        """
        if not output:
            try:
                output = self.output
            except:
                output = sys.stdout
        else:
            # confirm if output is an iostream
            try:
                if type( output ) == str:
                    output = open( output, "w")
                else:
                    output = open( output.name, "w")

                if not output:
                    lopper.log._warning( f"{output} is not writable" )
                    return

            except (UnicodeDecodeError, AttributeError) as e:
                lopper.log._warning( f"{output} is not a writable {e}" )
                return

        self["/"].print( output )

    def resolve( self, check=False ):
        """resolve a tree

        Iterates all the nodes in a tree, and then the properties, making
        sure that everyting is fully resolved.

        Args:
           check (boolean,optional): flag indicating if the tree should be checked

        Returns:
           Nothing
        """

        if self.__symbols__:
            try:
                symbol_node = self['/__symbols__']
                # remove all the symbol entries. the nodes will
                # resolve their labels back into the symbol node
                # this allows us to track renames, deletes and
                # adds without doing anything fancy
                symbol_node.__props__ = OrderedDict()
            except:
                pass

        # if check is set to true, we'll throw warnings/errors
        # during the resolution process
        self.__check__ = check

        # walk each node, and individually resolve
        for n in self:
            n.resolve()
            # n.resolve() also resolves properties, so we can
            # eventually drop this properties iteration after
            # some extensive testing
            for p in n:
                p.resolve()

        self.__check__ = False


    def sync( self, fdt = None, only_if_required = False ):
        """Sync a tree to a backing FDT

        This routine walks the FDT, and sync's changes from any LopperTree nodes
        into the backing store.

        Once complete, all nodes are resolved() to ensure their attributes reflect
        the FDT status.

        Args:
           fdt (FDT,optional): the flattended device tree to sync to. If it isn't
                               passed, the stored FDT is use for sync.
           only_if_required(boolean,optional): flag to indicate that we should only
                                               sync if something is dirty

        Returns:
           Nothing

        """

        if only_if_required:
            if not self.__must_sync__:
                lopper.log._debug( f"not syncing, since __must_sync__ is not set" )
                return


        lopper.log._debug( f"[{fdt}]: tree sync start: {self}" )

        #
        # This triggers the "load" operation on the entire tree. That block
        # of code is responsible for fixing up paths, looking for renames,
        # etc.
        #
        # Note: this no longer writes to the FDT, that should be done by the
        #       Lopper.sync() call.
        #
        new_dct = self.export()

        self.load( new_dct )

        lopper.log._debug( f"[{fdt}]: tree sync end: {self}" )

        # resolve and details that may have changed from the sync
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

    def delete( self, node, delete_from_parent = True, force=False ):
        """delete a node from a tree

        If a node is resolved and syncd to the FDT, this routine deletes it
        from the FDT and the LopperTree structure.

        Args:
           node (int or LopperNode): the node to delete
           delete_fom_parent (bool): flag indicating if the node should be
                                     removed from the parent node.

        Returns:
           Boolean: True if deleted, False otherwise. KeyError if node is not found

        """
        n = node
        # not a great idea to delete by number, but we support it as
        # a transitional step
        if type(node) == int:
            # let any exceptions bubble back up
            n = self.__nnodes__[node]

        if force:
            n.__nstate__ = "resolved"

        lopper.log._debug( f"{self} attempting to delete [{[n]}] node {n.abs_path}, state: {n.__nstate__}" )
        if n.__nstate__ == "resolved" and self.__must_sync__ == False:
            lopper.log._debug( f"{self} deleting [{[n]}] node {n.abs_path}" )

            if n.child_nodes:
                for cn_path,cn in list(n.child_nodes.items()):
                    self.delete( cn, False, force=force )

            try:
                del self.__nodes__[n.abs_path]
            except Exception as e:
                pass

            try:
                del self.__pnodes__[n.phandle]
            except Exception as e:
                pass

            try:
                del self.__lnodes__[n.label]
            except Exception as e:
                pass

            try:
                del self.__nnodes__[n.number]
            except Exception as e:
                pass

            # snip the link if we are the first call, otherwise, the
            # recursive call above, will clear the delete flag. Otherwise, we
            # can't snip a node + subnodes and maintain them for another
            # location in the tree.

            # but the dictionary deletes above will ensure that the subnodes
            # are not accessibly directly fom the main tree dictionaries and
            # are hence still fundamentally deleted. As does the flagging as
            # state "deleted" below
            if n.parent and delete_from_parent:
                try:
                    ### we could try and iterate and check for object identity vs using the path here.
                    del n.parent.child_nodes[n.abs_path]
                except Exception as e:
                    lopper.log._warning( f"tree inconsistency. could not delete {n.abs_path} from {n.parent.abs_path}" )
                    lopper.log._warning( f"   parent child nodes: {n.parent.child_nodes}" )
                    for pp,cn in n.parent.child_nodes.items():
                        lopper.log._warning( f"       checking ids: {id(cn)} vs {id(n)}" )

            n.__nstate__ = "deleted"
            n.__modified__ = True

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
            raise Exception( "LopperNode was not passed" )

        self.add( other )

        return self

    def move( self, node, old_path, new_path, dont_sync = False ):
        """magic method for adding a node to a tree

        Supports adding a node to a tree through "+"

            tree + <LopperNode object>

        Args:
           other (LopperNode): node to add

        Returns:
           LopperTree: returns self, Exception on invalid input

        """
        self.delete( node )

        existing_path = node.abs_path
        node.abs_path = new_path
        try:
            self.add( node, dont_sync, merge=True )
        except Exception as e:
            lopper.log._warning( f"could not move node {node}, re-adding to tree" )

            noode.abs_path = existing_path
            self.add( node )

            return False

        return True



    def add( self, node, dont_sync = False, merge = False ):
        """Add a node to a tree

        Supports adding a node to a tree through:

            tree.add( <node> )

        The node is added to the FDT, resolved and syncd. It is then available
        for use in any tree operations.

        Args:
           node (LopperNode): node to add
           dont_sync (boolean, optional): don't invoke a tree wide sync when
                                          complete

        Returns:
           LopperTree: returns self, raises Exception on invalid parameter

        """

        lopper.log._debug( f"tree: node add: [{node.name}] {[ node ]} ({node.abs_path})({node.number})"
                           f" phandle: {node.phandle} label: {node.label}" )

        node_full_path = node.abs_path

        # if a node is being added, we should check to see if the object
        # is in the nodes list under a different path. because if it is
        # there, we can have issues with an inconsistent tree. In this
        # situation, it is really a move.
        move = None
        for pp,vv in self.__nodes__.items():
            # Note: We could also do a path compare here
            if id(vv) == id(node):
                lopper.log._debug( f"   reference detected for node: {node}, will copy, and trigger a move ({vv} == {node})" )
                lopper.log._debug( f"   ids are: {id(vv)} and {id(node)}" )
                # make a copy, since we are going to be updating the path ensure
                # it is fully deleted.
                move = vv()
                move.__nstate__ = "resolved"
                move.abs_path = pp
                move.resolve()
                # we need the parent, so that it can be removed
                # from the parents subnodes(). We could consider
                # making this part of the deepcopy ... but a copied
                # node really isn't part of the parent.
                move.parent = vv.parent

        if move:
            lopper.log._debug( f"move detected, will delete node: {move} state: {move.__nstate__}" )
            self.delete( move )

        # check all the path components, up until the last one (since
        # that's why this routine was called). If the nodes don't exist, we
        # need to add them, since that means we are trying to add a child
        # node
        if node_full_path != "/":
            for p in os.path.split( node_full_path )[:-1]:
                try:
                    existing_node = self.__nodes__[p]
                except:
                    existing_node = None

                if not existing_node:
                    # an intermediate node is missing, we need to add it
                    i_node = LopperNode( -1, p )
                    self.add( i_node, True, merge )

        # do we already have a node at this path ?
        try:
            existing_node = self.__nodes__[node.abs_path]
        except:
            existing_node = None

        if existing_node:
            if not merge:
                lopper.log._debug( f"add: node: {node.abs_path} already exists" )
                return self
            else:
                lopper.log._debug( f"add: node: {node.abs_path} exists, merging properties" )
                existing_node.merge( node )
                return self

        node.tree = self
        node.__dbg__ = self.__dbg__

        if node_full_path == "/":
            node.number = 0

        if not node.name:
            node.name = os.path.basename( node.abs_path )

        # pop one chunk off our path for the parent.
        parent_path = os.path.dirname( node.abs_path )
        # save the child nodes, they are cleared by load (and the
        # load routine is not recursive yet), so we'll need them
        # later.
        saved_child_nodes = list(node.child_nodes.values())

        # TODO: To be complete, we could add the properites of the node
        #       into the dictionary when calling load, that way we don't
        #       count on the current behaviour to not drop the properties.
        if node.phandle == -1:
            node.phandle = 0
        elif node.phandle > 0:
            # we need to generate a new phandle on a collision
            try:
                if self.__pnodes__[node.phandle]:
                    lopper.log._debug( f"node add: would duplicate phandle: {hex(node.phandle)} ({node.phandle})" )
                    new_phandle = self.phandle_gen()
                    node.phandle_set( new_phandle )
            except:
                pass

        node.load( { '__path__' : node.abs_path,
                     '__fdt_name__' : node.name,
                     '__fdt_phandle__' : node.phandle },
                   parent_path )

        lopper.log._debug( f"node add: {node.abs_path}, after load. depth is : {node.depth}"
                           f"         phandle: {node.phandle} tree: {node.tree}" )

        self.__nodes__[node.abs_path] = node

        # note: this is similar to the the tree.load() code, it should be
        #       consolidated
        if node.number >= 0:
            self.__nnodes__[node.number] = node
        if node.phandle > 0:
            # note: this should also have been done by node.load()
            #       and the phandle_set() that was called in case of
            #       a detected collision, but we assign it here to
            #       be sure and to mark that we consider it part of the
            #       tree at this point.
            self.__pnodes__[node.phandle] = node
        if node.label:
            # we should check if there's already a node at the label
            # value, and either warn, adjust or take some other appropriate
            # action
            try:
                if self.__lnodes__[node.label]:
                    node.label_set( node.label )
                    lopper.log._debug( f"node add: duplicate label, generated a new one: {node.label}" )
            except:
                pass

            self.__lnodes__[node.label] = node

        # Check to see if the node has any children. If it does, are they already in
        # our node dictionary ? If they aren't, it means we are not just adding one
        # node but a node + children.

        # we clear the node's child dict, since if they are new / valid, then
        # they'll be re-added to the dictionary with adjusted paths, etc.
        # saved_child_nodes = list(node.child_nodes.values())
        node.child_nodes = OrderedDict()
        for child in saved_child_nodes:
            try:
                existing_node = self.__nodes__[node.abs_path + child.name]
            except:
                existing_node = None

            if not existing_node:
                if self.__dbg__ > 2:
                    print ( f"[DBG+++]:     node add: adding child: {child.abs_path} ({[child]})")

                # this mainly adjusts the path, since it hasn't been sync'd yet.
                child.number = -1
                # Trying this ...
                child.abs_path = node.abs_path + "/" + child.name

                # in case the node has properties that were previously sync'd, we
                # need to resync them
                for p in child.__props__.values():
                    p.__pstate__ = "init"
                    p.__modified__ = True

                child.resolve()

                self.add( child, True )

                if self.__dbg__ > 2:
                    print ( f"[DBG+++]:     node add: child add complete: {child.abs_path} ({[child]})")

        # in case the node has properties that were previously sync'd, we
        # need to resync them
        # TODO: we can likely drop this with the dictionary scheme
        for p in node.__props__.values():
            p.__pstate__ = "init"
            p.__modified__ = True

        lopper.log._debug( f"node added: [{[node]}] {node.abs_path} ({node.label})" )
        if self.__dbg__ > 2:
            for p in node:
                lopper.log._debug( f"      property: {p.name} {p.value} (state:{p.__pstate__})" )

        # we can probably drop this by making the individual node sync's smarter and
        # more efficient when something doesn't need to be written
        #self.__must_sync__ = True
        self.__must_sync__ = False
        if dont_sync:
            lopper.log._debug( f"\n\n{self}: {sys._getframe(0).f_lineno}/{sys._getframe(0).f_code.co_name}: treewide sync inhibited" )
        else:
            lopper.log._debug( f"\n\n {self}: {sys._getframe(0).f_lineno}/{sys._getframe(0).f_code.co_name}: treewide sync started" )

            # Note: doesn't actually do anything except fixup states,
            #       but also does an export -> load, which means that
            #       node object values (addresses/ids) can change.
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
        # this is from the tree, the node has a confusingly similar
        # function and implementation.

        # gets you a list of all looper nodes under starting node
        all_kids = [ start_node ]
        for n in start_node.child_nodes.values():
            all_kids = all_kids + self.subnodes( n )

        all_matching_kids = []
        if node_regex:
            # we are filtering on a regex, drop nodes that don't match
            for n in all_kids:
                if re.search( node_regex, n.abs_path ):
                    all_matching_kids.append( n )
        else:
            all_matching_kids = all_kids

        kids_as_strings = ""
        for i in all_matching_kids:
            kids_as_strings += " " + i.abs_path

        return all_matching_kids


    def nodes( self, nodename, strict = False ):
        """Get nodes that match a given name or regex

        Looks for a node at a name/path, or nodes that match a regex.

        Args:
           nodename (string): node name or regex
           strict (boolean,optional): indicates that regex matches should be exact/strict

        Returns:
           list: a list all nodes that match the name or regex

        """
        matches = []
        if strict:
            nodename = "^" + nodename + "$"

        try:
            matches = [self.__nodes__[nodename]]
        except:
            # maybe it was a regex ?
            try:
                for n in self.__nodes__.keys():
                    if re.search( nodename, n ):
                        matches.append( self.__nodes__[n] )
            except:
                pass

        return matches


    def deref( self, phandle_or_label ):
        """Find a node by a phandle or label

        dereferences a phandle or label to find the target node.

        Args:
           phandle_or_label (int or string)

        Returns:
           LopperNode: the matching node if found, None otherwise

        """
        try:
            tgn = None
            trees_to_check = [ self ] + self._external_trees
            for t in [ self ] + self._external_trees:
                try:
                    if tgn:
                        break
                    tgn = t.pnode( phandle_or_label )
                    if tgn == None:
                        # if we couldn't find the target, maybe it is in
                        # as a string. So let's check that way.
                        tgn2 = t.nodes( phandle_or_label )
                        if not tgn2:
                            tgn2 = t.lnodes( re.escape(phandle_or_label) )

                        if tgn2:
                            tgn = tgn2[0]
                except:
                    pass
        except Exception as e:
            tgn = None

        return tgn

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


    def alias_node( self, alias ):
        """Find a node via an alias

        Safely (no exception raised) returns the node that can be found
        at a given alias.

        Args:
           alias (string): node alias to check

        Returns:
           node (LopperNode): the alias nodes if found, None otherwise
        """
        try:
            node = self.__aliases__[alias]
        except:
            node = None

        return node



    def lnodes( self, label, exact = True ):
        """Find nodes in a tree by label

        Safely (no exception raised) returns the node that can be found
        at a given label value.

        Args:
           label (string): node string  to check
           strict (boolean): flag indicating if exact or fuzzy matching

        Returns:
           list (LopperNode): the matching nodes if found, [] otherwise

        """
        nodes = []
        try:
            for l in self.__lnodes__.keys():
                if exact:
                    if re.search( "^" + label + "$", l ):
                        nodes.append( self.__lnodes__[l] )
                else:
                    if re.search( label, l ):
                        nodes.append( self.__lnodes__[l] )
        except:
            return nodes

        return nodes

    def cnodes( self, compatible_string ):
        """Returns the nodes in a tree that are compatible with the passed type

        Utility function to search a tree for nodes of a given "type"

        Args:
           compatible_string (string): compatibility string to match

        Returns:
           list (LopperNode): the matching nodes if found, [] otherwise

        """
        matching_nodes = []
        for n in self:
            try:
                compat_prop = n["compatible"]
                if compat_prop and compatible_string in compat_prop.value:
                    matching_nodes.append( n )
            except:
                pass

        return matching_nodes

    def addr_node(self, address):
        """Find a node in the tree based on an address

        This routine searches the tree for a node (device) that is
        at a given address. Only nodes with @ in their name are
        considered, since by the device tree spec, these are the
        required unit address.

        Note: the unit adress is only the starting point. Each
        identified node has its device translation performed (using
        the address() function). It is those translated addresses
        which are used to locate a target node (if one exists).

        Args:
          address (int): target translated address to match

        Returns:
          target node list (LopperNode): the matching node(s), empty otherwise
        """

        lopper.log._debug( f"addr_node {address}" )

        target_node = None

        # TODO: this may be better to calculate once, and then cache
        #       in a dictionary indexed by address, or add an
        #       "address" field to each node and consult it. But we
        #       would have to recalculate it on tree operations that
        #       modify properties that impact memory mapping.

        # gather the nodes with @ in their name
        address_nodes = []
        for n in self.__nodes__.values():
            if "@"  in n.name:
                address_nodes.append( n )

        # calculate all the addresses
        address_dict = {}
        for n in address_nodes:
            node_address = n.address()
            ## you are here. we shouldn't clobber existing (parent) addresses if we get a match from
            ## a child
            if node_address:
                lopper.log._debug( f"node {n.abs_path} has address: {hex(node_address)}" )
                try:
                    existing_addr_node = address_dict[hex(node_address)]
                    if existing_addr_node == n.parent:
                        lopper.log._debug( f"  parent node {n.parent} is already @ the addreess, {n} has the same range" )
                    else:
                        lopper.log._debug( f"  non parent node {n.parent} is already @ the addreess, {n} has the same range" )

                    # we add our node with the same address. This should be a child
                    # node with a parent with "ranges;" .. but we've logged which case
                    # it is above for later debug.
                    address_dict[hex(node_address)].append( n )
                except Exception as e:
                    address_dict[hex(node_address)] = [ n ]

        try:
            target_node = address_dict[address]
        except:
            target_node = None

        return target_node

    def exec_cmd( self, node, cmd, env = None, module_list=[], module_load_paths=[] ):
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
            __selected__ : the list of LopperNodes being processed
            node_name : the name of the node (as defined by the dts/dtb)
            node_number : the number of the node being processed

        The return value of the block is sent to the caller, so it can act
        accordingly.

        Args:
            node (LopperNode or string): starting node
            cmd (string): block of python code to execute
            env (dictionary,optional): values to make available as
                                       variables to the code block
            module_list (list,optional): list of assists to load before
                                         running the code block
            module_load_paths (list,optional): additional load paths to use
                                               when loading modules

        Returns:
            Return value from the execution of the code block

        """
        # only sync if required
        self.sync( None, True )

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
        safe_dict['fdt'] = None
        safe_dict['verbose'] = self.__dbg__
        safe_dict['tree'] = self

        lopper.log._debug( f"filter: base safe dict: {safe_dict}"
                           f"filter: node: {node}" )

        # build up the device tree node path
        # node_name = node_prefix + n
        node_name = n.abs_path
        node_number = n.number
        prop_list = n.__props__

        # add any needed builtins back in
        safe_dict['node'] = n
        safe_dict['node_number'] = node_number
        safe_dict['node_name' ] = node_name
        safe_dict['__selected__'] = self.__selected__

        if env:
            for e in env:
                safe_dict[e] = env[e]

        if module_list:
            mod_load = "assist_dir = os.path.dirname(os.path.realpath(__file__)) + '/assists/'\n"
            mod_load += "sys.path.append(assist_dir)\n"
            for m in module_load_paths:
                mod_load += f"sys.path.append('{m}')\n"
            mod_load += "import importlib\n"
        else:
            mod_load = ""

        for m in module_list:
            mod_load += f"{m} = importlib.import_module( '.{m}', package='lopper.assists' )\n"

        tc = cmd

        # we wrap the test command to control the ins and outs
        __nret = False
        # indent everything, its going in a function
        tc_indented = textwrap.indent( tc, '    ' )
        # define the function, add the body, call the function and grab the return value
        tc_full_block = mod_load + "def __node_test_block():\n" + tc_indented + "\n__nret = __node_test_block()"

        lopper.log._debug( f"node exec cmd:\n{tc_full_block}" )

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
            lopper.log._warning( f"Exception ({e}) raised by code block:\n{tc_full_block}")
            os._exit(1)

        lopper.log._debug( f"return code was: {m['__nret']}" )

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
        # only sync if required
        self.sync( fdt, True )

        lopper.log._debug( f"filtering nodes root: {node_prefix}" )

        if not node_prefix:
            node_prefix = "/"

        try:
            start_node = self[node_prefix]
            node_list = start_node.subnodes()
        except:
            start_node = None
            node_list = []
            lopper.log._error( f"no nodes found that match prefix {node_prefix}" )

        if verbose > 1:
            lopper.log._debug( f"filter: node list: " )
            for nn in node_list:
                lopper.log._debug( f"    {nn.abs_path}" )
            lopper.log._debug( f"" )

        for n in node_list:
            lopper.log._debug( f"filter node cmd:\n{test_cmd}" )

            test_cmd_result = self.exec_cmd( n, test_cmd )

            lopper.log._debug( f"return code was: {test_cmd_result}" )

            # did the block set the return variable to True ?
            if test_cmd_result:
                if action == LopperAction.DELETE:
                    lopper.log._info( f"deleting node {n.abs_path}" )
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
        # only sync if required
        self.sync( None, True )

        if self.__dbg__ > 4:
            lopper.log._debug( "LopperTree exec start" )

        last_children = []
        chain_close_dict = {}
        for n in self:
            if self.__dbg__ > 4:
                lopper.log._debug( f"node: {n.name}:{n.number} [{n.phandle}] parent: {n.parent} children: {n.child_nodes}" )

            if n.number == 0 or n.abs_path == "/":
                if self.start_tree_cb:
                    self.start_tree_cb( n )

            if n.child_nodes:
                last_child = list(n.child_nodes.values())
                last_child = last_child[-1]

                # add the last child in our list, we'll use it to know when to end a node.
                # we could remove these on the close, if memory becomes an issue
                if self.__dbg__ > 4:
                    lopper.log._debug( f"node {n.number} ({n.abs_path}) has last child {last_child}" )

                if not n.abs_path in last_children:
                    last_children.append( n.abs_path )

                last_children.append( last_child.abs_path )
                chain_close_dict[last_child.abs_path] = n
                if self.__dbg__ > 4:
                    lopper.log._debug( f"mapped chain close {n.number} ({n.abs_path}) to {last_child}" )

            if self.start_node_cb:
                self.start_node_cb( n )

            # node stuff
            # i.e. iterate the properties and print them
            for p in n:
                if self.property_cb:
                    self.property_cb( p )

            # check to see if we are closing the node.
            #if last_children and n.number == last_children[-1]:
            if last_children and n.abs_path == last_children[-1]:
                if self.__dbg__ > 4:
                    lopper.log._debug( f"{n.abs_path} matches last {last_children} ({last_children[-1]})" )

                # we are closing!
                if self.end_node_cb:
                    self.end_node_cb( n )

                # pop the last child
                del last_children[-1]

                cc_close = n.abs_path
                to_close = n.abs_path
                while cc_close in list(chain_close_dict.keys()):
                    if self.__dbg__ > 4:
                        lopper.log._debug( f"chain close" )

                    to_close = chain_close_dict[cc_close]

                    if self.__dbg__ > 4:
                        lopper.log._debug( f"would close {to_close.abs_path} {to_close}" )

                    if last_children[-1] == to_close.abs_path:
                        del last_children[-1]
                        del chain_close_dict[cc_close]
                    else:
                        lopper.log._warning( f"tree exec: inconsistency found walking tree" )

                    if self.end_node_cb:
                        self.end_node_cb( to_close )

                    cc_close = to_close.abs_path
            elif not n.child_nodes:
                # we are closing!
                if self.end_node_cb:
                    if self.__dbg__ > 4:
                        lopper.log._debug( f"no children, closing node" )
                    self.end_node_cb( n )

        if self.end_tree_cb:
            self.end_tree_cb( -1 )

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

    def load(self, dct = None ):
        """load a tree

        Loads the details around the nodes of a tree, and completes values that
        are not possible at initialization time.

        In particular, it updates the path, node and phandle ordered
        dictionaries to reflect the dictionary. This is often done after a node
        is added to ensure that iterations will see the new node in tree order,
        versus added order.

        Args:
           dct (Dictionary): dictionary from a lopper.fdt export, or a tree export

        Returns:
           Nothing

        """
        if dct:
            self.dct = dct
        else:
            dct = self.dct

        # take the dictionary format, which is a series of nested dicts
        # representing nodes and properties. We'd rather not recurse to do our
        # processing below, so we unroll the recursion into an ordered list of
        # nodes and properties.

        # we have a list of: containing dict, value, parent
        dwalk = [ [dct,dct,None]  ]
        node_ordered_list = []
        while dwalk:
            firstitem = dwalk.pop()
            if type(firstitem[1]) is OrderedDict: # or type(firstitem[1]) is dict:
                node_ordered_list.append( [firstitem[1], firstitem[0]] )
                for item,value in reversed(firstitem[1].items()):
                    dwalk.append([firstitem[1],value,firstitem[0]])
            elif type(firstitem[1]) is dict:
                node_ordered_list.append( [firstitem[1], firstitem[0]] )
                for item,value in firstitem[1].items():
                    dwalk.append([firstitem[1],value,firstitem[0]])
            else:
                pass

        # We are checking the __must_sync__ flag. Since this routine will throw
        # away unsync'd nodes, due to the fact that it reads from the FDT
        # and re-establishes nodes based on that. We can also check for
        # nodes in the "init" state, or with node number -1 and save them .. but
        # only if this check and exit starts catching valid use cases we can't fix
        if self.__must_sync__:
            lopper.log._error( f"tree should be sync'd before loading. Some nodes may be lost" )
            lopper.log._debug( f"     caller: {sys._getframe(1).f_lineno}/{sys._getframe(1).f_code.co_name}" )
            sys.exit(1)

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
            # nodes. indexed by alias
            self.__aliases__ = OrderedDict()

            lopper.log._debug( f"tree load start: {self}" )

            for n_item in node_ordered_list:
                node_in = n_item[0]
                node_in_parent = n_item[1]
                node_path = node_in['__path__']
                abs_path = node_path
                nn =  node_in['__fdt_number__']
                try:
                    # we try and re-use the node if possible, since that keeps
                    # old references valid for adding more properties, etc
                    node = nodes_saved[abs_path]
                except:
                    # node didn't exist before, create it as something new
                    node = LopperNode( nn, "", self )
                    node.indent_char = self.indent_char

                # special node processing
                if abs_path == "/memreserve":
                    lopper.log._debug( f"tree load: memreserve found: {node_in['__memreserve__']}" )
                    self.__memreserve__ = node_in["__memreserve__"]
                    continue

                node.__dbg__ = self.__dbg__

                # resolve the details against the dictionary
                node.load( node_in, node_in_parent['__path__'] )

                try:
                    node_check = self.__nodes__[node.abs_path]
                    if node_check:
                        lopper.log._error( f"tree inconsistency found, two nodes with the same path ({node_check.abs_path})" )
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

            for node_abs_path in nodes_saved:
                # invalidate nodes, in case someone is holding a reference
                # to them
                try:
                    state = self.__nodes__[node_abs_path]
                except:
                    # the node didn't get copied over, invalidate the state
                    nodes_saved[node_abs_path].__nstate__ = "*invalid*"

            # setup aliases
            try:
                alias_node = self.__nodes__["/aliases"]
                lopper.log._debug( f"aliases node found, registering aliases" )
                for alias in alias_node:
                    lopper.log._debug( f"alias: {alias.name} {alias.value[0]}" )
                    try:
                        alias_target = self.__nodes__[ alias.value[0] ]
                    except Exception as e:
                        alias_target = None

                        # TODO: this should be moved to a generic lookup routine so
                        #       it can be used everywhere for label path based lookups
                        # was the first component a label ?
                        components = alias.value[0].split('/')
                        try:
                            base_component = components[1]
                        except:
                            base_component = None

                        label_node = None
                        if base_component:
                            try:
                                label_node = self.__lnodes__[base_component]
                            except:
                                pass

                        if label_node:
                            label_chunk, _, rest = alias.value[0].partition( base_component )
                            label_adjusted_path = label_node.abs_path + rest
                            lopper.log._debug( f"alias: looking for node via label path: {label_adjusted_path}" )
                            try:
                                alias_target = self.__nodes__[ label_adjusted_path ]
                            except:
                                alias_target = None

                    if alias_target:
                        lopper.log._debug( f"alias target node found: {alias_target.abs_path}" )
                        self.__aliases__[alias.name] = alias_target
            except:
                pass
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

            child_nodes = self.subnodes( self.__nodes__[ "/"] )
            self.__node_iter__ = iter( child_nodes )

            if self.__current_node__ == "/" and self.__start_node__ == "/":
                # just get the first node out of the default iterator
                node = next(self.__node_iter__)
            elif self.__start_node__ != "/":
                # this is a starting node, so we fast forward and then use
                # the default iterator
                node = next(self.__node_iter__)
                while node and node.abs_path != self.__start_node__:
                    node = next(self.__node_iter__)
            else:
                # non-zero current_node, that means we'll do a custom iteration
                # of only the nodes that are underneath of the set current_node
                child_nodes = self.subnodes( self.__nodes__[self.__current_node__] )
                self.__node_iter__ = iter( child_nodes )
                node = next(self.__node_iter__)
        else:
            if self.depth_first:
                try:
                    node = next(self.__node_iter__)
                except StopIteration:
                    # reset for the next call
                    self.__current_node__ = 0
                    self.__new_iteration__ = True
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
    def __init__( self, snapshot = False, output=sys.stdout, debug=0 ):
        # init the base walker.
        super().__init__( snapshot )

        self.output = output
        try:
            if output != sys.stdout:
                self.output = open( output, "w")
        except Exception as e:
            lopper.log._warning( f"cannot open {output} for writing, using stdout: {e}" )
            self.output = sys.stdout

        self.__dbg__ = debug

    def exec(self):
        """ Excute the priting of a tree

        This keeps compatbility with the original LopperTreePrinter
        implementation that used callback to print a tree. They were
        triggered when exec() was called on the tree.

        We no longer use those callbacks, but we implement exec()
        so that existing code need not change.

        Args:
            None

        Returns:
            Nothing
        """

        # save the current / start nodes, since they'll be
        # changed/reset by the reolve
        start_save = self.__start_node__
        current_save = self.__current_node__

        super().resolve()

        # restore them
        self.__start_node__ = start_save
        self.__current_node__ = current_save

        if self.__start_node__ != "/":
            self.__nodes__[self.__start_node__].print(self.output)
        elif self.__current_node__ != "/":
            self.__nodes__[self.__current_node__].print( self.output )
        else:
            self.print( self.output )

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
                self.output = open( output_file, "w" )
            except Exception as e:
                lopper.log._warning( f"could not open {output_file} as output: {e}" )
        else:
            self.output = output_file
