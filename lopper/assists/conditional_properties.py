#/*
# * Copyright (c) 2026 Advanced Micro Devices, Inc. All rights reserved.
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

"""Activate a conditional-property overlay without applying domain pruning."""

import getopt
import logging
import re

import lopper.log


lopper.log._init(__name__)


def is_compat(node, compat_string_to_test):
    if re.search(r"module,.*conditional_properties", compat_string_to_test):
        return conditional_properties
    return ""


def _names(value):
    """Return non-empty condition names from a DT property value."""
    if isinstance(value, str):
        value = [value]
    return [str(v).strip() for v in (value or []) if str(v).strip()]


def activate(sdt, condition=None, target_node=None, os_type_fallback=True):
    """Replace ``sdt.tree`` with a named, fully merged overlay tree.

    When *condition* is omitted, read ``lopper,activate`` from *target_node*.
    ``os,type`` is consulted as a compatibility fallback unless disabled.

    Returns the activated condition name, or ``None`` when no condition was
    requested or registered.  No nodes are pruned.
    """
    candidates = _names(condition)
    if not candidates and target_node is not None:
        candidates = _names(target_node.propval("lopper,activate"))
        if not candidates and os_type_fallback:
            candidates = _names(target_node.propval("os,type"))

    for name in candidates:
        overlay = sdt.tree.overlay_tree(name)
        if overlay is not None:
            lopper.log._info(
                f"conditional_properties: activating overlay tree '{name}'"
            )
            sdt.tree = overlay
            return name

        lopper.log._warning(
            f"conditional_properties: no overlay is registered for '{name}'"
        )

    return None


def usage():
    print(
        """
   Usage: conditional_properties [OPTION]

      -c, --condition NAME  activate NAME explicitly
      -t, --target NODE     read lopper,activate from NODE
      --no-os-fallback      do not fall back to NODE's os,type
      -v, --verbose         enable verbose logging

   This assist only resolves conditional properties/nodes. It does not prune
   the tree. Either --condition or a target carrying lopper,activate is needed.
    """
    )


def conditional_properties(tgt_node, sdt, options):
    args = options.get("args", [])
    verbose = options.get("verbose", 0)

    try:
        opts, positional = getopt.getopt(
            args,
            "c:t:vh",
            ["condition=", "target=", "verbose", "help", "no-os-fallback"],
        )
    except getopt.GetoptError as error:
        lopper.log._error(str(error))
        usage()
        return False

    condition = None
    target = None
    os_type_fallback = True
    for opt, value in opts:
        if opt in ("-c", "--condition"):
            condition = value
        elif opt in ("-t", "--target"):
            target = value
        elif opt in ("-v", "--verbose"):
            verbose += 1
        elif opt == "--no-os-fallback":
            os_type_fallback = False
        elif opt in ("-h", "--help"):
            usage()
            return True

    if positional:
        lopper.log._error(
            "conditional_properties: unexpected arguments: " + " ".join(positional)
        )
        return False

    if verbose:
        lopper.log._level(logging.INFO, __name__)
    if verbose > 1:
        lopper.log._level(logging.DEBUG, __name__)

    activation_node = None
    if not condition:
        if target:
            try:
                activation_node = sdt.tree[target]
            except Exception:
                lopper.log._error(
                    f"conditional_properties: target '{target}' cannot be found"
                )
                return False
        elif getattr(tgt_node, "abs_path", "/") != "/":
            activation_node = sdt.tree[tgt_node]

    if not condition and activation_node is None:
        lopper.log._error(
            "conditional_properties: specify --condition or --target"
        )
        return False

    requested = _names(condition)
    if not requested and activation_node is not None:
        requested = _names(activation_node.propval("lopper,activate"))
        if not requested and os_type_fallback:
            requested = _names(activation_node.propval("os,type"))

    if not requested:
        lopper.log._warning(
            "conditional_properties: target has no activation condition"
        )
        return True

    return bool(activate(sdt, condition, activation_node, os_type_fallback))
