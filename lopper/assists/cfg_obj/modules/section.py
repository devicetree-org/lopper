# /*
# * Copyright (c) 2022 - 2023 Advanced Micro Devices, Inc. All Rights Reserved.
# *
# * Author:
# *       Madhav Bhatt <madhav.bhatt@amd.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

class Section:
    __section_identifier = ""
    __section_file_name = None
    __section_parsing_cb = None
    __sdtinfo_obj = None

    def __init__(self, identifier, section_parsing_cb, sdtinfo_obj):
        self.__section_identifier = identifier
        self.__section_parsing_cb = section_parsing_cb
        self.__sdtinfo_obj = sdtinfo_obj

    def replace_section(self, out_lines):
        data_lines = self.__section_parsing_cb(self.__sdtinfo_obj)
        line_num = 0
        #print(out_lines)
        for out_line in out_lines:
            if self.__section_identifier in out_line:
                out_lines.pop(line_num)
                break
            line_num += 1
        if line_num == len(out_lines):
            return out_lines
        for data_line in data_lines:
            out_lines.insert(line_num, data_line)
            line_num += 1
        return out_lines
