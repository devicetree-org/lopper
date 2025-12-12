#/*
# * Copyright (c) 2020 Xilinx Inc. All rights reserved.
# *
# * Author:
# *       Bruce Ashfield <bruce.ashfield@xilinx.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

import ruamel
from ruamel.yaml import YAML

import json
import sys
import copy
import os
import re
from packaging import version

from collections import OrderedDict

from lopper.tree import LopperTree
from lopper.tree import LopperTreePrinter
from lopper.tree import LopperNode
from lopper.tree import LopperProp

from anytree.importer import DictImporter
from anytree.exporter import DictExporter
from anytree.importer import JsonImporter
import pprint  # just for nice printing
from anytree import RenderTree  # just for nice printing
from anytree import PreOrderIter
from anytree import AnyNode
from anytree import Node

from lopper.log import _warning, _info, _error, _debug, _init, _level
import lopper.log
import logging

_init( __name__ )
_init( "yaml.py" )


# Attempt to import Merger globally and use a flag for availability
try:
    from deepmerge import Merger
    DEEPMERGE_AVAILABLE = True
except ImportError:
    DEEPMERGE_AVAILABLE = False

def flatten_dict(dd, separator ='_', prefix =''):
    return { prefix + separator + k if prefix else k : v
             for kk, vv in dd.items()
             for k, v in flatten_dict(vv, separator, kk).items()
    } if isinstance(dd, dict) else { prefix : dd }

# What's the difference between these two very similar routines ?
#
# One creates a copy, the other does changs in place. We need the in
# place variant as anchors and references in the yaml input are picked
# up by object references. If we make a copy, code that relies on
# those references will break.
def preprocess_yaml_data_in_place(data):
    """Recursively preprocess the data by changing all 'parent' keys to 'custom_parent',
    performed in place to maintain anchors and references.
    """
    if isinstance(data, dict):
        # Check if 'parent' exists and rename it to 'custom_parent'
        if 'parent' in data:
            data['custom_parent'] = data['parent']  # Create the new key
            del data['parent']  # Remove the old key

        # Iterate through all keys in the data
        for key in list(data.keys()):  # We need to list keys to prevent mutation issues
            preprocess_yaml_data_in_place(data[key])  # Recur for any nested dictionaries

    elif isinstance(data, list):
        for item in data:
            preprocess_yaml_data_in_place(item)  # Recur for list items

    return data  # Return modified data (though it's already modified in place)

def preprocess_yaml_data(data):
    if isinstance(data, dict):
        return {k if k != "parent" else "custom_parent": preprocess_yaml_data(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [preprocess_yaml_data(item) for item in data]
    return data

def postprocess_anytree_tree(root):
    # Traverse each node in the tree using PreOrderIter, which visits each node in a depth-first order
    for node in PreOrderIter(root):
        if 'custom_parent' in node.__dict__:
            # Rename 'custom_parent' back to 'parent'
            node.__dict__['parent'] = node.__dict__.pop('custom_parent')


from anytree.search import findall_by_attr

def is_ancestor(child, potential_ancestor):
    """Check if a node is an ancestor of another node.

    Determines whether the specified potential_ancestor is an ancestor
    of the given child node within the anytree structure.

    Args:
        child (Node): The node whose ancestry is being checked.
        potential_ancestor (Node): The node that might be an ancestor of the child node.

    Returns:
        bool: True if potential_ancestor is an ancestor of child; False otherwise.
    """
    return potential_ancestor in child.ancestors


def find_node_by_path(root, path):
    """Find a node by traversing the tree according to a given path.

    Traverses the tree starting from the root node, following the segments
    in the given path to locate the corresponding node.

    Args:
        root (Node): The root node of the tree structure to start the search.
        path (str): The path string separated by '/' indicating the node hierarchy.

    Returns:
        Node or None: The node corresponding to the provided path, or None if not found.
    """
    # Strip leading slash for consistent traversal
    if path.startswith('/'):
        path = path[1:]

    segments = path.split("/")
    current_node = root

    for segment in segments:
        next_node = None
        for child in current_node.children:
            if child.name == segment:
                next_node = child
                break
        if next_node is None:
            return None
        current_node = next_node

    return current_node


def reparent_nodes_by_specified_path(root, spec_data):
    """Reparent nodes based on specified paths in the given data.

    Using the provided specification data, reparent nodes within the anytree
    structure based on 'custom_parent' path indications, handling path-like
    and direct attribute syntax. Exits on missing paths.

    Args:
        root (Node): The root node of the anytree structure.
        spec_data (dict): The data specifying reparenting decisions based on 'custom_parent'.

    Returns:
        None: The tree structure is modified in place.
    """
    node_map = {node.name: node for node in PreOrderIter(root)}

    def traverse_yaml_data(yaml_data):
        for key, value in yaml_data.items():
            if isinstance(value, dict):
                if 'custom_parent' in value:
                    child_node = node_map.get(key)
                    parent_path = value['custom_parent']

                    if '/' in parent_path:
                        # Handle path-like syntax
                        new_parent_node = find_node_by_path(root, parent_path)
                    else:
                        # Handle direct name syntax using findall_by_attr
                        new_parent_node = findall_by_attr(root, value=parent_path, name="name", maxlevel=None)
                        new_parent_node = new_parent_node[0] if new_parent_node else None

                    if not new_parent_node:
                        _error(f"Path '{parent_path}' not found for '{key}'. Exiting.", True)

                    if child_node and is_ancestor(child_node, new_parent_node):
                        _debug(f"Skipping reparenting '{key}'; '{new_parent_node.name}' is already an ancestor.")
                    else:
                        _debug(f"Reparenting '{key}' under '{new_parent_node.name}'")
                        child_node.parent = new_parent_node

                traverse_yaml_data(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        traverse_yaml_data(item)

    traverse_yaml_data(spec_data)

class LopperTreeImporter(object):

    def __init__(self, nodecls=AnyNode):
        """
        Import Tree from LopperTree

        Every node is converted to an instance of `nodecls`.
        The node's children are converted likewise and added as children.

        Keyword Args:
            nodecls: class used for nodes.

        """
        self.nodecls = nodecls
        self.boolean_as_int = False

    def import_(self, data):
        """Import tree from `data`."""
        return self.__import(data)

    def __import(self, node, parent=None, name=None, verbose=0):
        assert isinstance(node, LopperNode)

        attrs = OrderedDict()

        name = node.name
        if not name:
            name = "root"

        attrs['name'] = name

        # Retain the LopperNode's 'parent' value but rename it to avoid conflict
        parent_value = None

        # Check and store the 'parent' value
        if 'parent' in node.__props__:
            parent_value = node.__props__['parent'].value # Extract the value for later use
            # Map it to a different attribute to avoid conflict
            attrs['custom_parent_value'] = parent_value

        for p in node.__props__:

            # Skip the Lopper 'parent' completely during regular property extraction
            # since this will break anytree's import
            if p == 'parent':
                continue

            # if the node is from a yaml source, it may have been json encoded,
            # so try that first and otherwise assign it directly.

            json_encoded_string = False
            if node._source != "yaml" and node.__props__[p].pclass != "json":
                # if the property isn't tagged as yaml or json, we do a double
                # check to see if it is a json encoded string. The test is simple.
                # try and decode it. If no exception is thrown, we consider that it
                # was valid json.
                if type(node.__props__[p].value) == list and len(node.__props__[p].value) == 1:
                    val = node.__props__[p].value[0]
                else:
                    val = node.__props__[p].value

                decode_val = val
                try:
                    val = json.loads(decode_val)
                    json_encoded_string = True
                except Exception as e:
                    pass

            if node._source == "yaml" or node.__props__[p].pclass == "json" or json_encoded_string:
                # property with no value is an encoded boolean "true" as an
                # empty list. So check for a value, try json, fallback to
                # assignment.
                decode = False
                if self.boolean_as_int:
                    decode = True
                else:
                    if node.__props__[p].value:
                        decode = True
                    else:
                        decode = False

                if decode:
                    try:
                        _debug( f"LopperTreeImporter: json load for prop {p} : {node.__props__[p].value}" )

                        decode_val = ""
                        val = []
                        if type(node.__props__[p].value) == list and len(node.__props__[p].value) == 1:
                            decode_val = node.__props__[p].value[0]
                            val = json.loads(decode_val)
                        else:
                            if type(node.__props__[p].value) == list:
                                for item in node.__props__[p].value:
                                    val.append( json.loads(item) )
                            else:
                                decode_val = node.__props__[p].value
                                val = json.loads(decode_val)

                    except Exception as e:
                        val = node.__props__[p].value

                    if type(val) == int:
                        if val == 1:
                            val = True
                        else:
                            val = False
                else:
                    val = True
            else:
                # everything is a list in a LopperProp, if the length is one, just grab the
                # element. We'll have better yaml in the end if this is done.
                if type(node.__props__[p].value) == list and len(node.__props__[p].value) == 1:
                    val = node.__props__[p].value[0]
                else:
                    val = node.__props__[p].value

            attrs[p] = val

        if type(attrs) == list and len(attrs) == 1:
            attrs = attrs[0]

        nnode = self.nodecls(parent=parent, **attrs)

        for child in node.child_nodes:
            self.__import(node.child_nodes[child], parent=nnode)

        return nnode

# Extension of the default anytree DictExporter, since we don't want added
# nodes like "root" and children to be in the export.
class LopperDictExporter(DictExporter):
    def export(self, node):
        """Export tree starting at `node`."""
        attriter = self.attriter or (lambda attr_values: attr_values)
        return self.__export(node, self.dictcls, attriter, self.childiter)

    def _lopper_iter_attr_values(self, node):
        """ Copy of Anytree default iterator generator. Used if the
            default behaviour is required (tuples), but we need to
            debug or instrument.
        """
        # print( "lopoper itert. dict type %s" % type(node.__dict__))
        for k, v in node.__dict__.items():
            if k in ('_NodeMixin__children', '_NodeMixin__parent'):
                continue
            # print( "    yeilding: %s %s" % (k,v))
            yield k, v

    # this is an override of the default anytree iterator (see above
    # for an exact copy. The issue with the iterator generator used by
    # anytree is that the resulting tuple gets sorted alphabetically
    # (even when yielded in the order of properties). This means that
    # our resulting dictionary has a different order then the nodes
    # and properties, and hence the yaml.
    #
    # To preserve the order, we use more memory (TBD if this is an
    # issue with a really large tree) and generate a list of all the
    # elements in the loop, and return the list. The maintains the
    # order and we don't get unwanted alphabetic sorting.
    def _lopper_attr_values_list(self, node):
        ret = []
        for k, v in node.__dict__.items():
            if k in ('_NodeMixin__children', '_NodeMixin__parent'):
                continue
            ret.append( (k, v) )

        return ret

    def __export(self, node, dictcls, attriter, childiter, level=1, verbose = 0):
        # attr_values = attriter(self._iter_attr_values(node))
        # attr_values = attriter(self._lopper_iter_attr_values(node))
        # data = dictcls(attr_values)
        attr_values = self._lopper_attr_values_list(node)
        data = dictcls(attr_values)
        maxlevel = self.maxlevel

        try:
            # if there's a name, its a node, but we don't want it in our output,
            # so we delete it from the attrs. It'll show up in our dictionary as
            # key instead.
            name = data['name']
            del data['name']
        except:
            name = None

        if maxlevel is None or level < maxlevel:
            children = [self.__export(child, dictcls, attriter, childiter, level=level + 1)
                        for child in childiter(node.children)]
            if children:
                # if there are children returned, we merge them into a single dictionary, so
                # that output can represent them as child nodes of the current one (see how
                # we return data)
                _debug( f"node: {name} has children: {children}" )

                new_dict = {}
                #for c in reversed(children):
                for c in children:
                    _debug( f"        merging dict: {c}" )
                    new_dict.update( c )

                data.update( new_dict )

        # if the node is NOT the root node, we index the attributes by the node
        # name, if root, we return the merged dictionary.
        if name != "root":
            return { name : data }
        else:
            return data

class LopperDictImporter(object):
    def __init__(self, nodecls=AnyNode):
        """
        Import Tree from dictionary.

        This is taken from the Anytree codebase, and modified to not
        require a "children" element in the yaml.

        Every dictionary is converted to an instance of `nodecls`.
        The dictionaries listed in the children attribute are converted
        likewise and added as children.

        Keyword Args:
            nodecls: class used for nodes.

        >>> from anytree.importer import DictImporter
        >>> from anytree import RenderTree
        >>> importer = DictImporter()
        >>> data = {
        ...     'a': 'root',
        ...     'children': [{'a': 'sub0',
        ...                   'children': [{'a': 'sub0A', 'b': 'foo'}, {'a': 'sub0B'}]},
        ...                  {'a': 'sub1'}]}
        >>> root = importer.import_(data)
        >>> print(RenderTree(root))
        AnyNode(a='root')
        ├── AnyNode(a='sub0')
        │   ├── AnyNode(a='sub0A', b='foo')
        │   └── AnyNode(a='sub0B')
        └── AnyNode(a='sub1')
        """
        self.nodecls = nodecls
        self.lists_as_nodes = False

    def import_(self, data):
        """Import tree from `data`."""
        return self.__import(data)

    def __import(self, data, parent=None, name=None):
        assert isinstance(data, dict)
        # The below assert breaks the loading of yaml with a "parent:"
        # key ... We are disabling this for now, otherwise, we may
        # need to preprocess the yaml and postprocess the tree to
        # restore "parent"
        assert "parent" not in data
        attrs = dict(data)
        verbose = 0

        _debug( f"===> __import ({name})" )
        _debug( f"            attrs: {attrs}" )

        if name:
            attrs['name'] = name

        children = []
        to_delete = []
        for k in attrs:
            if type(attrs[k]) == dict:
                # This is a child, name it
                # we need this deepcopy, since if the yaml has an alias and
                # reference, changing one dictionary changes the other. We
                # need a safe/full copy so we can assign the name safely.
                cdict = copy.deepcopy(attrs[k])
                cdict['name'] = k
                cdict['fdt_name'] = k

                _debug( f"      queuing child from dict node: name: {k} props: {cdict}" )

                children.append( cdict )
                # queue it for removal, since we don't want the child attributes to be
                # stored in the parent node. We don't remove it here, since the iterator
                # will change and we'll error
                to_delete.append( k )

            if type(attrs[k]) == list:
                if not self.lists_as_nodes:
                    continue


                # if the attribute is a list, and all of the subtypes are dictionaries
                # we had yaml something like:
                #    firewallconf:
                #       - block: true
                #       - block: never
                #         domain: 0x1
                #       - ....
                # We can expand that, and generate node names for the subdictionaries.
                # If we don't, the list will be json encoded and will have to be expanded
                # later.
                #
                all_dicts = all(isinstance(x, dict) for x in attrs[k])
                if all_dicts:
                    # Create a node, that will hold the unrolled child dictionaries
                    new_node = { 'name': k,
                                 'fdt_name' : k }
                    for i, kk in enumerate(attrs[k]):
                        sub_node_name = f"{k}@{i}"

                        d_to_process = kk
                        # if there is only one element in the dictionary, then pop it out, this
                        # avoids intermediate nodes of no value.
                        if len(kk.keys()) == 1:
                            # to be an intermediate node, the type of that single entry must
                            # be a dict, otherwise, we just leave things alone.
                            if type(list(kk.values())[0]) == dict:
                                d_to_process = list(kk.keys())[0]
                                d_to_process = kk[d_to_process]

                        new_node[sub_node_name] = d_to_process

                    children.append( new_node )

                    to_delete.append( k )

        for d in to_delete:
            del attrs[d]

        # if we didn't find a dictionary, look for a specially named "children"
        # attribute. This is for compatibility with anytree exported yaml.
        children_extra = attrs.pop("children", [])
        if not children and children_extra:
            children = children_extra

        # check if we had a name set, and if not, either set it
        # to 'root' (if we don't have a parent) or set it to the value
        # of the first entry in the dictionary
        try:
            name = attrs['name']
        except:
            if not parent:
                attrs['name'] = "root"
            else:
                first_key = list(attrs)[0]
                first_val = attrs[first_key]
                attrs['name'] = first_val

        _debug( f"      creating node with attrs: {attrs}" )

        node = self.nodecls(parent=parent, **attrs)
        for child in children:
            self.__import(child, parent=node)

        return node

class LopperJSON():
    """JSON read/writer for Lopper

    A Lopper "container" around a json input/output.

    This class is capable of reading a json inputfile, and
    creating a LopperTree. It is also capabable of taking a
    LopperTree and creating a json description of that tree.

    This is done by internally storing either a json or lopper tree
    input as a generic tree structure. The generic tree structure can
    be converted to a LopperTree or json file on demand. Hence we have
    the capability of converting between the two formats as required.

    """
    def __init__( self, tree = None, json = None, config = None ):
        """
        Initialize a a LopperJSON representation from either a json file
        or from a LopperTree.

        Args:
           tree (LopperTree,optional): reference to a LopperTree
           json (string,optional): path to a json input file
           config (configparser,optional): configuration instructions

        Returns:
           LopperYAML object: self
        """
        self.dct = None
        self.json_source = json
        self.anytree = None
        self.tree = tree

        self.boolean_as_int = False
        self.lists_as_nodes = False
        self.scalar_as_lists = False

        if self.json_source:
            self.load_json( self.json_source )

        if self.tree:
            self.load_tree( self.tree )

    def load_json( self, filename = None ):
        """Load/Read a json file into tree structure

        Create an internal tree object from an input json file. The file can be
        passed directly to this routine, or already be part of the object
        through initialization.

        Args:
            filename (string,optional): path to json file to read

        Returns:
            Nothing
        """
        in_name = self.json_source

        if filename:
            in_name = filename
        if not in_name:
            _error( f"no json source provided" )


        # option1: use anytree importer

        # with open(in_name) as f:
        #     json_data = f.read()
        # importer = JsonImporter()
        # root = importer.import_(json_data)

        # option2: use the same path as the yaml import

        inj = open( in_name )
        self.dct = json.load(inj)
        if not self.dct:
            _error( f"no data available to load" )
            sys.exit(1)

        # flatten the dictionary so we can look up aliases and anchors
        # by identity later .. without needing to recurse
        self.dct_flat = flatten_dict(self.dct,separator="/")
        importer = LopperDictImporter(Node)
        importer.lists_as_nodes = self.lists_as_nodes
        self.anytree = importer.import_(self.dct)

        # print(RenderTree(self.anytree.root))

    def to_tree( self ):
        """ Export LopperYAML to a LopperTree

        Args:
           None

        Returns:
          LopperTree object representation of YAML object
        """
        if not self.anytree:
            _error( f"cannot export tree, nothing is loaded" )
            return None

        lt = LopperTree()

        lt.strict = False

        excluded_props = [ "name", "fdt_name" ]
        serialize_json = True

        for node in PreOrderIter(self.anytree):
            if node.name == "root":
                ln = lt["/"]
                #ln = LopperNode( -1, None )
                #ln.abs_path = "/"
            else:
                ln = LopperNode( -1, node.name )
                ln.abs_path = self.path( node )

            if lopper.log._is_enabled(logging.INFO):
                lt.__dbg__ = 4
                ln.__dbg__ = 4

            ln._source = "yaml"

            # add the node to the tree
            lt = lt + ln

            props = self.props( node )
            for p in props:
                _debug( f" prop: {p} ({props[p]})" )

                if serialize_json:
                    use_json = False
                    skip = False
                    if type(props[p]) == list:
                        for p2 in props[p]:
                            if type(p2) == list or type(p2) == dict:
                                use_json = True
                    elif type(props[p]) == dict:
                        for p2 in props[p].values():
                            if type(p2) == list or type(p2) == dict:
                                use_json = True
                    elif type(props[p]) == bool:
                        # don't encode false bool, and a true is just an empty list
                        if props[p]:
                            if self.boolean_as_int:
                                props[p] = [ 1 ]
                            else:
                                props[p] = None
                        else:
                            if self.boolean_as_int:
                                props[p] = [ 0 ]
                            else:
                                skip = True
                    else:
                        # not a list, not a dict, not a bool, but if asked, we'll still
                        # encode as a list (for consistency with fdt reads).
                        if self.scalar_as_lists:
                            props[p] = [props[p]]

                    if use_json:
                        x = json.dumps(props[p])
                    else:
                        x = props[p]

                    if not skip:
                        if not p in excluded_props:
                            lp = LopperProp( p, -1, ln, x )
                            if use_json:
                                lp.pclass = "json"

                            lp.resolve()
                            # add the property to the node
                            ln + lp

                            # if this is a label property, bubble it up to the node
                            # Supports both lopper-label-* (generated) and simpler 'label:' syntax
                            if re.search(r'lopper-label.*', p) or p == 'label':
                                ln.label_set(lp.value[0] if isinstance(lp.value, list) else lp.value)
                else:
                    if type(props[p]) == list:
                        # we need to check if there are embedded dictionaries, and if so, expand them.
                        # since a dictionary doesn't map directly to device tree output.
                        prop_list = self.prop_expand( props[p] )
                        lp = LopperProp( p, -1, ln, prop_list )
                        lp.resolve()
                        ln + lp
                    elif type(props[p]) == bool:
                        if props[p]:
                            lp = LopperProp( p, -1, ln, [] )
                            lp.resolve()
                            # add the prop the node
                            ln + lp
                        else:
                            _info( f"not encoding false boolean type: {p}" )
                    elif type(props[p]) == dict:
                        # we need to check if there are embedded dictionaries, and if so, expand them.
                        # since a dictionary doesn't map directly to device tree output.
                        prop_list = self.prop_expand( props[p] )
                        lp = LopperProp( p, -1, ln, prop_list )
                        lp.resolve()
                        ln + lp
                    else:
                        if not p in excluded_props:
                            lp = LopperProp( p, -1, ln, props[p] )
                            lp.resolve()
                            ln + lp

        lt.resolve()

        # are there any nodes in the tree with properties containing a merge ?
        #   - as the value ?
        #   - as the key in a dictionary ?
        # lt.print()
        for n in lt:
            new_node = None
            props_to_delete = []
            for p in n:
                new_list = []

                # "<<+" is the extended expand
                # "<<*" is a node extended expand
                expand_string = "<<+"
                if p.name == "<<*":
                    _debug( f"node extension <<* detected in node {p.node.abs_path}" )
                    # to make the standard processing work below, we upgrade the
                    # property to json formatted if it already isn't
                    expand_string = "<<*"
                    if p.pclass != "json":
                        p.pclass = "json"
                        p.__dict__["value"] = json.dumps(p.value)

                    # we could do special processing here, with merging, etc, but
                    # for now, we'll just leave it as a placeholder, since it is
                    # better to have it as generic as possible.
                    # for label_prop in p.value:
                    #     tgt_node = lt.lnodes(label_prop)
                    #     if tgt_node:
                    #         print( "[DBG]: target found, pulling properties" )

                if p.pclass == "json":
                    _debug( f"json: {p.value} (len: {len(p)})" )
                    extension_found = False
                    trace_enabled = lopper.log._is_enabled(lopper.log.TRACE)
                    for x in range(0, len(p)):
                        try:
                            m_val = p[x][expand_string]
                            extension_found = True
                            _debug( f"found extension marker: {m_val}" )
                        except:
                            pass

                    if not extension_found:
                        if p.name == expand_string:
                            extension_found = True
                            _debug( f"found extension name ({expand_string})" )

                    for x in range(0, len(p)):
                        _debug( f"     [{x}] chunk: {p[x]} ({type(p[x])})" )
                        try:
                            # an exception is raised if the chunk doesn't have an index
                            # with <<+, so everything below can assume this is true.
                            if p.name == expand_string:
                                m_val = p[x]
                                dict_check = 0
                            else:
                                m_val = p[x][expand_string]
                                dict_check = x
                            lopper.log._debug( f"                      mval {m_val} ({dict_check})", level=lopper.log.TRACE )
                            # Whether we allow chunks greater than 1 to go through this loop changes
                            # the output nodes (versus json strings). Keeping the old condition around
                            # as a reference, since this may become a tunable in the future
                            # if type(m_val) == list and len(m_val) == 1 and type(m_val[dict_check]) == dict:
                            if type(m_val) == list and len(m_val) > 0 and type(m_val[dict_check]) == dict:
                                lopper.log._debug( f"-------> promote json to a node: {p.name} ({p.abs_path})", level=lopper.log.TRACE )

                                # the new node name will be: <parent node>@<number> by default
                                new_name = p.name
                                if p.name == expand_string:
                                    try:
                                        # this can be overriden by an anchor name, if discovered below
                                        new_name = p.node.name + "@" + str(x)
                                    except Exception as e:
                                        lopper.log._debug( f"Exception during new node name creation: {e}", level=lopper.log.TRACE )

                                # A secondary name (and better) for the new node, is the name of
                                # the alias (if one was used). To do that, we pull out the dictionary
                                # that was stored in the flattened yaml dict structure, and check that
                                # dictionary for identity in the anchors.
                                try:
                                    # Grab the dictionary that is at the same path as this node (minus
                                    # the leading "/" from the flattened dictionary..
                                    possible_alias = self.dct_flat[p.abs_path[1:]][x]
                                    # if there is a dictionary there, then this could have been an
                                    # expanded alias.
                                    lopper.log._debug( f"[{p.abs_path[1:]}] found {possible_alias}", level=lopper.log.TRACE )

                                    # Searching that same flattened yaml dictionary for the *same*
                                    # dictionary in another location indicates that this is an expanded
                                    # alias. We can get the name from the target anchor.
                                    i_name = None
                                    for flat_thing_name,flat_thing in self.dct_flat.items():
                                        if possible_alias is flat_thing:
                                            lopper.log._debug( f"alias/anchor identity match: {flat_thing_name}", level=lopper.log.TRACE )
                                            # we take the first hit, that's the anchor, other hits will just
                                            # be more aliases .. which point to the same anchor and of course
                                            # will also pass
                                            if not i_name:
                                                i_name = flat_thing_name

                                    if i_name:
                                        # TODO: we should look to see if there's already a node with this
                                        #       name, if so, we need the @<number>
                                        new_name = os.path.basename(i_name) #  + "@" + str(x)

                                except Exception as e:
                                    pass

                                lopper.log._debug( f"new node name: {new_name}", level=lopper.log.TRACE )
                                try:
                                    lopper.log._debug( f"checking for existing node at: {n.abs_path}/{new_name}", level=lopper.log.TRACE )
                                    node_check = lt[n.abs_path + "/" + new_name]
                                    lopper.log._debug( "node exists", level=lopper.log.TRACE )
                                except:
                                    pass

                                ## note: in the list > 1 entry list, we could do something smart with repeating
                                ##       values and make them @<x> properties.
                                new_node = LopperNode( -1, name=new_name )
                                for i,v in m_val[dict_check].items():
                                    lopper.log._debug( f"                adding prop: {i} -> {v}", level=lopper.log.TRACE )
                                    new_prop = LopperProp(i, -1, new_node, v )
                                    new_node + new_prop
                                    new_prop.resolve()

                                new_node.resolve()

                                if p.name != expand_string:
                                    n.delete( p )

                                # this is wrong, there's a bug in node adding .. we
                                # should be able to just add this to the parent node
                                # and be done.
                                # n = n + new_node
                                new_node.abs_path = n.abs_path + "/" + new_node.name
                                lt = lt + new_node
                            else:
                                if new_node:
                                    lopper.log._debug( "extension found, adding as property to the new node", level=lopper.log.TRACE )
                                    new_prop_name = new_node.name + "-xtend"
                                    new_prop = LopperProp( new_prop_name, -1, new_node, json.dumps(m_val) )
                                    new_prop.pclass = "json"
                                    new_node + new_prop
                                    new_prop.resolve()
                                else:
                                    if type(m_val) == dict:
                                        lopper.log._debug( f"extending properties to node: {n.abs_path}", level=lopper.log.TRACE )
                                        for i,v in m_val.items():
                                            lopper.log._debug( f"                    adding prop: {i} -> {v}", level=lopper.log.TRACE )
                                            new_prop = LopperProp(i, -1, n, v )
                                            n + new_prop
                                            new_prop.resolve()

                                    # unroll a loop ?
                                    if type(m_val) == list:
                                        possible_merge = m_val
                                        nested_lists = any(isinstance(i, list) for i in possible_merge)
                                        newlist = possible_merge
                                        while nested_lists:
                                            newlist = [item for items in newlist for item in items]
                                            nested_lists = any(isinstance(i, list) for i in newlist)

                                        new_list.extend(newlist)
                                    else:
                                        new_list.extend( [m_val] )

                        # catches the check for expand_string ("<<+" or "<<*")
                        except Exception as e:
                            if extension_found:
                                new_list.append(p[x])

                    # This delete blows up the iteration. We need to save the
                    # list of properties to delete and delete them AFTER we
                    # finish the loop end of the loop through the property
                    # chunks
                    if p.name == expand_string:
                        props_to_delete.append(p)
                        #n.delete( p )

                    # we've finished looping through all the json chunks
                    if new_list and extension_found:
                        # we do this to avoid the wrapping routines in LopperProperty
                        p.__dict__["value"] = json.dumps(new_list)
                        p.resolve( False )

            # we've finished looping through all the properties
            if props_to_delete:
                for p in props_to_delete:
                    n.delete(p)

        lt.sync()
        return lt

    def to_json( self, outfile = None, verbose = 0 ):
        """ Export LopperJSON tree to a json output file

        Args:
           outfile (string): path to a json output file

        Returns:
           Nothing
        """
        if self.anytree:
            trace_enabled = verbose > 2 or lopper.log._is_enabled(lopper.log.TRACE)
            # if there's only one child, we use that, which allows us to skip the Anytree
            # "root" node, without any tricks.
            no_root = False
            start_node = self.anytree
            if no_root:
                if len(self.anytree.children) == 1:
                    start_node = self.anytree.children[0]

            # at high verbosity, use an ordered dict for debug reasons
            if trace_enabled:
                dcttype=OrderedDict
            else:
                dcttype=dict

            dct = LopperDictExporter(dictcls=dcttype,attriter=sorted).export(start_node)

            lopper.log._debug("dumping export dictionary", level=lopper.log.TRACE)
            lopper.log._debug(pprint.pformat(dct), level=lopper.log.TRACE)

            if not outfile:
                print(json.dump(dct))
            else:
                pjson = json.dumps(dct, indent=4, separators=(',', ': '))
                lopper.log._debug("dumping generated json to stdout:", level=lopper.log.TRACE)
                lopper.log._debug(pjson, level=lopper.log.TRACE)

                with open( outfile, "w") as file:
                    file.write(pjson)

    def prop_expand( self, prop ):
        """Expand a property into a format a device tree can represent

        This routine is for use when json is not available as a serialization
        mechanism for imported yaml. It expands lists and dictionaries into
        a ":::" separate string, that can be carried in a device tree.

        This is mostly obsolete, but is kept for compatibility

        Args:
           Anytree Property

        Returns:
           string: serialized representation of the property
        """
        # expands a complex property type into something a device tree
        # can represent.
        prop_list = []
        if type(prop) == list:
            for item in prop:
                if type(item) == dict:
                    prop_list.extend( self.prop_expand( item ) )
                    if item != prop[-1]:
                        prop_list.append( ":::" )
                else:
                    prop_list.append( item )

            return prop_list
        elif type(prop) == dict:
            value_types = None
            uniform_values = True
            for k,v in prop.items():
                if not value_types:
                    value_types = type(v)
                else:
                    if value_types != type(v):
                        uniform_values = False

            # temporarily turning off uniform values, to see if it makes
            # processing easier
            uniform_values = False

            for k,v in prop.items():
                # expand again, in case the value is a dictionary ...
                v_exp = self.prop_expand( v )
                # unwind lists of one, only at this level
                if type(v_exp) == list and len(v_exp) == 1:
                    v_exp = v_exp[0]

                if uniform_values:
                    # type #2
                    prop_list.append( v_exp )
                else:
                    # type #1
                    prop_list.append( str(k) + ":" + str(v_exp) )

            return prop_list
        else:
            return prop


    def print( self ):
        """ Print/Render tree representation of the YAML input

        Args:
            None

        Returns:
            Nothing
        """
        print( RenderTree( self.anytree ) )

    def props( self, node ):
        """Create a dictionary representation of Node attributes

        Gather a dictionary representation of the properties of a LopperJSON
        Node.

        This routine skips internal members of a node, and returns only
        attributes that are meaningful to the caller.

        It knows how to expand node references and returns them as properties
        of the node.

        Args:
            node (AnyTreeNode): node to export as dictionary

        Returns:
            dict of node names -> properties
        """
        pdict = {}
        for a in node.__dict__:
            if not a.startswith( "_NodeMixin" ):
                # if we have a list, and the elements are a dictionary, we need
                # to expand the dict and add them individually. If it is just a
                # list, we add it by itself.
                pdict[a] = node.__dict__[a]

        return pdict

    def path( self, node ):
        """Determine the string representation of a Node's path

        Generate and return a string that represents the path of a node in
        the Yaml internal tree.

        Args:
            node (AnyTreeNode): node to query for path

        Returns:
            string: absolute path of the node
        """
        path_gen=""
        for p in node.path:
            if p.name == "root":
                if len(node.path) == 1:
                    path_gen += "/"
            else:
                path_gen += "/" + p.name

        return path_gen

    def dump( self ):
        """Dump/print the internal representation of the YAML tree

        Debug routine to print the details of the Tree created for the
        input YAML or LopperTree

        Args:
            None

        Returns:
            None
        """
        for node in PreOrderIter(self.anytree):
            print( f"node: {node.name} depth: {node.depth}" )
            print( f"   raw: {node}" )
            print( "   attributes:" )
            for a in node.__dict__:
                if not a.startswith( "_NodeMixin" ):
                    print( f"        {a}: {node.__dict__[a]}")
            print( f"   path: {type(node.path)}" )
            path_gen=""
            for p in node.path:
                print( f"      path node: {p}" )
                if p.name == "root":
                    if len(node.path) == 1:
                        path_gen += "/"
                else:
                    path_gen += "/" + p.name
            print( f"   full path: {path_gen}" )

    def flatten(self,d):
        out = {}
        for key, val in d.items():
            if isinstance(val, dict):
                val = [val]
            if isinstance(val, list):
                for subdict in val:
                    if isinstance(subdict,dict):
                        deeper = self.flatten(subdict).items()
                        out.update({key + '_' + key2: val2 for key2, val2 in deeper})
            else:
                out[key] = val
        return out

    def load_tree( self, tree = None ):
        """Load/Read a LopperTree into a YAML representation

        Create an internal tree object from an input LopperTree. The tree can be
        passed directly to this routine, or already be part of the object
        through initialization.

        Args:
            tree (LopperTree,optional): LopperTree representation of a device tree

        Returns:
            Nothing
        """
        in_tree = tree
        if not in_tree:
            in_tree = self.tree

        if not in_tree:
            print( "[ERROR]: no tree provided" )
            sys.exit(1)

        importer = LopperTreeImporter(Node)
        importer.boolean_as_int = self.boolean_as_int
        self.anytree = importer.import_(in_tree["/"])

class LopperYAML(LopperJSON):
    """YAML read/writer for Lopper

    A Lopper "container" around a yaml input/output.

    This class is capable of reading a yaml inputfile, and
    creating a LopperTree. It is also capabable of taking a
    LopperTree and creating a yaml description of that tree.

    See LopperJSON for the details of conversion between the
    formats.
    """
    def __init__( self, yaml_file = None, tree = None, config = None ):
        """
        Initialize a a LopperYAML representation from either a yaml file
        or from a LopperTree.

        Args:
           yaml_file (string,optional): path to a yaml input file
           tree (LopperTree,optional): reference to a LopperTree
           config (configparser,optional): configuration instructions

        Returns:
           LopperYAML object: self
        """
        super().__init__( tree, json = None, config = config )

        self.yaml_source = yaml_file

        if config:
            try:
                self.boolean_as_int = config.getboolean( 'yaml','bool_as_int' )
            except:
                pass

            try:
                self.lists_as_nodes = config.getboolean( 'yaml','lists_as_nodes' )
            except:
                pass

            try:
                self.scalar_as_lists = config.getboolean( 'yaml','scalar_as_lists' )
            except:
                pass

        if self.yaml_source and self.tree:
            print( "[ERROR]: both yaml and lopper tree provided" )
            sys.exit(1)

        if self.yaml_source:
            self.load_yaml( self.yaml_source )


    def to_yaml( self, outfile = None, verbose = 0 ):
        """ Export LopperYAML tree to a yaml output file

        Args:
           outfile (string): path to a yaml output file

        Returns:
           Nothing
        """
        if self.anytree:
            trace_enabled = verbose > 2 or lopper.log._is_enabled(lopper.log.TRACE)
            # if there's only one child, we use that, which allows us to skip the Anytree
            # "root" node, without any tricks.
            no_root = False
            start_node = self.anytree
            if no_root:
                if len(self.anytree.children) == 1:
                    start_node = self.anytree.children[0]

            # at high verbosity, use an ordered dict for debug reasons
            if trace_enabled:
                dcttype=OrderedDict
            else:
                #dcttype=dict
                dcttype=OrderedDict

            # Traverse the AnyTree structure and rename 'custom_parent' to 'parent'
            def update_custom_parent(node):
                if hasattr(node, 'custom_parent_value'):
                    node.__dict__['parent'] = node.__dict__.pop('custom_parent_value')

                for child in node.children:
                    update_custom_parent(child)

            update_custom_parent(start_node)  # Update the custom attributes first

            dct = LopperDictExporter(dictcls=dcttype,attriter=sorted).export(start_node)
            # This converts the ordered dicts to regular dicts at the last moment
            # As a result, the order is preserved AND we don't get YAML that is all
            # list based, which is what you get from OrderedDicts when they are dumped
            # to yaml.
            dct = json.loads(json.dumps(dct))

            lopper.log._debug("to_yaml: dumping export dictionary", level=lopper.log.TRACE)
            lopper.log._debug(pprint.pformat(dct), level=lopper.log.TRACE)

            if version.parse(ruamel.yaml.__version__) < version.parse("0.18"):
                yaml = ruamel.yaml
                yaml_obj = None
            else:
                yaml_obj = YAML(typ='safe')
                yaml_obj.default_flow_style = False
                yaml_obj.canonical = False
                yaml_obj.default_style = None

            # This stops tags from being output.
            # We could make this a configuration option in the future

            # The emitter is only valid for a non ruamel yaml, which is no longer
            # supported
            # yaml.emitter.Emitter.process_tag = lambda self, *args, **kw: None
            if not outfile:
                if yaml_obj:
                    from io import StringIO
                    buf = StringIO()
                    yaml_obj.dump(dct, buf)
                    print(buf.getvalue())
                else:
                    print(yaml.dump(dct))
            else:
                lopper.log._debug(str(RenderTree(self.anytree.root)), level=lopper.log.TRACE)
                lopper.log._debug("dumping generated yaml to stdout:", level=lopper.log.TRACE)
                if version.parse(ruamel.yaml.__version__) < version.parse("0.18"):
                    lopper.log._debug(ruamel.yaml.dump(dct,
                                                       default_flow_style=False,
                                                       canonical=False,
                                                       default_style=None), level=lopper.log.TRACE)
                else:
                    from io import StringIO

                    buf = StringIO()
                    yaml_obj.dump(dct, buf)
                    lopper.log._debug(buf.getvalue(), level=lopper.log.TRACE)

                with open( outfile, "w") as file:
                    if version.parse(ruamel.yaml.__version__) < version.parse("0.18"):
                        ruamel.yaml.round_trip_dump(dct, file,
                                                    default_flow_style=False,
                                                    canonical=False,
                                                    default_style=None)
                    else:
                        yaml_obj.dump(dct, file )



    def load_yaml( self, filename = None ):
        """Load/Read a YAML file into tree structure

        Create an internal tree object from an input YAML file. The file can be
        passed directly to this routine, or already be part of the object
        through initialization.

        Args:
            filename (string,optional): path to yaml file to read

        Returns:
            Nothing
        """
        in_name = self.yaml_source

        if filename:
            in_name = filename
        if not in_name:
            print( "[ERROR]: no yaml source provided" )

        iny = open( in_name )

        if version.parse(ruamel.yaml.__version__) < version.parse("0.18"):
            self.dct = ruamel.yaml.safe_load( iny )
        else:
            yaml = YAML(typ='safe')
            self.dct = yaml.load( iny )

        if not self.dct:
            print( "[ERROR]: no data available to load" )
            sys.exit(1)

        # this renames "parent" keys to "custom_parent", so that the anytree
        # loading won't hit an internal assert (where it checks for parent)
        orig_dct = self.dct
        new_dct = preprocess_yaml_data_in_place( self.dct )
        self.dct = new_dct

        # flatten the dictionary so we can look up aliases and anchors
        # by identity later .. without needing to recurse
        self.dct_flat = flatten_dict(self.dct,separator="/")
        importer = LopperDictImporter(Node)
        importer.lists_as_nodes = self.lists_as_nodes
        self.anytree = importer.import_(self.dct)

        reparent_nodes_by_specified_path( self.anytree, self.dct )

        # This walks the tree and fixes the preprocesed "custom_parent"
        # keys to "parent"
        postprocess_anytree_tree( self.anytree )

    @staticmethod
    def create_merger():
        """Create a merger configuration or indicate a fallback.

        This method attempts to create a deepmerge `Merger` configuration with specific strategies
        for handling lists, dictionaries, and sets if the deepmerge library is available.

        Strategies:
            - Lists are appended.
            - Dictionaries are merged.
            - Sets are united.
            - Fallback strategies and conflict strategies use `override`.

        Returns:
            Merger object configured with specific strategies if available, otherwise None.
        """
        if DEEPMERGE_AVAILABLE:
            return Merger(
                [
                    (list, ["append"]),  # Strategy for lists
                    (dict, ["merge"]),   # Strategy for dictionaries
                    (set, ["union"])     # Strategy for sets
                ],
                ["override"],           # Default strategy for other types
                ["override"]            # Strategy for type conflicts
            )
        return None  # Return None when deepmerge isn't available

    @staticmethod
    def deep_merge(dict1, dict2):
        """Merge two dictionaries using deepmerge or custom logic.

        Depending on the availability of the deepmerge library, this method will either
        merge the dictionaries using deepmerge strategies or revert to custom recursive logic
        to perform the merging.

        Args:
            dict1 (dict): First dictionary to merge.
            dict2 (dict): Second dictionary to be merged into the first.

        Returns:
            dict: A merged dictionary containing elements from both inputs.
        """
        if DEEPMERGE_AVAILABLE:
            merger = LopperYAML.create_merger()
            return merger.merge(dict1, dict2)
        else:
            # Custom recursive merge logic
            for key, value in dict2.items():
                if key in dict1:
                    if isinstance(dict1[key], dict) and isinstance(value, dict):
                        dict1[key] = LopperYAML.deep_merge(dict1[key], value)
                    elif isinstance(dict1[key], list) and isinstance(value, list):
                        dict1[key].extend(value)
                    else:
                        dict1[key] = value
                else:
                    dict1[key] = value
            return dict1

    @staticmethod
    def yaml_to_data(yaml_file):
        """Load YAML from a file into a Python data structure.

        This method reads a YAML file and parses its content into
        Python data structures using `ruamel.yaml`.

        Args:
            yaml_file (str): Path to the YAML file to read.

        Returns:
            The data structure representation of the YAML file.
        """
        yaml = ruamel.yaml.YAML()
        with open(yaml_file, 'r') as file:
            return yaml.load(file)

    @staticmethod
    def data_to_yaml(data, file_path):
        """Write data to a YAML file.

        This method takes a Python data structure and writes it to a file as
        YAML content, ensuring proper serialization.

        Args:
            data (object): The Python data structure to serialize into YAML.
            file_path (str): The path to the file where YAML content will be written.

        Returns:
            None
        """
        yaml = ruamel.yaml.YAML()
        with open(file_path, 'w') as file:
            yaml.dump(data, file)
