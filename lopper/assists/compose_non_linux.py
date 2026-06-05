#/*
# * Copyright (c) 2026 Advanced Micro Devices, Inc. All Rights Reserved.
# *
# * Author:
# *       Bruce Ashfield <bruce.ashfield@amd.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

"""
Extract non-Linux device-tree content into a rich properties YAML.

This assist is the distillation stage of the sdt-from-linux pipeline
for everything that is NOT already in the Linux device tree:
co-processor side content from a Zephyr device tree, integration
decisions from a per-board augment YAML, and any hand-curated
extra inputs.

The Linux DT is assumed to be the base of the eventual SDT, so its
devices come along verbatim when assemble_sdt loads it. This assist
captures only the things that need to be ADDED on top: additional
CPU clusters (R5, M4), TCM/OCRAM regions, the co-processor side of
the IPC fabric, and any board-level reserved-memory / peripherals
the Linux DT doesn't carry.

Output is a YAML capturing each kept node's full DT property set
(compatible, reg, interrupts, clocks, vendor properties, …) with
phandle references encoded as `"&label"` strings (or
`["&label", cell, ...]` lists when cells follow). This matches the
existing Lopper YAML convention recognised by
`LopperTree.label_to_phandle()`, so assemble_sdt can resolve them
against the merged Linux-DT-base tree at assembly time.

Usage:
    lopper <flat-zephyr.dts> - -- compose_non_linux \\
        --linux-dt <flat-linux.dts> \\
        [--augment <augment.yaml>] \\
        [--board <name>] \\
        -o non-linux.yaml

Options:
    -v, --verbose          Enable verbose output
    --linux-dt PATH        Pre-flattened Linux DT, used to figure out
                           which addresses are already covered (those
                           are dropped — Linux DT supplies them).
                           Required.
    --augment PATH         Optional per-board augment.yaml to merge
                           in (cpus / memory / sram / access lists).
                           If --board is given but --augment isn't,
                           the assist looks up
                           lopper/data/boards/<board>/augment.yaml
                           automatically.
    -b, --board NAME       Optional board name for board-config
                           lookup (only used to find augment.yaml
                           when --augment is omitted).
    -o, --output PATH      Output YAML path (required).
"""

import getopt
import logging
import os
import re
import sys
import tempfile

from ruamel.yaml.scalarint import HexInt

from lopper import LopperSDT
import lopper
import lopper.log
import lopper.base


lopper.log._init(__name__)


# Repo root and per-board data root, for augment lookup.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
_BOARDS_ROOT = os.path.join(_REPO_ROOT, 'lopper', 'data', 'boards')


def is_compat(node, compat_string_to_test):
    """Assist-framework dispatch."""
    if re.search("compose-non-linux,compose-v1", compat_string_to_test):
        return compose_non_linux
    if re.search("module,compose_non_linux", compat_string_to_test):
        return compose_non_linux
    return ""


def usage():
    print("""
   Usage: compose_non_linux --linux-dt <linux.dts> -o <out.yaml> [options]

      -v, --verbose          Enable verbose output
      --linux-dt PATH        Pre-flattened Linux DT (required) — used
                             to skip addresses Linux already covers.
      --augment PATH         Optional augment.yaml to merge.
      -b, --board NAME       Optional board name; auto-locates
                             augment.yaml under lopper/data/boards/.
      -o, --output PATH      Output YAML path (required).

   Walks the Zephyr device tree (the main lopper -f input), captures
   every node not present in the Linux DT at the same address, emits
   rich per-device properties (with phandle references encoded as
   dicts), and merges the per-board augment overlay. The resulting
   non-linux.yaml is consumed by assemble_sdt to produce the SDT
   alongside the Linux DT base.
    """)


# --- helpers -----------------------------------------------------------

def _load_linux_sdt(dts_path):
    """Load the Linux DT as a secondary LopperSDT (for address dedup)."""
    if not os.path.isfile(dts_path):
        raise RuntimeError(f"linux DT not found: {dts_path}")
    sdt = LopperSDT(dts_path)
    sdt.dryrun = True
    sdt.permissive = True
    sdt.outdir = tempfile.mkdtemp(prefix='compose-non-linux-linux-')
    sdt.setup(dts_path, "", "", False, True, None)
    return sdt


def _collect_addresses(sdt):
    """Return the set of @addr unit-addresses present in `sdt`.

    Used to dedup against the Linux side — Zephyr nodes at the same
    address as a Linux node are skipped (Linux DT supplies them via
    the SDT base).
    """
    addrs = set()
    for n in sdt.tree:
        m = re.search(r'@([0-9a-fA-F]+)$', n.name)
        if m:
            try:
                addrs.add(int(m.group(1), 16))
            except ValueError:
                pass
    return addrs


def _node_addr(node_name):
    """Parse the unit-address from a node name; None if none."""
    m = re.search(r'@([0-9a-fA-F]+)$', node_name or '')
    if not m:
        return None
    try:
        return int(m.group(1), 16)
    except ValueError:
        return None


def _resolve_phandle_label(sdt, phandle_int):
    """Look up phandle int → node → label string.

    Returns the label (without `&` prefix), or None if not resolvable.
    """
    try:
        node = sdt.tree.pnode(phandle_int)
    except Exception:
        return None
    if not node:
        return None
    return node.label or None


def _phandle_props():
    """Cache the Lopper phandle-property registry.

    Maps property name → format string (e.g. 'phandle:#clock-cells').
    """
    raw = lopper.base.lopper_base.phandle_possible_properties()
    out = {}
    for k, v in raw.items():
        if k in ('DEFAULT', '__phandle_exclude__'):
            continue
        if isinstance(v, list) and v:
            out[k] = v[0]
        else:
            out[k] = str(v)
    return out


_PHANDLE_PROPS = None


def _is_phandle_prop(prop_name):
    """Quick check: does this property carry phandle references?"""
    global _PHANDLE_PROPS
    if _PHANDLE_PROPS is None:
        _PHANDLE_PROPS = _phandle_props()
    return prop_name in _PHANDLE_PROPS


def _cell_count_after_phandle(prop_name, target_node):
    """Given a phandle property and its target, return the number of
    cells that follow the phandle in the parent's value.

    e.g. for clocks pointing at a controller with #clock-cells = 1,
    return 1. Falls back to 1 if we can't resolve the cell-count
    property on the target.
    """
    if _PHANDLE_PROPS is None:
        _phandle_props()
    fmt = (_PHANDLE_PROPS or {}).get(prop_name, '')
    # Format like 'phandle:#clock-cells' or 'phandle' (no extra cells)
    m = re.search(r'#([a-zA-Z0-9-]+)', fmt)
    if not m:
        # No cell-count specified; default to 0 (the phandle alone)
        return 0
    cell_prop = '#' + m.group(1)
    if target_node is None:
        return 1
    val = target_node.propval(cell_prop)
    if isinstance(val, list) and val and isinstance(val[0], int):
        return val[0]
    return 1


def _encode_phandle_property(sdt, prop_name, raw_value):
    """Encode a phandle-bearing property in Lopper's canonical YAML form.

    Produces a flat list mixing `"&label"` strings (one per phandle
    entry) with HexInt cells, matching what
    `LopperTree.label_to_phandle()` accepts on the consumer side.

    A property with a single phandle and no trailing cells collapses to
    a bare `"&label"` string.

    Falls back to the raw value if the input doesn't look like a packed
    phandle/cells array.
    """
    if not isinstance(raw_value, list):
        return raw_value
    if not all(isinstance(v, int) for v in raw_value):
        return raw_value

    encoded = []
    i = 0
    while i < len(raw_value):
        phandle_int = raw_value[i]
        i += 1
        target = None
        try:
            target = sdt.tree.pnode(phandle_int)
        except Exception:
            target = None
        n_cells = _cell_count_after_phandle(prop_name, target)
        cells = raw_value[i:i + n_cells]
        i += n_cells
        label = _resolve_phandle_label(sdt, phandle_int)
        if label is None:
            # Unresolvable — preserve the raw int so nothing is lost,
            # plus the cells. label_to_phandle() will pass non-string
            # values through unchanged.
            encoded.append(HexInt(phandle_int))
        else:
            encoded.append(f'&{label}')
        encoded.extend(HexInt(c) for c in cells)

    # Collapse the common single-phandle-no-cells case to a scalar.
    if len(encoded) == 1 and isinstance(encoded[0], str):
        return encoded[0]
    return encoded


def _encode_property(sdt, prop_name, raw_value):
    """Encode one property value for the YAML output.

    Plain int / int-array / string-array values pass through with
    HexInt wrapping for ints. Phandle-bearing properties get the
    dict-encoded form.
    """
    # Boolean / present-empty properties: lopper returns [''] for
    # both absent (we won't call this then) and present-boolean.
    if raw_value == ['']:
        return True

    if _is_phandle_prop(prop_name):
        encoded = _encode_phandle_property(sdt, prop_name, raw_value)
        if encoded is not raw_value:
            return encoded

    # String-typed properties: pass through.
    if isinstance(raw_value, list) and raw_value and \
            all(isinstance(v, str) for v in raw_value):
        # Collapse one-element string lists to a scalar string.
        return raw_value[0] if len(raw_value) == 1 else raw_value

    # Integer-typed properties: wrap each int as HexInt for hex format
    # in the YAML output.
    if isinstance(raw_value, list):
        out = []
        for v in raw_value:
            if isinstance(v, int):
                out.append(HexInt(v))
            else:
                out.append(v)
        # Collapse single-element lists for readability.
        return out[0] if len(out) == 1 else out

    return raw_value


def _extract_node_properties(sdt, node):
    """Return a dict of every property of `node`, encoded for YAML."""
    out = {}
    props = getattr(node, '__props__', {}) or {}
    for prop_name, prop in props.items():
        # Skip lopper-internal properties.
        if prop_name in ('phandle', '__symbols__'):
            continue
        raw = prop.value
        out[prop_name] = _encode_property(sdt, prop_name, raw)
    return out


def _extract_node(sdt, node, source_tag='zephyr'):
    """Build the YAML entry for one node."""
    entry = {
        'source': source_tag,
        'properties': _extract_node_properties(sdt, node),
    }
    if node.label:
        entry['label'] = node.label
    return entry


# --- main extraction --------------------------------------------------

def _extract_non_linux(zephyr_sdt, linux_sdt):
    """Walk Zephyr DT, collect everything not already at a Linux DT
    address.

    Returns a dict structured as:
        {
          'clusters': { node_name: entry, ... },     # cpus,cluster nodes
          'memory':   { node_name: entry, ... },     # memory@<addr>
          'devices':  { node_name: entry, ... },     # peripherals
        }
    """
    linux_addrs = _collect_addresses(linux_sdt)

    out = {'clusters': {}, 'memory': {}, 'devices': {}}

    for node in zephyr_sdt.tree:
        name = node.name
        if not name or name in ('/', 'chosen', 'aliases', '__symbols__',
                                '__fixups__', '__local_fixups__'):
            continue

        addr = _node_addr(name)
        # Skip nodes whose unit-address is already present on the Linux side.
        if addr is not None and addr in linux_addrs:
            continue

        # Classify the node.
        is_cpus_cluster = False
        for child in (node.child_nodes.values() if node.child_nodes else []):
            if child.propval("device_type") == ['cpu']:
                is_cpus_cluster = True
                break

        if is_cpus_cluster:
            out['clusters'][name] = _extract_node(zephyr_sdt, node,
                                                  source_tag='zephyr')
            # Walk children (cpu@N nodes) and store as nested
            children = {}
            for child in node.child_nodes.values():
                if child.propval("device_type") == ['cpu']:
                    children[child.name] = _extract_node(zephyr_sdt, child,
                                                         source_tag='zephyr')
            if children:
                out['clusters'][name]['cpus'] = children
            continue

        if name.startswith('memory@') or 'tcm' in name.lower() \
                or 'ocram' in name.lower() or 'sram' in name.lower():
            entry = _extract_node(zephyr_sdt, node, source_tag='zephyr')
            ac, sc = _parent_cells(node)
            entry['properties'] = _normalise_memory_properties(
                entry['properties'], ac, sc)
            out['memory'][name] = entry
            continue

        # Peripheral with an @addr that Linux doesn't have.
        if addr is not None:
            out['devices'][name] = _extract_node(zephyr_sdt, node,
                                                 source_tag='zephyr')

    return out


def _parent_cells(node):
    """Return (#address-cells, #size-cells) of node's parent.

    DT spec default is (2, 1) when not specified on the parent.
    """
    ac, sc = 2, 1
    parent = getattr(node, 'parent', None)
    if parent is not None:
        v = parent.propval('#address-cells')
        if isinstance(v, list) and v and isinstance(v[0], int):
            ac = v[0]
        v = parent.propval('#size-cells')
        if isinstance(v, list) and v and isinstance(v[0], int):
            sc = v[0]
    return ac, sc


def _cells_to_int(cells):
    """Combine a list of 32-bit cells into a single int (big-endian)."""
    v = 0
    for c in cells:
        v = (v << 32) | (int(c) & 0xffffffff)
    return v


def _normalise_memory_properties(props, ac, sc):
    """Translate `reg: [base..., size...]` into `start`/`size` HexInts.

    Aligns memory entries with the SDT spec / `domains.yaml` convention
    where memory is described as a `{start, size}` pair. Leaves multi-
    entry reg or unexpected shapes untouched.
    """
    reg = props.get('reg')
    if reg is None:
        return props
    if isinstance(reg, int):
        reg = [reg]
    if not isinstance(reg, list) or not all(isinstance(v, int) for v in reg):
        return props
    pair = ac + sc
    if pair == 0 or len(reg) != pair:
        return props
    start = _cells_to_int(reg[:ac])
    size = _cells_to_int(reg[ac:ac + sc])
    out = {}
    for k, v in props.items():
        if k == 'reg':
            out['start'] = HexInt(start)
            out['size'] = HexInt(size)
        else:
            out[k] = v
    return out


_AUGMENT_HEX_FIELDS = ('start', 'size', 'addr', 'reg')


def _normalise_augment_entry(entry):
    """User-written ints in augment.yaml come in as plain Python ints
    after the YAML loader. Wrap the conventional address/size fields
    as HexInt so the output formats them in hex — matching how
    extractor-derived values look in the same YAML.
    """
    out = {}
    for k, v in entry.items():
        if k == 'dev':
            continue
        if k in _AUGMENT_HEX_FIELDS and isinstance(v, int) \
                and not isinstance(v, HexInt):
            out[k] = HexInt(v)
        else:
            out[k] = v
    return out


def _merge_augment(non_linux, augment_path):
    """Merge augment YAML's openamp,domain-v1,board-augment block in.

    Augment entries are simpler than Zephyr-extracted ones (they
    typically only declare reserved-memory carve-outs and the like)
    so we keep their YAML form as-is, just tagged source: augment.
    """
    if not augment_path or not os.path.isfile(augment_path):
        return non_linux
    from ruamel.yaml import YAML
    yaml = YAML(typ='safe')
    try:
        with open(augment_path) as fh:
            data = yaml.load(fh) or {}
    except Exception as e:
        lopper.log._warning(f"compose_non_linux: failed to read {augment_path}: {e}")
        return non_linux

    domains = data.get('domains') or {}
    for block_name, block in domains.items():
        if not isinstance(block, dict):
            continue
        if block.get('compatible') != 'openamp,domain-v1,board-augment':
            continue
        # Each inventory list becomes augment-tagged entries in the
        # corresponding non-linux bucket.
        for entry in (block.get('cpus') or []):
            name = entry.get('dev', f'augment-cpus-{len(non_linux["clusters"])}')
            non_linux['clusters'][name] = {
                'source': 'augment',
                'properties': _normalise_augment_entry(entry),
            }
        for entry in (block.get('memory') or []) + (block.get('sram') or []):
            name = entry.get('dev', f'augment-mem-{len(non_linux["memory"])}')
            non_linux['memory'][name] = {
                'source': 'augment',
                'properties': _normalise_augment_entry(entry),
            }
        for entry in (block.get('access') or []):
            name = entry.get('dev', f'augment-dev-{len(non_linux["devices"])}')
            non_linux['devices'][name] = {
                'source': 'augment',
                'properties': _normalise_augment_entry(entry),
            }
    return non_linux


def _resolve_augment_path(args_augment, board_name):
    """If --augment was given, use it. Else if --board, look it up."""
    if args_augment:
        return args_augment
    if board_name:
        return os.path.join(_BOARDS_ROOT, board_name, 'augment.yaml')
    return None


# --- entry point ------------------------------------------------------

def compose_non_linux(tgt_node, sdt, options):
    """Assist entry point. `sdt` is the Zephyr DT."""
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
            args, "hvb:o:",
            ["help", "verbose", "linux-dt=", "augment=",
             "board=", "output="])
    except getopt.GetoptError as e:
        lopper.log._error(f"compose_non_linux: invalid option: {e}")
        usage()
        return False

    linux_dt_path = None
    augment_path = None
    board_name = None
    output_file = None

    for o, a in opts:
        if o in ('-h', '--help'):
            usage()
            return True
        elif o in ('-v', '--verbose'):
            verbose += 1
        elif o == '--linux-dt':
            linux_dt_path = a
        elif o == '--augment':
            augment_path = a
        elif o in ('-b', '--board'):
            board_name = a
        elif o in ('-o', '--output'):
            output_file = a

    if verbose > 1:
        lopper.log._level(logging.DEBUG, __name__)
    elif verbose > 0:
        lopper.log._level(logging.INFO, __name__)

    if not linux_dt_path:
        lopper.log._error("compose_non_linux: --linux-dt is required")
        usage()
        return False
    if not output_file:
        lopper.log._error("compose_non_linux: --output is required")
        usage()
        return False

    # Resolve augment path.
    augment_path = _resolve_augment_path(augment_path, board_name)

    # Load Linux DT (secondary).
    try:
        linux_sdt = _load_linux_sdt(linux_dt_path)
    except RuntimeError as e:
        lopper.log._error(str(e))
        return False

    lopper.log._info(
        f"compose_non_linux: zephyr={sdt.dts}, linux={linux_dt_path}, "
        f"augment={augment_path or '(none)'}")

    non_linux = _extract_non_linux(sdt, linux_sdt)
    non_linux = _merge_augment(non_linux, augment_path)

    # Build the YAML.
    payload = {
        'non_linux': {
            'compatible': 'openamp,domain-v1,non-linux',
            'source_summary': {
                'clusters': len(non_linux['clusters']),
                'memory':   len(non_linux['memory']),
                'devices':  len(non_linux['devices']),
            },
            'clusters': non_linux['clusters'],
            'memory':   non_linux['memory'],
            'devices':  non_linux['devices'],
        }
    }
    if board_name:
        payload['non_linux']['board'] = board_name

    out_dir = os.path.dirname(output_file)
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir, exist_ok=True)

    from ruamel.yaml import YAML
    yaml = YAML()
    yaml.default_flow_style = False
    yaml.indent(mapping=2, sequence=4, offset=2)
    with open(output_file, 'w') as fh:
        yaml.dump(payload, fh)

    lopper.log._info(
        f"compose_non_linux: wrote {output_file} "
        f"({non_linux['source_summary']['clusters'] if False else len(non_linux['clusters'])} clusters, "
        f"{len(non_linux['memory'])} memory, {len(non_linux['devices'])} devices)")
    return True
