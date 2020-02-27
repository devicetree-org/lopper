config/setup:
-------------

Lopper is in a single repository:

  % git clone git://<location to lopper repo/

Ensure that the prerequisite tools are installed on your host. Lopper is written
in python3, and requires that standard support libraries are installed.

In addition to the standard libraries, Lopper uses: cpp, dtc and libfdt for
processing and manipulating device trees. These tools must be installed and
on the PATH.

Make sure you have libfdt installed and available. If it is not in a standard
location, make sure it is on PYTHONPATH:

     export PYTHONPATH=<path to pylibfdt>:$PYTHONPATH

Lopper overview:
----------------

lopper.py --help

   Usage: lopper.py [OPTION] <system device tree> [<output file>]...
      -v, --verbose	  enable verbose/debug processing (specify more than once for more verbosity)
      -t, --target	  indicate the starting domain for processing (i.e. chosen node or domain label)
	, --dryrun	  run all processing, but don't write any output files
      -d, --dump	  dump a dtb as dts source
      -i, --input	  process supplied input device tree description
      -a, --assist	  load specified python assist (for node or output processing)
      -o, --output	  output file
      -f, --force	  force overwrite output file(s)
        , --werror	  treat warnings as errors
      -S, --save-temps	  don't remove temporary files
      -h, --help	  display this help and exit
      -O, --outdir	  directory to use for output files
       , --version	  output the version and exit

Fundamentally, lopper takes an  input device tree (normally a system device tree),
applies operations to that tree, and outputs a modified/processed tree.

A few command line notes:

 -i <file>: these can be either lop files, or device tree files (system device
            tree or other). The compatible string in lop files is used to
            distinguish operation files from device tree files. If passed, multiple
            device tree files are concatenated before processing.

 <output> file: The default output file for the modified system device tree. lopper
                operations can output more variants as required

Note that since lopper manipulates dtb's (as compiled by dtc) some information
that is in the source dts is lost on the output of the final dts. This includes
comments, symbolic phandles, formatting of strings, etc. To maintain this
information, changes to dtc are required, and while that work is planned, there
is no estimated completion date.

Lopper puts the preprocessed file (.pp) into the same directory as the system
device tree. Without doing this, dtc cannot resolve labels from include files,
and will throw an error. If we get into a mode where the system device tree's
directory is not writeable, then we'll have to either copy everything or look
into why dtc can't handle the split directories and include files.


Lopper processing flow:
-----------------------

Lopper is completely data driven and only performs operations or invokes assist
routines as passed into it via command line files and options. It does not have
codified understanding of a system device tree, and doesn't infer or trigger
operations based on the content of the tree.

Complex logic can be performed in assist routines, or multiple core operations
can be stacked to modify and transform the tree. Depending on how the inputs to
the tool are produced, lop files can be large and complex, or small and simple
with logic resting in the assist modules. The choice is up to the user.

Lopper abstracts the libraries and internal formats used to manipulate the
device tree. As long as Lopper routines and abstractions are used, the internal
format of the files, and libraries to manipulate the tree can change in the
future without the inputs and outputs differing.

Currently, Lopper operates on dtb files. It does not parse or otherwise
manipulate source dts files. It currently uses libfdt for operations on these
files, and uses the standard dtc tools to prepare the files for manipulation.

The flow of lopper processing is broken into the following broad categories:

  - setup

    The inputs are validated and the base SystemDeviceTree object created to
    manage the provided system device tree.

  - input file normalization with standard tools

    Lopper processes input files by invoking a standard pipeline of processing
    on dts files using standard tools. cpp is used for preprocessing and
    expansion, and dtc is used to compile dts inputs into dtbs. Lopper is
    somewhat tolerant of incomplete dts inputs, and will use forced dtc
    compilation to ensure that dtbs are generated (with the assumption that the
    lopper operations will adjust and fix any issues with the input files).

    system device tree, device tree and lopper operations files are all
    processed with the same tools during input file normalization.

    Note that lopper operations file can be passed directly as dtb files, and
    applied to the tree, but the system device tree and other device tree
    fragments must be source (so they can be preprocessed and concatenated
    as needed).

  - operation runqueue execution

    Once the system device tree is established, and lopper operation files
    identified, the lops are processed in priority order (priority specified at
    the file level), and the rules processed in order as they appear in the lop
    file.

    lopper operations can immediately process the output of the previous
    operation and hence can be stacked to perform complex operations.

  - finalization / output

    Once all operations have been exected against a tree, some common
    finalization / resize and other sanity checks are executed against the tree.
    If inconsistencies or other errors are detected, the user is notified and
    lopper exits.

  - cleanup

    As part of processing the input files, lopper generates temp or intermediate
    files. An exit and trap handler are part of lopper and will clean up in the
    case or normal or abnormal exit.

Lopper Classes / Routines:
--------------------------

Lopper contains the following classes for use when manipulating a system device
tree:

  - Lopper
  - SystemDeviceTree
  - Lop (internal use only)
  - LopAssist (internal use only)

Lopper provides utility routines and wrappers around libfdt functions to operate
on flattened device trees. More robust encode, decode of properties, node copy,
etc. These utilities routines will work on any FDT object as returned by libfdt,
and hence can work on both the fdt embedded in a SystemDeviceTree object, or on
loaded lopper operation files.

The SystemDeviceTree object is an abstraction around the loaded system device
tree, and is the main target of lopper operations. It provides 1:1 wrappers
around Loppper static methods (passing its internal flattened device tree to
lopper by default) and also provides routines to read/write device trees, tree
filtering routines, etc.

The SystemDeviceTree object is responsible for the setup of the FDT (using dtc,
cpp, etc, to compile it to a dtb), loading operations and assists, running
operations, writing the default output and cleaning up any temporary files.

TODO: property document all the routines and which ones are used from assists, etc
      This will be via pydoc strings

Lopper operations
-----------------

Lopper operations are passed to the tool in a dts format file. Any number of
operations files can be passed, and they will be executed in priority order.

A lopper operations (lops) file has the following structure:

-----
/dts-v1/;

/ {
        compatible = "system-device-tree-v1";
        // optional priority, normally not specified
        priority = <1>;
        lops {  
                lop_<number> { 
                        compatible = "system-device-tree-v1,lop,<lop type>";
                        <lop specific properties>;
                };
                lop_<number> { 
                        compatible = "system-device-tree-v1,lop,<lop type>";
                        <lop specific properties>;
                };
        };
};
-----

The important elements of the file are that it is structured like any standard
dts file, and that the root of the device tree must have the compatible string,
so that it will be recognized as a lop file.

  compatible = "system-device-tree-v1";

The lops can then have a specified priority, with <1> being the highest 
priority and <10> being the lowest. This is used to broadly order operations
such that preparation lops (such as loading a module) can be run before
dependent operations.

Finally, a set of lops are passed. The lops are identified by lop_<number>
and have a compatible string that identifies the type of operation, followed
by any lop specific properties.

NOTE/TODO: bindings will be written for the lopper operations.

The following types of lops are currently valid:

# module load: load a lopper assist module

               lop_0 {
                        compatible = "system-device-tree-v1,lop,load";
                        // load: name of the module to load
                        load = "<python module name>.py";
                };

                lop_1 {
                        compatible = "system-device-tree-v1,lop,load";
                        load = "cdo.py";
                        // props describes the extra properties of this assist,
                        // so they can be loaded and stored with the module
                        props = "id", "file_ext";
                        // the extension of the output file that this is
                        // compatible with.
                        file_ext = ".cdo";
                        // the id that this module is compatible with
                        id = "xlnx,output,cdo";
                };

# assist: call an assist function that is compatible with the id

                lop_0 {
                        compatible = "system-device-tree-v1,lop,assist-v1";
                        // node: path to the device tree node to search for an assist
                        node = "/chosen/openamp_r5";
                        // id: string to pass to assist modules to identify compatible
                        //     assists
                        id = "openamp,domain-v1";
                };

# modify: a general purpose node and property modify/delete/add operation
#
#         format is: "path":"property":"replacement"
#                     - modify to "nothing", is a remove operation
#                     - modify with no property is node operation (rename or remove)
#

                lop_1 { 
                        compatible = "system-device-tree-v1,lop,modify";
                        // node name modify. rename /cpus_r5/ to /cpus/
                        modify = "/cpus_r5/::/cpus/";
                };
                lop_2 { 
                        compatible = "system-device-tree-v1,lop,modify";
                        // remove access property from /cpus/ node
                        modify = "/cpus/:access:";
                };
                lop_4 {
                        compatible = "system-device-tree-v1,lop,modify";
                        // node delete
                        modify = "/amba/::";
                };
                lop_11 {
                        compatible = "system-device-tree-v1,lop,modify";
                        // property value modify
                        modify = "/:model:this is a test";
                };
                lop_12 {
                       compatible = "system-device-tree-v1,lop,modify";
                       // property add
                       // example: add a special ID into various nodes
                       modify = "/:pnode-id:0x7";
                };
                lop_15 {
                       compatible = "system-device-tree-v1,lop,modify";
                       // property add to node + matching child nodes
                       modify = "/amba/:testprop:testvalue";
                       // nodes that match this regex will have the operation applied
                       nodes = "/amba/.*ethernet.*phy.*";
                };



# node add: copies the compiled node to the target device tree
#
# Additional operations or assists can modify this node just as if it was
# compiled into the original device tree. in this example the __...__ values
# will be filled in by an assist routine.
#

                lop_7 { 
                        // node add
                        compatible = "system-device-tree-v1,lop,add";
                        // name of the embedded node
                        node_src = "zynqmp-rpu";
                        // destination path in the target device tree
                        node_dest = "/zynqmp-rpu";
                        zynqmp-rpu {
                            compatible = "xlnx,zynqmp-r5-remoteproc-1.0";
                            #address-cells = <2>;
                            #size-cells = <2>;
                            ranges;
                            core_conf = "__core_conf__";
                            r5_0: __cpu__ {
                                  // #address-cells = <2>;
                                  // #size-cells = <2>;
                                  // <0xF> indicates that it must be replaced
                                  #address-cells = <0xF>;
                                  #size-cells = <0xF>;
                                  ranges;
                                  // memory-region = <&rproc_0_reserved>, <&rproc_0_dma>;
                                  memory-region = <&__memory_access__>;
                                  pnode-id = <0x7>;
                                  // mboxes = <&ipi_mailbox_rpu0 0>, <&ipi_mailbox_rpu0 1>;
                                  mboxes = <&__mailbox_ipi__>;
                                  // mbox-names = "tx", "rx";
                                  mbox-names = "__mbox_names__";
                                  tcm_0_a: tcm_0@0 {
                                           reg = <0x0 0xFFE00000 0x0 0x10000>;
                                           pnode-id = <0xf>;
                                  };
                                  tcm_0_b: tcm_0@1 {
                                         reg = <0x0 0xFFE20000 0x0 0x10000>;
                                         pnode-id = <0x10>;
                                  };
                            };
                        };
                  };


# output: write selected nodes to an output file
#
# multiple of these can be in a single lop file. They pull fields from the
# modified system device tree and write them to output files.

                lop_13 {
                       compatible = "system-device-tree-v1,lop,output";
                       outfile = "openamp-test.dtb";
                       // list of nodes to select and output
                       nodes = "reserved-memory", "zynqmp-rpu", "zynqmp_ipi1";
                };
                lop_14 {
                       compatible = "system-device-tree-v1,lop,output";
                       outfile = "linux.dtb";
                       // * is "all nodes"
                       nodes = "*";
                };
		lop_16 {
		       compatible = "system-device-tree-v1,lop,output";
		       outfile = "linux-partial.dts";
		       // * is "all nodes"
		       nodes = "amba.*";
	        };
		lop_17 {
                       compatible = "system-device-tree-v1,lop,output";
		       outfile = "plm.cdo";
		       // * is "all nodes"
		       nodes = "*";
                       // lopper output assist will kick in
		       id = "xlnx,output,cdo";
	        };
		lop_18 {
		       compatible = "system-device-tree-v1,lop,output";
		       outfile = "linux-special-props.dts";
		       // nodes (regex), with a property that must be set
		       nodes = "amba.*:testprop:testvalue";
		};

Lopper Assists
--------------

Assists can be used to perform operations on the tree (using libfdt or Lopper
utility routines) or to generate output from a tree.

Note: assists are capable of modifying any part of the tree, so must be fully
      tested and debugged before release.

Assists are written in python and are loaded by Lopper as instructed by either
the command line --assist or by a lopper operation (lop) input file. Assist are
not full python modules as defined by the python specification, but are normal
scripts that are loaded by Lopper and executed in its name space.

Modules must implement a function of the following type:

   is_compat( node, compat_string_to_test )

Lopper will call that function when processing an lopper assist operation for a
specified node. The node in question and the lopper operation defined ID string
are arguments to the function call. If the module is compatible with the passed
ID string, it returns the function name to call for further processing and empty
string is returned if the module is not compatible.

For example, the openamp's function is as follows:

    def is_compat( node, compat_string_to_test ):
        if re.search( "openamp,domain-v1", compat_string_to_test):
            return process_domain
        return ""

The returned function must be of the following format (the name of the function
doesn't matter):

   def assist_routine( tgt_node, sdt, verbose=0 )

If compatible, lopper calls the assist routine with the arguments set to:

  - the target node number
  - the system device tree object
  - the verbose flag

Once called the function can use the node number and the system device tree
(with its embedded FDT) to discover more information about the tree and
manipulate the tree itself (and hence used by future lopper operations or assist
calls). Lopper utility routines, SDT object calls or raw libfdt routines can be
used to modify the tree.

If the module has invalid code, or otherwise generates and exception, Lopper
catches it and reports the error to the user.

Note: the exact details of all exceptions cannot always be displayed so
      unit tested or stepping through the code may be required.

output assists:
---------------

Output assists are similar to standard (node) assists, except they are called
when an output file extension is not recognized. Each loaded assist is queried
with either an id that was provided when the output assist lop was loaded (see
example above), or one that is associated with an output node in a lop file.

If compatible, and output assist should return a function of the same
format as a standard assist:

   def assist_write( node, sdt, outfile, verbose=0 ):

The routine can write the appropriate parts of the system device tree to the
passed output filename.

execution samples:
------------------

# testing with openamp domains
#
# Notes: -v -v: verbosity level 2
#        -f: force overwrite files if they exist
#        -i: module load lop
#        -i: main lop file for modyfing system device tree (with a unified chosen node in this example)
#        foo.dts: output file (dts format)
# 
lopper.py -f -v -v -i lop-load.dts -i xform-domain-r5.dts system-device-tree-domains.dts foo.dts

# testing with binary transform
lopper.py -f -v -v -i xform-load.dts -i xform-domain-r5.dts -i xform-bin.dtb system-device-tree-domains.dts foo.dts

# testing with split chosen node
lopper.py -f --werror -v -v -v -v -i lop-load.dts -i lop-domain-r5.dts -i lop-bin.dtb -i system-device-tree-chosen.dts system-device-tree-domains.dts foo.dts 
lopper.py -f --werror -v -v -v -v -i lop-load.dts -i lop-domain-a53.dts -i lop-bin.dtb -i system-device-tree-chosen.dts system-device-tree-domains.dts foo.dts

# dump a dtb to console as a "dts"
lopper.py --dump linux.dtb
