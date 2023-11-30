#/*
# * Copyright (c) 2019,2020 Xilinx Inc. All rights reserved.
# *
# * Author:
# *       Bruce Ashfield <bruce.ashfield@xilinx.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

import sys
import os
import re
import shutil
from pathlib import Path
from io import StringIO
import contextlib
from importlib.machinery import SourceFileLoader
import tempfile
from collections import OrderedDict

from lopper.fmt import LopperFmt

from lopper.tree import LopperNode, LopperTree, LopperTreePrinter, LopperProp
import lopper.tree

import lopper.log

lopper_directory = os.path.dirname(os.path.realpath(__file__))

try:
    from lopper.yaml import *
    yaml_support = True
except Exception as e:
    print( "[WARNING]: cant load yaml, disabling support: %s" % e )
    yaml_support = False

@contextlib.contextmanager
def stdoutIO(stdout=None):
    old = sys.stdout
    if stdout is None:
        stdout = StringIO()
        sys.stdout = stdout
        yield stdout
        sys.stdout = old

def lopper_type(cls):
    global Lopper
    Lopper = cls
    lopper.tree.Lopper = cls

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
        self.lops_optional = []
        self.verbose = 0
        self.dry_run = False
        self.assists = []
        self.output_file = ""
        self.cleanup_flag = True
        self.save_temps = False
        self.enhanced = False
        self.FDT = None
        self.tree = None
        self.subtrees = {}
        self.outdir = "./"
        self.target_domain = ""
        self.load_paths = []
        self.permissive = False
        self.merge = False
        self.support_files = False

    def setup(self, sdt_file, input_files, include_paths, force=False, libfdt=True, config=None):
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
        if libfdt:
            lopper.log._info( f"loading dtb and using libfdt to manipulate tree" )

        # check for required support applications
        if libfdt:
            support_bins = [ "dtc", "cpp" ]
        else:
            support_bins = [ "cpp" ]

        for s in support_bins:
            lopper.log._info( f"checking for support binary: {s}" )
            if not shutil.which(s):
                lopper.log._error( f"support application '{s}' not found, exiting" )
                sys.exit(2)

        self.use_libfdt = libfdt

        current_dir = os.getcwd()

        lop_files = []
        sdt_files = []
        support_files = []
        for ifile in input_files:
            if re.search( ".dts$", ifile ) or re.search( ".dtsi$", ifile ):
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
            elif re.search( ".yaml$", ifile ) or re.search( ".json$", ifile):
                if yaml_support:
                    with open(ifile) as f:
                        datafile = f.readlines()
                        found = False
                        dts_found = False
                        for line in datafile:
                            if not found:
                                if re.search( "system-device-tree-v1,lop", line ):
                                    lop_files.append( ifile )
                                    found = True
                                if re.search( "/dts-v1/", line ):
                                    found = True
                                    sdt_files.append( ifile )
                                if re.search( "compatible: .*subsystem", line ) or \
                                   re.search( ",domain-v1", line ):
                                    sdt_files.append( ifile )

                    # it didn't have a dts identifier in the input json or yaml file
                    # so it a supporting input. We need to store it as such.
                    if not found:
                        support_files.append( ifile )
                else:
                    lopper.log._error( f"YAML/JSON support is not loaded, check dependencies" )
                    sys.exit(1)
            else:
                lopper.log._error( f"input file {ifile} cannot be processed (no handler)" )
                sys.exit(1)

        # is the sdt a dts ?
        sdt_extended_trees = []
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
                        if re.search( ".dts$", f ):
                            with open(f,'rb') as fd:
                                shutil.copyfileobj(fd, wfd)

                        elif re.search( ".yaml$", f ):
                            # look for a special front end, for this or any file for that matter
                            yaml = LopperYAML( f, config=config )
                            yaml_tree = yaml.to_tree()

                            # save the tree for future processing (and joining with the main
                            # system device tree). No code after this needs to be concerned that
                            # this came from yaml.
                            sdt_extended_trees.append( yaml_tree )
                        elif re.search( ".json$", f ):
                            # look for a special front end, for this or any file for that matter
                            json = LopperJSON( json=f, config=config )
                            json_tree = json.to_tree()

                            # save the tree for future processing (and joining with the main
                            # system device tree). No code after this needs to be concerned that
                            # this came from yaml.
                            sdt_extended_trees.append( json_tree )

                fp = fpp.name
            else:
                sdt_files.append( sdt_file )
                fp = sdt_file

            # note: input_files isn't actually used by dt_compile, otherwise, we'd need to
            #       filter out non-dts files before the call .. we should probably still do
            #       that.
            sdt_file = Path( sdt_file )
            sdt_file_abs = sdt_file.resolve( True )

            # we need the original location of the main SDT file on the search path
            # in case there are dtsi files, etc.
            include_paths += " " + str(sdt_file.parent) + " "
            self.dtb = Lopper.dt_compile( fp, input_files, include_paths, force, self.outdir,
                                          self.save_temps, self.verbose, self.enhanced, self.permissive )

            if self.use_libfdt:
                self.FDT = Lopper.dt_to_fdt(self.dtb, 'rb')
            else:
                lopper.log._info( f"using python devicetree for parsing" )

                # TODO: "FDT" should now be "token" or something equally generic
                self.FDT = self.dtb
                self.dtb = ""

            dct = Lopper.export( self.FDT )

            self.tree = LopperTree()
            self.tree.strict = not self.permissive
            self.tree.load( dct )

            # join any extended trees to the one we just created
            for t in sdt_extended_trees:
                for node in t:
                    if node.abs_path != "/":
                        # old: deep copy the node
                        # new_node = node()
                        # assign it to the main system device tree
                        self.tree = self.tree.add( node, merge=self.merge )

            fpp.close()
        elif re.search( ".yaml$", self.dts ):
            if not yaml_support:
                lopper.log._error( f"no yaml support detected, but system device tree is yaml" )
                sys.exit(1)

            fp = ""
            fpp = tempfile.NamedTemporaryFile( delete=False )
            if sdt_files:
                sdt_files.insert( 0, self.dts )

                # this block concatenates all the files into a single yaml file to process
                with open( fpp.name, 'wb') as wfd:
                    for f in sdt_files:
                        with open(f,'rb') as fd:
                            shutil.copyfileobj(fd, wfd)

                fp = fpp.name
            else:
                sdt_files.append( sdt_file )
                fp = sdt_file

            yaml = LopperYAML( fp, config=config )
            lt = yaml.to_tree()

            self.dtb = None
            if self.use_libfdt:
                self.FDT = Lopper.fdt()
            else:
                self.FDT = None
            self.tree = lt
        elif re.search( ".json$", self.dts ):
            if not yaml_support:
                lopper.log._error( f"no json detected, but system device tree is json" )
                sys.exit(1)

            fp = ""
            fpp = tempfile.NamedTemporaryFile( delete=False )
            if sdt_files:
                sdt_files.insert( 0, self.dts )

                # this block concatenates all the files into a single yaml file to process
                with open( fpp.name, 'wb') as wfd:
                    for f in sdt_files:
                        with open(f,'rb') as fd:
                            shutil.copyfileobj(fd, wfd)

                fp = fpp.name
            else:
                sdt_files.append( sdt_file )
                fp = sdt_file

            json = LopperJSON( json=fp, config=config )
            lt = json.to_tree()

            self.dtb = None
            if self.use_libfdt:
                self.FDT = Lopper.fdt()
            else:
                self.FDT = None
            self.tree = lt
        else:
            # the system device tree is a dtb
            self.dtb = sdt_file
            self.dts = sdt_file
            if not self.use_libfdt:
                lopper.log._error( f"dtb system device tree passed ({self.dts}), and libfdt is disabled" )
                sys.exit(1)
            self.FDT = Lopper.dt_to_fdt(self.dtb, 'rb')
            self.tree = LopperTree()
            self.tree.load( Lopper.export( self.FDT ) )
            self.tree.strict = not self.permissive

        try:
            lops = self.tree["/lops"]
            if lops:
                lopper.log._info( f"embedded lops detected, extracting and queuing" )

                # free the lops from the input tree
                self.tree.delete(lops)

                # and save them in a lops tree
                embedded_lops_tree = LopperTree()
                embedded_lops_tree + lops

                lop = LopperFile( "" )
                lop.dts = ""
                lop.dtb = ""
                lop.fdt = None
                lop.tree = embedded_lops_tree

                if self.autorun:
                    self.lops.append( lop )
                else:
                    self.lops_optional.append( lop )
        except Exception as e:
            pass

        # exceptions are carrying us on and causing us trouble!
        #os._exit(1)

        self.support_files = support_files

        if self.verbose:
            print( "" )
            print( "Lopper summary:")
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
                    lopper.log._error( f"could not compile file {ifile}" )
                    sys.exit(1)

                if self.use_libfdt:
                    lop.dtb = compiled_file
                else:
                    lop.dtb = ""
                    lop.fdt = None
                    dct = Lopper.export( compiled_file )
                    lop.tree = LopperTree()
                    lop.tree.load( dct )

                self.lops.append( lop )
            elif re.search( ".yaml$", ifile ):
                yaml = LopperYAML( ifile, config=config )
                yaml_tree = yaml.to_tree()

                lop = LopperFile( ifile )
                lop.dts = ""
                lop.dtb = ""
                lop.fdt = None
                lop.tree = yaml_tree
                self.lops.append( lop )
            elif re.search( ".json$", ifile ):
                json = LopperJSON( json=ifile, config=config )
                json_tree = json.to_tree()

                lop = LopperFile( ifile )
                lop.dts = ""
                lop.dtb = ""
                lop.fdt = None
                lop.tree = json_tree
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

    def assist_autorun_setup( self, module_name, module_args = [] ):
        lt = LopperTree()

        lopper.log._debug( f"setting up module {module_name} with args:{module_args}" )

        lt['/']['compatible'] = [ 'system-device-tree-v1' ]
        lt['/']['priority'] = [ 3 ]

        ln = LopperNode()
        ln.name = "lops"

        mod_count = 0
        lop_name = "lop_{}".format( mod_count )

        lop_node = LopperNode()
        lop_node.name = lop_name
        lop_node['compatible'] = [ 'system-device-tree-v1,lop,assist-v1' ]
        lop_node['node'] = [ '/' ]

        if module_args:
            module_arg_string = ""
            for m in module_args:
                module_arg_string = module_arg_string + " " + m
                lop_node['options'] = [ module_arg_string ]

        lop_node['id'] = [ "module," + module_name ]

        ln = ln + lop_node
        lt = lt + ln

        lop = LopperFile( 'commandline' )
        lop.dts = ""
        lop.dtb = ""
        lop.fdt = None
        lop.tree = lt

        lopper.log._debug( f"generated assist run for {module_name}" )

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

    def write( self, tree = None, output_filename = None, overwrite = True, enhanced = False ):
        """Write a system device tree to a file

        Write a fdt (or system device tree) to an output file. This routine uses
        the output filename to determine if a module should be used to write the
        output.

        If the output format is .dts or .dtb, Lopper takes care of writing the
        output. If it is an unrecognized output type, the available assist
        modules are queried for compatibility. If there is a compatible assist,
        it is called to write the file, otherwise, a warning or error is raised.

        Args:
            tree (LopperTree,optional): LopperTree to write
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

        tree_to_write = tree
        if not tree_to_write:
            tree_to_write = self.tree

        if re.search( ".dtb", output_filename ):
            if self.use_libfdt:
                fdt = Lopper.fdt()
                Lopper.sync( fdt, tree_to_write.export() )
                Lopper.write_fdt( fdt, output_filename, overwrite, self.verbose )
            else:
                lopper.log._error( f"dtb output selected ({output_filename}), but libfdt is not enabled" )
                sys.exit(1)

        elif re.search( ".dts", output_filename ):
            o = Path(output_filename)
            if o.exists() and not overwrite:
                lopper.log._error( f"output file {output_filename} exists and force overwrite is not enabled" )
                sys.exit(1)

            printer = LopperTreePrinter( True, output_filename, self.verbose )
            printer.strict = not self.permissive
            try:
                if self.config['dts']['tabs']:
                    printer.indent_char = '\t'
                else:
                    printer.indent_char = ' '
            except:
                pass
            printer.load( tree_to_write.export() )
            printer.exec()

        elif re.search( ".yaml", output_filename ):
            o = Path(output_filename)
            if o.exists() and not overwrite:
                lopper.log._error( f"output file {output_filename} exists and force overwrite is not enabled"  )
                sys.exit(1)

            yaml = LopperYAML( None, tree_to_write, config=self.config )
            yaml.to_yaml( output_filename )
        elif re.search( ".json", output_filename ):
            o = Path(output_filename)
            if o.exists() and not overwrite:
                lopper.log._error( f"output file {output_filename} exists and force overwrite is not enabled" )
                sys.exit(1)

            json = LopperYAML( None, self.tree, config=self.config )
            json.to_json( output_filename )
        else:
            # we use the outfile extension as a mask
            (out_name, out_ext) = os.path.splitext(output_filename)
            cb_funcs = self.find_compatible_assist( 0, "", out_ext )
            if cb_funcs:
                for cb_func in cb_funcs:
                    try:
                        out_tree = LopperTreePrinter( True, output_filename, self.verbose )
                        out_tree.load( tree_to_write.export() )
                        out_tree.strict = not self.permissive
                        if not cb_func( 0, out_tree, { 'outfile': output_filename, 'verbose' : self.verbose } ):
                            lopper.log._warning( f"output assist returned false, check for errors ..." )
                    except Exception as e:
                        lopper.log._warning( f"output assist {cb_func} failed: {e}" )
                        exc_type, exc_obj, exc_tb = sys.exc_info()
                        fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
                        print(exc_type, fname, exc_tb.tb_lineno)
                        if self.werror:
                            sys.exit(1)
            else:
                lopper.log._info( f"no compatible output assist found, skipping" )
                if self.werror:
                    lopper.log._error( f"werror is enabled, and no compatible output assist found, exiting" )
                    sys.exit(2)

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

        lopper.log._info( f"assist_find: {assist_name} local search: {local_load_paths}" )


        # anything less than python 3.6.x doesn't take "true" as a parameter to
        # resolve. So we make it conditional on the version.

        try:
            if sys.version_info.minor < 6:
                mod_file_abs = mod_file.resolve()
            else:
                mod_file_abs = mod_file.resolve( True )
            if not mod_file_abs:
                raise FileNotFoundError( "Unable to find assist: %s" % mod_file )
        except FileNotFoundError:
            # check the path from which lopper is running, that directory + assists, and paths
            # specified on the command line
            search_paths =  [ lopper_directory ] + [ lopper_directory + "/assists/" ] + local_load_paths
            for s in search_paths:
                mod_file = Path( s + "/" + mod_file.name )
                try:
                    if sys.version_info.minor < 6:
                        mod_file_abs = mod_file.resolve()
                    else:
                        mod_file_abs = mod_file.resolve( True )
                    if not mod_file_abs:
                        raise FileNotFoundError( "Unable to find assist: %s" % mod_file )
                except FileNotFoundError:
                    mod_file_abs = ""

                if not mod_file_abs and not mod_file.name.endswith( ".py"):
                    # try it with a .py
                    mod_file = Path( s + "/" + mod_file.name + ".py" )
                    try:
                        if sys.version_info.minor < 6:
                            mod_file_abs = mod_file.resolve()
                        else:
                            mod_file_abs = mod_file.resolve( True )
                        if not mod_file_abs:
                            raise FileNotFoundError( "Unable to find assist: %s" % mod_file )
                    except FileNotFoundError:
                        mod_file_abs = ""


            if not mod_file_abs:
                lopper.log._error( f"module file {assist_name} not found" )
                if self.werror:
                    sys.exit(1)
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
            lt = LopperTree()

            lt['/']['compatible'] = [ 'system-device-tree-v1' ]
            lt['/']['priority'] = [ 1 ]

            ln = LopperNode()
            ln.name = "lops"

            assist_count = 0
            for a in set(self.assists):
                lop_name = "lop_{}".format( assist_count )

                lop_node = LopperNode()
                lop_node.name = lop_name
                lop_node['compatible'] = [ 'system-device-tree-v1,lop,load' ]
                lop_node['load'] = [ a.file ]

                ln = ln + lop_node

                lopper.log._debug( f"generated load lop for assist {a}" )

                assist_count = assist_count + 1

            lt = lt + ln

            lop = LopperFile( 'commandline' )
            lop.dts = ""
            lop.dtb = ""
            lop.fdt = None
            lop.tree = lt

            self.lops.insert( 0, lop )

    def domain_spec(self, tgt_domain, tgt_domain_id = "openamp,domain-v1"):
        """generate a lop for a command line passed domain

        When a target domain is passed on the command line, we must generate
        a lop dtb for it, so that it can be processed along with other
        operations

        Args:
           tgt_domain (string): path to the node to use as the domain
           tgt_domain_id (string): assist identifier to use for locating a
                                   registered assist.

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

        lt = LopperTree()

        lt['/']['compatible'] = [ 'system-device-tree-v1' ]
        lt['/']['priority'] = [ 3 ]

        ln = LopperNode()
        ln.name = "lops"

        mod_count = 0
        lop_name = "lop_{}".format( mod_count )

        lop_node = LopperNode()
        lop_node.name = lop_name
        lop_node['compatible'] = [ 'system-device-tree-v1,lop,assist-v1' ]
        lop_node['id'] = [ tgt_domain_id ]

        ln = ln + lop_node
        lt = lt + ln

        lop = LopperFile( 'commandline' )
        lop.dts = ""
        lop.dtb = ""
        lop.fdt = None
        lop.tree = lt

        self.lops.insert( 0, lop )

    def find_compatible_assist( self, cb_node = None, cb_id = "", mask = "" ):
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
                    lopper.log._warning( f"a configured assist has no module loaded" )
        else:
            lopper.log._warning( f"no modules loaded, no compat search is possible" )

        return cb_func

    def exec_lop( self, lop_node, lops_tree, options = None ):
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

        # TODO: stop using this and go to the searching in the lops processing loop.
        lop_type = lop_node['compatible'].value[0]
        # TODO: lop_args is really a "subtype"
        try:
            lop_args = lop_node['compatible'].value[1]
        except:
            lop_args = ""

        lopper.log._debug( f"executing lop: {lop_type}" )

        if re.search( ".*,exec.*$", lop_type ):
            lopper.log._debug( f"code exec jump" )
            try:
                try:
                    node_spec = lop_node['node'].value[0]
                except:
                    if self.tree.__selected__:
                        node_spec = self.tree.__selected__[0]
                    else:
                        node_spec = ""

                if not options:
                    options = {}

                try:
                    options_spec = lop_node['options'].value
                except:
                    options_spec = ""

                if options_spec:
                    for o in options_spec:
                        opt_key,opt_val = o.split(":")
                        if opt_key:
                            options[opt_key] = opt_val

                exec_tgt = lop_node['exec'].value[0]
                target_node = lops_tree.pnode( exec_tgt )

                lopper.log._debug( f"exec phandle: {hex(exec_tgt)} target: {target_node}", lop_node )

                if target_node:
                    try:
                        if node_spec:
                            options['start_node'] = node_spec

                        ret = self.exec_lop( target_node, lops_tree, options )
                    except Exception as e:
                        lopper.log._warning( f"exec block caused exception: {e}" )
                        ret = False

                    return ret
                else:
                    return False

            except Exception as e:
                lopper.log._warning( f"exec lop exception: {e}" )
                return False

        if re.search( ".*,print.*$", lop_type ):
            print_props = lop_node.props('print.*')
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

            return True

        if re.search( ".*,select.*$", lop_type ):
            select_props = lop_node.props( 'select.*' )

            try:
                tree_name = lop_node['tree'].value[0]
                try:
                    tree = self.subtrees[tree_name]
                except:
                    lopper.log._error( f"tree name provided ({tree_name}), but not found" )
                    sys.exit(1)
            except:
                tree = self.tree

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
            selected_nodes_possible = []
            for sel in select_props:
                if sel.value == ['']:
                    lopper.log._debug( f"clearing selected nodes" )
                    tree.__selected__ = []
                else:
                    # if different node regex + properties are listed in the same
                    # select = "foo","bar","blah", they are always AND conditions.
                    for s in sel.value:
                        lopper.log._debug( f"running node selection: {s} ({selected_nodes_possible})" )
                        try:
                            node_regex, prop, prop_val = s.split(":")
                        except:
                            node_regex = s
                            prop = ""
                            prop_val = ""

                        if node_regex:
                            if node_regex.startswith( "/" ):
                                if selected_nodes_possible:
                                    selected_nodes_possible = selected_nodes_possible + tree.nodes( node_regex )
                                else:
                                    selected_nodes_possible = tree.nodes( node_regex )
                            else:
                                # search with it as a label
                                if selected_nodes_possible:
                                    selected_nodes_possible = selected_nodes_possible + tree.lnodes( node_regex )
                                else:
                                    selected_nodes_possible = tree.lnodes( node_regex )

                        else:
                            # if the node_regex is empty, we operate on previously
                            # selected nodes.
                            if selected_nodes:
                                selected_nodes_possible = selected_nodes
                            else:
                                selected_nodes_possible = tree.__selected__

                            if self.verbose > 1:
                                lopper.log._debug( f"selected potential nodes:" )
                                for n in selected_nodes_possible:
                                    print( "       %s" % n )

                        if prop and prop_val:
                            invert_result = False
                            if re.search( "\!", prop_val ):
                                invert_result = True
                                prop_val = re.sub( '^\!', '', prop_val )
                                lopper.log._debug( f"select: inverting result" )

                            # in case this is a formatted list, ask lopper to convert
                            prop_val = Lopper.property_convert( prop_val )

                            # construct a test prop, so we can use the internal compare
                            test_prop = LopperProp( prop, -1, None, prop_val )
                            test_prop.ptype = test_prop.property_type_guess( True )

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
                                    if invert_result:
                                        are_they_equal = not are_they_equal

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

                        if prop and not prop_val:
                            # an empty property value means we are testing if the property exists

                            # if the property name is "!<property>" and the val is empty, then we
                            # are testing if it doesn't exist.

                            prop_exists_test = True
                            if re.search( "\!", prop ):
                                prop_exists_test = False

                            # remove any leading '!' from the name.
                            prop = re.sub( '^\!', '', prop )

                            for sl in list(selected_nodes_possible):
                                try:
                                    sl_prop = sl[prop]
                                except Exception as e:
                                    sl_prop = None

                                if prop_exists_test:
                                    if sl_prop != None:
                                        if not sl in selected_nodes:
                                            selected_nodes.append( sl )
                                    else:
                                        if sl in selected_nodes:
                                            selected_nodes.remove( sl )
                                else:
                                    # we are looking for the *lack* of a property
                                    if sl_prop:
                                        if sl in selected_nodes:
                                            selected_nodes.remove( sl )
                                    else:
                                        if not sl in selected_nodes:
                                            selected_nodes.append( sl )

                        if not prop and not prop_val:
                            selected_nodes = selected_nodes_possible


                    if self.verbose > 1:
                        lopper.log._debug( f"select pass done: selected nodes:" )
                        for n in selected_nodes:
                            print( "    %s" % n )

                    # these are now our possible selected nodes for any follow
                    # up "or" conditions
                    selected_nodes_possible = selected_nodes

            # update the tree selection with our results
            tree.__selected__ = selected_nodes

            if tree.__selected__:
                return True

            return False

        if re.search( ".*,meta.*$", lop_type ):
            if re.search( "phandle-desc", lop_args ):
                lopper.log._debug( f"processing phandle meta data {type(Lopper)}")

                # grab all the defaults
                Lopper.phandle_possible_prop_dict = Lopper.phandle_possible_properties()
                try:
                    del Lopper.phandle_possible_prop_dict["DEFAULT"]
                except:
                    pass

                # now override, remove, extend
                for p in lop_node:
                    # we skip compatible, since that is actually the compatibility value
                    # of the node, not a meta data entry. Everything else is though
                    if p.name != "compatible":
                        if re.search( r'^reset$', p.name ):
                            lopper.log._debug( f"resetting phandle table" )
                            Lopper.phandle_possible_prop_dict = OrderedDict()
                        elif re.search( r'^lopper-comment.*', p.name ):
                            # skip
                            pass
                        elif re.search( r'^\-.*', p.name ):
                            # delete
                            p.name = re.sub( r'^\-', '', p.name )
                            try:
                                del Lopper.phandle_possible_prop_dict[p.name]
                            except:
                                pass
                        else:
                            Lopper.phandle_possible_prop_dict[p.name] = [ p.value[0] ]

            return True

        if re.search( ".*,output$", lop_type ):
            try:
                output_file_name = lop_node['outfile'].value[0]
            except:
                lopper.log._error( f"cannot get output file name from lop" )
                sys.exit(1)

            lopper.log._debug( f"outfile is: {output_file_name}" )

            try:
                tree_name = lop_node['tree'].value[0]
                try:
                    tree = self.subtrees[tree_name]
                except:
                    lopper.log._error( f"tree name provided ({tree_name}), but not found" )
                    sys.exit(1)
            except:
                tree = self.tree


            output_nodes = []
            try:
                output_regex = lop_node['nodes'].value
            except:
                output_regex = []

            if not output_regex:
                if tree.__selected__:
                    output_nodes = tree.__selected__

            if not output_regex and not output_nodes:
                return False

            lopper.log._debug( f"output regex: {output_regex}" )

            output_tree = None
            if output_regex:
                output_nodes = []
                # select some nodes!
                if "*" in output_regex:
                    output_tree = LopperTree( True )
                    output_tree.load( tree.export() )
                    output_tree.strict = not self.permissive
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

                            o_nodes = tree.nodes(o_node_regex)
                            if not o_nodes:
                                # was it a label ?
                                label_nodes = []
                                try:
                                    o_nodes = tree.lnodes(o_node_regex)
                                except Exception as e:
                                    pass

                            for o in o_nodes:
                                lopper.log._debug( f"output lop, checking node: {o.abs_path}" )

                                # we test for a property in the node if it was defined
                                if o_prop_name:
                                    p = tree[o].propval(o_prop_name)
                                    if o_prop_val:
                                        if p:
                                            if o_prop_val in p:
                                                if not o in output_nodes:
                                                    output_nodes.append( o )
                                else:
                                    if not o in output_nodes:
                                        output_nodes.append( o )

                        except Exception as e:
                            lopper.log._warning( f"exception caught during output processing: {e}" )

                if output_regex:
                    if self.verbose > 2:
                        lopper.log._debug( f"output lop, final nodes:" )
                        for oo in output_nodes:
                            print( "       %s" % oo.abs_path )

                if not output_tree and output_nodes:
                    output_tree = LopperTreePrinter()
                    output_tree.strict = not self.permissive
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

                    if self.use_libfdt:
                        # create a FDT
                        dct = output_tree.export()
                        out_fdt = Lopper.fdt()
                        Lopper.sync( out_fdt, dct )

                    # we should consider checking the type, and not doing the export
                    # if going to dts, since that is already easily done with the tree.
                    self.write( output_tree, output_file_full, True, self.enhanced )
            else:
                lopper.log._info( f"dryrun detected, not writing output file {output_file_name}" )

            return True
        if re.search( ".*,tree$", lop_type ):
            # TODO: consolidate this with the output lop
            try:
                tree_name = lop_node['tree'].value[0]
            except:
                lopper.log._error( f"tree lop: cannot get tree name from lop" )
                sys.exit(1)

            lopper.log._debug( f"tree lop: tree is: {tree_name}" )

            tree_nodes = []
            try:
                tree_regex = lop_node['nodes'].value
            except:
                tree_regex = []

            if not tree_regex:
                if self.tree.__selected__:
                    tree_nodes = self.tree.__selected__

            if not tree_regex and not tree_nodes:
                lopper.log._warning( f"tree lop: no nodes or regex proviced for tree, returning" )
                return False

            new_tree = None
            if tree_regex:
                tree_nodes = []
                # select some nodes!
                if "*" in tree_regex:
                    new_tree = LopperTree( True )
                    new_tree.strict = False
                    new_tree.load( Lopper.export( self.FDT ) )
                    new_tree.resolve()
                    new_tree.strict = not self.permissive
                else:
                    # we can gather the tree nodes and unify with the selected
                    # copy below.
                    for regex in tree_regex:

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
                                                if not o in tree_nodes:
                                                    tree_nodes.append( o )
                                else:
                                    if not o in tree_nodes:
                                        tree_nodes.append( o )

                        except Exception as e:
                            lopper.log._warning( f"exception caught during tree processing: {e}" )

                if not new_tree and tree_nodes:
                    new_tree = LopperTreePrinter()
                    new_tree.strict = not self.permissive
                    new_tree.__dbg__ = self.verbose
                    for on in tree_nodes:
                        # make a deep copy of the selected node
                        new_node = on()
                        new_node.__dbg__ = self.verbose
                        # and assign it to our tree
                        # if the performance of this becomes a problem, we can use
                        # direct calls to Lopper.node_copy_from_path()
                        new_tree + new_node

            if new_tree:
                self.subtrees[tree_name] = new_tree
            else:
                lopper.log._error( f"no tree created, exiting" )
                sys.exit(1)

            return True

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
            try:
                cb_tgt_node_name = lop_node['node'].value[0]
            except:
                lopper.log._error( f"cannot find target node for assist" )
                sys.exit(1)

            cb_outdir = self.outdir
            try:
                cb = lop_node.propval('assist')[0]
                cb_id = lop_node.propval('id')[0]
                cb_opts = lop_node.propval('options')[0]
                cb_opts = cb_opts.lstrip()
                if cb_opts:
                    cb_opts = cb_opts.split( ' ' )
                else:
                    cb_opts = []
                if lop_node.propval('outdir') != ['']:
                    cb_outdir = lop_node.propval('outdir')[0]
            except Exception as e:
                lopper.log._error( f"callback options are missing: {e}" )
                sys.exit(1)

            try:
                cb_node = self.tree.nodes(cb_tgt_node_name )[0]
            except:
                cb_node = None

            if not cb_node:
                if self.werror:
                    lopper.log._error( f"cannot find assist target node in tree" )
                    sys.exit(1)
                else:
                    return False

            if self.verbose:
                lopper.log._info( f"assist lop detected" )
                if cb:
                    print( "        cb: %s" % cb )
                print( "        id: %s opts: %s" % (cb_id,cb_opts) )

            cb_funcs = self.find_compatible_assist( cb_node, cb_id )
            if cb_funcs:
                for cb_func in cb_funcs:
                    try:
                        if not cb_func( cb_node, self, { 'verbose' : self.verbose, 'outdir' : cb_outdir, 'args': cb_opts } ):
                            lopper.log._warning( f"the assist returned false, check for errors ..." )
                    except Exception as e:
                        lopper.log._warning( f"assist %{cb_func} failed: {e}" )
                        exc_type, exc_obj, exc_tb = sys.exc_info()
                        fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
                        print(exc_type, fname, exc_tb.tb_lineno)
                        # exit if warnings are treated as errors
                        if self.werror:
                            sys.exit(1)

                        return False
            else:
                lopper.log._info( f"no compatible assist found, skipping: {cb_tgt_node_name}{cb}")
                return False

            return True

        if re.search( ".*,lop,load$", lop_type ):
            prop_id = ""
            prop_extension = ""

            try:
                load_prop = lop_node['load'].value[0]
            except:
                load_prop = ""

            if load_prop:
                # for submodule loading
                for p in self.load_paths:
                    if p not in sys.path:
                        sys.path.append( p )

                lopper.log._info( f"loading module {load_prop}" )

                mod_file = self.assist_find( load_prop, self.load_paths )
                if not mod_file:
                    lopper.log._error( f"unable to find assist ({load_prop })" )
                    sys.exit(1)

                mod_file_abs = mod_file.resolve()
                # append the directory of the located module onto the search
                # path. This is needed if that module imports something from
                # its own directory
                sys.path.append( str(mod_file_abs.parent) )
                try:
                    imported_module = SourceFileLoader( mod_file.name, str(mod_file_abs) ).load_module()
                except Exception as e:
                    lopper.log._error( f"could not load assist: {mod_file_abs}: {e}" )
                    sys.exit(1)

                assist_properties = {}
                try:
                    props = lop_node['props'].value
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
                            prop_extension = lop_node['file_ext'].value[0]
                        except:
                            try:
                                prop_extension = imported_module.file_ext()
                            except:
                                prop_extension = ""

                        assist_properties['mask'] = prop_extension

                    if p == "id":
                        try:
                            prop_id = lop_node['id'].value[0]
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
                    lopper.log._info( f"loading assist with properties ({prop_extension}, {prop_id})", prop_extension )
                    self.assists.append( LopperAssist( mod_file.name, imported_module, assist_properties ) )

            return True

        if re.search( ".*,lop,add$", lop_type ):
            lopper.log._info( f"node add lop" )

            try:
                src_node_name = lop_node['node_src'].value[0]
            except:
                lopper.log._error( f"node add detected, but no node name found" )
                sys.exit(1)

            try:
                tree_name = lop_node['tree'].value[0]
                try:
                    tree = self.subtrees[tree_name]
                except:
                    lopper.log._error( f"tree name provided ({tree_name}), but not found", True )
            except:
                tree = self.tree


            lops_node_path = lop_node.abs_path
            src_node_path = lops_node_path + "/" + src_node_name

            try:
                dest_node_path = lop_node["node_dest"].value[0]
            except:
                dest_node_path = "/" + src_node_name


            lopper.log._info( f"add node name: {src_node_path} node path: {dest_node_path}" )


            if tree:
                src_node = lops_tree[src_node_path]

                # copy the source node
                dst_node = src_node()
                # adjust the path to where it will land
                dst_node.abs_path = dest_node_path

                # add it to the tree, and this will adjust the children appropriately
                tree + dst_node
            else:
                lopper.log._error( f"unable to copy node: {src_node_name}", True )

            return True

        if re.search( ".*,lop,conditional.*$", lop_type ):
            lopper.log._info( f"conditional lop found" )

            try:
                tree_name = lop_node['tree'].value[0]
                try:
                    tree = self.subtrees[tree_name]
                except:
                    lopper.log._error( f"tree name provided ({tree_name}), but not found", True)
            except:
                tree = self.tree

            this_lop_subnodes = lop_node.subnodes()
            # the "cond_root" property of the lop node is the name of a node
            # under the same lop node that is the start of the conditional node
            # chain. If one wasn't provided, we start at '/'
            try:
                root = lop_node["cond_root"].value[0]
            except:
                root = "/"

            try:
                conditional_start = lops_tree[lop_node.abs_path + "/" + root]
            except:
                lopper.log._info( f"conditional node {lop_node.abs_path + '/' + root} not found, returning" )
                return False

            try:
                cond_select = lop_node["cond_select"]
            except:
                cond_select = None

            # the subnodes of the conditional lop represent the set of conditions
            # to use. The deepest node is what we'll be comparing
            cond_nodes = conditional_start.subnodes()
            # get the last node
            cond_last_node = cond_nodes[-1]

            if cond_select:
                cond_path = cond_select.value[0]
            else:
                # drop the path to the this conditional lop from the full path of
                # the last node in the chain. That's the path we'll look for in the
                # system device tree.
                cond_path = re.sub( lop_node.abs_path, "", cond_last_node.abs_path)

            sdt_tgt_nodes = tree.nodes(cond_path)
            if not sdt_tgt_nodes:
                lopper.log._debug( f"no target nodes found at: {cond_path}, returning" )
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

                lopper.log._debug( f"conditional property: {cond_prop_name} tgt_nodes: {sdt_tgt_nodes}" )

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
                        lopper.log._debug( f"   ({tgt_node.abs_path}:{tgt_node_prop.value[0]}) condition check final value: {invert_check} {check_val} was {check_val_final}")
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
                        lopper.log._debug( f"system device tree node '{tgt_node}' does not have property '{cond_prop_name}'" )

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
                            lopper.log._debug( f"true subnode found with lop:{n['compatible'].value[0]}" )
                            try:
                                # run the lop, passing the target node as an option (the lop may
                                # or may not use it)
                                ret = self.exec_lop( n, lops_tree, { 'start_node' : tgt_match.abs_path } )
                            except Exception as e:
                                lopper.log._warning( f"true block had an exception: {e}" )
                                ret = False

                            # no more looping if the called lop return False
                            if ret == False:
                                lopper.log._debug( f"code block returned false, stop executing true blocks" )
                                break
                except Exception as e:
                    lopper.log._warning( f"conditional had exception: {e}" )

            # just like the target matches, we iterate any failed matches to see
            # if false blocks were defined.
            for tgt_match in tgt_false_matches:
                # no match, is there a false block ?
                try:
                    for n in this_lop_subnodes:
                        if n.name.startswith( "false" ):
                            lopper.log._debug( f"false subnode found with lop: {n['compatible'].value[0]}" )

                            try:
                                ret = self.exec_lop( n, lops_tree, { 'start_node' : tgt_match.abs_path } )
                            except Exception as e:
                                lopper.log._warning( f"false block had an exception: {e}" )
                                ret = False

                            # if any of the blocks return False, we are done
                            if ret == False:
                                lopper.log._debug( f"code block returned false, stop executing true blocks" )
                                break
                except Exception as e:
                    lopper.log._warning( f"conditional false block had exception: {e}" )

            return ret

        if re.search( ".*,lop,code.*$", lop_type ) or re.search( ".*,lop,xlate.*$", lop_type ):
            # execute a block of python code against a specified start_node
            code = lop_node['code'].value[0]

            if not options:
                options = {}

            try:
                options_spec = lop_node['options'].value
            except:
                options_spec = ""

            try:
                tree_name = lop_node['tree'].value[0]
                try:
                    tree = self.subtrees[tree_name]
                except:
                    lopper.log._error( f"tree name provided ({tree_name}), but not found", True )
            except:
                tree = self.tree

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
                if tree.__selected__:
                    start_node = tree.__selected__[0]
                else:
                    start_node = "/"

            try:
                inherit_list = lop_node['inherit'].value[0].replace(" ","").split(",")
            except:
                inherit_list = []

            lopper.log._debug( f"code lop found, node context: {start_node}" )

            if re.search( ".*,lop,xlate.*$", lop_type ):
                inherit_list.append( "lopper_lib" )

                if tree.__selected__:
                    node_list = tree.__selected__
                else:
                    node_list = [ "/" ]

                for n in node_list:
                    ret = tree.exec_cmd( n, code, options, inherit_list, self.load_paths )
                    # who knows what the command did, better sync!
                    tree.sync()
            else:
                ret = tree.exec_cmd( start_node, code, options, inherit_list, self.load_paths )
                # who knows what the command did, better sync!
                tree.sync()

            return ret

        if re.search( ".*,lop,modify$", lop_type ):
            node_name = lop_node.name
            lopper.log._info( f"node {node_name} is a compatible modify lop" )
            try:
                prop = lop_node["modify"].value[0]
            except:
                prop = ""

            try:
                tree_name = lop_node['tree'].value[0]
                try:
                    tree = self.subtrees[tree_name]
                except:
                    lopper.log._error( f"tree name provided ({tree_name}), but not found", True )
            except:
                tree = self.tree

            try:
                nodes_selection = lop_node["nodes"].value[0]
            except:
                nodes_selection = ""
            if prop:
                lopper.log._debug( f"modify property found: {prop}" )

                # format is: "path":"property":"replacement"
                #    - modify to "nothing", is a remove operation
                #    - modify with no property is node operation (rename or remove)
                modify_expr = prop.split(":")
                # combine these into the assigment, once everything has bee tested
                modify_path = modify_expr[0]
                modify_prop = modify_expr[1]
                modify_val = modify_expr[2]

                lopper.log._info( f"modify path: {modify_expr[0]}" )
                lopper.log._info( f"modify prop: {modify_expr[1]}" )
                lopper.log._info( f"modify repl: {modify_expr[2]}" )
                if nodes_selection:
                    lopper.log._info( f"modify regex: {nodes_selection}" )

                # if modify_expr[0] (the nodes) is empty, we use the selected nodes
                # if they are available
                if not modify_path:
                    if not tree.__selected__:
                        lopper.log._warning( f"no nodes supplied to modify, and no nodes are selected" )
                        return False
                    else:
                        nodes = tree.__selected__
                else:
                    try:
                        nodes = tree.subnodes( tree[modify_path] )
                    except Exception as e:
                        lopper.log._debug( f"modify lop: node issue: {e}" )
                        nodes = []

                if modify_prop:
                    # property operation
                    if not modify_val:
                        lopper.log._info( f"property remove operation detected: {modify_path} {modify_prop}" )

                        try:
                            # TODO: make a special case of the property_modify_below
                            tree.sync()

                            for n in nodes:
                                try:
                                    n.delete( modify_prop )
                                except:
                                    lopper.log._warning( f"property {modify_prop} not found, and not deleted" )
                                    # no big deal if it doesn't have the property
                                    pass

                            tree.sync()
                        except Exception as e:
                            lopper.log._error( f"unable to remove property {modify_path}/{modify_prop} ({e})", True )
                    else:
                        lopper.log._info( f"property modify operation detected" )

                        # set the tree state to "syncd", so we'll be able to test for changed
                        # state later.
                        tree.sync()

                        # we re-do the nodes fetch here, since there are slight behaviour/return
                        # differences between nodes() (what this has always used), and subnodes()
                        # which is what we do above. We can re-test and reconcile this in the future.
                        if modify_path:
                            nodes = tree.nodes( modify_path )
                        else:
                            nodes = tree.__selected__

                        if not nodes:
                            lopper.log._warning( f"node {modify_path} not found,  property {modify_prop} not modified " )

                        # if the value has a "&", it is a phandle, and we need
                        # to try and look it up.
                        if re.search( '&', modify_val ):
                            node = modify_val.split( '#' )[0]
                            try:
                                node_property =  modify_val.split( '#' )[1]
                            except:
                                node_property = None

                            phandle_node_name = re.sub( '&', '', node )
                            pfnodes = tree.nodes( phandle_node_name )
                            if not pfnodes:
                                pfnodes = tree.lnodes( phandle_node_name )
                                if not pfnodes:
                                    # was it a local phandle (i.e. in the lop tree?)
                                    pfnodes = lops_tree.nodes( phandle_node_name )
                                    if not pfnodes:
                                        pfnodes = lops_tree.lnodes( phandle_node_name )

                            if node_property:
                                # there was a node property, that means we actualy need
                                # to lookup the phandle and find a property within it. That's
                                # the replacement value
                                if pfnodes:
                                    try:
                                        modify_val = pfnodes[0][node_property].value
                                    except:
                                        modify_val = pfnodes[0].phandle
                                else:
                                    modify_val = 0
                            else:
                                if pfnodes:
                                    phandle = pfnodes[0].phandle
                                    if not phandle:
                                        # this is a reference, generate a phandle
                                        pfnodes[0].phandle = tree.phandle_gen()
                                        phandle = pfnodes[0].phandle
                                else:
                                    phandle = 0

                                modify_val = phandle

                        else:
                            modify_val = Lopper.property_convert( modify_val )

                        for n in nodes:
                            if type( modify_val ) == list:
                                n[modify_prop] = modify_val
                            else:
                                n[modify_prop] = [ modify_val ]

                        tree.sync()
                else:
                    lopper.log._warning( f"modify lop, node operation" )

                    # drop the list, since if we are modifying a node, it is just one
                    # target node.
                    try:
                        node = nodes[0]
                    except:
                        node = None

                    if not node:
                        lopper.log._error( f"no nodes found for {modify_path}", True )

                    # node operation
                    # in case /<name>/ was passed as the new name, we need to drop them
                    # since they aren't valid in set_name()
                    if modify_val:
                        modify_source_path = Path(node.abs_path)

                        if modify_val.startswith( "/" ):
                            modify_dest_path = Path( modify_val )
                        else:
                            modify_dest_path = Path( "/" + modify_val )

                        if modify_source_path.parent != modify_dest_path.parent:
                            lopper.log._debug( f"[{tree}] node move: {modify_source_path} -> {modify_dest_path}" )
                            # deep copy the node
                            new_dst_node = node()
                            new_dst_node.abs_path = modify_val

                            tree + new_dst_node

                            # delete the old node
                            tree.delete( node )

                            tree.sync()

                        if modify_source_path.name != modify_dest_path.name:
                            lopper.log._debug( f"[{tree}] node rename: {modify_source_path.name} -> {modify_dest_path.name}" )

                            modify_val = modify_val.replace( '/', '' )
                            try:

                                # is there already a node at the new destination path ?
                                try:
                                    old_node = tree[str(modify_dest_path)]
                                    if old_node:
                                        # we can error, or we'd have to delete the old one, and
                                        # then let the rename happen. But really, you can just
                                        # write a lop that takes care of that before calling such
                                        # a bad rename lop.
                                        lopper.log._debug( f"node exists at rename target: {old_node.abs_path}" )
                                        lopper.log._debug( f"Deleting it, to allow rename to continue" )

                                        tree.delete( old_node )
                                except Exception as e:
                                    # no node at the dest
                                    pass

                                # change the name of the node
                                node.name = modify_val
                                tree.sync()

                            except Exception as e:
                                lopper.log._error( f"cannot rename node '{node.abs_path}' to '{modify_val}' ({e})", True )
                    else:
                        # first we see if the node prefix is an exact match
                        node_to_remove = node

                        if not node_to_remove:
                            lopper.log._warning( f"Cannot find node {node.abs_path} for delete operation"  )
                            if self.werror:
                                sys.exit(1)
                        else:
                            try:
                                tree.delete( node_to_remove )
                                tree.sync()
                            except:
                                lopper.log._warning( f"could not remove node number: {node_to_remove.abs_path}" )

            return True

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
            # is the target domain in our tree ? If not, don't bother queing it, since
            # it is a lop specification
            try:
                td = self.tree[self.target_domain]
                self.domain_spec(self.target_domain)
            except:
                optional_lops_tree = LopperTree()
                for l in self.lops_optional:
                    try:
                        lop_possible = l.tree.nodes(self.target_domain)
                        for ll in lop_possible:
                            optional_lops_tree + ll
                    except:
                        pass

                # promote any matching optional lops, to lops that will be run
                lop = LopperFile( "" )
                lop.dts = ""
                lop.dtb = ""
                lop.fdt = None
                lop.tree = optional_lops_tree
                self.lops.append( lop )

        # force verbose output if --dryrun was passed
        if self.dryrun:
            self.verbose = 2

        lopper.log._info( f"\'{len(self.lops)}\' lopper operation files will be processed" )

        lops_runqueue = {}
        for pri in range(1,10):
            lops_runqueue[pri] = []

        # iterate the lops, look for priority. If we find those, we'll run then first
        for x in self.lops:
            if x.fdt:
                lops_fdt = x.fdt
                lops_tree = None
            elif x.dtb:
                lops_fdt = Lopper.dt_to_fdt(x.dtb)
                x.dtb = None
                x.fdt = lops_fdt
            elif x.tree:
                lops_fdt = None
                lops_tree = x.tree

            if lops_fdt:
                lops_tree = LopperTree()
                try:
                    dct = Lopper.export( lops_fdt, strict=True )
                except Exception as e:
                    lopper.log._error( f"({x}) {e}", True)

                lops_tree.load( dct )

                x.tree = lops_tree

            if not lops_tree:
                lopper.log._error( f"invalid lop file {x}, cannot process", True )

            try:
                ln = lops_tree['/']
                lops_file_priority = ln["priority"].value[0]
            except Exception as e:
                lops_file_priority = 5

            lops_runqueue[lops_file_priority].append(x)

        lopper.log._debug( f"lops runqueue: {lops_runqueue}" )

        lop_results = {}
        # iterate over the lops (by lop-file priority)
        for pri in range(1,10):
            for x in lops_runqueue[pri]:
                fdt_tree = x.tree
                lop_test = re.compile('system-device-tree-v1,lop.*')
                lop_cond_test = re.compile('.*,lop,conditional.*$' )
                skip_list = []
                for f in fdt_tree:
                    if not any(lop_test.match(i) for i in f.type):
                        continue

                    # past here, we know the node is a lop variant, we need one
                    # more check. Is the parent conditional ? if so, we don't
                    # excute it directly.
                    if any( lop_cond_test.match(i) for i in f.type):
                        skip_list = f.subnodes()
                        # for historical resons, the current node is in the subnodes
                        # yank it out or we'll be skipped!
                        skip_list.remove( f )

                    try:
                        noexec = f['noexec']
                    except:
                        noexec = False

                    try:
                        cond_exec = f['cond'].value[0]
                        tgt_lop = fdt_tree.pnode(cond_exec)
                        cond_exec_value = lop_results[tgt_lop.name]
                        if self.verbose > 1:
                            print( "[INFO]: conditional %s has result %s" % (tgt_lop.name,cond_exec_value))
                        if cond_exec_value:
                            noexec = False
                        else:
                            noexec = True
                    except Exception as e:
                        pass

                    if noexec or f in skip_list:
                        lopper.log._debug( f"noexec or skip set for:{f.abs_path}" )
                        continue

                    lopper.log._info( f"------> processing lop: {f.abs_path}" )

                    result = self.exec_lop( f, fdt_tree )
                    lop_results[f.name] = result
                    if self.verbose:
                        print( "[INFO]: ------> logged result %s for lop %s" % (result,f.name))


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

