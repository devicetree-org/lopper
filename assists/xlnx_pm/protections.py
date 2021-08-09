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
    "versal_cips_0_pspmc_0_psv_r5_tcm_ram_global",
    "psv_r5_tcm@ffe00000",
    "versal_cips_0_pspmc_0",
    "pspmc@fffc0000",
    # "cpus-a72@0", "cpus-r5@3"   # "reg" property is missing
]

debug = 0
sub_id_to_node = {}  # { str: Subsystem() instance }


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
        self.aper_idx = -1
        self.aper_masks = {}  # { subsystem_id : 20-bit aperture mask }
        self.pm = set()  # xilpm label(s)
        self.ftb_entries = {}  # { subsystem_id : [FirewallTableEntry instances] }
        self.ftb_entries_in = {}  # { subsystem_id : [FirewallTableEntry instances] }

    def __str__(self):
        printstr = "Module: {0:25} {1}".format(
            self.node.label, self.pm if bool(self.pm) is True else "{}"
        )
        for subsys in self.ftb_entries:
            for each_entry in self.ftb_entries[subsys]:
                printstr += "\n- {} : ".format(hex(subsys)) + each_entry.__str__()
        return printstr

    def print_entry(self, fp=None):
        if self.ftb_entries == {}:
            return

        print("# {0}: {1}".format(self.node.label, self.name), end="", file=fp)

        if bool(self.pm) is True:
            print(
                " ({0})".format(" ".join(tag.lower() for tag in sorted(self.pm))),
                file=fp,
            )
        else:
            print("", file=fp)

        for subsys in self.ftb_entries:
            for entry in self.ftb_entries[subsys]:
                entry.print_entry(fp)

    def print_entry_custom(self, fp=None):
        if self.ftb_entries_in == {}:
            return

        print("# {0}: {1}".format(self.node.label, self.name), end="", file=fp)

        if bool(self.pm) is True:
            print(
                " ({0})".format(" ".join(tag.lower() for tag in sorted(self.pm))),
                file=fp,
            )
        else:
            print("", file=fp)

        for subsys in self.ftb_entries_in:
            for entry in self.ftb_entries_in[subsys]:
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
                if debug > 0:
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
        pm_name="",
        custom=0,
    ):
        if custom != 0:
            ftb_ents = self.ftb_entries_in
        else:
            ftb_ents = self.ftb_entries

        # add subsystem's entry for this module if missing
        if subsystem_id not in ftb_ents:
            ftb_ents[subsystem_id] = []

        if priority == 0:
            priority = 10  # "0" is highest priority from subsystem plugin

        ftb_entry = ftb.FirewallTableEntry(
            subsystem_id,
            base_addr,
            size,
            rw,
            tz,
            mid_list,
            module_name,
            pm_tag=pm_name,
            priority=priority,
        )

        # add to the subsystem's ftb entries
        ftb_ents[subsystem_id].append(ftb_entry)

    def compute_aper_mask(self, x_inst, custom=0):
        if custom != 0:
            ftb_ents = self.ftb_entries_in
        else:
            ftb_ents = self.ftb_entries

        # loop over the appropriate ftb (custom vs non-custom)
        for sub_id, entries in ftb_ents.items():
            for entry in entries:  # ftb.FirewallTableEntry instance per subsystem
                mask = 0
                for mid_entry in entry.mid_list:
                    mask_idx = x_inst.get_master_by_smrw(
                        mid_entry.smid, mid_entry.mask, entry.rw
                    )
                    if mask_idx is not None:
                        mask = mask | (1 << mask_idx)
                # store aper mask for this entry
                entry.aper_mask = mask

    def set_default_mask_for_xppu(self, x_inst, custom=0):
        if custom != 0:
            ftb_ents = self.ftb_entries_in
        else:
            ftb_ents = self.ftb_entries

        # only get pmc subsystem's entry for xppus
        mask = 0

        if 0x1C000000 not in ftb_ents:
            return

        for entry in ftb_ents[0x1C000000]:  # ftb.FirewallTableEntry
            mask = mask | entry.aper_mask
        # set default aper mask for this xppu instance
        x_inst.set_default_aperture(mask)

    def get_aperture(self, subsystem_id, custom=0):
        if custom != 0:
            ftb_ents = self.ftb_entries_in
        else:
            ftb_ents = self.ftb_entries

        mask = 0

        if subsystem_id not in ftb_ents:
            return mask

        for entry in ftb_ents[subsystem_id]:
            mask = mask | entry.aper_mask

        return mask


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
        pm_name="",
    ):

        if module_name not in self.modules:
            print("[ERROR] Missing module {} from prot_map".format(module_name))
            return

        if module_name in SKIP_MODULES:
            return

        # add entry
        self.modules[module_name].add_ftb_entry(
            subsystem_id,
            module_name,
            base_addr,
            size,
            rw,
            tz,
            priority,
            mid_list,
            pm_name=pm_name,
        )

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
        # ss_bus_mids is a list of tuples
        if ss_or_dom_name not in self.ss_or_dom:
            return None

        ss_bus_mids = self.ss_or_dom[ss_or_dom_name]
        mid_list = [n[1] for n in ss_bus_mids[1]]

        mask_dict = {
            x_name: 0
            for x_name, x_inst in self.ppus.items()
            if x_inst.hw_instance != None
        }

        for x_name in mask_dict:
            for m in mid_list:
                idx = self.ppus[x_name].hw_instance.get_master_by_smid(m)
                mask_dict[x_name] = mask_dict[x_name] | (1 << idx)
            mask_dict[x_name] = hex(mask_dict[x_name])

        return mask_dict

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
            print(
                "[DBG++] {} -> {} [Mask: {}]".format(
                    ss, mid_list, self.derive_ss_mask(ss)
                )
            )

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

    def write_ftb_custom(self, fp=None):
        # write out ppu entries first
        for ppu in self.ppus:
            if ppu in self.modules:
                self.modules[ppu].print_entry_custom(fp)

        # write out all other entries
        for module in self.modules:
            if module not in self.ppus:
                if module in SKIP_MODULES:
                    # print("[DBG++] Skipping from firewall-table:", module)
                    pass
                else:
                    self.modules[module].print_entry_custom(fp)

    def generate_aper_masks(self, custom=0):
        # write out all other entries
        mod_list = (module for module in self.modules if module not in SKIP_MODULES)

        for module in mod_list:
            # get parent firewall controller instance
            x_inst_name = self.mod_to_ppu_map[module][0]
            x_inst = self.ppus[x_inst_name].hw_instance
            if x_inst is not None:
                if debug > 0:
                    print(
                        "[DBG++] Aper Config for {} -> {}".format(module, x_inst_name)
                    )
                self.modules[module].compute_aper_mask(x_inst, custom)
            else:
                if debug > 0:
                    print(
                        "[DBG++] Skipped: Aper Config for {} -> {}".format(
                            module, x_inst_name
                        )
                    )

    def set_default_aper_masks_per_xppu(self, custom=0):
        # write out xppu def aper entries
        mod_list = (module for module in self.modules if module in self.ppus)

        for module in mod_list:
            # get parent firewall controller instance
            x_inst_name = self.mod_to_ppu_map[module][0]
            x_inst = self.ppus[x_inst_name].hw_instance
            if x_inst is not None:
                if debug > 0:
                    print(
                        "[DBG++] Aper Config for {} -> {}".format(module, x_inst_name)
                    )
                self.modules[module].set_default_mask_for_xppu(x_inst, custom)
            else:
                if debug > 0:
                    print(
                        "[DBG++] Skipped: Aper Config for {} -> {}".format(
                            module, x_inst_name
                        )
                    )


# Global PPU <-> Peripherals Map
prot_map = FirewallToModuleMap()

# Firewall table (read from user)
firewall_table = ftb.FirewallTable()


def chunks(list_of_elements, num):
    """Yield successive num-sized chunks from input list"""
    for i in range(0, len(list_of_elements), num):
        yield list_of_elements[i : i + num]


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
        "a72": {0x1: 0x260, 0x2: 0x261},
        "r5": {0x1: 0x200, 0x2: 0x204},
    }

    for substr in cpu_map:
        if substr in cpu_node.name:
            if cpu_node.name not in prot_map.bus_mids:
                print(
                    "[WARNING] Node {} is missing from prot map bus master IDs".format(
                        cpu_node.name
                    )
                )
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


def get_dev_aperture(subsystem_id: int, pm_name: str, custom=0):
    """returns an aperture mask for the device"""
    mod_tags = ""
    mod_tag = ""
    mod_node = None

    try:
        mod_tags = prot_map.pm_label_to_mod_map[pm_name]
        # print(mod_tags, len(mod_tags))
    except:
        if debug > 0:
            print("[WARNING] FIXME:: Invalid or missing PM Node {}".format(pm_name))
        return 0

    mod_tags_list = list(mod_tags)

    if len(mod_tags_list) == 1:
        mod_tag = mod_tags_list[0]
    elif len(mod_tags_list) == 2:
        if debug > 0:
            print(
                "[DBG++] Warning: found more than one nodes with same addresses:",
                mod_tags_list,
            )
        # pick the first one
        for tag in mod_tags_list:
            if tag not in SKIP_MODULES:
                mod_tag = tag

    try:
        mod_node = prot_map.modules[mod_tag]
        # print(mod_node.name)
    except:
        print("[ERROR] Invalid or missing Module {}".format(mod_node))
        return 0

    if debug > 0:
        print("[DBG++++]", hex(subsystem_id), pm_name, mod_node.name)

    # return aperture for this subsystem
    return mod_node.get_aperture(subsystem_id, custom)


def setup_dev_ftb_entry(
    subsystem_id: int, pm_name: str, rw: int, tz: int, mid_list: list
):  # allowed: [(dom/ss name, priority),..]
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
        if debug > 0:
            print("[WARNING] FIXME:: Invalid or missing PM Node {}".format(pm_name))
        return

    mod_tags_list = list(mod_tags)

    if len(mod_tags_list) == 1:
        mod_tag = mod_tags_list[0]
    elif len(mod_tags_list) == 2:
        if debug > 0:
            print(
                "[DBG++] Warning: found more than one nodes with same addresses:",
                mod_tags_list,
            )
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
        if dom_or_ss_name not in prot_map.ss_or_dom:
            # print("Skipping dom/ss:", dom_or_ss_name, "for", pm_name)
            continue
        # print("[DBG++++]", prot_map.ss_or_dom[dom_or_ss_name])
        for bus_mid_name, smid in prot_map.ss_or_dom[dom_or_ss_name][1]:
            if priority not in bus_mids:
                bus_mids[priority] = []
            bus_mids[priority].append(ftb.MidEntry(smid, 0x3FF, name=bus_mid_name))

    if debug > 0:
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
            0x1C000000,  # PLM Subsystem ID
            module,
            base_addr,
            size,
            xppu.RW,  # RW
            1,  # TZ (Non-secure)
            10,  # Highest priority
            mid_list,
        )


def ftb_setup_modules():
    for tline in firewall_table.tokens:
        # if tline[2] == "*":
        #     print(tline)
        #     continue

        # find matching sdt module for this entry
        baddr = xppu.h2i(tline[1])
        # FIXME
        size = xppu.h2i(tline[2]) if tline[2] != "*" else 0x2000

        mod_inst = None

        match = [
            inst
            for mod, inst in prot_map.modules.items()
            if inst.address == baddr and inst.size == size
        ]

        if len(match) >= 1:
            # print("[ERROR] More than one instance found for", tline, ":",
            #       ', '.join(i.name for i in match))
            # pick first match
            if len(match) == 1:
                mod_inst = match[0]
            else:
                for i, m in enumerate(match):
                    if m.name not in SKIP_MODULES:
                        mod_inst = match[i]

        # not found
        if mod_inst is None:
            continue

        # create mid_list for this entry
        mid_list = []
        for mid_pair in tline[6:]:
            smid, mask = mid_pair.split("/")
            mid_list.append(ftb.MidEntry(xppu.h2i(smid), xppu.h2i(mask)))

        subsystem_id = xppu.h2i(tline[0])
        rw = int(tline[3])
        tz = int(tline[4])
        priority = int(tline[5])

        # add ftb entry (custom)
        mod_inst.add_ftb_entry(
            subsystem_id,
            mod_inst.name,
            baddr,
            # FIXME: Remove special condition
            size if tline[2] != "*" else tline[2],
            rw,
            tz,
            priority,
            mid_list,
            custom=1,
        )


def generate_aper_masks_all(custom=0):
    # print("--- mask begin [custom: {0}] ---".format(custom))
    prot_map.generate_aper_masks(custom)
    # print("--- xppu mask begin [custom: {0}] ---".format(custom))
    prot_map.set_default_aper_masks_per_xppu(custom)


def ftb_setup(filepath, sdt, options):
    if firewall_table.read_file(filepath) is False:
        return
    # firewall_table.dump()

    # setup ftb modules
    ftb_setup_modules()


def write_to_cdo(filep, verbose):
    with open(filep, "a") as fp:
        for ppu_name, ppu_obj in prot_map.ppus.items():

            xppu_instance = ppu_obj.hw_instance
            xppu_label = ppu_obj.node.label

            if verbose > 1:
                print(
                    "[INFO]: Generating configuration for {0} XPPU ({1})".format(
                        xppu_label, filep
                    )
                )

            if xppu_instance is not None:
                cdogen.write_xppu(xppu_instance, fp)


def setup(root_node, sdt, options):
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
