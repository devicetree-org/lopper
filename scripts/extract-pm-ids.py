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

Recognised name prefixes (built in):
    PM_DEV_*    (Xilinx Versal, ZynqMP convention)
    K3_DEV_*    (TI K3 sysfw convention)
    STM32MP1_*  (STMicroelectronics — variable, may need regex tweaks)

A vendor whose header uses a different convention can be handled
without editing this script via two escape hatches that append to
the built-in set:

    # literal prefix(es) — matches "<PREFIX>_NAME"
    scripts/extract-pm-ids.py <header.h> --family <name> \\
        --prefix MTK_PD --prefix MTK_POWER

    # raw regex(es) — for what a fixed prefix can't express, e.g. a
    # number-variable family or a suffix-discriminated convention
    scripts/extract-pm-ids.py <header.h> --family <name> \\
        --prefix-regex 'STM32MP\\d+_[A-Za-z0-9_]+' \\
        --prefix-regex '[A-Z0-9_]+_POWER_DOMAIN'

A --prefix fragment is regex-escaped and gets `_NAME` appended; a
--prefix-regex fragment is spliced in verbatim and must match the
whole symbol name itself. (If a header's *value* format differs —
not hex, say — _build_define_re still needs a tweak; both flags only
cover the symbol-name convention, not the value syntax.)
"""

import argparse
import os
import re
import sys
from datetime import date

# Vendor PM-ID symbol prefixes recognised out of the box. A new
# vendor whose header uses a different convention can be handled
# without editing this file via the --prefix escape hatch (see main).
_DEFAULT_PREFIXES = ('PM_DEV', 'K3_DEV', 'STM32MP1')


def _build_define_re(prefixes, regexes=()):
    """Compile the `#define <SYMBOL> 0xVALUE` matcher.

    `prefixes` are literal symbol prefixes — each is regex-escaped and
    matches `<prefix>_NAME` (so a literal prefix is matched even if it
    contains regex metacharacters). `regexes` are raw regex fragments
    spliced into the same name alternation un-escaped, for conventions
    a fixed prefix can't express (e.g. `STM32MP\\d+`, `RK\\d+_PD`, or a
    suffix-discriminated `[A-Z0-9_]+_POWER_DOMAIN`).

    A prefix `P` contributes the alternative `P_[A-Za-z0-9_]+`; a regex
    fragment is used verbatim, so it must match the *whole* symbol name
    itself (including any trailing name characters). The value side
    (parenthesised/hex/U-L-suffixed, optional trailing comment) is
    shared by both.
    """
    alts = [re.escape(p) + r'_[A-Za-z0-9_]+' for p in prefixes]
    alts += list(regexes)
    alt = '|'.join(alts)
    return re.compile(
        r'^\s*#\s*define\s+'
        r'(?P<name>(?:' + alt + r'))'
        r'\s+'
        r'\(?\s*0x(?P<hex>[0-9a-fA-F]+)U?L?L?\s*\)?'
        r'\s*(?:/\*.*?\*/|//.*)?\s*$'
    )


def extract(header_path, prefixes=_DEFAULT_PREFIXES, regexes=()):
    """Return list of (name, int_value) tuples in source order.

    `prefixes` is the set of recognised literal symbol prefixes
    (defaults to the built-in vendor set); `regexes` is an optional
    set of raw name-matching regex fragments for conventions a fixed
    prefix can't express.
    """
    define_re = _build_define_re(prefixes, regexes)
    entries = []
    seen = set()
    with open(header_path) as fh:
        for line in fh:
            m = define_re.match(line)
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
    p.add_argument('--prefix', action='append', default=[], metavar='PREFIX',
                   help='Additional literal PM-ID symbol prefix to '
                        'recognise, on top of the built-in set (PM_DEV, '
                        'K3_DEV, STM32MP1). The parser matches '
                        '"<PREFIX>_NAME". Repeatable. Escape hatch for a '
                        'vendor whose header uses a prefix this script '
                        'does not ship with.')
    p.add_argument('--prefix-regex', action='append', default=[],
                   metavar='REGEX',
                   help='Raw regex fragment matching a whole symbol name, '
                        r'for conventions a fixed prefix cannot express '
                        r'(e.g. "STM32MP\\d+_[A-Za-z0-9_]+" for a '
                        'number-variable family). Repeatable. Spliced '
                        'into the name alternation un-escaped.')
    args = p.parse_args()

    if not os.path.isfile(args.header):
        print(f"error: header not found: {args.header}", file=sys.stderr)
        sys.exit(1)

    prefixes = tuple(_DEFAULT_PREFIXES) + tuple(args.prefix)
    regexes = tuple(args.prefix_regex)
    entries = extract(args.header, prefixes, regexes)
    if not entries:
        pretty = ', '.join(f'{p}_' for p in prefixes)
        print(f"error: no matching defines found in {args.header}",
              file=sys.stderr)
        print(f"       (recognised prefixes: {pretty}"
              + (f"; regexes: {', '.join(regexes)}" if regexes else "")
              + ")", file=sys.stderr)
        print(f"       add another with --prefix <PREFIX> (literal) or "
              f"--prefix-regex <REGEX> if this header uses a different "
              f"convention", file=sys.stderr)
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
