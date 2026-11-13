#/*
# * Copyright (C) 2026 Advanced Micro Devices, Inc. All Rights Reserved.
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

"""Prune Linux DT properties by name against one schema per node.

A node is matched by its first indexed compatible, or by a child rule on
the parent schema. Property values are not checked for validity. They are
read only to choose if, anyOf, and oneOf branches, and the chosen branch
supplies property names for that node. A node with no schema keeps its
properties. SDT metadata is removed from every node.

The same pass then disables a QDMA PCI endpoint and prunes RFDC children
with the parent node's allowlist. The endpoint is recognized before
properties are deleted, because that pass can remove the property that
identifies it.
"""

from __future__ import annotations

import os
import re
import sys
from typing import Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import unquote, urlparse

import lopper

sys.path.append(os.path.dirname(__file__))
import common_utils as utils


# Generic properties supplied by common Linux schemas.  Keep these when the
# corresponding common schema is not shipped beside the vendor bindings.
_LINUX_COMMON_PROPS = frozenset({
    "compatible", "status", "reg", "reg-names",
    "interrupts", "interrupts-extended", "interrupt-parent",
    "interrupt-names", "interrupt-controller", "#interrupt-cells",
    "#address-cells", "#size-cells", "#clock-cells",
    "clocks", "clock-names", "clock-frequency",
    "resets", "reset-names",
    "assigned-clocks", "assigned-clock-rates", "assigned-clock-parents",
    "dma-coherent", "dmas", "dma-names",
    "phys", "phy-names", "phy-mode", "phy-handle",
    "power-domains", "mboxes", "mbox-names",
    "ranges", "dma-ranges", "name", "phandle", "linux,phandle",
    "device_type", "model",
    "bootph-all", "bootph-pre-sram", "bootph-pre-ram",
    "pinctrl-0", "pinctrl-1", "pinctrl-names",
    "reset-gpios", "scl-gpios", "sda-gpios", "cs-gpios", "cd-gpios",
})

# Present on SDT nodes for generation. Not part of a Linux binding.
_SDT_METADATA = frozenset({
    "xlnx,is-hierarchy", "xlnx,rable", "xlnx,interconnect-s-axi-masters",
    "xlnx,cpu-name", "memory_type",
})

_SKIP_PATHS = frozenset({
    "/", "/chosen", "/aliases", "/__symbols__", "/__lopper-phandles__",
})


def _iter_values(value, key: str) -> Iterable:
    """Yield every nested value stored under ``key``.

    A dict entry whose name is ``key`` is yielded as a whole. Other dict
    entries and every list element are searched further.
    """
    if isinstance(value, dict):
        for name, child in value.items():
            if name == key:
                yield child
            else:
                yield from _iter_values(child, key)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_values(child, key)


def _schema_compatibles(schema: dict) -> List[str]:
    """Return compatible strings from the top-level compatible property.

    Nested const and enum values are included. Compatibles declared only
    on a child node are not.
    """
    compatible = schema.get("properties", {}).get("compatible", {})
    if not isinstance(compatible, dict):
        return []

    values: List[str] = []
    for key in ("const", "enum"):
        for item in _iter_values(compatible, key):
            if isinstance(item, list):
                values.extend(str(v) for v in item)
            elif isinstance(item, str):
                values.append(item)
    return list(dict.fromkeys(values))


def _node_values(node, prop: str) -> List:
    """Return the property cells, or an empty list when it is absent."""
    if prop not in node.__props__:
        return []
    value = node.propval(prop, list)
    return [] if value == [""] else list(value)


def _node_instance(node) -> Dict[str, object]:
    """Return the node's properties as a name-to-cells mapping."""
    return {name: _node_values(node, name) for name in node.__props__}


def _scalar_or_values(value):
    """Return the single cell of a one-cell list, or the value unchanged."""
    if isinstance(value, list) and len(value) == 1:
        return value[0]
    return value


def _matches_value(value, schema) -> bool:
    """Return true when a property value satisfies a branch constraint.

    Callers use the result to select an if, anyOf, or oneOf branch. A match
    does not keep the property, and a mismatch does not remove it. type is
    not tested. A one-cell list is compared as its single value. minimum
    and maximum are inclusive; a value that cannot be ordered against the
    bound does not match.
    """
    if schema is True:
        return True
    if schema is False or not isinstance(schema, dict):
        return False

    if "allOf" in schema and not all(
            _matches_value(value, branch) for branch in schema["allOf"]):
        return False
    if "anyOf" in schema and not any(
            _matches_value(value, branch) for branch in schema["anyOf"]):
        return False
    if "oneOf" in schema and sum(
            _matches_value(value, branch) for branch in schema["oneOf"]) != 1:
        return False
    if "not" in schema and _matches_value(value, schema["not"]):
        return False

    scalar = _scalar_or_values(value)
    if "const" in schema and scalar != schema["const"]:
        return False
    if "enum" in schema and scalar not in schema["enum"]:
        return False

    if "contains" in schema:
        values = value if isinstance(value, list) else [value]
        if not any(_matches_value(item, schema["contains"]) for item in values):
            return False

    item_schemas = schema.get("items")
    if isinstance(item_schemas, list):
        values = value if isinstance(value, list) else [value]
        if len(values) < len(item_schemas):
            return False
        if not all(_matches_value(values[i], item)
                   for i, item in enumerate(item_schemas)):
            return False
    elif isinstance(item_schemas, dict) and isinstance(value, list):
        if not all(_matches_value(item, item_schemas) for item in value):
            return False

    try:
        if "minimum" in schema and scalar < schema["minimum"]:
            return False
        if "maximum" in schema and scalar > schema["maximum"]:
            return False
    except TypeError:
        return False

    if "pattern" in schema:
        try:
            if re.search(schema["pattern"], str(scalar)) is None:
                return False
        except re.error:
            return False
    return True


def _matches_instance(instance: Dict[str, object], schema) -> bool:
    """Return true when a node satisfies an if, anyOf, or oneOf condition.

    Every required name must be present. A properties entry is tested with
    _matches_value only when the node has that property.
    """
    if schema is True:
        return True
    if schema is False or not isinstance(schema, dict):
        return False

    if "allOf" in schema and not all(
            _matches_instance(instance, branch) for branch in schema["allOf"]):
        return False
    if "anyOf" in schema and not any(
            _matches_instance(instance, branch) for branch in schema["anyOf"]):
        return False
    if "oneOf" in schema and sum(
            _matches_instance(instance, branch)
            for branch in schema["oneOf"]) != 1:
        return False
    if "not" in schema and _matches_instance(instance, schema["not"]):
        return False

    if not all(name in instance for name in schema.get("required", [])):
        return False

    properties = schema.get("properties", {})
    if isinstance(properties, dict):
        for name, constraint in properties.items():
            if name in instance and not _matches_value(instance[name], constraint):
                return False
    return True


class BindingIndex:
    """Index every YAML binding by path, $id, and compatible.

    The last file to declare a compatible replaces any earlier one.
    schema_for returns the schema for the first compatible on the node
    that is present in this index.
    """

    def __init__(self, binding_dir: str) -> None:
        """Load every YAML binding under ``binding_dir``."""
        self.binding_dir = os.path.realpath(binding_dir)
        self.by_compat: Dict[str, dict] = {}
        self.by_path: Dict[str, dict] = {}
        self.by_id: Dict[str, dict] = {}
        self.schema_paths: Dict[int, str] = {}
        self.unresolved_refs: Set[str] = set()
        self._load()

    def _load(self) -> None:
        """Index each schema by its path, its ``$id``, and its compatibles.

        A directory that does not exist leaves the index empty. A compatible
        declared by a later file replaces the schema stored for that string.
        """
        if not os.path.isdir(self.binding_dir):
            return
        for root, _dirs, files in os.walk(self.binding_dir):
            for filename in files:
                if not filename.endswith((".yaml", ".yml")):
                    continue
                path = os.path.realpath(os.path.join(root, filename))
                schema = utils.load_yaml(path)
                if not isinstance(schema, dict):
                    continue
                self.by_path[path] = schema
                self.schema_paths[id(schema)] = path
                schema_id = schema.get("$id")
                if isinstance(schema_id, str):
                    self.by_id[schema_id.split("#", 1)[0]] = schema
                for compatible in _schema_compatibles(schema):
                    self.by_compat[compatible] = schema

    def schema_for(self, compatibles: Iterable[str]) -> Optional[dict]:
        """Return the schema for the first compatible that this index has."""
        for compatible in compatibles:
            if compatible in self.by_compat:
                return self.by_compat[compatible]
        return None

    @staticmethod
    def _pointer(document, fragment: str):
        """Return the JSON Pointer ``fragment`` inside ``document``.

        An empty fragment is the document. A fragment that does not start
        with ``/``, or that names a missing key, returns None.
        """
        current = document
        if not fragment:
            return current
        if fragment.startswith("/"):
            for token in fragment[1:].split("/"):
                token = unquote(token).replace("~1", "/").replace("~0", "~")
                if not isinstance(current, dict) or token not in current:
                    return None
                current = current[token]
            return current
        return None

    def resolve_ref(self, ref: str, current_schema: dict):
        """Return the referenced fragment and the document that holds it.

        A fragment-only ref is resolved in ``current_schema``. Any other
        ref is found by ``$id``, then by a path under the binding directory,
        then by a filename that occurs once in the index. A document that
        cannot be found returns ``(None, None)``. A document that is found
        but whose fragment is missing returns ``(None, document)``.
        """
        if not isinstance(ref, str):
            return None, None
        document_ref, _, fragment = ref.partition("#")
        document = current_schema
        if document_ref:
            document = self.by_id.get(document_ref)
            if document is None:
                parsed = urlparse(document_ref)
                candidate = parsed.path if parsed.scheme else document_ref
                if candidate.startswith("/schemas/"):
                    candidate = candidate[len("/schemas/"):]
                    candidate = os.path.join(self.binding_dir, candidate)
                elif os.path.isabs(candidate):
                    candidate = os.path.join(
                        self.binding_dir, candidate.lstrip("/"))
                else:
                    current_path = self.schema_paths.get(
                        id(current_schema),
                        os.path.join(self.binding_dir, "_schema.yaml"))
                    candidate = os.path.join(
                        os.path.dirname(current_path), candidate)
                document = self.by_path.get(os.path.realpath(candidate))
            if document is None:
                basename = os.path.basename(urlparse(document_ref).path)
                matches = [
                    schema for path, schema in self.by_path.items()
                    if os.path.basename(path) == basename
                ]
                if len(matches) == 1:
                    document = matches[0]
        if document is None:
            return None, None
        return self._pointer(document, fragment), document

    @staticmethod
    def child_schema(parent_schema: dict, child_name: str) -> Optional[dict]:
        """Return the parent rule that describes a child of this name.

        A same-named ``properties`` entry of type object wins. Otherwise
        the first ``patternProperties`` pattern that matches the name is
        used. An invalid pattern is skipped.
        """
        properties = parent_schema.get("properties", {})
        if isinstance(properties, dict):
            direct = properties.get(child_name)
            if isinstance(direct, dict) and direct.get("type") == "object":
                return direct
        patterns = parent_schema.get("patternProperties", {})
        if isinstance(patterns, dict):
            for pattern, child_schema in patterns.items():
                try:
                    if re.search(pattern, child_name):
                        return child_schema
                except re.error:
                    continue
        return None


def _merge_declared(fragment: dict, allowed: Set[str],
                    forbidden: Set[str]) -> None:
    """Add this fragment's property names to ``allowed``.

    ``required`` names and ``properties`` names are allowed. A property
    whose schema is false is moved to ``forbidden``. A later allowed
    declaration of the same name removes it from ``forbidden``.
    """
    required = fragment.get("required", [])
    if isinstance(required, list):
        allowed.update(str(name) for name in required)

    properties = fragment.get("properties", {})
    if not isinstance(properties, dict):
        return
    for name, property_schema in properties.items():
        if property_schema is False:
            forbidden.add(name)
            allowed.discard(name)
        else:
            allowed.add(name)
            forbidden.discard(name)


def _apply_schema(instance: Dict[str, object], fragment, index: BindingIndex,
                  root_schema: dict, allowed: Set[str],
                  forbidden: Set[str], resolving: Set[Tuple[int, str]]) -> None:
    """Collect property names from one schema fragment.

    A ``$ref`` is followed once per schema and ref while this walk is
    inside it. A missing external ref is recorded and contributes no
    names. ``allOf`` contributes every branch. ``anyOf`` contributes the
    branches the node matches, or every branch when none match. ``oneOf``
    contributes the single matching branch, or every branch otherwise.
    ``if`` contributes ``then`` or ``else`` from the node values. ``not``
    forbids a property whose schema is true, and a ``required`` list of
    one name.
    """
    if fragment is True or not isinstance(fragment, dict):
        return

    ref = fragment.get("$ref")
    if isinstance(ref, str):
        marker = (id(root_schema), ref)
        if marker not in resolving:
            target, target_root = index.resolve_ref(ref, root_schema)
            if isinstance(target, dict):
                resolving.add(marker)
                _apply_schema(instance, target, index, target_root,
                              allowed, forbidden, resolving)
                resolving.remove(marker)
            elif target is None and not ref.startswith("#"):
                # A schema we do not ship cannot contribute its properties,
                # so anything it documents would be silently pruned.
                index.unresolved_refs.add(ref)

    _merge_declared(fragment, allowed, forbidden)

    for branch in fragment.get("allOf", []):
        _apply_schema(instance, branch, index, root_schema,
                      allowed, forbidden, resolving)

    for keyword in ("anyOf", "oneOf"):
        branches = fragment.get(keyword, [])
        if not isinstance(branches, list) or not branches:
            continue
        matched = [
            branch for branch in branches
            if _matches_instance(instance, branch)
        ]
        # oneOf needs exactly one match. anyOf needs at least one.
        # Anything else keeps every branch so properties are not dropped.
        if keyword == "oneOf":
            selected = matched if len(matched) == 1 else branches
        else:
            selected = matched or branches
        for branch in selected:
            _apply_schema(instance, branch, index, root_schema,
                          allowed, forbidden, resolving)

    condition = fragment.get("if")
    if isinstance(condition, dict):
        branch = fragment.get(
            "then" if _matches_instance(instance, condition) else "else")
        _apply_schema(instance, branch, index, root_schema,
                      allowed, forbidden, resolving)

    negated = fragment.get("not")
    if isinstance(negated, dict):
        properties = negated.get("properties", {})
        if isinstance(properties, dict):
            for name, property_schema in properties.items():
                if property_schema is True:
                    forbidden.add(name)
                    allowed.discard(name)
        required = negated.get("required", [])
        if isinstance(required, list) and len(required) == 1:
            forbidden.add(required[0])
            allowed.discard(required[0])


def allowed_properties_for_node(node, schema: Optional[dict],
                                index: BindingIndex) -> Set[str]:
    """Return the property names this node may keep.

    The set starts with the common Linux properties. Names from
    ``schema`` are added and forbidden names are removed. With no schema
    the result is only the common set.
    """
    allowed = set(_LINUX_COMMON_PROPS)
    forbidden: Set[str] = set()
    if schema:
        _apply_schema(_node_instance(node), schema, index, schema,
                      allowed, forbidden, set())
    return allowed - forbidden


def _compatibles(node) -> List[str]:
    """Return the node's compatible strings, in property order."""
    return [str(value) for value in _node_values(node, "compatible")]


def _schema_for_node(node, index: BindingIndex,
                     schemas_by_path: Dict[str, Optional[dict]]) -> Optional[dict]:
    """Return this node's schema.

    The node's own compatible is tried first. When none is indexed, the
    parent's already chosen schema is asked for a child rule under this
    node's name.
    """
    schema = index.schema_for(_compatibles(node))
    if schema:
        return schema

    parent = getattr(node, "parent", None)
    parent_schema = schemas_by_path.get(
        parent.abs_path) if parent is not None else None
    if parent_schema:
        return index.child_schema(parent_schema, node.name)
    return None


def _walk(node) -> Iterable:
    """Yield each descendant, visiting a node before its children."""
    for child in list(node.child_nodes.values()):
        yield child
        yield from _walk(child)


def _strip_metadata(node, stats: dict) -> None:
    """Delete SDT metadata properties and count each deletion."""
    for name in list(node.__props__):
        if name in _SDT_METADATA:
            node.delete(name)
            stats["sdt_stripped"] += 1


def _prune_node(node, allowed: Set[str], stats: dict) -> None:
    """Delete metadata and any property whose name is outside ``allowed``."""
    for name in list(node.__props__):
        if name in _SDT_METADATA:
            node.delete(name)
            stats["sdt_stripped"] += 1
        elif name not in allowed:
            node.delete(name)
            stats["props_pruned"] += 1


_RFDC_COMPATIBLE = "xlnx,usp-rf-data-converter-2.6"


def _is_qdma_endpoint(node) -> bool:
    """Return true when the node is a QDMA PCI endpoint.

    The label contains ``qdma`` and ``xlnx,device_port_type`` is the
    endpoint value. Callers read that property before the schema pass,
    which can delete it.
    """
    label = node.label or ""
    return ("qdma" in label and
            node.propval("xlnx,device_port_type") ==
            ["PCI_Express_Endpoint_device"])


def _is_rfdc(node) -> bool:
    """Return true when the node is an RF data converter."""
    return _RFDC_COMPATIBLE in node.propval("compatible", list)


def _disable_qdma_endpoint(node) -> None:
    """Set a QDMA PCI endpoint status from okay to disabled."""
    status = node.propval("status", list)
    if status and status != [""] and "okay" in str(status[0]):
        status[0] = "disabled"


def _prune_rfdc_children(node, allowed: Set[str]) -> None:
    """Prune every descendant against the RFDC parent's allowlist.

    Children are visited first. A property whose name is outside
    ``allowed`` is deleted. A child that then has no properties and no
    children is deleted. The node this was called on is left in place.
    """
    for child in list(node.child_nodes.values()):
        _prune_rfdc_children(child, allowed)
        for name in list(child.__props__):
            if name not in allowed:
                child.delete(name)
        if not child.__props__ and not child.child_nodes:
            node.delete(child)


def _apply_ip_fixups(qdma_endpoints, rfdc_nodes, index: BindingIndex) -> None:
    """Disable recorded QDMA endpoints and prune RFDC children.

    Each RFDC node's children use the allowlist of that node's own schema.
    An RFDC node whose schema was not loaded keeps its children.
    """
    for node in qdma_endpoints:
        _disable_qdma_endpoint(node)
    for node in rfdc_nodes:
        compat = node.propval("compatible", list)
        compat = [] if compat == [""] else compat
        schema = index.schema_for(compat)
        if schema is None:
            continue
        allowed = allowed_properties_for_node(node, schema, index)
        _prune_rfdc_children(node, allowed)


def prune_linux_dt_bindings(tree, binding_dir: str) -> dict:
    """Remove properties whose names the node's own schema does not declare.

    The allowlist is the common Linux property set plus names from that
    one schema, including the selected branches and any resolved $ref.
    It is not the union of every binding. An unresolved external $ref
    contributes no names, so properties documented only there are removed.
    A node with no matching schema is left unchanged except for SDT
    metadata, which is removed from every node.

    QDMA PCI endpoints and RFDC nodes are recorded during the walk. After
    the schema pass, an endpoint whose status is okay is set to disabled,
    and each RFDC node whose schema was loaded has its children pruned
    with that allowlist. A missing binding directory leaves the tree
    unchanged.
    """
    stats = {
        "visited": 0, "matched": 0, "unknown": 0,
        "props_pruned": 0, "sdt_stripped": 0,
    }
    index = BindingIndex(binding_dir)
    if not index.by_compat:
        lopper.log._warning(
            f"binding prune: no YAML bindings in {binding_dir}")
        return stats

    qdma_endpoints = []
    rfdc_nodes = []
    schemas_by_path: Dict[str, Optional[dict]] = {}
    for node in _walk(tree["/"]):
        if node.abs_path in _SKIP_PATHS:
            continue
        if _is_qdma_endpoint(node):
            qdma_endpoints.append(node)
        if _is_rfdc(node):
            rfdc_nodes.append(node)
        stats["visited"] += 1
        schema = _schema_for_node(node, index, schemas_by_path)
        schemas_by_path[node.abs_path] = schema
        if schema:
            stats["matched"] += 1
            allowed = allowed_properties_for_node(node, schema, index)
            _prune_node(node, allowed, stats)
        else:
            stats["unknown"] += 1
            _strip_metadata(node, stats)

    lopper.log._info(
        f"binding prune: matched={stats['matched']} "
        f"unknown={stats['unknown']} props_removed="
        f"{stats['props_pruned'] + stats['sdt_stripped']}")
    if index.unresolved_refs:
        lopper.log._warning(
            "linux_dt binding prune: unresolved $ref(s), properties they "
            "document were pruned: "
            + ", ".join(sorted(index.unresolved_refs)))
    stats["unresolved_refs"] = sorted(index.unresolved_refs)
    _apply_ip_fixups(qdma_endpoints, rfdc_nodes, index)
    return stats
