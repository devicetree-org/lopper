#/*
# * Copyright (c) 2019,2020 Xilinx Inc. All rights reserved.
# *
# * Author:
# *       Bruce Ashfield <bruce.ashfield@xilinx.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

import getopt
import os
import sys
import atexit
from pathlib import Path
import configparser
import re

from lopper import LopperSDT

lopper_directory = os.path.dirname(os.path.realpath(__file__))

global device_tree
device_tree = None

def at_exit_cleanup():
    if device_tree:
        device_tree.cleanup()
    else:
        pass

with open(Path(__file__).parent / 'VERSION', 'r') as f:
    LOPPER_VERSION = f.read().strip()

def usage():
    prog = "lopper"
    print('Usage: %s [OPTION] <system device tree> [<output file>]...' % prog)
    print('  -v, --verbose       enable verbose/debug processing (specify more than once for more verbosity)')
    print('  -t, --target        indicate the starting domain for processing (i.e. chosen node or domain label)' )
    print('    , --dryrun        run all processing, but don\'t write any output files' )
    print('  -d, --dump          dump a dtb as dts source' )
    print('  -i, --input         process supplied input device tree description')
    print('  -a, --assist        load specified python assist (for node or output processing)' )
    print('  -A, --assist-paths  colon separated lists of paths to search for assist loading' )
    print('    , --enhanced      when writing output files, do enhanced processing (this includes phandle replacement, comments, etc' )
    print('    . --auto          automatically run any eligible assists (via -a) or lops (embedded)' )
    print('    , --permissive    do not enforce fully validated properties (phandles, etc)' )
    print('  -o, --output        output file')
    print('    , --overlay       Allow input files (dts or yaml) to overlay system device tree nodes' )
    print('  -x. --xlate         run automatic translations on nodes for indicated input types (yaml,dts)' )
    print('    , --no-libfdt     don\'t use dtc/libfdt for parsing/compiling device trees' )
    print('  -f, --force         force overwrite output file(s)')
    print('    , --werror        treat warnings as errors' )
    print('  -S, --save-temps    don\'t remove temporary files' )
    print('    , --cfgfile       specify a lopper configuration file to use (configparser format) ' )
    print('    , --cfgval        specify a configuration value to use (in configparser section format). Can be specified multiple times' )
    print('  -h, --help          display this help and exit')
    print('  -O, --outdir        directory to use for output files')
    print('    , --server        after processing, start a server for ReST API calls')
    print('    , --version       output the version and exit')
    print('')

def main():
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
    server = False
    auto_run = False
    permissive = False
    libfdt = True
    xlate = []
    overlay = False
    config_file = None
    config_vals = {}

    try:
        opts, args = getopt.getopt(sys.argv[1:], "A:t:dfvdhi:o:a:SO:D:x:",
                                   [ "debug=", "assist-paths=", "outdir", "enhanced",
                                     "save-temps", "version", "werror","target=", "dump",
                                     "force","verbose","help","input=","output=","dryrun",
                                     "assist=","server", "auto", "permissive", "xlate=",
                                     "no-libfdt", "overlay", "cfgfile=", "cfgval="] )
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
            debug = a
        elif o in ('-t', '--target'):
            target_domain = a
        elif o in ('-o', '--output'):
            output = a
        elif o in ('--dryrun'):
            dryrun=True
        elif o in ('--werror'):
            werror=True
        elif o in ('--server'):
            server=True
        elif o in ('-S', '--save-temps' ):
            save_temps=True
        elif o in ('--no-libfdt' ):
            libfdt=False
        elif o in ('--enhanced' ):
            enhanced_print = True
        elif o in ('--auto' ):
            auto_run = True
        elif o in ('--permissive' ):
            permissive = True
        elif o in ('--overlay' ):
            overlay = True
        elif o in ('--cfgfile' ):
            config_file = a
        elif o in ('--cfgval' ):
            config_vals[a] = a
        elif o in ('-x', '--xlate'):
            xlate.append(a)
        elif o in ('--version'):
            print( "%s" % LOPPER_VERSION )
            sys.exit(0)
        else:
            assert False, "unhandled option"

    # any args should be <system device tree> <output file>
    module_name = ""
    module_args = {}
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
                        module_args[module_name] = []
                    else:
                        module_args[module_name].append( item )
                else:
                    if module_name:
                        # another module, clear the name to trigger a re-start of the
                        # processing
                        module_name = ""

    if module_name and verbose:
        print( "[DBG]: modules found: %s" % list(module_args.keys()) )
        print( "         args: %s" % module_args )

    if not sdt:
        print( "[ERROR]: no system device tree was supplied\n" )
        usage()
        sys.exit(1)

    if not libfdt:
        import lopper.dt
        lopper.lopper_type(lopper.dt.LopperDT)
    else:
        import lopper.fdt
        lopper.lopper_type(lopper.fdt.LopperFDT)

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

        valid_ifile_types = [ ".dtsi", ".dtb", ".dts", ".yaml" ]
        itype = lopper.Lopper.input_file_type(i)
        if not itype in valid_ifile_types:
            print( "[ERROR]: unrecognized input file type passed" )
            sys.exit(1)

    # config file handling
    config = configparser.ConfigParser()
    if not config_file:
        config_file = "{}/lopper.ini".format( lopper_directory )

    inf = Path(config_file)
    if not inf.exists():
        print( "Error: config file %s does not exist" % config_file )
        sys.exit(1)

    config.read( inf.absolute() )

    if config_vals:
        # was there a ".", if so that's the section split marker
        for i,k in config_vals.items():
            config_sections = k.split( '.' )
            if len(config_sections) > 1:
                # we have sections
                config_option = config_sections[-1]
                config_option_name = config_option.split('=')[0]
                config_option_val = config_option.split('=')[-1]
                if config_option_name == config_option_val:
                    config_option_val = True

                for item in config_sections[:-1]:
                    try:
                        section = config[item]
                    except:
                        config[item] = {}

                    config[item][config_option_name] = str(config_option_val)

            else:
                # global section, not currently implemented
                pass

    if xlate:
        for x in xlate:
            # *x_lop gets all remaining splits. We don't always have the ":", so
            # we need that flexibility.
            x_type, *x_lop = x.split(":")

            x_files = []
            if x_lop:
                x_files.append( x_lop[0] )
            else:
                # generate the lop name
                extension = Path(x_type).suffix
                extension = re.sub( "\.", "", extension )
                x_lop_gen = "lop-xlate-{}.dts".format(extension)
                x_files.append( x_lop_gen )

        # check that the xlate files exist
        for x in x_files:
            inf = Path(x)
            if not inf.exists():
                x = "{}/lops/".format( lopper_directory ) + x
                inf = Path( x )
                if not inf.exists():
                    print( "[ERROR]: input file %s does not exist" % x )
                    sys.exit(1)

            inputfiles.append( x )

    if dump_dtb:
        lopper.Lopper.dtb_dts_export( sdt, verbose )
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
    device_tree.permissive = permissive
    device_tree.merge = overlay
    device_tree.autorun = auto_run
    device_tree.config = config

    device_tree.setup( sdt, inputfiles, "", force, libfdt, config )
    device_tree.assists_setup( cmdline_assists )

    if auto_run:
        for a in cmdline_assists:
            try:
                assist_args = module_args[a]
            except:
                assist_args = []

            device_tree.assist_autorun_setup( a, assist_args )
    else:
        # "modules" are assists passed after -- on the command line call to
        # lopper.
        if module_args:
            # This sets the trigger node of "/", and makes it autorun
            for module_name in reversed(list(module_args.keys())):
                m_args = module_args[module_name]
                device_tree.assist_autorun_setup( module_name, m_args )

    if debug:
        if debug == "profile":
            import cProfile
            cProfile.runctx( 'device_tree.perform_lops()', globals(), locals() )

        elif re.search( r'\.py$', debug ):
            from importlib.machinery import SourceFileLoader

            for p in load_paths:
                if p not in sys.path:
                    sys.path.append( p )

            try:
                imported_test = SourceFileLoader( "debug", debug ).load_module()
                func_name_to_call = Path( debug ).stem
                func_to_call = getattr( imported_test, func_name_to_call )
                func_to_call( device_tree )
            except Exception as e:
                print( "ERROR: %s" % e )
        else:
            try:
                # is it a python string ? try compiling and runnig it
                block = compile( debug, '<string>', 'exec' )
                eval( block )
            except Exception as e:
                print( "ERROR: %s" % e )

        sys.exit(1)
    else:
        device_tree.perform_lops()

    if not dryrun:
        # write any changes to the FDT, before we do our write
        lopper.Lopper.sync( device_tree.FDT, device_tree.tree.export() )
        device_tree.write( enhanced = device_tree.enhanced )
    else:
        print( "[INFO]: --dryrun was passed, output file %s not written" % output )

    if server:
        if verbose:
            print( "[INFO]: starting WSGI server" )

        try:
            import lopper.rest
            rest_support = True
        except Exception as e:
            print( "[ERROR]: rest support is not loaded, check dependencies: %s" % e )
            rest_support = False

        if rest_support:
            lopper.rest.sdt = device_tree
            lopper.rest.app.run()  # run our Flask app

        sys.exit(1)

    device_tree.cleanup()


if __name__ == "__main__":
    main()

