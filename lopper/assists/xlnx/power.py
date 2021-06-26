# /*
# * Copyright (c) 2019,2020,2021 Xilinx Inc. All rights reserved.
# *
# * Author:
# *     Ben Levinsky <ben.levinsky@xilinx.com>
# *     Izhar Shaikh <izhar.ameer.shaikh@xilinx.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

from enum import IntEnum


class REQ_USAGE(IntEnum):
    REQ_NO_RESTRICTION = 0
    REQ_SHARED = 1
    REQ_NONSHARED = 2
    REQ_TIME_SHARED = 3


# if this bit combination is on for usage offset, the meaning is as described below
req_usage_message = "Device usage policies"
req_usage = {
    REQ_USAGE.REQ_NO_RESTRICTION:
    "device accessible from all subsystem",
    REQ_USAGE.REQ_SHARED:
    "device simultaneously shared between two or more subsystems",
    REQ_USAGE.REQ_NONSHARED:
    "device exclusively reserved by one subsystem, always",
    REQ_USAGE.REQ_TIME_SHARED:
    "device is time shared between two or more subsystems",
}

usage_mask = 0x3


def usage(flags):
    msg = "#    usage: "
    msg += req_usage[flags & usage_mask]
    return msg


class REGION_SECURITY(IntEnum):
    ACCESS_FROM_SECURE = 0
    ACCESS_FROM_NONSECURE = 1


req_security_message = "Device/Memory region security status requirement per TrustZone."
req_security = {
    REGION_SECURITY.ACCESS_FROM_SECURE:
    "Device/Memory region only allows access from secure masters",
    REGION_SECURITY.ACCESS_FROM_NONSECURE:
    "Device/Memory region allow both secure or non-secure masters",
}
security_mask = 0x4
security_offset = 0x2


def security(flags):
    msg = "#    security: "
    msg += req_security[(flags & security_mask) >> security_offset]
    return msg


class RDWR_POLICY(IntEnum):
    ALLOWED = 0
    NOT_ALLOWED = 1


# this map is only applicable for memory regions
req_rd_wr_message = "Read/Write access control policy"
req_rd_wr = {
    RDWR_POLICY.ALLOWED: "Transaction allowed",
    RDWR_POLICY.NOT_ALLOWED: "Transaction not Allowed",
}
rd_policy_mask = 0x8
rd_policy_offset = 0x3
wr_policy_mask = 0x10
wr_policy_offset = 0x4
rw_message = "Read/Write access control policy."


def read_policy(flags):
    msg = "#    read policy: "
    msg += req_rd_wr[(flags & rd_policy_mask) >> rd_policy_offset]
    return msg


def write_policy(flags):
    msg = "#    write policy: "
    msg += req_rd_wr[(flags & wr_policy_mask) >> wr_policy_offset]
    return msg


nsregn_check_mask = 0x20
nsregn_check_offset = 0x5


class NSREGN_POLICY(IntEnum):
    RELAXED = 0
    STRICT = 1


nsregn_message = "Non-secure memory region check type policy."
nsregn = {
    NSREGN_POLICY.RELAXED: "RELAXED",
    NSREGN_POLICY.STRICT: "STRICT",
}


def nsregn_policy(flags):
    msg = "#    Non-secure memory region check type policy: "
    msg += nsregn[(flags & nsregn_check_mask) >> nsregn_check_offset]
    return msg


capability_offset = 0x8
capability_mask = 0x7F00

cap_message = "capability: "


def capability_policy(flags):
    msg = "#    Capability policy: "
    msg += hex((flags & capability_mask) >> capability_offset)
    return msg


prealloc_offset = 6
prealloc_mask = (0x1 << 6)


class PREALLOC(IntEnum):
    NOT_REQUIRED = 0
    REQUIRED = 1


prealloc = {
    PREALLOC.NOT_REQUIRED: "prealloc not required",
    PREALLOC.REQUIRED: "prealloc required",
}

prealloc_message = "prealloc policy "


def prealloc_policy(flags):
    msg = "#    Preallocation policy: "
    msg += prealloc[(flags & prealloc_mask) >> prealloc_offset]
    return msg


def prealloc_detailed_policy(flags):
    msg = "#    Preallocation detailed: "
    caps = [
        "full access", "preserve context", "emit wake interrupts",
        "not usable", "secure access", "coherent access", "virtualized access"
    ]
    for index, s in enumerate(caps):
        match = ((0x1 << index) & flags) >> index
        if match == 1:
            msg += " " + s
    return msg


class Requirement:
    def __init__(self, subsystem, node, prealloc, capability, nsregn_policy,
                 read_policy, write_policy, security, usage):
        self.prealloc = prealloc
        self.capability = capability
        self.nsregn_policy = nsregn_policy
        self.read_policy = read_policy
        self.write_policy = write_policy
        self.security = security
        self.usage = usage
        self.subsystem = subsystem
        self.node = node


def mem_regn_node(node_id):
    return ((0x3F << 20) & node_id) == 0x300000


misc_devices = {
    "mailbox@ff320000": "PM_DEV_IPI_0",
    "mailbox@ff390000": "PM_DEV_IPI_1",
    "mailbox@ff310000": "PM_DEV_IPI_2",
    "mailbox@ff330000": "PM_DEV_IPI_3",
    "mailbox@ff340000": "PM_DEV_IPI_4",
    "mailbox@ff350000": "PM_DEV_IPI_5",
    "mailbox@ff360000": "PM_DEV_IPI_6",
    "watchdog@ff120000": "PM_DEV_SWDT_LPD",
}

xlnx_pm_mem_node_to_base = {
    "PM_DEV_OCM_0": 0xff960000,
    "PM_DEV_OCM_1": 0xff960000,
    "PM_DEV_OCM_2": 0xff960000,
    "PM_DEV_OCM_3": 0xff960000,
    "PM_DEV_TCM_0_A": 0xffe00000,
    "PM_DEV_TCM_0_B": 0xffe20000,
    "PM_DEV_TCM_1_A": 0xffe90000,
    "PM_DEV_TCM_1_B": 0xffeb0000,
}

xlnx_pm_devname_to_id = {
    "PM_DEV_PLD_0": 0x18700000,
    "PM_DEV_PMC_PROC": 0x18104001,
    "PM_DEV_PSM_PROC": 0x18108002,
    "PM_DEV_ACPU_0": 0x1810c003,
    "PM_DEV_ACPU_1": 0x1810c004,
    "PM_DEV_RPU0_0": 0x18110005,
    "PM_DEV_RPU0_1": 0x18110006,
    "PM_DEV_OCM_0": 0x18314007,
    "PM_DEV_OCM_1": 0x18314008,
    "PM_DEV_OCM_2": 0x18314009,
    "PM_DEV_OCM_3": 0x1831400a,
    "PM_DEV_TCM_0_A": 0x1831800b,
    "PM_DEV_TCM_0_B": 0x1831800c,
    "PM_DEV_TCM_1_A": 0x1831800d,
    "PM_DEV_TCM_1_B": 0x1831800e,
    "PM_DEV_L2_BANK_0": 0x1831c00f,
    "PM_DEV_DDR_0": 0x18320010,
    "PM_DEV_USB_0": 0x18224018,
    "PM_DEV_GEM_0": 0x18224019,
    "PM_DEV_GEM_1": 0x1822401a,
    "PM_DEV_SPI_0": 0x1822401b,
    "PM_DEV_SPI_1": 0x1822401c,
    "PM_DEV_I2C_0": 0x1822401d,
    "PM_DEV_I2C_1": 0x1822401e,
    "PM_DEV_CAN_FD_0": 0x1822401f,
    "PM_DEV_CAN_FD_1": 0x18224020,
    "PM_DEV_UART_0": 0x18224021,
    "PM_DEV_UART_1": 0x18224022,
    "PM_DEV_GPIO": 0x18224023,
    "PM_DEV_TTC_0": 0x18224024,
    "PM_DEV_TTC_1": 0x18224025,
    "PM_DEV_TTC_2": 0x18224026,
    "PM_DEV_TTC_3": 0x18224027,
    "PM_DEV_SWDT_LPD": 0x18224028,
    "PM_DEV_SWDT_FPD": 0x18224029,
    "PM_DEV_OSPI": 0x1822402a,
    "PM_DEV_QSPI": 0x1822402b,
    "PM_DEV_GPIO_PMC": 0x1822402c,
    "PM_DEV_I2C_PMC": 0x1822402d,
    "PM_DEV_SDIO_0": 0x1822402e,
    "PM_DEV_SDIO_1": 0x1822402f,
    "PM_DEV_RTC": 0x18224034,
    "PM_DEV_ADMA_0": 0x18224035,
    "PM_DEV_ADMA_1": 0x18224036,
    "PM_DEV_ADMA_2": 0x18224037,
    "PM_DEV_ADMA_3": 0x18224038,
    "PM_DEV_ADMA_4": 0x18224039,
    "PM_DEV_ADMA_5": 0x1822403a,
    "PM_DEV_ADMA_6": 0x1822403b,
    "PM_DEV_ADMA_7": 0x1822403c,
    "PM_DEV_IPI_0": 0x1822403d,
    "PM_DEV_IPI_1": 0x1822403e,
    "PM_DEV_IPI_2": 0x1822403f,
    "PM_DEV_IPI_3": 0x18224040,
    "PM_DEV_IPI_4": 0x18224041,
    "PM_DEV_IPI_5": 0x18224042,
    "PM_DEV_IPI_6": 0x18224043,
    "PM_DEV_SOC": 0x18428044,
    "PM_DEV_DDRMC_0": 0x18520045,
    "PM_DEV_DDRMC_1": 0x18520046,
    "PM_DEV_DDRMC_2": 0x18520047,
    "PM_DEV_DDRMC_3": 0x18520048,
    "PM_DEV_GT_0": 0x1862c049,
    "PM_DEV_GT_1": 0x1862c04a,
    "PM_DEV_GT_2": 0x1862c04b,
    "PM_DEV_GT_3": 0x1862c04c,
    "PM_DEV_GT_4": 0x1862c04d,
    "PM_DEV_GT_5": 0x1862c04e,
    "PM_DEV_GT_6": 0x1862c04f,
    "PM_DEV_GT_7": 0x1862c050,
    "PM_DEV_GT_8": 0x1862c051,
    "PM_DEV_GT_9": 0x1862c052,
    "PM_DEV_GT_10": 0x1862c053,
    "PM_DEV_EFUSE_CACHE": 0x18330054,
    "PM_DEV_AMS_ROOT": 0x18224055,
    "PM_DEV_AIE": 0x18224072,
    "PM_DEV_IPI_PMC": 0x18224073,
}

xlnx_pm_devid_to_name = {
    0x18700000: 'PM_DEV_PLD_0',
    0x18104001: 'PM_DEV_PMC_PROC',
    0x18108002: 'PM_DEV_PSM_PROC',
    0x1810c003: 'PM_DEV_ACPU_0',
    0x1810c004: 'PM_DEV_ACPU_1',
    0x18110005: 'PM_DEV_RPU0_0',
    0x18110006: 'PM_DEV_RPU0_1',
    0x18314007: 'PM_DEV_OCM_0',
    0x18314008: 'PM_DEV_OCM_1',
    0x18314009: 'PM_DEV_OCM_2',
    0x1831400a: 'PM_DEV_OCM_3',
    0x1831800b: 'PM_DEV_TCM_0_A',
    0x1831800c: 'PM_DEV_TCM_0_B',
    0x1831800d: 'PM_DEV_TCM_1_A',
    0x1831800e: 'PM_DEV_TCM_1_B',
    0x1831c00f: 'PM_DEV_L2_BANK_0',
    0x18320010: 'PM_DEV_DDR_0',
    0x18224018: 'PM_DEV_USB_0',
    0x18224019: 'PM_DEV_GEM_0',
    0x1822401a: 'PM_DEV_GEM_1',
    0x1822401b: 'PM_DEV_SPI_0',
    0x1822401c: 'PM_DEV_SPI_1',
    0x1822401d: 'PM_DEV_I2C_0',
    0x1822401e: 'PM_DEV_I2C_1',
    0x1822401f: 'PM_DEV_CAN_FD_0',
    0x18224020: 'PM_DEV_CAN_FD_1',
    0x18224021: 'PM_DEV_UART_0',
    0x18224022: 'PM_DEV_UART_1',
    0x18224023: 'PM_DEV_GPIO',
    0x18224024: 'PM_DEV_TTC_0',
    0x18224025: 'PM_DEV_TTC_1',
    0x18224026: 'PM_DEV_TTC_2',
    0x18224027: 'PM_DEV_TTC_3',
    0x18224028: 'PM_DEV_SWDT_LPD',
    0x18224029: 'PM_DEV_SWDT_FPD',
    0x1822402a: 'PM_DEV_OSPI',
    0x1822402b: 'PM_DEV_QSPI',
    0x1822402c: 'PM_DEV_GPIO_PMC',
    0x1822402d: 'PM_DEV_I2C_PMC',
    0x1822402e: 'PM_DEV_SDIO_0',
    0x1822402f: 'PM_DEV_SDIO_1',
    0x18224034: 'PM_DEV_RTC',
    0x18224035: 'PM_DEV_ADMA_0',
    0x18224036: 'PM_DEV_ADMA_1',
    0x18224037: 'PM_DEV_ADMA_2',
    0x18224038: 'PM_DEV_ADMA_3',
    0x18224039: 'PM_DEV_ADMA_4',
    0x1822403a: 'PM_DEV_ADMA_5',
    0x1822403b: 'PM_DEV_ADMA_6',
    0x1822403c: 'PM_DEV_ADMA_7',
    0x1822403d: 'PM_DEV_IPI_0',
    0x1822403e: 'PM_DEV_IPI_1',
    0x1822403f: 'PM_DEV_IPI_2',
    0x18224040: 'PM_DEV_IPI_3',
    0x18224041: 'PM_DEV_IPI_4',
    0x18224042: 'PM_DEV_IPI_5',
    0x18224043: 'PM_DEV_IPI_6',
    0x18428044: 'PM_DEV_SOC',
    0x18520045: 'PM_DEV_DDRMC_0',
    0x18520046: 'PM_DEV_DDRMC_1',
    0x18520047: 'PM_DEV_DDRMC_2',
    0x18520048: 'PM_DEV_DDRMC_3',
    0x1862c049: 'PM_DEV_GT_0',
    0x1862c04a: 'PM_DEV_GT_1',
    0x1862c04b: 'PM_DEV_GT_2',
    0x1862c04c: 'PM_DEV_GT_3',
    0x1862c04d: 'PM_DEV_GT_4',
    0x1862c04e: 'PM_DEV_GT_5',
    0x1862c04f: 'PM_DEV_GT_6',
    0x1862c050: 'PM_DEV_GT_7',
    0x1862c051: 'PM_DEV_GT_8',
    0x1862c052: 'PM_DEV_GT_9',
    0x1862c053: 'PM_DEV_GT_10',
    0x18330054: 'PM_DEV_EFUSE_CACHE',
    0x18224055: 'PM_DEV_AMS_ROOT',
    0x18224072: 'PM_DEV_AIE',
    0x18224073: 'PM_DEV_IPI_PMC',
}
