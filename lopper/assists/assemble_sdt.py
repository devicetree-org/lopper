#/*
# * Copyright (c) 2026 Advanced Micro Devices, Inc. All Rights Reserved.
# *
# * Author:
# *       Bruce Ashfield <bruce.ashfield@amd.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

"""
Assemble a system-top.dts from a Linux DT base plus a non-linux YAML.

The Linux DT is the BASE of the SDT — its nodes, properties, phandles
and labels round-trip verbatim, so that the SDT remains close enough
to bootable that the standard downstream Lopper slicing flow can
produce a complete per-OS Linux device tree from it.

On top of that base, this assist wraps /cpus into the cpus,cluster
convention the SDT spec uses, then attaches the non-linux content
emitted by compose_non_linux: additional CPU clusters (R5, M4),
TCM/OCRAM regions, reserved-memory carve-outs for co-processor
firmware, and co-processor-side peripherals. Phandle references in
the non-linux YAML use the canonical Lopper "&label" form and are
resolved against the merged tree at assembly time.

Usage:
    lopper - - -- assemble_sdt \\
        --linux-dt <flat-linux.dts> \\
        --non-linux <non-linux.yaml> \\
        -o <system-top.dts>

Options:
    -v, --verbose          Enable verbose output
    --linux-dt PATH        Pre-flattened Linux DT (required) — base tree
    --non-linux PATH       compose_non_linux YAML (required)
    -o, --output PATH      Output system-top.dts path (required)
"""

import getopt
import logging
import os
import re
import sys
import tempfile

from ruamel.yaml import YAML
from ruamel.yaml.scalarint import HexInt

import lopper
import lopper.log
from lopper import LopperSDT
from lopper.tree import LopperNode, LopperProp, LopperTreePrinter
from lopper.assists import lopper_lib

lopper.log._init(__name__)


def is_compat(node, compat_string_to_test):
    """Assist-framework dispatch."""
    if re.search("assemble-sdt,assemble-v1", compat_string_to_test):
        return assemble_sdt
    if re.search("module,assemble_sdt", compat_string_to_test):
        return assemble_sdt
    return ""


def usage():
    print("""
   Usage: assemble_sdt --linux-dt <dts> --non-linux <yaml> -o <out.dts>

      -v, --verbose          Enable verbose output
      --linux-dt PATH        Pre-flattened Linux DT (base tree)
      --non-linux PATH       compose_non_linux YAML
      -o, --output PATH      Output system-top.dts path

   Loads the Linux DT as the SDT base, wraps its /cpus block into the
   cpus,cluster convention, attaches non-Linux clusters / memory /
   devices from the YAML, resolves "&label" phandle refs against the
   merged tree, and writes the result as a system-top.dts.
    """)


# --- YAML loading ---------------------------------------------------------

def _load_non_linux_yaml(path):
    """Read the compose_non_linux YAML payload."""
    yaml = YAML(typ='safe')
    with open(path) as fh:
        data = yaml.load(fh) or {}
    nl = data.get('non_linux') or {}
    if nl.get('compatible') != 'openamp,domain-v1,non-linux':
        raise RuntimeError(
            f"{path}: missing 'non_linux:' block with "
            f"compatible 'openamp,domain-v1,non-linux'")
    return nl


# --- base tree load ------------------------------------------------------

def _load_linux_base(linux_dt_path):
    """Load the Linux DT as a LopperSDT we can mutate in place."""
    if not os.path.isfile(linux_dt_path):
        raise RuntimeError(f"Linux DT not found: {linux_dt_path}")
    sdt = LopperSDT(linux_dt_path)
    sdt.dryrun = True
    sdt.permissive = True
    sdt.outdir = tempfile.mkdtemp(prefix='assemble-sdt-')
    sdt.setup(linux_dt_path, "", "", False, True, None)
    return sdt


# --- property value encoding --------------------------------------------

_EMPTY = object()  # sentinel: emit as a no-value (boolean-present) property


def _value_for_lopperprop(raw):
    """Translate a YAML value back into the form LopperProp expects.

    - HexInt / int → int
    - 'string'    → string
    - True (bare boolean-present property) → _EMPTY sentinel
    - False        → None (drop the property entirely)
    - list mixing strings ('&label') and ints → flat list (phandles
      stay as '&label' strings for LopperTree.label_to_phandle to
      resolve later)
    """
    if raw is True:
        return _EMPTY
    if raw is False:
        return None
    if isinstance(raw, (HexInt, int)) and not isinstance(raw, bool):
        return int(raw)
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        out = []
        for v in raw:
            if isinstance(v, bool):
                continue
            if isinstance(v, (HexInt, int)):
                out.append(int(v))
            else:
                out.append(v)
        return out
    return raw


def _attach_property(node, prop_name, raw_value):
    """Build a LopperProp from a YAML-shaped value and attach it."""
    val = _value_for_lopperprop(raw_value)
    if val is None:
        return
    if val is _EMPTY:
        node + LopperProp(name=prop_name)
    else:
        node + LopperProp(name=prop_name, value=val)


def _attach_properties(node, prop_dict):
    if not prop_dict:
        return
    for prop_name, raw in prop_dict.items():
        _attach_property(node, prop_name, raw)


# --- /cpus wrapping ------------------------------------------------------

def _wrap_linux_cpus(sdt):
    """Mark the Linux DT's /cpus block as an SDT cpus,cluster.

    Keep the /cpus node name intact (so any Linux DT references to
    its children at /cpus/cpu@N paths still work), just attach a
    `cpus_<arch>` label and a `compatible = "cpus,cluster"` property
    so downstream slicing recognises the Linux cluster alongside the
    non-Linux ones.
    """
    try:
        cpus = sdt.tree['/cpus']
    except Exception:
        lopper.log._warning("assemble_sdt: no /cpus node in Linux DT — skipping wrap")
        return None

    arch = 'unknown'
    for child in cpus.child_nodes.values():
        if child.propval('device_type') == ['cpu']:
            compat = child.propval('compatible')
            if compat:
                arch = lopper_lib.arch_label(compat[0] if isinstance(compat, list) else compat)
                break

    if not cpus.label:
        cpus.label = f'cpus_{arch}'
    existing_compat = cpus.propval('compatible')
    if not existing_compat or existing_compat == ['']:
        cpus + LopperProp(name='compatible', value='cpus,cluster')

    return cpus


# --- non-linux cluster / memory / device attachment ---------------------

def _arch_from_cluster_entry(entry):
    """Pick the arch label for a non-linux cluster entry."""
    cpus = entry.get('cpus') or {}
    for cpu_name, cpu in cpus.items():
        props = (cpu or {}).get('properties') or {}
        compat = props.get('compatible')
        if compat:
            return lopper_lib.arch_label(compat)
    return 'unknown'


def _add_non_linux_cluster(sdt, cluster_name, entry, taken_cluster_indices):
    """Add one non-linux cluster as a root-level cpus,cluster node.

    Naming: cpus-<arch>@<N> where N is the next free index across
    same-arch clusters in the merged tree.
    """
    arch = _arch_from_cluster_entry(entry)
    idx = taken_cluster_indices.get(arch, 0)
    while True:
        candidate_path = f'/cpus-{arch}@{idx}'
        if candidate_path not in sdt.tree.__nodes__:
            break
        idx += 1
    taken_cluster_indices[arch] = idx + 1

    node = LopperNode(-1, candidate_path)
    node.label = f'cpus_{arch}_{idx}' if idx > 0 else f'cpus_{arch}'
    sdt.tree.add(node)

    _attach_properties(node, entry.get('properties'))
    # The cluster node may have come in without explicit
    # #address-cells/#size-cells; default to the SDT convention for
    # cpu enumeration.
    if not node.propval('#address-cells') or node.propval('#address-cells') == ['']:
        node + LopperProp(name='#address-cells', value=1)
    sc = node.propval('#size-cells')
    if not sc or sc == ['']:
        node + LopperProp(name='#size-cells', value=0)
    compat = node.propval('compatible')
    if not compat or compat == ['']:
        node + LopperProp(name='compatible', value='cpus,cluster')
    node + LopperProp(name='lopper-source', value=entry.get('source', 'zephyr'))

    # Attach cpu children.
    for cpu_name, cpu_entry in (entry.get('cpus') or {}).items():
        cpu_path = f'{candidate_path}/{cpu_name}'
        cpu_node = LopperNode(-1, cpu_path)
        if (cpu_entry or {}).get('label'):
            cpu_node.label = cpu_entry['label']
        sdt.tree.add(cpu_node)
        _attach_properties(cpu_node, (cpu_entry or {}).get('properties'))
        if cpu_entry and cpu_entry.get('source'):
            cpu_node + LopperProp(name='lopper-source', value=cpu_entry['source'])

    sdt.tree.resolve()
    return node


def _add_non_linux_clusters(sdt, non_linux):
    taken = {}
    clusters = non_linux.get('clusters') or {}
    for name, entry in clusters.items():
        _add_non_linux_cluster(sdt, name, entry, taken)


def _add_or_get_reserved_memory_root(sdt):
    """Return the /reserved-memory node, creating it if absent."""
    try:
        return sdt.tree['/reserved-memory']
    except Exception:
        pass
    node = LopperNode(-1, '/reserved-memory')
    sdt.tree.add(node)
    node + LopperProp(name='#address-cells', value=2)
    node + LopperProp(name='#size-cells', value=2)
    node + LopperProp(name='ranges')
    sdt.tree.resolve()
    return node


def _root_cells(sdt):
    """Return (#address-cells, #size-cells) for the SDT root."""
    try:
        root = sdt.tree['/']
    except Exception:
        return 2, 2
    return lopper_lib.node_property_cells(root, 2, 2)


def _start_size_to_reg(start, size, ac, sc):
    """Build a reg cell-list from start/size at the given cell counts."""
    return (lopper_lib.int_to_cells(start or 0, ac)
            + lopper_lib.int_to_cells(size or 0, sc))


def _add_non_linux_memory(sdt, non_linux):
    """Attach each non-linux memory entry to the SDT.

    - properties.no-map=true → child of /reserved-memory
    - else                   → /memory@<addr> at root
    Both branches translate start/size back into a reg cell-list at
    the parent's #address-cells/#size-cells.
    """
    memory = non_linux.get('memory') or {}
    if not memory:
        return

    for name, entry in memory.items():
        props = (entry or {}).get('properties') or {}
        no_map = bool(props.get('no-map'))
        start = props.get('start')
        size = props.get('size')

        if no_map:
            resmem = _add_or_get_reserved_memory_root(sdt)
            ac, sc = 2, 2  # /reserved-memory uses 2/2 per our helper
            v = resmem.propval('#address-cells')
            if isinstance(v, list) and v:
                ac = int(v[0])
            v = resmem.propval('#size-cells')
            if isinstance(v, list) and v:
                sc = int(v[0])

            child_name = name
            if '@' not in child_name and start is not None:
                child_name = f'{name}@{int(start):x}'
            child_path = f'/reserved-memory/{child_name}'
            child = LopperNode(-1, child_path)
            sdt.tree.add(child)
            if start is not None and size is not None:
                child + LopperProp(name='reg',
                                   value=_start_size_to_reg(start, size, ac, sc))
            for k, v in props.items():
                if k in ('start', 'size', 'no-map'):
                    continue
                _attach_property(child, k, v)
            child + LopperProp(name='no-map')
            child + LopperProp(name='lopper-source',
                               value=entry.get('source', 'domain'))
        else:
            ac, sc = _root_cells(sdt)
            node_name = name
            if '@' not in node_name and start is not None:
                node_name = f'memory@{int(start):x}'
            node_path = f'/{node_name}'
            mem = LopperNode(-1, node_path)
            sdt.tree.add(mem)
            if not props.get('device_type'):
                mem + LopperProp(name='device_type', value='memory')
            if start is not None and size is not None:
                mem + LopperProp(name='reg',
                                 value=_start_size_to_reg(start, size, ac, sc))
            for k, v in props.items():
                if k in ('start', 'size'):
                    continue
                _attach_property(mem, k, v)
            mem + LopperProp(name='lopper-source',
                             value=entry.get('source', 'zephyr'))

    sdt.tree.resolve()


def _ensure_non_linux_soc(sdt):
    """Return the /non_linux_soc bus wrapper, creating it on first use.

    Non-Linux peripherals come from the co-processor's view of the SoC
    bus and their reg values are bus-relative (e.g., M4 NVIC at
    0xe000e100, GPIO bank at 0x3). Hoisting them to the SDT root would
    either clash with Linux peripherals at unrelated absolute addresses
    or break unit-address uniqueness among siblings. Park them under a
    dedicated simple-bus with #address-cells/#size-cells of 1 and an
    empty ranges, matching how their native source DT addressed them.
    """
    try:
        return sdt.tree['/non_linux_soc']
    except Exception:
        pass
    bus = LopperNode(-1, '/non_linux_soc')
    bus.label = 'non_linux_soc'
    sdt.tree.add(bus)
    bus + LopperProp(name='compatible', value='simple-bus')
    bus + LopperProp(name='#address-cells', value=1)
    bus + LopperProp(name='#size-cells', value=1)
    # Intentionally no `ranges` — the co-processor's view of these
    # addresses is not necessarily reachable from the root address
    # space, and an empty `ranges` would only assert an identity
    # translation that may not hold.
    bus + LopperProp(name='lopper-source', value='non-linux')
    sdt.tree.resolve()
    return bus


def _add_non_linux_devices(sdt, non_linux):
    """Attach each non-linux peripheral under /non_linux_soc, tagged
    with the source it came from. Downstream slicers will move them
    into the right per-OS DT based on partition intent."""
    devices = non_linux.get('devices') or {}
    if not devices:
        return

    bus = _ensure_non_linux_soc(sdt)
    for name, entry in devices.items():
        path = f'{bus.abs_path}/{name}'
        if path in sdt.tree.__nodes__:
            lopper.log._warning(
                f"assemble_sdt: skipping {name} — already in tree at {path}")
            continue
        node = LopperNode(-1, path)
        if (entry or {}).get('label'):
            node.label = entry['label']
        sdt.tree.add(node)
        _attach_properties(node, (entry or {}).get('properties'))
        node + LopperProp(name='lopper-source',
                          value=entry.get('source', 'zephyr'))

    sdt.tree.resolve()


# --- phandle resolution -------------------------------------------------

def _load_soc_facts(sdt):
    """Return the SoC-facts block matching the base tree's root compatible.

    Reuses the shared DevicesCore loader, which scans the built-in
    lopper/data/socs/ plus any `-I` include dirs (via sdt.load_paths)
    for an `openamp,domain-v1,soc-facts` block whose `matches:`
    overlaps the root compatible. Returns {} when no SoC file matches
    (e.g. i.MX, which has no cluster_templates) — the caller then
    no-ops.
    """
    try:
        from lopper.assists._devices_core import DevicesCore
    except Exception as e:
        lopper.log._warning(f"assemble_sdt: cannot load SoC facts: {e}")
        return {}
    root = sdt.tree['/']
    compat = root.propval('compatible')
    if isinstance(compat, str):
        compat = [compat]
    compat = [c for c in (compat or []) if isinstance(c, str) and c]
    if not compat:
        return {}
    search_dirs = getattr(sdt, 'load_paths', None) or []
    try:
        return DevicesCore._load_soc_data(compat, search_dirs) or {}
    except Exception as e:
        lopper.log._warning(f"assemble_sdt: SoC-facts lookup failed: {e}")
        return {}


def _present_cluster_cpu_compatibles(sdt):
    """Set of cpu compatibles already present in the merged tree.

    Used to decide which cluster_templates the Linux + Zephyr inputs
    already supplied (so we only inject the genuinely missing ones).

    Scans every `device_type = "cpu"` node directly rather than
    walking cluster `child_nodes`, because a dynamically-added cluster
    (the Zephyr R5 attached earlier in this run) can have stale
    child-node linkage even after a resolve — but its cpu node is
    still in the flat tree.
    """
    present = set()
    for n in sdt.tree:
        dt = n.propval('device_type')
        if isinstance(dt, list):
            dt = dt[0] if dt else ''
        if dt != 'cpu':
            continue
        cc = n.propval('compatible')
        cc = cc if isinstance(cc, list) else [cc]
        for c in cc:
            if isinstance(c, str) and c:
                present.add(c)
    return present


def _template_role_present(template, present_compatibles):
    """True if a cluster of this template's CPU type is already in the tree.

    Matches on a prefix so the template's `arm,cortex-r5` covers the
    Zephyr-supplied `arm,cortex-r5f`, and `arm,cortex-a72` covers
    `arm,cortex-a72` / `…,armv8` enumerated by Linux.
    """
    tcompat = template.get('compatible') or ''
    if not tcompat:
        return False
    for c in present_compatibles:
        if c == tcompat or c.startswith(tcompat):
            return True
    return False


def _inject_soc_clusters(sdt, soc_facts):
    """Inject the fixed silicon clusters the upstream inputs don't carry.

    The Linux DT supplies the APU cluster and the Zephyr DT supplies
    the RPU cluster, but the PMC and PSM microblaze management cores
    (and any other always-present cluster a SoC declares) come from
    neither. They are stable silicon facts, so synthesise them from
    the per-SoC `cluster_templates` block. Each injected cluster is
    tagged `lopper-source = "soc-facts"`.

    Only roles whose CPU type is absent from the merged tree are
    injected; a role the Linux/Zephyr side already provided is left
    untouched.
    """
    templates = (soc_facts or {}).get('cluster_templates') or []
    if not templates:
        return

    present = _present_cluster_cpu_compatibles(sdt)
    for tmpl in templates:
        if _template_role_present(tmpl, present):
            continue
        role = (tmpl.get('role') or
                lopper_lib.arch_label(tmpl.get('compatible') or '') or 'unknown')
        path = f'/cpus-{role}@0'
        if path in sdt.tree.__nodes__:
            continue
        node = LopperNode(-1, path)
        node.label = f'cpus_{role}'
        sdt.tree.add(node)
        node + LopperProp(name='#address-cells', value=1)
        node + LopperProp(name='#size-cells', value=0)
        node + LopperProp(name='compatible',
                          value=tmpl.get('cluster_compatible') or 'cpus,cluster')
        node + LopperProp(name='lopper-source', value='soc-facts')

        pm_ids = tmpl.get('per_cpu_pm_ids') or []
        ncpus = int(tmpl.get('max_cpus') or len(pm_ids) or 1)
        cpu_compat = tmpl.get('compatible') or ''
        for i in range(ncpus):
            cpu = LopperNode(-1, f'{path}/cpu@{i}')
            sdt.tree.add(cpu)
            cpu + LopperProp(name='device_type', value='cpu')
            if cpu_compat:
                cpu + LopperProp(name='compatible', value=cpu_compat)
            cpu + LopperProp(name='reg', value=i)
            if tmpl.get('enable_method'):
                cpu + LopperProp(name='enable-method',
                                 value=tmpl['enable_method'])
            if i < len(pm_ids):
                cpu + LopperProp(name='lopper-pm-node', value=pm_ids[i])
            cpu + LopperProp(name='lopper-source', value='soc-facts')

        lopper.log._info(
            f"assemble_sdt: injected {role} cluster from soc-facts "
            f"({ncpus} cpu(s), compatible {cpu_compat!r})")

    sdt.tree.resolve()


# --- baseline per-cluster address-map -----------------------------------

def _first_int(val, default=None):
    """Coerce a LopperProp value (scalar or list) to a single int."""
    if isinstance(val, list):
        val = val[0] if val else None
    if val is None or val == '':
        return default
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _abs_reg(node):
    """(base, size) of node's first reg region in the root address space,
    or None if it can't be translated 1:1 (an ancestor bus is not an
    identity bus). reg cells are read at the immediate parent's
    #address-cells/#size-cells via lopper_lib.node_reg_start_size."""
    if 'reg' not in getattr(node, '__props__', {}):
        return None
    p = node.parent
    while p is not None and p.abs_path != '/':
        if not lopper_lib.is_identity_bus(p):
            return None
        p = p.parent
    start, size = lopper_lib.node_reg_start_size(node)
    if start is None:
        return None
    return start, (size or 0)


_ADDRMAP_SKIP_PREFIXES = ('/cpus', '/non_linux_soc', '/reserved-memory')
_ADDRMAP_SKIP_PATHS = ('/chosen', '/aliases')


def _collect_addressable(sdt):
    """Every reg-bearing device/memory node reachable 1:1 from the root,
    as (node, base, size). Excludes cpu/cluster nodes, the non-Linux
    bus subtree (bus-relative addrs), reserved-memory carveouts, and
    chosen/aliases. Sorted by base address for stable output."""
    out = []
    for n in sdt.tree:
        path = n.abs_path
        if path == '/' or path in _ADDRMAP_SKIP_PATHS:
            continue
        if any(path == p or path.startswith(p + '/') or path.startswith(p + '@')
               for p in _ADDRMAP_SKIP_PREFIXES):
            continue
        dt = n.propval('device_type')
        dt = dt[0] if isinstance(dt, list) and dt else dt
        if dt == 'cpu':
            continue
        ar = _abs_reg(n)
        if ar is None:
            continue
        out.append((n, ar[0], ar[1]))
    out.sort(key=lambda t: t[1])
    return out


def _existing_bases(sdt):
    """Set of root-space base addresses already present in the tree."""
    bases = set()
    for n in sdt.tree:
        ar = _abs_reg(n)
        if ar is not None:
            bases.add(ar[0])
    return bases


def _find_main_bus(sdt):
    """The top-level identity simple-bus with the most reg-bearing
    children — the bus the on-chip peripherals hang off."""
    best, best_count = None, -1
    for n in sdt.tree:
        if n.parent is None or n.parent.abs_path != '/':
            continue
        compat = n.propval('compatible')
        compat = compat if isinstance(compat, list) else [compat]
        if 'simple-bus' not in [c for c in compat if isinstance(c, str)]:
            continue
        if not lopper_lib.is_identity_bus(n):
            continue
        count = sum(1 for c in n.child_nodes.values()
                    if 'reg' in getattr(c, '__props__', {}))
        if count > best_count:
            best, best_count = n, count
    return best


def _inject_soc_apertures(sdt, soc_facts):
    """Materialize the genuinely-absent fixed silicon apertures (OCM, IPI,
    TCM) as real DT nodes on the main bus, from the SoC-facts reference
    blocks. Once present they are ordinary nodes and get picked up by
    address-map inference like any upstream node. Skips any aperture
    whose base address is already in the tree (reference the existing
    node instead of duplicating it)."""
    if not soc_facts:
        return
    bus = _find_main_bus(sdt)
    if bus is None:
        lopper.log._warning(
            "assemble_sdt: no main simple-bus found; skipping aperture injection")
        return
    bus_ac, bus_sc = lopper_lib.node_property_cells(bus)
    existing = _existing_bases(sdt)

    def _emit(base, size, node_name, *, device_type=None, compatible=None):
        if base is None or size is None or base in existing:
            return
        path = f'{bus.abs_path}/{node_name}'
        if path in sdt.tree.__nodes__:
            return
        node = LopperNode(-1, path)
        sdt.tree.add(node)
        if device_type:
            node + LopperProp(name='device_type', value=device_type)
        if compatible:
            node + LopperProp(name='compatible', value=compatible)
        node + LopperProp(name='reg',
                          value=_start_size_to_reg(base, size, bus_ac, bus_sc))
        node + LopperProp(name='lopper-source', value='soc-facts')
        existing.add(base)

    ocm = soc_facts.get('ocm_map') or {}
    _emit(_first_int(ocm.get('base')), _first_int(ocm.get('total_size')),
          f"memory@{_first_int(ocm.get('base'), 0):x}", device_type='memory')

    ipi = soc_facts.get('ipi') or {}
    ipi_base = _first_int(ipi.get('buffer_base'))
    _emit(ipi_base, _first_int(ipi.get('buffer_size')),
          f"mailbox@{ipi_base:x}" if ipi_base is not None else 'mailbox',
          compatible=ipi.get('compatible'))

    for bank in (soc_facts.get('tcm_map') or []):
        g = _first_int(bank.get('global_addr'))
        _emit(g, _first_int(bank.get('size')),
              f"memory@{g:x}" if g is not None else None,
              device_type='memory')

    sdt.tree.resolve()


def _attach_address_map(sdt, cluster, addressable):
    """Emit an inferred identity address-map on a cluster: one
    <child &node parent size> entry per addressable node, at 2/2 cells.
    The phandle slot carries the target's phandle int; lopper renders it
    as &label at output (auto-labelling unlabelled targets)."""
    na, ns = 2, 2
    value = []
    for node, base, size in addressable:
        ph = node.phandle_or_create()
        value += lopper_lib.int_to_cells(base, na)
        value.append(ph)
        value += lopper_lib.int_to_cells(base, na)
        value += lopper_lib.int_to_cells(size, ns)
    if not value:
        return
    # Store the cell-count companions as single-element lists: the
    # address-map phandle descriptor reads them via .value[0] when
    # chunking records (tree.py phandle_map), and a scalar int there
    # raises, collapsing every field width to 1 and mis-placing the
    # phandle slot.
    cluster + LopperProp(name='#ranges-address-cells', value=[na])
    cluster + LopperProp(name='#ranges-size-cells', value=[ns])
    cluster + LopperProp(name='address-map', value=value)


def _emit_cluster_address_maps(sdt, soc_facts):
    """Give every cpus,cluster a baseline address-map inferred from the
    fixed-silicon nodes present in the merged tree. No cluster is
    special-cased out by CPU type — the map describes each cluster's
    reachability; access restriction is the domain layer's job.

    Gated on the SoC-facts carrying `cluster_templates` (the same gate
    Increment 1 uses for cluster injection): we only emit baseline maps
    for SoCs whose facts describe their cluster topology (Versal today).
    Generic multi-vendor map inference is deferred, so a SoC whose facts
    file lacks cluster_templates (e.g. i.MX) stays untouched this
    increment."""
    if not (soc_facts or {}).get('cluster_templates'):
        return
    addressable = _collect_addressable(sdt)
    if not addressable:
        return
    for n in list(sdt.tree):
        if lopper_lib.is_cpu_cluster(n):
            _attach_address_map(sdt, n, addressable)
    sdt.tree.resolve()


def _resolve_phandles(sdt):
    """Walk the merged tree and resolve "&label" string refs to phandle ints.

    Uses the existing LopperTree.label_to_phandle() consolidation.
    Phandle-bearing properties (per phandle_possible_properties) are
    resolved in bare-label and &label modes; for unknown properties,
    only explicit "&label" strings are resolved.
    """
    import lopper.base
    phandle_props = lopper.base.lopper_base.phandle_possible_properties()

    for n in sdt.tree:
        for prop_name, prop in list((getattr(n, '__props__', {}) or {}).items()):
            val = prop.value
            if val is None or val == '':
                continue
            resolved = sdt.tree.label_to_phandle(val)
            if resolved == val and prop_name in phandle_props:
                resolved = sdt.tree.label_to_phandle(val, bare_label=True)
            if resolved != val:
                prop.__dict__['value'] = resolved
                prop.__modified__ = True


# --- output -------------------------------------------------------------

def _emit_dts(sdt, output_path):
    """Print the assembled tree to a system-top.dts at output_path.

    Post-processes the output to replace the Lopper sentinel
    `&invalid_phandle` with `0x0`. Lopper synthesises that sentinel
    when a phandle-bearing property has a literal-zero slot (a valid
    DT idiom meaning "no phandle here"); emitting it as `&invalid_phandle`
    produces a dtc error because no such label exists. Substituting
    `0x0` restores the original null-phandle semantics.
    """
    printer = LopperTreePrinter(True, output_path, 0)
    printer.load(sdt.tree.export())
    printer.strict = False
    printer.exec()

    with open(output_path) as fh:
        text = fh.read()
    fixed = text.replace('&invalid_phandle', '0x0')
    if fixed != text:
        with open(output_path, 'w') as fh:
            fh.write(fixed)


# --- entry point --------------------------------------------------------

def assemble_sdt(tgt_node, sdt, options):
    """Assist entry point. Ignores the main sdt input — assemble_sdt's
    real inputs are the --linux-dt and --non-linux args.
    """
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
            args, "hvo:",
            ["help", "verbose", "linux-dt=", "non-linux=", "output="])
    except getopt.GetoptError as e:
        lopper.log._error(f"assemble_sdt: invalid option: {e}")
        usage()
        return False

    linux_dt_path = None
    non_linux_path = None
    output_path = None
    for o, a in opts:
        if o in ('-h', '--help'):
            usage()
            return True
        elif o in ('-v', '--verbose'):
            verbose += 1
        elif o == '--linux-dt':
            linux_dt_path = a
        elif o == '--non-linux':
            non_linux_path = a
        elif o in ('-o', '--output'):
            output_path = a

    if verbose > 1:
        lopper.log._level(logging.DEBUG, __name__)
    elif verbose > 0:
        lopper.log._level(logging.INFO, __name__)

    if not linux_dt_path:
        lopper.log._error("assemble_sdt: --linux-dt is required")
        usage()
        return False
    if not output_path:
        lopper.log._error("assemble_sdt: --output is required")
        usage()
        return False

    lopper.log._info(
        f"assemble_sdt: linux-dt={linux_dt_path}, "
        f"non-linux={non_linux_path or '(none)'}, out={output_path}")

    try:
        base = _load_linux_base(linux_dt_path)
        non_linux = (_load_non_linux_yaml(non_linux_path)
                     if non_linux_path else {})
        _wrap_linux_cpus(base)
        if non_linux:
            _add_non_linux_clusters(base, non_linux)
            _add_non_linux_memory(base, non_linux)
            _add_non_linux_devices(base, non_linux)
        # Inject the always-present silicon clusters (PMC/PSM, …) that
        # neither the Linux nor Zephyr inputs carry, from the per-SoC
        # cluster_templates. No-op for SoCs without that facts file.
        soc_facts = _load_soc_facts(base)
        _inject_soc_clusters(base, soc_facts)
        # Materialize the fixed silicon apertures (OCM/IPI/TCM) the inputs
        # don't carry, then give each partitionable cluster a baseline
        # address-map inferred from the nodes now present. No-op without
        # a SoC-facts file.
        _inject_soc_apertures(base, soc_facts)
        _emit_cluster_address_maps(base, soc_facts)
        _resolve_phandles(base)

        out_dir = os.path.dirname(output_path)
        if out_dir and not os.path.exists(out_dir):
            os.makedirs(out_dir, exist_ok=True)
        _emit_dts(base, output_path)
    except Exception as e:
        lopper.log._error(f"assemble_sdt: failed: {e}")
        import traceback
        traceback.print_exc()
        return False

    lopper.log._info(f"assemble_sdt: success → {output_path}")
    return True
