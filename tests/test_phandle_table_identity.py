# Copyright (c) 2026 Advanced Micro Devices, Inc. All Rights Reserved.
# Author: Bruce Ashfield <bruce.ashfield@amd.com>
# SPDX-License-Identifier: BSD-3-Clause
"""
The phandle property table has to be one table.

It is a class variable on lopper_base, inherited by whichever concrete class
is selected (LopperFDT or LopperDT). Inheriting shares the object; *assigning*
through the subclass does not write through, it creates a shadowing attribute
on the subclass. After that, a lookup taken through the subclass and a
property learned from a scanned source address two different dictionaries.

That is not cosmetic. Phandle rendering asks the table whether a property
holds phandles, and a property missing from the table it happens to consult
is written out as a bare number rather than as <&label>. The split is silent
and permanent for the life of the process, so everything learned after the
first assignment is lost.

The rule these tests pin: nothing rebinds the table, it is only ever mutated.
"""

import pytest

import lopper.base as lb


@pytest.fixture
def table():
    """The live table, restored afterwards so tests do not leak into each other."""
    t = lb.lopper_base.phandle_possible_prop_dict
    saved = dict(t)
    yield t
    t.clear()
    t.update(saved)


class TestTheTableIsShared:
    def test_a_subclass_sees_the_same_object(self):
        """Reading through a subclass must find the base's table, not a copy."""
        import lopper.fdt
        assert lopper.fdt.LopperFDT.phandle_possible_prop_dict is \
               lb.lopper_base.phandle_possible_prop_dict, \
            "the concrete class has its own table; lookups and learning have split"

    def test_learning_is_visible_through_the_subclass(self, table):
        """A property learned after setup has to be visible where it is read."""
        import lopper.fdt
        lb.lopper_base.update_phandle_property_descriptions(
            {"test-only-endpoint": ["phandle", 0]})
        assert "test-only-endpoint" in \
               lopper.fdt.LopperFDT.phandle_possible_properties(), \
            "a learned property is not visible through the class that renders it"


class TestLearningDoesNotRebind:
    def test_the_table_object_survives_learning(self, table):
        """update_phandle_property_descriptions must not swap the table out."""
        before = id(table)
        lb.lopper_base.update_phandle_property_descriptions(
            {"test-only-prop-a": ["phandle", 0]})
        after = id(lb.lopper_base.phandle_possible_prop_dict)
        assert before == after, \
            "learning replaced the table rather than adding to it"

    def test_learning_adds_the_property(self, table):
        lb.lopper_base.update_phandle_property_descriptions(
            {"test-only-prop-b": ["phandle", 0]})
        assert table.get("test-only-prop-b") == ["phandle", 0]

    def test_an_existing_description_is_left_alone(self, table):
        table["test-only-prop-c"] = ["phandle", 0]
        lb.lopper_base.update_phandle_property_descriptions(
            {"test-only-prop-c": ["something-else", 1]})
        assert table["test-only-prop-c"] == ["phandle", 0], \
            "learning overwrote a description it should have left alone"

    def test_lazy_initialisation_fills_the_real_table(self, table):
        """An empty table takes the defaults into itself, not into a copy."""
        table.clear()
        before = id(lb.lopper_base.phandle_possible_prop_dict)
        lb.lopper_base.update_phandle_property_descriptions(
            {"test-only-prop-d": ["phandle", 0]})
        after = lb.lopper_base.phandle_possible_prop_dict
        assert id(after) == before, "lazy init replaced the table"
        assert "test-only-prop-d" in after
        assert len(after) > 1, \
            "the defaults were not folded into the table on lazy init"
