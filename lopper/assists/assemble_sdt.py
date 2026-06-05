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


# --- arch label helper -----------------------------------------------------

_ARCH_PATTERNS = (
    (re.compile(r'arm,cortex-(a\d+)'), lambda m: m.group(1)),
    (re.compile(r'arm,cortex-(r\d+)f?'), lambda m: m.group(1)),
    (re.compile(r'arm,cortex-(m\d+)'), lambda m: m.group(1)),
    (re.compile(r'arm,armv8'), lambda m: 'a72'),
    (re.compile(r'arm,armv7m'), lambda m: 'm'),
)


def _arch_label(compatible):
    """'arm,cortex-a72' → 'a72'; 'arm,cortex-r5f' → 'r5'; …"""
    if not compatible:
        return 'unknown'
    if isinstance(compatible, list):
        compatible = compatible[0]
    for pat, fn in _ARCH_PATTERNS:
        m = pat.search(compatible)
        if m:
            return fn(m)
    return re.sub(r'[^a-zA-Z0-9]+', '_', compatible.split(',')[-1])


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
                arch = _arch_label(compat[0] if isinstance(compat, list) else compat)
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
            return _arch_label(compat)
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
        ac = root.propval('#address-cells')
        sc = root.propval('#size-cells')
    except Exception:
        ac, sc = [2], [2]
    if isinstance(ac, list) and ac:
        ac = ac[0]
    if isinstance(sc, list) and sc:
        sc = sc[0]
    return int(ac or 2), int(sc or 2)


def _int_to_cells(value, n_cells):
    """Split a wide int into n_cells 32-bit cells, big-endian."""
    cells = []
    for i in range(n_cells - 1, -1, -1):
        cells.append((int(value) >> (32 * i)) & 0xffffffff)
    return cells


def _start_size_to_reg(start, size, ac, sc):
    """Build a reg cell-list from start/size at the given cell counts."""
    cells = _int_to_cells(start or 0, ac) + _int_to_cells(size or 0, sc)
    return cells


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
                               value=entry.get('source', 'augment'))
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
