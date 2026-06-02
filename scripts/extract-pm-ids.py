#!/usr/bin/env python3
# Copyright (c) 2026 Advanced Micro Devices, Inc. All Rights Reserved.
# Author: Bruce Ashfield <bruce.ashfield@amd.com>
# SPDX-License-Identifier: BSD-3-Clause
"""
Extract PM device IDs from a public PM-ID header (Xilinx, TI, ST, …)
and emit a starter `lopper/data/socs/<family>.yaml` in the unified
openamp,domain-v1,soc-facts schema (see sdt-from-linux design §6.1).

This handles the 80% mechanical part of standing up a new SoC. The
remaining 20% — choosing the `matches:` list, citing the source, and
later adding cluster templates / TCM map / IPI / etc. — is left to the
human as TODO markers in the generated YAML.

Usage:
    scripts/extract-pm-ids.py <header.h> --family <name> [-o <path>]

Examples:
    # Versal (from upstream Linux):
    scripts/extract-pm-ids.py \\
        /path/to/linux/include/dt-bindings/power/xlnx-versal-power.h \\
        --family versal -o lopper/data/socs/versal.yaml

    # ZynqMP:
    scripts/extract-pm-ids.py \\
        /path/to/linux/include/dt-bindings/power/xlnx-zynqmp-power.h \\
        --family zynqmp

Recognised name prefixes:
    PM_DEV_*    (Xilinx Versal, ZynqMP convention)
    K3_DEV_*    (TI K3 sysfw convention)
    STM32MP1_*  (STMicroelectronics — variable, may need regex tweaks)
"""

import argparse
import os
import re
import sys
from datetime import date

# Patterns for "#define NAME (0xVALUE...)" or "#define NAME 0xVALUE".
# We accept either parenthesised or bare hex, optional U/UL suffix.
_DEFINE_RE = re.compile(
    r'^\s*#\s*define\s+'
    r'(?P<name>(?:PM_DEV|K3_DEV|STM32MP1)_[A-Za-z0-9_]+)'
    r'\s+'
    r'\(?\s*0x(?P<hex>[0-9a-fA-F]+)U?L?L?\s*\)?'
    r'\s*(?:/\*.*?\*/|//.*)?\s*$'
)


def extract(header_path):
    """Return list of (name, int_value) tuples in source order."""
    entries = []
    seen = set()
    with open(header_path) as fh:
        for line in fh:
            m = _DEFINE_RE.match(line)
            if not m:
                continue
            name = m.group('name')
            if name in seen:
                continue
            seen.add(name)
            entries.append((name, int(m.group('hex'), 16)))
    return entries


def emit_yaml(entries, family, source_path):
    """Build the starter YAML as a single string."""
    today = date.today().isoformat()
    src_basename = os.path.basename(source_path)
    header = [
        f"# {family} SoC hardware description",
        f"#",
        f"# AUTO-GENERATED starter file by scripts/extract-pm-ids.py on {today}.",
        f"# Source: {src_basename}",
        f"#   <fill in upstream URL or repo + commit ref>",
        f"# License: <fill in — e.g. GPL-2.0 for Linux kernel headers>",
        f"#",
        f"# REVIEW BEFORE COMMIT:",
        f"#   - Fill in the `matches:` list from the SoC's .dtsi root compatible.",
        f"#   - Verify NOT sourced from any reference SDT or internal-only header.",
        f"#   - Add the public-source citation above (URL + commit ref).",
        f"#",
        f"# Schema: openamp,domain-v1,soc-facts (see sdt-from-linux design §6.1).",
        f"",
        f"domains:",
        f"  {family}:",
        f"    compatible: openamp,domain-v1,soc-facts",
        f"    soc_family: <vendor>,{family}    # TODO: verify against the SoC dtsi root compatible",
        f"    matches:                         # TODO: fill from the SoC dtsi root compatible",
        f"      # - <vendor>,{family}",
        f"      # - <vendor>,{family}-<board>",
        f"",
        f"    pm_devices:",
    ]
    body = [
        f"      0x{value:08x}: {name}"
        for name, value in entries
    ]
    return '\n'.join(header + body) + '\n'


def main():
    p = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('header', help='Path to the PM-ID dt-binding header (.h)')
    p.add_argument('--family', required=True,
                   help='Short SoC family name (versal, zynqmp, am62x, ...)')
    p.add_argument('-o', '--output',
                   help='Output YAML path (default: stdout)')
    args = p.parse_args()

    if not os.path.isfile(args.header):
        print(f"error: header not found: {args.header}", file=sys.stderr)
        sys.exit(1)

    entries = extract(args.header)
    if not entries:
        print(f"error: no PM_DEV_/K3_DEV_/STM32MP1_ defines found in {args.header}",
              file=sys.stderr)
        print(f"       (recognised prefixes: PM_DEV_, K3_DEV_, STM32MP1_)",
              file=sys.stderr)
        sys.exit(2)

    yaml_text = emit_yaml(entries, args.family, args.header)

    if args.output:
        os.makedirs(os.path.dirname(args.output) or '.', exist_ok=True)
        with open(args.output, 'w') as fh:
            fh.write(yaml_text)
        print(f"wrote {len(entries)} entries to {args.output}", file=sys.stderr)
        print(f"next steps:", file=sys.stderr)
        print(f"  1. edit {args.output} — fill in matches: and the source citation", file=sys.stderr)
        print(f"  2. run sdt_devices on a tree with a matching root compatible", file=sys.stderr)
        print(f"     to verify the pm_node tags appear in the output", file=sys.stderr)
    else:
        sys.stdout.write(yaml_text)
        print(f"# ({len(entries)} entries extracted)", file=sys.stderr)


if __name__ == '__main__':
    main()
