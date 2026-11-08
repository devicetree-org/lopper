# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause
"""Generate per-application xiltimer selections from an assembled domain tree.

Invoke independently of OpenAMP metadata generation::

    lopper -f --enhanced -O app assembled.dts unused.dts -- xlnx_rpu_timers \
        --domain=/domains/RPU0 --output=amd_timer_config.c

Explicit domain properties take precedence over the RPMsg relation fallback.
Without a selection nothing is written. Invalid explicit content is fatal.
Compile the generated C file directly into the application executable.
"""

import argparse
import os
import re
import tempfile

from lopper.assists import openamp_xlnx_timers as timers
from lopper.log import _error, _info, _warning


def is_compat(node, compat_string_to_test):
    if re.search(r"module,xlnx_rpu_timers$", compat_string_to_test):
        return generate_timer_config
    return ""


def generate_timer_config(root_node, sdt, options):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--domain", required=True,
                        help="absolute path of the domain in the assembled tree")
    parser.add_argument("--output", default=timers.TIMER_CONFIG_FILE,
                        help="C output file, relative to Lopper's output directory")
    parser.add_argument("--bsp-sleep-timer", type=lambda value: int(value, 0))
    parser.add_argument("--bsp-tick-timer", type=lambda value: int(value, 0))
    try:
        args = parser.parse_args(options.get("args", []))
    except SystemExit as exc:
        if exc.code == 0:
            return True
        _error("xlnx_rpu_timers: invalid arguments", also_exit=1)

    try:
        tree = sdt.tree
        try:
            domain = tree[args.domain]
        except KeyError as exc:
            raise timers.TimerError(f"domain '{args.domain}' was not found") from exc
        if domain not in timers.domain_nodes(tree):
            raise timers.TimerError(f"'{args.domain}' is not a domain with cpus")
        claims = timers.timer_claims(tree)
        result = timers.resolve_domain_timers(tree, domain, claims)
        for warning in result.warnings:
            _warning(f"xlnx_rpu_timers: {warning}")
        if result.errors:
            raise timers.TimerError("; ".join(result.errors))
        bsp_timers = {timers.SLEEP: args.bsp_sleep_timer,
                      timers.TICK: args.bsp_tick_timer}
        for warning in timers.bsp_timer_warnings(tree, result, bsp_timers, claims):
            _warning(f"xlnx_rpu_timers: {warning}")
        if not result.choices:
            _info(f"xlnx_rpu_timers: {domain.abs_path}: no timers selected; "
                  "the BSP defaults apply")
            return True
        output = args.output
        if not os.path.isabs(output):
            output = os.path.join(getattr(sdt, "outdir", None) or ".", output)
        # Publish only the complete file, including after a write failure.
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                             dir=os.path.dirname(output) or ".",
                                             delete=False) as handle:
                temporary = handle.name
                handle.write(timers.render_timer_config(result))
            os.replace(temporary, output)
            temporary = None
        finally:
            if temporary is not None:
                os.unlink(temporary)
        _info(f"xlnx_rpu_timers: wrote {output} for {domain.abs_path}")
        return True
    except (timers.TimerError, OSError) as exc:
        _error(f"xlnx_rpu_timers: {exc}", also_exit=1)
