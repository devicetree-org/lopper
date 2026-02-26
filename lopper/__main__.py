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

from lopper.log import _warning, _info, _error, _debug
import logging
from lopper import lopper_directory

global device_tree
device_tree = None

def at_exit_cleanup():
    if device_tree:
        device_tree.cleanup()
    else:
        pass

with open(Path(__file__).parent / 'VERSION', 'r') as f:
    LOPPER_VERSION = f.read().strip()


def parse_schema_argument(schema_arg):
    """Parse schema argument to determine action and output path.

    Returns: (action, target) tuple where:
        action is one of: "none", "learn", "learn_dump", "load"
        target is: None, output path, or schema path
    """
    if not schema_arg:
        return ("learn", None)

    if schema_arg == "none":
        return ("none", None)
    elif schema_arg == "learn":
        return ("learn", None)
    elif schema_arg.startswith("learn:"):
        output_path = schema_arg[6:]  # Remove "learn:"
        if not output_path:
            _error("schema output path cannot be empty after 'learn:'", also_exit=1)
        return ("learn_dump", output_path)
    else:
        # Assume it's a path to an existing schema
        return ("load", schema_arg)

def usage():
    prog = "lopper"
    print(f'Usage: {prog} [OPTION] <system device tree> [<output file>]...')
    print('  -v, --verbose       enable verbose/debug processing (specify more than once for more verbosity)')
    print('  -t, --target        indicate the starting domain for processing (i.e. chosen node or domain label)' )
    print('    , --dryrun        run all processing, but don\'t write any output files' )
    print('  -d, --dump          dump a dtb as dts source' )
    print('  -i, --input         process supplied input device tree description')
    print('  -I, --input-dirs    colon separated list of directories to search for input files (any type)')
    print('                      input directories can also be set by environment variable LOPPER_INPUT_DIRS')
    print('  -a, --assist        load specified python assist (for node or output processing)' )
    print('  -A, --assist-paths  colon separated lists of paths to search for assist loading' )
    print('    , --enhanced      when writing output files, do enhanced processing (this includes phandle replacement, comments, etc' )
    print('    . --auto          automatically run any eligible assists (via -a) or lops (embedded)' )
    print('    , --permissive    do not enforce fully validated properties (phandles, etc)' )
    print('    , -W              enable a warning: '  )
    print('                          invalid_phandle (warn on invalid phandle references)' )
    print('                          duplicate_phandle (warn on duplicate phandle values)' )
    print('                          all (enable all warnings)' )
    print('    , --symbols       generate (and maintain) the __symbols__ node during processing' )
    print('  -o, --output        output file')
    print('    , --overlay       Allow input files (dts or yaml) to overlay system device tree nodes' )
    print('  -x. --xlate         run automatic translations on nodes for indicated input types (yaml,dts)' )
    print('    , --no-libfdt     don\'t use dtc/libfdt for parsing/compiling device trees' )
    print('  -f, --force         force overwrite output file(s)')
    print('    , --werror        treat warnings as errors' )
    print('  -S, --save-temps    don\'t remove temporary files' )
    print('    , --cfgfile       specify a lopper configuration file to use (configparser format) ' )
    print('    , --cfgval        specify a configuration value to use (in configparser section format). Can be specified multiple times' )
    print('    , --schema        one of: "path to a dts schema", "learn" or "none" ')
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
    input_dirs = []
    server = False
    auto_run = False
    permissive = False
    libfdt = True
    xlate = []
    overlay = False
    config_file = None
    config_vals = {}
    symbols = False
    warnings = []
    usage_flag = False
    schema = None

    try:
        opts, args = getopt.getopt(sys.argv[1:], "I:W:A:t:dfvdhi:o:a:SO:D:x:",
                                   [ "debug=", "assist-paths=", "outdir", "enhanced",
                                     "schema=", "save-temps", "version", "werror","target=", "dump",
                                     "force","verbose","help","input=","output=","dryrun",
                                     "assist=","server", "auto", "permissive", 'symbols', "xlate=",
                                     "no-libfdt", "overlay", "cfgfile=", "cfgval=", "input-dirs"] )
    except getopt.GetoptError as err:
        _error(f"{err}")
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
            # usage()
            usage_flag = True
        elif o in ('-i', '--input'):
            inputfiles.append(a)
        elif o in ('-a', '--assist'):
            cmdline_assists.append(a)
        elif o in ('-A', '--assist-path'):
            load_paths += a.split(":")
        elif o in ('-I', '--input-dirs'):
            input_dirs += a.split(":")
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
        elif o in ('--schema'):
            schema = a
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
        elif o in ('--symbols' ):
            symbols = True
        elif o in ('--overlay' ):
            overlay = True
        elif o in ('--cfgfile' ):
            config_file = a
        elif o in ('--cfgval' ):
            config_vals[a] = a
        elif o in ('-x', '--xlate'):
            xlate.append(a)
        elif o in ('-W'):
            # warning processing
            warnings.append(a)
        elif o in ('--version'):
            print( f"{LOPPER_VERSION}" )
            sys.exit(0)
        else:
            assert False, "unhandled option"


    # We split the options into two groups:
    #    1) options after -- on the command line
    #    2) options before the -- on the command line
    #
    # We can't just reply on getopt processing, since it will stop
    # handling arguments above when the firt unrecognized non dashed
    # option is found or when "--" is found as a delimeter.
    #
    # That's all fine, but it also doesn't tell us WHY it stopped
    # handling the options (-- or a non-dashed opttion), which means
    # we can't easily tell if a subcommand was being run, or it is a
    # system device tree that was being passed.
    #
    # So we double check against argv and split into the two parts
    # anything remaining before the dash could be a SDT, everything
    # after is for modules/commands
    #
    option_args = []
    non_option_args = []
    if '--' in sys.argv:
        double_dash_index = sys.argv.index('--', 1)
        # All arguments after '--' are not options
        option_args_possible = sys.argv[1:double_dash_index]
        non_option_args = sys.argv[double_dash_index + 1:]

        # Separate only unprocessed arguments
        option_args = [
            arg for arg in option_args_possible
            if arg not in [opt for opt, val in opts] and arg not in [val for opt, val in opts]
        ]

        # print( f"getopt remaining args: {args} option_args: {option_args} non_option_args: {non_option_args}" )
    else:
        option_args = args

    # any args should be <system device tree> <output file>
    module_name = ""
    module_args = {}
    module_args_found = False
    for idx, item in enumerate(option_args):
        # validate that the system device tree file exists
        if idx == 0:
            sdt = item
            sdt_file = Path(sdt)
            try:
                my_abs_path = sdt_file.resolve(strict=True)
            except FileNotFoundError:
                _error(f"system device tree {sdt} does not exist", also_exit=1)

        if idx == 1:
            if output:
                _error("output was already provided via -o")
                usage()
                sys.exit(1)
            else:
                output = item
                output_file = Path(output)
                if output_file.exists():
                    if not force:
                        _error(f"output file {output} exists, and -f was not passed", also_exit=1)

    # these are options that followed -- on the original command line
    for idx, item in enumerate(non_option_args):
        # check for chained modules "--"
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

    if module_name:
        _debug(f"modules found: {list(module_args.keys())}")
        _debug(f"         args: {module_args}")

    # was --help passed ?
    if usage_flag:
        if not module_name:
            usage()
        else:
            # a module name was found, let's pass this onto it
            pass

    if not usage_flag and not sdt:
        # if a module was found, pass along everything to it
        if not module_name:
            _error("no system device tree was supplied")
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
            op.resolve(True)
        except Exception:
            _error(f"output directory \"{outdir}\" does not exist", also_exit=1)

    # Not indicated in the help message, but we combine all the search
    # directories + environment variables. Assists, lops and dts files
    # can be found in any input directory type, but for the purpose of
    # documentation, they are considered separate for now.
    env_paths = (os.environ.get('LOPPER_INPUT_DIRS') or "").split(":")
    all_search_paths_as_passed = load_paths + input_dirs + env_paths
    all_search_paths = []
    for p in all_search_paths_as_passed:
        p_path = Path(p)
        abs_p_path = p_path.resolve()
        if not abs_p_path.exists():
            lopper.log._debug( f"input search directory {p_path} not found" )
        else:
            all_search_paths.append( abs_p_path.as_posix() )

    # check that the input files (passed via -i) exist
    for i in inputfiles:
        valid_ifile_types = [ ".json", ".dtsi", ".dtb", ".dts", ".yaml" ]
        itype = lopper.Lopper.input_file_type(i)
        if not itype in valid_ifile_types:
            _error("unrecognized input file type passed", also_exit=1)

    # config file handling
    config = configparser.ConfigParser()
    if not config_file:
        config_file = f"{lopper_directory}/lopper.ini"

    inf = Path(config_file)
    if not inf.exists():
        _error(f"config file {config_file} does not exist", also_exit=1)

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

    if schema:
        action, target = parse_schema_argument(schema)

        if action == "none":
            schema = None
        elif action == "learn":
            schema = "learn"
        elif action == "learn_dump":
            # Check if output file exists
            if target != "-":  # Not stdout
                output_path = Path(target)
                if output_path.exists():
                    _error(f"schema output file {target} already exists. Please remove it or choose a different filename.", also_exit=1)
            schema = ("learn_dump", target)
        elif action == "load":
            schemaf = Path(target)
            if not schemaf.exists():
                _error(f"schema file {target} does not exist", also_exit=1)
            schema = target
    else:
        schema = "learn"

    # Track if -x/--xlate was used for legacy fallback
    xlate_fallback = False
    if xlate:
        # -x/--xlate is deprecated in favor of --auto with BitBake-style lop matching
        # Enable autorun mode so that input files are matched against lops like %.yaml.lop
        auto_run = True
        xlate_fallback = True

    if dump_dtb:
        lopper.Lopper.dtb_dts_export( sdt, verbose )
        sys.exit(0)

    device_tree = LopperSDT( sdt )

    atexit.register(at_exit_cleanup)

    lopper.log._init( __name__ )
    lopper.log._init( "___main.py__" )

    lopper.log.init( verbose )

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
    device_tree.load_paths = all_search_paths
    device_tree.permissive = permissive
    device_tree.merge = overlay
    device_tree.autorun = auto_run
    device_tree.config = config
    device_tree.symbols = symbols
    device_tree.warnings = warnings
    device_tree.schema = schema

    if auto_run:
        # look for lops that match the pattern of the input
        # files, if so, queue them to run

        # note: this may cause duplicates, since all input files are searched
        #       and they may already be on the list. duplicates will be dealt
        #       with later.
        auto_assists = device_tree.find_any_matching_assists( inputfiles + [sdt],
                                                               xlate_fallback=xlate_fallback )
        inputfiles.extend( auto_assists )

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
                _error(f"{e}")
        else:
            try:
                # is it a python string ? try compiling and runnig it
                block = compile( debug, '<string>', 'exec' )
                eval( block )
            except Exception as e:
                _error(f"{e}")

        sys.exit(1)
    else:
        device_tree.perform_lops()

    if not dryrun:
        # write any changes to the FDT, before we do our write
        if device_tree.dts:
            lopper.Lopper.sync( device_tree.FDT, device_tree.tree.export() )
            device_tree.write( enhanced = device_tree.enhanced )
    else:
        _info(f"--dryrun was passed, output file {output} not written")

    if server:
        _info("starting WSGI server")

        try:
            import lopper.rest
            rest_support = True
        except Exception as e:
            _error(f"rest support is not loaded, check dependencies: {e}")
            rest_support = False

        if rest_support:
            lopper.rest.sdt = device_tree
            lopper.rest.app.run()  # run our Flask app

        sys.exit(1)

    device_tree.cleanup()


if __name__ == "__main__":
    main()
