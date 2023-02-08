# /*
# * Copyright (c) 2022 - 2023 Advanced Micro Devices, Inc. All Rights Reserved.
# *
# * Author:
# *       Madhav Bhatt <madhav.bhatt@amd.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

import cfgobj_hard_coding as chc

class SdtInfo:
    __sdt__ = None
    masters = {}
    ocm_high_value = None
    ocm_base_value = None
    gpos = {}
    rpu0_as_power_management_master = False
    rpu1_as_power_management_master = False
    apu_as_power_management_master = False
    rpu0_as_reset_management_master = False
    rpu1_as_reset_management_master = False
    apu_as_reset_management_master = False
    rpu0_as_overlay_config_master = False
    rpu1_as_overlay_config_master = False
    apu_as_overlay_config_master = False
    subsys_str = None
    existing_proc_type = None
    proc_type = None

    def __get_cpu_name(self, node):
        try:
            cpu_name = node["xlnx,cpu-name"].value
        except:
            cpu_name = ""
        return cpu_name

    def __is_master_defined(self, master):
        found_master = False
        subsys_str = self.subsys_str
        subsys_list = subsys_str.split("|")
        for subsys in subsys_list:
            if master in subsys.split(":")[1].split(";"):
                found_master = True
        return found_master

    def __get_masters(self):
        masters_tmp = {}
        for master in chc.masters.keys():
            for node in self.__sdt__.tree:
                if chc.masters[master]["name"] in  self.__get_cpu_name(node):
                    if self.__is_master_defined(master):
                        master_tmp[master] = chc.masters[master]
        if len(masters_tmp) != 0:
            self.masters = maaster_tmp
        else:
            self.masters = chc.masters
        return


    def __get_ip_name(self, node):
        try:
            ip_name = node['xlnx,ip-name'].value
        except:
            ip_name = None
        return ip_name

    def __parse_ipi_bit_pos(self):
        ip_name = []
        for node in self.__sdt__.tree:
            ip_name = self.__get_ip_name(node)
            if ip_name != None and "psu_ipi" in ip_name:
                for master in self.masters:
                    if self.masters[master]["name"] in self.__get_cpu_name(node):
                       self.masters[master]["ipi_bit_pos"] = node["xlnx,bit-position"].value[0]
                       self.masters[master]["is_ipi_present"] = True
        return


    def __parse_gpo_info(self):
        ip_name = []
        for node in self.__sdt__.tree:
            ip_name = self.__get_ip_name(node)
            if ip_name != None and "psu_pmu_iomodule" in ip_name:
                for num in chc.gpo_nums:
                    gpo_tmp = {}
                    enable_prop_str = "xlnx,gpo" + str(num) + "-enable"
                    polarity_prop_str = "xlnx,gpo" + str(num) + "-polarity"
                    try:
                        gpo_tmp["enable"] = node[enable_prop_str].value[0]
                    except:
                        gpo_tmp["enable"] = 0
                    if 1 == gpo_tmp["enable"]:
                        gpo_tmp["polarity"] = node[polarity_prop_str].value[0]
                    else:
                        gpo_tmp["polarity"] = 0
                    self.gpos["gpo" + str(num)] = gpo_tmp
                break
        return

    def __parse_ocm_base_high(self):
        ip_name = []
        for node in self.__sdt__.tree:
            if len(node.child_nodes) > 0:
                for child_node in node.child_nodes.values():
                    ip_name = self.__get_ip_name(child_node)
                    if ip_name != None and "psu_ocm_ram_0" in ip_name:
                        base = child_node["reg"].value[1]
                        size = child_node["reg"].value[3]
                        self.ocm_base_value = base
                        self.ocm_high_value = base + size - 1
                        break
        return

    def __parse_slaves_for_master(self):
        nodes_temp = self.__sdt__.tree
        nodelist = {}
        for node in nodes_temp:
            temp = node.name.split('@')
            try:
                nodelist[int(temp[1], 16)] = temp[0]
            except:
                None
        nodes = self.__sdt__.tree.nodes('/cpu.*')
        for node in nodes:
            slaves = []
            try:
                if node.propval('reg') != '':
                    if 'cpus,cluster' in node["compatible"].value:
                        cnt = 0
                        addr_cell_size = node["#ranges-address-cells"].value[0]
                        size_cell_size = node["#ranges-size-cells"].value[0]
                        while cnt < len(node["address-map"].value):
                            addr = 0
                            for cell_num in range(addr_cell_size):
                                addr = addr << 16 | node["address-map"].value[cnt + cell_num]
                            cnt += (2 * addr_cell_size) + size_cell_size + 1
                            for key in chc.node_map.keys():
                                try:
                                    for base_addr in chc.node_map[key]["base_addr"]:
                                        if base_addr == addr:
                                            periph = ""
                                            if "psu_ocm_" in chc.node_map[key]["periph"]:
                                                periph = "psu_ocm_ram_0"
                                            elif "psu_ddr" in chc.node_map[key]["periph"]:
                                                if "cpus-a53" in node.name:
                                                    periph = "psu_ddr_0"
                                                elif "cpus-r5" in  node.name:
                                                    periph = "psu_r5_ddr_0"
                                            else:
                                                periph = chc.node_map[key]["periph"]
                                            if periph not in slaves:
                                                slaves.append(periph)
                                except:
                                    None
                if node.name == "cpus-a53@0":
                    self.masters["psu_cortexa53_0"]["slaves"] = slaves
                elif node.name == "cpus-r5@0":
                    self.masters["psu_cortexr5_0"]["slaves"] = slaves
                elif node.name == "cpus-r5@1":
                    self.masters["psu_cortexr5_1"]["slaves"] = slaves
            except:
                None

    def __get_proc_type(self, options):
        try:
            self.proc_type = options['args'][1]
        except:
            self.proc_type = chc.hardcoded_proc_type
        return

    def __init__(self, sdt, options):
        self.__sdt__ = sdt
        self.__get_proc_type(options)

        # Hard coded values
        self.rpu0_as_power_management_master = chc.rpu0_as_power_management_master
        self.rpu1_as_power_management_master = chc.rpu1_as_power_management_master
        self.apu_as_power_management_master = chc.apu_as_power_management_master
        self.rpu0_as_reset_management_master = chc.rpu0_as_reset_management_master
        self.rpu1_as_reset_management_master = chc.rpu1_as_reset_management_master
        self.apu_as_reset_management_master = chc.apu_as_reset_management_master
        self.rpu0_as_overlay_config_master = chc.rpu0_as_overlay_config_master
        self.rpu1_as_overlay_config_master = chc.rpu1_as_overlay_config_master
        self.apu_as_overlay_config_master = chc.apu_as_overlay_config_master
        self.subsys_str = chc.subsys_str

        # Parsing values from SDT
        self.__get_masters()
        self.__parse_slaves_for_master()
        self.__parse_ipi_bit_pos()
        self.__parse_ocm_base_high()
        self.__parse_gpo_info()
