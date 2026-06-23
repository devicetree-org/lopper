#/*
# * Copyright (c) 2026 AMD Inc. All rights reserved.
# *
# * Author:
# *       Bruce Ashfield <bruce.ashfield@amd.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

"""Shared Xen passthrough conversion logic.

Single source of truth for turning system-device-tree device nodes into Xen
dom0less passthrough device tree fragments. Used by:

  - image-builder --gen-config : builds a per-guest passthrough overlay inline
    (single pass) from a dom0less guest's access list.
  - extract-xen                : the standalone two-pass CLI, which collects
    nodes into sdt.subtrees["extracted"] via the `extract` assist and then
    runs them through the same conversion here.

The conversion (xenify_node / xenify_iommus) and the device-tree it produces
are identical regardless of entry point.
"""

import re

import lopper
import lopper.log
from lopper.tree import LopperTree, LopperNode, LopperProp

lopper.log._init(__name__)

# Xen dom0less guests see a synthetic GIC at this phandle. Xen rewrites
# interrupt-parent of passed-through devices to point at the guest's GIC, not
# the host's. Historically this was the bare literal 0xfde8 in extract-xen.py;
# named here so the meaning is discoverable.
GUEST_GIC_PHANDLE = 0xfde8


def _first_or_none(value):
    """propval/value helpers return lists; collapse to a scalar or None."""
    if value is None:
        return None
    if isinstance(value, list):
        if not value or value == ['']:
            return None
        return value[0]
    return value


def collect_device_nodes(device_nodes):
    """Build a fresh /passthrough LopperTree from copies of device_nodes.

    Each device node and its phandle dependencies are *copied* (not moved) into
    a new tree, so the source SDT is left structurally intact. Mirrors the node
    gathering that the `extract` assist does into sdt.subtrees["extracted"],
    but in-memory and without the cross-pass handoff.

    Returns the new LopperTree (unresolved; caller establishes overlay
    relationship + resolves).
    """
    extracted_tree = LopperTree()
    root = extracted_tree["/"]
    root["#address-cells"] = 2
    root["#size-cells"] = 2

    container = LopperNode(-1, "/passthrough")
    container["compatible"] = "simple-bus"
    container["ranges"] = None
    container["#address-cells"] = 2
    container["#size-cells"] = 2
    extracted_tree = extracted_tree + container

    seen = set()
    for dev in device_nodes:
        refs = dev.resolve_all_refs(parents=False)
        for r in refs:
            if r == dev.parent:
                continue
            if r.abs_path in seen:
                continue
            seen.add(r.abs_path)
            copy_node = r()
            container + copy_node
            # record where this came from; xenify_node promotes it to xen,path
            copy_node["extracted,path"] = r.abs_path

    return extracted_tree


def xenify_iommus(node, sdt):
    """Translate a device's iommus = <&smmu stream-id...> into Xen SMMU bindings.

    Xen needs each passed-through device's SMMU stream IDs so it can program the
    SMMU on the guest's behalf. The SDT expresses these as
    `iommus = <&smmu sid0 [sid1 ...]>`, where the number of cells per entry is
    the smmu node's #iommu-cells.

    Emits:
      - xen,smmu-stream-ids = <sid0 sid1 ...>   (the extracted stream IDs)
    and removes the host `iommus` property (its phandle points at the host
    smmu, which the guest passthrough tree does not contain).

    Returns (emitted, smmu_paths):
      emitted    True if stream IDs were emitted, False if no usable iommus
                 (caller then applies xen,force-assign-without-iommu).
      smmu_paths list of host smmu node abs_paths the iommus referenced; the
                 caller should prune these from the passthrough tree (a Xen
                 guest fragment carries stream-ids, not the host smmu node).
    """
    try:
        iommus = node["iommus"]
    except Exception:
        return (False, [])

    iommus_val = iommus.value
    if not iommus_val or iommus_val == ['']:
        return (False, [])

    # Resolve the referenced smmu node(s) to read #iommu-cells. Each iommus
    # entry is <phandle, sid...> with sid width = smmu's #iommu-cells.
    stream_ids = []
    smmu_paths = []
    idx = 0
    n = len(iommus_val)
    while idx < n:
        phandle = iommus_val[idx]
        smmu = sdt.tree.pnode(phandle)
        cells = 1
        if smmu is not None:
            if smmu.abs_path not in smmu_paths:
                smmu_paths.append(smmu.abs_path)
            c = _first_or_none(smmu.propval("#iommu-cells"))
            if c is not None:
                cells = int(c)
        idx += 1
        for _ in range(cells):
            if idx < n:
                stream_ids.append(int(iommus_val[idx]))
                idx += 1

    if not stream_ids:
        return (False, smmu_paths)

    sid_prop = LopperProp("xen,smmu-stream-ids")
    sid_prop.value = stream_ids
    node + sid_prop

    # Drop the host iommus property — its phandle target isn't in the guest tree
    try:
        node - iommus
    except Exception:
        pass

    return (True, smmu_paths)


def xenify_node(node, sdt, is_target=False):
    """Apply the Xen passthrough conversions to a single copied node.

    Mirrors the per-node logic previously inlined in extract-xen.py:
      - extracted,path -> xen,path  (when the node has iommus, or is_target)
      - interrupt-parent -> GUEST_GIC_PHANDLE (non-resolving literal)
      - reg -> xen,reg  (<phys size guest_addr>, guest_addr == phys)
      - iommus -> xen,smmu-stream-ids, or xen,force-assign-without-iommu
    """
    # interrupt-parent rewrite to the guest GIC
    try:
        ip = node["interrupt-parent"]
        ip.value = GUEST_GIC_PHANDLE
        ip.phandle_resolution = False
        ip.resolve(strict=False)
        lopper.log._info(f"{node.name}: interrupt-parent retargeted to guest GIC")
    except Exception:
        pass

    has_iommus = False
    try:
        node["iommus"]
        has_iommus = True
    except Exception:
        has_iommus = False

    # extracted,path -> xen,path
    try:
        p = node["extracted,path"]
        if has_iommus or is_target:
            p.name = "xen,path"
    except Exception:
        pass

    # SMMU stream-ids, or force-assign fallback
    emitted_sids, smmu_paths = xenify_iommus(node, sdt)
    if not emitted_sids and is_target:
        fa = LopperProp("xen,force-assign-without-iommu")
        fa.value = 1
        node + fa

    # reg -> xen,reg : <phys_addr size guest_addr> with guest_addr == phys_addr
    try:
        reg = node["reg"]
        xen_reg = LopperProp("xen,reg")
        reg_chunks = [reg.value[x:x + 4] for x in range(0, len(reg.value), 4)]
        for chunk in reg_chunks:
            if len(chunk) < 4:
                continue
            addr = [chunk[0], chunk[1]]
            size = [chunk[2], chunk[3]]
            xen_reg.value.extend(addr)
            xen_reg.value.extend(size)
            xen_reg.value.extend(addr)
        node + xen_reg
    except Exception as e:
        lopper.log._debug(f"{node.name}: no reg to convert: {e}")

    return smmu_paths


def build_passthrough_overlay(sdt, device_nodes, guest_name, target_names=None):
    """Build a standalone Xen passthrough device tree for a guest's devices.

    1. Copy device_nodes + phandle deps into a fresh /passthrough tree
       (source SDT untouched — nodes are copied, not moved).
    2. xenify each copied node.
    3. Link the tree to the base SDT for phandle/label resolution only
       (link_for_resolution), NOT overlay_of: the output is a standalone
       partial DT that Xen loads as DOMU_PASSTHROUGH_DTB, not a dtc overlay.

    target_names: optional set of node names treated as the primary
    passthrough targets (forces xen,path + force-assign fallback). If None,
    every copied device node is treated as a target.

    Returns the resolved passthrough LopperTree.

    (Name retains the historical "overlay" suffix for call-site stability; the
    artifact is a standalone tree, not a DT overlay.)
    """
    tree = collect_device_nodes(device_nodes)

    container = tree["/passthrough"]
    # copy sdt root compatibles onto the container (matches extract-xen)
    try:
        root_compat = list(sdt.tree["/"]["compatible"].value)
        root_compat.append(container["compatible"].value)
        container["compatible"].value = root_compat
    except Exception:
        pass

    smmu_paths_to_prune = []
    for n in tree:
        if n.abs_path in ("/", "/passthrough"):
            continue
        is_target = True if target_names is None else (n.name in target_names)
        smmu_paths = xenify_node(n, sdt, is_target=is_target)
        for p in smmu_paths:
            if p not in smmu_paths_to_prune:
                smmu_paths_to_prune.append(p)

    # A Xen guest passthrough fragment carries stream-ids, not the host smmu
    # node itself. resolve_all_refs pulled the smmu in via the iommus phandle;
    # remove it (matched by basename, since it sits under /passthrough now).
    smmu_basenames = set(p.split('/')[-1] for p in smmu_paths_to_prune)
    for n in list(tree):
        if n.abs_path in ("/", "/passthrough"):
            continue
        if n.name in smmu_basenames:
            try:
                tree - n
            except Exception:
                pass

    # We want a *standalone* /passthrough device tree (Xen loads it directly as
    # DOMU_PASSTHROUGH_DTB), not a dtc overlay — so we do NOT call overlay_of().
    # The base SDT stays whole because the nodes above were copied, not moved.
    # We only need cross-references (clocks, etc.) to resolve against the base,
    # which is the resolution half of overlay_of(), factored out as
    # link_for_resolution().
    tree.link_for_resolution(sdt.tree)
    tree.strict = False
    tree.resolve()
    return tree


def mark_source_passthrough(sdt, device_paths):
    """Stamp xen,passthrough on the source devices in the base SDT.

    Done as a direct property add on the base tree (the device must carry
    xen,passthrough so the host knows it is delegated). Kept minimal: a single
    empty-valued property per device.
    """
    for path in device_paths:
        try:
            node = sdt.tree[path]
        except Exception:
            continue
        try:
            node["xen,passthrough"]
            continue  # already marked
        except Exception:
            pass
        xp = LopperProp("xen,passthrough")
        xp.value = ""
        node + xp
