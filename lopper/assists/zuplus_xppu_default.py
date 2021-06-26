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

def MIDR(value):
    value_binary = "{0:08b}".format(int(value, 32))
    read_permission = value_binary[1]
    return read_permission

def MIDM(value):
    value_binary = "{0:08b}".format(int(value, 32))
    mask = ""
    for i in range(6,16):
        mask = mask + str(value_binary[i])
    mask_dec = int(mask,2)
    return mask_dec

def MID(value):
    value_binary = "{0:08b}".format(int(value, 32))
    master_ID = ""
    for i in range(22,32):
        master_ID = master_ID + str(value_binary[i])
    masterID_dec = int(master_ID,2)
    return masterID_dec

def xppu_masterID_lines(n, masterID_type):
    masterID_lines = "\n	/*\n    * Register : MASTER_ID" + str(n) + "@ " + masterid_reg_dict[str(n)] + "\n\n    * If set, only read transactions are allowed for the masters matching this\n    *  register\n    *  PSU_LPD_XPPU_CFG_MASTER_ID" + str(n) + "_MIDR                           " + MIDR(masterid_value_dict[str(n)]) + "\n\n    * Mask to be applied before comparing\n    *  PSU_LPD_XPPU_CFG_MASTER_ID" + str(n) + "_MIDM                           " + MIDM(masterid_value_dict[str(n)]) + "\n\n    * " + str(masterID_type) + " Master ID " + masterid_processor_dict[str(n)] + "\n    *  PSU_LPD_XPPU_CFG_MASTER_ID" + str(n) + "_MID                            " + MID(masterid_value_dict[str(n)]) + "\n\n    * Master ID " + str(i) + " Register\n    * (OFFSET, MASK, VALUE)      (" + masterid_reg_dict[str(n)] + ", 0x43FF03FFU, " + masterid_value_dict[str(n)] + "U" + ")\n    */\n	PSU_Mask_Write(LPD_XPPU_CFG_MASTER_ID" + str(n) + "_OFFSET,\n		0x43FF03FFU, " + masterid_value_dict[str(n)] + "U" + ");\n/*##################################################################### */\n"
    return masterID_lines

def aperperm_permission(value):
    value_binary = "{0:08b}".format(int(value, 32))
    master_ID = ""
    for i in range(12,32):
        master_ID = master_ID + str(value_binary[i])
    masterID_hex = hex(int(str(master_ID), 2))
    return masterID_hex

def aperperm_tz(value):
    value_binary = "{0:08b}".format(int(value, 32))
    read_permission = value_binary[4]
    read_permission_hex = hex(int(str(read_permission), 2))
    return read_permission_hex

def aperperm_parity(value):
    value_binary = "{0:08b}".format(int(value, 32))
    parity = ""
    for i in range(0,4):
        parity = parity + str(value_binary[i])
    parity_hex = hex(int(str(parity), 2))
    return parity_hex

def base_add(n, size):
    base_add = hex(int(base_add, 16) + int(size, 16))
    return base_add

def xppu_AperPerm_lines(n, size, base_add, aperture_type):
    aperperm_lines = "\n\n    /*\n    * Register : APERPERM_" + str(n) + " @ " + str(aperperm_reg_dict[str(n)]) + "\n\n    * This field defines the MASTER ID match criteria. Each entry in the IDL c\n    * orresponds to a bit in this field. 0=not match, 1=match.\n    *  PSU_LPD_XPPU_CFG_APERPERM_" + str(n) + "_PERMISSION                    " + aperperm_permission(aperperm_value_dict[str(n)]) + "\n\n    * 1=secure or non-secure transactions are allowed 0=only secure transactio\n    * na are allowed\n    *  PSU_LPD_XPPU_CFG_APERPERM_" + str(n) + "_TRUSTZONE                     " + aperperm_tz(aperperm_value_dict[str(n)]) + "\n\n    * SW must calculate and set up parity, if parity check is enabled by the C\n    * TRL register. 31: parity for bits 19:15 30: parity for bits 14:10 29: pa\n    * rity for bits 9:5 28: parity for bits 27, 4:0\n    *  PSU_LPD_XPPU_CFG_APERPERM_" + str(n) + "_PARITY                        " + aperperm_parity(aperperm_value_dict[str(n)]) + "\n\n    * Entry " + str(n) + " of the Aperture Permission List, for " + size + "-byte " + aperture_type + " at\n    *  BASE_" + size + "B + " + base_add + "\n    * (OFFSET, MASK, VALUE)      (" + aperperm_reg_dict[str(n)] + ", 0xF80FFFFFU ," + aperperm_value_dict[str(n)] + ")\n    */\n	PSU_Mask_Write(LPD_XPPU_CFG_APERPERM_" + str(n) + "_OFFSET,\n		0xF80FFFFFU, " + aperperm_value_dict[str(n)] + ");\n/*##################################################################### */\n\n"
    return aperperm_lines

def xppu_masteridlist():

    masterid_reg_dict = { '00' : '0XFF980100', '01' : '0XFF980104', '02' : '0XFF980108', '03' : '0XFF98010C', '04' : '0XFF980110', '05' : '0XFF980114', '06' : '0XFF980118', '07' : '0XFF98011C', '08' : '0XFF980120', '09' : '0XFF980124', '10' : '0XFF980128', '11' : '0XFF98012C', '12' : '0XFF980130', '13' : '0XFF980134', '19' : '0XFF98014C' }
    
    masterid_value_dict = { '00' : '0x00480048', '01' : '0x00500050', '02' : '0x00620060', '03' : '0x00C000C0', '04' : '0x02000200', '05' : '0x42C00080', '06' : '0x02C00080', '07' : '0x03FF0062', '08' : '0x43FF0040', '09' : '0x03FF0040', '10' : '0x42D00000', '11' : '0x02D00000', '12' : '0x42D00010', '13' : '0x02D00010', '19' : '0x00000000' }

    masterid_processor_dict = { '00' : 'for RMU', '01' : 'for RPU0', '02' : 'for RPU1', '03' : 'for APU', '04' : 'for A53 Core 0', '05' : 'for A53 Core 1', '06' : 'for A53 Core 2', '07' : 'for A53 Core 3', '08' : ' ', '09' : ' ', '10' : ' ', '11' : ' ', '12' : ' ', '13' : ' '}
    
    xppu_masterid_lines = "\n	/*\n    * MASTER ID LIST\n    */"
    for i in range(0, 8):
        n = "0" + str(i)
        masterID_type = "Predefined"
        xppu_masterid_lines = xppu_masterid_lines + xppu_masterID_lines(n, masterID_type)

    for i in range(8, 14):
        if i<10:
            n = "0" + str(i)
            masterID_type = "Programmable"
            xppu_masterid_lines = xppu_masterid_lines + xppu_masterID_lines(n, masterID_type)

        elif:
            n = str(i)
            masterID_type = "Programmable"
            xppu_masterid_lines = xppu_masterid_lines + xppu_masterID_lines(n, masterID_type)

    xppu_masterid_lines = xppu_masterid_lines + "\n	/*\n    * Register : MASTER_ID19 @ 0XFF98014C\n\n    * If set, only read transactions are allowed for the masters matching this\n    *  register\n    *  PSU_LPD_XPPU_CFG_MASTER_ID19_MIDR                           0\n\n    * Mask to be applied before comparing\n    *  PSU_LPD_XPPU_CFG_MASTER_ID19_MIDM                           0\n\n    * Programmable Master ID\n    *  PSU_LPD_XPPU_CFG_MASTER_ID19_MID                            0\n\n    * Master ID19 Register\n    * (OFFSET, MASK, VALUE)      (0XFF980100, 0x43FF03FFU, 0x00000000U)\n    */\n	PSU_Mask_Write(LPD_XPPU_CFG_MASTER_ID19_OFFSET,\n		0x43FF03FFU, 0x00000000U);\n/*##################################################################### */\n"    

    xppu_masterid_data = xppu_masterid_lines.splitlines()
    
    return xppu_masterid_data

def xppu_aperpermlist():

    aperperm_reg_dict = { '024' : '0XFF981060', '025' : '0XFF981064', '026' : '0XFF981068', '027' : '0XFF98106C', '028' : '0XFF981070', '029' : '0XFF981074', '030' : '0XFF981078', '031' : '0XFF98107C', '032' : '0XFF981080', '033' : '0XFF981084', '034' : '0XFF981088', '035' : '0XFF98108C', '048' : '0XFF9810C0', '049' : '0XFF9810C4', '050' : '0XFF9810C8', '051' : '0XFF9810CC', '065' : '0XFF981104', '066' : '0XFF981108', '067' : '0XFF98110C', '068' : '0XFF981110', '069' : '0XFF981114', '070' : '0XFF981118', '071' : '0XFF98111C', '072' : '0XFF98111C', '073' : '0XFF981124', '074' : '0XFF981128', '094' : '0XFF981178', '095' : '0XFF98117C', '096' : '0XFF981180', '097' : '0XFF981184', '098' : '0XFF981188', '099' : '0XFF98118C', '100' : '0XFF981190', '101' : '0XFF981194', '102' : '0XFF981198', '103' : '0XFF98119C', '104' : '0XFF9811A0', '105' : '0XFF9811A4', '106' : '0XFF9811A8', '107' : '0XFF9811AC', '108' : '0XFF9811B0', '109' : '0XFF9811B4', '110' : '0XFF9811B8', '111' : '0XFF9811BC', '112' : '0XFF9811C0', '113' : '0XFF9811C4', '114' : '0XFF9811C8', '115' : '0XFF9811CC', '116' : '0XFF9811D0', '117' : '0XFF9811D4', '118' : '0XFF9811D8', '119' : '0XFF9811DC', '120' : '0XFF9811E0', '121' : '0XFF9811E4', '122' : '0XFF9811E8', '123' : '0XFF9811EC', '124' : '0XFF9811F0', '125' : '0XFF9811F4', '126' : '0XFF9811F8', '127' : '0XFF9811FC', '128' : '0XFF981200', '129' : '0XFF981204', '130' : '0XFF981208', '131' : '0XFF98120C', '132' : '0XFF981210', '133' : '0XFF981214', '154' : '0XFF981268', '167' : '0XFF98129C', '204' : '0XFF981330', '256' : '0XFF981400', '257' : '0XFF981404', '258' : '0XFF981408', '259' : '0XFF98140C', '260' : '0XFF981410', '261' : '0XFF981414', '262' : '0XFF981418', '263' : '0XFF98141C', '264' : '0XFF981420', '265' : '0XFF981424', '266' : '0XFF981428', '267' : '0XFF98142C', '268' : '0XFF981430', '269' : '0XFF981434', '270' : '0XFF981438', '271' : '0XFF98143C', '272' : '0XFF981440', '273' : '0XFF981444', '274' : '0XFF981448', '275' : '0XFF98144C', '276' : '0XFF981450', '277' : '0XFF981454', '278' : '0XFF981458', '279' : '0XFF98145C', '280' : '0XFF981460', '281' : '0XFF981464', '282' : '0XFF981468', '283' : '0XFF98146C', '284' : '0XFF981470', '285' : '0XFF981474', '286' : '0XFF981478', '287' : '0XFF98147C', '288' : '0XFF981480', '289' : '0XFF981484', '290' : '0XFF981488', '291' : '0XFF98148C', '292' : '0XFF981490', '293' : '0XFF981494', '294' : '0XFF981498', '295' : '0XFF98149C', '296' : '0XFF9814A0', '297' : '0XFF9814A4', '298' : '0XFF9814A8', '299' : '0XFF9814AC', '300' : '0XFF9814B0', '301' : '0XFF9814B4', '302' : '0XFF9814B8', '303' : '0XFF9814BC', '304' : '0XFF9814C0', '305' : '0XFF9814C4', '306' : '0XFF9814C8', '307' : '0XFF9814CC', '308' : '0XFF9814D0', '309' : '0XFF9814D4', '318' : '0XFF9814F8', '319' : '0XFF9814FC', '320' : '0XFF981500', '321' : '0XFF981504', '322' : '0XFF981508', '323' : '0XFF98150C', '324' : '0XFF981510', '325' : '0XFF981514', '334' : '0XFF981538', '335' : '0XFF98153C', '336' : '0XFF981540', '337' : '0XFF981544', '338' : '0XFF981548', '339' : '0XFF98154C', '340' : '0XFF981550', '341' : '0XFF981554', '350' : '0XFF981578', '351' : '0XFF98157C', '352' : '0XFF981580', '353' : '0XFF981584', '354' : '0XFF981588', '355' : '0XFF98158C', '356' : '0XFF981590', '357' : '0XFF981594', '366' : '0XFF9815B8', '367' : '0XFF9815BC', '368' : '0XFF9815C0', '369' : '0XFF9815C4', '370' : '0XFF9815C8', '371' : '0XFF9815CC', '372' : '0XFF9815D0', '373' : '0XFF9815D4', '374' : '0XFF9815D8', '375' : '0XFF9815DC', '376' : '0XFF9815E0', '377' : '0XFF9815E4', '378' : '0XFF9815E8', '379' : '0XFF9815EC', '380' : '0XFF9815F0', '381' : '0XFF9815F4', '382' : '0XFF9815F8', '383' : '0XFF9815FC' }

    aperperm_value_dict = { '024' : '0x00002ADFU', '025' : '0x00002ADFU', '026' : '0x00002ADFU', '027' : '0x00002ADFU', '028' : '0x00002ADFU', '029' : '0x00002ADFU', '030' : '0x00002ADFU', '031' : '0x00002ADFU', '032' : '0x00002ADFU', '033' : '0x00002ADFU', '034' : '0x00002ADFU', '035' : '0x00002ADFU', '048' : '0x08000040U', '049' : '0x08000800U', '050' : '0x08002000U', '051' : '0x08000200U', '065' : '0x00000280U', '066' : '0x00000280U', '067' : '0x00000280U', '068' : '0x00000280U', '069' : '0x00000280U', '070' : '0x00000280U', '071' : '0x00000280U', '072' : '0x00000280U', '073' : '0x00000280U', '074' : '0x00000280U', '094' : '0x00002ADFU', '095' : '0x00002ADFU', '096' : '0x00002ADFU', '097' : '0x00002ADFU', '098' : '0x00002ADFU', '099' : '0x00002ADFU', '100' : '0x00002ADFU', '101' : '0x00002ADFU', '102' : '0x00002ADFU', '103' : '0x00002ADFU', '104' : '0x00002ADFU', '105' : '0x00002ADFU', '106' : '0x00002ADFU', '107' : '0x00002ADFU', '108' : '0x00002ADFU', '109' : '0x00002ADFU', '110' : '0x00002ADFU', '111' : '0x00002ADFU', '112' : '0x00002ADFU', '113' : '0x00002ADFU', '114' : '0x00002ADFU', '115' : '0x00002ADFU', '116' : '0x00002ADFU', '117' : '0x00002ADFU', '118' : '0x00002ADFU', '119' : '0x00002ADFU', '120' : '0x00002ADFU', '121' : '0x00002ADFU', '122' : '0x00002ADFU', '123' : '0x00002ADFU', '124' : '0x00002ADFU', '125' : '0x00002ADFU', '126' : '0x00002ADFU', '127' : '0x00002ADFU', '128' : '0x00002ADFU', '129' : '0x00002ADFU', '130' : '0x00002ADFU', '131' : '0x00002ADFU', '132' : '0x00002ADFU', '133' : '0x00002ADFU', '154' : '0x00002ADFU', '167' : '0x00000280U', '204' : '0x00002ADFU', '256' : '0x08000800U', '257' : '0x08000800U', '258' : '0x08001800U', '259' : '0x08002400U', '260' : '0x08000820U', '261' : '0x08000440U', '262' : '0x08000800U', '263' : '0x08000400U', '264' : '0x08000800U', '265' : '0x08000400U', '266' : '0x08000800U', '267' : '0x08000400U', '268' : '0x08000800U', '269' : '0x08000400U', '270' : '0x08000900U', '271' : '0x08000600U', '272' : '0x08002400U', '273' : '0x08001800U', '274' : '0x08002000U', '275' : '0x08002000U', '276' : '0x08002020U', '277' : '0x08001040U', '278' : '0x08002000U', '279' : '0x08001000U', '280' : '0x08002000U', '281' : '0x08001000U', '282' : '0x08002000U', '283' : '0x08001000U', '284' : '0x08002000U', '285' : '0x08001000U', '286' : '0x08002100U', '287' : '0x08001200U', '288' : '0x08000440U', '289' : '0x08000820U', '290' : '0x08001040U', '291' : '0x08002020U', '292' : '0x08000040U', '293' : '0x08000040U', '294' : '0x08000040U', '295' : '0x08000020U', '296' : '0x08000040U', '297' : '0x08000020U', '298' : '0x08000040U', '299' : '0x08000020U', '300' : '0x08000040U', '301' : '0x08000020U', '302' : '0x08000140U', '303' : '0x08000220U', '304' : '0x08000400U', '305' : '0x08000800U', '306' : '0x08001000U', '307' : '0x08002000U', '308' : '0x08000020U', '309' : '0x08000040U', '318' : '0x08000100U', '319' : '0x08000200U', '320' : '0x08000400U', '321' : '0x08000800U', '322' : '0x08001000U', '323' : '0x08002000U', '324' : '0x08000020U', '325' : '0x08000040U', '334' : '0x08000100U', '335' : '0x08000200U', '336' : '0x08000400U', '337' : '0x08000800U', '338' : '0x08001000U', '339' : '0x08002000U', '340' : '0x08000020U', '341' : '0x08000040U', '350' : '0x08000100U', '351' : '0x08000200U', '352' : '0x08000400U', '353' : '0x08000800U', '354' : '0x08001000U', '355' : '0x08002000U', '356' : '0x08000020U', '357' : '0x08000040U', '366' : '0x08000100U', '367' : '0x08000200U', '368' : '0x08000600U', '369' : '0x08000900U', '370' : '0x08001200U', '371' : '0x08002100U', '372' : '0x08000220U', '373' : '0x08000140U', '374' : '0x08000200U', '375' : '0x08000100U', '376' : '0x08000200U', '377' : '0x08000100U', '378' : '0x08000200U', '379' : '0x08000100U', '380' : '0x08000200U', '381' : '0x08000100U', '382' : '0x08000200U', '383' : '0x08000200U' }

    xppu_aperperm_lines = ""
    
    base_add1 = "0x00180000"
    for i in range(24,36):
        n = "0" + str(i)
        size = "64K"
        size_hex = "10000"
        aperture_type = "aperture"
        base_add1 = hex(int(base_add1, 16) + int(size_hex, 16))
        xppu_aperperm_lines = xppu_aperperm_lines + xppu_AperPerm_lines(n, size, base_add1, aperture_type)

    base_add2 = "0x00300000"
    for i in range(48,52):
        n = "0" + str(i)
        size = "64K"
        size_hex = "10000"
        aperture_type = "aperture"
        base_add2 = hex(int(base_add2, 16) + int(size_hex, 16))
        xppu_aperperm_lines = xppu_aperperm_lines + xppu_AperPerm_lines(n, size, base_add2, aperture_type)

    base_add3 = "0x00410000"
    for i in range(65,75):
        n = "0" + str(i)
        size = "64K"
        size_hex = "10000"
        aperture_type = "aperture"
        base_add3 = hex(int(base_add3, 16) + int(size_hex, 16))
        xppu_aperperm_lines = xppu_aperperm_lines + xppu_AperPerm_lines(n, size, base_add3, aperture_type)

    base_add4 = "0x005E0000"
    base_add5 = "0x00640000"
    for i in range(94,134):
        if i<100:
            n = "0" + str(i)
            size = "64K"
            size_hex = "10000"
            aperture_type = "aperture"
            base_add4 = hex(int(base_add4, 16) + int(size_hex, 16))
            xppu_aperperm_lines = xppu_aperperm_lines + xppu_AperPerm_lines(n, size, base_add4, aperture_type)
 
        elif:
            n = str(i)
            size = "64K"
            size_hex = "10000"
            aperture_type = "aperture"
            base_add5 = hex(int(base_add5, 16) + int(size_hex, 16))
            xppu_aperperm_lines = xppu_aperperm_lines + xppu_AperPerm_lines(n, size, base_add5, aperture_type)

    xppu_aperperm_lines = xppu_aperperm_lines + "\n\n    /*\n    * Register : APERPERM_167 @ 0XFF98129C\n\n    * This field defines the MASTER ID match criteria. Each entry in the IDL c\n    * orresponds to a bit in this field. 0=not match, 1=match.\n    *  PSU_LPD_XPPU_CFG_APERPERM_167_PERMISSION                    0x280\n\n    * 1=secure or non-secure transactions are allowed 0=only secure transactio\n    * na are allowed\n    *  PSU_LPD_XPPU_CFG_APERPERM_167_TRUSTZONE                     0x0\n\n    * SW must calculate and set up parity, if parity check is enabled by the C\n    * TRL register. 31: parity for bits 19:15 30: parity for bits 14:10 29: pa\n    * rity for bits 9:5 28: parity for bits 27, 4:0\n    *  PSU_LPD_XPPU_CFG_APERPERM_167_PARITY                        0x0\n\n    * Entry 167 of the Aperture Permission List, for 64K-byte aperture at\n    *  BASE_64KB + 0x00A70000\n    * (OFFSET, MASK, VALUE)      (0XFF98129C, 0xF80FFFFFU ,0x00000280U)\n    */\n	PSU_Mask_Write(LPD_XPPU_CFG_APERPERM_167_OFFSET,\n		0xF80FFFFFU, 0x00000280U);\n/*##################################################################### */\n\n"

    xppu_aperperm_lines = xppu_aperperm_lines + "\n\n    /*\n    * Register : APERPERM_204 @ 0XFF981330\n\n    * This field defines the MASTER ID match criteria. Each entry in the IDL c\n    * orresponds to a bit in this field. 0=not match, 1=match.\n    *  PSU_LPD_XPPU_CFG_APERPERM_204_PERMISSION                    0x2adf\n\n    * 1=secure or non-secure transactions are allowed 0=only secure transactio\n    * na are allowed\n    *  PSU_LPD_XPPU_CFG_APERPERM_204_TRUSTZONE                     0x0\n\n    * SW must calculate and set up parity, if parity check is enabled by the C\n    * TRL register. 31: parity for bits 19:15 30: parity for bits 14:10 29: pa\n    * rity for bits 9:5 28: parity for bits 27, 4:0\n    *  PSU_LPD_XPPU_CFG_APERPERM_204_PARITY                        0x0\n\n    * Entry 204 of the Aperture Permission List, for 64K-byte aperture at\n    *  BASE_64KB + 0x00CC0000\n    * (OFFSET, MASK, VALUE)      (0XFF981330, 0xF80FFFFFU ,0x00002ADFUU)\n    */\n	PSU_Mask_Write(LPD_XPPU_CFG_APERPERM_204_OFFSET,\n		0xF80FFFFFU, 0x00002ADFU);\n/*##################################################################### */\n\n"

    base_add6 = "0x00000000"
    for i in range(256,310):
        size = "32"
        size_hex = "20"
        aperture_type = "IPI buffer"
        base_add6 = hex(int(base_add6, 16) + int(size_hex, 16))
        xppu_aperperm_lines = xppu_aperperm_lines + xppu_AperPerm_lines(n, size, base_add6, aperture_type)

    base_add7 = "0x000007C0"
    for i in range(318,326):
        size = "32"
        size_hex = "20"
        aperture_type = "IPI buffer"
        base_add7 = hex(int(base_add7, 16) + int(size_hex, 16))
        xppu_aperperm_lines = xppu_aperperm_lines + xppu_AperPerm_lines(n, size, base_add7, aperture_type)

    base_add8 = "0x000009C0"
    for i in range(334,342):
        size = "32"
        size_hex = "20"
        aperture_type = "IPI buffer"
        base_add8 = hex(int(base_add8, 16) + int(size_hex, 16))
        xppu_aperperm_lines = xppu_aperperm_lines + xppu_AperPerm_lines(n, size, base_add8, aperture_type)

    base_add9 = "0x00000BC0"
    for i in range(350,358):
        size = "32"
        size_hex = "20"
        aperture_type = "IPI buffer"
        base_add9 = hex(int(base_add9, 16) + int(size_hex, 16))
        xppu_aperperm_lines = xppu_aperperm_lines + xppu_AperPerm_lines(n, size, base_add9, aperture_type)

    base_add10 = "0x00000DC0"
    for i in range(366,384):
        size = "32"
        size_hex = "20"
        aperture_type = "IPI buffer"
        base_add10 = hex(int(base_add10, 16) + int(size_hex, 16))
        xppu_aperperm_lines = xppu_aperperm_lines + xppu_AperPerm_lines(n, size, base_add10, aperture_type)

    xppu_aperperm_lines = xppu_aperperm_lines + "	/*\n	* Register : IEN @ 0XFF980018\n\n    * See Interuppt Status Register for details\n    *  PSU_LPD_XPPU_CFG_IEN_APER_PARITY                            0X1\n\n    * See Interuppt Status Register for details\n    *  PSU_LPD_XPPU_CFG_IEN_APER_TZ                                0X1\n\n    * See Interuppt Status Register for details\n    *  PSU_LPD_XPPU_CFG_IEN_APER_PERM                              0X1\n\n    * See Interuppt Status Register for details\n    *  PSU_LPD_XPPU_CFG_IEN_MID_PARITY                             0X1\n\n    * See Interuppt Status Register for details\n    *  PSU_LPD_XPPU_CFG_IEN_MID_RO                                 0X1\n\n    * See Interuppt Status Register for details\n    *  PSU_LPD_XPPU_CFG_IEN_MID_MISS                               0X1\n\n    * See Interuppt Status Register for details\n    *  PSU_LPD_XPPU_CFG_IEN_INV_APB                                0X1\n\n    * Interrupt Enable Register\n    * (OFFSET, MASK, VALUE)      (0XFF980018, 0x000000EFU ,0x000000EFU)\n    */\n	PSU_Mask_Write(LPD_XPPU_CFG_IEN_OFFSET, 0x000000EFU, 0x000000EFU);\n/*##################################################################### */\n\n    /*\n    * XPPU CONTROL\n    */\n    /*\n    * Register : err_ctrl @ 0XFF9CFFEC\n\n    * Whether an APB access to the hole region and to an unimplemented regis\n    * ter space causes PSLVERR\n    *  PSU_LPD_XPPU_SINK_ERR_CTRL_PSLVERR                          1\n\n    * Error control register\n    * (OFFSET, MASK, VALUE)      (0XFF9CFFEC, 0x00000001U ,0x00000001U)\n    */\n	PSU_Mask_Write(LPD_XPPU_SINK_ERR_CTRL_OFFSET,\n		0x00000001U, 0x00000001U);\n/*##################################################################### */\n\n    /*\n    * Register : CTRL @ 0XFF980000\n\n    * 0=Bypass XPPU (transparent) 1=Enable XPPU permission checking\n    *  PSU_LPD_XPPU_CFG_CTRL_ENABLE                                1\n\n    * XPPU Control Register\n    * (OFFSET, MASK, VALUE)      (0XFF980000, 0x00000001U ,0x00000001U)\n    */\n	PSU_Mask_Write(LPD_XPPU_CFG_CTRL_OFFSET, 0x00000001U, 0x00000001U);\n/*##################################################################### */"

    xppu_aperperm_data = xppu_aperperm_lines.splitlines()
    
    return xppu_aperperm_data

def xppu_aperpermram():

    xppu_aperpermram_lines = "unsigned long APER_OFFSET = 0xFF981000;\nint i = 0;\n\nfor (; i <= 400; i++) {\n		PSU_Mask_Write(APER_OFFSET, 0xF80FFFFFU, 0x08080000U);\n		APER_OFFSET = APER_OFFSET + 0x4;\n	}"

    xppu_aper_ram = xppu_aperpermram_lines.splitlines()
    return xppu_aper_ram    
