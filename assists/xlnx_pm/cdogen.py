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


def mask_write_cmd(addr, mask, val):
    cmd = "mask_write {0} {1} {2}".format(addr, mask, val)
    return cmd


def pm_init_node_cmd1(pm_id, val):
    cmd = "pm_init_node {0} {1} {2}".format(pm_id, hex(0x1), val)
    return cmd


def pm_init_node_cmd2(pm_id, aper, val):
    cmd = "pm_init_node {0} {1} {2} {3}".format(pm_id, hex(0x2), hex(aper), val)
    return cmd


def write_header(x, fp=None):
    print("marker", hex(0x64), '"{}"'.format(x.name.upper()), file=fp)
    cmt = "# {0} : Setup START".format(x.name.upper())
    print(cmt, file=fp)


def write_footer(x, fp=None):
    print("marker", hex(0x65), '"{}"'.format(x.name.upper()), file=fp)
    cmt = "# {0} : Setup END".format(x.name.upper())
    print(cmt, file=fp)


def write_mid_list(x, fp=None):
    for i in range(xppu.MASTERS):
        master = x.get_master(i)
        if master is None:
            continue
        cmt = "# {0} XPPU: Set Master ID {1} to {2}".format(x.name, i, master.name)
        cmt += "\n# [MID: {0}, Mask: {1}, {2}, Parity: {3}]".format(
            master.mid,
            master.mask,
            "Read Only" if master.rw == 1 else "Read/Write",
            "Enable" if master.parity == 1 else "Disable",
        )
        cmd = write_cmd(x.get_master_addr(i), x.get_master_val(i))
        print(cmt, file=fp)
        print(cmd, file=fp)


def write_ctrl_reg(x, fp=None):
    cmt = "# Parity error checking for Aperture and Master ID entries\n"
    cmt += "# APER_PARITY_EN = {0}\n".format(xppu.APER_PARITY)
    cmt += "# MID_PARITY_EN = {0}".format(xppu.MID_PARITY)
    val = x.get_ctrl_reg_val()
    cmd = mask_write_cmd(x.baseaddr, val, val)
    print(cmt, file=fp)
    print(cmd, file=fp)


def write_ien_reg(x, fp=None):
    cmt = "# Interrupt Enable\n"
    for istr, ival in xppu.Interrupts.items():
        cmt += "# {0} = {1}\n".format(istr.upper(), ival[0])
    addr, val = x.get_ien_reg_addr_val()
    cmd = write_cmd(addr, val)
    print(cmt + cmd, file=fp)


def write_enable_xppu_cmd(x, fp=None):
    cmt = "# Enable {0} XPPU\n".format(x.name)
    cmt += "# Initialize all apertures with default aperture value\n"
    cmt += "# Default masters are {0}".format(xppu.DEF_MASTERS)
    cmd = pm_init_node_cmd1(x.pm_id, x.get_default_aperture())
    print(cmt, file=fp)
    print(cmd, file=fp)


def write_xppu(x, fp=None):
    write_header(x, fp)
    write_mid_list(x, fp)
    write_ctrl_reg(x, fp)
    write_ien_reg(x, fp)
    write_enable_xppu_cmd(x, fp)
    write_footer(x, fp)
