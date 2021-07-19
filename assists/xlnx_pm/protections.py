# /*
# * Copyright (c) 2021 Xilinx Inc. All rights reserved.
# *
# * Author:
# *       Izhar Shaikh <izhar.ameer.shaikh@xilinx.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

import xppu
import cdogen
from lopper import Lopper
import lopper
from lopper_tree import *

sys.path.append(os.path.dirname(__file__))


class XppuNode():
    def __init__(self, name, node):
        self.name = name
        self.node = node
        self.hw_instance = xppu.init_xppu(node.label)


class ModuleNode():
    def __init__(self, name, node):
        self.name = name
        self.node = node


class BusMid():
    def __init__(self, name, node):
        self.name = name
        self.node = node


class FirewallToModuleMap():
    def __init__(self):
        self.ss_or_dom = {}
        self.ppus = {}
        self.modules = {}
        self.bus_mids = {}
        self.ppu_to_mod_map = {}
        self.mod_to_ppu_map = {}
        self.ppu_to_mid_map = {}

    def add_xppu(self, name, node):
        if name not in self.ppus:
            self.ppus[name] = XppuNode(name, node)
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

    def add_bus_mid(self, name, node, parents):
        """ parents -> tuple of (<firewall-name, smid>) """
        if name not in self.bus_mids:
            self.bus_mids[name] = BusMid(name, node)
        # Add MIDs for ppu instances
        for ppu, smid in parents:
            if ppu not in self.ppus:
                print("[WARNING] Missing {} instance".format(ppu))
            self.ppu_to_mid_map[ppu].append((name, smid))

    def add_ss_to_bus_mids(self, ss_or_dom_name, ss_or_dom_node, bus_mid_node):
        """ subsystem/domain to bus_mid node map
            bus_mid_node -> tuple of (<bus_mid_node_name smid>)
        """
        if ss_or_dom_name not in self.ss_or_dom:
            # (node, [bus_mids])
            self.ss_or_dom[ss_or_dom_name] = (ss_or_dom_node, [bus_mid_node])
        else:
            self.ss_or_dom[ss_or_dom_name][1].append(bus_mid_node)

    def derive_ss_mask(self, ss_or_dom_name):
        """ Must execute after all ppus and ss/dom -> bus mids are added """
        mask = 0
        # ss_bus_mids is a list of tuples
        if ss_or_dom_name not in self.ss_or_dom:
            return mask
        ss_bus_mids = self.ss_or_dom[ss_or_dom_name]
        mid_list = [n[1] for n in ss_bus_mids[1]]
        # FIXME:: derive masks for each unit
        for m in mid_list:
            idx = self.ppus['xppu@f1310000'].hw_instance.get_master_by_smid(m)
            mask = mask | (1 << idx)
        return mask

    def print_firewall_to_module_map(self):
        for firewall in self.ppu_to_mod_map:
            print("{}:".format(firewall))
            for module in self.ppu_to_mod_map[firewall]:
                print("\t{}".format(module))
        for firewall in self.ppu_to_mid_map:
            print("{}:".format(firewall))
            for mid in self.ppu_to_mid_map[firewall]:
                print("\t({}, {})".format(mid[0], hex(mid[1])))
        print("bus_mids:")
        for mid_names in self.bus_mids:
            print("\t{}".format(mid_names))

    def print_module_to_firewall_map(self):
        for module, firewalls in self.mod_to_ppu_map.items():
            print("{} -> {}".format(module, firewalls))

    def print_ss_to_bus_mids_map(self):
        for ss, bus_mid_nodes in self.ss_or_dom.items():
            mid_list = [(n[0], hex(n[1])) for n in bus_mid_nodes[1]]
            print("{} -> {} [Mask: {}]".format(ss, mid_list,
                                               hex(self.derive_ss_mask(ss))))


# Global PPU <-> Peripherals Map
prot_map = FirewallToModuleMap()


def chunks(list_of_elements, num):
    """ Yield successive num-sized chunks from input list """
    for i in range(0, len(list_of_elements), num):
        yield list_of_elements[i:i + num]


def setup_firewall_nodes(root_tree):
    for node in root_tree.subnodes():
        # A firewall controller
        if "xlnx,xppu" in node.propval("compatible"):
            # print(node.name, node.__props__)
            prot_map.add_xppu(node.name, node)


def setup_module_nodes(root_tree):
    for node in root_tree.subnodes():
        # A module protected by a firewall
        if node.propval("firewall-0") != [""]:
            for parent in node.propval("firewall-0"):
                firewall = root_tree.tree.pnode(parent)
                # print(node.name, ":", node.abs_path, firewall.name)
                prot_map.add_module(node.name, node, firewall.name)


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
    if cpus_fields != ['']:
        cpu_node = sdt.tree.pnode(cpus_fields[0])
        mask = cpus_fields[1]
    else:
        return None

    # cpu smid map
    cpu_map = {
        'a72': {
            0x1: 0x260,
            0x2: 0x261
        },
        'r5': {
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
                    sub_or_dom_node.name, sub_or_dom_node,
                    (cpu_node.name, cpu_map[substr][mask & 0x1]))
            if (mask & 0x2) in cpu_map[substr]:
                prot_map.add_ss_to_bus_mids(
                    sub_or_dom_node.name, sub_or_dom_node,
                    (cpu_node.name, cpu_map[substr][mask & 0x2]))


def write_cdo(filep, verbose):
    with open(filep, 'a') as fp:
        for ppu_name, ppu_obj in prot_map.ppus.items():
            xppu_instance = ppu_obj.hw_instance
            xppu_label = ppu_obj.node.label
            if verbose > 1:
                print("[INFO]: Generating configuration for {0} XPPU ({1})".
                      format(xppu_label, filep))
            cdogen.write_xppu(xppu_instance, fp)


def setup_node_to_prot_map(root_node, sdt, options):
    '''
    Setup a map with relationships between a node and its corresponding
    firewall controller (xppu + xmpu)
    '''
    # FIXME:: Do for XMPU
    root = root_node.tree["/"]

    setup_firewall_nodes(root)
    setup_module_nodes(root)
    setup_mid_nodes(root)
    xppu.init_instances()

    # prot_map.print_firewall_to_module_map()
    # prot_map.print_module_to_firewall_map()
    # prot_map.print_ss_to_bus_mids_map()
