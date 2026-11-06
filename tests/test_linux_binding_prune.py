"""Tests for Linux binding-aware property pruning."""

import yaml

from lopper.assists.linux_binding_prune_xlnx import (
    BindingIndex,
    allowed_properties_for_node,
    prune_linux_dt_bindings,
)
from lopper.tree import LopperNode, LopperTree


def _write_schema(tmp_path, name, schema):
    path = tmp_path / name
    path.write_text(yaml.safe_dump(schema), encoding="utf-8")
    return path


def test_documented_properties_defs_and_active_condition_are_kept(tmp_path):
    schema = {
        "$defs": {
            "common": {
                "properties": {
                    "defined-property": {"description": "optional but valid"},
                },
            },
        },
        "properties": {
            "compatible": {"const": "vendor,device"},
            "mode": {"enum": [1, 2]},
            "documented-property": {"description": "not required"},
        },
        "required": ["compatible"],
        "allOf": [
            {"$ref": "#/$defs/common"},
            {
                "if": {
                    "required": ["mode"],
                    "properties": {"mode": {"const": 1}},
                },
                "then": {
                    "properties": {
                        "then-property": {},
                        "forbidden-property": False,
                    },
                },
                "else": {
                    "properties": {
                        "else-property": {},
                    },
                },
            },
        ],
    }
    _write_schema(tmp_path, "device.yaml", schema)
    index = BindingIndex(str(tmp_path))
    node = LopperNode(-1, "/device@0")
    node["compatible"] = ["vendor,device"]
    node["mode"] = [1]

    allowed = allowed_properties_for_node(node, schema, index)

    assert {
        "compatible", "mode", "documented-property",
        "defined-property", "then-property",
    } <= allowed
    assert "else-property" not in allowed
    assert "forbidden-property" not in allowed


def test_anyof_condition_selects_then_properties(tmp_path):
    schema = {
        "properties": {
            "compatible": {"const": "vendor,device"},
            "mode": {},
        },
        "if": {
            "anyOf": [
                {"properties": {"mode": {"const": 1}}},
                {"properties": {"mode": {"const": 2}}},
            ],
        },
        "then": {"properties": {"then-property": {}}},
        "else": {"properties": {"else-property": {}}},
    }
    _write_schema(tmp_path, "device.yaml", schema)
    index = BindingIndex(str(tmp_path))
    node = LopperNode(-1, "/device@0")
    node["compatible"] = ["vendor,device"]
    node["mode"] = [2]

    allowed = allowed_properties_for_node(node, schema, index)

    assert "then-property" in allowed
    assert "else-property" not in allowed


def test_else_branch_is_selected_from_node_values(tmp_path):
    schema = {
        "properties": {
            "compatible": {"const": "vendor,device"},
            "mode": {},
        },
        "if": {
            "required": ["mode"],
            "properties": {"mode": {"const": 1}},
        },
        "then": {"properties": {"then-property": {}}},
        "else": {"properties": {"else-property": {}}},
    }
    _write_schema(tmp_path, "device.yaml", schema)
    index = BindingIndex(str(tmp_path))
    node = LopperNode(-1, "/device@0")
    node["compatible"] = ["vendor,device"]
    node["mode"] = [2]

    allowed = allowed_properties_for_node(node, schema, index)

    assert "else-property" in allowed
    assert "then-property" not in allowed


def test_prune_uses_parent_pattern_schema_and_leaves_unknown_nodes(tmp_path):
    schema = {
        "properties": {
            "compatible": {"const": "vendor,parent"},
            "parent-property": {},
        },
        "patternProperties": {
            "^channel@[0-9a-f]+$": {
                "type": "object",
                "properties": {
                    "compatible": {"const": "vendor,channel"},
                    "child-property": {},
                },
                "required": ["child-property"],
                "additionalProperties": False,
            },
        },
        "required": ["compatible"],
        "additionalProperties": False,
    }
    _write_schema(tmp_path, "parent.yaml", schema)

    tree = LopperTree()
    parent = LopperNode(-1, "/parent@0")
    parent["compatible"] = ["vendor,parent"]
    parent["parent-property"] = [1]
    parent["undocumented"] = [1]
    child = LopperNode(-1, "/parent@0/channel@1")
    child["compatible"] = ["vendor,channel"]
    child["child-property"] = [1]
    child["undocumented"] = [1]
    tree + parent
    tree + child

    unknown = LopperNode(-1, "/unknown@0")
    unknown["compatible"] = ["vendor,unknown"]
    unknown["custom-property"] = [1]
    unknown["xlnx,rable"] = [1]
    tree + unknown

    stats = prune_linux_dt_bindings(tree, str(tmp_path))

    assert parent.propval("parent-property", list) == [1]
    assert not parent.props("undocumented")
    assert child.propval("child-property", list) == [1]
    assert not child.props("undocumented")
    assert unknown.propval("custom-property", list) == [1]
    assert not unknown.props("xlnx,rable")
    assert stats["matched"] == 2
    assert stats["unknown"] == 1


def test_qdma_endpoint_is_disabled_and_rfdc_children_are_removed(tmp_path):
    _write_schema(tmp_path, "qdma.yaml", {
        "properties": {"compatible": {"const": "xlnx,qdma"}},
        "additionalProperties": False,
    })
    _write_schema(tmp_path, "rfdc.yaml", {
        "properties": {
            "compatible": {"const": "xlnx,usp-rf-data-converter-2.6"},
            "reg": {},
        },
        "additionalProperties": False,
    })

    tree = LopperTree()
    qdma = LopperNode(-1, "/qdma@0")
    qdma.label = "axi_qdma"
    qdma["compatible"] = ["xlnx,qdma"]
    qdma["status"] = ["okay"]
    qdma["xlnx,device_port_type"] = ["PCI_Express_Endpoint_device"]
    tree + qdma

    rfdc = LopperNode(-1, "/rfdc@0")
    rfdc["compatible"] = ["xlnx,usp-rf-data-converter-2.6"]
    rfdc["reg"] = [0]
    child = LopperNode(-1, "/rfdc@0/dac@0")
    child["xlnx,mixer-mode"] = [1]
    tree + rfdc
    tree + child

    prune_linux_dt_bindings(tree, str(tmp_path))

    assert qdma.propval("status", list) == ["disabled"]
    assert not qdma.props("xlnx,device_port_type")
    assert rfdc.propval("reg", list) == [0]
    assert not rfdc.child_nodes


def _qdma_and_rfdc_tree():
    tree = LopperTree()
    qdma = LopperNode(-1, "/qdma@0")
    qdma.label = "axi_qdma"
    qdma["compatible"] = ["xlnx,qdma"]
    qdma["status"] = ["okay"]
    qdma["xlnx,device_port_type"] = ["PCI_Express_Endpoint_device"]
    tree + qdma
    rfdc = LopperNode(-1, "/rfdc@0")
    rfdc["compatible"] = ["xlnx,usp-rf-data-converter-2.6"]
    rfdc["reg"] = [0]
    child = LopperNode(-1, "/rfdc@0/dac@0")
    child["xlnx,mixer-mode"] = [1]
    tree + rfdc
    tree + child
    return tree, qdma, rfdc, child


def test_missing_bindings_leave_qdma_and_rfdc_unchanged(tmp_path):
    tree, qdma, rfdc, child = _qdma_and_rfdc_tree()

    stats = prune_linux_dt_bindings(tree, str(tmp_path))

    assert stats["visited"] == 0
    assert qdma.propval("status", list) == ["okay"]
    assert qdma.propval("xlnx,device_port_type", list) == [
        "PCI_Express_Endpoint_device"]
    assert child.propval("xlnx,mixer-mode", list) == [1]
    assert rfdc.child_nodes


def test_rfdc_children_stay_when_its_schema_is_absent(tmp_path):
    _write_schema(tmp_path, "qdma.yaml", {
        "properties": {"compatible": {"const": "xlnx,qdma"}},
        "additionalProperties": False,
    })
    tree, qdma, rfdc, child = _qdma_and_rfdc_tree()

    prune_linux_dt_bindings(tree, str(tmp_path))

    assert qdma.propval("status", list) == ["disabled"]
    assert child.propval("xlnx,mixer-mode", list) == [1]
    assert rfdc.child_nodes
