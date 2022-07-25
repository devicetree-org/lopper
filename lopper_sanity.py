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
import os
import re
import shutil
import filecmp
from pathlib import Path
from pathlib import PurePath
import tempfile
from enum import Enum
import textwrap
from collections import UserDict
from collections import OrderedDict
import copy
import getopt

from lopper.tree import *

from lopper import *
import lopper
from lopper.yaml import *

from io import StringIO
import sys

from io import StringIO
import sys

class Capturing(list):
    def __enter__(self):
        self._stdout = sys.stdout
        sys.stdout = self._stringio = StringIO()
        return self
    def __exit__(self, *args):
        self.extend(self._stringio.getvalue().splitlines())
        sys.stdout = self._stdout
    def reset(self):
        del self._stringio
        sys.stdout = self._stdout

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
                        // sanity on modules is not currently implemented
                        compatible = "system-device-tree-v1,lop,assist-v1";
                        node = "/domains/openamp_r5";
                        id = "openamp,domain-v1";
                        noexec;
                };
                lop_1 {
                        // node name modify
                        compatible = "system-device-tree-v1,lop,modify";
                        modify = "/cpus::cpus_a72";
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
                        modify = "/cpus_a72/:no-access:";
                };
                lop_4 {
                        // node delete
                        compatible = "system-device-tree-v1,lop,modify";
                        modify = "/anode_to_delete::";
                };
                lop_5 {
                        // node name modify
                        compatible = "system-device-tree-v1,lop,modify";
                        modify = "/amba/::";
                        noexec;
                };
                lop_6 {
                        compatible = "system-device-tree-v1,lop,modify";
                        modify = "/amba_apu/nested-node::";
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
                                  // mboxes = <&ipi_mailbox_rpu0 0>, <&ipi_mailbox_rpu0 1>;
                                  mboxes = <&__mailbox_ipi__>;
                                  // mbox-names = "tx", "rx";
                                  mbox-names = "__mbox_names__";
                                  tcm_0_a: tcm_0@0 {
                                           reg = <0x0 0xFFE00000 0x0 0x10000>;
                                  };
                                  tcm_0_b: tcm_0@1 {
                                         reg = <0x0 0xFFE20000 0x0 0x10000>;
                                  };
                            };
                        };
                  };
                  lop_9 {
                          compatible = "system-device-tree-v1,lop,modify";
                          modify = "/zynqmp-rpu:mbox-names:lopper-mboxes";
                  };
                  lop_10 {
                          // optionally execute a routine in a loaded module. If the routine
                          // isn't found, this is NOT a failure. Since we don't want to tightly
                          // couple these transforms and loaded modules
                          compatible = "system-device-tree-v1,lop,assist-v1";
                          id = "openamp,xlnx-rpu";
                          node = "/domains/openamp_r5";
                  };
                  lop_11 {
                        // property value modify
                        compatible = "system-device-tree-v1,lop,modify";
                        // disabled for now: will be put in a test transforms .dts file
                        modify = "/:model:this is a test";
                 };
                 lop_11_1 {
                         compatible = "system-device-tree-v1,lop,modify";
                         modify = "/amba/.*ethernet.*phy.*:regexprop:lopper-id-regex";
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
                        outfile = "openamp-test.dts";
                        nodes = "reserved-memory", "zynqmp-rpu", "zynqmp_ipi1";
                 };
                 lop_13_1_1 {
                        compatible = "system-device-tree-v1,lop,output";
                        outfile = "openamp-test.dtb";
                        nodes = "reserved-memory", "zynqmp-rpu", "zynqmp_ipi1";
                 };
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
                        outfile = "openamp-test2.dts";
                        nodes = "reserved-memory", "zynqmp-rpu", "zynqmp_ipi1";
                 };

                 lop_14 {
                        compatible = "system-device-tree-v1,lop,output";
                        outfile = "linux.dts";
                        nodes = "*";
                 };
                 lop_14_1 {
                        compatible = "system-device-tree-v1,lop,output";
                        outfile = "linux-amba.dts";
                        nodes = ".*amba.*";
                 };
                 lop_15_2 {
                       compatible = "system-device-tree-v1,lop,modify";
                       modify = "/cpus_a72/cpu@0:listval:<0xF 0x5>";
                 };
                 lop_15_3 {
                       compatible = "system-device-tree-v1,lop,modify";
                       modify = "/cpus_a72/cpu@0:liststring:'four','five'";
                 };
                 lop_15_4 {
                       compatible = "system-device-tree-v1,lop,modify";
                       modify = "/cpus_a72/cpu@0:singlestring:newcpu";
                 };
                 lop_15_5 {
                       compatible = "system-device-tree-v1,lop,modify";
                       modify = "/cpus_a72/cpu@0:singleval:<5>";
                 };
        };
};
            """)

    return outdir + "/lops.dts"

def setup_code_lops( outdir ):
    with open( outdir + "/lops-code.dts", "w") as w:
            w.write("""\
/*
 * Copyright (c) 2020 Xilinx Inc. All rights reserved.
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
                        // sanity on modules is not currently implemented
                        compatible = "system-device-tree-v1,lop,assist-v1";
                        node = "/domains/openamp_r5";
                        id = "openamp,domain-v1";
                        noexec;
                };
                lop_1 {
                        // node name modify
                        compatible = "system-device-tree-v1,lop,modify";
                        modify = "/cpus::cpus_a72";
                        noexec;
                };
                lop_2 {
                        compatible = "system-device-tree-v1,lop,modify";
                        // format is: "path":"property":"replacement"
                        //    - modify to "nothing", is a remove operation
                        //    - modify with no property is node operation (rename or remove)
                        modify = "/cpus/:access:";
                        noexec;
                };
                lop_15 {
                        compatible = "system-device-tree-v1,lop,code-v1";
                        code = "
                            nodes = tree.nodes('/cpus.*/cpu@.*')
                            for n in nodes:
                                print( 'n: %s (%s)' % (n.abs_path,[n]) )
                                compat = n['compatible'].value
                                print( 'compat: %s' % compat )
                                for c in compat:
                                    if 'cortex-a72' in c:
                                        print( '[INFO]: a72 found, tagging' )
                                        n['tag'] = 'a72'

                            return 1
                        ";
                };
                lop_15_1 {
                      compatible = "system-device-tree-v1,lop,conditional-v1";
                      cond_root = "cpus";
                      cond_select = "/cpus/cpu@.*";
                      cpus {
                           cpu@ {
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
                      cond_select = "/cpus/cpu@.*";
                      cpus {
                           cpu@ {
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
                      cond_select = "/cpus/cpu@.*";
                      cpus {
                           cpu@ {
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
                      cond_select = "/cpus/cpu@.*";
                      cpus {
                           cpu@ {
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
                      cond_select = "/cpus/cpu@.*";
                      cpus {
                           cpu@ {
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
                      cond_select = "/cpus/cpu@.*";
                      cpus {
                           cpu@ {
                               compatible = ".*a72.*";
                               cpu-idle-states__not__ = <0x1 0x2>;
                           };
                      };
                      true {
                           compatible = "system-device-tree-v1,lop,code-v1";
                           code = "
                               print( '[INFO]: not one or two' )
                               return True
                               ";
                      };
                };
                lop_15_8 {
                      compatible = "system-device-tree-v1,lop,conditional-v1";
                      cond_root = "cpus";
                      cond_select = "/cpus/cpu@.*";
                      cpus {
                           cpu@ {
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
                track_feature: track_feature {
                        compatible = "system-device-tree-v1,lop,code-v1";
                        noexec;
                        code = "
                            print( 'track: lopper library routine: %s' % node )
                            try:
                                node.ttunes[prop] = prop
                            except:
                                pass
                        ";
                };
                lop_16_1 {
                      compatible = "system-device-tree-v1,lop,conditional-v1";
                      cond_root = "cpus";
                      cond_select = "/cpus/cpu@.*";
                      cpus {
                           cpu@ {
                               compatible = ".*a72.*";
                               clocks = <0x3 0x4d>;
                           };
                      };
                      true {
                           compatible = "system-device-tree-v1,lop,exec-v1";
                           exec = <&track_feature>;
                      };
                };
                lop_16_2 {
                      compatible = "system-device-tree-v1,lop,print-v1";
                      print = "print_test: print 1";
                      print2 = "print_test: print2";
                      print3 = <0x2>;
                };
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
                      select_3 = "/amba/.*:phy-handle:0x9";
                };
                lop_17_5 {
                      compatible = "system-device-tree-v1,lop,code-v1";
                      code = "
                          print( 'node2: %s' % node )
                          for s in tree.__selected__:
                              print( 'selected2: %s' % s.abs_path )
                      ";
                };
        };
};
            """)

    return "/tmp/lops-code.dts"

def setup_assist_lops( outdir ):
    with open( outdir + "/lops-assists.dts", "w") as w:
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
                lop_0 {
                        compatible = "system-device-tree-v1,lop,assist-v1";
                        node = "/domains/openamp_r5";
                        id = "access-domain,domain-v1";
                };
        };
};
            """)

    return outdir + "/lops-assists.dts"


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

/memreserve/    0x0000000000000000 0x0000000000001000;
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
                serial0: serial@ff000000 {
                        compatible = "arm,pl011", "arm,sbsa-uart";
                        status = "okay";
                        reg = <0x0 0xff000000 0x0 0x1000>;
                        interrupts = <0x0 0x12 0x4>;
                        clock-names = "uart_clk", "apb_clk";
                        current-speed = <0x1c200>;
                        clocks = <0x3 0x5c 0x3 0x52>;
                        power-domains = <0x7 0x18224021>;
                        cts-override;
                        device_type = "serial";
                        port-number = <0x0>;
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

                gic_r5: interrupt-controller@f9f00000 {
                        compatible = "arm,gic-v3";
                        #interrupt-cells = <0x3>;
                        #address-cells = <0x2>;
                        #size-cells = <0x2>;
                        ranges;
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


        domains {
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
                        cpus = <&cpus 0x2 0x80000000>;
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

        memory_r5: memory@00000000 {
                device_type = "memory";
                reg = <0x0 0x0 0x0 0x80000000>;
        };
        tcm: tcm {
                device_type = "memory";
        };
        ethernet0: ethernet0 {
                device_type = "eth";
        };
        aliases {
                serial0 = "/amba/serial@ff000000";
                ethernet0 = "/ethernet0";
                imux = "/ethernet0";
        };
};
""")

    return outdir + "/tester.dts"

def setup_system_device_tree( outdir ):
    with open( outdir + "/sdt-tester.dts", "w") as w:
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
                no-access = <0x1>;

                cpu@0 {
                        compatible = "arm,cortex-a72", "arm,armv8";
                        device_type = "cpu";
                        enable-method = "psci";
                        operating-points-v2 = <0x1>;
                        reg = <0x0>;
                        cpu-idle-states = <0x2>;
                        clocks = <0x3 0x4d>;
                        listval = <0x1 0x3>;
                        liststring = "one", "three";
                        singlestring = "test";
                        singleval = <0x4>;
                };

                cpu@1 {
                        compatible = "arm,cortex-a72", "arm,armv8";
                        device_type = "cpu";
                        enable-method = "psci";
                        operating-points-v2 = <0x1>;
                        reg = <0x1>;
                        cpu-idle-states = <0x1>;
                };

                cpu@2 {
                        compatible = "arm,cortex-a72", "arm,armv8";
                        device_type = "cpu";
                        enable-method = "psci";
                        operating-points-v2 = <0x2>;
                        reg = <0x1>;
                        cpu-idle-states = <0x3>;
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

        amba_foo: amba {
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

                gic_r5: interrupt-controller@f9f00000 {
                        compatible = "arm,gic-v3";
                        #interrupt-cells = <0x3>;
                        #address-cells = <0x2>;
                        #size-cells = <0x2>;
                        ranges;
                };

                ethernet0: ethernet@ff0c0000 {
                        compatible = "cdns,versal-gem";
                        status = "okay";
                        reg = <0x0 0xff0c0000 0x0 0x1000>;
                        interrupts = <0x0 0x38 0x4 0x0 0x38 0x4>;
                        clock-names = "pclk", "hclk", "tx_clk", "rx_clk", "tsu_clk";
                        #stream-id-cells = <0x1>;
                        #address-cells = <0x1>;
                        #size-cells = <0x0>;
                        iommus = <&iommu 0x234>;
                        phy-handle = <0x9>;
                        phy-mode = "rgmii-id";
                        clocks = <0x3 0x52 0x3 0x58 0x3 0x31 0x3 0x30 0x3 0x2b>;
                        power-domains = <0x7 0x18224019>;
                        phandle = <0xb>;

                        phy@1 {
                                reg = <0x1>;
                                ti,rx-internal-delay = <0xb>;
                                ti,tx-internal-delay = <0xa>;
                                ti,fifo-depth = <0x1>;
                                ti,dp83867-rxctrl-strap-quirk;
                                phandle = <0x9>;
                        };

                        phy@2 {
                                reg = <0x2>;
                                ti,rx-internal-delay = <0xb>;
                                ti,tx-internal-delay = <0xa>;
                                ti,fifo-depth = <0x1>;
                                ti,dp83867-rxctrl-strap-quirk;
                                phandle = <0xa>;
                        };
                };

                ethernet@ff0d0000 {
                        compatible = "cdns,versal-gem";
                        status = "okay";
                        reg = <0x0 0xff0d0000 0x0 0x1000>;
                        interrupts = <0x0 0x3a 0x4 0x0 0x3a 0x4>;
                        clock-names = "pclk", "hclk", "tx_clk", "rx_clk", "tsu_clk";
                        #stream-id-cells = <0x1>;
                        #address-cells = <0x1>;
                        #size-cells = <0x0>;
                        iommus = <&iommu 0x235>;
                        phy-handle = <0xa>;
                        phy-mode = "rgmii-id";
                        clocks = <0x3 0x52 0x3 0x59 0x3 0x33 0x3 0x32 0x3 0x2b>;
                        power-domains = <0x7 0x1822401a>;
                        phandle = <0xc>;
                };
        };

        anode_to_delete {
            compatible = "i should be deleted";
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

                nested-node {
                    compatible = "delete-me";
                    nested-node-child1 {
                         compatible = "delete-me2";
                    };
                };
        };

        reserved-memory {
                #address-cells = <0x2>;
                #size-cells = <0x2>;
                ranges;

                /* For compatibility with default the cpus cluster */
                memory_r5@0 {
                        compatible = "openamp,domain-memory-v1";
                        reg = <0x0 0x0 0x0 0x8000000>;
                };
        };

        domains {
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
                        cpus = <&cpus 0x3 0x80000000>;
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

        memory_r5: memory@00000000 {
                device_type = "memory";
                reg = <0x0 0x0 0x0 0x80000000>;
        };
        tcm: tcm {
                device_type = "memory";
        };
};
""")

    return outdir + "/sdt-tester.dts"

def setup_format_tree( outdir ):
    with open( outdir + "/format-tester.dts", "w") as w:
            w.write("""\
/dts-v1/;

/ {
        compatible = "xlnx,versal-vc-p-a2197-00-revA","lnx,versal-vc-p-a2197-00\0xlnx,versal-vc-p-a2197\0xlnx,versal";
        #address-cells = <0x02>;
        #size-cells = <0x02>;
        model = "Xilinx Versal A2197 Processor board revA";

        cpus {

                cpu@0 {
                        compatible = "arm,cortex-a72","arm,armv8";
                        compatibleshort = [20 00];
                        device_type = "cpu";
                        operating-points-v2 = <0x01>;
                        num-of-clocks;
                        clocks = <0x03 0x4d>;
                        width = [20];
                        width-2 = [FF];
                        mac = [ 01 02 03 04 05 06 07 08];
                        mac-2 = <0x00 0x01>;
                        mac-3 = <0x10203 0x4050607>;
                        queues = [00 01];
                        queues-2 = <0x00 0x01>;
                };
        };
        channel0vdev0buffer: channel0vdev0buffer@3ed48000 {
            no-map;
            #address-cells = <2>;
            #size-cells = <2>;
            reg = <0x3ed48000 0x100000>;
            reg2 = <0x3ed48 0x100000>;
            compatible = "openamp,xlnx,mem-carveout";
        };
};
""")

    return outdir + "/format-tester.dts"

def setup_yaml( outdir ):
    with open( outdir + "/yaml-tester.yaml", "w") as w:
            w.write("""\
compatible: [ "ti,am335x-bone-black", "ti,am335x-bone", "ti,am33xx" ]
interrupt-parent: # *intr
"#address-cells": 1
"#size-cells": 1
model: "TI AM335x BeagleBone Black"
interrupt-controller: &intr
  compatible: "ti,am33xx-intc"
  interrupt-controller: true
  "#interrupt-cells": 1
  reg: [ 0x48200000, 0x00001000 ]
chosen:
  stdout-path: "/ocp/serial@44e09000"
ocp:
  compatible: "simple-bus"
  "#address-cells": 0x00000001
  "#size-cells": 0x00000001
  ranges: true
  ti,hwmods: "l3_main"
  gpio@481ac000: &gpio
    compatible: "ti,omap4-gpio"
    ti,hwmods: "gpio3"
    gpio-controller: true
    "#gpio-cells": 0x00000002
    interrupt-controller: true
    "#interrupt-cells": 0x00000002
    reg: [ 0x481ac000,  0x00001000 ]
    interrupts: 0x00000020
  TIMER4:
    gpio: [ *gpio, 0x00000002 ]
""")

    return outdir + "/yaml-tester.yaml"


def setup_fdt( device_tree, outdir ):
    dt = Lopper.dt_compile( device_tree, "", "", True, outdir )

    if libfdt:
        fdt = Lopper.dt_to_fdt( device_tree + ".dtb" )
    else:
        fdt = dt

    return fdt

def test_failed( error_msg ):
    print( "[TEST FAILED]: " + error_msg )
    if continue_on_error:
        print( "[NOTE]: continue is enabled, not exiting" )
    else:
        sys.exit(1)

def test_passed( msg ):
    print( "[TEST PASSED]: " + msg )

def test_pattern_count( fpp, pattern ):
    count = 0
    with open(fpp) as fp:
        for line in fp:
            if re.search( pattern, line ):
                count += 1

    return count

def tree_sanity_test( fdt, verbose=0 ):
    # test1: simple tree walking routine
    print( "[TEST]: start: tree walk" )
    #  - test:
    #      - that we don't assert
    #      - that we have the right number of nodes
    walker = LopperTree()
    walker.load( Lopper.export( fdt ) )

    fpp = tempfile.NamedTemporaryFile( delete=True )
    fpw = open( fpp.name, 'w+')
    for n in walker:
        try:
            print( "\nnode: %s:%s [%s] parent: %s children: %s depth: %s" % (n.name, n.number,
                                                                             hex(n.phandle), n.parent,
                                                                             n.child_nodes, n.depth), file=fpw)
            for prop in n:
                print( "    property: %s %s" % (prop.name, prop.value), file=fpw)
                print( "    raw: %s" % (n[prop.name]), file=fpw )
        except Exception as e:
            print( "ERROR. exception while walking nodes: %s" % e )
            sys.exit(1)

    fpw.seek(0)
    # we should have: <x> "node:" prints
    with open(fpp.name) as fp:
        node_count = 0
        for line in fp:
            print( "%s" % line )
            if re.search( "node:", line ):
                node_count += 1

    if node_count != 21:
        test_failed( "node count (1) is incorrect. Got %s, expected %s" % (node_count,21) )
    else:
        test_passed( "end: node walk passed\n" )

    fpw.close()

    # test2: tree print
    print( "[TEST]: start: tree print" )

    # test the build in print() routines (not the walker version), and
    # test for /memreserve/
    memres_tree = LopperTree()
    dct = Lopper.export( fdt )
    memres_tree.load( dct )
    fpp = tempfile.NamedTemporaryFile( delete=True )
    fpw = open( fpp.name, 'w+')
    memres_tree.print( fpw )

    memres_found = None
    memres_regex = re.compile( r'\/memreserve\/(.*)?;' )
    with open(fpp.name) as fp:
        for line in fp:
            memres = re.search( memres_regex, line )
            if memres:
                memres_found = memres

    if not memres_found:
        test_failed( "/memreserve/ was not maintained through processing" )
    else:
        test_passed( "/memreserve/ was maintained through processing" )
    fpw.close()

    fpp = tempfile.NamedTemporaryFile( delete=True )

    printer = LopperTreePrinter( True, fpp.name )
    printer.load( Lopper.export( fdt ) )
    printer.exec()

    with open(fpp.name) as fp:
        node_count = 0
        for line in fp:
            if re.search( "{", line ):
                node_count += 1

    if node_count != 21:
        test_failed( "node count (2) is incorrect (%s expected %s)" % (node_count, 21) )
    else:
        test_passed( "end: tree print passed\n")
    fpw.close()

    # test3: node manipulations/access
    print( "[TEST]: start: node manipulations" )
    try:
        n = printer['/amba']
        if verbose:
            print( "    node access via '/amba' found: %s, %s" % (n, [n]) )
        test_passed( "node access by path" )
    except:
        test_failed( "node access by path failed" )

    try:
        n2 = printer[n.number]
        if verbose:
            print( "    node access by number '%s' found: %s" % (n.number,[n2]) )
        test_passed( "node access by number" )
    except:
        test_failed( "node access by path failed" )

    # write it back, as a test only
    try:
        printer['/amba'] = n
        test_passed( "node reassignment by name" )
    except:
        test_failed( "node reassignment by name" )

    try:
        pp = n['compatible']
        if verbose:
            print( "    property access (compatible): name: %s value: %s string: %s" % (pp.name, pp.value, pp) )
        test_passed( "property access by name" )
    except:
        test_failed( "property access by name" )

    # phandle tests
    try:
        n = printer.pnode( n.phandle )
        if verbose:
            print( "    node access via phandle %s: %s" % (hex(n.phandle), n) )
        test_passed( "node access via phandle" )
    except:
        test_failed( "node access via phandle" )

    print( "[TEST]: end: node manipulations\n" )

    # iteration tests
    print( "[TEST]: start: custom node lists" )

    fpp = tempfile.NamedTemporaryFile( delete=False )

    printer.reset( fpp.name )

    printer.__new_iteration__ = True
    printer.__current_node__ = "/amba_apu"
    printer.exec()

    c = test_pattern_count( fpp.name, "{" )
    if c == 6:
        test_passed( "custom node print" )
    else:
        test_failed( "custom node print (%s vs %s)" % (c,6) )

    # shouldn't break anything: random re-resolves
    printer.resolve()
    fpp.close()

    fpp = tempfile.NamedTemporaryFile( delete=False )
    fpw = open( fpp.name, 'w+')

    print( "\n[SUB TEST]: full node walk after custom node list" )
    for p in printer:
        print( "        node: %s" % p, file=fpw )

    fpw.close()
    c = test_pattern_count( fpp.name, ".*node:" )
    if c == 21:
        test_passed( "full walk, after restricted walk" )
    else:
        test_failed( "full walk, after restricted walk (wrong number of nodes)" )

    print( "[SUB TEST]: end full node walk after custom node list\n" )

    os.unlink( fpw.name )


    print( "[SUB TEST]: subtree walk" )
    # this should only walk the amba sub-tree
    printer.__current_node__ = "/amba"
    count = 0
    for p in printer:
        if verbose:
            print( "    /amba restricted test: node: %s" % p )
        count += 1

    if count == 3:
        test_passed( "subtree walk" )
    else:
        test_failed( "subtree walk (%s vs %s)" % (count,3))
    print( "[SUB TEST]: end subtree walk\n" )

    print( "[SUB TEST]: start node -> end of tree walk" )
    printer.reset()

    # the difference here, is that this starts at amba and walks ALL the way to
    # the end of the tree.
    printer.__start_node__ = "/amba"
    count = 0
    for p in printer:
        if verbose:
            print( "       starting node test: node: %s" % p )
        count += 1

    if count == 15:
        test_passed( "start -> end walk" )
    else:
        test_failed( "start -> end walk (%s vs %s)" % (count,15))

    print( "[SUB TEST]: start node -> end of tree walk\n" )

    # debug level bump up.
    printer.__dbg__ = 3

    # subnode routine tests
    print( "[TEST]: start: subnode calls (both should be the same)" )
    kiddies = printer.subnodes( printer.__nodes__['/amba'] )
    subnode_count = 0
    if verbose:
        print( "amba subnodes: %s" % kiddies )
    for k in kiddies:
        if verbose:
            print( "    node: %s" % k.abs_path )
        subnode_count += 1

    subnode_count2 = 0
    kiddies2 = printer['/amba'].subnodes()
    if verbose:
        print( "abma subnodes type 2: %s" % kiddies2 )
    for k in kiddies2:
        if verbose:
            print( "    node2: %s" % k.abs_path )
        subnode_count2 += 1

    if subnode_count == subnode_count2:
        test_passed( "subnode count" )
    else:
        test_failed( "subnode count (%s vs %s)" % (subnode_count,subnode_count2))

    subnodecount = 0
    kiddies = printer.subnodes( printer['/'] )
    if verbose:
        print( "/ subnodes: %s" % kiddies )
    for k in kiddies:
        if verbose:
            print( "    node: %s" % k.abs_path )
        subnodecount += 1

    if subnodecount == 21:
        test_passed( "full tree subnode" )
    else:
        test_failed( "full tree subnode (%s vs %s)" % (subnodecount,21))

    subnodecount = 0
    kiddies = printer.subnodes( printer['/'], ".*amba.*" )
    if verbose:
        print( "/ subnodes matching regex '.*amba.*': %s" % kiddies )
    for k in kiddies:
        if verbose:
            print( "    node: %s" % k.abs_path )
        subnodecount += 1

    if subnodecount == 9:
        test_passed( "regex subnode" )
    else:
        test_failed( "regex subnode (%s vs %s)" % (subnodecount,9))

    print( "[TEST]: end: subnode calls\n" )

    print( "[TEST]: start: resolve test" )
    refcount = 0
    root_found = False
    amba_found = False

    all_refs = printer['/amba/interrupt-multiplex'].resolve_all_refs()
    for a in all_refs:
        if a.abs_path == "/":
            root_found = True
        if a.abs_path == "/amba":
            amba_found = True

        if verbose:
            print( "/amba/interrupt-multiplex ref: %s" % a.abs_path )

        refcount += 1

    if refcount == 6:
        test_passed( "resolve" )
    else:
        test_failed( "resolve (%s vs %s)" % (refcount,6))

    if root_found and amba_found:
        test_passed( "parent nodes found" )
    else:
        test_failed( "parent nodes found (%s,%s)" % (root_found,amba_found) )

    print( "[TEST]: end: resolve test" )

    print( "[TEST]: start: node access tests and __str__ routine" )
    printer.__dbg__ = 0
    if verbose:
        print( "amba node: %s" % printer.__nodes__['/amba'] )
        print( "amba node number: %s " % int(printer.__nodes__['/amba']))
    if "/amba" == str(printer.__nodes__['/amba']):
        test_passed( "__str__" )
    else:
        test_failed( "__str__" )

    printer.__dbg__ = 3

    if verbose:
        print( "amba node raw: %s" % printer.__nodes__['/amba'] )
    if re.search( "<lopper.tree.LopperNode.*", str(printer.__nodes__['/amba']) ):
        test_passed( "__str__ raw" )
    else:
        test_failed( "__str__ raw" )

    printer.__dbg__ = 0

    if verbose:
        print( "type: %s" % type(printer.__nodes__['/amba']) )
    if isinstance( printer.__nodes__['/amba'], LopperNode ):
        test_passed( "instance type" )
    else:
        test_failed( "instance type" )

    print( "[TEST]: end: node access tests and __str__ routine\n" )

    print( "[TEST]: start: node comparison tests" )
    if verbose:
        print( "Comparing '/amba' and '/amba_apu'" )
    if printer.__nodes__['/amba'] == printer.__nodes__['/amba_apu']:
        test_failed( "equality test" )
    else:
        test_passed( "equality test" )
    print( "[TEST]: end: node comparison tests" )

    print( "[TEST]: start: node regex find test" )

    if verbose:
        print( "Searching for /amba/.*" )

    matches = printer.nodes( "/amba/.*" )
    count = 0
    multiplex = False
    for m in matches:
        count += 1
        if m.name == "interrupt-multiplex":
            multiplex = True
        if verbose:
            print( " match: %s [%s]" % (m.abs_path, m) )

    if count == 2 and multiplex:
        test_passed( "regex node match" )
    else:
        test_failed( "regex node match (wrong number of nodes)" )

    if verbose:
        print( "searching for /amba.*" )
    count = 0
    matches = printer.nodes( "/amba.*" )
    for m in matches:
        count += 1
        if m.name == "interrupt-multiplex":
            multiplex = True
        if verbose:
            print( "    match: %s [%s]" % (m.abs_path, m) )

    if count == 9 and multiplex:
        test_passed( "regex node match 2" )
    else:
        test_failed( "regex node match 2 (wrong number of nodes)" )

    if verbose:
        print( "exact node match: /amba" )
    matches = printer.nodes( "/amba" )
    for m in matches:
        if verbose:
            print( "    match: %s" % m.abs_path )
        pass

    amba = matches[0]
    if len(matches) == 1 and amba.abs_path == "/amba":
        test_passed( "exact node match" )
    else:
        test_failed( "exact node match" )
    print( "[TEST]: end: node regex find test\n" )

    print( "[TEST]: start: property regex find test" )
    p = amba.props( 'compat.*' )
    p = p[0]
    if verbose:
        print( "prop type is: %s" % type(p) )
        print( "amba p0: %s" % p.value[0] )
        print( "amba p1: %s" % p )
    if isinstance( p, LopperProp ):
        test_passed( "prop match type" )
    else:
        test_failed( "prop match type" )
    if p.value[0] == "simple-bus":
        test_passed( "prop value" )
    else:
        test_failed( "prop value (%s vs %s)" % ( p.value[0], "simple-bus" ) )
    if str(p) == "compatible = \"simple-bus\";":
        test_passed( "prop str" )
    else:
        test_failed( "prop str" )
    print( "[TEST]: end: property regex find test\n" )

    print( "[TEST]: start: property assign test" )
    p.value = "testing 1.2.3"
    if verbose:
        print( "amba p2: %s" % p.value[0] )
        print( "amba p3: %s" % str(p) )

    if p.value[0] == "testing 1.2.3":
        test_passed( "prop value re-assign" )
    else:
        test_failed( "prop value re-assign (%s vs %s)" % (p.value[0],"testing 1.2.3"))

    if str(p) == "compatible = \"testing 1.2.3\";":
        test_passed( "prop re-assign, resolve" )
    else:
        test_failed( "prop re-assign, resolve (%s vs %s)" % (str(p), "compatible = \"testing 1.2.3\";" ))
    print( "[TEST]: end: property assign test\n" )

    print( "[TEST]: start: tree manipulation tests" )
    new_node = LopperNode( -1, "/amba/bruce" )
    if verbose:
        print( "    new node name: %s" % new_node.name )
        print( "    new node refcount: %s" % new_node.ref )
    new_node.ref = 2
    new_node.ref = 1
    if verbose:
        print( "    new node refcount is: %s" % new_node.ref )

    if new_node.ref == 3:
        test_passed( "node ref" )
    else:
        test_failed( "node ref (%s vs %s)" % (new_node.ref,3))

    if verbose:
        print( "\n" )
        print( "creating new property for new node .... " )

    new_property = LopperProp( "foobar", -1, new_node, [ "testingfoo" ] )

    if verbose:
        print( "Property (%s) add to node: %s" % (new_property.name,new_node ))

    # new_node.add( new_property )
    new_node + new_property
    # printer.add( new_node )
    printer + new_node

    # confirm the node details are the same:
    if verbose:
        print( "new_node path: %s" % new_node.abs_path )
        print( "ref count: %s" % printer[new_node].ref )
    if new_node.abs_path == "/amba/bruce" and new_node.ref == 3:
        test_passed( "node + prop + tree" )
    else:
        test_failed( "node + prop + tree" )

    print( "[TEST]: end: tree manipulation tests\n" )

    print( "[TEST]: start: tree ref count test" )
    refd = printer.refd()
    if verbose:
        print( "referenced nodes: %s" % refd[0].abs_path )

    if len(refd) == 1 and refd[0].abs_path == "/amba/bruce":
        test_passed( "node ref persistence" )
    else:
        test_failed( "node ref persistence" )

    printer.ref( 0 )
    refd = printer.refd()
    if verbose:
        print( "After clear, referenced nodes: %s" % refd )
    if len(refd) == 0:
        test_passed( "node ref reset" )
    else:
        test_failed( "node ref reset" )
    print( "[TEST]: end: tree ref count test\n" )

    print( "[TEST]: start: tree re-resolve test" )
    if verbose:
        print( "======================= re resolving =======================" )
    printer.resolve()
    if verbose:
        print( "======================= resolve done =======================" )

    if verbose:
        for n in printer:
            print( "node: %s" % n.abs_path )

    fpp = tempfile.NamedTemporaryFile( delete=False )

    printer.__dbg__ = 0
    printer.__start_node__ = 0
    printer.reset( fpp.name )
    printer.exec()

    # if we get here, we passed
    test_passed( "tree re-resolve\n" )

    print( "[TEST]: end: tree re-resolve test\n" )

    print( "[TEST]: start: second tree test" )
    print2 = LopperTreePrinter()
    print2.load( printer.export() )
    if verbose:
        for n in print2:
            print( "2node: %s" % n )
        print( "\n" )

    fpp2 = tempfile.NamedTemporaryFile( delete=False )
    print2.reset( fpp2.name )
    print2.exec()

    if filecmp.cmp( fpp.name, fpp2.name ):
        test_passed( "two tree print" )
    else:
        test_failed( "two tree print (%s does not equal %s)" % (fpp.name,fpp2.name))

    print( "[TEST]: end: second tree test\n" )

    print( "[TEST]: start: node persistence test" )
    latched_state = new_node.__nstate__
    if verbose:
        print( "new node's state is now: %s" % new_node.__nstate__ )

    if new_node.__nstate__ == "resolved":
        test_passed( "node persistence test" )
    else:
        test_failed( "node persistence test" )

    print( "[TEST]: end: node persistence test\n" )

    print( "[TEST]: start: second property test" )
    new_property2 = LopperProp( "number2", -1, new_node, [ "i am 2" ] )

    # type1: can we just add to the node and sync it ?
    new_node + new_property2
    if verbose:
        print( "syncing new property" )
    printer.sync()
    if verbose:
        print( "end syncing new property" )
    # the above works when nodes are fully reused

    # type2: or can we fetch it out of the tree, assign and sync
    # printer[new_node] + new_property2
    # printer.sync()
    # the above works

    # is a double node add an error ?
    printer + new_node
    printer.sync()
    # end double add

    if verbose:
        print( "writing to: /tmp/tester-output.dts" )

    LopperSDT(None).write( printer, "/tmp/tester-output.dts", True, True )

    # remove the 2nd property, re-write
    if verbose:
        print( "writing to: /tmp/tester-output2.dts (with one less property)" )
    new_node - new_property2
    printer.sync()
    LopperSDT(None).write( printer, "/tmp/tester-output2.dts", True, True )

    if filecmp.cmp( "/tmp/tester-output.dts", "/tmp/tester-output2.dts", False ):
        test_failed( "node remove write should have differed" )
    else:
        test_passed( "node remove write" )

    test_passed( "second property test" )
    print( "[TEST]: end: second property test\n" )

    print( "[TEST]: start: second tree test, node deep copy" )

    tree2 = LopperTreePrinter( True )
    tree2.load( Lopper.export( fdt ) )
    # new_node2 = LopperNode()
    # invokes a deep copy on the node
    new_node2 = new_node()

    if verbose:
        print( "node2: %s" % new_node2.abs_path )
    #new_node2 = new_node2(new_node)
    if verbose:
        print( "node2: %s" % new_node2.abs_path )

    # the property objects, should be different, since these are copies
    if verbose:
        print( "node1 props: %s" % new_node.__props__ )
        print( "node2 props: %s" % new_node2.__props__ )

    if new_node.abs_path != new_node2.abs_path:
        test_failed( "copied nodes should be equal" )
    else:
        test_passed( "copied nodes equal" )

    if new_node.__props__ == new_node2.__props__:
        test_failed( "copied properties should not be equal" )

    # not required, but could re-add to make sure they don't harm anything
    # new_node2.resolve( tree2.fdt )
    # new_node2.sync( tree2.fdt )

    if verbose:
        tree2.__dbg__ = 3

    tree2 + new_node2

    LopperSDT(None).write( tree2, "/tmp/tester-output-tree2.dts", True, True )

    print( "[TEST]: end: second tree test, node deep copy\n" )

    print( "[TEST]: start: tree test, node remove" )

    printer = printer - new_node
    #printer.delete( new_node )

    LopperSDT(None).write( printer, "/tmp/tester-output-node-removed.dts", True, True )
    print( "[TEST]: end: tree test, node remove" )

    if filecmp.cmp( "/tmp/tester-output-node-removed.dts", "/tmp/tester-output-tree2.dts", False ):
        test_failed( "node remove write should have differed" )
    else:
        test_passed( "node remove differed" )

    # another propery add manipulation test
    prop = LopperProp( "newproperty_existingnode" )
    existing_node = printer['/amba']
    existing_node + prop
    # you can resolve the prop, but it isn't required
    # prop.resolve( printer.fdt )
    # You also don't need this full sync
    # printer.sync()
    # you can sync the node if you want, but also, not
    # required
    # existing_node.sync( printer.fdt )

    printer.reset( fpp.name )
    printer.exec()

    c = test_pattern_count( fpp.name, "newproperty_existingnode" )
    if c == 1:
        test_passed( "property add, existing node (%s)" % fpp.name )
    else:
        test_failed( "property add, existing node (%s)" % fpp.name )

    print( "[TEST]: start, new tree test" )

    fpp = tempfile.NamedTemporaryFile( delete=True )

    new_tree = LopperTreePrinter( False, fpp.name )
    # make a copy of our existing node
    new_tree_new_node = new_node()

    new_tree + new_tree_new_node
    new_tree_new_node + prop
    new_tree.exec()

    c = test_pattern_count( fpp.name, "amba" )
    if c != 1:
        test_failed( "new tree test" )
    c = test_pattern_count( fpp.name, "bruce" )
    if c != 1:
        test_failed( "new tree test" )
    c = test_pattern_count( fpp.name, "newproperty_existingnode" )
    if c != 1:
        test_failed( "new tree test" )

    test_passed( "new tree test" )

    # property access tests
    prop_tree = LopperTree()
    prop_tree.load( Lopper.export( fdt ) )

    cpu_node = prop_tree["/cpus/cpu@0"]
    cpu_prop = cpu_node["compatible"]
    compat1 = cpu_prop[0]
    compat2 = cpu_prop[1]

    if compat1 == "arm,cortex-a72" and \
       compat2 == "arm,armv8":
        test_passed( "simple property index access" )
    else:
        test_failed( "simple property index access" )

    prop_dict = dict(cpu_prop)
    if prop_dict['value'] == ['arm,cortex-a72', 'arm,armv8']:
        test_passed( "property dict access" )
    else:
        test_failed( "property dict access" )

    if len(cpu_prop) == 2:
        test_passed( "property length" )
    else:
        test_failed( "property length" )

    pp = cpu_node.propval( "compatible", dict )
    if pp['value'] == ['arm,cortex-a72', 'arm,armv8']:
        test_passed( "propval dict access" )
    else:
        test_failed( "propval dict access" )

    # alias test
    alias = printer.alias_node( "imux" )
    if not alias:
        test_failed( "alias lookup for valid node" )
    else:
        test_passed( "alias lookup for valid node" )
    alias = printer.alias_node( "serial0-fake" )
    if alias:
        test_failed( "alias lookup for invalid node" )
    else:
        test_passed( "alias lookup for invalid node" )


def lops_code_test( device_tree, lop_file, verbose ):

    device_tree.setup( dt, [lop_file], "", True, libfdt = libfdt )

    with Capturing() as output:
        device_tree.perform_lops()

    if verbose:
        print( output._stringio.getvalue() )

    test_output = output._stringio.getvalue()

    if re.search( "a72 found, tagging", test_output ):
        test_passed( "code block exec" )
    else:
        test_failed( "code block exec" )

    if re.search( "\[FOUND\] enable-method", test_output ):
        test_passed( "enable-method, true block" )
    else:
        test_failed( "compatible node, true block" )

    if re.search( "\[FOUND 2\] enable-method", test_output ):
        test_passed( "enable-method, chained true block" )
    else:
        test_failed( "enable-method, chained true block" )

    c = len(re.findall( "[^']\[FOUND\] cpu that does not match invalid a72", test_output ))
    if c == 3:
        test_passed( "compatible node, false block" )
    else:
        test_failed( "compatible node, false block" )

    c = len(re.findall( "[^']\[INFO\] double condition a72 found", test_output ))
    if c == 2:
        test_passed( "double condition" )
    else:
        test_failed( "double condition" )

    c = len(re.findall( "[^']\[INFO\] double condition a72 not found", test_output ))
    if c == 2:
        test_passed( "double condition, false" )
    else:
        test_failed( "double condition, false" )

    c = len(re.findall( "[^']\[INFO\] double condition inverted a72 found", test_output ))
    if c == 2:
        test_passed( "double condition, inverted" )
    else:
        test_failed( "double condition, inverted" )

    c = len(re.findall( "[^']\[INFO\] double condition list a72 found", test_output ))
    if c == 1:
        test_passed( "double condition, list" )
    else:
        test_failed( "double condition, list" )

    c = len(re.findall( "[^']\[INFO\] node tag:", test_output ))
    if c == 3:
        test_passed( "data persistence" )
    else:
        test_failed( "data persistence" )

    c = len(re.findall( "[^']\[INFO\] clock magic", test_output ))
    if c == 1:
        test_passed( "data persistence 2" )
    else:
        test_failed( "data persistence 2" )

    c = len(re.findall( "[^']track: lopper library routine", test_output ))
    if c == 1:
        test_passed( "exec/library routine" )
    else:
        test_failed( "exec/library routine" )

    c = len(re.findall( "[^']print_test", test_output ))
    if c == 2:
        test_passed( "print_test" )
    else:
        test_failed( "print_test" )

    if re.search( "arm,idle-state", test_output ):
        test_passed( "node print test" )
    else:
        test_failed( "node print test" )

    if re.search( "[^']selected: /cpus/cpu@2" , test_output ):
        c = len(re.findall( "testprop: testvalue", test_output ))
        if c == 1:
            test_passed( "selection test (and)" )
        else:
            test_failed( "selection test (and)" )

    c = len(re.findall( "[^']selected2:", test_output ))
    if c == 4:
        test_passed( "selection test (or)" )
    else:
        test_failed( "selection test (or) (found %s, expected: %s)" %(c,4))

    output.reset()

def lops_sanity_test( device_tree, lop_file, verbose ):
    if not libfdt:
        return

    device_tree.setup( dt, [lop_file], "", True, libfdt=libfdt )

    device_tree.verbose = 5

    device_tree.perform_lops()

    print( "[TEST]: writing to %s" % (device_tree.output_file))

    Lopper.sync( device_tree.FDT, device_tree.tree.export() )
    device_tree.write( enhanced = True )

    print( "\n[TEST]: check lopper operations on: %s" % (device_tree.output_file))
    c = test_pattern_count( device_tree.output_file, "anode_to_delete" )
    if c != 0:
        test_failed( "node deletion failed" )
    else:
        test_passed( "node deletion" )

    c = test_pattern_count( device_tree.output_file, "cpus_a72" )
    if c != 1:
        test_failed( "node rename failed" )
    else:
        test_passed( "node rename" )

    c = test_pattern_count( device_tree.output_file, "no-access" )
    if c != 0:
        test_failed( "property remove failed" )
    else:
        test_passed( "property remove" )

    c = test_pattern_count( device_tree.output_file, "nested-node" )
    if c != 0:
        test_failed( "nested node deletion failed" )
    else:
        test_passed( "nested node deletion" )

    c = test_pattern_count( device_tree.output_file, "zynqmp-rpu" )
    if c == 1:
        c = test_pattern_count( device_tree.output_file, "__cpu__" )
        if c == 1:
            test_passed( "node add" )
        else:
            test_failed( "node add" )
    else:
        test_failed( "node add" )

    c = test_pattern_count( device_tree.output_file, "lopper-mboxes" )
    if c == 1:
        test_passed( "new node property modify" )
    else:
        test_failed( "new node property modify" )

    c = test_pattern_count( device_tree.output_file, "model = \"this is a test\"" )
    if c == 1:
        test_passed( "root property modify" )
    else:
        test_failed( "root property modify" )

    c = test_pattern_count( "/tmp/openamp-test.dts", "zynqmp-rpu" )
    if c == 1:
        test_passed( "node selective output" )
    else:
        test_failed( "node selective output" )

    c = test_pattern_count( "/tmp/linux-amba.dts", ".*amba.*{" )
    if c == 2:
        test_passed( "node regex output" )
    else:
        test_failed( "node regex output" )

    c = test_pattern_count( device_tree.output_file, "pnode-id =" )
    if c == 1:
        test_passed( "property add" )
    else:
        test_failed( "property add" )

    c = test_pattern_count( device_tree.output_file, "lopper-id-regex" )
    if c == 2:
        test_passed( "property via regex add" )
    else:
        test_failed( "property via regex add" )

    sub_tree = device_tree.subtrees["openamp-test"]
    sub_tree_output = Path("/tmp/openamp-test2.dts")
    sub_tree_file = sub_tree_output.resolve()
    if not sub_tree_file:
        test_failed( "subtree write" )
    else:
        test_passed( "subtree_wrte" )

    c = test_pattern_count( str(sub_tree_file), "#size-cells = <0x3>;" )
    if c == 1:
        test_passed( "subtree property modify" )
    else:
        test_failed( "subtree property modify" )

    # if the indentation matches this, it was moved
    c = test_pattern_count( str(sub_tree_file), "                reserved-memory {" )
    if c == 1:
        test_passed( "subtree node move" )
    else:
        test_failed( "subtree node move" )

    # test list modify lops
    c = test_pattern_count( device_tree.output_file, "listval = <0xf 0x5>" )
    if c == 1:
        test_passed( "listval modify" )
    else:
        test_failed( "listval modify" )

    c = test_pattern_count( device_tree.output_file, "liststring = \"four\", \"five\"" )
    if c == 1:
        test_passed( "liststring modify" )
    else:
        test_failed( "liststring modify" )

    c = test_pattern_count( device_tree.output_file, "singlestring = \"newcpu\"" )
    if c == 1:
        test_passed( "single string modify" )
    else:
        test_failed( "single string modify" )

    c = test_pattern_count( device_tree.output_file, "singleval = <0x5>" )
    if c == 1:
        test_passed( "single val modify" )
    else:
        test_failed( "single val modify" )

    device_tree.cleanup()

def assists_sanity_test( device_tree, lop_file, verbose ):
    device_tree.setup( dt, [lop_file], "", True, libfdt = libfdt )
    device_tree.assists_setup( [ "lopper/assists/domain-access.py" ] )

    print( "[TEST]: running assist against tree" )
    device_tree.perform_lops()

    print( "[TEST]: writing resulting FDT to %s" % device_tree.output_file )
    device_tree.write( enhanced = True )

    device_tree.cleanup()

def format_sanity_test( device_tree, verbose ):
    device_tree.setup( dt, [], "", True, libfdt = libfdt )

    print( "[TEST]: writing to %s" % (device_tree.output_file))
    device_tree.write( enhanced = True )


def fdt_sanity_test( device_tree, verbose ):

    device_tree.setup( dt, [], "", True, libfdt = libfdt )
    dct = Lopper.export( device_tree.FDT )

    # we have a list of: containing dict, value, parent
    dwalk = [ [dct,dct,None]  ]
    node_ordered_list = []
    while dwalk:
        firstitem = dwalk.pop()
        if type(firstitem[1]) is OrderedDict:
            node_ordered_list.append( [firstitem[1], firstitem[0]] )
            for item,value in reversed(firstitem[1].items()):
                dwalk.append([firstitem[1],value,firstitem[0]])
        else:
            pass

    print( "[INFO]: exported dictionary, node walk: " )
    for n in node_ordered_list:
        print( "    node: %s (parent: %s)" % (n[0]['__path__'],n[1]['__path__']) )
        for i,v in n[0].items():
            if type(v) != OrderedDict and i != "__path__":
                print("         %s -> %s" % (i,v))

    lt = lopper.tree.LopperTreePrinter()
    lt.load( dct )

    print( "[INFO]: printing loaded tree" )
    lt.__dbg__ = 0
    lt.exec()
    print( "[INFO]: ending tree print" )

    print( "[INFO]: starting tree print #2" )
    dct2 = Lopper.export( device_tree.FDT )
    lt.load( dct2 )
    lt.__dbg__ = 0
    lt.exec()
    print( "[INFO]: ending tree print #2" )

    print( "[INFO]: starting tree write" )
    # lt.__dbg__= 5
    dct2 = lt.export()
    Lopper.sync( device_tree.FDT, dct2 )
    print( "[INFO]: ending tree write" )


    print( "[INFO]: reading tree back" )
    dct3 = Lopper.export( device_tree.FDT )
    lt3 = lopper.tree.LopperTreePrinter()
    lt3.load( dct3 )

    print( "[INFO]: starting re-read tree print" )
    lt3.__dbg__ = 0
    lt3.exec()
    print( "[INFO]: ending re-read tree print" )

    print( "[INFO]: deleting nodes" )
    # drop a node
    nd = lt3['/cpus/idle-states']
    lt3.delete( nd )

    nd = lt3['/cpus/cpu@0']
    lt3.delete(nd)

    nd = lt3['/cpus']
    nd.delete( 'compatible' )

    print( "[INFO]: adding nodes" )
    new_node = LopperNode( -1, "/bruce" )
    new_prop = LopperProp( "testing" )
    new_prop.value = "1.2.3"

    new_node = new_node + new_prop
    lt3.__dbg__ = 4
    lt3.add( new_node )

    new_node = LopperNode( -1, "/cpus-cluster@0/cpu@1/bruce2" )
    lt3.add( new_node )

    print( "[INFO]: node dump" )
    sub = lt3.subnodes( lt3.__nodes__["/"] )
    for s in sub:
        print( "nd1: %s" % s.abs_path )

        print( "[INFO]: node dump2, new iterator" )
    for n in lt3:
        print("nd2: %s" % n.abs_path )

    print( "[INFO]: nodes should be gone, one new one added (in memory copy only)" )
    # reprint
    lt3.__dbg__ = 0
    lt3.exec()

    # export, this should delete the node ..., and add the new ones
    dct2 = lt3.export()
    Lopper.sync( device_tree.FDT, dct2 )
    dct3 = Lopper.export( device_tree.FDT )

    print( "[INFO] second print. nodes gone, and new ones still present ")
    lt3 = lopper.tree.LopperTreePrinter()
    lt3.load( dct3 )
    lt3.__dbg__ = 0
    lt3.exec()



def yaml_sanity_test( device_tree, yaml_file, outdir, verbose ):
    device_tree.setup( dt, [], "", True, libfdt = libfdt )

    yaml = LopperYAML( yaml_file )

    print( "[TEST]: writing yaml to: %s" % outdir + "output_yaml.yaml" )
    yaml.to_yaml( outdir + "output_yaml.yaml" )

    lt = yaml.to_tree()

    print( "[TEST]: writing dts from yaml to: %s" % outdir + "output_yaml_to_dts.dts" )
    LopperSDT(None).write( lt, outdir + "output_yaml_to_dts.dts", True, True )

    print( "[TEST]: converting SDT to yaml" )
    yaml2 = LopperYAML( None, device_tree.tree )
    yaml2.dump()

    print( "\n\n\n" )
    ocp_node = lt["/ocp"]
    ocp_node.print()

    timer_node = lt[ "/ocp/TIMER4" ]
    gpio = timer_node["gpio" ]

    gpio_compat = gpio[0]
    print( gpio_compat )
    if type(gpio_compat) == dict:
        test_passed( "yaml complex property access" )
    else:
        test_failed( "yaml complex property access" )

    if gpio[0]['compatible'] == "ti,omap4-gpio":
        test_passed( "yaml complex struct access" )
    else:
        test_failed( "yaml complex struct access" )

def usage():
    prog = os.path.basename(sys.argv[0])
    print('Usage: %s [OPTION] ...' % prog)
    print('  -v, --verbose       enable verbose/debug processing (specify more than once for more verbosity)')
    print('  -t, --tree          run lopper tree tests' )
    print('  -l, --lops          run lop tests' )
    print('  -a, --assists       run assist tests' )
    print('  -f, --format        run format tests (dts/yaml)' )
    print('  -d, --fdt           run fdt abstraction tests' )
    print('    , --werror        treat warnings as errors' )
    print('    , --all           run all sanity tests' )
    print('  -h, --help          display this help and exit')
    print('')

def main():
    global verbose
    global force
    global werror
    global outdir
    global lops
    global tree
    global assists
    global format
    global continue_on_error
    global fdttest
    global libfdt

    verbose = 0
    force = False
    werror = False
    outdir="/tmp/"
    tree = False
    lops = False
    assists = False
    format = False
    fdttest = False
    continue_on_error = False
    libfdt = True
    try:
        opts, args = getopt.getopt(sys.argv[1:], "avtlhd", [ "no-libfdt", "all", "fdt", "continue", "format", "assists", "tree", "lops", "werror","verbose", "help"])
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
        elif o in ( '-a','--assists'):
            assists=True
        elif o in ( '-f', '--format'):
            format=True
        elif o in ( '-d', '--fdt' ):
            fdttest = True
        elif o in ( '--no-libfdt' ):
            libfdt = False
        elif o in ( '--all' ):
            tree = True
            lops = True
            assists = True
            fdttest = True
            format = True
        elif o in ( '--continue' ):
            continue_on_error = True
        elif o in ('--version'):
            print( "%s" % LOPPER_VERSION )
            sys.exit(0)
        else:
            assert False, "unhandled option"


if __name__ == "__main__":

    main()

    if libfdt:
        import lopper.fdt
        lopper.lopper_type(lopper.fdt.LopperFDT)
    else:
        import lopper.dt
        lopper.lopper_type(lopper.dt.LopperDT)

    Lopper = lopper.Lopper

    if tree:
        dt = setup_device_tree( outdir )
        fdt = setup_fdt( dt, outdir )
        tree_sanity_test( fdt, verbose )

    if lops:
        dt = setup_system_device_tree( outdir )
        lop_file = setup_lops( outdir )
        lop_file_2 = setup_code_lops( outdir )

        device_tree = LopperSDT( dt )

        device_tree.dryrun = False
        device_tree.verbose = verbose
        device_tree.werror = werror
        device_tree.output_file = outdir + "/sdt-output.dts"
        device_tree.cleanup_flag = True
        device_tree.save_temps = False
        device_tree.enhanced = True
        device_tree.outdir = outdir
        device_tree.use_libfdt = libfdt

        lops_sanity_test( device_tree, lop_file, verbose )

        # reset for the 2nd test
        print( "\n\n[INFO]: resetting and running code tests\n\n" )
        device_tree = LopperSDT( dt )

        device_tree.dryrun = False
        device_tree.verbose = verbose
        device_tree.werror = werror
        device_tree.output_file = outdir + "/sdt-output.dts"
        device_tree.cleanup_flag = True
        device_tree.save_temps = False
        device_tree.enhanced = True
        device_tree.outdir = outdir
        device_tree.use_libfdt = libfdt

        lops_code_test( device_tree, lop_file_2, verbose )

    if assists:
        dt = setup_system_device_tree( outdir )
        lop_file = setup_assist_lops( outdir )

        device_tree = LopperSDT( dt )

        device_tree.dryrun = False
        device_tree.verbose = verbose
        device_tree.werror = werror
        device_tree.output_file = outdir + "/assist-output.dts"
        device_tree.cleanup_flag = True
        device_tree.save_temps = False
        device_tree.enhanced = True
        device_tree.outdir = outdir
        device_tree.use_libfdt = libfdt

        assists_sanity_test( device_tree, lop_file, verbose )

    if format:
        dt = setup_format_tree( outdir )
        yt =  setup_yaml( outdir )
        device_tree = LopperSDT( dt )

        device_tree.dryrun = False
        device_tree.verbose = verbose
        device_tree.werror = werror
        device_tree.output_file = outdir + "/format-test-output.dts"
        device_tree.cleanup_flag = True
        device_tree.save_temps = False
        device_tree.enhanced = True
        device_tree.outdir = outdir
        device_tree.use_libfdt = libfdt

        format_sanity_test( device_tree, verbose )

        yaml_sanity_test( device_tree, yt, outdir, verbose )

    if fdttest:
        dt = setup_system_device_tree( outdir )

        device_tree = LopperSDT( dt )

        device_tree.dryrun = False
        device_tree.verbose = verbose
        device_tree.werror = werror
        device_tree.output_file = outdir + "/fdt-output.dts"
        device_tree.cleanup_flag = True
        device_tree.save_temps = False
        device_tree.enhanced = True
        device_tree.outdir = outdir
        device_tree.libfdt = libfdt

        fdt_sanity_test( device_tree, verbose )

        device_tree.tree.print()
