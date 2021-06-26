# /*
# * Copyright (c) 2021 Xilinx Inc. All rights reserved.
# *
# * Author:
# *       Izhar Shaikh <izhar.ameer.shaikh@xilinx.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

from collections import OrderedDict

nodes = dict()
debug = False

# Constants
MID_PARITY = True  # Set parity true for masters
APER_PARITY = True  # Set parity true for apertures
TZ = True  # Set trustzone true for default masters
RO = 1  # read only
RW = 0  # read/write okay

# Interrupts
Interrupts = {
    "IEN_APER_PARITY": [True, 7],  # Enable Parity Error Interrupt
    "IEN_APER_TZ": [True, 6],  # Enable TrustZone Violation Interrupt.
    "IEN_APER_PERM": [True, 5],  # Enable Master ID Access Violation Interrupt.
    "IEN_MID_PARITY": [True, 3],  # Enable Master ID Parity Error Interrupt.
    "IEN_MID_RO": [True, 2],  # Enable Read permission Violation Interrupt.
    "IEN_MID_MISS": [True, 1],  # Enable Master ID MISS Interrupt.
    "IEN_INV_APB": [True, 0],  # Enable APB Register Access Error Interrupt.
}

# Aper size constants
SIZE_64K = 64 * 1024  # 64k
SIZE_1MB = 1024 * 1024  # 1MB
SIZE_512MB = 512 * SIZE_1MB  # 512MB

# Aper start offsets
APER_64K_START = 0  # 0
APER_64K_END = 255  # 255
APER_1MB_START = 384  # 384
APER_1MB_END = 399  # 399
APER_512MB = 400  # 400

# Total masters
MASTERS = 20  # 20 Masters

# Aper Address start
APER_ADDR_START = 0x1000

# Master ID List (Global)
MIDL = {
    "PPU0": [0x246, 0x3FF],
    "PPU1": [0x247, 0x3FF],
    "DAP": [0x240, 0x3FF],
    "HSDP_DPC": [0x249, 0x3FF],
    "PMC_DMA0": [0x248, 0x3FF],
    "PMC_DMA1": [0x24B, 0x3FF],
    "APU": [0x260, 0x3F0],
    "APU0": [0x260, 0x3FF],
    "APU1": [0x261, 0x3FF],
    "RPU0": [0x200, 0x3FF],
    "RPU1": [0x204, 0x3FF],
    "APU0_RO": [0x260, 0x3FF],
    "APU1_RO": [0x261, 0x3FF],
    "RPU0_RO": [0x200, 0x3FF],
    "RPU1_RO": [0x204, 0x3FF],
    "PSM": [0x238, 0x3FF],
    # -- #
    "ANY_RO": [0x0, 0x0],
    "ANY_RW": [0x0, 0x0],
}

# default masters list
DEF_MASTERS = ["PPU0", "PPU1", "PSM", "DAP", "HSDP_DPC", "PMC_DMA0", "PMC_DMA1"]

# Xppu offsets
mid_offset_start = 0x100  # Master IDXX Start offset

# Apertures
range_64k = 256 * SIZE_64K  # 256 64k
range_1m = 16 * SIZE_1MB  # 16 1mb
range_512m = 512 * SIZE_1MB  # 1 512mb

# XPPU modules
xppu_hw = {
    # Name          # base      # 64k base  # 1mb base  # 512mb base # xppu pm id
    "pmc_xppu": (0xF1310000, 0xF1000000, 0xF0000000, 0xC0000000, 0x24000002),
    "pmc_xppu_npi": (0xF1300000, 0xF6000000, 0xF7000000, None, 0x24000003),
    "lpd_xppu": (0xFF990000, 0xFF000000, 0xFE000000, 0xE0000000, 0x24000001),
}


def h2i(string):
    if string is None:
        return 0
    return int(string, 16)


def i2h(num):
    return hex(num)


def calc_parity(num):
    num ^= num >> 16
    num ^= num >> 8
    num ^= num >> 4
    num ^= num >> 2
    num ^= num >> 1
    return num & 1


class MasterId:
    """XPPU/IPI MASTER_IDXX"""

    def __init__(self, name, mid, midm, rw, parity):
        self.name = name  # Master name string
        self.mid = hex(mid)  # Master Id (SMID)
        self.mask = hex(midm)  # Master Id Mask
        self.rw = int(rw)  # 0: read/write, 1: read only
        self.parity = parity  # 0: disable, 1: enable

    def __str__(self):
        return str(self.__class__) + ": " + str(self.__dict__)

    def comp_parity(self):
        if self.parity is False:
            return 0
        else:
            pmid = calc_parity(h2i(self.mid))
            pmask = calc_parity(h2i(self.mask))
            prw = self.rw & 1
            num = (prw << 2) | (pmask << 1) | (pmid << 0)
            # print(self.parity, pmid, pmask, prw, num)
            return calc_parity(num)

    def val(self):
        r = int(self.rw) << 30
        midm = int(self.mask, 16) << 16
        mid = int(self.mid, 16)
        p = self.comp_parity() << 31
        v = p + r + midm + mid
        # print (hex(p), hex(r), hex(midm), hex(mid))
        # print (hex(v))
        return hex(v)


def in_range(saddr, smin, smax):
    haddr = h2i(saddr)
    hmin = h2i(smin)
    hmax = h2i(smax)
    # print('{0} >= {1} and {0} <= {2}'.format(saddr, smin, smax))
    res = True if (haddr >= hmin) and (haddr <= hmax) else False
    return res


def calc_aperture(saddr, sbase, hsize):
    haddr = h2i(saddr)
    hbase = h2i(sbase)
    return (haddr - hbase) / (hsize)


class Xppu:
    """Xppu base class"""

    def __init__(self, name, addr, b64k, b1m, b512m, pm_id):
        self.name = name
        self.baseaddr = hex(addr)
        self.b64k_start = hex(b64k)
        self.b64k_end = hex(b64k + range_64k - 1)
        self.b1m_start = hex(b1m)
        self.b1m_end = hex(b1m + range_1m - 1)
        self.b512m_start = "0x0" if b512m is None else hex(b512m)
        self.b512m_end = "0x0" if b512m is None else hex(b512m + range_512m - 1)
        self.pm_id = hex(pm_id)
        self.def_apermask = 0
        self.masters = OrderedDict()
        # initialize aperture address map
        self.apermap = dict()
        for aper in range(APER_512MB + 1):
            if aper <= APER_64K_END or aper >= APER_1MB_START:
                self.apermap[aper] = self.aper_get_protected_range(aper)
        # masters
        for m in range(mid_offset_start, mid_offset_start + (4 * MASTERS), 0x4):
            self.masters[m] = None

    def __str__(self):
        return str(self.__class__) + ": " + str(self.__dict__)

    def is_addr_in_range(self, addr):
        if in_range(addr, self.b64k_start, self.b64k_end):
            return (True, 0, self.calc_64k_aper_index(addr))
        elif in_range(addr, self.b1m_start, self.b1m_end):
            return (True, 1, self.calc_1mb_aper_index(addr))
        elif in_range(addr, self.b512m_start, self.b512m_end):
            return (True, 2, APER_512MB)
        else:
            return (False, None, None)

    def calc_64k_aper_index(self, addr):
        return APER_64K_START + calc_aperture(addr, self.b64k_start, SIZE_64K)

    def calc_1mb_aper_index(self, addr):
        return APER_1MB_START + calc_aperture(addr, self.b1m_start, SIZE_1MB)

    def aper_to_addr(self, aper_idx):
        return h2i(self.baseaddr) + APER_ADDR_START + (aper_idx * 0x4)

    def aper_get_protected_range(self, aper_idx):
        if aper_idx >= APER_64K_START and aper_idx <= APER_64K_END:
            start = h2i(self.b64k_start) + aper_idx * SIZE_64K
            end = start + SIZE_64K - 1
        elif aper_idx >= APER_1MB_START and aper_idx <= APER_1MB_END:
            start = h2i(self.b1m_start) + (aper_idx - APER_1MB_START) * SIZE_1MB
            end = start + SIZE_1MB - 1
        elif aper_idx == APER_512MB:
            start = h2i(self.b512m_start)
            end = h2i(self.b512m_end)
        else:
            sys.exit("Fatal Error!")
        return (start, end)

    def set_master(self, idx, master):
        k = mid_offset_start + (idx * 4)
        if k in self.masters:
            self.masters[k] = master
        # print(hex(k), master)

    def get_master(self, idx):
        k = mid_offset_start + (idx * 4)
        if k in self.masters:
            return self.masters[k]

    def get_master_by_smid(self, mid):
        for m in self.masters:
            if self.masters[m] is not None:
                if h2i(self.masters[m].mid) == mid:
                    return int((m - mid_offset_start) / 4)
        return None

    def get_master_by_smrw(self, mid, mask, rw):
        for m in self.masters:
            if self.masters[m] is not None:
                m_inst = self.masters[m]
                if (
                    h2i(m_inst.mid) == mid
                    and h2i(m_inst.mask) == mask
                    and m_inst.rw == rw
                ):
                    return int((m - mid_offset_start) / 4)
        return None

    def get_master_addr(self, idx):
        k = mid_offset_start + (idx * 4)
        if k in self.masters:
            return hex(int(self.baseaddr, 16) + k)

    def get_master_val(self, idx):
        k = mid_offset_start + (idx * 4)
        if k in self.masters:
            return 0 if self.masters[k] is None else self.masters[k].val()

    def mid_name_to_idxx(self, name):
        return list(self.masters).index(name)

    def get_ctrl_reg_val(self):
        return hex((APER_PARITY << 2) | (MID_PARITY << 1))

    def get_ien_reg_addr_val(self):
        reg_addr = h2i(self.baseaddr) + 0x18
        reg_val = 0
        for istr, ival in Interrupts.items():
            reg_val = reg_val | (ival[0] << ival[1])
        return hex(reg_addr), hex(reg_val)

    def get_aperture(self, master_list):
        aperture = 0
        for pos, m in self.masters.items():
            if m is not None and m.name in master_list:
                pos = self.mid_name_to_idxx(pos)
                aperture = aperture | (1 << pos)
            aperture = aperture | (TZ << 27)  # TrustZone
        return aperture

    def set_default_aperture(self, mask):
        self.def_apermask = mask
        # print("SET:", self.name, self.def_apermask)

    def get_default_aperture(self):
        # return hex(self.get_aperture(DEF_MASTERS))
        # print("GET:", self.name, self.def_apermask)
        return hex(self.def_apermask)

    def get_nondef_rw_aperture(self):
        def_aperture = int(self.get_default_aperture(), 16)
        return hex(def_aperture | self.get_aperture(["ANY_RW"]))

    def get_nondef_ro_aperture(self):
        def_aperture = int(self.get_default_aperture(), 16)
        return hex(def_aperture | self.get_aperture(["ANY_RO"]))

    def get_master_list_from_aperture(self, aper_mask):
        midx = (i for i in range(20) if ((aper_mask >> i) & 0x1) == 1)
        mid_list = [self.masters[list(self.masters)[idx]].name for idx in midx]
        return mid_list[::-1]


def mid(master, rw, parity):
    if master in MIDL:
        mid = MIDL[master][0]
        midm = MIDL[master][1]
    else:
        print("Error: missing MID in the list!")
    return MasterId(master, mid, midm, rw, parity)


def init_masters(xppu):
    xppu.set_master(0, mid("PSM", RW, MID_PARITY))  # PSM
    xppu.set_master(1, mid("RPU0", RW, MID_PARITY))  # RPU0
    xppu.set_master(2, mid("RPU1", RW, MID_PARITY))  # RPU1
    # xppu.set_master(3,  mid('APU',       RW, MID_PARITY))  # APU
    xppu.set_master(3, mid("APU0", RW, MID_PARITY))  # APU0
    xppu.set_master(4, mid("APU1", RW, MID_PARITY))  # APU1
    xppu.set_master(5, mid("HSDP_DPC", RW, MID_PARITY))  # HSDP_DPC
    xppu.set_master(6, mid("DAP", RW, MID_PARITY))  # DAP
    xppu.set_master(7, mid("PPU1", RW, MID_PARITY))  # PPU1 (PMC)
    xppu.set_master(8, mid("PPU0", RW, MID_PARITY))  # PPU0
    xppu.set_master(9, mid("PMC_DMA0", RW, MID_PARITY))  # PMC DMA0
    xppu.set_master(10, mid("PMC_DMA1", RW, MID_PARITY))  # PMC DMA1
    # -- RO (APU)
    xppu.set_master(11, mid("APU0_RO", RO, MID_PARITY))  # APU0 (RO)
    xppu.set_master(12, mid("APU1_RO", RO, MID_PARITY))  # APU1 (RO)
    # -- RO (RPU)
    xppu.set_master(13, mid("RPU0_RO", RO, MID_PARITY))  # RPU0
    xppu.set_master(14, mid("RPU1_RO", RO, MID_PARITY))  # RPU1
    # Skip -- the middle entries for now
    xppu.set_master(18, mid("ANY_RO", RO, MID_PARITY))  # Any master (RO)
    xppu.set_master(19, mid("ANY_RW", RW, MID_PARITY))  # Any master (RW)


def print_masters(xppu):
    for m in range(MASTERS):
        print(xppu.get_master_addr(m), xppu.get_master_val(m))


def print_apertures(xppu):
    def_aper = xppu.get_default_aperture()
    rw_aper = xppu.get_nondef_rw_aperture()
    ro_aper = xppu.get_nondef_ro_aperture()
    print("def_aper:", def_aper, "rw_aper:", rw_aper, "ro_aper:", ro_aper)
    # for k, v in xppu.apermap.items():
    #    print('{0} : {1} - {2}'.format(k, hex(v[0]), hex(v[1])))


def init_xppu(name):
    x = xppu_hw[name]
    nodes[name] = Xppu(name, x[0], x[1], x[2], x[3], x[4])
    return nodes[name]


def init_instances():
    for ppu in nodes:
        init_masters(nodes[ppu])
        if debug:
            # print(nodes[ppu])
            print_masters(nodes[ppu])
            print_apertures(nodes[ppu])
