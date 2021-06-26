#/*
# * Copyright (c) 2020 Xilinx Inc. All rights reserved.
# *
# * Author:
# *       Bruce Ashfield <bruce.ashfield@xilinx.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

import ruamel.yaml as yaml

import json
import sys
import copy

from collections import OrderedDict

from lopper.tree import LopperTree
from lopper.tree import LopperTreePrinter
from lopper.tree import LopperNode
from lopper.tree import LopperProp

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
        for p in node.__props__:
            # if the node is from a yaml source, it may have been json encoded,
            # so try that first and otherwise assign it directly.
            if node._source == "yaml" or node.__props__[p].pclass == "json":
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
                        if verbose:
                            print( "[DBG++]: LopperTreeImporter: json load for prop %s : %s" % (p,node.__props__[p].value))

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

    def __export(self, node, dictcls, attriter, childiter, level=1, verbose = 0):
        attr_values = attriter(self._iter_attr_values(node))
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
                if verbose > 2:
                    print( "[DBG+++]: node: %s has children: %s" % (name,children) )

                new_dict = {}
                for c in reversed(children):
                    if verbose > 2:
                        print( "[DBG+++]:        merging dict: %s" % c )
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
        assert "parent" not in data
        attrs = dict(data)
        verbose = 0

        if verbose:
            print( "[DBG]: ===> __import (%s)" % name )
            print( "            attrs: %s" % attrs )

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

                if verbose:
                    print( "[DBG]      queuing child from dict node: name: %s props: %s" % (k,cdict))

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
                        sub_node_name = "{}@{}".format( k, i )

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

        if verbose:
            print( "[DBG]      creating node with attrs: %s" % attrs )

        node = self.nodecls(parent=parent, **attrs)
        for child in children:
            self.__import(child, parent=node)

        return node

class LopperDumper(yaml.Dumper):
    """Lopper specific dumper

    Any simple formating changes to the yaml output are contained in
    this class.

    Currently it only increases the indent on yaml sequences, but may
    container more adjustments in the future.
    """
    def increase_indent(self, flow=False, indentless=False):
        return super(LopperDumper, self).increase_indent(flow, False)

class LopperYAML():
    """YAML read/writer for Lopper

    A Lopper "container" around a yaml input/output.

    This class is capable of reading a yaml inputfile, and
    creating a LopperTree. It is also capabable of taking a
    LopperTree and creating a yaml description of that tree.

    This is done by internally storing either a yaml or lopper tree input as a
    generic tree structure. The generic tree structure can be converted to a
    LopperTree or Yaml file on demand. Hence we have the capability of
    converting between the two formats as required.
    """
    def __init__( self, yaml_file = None, tree = None, config = None ):
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

        self.boolean_as_int = False
        self.lists_as_nodes = False
        if config:
            try:
                self.boolean_as_int = config.getboolean( 'yaml','bool_as_int' )
            except:
                pass

            try:
                self.lists_as_nodes = config.getboolean( 'yaml','lists_as_nodes' )
            except:
                pass



        if self.yaml_source and self.tree:
            print( "[ERROR]: both yaml and lopper tree provided" )
            sys.exit(1)

        if self.yaml_source:
            self.load_yaml( self.yaml_source )

        if self.tree:
            self.load_tree( self.tree )

    def to_yaml( self, outfile = None, verbose = 0 ):
        """ Export LopperYAML tree to a yaml output file

        Args:
           outfile (string): path to a yaml output file

        Returns:
           Nothing
        """
        if self.anytree:
            # if there's only one child, we use that, which allows us to skip the Anytree
            # "root" node, without any tricks.
            no_root = False
            start_node = self.anytree
            if no_root:
                if len(self.anytree.children) == 1:
                    start_node = self.anytree.children[0]

            # at high verbosity, use an ordered dict for debug reasons
            if verbose > 2:
                dcttype=OrderedDict
            else:
                dcttype=dict

            dct = LopperDictExporter(dictcls=dcttype,attriter=sorted).export(start_node)

            if verbose > 1:
                print( "[DBG++]: dumping exporting dictionary" )
                pprint( dct )

            if not outfile:
                print(yaml.dump(dct))
            else:
                if verbose > 1:
                    print( "[DBG++]: dumping generated yaml to stdout:" )
                    print(yaml.dump(dct))

                with open( outfile, "w") as file:
                    yaml.dump(dct, file)


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
        serialize_json = True
        verbose = 0

        for node in PreOrderIter(self.anytree):
            if node.name == "root":
                ln = lt["/"]
                #ln = LopperNode( -1, None )
                #ln.abs_path = "/"
            else:
                ln = LopperNode( -1, node.name )
                ln.abs_path = self.path( node )

            if verbose:
                lt.__dbg__ = 4
                ln.__dbg__ = 4

            ln._source = "yaml"

            # add the node to the tree
            lt = lt + ln

            props = self.props( node )
            for p in props:
                if verbose:
                    print( "[DBG+]: prop: %s (%s)" % (p,props[p]) )
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

                    if use_json:
                        x = json.dumps(props[p])
                    else:
                        x = props[p]

                    if not skip:
                        if not p in excluded_props:
                            lp = LopperProp( p, -1, ln, x )
                            lp.resolve()
                            if use_json:
                                lp.pclass = "json"
                            # add the property to the node
                            ln + lp
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
                            print( "[INFO]: not encoding false boolean type: %s" % p)
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
        self.dct = yaml.safe_load( iny )

        if not self.dct:
            print( "[ERROR]: no data available to load" )
            sys.exit(1)

        importer = LopperDictImporter(Node)
        importer.lists_as_nodes = self.lists_as_nodes
        self.anytree = importer.import_(self.dct)

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


