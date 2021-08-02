# /*
# * Copyright (c) 2021 Xilinx Inc. All rights reserved.
# *
# * Author:
# *       Izhar Shaikh <izhar.ameer.shaikh@xilinx.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

from lopper import Lopper
import lopper
from lopper_tree import *
from xlnx_versal_power import xlnx_pm_devid_to_name, xlnx_pm_devname_to_id

sys.path.append(os.path.dirname(__file__))

import xppu
import cdogen
import ftb

# Skip from FTB (TODO: Fix this in SDT in long term)
SKIP_MODULES = [
    "versal_cips_0_pspmc_0_psv_r5_tcm_ram_global", "psv_r5_tcm@ffe00000"
]

class XppuNode:
    def __init__(self, name, node, compat):
        self.name = name
        self.node = node
        self.compat = compat
        if "xlnx,xppu" in self.compat:
            self.hw_instance = xppu.init_xppu(node.label)
        elif "xlnx,xmpu" in compat:
            self.hw_instance = None
        else:
            print("[ERROR] Invalid protection node {}".format(name))


class ModuleNode:
    def __init__(self, name, node):
        self.name = name
        self.node = node
        self.address = -1
        self.size = -1
        self.pm = set()  # xilpm label(s)
        self.ftb_entries = {}  # { subsystem_id : FirewallTableEntry instance }

    def __str__(self):
        printstr = "Module: {0:25} {1}".format(
            self.node.label, self.pm if bool(self.pm) is True else "{}")
        for subsys in self.ftb_entries:
            for each_entry in self.ftb_entries[subsys]:
                printstr += "\n- {} : ".format(
                    hex(subsys)) + each_entry.__str__()
        return printstr

    def print_entry(self, fp=None):
        print("# {0}: {1}".format(self.node.label, self.name),
              end='', file=fp)

        if bool(self.pm) is True:
            print(" ({0})"
                  .format(" ".join(tag.lower() for tag in sorted(self.pm))),
                  file=fp)
        else:
            print("", file=fp)

        for subsys in self.ftb_entries:
            for entry in self.ftb_entries[subsys]:
                entry.print_entry(fp)

    def get_base_and_size(self):
        if self.address != -1 and self.size != -1:
            return self.address, self.size

        addr = self.node.propval("reg")
        # print("[DBG++++]", self.name, self.node.label, addr)

        if addr != [""]:
            if len(addr) % 4 == 0:
                self.address = (addr[0] << 32) | (addr[1])
                self.size = (addr[2] << 32) | (addr[3])
            elif len(addr) % 2 == 0:
                self.address = addr[0]
                self.size = addr[1]
            else:
                print(
                    "[WARNING++++] FIXME:: len(addr) is not 2 or 4",
                    self.name,
                    self.node.label,
                    addr,
                )

        return self.address, self.size

    def add_ftb_entry(
            self,
            subsystem_id: int,
            module_name: str,
            base_addr: int,
            size: int,
            rw: int,
            tz: int,
            priority: int,
            mid_list: [[ftb.MidEntry]],  # [ MidEntry(name, smid, mask), ...]
            pm_name=''):
        # add subsystem's entry for this module if missing
        if subsystem_id not in self.ftb_entries:
            self.ftb_entries[subsystem_id] = []

        if priority == 0:
            priority = 10  # "0" is highest priority from subsystem plugin

        ftb_entry = ftb.FirewallTableEntry(subsystem_id,
                                           base_addr,
                                           size,
                                           rw,
                                           tz,
                                           mid_list,
                                           module_name,
                                           pm_tag=pm_name,
                                           priority=priority)

        self.ftb_entries[subsystem_id].append(ftb_entry)


class BusMid:
    def __init__(self, name, node):
        self.name = name
        self.node = node


class FirewallToModuleMap:
    def __init__(self):
        self.ss_or_dom = {}
        self.ppus = {}
        self.modules = {}
        self.bus_mids = {}
        self.ppu_to_mod_map = {}
        self.mod_to_ppu_map = {}
        self.ppu_to_mid_map = {}
        self.pm_label_to_mod_map = {}

    def add_xppu(self, name, node, compat):
        if name not in self.ppus:
            self.ppus[name] = XppuNode(name, node, compat)
            self.ppu_to_mid_map[name] = []
            self.ppu_to_mod_map[name] = []
        else:
            print("[WARNING] Trying to add {} instance again".format(name))

    def add_module(self, name, node, parent_name):
        parent = parent_name
        if name not in self.modules:
            self.modules[name] = ModuleNode(name, node)
            self.ppu_to_mod_map[parent].append(name)
            self.mod_to_ppu_map[name] = []
            self.mod_to_ppu_map[name].append(parent)
        else:
            print("[WARNING] Trying to add {} instance again".format(name))

    def add_module_node_id(self, pm_name, mod_name):
        if mod_name not in self.modules:
            print("[WARNING] Missing node {} from modules".format(mod_name))
        else:
            if pm_name not in self.pm_label_to_mod_map:
                self.pm_label_to_mod_map[pm_name] = set()
            self.pm_label_to_mod_map[pm_name].add(mod_name)
            self.modules[mod_name].pm.add(pm_name)

    def add_module_ftb_entry(
            self,
            subsystem_id: int,
            module_name: str,
            base_addr: int,
            size: int,
            rw: int,
            tz: int,
            priority: int,
            mid_list: [[ftb.MidEntry]],  # [ MidEntry(name, smid, mask), ...]
            pm_name=''):
        if module_name not in self.modules:
            print("[ERROR] Missing module {} from prot_map"
                  .format(module_name))
            return
        if module_name in SKIP_MODULES:
            return
        # add entry
        self.modules[module_name].add_ftb_entry(subsystem_id,
                                                module_name,
                                                base_addr,
                                                size,
                                                rw,
                                                tz,
                                                priority,
                                                mid_list,
                                                pm_name=pm_name)

    def add_bus_mid(self, name, node, parents):
        """parents -> tuple of (<firewall-name, smid>)"""
        if name not in self.bus_mids:
            self.bus_mids[name] = BusMid(name, node)
        # Add MIDs for ppu instances
        for ppu, smid in parents:
            if ppu not in self.ppus:
                print("[WARNING] Missing {} instance".format(ppu))
            self.ppu_to_mid_map[ppu].append((name, smid))

    def add_ss_to_bus_mids(self, ss_or_dom_name, ss_or_dom_node, bus_mid_node):
        """subsystem/domain to bus_mid node map
        bus_mid_node -> tuple of (<bus_mid_node_name smid>)
        """
        if ss_or_dom_name not in self.ss_or_dom:
            # (node, [bus_mids])
            self.ss_or_dom[ss_or_dom_name] = (ss_or_dom_node, [bus_mid_node])
        else:
            self.ss_or_dom[ss_or_dom_name][1].append(bus_mid_node)

    def derive_ss_mask(self, ss_or_dom_name):
        """Must execute after all ppus and ss/dom -> bus mids are added"""
        mask = 0
        # ss_bus_mids is a list of tuples
        if ss_or_dom_name not in self.ss_or_dom:
            return mask
        ss_bus_mids = self.ss_or_dom[ss_or_dom_name]
        mid_list = [n[1] for n in ss_bus_mids[1]]
        # FIXME:: derive masks for each unit
        for m in mid_list:
            idx = self.ppus["xppu@f1310000"].hw_instance.get_master_by_smid(m)
            mask = mask | (1 << idx)
        return mask

    def print_firewall_to_module_map(self):
        for firewall in self.ppu_to_mod_map:
            print("[DBG++] {}:".format(firewall))
            for module in self.ppu_to_mod_map[firewall]:
                print("[DBG++] \t{0:40}".format(module), self.modules[module])
        for firewall in self.ppu_to_mid_map:
            print("[DBG++] {}:".format(firewall))
            for mid in self.ppu_to_mid_map[firewall]:
                print("[DBG++] \t({}, {})".format(mid[0], hex(mid[1])))
        print("[DBG++] bus_mids:")
        for mid_names in self.bus_mids:
            print("[DBG++] \t{}".format(mid_names))

    def print_module_to_firewall_map(self):
        for module, firewalls in self.mod_to_ppu_map.items():
            print("[DBG++] {} -> {}".format(module, firewalls))

    def print_ss_to_bus_mids_map(self):
        for ss, bus_mid_nodes in self.ss_or_dom.items():
            mid_list = [(n[0], hex(n[1])) for n in bus_mid_nodes[1]]
            print("[DBG++] {} -> {} [Mask: {}]".format(
                ss, mid_list, hex(self.derive_ss_mask(ss))))

    def print_mod_ftb_map(self):
        for module in self.modules:
            print("[DBG++]", self.modules[module])

    def write_ftb(self, fp=None):
        # write out ppu entries first
        for ppu in self.ppus:
            if ppu in self.modules:
                self.modules[ppu].print_entry(fp)
        # write out all other entries
        for module in self.modules:
            if module not in self.ppus:
                if module in SKIP_MODULES:
                    # print("[DBG++] Skipping from firewall-table:", module)
                    pass
                else:
                    self.modules[module].print_entry(fp)


# Global PPU <-> Peripherals Map
prot_map = FirewallToModuleMap()


def chunks(list_of_elements, num):
    """Yield successive num-sized chunks from input list"""
    for i in range(0, len(list_of_elements), num):
        yield list_of_elements[i:i + num]


def setup_firewall_nodes(root_tree):
    for node in root_tree.subnodes():
        # A firewall controller
        compat = node.propval("compatible")
        if "xlnx,xppu" in compat:
            # print(node.name, node.__props__)
            prot_map.add_xppu(node.name, node, "xlnx,xppu")
        elif "xlnx,xmpu" in compat:
            prot_map.add_xppu(node.name, node, "xlnx,xmpu")


def setup_module_nodes(root_tree):
    for node in root_tree.subnodes():
        # A module protected by a firewall
        if node.propval("firewall-0") != [""]:
            for parent in node.propval("firewall-0"):
                firewall = root_tree.tree.pnode(parent)
                # print(node.name, ":", node.abs_path, firewall.name)
                prot_map.add_module(node.name, node, firewall.name)


def setup_module_node_ids_if_present(root_tree):
    for node in root_tree.subnodes():
        # A module to pm id mapping
        pd = node.propval("power-domains")
        if pd != [""]:
            # print("[DBG++] >>>>> {}".format(node.abs_path))
            pm_name = None
            try:
                pm_name = xlnx_pm_devid_to_name[pd[1]]
            except:
                pm_name = ""

            if pm_name != "":
                prot_map.add_module_node_id(pm_name, node.name)
            else:
                print("[WARNING] Invalid or missing PM Node {}".format(pd[1]))


def setup_mid_nodes(root_tree):
    for node in root_tree.subnodes():
        # A bus-firewall master
        mids = node.propval("bus-master-id")
        if mids != [""]:
            firewalls = []
            for fw, smid in chunks(mids, 2):
                firewall = root_tree.tree.pnode(fw)
                firewalls.append((firewall.name, smid))
            # print(node.name, ":", node.abs_path, firewalls, smid)
            prot_map.add_bus_mid(node.name, node, firewalls)


def setup_dom_to_bus_mids(sub_or_dom_node, sdt):
    cpus_fields = sub_or_dom_node.propval("cpus")
    if cpus_fields != [""]:
        cpu_node = sdt.tree.pnode(cpus_fields[0])
        mask = cpus_fields[1]
    else:
        return None

    # cpu smid map
    cpu_map = {
        "a72": {
            0x1: 0x260,
            0x2: 0x261
        },
        "r5": {
            0x1: 0x200,
            0x2: 0x204
        },
    }

    for substr in cpu_map:
        if substr in cpu_node.name:
            if cpu_node.name not in prot_map.bus_mids:
                print(
                    "[WARNING] Node {} is missing from prot map bus master IDs"
                    .format(cpu_node.name))
                return None
            # check for cores, add appropriately
            if (mask & 0x1) in cpu_map[substr]:
                prot_map.add_ss_to_bus_mids(
                    sub_or_dom_node.name,
                    sub_or_dom_node,
                    (cpu_node.name, cpu_map[substr][mask & 0x1]),
                )
            if (mask & 0x2) in cpu_map[substr]:
                prot_map.add_ss_to_bus_mids(
                    sub_or_dom_node.name,
                    sub_or_dom_node,
                    (cpu_node.name, cpu_map[substr][mask & 0x2]),
                )


def setup_dev_ftb_entry(
        subsystem_id: int, pm_name: str, rw: int, tz: int,
        mid_list: list):  # allowed: [(dom/ss name, priority),..]
    """create an ftb entry for given device with a linked subsystem"""
    mod_tags = ""
    mod_tag = ""
    mod_node = None
    bus_mids = {}  # { priority: [ MidEntry(name, smid, mask), ...] }

    if mid_list == [""]:
        return

    try:
        mod_tags = prot_map.pm_label_to_mod_map[pm_name]
        # print(mod_tags, len(mod_tags))
    except:
        print(
            "[ERROR] FIXME:: Invalid or missing PM Node {}".format(pm_name))
        return

    mod_tags_list = list(mod_tags)

    if len(mod_tags_list) == 1:
        mod_tag = mod_tags_list[0]
    elif len(mod_tags_list) == 2:
        print("[DBG++] Warning: found more than one nodes with same addresses:",
              mod_tags_list)
        # pick the first one
        for tag in mod_tags_list:
            if tag not in SKIP_MODULES:
                mod_tag = tag

    try:
        mod_node = prot_map.modules[mod_tag]
        # print(mod_node.name)
    except:
        print("[ERROR] Invalid or missing Module {}".format(mod_node))
        return

    # get module base address and size
    base_addr, size = mod_node.get_base_and_size()

    for dom_or_ss_name, priority in mid_list:
        # list of bus mids per domain or ss node
        bus_mids[priority] = []
        # print("[DBG++++]", prot_map.ss_or_dom[dom_or_ss_name])
        for bus_mid_name, smid in prot_map.ss_or_dom[dom_or_ss_name][1]:
            bus_mids[priority].append(
                ftb.MidEntry(smid, 0x3FF, name=bus_mid_name))

    print(
        "[DBG++++]",
        hex(subsystem_id),
        hex(base_addr),
        hex(size),
        rw,
        tz,
        mod_tags_list,
        mod_node.name,
        bus_mids,
    )

    # entry for each priority in bus_mids
    # { priority: [ MidEntry(name, smid, mask), ...] }
    for priority in bus_mids:
        prot_map.add_module_ftb_entry(
            subsystem_id,
            mod_node.name,
            base_addr,
            size,
            rw,
            tz,
            priority,
            bus_mids[priority],  # [ MidEntry(name, smid, mask), ...]
            pm_name=pm_name,
        )


def setup_default_ftb_entries():
    mid_list = [
        ftb.MidEntry(xppu.MIDL[mid][0], xppu.MIDL[mid][1], name=mid)
        for mid in xppu.DEF_MASTERS
    ]

    for module in prot_map.modules:
        base_addr, size = prot_map.modules[module].get_base_and_size()
        if module in prot_map.ppus:
            size = "*"
        prot_map.add_module_ftb_entry(
            0x1c000000,  # PLM Subsystem ID
            module,
            base_addr,
            size,
            1,  # RW
            1,  # TZ (Non-secure)
            10,  # Highest priority
            mid_list)


def write_cdo(filep, verbose):
    with open(filep, "a") as fp:
        for ppu_name, ppu_obj in prot_map.ppus.items():
            xppu_instance = ppu_obj.hw_instance
            xppu_label = ppu_obj.node.label
            if verbose > 1:
                print("[INFO]: Generating configuration for {0} XPPU ({1})".
                      format(xppu_label, filep))
            if xppu_instance is not None:
                cdogen.write_xppu(xppu_instance, fp)


def setup_node_to_prot_map(root_node, sdt, options):
    """
    Setup a map with relationships between a node and its corresponding
    firewall controller (xppu + xmpu)
    """
    # FIXME:: Do for XMPU
    root = root_node.tree["/"]

    setup_firewall_nodes(root)
    setup_module_nodes(root)
    setup_module_node_ids_if_present(root)
    setup_mid_nodes(root)
    xppu.init_instances()

    # prot_map.print_module_to_firewall_map()
    # prot_map.print_firewall_to_module_map()
    # prot_map.print_ss_to_bus_mids_map()
