# Overview:

Fundamentally, lopper takes an input device tree (normally a system device tree),
applies operations to that tree, and outputs one or more modified/processed trees.

See the README-architecture.txt for details on how lopper works. This README file
has practical information, known limitations and TODO items.

# config/setup:

Lopper is in a single repository, and is available via git or pypi:

### git:

   % git clone git://github.com/devicetree-org/lopper

   Ensure that the prerequisite tools are installed on your host. Lopper is written
   in python3, and requires that standard support libraries are installed. Current
   testing has been against python3.5.x, but no issues are expected on newer 3.x
   releases.

   In addition to the standard libraries, Lopper uses: pcpp (or cpp), dtc and libfdt for
   processing and manipulating device trees. These tools must be installed and
   on the PATH.

   **Note:** (python cpp) pcpp is optional (available on PyPi), and if not available cpp
   will be used for pre-processing input files. If comments are to be maintained
   through the processing flow, pcpp must be used since it has functionality to
   not strip them during processing.

   For yaml file processing, lopper has an optional dependency on python's yaml
   and ruamel as well as anytree for importing the contents of yaml files.

### pypi:

   % pip install lopper

   The pip installation will pull in the required dependencies, and also contains
   the following optional features:

      - 'server' : enable if the ReST API server is required
      - 'yaml'   : enable for yaml support
      - 'dt'     : enable if non-libfdt support is required
      - 'pcpp'   : enable for enhanced preprocessing functionality

   i.e.:

   % pip install loppper[server,yaml,dt,pcpp]

   **Note:** lopper (via clone or pip) contains a vendored python libfdt (from dtc), since
   it is not available via a pip dependency. If the vendored versions do not match
   the python in use, you must manually ensure that libfdt is installed and
   available.

   If it is not in a standard location, make sure it is on PYTHONPATH:

   % export PYTHONPATH=<path to pylibfdt>:$PYTHONPATH

# Lopper overview:

lopper.py --help

    Usage: lopper.py [OPTION] <system device tree> [<output file>]...
      -v, --verbose       enable verbose/debug processing (specify more than once for more verbosity)
      -t, --target        indicate the starting domain for processing (i.e. chosen node or domain label)
        , --dryrun        run all processing, but don't write any output files
      -d, --dump          dump a dtb as dts source
      -i, --input         process supplied input device tree description
      -a, --assist        load specified python assist (for node or output processing)
      -A, --assist-paths  colon separated lists of paths to search for assist loading
        , --enhanced      when writing output files, do enhanced processing (this includes phandle replacement, comments, etc
        . --auto          automatically run any assists passed via -a
        , --permissive    do not enforce fully validated properties (phandles, etc)
      -o, --output        output file
        , --overlay       Allow input files (dts or yaml) to overlay system device tree nodes
      -x. --xlate         run automatic translations on nodes for indicated input types (yaml,dts)
        , --no-libfdt     don't use dtc/libfdt for parsing/compiling device trees
      -f, --force         force overwrite output file(s)
        , --werror        treat warnings as errors
      -S, --save-temps    don't remove temporary files
        , --cfgfile       specify a lopper configuration file to use (configparser format)
        , --cfgval        specify a configuration value to use (in configparser section format). Can be specified multiple times
      -h, --help          display this help and exit
      -O, --outdir        directory to use for output files
        , --server        after processing, start a server for ReST API calls
        , --version       output the version and exit

A few command line notes:

 -i <file>: these can be either lop files, or device tree files (system device
            tree or other). The compatible string in lop files is used to
            distinguish operation files from device tree files. If passed, multiple
            device tree files are concatenated before processing.

 <output> file: The default output file for the modified system device tree. lopper
                operations can output more variants as required

**Note:** Since lopper manipulates dtb's (as compiled by dtc), some information
that is in the source dts is lost on the output of the final dts. This includes
comments, symbolic phandles, formatting of strings, etc. If you are transforming
to dts files and want to maintain this information, use the --enhanced flag.
This flag indicates that lopper should perform pre-processing and output phandle
mapping to restore both comments, labels and symbolic phandles to the final
output.

**Note:** By default Lopper puts pre-processed files (.pp) into the same
directory as the system device tree. This is required, since in some cases when
the .pp files and device tree are not in the same directory, dtc cannot resolve
labels from include files, and will error. That being said, if the -O option is
used to specify an output directory, the pre-processed file will be placed
there. If we get into a mode where the system device tree's directory is not
writeable, or the -O option is breaking symbol resolution, then we'll have to
either copy everything to the output directory, or look into why dtc can't
handle the split directories and include files.

## Sample run:

  % ./lopper.py -f --enhanced --werror -v -v -i lops/lop-load.dts -i lops/lop-domain-r5.dts device-trees/system-device-tree.dts modified-sdt.dts


  % python -m lopper -f --enhanced --werror -v -v -i lops/lop-load.dts -i lops/lop-domain-r5.dts device-trees/system-device-tree.dts modified-sdt.dts

## Limitations:

 - This is a pre-release, internal interfaces are still subject to change

