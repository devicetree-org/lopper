#/*
# * Copyright (c) 2020 Xilinx Inc. All rights reserved.
# *
# * Author:
# *       Bruce Ashfield <bruce.ashfield@xilinx.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

import json
import os
import re
import sys

from lopper.tree import LopperNode, LopperProp, LopperTree
from lopper.yaml import LopperYAML

from .yaml_to_dts_expansion import subsystem_expand as _yaml_subsystem_expand

sys.path.append(os.path.dirname(__file__))


def is_compat(node, compat_string_to_test):
    """Identify whether this assist handles the provided compatibility string."""
    if re.search(r"module,subsystem", compat_string_to_test):
        return subsystem
    return ""


def subsystem(tgt_node, sdt, options):
    """Entry point for subsystem assist processing."""
    verbose = options.get("verbose", 0)
    args = options.get("args", [])

    if verbose:
        print(f"[INFO]: cb: subsystem( {tgt_node}, {sdt}, {verbose}, {args} )")

    if "generate" in args or "--generate" in args:
        subsystem_generate(tgt_node, sdt, verbose)
    else:
        _yaml_subsystem_expand(tgt_node, sdt, verbose)

    return True


def subsystem_generate(tgt_node, sdt, verbose=0):
    """Generate a template subsystem description within ``/domains``."""
    if verbose:
        print(f"[INFO]: cb: subsystem_generate( {tgt_node}, {sdt} )")

    tree = sdt.tree
    domain_tree = LopperTree()

    try:
        domain_node = tree["/domains"]
    except Exception:
        domain_node = LopperNode(-1, "/domains")

    domain_tree.__dbg__ = 4
    domain_tree = domain_tree + domain_node

    subsystem_node = LopperNode(-1)
    subsystem_node.name = "subsystem1"

    domain_node + subsystem_node

    cpu_prop = None
    for node in sdt.tree:
        try:
            compatibility = node["compatible"]
        except Exception:
            compatibility = None

        if compatibility:
            cpu_compat = re.findall(
                r"(?=(" + "|".join(compatibility.value) + r"))", "cpus,cluster"
            )
            if cpu_compat:
                if not cpu_prop:
                    cpu_prop = LopperProp(
                        "cpus",
                        -1,
                        subsystem_node,
                        [
                            json.dumps(
                                {
                                    "cluster": node.label,
                                    "cpu_mask": 0x3,
                                    "mode": {"secure": True},
                                }
                            )
                        ],
                    )
                    cpu_prop.pclass = "json"
                    subsystem_node = subsystem_node + cpu_prop
                else:
                    cpu_prop.value.append(
                        json.dumps(
                            {
                                "cluster": node.label,
                                "cpu_mask": 0x3,
                                "mode": {"secure": True},
                            }
                        )
                    )

    if verbose > 3:
        tree.__dbg__ = 4

    tree = tree + domain_node

    if verbose > 2:
        print("[DBG++]: dumping yaml generated default subystem")
        yaml = LopperYAML(None, domain_tree)
        yaml.to_yaml()

    return True


# Backward compatibility: expose expansion entry point from the new module.
subsystem_expand = _yaml_subsystem_expand
