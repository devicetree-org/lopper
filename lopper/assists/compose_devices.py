#/*
# * Copyright (c) 2026 Advanced Micro Devices, Inc. All Rights Reserved.
# *
# * Author:
# *       Bruce Ashfield <bruce.ashfield@amd.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

"""
Compose a device inventory YAML from a Linux device tree, optionally
merged with a Zephyr device tree for the co-processor side, plus
(future) per-board augmentation.

This is the Linux-DT-side counterpart to sdt_devices. It accepts a
pre-flattened Linux device tree as input (the lopper assist framework
expects the input sdt up front; the cpp + dtc flattening step is the
caller's responsibility) and runs the same DevicesCore extraction
sdt_devices uses. When given a Zephyr DT via --zephyr-dt (or implicit
via --board), it extracts that tree separately and merges its devices
into the Linux-side inventory — Linux is authoritative for shared
addresses, Zephyr-only entries (TCM/OCRAM/M-core CPU/M-side mailbox)
get added with a `source: zephyr` tag. M9 will then overlay the
per-board augment.yaml.

Usage:
    # Linux-only:
    lopper <flat-linux.dts> - -- compose_devices --board <name> -o out.yaml

    # Linux + Zephyr merge:
    lopper <flat-linux.dts> - -- compose_devices --board <name> \\
        --zephyr-dt <flat-zephyr.dts> -o out.yaml

    # Ad-hoc, no board config:
    lopper <flat-linux.dts> - -- compose_devices -o out.yaml

Options:
    -v, --verbose          Enable verbose output
    -b, --board NAME       Use lopper/data/boards/<NAME>/ as the board
                           reference. Optional — without it, behaves
                           like sdt_devices on the input tree.
    --zephyr-dt PATH       Pre-flattened Zephyr DT to extract and merge
                           into the Linux inventory. The Linux side is
                           authoritative for shared addresses; the
                           Zephyr side contributes co-processor CPU,
                           TCM/OCRAM, and the M-/R-side view of shared
                           peripherals (tagged source: zephyr).
    -o, --output PATH      Output YAML path
    --include-clocks       Include clock nodes
    --include-infrastructure CSV
                           Comma-separated infrastructure categories

Pipeline:
    1. Validate input root compatible against board's expected list.
    2. Run DevicesCore extraction on the Linux input (always).
    3. If --zephyr-dt given, run DevicesCore on it as a second source
       and merge into the Linux inventory (address-keyed, Linux wins).
    4. Linux's SoC identity and /aliases drive the domain metadata.
    5. (M9) Overlay augment.yaml from the board directory.
    6. Emit openamp,domain-v1,devices YAML.
"""

import getopt
import logging
import os
import re
import sys
import tempfile

from ruamel.yaml.scalarint import HexInt

from lopper import LopperSDT
from lopper.yaml import LopperYAML
import lopper
import lopper.log

from lopper.assists._devices_core import DeviceCategory, DevicesCore

# Augment entries that come in as plain ints from the YAML loader
# need to be HexInt-wrapped so the output emitter formats them in hex,
# matching what the extractor produces from real DT reg properties.
_HEX_FIELDS = ('start', 'size', 'addr', 'reg')

lopper.log._init(__name__)


# Repo root is two levels up from this file (lopper/assists/ → repo/).
# Boards data lives under <repo>/lopper/data/boards/.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
_BOARDS_ROOT = os.path.join(_REPO_ROOT, 'lopper', 'data', 'boards')


def is_compat(node, compat_string_to_test):
    """Assist-framework dispatch for compose_devices."""
    if re.search("compose-devices,compose-v1", compat_string_to_test):
        return compose_devices
    if re.search("module,compose_devices", compat_string_to_test):
        return compose_devices
    return ""


def usage():
    print("""
   Usage: compose_devices [OPTION]

      -v, --verbose          Enable verbose output
      -b, --board NAME       Use lopper/data/boards/<NAME>/ as the
                             board reference; reads augment.yaml from
                             there. Optional.
      -o, --output PATH      Output YAML path
      --include-clocks       Include clock nodes in the inventory
      --include-infrastructure CSV
                             Comma-separated infrastructure categories
                             to include (interrupt, smmu, power, slcr,
                             …; see sdt_devices --list-infrastructure)

   Compose a device inventory YAML from a Linux device tree, with the
   same mining enhancements sdt_devices uses (PM-ID decode, multi-range
   memory split, aliases pass-through, bootph preservation, SoC family
   tagging). When --board is supplied, additionally overlays the
   per-board augment.yaml (M9; currently a no-op stub).

   Example:
      lopper flat-linux.dts - -- compose_devices \\
          --board versal-vck190 -o /tmp/devices.yaml
    """)


def _load_board_source(board_name):
    """Load lopper/data/boards/<name>/source.yaml, return its dict."""
    path = os.path.join(_BOARDS_ROOT, board_name, 'source.yaml')
    if not os.path.isfile(path):
        raise RuntimeError(f"board {board_name!r}: source.yaml not found at {path}")
    try:
        from ruamel.yaml import YAML
        yaml = YAML(typ='safe')
        with open(path) as fh:
            return yaml.load(fh) or {}
    except Exception as e:
        raise RuntimeError(f"board {board_name!r}: failed to parse {path}: {e}")


def _sanity_check_board(sdt, board_data, board_name):
    """Confirm the input tree's root compatible matches what the board
    config expects. Warns on mismatch but does not abort — the user may
    legitimately be running an evolved DT against a stable board config.
    """
    expected = ((board_data.get('board') or {})
                .get('expected_root_compatible') or [])
    if not expected:
        return

    root_compat = []
    try:
        root = sdt.tree["/"]
        rc = root.propval("compatible") or []
        root_compat = [str(c).strip() for c in rc if c and str(c).strip()]
    except Exception:
        pass

    overlap = [c for c in expected if c in root_compat]
    if overlap:
        lopper.log._info(
            f"compose_devices: board {board_name!r} root compatible match: {overlap}")
    else:
        lopper.log._warning(
            f"compose_devices: board {board_name!r} expected one of "
            f"{expected}; input tree's root compatible is {root_compat} "
            f"— proceeding, but verify you're feeding the right file")


def _addr_key(entry):
    """Best-effort identity key for an inventory entry.

    For nodes named with a unit address (`uart@30860000`), use the
    address — same hardware peripheral seen from different sides
    (Linux vs Zephyr) appears at the same address. For nodes without
    an address (toplevel things like `pmu`, `versal_firmware`), use
    the bare name. Returns (kind, value) so different keying styles
    don't collide.
    """
    dev_name = entry.get('dev', '')
    m = re.search(r'@([0-9a-fA-F]+)$', dev_name)
    if m:
        try:
            return ('addr', int(m.group(1), 16))
        except ValueError:
            pass
    return ('name', dev_name)


def _load_secondary_sdt(dts_path):
    """Load a second pre-flattened .dts as a LopperSDT for extraction.

    The lopper assist framework hands us the primary sdt up front; for
    the Zephyr-side input we instantiate a second LopperSDT manually.
    Setup is configured for dry-run / permissive parsing since we only
    care about the parsed tree, not lop application or output emission.
    """
    if not os.path.isfile(dts_path):
        raise RuntimeError(f"zephyr DT not found: {dts_path}")
    sdt = LopperSDT(dts_path)
    sdt.dryrun = True
    sdt.permissive = True
    sdt.outdir = tempfile.mkdtemp(prefix='compose-devices-zephyr-')
    # setup signature accepts (sdt_file, input_files, include_paths, ...);
    # existing test infra passes empty strings rather than empty lists for
    # the variadic args. Match that convention.
    sdt.setup(dts_path, "", "", False, True, None)
    return sdt


def _merge_inventories(base_devs, extra_devs, source_tag):
    """Combine a secondary inventory into a base inventory.

    Used twice in compose_devices:
      1. base = Linux-side, extra = Zephyr-side, tag = "zephyr"
      2. base = (Linux+Zephyr), extra = board augment YAML,
         tag = "augment"

    Merge rules:
      - cpus: always appended; different clusters never alias
      - memory, sram, access: base entries kept as-is; extra entries
        added only when their identity key (see _addr_key) is not
        already present in the base. Added entries get a `source: tag`
        annotation so the merge is observable downstream.

    Order preserved: base first, then surviving extra entries.
    """
    merged = {k: list(base_devs.get(k) or []) for k in
              ('cpus', 'memory', 'sram', 'access')}

    # CPUs: append every extra cluster, tagged.
    for cpu in (extra_devs.get('cpus') or []):
        tagged = dict(cpu)
        tagged.setdefault('source', source_tag)
        merged['cpus'].append(tagged)

    # memory / sram / access: skip duplicates by address-or-name key.
    for prop_name in ('memory', 'sram', 'access'):
        base_keys = {_addr_key(e) for e in (base_devs.get(prop_name) or [])}
        added = skipped = 0
        for entry in (extra_devs.get(prop_name) or []):
            key = _addr_key(entry)
            if key in base_keys:
                skipped += 1
                lopper.log._debug(
                    f"compose_devices: {source_tag} {prop_name} entry "
                    f"{entry.get('dev','?')!r} collides with existing inventory "
                    f"entry; skipping (base wins)")
                continue
            tagged = dict(entry)
            tagged.setdefault('source', source_tag)
            merged[prop_name].append(tagged)
            base_keys.add(key)
            added += 1
        if added or skipped:
            lopper.log._info(
                f"compose_devices: {source_tag} {prop_name}: "
                f"merged {added}, skipped {skipped} duplicate")

    return merged


def _load_board_augment(board_name):
    """Load the per-board augment.yaml (M9 overlay input).

    Returns the augment block (the inventory-shaped dict with
    cpus/memory/sram/access lists) when one is present and has the
    openamp,domain-v1,board-augment compatible, or {} otherwise.

    Empty augment files (stub `domains: {}`) and missing files both
    return {} silently — this is the no-op augment case.
    """
    if not board_name:
        return {}
    path = os.path.join(_BOARDS_ROOT, board_name, 'augment.yaml')
    if not os.path.isfile(path):
        return {}
    try:
        from ruamel.yaml import YAML
        yaml = YAML(typ='safe')
        with open(path) as fh:
            data = yaml.load(fh) or {}
    except Exception as e:
        lopper.log._warning(f"compose_devices: failed to parse {path}: {e}")
        return {}

    domains = data.get('domains') or {}
    for block_name, block in domains.items():
        if not isinstance(block, dict):
            continue
        if block.get('compatible') != 'openamp,domain-v1,board-augment':
            continue
        # Only the inventory blocks bubble up — identity fields stay on
        # the augment side (Linux's identity is authoritative).
        inventory = {k: list(block.get(k) or [])
                     for k in ('cpus', 'memory', 'sram', 'access')}
        # Normalise: addresses/sizes the user wrote as 0xNNNN come back
        # as plain ints from the YAML loader. Re-wrap as HexInt so the
        # output emitter formats them in hex (matching extractor output).
        for entries in inventory.values():
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                for field in _HEX_FIELDS:
                    if field in entry and isinstance(entry[field], int) \
                            and not isinstance(entry[field], HexInt):
                        entry[field] = HexInt(entry[field])
        nonempty = {k: v for k, v in inventory.items() if v}
        if nonempty:
            lopper.log._info(
                f"compose_devices: loaded augment block '{block_name}' from "
                f"{path} ({sum(len(v) for v in nonempty.values())} entries "
                f"across {list(nonempty)})")
        return inventory
    return {}


def _apply_board_augment(devices, board_name, *, enabled=True):
    """Merge the per-board augment.yaml into the inventory dict.

    Reads `lopper/data/boards/<name>/augment.yaml`, finds the
    openamp,domain-v1,board-augment block, and merges its inventory
    lists into `devices` via the same address-keyed dedup logic
    used for the Zephyr-side merge. Entries from the augment file
    carry a `source: augment` tag.

    No-op when:
      - enabled is False
      - board_name is empty
      - no augment.yaml exists for this board
      - the file's augment block is empty (the stub `domains: {}` case)

    Returns the (possibly-augmented) devices dict.
    """
    if not enabled or not board_name:
        return devices
    augment = _load_board_augment(board_name)
    if not any(augment.values()):
        return devices
    return _merge_inventories(devices, augment, source_tag='augment')


def compose_devices(tgt_node, sdt, options):
    """Entry point. Same shape as sdt_devices for assist-framework parity."""
    try:
        verbose = options['verbose']
    except KeyError:
        verbose = 0
    try:
        args = options['args']
    except KeyError:
        args = []

    try:
        opts, _ = getopt.getopt(
            args,
            "hvb:o:",
            ["help", "verbose", "board=", "output=", "zephyr-dt=",
             "no-augment",
             "include-clocks", "include-infrastructure="])
    except getopt.GetoptError as e:
        lopper.log._error(f"compose_devices: invalid option: {e}")
        usage()
        return False

    board_name = None
    output_file = None
    zephyr_dt_path = None
    apply_augment = True
    include_clocks = False
    include_infrastructure = []

    for o, a in opts:
        if o in ('-h', '--help'):
            usage()
            return True
        elif o in ('-v', '--verbose'):
            verbose += 1
        elif o in ('-b', '--board'):
            board_name = a
        elif o in ('-o', '--output'):
            output_file = a
        elif o == '--zephyr-dt':
            zephyr_dt_path = a
        elif o == '--no-augment':
            apply_augment = False
        elif o == '--include-clocks':
            include_clocks = True
        elif o == '--include-infrastructure':
            include_infrastructure = [c.strip().lower() for c in a.split(',')]

    if verbose > 1:
        lopper.log._level(logging.DEBUG, __name__)
    elif verbose > 0:
        lopper.log._level(logging.INFO, __name__)

    board_data = {}
    if board_name:
        try:
            board_data = _load_board_source(board_name)
        except RuntimeError as e:
            lopper.log._error(str(e))
            return False
        _sanity_check_board(sdt, board_data, board_name)

    # Run extraction on the Linux side. Domain name reflects intent:
    # this came from the Linux DT compose path, not from an SDT.
    domain_name = (board_data.get('board') or {}).get('name', 'composed_devices')
    lopper.log._info(
        f"compose_devices: extracting Linux-side inventory as domain '{domain_name}'")

    linux_gen = DevicesCore(sdt,
                            include_clocks=include_clocks,
                            include_infrastructure=include_infrastructure)
    linux_devices = linux_gen.discover_all()
    identity = linux_gen._detect_soc_identity()
    aliases = linux_gen.discover_aliases()

    # Optional Zephyr-side merge.
    merged_devices = linux_devices
    if zephyr_dt_path:
        lopper.log._info(
            f"compose_devices: extracting Zephyr-side inventory from {zephyr_dt_path}")
        try:
            zsdt = _load_secondary_sdt(zephyr_dt_path)
        except RuntimeError as e:
            lopper.log._error(str(e))
            return False
        zephyr_gen = DevicesCore(zsdt,
                                 include_clocks=include_clocks,
                                 include_infrastructure=include_infrastructure)
        zephyr_devices = zephyr_gen.discover_all()
        merged_devices = _merge_inventories(linux_devices, zephyr_devices,
                                            source_tag='zephyr')

    # Apply the board augment overlay (M9). No-op when the board has
    # no augment.yaml or its block is empty; --no-augment disables.
    merged_devices = _apply_board_augment(merged_devices, board_name,
                                          enabled=apply_augment)

    # Build the unified domain tree using the (possibly merged + augmented)
    # inventory. Linux identity + aliases are authoritative; the Zephyr and
    # augment sides' identity is intentionally discarded since the
    # user-facing SDT name is the Linux-side board.
    tree = linux_gen.build_domain_tree(domain_name, merged_devices,
                                       identity=identity, aliases=aliases)

    # Output file resolution mirrors sdt_devices.
    if not output_file:
        if hasattr(sdt, 'output_file') and sdt.output_file:
            output_file = sdt.output_file
        else:
            lopper.log._error("compose_devices: no output file specified (use -o)")
            usage()
            return False
    if not output_file.endswith('.yaml'):
        base, _ = os.path.splitext(output_file)
        output_file = base + '.yaml'

    out_dir = os.path.dirname(output_file)
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir, exist_ok=True)

    lopper.log._info(f"compose_devices: writing inventory to {output_file}")
    try:
        config = getattr(sdt, 'config', {})
        LopperYAML(None, tree, config=config).to_yaml(output_file)
    except Exception as e:
        lopper.log._error(f"compose_devices: failed to write {output_file}: {e}")
        return False

    lopper.log._info(f"compose_devices: success ({output_file})")
    return True
