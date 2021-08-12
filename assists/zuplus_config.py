# -*- coding: utf-8 -*-
import sys
import types
import os
import getopt
import re
from pathlib import Path
from pathlib import PurePath
from io import StringIO
import contextlib
import importlib
from lopper import Lopper
from lopper import LopperFmt
import lopper
from lopper_tree import *
from re import *
import numpy as np

sys.path.append(os.path.dirname(__file__))
from bmcmake_metadata_xlnx import *
from baremetalconfig_xlnx import *

memory_dict = { '0xffa70000' : 'OCM_XMPU', '0xfd5d0000' : 'FPD_XMPU', '0xfd000000' : 'DDR_XMPU0', '0xfd010000' : 'DDR_XMPU1', '0xfd020000' : 'DDR_XMPU2', '0xfd030000' : 'DDR_XMPU3', '0xfd040000' : 'DDR_XMPU4', '0xfd050000' : 'DDR_XMPU5'}

def is_compat(node, compat_string_to_test):
    if re.search( "module,zuplus_config_new", compat_string_to_test):
        return configuration
    return ""

def parity(integer):
    count = 0
    parity = 0
    for i in range(0, len(integer)):
        if integer[i] == 1:
            count = count + 1
    if count%2 == 1:
        parity = 1
    return parity

def base_add(offset):
    offset_number = int(str(offset[22]+offset[23]+offset[24]))
    base_address = "0x0"
    size = "0x10000"
    for i in range(0, offset_number):
        #base_address = hex(int(base_address,16)+int(size,16))
        base_address = '0x'+(hex(int(base_address,16)+int(size,16)).upper())[2:].zfill(8)
    return base_address

def permission(value):
    perm = np.zeros(shape=(20), dtype = int)
    for i in range(0,20):
        perm[i] = value[12+i]
    perm_str = ""
    for i in range(0,20):
        perm_str = perm_str + str(perm[i])
    permission = hex(int(str(perm_str), 2))
    return permission

def parity_value(value):
    par = np.zeros(shape=(4), dtype = int)
    for i in range(0,4):
        par[i] = value[i]
    par_str = ""
    for i in range(0,4):
        par_str = par_str + str(par[i])
    parity_value = hex(int(str(par_str), 2))
    return parity_value

def memory_region_register(memory, n):
    register_dict = {'DDR_XMPU0' : '0xFD000100', 'DDR_XMPU1' : '0xFD010100', 'DDR_XMPU2' : '0xFD020100', 'DDR_XMPU3' : '0xFD030100', 'DDR_XMPU4' : '0xFD040100', 'DDR_XMPU5' : '0xFD050100', 'OCM_XMPU' : '0xFFA70100', 'FPD_XMPU' : '0xFD5D0100'}
    register = register_dict[str(memory)]
    region_number = int(n)
    for i in range(0,region_number):
        register = hex(int(register,16)+int("0x10",16)).upper()
    return register

def start_address(memory, address):
    if memory == "OCM_XMPU":
        address_bin = bin(address).replace("0b", "").zfill(32)
        value = np.zeros(shape=(32), dtype = int)
        for i in range(12,32):
            value[i] = str(address_bin[i-12])
                
    if "DDR_XMPU" in memory:
        address_bin = bin(address).replace("0b", "").zfill(40)
        value = np.zeros(shape=(32), dtype = int)
        for i in range(4,24):
            value[i] = str(address_bin[i-4])

    value_str = "" 
    value_hex = ""
    for i in range(0,32):
        value_str = value_str + str(value[i])
    # Converting Value from binary to hexadecimal
    #value_hex = hex(int(str(value_str), 2))
    value_hex = '0x'+(hex(int(str(value_str), 2)).upper())[2:].zfill(8)

    return value_hex

def end_address(memory, address):
    if memory == "OCM_XMPU":
        address_bin = bin(address).replace("0b", "").zfill(32)
        value = np.zeros(shape=(32), dtype = int)
        for i in range(12,32):
            value[i] = str(address_bin[i-12])
                
    if "DDR_XMPU" in memory:
        address_bin = bin(address).replace("0b", "").zfill(40)
        value = np.zeros(shape=(32), dtype = int)
        for i in range(4,24):
            value[i] = str(address_bin[i-4])
        for i in range(24,32):
            value[i] = 1

    value_str = "" 
    value_hex = ""
    for i in range(0,32):
        value_str = value_str + str(value[i])
    # Converting Value from binary to hexadecimal
    #value_hex = hex(int(str(value_str), 2))
    value_hex = '0x'+(hex(int(str(value_str), 2)).upper())[2:].zfill(8)

    return value_hex

def region_00(reg_value, start_add, end_add):
    memory = memory_dict[reg_value]
    region_00 = ["","","",""]
    region_00[0] = region_00[0] + "    /*\n    * Register : R00_CONFIG @ " + str(hex(int(memory_region_register(memory, "00"),16)+int("0xC",16)).upper()) + "\n\n    * 0: Region is disabled 1: Region is enabled\n    *  PSU_" + memory + "_CFG_R00_CONFIG_ENABLE                         1\n\n    * 0: Relaxed NS checking. A secure access is allowed to access secure or n\n    * on-secure region based on Rd/WrAllowed configuration. A non-secure acces\n    * s can only access non-secure region based on Rd/WrAllowed configuration\n    * 1: Strict NS checking. A secure access can only access secure region bas\n    * ed on Rd/WrAllowed configuration. A non-secure access can only access no\n    * n-secure region based on Rd/WrAllowed configuration\n    *  PSU_" + memory + "_CFG_R00_CONFIG_NSCHECKTYPE                    0\n\n    * 0: Region is configured to be secure 1: Region is configured to be non-s\n    * ecure (NS)\n    *  PSU_" + memory + "_CFG_R00_CONFIG_REGIONNS                       0\n\n    * 0: Write address matching this region are poisoned 1: Write address matc\n    * hing this region are allowed\n    *  PSU_" + memory + "_CFG_R00_CONFIG_WRALLOWED                      0\n\n    * 0: Read address matching this region are poisoned 1: Read address matchi\n    * ng this region are allowed\n    *  PSU_" + memory + "_CFG_R00_CONFIG_RDALLOWED                      0\n\n    * Region Configuration Register\n    * (OFFSET, MASK, VALUE)      (" + str(hex(int(memory_region_register(memory, "00"),16)+int("0xC",16)).upper()) + ", 0x0000001FU ,0x00000001U)\n    */\n	PSU_Mask_Write(" + memory + "_CFG_R00_CONFIG_OFFSET,\n		0x0000001FU, 0x00000001U);\n/*##################################################################### */\n\n"
    start = start_address(memory, start_add)
    region_00[1] = region_00[1] + "    /*\n    * Register : R00_START @ " + str(memory_region_register(memory, "00")) + "\n\n    * This field sets the start address bits [39:12] of this region (aligned t\n    * o 4kB). All bits of this field are used during comparison.\n    *  PSU_" + memory + "_CFG_R00_START_ADDR                            " + str(int(start,16)) + "\n\n    * Region Start Address Register\n    * (OFFSET, MASK, VALUE)      (" + str(memory_region_register(memory, "00")) + ", 0x0FFFFFFFU ," + str(start) + "U)\n    */\n	PSU_Mask_Write(" + memory + "_CFG_R00_START_OFFSET,\n		0x0FFFFFFFU, " + str(start) + "U);\n/*##################################################################### */\n\n"

    end = end_address(memory, end_add)
    region_00[2] = region_00[2] + "    /*\n    * Register : R00_END @ " + str(hex(int(memory_region_register(memory, "00"),16)+int("0x4",16)).upper()) + "\n\n    * This field sets the end address bits [39:12] of this region (aligned t\n    * o 4kB). All bits of this field are used during comparison.\n    *  PSU_" + memory + "_CFG_R00_END_ADDR                            " + str(int(end,16)) + "\n\n    * Region End Address Register\n    * (OFFSET, MASK, VALUE)      (" + str(hex(int(memory_region_register(memory, "00"),16)+int("0x4",16)).upper()) + ", 0x0FFFFFFFU ," + str(end) + "U)\n    */\n	PSU_Mask_Write(" + memory + "_CFG_R00_END_OFFSET,\n		0x0FFFFFFFU, " + str(end) + "U);\n/*##################################################################### */\n\n"

    region_00[3] = region_00[3] + "    /*\n    * Register : R00_MASTER @ " + str(hex(int(memory_region_register(memory, "00"),16)+int("0x8",16)).upper()) + "\n\n    * Master ID mask.\n    *  PSU_" + memory + "_CFG_R00_MASTER_MASK                           0\n\n    * Master ID value. An AXI MasterID will match this Master ID value of this\n    *  region if: AXI_MasterID AND MASK == ID AND MASK\n    *  PSU_" + memory + "_CFG_R00_MASTER_ID                             0\n\n    * Region Master ID Register\n    * (OFFSET, MASK, VALUE)      (" + str(hex(int(memory_region_register(memory, "00"),16)+int("0x8",16)).upper()) + ", 0x03FF03FFU ,0x00000000U)\n    */\n	PSU_Mask_Write(" + memory + "_CFG_R00_MASTER_OFFSET,\n		0x03FF03FFU, 0x00000000U);\n/*##################################################################### */\n"

    return region_00

def dap_region(reg_value, start_add, end_add, n):
    memory = memory_dict[reg_value]
    region_dap = ["","","",""]
    if "DDR_XMPU" in memory:
        config_value = "0x0000000F"
        master_value = "0x03FF0062"
    region_dap[0] = region_dap[0] + "    /*\n    * Register : R" + str(n) + "_CONFIG @ " + str(hex(int(memory_region_register(memory, n),16)+int("0xC",16)).upper()) + "\n\n    * 0: Region is disabled 1: Region is enabled\n    *  PSU_" + memory + "_CFG_R" + str(n) + "_CONFIG_ENABLE                         1\n\n    * 0: Relaxed NS checking. A secure access is allowed to access secure or n\n    * on-secure region based on Rd/WrAllowed configuration. A non-secure acces\n    * s can only access non-secure region based on Rd/WrAllowed configuration\n    * 1: Strict NS checking. A secure access can only access secure region bas\n    * ed on Rd/WrAllowed configuration. A non-secure access can only access no\n    * n-secure region based on Rd/WrAllowed configuration\n    *  PSU_" + memory + "_CFG_R" + str(n) + "_CONFIG_NSCHECKTYPE                    0\n\n    * 0: Region is configured to be secure 1: Region is configured to be non-s\n    * ecure (NS)\n    *  PSU_" + memory + "_CFG_R" + str(n) + "_CONFIG_REGIONNS                       1\n\n    * 0: Write address matching this region are poisoned 1: Write address matc\n    * hing this region are allowed\n    *  PSU_" + memory + "_CFG_R" + str(n) + "_CONFIG_WRALLOWED                      1\n\n    * 0: Read address matching this region are poisoned 1: Read address matchi\n    * ng this region are allowed\n    *  PSU_" + memory + "_CFG_R" + str(n) + "_CONFIG_RDALLOWED                      1\n\n    * Region " + str(n) + " Configuration Register\n    * (OFFSET, MASK, VALUE)      (" + str(hex(int(memory_region_register(memory, n),16)+int("0xC",16)).upper()) + ", 0x0000001FU ," +config_value + "U)\n    */\n	PSU_Mask_Write(" + memory + "_CFG_R" + str(n) + "_CONFIG_OFFSET,\n		0x0000001FU, " + config_value + "U);\n/*##################################################################### */\n\n"
    
    start = start_address(memory, start_add)
#    print(start_add)
    region_dap[1] = region_dap[1] + "    /*\n    * Register : R" + str(n) + "_START @ " + str(memory_region_register(memory, n)) + "\n\n    * This field sets the start address bits [39:12] of this region (aligned t\n    * o 4kB). All bits of this field are used during comparison.\n    *  PSU_" + memory + "_CFG_R" + str(n) + "_START_ADDR                            " + str(int(start,16)) + "\n\n    * Region " + str(n) + " Start Address Register\n    * (OFFSET, MASK, VALUE)      (" + str(memory_region_register(memory, n)) + ", 0x0FFFFFFFU ," + str(start) + "U)\n    */\n	PSU_Mask_Write(" + memory + "_CFG_R" + str(n) + "_START_OFFSET,\n		0x0FFFFFFFU, " + str(start) + "U);\n/*##################################################################### */\n\n"

    end = end_address(memory, end_add)
    region_dap[2] = region_dap[2] + "    /*\n    * Register : R" + str(n) + "_END @ " + str(hex(int(memory_region_register(memory, n),16)+int("0x4",16)).upper()) + "\n\n    * This field sets the end address bits [39:12] of this region (aligned t\n    * o 4kB). All bits of this field are used during comparison.\n    *  PSU_" + memory + "_CFG_R" + str(n) + "_END_ADDR                            " + str(int(end,16)) + "\n\n    * Region " + str(n) + " End Address Register\n    * (OFFSET, MASK, VALUE)      (" + str(hex(int(memory_region_register(memory, n),16)+int("0x4",16)).upper()) + ", 0x0FFFFFFFU ," + str(end) + "U)\n    */\n	PSU_Mask_Write(" + memory + "_CFG_R" + str(n) + "_END_OFFSET,\n		0x0FFFFFFFU, " + str(end) + "U);\n/*##################################################################### */\n\n"

    region_dap[3] = region_dap[3] + "    /*\n    * Register : R" + str(n) + "_MASTER @ " + str(hex(int(memory_region_register(memory, n),16)+int("0x8",16)).upper()) + "\n\n    * Master ID mask.\n    *  PSU_" + memory + "_CFG_R" + str(n) + "_MASTER_MASK                           0\n\n    * Master ID value. An AXI MasterID will match this Master ID value of this\n    *  region if: AXI_MasterID AND MASK == ID AND MASK\n    *  PSU_" + memory + "_CFG_R" + str(n) + "_MASTER_ID                             0\n\n    * Region " + str(n) + " Master ID Register\n    * (OFFSET, MASK, VALUE)      (" + str(hex(int(memory_region_register(memory, n),16)+int("0x8",16)).upper()) + ", 0x03FF03FFU ," + master_value + "U)\n    */\n	PSU_Mask_Write(" + memory + "_CFG_R" + str(n) + "_MASTER_OFFSET,\n		0x03FF03FFU, " + master_value + "U);\n/*##################################################################### */\n"

    return region_dap


def xppu(access_register_value, access_masterslave_dict):
    # Aperture permission offset dictionary
    offset_dict = {'0xff000000' : 'LPD_XPPU_CFG_APERPERM_000_OFFSET', '0xff010000' : 'LPD_XPPU_CFG_APERPERM_001_OFFSET', '0xff020000' : 'LPD_XPPU_CFG_APERPERM_002_OFFSET', '0xff030000' : 'LPD_XPPU_CFG_APERPERM_003_OFFSET', '0xff040000' : 'LPD_XPPU_CFG_APERPERM_004_OFFSET', '0xff050000' : 'LPD_XPPU_CFG_APERPERM_005_OFFSET', '0xff060000' : 'LPD_XPPU_CFG_APERPERM_006_OFFSET', '0xff070000' : 'LPD_XPPU_CFG_APERPERM_007_OFFSET', '0xff080000' : 'LPD_XPPU_CFG_APERPERM_008_OFFSET', '0xff090000' : 'LPD_XPPU_CFG_APERPERM_009_OFFSET', '0xff0a0000' : 'LPD_XPPU_CFG_APERPERM_010_OFFSET', '0xff0b0000' : 'LPD_XPPU_CFG_APERPERM_011_OFFSET', '0xff0c0000' : 'LPD_XPPU_CFG_APERPERM_012_OFFSET', '0xff0d0000' : 'LPD_XPPU_CFG_APERPERM_013_OFFSET', '0xff0e0000' : 'LPD_XPPU_CFG_APERPERM_014_OFFSET', '0xff0f0000' : 'LPD_XPPU_CFG_APERPERM_015_OFFSET', '0xff100000' : 'LPD_XPPU_CFG_APERPERM_016_OFFSET', '0xff110000' : 'LPD_XPPU_CFG_APERPERM_017_OFFSET', '0xff120000' : 'LPD_XPPU_CFG_APERPERM_018_OFFSET', '0xff130000' : 'LPD_XPPU_CFG_APERPERM_019_OFFSET', '0xff140000' : 'LPD_XPPU_CFG_APERPERM_020_OFFSET', '0xff150000' : 'LPD_XPPU_CFG_APERPERM_021_OFFSET', '0xff160000' : 'LPD_XPPU_CFG_APERPERM_022_OFFSET', '0xff170000' : 'LPD_XPPU_CFG_APERPERM_023_OFFSET', '0xff240000' : 'LPD_XPPU_CFG_APERPERM_036_OFFSET', '0xff250000' : 'LPD_XPPU_CFG_APERPERM_037_OFFSET', '0xff260000' : 'LPD_XPPU_CFG_APERPERM_038_OFFSET', '0xff270000' : 'LPD_XPPU_CFG_APERPERM_039_OFFSET', '0xff280000' : 'LPD_XPPU_CFG_APERPERM_040_OFFSET', '0xff290000' : 'LPD_XPPU_CFG_APERPERM_041_OFFSET', '0xff2a0000' : 'LPD_XPPU_CFG_APERPERM_042_OFFSET', '0xff2b0000' : 'LPD_XPPU_CFG_APERPERM_043_OFFSET', '0xff2c0000' : 'LPD_XPPU_CFG_APERPERM_044_OFFSET', '0xff2d0000' : 'LPD_XPPU_CFG_APERPERM_045_OFFSET', '0xff2e0000' : 'LPD_XPPU_CFG_APERPERM_046_OFFSET', '0xff2f0000' : 'LPD_XPPU_CFG_APERPERM_047_OFFSET', '0xff340000' : 'LPD_XPPU_CFG_APERPERM_052_OFFSET', '0xff350000' : 'LPD_XPPU_CFG_APERPERM_053_OFFSET', '0xff360000' : 'LPD_XPPU_CFG_APERPERM_054_OFFSET', '0xff370000' : 'LPD_XPPU_CFG_APERPERM_055_OFFSET', '0xff380000' : 'LPD_XPPU_CFG_APERPERM_056_OFFSET', '0xff390000' : 'LPD_XPPU_CFG_APERPERM_057_OFFSET', '0xff3a0000' : 'LPD_XPPU_CFG_APERPERM_058_OFFSET', '0xff3b0000' : 'LPD_XPPU_CFG_APERPERM_059_OFFSET', '0xff3c0000' : 'LPD_XPPU_CFG_APERPERM_060_OFFSET', '0xff3d0000' : 'LPD_XPPU_CFG_APERPERM_061_OFFSET', '0xff3e0000' : 'LPD_XPPU_CFG_APERPERM_062_OFFSET', '0xff3f0000' : 'LPD_XPPU_CFG_APERPERM_063_OFFSET', '0xff400000' : 'LPD_XPPU_CFG_APERPERM_064_OFFSET', '0xff4b0000' : 'LPD_XPPU_CFG_APERPERM_075_OFFSET', '0xff4c0000' : 'LPD_XPPU_CFG_APERPERM_076_OFFSET', '0xff4d0000' : 'LPD_XPPU_CFG_APERPERM_077_OFFSET', '0xff4e0000' : 'LPD_XPPU_CFG_APERPERM_078_OFFSET', '0xff4f0000' : 'LPD_XPPU_CFG_APERPERM_079_OFFSET', '0xff500000' : 'LPD_XPPU_CFG_APERPERM_080_OFFSET', '0xff510000' : 'LPD_XPPU_CFG_APERPERM_081_OFFSET', '0xff520000' : 'LPD_XPPU_CFG_APERPERM_082_OFFSET', '0xff530000' : 'LPD_XPPU_CFG_APERPERM_083_OFFSET', '0xff540000' : 'LPD_XPPU_CFG_APERPERM_084_OFFSET', '0xff550000' : 'LPD_XPPU_CFG_APERPERM_085_OFFSET', '0xff560000' : 'LPD_XPPU_CFG_APERPERM_086_OFFSET', '0xff570000' : 'LPD_XPPU_CFG_APERPERM_087_OFFSET', '0xff580000' : 'LPD_XPPU_CFG_APERPERM_088_OFFSET', '0xff590000' : 'LPD_XPPU_CFG_APERPERM_089_OFFSET', '0xff5a0000' : 'LPD_XPPU_CFG_APERPERM_090_OFFSET', '0xff5b0000' : 'LPD_XPPU_CFG_APERPERM_091_OFFSET', '0xff5c0000' : 'LPD_XPPU_CFG_APERPERM_092_OFFSET', '0xff5d0000' : 'LPD_XPPU_CFG_APERPERM_093_OFFSET', '0xff860000' : 'LPD_XPPU_CFG_APERPERM_134_OFFSET', '0xff870000' : 'LPD_XPPU_CFG_APERPERM_135_OFFSET', '0xff880000' : 'LPD_XPPU_CFG_APERPERM_136_OFFSET', '0xff890000' : 'LPD_XPPU_CFG_APERPERM_137_OFFSET', '0xff8a0000' : 'LPD_XPPU_CFG_APERPERM_138_OFFSET', '0xff8b0000' : 'LPD_XPPU_CFG_APERPERM_139_OFFSET', '0xff8c0000' : 'LPD_XPPU_CFG_APERPERM_140_OFFSET', '0xff8d0000' : 'LPD_XPPU_CFG_APERPERM_141_OFFSET', '0xff8e0000' : 'LPD_XPPU_CFG_APERPERM_142_OFFSET', '0xff8f0000' : 'LPD_XPPU_CFG_APERPERM_143_OFFSET', '0xff900000' : 'LPD_XPPU_CFG_APERPERM_144_OFFSET', '0xff910000' : 'LPD_XPPU_CFG_APERPERM_145_OFFSET', '0xff920000' : 'LPD_XPPU_CFG_APERPERM_146_OFFSET', '0xff930000' : 'LPD_XPPU_CFG_APERPERM_147_OFFSET', '0xff940000' : 'LPD_XPPU_CFG_APERPERM_148_OFFSET', '0xff950000' : 'LPD_XPPU_CFG_APERPERM_149_OFFSET', '0xff960000' : 'LPD_XPPU_CFG_APERPERM_150_OFFSET', '0xff970000' : 'LPD_XPPU_CFG_APERPERM_151_OFFSET', '0xff980000' : 'LPD_XPPU_CFG_APERPERM_152_OFFSET', '0xff990000' : 'LPD_XPPU_CFG_APERPERM_153_OFFSET', '0xff9b0000' : 'LPD_XPPU_CFG_APERPERM_155_OFFSET', '0xff9c0000' : 'LPD_XPPU_CFG_APERPERM_156_OFFSET', '0xff9d0000' : 'LPD_XPPU_CFG_APERPERM_157_OFFSET', '0xff9e0000' : 'LPD_XPPU_CFG_APERPERM_158_OFFSET', '0xffa00000' : 'LPD_XPPU_CFG_APERPERM_160_OFFSET', '0xffa10000' : 'LPD_XPPU_CFG_APERPERM_161_OFFSET', '0xffa50000' : 'LPD_XPPU_CFG_APERPERM_165_OFFSET', '0xffa60000' : 'LPD_XPPU_CFG_APERPERM_166_OFFSET', '0xffa80000' : 'LPD_XPPU_CFG_APERPERM_168_OFFSET', '0xffa90000' : 'LPD_XPPU_CFG_APERPERM_169_OFFSET', '0xffaa0000' : 'LPD_XPPU_CFG_APERPERM_170_OFFSET', '0xffab0000' : 'LPD_XPPU_CFG_APERPERM_171_OFFSET', '0xffac0000' : 'LPD_XPPU_CFG_APERPERM_172_OFFSET', '0xffad0000' : 'LPD_XPPU_CFG_APERPERM_173_OFFSET', '0xffae0000' : 'LPD_XPPU_CFG_APERPERM_174_OFFSET', '0xffaf0000' : 'LPD_XPPU_CFG_APERPERM_175_OFFSET', '0xffc30000' : 'LPD_XPPU_CFG_APERPERM_195_OFFSET', '0xffc80000' : 'LPD_XPPU_CFG_APERPERM_200_OFFSET', '0xffca0000' : 'LPD_XPPU_CFG_APERPERM_202_OFFSET', '0xffcb0000' : 'LPD_XPPU_CFG_APERPERM_203_OFFSET', '0xffce0000' : 'LPD_XPPU_CFG_APERPERM_206_OFFSET', '0xffcf0000' : 'LPD_XPPU_CFG_APERPERM_207_OFFSET', '0xffd80000' : 'LPD_XPPU_CFG_APERPERM_216_OFFSET', '0xfe000000' : 'LPD_XPPU_CFG_APERPERM_384_OFFSET', '0xfe100000' : 'LPD_XPPU_CFG_APERPERM_385_OFFSET', '0xfe200000' : 'LPD_XPPU_CFG_APERPERM_386_OFFSET', '0xfe300000' : 'LPD_XPPU_CFG_APERPERM_387_OFFSET', '0xfe400000' : 'LPD_XPPU_CFG_APERPERM_388_OFFSET', '0xfe500000' : 'LPD_XPPU_CFG_APERPERM_389_OFFSET', '0xfe600000' : 'LPD_XPPU_CFG_APERPERM_390_OFFSET', '0xfe700000' : 'LPD_XPPU_CFG_APERPERM_391_OFFSET', '0xfe800000' : 'LPD_XPPU_CFG_APERPERM_392_OFFSET', '0xfe900000' : 'LPD_XPPU_CFG_APERPERM_393_OFFSET', '0xfea00000' : 'LPD_XPPU_CFG_APERPERM_394_OFFSET', '0xfeb00000' : 'LPD_XPPU_CFG_APERPERM_395_OFFSET', '0xfec00000' : 'LPD_XPPU_CFG_APERPERM_396_OFFSET', '0xfed00000' : 'LPD_XPPU_CFG_APERPERM_397_OFFSET', '0xfee00000' : 'LPD_XPPU_CFG_APERPERM_398_OFFSET', '0xfef00000' : 'LPD_XPPU_CFG_APERPERM_399_OFFSET', '0xc0000000' : 'LPD_XPPU_CFG_APERPERM_400_OFFSET'}

    aperperm_register_dict = {'0xff000000' : '0xFF981000', '0xff010000' : '0xFF981004', '0xff020000' : '0xFF981008', '0xff030000' : '0xFF98100C', '0xff040000' : '0xFF981010', '0xff050000' : '0xFF981014', '0xff0f0000' : '0xFF98103C', '0xff170000' : '0xFF98105C', '0xff070000' : '0xFF98101C'}


    # CPU dictionary
    cpu_dict = { 'cpus-a53@0' : 'APU', 'cpus-r5@1' : 'RPU0', 'cpus-r5@0' : 'RPU1' }
    #cpu_dict = { '0010000000' : 'APU', '0000000000' : 'RPU0', '0000010000' : 'RPU1', '0001010000' : 'csu', 'PMU' : '0001000000', 'gpu' : '0011000100', 'qspi' : '0001110011', 'nand' : '0001110010', 'sd_1' : '0001110001', 'gem_3' : '0001110111', 'usb_0' : '0001100000', 'sata_0' : '0011000000', 'sata_1' : '0011000001', 'dp' : '0011100000', 'pcie' : '0011010000', 'lpd_dma' : '0001101000', 'fpd_dma' : '0011101000', 'coresight' : '0011000101'}
    # Finding the Master 
    #master = []
    xppu_line = ["" for xppu_line in range(len(access_register_value))]
    #value_str = ["" for value_str in range(len(access_register_value))] 
    #value_hex = ["" for value_hex in range(len(access_register_value))]
    for i in range(0, len(access_register_value)):
        master = []
        for j in range(0, len(access_masterslave_dict[access_register_value[i]])):
            #master.append(cpu_dict[str(access_masterslave_dict[access_register_value[i]][j][0])])
            #master_id = bin(int(access_masterslave_dict[access_register_value[i]][j][0])).replace("0b", "").zfill(10)
            master.append(cpu_dict[str(access_masterslave_dict[access_register_value[i]][j][0])])
        # Finiding the Aperture permission offset value
        offset_value = offset_dict[hex(access_register_value[i])]
        aperperm_register = '0xff981000'
        offset_number = str(offset_value[22]) + str(offset_value[23]) + str(offset_value[24])
        offset_number_int = int(offset_number)
        for j in range(0, offset_number_int+1):
            aperperm_register = hex(int(aperperm_register,16)+int("0x4",16)).upper()

        # Converting the Decimal value into Binary
        setting = access_masterslave_dict[access_register_value[i]][0][1]
    
        # Initialising all the bits of value into zeros and switching them to ones based on the settings
        value = np.zeros(shape=(32), dtype = int)
        par_0 = np.zeros(shape=(6), dtype = int)
        par_1 = np.zeros(shape=(5), dtype = int)
        par_2 = np.zeros(shape=(5), dtype = int)
        par_3 = np.zeros(shape=(5), dtype = int)
        value_str = "" 
        value_hex = ""

        value[24]=1  

        if "rw" in setting:
            for j in range(0, len(master)):
                if master[j] == "APU":
                    value[25] = 1
                if master[j] == "RPU0":
                    value[20] = 1
                if master[j] == "RPU1":
                    value[18] = 1

        elif "ro" in setting:
            for j in range(0, len(master)):
                if master[j] == "APU":
                    value[26] = 1
                elif master[j] == "RPU0":
                    value[21] = 1 
                elif master[j] == "RPU1":
                    value[19] = 1

 
        #if setting_bin[31] == "0":
        #    value[4] =1
    
        # Checking the parity
        par_0[0] = 0
        for j in range(12,17):
            for k in range(1,len(par_0)):
                par_0[k] = value[j]
        value[0] = parity(par_0)
    
        for j in range(17,22):
            for k in range(0,len(par_1)):
                par_1[k] = value[j]
        value[1] = parity(par_1)
        
        for j in range(22,27):
            for k in range(0,len(par_2)):
                par_2[k] = value[j]
        value[2] = parity(par_2)
        
        for j in range(27,32):
            for k in range(0,len(par_3)):
                par_3[k] = value[j]
        value[3] = parity(par_3)
    
        
        for j in range(0, len(value)):
            value_str = value_str + str(value[j])
        
        # Converting Value from binary to hexadecimal
        value_hex = '0x'+(hex(int(str(value_str), 2)).upper())[2:].zfill(8)
        #value_hex = ("{0:#0{1}x}".format(value_str,10)).upper()
        offset_number = str(offset_value[22]+offset_value[23]+offset_value[24])
        xppu_line[i] = "\n    /*\n    * Register : APERPERM_" + str(offset_number) + "@ " + aperperm_register + "\n\n    * This field defines the MASTER ID match criteria. Each entry in the IDL c\n    * orresponds to a bit in this field. 0=not match, 1=match.\n    *  PSU_LPD_XPPU_CFG_APERPERM_" + str(offset_number) + "_PERMISSION                    " + str(permission(value)) + "\n\n    * 1=secure or non-secure transactions are allowed 0=only secure transactio\n    * na are allowed\n    * PSU_PSU_LPD_XPPU_CFG_APERPERM_"+ str(offset_number) + "_TRUSTZONE                     " + str(hex(value[4])) + "\n\n    * SW must calculate and set up parity, if parity check is enabled by the C\n    * TRL register. 31: parity for bits 19:15 30: parity for bits 14:10 29: pa\n    * rity for bits 9:5 28: parity for bits 27, 4:0\n    *  PSU_LPD_XPPU_CFG_APERPERM_" + str(offset_number) + "_PARITY                        " + str(parity_value(value))+ "\n\n    * Entry " + str(offset_number) + " of the Aperture Permission List, for the 64K-byte aperture at\n    * BASE_64KB + " + str(base_add(str(offset_value))) + "\n    * (OFFSET, MASK, VALUE)      (" + aperperm_register + ", 0xF80FFFFFU , " + str(value_hex) + "U)\n    */\n	PSU_Mask_Write(" + str(offset_value) + ", " + "0xF80FFFFFU" + ", " + str(value_hex) +"U);\n/*##################################################################### */\n"

    return xppu_line

def xmpu_region_config(reg_value, setting, n):
    # Finding the memory slave
    memory = memory_dict[reg_value]
    # Converting the Decimal value into Binary
    #setting_bin = bin(setting).replace("0b", "").zfill(32)
    # Initialising all the bits of value into zeros and switching them to ones based on the settings
    value = np.zeros(shape=(32), dtype = int)
    config_line = "" 
    value_str = "" 
    value_hex = ""
    value[31] = 1
    if "ro" in setting:
        value[30] = 1
    if "wo" in setting:
        value[29] = 1
    if "rw" in setting:
        value[30] = 1
        value[29] = 1
    if "nonsecure" in setting:
        value[28] = 1
   
    for i in range(0,32):
        value_str = value_str + str(value[i])
    # Converting Value from binary to hexadecimal
    #value_hex = hex(int(str(value_str), 2))
    value_hex = '0x'+(hex(int(str(value_str), 2)).upper())[2:].zfill(8)
    reg = hex(int(memory_region_register(memory, n),16)+int("0xC",16)).upper()
    config_line = "\n    /*\n    * Register : R" + str(n) + "_CONFIG @ " + str(reg) + "\n\n    * 0: Region is disabled 1: Region is enabled\n    *  PSU_" + str(memory) + "_CFG_R" + str(n) + "_CONFIG_ENABLE                         1\n\n    * 0: Relaxed NS checking. A secure access is allowed to access secure or n\n    * on-secure region based on Rd/WrAllowed configuration. A non-secure acces\n    * s can only access non-secure region based on Rd/WrAllowed configuration\n    * 1: Strict NS checking. A secure access can only access secure region bas\n    * ed on Rd/WrAllowed configuration. A non-secure access can only access no\n    * n-secure region based on Rd/WrAllowed configuration\n    *  PSU_" + str(memory) + "_CFG_R" + str(n) + "_CONFIG_NSCHECKTYPE                    " + str(value[27]) + "\n\n    * 0: Region is configured to be secure 1: Region is configured to be non-s\n    * ecure (NS)\n    *  PSU_" + str(memory) + "_CFG_R" + str(n) + "_CONFIG_REGIONNS                       " + str(value[28]) + "\n\n    * 0: Write address matching this region are poisoned 1: Write address matc\n    * hing this region are allowed\n    *  PSU_" + str(memory) + "_CFG_R" + str(n) + "_CONFIG_WRALLOWED                      " + str(value[29]) + "\n\n    * 0: Read address matching this region are poisoned 1: Read address matchi\n    * ng this region are allowed\n    *  PSU_" + str(memory) + "_CFG_R" + str(n) + "_CONFIG_RDALLOWED                      " + str(value[30]) + "\n\n    * Region " + str(n) + " Configuration Register\n    * (OFFSET, MASK, VALUE)      (" + str(reg) + ", 0x0000001FU ," + str(value_hex) + "U)\n    */\n	PSU_Mask_Write(" + str(memory) + "_CFG_R" + str(n) + "_CONFIG_OFFSET, 0x0000001FU, " + str(value_hex) + "U);\n/*##################################################################### */\n\n"
            
    return config_line


def xmpu_region_start(address, reg_value, n):
    # Finding the memory slave
    memory = memory_dict[reg_value]

    if memory == "OCM_XMPU":
        address_bin = bin(address).replace("0b", "").zfill(32)
        value = np.zeros(shape=(32), dtype = int)
        address_line = ""
        value_str = "" 
        value_hex = ""
        for i in range(12,32):
            value[i] = str(address_bin[i-12])
        for i in range(0,32):
            value_str = value_str + str(value[i])
        # Converting Value from binary to hexadecimal
        #value_hex = hex(int(str(value_str), 2))
        value_hex = '0x'+(hex(int(str(value_str), 2)).upper())[2:].zfill(8)
        #address_line = address_line + "    PSU_Mask_Write(" + memory + "_CFG_R" + str(n) + "_START_OFFSET, 0x0FFFFFFFU, " + str(value_hex) + "U);\n"
        
    if memory == "FPD_XMPU":
        address_bin = bin(address).replace("0b", "").zfill(40)
        value = np.zeros(shape=(32), dtype = int)
        adress_line = "" 
        value_str = "" 
        value_hex = ""
        for i in range(4,32):
            value[i] = str(address_bin[i-4])
        for i in range(0,32):
            value_str = value_str + str(value[i])
        # Converting Value from binary to hexadecimal
        #value_hex = hex(int(str(value_str), 2))
        value_hex = '0x'+(hex(int(str(value_str), 2)).upper())[2:].zfill(8)
        #address_line = "	PSU_Mask_Write(" + memory + "_CFG_R" + str(n) + "_START_OFFSET, 0x0FFFFFFFU, " + str(value_hex) + "U);\n"
        
    if "DDR_XMPU" in memory:
        address_bin = bin(address).replace("0b", "").zfill(40)
        value = np.zeros(shape=(32), dtype = int)
        address_line = "" 
        value_str = "" 
        value_hex = ""
        for i in range(4,24):
            value[i] = str(address_bin[i-4])
        for i in range(0,32):
            value_str = value_str + str(value[i])
        # Converting Value from binary to hexadecimal
        #value_hex = hex(int(str(value_str), 2))
        value_hex = '0x'+(hex(int(str(value_str), 2)).upper())[2:].zfill(8)
        #address_line = "	PSU_Mask_Write(" + memory + "_CFG_R" + str(n) + "_START_OFFSET, 0x0FFFFFFFU, " + str(value_hex) + "U);\n"
    reg = memory_region_register(memory, n)
    address_line = "    /*\n    * Register : R" + str(n) + "_START @ " + str(reg) + "\n\n    * This field sets the start address bits [39:12] of this region (aligned t\n    * o 4kB). All bits of this field are used during comparison.\n    *  PSU_" + memory + "_CFG_R" + str(n) + "_START_ADDR                            " + str(int(value_hex,16)) + "\n\n    * Region" + str(n) + " Start Address Register\n    * (OFFSET, MASK, VALUE)      (" + str(reg) + ", 0x0FFFFFFFU ," + str(value_hex) + "U)\n    */\n"

    address_line = address_line + "	PSU_Mask_Write(" + memory + "_CFG_R" + str(n) + "_START_OFFSET, 0x0FFFFFFFU, " + str(value_hex) + "U);\n/*##################################################################### */\n\n"
    return address_line

def xmpu_region_end(address, reg_value, n):
    # Finding the memory slave
    memory = memory_dict[reg_value]

    
    if memory == "OCM_XMPU":
        address_bin = bin(address).replace("0b", "").zfill(32)
        value = np.zeros(shape=(32), dtype = int)
        address_line = ""
        value_str = "" 
        value_hex = ""
        for i in range(12,32):
            value[i] = str(address_bin[i-12])
        for i in range(0,32):
            value_str = value_str + str(value[i])
        # Converting Value from binary to hexadecimal
        #value_hex = hex(int(str(value_str), 2))
        value_hex = '0x'+(hex(int(str(value_str), 2)).upper())[2:].zfill(8)
        #address_line = "	PSU_Mask_Write(" + memory + "_CFG_R" + str(n) + "_END_OFFSET, 0x0FFFFFFFU, " + str(value_hex) + "U);\n"
        
    if memory == "FPD_XMPU":
        address_bin = bin(address).replace("0b", "").zfill(40)
        value = np.zeros(shape=(32), dtype = int)
        adress_line = "" 
        value_str = "" 
        value_hex = ""
        for i in range(4,32):
            value[i] = str(address_bin[i-4])
        for i in range(0,32):
            value_str = value_str + str(value[i])
        # Converting Value from binary to hexadecimal
        #value_hex = hex(int(str(value_str), 2))
        value_hex = '0x'+(hex(int(str(value_str), 2)).upper())[2:].zfill(8)
        #address_line = "	PSU_Mask_Write(" + memory + "_CFG_R" + str(n) + "_END_OFFSET, 0x0FFFFFFFU, " + str(value_hex) + "U);\n"
        
    if "DDR_XMPU" in memory:
        address_bin = bin(address).replace("0b", "").zfill(40)
        value = np.zeros(shape=(32), dtype = int)
        address_line = "" 
        value_str = "" 
        value_hex = ""
        for i in range(4,24):
            value[i] = str(address_bin[i-4])
        for i in range(24,32):
            value[i] = 1
        for i in range(0,32):
            value_str = value_str + str(value[i])
        # Converting Value from binary to hexadecimal
        #value_hex = hex(int(str(value_str), 2))
        value_hex = '0x'+(hex(int(str(value_str), 2)).upper())[2:].zfill(8)
        #address_line = "	PSU_Mask_Write(" + memory + "_CFG_R" + str(n) + "_END_OFFSET, 0x0FFFFFFFU, " + str(value_hex) + "U);\n"
    reg = hex(int(memory_region_register(memory, n),16)+int("0x4",16)).upper()
    address_line = "    /*\n    * Register : R" + str(n) + "_END @ " + str(reg) + "\n\n    * This field sets the end address bits [39:12] of this region (aligned to\n    * 4kB). All bits of this field are used during comparison.\n    *  PSU_" + memory + "_CFG_R" + str(n) + "_END_ADDR                            " + str(int(value_hex,16)) + "\n\n    * Region End Address Register\n    * (OFFSET, MASK, VALUE)      (" + str(reg) + ", 0x0FFFFFFFU ," + str(value_hex) + "U)\n    */\n"

    address_line = address_line + "	PSU_Mask_Write(" + memory + "_CFG_R" + str(n) + "_END_OFFSET, 0x0FFFFFFFU, " + str(value_hex) + "U);\n/*##################################################################### */\n\n"
        
    return address_line

def xmpu_region_master(cpu_name, reg_value, n):
    # Finding the memory slave
    memory = memory_dict[reg_value]

    cpu_dict = { 'cpus-a53@0' : '0010000000', 'cpus-r5@1' : '0000000000', 'csu' : '0001010000', 'cpus_microblaze@1' : '0001000000', 'gpu' : '0011000100', 'qspi' : '0001110011', 'nand' : '0001110010', 'sd_1' : '0001110001', 'gem_3' : '0001110111', 'usb_0' : '0001100000', 'sata_0' : '0011000000', 'sata_1' : '0011000001', 'dp' : '0011100000', 'pcie' : '0011010000', 'lpd_dma' : '0001101000', 'fpd_dma' : '0011101000', 'coresight' : '0011000101'}

    # Finding the Master 
    master = cpu_dict[cpu_name]
    #master = str(bin(int(cpu_name)).replace("0b", "").zfill(10))
    value = np.zeros(shape=(32), dtype = int)
    master_line = ""
    value_str = "" 
    value_hex = ""
    if memory != "FPD_XMPU":
        mask = "1111000000"
        value_str = value_str + "000000" + "1111000000" + "000000" + str(master)
    elif memory == "FPD_XMPU":
        mask = "1011000000"
        value_str = value_str + "000000" + "1011000000" + "000000" + str(master)
    # Converting Value from binary to hexadecimal
    #value_hex = hex(int(str(value_str), 2))
    value_hex = '0x'+(hex(int(str(value_str), 2)).upper())[2:].zfill(8)
    reg = hex(int(memory_region_register(memory, n),16)+int("0x8",16)).upper()
    master_line = "    /*\n    * Register : R" + str(n) + "_MASTER @ " + str(reg) + "\n\n    * Master ID mask.\n    *  PSU_" + memory + "_CFG_R" + str(n) + "_MASTER_MASK                           " + str(int(mask,2)) + "\n\n    * Master ID value. An AXI MasterID will match this Master ID value of this\n    *  region if: AXI_MasterID AND MASK == ID AND MASK\n    *  PSU_" + memory + "_CFG_R" + str(n) + "_MASTER_ID                             " + str(cpu_name) + "\n\n    * Region " + str(n) + " Master ID Register\n    * (OFFSET, MASK, VALUE)      (" + str(reg) + ", 0x03FF03FFU ," + str(value_hex) + "U)\n    */\n"
    master_line = master_line + " 	PSU_Mask_Write(" + str(memory) + "_CFG_R" + str(n) + "_MASTER_OFFSET, 0x03FF03FFU, " + str(value_hex) + "U);\n/*##################################################################### */\n\n"
    return master_line       

def xmpu_poison(memory_reg):
    memory = memory_dict[memory_reg]
    xmpu_poison_lines = ["" for xmpu_poison_lines in range(0,3)]
    n = "00"
    xmpu_poison_lines[0] = "\n    /*\n    * Register : POISON @ " + str(hex(int(memory_region_register(memory, n),16)-int("0x100",16)+int("0xC",16)).upper()) + "\n\n    * This field sets the poison attribute when CTRL.PoisonCfg is 0\n    *  PSU_" + memory + "_CFG_POISON_ATTRIB                             1\n\n    * XMPU Poison Address Attribute\n    * (OFFSET, MASK, VALUE)      (" + str(hex(int(memory_region_register(memory, n),16)-int("0x100",16)+int("0xC",16)).upper()) + ", 0x00100000U ,0x00100000U)\n    */\n	PSU_Mask_Write(" + memory + "_CFG_POISON_OFFSET, 0x00100000U, 0x00100000U);\n/*##################################################################### */\n\n" 
    xmpu_poison_lines[1] = "\n    /*\n    * Register : CTRL @ " + str(hex(int(memory_region_register(memory, n),16)-int("0x100",16)).upper()) + "\n\n    * Default write permission 0: If AXI write Address/ID doesn't match with a\n    * ny of the enabled regions, then write is poisoned 1: If AXI write Addres\n    * s/ID doesn't match with any of the enabled regions, then write is allowe\n    * d to go through\n    *  PSU_" + memory + "_CFG_CTRL_DEFWRALLOWED                         1\n\n    * Default read permission 0: If AXI read Address/ID doesn't match with any\n    *  of the enabled regions, then read is poisoned 1: If AXI read Address/ID\n    *  doesn't match with any of the enabled regions, then read is allowed to\n    * go through\n    *  PSU_" + memory + "_CFG_CTRL_DEFRDALLOWED                         1\n\n    * XMPU Control Register\n    * (OFFSET, MASK, VALUE)      (" + str(hex(int(memory_region_register(memory, n),16)-int("0x100",16)).upper()) + ", 0x00000003U ,0x00000003U)\n    */\n	PSU_Mask_Write(" + memory + "_CFG_CTRL_OFFSET, 0x00000003U, 0x00000003U);\n/*##################################################################### */\n\n"
    xmpu_poison_lines[2] = "\n    /*\n    * DDR XMPU INTERRUPT ENABLE\n    */\n    /*\n    * Register : IEN @ " + str(hex(int(memory_region_register(memory, n),16)-int("0x100",16)+int("0x18",16)).upper()) + "\n\n    * see INT_STATUS register for details\n    *  PSU_" + memory + "_CFG_IEN_SECURITYVIO                           0X1\n\n    * see INT_STATUS register for details\n    *  PSU_" + memory + "_CFG_IEN_WRPERMVIO                             0X1\n\n    * see INT_STATUS register for details\n    *  PSU_" + memory + "_CFG_IEN_RDPERMVIO                             0X1\n\n    * see INT_STATUS register for details\n    *  PSU_" + memory + "_CFG_IEN_INV_APB                               0X1\n\n    * Interrupt Enable Register\n    * (OFFSET, MASK, VALUE)      (" + str(hex(int(memory_region_register(memory, n),16)-int("0x100",16)+int("0x18",16)).upper()) + ", 0x0000000FU ,0x0000000FU)\n    */\n	PSU_Mask_Write(" + memory + "_CFG_IEN_OFFSET, 0x0000000FU, 0x0000000FU);\n/*##################################################################### */\n\n"
    return xmpu_poison_lines

def fpd_add(address):
    address_bin = bin(address).replace("0b", "").zfill(40)
    value = np.zeros(shape=(32), dtype = int)
    adress_line = "" 
    value_str = "" 
    value_hex = ""
    for i in range(4,32):
        value[i] = str(address_bin[i-4])
    for i in range(0,32):
        value_str = value_str + str(value[i])
    # Converting Value from binary to hexadecimal
    value_hex = hex(int(str(value_str), 2))
    return value_hex

def append_lines(lines, array):
    for i in range(0, (len(lines)-1)):
        for j in range(0,len(lines[i])):
            array.append(lines[i][len(lines[i])-1-j])
            array.append(lines[i+1][len(lines[i])-1-j])
            array.append(lines[i+2][len(lines[i])-1-j])
            array.append(lines[i+3][len(lines[i])-1-j])
        break
    for i in range(0, len(lines[4])):
        array.append(lines[4][i])
    return array


def configuration(tgt_node, sdt, options):
    root_node = sdt.tree[tgt_node]
    root_sub_nodes = root_node.subnodes()
    memory_slave = []
    access_slave = []
    cpu_master = []
    access_flags = []
    
    # Traverse the tree and find the nodes having status=ok property
    for node in root_sub_nodes:
       
        try:
            access = node["access"].value
            access_slave.append(access)
            access_flags.append(node["access-flags-names"].value)
        except:
            pass

    for node in root_sub_nodes:
       
        try:
            memory = node["memory"].value
            memory_slave.append(memory)
            
        except:
            pass

    for node in root_sub_nodes:
       
        try:
            cpu = node["cpus"].value
            cpu_master.append(cpu)
            
        except:
            pass

    access_regvalue = []
    access_masterslave = []
    access_prop = "access"
    for i in range(0, len(access_slave)):
        for j in range(0, int(len(access_slave[i])-1)):
            access_local = [access_slave[i][j]]
            #print(access_local)
            cpu_parent_node = sdt.FDT.node_offset_by_phandle(cpu_master[i][0])
            cpu_name = sdt.FDT.get_name(cpu_parent_node)
            #access_append = [get_phandle_regprop(sdt, access_prop, access_local), cpu_name, access_slave[i][(2*j)+1]]
            access_append = [get_phandle_regprop(sdt, access_prop, access_local), cpu_name, access_flags[i][j]]
            access_masterslave.append(access_append)
            access_regvalue.append(get_phandle_regprop(sdt, access_prop, access_local))
    access_register_value = []
    [access_register_value.append(x) for x in access_regvalue if x not in access_register_value]
    access_masterslave_dict = {}
    for row in access_masterslave:
        if row[0] not in access_masterslave_dict:
            access_masterslave_dict[row[0]] = []    
        access_masterslave_dict[row[0]].append(row[1:])
    xppu_lines = xppu(access_register_value, access_masterslave_dict)
    #print(xppu_lines)
    
    memory_regvalue = []
    memory_masterslave = []
    memory_prop = "memory"
    for i in range(0, len(memory_slave)):
        for j in range(0, int(len(memory_slave[i])/4)):
            #memory_local = [memory_slave[i][(4*j)], memory_slave[i][(4*j)+1], memory_slave[i][(4*j)+2], memory_slave[i][(4*j)+3]]
            cpu_parent_node = sdt.FDT.node_offset_by_phandle(cpu_master[i][0])
            cpu_name = sdt.FDT.get_name(cpu_parent_node)
            #memory_append = [get_phandle_regprop(sdt, memory_prop, memory_local), cpu_name, memory_slave[i][(4*j)+1], memory_slave[i][(4*j)+2], memory_slave[i][(4*j)+3]]
            memory_append = [hex(memory_slave[i][(4*j)+1]), cpu_name, hex(memory_slave[i][(4*j)+3]), access_flags[i][int(len(access_slave[i])-1)]]
            memory_masterslave.append(memory_append)
            memory_regvalue.append(hex(memory_slave[i][(4*j)+1]))
    memory_register_value = []
    [memory_register_value.append(x) for x in memory_regvalue if x not in memory_register_value]
    memory_masterslave_dict = {}
    for row in memory_masterslave:
        if row[0] not in memory_masterslave_dict:
            memory_masterslave_dict[row[0]] = []    
        memory_masterslave_dict[row[0]].append(row[1:])

    #print(bin(memory_masterslave_dict[memory_register_value[0]][0][2]).replace("0b", "").zfill(40))
    memory_dict_local = { '0xffa70000' : 'ocm_xmpu', '0xfd5d0000' : 'fpd_xmpu', '0xfd000000' : 'ddr_xmpu0', '0xfd010000' : 'ddr_xmpu1', '0xfd020000' : 'ddr_xmpu2', '0xfd030000' : 'ddr_xmpu3', '0xfd040000' : 'ddr_xmpu4', '0xfd050000' : 'ddr_xmpu5'}
    #print(memory_masterslave_dict)
    ocm_xmpu_lines = [[],[],[],[]]
    ddr_xmpu_lines = [[[],[],[],[]], [[],[],[],[]], [[],[],[],[]], [[],[],[],[]], [[],[],[],[]], [[],[],[],[]]]
    fpd_xmpu_lines = [[],[],[],[]]
    for i in range(0,len(memory_register_value)):
        if "0xfff" in hex(memory_register_value[i]):
            ocm_region_number = 0
            ocm_xmpu_lines[0].append(region_00("0xffa70000", memory_masterslave_dict[memory_register_value[i]][0][2], memory_masterslave_dict[memory_register_value[i]][0][3])[0])
            ocm_xmpu_lines[1].append("	PSU_Mask_Write(OCM_XMPU_CFG_R00_START_OFFSET, 0x0FFFFFFFU, 0x000FFFC0U);\n")
            ocm_xmpu_lines[2].append("	PSU_Mask_Write(OCM_XMPU_CFG_R00_END_OFFSET, 0x0FFFFFFFU, 0x000FFFCFU);\n")
            ocm_xmpu_lines[3].append("	PSU_Mask_Write(OCM_XMPU_CFG_R00_MASTER_OFFSET, 0x03FF03FFU, 0x00000000U);\n")
            count = 0
            for j in range(0, len(memory_masterslave_dict[memory_register_value[i]])):
               
                #ocm_region_number = count+2
                
                #if j<9:
                #    n = str("0"+str(count+1))
                #else:
                #    n = str(count+1)
                
                if (memory_masterslave_dict[memory_register_value[i]][j][0]) == 0 or (memory_masterslave_dict[memory_register_value[i]][j][0]) == 16:
                    count = count-1

                elif memory_masterslave_dict[memory_register_value[i]][j][0] != 0 and memory_masterslave_dict[memory_register_value[i]][j][0] != 16:
                    if j<9:
                        n = str("0"+str(count+1))
                    else:
                        n = str(count+1)
                    print(count)
                    ocm_xmpu_lines[0].append(xmpu_region_config(hex(memory_register_value[i]), memory_masterslave_dict[memory_register_value[i]][j][1], n))
                    ocm_xmpu_lines[1].append(xmpu_region_start(memory_masterslave_dict[memory_register_value[i]][j][2], hex(memory_register_value[i]), n))
                    ocm_xmpu_lines[2].append(xmpu_region_end(memory_masterslave_dict[memory_register_value[i]][j][3], hex(memory_register_value[i]), n))
                    ocm_xmpu_lines[3].append(xmpu_region_master(memory_masterslave_dict[memory_register_value[i]][j][0], hex(memory_register_value[i]), n))
                    count = count +1 
                ocm_region_number = count+2

            if ocm_region_number<10:
                ocm_region_number_str = "0"+str(ocm_region_number)
            else:
                ocm_region_number_str = str(ocm_region_number)
            ocm_xmpu_lines[0].append("	PSU_Mask_Write(OCM_XMPU_CFG_R" + str(ocm_region_number_str) + "_CONFIG_OFFSET, 0x0000001FU, 0x00000007U);\n")
            ocm_xmpu_lines[1].append("	PSU_Mask_Write(OCM_XMPU_CFG_R" + str(ocm_region_number_str) + "_START_OFFSET, 0x0FFFFFFFU, 0x000FFFC0U);\n")
            ocm_xmpu_lines[2].append("	PSU_Mask_Write(OCM_XMPU_CFG_R" + str(ocm_region_number_str) + "_END_OFFSET, 0x0FFFFFFFU, 0x000FFFCFU);\n")
            ocm_xmpu_lines[3].append("	PSU_Mask_Write(OCM_XMPU_CFG_R" + str(ocm_region_number_str) + "_MASTER_OFFSET, 0x03FF03FFU, 0x03FF0062U);\n")
            ocm_xmpu_lines.append(xmpu_poison("0xffa70000"))

        elif hex(memory_register_value[i]) == "0x1000000":
            ddr_reg = ["0xfd000000", "0xfd010000", "0xfd020000", "0xfd030000", "0xfd040000", "0xfd050000"]
            ddr_region = 0
            for j in range(0, 6):
                ddr_xmpu_lines[j][0].append(region_00(ddr_reg[j], memory_register_value[i], hex(int(memory_register_value[i],16)+int("FFFFF",16)))[0])
                ddr_xmpu_lines[j][1].append(region_00(ddr_reg[j], memory_register_value[i], hex(int(memory_register_value[i],16)+int("FFFFF",16)))[1])    
                ddr_xmpu_lines[j][2].append(region_00(ddr_reg[j], memory_register_value[i], hex(int(memory_register_value[i],16)+int("FFFFF",16)))[2])
                ddr_xmpu_lines[j][3].append(region_00(ddr_reg[j], memory_register_value[i], hex(int(memory_register_value[i],16)+int("FFFFF",16)))[3])    
                ddr_xmpu_lines[j].append(xmpu_poison(ddr_reg[j]))
                count = 0
                if j == 1 or j == 2:
                    for k in range(0, len(memory_masterslave_dict[memory_register_value[i]])):
                        
                        #if k<9:
                        #    n = str("0"+str(k+1))
                        #else:
                        #    n = str(k+1)
                        
                        if memory_masterslave_dict[memory_register_value[i]][k][0] == "cpus-r5@1":
                            count = count-1
                            
                        elif memory_masterslave_dict[memory_register_value[i]][k][0] != "cpus-r5@1":
                            if k<9:
                                n = str("0"+str(k+1))
                            else:
                                n = str(k+1)
                            
                            ddr_xmpu_lines[j][0].append(xmpu_region_config(ddr_reg[j], memory_masterslave_dict[memory_register_value[i]][k][2], n))
                            ddr_xmpu_lines[j][1].append(xmpu_region_start(memory_register_value[i], ddr_reg[j], n))   
                            ddr_xmpu_lines[j][2].append(xmpu_region_end(hex(int(memory_register_value[i],16)+int("FFFFF",16)), ddr_reg[j], n))
                            ddr_xmpu_lines[j][3].append(xmpu_region_master(memory_masterslave_dict[memory_register_value[i]][k][0], ddr_reg[j], n))
                            count = count +1
                    ddr_region = count+1
                    if ddr_region<10:
                        ddr_region_number = "0"+str(ddr_region)
                    else:
                        ddr_region_number = str(ddr_region)
                    ddr_xmpu_lines[j][0].append(dap_region(ddr_reg[j], memory_register_value[i], hex(int(memory_register_value[i],16)+int("FFFFF",16)), ddr_region_number)[0])
                    ddr_xmpu_lines[j][1].append(dap_region(ddr_reg[j], memory_register_value[i], hex(int(memory_register_value[i],16)+int("FFFFF",16)), ddr_region_number)[1])    
                    ddr_xmpu_lines[j][2].append(dap_region(ddr_reg[j], memory_register_value[i], hex(int(memory_register_value[i],16)+int("FFFFF",16)), ddr_region_number)[2])
                    ddr_xmpu_lines[j][3].append(dap_region(ddr_reg[j], memory_register_value[i], hex(int(memory_register_value[i],16)+int("FFFFF",16)), ddr_region_number)[3])   

        elif hex(memory_register_value[i]) == "0x800000000":
            ddr_reg = ["0xfd000000", "0xfd010000", "0xfd020000", "0xfd030000", "0xfd040000", "0xfd050000"]
            ddr_region = 0
            for j in range(0, 6):
                ddr_xmpu_lines[j][0].append("	PSU_Mask_Write(DDR_XMPU" + str(j) + "_CFG_R00_CONFIG_OFFSET, 0x0000001FU, 0x00000001U);\n")
                ddr_xmpu_lines[j][1].append("	PSU_Mask_Write(DDR_XMPU" + str(j) + "_CFG_R00_START_OFFSET, 0x0FFFFFFFU, 0x00800000U);\n")    
                ddr_xmpu_lines[j][2].append("	PSU_Mask_Write(DDR_XMPU" + str(j) + "_CFG_R00_END_OFFSET, 0x0FFFFFFFU, 0x008000FFU);\n")
                ddr_xmpu_lines[j][3].append("	PSU_Mask_Write(DDR_XMPU" + str(j) + "_CFG_R00_MASTER_OFFSET, 0x03FF03FFU, 0x00000000U);\n")    
                ddr_xmpu_lines[j].append(xmpu_poison(ddr_reg[j]))

                if j == 1 or j == 2:
                    for k in range(0, len(memory_masterslave_dict[memory_register_value[i]])):
                        if k<9:
                            n = str("0"+str(k+1))
                        else:
                            n = str(k+1)
                        if memory_masterslave_dict[memory_register_value[i]][k][0] == "0" or memory_masterslave_dict[memory_register_value[i]][k][0] == "16":
                            ddr_region = k+1
                        elif memory_masterslave_dict[memory_register_value[i]][k][0] != "0" and memory_masterslave_dict[memory_register_value[i]][k][0] != "16":
                            ddr_region = k+2
                            ddr_xmpu_lines[j][0].append(xmpu_region_config(ddr_reg[j], memory_masterslave_dict[memory_register_value[i]][k][1], n))
                            ddr_xmpu_lines[j][1].append(xmpu_region_start(memory_masterslave_dict[memory_register_value[i]][k][2], ddr_reg[j], n))   
                            ddr_xmpu_lines[j][2].append(xmpu_region_end(memory_masterslave_dict[memory_register_value[i]][k][3], ddr_reg[j], n))
                            ddr_xmpu_lines[j][3].append(xmpu_region_master(memory_masterslave_dict[memory_register_value[i]][k][0], ddr_reg[j], n))
                    if ddr_region<10:
                        ddr_region_number = "0"+str(ddr_region)
                    else:
                        ddr_region_number = str(ddr_region)
                    ddr_xmpu_lines[j][0].append("	PSU_Mask_Write(DDR_XMPU" + str(j) + "_CFG_R" + str(ddr_region_number) + "_CONFIG_OFFSET, 0x0000001FU, 0x0000000FU);\n")
                    ddr_xmpu_lines[j][1].append("	PSU_Mask_Write(DDR_XMPU" + str(j) + "_CFG_R" + str(ddr_region_number) + "_START_OFFSET, 0x0FFFFFFFU, 0x00800000U);\n")    
                    ddr_xmpu_lines[j][2].append("	PSU_Mask_Write(DDR_XMPU" + str(j) + "_CFG_R" + str(ddr_region_number) + "_END_OFFSET, 0x0FFFFFFFU, 0x008000FFU);\n")
                    ddr_xmpu_lines[j][3].append("	PSU_Mask_Write(DDR_XMPU" + str(j) + "_CFG_R" + str(ddr_region_number) + "_MASTER_OFFSET, 0x03FF03FFU, 0x03FF0062U);\n")   
    
        elif hex(memory_register_value[i]) != "0xffa70000" or hex(memory_register_value[i]) != "0x1000000" or hex(memory_register_value[i]) != "0x800000000":
            fpd_region_number = 0
            for j in range(0, len(memory_masterslave_dict[memory_register_value[i]])):
                fpd_region_number = 10+j
                fpd_xmpu_lines[0].append(xmpu_region_config("0xfd5d0000", memory_masterslave_dict[memory_register_value[i]][j][1], str(fpd_region_number)))
                fpd_xmpu_lines[1].append(xmpu_region_start(memory_masterslave_dict[memory_register_value[i]][j][2], "0xfd5d0000", str(fpd_region_number)))
                fpd_xmpu_lines[2].append(xmpu_region_end(memory_masterslave_dict[memory_register_value[i]][j][3], "0xfd5d0000", str(fpd_region_number)))
                fpd_xmpu_lines[3].append(xmpu_region_master(memory_masterslave_dict[memory_register_value[i]][j][0], "0xfd5d0000", str(fpd_region_number)))
            fpd_region_number = fpd_region_number + 1
            fpd_xmpu_lines[0].append("	PSU_Mask_Write(FPD_XMPU_CFG_R" + str(fpd_region_number) + "_CONFIG_OFFSET, 0x0000001FU, 0x00000007U);\n")
            fpd_xmpu_lines[1].append("	PSU_Mask_Write(FPD_XMPU_CFG_R" + str(fpd_region_number) + "_START_OFFSET, 0x0FFFFFFFU, "+fpd_add(memory_masterslave_dict[memory_register_value[i]][j][2])+"U);\n")
            fpd_xmpu_lines[2].append("	PSU_Mask_Write(FPD_XMPU_CFG_R" + str(fpd_region_number) + "_END_OFFSET, 0x0FFFFFFFU, "+fpd_add(memory_masterslave_dict[memory_register_value[i]][j][3])+"U);\n")
            fpd_xmpu_lines[3].append("	PSU_Mask_Write(FPD_XMPU_CFG_R" + str(fpd_region_number) + "_MASTER_OFFSET, 0x03FF03FFU, 0x03FF0040U);\n")
            

    o = open("/proj/xhdsswstaff1/srilaxmi/vimdiff/psu_init.c", "rt")
    code = o.readlines()
    line_number = []
    code_lines = []
    new_lines = []

    for i, line in enumerate(code):
        line_number.append(i+1)
        code_lines.append(line)

    a=b=c=d=e=f=g=h=p=0
    
    for i in range(0,len(line_number)):
        if "LPD_XPPU_CFG_MASTER_ID19_OFFSET" in code_lines[i]:
            a = i
        if "psu_ocm_xmpu_data(void)" in code_lines[i]:
            b = i
        if "psu_ddr_xmpu0_data(void)" in code_lines[i]:
            c = i
        if "psu_ddr_xmpu1_data(void)" in code_lines[i]:
            d = i
        if "psu_ddr_xmpu2_data(void)" in code_lines[i]:
            e = i
        if "psu_ddr_xmpu3_data(void)" in code_lines[i]:
            f = i
        if "psu_ddr_xmpu4_data(void)" in code_lines[i]:
            g = i
        if "psu_ddr_xmpu5_data(void)" in code_lines[i]:
            h = i
        if "psu_fpd_xmpu_data(void)" in code_lines[i]:
            p = i
    
    for i in range(0,a+3):
        new_lines.append(code_lines[i])
    for i in range(0,len(xppu_lines)):
        new_lines.append(xppu_lines[i])
    
    for i in range(a+3, c+5):
        new_lines.append(code_lines[i])
    if len(ddr_xmpu_lines[0][0]) != 0:
        append_lines(ddr_xmpu_lines[0], new_lines)
    for i in range(c+5, d+5):
        new_lines.append(code_lines[i])
    if len(ddr_xmpu_lines[1][0]) != 0:
        append_lines(ddr_xmpu_lines[1], new_lines)
    for i in range(d+5, e+5):
        new_lines.append(code_lines[i])
    if len(ddr_xmpu_lines[2][0]) != 0:
        append_lines(ddr_xmpu_lines[2], new_lines)
    for i in range(e+5, f+5):
        new_lines.append(code_lines[i])
    if len(ddr_xmpu_lines[3][0]) != 0:
        append_lines(ddr_xmpu_lines[3], new_lines)
    for i in range(f+5, g+5):
        new_lines.append(code_lines[i])
    if len(ddr_xmpu_lines[4][0]) != 0:
        append_lines(ddr_xmpu_lines[4], new_lines)
    for i in range(g+5, h+5):
        new_lines.append(code_lines[i])
    if len(ddr_xmpu_lines[5][0]) != 0:
        append_lines(ddr_xmpu_lines[5], new_lines)

    for i in range(h+5, b+5):
        new_lines.append(code_lines[i])
    if len(ocm_xmpu_lines[0]) != 0:
        append_lines(ocm_xmpu_lines, new_lines)

    for i in range(b+5, p+2):
        new_lines.append(code_lines[i])
    if len(fpd_xmpu_lines[0]) != 0:
        append_lines(fpd_xmpu_lines, new_lines)
    
    for i in range(p+5, len(line_number)):
        new_lines.append(code_lines[i])


    o.close()
    # Re-writing the psu_init.c file
    open(str(options['args'][1]), "w").close()
    
    n = open(str(options['args'][1]), "w")
    for i in range(0, (len(new_lines))):
        n.write(new_lines[i])
    n.close()
    
    return 0
