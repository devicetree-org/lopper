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
   testing has been against python 3.10.x (the minimum version), but no issues are
   expected on newer 3.x releases.

   In addition to the standard libraries, Lopper uses: pcpp (or cpp), humanfriendly,
   dtc and libfdt for processing and manipulating device trees. These tools must be
   installed and on the PATH. You can also use the [venv](https://docs.python.org/3/library/venv.html)
   to install and manage the python dependencies, more on this below.

   **Note:** (python cpp) pcpp is optional (available on PyPi), and if not available cpp
   will be used for pre-processing input files. If comments are to be maintained
   through the processing flow, pcpp must be used since it has functionality to
   not strip them during processing.

   For yaml file processing, lopper has an optional dependency on python's yaml
   and ruamel, and anytree for importing the contents of yaml files.

#### Using [venv](https://docs.python.org/3/library/venv.html) based flow with git:

   Using python3's venv faciliates lopper development and usage. Please refer to
   python documentation to get more information about this topic. Some starting
   information follows:

First time virtual env setup:

```
    cd <lopper-repo>
    python3 -m venv .venv
    source .venv/bin/activate
    # this will install all lopper dependencies locally within the virtual env
    (.venv) % pip3 install -r requirements.txt
    (.venv) % pip list
    deactivate
```
Now, everytime you'd like to use lopper, just activate and deactivate within any shell:

```
    # Activate the virtual env on demand
    cd <lopper-repo>
    source .venv/bin/activate

    # Now, you are inside a virtual env
    # Use lopper, do development etc.
    # (.venv) % ./lopper.py ...

    # when done, deactivate virtual env
    deactivate
```

### pypi:

   % pip install lopper

   The pip installation will pull in the required dependencies, and also contains
   the following optional features:

      - 'server' : enable if the ReST API server is required
      - 'yaml'   : enable for yaml support
      - 'dt'     : enable if non-libfdt support is required
      - 'pcpp'   : enable for enhanced preprocessing functionality

   i.e.:

   % pip install lopper[server,yaml,dt,pcpp]

   **Note:** lopper (via clone or pip) contains a vendored python libfdt (from dtc),
   since it is not available via a pip dependency. If the vendored versions do not
   match the python in use, you must manually ensure that libfdt is installed and
   available.

   If libfdt python bindings are not in a standard location, make sure they are
   on PYTHONPATH:

   % export PYTHONPATH=<path to pylibfdt>:$PYTHONPATH

# submitting patches / reporting issues

Pull requests or patches are acceptable for sending changes/fixes/features to Lopper,
chose whichever matches your preferred workflow.

For pull requests and issues:

  - Use the Lopper github: https://github.com/devicetree-org/lopper

For Patches:

  - Use the groups.io mailing list: https://groups.io/g/lopper-devel
  - kernel (lkml) style patch sending is preferred
  - Send patches via git send-mail, using something like:

     % git send-email -M --to lopper-devel@groups.io <path to your patches>

For discussion:

  - Use the mailing list or the github wiki/discussions/issue tracker

# Lopper overview:

lopper.py --help

   Usage: lopper [OPTION] <system device tree> [<output file>]...
     -v, --verbose       enable verbose/debug processing (specify more than once for more verbosity)
     -t, --target        indicate the starting domain for processing (i.e. chosen node or domain label)
       , --dryrun        run all processing, but don't write any output files
     -d, --dump          dump a dtb as dts source
     -i, --input         process supplied input device tree description
     -I, --input-dirs    colon separated list of directories to search for input files (any type)
                         input directories can also be set by environment variable LOPPER_INPUT_DIRS
     -a, --assist        load specified python assist (for node or output processing)
     -A, --assist-paths  colon separated lists of paths to search for assist loading
       , --enhanced      when writing output files, do enhanced processing (this includes phandle replacement, comments, etc
       . --auto          automatically run any eligible assists (via -a) or lops (embedded)
       , --permissive    do not enforce fully validated properties (phandles, etc)
       , -W              enable a warning: 
                             invalid_phandle (warn on invalid phandles)
                             all (enable all warnings)
       , --symbols       generate (and maintain) the __symbols__ node during processing
     -o, --output        output file
       , --overlay       Allow input files (dts or yaml) to overlay system device tree nodes
     -x. --xlate         run automatic translations on nodes for indicated input types (yaml,dts)
       , --no-libfdt     don't use dtc/libfdt for parsing/compiling device trees
     -f, --force         force overwrite output file(s)
       , --werror        treat warnings as errors
     -S, --save-temps    don't remove temporary files
       , --cfgfile       specify a lopper configuration file to use (configparser format) 
       , --cfgval        specify a configuration value to use (in configparser section format). Can be specified multiple times
       , --schema        one of: "path to a dts schema", "learn" or "none" 
     -h, --help          display this help and exit
     -O, --outdir        directory to use for output files
       , --server        after processing, start a server for ReST API calls
       , --version       output the version and exit

A few command line notes:

 -i <file>: these can be any supported input file (.yaml, .dts). The compatible
            string in .dts files is used to distinguish operation files (lops) 
            from device tree files. If passed, multiple compatible input files are
            merged before processing.

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
there.

## Sample run:

  % ./lopper.py -f --enhanced --werror -v -v -i lopper/lops/lop-load.dts -i lopper/lops/lop-domain-r5.dts device-trees/system-device-tree.dts modified-sdt.dts


  % python -m lopper -f --enhanced --werror -v -v -i lopper/lops/lop-load.dts -i lopper/lops/lop-domain-r5.dts device-trees/system-device-tree.dts modified-sdt.dts

## Limitations:

 - Internal interfaces are subject to change

