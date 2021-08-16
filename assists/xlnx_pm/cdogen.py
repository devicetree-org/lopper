# /*
# * Copyright (c) 2021 Xilinx Inc. All rights reserved.
# *
# * Author:
# *       Izhar Shaikh <izhar.ameer.shaikh@xilinx.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

import xppu
from lopper import Lopper
import lopper
from lopper_tree import *

sys.path.append(os.path.dirname(__file__))


def write_cmd(addr, val):
    cmd = "write {0} {1}".format(addr, val)
    return cmd


def write_cmd_print(addr, val, fp=None):
    print(write_cmd(hex(addr), hex(val)), file=fp)


def mask_write_cmd(addr, mask, val):
    cmd = "mask_write {0} {1} {2}".format(addr, mask, val)
    return cmd


def pm_init_node_cmd1(pm_id, val):
    cmd = "pm_init_node {0} {1} {2}".format(pm_id, hex(0x1), val)
    return cmd


def pm_init_node_cmd2(pm_id, aper, val):
    cmd = "pm_init_node {0} {1} {2} {3}".format(pm_id, hex(0x2), hex(aper),
                                                val)
    return cmd


def write_header(x, fp=None):
    print("marker", hex(0x64), '"{}"'.format(x.name.upper()), file=fp)
    cmt = "# {0} : Setup START".format(x.name.upper())
    print(cmt, file=fp)


def write_footer(x, fp=None):
    cmt = "# {0} : Setup END".format(x.name.upper())
    print(cmt, file=fp)
    print("marker", hex(0x65), '"{}"'.format(x.name.upper()), file=fp)


def write_xppu_mid_list(x, fp=None):
    for i in range(xppu.MASTERS):
        master = x.get_master(i)
        if master is None:
            continue
        cmt = "# {0} XPPU: Set Master ID {1} to {2}".format(
            x.name, i, master.name)
        cmt += "\n# [MID: {0}, Mask: {1}, {2}, Parity: {3}]".format(
            master.mid,
            master.mask,
            "Read Only" if master.rw == 1 else "Read/Write",
            "Enable" if master.parity == 1 else "Disable",
        )
        cmd = write_cmd(x.get_master_addr(i), x.get_master_val(i))
        print(cmt, file=fp)
        print(cmd, file=fp)


def write_xppu_ctrl_reg(x, fp=None):
    cmt = "# Parity error checking for Aperture and Master ID entries\n"
    cmt += "# APER_PARITY_EN = {0}\n".format(xppu.APER_PARITY)
    cmt += "# MID_PARITY_EN = {0}".format(xppu.MID_PARITY)
    val = x.get_ctrl_reg_val()
    cmd = mask_write_cmd(x.baseaddr, val, val)
    print(cmt, file=fp)
    print(cmd, file=fp)


def write_xppu_ien_reg(x, fp=None):
    cmt = "# Interrupt Enable\n"
    for istr, ival in xppu.Interrupts.items():
        cmt += "# {0} = {1}\n".format(istr.upper(), ival[0])
    addr, val = x.get_ien_reg_addr_val()
    cmd = write_cmd(addr, val)
    print(cmt + cmd, file=fp)


def write_xppu_en_cmd(x, fp=None):
    cmt = "# Enable {0} XPPU\n".format(x.name)
    cmt += "# Initialize all apertures with default aperture value\n"
    # cmt += "# Default masters are {0}".format(xppu.DEF_MASTERS)
    def_aperture = x.get_default_aperture()
    cmt += "# Default masters are {0}".format(
        x.get_master_list_from_aperture(xppu.h2i(def_aperture)))
    cmd = pm_init_node_cmd1(x.pm_id, def_aperture)
    print(cmt, file=fp)
    print(cmd, file=fp)


def write_xppu(x, fp=None):
    write_header(x, fp)
    write_xppu_mid_list(x, fp)
    write_xppu_ctrl_reg(x, fp)
    write_xppu_ien_reg(x, fp)
    write_xppu_en_cmd(x, fp)
    write_footer(x, fp)


def write_xmpu_regions(x, fp=None):
    for i, region in x.regions.items():
        if not region.config.enable:
            continue

        cmt = "# Region {0}".format(i)
        print(cmt, file=fp)

        # addr start
        addr, val = region.get_start_lo()
        write_cmd_print(x.baseaddr + addr, val, fp)
        addr, val = region.get_start_hi()
        write_cmd_print(x.baseaddr + addr, val, fp)

        # addr end
        addr, val = region.get_end_lo()
        write_cmd_print(x.baseaddr + addr, val, fp)
        addr, val = region.get_end_hi()
        write_cmd_print(x.baseaddr + addr, val, fp)

        # master
        addr, val = region.get_master()
        write_cmd_print(x.baseaddr + addr, val, fp)

        # config
        addr, val = region.get_config()
        write_cmd_print(x.baseaddr + addr, val, fp)


def write_xmpu_ien_reg(x, fp):
    en_regions = [r for _, r in x.regions.items() if r.config.enable]
    if len(en_regions) <= 0:
        return
    cmt = "# Interrupt Enable\n"
    addr, val = x.get_ien_reg_addr_val()
    cmt += write_cmd(addr, val)
    print(cmt, file=fp)


def write_xmpu(x, fp=None):
    write_header(x, fp)
    write_xmpu_regions(x, fp)
    write_xmpu_ien_reg(x, fp)
    write_footer(x, fp)
