#/*
# * Copyright (c) 2020 Xilinx Inc. All rights reserved.
# *
# * Author:
# *       Bruce Ashfield <bruce.ashfield@xilinx.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

import yaml
import sys

from collections import OrderedDict

from lopper_tree import LopperTree
from lopper_tree import LopperTreePrinter
from lopper_tree import LopperNode
from lopper_tree import LopperProp

from anytree.importer import DictImporter
from anytree.exporter import DictExporter
from pprint import pprint  # just for nice printing
from anytree import RenderTree  # just for nice printing
from anytree import PreOrderIter
from anytree import AnyNode
from anytree import Node

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

    def import_(self, data):
        """Import tree from `data`."""
        return self.__import(data)

    def __import(self, node, parent=None, name=None):
        assert isinstance(node, LopperNode)

        attrs = {}
        for p in node.__props__:
            attrs[p] = node.__props__[p].value

        name = node.name
        if not name:
            name = "root"

        attrs['name'] = name

        nnode = self.nodecls(parent=parent, **attrs)
        for child in node.child_nodes:
            self.__import(node.child_nodes[child], parent=nnode)

        return nnode

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

    def import_(self, data):
        """Import tree from `data`."""
        return self.__import(data)

    def __import(self, data, parent=None, name=None):
        assert isinstance(data, dict)
        assert "parent" not in data
        attrs = dict(data)

        if name:
            attrs['name'] = name

        children = []
        for k in list(attrs):
            if type(attrs[k]) == dict:
                # This is a child, name it
                cdict = attrs[k]
                cdict['name'] = k
                cdict['fdt_name'] = k
                children.append( cdict )
                # remove it, since we don't want the child attributes to be
                # stored in the parent node
                del attrs[k]

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

        node = self.nodecls(parent=parent, **attrs)
        for child in children:
            self.__import(child, parent=node)

        return node


class LopperYAML():
    """YAML read/writer for Lopper

    A Lopper "container" around a yaml input/output.

    This class is capabable of reading a yaml inputfile, and
    creating a LopperTree. It is also capabable of taking a
    LopperTree and creating a yaml description of that tree.

    This is done by internally storing either a yaml or lopper tree input as a
    generic tree structure. The generic tree structure can be converted to a
    LopperTree or Yaml file on demand. Hence we have the capability of
    converting between the two formats as required.
    """
    def __init__( self, yaml_file = None, tree = None ):
        """
        Initialize a a LopperYAML representation from either a yaml file
        or from a LopperTree.

        Args:
           yaml_file (string,optional): path to a yaml input file
           tree (LopperTree,optional): reference to a LopperTree

        Returns:
           LopperYAML object: self
        """
        self.dct = None
        self.yaml_source = yaml_file
        self.anytree = None
        self.tree = tree

        if self.yaml_source and self.tree:
            print( "[ERROR]: both yaml and lopper tree provided" )
            sys.exit(1)

        if self.yaml_source:
            self.load_yaml( self.yaml_source )

        if self.tree:
            self.load_tree( self.tree )

    def to_yaml( self, outfile = None ):
        """ Export LopperYAML tree to a yaml output file

        Args:
           outfile (string): path to a yaml output file

        Returns:
           Nothing
        """
        if self.anytree:
            #dct = DictExporter(dictcls=OrderedDict, attriter=sorted).export(self.anytree)
            dct = DictExporter(dictcls=OrderedDict).export(self.anytree)
            #dct = DictExporter().export(self.anytree)

            # print( "blah: %s" % dct )
            # for d in dct:
            #     print( "%s" % d )

            if not outfile:
                print(yaml.dump(dct, default_flow_style=False,default_style='"'))
            else:
                with open( outfile, "w") as file:
                      yaml.dump(dct, file, default_flow_style=False)


    def to_tree( self ):
        """ Export LopperYAML to a LopperTree

        Args:
           None

        Returns:
          LopperTree object representation of YAML object
        """
        if not self.anytree:
            print( "[ERROR]: cannot export tree, nothing is loaded" )
            return None

        lt = LopperTreePrinter()

        excluded_props = [ "name", "fdt_name" ]

        for node in PreOrderIter(self.anytree):
            if node.name == "root":
                ln = lt["/"]
            else:
                ln = LopperNode( -1, node.name )
                ln.abs_path = self.path( node )

            props = self.props( node )
            lt = lt + ln

            for p in props:
                if type(props[p]) == list:
                    lp = LopperProp( p, -1, ln, props[p] )
                    ln + lp
                if type(props[p]) == bool:
                    if props[p]:
                        lp = LopperProp( p, -1, ln, [] )
                        ln + lp
                    else:
                        print( "[INFO]: not encoding false boolean type: %s" % p)
                else:
                    if not p in excluded_props:
                        lp = LopperProp( p, -1, ln, props[p] )
                        ln + lp


        lt.resolve()
        lt.sync()

        return lt

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

        Gather a dictionary representation of the properties of a LopperYAML
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
                node_references = []
                if type(node.__dict__[a]) == list:
                    prop_list = node.__dict__[a]
                    for sub_prop in node.__dict__[a]:
                        if type(sub_prop) == dict:
                            node_references.append( sub_prop )
                            prop_list.remove( sub_prop )

                    pdict[a] = prop_list
                    # now iterate the node_refs, if any
                    for n in node_references:
                        for node_reference_prop in n:
                             pdict[node_reference_prop] = n[node_reference_prop]
                else:
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
            print( "node: %s depth: %s" % (node.name,node.depth) )
            print( "   raw: %s" % node )
            print( "   attributes:" )
            for a in node.__dict__:
                if not a.startswith( "_NodeMixin" ):
                    print( "        %s: %s" % (a,node.__dict__[a] ))
            print( "   path: %s" % type(node.path) )
            path_gen=""
            for p in node.path:
                print( "      path node: %s" % p )
                if p.name == "root":
                    if len(node.path) == 1:
                        path_gen += "/"
                else:
                    path_gen += "/" + p.name
            print( "   full path: %s" % path_gen )

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
        self.dct = yaml.load( iny )

        if not self.dct:
            print( "[ERROR]: no data available to load" )
            sys.exit(1)

        self.anytree = LopperDictImporter(Node).import_(self.dct)


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

        self.anytree = LopperTreeImporter(Node).import_(in_tree["/"])


