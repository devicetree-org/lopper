# /*
# * Copyright (c) 2019,2020 Xilinx Inc. All rights reserved.
# *
# * Author:
# *     Ben Levinsky <ben.levinsky@xilinx.com>
# *     Izhar Ameer Shaikh <izhar.ameer.shaikh@xilinx.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

from lopper import Lopper
import lopper
from lopper_tree import *
from xlnx_versal_power import *

sys.path.append(os.path.dirname(__file__))


class Device():
    def __init__(self, nodes, node_id, flags):
        self.nodes = nodes
        self.node_id = node_id
        self.flags = flags
        self.pm_reqs = [0, 0, 0, 0]


class Subsystem():
    def __init__(self, sub_node):
        self.flag_references = {}
        self.dev_dict = {}
        self.sub_node = sub_node


def valid_subsystems(domain_node, sdt, options):
    # return list of valid Xilinx subsystems
    subsystems = []

    # a node is a Xilinx subsystem if:
    # one of the top level domain nodes
    # has compatible string: "xilinx,subsystem-v1"
    # has id property
    for node in domain_node.subnodes():
        if node.parent != domain_node:
            continue
        if node.propval("id") == ['']:
            continue

        if node.propval("compatible") == ['']:
            continue

        compat = node.propval("compatible")
        for c in compat:
            if c == 'xilinx,subsystem-v1':
                subsystems.append(Subsystem(node))

    return subsystems


def usage_no_restrictions(node):
    firewallconfig_default = node.propval("firewallconfig-default")
    return (firewallconfig_default != [''] and firewallconfig_default[0] == 0
            and firewallconfig_default[1] == 0)


def find_cpu_ids(node, sdt):
    # given a node that has a cpus property, return list of  corresponding
    # XilPM Node IDs for the core node
    cpu_phandle = node.propval('cpus')[0]
    cpu_mask = node.propval('cpus')[1]
    dev_str = "dev_"
    cpu_xilpm_ids = []

    cpu_node = sdt.tree.pnode(cpu_phandle)
    if cpu_node is None:
        return cpu_xilpm_ids

    # based on cpu arg cpumask we can determine which cores are used
    if 'a72' in cpu_node.name:
        dev_str += "acpu"
        cpu_xilpm_ids.append(existing_devices["dev_ams_root"])
        cpu_xilpm_ids.append(existing_devices["dev_l2_bank_0"])
        cpu_xilpm_ids.append(existing_devices["dev_aie"])

    elif 'r5' in cpu_node.name:
        dev_str += "rpu0"
    else:
        # for now only a72 or r5 are cores described in Xilinx subsystems
        return cpu_xilpm_ids

    if cpu_mask & 0x1:
        cpu_xilpm_ids.append(existing_devices[dev_str + "_0"])
    if cpu_mask & 0x2:
        cpu_xilpm_ids.append(existing_devices[dev_str + "_1"])

    return cpu_xilpm_ids


def prelim_flag_processing(node, prefix):
    # return preliminary processing of flags
    flags = ''
    if node.propval(prefix + '-flags-names') == ['']:
        flags = 'default'
    else:
        flags = node.propval(prefix + '-flags-names')
        if isinstance(flags, list):
            flags = flags[0]

    if usage_no_restrictions(node):
        flags = flags + '::no-restrictions'

    return flags


def find_mem_ids(node, sdt):
    # given a node that has a memory or sram property, return list of  corresponding
    # XilPM Node IDs for the mem/sram node
    flags = prelim_flag_processing(node, 'memory')

    mem_xilpm_ids = []
    if node.propval('memory') != ['']:
        xilpm_id = mem_xilpm_ids.append((existing_devices['dev_ddr_0'], flags))

    return mem_xilpm_ids


def find_sram_ids(node, sdt):
    sram_base = node.propval('sram')[0]
    sram_end = node.propval('sram')[1] + sram_base
    mem_xilpm_ids = []
    id_with_flags = []
    flags = prelim_flag_processing(node, 'sram')

    ocm_len = 0xFFFFF
    if 0xFFFC0000 <= sram_base <= 0xFFFFFFFF:
        # OCM
        if sram_base <= 0xFFFC0000 + ocm_len:
            mem_xilpm_ids.append("dev_ocm_bank_0")
        if sram_base < 0xFFFDFFFF and sram_end > 0xFFFD0000:
            mem_xilpm_ids.append("dev_ocm_bank_1")
        if sram_base < 0xFFFEFFFF and sram_end > 0xFFFE0000:
            mem_xilpm_ids.append("dev_ocm_bank_2")
        if sram_base < 0xFFFFFFFF and sram_end > 0xFFFF0000:
            mem_xilpm_ids.append("dev_ocm_bank_3")
    elif 0xFFE00000 <= sram_base <= 0xFFEBFFFF:
        # TCM
        if sram_base < 0xFFE1FFFF:
            mem_xilpm_ids.append("dev_tcm_0_a")
        if sram_base <= 0xFFE2FFFF and sram_end > 0xFFE20000:
            mem_xilpm_ids.append("dev_tcm_0_b")
        if sram_base <= 0xFFE9FFFF and sram_end > 0xFFE90000:
            mem_xilpm_ids.append("dev_tcm_1_a")
        if sram_base <= 0xFFEBFFFF and sram_end > 0xFFEB0000:
            mem_xilpm_ids.append("dev_tcm_1_b")

    for i in mem_xilpm_ids:
        id_with_flags.append((existing_devices[i], flags))

    return id_with_flags


def xilpm_id_from_devnode(subnode):
    power_domains = subnode.propval('power-domains')
    if power_domains != [''] \
            and power_domains[1] in xilinx_versal_device_names.keys():
        return power_domains[1]
    elif subnode.name in misc_devices:
        if misc_devices[subnode.name] is not None:
            mailbox_xilpm_id = existing_devices[misc_devices[subnode.name]]
            if mailbox_xilpm_id is not None:
                return mailbox_xilpm_id
    return -1


def process_acccess(subnode, sdt, options):
    # return list of node ids and corresponding flags
    access_list = []
    access_flag_names = subnode.propval('access-flags-names')
    access_phandles = subnode.propval('access')
    f = ''
    if usage_no_restrictions(subnode):
        f += '::no-restrictions'

    if len(access_flag_names) != len(access_phandles):
        print("WARNING: subnode: ", subnode,
              " has length of access and access-flags-names mismatch: ",
              access_phandles, access_flag_names)
        return access_list

    for index, phandle in enumerate(access_phandles):
        dev_node = sdt.tree.pnode(phandle)
        if dev_node is None:
            print(
                "WARNING: acccess list device phandle does not have matching device in tree: ",
                hex(phandle), " for node: ", subnode)
            return access_list

        xilpm_id = xilpm_id_from_devnode(dev_node)
        if xilpm_id == -1:
            print("WARNING: no xilpm ID for node: ", dev_node)
            continue

        access_list.append(
            (xilpm_id, access_flag_names[index] + f + '::access'))
    return access_list


def document_requirement(output, subsystem, device):
    subsystem_name = "subsystem_" + str(subsystem.sub_node.propval("id"))
    sub_id = sub.sub_node.propval("id")
    if isinstance(sub_id, list):
        sub_id = sub_id[0]
    cdo_sub_str = "subsystem_" + hex(sub_id)

    cdo_sub_id = hex(0x1c000000 | sub_id)

    flags_arg = device.pm_reqs[0]

    print("#", file=output)
    print("#", file=output)
    print("# subsystem:", file=output)
    print("#    name: " + subsystem_name, file=output)
    print("#    ID: " + cdo_sub_id, file=output)
    print("#", file=output)
    print("# node:", file=output)
    print("#    name: " + xilinx_versal_device_names[device.node_id],
          file=output)
    print("#    ID: " + hex(device.node_id), file=output)
    print("#", file=output)
    arg_names = {
        0: "flags",
        1: "XPPU Aperture Permission Mask",
        2: "Prealloc capabilities",
        3: "Quality of Service"
    }
    for index, flag in enumerate(device.pm_reqs):
        print("# requirements: ",
              arg_names[index],
              ": " + hex(flag),
              file=output)

    print(usage(flags_arg), file=output)
    print(security(flags_arg), file=output)
    print(prealloc_policy(flags_arg), file=output)

    if ((flags_arg & prealloc_mask) >> prealloc_offset == PREALLOC.REQUIRED):
        # detail prealloc if enabled
        print(prealloc_detailed_policy(device.pm_reqs[3]), file=output)

    if mem_regn_node(device.node_id):
        print(read_policy(flags_arg), file=output)
        print(write_policy(flags_arg), file=output)
        print(nsregn_policy(flags_arg), file=output)
        print("#", file=output)


def process_subsystem(subsystem, subsystem_node, sdt, options, included=False):
    # given a device tree node
    # traverse for devices that are either implicitly
    # or explicitly lnked to the device tree node
    # this includes either Xilinx subsystem nodes or nodes that are
    # resource group included nodes

    #
    # In addition, for each device, identify the flag reference that
    # corresponds.

    # collect nodes, xilpm IDs, flag strings here
    for node in subsystem_node.subnodes():
        device_flags = []

        if node.propval('cpus') != ['']:
            f = 'default'
            f += '::no-restrictions'  # by default, cores are not restricted
            for xilpm_id in find_cpu_ids(node, sdt):
                device_flags.append((xilpm_id, f))

        if node.propval('memory') != ['']:
            device_flags.extend(find_mem_ids(node, sdt))
        if node.propval('sram') != ['']:
            device_flags.extend(find_sram_ids(node, sdt))
        if node.propval('access') != ['']:
            device_flags.extend(process_acccess(node, sdt, options))

        if node.propval('include') != ['']:
            for included_phandle in node.propval('include'):
                included_node = sdt.tree.pnode(included_phandle)
                if included_node is None:
                    print("WARNING: included_phandle: ", hex(included_phandle),
                          " does not have corresponding node in tree.")
                else:
                    # recursive base case for handling resource group
                    process_subsystem(subsystem,
                                      included_node,
                                      sdt,
                                      options,
                                      included=True)

        # after collecting a device tree node's devices, add this to subsystem's device nodes collection
        # if current node is resource group, recurse 1 time
        for xilpm_id, flags in device_flags:
            if included:
                if isinstance(flags, list):
                    flags = flags[0]
                flags = flags + "::included-from-" + subsystem_node.name

                # strip out access. as this is used for non-sharing purposes
                # if a device is in a resource group, it should not show as
                # 'non-shared'
                flags.replace('access', '')

            # add to subsystem's list of xilpm nodes
            if xilpm_id not in subsystem.dev_dict.keys():
                subsystem.dev_dict[xilpm_id] = Device([node], xilpm_id,
                                                      [flags])
            else:
                device = subsystem.dev_dict[xilpm_id]
                device.nodes.append(node)
                device.flags.append(flags)


def construct_flag_references(subsystem):
    flags_list = subsystem.sub_node.propval("flags")
    for index, flags_name in enumerate(
            subsystem.sub_node.propval("flags-names")):
        ref_flags = [0x0, 0x0, 0x0, 0x0]

        current_base = index * 4  # 4 elements per requirement of device
        for i in range(0, 3):
            ref_flags[i] = flags_list[current_base + i]

        subsystem.flag_references[flags_name] = ref_flags


def determine_inclusion(device_flags, other_device_flags, sub, other_sub):

    # look for time share in the flags defined by each subsystem that correspond
    # to the device

    # first get current device
    dev_timeshare = device_flags[0].split("::")[0]
    if dev_timeshare not in sub.flag_references.keys():
        dev_timeshare = 'default'

    dev_timeshare = copy.deepcopy(sub.flag_references[dev_timeshare])

    # second get time share of other device
    other_dev_timeshare = device_flags[0].split("::")[0]
    if other_dev_timeshare not in other_sub.flag_references.keys():
        other_dev_timeshare = 'default'

    other_dev_timeshare = copy.deepcopy(
        other_sub.flag_references[other_dev_timeshare])
    included = 0x0
    for f in device_flags:
        if 'include' in f:
            included |= 0x1
        if 'access' in f and 'include' not in f:
            included |= 0x2
    for f in other_device_flags:
        if 'include' in f:
            included |= 0x8
        if 'access' in f and 'include' not in f:
            included |= 0x4

    # determine if timeshare present in one of the flags for a
    # device-subsystem link
    if dev_timeshare[0] & 0x3 == 0x3:
        included |= 16
    if other_dev_timeshare[0] & 0x3 == 0x3:
        included |= 32

    return included


def set_dev_pm_reqs(sub, device, usage):
    new_dev_flags = device.flags[0].split("::")[0]

    if new_dev_flags not in sub.flag_references.keys():
        new_dev_flags = 'default'

    new_dev_flags = copy.deepcopy(sub.flag_references[new_dev_flags])

    device.pm_reqs = new_dev_flags

    device.pm_reqs[0] |= usage


def construct_pm_reqs(subsystems):
    # given list of all subsystems,
    # for each device
    #    look for device in other domains
    #        if found via include, update usage
    #        if found otherwise report as error
    #        else set as non shared
    #
    #    set rest of flags per relevant reference housed in subsystem
    for sub in subsystems:
        for device in sub.dev_dict.values():
            usage = 0x2  # non-shared
            included = 0x0
            for other_sub in subsystems:
                if sub == other_sub:
                    continue

                if device.node_id in other_sub.dev_dict.keys():
                    other_device = other_sub.dev_dict[device.node_id]
                    included = determine_inclusion(device.flags,
                                                   other_device.flags, sub,
                                                   other_sub)

                    # this means neither reference this via include so raise
                    # error
                    if included & 0x6 != 0x0:
                        print('WARNING: ', hex(device.node_id),
                              'found in multiple domains without includes ',
                              sub.sub_node, other_sub.sub_node, included,
                              usage, device.flags, other_device.flags)
                        return
                    if (included & 16 != 0) and (included & 32 == 0):
                        print(
                            'WARNING: ', hex(device.node_id),
                            'found in multiple domains with mismatch of timeshare',
                            sub.sub_node, other_sub.sub_node, included, usage)
                        return

                    if included == 0x1 or included == 0x8 or included == 0x9:
                        usage = 0x1  # update to shared
                else:
                    # if from resource group in only domain should still be
                    # shared
                    included = determine_inclusion(device.flags, [], sub,
                                                   other_sub)
                    if included == 0x1:
                        usage = 0x1

            for f in device.flags:
                if 'no-restrictions' in f:
                    usage = 0x0

            set_dev_pm_reqs(sub, device, usage)


# TODO hard coded for now. need to add this to spec, YAML, etc
def sub_operations_allowed(subsystems, output):
    for sub in subsystems:
        sub_id = sub.sub_node.propval("id")
        if isinstance(sub_id, list):
            sub_id = sub_id[0]

        host_sub_str = "subsystem_" + str(sub_id)
        host_sub_id = 0x1c000000 | sub_id

        for other_sub in subsystems:
            if sub == other_sub:
                continue

            other_sub_id = other_sub.sub_node.propval("id")
            if isinstance(other_sub_id, list):
                other_sub_id = other_sub_id[0]

            other_sub_str = "subsystem_" + hex(other_sub_id)
            other_sub_id = 0x1c000000 | other_sub_id

            cdo_str = "# " + host_sub_str + " can  enact only non-secure ops upon " + other_sub_str
            cdo_cmd = "pm_add_requirement " + hex(host_sub_id) + " "
            cdo_cmd += hex(other_sub_id) + " " + hex(0x7)

            print(cdo_str, file=output)
            print(cdo_cmd, file=output)


# TODO hard coded for now. need to add this to spec, YAML, etc
def sub_ggs_perms(sub, output):
    sub_id = sub.sub_node.propval("id")
    if isinstance(sub_id, list):
        sub_id = sub_id[0]

    cdo_sub_str = "subsystem_" + str(sub_id)
    cdo_sub_id = 0x1c000000 | sub_id

    for i in range(0x18248000, 0x18248003 + 1):
        dev_str = "ggs_" + hex(i & 0x7).replace('0x', '')
        dev_id = hex(i)

        cdo_str = "# " + cdo_sub_str + " can perform non-secure read/write "
        cdo_str += dev_str

        cdo_cmd = "pm_add_requirement " + hex(cdo_sub_id) + " "
        cdo_cmd += dev_id + " "
        cdo_cmd += hex(0x3)
        print(cdo_str, file=output)
        print(cdo_cmd, file=output)


# TODO hard coded for now. need to add this to spec, YAML, etc
def sub_pggs_perms(sub, output):
    sub_id = sub.sub_node.propval("id")
    if isinstance(sub_id, list):
        sub_id = sub_id[0]

    cdo_sub_str = "subsystem_" + str(sub_id)
    cdo_sub_id = 0x1c000000 | sub_id

    for i in range(0x1824c004, 0x1824c007 + 1):
        dev_str = "pggs_" + hex((i & 0x7) - 0x4).replace('0x', '')
        dev_id = hex(i)

        cdo_str = "# " + cdo_sub_str + " can perform non-secure read/write "
        cdo_str += dev_str

        cdo_cmd = "pm_add_requirement " + hex(cdo_sub_id) + " "
        cdo_cmd += dev_id + " "
        cdo_cmd += hex(0x3)
        print(cdo_str, file=output)
        print(cdo_cmd, file=output)


# TODO hard coded for now. need to add this to spec, YAML, etc
def sub_reset_perms(sub, output):
    sub_id = sub.sub_node.propval("id")
    if isinstance(sub_id, list):
        sub_id = sub_id[0]

    cdo_sub_str = "subsystem_" + str(sub_id)
    cdo_sub_id = 0x1c000000 | sub_id

    print("#",
          cdo_sub_str,
          " can enact only non-secure system-reset (rst_pmc)",
          file=output)
    print("pm_add_requirement " + hex(cdo_sub_id) + " 0xc410002 0x1",
          file=output)


def sub_perms(subsystems, output):
    # add cdo commands for sub-sub permissions, reset perms and xGGS perms
    sub_operations_allowed(subsystems, output)

    for sub in subsystems:
        sub_ggs_perms(sub, output)
        sub_pggs_perms(sub, output)
        sub_reset_perms(sub, output)
