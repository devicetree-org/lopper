#/*
# * Copyright (c) 2026 Advanced Micro Devices, Inc. All Rights Reserved.
# *
# * Author:
# *       Bruce Ashfield <bruce.ashfield@amd.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

"""
Generate a starter domains.yaml from a System Device Tree.

Reads an assembled SDT, finds every cpus,cluster node, and emits one
starter domain per cluster — partitioned by the `lopper-source` tags
that assemble_sdt attached during assembly. The output is an
openamp,domain-v1 YAML the user edits before handing off to the
downstream Lopper domain-processing tools.

Naming parallels `sdt_devices` (which produces a flat device
enumeration from an SDT); this assist produces the *partition* of
that enumeration. Both run post-`assemble_sdt`.

The partition heuristics are intentionally simple — this assist
produces a *starting point*, not a final partition:

- Each `cpus,cluster` node becomes one domain. The domain inherits
  the cluster's label as its name (Linux/A-class cluster typically
  ends up `cpus_a72` / `cpus_a53`; co-processor clusters use the
  label compose_non_linux + assemble_sdt assigned, e.g. `cpus_r5`
  or `cpus_m4`).
- Untagged cluster (the Linux side) → access list is a single
  `dev: "*"` glob, since Linux normally claims everything not
  explicitly assigned elsewhere. The user narrows it.
- Tagged cluster (lopper-source = zephyr/domain/non-linux) →
  access list enumerates the children of `/non_linux_soc`
  carrying the same source tag, plus explicit memory entries.
- Reserved-memory carve-outs (whether contributed by the board
  domains.yaml or already in the SDT) are heuristic-matched to
  clusters by name prefix (`rpu0_*` → R5 cluster, `m4_*` → M4
  cluster); shared regions (`rpmsg_*`, `shmem_*`, `vdev*`) are
  attached to every non-Linux domain.

Usage:
    lopper <system-top.dts> - -- sdt_domains -o <domains.yaml>

Options:
    -v, --verbose          Enable verbose output
    -o, --output PATH      Output domains YAML path (required)
"""

import getopt
import logging
import os
import re
import sys

from ruamel.yaml import YAML
from ruamel.yaml.scalarint import HexInt

import lopper
import lopper.log

lopper.log._init(__name__)


def is_compat(node, compat_string_to_test):
    """Assist-framework dispatch."""
    if re.search("sdt-domains,sdt-v1", compat_string_to_test):
        return sdt_domains
    if re.search("module,sdt_domains", compat_string_to_test):
        return sdt_domains
    return ""


def usage():
    print("""
   Usage: sdt_domains -o <domains.yaml>

      -v, --verbose       Enable verbose output
      -o, --output PATH   Output domains YAML path (required)

   Walks the SDT for cpus,cluster nodes and emits a starter
   domains.yaml partitioning resources across one domain per
   cluster, using the lopper-source tags assemble_sdt attached.
    """)


# --- tree-walk helpers --------------------------------------------------

def _is_cpus_cluster(node):
    """True iff `node` is a cpus,cluster wrapper."""
    compat = node.propval('compatible')
    if not compat or compat == ['']:
        return False
    if isinstance(compat, list):
        return 'cpus,cluster' in compat
    return 'cpus,cluster' == compat


def _source_tag(node):
    """Return the lopper-source tag on `node`, or '' if absent."""
    v = node.propval('lopper-source')
    if isinstance(v, list) and v:
        return v[0] if isinstance(v[0], str) else ''
    if isinstance(v, str):
        return v
    return ''


def _arch_from_cluster(node):
    """Pick a short arch token from the first cpu child's compatible."""
    for child in (node.child_nodes or {}).values():
        if child.propval('device_type') == ['cpu']:
            compat = child.propval('compatible')
            if isinstance(compat, list):
                compat = compat[0] if compat else ''
            if not compat:
                continue
            m = re.search(r'cortex-([am]\d+|r\d+f?)', compat)
            if m:
                return m.group(1).rstrip('f')
    return 'unknown'


def _count_cpus(node):
    """Number of cpu@N children under a cpus,cluster node."""
    n = 0
    for child in (node.child_nodes or {}).values():
        if child.propval('device_type') == ['cpu']:
            n += 1
    return n


def _cpumask(node):
    """A starter cpumask covering all cpus in the cluster."""
    n = _count_cpus(node) or 1
    return (1 << n) - 1


def _cluster_label(node):
    """Stable label for the cluster — falls back to a synthesized one."""
    if node.label:
        return node.label
    return f'cpus_{_arch_from_cluster(node)}'


def _domain_name_from_cluster(node):
    """Domain key in the output YAML.

    A class → APU; R class → RPU; M class → MCU; fallback to the
    cluster label. Duplicate-name disambiguation happens in
    `_build_domains_payload`.
    """
    arch = _arch_from_cluster(node)
    if re.match(r'a\d+', arch):
        return 'APU'
    if re.match(r'r\d+', arch):
        return 'RPU'
    if re.match(r'm\d+', arch):
        return 'MCU'
    return _cluster_label(node)


# --- memory / access enumeration ---------------------------------------

_SHARED_CARVEOUT_RE = re.compile(r'(rpmsg|shmem|vdev|ipc_share)',
                                 re.IGNORECASE)
_CLUSTER_CARVEOUT_RE = re.compile(r'(rpu\d*|r5|m4|m7|mcu)[_\-]?',
                                  re.IGNORECASE)
_LINUX_CARVEOUT_PROPS = ('linux,cma-default', 'linux,dma-default')


def _node_has_prop(node, name):
    return name in (getattr(node, '__props__', {}) or {})


def _carveout_owner(node):
    """Classify a /reserved-memory carve-out by its likely owner.

    Returns one of:
      'linux'         — belongs to the Linux/APU domain
      'shared'        — belongs to every co-processor domain (OpenAMP IPC)
      '<arch>' token  — belongs to one cluster ('r5', 'm4', …)

    Heuristics, in priority order:
      1. Linux-owned pools — a `linux,*` node name, a `linux,cma-default`
         / `linux,dma-default` flag, or a reusable `shared-dma-pool`
         (the Linux CMA convention).
      2. Shared OpenAMP regions — rpmsg / shmem / vdev / ipc_share.
      3. Cluster-specific — name prefixed rpu* / m4 / m7 / mcu.
      4. Anything else defaults to 'linux': the SDT base is the Linux
         DT, so an unrecognised carve-out is far more likely Linux's
         than a co-processor's. Co-processor carve-outs are the named
         exceptions (rules 2–3), not the default.
    """
    name = node.name or ''
    if name.startswith('linux,') or \
            any(_node_has_prop(node, p) for p in _LINUX_CARVEOUT_PROPS):
        return 'linux'
    compat = node.propval('compatible')
    if isinstance(compat, list):
        compat = compat[0] if compat else ''
    if compat == 'shared-dma-pool' and _node_has_prop(node, 'reusable'):
        return 'linux'
    if _SHARED_CARVEOUT_RE.search(name):
        return 'shared'
    m = _CLUSTER_CARVEOUT_RE.match(name)
    if m:
        token = m.group(1).lower()
        return 'r5' if token.startswith('rpu') else token
    return 'linux'


def _enumerate_root_memory(sdt, want_source=None):
    """Return /memory@<addr> nodes at root, filtered by lopper-source tag.

    `want_source=None` selects the untagged (Linux-side) memory; a
    string value selects only nodes with that lopper-source tag.
    """
    out = []
    for n in sdt.tree:
        if n.abs_path.count('/') != 1:
            continue
        if not n.name.startswith('memory@'):
            continue
        src = _source_tag(n)
        if want_source is None and not src:
            out.append(n)
        elif want_source is not None and src == want_source:
            out.append(n)
    return out


def _enumerate_reserved_memory(sdt):
    """Return children of /reserved-memory (board-declared carve-outs)."""
    try:
        resmem = sdt.tree['/reserved-memory']
    except Exception:
        return []
    return list((resmem.child_nodes or {}).values())


def _enumerate_non_linux_bus_children(sdt):
    """Return the per-device children of /non_linux_soc, if present."""
    try:
        bus = sdt.tree['/non_linux_soc']
    except Exception:
        return []
    return list((bus.child_nodes or {}).values())


def _parent_cells(node):
    """(#address-cells, #size-cells) from `node`'s parent, with DT defaults."""
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
    v = 0
    for c in cells:
        v = (v << 32) | (int(c) & 0xffffffff)
    return v


def _node_reg_start_size(node):
    """First-range (start, size) extraction from a node's `reg`.

    Multi-range reg properties are common on memory nodes that span
    discontinuous regions (DDR-low + DDR-high). The starter only
    takes the first range — the user can extend if they need more.
    """
    reg = node.propval('reg')
    if not reg or reg == ['']:
        return None, None
    if isinstance(reg, int):
        return reg, None
    ac, sc = _parent_cells(node)
    pair = ac + sc
    if pair == 0 or len(reg) < pair:
        if len(reg) >= 1:
            return reg[0], None
        return None, None
    start = _cells_to_int(reg[:ac])
    size = _cells_to_int(reg[ac:ac + sc])
    return start, size


def _memory_entry(node, source_hint=None):
    """Build a memory list entry in domains.yaml shape."""
    start, size = _node_reg_start_size(node)
    entry = {'dev': node.name}
    if start is not None:
        entry['start'] = HexInt(start)
    if size is not None:
        entry['size'] = HexInt(size)
    src = source_hint or _source_tag(node)
    if src:
        entry['source'] = src
    return entry


def _access_entry(node):
    """Build an access list entry."""
    entry = {'dev': node.name, 'flags': {}}
    if node.label:
        entry['label'] = node.label
    src = _source_tag(node)
    if src:
        entry['source'] = src
    return entry


# --- domain construction ----------------------------------------------

def _build_linux_domain(sdt, cluster_node, reserved_mem):
    """Domain block for the Linux-side cluster.

    Linux owns the bulk of the peripheral tree, so a single `*` glob
    in access is the right starter — the user narrows it as they
    move resources to co-processor domains.
    """
    domain = {
        'compatible': 'openamp,domain-v1',
        'cpus': [{
            'cluster': _cluster_label(cluster_node),
            'cpumask': f'0x{_cpumask(cluster_node):x}',
            'mode': {'el': HexInt(0x3), 'secure': False},
        }],
        'memory': [_memory_entry(m) for m in _enumerate_root_memory(sdt)],
        'access': [{'dev': '*'}],
    }
    # Linux-owned reserved-memory carve-outs (CMA/DMA pools, etc.)
    # belong here, not in a co-processor domain.
    for m in reserved_mem:
        if _carveout_owner(m) == 'linux':
            domain['memory'].append(_memory_entry(m))
    return domain


def _build_non_linux_domain(sdt, cluster_node, cluster_arch, cluster_source,
                            reserved_mem, bus_children):
    """Domain block for one co-processor cluster.

    Pulls in:
      - The matching reserved-memory carve-outs (heuristic by name
        prefix), plus any shared (`rpmsg_*`, `shmem_*`) carve-outs.
      - Every child of /non_linux_soc tagged matching the cluster's
        source (typically all of them — the bus wraps the
        co-processor's view).
    """
    memory_entries = []
    # 1. Root-level memory tagged with this cluster's source
    #    (e.g. M4 DTCM / OCRAM, R5 OCM).
    for m in _enumerate_root_memory(sdt, want_source=cluster_source):
        memory_entries.append(_memory_entry(m))
    # 2. Reserved-memory carve-outs owned by this cluster (name
    #    heuristic rpu*/m4*/...) plus shared OpenAMP regions
    #    (rpmsg/shmem/vdev) that every co-processor domain maps.
    #    Linux-owned pools (CMA etc.) are excluded — they go to APU.
    for m in reserved_mem:
        owner = _carveout_owner(m)
        if owner == cluster_arch or owner == 'shared':
            memory_entries.append(_memory_entry(m, source_hint='domain'))

    access_entries = [_access_entry(child) for child in bus_children]

    domain = {
        'compatible': 'openamp,domain-v1',
        'cpus': [{
            'cluster': _cluster_label(cluster_node),
            'cpumask': f'0x{_cpumask(cluster_node):x}',
            'mode': {'secure': False},
        }],
        'memory': memory_entries,
        'access': access_entries,
    }
    return domain


def _is_management_cluster(node):
    """True for fixed management cores that aren't OS-partition targets.

    The Versal PMC and PSM microblazes run AMD's PLM / PSM firmware,
    not a user OS — they're in the SDT for completeness (and as
    address-map targets) but should not become partition domains in
    the starter.
    """
    for child in (node.child_nodes or {}).values():
        cc = child.propval('compatible')
        cc = cc if isinstance(cc, list) else [cc]
        if any(isinstance(c, str) and 'microblaze' in c for c in cc):
            return True
    return False


def _build_domains_payload(sdt):
    """Walk the SDT, return the full {domains: ...} payload."""
    clusters = [n for n in sdt.tree
                if _is_cpus_cluster(n) and not _is_management_cluster(n)]
    if not clusters:
        raise RuntimeError(
            "no cpus,cluster nodes found in SDT — "
            "did assemble_sdt run first?")

    reserved_mem = _enumerate_reserved_memory(sdt)
    bus_children = _enumerate_non_linux_bus_children(sdt)

    inner = {}
    domain_id = 0
    for cluster in clusters:
        src = _source_tag(cluster)
        arch = _arch_from_cluster(cluster)
        name = _domain_name_from_cluster(cluster)
        # Avoid duplicate keys if two clusters resolve to the same name.
        candidate = name
        idx = 0
        while candidate in inner:
            idx += 1
            candidate = f'{name}_{idx}'
        name = candidate

        if not src:
            domain = _build_linux_domain(sdt, cluster, reserved_mem)
        else:
            domain = _build_non_linux_domain(
                sdt, cluster, arch, src, reserved_mem, bus_children)
        domain['id'] = domain_id
        domain_id += 1
        inner[name] = domain

    return {
        'domains': {
            'default': {
                'compatible': 'openamp,domain-v1',
                'id': 0,
                'domains': inner,
            }
        }
    }


# --- entry point ------------------------------------------------------

def sdt_domains(tgt_node, sdt, options):
    """Assist entry point."""
    try:
        verbose = options['verbose']
    except KeyError:
        verbose = 0
    try:
        args = options['args']
    except KeyError:
        args = []

    try:
        opts, _ = getopt.getopt(args, "hvo:",
                                ["help", "verbose", "output="])
    except getopt.GetoptError as e:
        lopper.log._error(f"sdt_domains: invalid option: {e}")
        usage()
        return False

    output_path = None
    for o, a in opts:
        if o in ('-h', '--help'):
            usage()
            return True
        elif o in ('-v', '--verbose'):
            verbose += 1
        elif o in ('-o', '--output'):
            output_path = a

    if verbose > 1:
        lopper.log._level(logging.DEBUG, __name__)
    elif verbose > 0:
        lopper.log._level(logging.INFO, __name__)

    if not output_path:
        lopper.log._error("sdt_domains: --output is required")
        usage()
        return False

    try:
        payload = _build_domains_payload(sdt)
    except Exception as e:
        lopper.log._error(f"sdt_domains: failed: {e}")
        import traceback
        traceback.print_exc()
        return False

    out_dir = os.path.dirname(output_path)
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir, exist_ok=True)
    yaml = YAML()
    yaml.default_flow_style = False
    yaml.indent(mapping=2, sequence=4, offset=2)
    with open(output_path, 'w') as fh:
        yaml.dump(payload, fh)
    lopper.log._info(
        f"sdt_domains: wrote {output_path} "
        f"({len(payload['domains']['default']['domains'])} domains)")
    return True
