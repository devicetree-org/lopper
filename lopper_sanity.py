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
import os
import re
import shutil
from pathlib import Path
from pathlib import PurePath
import tempfile
from enum import Enum
import textwrap
from collections import UserDict
from collections import OrderedDict
import copy

import libfdt
from libfdt import Fdt, FdtException, QUIET_NOTFOUND, QUIET_ALL

from lopper_tree import *
from lopper import *
from lopper_fdt import *

def setup_lops( outdir ):
    with open( outdir + "/lops.dts", "w") as w:
            w.write("""\
/*
 * Copyright (c) 2019,2020 Xilinx Inc. All rights reserved.
 *
 * Author:
 *       Bruce Ashfield <bruce.ashfield@xilinx.com>
 *
 * SPDX-License-Identifier: BSD-3-Clause
 */


/dts-v1/;

/ {
        compatible = "system-device-tree-v1";
        lops {
                // compatible = "system-device-tree-v1,lop";
                lop_0 {
                        compatible = "system-device-tree-v1,lop,assist-v1";
                        node = "/chosen/openamp_r5";
                        id = "openamp,domain-v1";
                        noexec;
                };
                lop_1 {
                        // node name modify
                        compatible = "system-device-tree-v1,lop,modify";
                        modify = "/cpus/::/cpus_a72/";
                };
                lop_2 {
                        compatible = "system-device-tree-v1,lop,modify";
                        // format is: "path":"property":"replacement"
                        //    - modify to "nothing", is a remove operation
                        //    - modify with no property is node operation (rename or remove)
                        modify = "/cpus/:access:";
                };
                lop_3 {
                        // modify to "nothing", is a remove operation
                        compatible = "system-device-tree-v1,lop,modify";
                        modify = "/cpus:no-access:";
                };
                lop_4 {
                        // node delete
                        compatible = "system-device-tree-v1,lop,modify";
                        // commented out. test purposes
                        // modify = "/amba/::";
                };
                lop_5 {
                        // node name modify
                        compatible = "system-device-tree-v1,lop,modify";
                        // commented out. test purposes
                        // modify = "/amba/::";
                };
                lop_6 {
                        compatible = "system-device-tree-v1,lop,modify";
                        modify = "/chosen::";
                        noexec;
                };
                lop_7 {
                        // node add
                        compatible = "system-device-tree-v1,lop,add";
                        node_src = "zynqmp-rpu";
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
                  lop_9 {
                          // temp temp temp, can we modify a recently added node ??
                          compatible = "system-device-tree-v1,lop,modify-do-not-use";
                          modify = "/zynqmp-rpu::";
                  };
                  lop_10 {
                          // optionally execute a routine in a loaded module. If the routine
                          // isn't found, this is NOT a failure. Since we don't want to tightly
                          // couple these transforms and loaded modules
                          compatible = "system-device-tree-v1,lop,assist-v1";
                          // TODO: put a node compatible string here
                          // module = "openamp,xlnx-rpu";
                          id = "openamp,xlnx-rpu";
                          node = "/chosen/openamp_r5";
                          // assist = "xlnx_openamp_rpu";
                  };
                  lop_11 {
                        // property value modify
                        compatible = "system-device-tree-v1,lop,modify";
                        // disabled for now: will be put in a test transforms .dts file
                        // modify = "/:model:this is a test";
                 };
                 lop_12 {
                        // test: property add
                        // example: add a special ID into various nodes
                        compatible = "system-device-tree-v1,lop,modify";
                        // disabled for now: will be put in a test transforms .dts file
                        modify = "/:pnode-id:0x7";
                 };
                 lop_13 {
                        compatible = "system-device-tree-v1,lop,output";
                        outfile = "openamp-test.dtb";
                        nodes = "reserved-memory", "zynqmp-rpu", "zynqmp_ipi1";
                 };
                 lop_14 {
                        compatible = "system-device-tree-v1,lop,output";
                        outfile = "linux.dtb";
                        nodes = "*";
                 };
        };
};
            """)

    return "/tmp/lops.dts"

def setup_device_tree( outdir ):
    with open( outdir + "/tester.dts", "w") as w:
            w.write("""\
/*
 * Copyright (c) 2019,2020 Xilinx Inc. All rights reserved.
 *
 * Author:
 *       Bruce Ashfield <bruce.ashfield@xilinx.com>
 *
 * SPDX-License-Identifier: BSD-3-Clause
 */

/dts-v1/;

/ {
        compatible = "xlnx,versal-vc-p-a2197-00-revA", "xlnx,versal-vc-p-a2197-00", "xlnx,versal-vc-p-a2197", "xlnx,versal";
        #address-cells = <0x2>;
        #size-cells = <0x2>;
        model = "Xilinx Versal A2197 Processor board revA";

        /* test comment */
        cpus: cpus {
                #address-cells = <0x1>;
                #size-cells = <0x0>;
                #cpus-mask-cells = <0x1>;
                compatible = "cpus,cluster";

                cpu@0 {
                        compatible = "arm,cortex-a72", "arm,armv8";
                        device_type = "cpu";
                        enable-method = "psci";
                        operating-points-v2 = <0x1>;
                        reg = <0x0>;
                        cpu-idle-states = <0x2>;
                        clocks = <0x3 0x4d>;
                };

                cpu@1 {
                        compatible = "arm,cortex-a72", "arm,armv8";
                        device_type = "cpu";
                        enable-method = "psci";
                        operating-points-v2 = <0x1>;
                        reg = <0x1>;
                        cpu-idle-states = <0x2>;
                };

                idle-states {
                        entry-method = "psci";

                        cpu-sleep-0 {
                                compatible = "arm,idle-state";
                                arm,psci-suspend-param = <0x40000000>;
                                local-timer-stop;
                                entry-latency-us = <0x12c>;
                                exit-latency-us = <0x258>;
                                min-residency-us = <0x2710>;
                                phandle = <0x2>;
                        };
                };
        };

        amba: amba {
                compatible = "simple-bus";
                #address-cells = <0x2>;
                #size-cells = <0x2>;
                phandle = <0xbeef>;
                ranges;

                /* Proxy Interrupt Controller */
                imux: interrupt-multiplex {
                        compatible = "interrupt-multiplex";
                        #address-cells = <0x0>;
                        #interrupt-cells = <3>;
                        /* copy all attributes from child to parent */
                        interrupt-map-pass-thru = <0xffffffff 0xffffffff 0xffffffff>;
                        /* mask all child bits to always match the first 0x0 entries */
                        interrupt-map-mask = <0x0 0x0 0x0>;
                        /* 1:1 mapping of all interrupts to gic_a72 and gic_r5 */
                        /* child address cells, child interrupt cells, parent, parent interrupt cells */
                        interrupt-map = <0x0 0x0 0x0 &gic_a72 0x0 0x0 0x0>,
                                        <0x0 0x0 0x0 &gic_r5 0x0 0x0 0x0>;
                };

        };

        amba_apu: amba_apu {
                compatible = "simple-bus";
                #address-cells = <0x2>;
                #size-cells = <0x2>;
                ranges;

                gic_a72: interrupt-controller@f9000000 {
                        compatible = "arm,gic-v3";
                        #interrupt-cells = <0x3>;
                        #address-cells = <0x2>;
                        #size-cells = <0x2>;
                        ranges;
                        reg = <0x0 0xf9000000 0x0 0x80000 0x0 0xf9080000 0x0 0x80000>;
                        interrupt-controller;
                        interrupt-parent = <&gic_a72>;
                        interrupts = <0x1 0x9 0x4>;
                        num_cpus = <0x2>;
                        num_interrupts = <0x60>;
                        phandle = <0x5>;

                        gic-its@f9020000 {
                                compatible = "arm,gic-v3-its";
                                msi-controller;
                                msi-cells = <0x1>;
                                reg = <0x0 0xf9020000 0x0 0x20000>;
                                phandle = <0x1b>;
                        };
                };

                iommu: smmu@fd800000 {
                    compatible = "arm,mmu-500";
                    status = "okay";
                    reg = <0x0 0xfd800000 0x0 0x40000>;
                    stream-match-mask = <0x7c00>;
                    #iommu-cells = <0x1>;
                    #global-interrupts = <0x1>;
                    interrupt-parent = <&gic_a72>;
                    interrupts = <0x0 0x6b 0x4 0x0 0x6b 0x4 0x0 0x6b 0x4 0x0 0x6b 0x4 0x0 0x6b 0x4 0x0 0x6b 0x4 0x0 0x6b 0x4 0x0 0x6b 0x4 0x0 0x6b 0x4 0x0 0x6b 0x4 0x0 0x6b 0x4 0x0 0x6b 0x4 0x0 0x6b 0x4 0x0 0x6b 0x4 0x0 0x6b 0x4 0x0 0x6b 0x4 0x0 0x6b 0x4 0x0 0x6b 0x4 0x0 0x6b 0x4 0x0 0x6b 0x4 0x0 0x6b 0x4 0x0 0x6b 0x4 0x0 0x6b 0x4 0x0 0x6b 0x4 0x0 0x6b 0x4 0x0 0x6b 0x4 0x0 0x6b 0x4 0x0 0x6b 0x4 0x0 0x6b 0x4 0x0 0x6b 0x4 0x0 0x6b 0x4 0x0 0x6b 0x4 0x0 0x6b 0x4>;
                };

                timer {
                     compatible = "arm,armv8-timer";
                     interrupt-parent = <&gic_a72>;
                     interrupts = <0x1 0xd 0x4 0x1 0xe 0x4 0x1 0xb 0x4 0x1 0xa 0x4>;
                };
        };


        chosen {
                #address-cells = <0x2>;
                #size-cells = <0x2>;

                openamp_r5 {
                        compatible = "openamp,domain-v1";
                        #address-cells = <0x2>;
                        #size-cells = <0x2>;
                        /*
                         * 1:1 map, it should match the memory regions
                         * specified under access below.
                         *
                         * It is in the form:
                         * memory = <address size address size ...>
                         */
                        memory = <0x0 0x0 0x0 0x8000000>;
                        /*
                         * cpus specifies on which CPUs this domain runs
                         * on
                         *
                         * link to cluster | cpus-mask | execution-mode
                         *
                         * execution mode for ARM-R CPUs:
                         * bit 30: lockstep (lockstep enabled == 1)
                         * bit 31: secure mode / normal mode (secure mode == 1)
                         */
                        /* cpus = <&cpus_r5 0x2 0x80000000>, <&cpus 0x3 0x80000000>; */
                        cpus = <&cpus_r5 0x2 0x80000000>;
                        /*
                         * Access specifies which resources this domain
                         * has access to.
                         *
                         * Link to resource | flags
                         *
                         * The "flags" field is mapping specific
                         *
                         * For memory, reserved-memory, and sram:
                         *   bit 0: 0/1: RO/RW
                         *
                         * Other cases: unused
                         *
                         * In this example we are assigning:
                         * - memory range 0x0-0x8000000 RW
                         * - tcm RW
                         * - ethernet card at 0xff0c0000
                         */
                        access = <&memory_r5 0x1>, <&tcm 0x1>, <&ethernet0 0x0>;
                        /*access = <&tcm 0x1>;*/
                };
        };

        memory: memory@00000000 {
                device_type = "memory";
                reg = <0x0 0x0 0x0 0x80000000>;
        };
};
""")

    return "/tmp/tester.dts"

def setup_fdt( device_tree, outdir ):
    Lopper.dt_compile( device_tree, "", "", True, outdir )
    return libfdt.Fdt(open( device_tree + ".dtb", mode='rb').read())


def tree_sanity_test( fdt ):
    # test1: simple tree walking routine
    print( "[TEST]: start: tree walk" )
    walker = LopperTree( fdt )
    for n in walker:
        print( "\nnode: %s:%s [%s] parent: %s children: %s depth: %s" % (n.name, n.number,
                                                                         hex(n.phandle), n.parent,
                                                                         n.children, n.depth))
        for prop in n:
            print( "    property: %s %s" % (prop.name, prop.value))
            print( "    raw: %s" % (n[prop.name]) )
    print( "[TEST]: end: tree walk\n" )

    # test2: tree print
    print( "[TEST]: start: tree print" )
    printer = LopperTreePrinter( fdt, True )
    printer.exec()
    print( "[TEST]: end: tree print\n" )

    # test3: node manipulations/access
    print( "[TEST]: start: node manipulations" )
    n = printer['/amba']
    print( "    node access via '/amba' found: %s, %s" % (n, [n]) )
    n2 = printer[n.number]
    print( "    node access by number '%s' found: %s" % (n.number,[n2]) )
    # write it back, as a test only
    printer['/amba'] = n
    print( "    node reassignment by name .... worked" )

    pp = n['compatible']
    print( "    property access (compatible): name: %s value: %s string: %s" % (pp.name, pp.value, pp) )

    # phandle tests
    n = printer.pnode( n.phandle )
    print( "    node access via phandle %s: %s" % (hex(n.phandle), n) )
    print( "[TEST]: end: node manipulations\n" )


    # iteration tests
    print( "[TEST]: start: custom node lists" )
    printer.__new_iteration__ = True
    printer.__current_node__ = "/amba_apu"
    printer.exec()
    printer.resolve()

    print( "\n[SUB TEST]: full node walk after custom node list" )
    for p in printer:
        print( "        node: %s" % p )
    print( "[SUB TEST]: end full node walk after custom node list\n" )

    print( "[SUB TEST]: subtree walk" )
    # this should only walk the amba sub-tree
    printer.__current_node__ = "/amba"
    for p in printer:
        print( "    /amba restricted test: node: %s" % p )
    print( "[SUB TEST]: end subtree walk\n" )

    print( "[SUB TEST]: start node -> end of tree walk" )
    printer.reset()
    # the difference here, is that this starts at amba and walks ALL the way to
    # the end of the tree.
    printer.__start_node__ = "/amba"
    for p in printer:
        print( "       starting node test: node: %s" % p )
    print( "[SUB TEST]: start node -> end of tree walk\n" )

    # debug level bump up.
    printer.__dbg__ = 3

    # subnode routine tests
    print( "[TEST]: start: subnode calls (both should be the same)" )
    kiddies = printer.subnodes( printer.__nodes__['/amba'] )
    print( "amba subnodes: %s" % kiddies )
    for k in kiddies:
        print( "    node: %s" % k.abs_path )

    kiddies2 = printer['/amba'].subnodes()
    print( "abma subnodes type 2: %s" % kiddies2 )
    for k in kiddies2:
        print( "    node2: %s" % k.abs_path )

    kiddies = printer.subnodes( printer['/'] )
    print( "/ subnodes: %s" % kiddies )
    for k in kiddies:
        print( "    node: %s" % k.abs_path )

    kiddies = printer.subnodes( printer['/'], ".*amba.*" )
    print( "/ subnodes matching regex '.*amba.*': %s" % kiddies )
    for k in kiddies:
        print( "    node: %s" % k.abs_path )

    print( "[TEST]: end: subnode calls\n" )

    print( "[TEST]: start: reference tracking tests" )
    all_refs = printer['/amba/interrupt-multiplex'].resolve_all_refs( printer.fdt )
    for a in all_refs:
        print( "/amba/interrupt-multiplex ref: %s" % a.abs_path )
    print( "[TEST]: end: reference tracking tests" )

    print( "[TEST]: start: node access tests and __str__ routine" )
    printer.__dbg__ = 0
    print( "amba node: %s" % printer.__nodes__['/amba'] )
    print( "amba node number: %s " % int(printer.__nodes__['/amba']))
    printer.__dbg__ = 3
    print( "amba node raw: %s" % printer.__nodes__['/amba'] )
    print( "type: %s" % type(printer.__nodes__['/amba']) )
    print( "[TEST]: end: node access tests and __str__ routine\n" )

    print( "[TEST]: start: node comparison tests" )
    print( "Comparing '/amba' and '/amba_apu'" )
    if printer.__nodes__['/amba'] == printer.__nodes__['/amba_apu']:
        print( "    they are equals" )
    else:
        print( "    they are not equal" )
    print( "[TEST]: start: node comparison tests" )

    print( "[TEST]: start: node regex find test" )
    print( "searching for /amba/.*" )
    matches = printer.nodes( "/amba/.*" )
    for m in matches:
        print( "    match: %s [%s]" % (m.abs_path, m) )
    print( "searching for /amba.*" )
    matches = printer.nodes( "/amba.*" )
    for m in matches:
        print( "    match: %s [%s]" % (m.abs_path, m) )

    print( "exact node match: /amba" )
    matches = printer.nodes( "/amba" )
    for m in matches:
         print( "    match: %s" % m.abs_path )
    amba = matches[0]

    print( "[TEST]: end: node regex find test\n" )


    print( "[TEST]: start: property regex find test" )
    p = amba.props( 'compat.*' )
    p = p[0]
    print( "prop type is: %s" % type(p) )
    print( "amba p0: %s" % p.value[0] )
    print( "amba p1: %s" % p )
    print( "[TEST]: end: property regex find test\n" )

    print( "[TEST]: start: property assign test" )
    p.value = "testing 1.2.3"
    print( "amba p2: %s" % p.value[0] )
    print( "amba p3: %s" % str(p) )
    print( "[TEST]: end: property assign test\n" )

    print( "[TEST]: start: tree manipulation tests" )
    new_node = LopperNode( -1, "/amba/bruce" )
    print( "    new node name: %s" % new_node.name )
    print( "    new node refcount: %s" % new_node.ref )
    new_node.ref = 2
    new_node.ref = 1
    print( "    new node refcount is: %s" % new_node.ref )

    print( "\n" )
    print( "creating new property for new node .... " )
    new_property = LopperProp( "foobar", -1, new_node, [ "testingfoo" ] )

    print( "Property add to node: ")
    # new_node.add( new_property )
    new_node + new_property
    print( "Node add to tree" )
    #printer.add( new_node )
    printer + new_node

    # confirm the node details are the same:
    print( "new_node path: %s" % new_node.abs_path )
    print( "ref count: %s" % printer[new_node].ref )
    print( "[TEST]: end: tree manipulation tests\n" )

    print( "[TEST]: start: tree ref count test" )
    refd = printer.refd()
    print( "referenced nodes: %s" % refd[0].abs_path )

    printer.ref( 0 )
    refd = printer.refd()
    print( "After clear, referenced nodes: %s" % refd )

    print( "[TEST]: end: tree ref count test\n" )

    print( "[TEST]: start: tree re-resolve test" )
    print( "======================= re resolving =======================" )
    printer.resolve()
    print( "======================= resolve done =======================" )

    for n in printer:
        print( "node: %s" % n.abs_path )

    print( "\n" )
    printer.__dbg__ = 0
    printer.__start_node__ = 0
    printer.exec()
    print( "[TEST]: end: tree re-resolve test\n" )

    print( "[TEST]: start: second tree test" )
    print2 = LopperTreePrinter( printer.fdt )
    for n in print2:
        print( "2node: %s" % n )

    print( "\n" )
    print2.exec()
    print( "[TEST]: end: second tree test\n" )

    print( "[TEST]: start: node persistence test" )
    print( "new node's state is now: %s" % new_node.__nstate__ )
    print( "[TEST]: end: node persistence test\n" )

    print( "[TEST]: start: second property test" )
    new_property2 = LopperProp( "number2", -1, new_node, [ "i am 2" ] )

    # type1: can we just add to the node and sync it ?
    new_node + new_property2
    print( "syncing new property" )
    printer.sync()
    print( "end syncing new property" )
    # the above works when nodes are fully reused

    # type2: or can we fetch it out of the tree, assign and sync
    #printer[new_node] + new_property2
    #printer.sync()
    # the above works

    # is a double node add an error ?
    printer + new_node
    printer.sync()
    # end double add

    print( "writing to: tester-output.dts" )
    Lopper.write_fdt( printer.fdt, "tester-output.dts", None, True, 3, True )

    # remove the 2nd property, re-write
    print( "writing to: tester-output2.dts (with one less property" )
    new_node - new_property2
    printer.sync()
    Lopper.write_fdt( printer.fdt, "tester-output2.dts", None, True, 3, True )

    print( "[TEST]: end: second property test\n" )

    print( "[TEST]: start: second tree test, node deep copy" )

    tree2 = LopperTreePrinter( fdt, True )
    # new_node2 = LopperNode()
    # invokes a deep copy on the node
    new_node2 = new_node()

    print( "node2: %s" % new_node2.abs_path )
    #new_node2 = new_node2(new_node)
    print( "node2: %s" % new_node2.abs_path )
    # the property objects, should be different, since these are copies
    print( "node1 props: %s" % new_node.__props__ )
    print( "node2 props: %s" % new_node2.__props__ )

    # not required, but could re-add to make sure they don't harm anything
    # new_node2.resolve( tree2.fdt )
    # new_node2.sync( tree2.fdt )

    tree2.__dbg__ = 3
    tree2 + new_node2

    Lopper.write_fdt( tree2.fdt, "tester-output-tree2.dts", None, True, 3, True )

    print( "[TEST]: end: second tree test, node deep copy\n" )

    print( "[TEST]: start: tree test, node remove" )

    printer = printer - new_node
    #printer.delete( new_node )

    Lopper.write_fdt( printer.fdt, "tester-output-node-removed.dts", None, True, 3, True )
    print( "[TEST]: end: tree test, node remove" )

    sys.exit(1)

def usage():
    prog = os.path.basename(sys.argv[0])
    print('Usage: %s [OPTION] ...' % prog)
    print('  -v, --verbose       enable verbose/debug processing (specify more than once for more verbosity)')
    print('  -t, --tree          run lopper tree tests' )
    print('  -l, --lops          run lop tests' )
    print('    , --werror        treat warnings as errors' )
    print('  -h, --help          display this help and exit')
    print('')

def main():
    global verbose
    global force
    global werror
    global outdir
    global lops
    global tree

    verbose = 0
    force = False
    werror = False
    outdir="/tmp/"
    tree = False
    lops = False
    try:
        opts, args = getopt.getopt(sys.argv[1:], "vtlh", [ "tree", "lops", "werror","verbose", "help"])
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
        elif o in ('-f', "--force"):
            force = True
        elif o in ('-h', '--help'):
            usage()
            sys.exit(0)
        elif o in ('-O', '--outdir'):
            outdir = a
        elif o in ('--werror'):
            werror=True
        elif o in ( '-l','--lops'):
            lops=True
        elif o in ( '-t','--tree'):
            tree=True
        elif o in ('--version'):
            print( "%s" % LOPPER_VERSION )
            sys.exit(0)
        else:
            assert False, "unhandled option"


if __name__ == "__main__":

    main()

    if tree:
        dt = setup_device_tree( outdir )
        fdt = setup_fdt( dt, outdir )
        tree_sanity_test( fdt )

    if lops:
        dt = setup_device_tree( outdir )
        lops = setup_lops( outdir )

        device_tree = LopperSDT( dt )

        device_tree.dryrun = False
        device_tree.verbose = 3
        device_tree.werror = werror
        device_tree.output_file = outdir + "/sdt-output.dts"
        device_tree.cleanup_flag = True
        device_tree.save_temps = False
        device_tree.pretty = True
        device_tree.outdir = outdir

        device_tree.setup( dt, [lops], "", "", True )
        device_tree.perform_lops()
        Lopper.write_fdt( device_tree.FDT, device_tree.output_file, device_tree, True, device_tree.verbose, device_tree.pretty )
        device_tree.cleanup()

