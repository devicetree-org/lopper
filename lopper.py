#!/usr/bin/env python3

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

    def setup(self, sdt_file, input_files, include_paths, force=False):
        """executes setup and initialization tasks for a system device tree

        setup validates the inputs, and calls the appropriate routines to
        preprocess and compile passed input files (.dts).

        Args:
           sdt_file (String): system device tree path/file
           input_files (list): list of input files (.dts, or .dtb) in addition to the sdt_file
           include_paths (list): list of paths to search for files
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
            # the system device tree is a dtb
            self.dtb = sdt_file
            self.dts = sdt_file
            self.FDT = Lopper.dt_to_fdt(self.dtb, 'rb')
            self.tree = lt.LopperTree( self.FDT )

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

    def assists_setup( self, assists = []):
        """
                   assists (list,optional): list of python assist modules to load. Default is []
        """
        for a in assists:
            a_file = self.assist_find( a )
            if a_file:
                self.assists.append( LopperAssist( str(a_file.resolve()) ) )

        self.assists_wrap()

    def module_setup( self, module_name, module_args = [] ):
        sw = libfdt.Fdt.create_empty_tree( 2048 )
        sw.setprop_str( 0, 'compatible', 'system-device-tree-v1' )
        sw.setprop_u32( 0, 'priority', 3)
        offset = sw.add_subnode( 0, 'lops' )

        mod_count = 0
        lop_name = "lop_{}".format( mod_count )
        offset = sw.add_subnode( offset, lop_name )
        sw.setprop_str( offset, 'compatible', 'system-device-tree-v1,lop,assist-v1')
        sw.setprop_str( offset, 'node', '/' )

        if module_args:
            module_arg_string = ""
            for m in module_args:
                module_arg_string = module_arg_string + " " + m
                sw.setprop_str( offset, 'options', module_arg_string )

        sw.setprop_str( offset, 'id', "module," + module_name )
        lop = LopperFile( 'commandline' )
        lop.dts = ""
        lop.dtb = ""
        lop.fdt = sw

        if self.verbose > 1:
            print( "[INFO]: generated assist run for %s" % module_name )

        self.lops.insert( 0, lop )

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
                if self.dtb != self.dts:
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

    def write( self, fdt = None, output_filename = None, overwrite = True, enhanced = False ):
        """Write a system device tree to a file

        Write a fdt (or system device tree) to an output file. This routine uses
        the output filename to determine if a module should be used to write the
        output.

        If the output format is .dts or .dtb, Lopper takes care of writing the
        output. If it is an unrecognized output type, the available assist
        modules are queried for compatibility. If there is a compatible assist,
        it is called to write the file, otherwise, a warning or error is raised.

        Args:
            fdt (fdt,optional): source flattened device tree to write
            output_filename (string,optional): name of the output file to create
            overwrite (bool,optional): Should existing files be overwritten. Default is True.
            enhanced(bool,optional): whether enhanced printing should be performed. Default is False

        Returns:
            Nothing

        """
        if not output_filename:
            output_filename = self.output_file

        if not output_filename:
            return

        fdt_to_write = fdt
        if not fdt_to_write:
            fdt_to_write = self.FDT

        if re.search( ".dtb", output_filename ):
            Lopper.write_fdt( fdt_to_write, output_filename, True, self.verbose )

        elif re.search( ".dts", output_filename ):
            if enhanced:
                o = Path(output_filename)
                if o.exists() and not overwrite:
                    print( "[ERROR]: output file %s exists and force overwrite is not enabled" % output_filename )
                    sys.exit(1)

                printer = lt.LopperTreePrinter( fdt_to_write, True, output_filename, self.verbose )
                printer.exec()
            else:
                Lopper.write_fdt( fdt_to_write, output_filename, overwrite, self.verbose, False )

        else:
            # we use the outfile extension as a mask
            (out_name, out_ext) = os.path.splitext(output_filename)
            cb_funcs = self.find_compatible_assist( 0, "", out_ext )
            if cb_funcs:
                for cb_func in cb_funcs:
                    try:
                        out_tree = lt.LopperTreePrinter( fdt_to_write, True, output_filename, self.verbose )
                        if not cb_func( 0, out_tree, { 'outfile': output_filename, 'verbose' : self.verbose } ):
                            print( "[WARNING]: the assist returned false, check for errors ..." )
                    except Exception as e:
                        print( "[WARNING]: output assist %s failed: %s" % (cb_func,e) )
                        exc_type, exc_obj, exc_tb = sys.exc_info()
                        fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
                        print(exc_type, fname, exc_tb.tb_lineno)
                        if self.werror:
                            sys.exit(1)
            else:
                if self.verbose:
                    print( "[INFO]: no compatible output assist found, skipping" )
                if self.werror:
                    sys.exit(2)

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


    def assist_find(self, assist_name, local_load_paths = []):
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
            print( "[DBG+]: assist_find: %s local search: %s" % (assist_name,local_load_paths) )

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

                if not mod_file_abs and not mod_file.name.endswith( ".py"):
                    # try it with a .py
                    mod_file = Path( s + "/" + mod_file.name + ".py" )
                    try:
                        mod_file_abs = mod_file.resolve()
                        break
                    except FileNotFoundError:
                        mod_file_abs = ""


            if not mod_file_abs:
                print( "[ERROR]: module file %s not found" % assist_name )
                return None

        return mod_file

    def assists_wrap(self):
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

    def exec_lop( self, lops_fdt, lop_node_number, options = None ):
        """Executes a a lopper operation (lop)

        Runs a lopper operation against the system device tree.

        Details of the lop are in the lops_fdt, with extra parameters and lop
        specific information from the caller being passed in the options
        variable.

        Args:
            lops_fdt (FDT): lopper operation flattened device tree
            lop_node_number (int): node number for the operation in lops_fdt
            options (dictionary,optional): lop specific options passed from the caller

        Returns:
            boolean

        """

        lop = Lopper.property_get( lops_fdt, lop_node_number, "compatible", LopperFmt.COMPOUND )
        lop_type = lop[0]
        try:
            lop_args = lop[1]
        except:
            lop_args = ""

        if self.verbose > 1:
            print( "[DBG++]: executing lop: %s" % lop_type )

        lops_tree = lt.LopperTree( lops_fdt )
        this_lop_node = lops_tree[lop_node_number]

        if re.search( ".*,exec.*$", lop_type ):
            if self.verbose > 1:
                print( "[DBG++]: code exec jump" )
            try:
                try:
                    node_spec = this_lop_node['node'].value[0]
                except:
                    if self.tree.__selected__:
                        node_spec = self.tree.__selected__[0]
                    else:
                        node_spec = ""

                if not options:
                    options = {}

                try:
                    options_spec = this_lop_node['options'].value
                except:
                    options_spec = ""

                if options_spec:
                    for o in options_spec:
                        opt_key,opt_val = o.split(":")
                        if opt_key:
                            options[opt_key] = opt_val

                exec_tgt = this_lop_node['exec'].value[0]
                target_node = lops_tree.pnode( exec_tgt )
                if self.verbose > 1:
                    print( "[DBG++]: exec phandle: %s target: %s" % (exec_tgt,target_node))

                if target_node:
                    try:
                        if node_spec:
                            options['start_node'] = node_spec

                        ret = self.exec_lop( lops_fdt, target_node.number, options )
                    except Exception as e:
                        print( "[WARNING]: exec block caused exception: %s" % e )
                        ret = False

                    return ret
                else:
                    return False

            except Exception as e:
                print( "[WARNING]: exec lop exception: %s" % e )
                return False

        if re.search( ".*,print.*$", lop_type ):
            print_props = this_lop_node.props('print.*')
            for print_prop in print_props:
                for line in print_prop.value:
                    if type(line) == str:
                        print( line )
                    else:
                        # is it a phandle?
                        node = self.tree.pnode(line)
                        if node:
                            print( "%s {" % node )
                            for p in node:
                                print( "    %s" % p )
                            print( "}" )

        if re.search( ".*,select.*$", lop_type ):
            select_props = this_lop_node.props( 'select.*' )

            #
            # to do an "or" condition
            #    select_1 = "/path/or/regex/to/nodes:prop:val";
            #    select_2 = "/path/or/2nd/regex:prop2:val2";
            #
            # to do an "and" condition:
            #    select_1 = "/path/or/regex/to/nodes:prop:val";
            #    select_2 = ":prop2:val2";
            #
            selected_nodes = []
            for sel in select_props:
                if sel.value == ['']:
                    if self.verbose > 1:
                        print( "[DBG++]: clearing selected nodes" )
                    self.tree.__selected__ = []
                else:
                    # if different node regex + properties are listed in the same
                    # select = "foo","bar","blah", they are always AND conditions.
                    for s in sel.value:
                        if self.verbose > 1:
                            print( "[DBG++]: running node selection: %s" % s )

                        try:
                            node_regex, prop, prop_val = s.split(":")
                        except:
                            node_regex = s
                            prop = ""
                            prop_val = ""

                        if node_regex:
                            # if selected_nodes:
                            #     selected_nodes_possible = selected_nodes
                            # else:
                            selected_nodes_possible = self.tree.nodes( node_regex )
                        else:
                            # if the node_regex is empty, we operate on previously
                            # selected nodes.
                            if selected_nodes:
                                selected_nodes_possible = selected_nodes
                            else:
                                selected_nodes_possible = self.tree.__selected__

                        if self.verbose > 1:
                            print( "[DBG++]: selected potential nodes %s" % selected_nodes_possible )
                            for n in selected_nodes_possible:
                                print( "       %s" % n )

                        if prop and prop_val:
                            # construct a test prop, so we can use the internal compare
                            test_prop = lt.LopperProp( prop, -1, None, [prop_val] )
                            test_prop.resolve( None )

                            # we need this list(), since the removes below will yank items out of
                            # our iterator if we aren't careful
                            for sl in list(selected_nodes_possible):
                                try:
                                    sl_prop = sl[prop]
                                except Exception as e:
                                    sl_prop = None
                                    are_they_equal = False

                                if sl_prop:
                                    if self.verbose > 2:
                                        test_prop.__dbg__ = self.verbose

                                    are_they_equal = test_prop.compare( sl_prop )
                                    if are_they_equal:
                                        if not sl in selected_nodes:
                                            selected_nodes.append( sl )
                                    else:
                                        # no match, you are out! (only if this is an AND operation though, which
                                        # is indicated by the lack of a node regex)
                                        if not node_regex:
                                            if sl in selected_nodes:
                                                selected_nodes.remove( sl )
                                else:
                                    # no prop, you are out! (only if this is an AND operation though, which
                                    # is indicated by the lack of a node regex)
                                    if not node_regex:
                                        if sl in selected_nodes:
                                            selected_nodes.remove( sl )

                    if self.verbose > 1:
                        print( "[DBG++]: selected nodes %s" % selected_nodes )
                        for n in selected_nodes:
                            print( "    %s" % n )

            # update the tree selection with our results
            self.tree.__selected__ = selected_nodes

        if re.search( ".*,meta.*$", lop_type ):
            if re.search( "phandle-desc", lop_args ):
                if self.verbose > 1:
                    print( "[DBG++]: processing phandle meta data" )
                Lopper.phandle_possible_prop_dict = OrderedDict()
                for p in this_lop_node:
                    Lopper.phandle_possible_prop_dict[p.name] = [ p.value[0] ]

        if re.search( ".*,output$", lop_type ):
            try:
                output_file_name = this_lop_node['outfile'].value[0]
            except:
                print( "[ERROR]: cannot get output file name from lop" )
                sys.exit(1)

            if self.verbose > 1:
                print( "[DBG+]: outfile is: %s" % output_file_name )

            output_nodes = []
            try:
                output_regex = this_lop_node['nodes'].value
            except:
                output_regex = []

            if not output_regex:
                if self.tree.__selected__:
                    output_nodes = self.tree.__selected__

            if not output_regex and not output_nodes:
                return False

            if self.verbose > 1:
                print( "[DBG+]: output regex: %s" % output_regex )

            output_tree = None
            if output_regex:
                output_nodes = []
                # select some nodes!
                if "*" in output_regex:
                    output_tree = lt.LopperTree( self.FDT, True )
                else:
                    # we can gather the output nodes and unify with the selected
                    # copy below.
                    for regex in output_regex:

                        split_node = regex.split(":")
                        o_node_regex = split_node[0]
                        o_prop_name = ""
                        o_prop_val = ""
                        if len(split_node) > 1:
                            o_prop_name = split_node[1]
                            if len(split_node) > 2:
                                o_prop_val = split_node[2]

                        # Note: we may want to switch this around, and copy the old tree and
                        #       delete nodes. This will be important if we run into some
                        #       strangely formatted ones that we can't copy.

                        try:
                            # if there's no / anywhere in the regex, then it is just
                            # a node name, and we need to wrap it in a regex. This is
                            # for compatibility with when just node names were allowed
                            c = re.findall( '/', o_node_regex )
                            if not c:
                                o_node_regex = ".*" + o_node_regex

                            o_nodes = self.tree.nodes(o_node_regex)
                            if not o_nodes:
                                # was it a label ?
                                label_nodes = []
                                try:
                                    o_nodes = self.tree.lnodes(o_node_regex)
                                except Exception as e:
                                    pass

                            for o in o_nodes:
                                # we test for a property in the node if it was defined
                                if o_prop_name:
                                    p = self.tree[o].propval(o_prop_name)
                                    if o_prop_val:
                                        if p:
                                            if o_prop_val in p:
                                                if not o in output_nodes:
                                                    output_nodes.append( o )
                                else:
                                    if not o in output_nodes:
                                        output_nodes.append( o )

                        except Exception as e:
                            print( "[WARNING]: except caught during output processing: %s" % e )

                if not output_tree and output_nodes:
                    output_tree = lt.LopperTreePrinter()
                    output_tree.__dbg__ = self.verbose
                    for on in output_nodes:
                        # make a deep copy of the selected node
                        new_node = on()
                        new_node.__dbg__ = self.verbose
                        # and assign it to our tree
                        # if the performance of this becomes a problem, we can use
                        # direct calls to Lopper.node_copy_from_path()
                        output_tree + new_node

            if not self.dryrun:
                if output_tree:
                    output_file_full = self.outdir + "/" + output_file_name
                    self.write( output_tree.fdt, output_file_full, True, self.enhanced )
            else:
                print( "[NOTE]: dryrun detected, not writing output file %s" % output_file_name )

        if re.search( ".*,assist-v1$", lop_type ):
            # also note: this assist may change from being called as
            # part of the lop loop, to something that is instead
            # called by walking the entire device tree, looking for
            # matching nodes and making assists at that moment.
            #
            # but that sort of node walking, will invoke the assists
            # out of order with other lopper operations, so it isn't
            # particularly feasible or desireable.
            #
            cb_tgt_node_name = Lopper.property_get( lops_fdt, lop_node_number, 'node' )
            if not cb_tgt_node_name:
                print( "[ERROR]: cannot find target node for the assist" )
                sys.exit(1)

            cb = Lopper.property_get( lops_fdt, lop_node_number, 'assist' )
            cb_id = Lopper.property_get( lops_fdt, lop_node_number, 'id' )
            cb_opts = Lopper.property_get( lops_fdt, lop_node_number, 'options' )
            cb_opts = cb_opts.lstrip()
            if cb_opts:
                cb_opts = cb_opts.split( ' ' )
            else:
                cb_opts = []
            cb_node = Lopper.node_find( self.FDT, cb_tgt_node_name )
            if cb_node < 0:
                if self.werror:
                    print( "[ERROR]: cannot find assist target node in tree" )
                    sys.exit(1)
                else:
                    return
            if self.verbose:
                print( "[INFO]: assist lop detected" )
                if cb:
                    print( "        cb: %s" % cb )
                print( "        id: %s opts: %s" % (cb_id,cb_opts) )

            cb_funcs = self.find_compatible_assist( cb_node, cb_id )
            if cb_funcs:
                for cb_func in cb_funcs:
                    try:
                        if not cb_func( cb_node, self, { 'verbose' : self.verbose, 'args': cb_opts } ):
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

        if re.search( ".*,lop,load$", lop_type ):
            prop_id = ""
            prop_extension = ""

            try:
                load_prop = this_lop_node['load'].value[0]
            except:
                load_prop = ""

            if load_prop:
                # for submodule loading
                for p in self.load_paths:
                    if p not in sys.path:
                        sys.path.append( p )

                if self.verbose:
                    print( "[INFO]: loading module %s" % load_prop )

                mod_file = self.assist_find( load_prop, self.load_paths )
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
                    props = this_lop_node['props'].value
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
                            prop_extension = this_lop_node['file_ext'].value[0]
                        except:
                            try:
                                prop_extension = imported_module.file_ext()
                            except:
                                prop_extension = ""

                        assist_properties['mask'] = prop_extension

                    if p == "id":
                        try:
                            prop_id = this_lop_node['id'].value[0]
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
                    if self.verbose > 1:
                        if prop_extension:
                            print( "[INFO]: loading assist with properties (%s,%s)" % (prop_extension, prop_id) )

                    self.assists.append( LopperAssist( mod_file.name, imported_module, assist_properties ) )

        if re.search( ".*,lop,add$", lop_type ):
            if self.verbose:
                print( "[INFO]: node add lop" )

            src_node_name = Lopper.property_get( lops_fdt, lop_node_number, "node_src" )
            if not src_node_name:
                print( "[ERROR]: node add detected, but no node name found" )
                sys.exit(1)

            lops_node_path = Lopper.node_abspath( lops_fdt, lop_node_number )
            src_node_path = lops_node_path + "/" + src_node_name

            dest_node_path = Lopper.property_get( lops_fdt, lop_node_number, "node_dest" )
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

        if re.search( ".*,lop,conditional.*$", lop_type ):
            if self.verbose:
                print( "[INFO]: conditional lop found" )

            lop_tree = lt.LopperTree( lops_fdt )
            this_lop = lop_tree[lop_node_number]

            this_lop_subnodes = this_lop.subnodes()
            # the "cond_root" property of the lop node is the name of a node
            # under the same lop node that is the start of the conditional node
            # chain. If one wasn't provided, we start at '/'
            try:
                root = this_lop["cond_root"]
            except:
                root = "/"

            try:
                conditional_start = lop_tree[this_lop.abs_path + "/" + root.value[0]]
            except:
                print( "[INFO]: conditional node %s not found, returning" % this_lop.abs_path + "/" + root.value[0] )
                return False

            # the subnodes of the conditional lop represent the set of conditions
            # to use. The deepest node is what we'll be comparing
            cond_nodes = conditional_start.subnodes()
            # get the last node
            cond_last_node = cond_nodes[-1]
            # drop the path to the this conditional lop from the full path of
            # the last node in the chain. That's the path we'll look for in the
            # system device tree.
            cond_path = re.sub( this_lop.abs_path, "", cond_last_node.abs_path)

            sdt_tgt_nodes = self.tree.nodes(cond_path)
            if not sdt_tgt_nodes:
                if self.verbose > 1:
                    print( "[DBG++]: no target nodes found at: %s, returning" % cond_path )
                return False

            tgt_matches = []
            tgt_false_matches = []
            # iterate the properties in the final node of the conditional tree,
            # these are the conditions that we are checking.
            for cond_prop in cond_last_node:
                cond_prop_name = cond_prop.name
                invert_check = ""
                # remove __not__ from the end of a property name, that is an
                # indication for us only, and won't be in the SDT node
                if cond_prop.name.endswith( "__not__" ):
                    cond_prop_name = re.sub( "__not__$", "", cond_prop.name )
                    invert_check = "not"
                if self.verbose > 1:
                    print( "[DBG++]: conditional property:  %s tgt_nodes: %s" % (cond_prop_name,sdt_tgt_nodes) )

                for tgt_node in sdt_tgt_nodes:
                    # is the property present in the target node ?
                    try:
                        tgt_node_prop = tgt_node[cond_prop_name]
                    except:
                        tgt_node_prop = None

                    # no need to compare if the target node doesn't have the property
                    if tgt_node_prop:
                        check_val = cond_prop.compare( tgt_node_prop )

                        # if there was an inversion in the name, flip the result
                        check_val_final = eval( "{0} {1}".format(invert_check, check_val ))
                        if self.verbose > 1:
                            print ( "[DBG++]   ({0}:{1}) condition check final value: {2} {3} was {4}".format(tgt_node.abs_path,tgt_node_prop.value[0],invert_check, check_val, check_val_final ))
                        if check_val_final:
                            # if not already in the list, we need to add the target node
                            if not tgt_node in tgt_matches:
                                tgt_matches.append(tgt_node)
                        else:
                            # if subsequent props are not True, then we need to yank out
                            # the node from our match list
                            if tgt_node in tgt_matches:
                                tgt_matches.remove(tgt_node)
                            # and add it to the false matches list
                            if not tgt_node in tgt_false_matches:
                                tgt_false_matches.append(tgt_node)
                    else:
                        # if it doesn't have it, that has to be a false!
                        if self.verbose:
                            print( "[DBG]: system device tree node '%s' does not have property '%s'" %
                                   (tgt_node,cond_prop_name))

                        # if subsequent props are not True, then we need to yank out
                        # the node from our match list
                        if tgt_node in tgt_matches:
                            tgt_matches.remove(tgt_node)
                        # and add it to the false matches list
                        if not tgt_node in tgt_false_matches:
                            tgt_false_matches.append(tgt_node)

            # loop over the true matches, executing their operations, if one of them returns
            # false, we stop the loop
            for tgt_match in tgt_matches:
                try:
                    # we look through all the subnodes of this lopper operation. If any of them
                    # start with "true", it is a nested lop that we will execute
                    for n in this_lop_subnodes:
                        if n.name.startswith( "true" ):
                            if self.verbose > 1:
                                print( "[DBG++]: true subnode found with lop: %s" % (n['compatible'].value[0] ) )
                            try:
                                # run the lop, passing the target node as an option (the lop may
                                # or may not use it)
                                ret = self.exec_lop( lops_fdt, n.number, { 'start_node' : tgt_match.abs_path } )
                            except Exception as e:
                                print( "[WARNING]: true block had an exception: %s" % e )
                                ret = False

                            # no more looping if the called lop return False
                            if ret == False:
                                if self.verbose > 1:
                                    print( "[DBG++]: code block returned false, stop executing true blocks" )
                                break
                except Exception as e:
                    print( "[WARNING]: conditional had exception: %s" % e )

            # just like the target matches, we iterate any failed matches to see
            # if false blocks were defined.
            for tgt_match in tgt_false_matches:
                # no match, is there a false block ?
                try:
                    for n in this_lop_subnodes:
                        if n.name.startswith( "false" ):
                            if self.verbose > 1:
                                print( "[DBG++]: false subnode found with lop: %s" % (n['compatible'].value[0] ) )

                            try:
                                ret = self.exec_lop( lops_fdt, n.number, { 'start_node' : tgt_match.abs_path } )
                            except Exception as e:
                                print( "[WARNING]: false block had an exception: %s" % e )
                                ret = False

                            # if any of the blocks return False, we are done
                            if ret == False:
                                if self.verbose > 1:
                                    print( "[DBG++]: code block returned false, stop executing true blocks" )
                                break
                except Exception as e:
                    print( "[WARNING]: conditional false block had exception: %s" % e )

        if re.search( ".*,lop,code.*$", lop_type ):
            # execute a block of python code against a specified start_node
            code = Lopper.property_get( lops_fdt, lop_node_number, "code" )

            if not options:
                options = {}

            try:
                options_spec = this_lop_node['options'].value
            except:
                options_spec = ""

            if options_spec:
                for o in options_spec:
                    opt_key,opt_val = o.split(":")
                    if opt_key:
                        options[opt_key] = opt_val

            try:
                start_node = options['start_node']
            except:
                # were there selected nodes ? Make them the context, unless overrriden
                # by an explicit start_node property
                if self.tree.__selected__:
                    start_node = self.tree.__selected__[0]
                else:
                    start_node = "/"

            if self.verbose:
                print ( "[DBG]: code lop found, node context: %s" % start_node )

            ret = self.tree.exec_cmd( start_node, code, options )
            # who knows what the command did, better sync!
            self.tree.sync()

            return ret

        if re.search( ".*,lop,modify$", lop_type ):
            node_name = lops_fdt.get_name( lop_node_number )
            if self.verbose:
                print( "[INFO]: node %s is a compatible modify lop" % node_name )
            try:
                prop = lops_fdt.getprop( lop_node_number, 'modify' ).as_str()
            except:
                prop = ""

            # was there a regex passed for node matching ?
            nodes_selection = Lopper.property_get( lops_fdt, lop_node_number, "nodes" )
            if prop:
                if self.verbose:
                    print( "[INFO]: modify property found: %s" % prop )

                # format is: "path":"property":"replacement"
                #    - modify to "nothing", is a remove operation
                #    - modify with no property is node operation (rename or remove)
                modify_expr = prop.split(":")
                # combine these into the assigment, once everything has bee tested
                modify_path = modify_expr[0]
                modify_prop = modify_expr[1]
                modify_val = modify_expr[2]
                if self.verbose:
                    print( "[INFO]: modify path: %s" % modify_expr[0] )
                    print( "        modify prop: %s" % modify_expr[1] )
                    print( "        modify repl: %s" % modify_expr[2] )
                    if nodes_selection:
                        print( "        modify regex: %s" % nodes_selection )

                # if modify_expr[0] (the nodes) is empty, we use the selected nodes
                # if they are available
                if not modify_path:
                    if not self.tree.__selected__:
                        print( "[WARNING]: no nodes supplied to modify, and no nodes are selected" )
                        return False
                    else:
                        nodes = self.tree.__selected__
                else:
                    try:
                        nodes = self.tree.subnodes( self.tree[modify_path] )
                    except:
                        nodes = []

                if modify_prop:
                    # property operation
                    if not modify_val:
                        if self.verbose:
                            print( "[INFO]: property remove operation detected: %s %s" % (modify_path, modify_prop))

                        try:
                            # TODO: make a special case of the property_modify_below

                            # just to be sure that any other pending changes have been
                            # written, since we need accurate node IDs
                            self.tree.sync()
                            for n in nodes:
                                try:
                                    n.delete( modify_prop )
                                except:
                                    # no big deal if it doesn't have the property
                                    pass
                                self.tree.sync()
                        except Exception as e:
                            print( "[WARNING]: unable to remove property %s/%s (%s)" % (modify_path,modify_prop,e))
                    else:
                        if self.verbose:
                            print( "[INFO]: property modify operation detected" )

                        # just to be sure that any other pending changes have been
                        # written, since we need accurate node IDs
                        self.tree.sync()

                        # we re-do the nodes fetch here, since there are slight behaviour/return
                        # differences between nodes() (what this has always used), and subnodes()
                        # which is what we do above. We can re-test and reconcile this in the future.
                        if modify_path:
                            nodes = self.tree.nodes( modify_path )
                        else:
                            nodes = self.tree.__selected__
                        for n in nodes:
                            n[modify_prop] = [ modify_val ]
                            # this is fairly heavy, and may need to come out of the loop
                            self.tree.sync()
                else:
                    # drop the list, since if we are modifying a node, it is just one
                    # target node.
                    try:
                        node = nodes[0]
                    except:
                        node = None

                    # node operation
                    # in case /<name>/ was passed as the new name, we need to drop them
                    # since they aren't valid in set_name()
                    if modify_val:
                        modify_val = modify_val.replace( '/', '' )
                        try:
                            # change the name of the node
                            node.name = modify_val
                            self.tree.sync( self.FDT )
                        except Exception as e:
                            print( "[ERROR]:cannot rename node '%s' to '%s' (%s)" %(node.abs_path, modify_val, e))
                            sys.exit(1)
                    else:
                        # first we see if the node prefix is an exact match
                        node_to_remove = node

                        if not node_to_remove:
                            print( "[WARNING]: Cannot find node %s for delete operation" % node.abs_path )
                            if self.werror:
                                sys.exit(1)
                        else:
                            try:
                                self.tree.delete( node_to_remove )
                                self.tree.sync( self.FDT )
                            except:
                                print( "[WARNING]: could not remove node number: %s" % node_to_remove )

        # if the lop didn't return, we return false by default
        return False

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
            print( "[NOTE]: \'%d\' lopper operation files will be processed" % len(self.lops))

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

            lops_file_priority = Lopper.property_get( lops_fdt, 0, "priority" )
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
                lops_nodes = Lopper.nodes_with_property( lops_fdt, "compatible", "system-device-tree-v1,lop.*", "lops", False, 1 )
                for n in lops_nodes:
                    prop = lops_fdt.getprop( n, "compatible" )
                    val = Lopper.property_get( lops_fdt, n, "compatible" )
                    node_name = lops_fdt.get_name( n )

                    noexec = Lopper.property_get( lops_fdt, n, "noexec" )
                    if noexec:
                        if self.verbose > 1:
                            print( "[DBG+]: noexec flag found, skipping lop" )
                        continue

                    if self.verbose:
                        print( "[INFO]: ------> processing lop: %s" % val )

                    if self.verbose > 2:
                        print( "[DBG+]: prop: %s val: %s" % (prop.name, val ))
                        print( "[DBG+]: node name: %s" % node_name )

                    self.exec_lop( lops_fdt, n )


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
    global cmdline_assists
    global werror
    global save_temps
    global enhanced_print
    global outdir
    global load_paths
    global module_name
    global module_args
    global debug

    debug = False
    sdt = None
    verbose = 0
    output = ""
    inputfiles = []
    force = False
    dump_dtb = False
    dryrun = False
    target_domain = ""
    cmdline_assists = []
    werror = False
    save_temps = False
    enhanced_print = False
    outdir="./"
    load_paths = []
    try:
        opts, args = getopt.getopt(sys.argv[1:], "A:t:dfvdhi:o:a:SO:D", [ "debug", "assist-paths=", "outdir", "enhanced", "save-temps", "version", "werror","target=", "dump", "force","verbose","help","input=","output=","dryrun","assist="])
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
            cmdline_assists.append(a)
        elif o in ('-A', '--assist-path'):
            load_paths += a.split(":")
        elif o in ('-O', '--outdir'):
            outdir = a
        elif o in ('-D', '--debug'):
            debug = True
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
    module_name = ""
    module_args= []
    module_args_found = False
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

        else:
            if item == "--":
                module_args_found = True

            # the last input is the output file. It can't already exist, unless
            # --force was passed
            if not module_args_found:
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
            else:
                # module arguments
                if not item == "--":
                    if not module_name:
                        module_name = item
                        cmdline_assists.append( item )
                    else:
                        module_args.append( item )

    if module_name and verbose:
        print( "module found: %s" % module_name )
        print( "    args: %s" % module_args )

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

    device_tree.setup( sdt, inputfiles, "", force )
    device_tree.assists_setup( cmdline_assists )

    if module_name:
        device_tree.module_setup( module_name, module_args )

    if debug:
        import cProfile
        cProfile.run( 'device_tree.perform_lops()' )
    else:
        device_tree.perform_lops()

    if not dryrun:
        device_tree.write( enhanced = device_tree.enhanced )
    else:
        print( "[INFO]: --dryrun was passed, output file %s not written" % output )

    device_tree.cleanup()
