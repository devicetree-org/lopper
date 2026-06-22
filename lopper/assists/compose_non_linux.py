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

Two YAML files combine here for the integration overlay:

  - The shipped per-board template at
    lopper/data/boards/<board>/domains.yaml, auto-located via
    --board. Carries the board's stable integration declarations.
  - The user's per-deployment overlay passed via --domains. Holds
    only the edits the user adds on top of the template.

The two get deep-merged in memory via the shared
`LopperYAML.deep_merge` (dicts recurse, lists append) before
extraction; the per-`dev` extraction that follows is last-wins, so
an overlay entry overrides the template entry with the same `dev`
and adds entries with a new `dev`. Users don't have to copy the
template into their workspace and keep it in sync — they maintain a
small overlay file with their
specific changes, and `git pull` refreshes the template
underneath without disturbing their edits.

Usage:
    lopper <flat-zephyr.dts> - -- compose_non_linux \\
        --linux-dt <flat-linux.dts> \\
        [--board <name>] \\
        [--domains <user-overlay.yaml>] \\
        [--no-template] \\
        -o non-linux.yaml

Options:
    -v, --verbose          Enable verbose output
    --linux-dt PATH        Pre-flattened Linux DT, used to figure out
                           which addresses are already covered (those
                           are dropped — Linux DT supplies them).
                           Required.
    -b, --board NAME       Auto-locate the shipped per-board template
                           at lopper/data/boards/<NAME>/domains.yaml
                           and use it as the integration base.
    --domains PATH         User's per-deployment overlay, deep-merged
                           on top of the board template. Reserved-
                           memory carve-outs (`no-map: true` memory
                           entries) and board-only peripherals
                           declared here get tagged `source: domain`
                           and injected into the SDT by assemble_sdt.
                           Same file the downstream domain-processing
                           tools consume for partition intent after
                           the SDT exists.
    --no-template          Skip the shipped per-board template; use
                           only what --domains provides. Diagnostic /
                           bring-your-own-template use.
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
from lopper.yaml import LopperYAML
import lopper
import lopper.log
from lopper.assists import lopper_lib


lopper.log._init(__name__)


# Repo root and per-board data root, for domains.yaml lookup.
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
      --linux-dt PATH        Pre-flattened Linux DT (required).
      -b, --board NAME       Auto-locate the shipped per-board
                             domains.yaml template under
                             lopper/data/boards/<NAME>/.
      --domains PATH         User's per-deployment overlay, deep-
                             merged on top of the board template.
                             Overlay overrides template entries with
                             the same `dev`; new `dev`s are added.
      --no-template          Skip the shipped per-board template.
      -o, --output PATH      Output YAML path (required).

   Walks the Zephyr device tree (the main lopper -f input), captures
   every node not present in the Linux DT at the same address, emits
   rich per-device properties (with phandle references encoded as
   "&label" strings), and merges integration declarations from the
   per-board domains.yaml template plus any --domains overlay. The
   resulting non-linux.yaml is consumed by assemble_sdt to produce
   the SDT alongside the Linux DT base.
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


def _encode_phandle_property(prop):
    """Encode a phandle-bearing property in Lopper's canonical YAML form.

    Uses lopper core's `LopperProp.phandle_map()` to locate the phandle
    slots and their trailing cells — phandle_map walks the property
    against the phandle_possible_properties registry and reads each
    target node's #*-cells count itself, so we don't re-derive any cell
    arithmetic here. It returns one entry per value cell: `0` for a plain
    cell, the dereferenced node at a phandle slot, or `"#invalid"` for an
    unresolvable phandle.

    Output is a flat list mixing `"&label"` strings with HexInt cells, as
    `LopperTree.label_to_phandle()` expects; a lone phandle collapses to
    a bare `"&label"` string. Non-phandle properties (empty phandle_map)
    and shapes we don't recognise fall through to the raw value.
    """
    raw_value = prop.value
    if not isinstance(raw_value, list) or not all(isinstance(v, int)
                                                  for v in raw_value):
        return raw_value
    pmap = prop.phandle_map()
    if not pmap:
        return raw_value
    flat = [slot for record in pmap for slot in record]
    if len(flat) != len(raw_value):
        return raw_value

    encoded = []
    for slot, val in zip(flat, raw_value):
        # slot is 0 (plain cell), "#invalid" (unresolvable phandle), or
        # the dereferenced target node at a phandle position.
        if isinstance(slot, (int, str)) or slot is None:
            label = None
        else:
            label = slot.label or None
        encoded.append(f'&{label}' if label else HexInt(val))

    if len(encoded) == 1 and isinstance(encoded[0], str):
        return encoded[0]
    return encoded


def _encode_property(prop):
    """Encode one property value for the YAML output.

    Phandle-bearing properties (detected via core's phandle_map) get the
    "&label"/cells form; plain int / int-array / string-array values pass
    through with HexInt wrapping for ints.
    """
    raw_value = prop.value
    # Boolean / present-empty properties: lopper returns [''] for
    # both absent (we won't call this then) and present-boolean.
    if raw_value == ['']:
        return True

    encoded = _encode_phandle_property(prop)
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
        out[prop_name] = _encode_property(prop)
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
            ac, sc = lopper_lib.parent_cells(node)
            entry['properties'] = _normalise_memory_properties(
                entry['properties'], ac, sc)
            out['memory'][name] = entry
            continue

        # Peripheral with an @addr that Linux doesn't have.
        if addr is not None:
            out['devices'][name] = _extract_node(zephyr_sdt, node,
                                                 source_tag='zephyr')

    return out


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
    start = lopper_lib.cells_to_int(reg[:ac])
    size = lopper_lib.cells_to_int(reg[ac:ac + sc])
    out = {}
    for k, v in props.items():
        if k == 'reg':
            out['start'] = HexInt(start)
            out['size'] = HexInt(size)
        else:
            out[k] = v
    return out


_DOMAINS_HEX_FIELDS = ('start', 'size', 'addr', 'reg')


def _normalise_domains_entry(entry):
    """User-written ints in domains.yaml come in as plain Python ints
    after the YAML loader. Wrap the conventional address/size fields
    as HexInt so the output formats them in hex — matching how
    extractor-derived values look in the same YAML.
    """
    out = {}
    for k, v in entry.items():
        if k == 'dev':
            continue
        if k in _DOMAINS_HEX_FIELDS and isinstance(v, int) \
                and not isinstance(v, HexInt):
            out[k] = HexInt(v)
        else:
            out[k] = v
    return out


def _iter_domain_blocks(root):
    """Yield (name, block) for every openamp,domain-v1 sub-domain in
    the user's domains.yaml, walking the conventional shape
    `domains.<root>.domains.<name>: { ... }`.
    """
    domains = root.get('domains') or {}
    for outer_name, outer in domains.items():
        if not isinstance(outer, dict):
            continue
        # The outermost (default) block typically wraps per-OS sub-
        # domains under its own `domains:` key, but the user is
        # permitted to put declarations directly on the outer block.
        yield outer_name, outer
        inner = outer.get('domains') or {}
        for name, blk in inner.items():
            if isinstance(blk, dict):
                yield name, blk


def _is_reserved_memory_entry(entry):
    """Reserved-memory declarations are memory entries flagged with
    `no-map: true` (or `reusable: true`). They name a region the
    SDT must carry as a /reserved-memory child, even though the
    upstream Linux DT and Zephyr DT do not.
    """
    if not isinstance(entry, dict):
        return False
    return bool(entry.get('no-map') or entry.get('reusable'))


def _load_yaml_file(path):
    """Return parsed YAML or None if the file is missing/unreadable."""
    if not path or not os.path.isfile(path):
        return None
    from ruamel.yaml import YAML
    yaml = YAML(typ='safe')
    try:
        with open(path) as fh:
            return yaml.load(fh) or {}
    except Exception as e:
        lopper.log._warning(
            f"compose_non_linux: failed to read {path}: {e}")
        return None


def _merge_domains_integration(non_linux, template_path, overlay_path):
    """Inject integration declarations from the per-board domains.yaml
    template plus an optional user overlay.

    Both files use the standard openamp,domain-v1 shape. The template
    ships with the repo at lopper/data/boards/<board>/domains.yaml and
    is auto-located via --board; the overlay is the user's per-
    deployment file referenced via --domains. They get deep-merged in
    memory via the shared `LopperYAML.deep_merge` (dicts recurse,
    lists append), then the merged structure is scanned for:

      - Reserved-memory carve-outs (memory / sram entries with
        `no-map: true` or `reusable: true`) → injected as
        `source: domain` entries; assemble_sdt later puts each under
        the SDT's /reserved-memory.
      - Board-only peripherals in access lists that carry their own
        properties (i.e. declarations, not bare references / globs).

    Because the overlay is merged second, its list entries land after
    the template's; the per-`dev` dict assignment below is last-wins,
    so an overlay entry with the same `dev` as a template entry
    overrides it, and a new `dev` is added.

    Pure partition-intent entries (memory referencing an existing SDT
    node without no-map, access globs, etc.) are ignored here —
    they're consumed later by the domain-processing tools, not by
    SDT assembly.
    """
    template = _load_yaml_file(template_path)
    overlay = _load_yaml_file(overlay_path)
    if template and overlay:
        merged = LopperYAML.deep_merge(template, overlay)
    else:
        merged = template or overlay
    if not merged:
        return non_linux

    for _name, block in _iter_domain_blocks(merged):
        # Reserved-memory carve-outs from the memory/sram lists.
        for entry in (block.get('memory') or []) + (block.get('sram') or []):
            if not _is_reserved_memory_entry(entry):
                continue
            dev = entry.get('dev') or f'domain-mem-{len(non_linux["memory"])}'
            non_linux['memory'][dev] = {
                'source': 'domain',
                'properties': _normalise_domains_entry(entry),
            }
        # Board-only peripherals from the access list — same idea:
        # only entries that declare a new node (not a glob, not a
        # bare reference to an SDT-resident name).
        for entry in (block.get('access') or []):
            if not isinstance(entry, dict):
                continue
            dev = entry.get('dev')
            # Skip globs and references that don't carry their own
            # properties — those are partition intent, not declarations.
            if not dev or '*' in dev:
                continue
            extra = {k: v for k, v in entry.items()
                     if k not in ('dev', 'flags', 'label', 'spec_name')}
            if not extra:
                continue
            non_linux['devices'][dev] = {
                'source': 'domain',
                'properties': _normalise_domains_entry(entry),
            }
    return non_linux


def _resolve_template_path(board_name):
    """Return the shipped per-board template path, or None."""
    if not board_name:
        return None
    return os.path.join(_BOARDS_ROOT, board_name, 'domains.yaml')


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
            ["help", "verbose", "linux-dt=", "domains=",
             "no-template", "board=", "output="])
    except getopt.GetoptError as e:
        lopper.log._error(f"compose_non_linux: invalid option: {e}")
        usage()
        return False

    linux_dt_path = None
    overlay_path = None
    board_name = None
    output_file = None
    suppress_template = False

    for o, a in opts:
        if o in ('-h', '--help'):
            usage()
            return True
        elif o in ('-v', '--verbose'):
            verbose += 1
        elif o == '--linux-dt':
            linux_dt_path = a
        elif o == '--domains':
            overlay_path = a
        elif o == '--no-template':
            suppress_template = True
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

    # Auto-locate the shipped per-board template (unless suppressed),
    # then overlay the user's --domains file on top.
    template_path = None if suppress_template else _resolve_template_path(board_name)

    # Load Linux DT (secondary).
    try:
        linux_sdt = _load_linux_sdt(linux_dt_path)
    except RuntimeError as e:
        lopper.log._error(str(e))
        return False

    lopper.log._info(
        f"compose_non_linux: zephyr={sdt.dts}, linux={linux_dt_path}, "
        f"template={template_path or '(none)'}, "
        f"overlay={overlay_path or '(none)'}")

    non_linux = _extract_non_linux(sdt, linux_sdt)
    non_linux = _merge_domains_integration(non_linux, template_path,
                                           overlay_path)

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
