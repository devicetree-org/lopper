# /*
# * Copyright (c) 2022 - 2023 Advanced Micro Devices, Inc. All Rights Reserved.
# *
# * Author:
# *       Madhav Bhatt <madhav.bhatt@amd.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

import sys
import os
import re

sys.path.append(os.path.dirname(__file__))
sys.path.append(os.path.dirname(__file__) + "/cfg_obj/modules/")

from section import Section
import cfgobj_hard_coding as chc
from sdtinfo import SdtInfo
import cfg_data_tpl

def props():
    return ["id", "file_ext"]

def id():
    return "xlnx,output,cfgobj"

def file_ext():
    return ".c"

def is_compat(node, compat_id):
    if re.search("module,generate_config_object", compat_id):
        return cfg_obj_write
    return ""

def get_slaves_for_master(sdtinfo_obj, master):
    try:
        slave_list = sdtinfo_obj.masters[master]["slaves"]
    except:
        slave_list = []
    return slave_list

def is_rpu_lockstep(sdtinfo_obj):
    subsys_str = sdtinfo_obj.subsys_str
    found_rpu0 = False
    found_rpu1 = False

    subsys_list  = subsys_str.split("|")
    for subsys in subsys_list:
        if "RPU0" in subsys.split(":")[1].split(";"):
            found_rpu0 = True
        if "RPU1" in subsys.split(":")[1].split(";"):
            found_rpu1 = True
    if found_rpu0 == True and found_rpu1 == False:
        return True
    else:
        return False

def mask_to_str(mask):
    return "0x%08X"%mask

def is_ipi_present(master, sdtinfo_obj):
    try:
        if sdtinfo_obj.masters[master]["is_ipi_present"] == True:
            return True
        else:
            return False
    except:
        return False

def get_ipi_mask(master, sdtinfo_obj):
    if is_ipi_present(master, sdtinfo_obj) == True:
        bit_pos = sdtinfo_obj.masters[master]["ipi_bit_pos"]
        return (1<<bit_pos)
    else:
        return 0

def get_ipi_mask_txt(master, sdtinfo_obj):
    if is_ipi_present(master, sdtinfo_obj) == True:
        return "PM_CONFIG_IPI_" + master.upper() + "_MASK"
    else:
        return ""

def get_all_masters_mask_txt(sdtinfo_obj):
    macro_list = []
    for master in sdtinfo_obj.masters.keys():
        if is_ipi_present(master, sdtinfo_obj) == True:
            macro_list.append(get_ipi_mask_txt(master, sdtinfo_obj))
    if len(macro_list) > 0:
        return " | ".join(macro_list)
    else:
        return "0U"

def get_all_other_masters_mask_txt(master_name, sdtinfo_obj):
    macro_list = []
    for master in sdtinfo_obj.masters.keys():
        if (master != master_name) and (is_ipi_present(master, sdtinfo_obj) == True):
            if "psu_cortexa53_0" == master_name:
                if sdtinfo_obj.rpu0_as_power_management_master == False and \
                    "psu_cortexr5_0" == master:
                    continue
                if sdtinfo_obj.rpu1_as_power_management_master == False and \
                    False == is_rpu_lockstep(sdtinfo_obj) and "psu_cortexr5_1" == master:
                    continue
            elif "psu_cortexr5_0" == master_name:
                if sdtinfo_obj.apu_as_power_management_master == False and \
                    "psu_cortexa53_0" == master:
                    continue
                if sdtinfo_obj.rpu1_as_power_management_master == False and \
                    False == is_rpu_lockstep(sdtinfo_obj) and "psu_cortexr5_1" == master:
                    continue
            elif False == is_rpu_lockstep(sdtinfo_obj) and "psu_cortexr5_1" == master_name:
                if sdtinfo_obj.apu_as_power_management_master == False and \
                    "psu_cortexa53_0" == master:
                    continue
                if sdtinfo_obj.rpu0_as_power_management_master == False and \
                    "psu_cortexr5_0" == master:
                    continue
            macro_list.append(get_ipi_mask_txt(master, sdtinfo_obj))
    if len(macro_list) > 0:
        return " | ".join(macro_list)
    else:
        return "0U"

def get_slave_perm_mask_txt(periph, sdtinfo_obj):
    macro_list = []
    for master in sdtinfo_obj.masters.keys():
        slave_list = get_slaves_for_master(sdtinfo_obj, master)
        for slave in slave_list:
            if periph == slave and (is_ipi_present(master, sdtinfo_obj) == True):
                macro_list.append(get_ipi_mask_txt(master, sdtinfo_obj))
    if len(macro_list) > 0:
        return " | ".join(macro_list)
    else:
        return "0U"


def get_slave_perm_mask(periph, sdtinfo_obj):
    perm_mask = 0x00000000
    for master in sdtinfo_obj.masters.keys():
        slave_list = get_slaves_for_master(sdtinfo_obj, master)
        for slave in slave_list:
            if re.search(periph,slave) != None and (is_ipi_present(master, sdtinfo_obj) == True):
                perm_mask  = perm_mask | get_ipi_mask(master, sdtinfo_obj)
    return perm_mask

def get_tcm_r5_perm_mask(r5_proc, tcm_bank, sdtinfo_obj):
    perm_mask = 0x00000000
    if "psu_cortexr5_0" == r5_proc:
        if is_rpu_lockstep(sdtinfo_obj) == True:
            perm_mask = get_ipi_mask(r5_proc, sdtinfo_obj)
        else:
            if tcm_bank == "psu_r5_0_atcm_global" or tcm_bank == "psu_r5_0_btcm_global":
                perm_mask = get_ipi_mask(r5_proc, sdtinfo_obj)
    elif r5_proc == "psu_cortexr5_1":
        if is_rpu_lockstep(sdtinfo_obj) == True:
            perm_mask = 0x00000000
        else:
            if tcm_bank == "psu_r5_1_atcm_global" or tcm_bank == "psu_r5_1_btcm_global":
                perm_mask = get_ipi_mask(r5_proc, sdtinfo_obj)
    else:
        perm_mask = 0x00000000
    return perm_mask

def convert_ipi_mask_to_txt(ipi_mask, sdtinfo_obj):
    macro_list = []
    for master in sdtinfo_obj.masters.keys():
        if ((ipi_mask & get_ipi_mask(master, sdtinfo_obj)) != 0) and is_ipi_present(master, sdtinfo_obj) == True:
            macro_list.append(get_ipi_mask_txt(master, sdtinfo_obj))
    if len(macro_list) > 0:
        return " | ".join(macro_list)
    else:
        return "0U"

def get_tcm_perm_mask(tcm, sdtinfo_obj):
    perm_mask = 0x00000000
    for master in sdtinfo_obj.masters.keys():
        if re.search("psu_cortexr5_*", master) != None:
            perm_mask  = perm_mask | get_tcm_r5_perm_mask(master, tcm, sdtinfo_obj)
        else:
            slave_list = get_slaves_for_master(sdtinfo_obj, master)
            for slave in slave_list:
                if tcm == slave:
                    perm_mask  = perm_mask | get_ipi_mask(master, sdtinfo_obj)
    return perm_mask

def get_ocm_perm_mask(ocm, sdtinfo_obj):
    perm_mask = 0x00000000
    island_base = sdtinfo_obj.ocm_base_value
    island_high = sdtinfo_obj.ocm_high_value
    for master in sdtinfo_obj.masters.keys():
        if "psu_ocm_ram_0" in get_slaves_for_master(sdtinfo_obj, master):
            base_val = sdtinfo_obj.ocm_base_value
            high_val = sdtinfo_obj.ocm_high_value
            if ((island_base >= base_val) and (island_base <= high_val)) or ((island_high >= base_val) and (island_high <= high_val)):
                perm_mask = perm_mask | get_ipi_mask(master, sdtinfo_obj)
    return perm_mask

def get_mem_perm_mask(mem, sdtinfo_obj):
    perm_mask = 0x00000000
    if "psu_ddr" in mem:
        perm_mask = get_slave_perm_mask("psu_ddr_", sdtinfo_obj) | get_slave_perm_mask("psu_r5_ddr_", sdtinfo_obj)
    elif "psu_ocm_" in mem:
        perm_mask  = get_ocm_perm_mask(mem, sdtinfo_obj)
    elif re.search("psu_r5_.*tcm_global", mem) != None:
        perm_mask = get_tcm_perm_mask(mem, sdtinfo_obj)
    else:
        perm_mask = 0x00
    return perm_mask

def get_power_domain_perm_mask_txt(pwr_domain, sdtinfo_obj):
    macro_list = []
    pwr_perm_masters = chc.power_perms[pwr_domain]
    for master in pwr_perm_masters:
        if master in sdtinfo_obj.masters.keys() and is_ipi_present(master, sdtinfo_obj) == True:
            if (sdtinfo_obj.apu_as_power_management_master == False) and ("psu_cortexa53_0" == master):
                continue
            elif (sdtinfo_obj.rpu0_as_power_management_master == False) and ("psu_cortexr5_0" == master):
                continue
            elif (sdtinfo_obj.rpu1_as_power_management_master == False) and ("psu_cortexr5_1" == master):
                continue
            macro_list.append(get_ipi_mask_txt(master, sdtinfo_obj))
    if (pwr_domain == "NODE_FPD" or pwr_domain == "NODE_APU") and (len(macro_list) == 0):
        macro_list.append("psu_cortexa53_0")
    if len(macro_list) > 0:
        return " | ".join(macro_list)
    else:
        return "0U"

def is_all_master_enabled(master_type, sdtinfo_obj):
    if "power" == master_type:
        rpu0_as_master = sdtinfo_obj.rpu0_as_power_management_master
        rpu1_as_master = sdtinfo_obj.rpu1_as_power_management_master
        apu_as_master =  sdtinfo_obj.apu_as_power_management_master
    elif "reset" == master_type:
        rpu0_as_master = sdtinfo_obj.rpu0_as_reset_management_master
        rpu1_as_master = sdtinfo_obj.rpu1_as_reset_management_master
        apu_as_master = sdtinfo_obj.apu_as_reset_management_master
    else:
        return -1
    if rpu0_as_master == True and apu_as_master == True:
        if is_rpu_lockstep(sdtinfo_obj) == False:
            if rpu1_as_master == True:
                return 1
            else:
                return 0
        else:
            return 1
    else:
        return 0


def get_list_of_management_master(master_type, sdtinfo_obj):
    macro_list = []
    if "power" == master_type:
        rpu0_as_master = sdtinfo_obj.rpu0_as_power_management_master
        rpu1_as_master = sdtinfo_obj.rpu1_as_power_management_master
        apu_as_master =  sdtinfo_obj.apu_as_power_management_master
    elif "reset" == master_type:
        rpu0_as_master = sdtinfo_obj.rpu0_as_reset_management_master
        rpu1_as_master = sdtinfo_obj.rpu1_as_reset_management_master
        apu_as_master = sdtinfo_obj.apu_as_reset_management_master
    elif "overlay_config" == master_type:
        rpu0_as_master = sdtinfo_obj.rpu0_as_overlay_config_master
        rpu1_as_master = sdtinfo_obj.rpu1_as_overlay_config_master
        apu_as_master = sdtinfo_obj.apu_as_overlay_config_master
    else:
        return "0U"
    for master in sdtinfo_obj.masters.keys():
        if is_ipi_present(master, sdtinfo_obj) != 0:
            if (apu_as_master == False) and ("psu_cortexa53_0" == master):
                continue
            elif (rpu0_as_master == False) and ("psu_cortexr5_0" == master):
                continue
            elif (rpu1_as_master == False) and ("psu_cortexr5_1" == master):
                continue
            macro_list.append(get_ipi_mask_txt(master, sdtinfo_obj))
    if len(macro_list) > 0:
        return " | ".join(macro_list)
    else:
        return "0U"

def generate_master_ipi_mask_def(sdtinfo_obj):
    out_lines = ["\n"]
    for master in sdtinfo_obj.masters.keys():
        if is_ipi_present(master, sdtinfo_obj) == True:
            out_lines.append("#define " + get_ipi_mask_txt(master, sdtinfo_obj) + "    " + mask_to_str(get_ipi_mask(master, sdtinfo_obj)) + "\n")
    out_lines.append("\n\n")
    return out_lines

def get_prealloc_for_master_txt(master_name, prealloc_list, sdtinfo_obj):
    node_count = 0
    master_prealloc_txt = []
    if is_ipi_present(master_name, sdtinfo_obj) != "":
        master_mask = get_ipi_mask_txt(master_name, sdtinfo_obj)
        master_prealloc_txt.append("\t/* Prealloc for " + master_name + " */\n")
        master_prealloc_txt.append("\t" + master_mask + ",\n")
        for key in prealloc_list:
            periph_perms = chc.node_map[key]['perms']
            periph_name = chc.node_map[key]['periph']
            periph_type = chc.node_map[key]['type']
            periph_label = chc.node_map[key]['label']
            if master_mask in periph_perms:
                master_prealloc_txt.append("\t" + periph_label + ",\n")
                master_prealloc_txt.append("\tPM_MASTER_USING_SLAVE_MASK, /* Master is using Slave */\n")
                master_prealloc_txt.append("\tPM_CAP_ACCESS | PM_CAP_CONTEXT, /* Current Requirements */\n")
                master_prealloc_txt.append("\tPM_CAP_ACCESS | PM_CAP_CONTEXT, /* Default Requirements */\n")
                master_prealloc_txt.append("\n")
                node_count += 1
    master_prealloc_txt.insert(2, "\t" + str(node_count) + ",\n")
    master_prealloc_txt.append("\n")
    return master_prealloc_txt


def generate_master_section_data(sdtinfo_obj):
    out_lines = []
    out_lines.append("\tPM_CONFIG_MASTER_SECTION_ID, /* Master SectionID */" + "\n")
    master_count = len(sdtinfo_obj.masters.keys())
    out_lines.append("\t" + str(master_count) + "U, /* No. of Masters*/" + "\n")
    out_lines.append("\n")
    for master in sdtinfo_obj.masters.keys():
        if sdtinfo_obj.masters[master]["name"] == "RPU0":
            if is_rpu_lockstep(sdtinfo_obj) == True:
                master_node = "NODE_RPU"
            else:
                master_node = "NODE_RPU_0"
        elif sdtinfo_obj.masters[master]["name"] == "RPU1":
            master_node = "NODE_RPU_1"
        elif sdtinfo_obj.masters[master]["name"] == "APU":
            master_node = "NODE_APU"
        out_lines.append("\t" + master_node + ", /* Master Node ID */" + "\n")
        if is_ipi_present(master, sdtinfo_obj) == True:
            out_lines.append("\t" + get_ipi_mask_txt(master, sdtinfo_obj) + ", /* IPI Mask of this master */" + "\n")
        else:
            out_lines.append("\t0U, /* IPI Mask of this master */" + "\n")
        out_lines.append("\tSUSPEND_TIMEOUT, /* Suspend timeout */" + "\n")
        out_lines.append("\t" +  get_all_other_masters_mask_txt(master, sdtinfo_obj) + ", /* Suspend permissions */" + "\n")
        out_lines.append("\t" +  get_all_other_masters_mask_txt(master, sdtinfo_obj) + ", /* Wake permissions */" + "\n")
        out_lines.append("\n")
    out_lines.append("\n")
    return out_lines

def generate_slave_section_data(sdtinfo_obj):
    out_lines = ["\n\n\tPM_CONFIG_SLAVE_SECTION_ID,\t/* Section ID */\n"]
    slave_count = 0
    for key in chc.node_map:
        periph_name = chc.node_map[key]['periph']
        periph_type = chc.node_map[key]['type']
        periph_label = chc.node_map[key]['label']
        if periph_type == "slave":
            chc.node_map[key]['perms'] = get_slave_perm_mask_txt(periph_name, sdtinfo_obj)
        elif (periph_type == "memory") and (periph_type != "NA"):
            chc.node_map[key]['perms'] = convert_ipi_mask_to_txt(get_mem_perm_mask(periph_name, sdtinfo_obj), sdtinfo_obj)
        elif periph_type == "others":
            chc.node_map[key]['perms'] = get_all_masters_mask_txt(sdtinfo_obj)

        if ("slave" == periph_type) or ("memory" == periph_type and "NA" != periph_type) or ("others" == periph_type):
            if chc.node_map[key]['perms'] == "0U":
                continue
            slave_count += 1
            out_lines.append("\t" + periph_label + ",\n")
            out_lines.append("\tPM_SLAVE_FLAG_IS_SHAREABLE,\n")
            out_lines.append("\t" + chc.node_map[key]['perms']+ ", /* IPI Mask */\n\n")

        ipi_perm = ""
        if periph_type == "ipi":
            if periph_label == "NODE_IPI_APU":
                if "psu_cortexa53_0" in sdtinfo_obj.masters.keys() and is_ipi_present("psu_cortexa53_0", sdtinfo_obj) != "":
                    ipi_perm = get_ipi_mask_txt("psu_cortexa53_0", sdtinfo_obj)
                else:
                    ipi_perm = ""
            elif periph_label == "NODE_IPI_RPU_0":
                if "psu_cortexr5_0" in sdtinfo_obj.masters.keys() and is_ipi_present("psu_cortexr5_0", sdtinfo_obj) != "":
                    ipi_perm = get_ipi_mask_txt("psu_cortexr5_0", sdtinfo_obj)
                else:
                    ipi_perm = ""
            elif periph_label == "NODE_IPI_RPU_1":
                if "psu_cortexr5_1" in sdtinfo_obj.masters.keys() and is_ipi_present("psu_cortexr5_1", sdtinfo_obj) != "":
                    ipi_perm = get_ipi_mask_txt("psu_cortexr5_1", sdtinfo_obj)
                else:
                    ipi_perm = ""
            else:
                ipi_perm = ""
            if ipi_perm != "":
                slave_count += 1
                chc.node_map[key]['perms'] = ipi_perm
                out_lines.append("\t" + periph_label + ",\n")
                out_lines.append("\t0U,\n")
                out_lines.append("\t" + chc.node_map[key]['perms']+ ", /* IPI Mask */\n\n")

    out_lines.insert(1, "\t" + str(slave_count) + ",\t\t\t\t/* Number of slaves */\n\n")
    out_lines.append("\n")
    return out_lines

def generate_prealloc_section_data(sdtinfo_obj):
    out_lines = ["\n"]
    master_count = 0
    proc_type = sdtinfo_obj.proc_type
    if is_ipi_present("psu_cortexa53_0", sdtinfo_obj) != "":
        chc.apu_prealloc_list.append("NODE_IPI_APU")
    if proc_type == "psu_cortexr5_0":
        chc.rpu_0_prealloc_list.extend(chc.rpu_0_prealloc_conditional_list)
    if is_ipi_present("psu_cortexr5_0", sdtinfo_obj) != "":
        chc.rpu_0_prealloc_list.append("NODE_IPI_RPU_0")
    if is_ipi_present("psu_cortexr5_1", sdtinfo_obj) != "":
        chc.rpu_1_prealloc_list.append("NODE_IPI_RPU_1")
    out_lines.append("\tPM_CONFIG_PREALLOC_SECTION_ID, /* Preallaoc SectionID */\n")
    for master in sdtinfo_obj.masters.keys():
        if is_ipi_present(master, sdtinfo_obj) == True:
            master_count += 1
    out_lines.append("\t" + str(master_count) + "U, /* No. of Masters*/\n")
    out_lines.append("\n")
    if "psu_cortexa53_0" in sdtinfo_obj.masters.keys() and is_ipi_present("psu_cortexa53_0", sdtinfo_obj):
        out_lines.extend(get_prealloc_for_master_txt("psu_cortexa53_0", chc.apu_prealloc_list, sdtinfo_obj))
    if "psu_cortexr5_0" in sdtinfo_obj.masters.keys() and is_ipi_present("psu_cortexr5_0", sdtinfo_obj):
        out_lines.extend(get_prealloc_for_master_txt("psu_cortexr5_0", chc.rpu_0_prealloc_list, sdtinfo_obj))
    if "psu_cortexr5_1" in sdtinfo_obj.masters.keys() and is_ipi_present("psu_cortexr5_1", sdtinfo_obj):
        out_lines.extend(get_prealloc_for_master_txt("psu_cortexr5_1", chc.rpu_1_prealloc_list, sdtinfo_obj))
    out_lines.append("\t\n")
    return out_lines

def generate_power_section_data(sdtinfo_obj):
    out_lines = ["\n"]
    out_lines.append("\tPM_CONFIG_POWER_SECTION_ID, /* Power Section ID */\n")
    out_lines.append("\t" + str(len(chc.power_node_list)) + "U, /* Number of power nodes */\n")
    out_lines.append("\n")
    for node in chc.power_node_list:
        out_lines.append("\t" + node + ", /* Power node ID */\n")
        out_lines.append("\t" + get_power_domain_perm_mask_txt(node, sdtinfo_obj) + ", /* Force power down permissions */\n")
        out_lines.append("\n")
    out_lines.append("\n")
    return out_lines

def generate_reset_section_data(sdtinfo_obj):
    out_lines = ["\n"]
    reset_management_master_list = get_list_of_management_master("reset", sdtinfo_obj)
    out_lines.append("\tPM_CONFIG_RESET_SECTION_ID, /* Reset Section ID */\n")
    out_lines.append("\t" + str(len(chc.reset_line_map)) + "U, /* Number of resets */\n")
    out_lines.append("\n")
    for reset_line in chc.reset_line_map:
        line_name = chc.reset_line_map[reset_line]["label"]
        line_type = chc.reset_line_map[reset_line]["type"]
        if line_type == "normal":
            out_lines.append("\t" + line_name + ", " + get_all_masters_mask_txt(sdtinfo_obj) + ",\n")
        elif line_type == "rpu_only":
            if ("psu_cortexr5_0" in sdtinfo_obj.masters.keys()) and (is_ipi_present("psu_cortexr5_0", sdtinfo_obj) != ""):
                out_lines.append("\t" + line_name + ", " + get_ipi_mask_txt("psu_cortexr5_0", sdtinfo_obj) + ",\n")
            else:
                out_lines.append("\t" + line_name + ", 0,\n")
        elif (1 == is_all_master_enabled("reset", sdtinfo_obj)) and ((line_type == "rst_periph") or (line_type == "rst_shared" ) or (line_type == "rst_proc")) :
            out_lines.append("\t" + line_name + ", " + get_all_masters_mask_txt(sdtinfo_obj) + ",\n")
        elif (0 == is_all_master_enabled("reset", sdtinfo_obj)) and ((line_type == "rst_periph") or (line_type == "rst_shared" ) or (line_type == "rst_proc")) :
            if line_type == "rst_periph":
                perms = get_periph_perm_mask_txt_for_rst_line(reset_line)
                out_lines.append("\t" + line_name + ", " + perms + ",\n")
            elif line_type == "rst_shared":
                out_lines.append("\t" + line_name + ", " + reset_management_master_list + ",\n")
            elif line_type == "rst_proc":
                line_proc = chc.reset_line_map[reset_line]["proc"]
                macro_list  = []
                master_txt = []
                if line_proc == "APU":
                    master_txt.append(get_ipi_mask_txt("psu_cortexa53_0", sdtinfo_obj))
                elif (line_proc == "RPU_1") or (line_proc == "RPU" and is_rpu_lockstep(sdtinfo_obj)) or line_proc == "RPU_0" :
                    master_txt.append(get_ipi_mask_txt("psu_cortexr5_0", sdtinfo_obj))
                elif line_proc == "RPU_1":
                    master_txt.append(get_ipi_mask_txt("psu_cortexr5_1", sdtinfo_obj))
                elif line_proc == "RPU":
                    master_rpu_0 = get_ipi_mask_txt("psu_cortexr5_0", sdtinfo_obj)
                    master_rpu_1 = get_ipi_mask_txt("psu_cortexr5_1", sdtinfo_obj)
                    if ((master_rpu_0 in reset_management_master_list) and len(master_rpu_0) > 0) and \
                       ((master_rpu_1 in reset_management_master_list) and len(master_rpu_1) > 0):
                        master_txt.append(master_rpu_0 + " | " + master_rpu_1)
                if (master_txt in reset_management_master_list) and len(master_txt) > 0:
                    macro_list.append(master_txt)
                if "0U" == reset_management_master_list:
                    if len(macro_list) > 0:
                        macro_list.append(" | ")
                    macro_list.append(reset_management_master_list)
                if len(macro_list) == 0:
                    out_lines.append("\t" + line_name + ",  " + macro_list + ",\n")
        else:
            out_lines.append("\t" + line_name + ", 0,\n")
    out_lines.append("\n")
    return out_lines

def generate_set_config_section_data(sdtinfo_obj):
    out_lines = []
    overlay_config_master_list = get_list_of_management_master("overlay_config", sdtinfo_obj)
    out_lines.append("\tPM_CONFIG_SET_CONFIG_SECTION_ID,\t\t/* Set Config Section ID */\n")
    out_lines.append("\t0U, /* Permissions to load base config object */\n")
    out_lines.append("\t" + overlay_config_master_list + ", /* Permissions to load overlay config object */\n")
    out_lines.append("\n")

    return out_lines

def generate_shutdown_section_data(sdtinfo_obj):
    out_lines = ["\n"]
    power_management_master_list = get_list_of_management_master("power", sdtinfo_obj)
    out_lines.append("\tPM_CONFIG_SHUTDOWN_SECTION_ID, /* Shutdown Section ID */\n")
    out_lines.append("\t" + power_management_master_list + ", /* System Shutdown/Restart Permission */\n")
    out_lines.append("\n")
    return out_lines

def generate_gpo_section_data(sdtinfo_obj):
    out_lines = []
    out_lines.append("\tPM_CONFIG_GPO_SECTION_ID,\t\t/* GPO Section ID */\n")
    for num in chc.gpo_nums:
        if 1 == sdtinfo_obj.gpos["gpo" + str(num)]["polarity"]:
            out_lines.append("\tPM_CONFIG_GPO1_BIT_" + str(num) + "_MASK |\n")
        if 1 == sdtinfo_obj.gpos["gpo" + str(num)]["enable"]:
            out_lines.append("\tPM_CONFIG_GPO1_MIO_PIN_"+ str(32+num) +"_MAP |\n")
    out_lines.append("\t0,\t\t\t\t\t/* State of GPO pins */")
    out_lines.append("\n")
    return out_lines

def generate_tpl_lines():
    final_lines = cfg_data_tpl.config_object_template.split('\n')
    for line_num in range(len(final_lines)):
        final_lines[line_num] += '\n'
    return final_lines

def cfg_obj_write(root_node, sdt, options):
    if options.get('outdir', {}):
        sdt.outdir = options['outdir']
    sdtinfo_obj = SdtInfo(sdt, options)

    final_lines = generate_tpl_lines()

    sections = [
                {"identifier" : "<<MASTER_IPI_MASK_DEF>>",     "handler" : generate_master_ipi_mask_def},
                {"identifier" : "<<MASTER_SECTION_DATA>>",     "handler" : generate_master_section_data},
                {"identifier" : "<<SLAVE_SECTION_DATA>>",      "handler" : generate_slave_section_data},
                {"identifier" : "<<PREALLOC_SECTION_DATA>>",   "handler" : generate_prealloc_section_data},
                {"identifier" : "<<POWER_SECTION_DATA>>",      "handler" : generate_power_section_data},
                {"identifier" : "<<RESET_SECTION_DATA>>",      "handler" : generate_reset_section_data},
                {"identifier" : "<<SET_CONFIG_SECTION_DATA>>", "handler" : generate_set_config_section_data},
                {"identifier" : "<<SHUTDOWN_SECTION_DATA>>",   "handler" : generate_shutdown_section_data},
                {"identifier" : "<<GPO_SECTION_DATA>>",        "handler" : generate_gpo_section_data},
               ]

    for section in sections:
        section_obj =  Section(section["identifier"], section["handler"], sdtinfo_obj)
        final_lines = section_obj.replace_section(final_lines)

    outfile_name = options["args"][0]
    outfile = os.path.join(sdt.outdir, outfile_name)
    outfile = open(outfile, 'w')
    outfile.writelines(final_lines)
    return True
