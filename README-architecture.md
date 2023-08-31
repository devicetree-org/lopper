# Lopper processing flow:

Lopper is data driven and only performs operations or invokes assist routines as
specified by its inputs (command line or operation files). This means that
Lopper does not have codified understanding of a system device tree, and doesn't
infer or trigger operations based on the content of the tree.

Complex logic can be performed in assist routines, and/or multiple core
operations can be stacked to modify and transform the tree. Depending on how the
inputs to the tool are produced, lop files can be large and complex, or small
and simple with more complex logic resting in the assist modules. The choice is
up to the user.

Lopper abstracts the libraries and internal formats used to manipulate the
device tree. As long as Lopper routines and abstractions are used, the internal
format of the files, and libraries to manipulate the tree can change in the
future without the inputs and outputs differing.

Currently, Lopper operates on dtb files. It does not parse or otherwise
manipulate source dts files (except for pre-processing). Lopper uses libfdt for
operations on these files, and uses the standard dtc tools to prepare the files
for manipulation.

The flow of lopper processing is broken into the following broad categories:

  - setup

    The inputs are validated and the base LopperSDT object created to manage the
    provided system device tree. This object abstracts the libraries and tree
    structure whenever possible.

  - input file normalization with standard tools

    Lopper processes input files by invoking a standard pipeline of processing
    on dts files using standard tools. pcpp (or cpp) is used for pre-processing and
    expansion, and dtc is used to compile dts inputs into dtbs. Lopper is
    somewhat tolerant of incomplete dts inputs, and will use forced dtc
    compilation to ensure that dtbs are generated (with the assumption that the
    lopper operations will adjust and fix any issues with the input files).

    system device tree, device tree and lopper operations files are all
    processed with the same tools during input file normalization.

    Note that lopper operations file can be passed directly as dtb files, and
    applied to the tree, but the system device tree and other device tree
    fragments must be source (so they can be pre-processed and concatenated
    as needed).

  - operation runqueue execution

    Once the system device tree is established, and lopper operation files
    identified, the lops are processed in priority order (priority specified at
    the file level), and the rules processed in order as they appear in the lop
    file.

    lopper operations can immediately process the output of the previous
    operation and hence can be stacked to perform complex operations.

  - finalization / output

    Once all operations have been executed against a tree, some common
    finalization / resize and other sanity checks are executed against the tree.
    If inconsistencies or other errors are detected, the user is notified and
    lopper exits.

    Lopper can also stay resident after execution and offer a ReST API to
    query the device tree. The details of the API are still in design, but will
    be described in this document when complete.

  - cleanup

    As part of processing the input files, lopper generates temp or intermediate
    files. An exit and trap handler are part of lopper and will clean up in the
    case or normal or abnormal exit.

# Lopper Classes / Routines:

Lopper contains the following classes for use when manipulating a system device
tree:

  - Lopper
  - LopperSDT
  - LopperTree
       - LopperNode
       - LopperProp
       - LopperTreePrinter
  - LopperYAML
  - LopperFile (internal use only)
  - LopperAssist (internal use only)

The Lopper class is a container class (with static methods) of utility routines
and wrappers around libfdt functions for flattened device trees. More robust
encode, decode of properties, node copy, etc. These utilities routines will work
on any FDT object as returned by libfdt, and hence can work on both the fdt
embedded in LopperSDT/LopperTree objects, or on loaded lopper operation files.

The LopperSDT class is an abstraction around the loaded system device tree, and
is the primary target of lopper operations. This class is responsible for the
setup of the FDT (using dtc, pcpp (or cpp), etc, to compile it to a dtb), loading
operations and assists, running operations, writing the default output and
cleaning up any temporary files.

LopperYAML is a reader/writer of YAML inputs, and it converts what is read to
LopperTree format (and from tree -> YAML on write). The internals of LopperYAML
are not significant, except for the routines to_yaml() and to_tree(), which are
used to convert formats.

LopperSDT uses the LopperTree + LopperNode + LopperProp classes to manipulate
the underlying FDT without the details of those manipulations being throughout
the classes. These classes provide python ways to iterate, access and write tree
based logic around the underlying device tree.

A snapshot of pydoc information for lopper is maintained in README.pydoc. For
the latest detailed information on lopper, execute the following:

    % pydoc3 lopper/__init__.py
    % pydoc3 lopper/tree.py
    % pydoc3 lopper/fdt.py
    % pydoc3 lopper/dt.py

# Lopper Inputs / Outputs:

Although most inputs and outputs from Lopper are dts files (or dtb in rare cases),
YAML is also supported. While not everything can (or should) be expressed in
YAML, both lops and device tree elements can be expressed in this format.

Note: The core of Lopper, assists, lops, etc, are not aware of the input /
output formats, but operate on the LopperTree/Lopper data structures. It is this
separation that allows Lopper to abstract both the tree and convert between the
various formats.

To aid decoding and interpretation of properties carried in a LopperTree, if a
node has been created from yaml, the LopperNode field '_source' is set to "yaml"
(otherwise it is "dts").

# Lopper Tree and complex (non-dts) types:

Depending on the input format, complex data types or associated data are
carried in the Lopper tree.

As an example, yaml constructs can be mapped directly to device tree formats
(strings, ints, etc), but other complex structures (maps, lists, mixed types)
need to be interpreted / expanded by an assist or an xlate lop.

To ensure that the LopperTree is always compatible with dts/fdt, these complex
types are json encoded and carried as a string in a LopperProp. When json
encoding is used, the "pclass" of the LopperProp is set to "json", so that it
can be loaded and expanded for processing.

# Lopper operations

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

  compatible = "system-device-tree-v1,lop";

The lops can then have a specified priority, with <1> being the highest
priority and <10> being the lowest. This is used to broadly order operations
such that preparation lops (such as loading a module) can be run before
dependent operations.

Finally, a set of lops are passed. The lops are identified by lop_<number> and
have a compatible string that identifies the type of operation, followed by any
lop specific properties. The <number> in a lop node is a convention for easier
reading, but is not used by lopper to order operations. The order lops appear in
the file is the order they are applied.

lops are identified through the compatible string:

    compatible = "system-device-tree-v1,lop,<lop type>

The valid lop types are desribed below. Note that the lop type can have an
optional "-v<version>" appended (i.e. -v1) and will be accepted. The version
specification is optional at the moment, since only -v1 operations exist.

A lop can be specified, and have execution inhibited via the 'nexec;' property
in that lop (child lops also need this to be specified). This allows for lopper
operations to be carried, but only enabled for debug, etc.

NOTE/TODO: bindings will be written for the lopper operations.

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
                        // the extension of the output file with which this is
                        // compatible.
                        file_ext = ".cdo";
                        // the id that this module is compatible with
                        id = "xlnx,output,cdo";
                };

# assist: call an assist function that is compatible with the id

                lop_0 {
                        compatible = "system-device-tree-v1,lop,assist-v1";
                        // node: path to the device tree node to search for an assist
                        node = "/domains/openamp_r5";
                        // id: string to pass to assist modules to identify compatible
                        //     assists
                        id = "openamp,domain-v1";
                        // output is optional, specify if a different output from the
                        // lopper default is required. lopper does no managment of files
                        // or this directory.
                        // output = "<output directory>";
                };

# modify: a general purpose node and property modify/delete/add/move operation

    #         format is: "path":"property":"replacement"
    #                     - modify to "nothing", is a remove operation
    #                     - modify with no property is node operation (rename or remove)
    #
    #         To update complex/compound values, see the phandle#property notation in the
    #         examples below

                lop_1 {
                        compatible = "system-device-tree-v1,lop,modify";
                        // node name modify. Rename /cpus_r5/ to /cpus/
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
                        modify = "/axi/::";
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
                       // nodes that match this regex will have the operation applied
                       modify = "/axi/.*ethernet.*phy.*:testprop:testvalue";
                       // note: nodes is legacy now. Just put the regex into the modify parameter
                       // nodes = "/axi/.*ethernet.*phy.*";
                };
                lop_16 {
                       compatible = "system-device-tree-v1,lop,modify";
                       // moves the node reserved-memory under a new parent
                       modify = "/reserved-memory::/zynqmp-rpu/reserved-memory";
                };
                lop_17 {
                        compatible = "system-device-tree-v1,lop,modify";
                        # finds the target phandle (modify_val), which can either be in
                        # this tree, or the system device tree, and looks up the property
                        # following '#', that value is used as the replacement. If no property
                        # is provided, then the value is changed to the phandle target.
                        modify = "/memory@800000000:reg:&modify_val#reg";
                        modify_val {
                            reg = <0x0 0x00000000 0x0 0x200000>;
                        };
                };

# node add: copies the compiled node to the target device tree

    # Additional operations or assists can modify this node just as if it was
    # compiled into the original device tree. In this example the __...__ values
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
		       nodes = "axi.*";
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
		       nodes = "axi.*:testprop:testvalue";
		};

# conditional: do a conditional test on nodes of the tree, and execute an operation

    # does a set of conditional tests against a nodes in the system device tree that
    # match the structure of the conditional tree found at the base defined by "cond_root".
    #
    # Note: although nodes/labels with syntax like: 'cpu@.*' were never accepted syntax
    #       dtc would allow it on a forced run. As of the latest releases of dtc (1.6.1+)
    #       a forced run generates invalid phandles, and even though we can still have
    #       the regex encoded into the node name, exec<> blocks won't properly execute
    #       due to invalid phandles.
    #
    #       If conditionals with wildcard paths are not working, either use the 'select'
    #       lop, or use the property 'cond_select' with the path as a string (with
    #       wildcards.
    #
    #       i.e.
    #
    #                   compatible = "system-device-tree-v1,lop,conditional-v1";
    #                   cond_root = "cpus";
    #                   cpus {
    #                        cpu@.* {
    #                            compatible = ".*a72.*";
    #                        };
    #                   };
    #
    #           becomes
    #
    #                   compatible = "system-device-tree-v1,lop,conditional-v1";
    #                   cond_root = "cpus";
    #                   cond_select = "/cpus/cpus@.*"
    #                   cpus {
    #                        cpu {
    #                            compatible = ".*a72.*";
    #                        };
    #                   };
    #
    #
    # conditions are compound, if any are not true, then the overall condition is False
    # conditions can be inverted with a suffix of __not__ at the end of property name
    #
    # if the result is true, all blocks that start with "true" are executed. These can
    # contain any valid lop, and can modify the tree. Execution stops if any lop returns
    # False.
    #
    # Similarly, false blocks are executed when the conditions evaluate to false.
    #
    # Note: the "select" lop is a newer, and cleaner way to select a group of nodes
    #       for code operation. Conditional continues to work, but consider using
    #       select -> code lops instead.
    #
    # code: a block of python code to execute against a node
    #
    # code blocks are identified with the compatible string: "system-device-tree-v1,lop,code"
    # '-v1' can follow 'code', but it is not required as only one format of python code
    # is currently supported.
    #
    # Execute python code in a restricted environment. This can be used to test and
    # produce special output without needing to write an assist. Changes made to a node
    # are persistent and hence collection of data can be done, as the following examples
    # show.
    #
    # The 'code' property contains the block of python code to be executed. Note, that
    # since this is compiled with dtc, you cannot use quotes " within the code, and
    # should use single quotes ' instead.
    #
    # The 'options' property in a code lop is of the format:
    #
    #    <variable name>:<value>
    #
    # Lopper will arrange for a variable to be available to the python code under
    # that name, with the specified value.
    #
    # Code blocks can also inherit Lopper assists, to facilitate code reuse.
    # The property "inherit" is a comma separate list of python
    # assists that should be loaded and made available to the code block
    # on execution.
    #
    # The python modules must be searchable by the python loader and in
    # an "assists" subdirectory. They will be loaded and made available under
    # their module name for direct use in the code block.
    #
    # See the 'xlate' lop for an example of 'inherit'
    #
    # See README.pydoc for details of the code execution environment
    
                lop_15_1 {
                      compatible = "system-device-tree-v1,lop,conditional-v1";
                      cond_root = "cpus";
                      cpus {
                           cpu@.* {
                               compatible = ".*a72.*";
                           };
                      };
                      true {
                           compatible = "system-device-tree-v1,lop,code-v1";
                           code = "
                               print( '[INFO] compatible a72 found: %s' % node )
                               print( '[FOUND] enable-method: %s' % node['enable-method'].value[0] )

                               return True
                               ";
                      };
                      true_2 {
                           compatible = "system-device-tree-v1,lop,code-v1";
                           code = "
                               print( '[FOUND 2] enable-method: %s' % node['enable-method'].value[0] )

                               return True
                               ";
                      };
                };
                lop_15_2 {
                      compatible = "system-device-tree-v1,lop,conditional-v1";
                      cond_root = "cpus";
                      cpus {
                           cpu@.* {
                               compatible = ".*invalid-proc-72.*";
                           };
                      };
                      true {
                           compatible = "system-device-tree-v1,lop,code-v1";
                           code = "
                               print( '[INFO] compatible invalid a72 found: %s' % node )

                               return True
                               ";
                      };
                      false {
                           compatible = "system-device-tree-v1,lop,code-v1";
                           code = "
                               print( '[FOUND] cpu that does not match invalid a72: %s' % node )

                               return True
                               ";
                      };
                };
                lop_15_3 {
                      compatible = "system-device-tree-v1,lop,conditional-v1";
                      cond_root = "cpus";
                      cpus {
                           cpu@.* {
                               compatible = ".*a72.*";
                               operating-points-v2 = <0x1>;
                           };
                      };
                      true {
                           compatible = "system-device-tree-v1,lop,code-v1";
                           code = "
                               print( '[INFO] double condition a72 found: %s' % node )

                               return True
                               ";
                      };
                };
                lop_15_4 {
                      compatible = "system-device-tree-v1,lop,conditional-v1";
                      cond_root = "cpus";
                      cpus {
                           cpu@.* {
                               compatible = ".*a72.*";
                               operating-points-v2 = <0x2>;
                           };
                      };
                      false {
                           compatible = "system-device-tree-v1,lop,code-v1";
                           code = "
                               print( '[INFO] double condition a72 not found: %s' % node )

                               return True
                               ";
                      };
                };
                lop_15_5 {
                      compatible = "system-device-tree-v1,lop,conditional-v1";
                      cond_root = "cpus";
                      cond_select = "/cpus/cpu@.*";
                      cpus {
                           cpu@ {
                               compatible = ".*a72.*";
                               operating-points-v2__not__ = <0x2>;
                           };
                      };
                      true {
                           compatible = "system-device-tree-v1,lop,code-v1";
                           code = "
                               print( '[INFO] double condition inverted a72 found: %s' % node )

                               return True
                               ";
                      };
                };
                lop_15_6 {
                      compatible = "system-device-tree-v1,lop,conditional-v1";
                      cond_root = "cpus";
                      cpus {
                           cpu@.* {
                               compatible = ".*a72.*";
                               clocks = <0x3 0x4d>;
                           };
                      };
                      true {
                           compatible = "system-device-tree-v1,lop,code-v1";
                           code = "
                               print( '[INFO] double condition list a72 found: %s' % node )
                               node['magic-clock'] = 'True'

                               return True
                               ";
                      };
                };
                lop_15_7 {
                      compatible = "system-device-tree-v1,lop,conditional-v1";
                      cond_root = "cpus";
                      cpus {
                           cpu@.* {
                               compatible = ".*a72.*";
                           };
                      };
                      true {
                           compatible = "system-device-tree-v1,lop,code-v1";
                           code = "
                               print( '[INFO] node tag: %s (tag:%s)' % (node,node['tag'].value[0]) )
                               try:
                                   print( '[INFO] clock magic: %s' % node['magic-clock'].value[0] )
                               except:
                                   pass

                               return True
                               ";
                      };
                };


# print: output strings during processing

    # print provides basic string output and is primarily provided for debug purposes
    # (complex output can be generated from code lops).
    #
    # Any properties that begin with "print" will be output to stdout when the lop
    # is processed.
    #
    # One convenience routine is provided to print a node. If the print property is
    # a valid phandle, then the node will be pretty printed to stdout.
    #

                lop_16_2 {
                      compatible = "system-device-tree-v1,lop,print-v1";
                      print = "print_test: print 1";
                      print2 = "print_test: print2";
                      print3 = <0x2>;
                };


# exec: execute another lop

    # Commonly used in combination with a conditional lop to avoid code duplication
    # and execute another lopper operation. i.e. renaming a node, deleting a property
    # running code, etc.
    #
    # The lop to exec is found in the "exec" property of the lop, and must be a valid
    # phandle to the target lopper operation.
    #
    # The 'options' property in a code lop is of the format:
    #
    #    <variable name>:<value>
    #
    # Lopper will arrange for those options to be available to the called lop routine.
    # If the exec'd lop is a code block, the options will be propagated to the code
    # as local variables.
    #

                track_feature: track_feature {
                        compatible = "system-device-tree-v1,lop,code-v1";
                        code = "
                            print( 'track: lopper library routine: %s' % node )
                            try:
                                node.ttunes[prop] = prop
                            except:
                                pass
                        ";
                };
                lop_exec {
                      compatible = "system-device-tree-v1,lop,exec-v1";
                      options = "prop:64-bit";
                      exec = <&track_feature>;
                };

# select: select nodes to be used in other lopper operations

    # select is provided to build up complex conditionals or series of nodes,
    # It is similar to the conditional lop (and could replace it in the
    # future). In particular regular expressions which are not valid to dtc
    # can be expressed in select.
    #
    # select operations are idenfied via: compatible = "system-device-tree-v1,lop,select";
    # Optionally a "-v1" can be appended, but currently is not required as only
    # one select format is supported.
    #
    # The syntax of a select test is exactly the same as the modify operation:
    #
    #    <path to node>:<property>:<value>
    #
    #    If <path to node> is omitted, then the nodes from the previous
    #    select operation are used. Hence you can build up a series of
    #    tests to refine search operations.
    #
    # Each select operation is a property of the format:
    #
    #    select[_]<extension> = <select expression>
    #
    # If "select"  with no extension is encountered, it clears any
    # previously selected nodes.
    #
    # Operations are processed in the order they are found in the lop node.
    #
    # Once selected, other lopper operations will use the nodes if no
    # override is supplied in their lop.
    #
    #    - code, exec: The selected node is the default node context of the block
    #                  And all selected nodes are available in the tree variable
    #                  __selected__
    #    - modify: If no node regex is supplied, the selected nodes are used
    #    - output: If no nodes are specified, the selected nodes are used
    #
    #
    # An example of an "or" condition (meaning both sets of matching nodes
    # will be selected).
    #
    #    select_1 = "/path/or/regex/to/nodes:prop:val";
    #    select_2 = "/path/or/2nd/node/regex:prop2:val2";
    #
    # to do an "and" condition (meaning only nodes that match both conditions
    # will be selected).
    #
    #    select_1 = "/path/or/regex/to/nodes:prop:val";
    #    select_2 = ":prop2:val2";
    #
                lop_17_1 {
                      compatible = "system-device-tree-v1,lop,select-v1";
                      // clear any old selections
                      select_1;
                      select_2 = "/cpus/.*:compatible:.*arm,cortex-a72.*";
                      // if the path specifier isn't used, we operate on previously selected nodes.
                      select_3 = ":cpu-idle-states:3";
                };
                lop_17_2 {
                      compatible = "system-device-tree-v1,lop,modify";
                      // this will use the selected nodes above, to add a property
                      modify = ":testprop:testvalue";
                };
                lop_17_3 {
                      compatible = "system-device-tree-v1,lop,code-v1";
                      code = "
                          print( 'node: %s' % node )
                          for s in tree.__selected__:
                              print( 'selected: %s' % s.abs_path )
                              print( '    testprop: %s' % s['testprop'].value[0] )
                      ";
                };
                lop_17_4 {
                      compatible = "system-device-tree-v1,lop,select-v1";
                      // clear any old selections
                      select_1;
                      select_2 = "/cpus/.*:compatible:.*arm,cortex-a72.*";
                      // since there's a path specifier, this is an OR operation
                      select_3 = "/axi/.*:phy-handle:0x9";
                };
                lop_17_5 {
                      compatible = "system-device-tree-v1,lop,code-v1";
                      code = "
                          print( 'node2: %s' % node )
                          for s in tree.__selected__:
                              print( 'selected2: %s' % s.abs_path )
                      ";
                };

# tree: create a subtree from specified nodes

    # To allow for nodes to not only be collected for output, but also for
    # modification, we have the "tree" lop.
    #
    # This lop follows the same syntax as "output". If no nodes are specified
    # in the lop itself, previously selected ones via "select" are used
    #
    # The lop must provide the name of the newly created tree via the "tree"
    # property. A new tree is created and stored in the system device tree
    # in the "subtrees" dictionary.
    #
    # lops that support specifying the tree, can then modify the named tree
    # instead of the default system device tree (which they do via an optional
    # "tree" property in their lops.
    #

                lop_13_1 {
                       compatible = "system-device-tree-v1,lop,tree";
                       tree = "openamp-test";
                       nodes = "reserved-memory", "zynqmp-rpu", "zynqmp_ipi1";
                };
                lop_13_2 {
                       compatible = "system-device-tree-v1,lop,modify";
                       tree = "openamp-test";
                       modify = "/reserved-memory:#size-cells:3";
                };
                lop_13_2_1 {
                       compatible = "system-device-tree-v1,lop,modify";
                       tree = "openamp-test";
                       modify = "/reserved-memory::/zynqmp-rpu/reserved-memory";
                };
                lop_13_3 {
                       compatible = "system-device-tree-v1,lop,output";
                       tree = "openamp-test";
                       outfile = "openamp-test-special.dts";
                       nodes = "reserved-memory", "zynqmp-rpu", "zynqmp_ipi1";
                };

# xlate: translate a node / properties

    # It is becoming more common that non dts compatible trees / properties
    # are carried along side of device tree ones (i.e. yaml), and those
    # properties often need to be translated / expanded to be device tree
    # format (since they are complex types).
    #
    # To make that easier, we have a translate "xlate" lopper operation.
    # This lop is expected to work in coordination with a select lop to
    # target specific nodes and properties that need translation. xlate is
    # very similar to "code", and in fact, you can do everything that xlate
    #  does with a code block (just more verbosely).
    #
    #    The differences (conveniences) from "code" are as follows:
    #
    #      - Automatic inheritance of lopper library functions (as: lopper_lib)
    #      - Automatic iteration over selected nodes
    #
    # See the description of the "code" lop for an explanation of 'inherit'

                    lop_0_1 {
                          compatible = "system-device-tree-v1,lop,select-v1";
                          select_1;
                          select_2 = "/domains/subsystem.*:cpus:.*";
                    };
                    lop_0_2 {
                            compatible = "system-device-tree-v1,lop,xlate-v1";
                            inherit = "subsystem";
                            code = "
                                 subsystem.cpu_expand( tree, node )
                                 subsystem.memory_expand( tree, node )
                                 subsystem.access_expand( tree, node )
                            ";
                    };

    # The impact of these two lops would be to run the code block in 0_2
    # against all nodes that match the selection criteria of 0_1. In this
    # case, it would be against any nodes under /domains/subsystem that have
    #  a 'cpus' property (that is set to anything).
    #
    # When the code block runs, the python module "subsystem" will be loaded
    # from the assists subdirectory and made available to the code block.
    # The calls to subsystem.<function> will leverage the transforms available
    # in that assist (and in this case, will expand various properties in
    # in the node).


# meta-v1, phandle-desc-v1

    # lopper performs phandle validation and phandle replacement during dts /
    # dtb output handling. To do these lookups, it must understand the names and
    # layout of properites that contain phandles.

    # Internally lopper has the following default phandle understanding:

        "DEFAULT" : [ 'this is the default provided phandle map' ],
        "address-map" : [ '#ranges-address-cells phandle #ranges-address-cells #ranges-size-cells', 0 ],
        "secure-address-map" : [ '#address-cells phandle #address-cells #size-cells', 0 ],
        "interrupt-parent" : [ 'phandle', 0 ],
        "iommus" : [ 'phandle field' ],
        "interrupt-map" : [ '#interrupt-cells phandle #interrupt-cells' ],
        "access" : [ 'phandle' ],
        "cpus" : [ 'phandle mask mode' ],
        "clocks" : [ 'phandle:#clock-cells' ],

    # the format of the entries is rudimentary, and will be enhanced with
    # bindings in future revisions. The important information in an entry
    # is the position of the phandle, non-phandle fields can be given any
    # name, and are simply placeholders for counting.
    #
    # lopper also supports dynamically sized properies when locating
    # a phandle for replacement/lookup. The property that varies in size
    # is prefixed with '#', followed by the property name. Lopper will lookup
    # that property in the device tree and expand the field count
    # appropriately.
    #
    # A phandle description is additive. Existing entries do not need
    # to be repeated, and existing values can be overwritten by putting
    # them in the lop.


                lop_0_0 {
                      compatible = "system-device-tree-v1,lop,meta-v1","phandle-desc-v1";
                      interrupt-map = "#interrupt-cells phandle #interrupt-cells";
                      access = "flags phandle flags";
                      mynewproperty = "phandle field field";
                };

# conditional execution or inhibiting of a lop execution

There are two properties that can be used to control the execution of a
lop.

  - "noexec"
  - "cond"

"noexec" takes no values and when present, the lop will not be exected:

	    lop_2 {
                  compatible = "system-device-tree-v1,lop,select-v1";
		  // do not run this lop
		  noexec;
                  // clear any old selections
                  select_1;
                  select_2 = "/:compatible:.*xlnx,zynq-zc702.*";
            };

"cond" specifies a target lop phandle. If the result of that lop
is "True", then the specifying lop will be executed. If the result
of the target lop is False, then the lop will not be exected. This
is typically used to trigger execution of lops based on selected
nodes, or a property found in a tree.

In the following example, either lop_1 or lop_1_1 are valid targets
of the "cond" property of lop_1_1_1. The result would be the same if
either was specified (and both are provided to show a long and short
form of the operation). If a matching compatible node is found in the
tree, then lop_1_1_1 will be executed.

	    lop_1: lop_1 {
		  compatible = "system-device-tree-v1,lop,select-v1";
		  // clear any old selections
		  select_1;
		  select_2 = "/:compatible:.*xlnx,versal-vc-p-a2197-00-revA.*";
	    };
	    lop_1_1: lop_1_1 {
		  compatible = "system-device-tree-v1,lop,code-v1";
		  code = "
			  if __selected__:
			      print( 'Compatible dts (type1) found: %s' % node )

			  if __selected__:
			      return True
			  else:
			      return False
		      ";
	    };
	    lop_1_1_1 {
		  compatible = "system-device-tree-v1,lop,code-v1";
		  cond = <&lop_1>;
		  code = "
			 print( 'Conditional Code is running!' )
			 ";
	     };



Note: the lopper_sanity.py utility has an embedded lops file that can be
used as a reference, as well as embedded LopperTree sanity tests.

# Lopper Assists

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

Lopper will call that function when processing a lopper assist operation for a
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

   def assist_routine( target_node, sdt, options )

If compatible, lopper calls the assist routine with the arguments set to:

  - the target node number
  - the lopper system device tree object
  - the assist options dictionary

The options dictionary minimally contains the following keys:

   'verbose': indicates the level of verbosity that lopper is using. 
   'outdir': indicates the lopper output directory (from a lop or command line)
   'args': any remaining assist specific arguments (from a lop or command line)

Once called, the function can use the node number and the system device tree
(with its embedded FDT) to discover more information about the tree and
manipulate the tree itself (and hence used by future lopper operations or assist
calls). Lopper utility routines, SDT object calls or LopperTree / LopperNode /
LopperProp routines can be used to modify the tree.

If the module has invalid code, or otherwise generates and exception, Lopper
catches it and reports the error to the user.

# Command line assists:

Commonly we want to run an assist against the loaded system device tree and exit.

That assist doesn't need to be run in order with other lopper operations or is
the only action to be taken.

Lopper can find an assist/module on the command line, and to pass arguments to
that assist (or any assist). Assists will be passed the id: "module,<their
name>" and must return True when their is_compat() function is called with that
id, or they will not be executed.

An example is a simple "grep" assist:

     % lopper.py device-trees/system-device-tree.dts -- grep compatible "/bus.*"

Everything after the "--" is of the format: <module> <arguments>

In this case, the grep.py assist is located, loaded and passed the system device
tree. It can then process the arguments as it sees fit:

     % lopper.py device-trees/system-device-tree.dts -- grep compatible "/bus.*"
       /bus@f1000000/spi@ff040000: compatible = "cdns,spi-r1p6";
       /bus@f1000000/pci@fca10000: compatible = "xlnx,versal-cpm-host-1.00";
       /bus@f1000000/dma@ffac0000: compatible = "xlnx,zynqmp-dma-1.0";
       /bus@f1000000/serial@ff010000: compatible = "arm,pl011","arm,sbsa-uart";
       /bus@f1000000/spi@f1030000: compatible = "xlnx,versal-qspi-1.0";
       /bus@f1000000/zynqmp_ipi: compatible = "xlnx,zynqmp-ipi-mailbox";
       /bus@f1000000/cci@fd000000/pmu@10000: compatible = "arm,cci-500-pmu,r0";
       /bus@f1000000/dma@ffae0000: compatible = "xlnx,zynqmp-dma-1.0";
       /bus@f1000000/dma@ffa90000: compatible = "xlnx,zynqmp-dma-1.0";

# output assists:

Output assists are similar to standard (node) assists, except they are called
when an output file extension is not recognized. Each loaded assist is queried
with either an id that was provided when the output assist lop was loaded (see
example above), or one that is associated with an output node in a lop file.

If compatible, and output assist should return a function of the following
format:

   def assist_write( node, lt, options ):

Note: A LopperTree is passed to the output assist, and not a system device
tree, since changes to the core SDT should not be made by an output
assist.

The routine can write the appropriate parts of the passed LopperTreePrinter (lt
above) to the passed output filename.

The output filename is passed via the options dictionary, in the key 'outfile'

# execution samples:

    # testing with openamp domains
    #
    # Notes: -v -v: verbosity level 2
    #        -f: force overwrite files if they exist
    #        -i: module load lop
    #        -i: main lop file for modifying system device tree (with a unified chosen node in this example)
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
