from lopper.tree import *

KERNEL_FLAG = 'openamp-xlnx-kernel'
HOST_FLAG = 'openamp-host'

zynqmp_ipi_to_irq_vect_id = {
  0xff330000 : 62,
  0xFF340000 : 63,
  0xFF350000 : 64,
  0xFF310000 : 65,
  0xff320000 : 66,
  0xff380000 : 67,
}

versal_ipi_to_irq_vect_id = {
    0xff330000 : 62,
    0xff340000 : 63,
    0xFF350000 : 64,
    0xFF360000 : 65,
    0xFF370000 : 66,
    0xFF380000 : 67,
}

ipi_to_agent = {
  0xff330000 : 0x400,
  0xff340000 : 0x600,
  0xff350000 : 0x800,
  0xff360000 : 0xa00,
  0xff370000 : 0xc00,
  0xff380000 : 0xe00,
}

openamp_supported_ipis = [ 0xff330000, 0xff340000, 0xff350000, 0xff360000, 0xff370000, 0xff380000 ]

ipi_addr_to_agent = {
    0xff310000 : 'psm',
    0xff320000 : 'pmc',

    0xff330000 : 'ipi0',
    0xff340000 : 'ipi1',
    0xff350000 : 'ipi2',
    0xff360000 : 'ipi3',
    0xff370000 : 'ipi4',
    0xff380000 : 'ipi5',

}


ipi_msg_buf_dest_agent_request_offsets = {
    0xff310000 : 0x000,
    0xff320000 : 0x040,
    0xff330000 : 0x080,
    0xff340000 : 0x0c0,
    0xff350000 : 0x100,
    0xff360000 : 0x140,
    0xff370000 : 0x180,
    0xff380000 : 0x1c0,

}

source_agent_to_ipi = {
  0x000: 'psm',  0x100: 'psm',
  0x200: 'pmc',  0x300: 'pmc',
  0x400: 'ipi0', 0x500: 'ipi0',
  0x600: 'ipi1', 0x700: 'ipi1',
  0x800: 'ipi2', 0x900: 'ipi2',
  0xa00: 'ipi3', 0xb00: 'ipi3',
  0xc00: 'ipi4', 0xd00: 'ipi4',
  0xe00: 'ipi5', 0xf00: 'ipi5',

}
agent_to_ipi_bitmask = {
  0x000: 0x1 ,
  0x200: 0x2 ,  
  0x400: 0x4,
  0x600: 0x8,
  0x800: 0x10,
  0xa00: 0x20,
  0xc00: 0x40,
  0xe00: 0x80,

  0x100: 0x1 ,
  0x300: 0x2 ,  
  0x500: 0x4,
  0x700: 0x8,
  0x900: 0x10,
  0xb00: 0x20,
  0xd00: 0x40,
  0xf00: 0x80,

}


class SOC_TYPE:
    UNINITIALIZED = -1
    VERSAL = 0
    ZYNQMP = 1
    ZYNQ = 2


def resolve_remoteproc_carveouts( tree, subnode, verbose = 0 ):
    prop = None
    domain_node = None
    new_prop_val = []

    if subnode.props("elfload") == []:
        print("WARNING:", "remoteproc relation does not have elfload carveouts", subnode.abs_path)
        return False

    prop = subnode.props("elfload")[0]

    # for each carveout
    # look for it in domain's reserved memory
    # get or create phandle of the carveout
    for carveout_str in prop.value:
        current_node = None

        for n in subnode.tree["/"].subnodes():
            if carveout_str == n.name or carveout_str == n.label:
                current_node = n
                break

        if current_node == None:
            print("WARNING:", "rpmsg relation can't find carveout name", carveout_str)
            return False

        if current_node.phandle == 0:
            current_node.phandle_or_create()

        if current_node.props("phandle") == []:
            current_node + LopperProp(name="phandle", value=current_node.phandle)

        new_prop_val.append(current_node.phandle)

    # update value of property to have phandles
    prop.value = new_prop_val

    return True

def resolve_rpmsg_carveouts( tree, subnode, verbose = 0 ):
    prop = None
    res_mem_node = None
    new_prop_val = []

    if subnode.props("carveouts") == []:
        print("WARNING:", "rpmsg relation does not have carveouts")
        return False

    prop = subnode.props("carveouts")[0]

    # for each carveout
    # look for it in domain's reserved memory
    # get or create phandle of the carveout

    try:
        res_mem_node = subnode.tree[subnode.parent.parent.abs_path + "/reserved-memory"]
    except KeyError:
        print("WARNING:", "rpmsg relation's backing domain does not have reserved memory with carveouts.", subnode.abs_path)
        return False

    for carveout_str in prop.value:
        current_node = None

        for n in res_mem_node.subnodes():
            if carveout_str == n.name or carveout_str == n.label:
                current_node = n
                break

        if current_node == None:
            print("WARNING:", "rpmsg relation can't find carveout name", carveout_str)
            return False

        if current_node.phandle == 0:
            current_node.phandle_or_create()

        if current_node.props("phandle") == []:
            current_node + LopperProp(name="phandle", value=current_node.phandle)

        new_prop_val.append(current_node.phandle)

    # update value of property to have phandles
    subnode.props("carveouts")[0].value = new_prop_val

    return True

def resolve_rpmsg_mbox( tree, subnode, verbose = 0 ):
    mbox_nodes = []
    props = []
    search_strs = []
    new_prop_val = []

    if subnode.props("mbox") == []:
        print("WARNING:", "rpmsg relation does not have mbox")
        return False

    props = subnode.props("mbox")[0].value

    for prop in props:
        search_strs.append ( prop.strip() )

    for search_str in search_strs:
        for n in subnode.tree["/"].subnodes():
            if search_str == n.name or search_str == n.label:
                mbox_nodes.append( n )
                break

    for i, mbox_node in enumerate(mbox_nodes):
        prop = props[i]
        if mbox_node == None:
            print("resolve_rpmsg_mbox: ", tree.lnodes(n.name, exact = False) )
        if mbox_node == None or mbox_node == []:
            print("WARNING:", "rpmsg relation can't find mbox name: ", prop.value)
            return False


        if mbox_node.phandle == 0:
            mbox_node.phandle_or_create()
        if mbox_node.props("phandle") == []:
            mbox_node + LopperProp(name="phandle", value=mbox_node.phandle)

        new_prop_val.append( mbox_node.phandle )

    subnode.props("mbox")[0].value = new_prop_val

    return True

def resolve_host_remote( tree, subnode, verbose = 0 ):
    prop_names = [ "host", "remote" ]
            
    if subnode.props(prop_names[0]) != [] and subnode.props(prop_names[1]) != []:
        print("WARNING:", "relation has both host and remote")
        return False
    for pn in prop_names:
        if subnode.props(pn) != [] and subnode.props(pn) != []:
            prop_val = subnode.props(pn)[0].value
            new_prop_val = []
            for p in prop_val:
                # find each host/remote matching domain node in tree
                for n in tree["/domains"].subnodes():
                    if p in n.name:
                        # give matching node phandle if needed
                        if n.phandle == 0:
                            n.phandle_or_create()
                        if n.props("phandle") == []:
                            n + LopperProp(name="phandle", value=n.phandle)
                        new_prop_val.append( n.phandle )
            subnode.props(pn)[0].value = new_prop_val
    
    return True

platform_info_header_a9_template = """
/*
 * Copyright (c) 2023 AMD, Inc.
 * All rights reserved.
 *
 * SPDX-License-Identifier: BSD-3-Clause
 */

#ifndef _AMD_GENERATED_H_
#define _AMD_GENERATED_H_

/* Interrupt vectors */
#define SGI_TO_NOTIFY           $SGI_TO_NOTIFY
#define SGI_NOTIFICATION        $SGI_NOTIFICATION

#define NUM_VRINGS              0x02
#define VRING_ALIGN             0x1000
#define VRING_SIZE              256

#define RING_TX                 $RING_TX
#define RING_RX                 $RING_RX

#define SHARED_MEM_PA           $SHARED_MEM_PA
#define SHARED_MEM_SIZE         $SHARED_MEM_SIZE
#define SHARED_BUF_OFFSET       $SHARED_BUF_OFFSET

#define SCUGIC_DEV_NAME         $SCUGIC_DEV_NAME
#define SCUGIC_BUS_NAME         $SCUGIC_BUS_NAME
#define SCUGIC_PERIPH_BASE      $SCUGIC_PERIPH_BASE
#define SCUGIC_DIST_BASE        ($SCUGIC_PERIPH_BASE + 0x00001000)

/* Memory attributes */
#define NORM_NONCACHE 0x11DE2   /* Normal Non-cacheable */
#define STRONG_ORDERED 0xC02    /* Strongly ordered */
#define DEVICE_MEMORY 0xC06 /* Device memory */
#define RESERVED 0x0        /* reserved memory */

/* Zynq CPU ID mask */
#define ZYNQ_CPU_ID_MASK 0x1UL

/* Another APU core ID. In this demo, the other APU core is 0. */
#define A9_CPU_ID   0UL

#endif /* _AMD_GENERATED_H_ */
"""

platform_info_header_r5_template = """
/*
 * Copyright (c) 2023 AMD, Inc.
 * All rights reserved.
 *
 * SPDX-License-Identifier: BSD-3-Clause
 */

#ifndef _AMD_GENERATED_H_
#define _AMD_GENERATED_H_

/* Interrupt vectors */
#define IPI_IRQ_VECT_ID         $IPI_IRQ_VECT_ID
#define POLL_BASE_ADDR          $POLL_BASE_ADDR
#define IPI_CHN_BITMASK         $IPI_CHN_BITMASK

#define NUM_VRINGS              0x02
#define VRING_ALIGN             0x1000
#define VRING_SIZE              256

#define RING_TX                 $RING_TX
#define RING_RX                 $RING_RX

#define SHARED_MEM_PA           $SHARED_MEM_PA
#define SHARED_MEM_SIZE         $SHARED_MEM_SIZE
#define SHARED_BUF_OFFSET       $SHARED_BUF_OFFSET

#define SHM_DEV_NAME            $SHM_DEV_NAME
#define DEV_BUS_NAME            $DEV_BUS_NAME
#define IPI_DEV_NAME            $IPI_DEV_NAME
#define RSC_MEM_SIZE            $RSC_MEM_SIZE
#define RSC_MEM_PA              $RSC_MEM_PA
#define SHARED_BUF_PA           $SHARED_BUF_PA
#define SHARED_BUF_SIZE         $SHARED_BUF_SIZE

#endif /* _AMD_GENERATED_H_ */
"""
