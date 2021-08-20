# /*
# * Copyright (c) 2021 Xilinx Inc. All rights reserved.
# *
# * Author:
# *       Izhar Shaikh <izhar.ameer.shaikh@xilinx.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

from collections import OrderedDict
from operator import ior
from functools import reduce

nodes = dict()
debug = False

# Constants
NS_CHECK_TYPE = False  # Region checking policy
TZ = True  # Set trustzone true for default masters
RD = 1  # read okay
WR = 1  # read/write okay

DEF_RD_ALLOWED = False
DEF_WR_ALLOWED = False

# Interrupts
Interrupts = {
    "IEN_SECURITY_VIO": [True, 3],  # Enable Security violation interrupt
    "IEN_WRPERM_VIO": [True, 2],  # Enable Write permission violation interrupt
    "IEN_RDPERM_VIO": [True, 1],  # Enable Read permission Violation interrupt
    "IEN_INV_APB": [True, 0],  # Enable Register Access interrupt on APB
}

# Total regions
REGIONS = 16  # 16 Regions

# Aper Address start
REGION_0_START = 0x100
REGION_STEP_SIZE = 0x18

# REGION offsets
REGN_X_START_LO_OFFSET = 0x0
REGN_X_START_HI_OFFSET = 0x4
REGN_X_END_LO_OFFSET = 0x8
REGN_X_END_HI_OFFSET = 0xC
REGN_X_MASTER_OFFSET = 0x10
REGN_X_CONFIG_OFFSET = 0x14

# Interrupt
IEN_OFFSET = 0x18

OFFSETS = {
    "s_lo": REGN_X_START_LO_OFFSET,
    "s_hi": REGN_X_START_HI_OFFSET,
    "e_lo": REGN_X_END_LO_OFFSET,
    "e_hi": REGN_X_END_HI_OFFSET,
    "master": REGN_X_MASTER_OFFSET,
    "config": REGN_X_CONFIG_OFFSET,
}

# Master ID List (Global)
MIDL = {
    "PPU0": [0x246, 0x3FF],
    "PPU1": [0x247, 0x3FF],
    "PPU0/PPU1": [0x246, 0x3FE],
    "DAP": [0x240, 0x3FF],
    "HSDP_DPC": [0x249, 0x3FF],
    "PMC_DMA0": [0x248, 0x3FF],
    "PMC_DMA1": [0x24B, 0x3FF],
    "APU": [0x260, 0x3F0],
    "APU0": [0x260, 0x3FF],
    "APU1": [0x261, 0x3FF],
    "RPU0": [0x200, 0x3FF],
    "RPU1": [0x204, 0x3FF],
    "PSM": [0x238, 0x3FF],
    # -- #
    "ANY": [0x0, 0x0],
}

# default masters list
DEF_MASTERS = [
    "PPU0", "PPU1", "PSM", "DAP", "HSDP_DPC", "PMC_DMA0", "PMC_DMA1"
]


def h2i(string):
    if string is None:
        return 0
    return int(string, 16)


def i2h(num):
    return hex(num)


def dword(num):
    return (num & 0xffffffff)


class Master:
    """XMPU MASTER_IDXX"""
    def __init__(self, mid, midm, name=''):
        self.name = name  # Master name string
        self.mid = mid  # Master Id (SMID)
        self.mask = midm  # Master Id Mask

    def __str__(self):
        return str(self.__class__) + ": " + str(self.__dict__)

    def val(self):
        midm = self.mask << 16
        mid = self.mid
        return midm + mid

    def val_s(self):
        return hex(self.val())


class Config:
    """ XMPU Region Config """
    def __init__(self, check_type, tz, wr_allowed, rd_allowed, enable):
        self.check_type = check_type
        self.tz = tz
        self.wr_allowed = wr_allowed
        self.rd_allowed = rd_allowed
        self.enable = enable

    def __str__(self):
        return str(self.__class__) + ": " + str(self.__dict__)

    def val(self):
        bits = [
            self.enable, self.rd_allowed, self.wr_allowed, self.tz,
            self.check_type
        ]
        bitv = (bit << pos for pos, bit in enumerate(bits))
        return reduce(ior, bitv)

    def set_tz(self, tz):
        self.tz = tz

    def set_wr_allowed(self, wr_allowed):
        self.wr_allowed = wr_allowed

    def set_rd_allowed(self, rd_allowed):
        self.rd_allowed = rd_allowed

    def set_rw(self, rw):
        if rw == 0:
            self.set_wr_allowed(1)
            self.set_rd_allowed(1)
        elif rw == 1:
            self.set_rd_allowed(1)
        elif rw == 2:
            self.set_wr_allowed(1)
        else:
            return

    def en(self):
        self.enable = 1

    def disable(self):
        self.enable = 0


class Region:
    """ XMPU Regions """
    def __init__(self, idx, offset):
        self.idx = idx
        self.offset = offset
        self.addr_start = 0
        self.addr_end = 0
        self.master = Master(0, 0)
        self.config = Config(NS_CHECK_TYPE, TZ, WR, RD, 0)

    def __str__(self):
        return str(self.__class__) + ": " + str(self.__dict__)

    def set_master(self, master: Master):
        self.master.mid = master.mid
        self.master.mask = master.mask
        self.master.name = master.name

    def set_addr(self, addr, size):
        self.addr_start = addr
        self.addr_end = addr + size

    def get_offset(self, offkey):
        return self.offset + OFFSETS[offkey]

    def get_master(self):
        return self.get_offset("master"), dword(self.master.val())

    def get_config(self):
        return self.get_offset("config"), dword(self.config.val())

    def get_start_lo(self):
        return self.get_offset("s_lo"), dword(self.addr_start)

    def get_start_hi(self):
        return self.get_offset("s_hi"), dword(self.addr_start >> 32)

    def get_end_lo(self):
        return self.get_offset("e_lo"), dword(self.addr_end)

    def get_end_hi(self):
        return self.get_offset("e_hi"), dword(self.addr_end >> 32)


class Xmpu:
    """Xmpu base class"""
    def __init__(self, name, addr, size):
        self.name = name
        self.baseaddr = addr
        self.size = size
        self.current = 0
        # initialize regions map
        self.regions = {
            i: Region(i, REGION_0_START + (i * REGION_STEP_SIZE))
            for i in range(REGIONS)
        }

    def __str__(self):
        return str(self.__class__) + ": " + str(self.__dict__)

    def set_master(self, idx, master: Master):
        if idx < len(self.regions):
            self.regions[idx].set_master(master)
        else:
            print("[ERROR] xmpu region idx {} too big (max: {})".format(
                idx, REGIONS))
            return

    def set_addr(self, idx, addr, size):
        if idx < len(self.regions):
            self.regions[idx].set_addr(addr, size)
        else:
            print("[ERROR] xmpu region idx {} too big (max: {})".format(
                idx, REGIONS))
            return

    def set_config(self, idx, tz, rw):
        if idx < len(self.regions):
            self.regions[idx].config.set_tz(tz)
            self.regions[idx].config.set_rw(rw)
        else:
            print("[ERROR] xmpu region idx {} too big (max: {})".format(
                idx, REGIONS))
            return

    def enable_region(self, idx):
        if idx < len(self.regions):
            self.regions[idx].config.en()
        else:
            print("[ERROR] xmpu region idx {} too big (max: {})".format(
                idx, REGIONS))
            return

    def create_region_and_en(self, addr, size, smid, mask, rw, tz, name=''):
        if self.current >= REGIONS:
            print("[ERROR] {} xmpu regions full!".format(self.name))
            return False

        self.set_addr(self.current, addr, size)
        self.set_master(self.current, Master(smid, mask, name=name))
        self.set_config(self.current, tz, rw)
        self.enable_region(self.current)
        self.current += 1
        return True

    def is_filled(self):
        return self.current < REGIONS

    def get_ctrl_reg_addr_val(self):
        return hex(self.baseaddr), hex((DEF_WR_ALLOWED << 1)
                                       | (DEF_RD_ALLOWED))

    def get_ien_reg_addr_val(self):
        reg_addr = self.baseaddr + IEN_OFFSET
        reg_val = 0
        for istr, ival in Interrupts.items():
            reg_val = reg_val | (ival[0] << ival[1])
        return hex(reg_addr), hex(reg_val)


def mid(master):
    if master in MIDL:
        mid = MIDL[master][0]
        midm = MIDL[master][1]
    else:
        print("Error: missing MID in the list!")
    return Master(mid, midm, name=master)


def init_masters(xmpu):
    xmpu.set_master(15, mid("PPU0/PPU1"))  # PPU0/PPU1 (PMC)
    xmpu.set_master(14, mid("PSM"))  # PSM
    xmpu.set_master(13, mid("PMC_DMA0"))  # PMC DMA0
    xmpu.set_master(12, mid("PMC_DMA1"))  # PMC DMA1
    xmpu.set_master(11, mid("DAP"))  # DAP
    xmpu.set_master(10, mid("HSDP_DPC"))  # HSDP_DPC
    # Skip -- the middle entries for now
    xmpu.set_master(0, mid("ANY"))  # Any master


def init_xmpu(name, reg):
    base = (reg[0] << 32) | reg[1]
    size = (reg[2] << 32) | reg[3]
    nodes[name] = Xmpu(name, base, size)
    return nodes[name]


def init_instances():
    for mpu, inst in nodes.items():
        init_masters(inst)

        if debug:
            print(inst)
            print(inst.get_ctrl_reg_addr_val())
            print(inst.get_ien_reg_addr_val())
            for i, r in inst.regions.items():
                print(r)
                print(r.master)
                print(r.get_start_lo())
                print(r.get_end_lo())
                print(r.get_start_hi())
                print(r.get_end_hi())
                print(r.get_master())
                print(r.get_config())
