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
import json

from lopper.fmt import LopperFmt

# must be set to the Lopper class to call
global Lopper

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

        self.abs_path = ""

        if value == None:
            self.value = []
        else:
            # we want to avoid the overriden __setattr__ below
            self.__dict__["value"] = value


    def __deepcopy__(self, memodict={}):
        """ Create a deep copy of a property

        Properties have links to nodes, so we need to ensure that they are
        cleared as part of a deep copy.

        """
        if self.__dbg__ > 1:
            print( "[DBG++]: property '%s' deepcopy start: %s" % (self.name,[self]) )
            print( "         value type: %s value len: %s value: %s" % (type(self.value),len(self.value),self.value ))

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
            print( "[DBG++]: property deep copy done: %s (%s)(%s)" % ([self],type(new_instance.value),new_instance.value) )

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
            yield 'value', loaded_j
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
            print( "[DBG++]: property compare (%s) vs (%s)" % (self,other_prop) )

        ret_val = False
        invert_check  = ""
        if len(self.value) == 1:
            # single comparison value
            lop_compare_value = self.value[0]

            if len( other_prop.value ) == 1:
                # check if this is actually a phandle property
                idx, pfields = self.phandle_params()
                # idx2, pfields2 = other_prop.phandle_params()
                if pfields > 0:
                    if self.ptype == LopperFmt.STRING:
                        # check for "&" to designate that it is a phandle, if it isn't
                        # there, throw an error. If it is there, remove it, since we
                        # don't use it for the lookup.
                        if re.search( '&', lop_compare_value ):
                            lop_compare_value = re.sub( '&', '', lop_compare_value )

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
                    constructed_condition = "{0} re.search(\"{1}\",'{2}')".format(invert_check,lop_compare_value,tgt_node_compare_value)
                elif other_prop.ptype == LopperFmt.UINT32: # type(lop_compare_value) == int:
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
                    if self.ptype == LopperFmt.STRING: # type(lop_compare_value) == str:
                        constructed_condition = "{0} re.search(\"{1}\",\"{2}\")".format(invert_check,lop_compare_value,tgt_node_compare_value)

                    elif self.ptype == LopperFmt.UINT32: # type(lop_compare_value) == int:
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
                    if self.ptype == LopperFmt.STRING: # type(lop_compare_value) == str:
                        constructed_condition = "{0} re.search(\"{1}\",\"{2}\")".format(invert_check,lop_compare_value,tgt_node_compare_value)

                    elif self.ptype == LopperFmt.UINT32: # type(lop_compare_value) == int:
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
                if re.search( '^#.*', f ):
                    try:
                        field_val = self.node.__props__[f].value[0]
                    except Exception as e:
                        field_val = 0

                    if not field_val:
                        field_val = 1

                    phandle_field_count = phandle_field_count + field_val
                elif re.search( '^phandle', f ):
                    phandle_field_count = phandle_field_count + 1
                    phandle_idx = phandle_field_count

                    # if a phandle field is of the format "phandle:<#property>", then
                    # we need to dereference the phandle, and get the value of #property
                    # to figure out the indexes.
                    derefs = f.split(':')
                    if len(derefs) == 2:
                        # we have to deference the phandle, and look at the property
                        # specified to know the count
                        try:
                            phandle_tgt_val = self.value[phandle_field_count - 1]
                            tgn = self.node.tree.pnode( phandle_tgt_val )
                            if tgn == None:
                                # if we couldn't find the target, maybe it is in
                                # as a string. So let's check that way.
                                tgn2 = self.node.tree.nodes( phandle_tgt_val )
                                if not tgn2:
                                    tgn2 = self.node.tree.lnodes( phandle_tgt_val )

                                if tgn2:
                                    tgn = tgn2[0]

                            if tgn:
                                try:
                                    cell_count = tgn[derefs[1]].value[0]
                                except:
                                    cell_count = 0

                                phandle_field_count = phandle_field_count + cell_count
                        except:
                            # either we had no value, or something else wasn't defined
                            # yet, so we continue on with the initial values set at
                            # the top (i.e. treat it just as a non dereferenced phandle
                            pass

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

        if self.__dbg__ > 2:
            print( "[DBG+++]: property sync: node: %s [%s], name: %s value: %s" %
                   ([self.node],self.node.number,self.name,self.value))

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

        idx, pfields = self.phandle_params()
        if idx == 0:
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

        phandle_idxs = list(range(1,len(prop_val) + 1))
        phandle_idxs = phandle_idxs[idx - 1::pfields]

        ctx_fields = []

        element_count = 1
        element_total = len(prop_val)

        record_list = []
        for i in prop_val:
            base = 10
            if re.search( "0x", i ):
                base = 16
            try:
                i_as_int = int(i,base)
                i = i_as_int
            except:
                pass

            record_list.append( i )
            if element_count % pfields == 0:
                ctx_fields.append( record_list )
                record_list = []

            if element_count in phandle_idxs:
                try:
                    lnode = self.node.tree.pnode( i )
                    if lnode:
                        phandle_targets.append( lnode )
                    else:
                        # was it a label ? If it was converted to an int above,
                        # we'll throw an exception and catch it below for proper
                        # processing. If it is a string, we'll try the lookup.
                        lnode = self.node.tree.lnodes( re.escape(i) )
                        if lnode:
                            phandle_targets.extend( lnode )
                        else:
                            phandle_targets.append( "#invalid" )
                except Exception as e:
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
        if self.node.indent_char == ' ':
            indent = (self.node.depth * 8) + 8
        else:
            indent = (self.node.depth) + 1

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
                outstring = re.sub( '\n\s*', '\n' + dstring, outstring, 0, re.MULTILINE | re.DOTALL)

        if outstring:
            print(outstring.rjust(len(outstring)+indent, self.node.indent_char), file=output)

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
                        if re.search( "0x", p ):
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
                if re.search( "0x", self.value ):
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
             None

        Returns:
           Nothing
        """
        outstring = "{0} = {1};".format( self.name, self.value )

        prop_val = self.value

        if self.node:
            self.abs_path = self.node.abs_path + "/" + self.name
        else:
            self.abs_path = self.name

        if re.search( "lopper-comment.*", self.name ):
            prop_type = "comment"
        elif re.search( "lopper-preamble", self.name ):
            prop_type = "preamble"
        elif re.search( "lopper-label.*", self.name ):
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

        if self.__dbg__ > 1:
            print( "[DBG+]: strict: %s property [%s] resolve: %s val: %s" % (strict,prop_type,self.name,self.value) )

        self.pclass = prop_type

        phandle_idx, phandle_field_count = self.phandle_params()
        phandle_tgts = self.resolve_phandles( True )

        if phandle_field_count and len(prop_val) % phandle_field_count != 0:
            # if the property values and the expected field counts do not match
            # zero phandles out to avoid processing below.
            phandle_idx = 0
            phandle_field_count = 0
            phandle_tgts = []

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
                # outstring = ""
                outstring = "{0};".format( self.name )
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
                if phandle_idx != 0:
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
                    if re.search( "0x", prop_val[0] ):
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
                drop_record = False
                drop_all = False

                formatted_records = []
                if phandle_field_count:
                    records_to_iterate = [prop_val[i:i + phandle_field_count] for i in range(0, len(prop_val), phandle_field_count)]
                    for rnum,r in enumerate(records_to_iterate):
                        try:
                            phandle_resolution = phandle_tgts.pop(0)
                        except:
                            phandle_resolution = "#invalid"

                        if phandle_resolution == "#invalid":
                            # drop the record, if strict
                            if not strict:
                                if type(r[phandle_idx - 1]) == str:
                                    phandle_tgt_name = r[phandle_idx - 1]
                                else:
                                    phandle_tgt_name = "invalid_phandle"
                            else:
                                # strict and an invalid phandle, jump to the next record
                                continue
                        else:
                            phandle_tgt_name = phandle_resolution.label
                            if not phandle_tgt_name:
                                phandle_tgt_name = Lopper.phandle_safe_name( phandle_resolution.name )

                        if self.binary:
                            formatted_records.append( "[" )
                        else:
                            # we have to open with a '<', if this is a list of numbers
                            formatted_records.append( "<" )

                        # keep the record
                        for i,element in enumerate(r):
                            if i == 0:
                                # first item, we don't want a leading space
                                pass
                            else:
                                formatted_records.append( " " )

                            phandle_replacement_flag = False
                            try:
                                if i == phandle_idx - 1:
                                    phandle_replacement_flag = True
                            except:
                                pass

                            if phandle_replacement_flag:
                                formatted_records.append( "&{0}".format( phandle_tgt_name ) )
                            else:
                                if self.binary:
                                    formatted_records.append( "{0:02X}".format( element ) )
                                else:
                                    try:
                                        formatted_records.append( "{0}".format( hex(element) ) )
                                    except:
                                        formatted_records.append( "{0}".format( element ) )
                        if self.binary:
                            formatted_records.append( "]" )
                        else:
                            formatted_records.append( ">" )

                        # if we aren't the last item, we continue with a ,
                        if rnum != len(records_to_iterate) - 1:
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
                                formatted_records.append( "{0:02X}".format( i ) )
                            else:
                                try:
                                    formatted_records.append( "{0}".format( hex(i) ) )
                                except:
                                    formatted_records.append( "{0}".format( i ) )
                        else:
                            formatted_records.append( "\"{0}\"".format( i ) )

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
            outstring = "{0} = \"{1}\";".format( self.name, prop_val )

        if not self.ptype:
            self.ptype = self.property_type_guess()
            if self.__dbg__ > 3:
                print( "[NOTE]: guessing type for: %s [%s]" % (self.name,self.ptype) )

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

        self.abs_path = abspath

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

        if self.__dbg__ > 1:
            print( "[DBG++]: node deepcopy start: %s" % [self.abs_path] )

        new_instance = LopperNode()

        # if we blindly want everything, we'd do this update. But it
        # is easier to pick out the properties that we do want, versus
        # copying and undoing.
        #      new_instance.__dict__.update(self.__dict__)

        # we loop instead of the copy below, since we want to preserve the order
        #      new_instance.__props__ = copy.deepcopy( self.__props__, memodict )
        new_instance.__props__ = OrderedDict()
        for p in reversed(self.__props__):
            new_instance[p] = copy.deepcopy( self.__props__[p], memodict )
            new_instance[p].node = new_instance

        new_instance.name = copy.deepcopy( self.name, memodict )
        new_instance.number = -1 # copy.deepcopy( self.number, memodict )
        new_instance.depth = copy.deepcopy( self.depth, memodict )
        new_instance.label = copy.deepcopy( self.label, memodict )
        new_instance.type = copy.deepcopy( self.type, memodict )
        new_instance.abs_path = copy.deepcopy( self.abs_path, memodict )
        new_instance.indent_char = self.indent_char

        new_instance._source = self._source

        new_instance.tree = None

        new_instance.child_nodes = OrderedDict()
        for c in reversed(self.child_nodes.values()):
            new_instance.child_nodes[c.abs_path] = copy.deepcopy( c, memodict )
            new_instance.child_nodes[c.abs_path].number = -1
            new_instance.child_nodes[c.abs_path].parent = new_instance

        if self.__dbg__ > 1:
            print( "[DBG++]: deep copy done: %s" % [self] )

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

    def print( self, output=None, strict=None ):
        """print a node

        Print a node to the passed output stream. If it isn't passed, then
        the containg tree's output is used. If the tree has no output, stdout
        is the final fallback.

        The node  will be indented to match the depth of a node
        in a tree.

        Args:
           output (optional, output stream).

        Returns:
           Nothing

        """
        if not output:
            try:
                output = self.tree.output
            except:
                output = sys.stdout

        if self.indent_char == ' ':
            indent = self.depth * 8
        else:
            indent = self.depth

        nodename = self.name

        # we test for None, not "if strict", since we don't want an
        # explicitly passed "False" to not take us into the check.
        resolve_props = False
        if strict != None:
            if self.tree.strict != strict:
                resolve_props = True

        if self.abs_path != "/":
            plabel = ""
            try:
                if n['lopper-label.*']:
                    plabel = n['lopper-label.*'].value[0]
            except:
                plabel = self.label

            if self.phandle != 0:
                if plabel:
                    outstring = plabel + ": " + nodename + " {"
                else:
                    outstring = Lopper.phandle_safe_name( nodename ) + ": " + nodename + " {"
            else:
                if plabel:
                    outstring = plabel + ": " + nodename + " {"
                else:
                    outstring = nodename + " {"

            print( "", file=output )
            print(outstring.rjust(len(outstring)+indent, self.indent_char), file=output )
        else:
            # root node
            # peek ahead to handle the preamble
            for p in self:
                if p.pclass == "preamble":
                    print( "%s" % p, file=output )

            print( "/dts-v1/;\n\n/ {", file=output )

        # now the properties
        for p in self:
            if resolve_props:
                p.resolve( strict )

            p.print( output )

        # child nodes
        for cn in self.child_nodes.values():
            cn.print( output )

        # end the node
        outstring = "};"
        print(outstring.rjust(len(outstring)+indent, self.indent_char), file=output)

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
            print( "[WARNING]: node export: unresolved node, not syncing" )
        else:
            dct['__fdt_number__'] = self.number
            dct['__fdt_name__'] = self.name
            dct['__fdt_phandle__'] = self.phandle

            last_chunk_of_path = os.path.basename( self.abs_path )
            if last_chunk_of_path != self.name:
                if self.__dbg__ > 1:
                    print( "[DBG+]: node export: name change detected, adjusting path" )
                self.abs_path = os.path.dirname( self.abs_path ) + "/" + self.name

            if self.parent:
                parent_chunk_of_path = os.path.dirname( self.abs_path )
                if parent_chunk_of_path != self.parent.abs_path:
                    if self.__dbg__ > 1:
                        print( "[DBG+]: node export: path component change detected, adjusting path" )
                    self.abs_path = self.parent.abs_path + "/" + self.name

            self.abs_path = self.abs_path.replace( "//", "/" )
            if self.__dbg__ > 1:
                print( "[DBG++]: node export: start: [%s][%s]" % (self.number,self.abs_path))

            dct['__path__'] = self.abs_path
            dct['__nodesrc__'] = self._source

            # property export
            for p in self.__props__.values():
                dct[p.name] = p.value
                if p.binary:
                    dct['__{}_type__'.format(p.name)] = LopperFmt.UINT8
                else:
                    dct['__{}_type__'.format(p.name)] = p.ptype

                dct['__{}_pclass__'.format(p.name)] = p.pclass

                if self.__dbg__ > 2:
                    print( "       node export: [%s] property: %s (state:%s)(type:%s)" %
                           (p.ptype,p.name,p.__pstate__,dct['__{}_type__'.format(p.name)]) )

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
            print( "[WARNING]: node sync: unresolved node, not syncing" )
        else:
            if self.__dbg__ > 1:
                print( "[DBG++]: node sync start: [%s][%s]" % (self.number,self.abs_path))

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
            if self.__dbg__ > 2:
                print( "[DBG++]: node %s adding property: %s" % (self.abs_path,prop.name) )

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

            if self.__dbg__ > 2:
                print( "[DBG++]: node %s added Node: %s" % (self.abs_path,node.name) )

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

            if self.__dbg__ > 2:
                print( "[DBG++]: node load start [%s][%s]: %s" % (self,self.number,self.abs_path))

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

                depth = len(re.findall( '/', self.abs_path ))
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
                if re.search( "^__", prop ) or prop.startswith( '/' ):
                    # internal property, skip
                    continue

                dtype = LopperFmt.UINT8
                try:
                    # see if we got a type hint as part of the input dictionary
                    dtype = dct['__{}_type__'.format(prop)]
                except Exception as e:
                    pass

                if self.__dbg__ > 3:
                    print( "[DBG++] node [%s] load: [%s] prop: %s val: %s" % (self,dtype, prop, prop_val ))

                try:
                    # see if we got a property class as part of the input dictionary
                    pclass = dct['__{}_pclass__'.format(prop)]
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
                        if re.search( "phandle-desc.*", p ):
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
                            if self.__dbg__ > 3:
                                print( "[DBG+++]: existing prop detected (%s), updating value: %s -> %s" %
                                       (self.__props__[prop].name,self.__props__[prop].value,prop_val))
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

            # 3rd pass: did we have any added, but not sync'd properites. They need
            #           to be brought back into the main property dictionary.
            for p in saved_props:
                if saved_props[p].__pstate__ != "deleted":
                    self.__props__[p] = saved_props[p]
                    self.__props__[p].node = self

            if not self.type:
                self.type = [ "" ]

            self.__nstate__ = "resolved"
            self.__modified__ = False

        if self.__dbg__ > 2:
            print( "[DGB++]: node resolution end: %s" % self)

    def resolve( self, fdt = None ):
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
            print( "[DBG++]: node resolution start [%s][%s]: %s" % (self,self.number,self.abs_path))

        ## This may be converted to a dictionary export -> call to lopper fdt
        ## to do a partial sync. But for now, it is just changing the state as
        ## the new load() function takes care of these details.

        ## We also may use this as recursive subnode resolve() call, so we
        ## can apply changes to a nodes properties and all subnode properties

        if self.abs_path == "/":
            self.depth = 0
        else:
            self.depth = len(re.findall( '/', self.abs_path ))

        if self.__dbg__ > 2:
            print( "[DBG++]: node resolve: calculating depth %s for: %s" % (self.abs_path, self.depth))

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

        self.dct = None

        # type
        self.depth_first = depth_first

        self.strict = True
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
        if self.__dbg__ > 2:
            print( "[DBG] tree export start: %s" % start_path )

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
            dct[n.abs_path] = self.export(n.abs_path)

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

        self["/"].print( output )

    def resolve( self ):
        # walk each node, and individually resolve
        for n in self:
            n.resolve()
            for p in n:
                p.resolve()

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
                if self.__dbg__ > 2:
                    print( "[DBG+++]: not syncing, since __must_sync__ is not set" )
                return


        if self.__dbg__ > 2:
            print( "[DBG++][%s]: tree sync start: %s" % (fdt,self) )

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

        if self.__dbg__ > 2:
            print( "[DBG++][%s]: tree sync end: %s" % (fdt,self) )

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

    def delete( self, node, delete_from_parent = True ):
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

        if n.__nstate__ == "resolved" and self.__must_sync__ == False:
            if self.__dbg__ > 1:
                print( "[DBG+]: %s deleting [%s] node %s" % (self, [n], n.abs_path))

            if n.child_nodes:
                for cn_path,cn in list(n.child_nodes.items()):
                    self.delete( cn, False )

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
                    del n.parent.child_nodes[n.abs_path]
                except Exception as e:
                    print( "[WARNING]: tree inconsistency. could not delete %s from %s" % (n.abs_path,n.parent.abs_path))

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

        if self.__dbg__ > 2:
            print( "[DBG+++]: tree: node add: [%s] %s (%s)(%s)" % (node.name,[ node ],node.abs_path,node.number) )

        node_full_path = node.abs_path

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
                if self.__dbg__ > 2:
                    print( "[WARNING]: add: node: %s already exists" % node.abs_path )
                return self
            else:
                if self.__dbg__ > 2:
                    print( "[INFO]: add: node: %s exists, merging properties" % node.abs_path )
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
        node.load( { '__path__' : node.abs_path,
                     '__fdt_name__' : node.name,
                     '__fdt_phandle__' : 0 },
                   parent_path )

        if self.__dbg__ > 2:
            print( "[DBG++]: node add: %s, after load. depth is :%s" % (node.abs_path,node.depth ))

        self.__nodes__[node.abs_path] = node

        # note: this is similar to the the tree.load() code, it should be
        #       consolidated
        if node.number >= 0:
            self.__nnodes__[node.number] = node
        if node.phandle > 0:
            self.__pnodes__[node.phandle] = node
        if node.label:
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
                    print ( "[DBG+++]:     node add: adding child: %s (%s)" % (child.abs_path,[child]))

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
                    print ( "[DBG+++]:     node add: child add complete: %s (%s)" % (child.abs_path,[child]))

        # in case the node has properties that were previously sync'd, we
        # need to resync them
        # TODO: we can likely drop this with the dictionary scheme
        for p in node.__props__.values():
            p.__pstate__ = "init"
            p.__modified__ = True

        if self.__dbg__ > 1:
            print( "[DBG+] node added: [%s] %s" % ([node],node.abs_path) )
            if self.__dbg__ > 2:
                for p in node:
                    print( "[DBG++]      property: %s %s (state:%s)" % (p.name,p.value,p.__pstate__) )

        # we can probably drop this by making the individual node sync's smarter and
        # more efficient when something doesn't need to be written
        #self.__must_sync__ = True
        self.__must_sync__ = False
        if dont_sync:
            if self.__dbg__ > 0:
                print( "\n\n[DBG]: %s: %s/%s: treewide sync inhibited" %
                       ( self, sys._getframe(0).f_lineno, sys._getframe(0).f_code.co_name ) )
        else:
            if self.__dbg__ > 0:
                print( "\n\n[DBG]: %s: %s/%s: treewide sync started" %
                       ( self, sys._getframe(0).f_lineno, sys._getframe(0).f_code.co_name ) )

            # Note: doesn't actually do anything except fixup states.
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
            try:
                for n in self.__nodes__.keys():
                    if re.search( nodename, n ):
                        matches.append( self.__nodes__[n] )
            except:
                pass

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
        safe_dict['__selected__'] = self.__selected__

        if env:
            for e in env:
                safe_dict[e] = env[e]

        if module_list:
            mod_load = "assist_dir = os.path.dirname(os.path.realpath(__file__)) + '/assists/'\n"
            mod_load += "sys.path.append(assist_dir)\n"
            for m in module_load_paths:
                mod_load += "sys.path.append('{}')\n".format( m )
            mod_load += "import importlib\n"
        else:
            mod_load = ""

        for m in module_list:
            mod_load += "{} = importlib.import_module( '.{}', package='lopper.assists' )\n".format(m,m)

        tc = cmd

        # we wrap the test command to control the ins and outs
        __nret = False
        # indent everything, its going in a function
        tc_indented = textwrap.indent( tc, '    ' )
        # define the function, add the body, call the function and grab the return value
        tc_full_block = mod_load + "def __node_test_block():\n" + tc_indented + "\n__nret = __node_test_block()"

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
            print("[WARNING]: Exception (%s) raised by code block:\n%s" % (e,tc_full_block))
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
        # only sync if required
        self.sync( fdt, True )

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
                print( "    %s" % nn.abs_path, end="  " )
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
        # only sync if required
        self.sync( None, True )

        if self.__dbg__ > 4:
            print( "[DBG++++]: LopperTree exec start" )

        last_children = []
        chain_close_dict = {}
        for n in self:
            if self.__dbg__ > 4:
                print( "[DBG++++]: node: %s:%s [%s] parent: %s children: %s" % (n.name, n.number, n.phandle, n.parent, n.child_nodes))

            if n.number == 0 or n.abs_path == "/":
                if self.start_tree_cb:
                    self.start_tree_cb( n )

            if n.child_nodes:
                last_child = list(n.child_nodes.values())
                last_child = last_child[-1]

                # add the last child in our list, we'll use it to know when to end a node.
                # we could remove these on the close, if memory becomes an issue
                if self.__dbg__ > 4:
                    print( "[DBG++++]: node %s (%s) has last child %s" % (n.number,n.abs_path,last_child))

                if not n.abs_path in last_children:
                    last_children.append( n.abs_path )

                last_children.append( last_child.abs_path )
                chain_close_dict[last_child.abs_path] = n
                if self.__dbg__ > 4:
                    print( "[DBG++++]: mapped chain close %s (%s) to %s" % (n.number,n.abs_path,last_child))

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
                    print( "[DBG++++]: %s matches last %s (%s)" % (n.abs_path, last_children, last_children[-1] ))

                # we are closing!
                if self.end_node_cb:
                    self.end_node_cb( n )

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
                        print( "[WARNING]: tree exec: inconsistency found walking tree" )

                    if self.end_node_cb:
                        self.end_node_cb( to_close )

                    cc_close = to_close.abs_path
            elif not n.child_nodes:
                # we are closing!
                if self.end_node_cb:
                    if self.__dbg__ > 4:
                        print( "[DBG++++]: no children, closing node" )
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
            print( "[ERROR]: tree should be sync'd before loading. Some nodes may be lost" )
            if self.__dbg__ > 2:
                print( "[DBG++]:     caller: %s/%s" %( sys._getframe(1).f_lineno, sys._getframe(1).f_code.co_name ) )
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

            if self.__dbg__ > 2:
                print( "[DGB+]: tree load start: %s" % self )

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

                node.__dbg__ = self.__dbg__

                # resolve the details against the dictionary
                node.load( node_in, node_in_parent['__path__'] )

                try:
                    node_check = self.__nodes__[node.abs_path]
                    if node_check:
                        print( "[ERROR]: tree inconsistency found, two nodes with the same path (%s)" % node_check.abs_path )
                        node_check.print()
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
                if self.__dbg__ > 2:
                    print( "[DBG++]: aliases node found, registring aliases" )
                for alias in alias_node:
                    if self.__dbg__ > 2:
                        print( "[DBG++]: alias: %s %s" % (alias.name,alias.value[0] ))
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
                            if self.__dbg__ > 2:
                                print( "[DBG++]: alias: looking for node via label path: %s" % label_adjusted_path )
                            try:
                                alias_target = self.__nodes__[ label_adjusted_path ]
                            except:
                                alias_target = None

                    if alias_target:
                        if self.__dbg__ > 2:
                            print( "[DBG++]: alias target node found: %s" % alias_target.abs_path )
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
    def __init__( self, snapshot = False, output=sys.stdout, debug=0 ):
        # init the base walker.
        super().__init__( snapshot )

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
        else:
            self.output = output_file

    def start(self, n ):
        """LopperTreePrinter start

        Prints the start / opening of a tree and handles the preamble.

        Args:
            n (LopperNode): the opening node of the tree

        Returns:
            Nothing
        """
        # peek ahead to handle the preamble
        for p in n:
            if p.pclass == "preamble":
                print( "%s" % p, file=self.output )

        print( "/dts-v1/;\n\n/ {", file=self.output )

    def start_node(self, n ):
        """LopperTreePrinter node start

        Prints the start / opening of a node

        Args:
            n (LopperNode): the node being opened

        Returns:
            Nothing
        """
        if n.indent_char == ' ':
            indent = n.depth * 8
        else:
            indent = n.depth

        nodename = n.name
        if n.number != 0:
            plabel = ""
            try:
                if n['lopper-label.*']:
                    plabel = n['lopper-label.*'].value[0]
            except:
                plabel = n.label

            if n.phandle != 0:
                if plabel:
                    outstring = plabel + ": " + nodename + " {"
                else:
                    outstring = Lopper.phandle_safe_name( nodename ) + ": " + nodename + " {"
            else:
                if plabel:
                    outstring = plabel + ": " + nodename + " {"
                else:
                    outstring = nodename + " {"

            print( "", file=self.output )
            print(outstring.rjust(len(outstring)+indent, n.indent_char), file=self.output )

    def end_node(self, n):
        """LopperTreePrinter node end

        Prints the end / closing of a node

        Args:
            n (LopperNode): the node being closed

        Returns:
            Nothing
        """
        if n.indent_char == ' ':
            indent = n.depth * 8
        else:
            indent = n.depth

        outstring = "};"
        print(outstring.rjust(len(outstring)+indent,n.indent_char), file=self.output)

    def start_property(self, p):
        """LopperTreePrinter property print

        Prints a property

        Args:
            p (LopperProperty): the property to print

        Returns:
            Nothing
        """
        # do we really need this resolve here ? We are already tracking if they
        # are modified/dirty, and we have a global resync/resolve now. I think it
        # can go
        p.resolve( self.strict )

        if p.node.indent_char == ' ':
            indent = (p.node.depth * 8) + 8
        else:
            indent = p.node.depth + 1

        outstring = str( p )
        only_align_comments = False

        if p.pclass == "preamble":
            # start tree peeked at this, so we do nothing
            outstring = ""
        else:
            # p.pclass == "comment"
            # we have to substitute \n for better indentation, since comments
            # are multiline

            do_indent = True
            if only_align_comments:
                if p.pclass != "comment":
                    do_indent = False

            if do_indent:
                dstring = ""
                dstring = dstring.rjust(len(dstring) + indent + 1, p.node.indent_char)
                outstring = re.sub( '\n\s*', '\n' + dstring, outstring, 0, re.MULTILINE | re.DOTALL)

        if outstring:
            print(outstring.rjust(len(outstring)+indent,p.node.indent_char), file=self.output)

    def end(self, n):
        """LopperTreePrinter tree end

        Ends the walking of a tree

        Args:
            n (LopperNode): -1

        Returns:
            Nothing
        """

        if self.output != sys.stdout:
            self.output.close()
