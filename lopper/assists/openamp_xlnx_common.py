"""Xilinx OpenAMP common helpers shared across assist modules.

This module defines enumerations representing CPU configuration state, the
memory map metadata consumed during remoteproc construction, and helper
routines that resolve references within generated device-tree relations.
Global collections exposed here are documented with inline docstrings so they
can be surfaced by documentation tooling.
"""

from lopper.tree import *
from enum import Enum
from enum import IntEnum
import ast

class CPU_CONFIG(IntEnum):
    """Enumerate the supported RPU execution configurations."""

    RPU_SPLIT = 0
    RPU_LOCKSTEP = 1

class RPU_CORE(IntEnum):
    """Enumerate individual RPU core indices."""

    RPU_0 = 0
    RPU_1 = 1
    RPU_2 = 2
    RPU_3 = 3
    RPU_4 = 4
    RPU_5 = 5
    RPU_6 = 6
    RPU_7 = 7
    RPU_8 = 8
    RPU_9 = 9

# This is used for YAML representation
# after this is parsed, the above enums are used for internal record keeping.
class CLUSTER_CONFIG(Enum):
    """Enumerate cluster configuration strings used by YAML input."""

    RPU_LOCKSTEP = 0
    RPU_0 = 1
    RPU_1 = 2

memory_nodes = {
    15: {  # psu_r5_0_atcm_global
        "system_view": [0x0, 0x0, 0x0, 0xffe00000, 0x0, 0x10000],
        "rpu_view": [0x0, 0x0, 0x0, 0x10000]
    },
    16: {  # psu_r5_0_btcm_global
        "system_view": [0x0, 0x20000, 0x0, 0xffe20000, 0x0, 0x10000],
        "rpu_view": [0x0, 0x20000, 0x0, 0x10000]
    },
    17: {  # psu_r5_1_atcm_global
        "system_view": [0x1, 0x0, 0x0, 0xffe90000, 0x0, 0x10000],
        "rpu_view": [0x1, 0x0, 0x0, 0x10000]
    },
    18: {  # psu_r5_1_btcm_global
        "system_view": [0x1, 0x20000, 0x0, 0xffeb0000, 0x0, 0x10000],
        "rpu_view": [0x1, 0x20000, 0x0, 0x10000]
    },
    0x1831800b: {  # psv_r5_0_atcm_global
        "system_view": [0x0, 0x0, 0x0, 0xffe00000, 0x0, 0x10000],
        "rpu_view": [0x0, 0x0, 0x0, 0x10000]
    },
    0x1831800c: {  # psv_r5_0_btcm_global
        "system_view": [0x0, 0x20000, 0x0, 0xffe20000, 0x0, 0x10000],
        "rpu_view": [0x0, 0x20000, 0x0, 0x10000]
    },
    0x1831800d: {  # psv_r5_1_atcm_global
        "system_view": [0x1, 0x0, 0x0, 0xffe90000, 0x0, 0x10000],
        "rpu_view": [0x1, 0x0, 0x0, 0x10000]
    },
    0x1831800e: {  # psv_r5_1_btcm_global
        "system_view": [0x1, 0x20000, 0x0, 0xffeb0000, 0x0, 0x10000],
        "rpu_view": [0x1, 0x20000, 0x0, 0x10000]
    },
    0x183180cb: {  # r52_0a_atcm_global
        "system_view": [0x0, 0x0, 0x0, 0xeba00000, 0x0, 0x10000],
        "rpu_view": [0x0, 0x0, 0x0, 0x10000]
    },
    0x183180cc: {  # r52_0a_btcm_global
        "system_view": [0x0, 0x10000, 0x0, 0xeba10000, 0x0, 0x8000],
        "rpu_view": [0x0, 0x10000, 0x0, 0x8000]
    },
    0x183180cd: {  # r52_0a_ctcm_global
        "system_view": [0x0, 0x18000, 0x0, 0xeba20000, 0x0, 0x8000],
        "rpu_view": [0x0, 0x18000, 0x0, 0x8000]
    },
    0x183180ce: {  # r52_0b_atcm_global
        "system_view": [0x1, 0x0, 0x0, 0xEBA80000, 0x0, 0x10000],
        "rpu_view": [0x1, 0x0, 0x0, 0x10000]
    },  
    0x183180cf: {  # r52_0b_btcm_global
        "system_view": [0x1, 0x10000, 0x0, 0xEBA90000, 0x0, 0x8000],
        "rpu_view": [0x1, 0x10000, 0x0, 0x8000]
    },  
    0x183180d0: {  # r52_0b_ctcm_global
        "system_view": [0x1, 0x18000, 0x0, 0xEBAA0000, 0x0, 0x8000],
        "rpu_view": [0x1, 0x18000, 0x0, 0x8000]
    },
    0x18318106: {  # r52_0d_atcm_global
        "system_view": [0x0, 0x0, 0x0, 0xEBC00000, 0x0, 0x10000],
        "rpu_view": [0x0, 0x0, 0x0, 0x10000]
    },  
    0x18318107: {  # r52_0d_btcm_global
        "system_view": [0x0, 0x10000, 0x0, 0xEBC10000, 0x0, 0x8000],
        "rpu_view": [0x0, 0x10000, 0x0, 0x8000]
    },  
    0x18318108: {  # r52_0d_ctcm_global
        "system_view": [0x0, 0x18000, 0x0, 0xEBC20000, 0x0, 0x8000],
        "rpu_view": [0x0, 0x18000, 0x0, 0x8000]
    },  
    0x18318109: {  # r52_1d_atcm_global
        "system_view": [0x1, 0x0, 0x0, 0xEBC40000, 0x0, 0x10000],
        "rpu_view": [0x1, 0x0, 0x0, 0x10000]
    },
    0x1831810a: {  # r52_1d_btcm_global
        "system_view": [0x1, 0x10000, 0x0, 0xEBC50000, 0x0, 0x8000],
        "rpu_view": [0x1, 0x10000, 0x0, 0x8000]
    },
    0x1831810b: {  # r52_1d_ctcm_global
        "system_view": [0x1, 0x18000, 0x0, 0xEBC60000, 0x0, 0x8000],
        "rpu_view": [0x1, 0x18000, 0x0, 0x8000]
    },
    0x1831810c: {  # r52_0e_atcm_global
        "system_view": [0x0, 0x0, 0x0, 0xEBC00000, 0x0, 0x10000],
        "rpu_view": [0x0, 0x0, 0x0, 0x10000]
    },  
    0x1831810d: {  # r52_0e_btcm_global
        "system_view": [0x0, 0x10000, 0x0, 0xEBC10000, 0x0, 0x8000],
        "rpu_view": [0x0, 0x10000, 0x0, 0x8000]
    },  
    0x1831810e: {  # r52_0e_ctcm_global
        "system_view": [0x0, 0x18000, 0x0, 0xEBC20000, 0x0, 0x8000],
        "rpu_view": [0x0, 0x18000, 0x0, 0x8000]
    },  
    0x1831810f: {  # r52_1e_atcm_global
        "system_view": [0x1, 0x0, 0x0, 0xEBC40000, 0x0, 0x10000],
        "rpu_view": [0x1, 0x0, 0x0, 0x10000]
    },
    0x18318110: {  # r52_1e_btcm_global
        "system_view": [0x1, 0x10000, 0x0, 0xEBC50000, 0x0, 0x8000],
        "rpu_view": [0x1, 0x10000, 0x0, 0x8000]
    },
    0x18318111: {  # r52_1e_ctcm_global
        "system_view": [0x1, 0x18000, 0x0, 0xEBC60000, 0x0, 0x8000],
        "rpu_view": [0x1, 0x18000, 0x0, 0x8000]
    },
}
"""dict[int, dict[str, list[int]]]: Mapping of power-domain identifiers to
system-view and RPU-view memory descriptors used for remoteproc construction."""

openamp_linux_hosts = [ "psv_cortexa72_0", "psx_cortexa78_0", "psu_cortexa53_0", "cortexa78_0" ]
"""list[str]: Names of processor nodes recognized as OpenAMP Linux hosts."""

openamp_remotes = { "psx_cortexr52_0", "psx_cortexr52_1", "psx_cortexr52_2", "psx_cortexr52_3",
 "cortexr52_0", "cortexr52_1", "cortexr52_2", "cortexr52_3", "cortexr52_4",
 "cortexr52_5", "cortexr52_6", "cortexr52_7", "cortexr52_8", "cortexr52_9",
 "psu_cortexr5_0", "psu_cortexr5_1", "psv_cortexr5_1", "psv_cortexr5_0", }
"""set[str]: Names of processor nodes supported as OpenAMP remotes."""

class SOC_TYPE:
    """Enum-like constants for supported SoC families."""

    UNINITIALIZED = -1
    VERSAL = 0
    ZYNQMP = 1
    ZYNQ = 2
    VERSAL_NET = 3
    VERSAL2 = 4

platform_info_header_r5_template = """
/*
 * Copyright (c) 2025 AMD, Inc.
 * All rights reserved.
 *
 * SPDX-License-Identifier: BSD-3-Clause
 */

#ifndef _AMD_GENERATED_H_
#define _AMD_GENERATED_H_

/* Interrupt vectors */
#if defined (FREERTOS_BSP) || defined (USE_FREERTOS)
#define IPI_IRQ_VECT_ID         $IPI_IRQ_VECT_ID_FREERTOS
#else
#define IPI_IRQ_VECT_ID         $IPI_IRQ_VECT_ID
#endif
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

$EXTRAS

#endif /* _AMD_GENERATED_H_ */
"""
"""str: Template used to generate OpenAMP R5 platform header files."""

libmetal_cmake_template = """
# ============================================================================
# AUTOGENERATED FILE — DO NOT EDIT
# Generated by Python (string.Template)
# ============================================================================

# ------------------------------
# Shared Memory Devices
# ------------------------------
set(SHM_DEV_NAME              "$SHM_DEV_NAME")
set(SHM0_DESC_DEV_NAME        "$SHM0_DESC_DEV_NAME")
set(SHM1_DESC_DEV_NAME        "$SHM1_DESC_DEV_NAME")

# ------------------------------
# Base Addresses
# ------------------------------
set(SHM_IMAGE_BASE            $SHM_IMAGE_BASE)
set(SHM0_DESC_BASE            $SHM0_DESC_BASE)
set(SHM1_DESC_BASE            $SHM1_DESC_BASE)

# ------------------------------
# Sizes
# ------------------------------
set(SHM_IMAGE_SIZE            $SHM_IMAGE_SIZE)
set(SHM0_DESC_SIZE            $SHM0_DESC_SIZE)
set(SHM1_DESC_SIZE            $SHM1_DESC_SIZE)

# ------------------------------
# Payload Configuration
# ------------------------------
set(SHM_PAYLOAD_BASE          $SHM_PAYLOAD_BASE)
set(SHM_PAYLOAD_SIZE          $SHM_PAYLOAD_SIZE)
set(SHM_PAYLOAD_HALF_SIZE     $SHM_PAYLOAD_HALF_SIZE)

set(SHM_PAYLOAD_RX_OFFSET     $SHM_PAYLOAD_RX_OFFSET)
set(SHM_PAYLOAD_TX_OFFSET     $SHM_PAYLOAD_TX_OFFSET)

# ------------------------------
# Aggregate SHM Region
# ------------------------------
set(SHM_BASE_ADDR             $SHM_BASE_ADDR)
set(SHM_SIZE                  $SHM_SIZE)

# ------------------------------
# IPI Configuration
# ------------------------------
set(IPI_DEV_NAME              "$IPI_DEV_NAME")
set(IPI_BASE_ADDR             $IPI_BASE_ADDR)
set(IPI_MASK                  $IPI_MASK)
set(IPI_IRQ_VECT_ID           $IPI_IRQ_VECT_ID)

# ------------------------------
# TTC Configuration
# ------------------------------
set(TTC_DEV_NAME              "$TTC_DEV_NAME")
set(TTC_NODEID                $TTC_NODEID)
set(TTC_BASE_ADDR            $TTC_BASE_ADDR)

# ------------------------------
# Bus Configuration
# ------------------------------
set(BUS_NAME                  "$BUS_NAME")
"""
