#!/usr/bin/python3

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

import libfdt
from libfdt import Fdt, FdtException, QUIET_NOTFOUND, QUIET_ALL

from lopper_tree import *
from lopper_fdt import *

LOPPER_VERSION = "2020.4-beta"

lopper_directory = os.path.dirname(os.path.realpath(__file__))

@contextlib.contextmanager
def stdoutIO(stdout=None):
    old = sys.stdout
    if stdout is None:
        stdout = StringIO()
        sys.stdout = stdout
        yield stdout
        sys.stdout = old

def at_exit_cleanup():
    if device_tree:
        device_tree.cleanup()
    else:
        pass

class LopperAssist:
    """Internal class to contain the details of a lopper assist

    """
    def __init__(self, lop_file, module = "", properties_dict = {}):
        self.module = module
        self.file = lop_file
        # holds specific key,value properties
        self.properties = properties_dict

class LopperSDT:
    """The LopperSDT Class represents and manages the full system DTS file

    In particular this class:
      - wraps a dts/dtb/fdt containing a system description
      - Has a LopperTree representation of the system device tree
      - manages and applies operations to the tree
      - calls modules and assist functions for processing of that tree

    Attributes:
      - dts (string): the source device tree file
      - dtb (blob): the compiled dts
      - FDT (fdt): the primary flattened device tree represention of the dts
      - lops (list): list of loaded lopper operations
      - verbose (int): the verbosity level of operations
      - tree (LopperTree): node/property representation of the system device tree
      - dry_run (bool): whether or not changes should be written to disk
      - output_file (string): default output file for writing

    """
    def __init__(self, sdt_file):
        self.dts = sdt_file
        self.dtb = ""
        self.lops = []
        self.verbose = 0
        self.dry_run = False
        self.assists = []
        self.output_file = ""
        self.cleanup_flag = True
        self.save_temps = False
        self.enhanced = False
        self.FDT = None
        self.tree = None
        self.outdir = "./"
        self.target_domain = ""
        self.load_paths = []

    def __comment_replacer(self,match):
        """private function to translate comments to device tree attributes"""
        s = match.group(0)
        if s.startswith('/'):
            global count
            count = count + 1
            r1 = re.sub( '\"', '\\"', s )
            r2 = "lopper-comment-{0} = \"{1}\";".format(count, r1)
            return r2
        else:
            return s

    def __comment_translate(self,text):
        """private function used to match (and replace) comments in DTS files"""
        global count
        count = 0
        pattern = re.compile(
                r'//.*?$|/\*.*?\*/|\'(?:\\.|[^\\\'])*\'|"(?:\\.|[^\\"])*"',
                re.DOTALL | re.MULTILINE
            )
        return re.sub(pattern, self.__comment_replacer, text)

    def __label_replacer(self,match):
        """private function to translate labels to device tree attributes"""
        s = match.group(0)
        s1 = match.group(1)
        s2 = match.group(2)
        #print( "   label group 0: %s" % s )
        #print( "   label group 1: %s" % s1 )
        #print( "   label group 2: %s" % s2 )
        if s1 and s2:
            #print( "      label match" )
            global lcount
            lcount = lcount + 1
            r1 = s1.lstrip()
            r1 = re.sub( ':', '', r1 )
            r2 = "{0}\nlopper-label-{1} = \"{2}\";".format(s, lcount, r1)
            return r2
        else:
            return s

    def __label_translate(self,text):
        """private function used to match (and replace) labels in DTS files"""
        global lcount
        lcount = 0
        pattern2 = re.compile(
            r'^\s*?\w*?\s*?\:', re.DOTALL
        )
        pattern = re.compile(
            r'^\s*?(\w*?)\s*?\:(.*?)$', re.DOTALL | re.MULTILINE
        )
        return re.sub(pattern, self.__label_replacer, text)

    def setup(self, sdt_file, input_files, include_paths, assists=[], force=False):
        """executes setup and initialization tasks for a system device tree

        setup validates the inputs, and calls the appropriate routines to
        preprocess and compile passed input files (.dts).

        Args:
           sdt_file (String): system device tree path/file
           input_files (list): list of input files (.dts, or .dtb) in addition to the sdt_file
           include_paths (list): list of paths to search for files
           assists (list,optional): list of python assist modules to load. Default is []
           force (bool,optional): flag indicating if files should be overwritten and compilation
                                  forced. Default is False.

        Returns:
           Nothing

        """
        if self.verbose:
            print( "[INFO]: loading dtb and using libfdt to manipulate tree" )

        # check for required support applications
        support_bins = ["dtc", "cpp" ]
        for s in support_bins:
            if self.verbose:
                print( "[INFO]: checking for support binary: %s" % s )
            if not shutil.which(s):
                print( "[ERROR]: support application '%s' not found, exiting" % s )
                sys.exit(2)

        self.use_libfdt = True

        # self.FDT = libfdt.Fdt(open(self.dtb, mode='rb').read())
        current_dir = os.getcwd()

        lop_files = []
        sdt_files = []
        for ifile in input_files:
            if re.search( ".dts$", ifile ):
                # an input file is either a lopper operation file, or part of the
                # system device tree. We can check for compatibility to decide which
                # it is.
                with open(ifile) as f:
                    datafile = f.readlines()
                    found = False
                    for line in datafile:
                        if not found:
                            if re.search( "system-device-tree-v1,lop", line ):
                                lop_files.append( ifile )
                                found = True

                if not found:
                    sdt_files.append( ifile )
            elif re.search( ".dtb$", ifile ):
                lop_files.append( ifile )

        # is the sdt a dts ?
        if re.search( ".dts$", self.dts ):
            # do we have any extra sdt files to concatenate first ?
            fp = ""
            fpp = tempfile.NamedTemporaryFile( delete=False )
            # TODO: if the count is one, we shouldn't be doing the tmp file processing.
            if sdt_files:
                sdt_files.insert( 0, self.dts )

                # this block concatenates all the files into a single dts to
                # compile
                with open( fpp.name, 'wb') as wfd:
                    for f in sdt_files:
                        with open(f,'rb') as fd:
                            shutil.copyfileobj(fd, wfd)

                fp = fpp.name
            else:
                sdt_files.append( sdt_file )
                fp = sdt_file

            if self.enhanced:
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

                        data = re.sub( preamble_regex, '/ {' + '\n\n{0}'.format(comment), data )

                # put the dts start info back in
                data = re.sub( '^', '/dts-v1/;\n\n', data )

                # finally, do comment substitution
                with open(fp_enhanced) as f:
                    fp_comments_as_attributes = self.__comment_translate(data)
                    fp_comments_and_labels_as_attributes = self.__label_translate(fp_comments_as_attributes)

                f = open( fp_enhanced, 'w' )
                f.write( fp_comments_and_labels_as_attributes )
                f.close()

                fp = fp_enhanced

            self.dtb = Lopper.dt_compile( fp, input_files, include_paths, force, self.outdir,
                                          self.save_temps, self.verbose )

            self.FDT = Lopper.dt_to_fdt(self.dtb, 'rb')

            # this is not a snapshot, but a reference, so we'll see all changes to
            # the backing FDT.
            self.tree = lt.LopperTree( self.FDT )

            fpp.close()
        else:
            self.dtb = sdt_file
            self.dts = ""

        if self.verbose:
            print( "" )
            print( "SDT summary:")
            print( "   system device tree: %s" % sdt_files )
            print( "   lops: %s" % lop_files )
            print( "   output: %s" % self.output_file )
            print( "" )

        # Individually compile the input files. At some point these may be
        # concatenated with the main SDT if dtc is doing some of the work, but for
        # now, libfdt is doing the transforms so we compile them separately
        for ifile in lop_files:
            if re.search( ".dts$", ifile ):
                lop = LopperFile( ifile )
                # TODO: this may need an output directory option, right now it drops
                #       it where lopper is called from (which may not be writeable.
                #       hence why our output_dir is set to "./"
                compiled_file = Lopper.dt_compile( lop.dts, "", include_paths, force, self.outdir,
                                                   self.save_temps, self.verbose )
                if not compiled_file:
                    print( "[ERROR]: could not compile file %s" % ifile )
                    sys.exit(1)
                lop.dtb = compiled_file
                self.lops.append( lop )
            elif re.search( ".dtb$", ifile ):
                lop = LopperFile( ifile )
                lop.dts = ""
                lop.dtb = ifile
                self.lops.append( lop )

        for a in assists:
            a_file = self.find_assist( a )
            if a_file:
                self.assists.append( LopperAssist( str(a_file.resolve()) ) )

        self.wrap_assists()

    def cleanup( self ):
        """cleanup any temporary or copied files

        Either called directly, or registered as an atexit handler. Any
        temporary or copied files are removed, as well as other relevant
        cleanup.

        Args:
           None

        Returns:
           Nothing

        """
        # remove any .dtb and .pp files we created
        if self.cleanup and not self.save_temps:
            try:
                os.remove( self.dtb )
                if self.enhanced:
                    os.remove( self.dts + ".enhanced" )
            except:
                # doesn't matter if the remove failed, it means it is
                # most likely gone
                pass

        # note: we are not deleting assists .dtb files, since they
        #       can actually be binary blobs passed in. We are also
        #       not cleaning up the concatenated compiled. pp file, since
        #       it is created with mktmp()

    def write( self, outfilename ):
        """write the system device tree to a file

        Writes the system device tree (modified or not) to the passed
        output file name

        Args:
           outfilename (string): output file name

        Returns:
           Nothing

        """
        byte_array = self.FDT.as_bytearray()

        if self.verbose:
            print( "[INFO]: writing output dtb: %s" % outfilename )

        with open(outfilename, 'wb') as w:
            w.write(byte_array)

    def find_assist(self, assist_name, local_load_paths = []):
        """Locates a python module that matches assist_name

        This routine searches both system (lopper_directory, lopper_directory +
        "assists", and passed paths (local_load_paths) to locate a matching
        python implementation.

        Args:
           assist_name (string): name of the assist to locate
           local_load_paths (list of strings, optional): list of directories to search
                                                         in addition to system dirs

        Returns:
           Path: Path object to the located python module, None on failure

        """
        mod_file = Path( assist_name )
        mod_file_wo_ext = mod_file.with_suffix('')

        if self.verbose > 1:
            print( "find_assist: %s local search: %s" % (assist_name,local_load_paths) )

        try:
            mod_file_abs = mod_file.resolve()
        except FileNotFoundError:
            # check the path from which lopper is running, that directory + assists, and paths
            # specified on the command line
            search_paths =  [ lopper_directory ] + [ lopper_directory + "/assists/" ] + local_load_paths
            for s in search_paths:
                mod_file = Path( s + "/" + mod_file.name )
                try:
                    mod_file_abs = mod_file.resolve()
                    break
                except FileNotFoundError:
                    mod_file_abs = ""

            if not mod_file_abs:
                print( "[ERROR]: module file %s not found" % assist_name )
                return None

        return mod_file

    def wrap_assists(self):
        """wrap assists that have been added to the device tree

        Wraps any command line assists that have been added to the system
        device tree. A standard lop format dtb is generated for any found
        assists, such that they will be loaded in the same manner as
        assists passed directly in lop files.

        Note: this is for internal use only

        Args:
           None

        Returns:
           Nothing

        """
        if self.assists:
            sw = libfdt.Fdt.create_empty_tree( 2048 )
            sw.setprop_str( 0, 'compatible', 'system-device-tree-v1' )
            sw.setprop_u32( 0, 'priority', 1)
            offset = sw.add_subnode( 0, 'lops' )

            assist_count = 0
            for a in set(self.assists):
                lop_name = "lop_{}".format( assist_count )
                offset = sw.add_subnode( offset, lop_name )
                sw.setprop_str( offset, 'compatible', 'system-device-tree-v1,lop,load')
                sw.setprop_str( offset, 'load', a.file )
                lop = LopperFile( 'commandline' )
                lop.dts = ""
                lop.dtb = ""
                lop.fdt = sw

                if self.verbose > 1:
                    print( "[INFO]: generated load lop for assist %s" % a )

                assist_count = assist_count + 1

            self.lops.insert( 0, lop )

    def domain_spec(self, tgt_domain):
        """generate a lop for a command line passed domain

        When a target domain is passed on the command line, we must generate
        a lop dtb for it, so that it can be processed along with other
        operations

        Args:
           tgt_domain (string): path to the node to use as the domain

        Returns:
           Nothing

        """
        # This is called from the command line. We need to generate a lop
        # device tree with:
        #
        # lop_0 {
        #     compatible = "system-device-tree-v1,lop,assist-v1";
        #     node = "/chosen/openamp_r5";
        #     id = "openamp,domain-v1";
        # };
        # and then inject it into self.lops to run first

        sw = libfdt.Fdt.create_empty_tree( 2048 )
        sw.setprop_str( 0, 'compatible', 'system-device-tree-v1' )
        offset = sw.add_subnode( 0, 'lops' )
        offset = sw.add_subnode( offset, 'lop_0' )
        sw.setprop_str( offset, 'compatible', 'system-device-tree-v1,lop,assist-v1')
        sw.setprop_str( offset, 'node', '/chosen/openamp_r5' )
        sw.setprop_str( offset, 'id', 'openamp,domain-v1' )
        lop = LopperFile( 'commandline' )
        lop.dts = ""
        lop.dtb = ""
        lop.fdt = sw

        self.lops.insert( 0, lop )

    def node_find( self, node_prefix ):
        """Finds a node by its prefix

        Wrapper around the Lopper routine of the same name, to abstract the
        FDT that is part of the LopperSDT class.
        """
        return Lopper.node_find( self.FDT, node_prefix )

    def find_compatible_assist( self, cb_node = 0, cb_id = "", mask = "" ):
        """Finds a registered assist that is compatible with a given ID

        Searches the registered assists for one that is compatible with an ID.

        The is_compat() routine is called for each registered module. If an
        assist is capabable of handling a given ID, it returns True and
        associated actions can then be taken.

        I addition to an ID string, a mask can optionally be provided to this
        routine. Any assists that have registered a mask, will have that
        checked, before calling the is_compat() routine. This allows assists to
        be generically registered, but filtered by the caller rather than only
        their is_compat() routines.

        Args:
            cb_node (int,optional): node offset to be tested. Default is 0 (root)
            cb_id (string,optional): ID to be tested for compatibility. Default is ""
            mask (string,optional): caller mask for filtering nodes. Default is ""

        Returns:
            function reference: the callback routine, or "", if no compatible routine found

        """
        # default for cb_node is "start at root (0)"
        cb_func = []
        if self.assists:
            for a in self.assists:
                if a.module:
                    # if the passed id is empty, check to see if the assist has
                    # one as part of its data
                    if not cb_id:
                        try:
                            cb_id = a.properties['id']
                        except:
                            cb_id = ""

                    # if a non zero mask was passed, and the module has a mask, make
                    # sure they match before even considering it.
                    mask_ok = True
                    try:
                        assist_mask = a.properties['mask']
                    except:
                        assist_mask = ""

                    if mask and assist_mask:
                        mask_ok = False
                        # TODO: could be a regex
                        if mask == assist_mask:
                            mask_ok = True

                    if mask_ok:
                        cb_f = a.module.is_compat( cb_node, cb_id )

                    if cb_f:
                        cb_func.append( cb_f )
                        # we could double check that the function exists with this call:
                        #    func = getattr( m, cbname )
                        # but for now, we don't
                else:
                    print( "[WARNING]: a configured assist has no module loaded" )
        else:
            print( "[WARNING]: no modules loaded, no compat search is possible" )

        return cb_func

    def perform_lops(self):
        """Execute all loaded lops

        Iterates and executes all the loaded lopper operations (lops) for the
        System Device tree.

        The lops are processed in priority order (priority specified at the file
        level), and the rules processed in order as they appear in the lop file.

        lopper operations can immediately process the output of the previous
        operation and hence can be stacked to perform complex operations.

        Args:
            None

        Returns:
            Nothing

        """
        # was --target passed on the command line ?
        if self.target_domain:
            self.domain_spec(target_domain)

        # force verbose output if --dryrun was passed
        if self.dryrun:
            self.verbose = 2

        if self.verbose:
            print( "[NOTE]: \'%d\' lopper operation input(s) available" % len(self.lops))

        lops_runqueue = {}
        for pri in range(1,10):
            lops_runqueue[pri] = []

        # iterate the lops, look for priority. If we find those, we'll run then first
        for x in self.lops:
            if not x.fdt:
                lops_fdt = libfdt.Fdt(open(x.dtb, mode='rb').read())
                x.fdt = lops_fdt
            else:
                lops_fdt = x.fdt

            lops_file_priority = Lopper.prop_get( lops_fdt, 0, "priority" )
            if not lops_file_priority:
                lops_file_priority = 5

            lops_runqueue[lops_file_priority].append(x)

        if self.verbose > 2:
            print( "[DBG+]: lops runqueue: %s" % lops_runqueue )

        # iterate over the lops (by lop-file priority)
        for pri in range(1,10):
            for x in lops_runqueue[pri]:
                if not x.fdt:
                    lops_fdt = libfdt.Fdt(open(x.dtb, mode='rb').read())
                else:
                    lops_fdt = x.fdt

                # Get all the nodes with a lop property
                lops_nodes = Lopper.nodes_with_property( lops_fdt, "compatible", "system-device-tree-v1,lop.*", "lops" )
                for n in lops_nodes:
                    prop = lops_fdt.getprop( n, "compatible" )
                    val = Lopper.prop_get( lops_fdt, n, "compatible" )
                    node_name = lops_fdt.get_name( n )

                    noexec = Lopper.prop_get( lops_fdt, n, "noexec" )
                    if noexec:
                        if self.verbose > 1:
                            print( "[DBG+]: noexec flag found, skipping lop" )
                        continue

                    if self.verbose:
                        print( "[INFO]: ------> processing lop: %s" % val )
                    if self.verbose > 2:
                        print( "[DBG+]: prop: %s val: %s" % (prop.name, val ))
                        print( "[DBG+]: node name: %s" % node_name )

                    # TODO: need a better way to search for the possible lop types, i.e. a dict
                    if re.search( ".*,output$", val ):
                        output_file_name = Lopper.prop_get( lops_fdt, n, 'outfile' )
                        if not output_file_name:
                            print( "[ERROR]: cannot get output file name from lop" )
                            sys.exit(1)

                        if self.verbose > 1:
                            print( "[DBG+]: outfile is: %s" % output_file_name )

                        output_nodes = Lopper.prop_get( lops_fdt, n, 'nodes', LopperFmt.COMPOUND, LopperFmt.STRING )

                        if self.verbose > 1:
                            print( "[DBG+]: output selected are: %s" % output_nodes )

                        if "*" in output_nodes:
                            ff = libfdt.Fdt(self.FDT.as_bytearray())
                        else:
                            # Note: we may want to switch this around, and copy the old tree and
                            #       delete nodes. This will be important if we run into some
                            #       strangely formatted ones that we can't copy.
                            ff = libfdt.Fdt.create_empty_tree( self.FDT.totalsize() )
                            for o_node in output_nodes:

                                split_node = o_node.split(":")
                                o_node = split_node[0]
                                o_prop_name = ""
                                o_prop_val = ""
                                if len(split_node) > 1:
                                    o_prop_name = split_node[1]
                                    if len(split_node) > 2:
                                        o_prop_val = split_node[2]

                                if o_prop_name:
                                    if self.verbose > 1:
                                        print( "[DBG+]: output prop: %s val: %s" % (o_prop_name, o_prop_val))

                                # TODO: this really should be using node_find() and we should make sure the
                                #       output 'lop' has full paths.

                                # regex capability in the output, comes from this call, where o_node can be a regex
                                # and return multiple matches

                                # TODO: convert this to use the tree routines for find and copy
                                node_to_copy, nodes_to_copy = Lopper.node_find_by_name( self.FDT, o_node, 0, True )
                                if node_to_copy == -1:
                                    print( "[WARNING]: could not find node to copy: %s" % o_node )
                                else:
                                    for n in nodes_to_copy:
                                        copy_node_flag = True

                                        # we test for a property in the node if it was defined
                                        if o_prop_name:
                                            copy_node_flag = False
                                            p = self.tree[n].propval(o_prop_name)
                                            if o_prop_val:
                                                if p:
                                                    if o_prop_val in p:
                                                        copy_node_flag = True

                                        if copy_node_flag:
                                            node_to_copy_path = Lopper.node_abspath( self.FDT, n )
                                            new_node = Lopper.node_copy_from_path( self.FDT, node_to_copy_path, ff, node_to_copy_path, self.verbose )
                                            if not new_node:
                                                print( "[ERROR]: unable to copy node: %s" % node_to_copy_path, )

                        if not self.dryrun:
                            output_file_full = self.outdir + "/" + output_file_name
                            Lopper.write_fdt( ff, output_file_full, self, True, self.verbose, self.enhanced )
                        else:
                            print( "[NOTE]: dryrun detected, not writing output file %s" % output_file_name )

                    if re.search( ".*,assist-v1$", val ):
                        # also note: this assist may change from being called as part of the
                        # tranform loop, to something that is instead called by walking the
                        # entire device tree, looking for matching nodes and making assists at
                        # that moment.
                        #
                        # but that sort of node walking, will invoke the assists out of order
                        # with other lopper operations, so it isn't particularly feasible or
                        # desireable.
                        #
                        cb_tgt_node_name = Lopper.prop_get( lops_fdt, n, 'node' )
                        if not cb_tgt_node_name:
                            print( "[ERROR]: cannot find target node for the assist" )
                            sys.exit(1)

                        cb = Lopper.prop_get( lops_fdt, n, 'assist' )
                        cb_id = Lopper.prop_get( lops_fdt, n, 'id' )
                        cb_node = Lopper.node_find( self.FDT, cb_tgt_node_name )
                        if cb_node < 0:
                            if self.werror:
                                print( "[ERROR]: cannot find assist target node in tree" )
                                sys.exit(1)
                            else:
                                continue
                        if self.verbose:
                            print( "[INFO]: assist lop detected" )
                            if cb:
                                print( "        cb: %s" % cb )
                            print( "        id: %s" % cb_id )

                        cb_funcs = self.find_compatible_assist( cb_node, cb_id )
                        if cb_funcs:
                            for cb_func in cb_funcs:
                                try:
                                    if not cb_func( cb_node, self, self.verbose ):
                                        print( "[WARNING]: the assist returned false, check for errors ..." )
                                except Exception as e:
                                    print( "[WARNING]: assist %s failed: %s" % (cb_func,e) )
                                    exc_type, exc_obj, exc_tb = sys.exc_info()
                                    fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
                                    print(exc_type, fname, exc_tb.tb_lineno)
                                    # exit if warnings are treated as errors
                                    if self.werror:
                                        sys.exit(1)
                        else:
                            print( "[INFO]: no compatible assist found, skipping" )

                    if re.search( ".*,lop,load$", val ):
                        prop_id = ""
                        prop_extension = ""

                        if self.verbose:
                            print( "--------------- [INFO]: node %s is a load module lop" % node_name )
                        try:
                            load_prop = lops_fdt.getprop( n, 'load' ).as_str()
                        except:
                            load_prop = ""

                        if load_prop:
                            # for submodule loading
                            for p in self.load_paths:
                                if p not in sys.path:
                                    sys.path.append( p )

                            if self.verbose:
                                print( "[INFO]: loading module %s" % load_prop )

                            mod_file = self.find_assist( load_prop, self.load_paths )
                            if not mod_file:
                                print( "[ERROR]: unable to find assist (%s)" % load_prop )
                                sys.exit(1)

                            mod_file_abs = mod_file.resolve()

                            try:
                                imported_module = SourceFileLoader( mod_file.name, str(mod_file_abs) ).load_module()
                            except Exception as e:
                                print( "[ERROR]: could not load assist: %s: %s" % (mod_file_abs,e) )
                                sys.exit(1)

                            assist_properties = {}
                            try:
                                props = lops_fdt.getprop( n, 'props' )
                                if props:
                                    props = Lopper.property_value_decode( props, 0, LopperFmt.COMPOUND )
                            except:
                                # does the module have a "props" routine for extra querying ?
                                try:
                                    props = imported_module.props()
                                except:
                                    props = []

                            for p in props:
                                # TODO: we can generate and evaluate these generically, right now, this
                                #       is ok as a proof of concept only
                                if p == "file_ext":
                                    try:
                                        prop_extension = lops_fdt.getprop( n, 'file_ext' ).as_str()
                                        # TODO: debug why the call below can't figure out that this is a
                                        #       string property.
                                        # Lopper.prop_get( lops_fdt, n, "file_ext" )
                                    except:
                                        try:
                                            prop_extension = imported_module.file_ext()
                                        except:
                                            prop_extension = ""

                                    assist_properties['mask'] = prop_extension

                                if p == "id":
                                    try:
                                        prop_id = lops_fdt.getprop( n, 'id' ).as_str()
                                    except:
                                        try:
                                            prop_id = imported_module.id()
                                        except:
                                            prop_id = ""

                                    assist_properties['id'] = prop_id

                            # TODO: move this "assist already available" check into a function
                            already_loaded = False
                            if self.assists:
                                for a in self.assists:
                                    try:
                                        if Path(a.file).resolve() == mod_file.resolve():
                                            already_loaded = True
                                            a.module = imported_module
                                            a.properties = assist_properties
                                    except:
                                        pass
                            if not already_loaded:
                                if verbose > 1:
                                    if prop_extension:
                                        print( "[INFO]: loading assist with properties (%s,%s)" % (prop_extension, prop_id) )

                                self.assists.append( LopperAssist( mod_file.name, imported_module, assist_properties ) )

                    if re.search( ".*,lop,add$", val ):
                        if self.verbose:
                            print( "[INFO]: node add lop found" )

                        src_node_name = Lopper.prop_get( lops_fdt, n, "node_src" )
                        if not src_node_name:
                            print( "[ERROR]: node add detected, but no node name found" )
                            sys.exit(1)

                        lops_node_path = Lopper.node_abspath( lops_fdt, n )
                        src_node_path = lops_node_path + "/" + src_node_name

                        dest_node_path = Lopper.prop_get( lops_fdt, n, "node_dest" )
                        if not dest_node_path:
                            dest_node_path = "/" + src_node_name

                        if self.verbose:
                            print( "[INFO]: node name: %s node path: %s" % (src_node_path, dest_node_path) )

                        # TODO: replace this copy with a sdt.tree node to node copy.
                        if not Lopper.node_copy_from_path( lops_fdt, src_node_path, self.FDT, dest_node_path, self.verbose ):
                            print( "[ERROR]: unable to copy node: %s" % src_node_name )
                            sys.exit(1)
                        else:
                            # self.FDT is backs the tree object, so we need to sync
                            self.tree.sync()

                    if re.search( ".*,lop,modify$", val ):
                        if self.verbose:
                            print( "[INFO]: node %s is a compatible property modify lop" % node_name )
                        try:
                            prop = lops_fdt.getprop( n, 'modify' ).as_str()
                        except:
                            prop = ""

                        # was there a regex passed for node matching ?
                        nodes = Lopper.prop_get( lops_fdt, n, "nodes" )
                        if prop:
                            if self.verbose:
                                print( "[INFO]: modify property found: %s" % prop )

                            # format is: "path":"property":"replacement"
                            #    - modify to "nothing", is a remove operation
                            #    - modify with no property is node operation (rename or remove)
                            modify_expr = prop.split(":")
                            if self.verbose:
                                print( "[INFO]: modify path: %s" % modify_expr[0] )
                                print( "        modify prop: %s" % modify_expr[1] )
                                print( "        modify repl: %s" % modify_expr[2] )
                                if nodes:
                                    print( "        modify regex: %s" % nodes )

                            if modify_expr[1]:
                                # property operation
                                if not modify_expr[2]:
                                    if self.verbose:
                                        print( "[INFO]: property remove operation detected: %s %s" % (modify_expr[0], modify_expr[1]))

                                    try:
                                        # TODO: make a special case of the property_modify_below

                                        # just to be sure that any other pending changes have been
                                        # written, since we need accurate node IDs
                                        self.tree.sync()
                                        nodes = self.tree.subnodes( self.tree[modify_expr[0]] )
                                        for n in nodes:
                                            try:
                                                n.delete( modify_expr[1] )
                                            except:
                                                # no big deal if it doesn't have the property
                                                pass
                                            self.tree.sync()
                                    except Exception as e:
                                        print( "[WARNING]: unable to remove property %s (%s)" % (modify_expr[0],e))
                                else:
                                    if self.verbose:
                                        print( "[INFO]: property modify operation detected" )

                                    # just to be sure that any other pending changes have been
                                    # written, since we need accurate node IDs
                                    self.tree.sync()
                                    nodes = self.tree.nodes( modify_expr[0] )
                                    for n in nodes:
                                        n[modify_expr[1]] = [ modify_expr[2] ]
                                        self.tree.sync()
                            else:
                                # node operation
                                # in case /<name>/ was passed as the new name, we need to drop them
                                # since they aren't valid in set_name()
                                if modify_expr[2]:
                                    modify_expr[2] = modify_expr[2].replace( '/', '' )
                                    try:
                                        # change the name of the node
                                        # hmm, should this be a copy and delete ? versus just a name change ?
                                        self.tree[modify_expr[0]].name = modify_expr[2]
                                        self.tree.sync( self.FDT )
                                    except Exception as e:
                                        print( "[ERROR]:cannot rename node '%s' to '%s' (%s)" %(modify_expr[0], modify_expr[2], e))
                                        sys.exit(1)
                                else:
                                    # first we see if the node prefix is an exact match
                                    # node_to_remove = Lopper.node_find( self.FDT, modify_expr[0] )
                                    try:
                                        node_to_remove = self.tree[modify_expr[0]]
                                    except:
                                        node_to_remove = None

                                    if not node_to_remove:
                                        print( "[WARNING]: Cannot find node %s for delete operation" % modify_expr[0] )
                                        if self.werror:
                                            sys.exit(1)
                                    else:
                                        try:
                                            self.tree.delete( node_to_remove )
                                            self.tree.sync( self.FDT )
                                        except:
                                            print( "[WARNING]: could not remove node number: %s" % node_to_remove )


class LopperFile:
    """Internal class to contain the details of a lopper file

    Attributes:
       - dts: the dts source file path for a lop
       - dtb: the compiled dtb file path for a lop
       - fdt: the loaded FDT representation of the dtb

    """
    def __init__(self, lop_file):
        self.dts = lop_file
        self.dtb = ""
        self.fdt = ""

def usage():
    prog = os.path.basename(sys.argv[0])
    print('Usage: %s [OPTION] <system device tree> [<output file>]...' % prog)
    print('  -v, --verbose       enable verbose/debug processing (specify more than once for more verbosity)')
    print('  -t, --target        indicate the starting domain for processing (i.e. chosen node or domain label)' )
    print('    , --dryrun        run all processing, but don\'t write any output files' )
    print('  -d, --dump          dump a dtb as dts source' )
    print('  -i, --input         process supplied input device tree description')
    print('  -a, --assist        load specified python assist (for node or output processing)' )
    print('  -A, --assist-paths  colon separated lists of paths to search for assist loading' )
    print('  -o, --output        output file')
    print('  -f, --force         force overwrite output file(s)')
    print('    , --werror        treat warnings as errors' )
    print('  -S, --save-temps    don\'t remove temporary files' )
    print('  -h, --help          display this help and exit')
    print('  -O, --outdir        directory to use for output files')
    print('    , --version       output the version and exit')
    print('')

def main():
    global inputfiles
    global output
    global output_file
    global sdt
    global sdt_file
    global verbose
    global force
    global dump_dtb
    global target_domain
    global dryrun
    global assists
    global werror
    global save_temps
    global enhanced_print
    global outdir
    global load_paths

    sdt = None
    verbose = 0
    output = ""
    inputfiles = []
    force = False
    dump_dtb = False
    dryrun = False
    target_domain = ""
    assists = []
    werror = False
    save_temps = False
    enhanced_print = False
    outdir="./"
    load_paths = []
    try:
        opts, args = getopt.getopt(sys.argv[1:], "A:t:dfvdhi:o:a:SO:", [ "assist-paths=", "outdir", "enhanced", "save-temps", "version", "werror","target=", "dump", "force","verbose","help","input=","output=","dryrun","assist="])
    except getopt.GetoptError as err:
        print('%s' % str(err))
        usage()
        sys.exit(2)

    if opts == [] and args == []:
        usage()
        sys.exit(1)

    for o, a in opts:
        if o in ('-v', "--verbose"):
            verbose = verbose + 1
        elif o in ('-d', "--dump"):
            dump_dtb = True
        elif o in ('-f', "--force"):
            force = True
        elif o in ('-h', '--help'):
            usage()
            sys.exit(0)
        elif o in ('-i', '--input'):
            inputfiles.append(a)
        elif o in ('-a', '--assist'):
            assists.append(a)
        elif o in ('-A', '--assist-path'):
            load_paths += a.split(":")
        elif o in ('-O', '--outdir'):
            outdir = a
        elif o in ('-t', '--target'):
            target_domain = a
        elif o in ('-o', '--output'):
            output = a
        elif o in ('--dryrun'):
            dryrun=True
        elif o in ('--werror'):
            werror=True
        elif o in ('-S', '--save-temps' ):
            save_temps=True
        elif o in ('--enhanced' ):
            enhanced_print = True
        elif o in ('--version'):
            print( "%s" % LOPPER_VERSION )
            sys.exit(0)
        else:
            assert False, "unhandled option"

    # any args should be <system device tree> <output file>
    for idx, item in enumerate(args):
        # validate that the system device tree file exists
        if idx == 0:
            sdt = item
            sdt_file = Path(sdt)
            try:
                my_abs_path = sdt_file.resolve()
            except FileNotFoundError:
                # doesn't exist
                print( "Error: system device tree %s does not exist" % sdt )
                sys.exit(1)

        # the last input is the output file. It can't already exist, unless
        # --force was passed
        if idx == 1:
            if output:
                print( "Error: output was already provided via -o\n")
                usage()
                sys.exit(1)
            else:
                output = item
                output_file = Path(output)
                if output_file.exists():
                    if not force:
                        print( "Error: output file %s exists, and -f was not passed" % output )
                        sys.exit(1)

    if not sdt:
        print( "[ERROR]: no system device tree was supplied\n" )
        usage()
        sys.exit(1)

    if outdir != "./":
        op = Path( outdir )
        try:
            op.resolve()
        except:
            print( "[ERROR]: output directory \"%s\" does not exist" % outdir )
            sys.exit(1)

    # check that the input files (passed via -i) exist
    for i in inputfiles:
        inf = Path(i)
        if not inf.exists():
            print( "Error: input file %s does not exist" % i )
            sys.exit(1)

        valid_ifile_types = [ ".dtsi", ".dtb", ".dts" ]
        itype = Lopper.input_file_type(i)
        if not itype in valid_ifile_types:
            print( "[ERROR]: unrecognized input file type passed" )
            sys.exit(1)



if __name__ == "__main__":

    # Main processes the command line, and sets some global variables we
    # use below
    main()

    if dump_dtb:
        Lopper.dtb_dts_export( sdt, verbose )
        sys.exit(0)

    device_tree = LopperSDT( sdt )

    atexit.register(at_exit_cleanup)

    # set some flags before we process the tree.
    device_tree.dryrun = dryrun
    device_tree.verbose = verbose
    device_tree.werror = werror
    device_tree.output_file = output
    device_tree.cleanup_flag = True
    device_tree.save_temps = save_temps
    device_tree.enhanced = enhanced_print
    device_tree.outdir = outdir
    device_tree.target_domain = target_domain
    device_tree.load_paths = load_paths

    device_tree.setup( sdt, inputfiles, "", assists, force )
    
    device_tree.perform_lops()

    if not dryrun:
        Lopper.write_phandle_map( device_tree.FDT, output + ".phandle", device_tree.verbose )
        Lopper.write_fdt( device_tree.FDT, output, device_tree, True, device_tree.verbose, enhanced_print )
    else:
        print( "[INFO]: --dryrun was passed, output file %s not written" % output )

    device_tree.cleanup()
