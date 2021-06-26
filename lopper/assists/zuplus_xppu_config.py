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
from lopper.tree import *
from re import *
import numpy as np

sys.path.append(os.path.dirname(__file__))
from bmcmake_metadata_xlnx import *
from baremetalconfig_xlnx import *
from zuplus_xppu_default2 import *

def is_compat(node, compat_string_to_test):
    if re.search( "module,zuplus_xppu_config", compat_string_to_test):
        return xppu
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



def config(cpu_name, reg_value, setting):
    # Aperture permission offset dictionary
    offset_dict = {'0xff000000' : 'LPD_XPPU_CFG_APERPERM_000_OFFSET', '0xff010000' : 'LPD_XPPU_CFG_APERPERM_001_OFFSET', '0xff020000' : 'LPD_XPPU_CFG_APERPERM_002_OFFSET', '0xff030000' : 'LPD_XPPU_CFG_APERPERM_003_OFFSET', '0xff040000' : 'LPD_XPPU_CFG_APERPERM_004_OFFSET', '0xff050000' : 'LPD_XPPU_CFG_APERPERM_005_OFFSET', '0xff060000' : 'LPD_XPPU_CFG_APERPERM_006_OFFSET', '0xff070000' : 'LPD_XPPU_CFG_APERPERM_007_OFFSET', '0xff080000' : 'LPD_XPPU_CFG_APERPERM_008_OFFSET', '0xff090000' : 'LPD_XPPU_CFG_APERPERM_009_OFFSET', '0xff0a0000' : 'LPD_XPPU_CFG_APERPERM_010_OFFSET', '0xff0b0000' : 'LPD_XPPU_CFG_APERPERM_011_OFFSET', '0xff0c0000' : 'LPD_XPPU_CFG_APERPERM_012_OFFSET', '0xff0d0000' : 'LPD_XPPU_CFG_APERPERM_013_OFFSET', '0xff0e0000' : 'LPD_XPPU_CFG_APERPERM_014_OFFSET', '0xff0f0000' : 'LPD_XPPU_CFG_APERPERM_015_OFFSET', '0xff100000' : 'LPD_XPPU_CFG_APERPERM_016_OFFSET', '0xff110000' : 'LPD_XPPU_CFG_APERPERM_017_OFFSET', '0xff120000' : 'LPD_XPPU_CFG_APERPERM_018_OFFSET', '0xff130000' : 'LPD_XPPU_CFG_APERPERM_019_OFFSET', '0xff140000' : 'LPD_XPPU_CFG_APERPERM_020_OFFSET', '0xff150000' : 'LPD_XPPU_CFG_APERPERM_021_OFFSET', '0xff160000' : 'LPD_XPPU_CFG_APERPERM_022_OFFSET', '0xff170000' : 'LPD_XPPU_CFG_APERPERM_023_OFFSET', '0xff240000' : 'LPD_XPPU_CFG_APERPERM_036_OFFSET', '0xff250000' : 'LPD_XPPU_CFG_APERPERM_037_OFFSET', '0xff260000' : 'LPD_XPPU_CFG_APERPERM_038_OFFSET', '0xff270000' : 'LPD_XPPU_CFG_APERPERM_039_OFFSET', '0xff280000' : 'LPD_XPPU_CFG_APERPERM_040_OFFSET', '0xff290000' : 'LPD_XPPU_CFG_APERPERM_041_OFFSET', '0xff2a0000' : 'LPD_XPPU_CFG_APERPERM_042_OFFSET', '0xff2b0000' : 'LPD_XPPU_CFG_APERPERM_043_OFFSET', '0xff2c0000' : 'LPD_XPPU_CFG_APERPERM_044_OFFSET', '0xff2d0000' : 'LPD_XPPU_CFG_APERPERM_045_OFFSET', '0xff2e0000' : 'LPD_XPPU_CFG_APERPERM_046_OFFSET', '0xff2f0000' : 'LPD_XPPU_CFG_APERPERM_047_OFFSET', '0xff340000' : 'LPD_XPPU_CFG_APERPERM_052_OFFSET', '0xff350000' : 'LPD_XPPU_CFG_APERPERM_053_OFFSET', '0xff360000' : 'LPD_XPPU_CFG_APERPERM_054_OFFSET', '0xff370000' : 'LPD_XPPU_CFG_APERPERM_055_OFFSET', '0xff380000' : 'LPD_XPPU_CFG_APERPERM_056_OFFSET', '0xff390000' : 'LPD_XPPU_CFG_APERPERM_057_OFFSET', '0xff3a0000' : 'LPD_XPPU_CFG_APERPERM_058_OFFSET', '0xff3b0000' : 'LPD_XPPU_CFG_APERPERM_059_OFFSET', '0xff3c0000' : 'LPD_XPPU_CFG_APERPERM_060_OFFSET', '0xff3d0000' : 'LPD_XPPU_CFG_APERPERM_061_OFFSET', '0xff3e0000' : 'LPD_XPPU_CFG_APERPERM_062_OFFSET', '0xff3f0000' : 'LPD_XPPU_CFG_APERPERM_063_OFFSET', '0xff400000' : 'LPD_XPPU_CFG_APERPERM_064_OFFSET', '0xff4b0000' : 'LPD_XPPU_CFG_APERPERM_075_OFFSET', '0xff4c0000' : 'LPD_XPPU_CFG_APERPERM_076_OFFSET', '0xff4d0000' : 'LPD_XPPU_CFG_APERPERM_077_OFFSET', '0xff4e0000' : 'LPD_XPPU_CFG_APERPERM_078_OFFSET', '0xff4f0000' : 'LPD_XPPU_CFG_APERPERM_079_OFFSET', '0xff500000' : 'LPD_XPPU_CFG_APERPERM_080_OFFSET', '0xff510000' : 'LPD_XPPU_CFG_APERPERM_081_OFFSET', '0xff520000' : 'LPD_XPPU_CFG_APERPERM_082_OFFSET', '0xff530000' : 'LPD_XPPU_CFG_APERPERM_083_OFFSET', '0xff540000' : 'LPD_XPPU_CFG_APERPERM_084_OFFSET', '0xff550000' : 'LPD_XPPU_CFG_APERPERM_085_OFFSET', '0xff560000' : 'LPD_XPPU_CFG_APERPERM_086_OFFSET', '0xff570000' : 'LPD_XPPU_CFG_APERPERM_087_OFFSET', '0xff580000' : 'LPD_XPPU_CFG_APERPERM_088_OFFSET', '0xff590000' : 'LPD_XPPU_CFG_APERPERM_089_OFFSET', '0xff5a0000' : 'LPD_XPPU_CFG_APERPERM_090_OFFSET', '0xff5b0000' : 'LPD_XPPU_CFG_APERPERM_091_OFFSET', '0xff5c0000' : 'LPD_XPPU_CFG_APERPERM_092_OFFSET', '0xff5d0000' : 'LPD_XPPU_CFG_APERPERM_093_OFFSET', '0xff860000' : 'LPD_XPPU_CFG_APERPERM_134_OFFSET', '0xff870000' : 'LPD_XPPU_CFG_APERPERM_135_OFFSET', '0xff880000' : 'LPD_XPPU_CFG_APERPERM_136_OFFSET', '0xff890000' : 'LPD_XPPU_CFG_APERPERM_137_OFFSET', '0xff8a0000' : 'LPD_XPPU_CFG_APERPERM_138_OFFSET', '0xff8b0000' : 'LPD_XPPU_CFG_APERPERM_139_OFFSET', '0xff8c0000' : 'LPD_XPPU_CFG_APERPERM_140_OFFSET', '0xff8d0000' : 'LPD_XPPU_CFG_APERPERM_141_OFFSET', '0xff8e0000' : 'LPD_XPPU_CFG_APERPERM_142_OFFSET', '0xff8f0000' : 'LPD_XPPU_CFG_APERPERM_143_OFFSET', '0xff900000' : 'LPD_XPPU_CFG_APERPERM_144_OFFSET', '0xff910000' : 'LPD_XPPU_CFG_APERPERM_145_OFFSET', '0xff920000' : 'LPD_XPPU_CFG_APERPERM_146_OFFSET', '0xff930000' : 'LPD_XPPU_CFG_APERPERM_147_OFFSET', '0xff940000' : 'LPD_XPPU_CFG_APERPERM_148_OFFSET', '0xff950000' : 'LPD_XPPU_CFG_APERPERM_149_OFFSET', '0xff960000' : 'LPD_XPPU_CFG_APERPERM_150_OFFSET', '0xff970000' : 'LPD_XPPU_CFG_APERPERM_151_OFFSET', '0xff980000' : 'LPD_XPPU_CFG_APERPERM_152_OFFSET', '0xff990000' : 'LPD_XPPU_CFG_APERPERM_153_OFFSET', '0xff9b0000' : 'LPD_XPPU_CFG_APERPERM_155_OFFSET', '0xff9c0000' : 'LPD_XPPU_CFG_APERPERM_156_OFFSET', '0xff9d0000' : 'LPD_XPPU_CFG_APERPERM_157_OFFSET', '0xff9e0000' : 'LPD_XPPU_CFG_APERPERM_158_OFFSET', '0xffa00000' : 'LPD_XPPU_CFG_APERPERM_160_OFFSET', '0xffa10000' : 'LPD_XPPU_CFG_APERPERM_161_OFFSET', '0xffa50000' : 'LPD_XPPU_CFG_APERPERM_165_OFFSET', '0xffa60000' : 'LPD_XPPU_CFG_APERPERM_166_OFFSET', '0xffa80000' : 'LPD_XPPU_CFG_APERPERM_168_OFFSET', '0xffa90000' : 'LPD_XPPU_CFG_APERPERM_169_OFFSET', '0xffaa0000' : 'LPD_XPPU_CFG_APERPERM_170_OFFSET', '0xffab0000' : 'LPD_XPPU_CFG_APERPERM_171_OFFSET', '0xffac0000' : 'LPD_XPPU_CFG_APERPERM_172_OFFSET', '0xffad0000' : 'LPD_XPPU_CFG_APERPERM_173_OFFSET', '0xffae0000' : 'LPD_XPPU_CFG_APERPERM_174_OFFSET', '0xffaf0000' : 'LPD_XPPU_CFG_APERPERM_175_OFFSET', '0xffc30000' : 'LPD_XPPU_CFG_APERPERM_195_OFFSET', '0xffc80000' : 'LPD_XPPU_CFG_APERPERM_200_OFFSET', '0xffca0000' : 'LPD_XPPU_CFG_APERPERM_202_OFFSET', '0xffcb0000' : 'LPD_XPPU_CFG_APERPERM_203_OFFSET', '0xffce0000' : 'LPD_XPPU_CFG_APERPERM_206_OFFSET', '0xffcf0000' : 'LPD_XPPU_CFG_APERPERM_207_OFFSET', '0xffd80000' : 'LPD_XPPU_CFG_APERPERM_216_OFFSET', '0xfe000000' : 'LPD_XPPU_CFG_APERPERM_384_OFFSET', '0xfe100000' : 'LPD_XPPU_CFG_APERPERM_385_OFFSET', '0xfe200000' : 'LPD_XPPU_CFG_APERPERM_386_OFFSET', '0xfe300000' : 'LPD_XPPU_CFG_APERPERM_387_OFFSET', '0xfe400000' : 'LPD_XPPU_CFG_APERPERM_388_OFFSET', '0xfe500000' : 'LPD_XPPU_CFG_APERPERM_389_OFFSET', '0xfe600000' : 'LPD_XPPU_CFG_APERPERM_390_OFFSET', '0xfe700000' : 'LPD_XPPU_CFG_APERPERM_391_OFFSET', '0xfe800000' : 'LPD_XPPU_CFG_APERPERM_392_OFFSET', '0xfe900000' : 'LPD_XPPU_CFG_APERPERM_393_OFFSET', '0xfea00000' : 'LPD_XPPU_CFG_APERPERM_394_OFFSET', '0xfeb00000' : 'LPD_XPPU_CFG_APERPERM_395_OFFSET', '0xfec00000' : 'LPD_XPPU_CFG_APERPERM_396_OFFSET', '0xfed00000' : 'LPD_XPPU_CFG_APERPERM_397_OFFSET', '0xfee00000' : 'LPD_XPPU_CFG_APERPERM_398_OFFSET', '0xfef00000' : 'LPD_XPPU_CFG_APERPERM_399_OFFSET', '0xc0000000' : 'LPD_XPPU_CFG_APERPERM_400_OFFSET'}
    # CPU dictionary
    cpu_dict = { 'cpus_a53' : 'APU', 'cpus_r5' : 'RPU'}
    # Finding the Master 
    master = cpu_dict[cpu_name]
    # Finiding the Aperture permission offset value
    offset_value = ['' for offset_value in range(len(reg_value))]
    for i in range(0, len(offset_value)):
        offset_value[i] = offset_value[i]+offset_dict[reg_value[i]]
    setting_bin = []
    # Converting the Decimal value into Binary
    for i in range(0, len(setting)):
        setting_bin.append(bin(setting[i]).replace("0b", "").zfill(32))
    
    # Initialising all the bits of value into zeros and switching them to ones based on the settings
    value = np.zeros(shape=(len(setting_bin),32), dtype = int)
    par_0 = np.zeros(shape=(len(setting_bin),6), dtype = int)
    par_1 = np.zeros(shape=(len(setting_bin),5), dtype = int)
    par_2 = np.zeros(shape=(len(setting_bin),5), dtype = int)
    par_3 = np.zeros(shape=(len(setting_bin),5), dtype = int)

    final_line = ["" for final_line in range(len(setting_bin))]
    value_str = ["" for value_str in range(len(setting_bin))] 
    value_hex = ["" for value_hex in range(len(setting_bin))]
    for i in range(0, len(setting_bin)):
        value[i][24]=1  

        if setting_bin[i][29]==setting_bin[i][30]:
            if master == "APU":
                value[i][25] = 1
            elif master == "RPU":
                value[i][20] = 1

        else:
            if master == "APU":
                value[i][26] = 1
            elif master == "RPU":
                value[i][21] = 1 
 
        if setting_bin[i][31] == "0":
            value[i][4] =1
    
        # Checking the parity
        par_0[i][0] = value[i][4]
        for j in range(12,17):
            for k in range(1,len(par_0[i])):
                par_0[i][k] = value[i][j]
        value[i][0] = parity(par_0[i])
    
        for j in range(17,22):
            for k in range(0,len(par_1[i])):
                par_1[i][k] = value[i][j]
        value[i][1] = parity(par_1[i])
        
        for j in range(22,27):
            for k in range(0,len(par_2[i])):
                par_2[i][k] = value[i][j]
        value[i][2] = parity(par_2[i])
        
        for j in range(27,32):
            for k in range(0,len(par_3[i])):
                par_3[i][k] = value[i][j]
        value[i][3] = parity(par_3[i])
    
        
        for j in range(0, len(value[i])):
            value_str[i] = value_str[i] + str(value[i][j])
        
        # Converting Value from binary to hexadecimal
        value_hex[i] = hex(int(str(value_str[i]), 2))

        final_line[i] = "	PSU_Mask_Write(" + str(offset_value[i]) + ", " + "0xF80FFFFFU" + ", " + str(value_hex[i]) +"U);"
    
    return final_line

def xppu(tgt_node, sdt, options):
    root_node = sdt.tree[tgt_node]
    root_sub_nodes = root_node.subnodes()
    node_list = []
    chosen_node = ""
    # Traverse the tree and find the value of the access and cpu property
    for node in root_sub_nodes:
        
        try:
            access = node["access"].value
            cpu = node["cpus"].value
            # Finding the value of reg property
            prop = "reg"
            value = node["reg"].value
            reg_value = get_phandle_regprop(sdt, prop, value)
	
        except:
            pass
    
    
    setting = []
    # Finding the name of the CPU
    parent_node_cpu = sdt.FDT.node_offset_by_phandle(cpu[0])
    name_cpu = sdt.FDT.get_name(parent_node_cpu)

    # Finding the read-write permissions and trustzone settings
    for i in range(0, len(access)):
        if i%2 == 0:
            setting.append(access[i+1])
   
    final_lines = config(name_cpu, reg_value, setting)
    print(final_lines)
    o = open(str(options['args'][0]), "rt")
    code = o.readlines()
    line_number = []
    code_lines = []
    new_lines = []

    for i, line in enumerate(code):
        line_number.append(i+1)
        code_lines.append(line)

    a=b=c=d=e=0


    for i in range(0,len(line_number)):
        if "psu_init_xppu_aper_ram();" in code_lines[i]:
            a= i
        if "psu_init_xppu_aper_ram(void)" in code_lines[i]:
            b=i
        if "psu_lpd_xppu_data(void)" in code_lines[i]:
            d = i
            
    for i in range(b, len(line_number)):
        if "return" in code_lines[i]:
            c = i
            break

    for i in range(d, len(line_number)):
        if "return" in code_lines[i]:
            e = i
            break

    # Functions returning the default MasterID list and Aperture permission list
    xppu_masterid_data = xppu_masteridlist()
    xppu_aperperm_data = xppu_aperpermlist()
    xppu_aper_ram = xppu_aperpermram()

    for i in range(0,d+2):
        new_lines.append(code_lines[i])

    for i in range(0, len(xppu_masterid_data)):
        new_lines.append(xppu_masterid_data[i])
 
    for i in range(0, len(final_lines)):
        new_lines.append(str(final_lines[i]))

    for i in range(0,len(xppu_aperperm_data)):
        new_lines.append(xppu_aperperm_data[i])

    for i in range(e,b+2):
        new_lines.append(code_lines[i])

    for i in range(0,len(xppu_aper_ram)):
        new_lines.append(xppu_aper_ram[i])

    for i in range(b+2, c+1):
        new_lines.append(code_lines[i])

    for i in range(c+1, a+1):
        new_lines.append(code_lines[i])

    new_lines.append(str("	psu_lpd_xppu_data();"))
	
    for i in range(a+2,(len(line_number)+1)):
        new_lines.append(code_lines[i-1])
    
    # Re-writing the psu_init.c file
    open(str(options['args'][0]), "w").close()
    n = open(str(options['args'][0]), "w")
    for i in range(0, (len(new_lines))):
        n.write(new_lines[i])
        n.write("\n")
    o.close()
    n.close()     
    return 0
 
