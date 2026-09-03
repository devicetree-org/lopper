#/*
# * Copyright (C) 2026 Advanced Micro Devices, Inc.  All rights reserved.
# * Author:
# *     Pamarthi Harish Babu <HarishBabu.Pamarthi@amd.com>
#
# * SPDX-License-Identifier: BSD-3-Clause
# */
"""
Generate cpulist.yaml with per-CPU supported OS lists.

Usage:
    lopper -f -O <outdir> system-top.dts -- cpu_oslist_xlnx /path/to/.repo.yaml

"""

import os
import re
import sys
import yaml
import common_utils as utils

from baremetal_getsupported_comp_xlnx import get_os_bsp_keys
from lopper.log import _init, _level, _error

sys.path.append(os.path.dirname(__file__))

_init(__name__)

CPULIST_FILENAME = "cpulist.yaml"
NON_CORTEX_OS = frozenset(("cortexa78", "cortexr52"))
STANDALONE_ONLY = ["standalone"]
AIE_ENTRY = {"ip_name": "ai_engine", "supported_os": ["aie_runtime"]}


def is_compat(node, compat_string_to_test):
    if "module,cpu_oslist_xlnx" in compat_string_to_test:
        return xlnx_generate_cpu_oslist
    return ""


def load_repo_schema(repo_path):
    abs_path = utils.get_abs_path(repo_path)
    if not utils.is_file(abs_path):
        _error(f"cpu_oslist_xlnx: pass repo path as .repo.yaml", True)

    schema = utils.load_yaml(abs_path) or {}
    return schema


def _supported_os_for_cpu(cpu_ip_name, cortex_os_list):
    if (
        ("cortex" in cpu_ip_name or cpu_ip_name == "microblaze")
        and cpu_ip_name not in NON_CORTEX_OS
    ):
        return cortex_os_list
    return STANDALONE_ONLY


def build_cpulist(tree, path_schema):
    """Build cpulist dict from CPU and AI engine nodes in the SDT."""
    cortex_os_list = get_os_bsp_keys(path_schema)
    cpulist = {}

    for node in tree:
        if not node.label:
            continue

        device_type = node.propval("device_type")
        if device_type and device_type[0] == "cpu":
            cpu_ip_name = node.propval("xlnx,ip-name")[0]
            if not cpu_ip_name:
                continue
            cpulist[node.label] = {
                "ip_name": cpu_ip_name,
                "supported_os": list(_supported_os_for_cpu(cpu_ip_name, cortex_os_list)),
            }
            continue

        ip_name = node.propval("xlnx,ip-name")
        if ip_name and ip_name[0] == "ai_engine":
            cpulist[node.label] = dict(AIE_ENTRY)

    return cpulist


def xlnx_generate_cpu_oslist(tgt_node, sdt, options):
    """
    Assist entry point: generate cpulist.yaml from the full SDT.

    """
    _level(utils.log_setup(options), __name__)
    outdir = options.get("outdir") or sdt.outdir
    args = options.get("args") or []
    if not args:
        _error(
            "cpu_oslist_xlnx: pass path to .repo.yaml, e.g. "
            "lopper -f -O <outdir> system-top.dts -- "
            "cpu_oslist_xlnx /path/to/.repo.yaml",
            True,
        )

    cpulist = build_cpulist(sdt.tree, load_repo_schema(args[0]))
    with open(os.path.join(outdir, CPULIST_FILENAME), "w") as fd:
        fd.write(yaml.dump(cpulist, indent=4))

    return True
