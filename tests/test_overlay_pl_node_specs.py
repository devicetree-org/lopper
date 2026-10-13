# Copyright (c) 2026 Advanced Micro Devices, Inc. All Rights Reserved.
# Author: Bruce Ashfield <bruce.ashfield@amd.com>
# SPDX-License-Identifier: BSD-3-Clause
"""
--nodes= property selection has to be able to name a vendor-prefixed property.

A comma is legal inside a device tree property name, and the vendor prefix
convention puts one in nearly every property anyone would want to select:
xlnx,dc-operating-mode is one property, not two. Splitting the property list
on commas therefore could not express those names at all -- the selection
silently resolved to two properties that do not exist, and nothing was
emitted.

A colon cannot appear in a property name, so it separates them unambiguously.
The comma is still honoured when no colon is present, since specs were
written against that syntax, but 'a,b' genuinely cannot be told apart from a
single prefixed name, so that reading is reported rather than assumed.
"""

import pytest

from lopper.assists.xlnx_overlay_pl_dt import parse_node_specs


class TestWholeNodeSpecs:
    def test_a_path_selects_the_whole_node(self):
        assert parse_node_specs('/amba_pl') == [
            {'node': '/amba_pl', 'properties': None}]

    def test_a_label_selects_the_whole_node(self):
        assert parse_node_specs('mmi_dc') == [
            {'node': 'mmi_dc', 'properties': None}]

    def test_semicolons_separate_node_specs(self):
        assert parse_node_specs('/amba_pl;phy0') == [
            {'node': '/amba_pl', 'properties': None},
            {'node': 'phy0', 'properties': None}]


class TestVendorPrefixedProperties:
    """The case the colon separator exists for."""

    def test_a_single_vendor_property_is_one_name(self):
        assert parse_node_specs('mmi_dc#xlnx,dc-operating-mode:') == [
            {'node': 'mmi_dc', 'properties': ['xlnx,dc-operating-mode']}]

    def test_two_vendor_properties_keep_their_commas(self):
        spec = 'mmi_dc#xlnx,dc-operating-mode:xlnx,dc-presentation-mode'
        assert parse_node_specs(spec) == [
            {'node': 'mmi_dc', 'properties': ['xlnx,dc-operating-mode',
                                              'xlnx,dc-presentation-mode']}]

    def test_a_vendor_property_mixed_with_a_plain_one(self):
        spec = 'mmi_dc#clocks:xlnx,dc-operating-mode'
        assert parse_node_specs(spec) == [
            {'node': 'mmi_dc', 'properties': ['clocks',
                                              'xlnx,dc-operating-mode']}]

    def test_colon_form_combines_with_semicolons(self):
        spec = 'mmi_dc#xlnx,dc-operating-mode:clocks;phy0'
        assert parse_node_specs(spec) == [
            {'node': 'mmi_dc', 'properties': ['xlnx,dc-operating-mode',
                                              'clocks']},
            {'node': 'phy0', 'properties': None}]


class TestCommaFormStillWorks:
    """Specs written before the colon separator existed."""

    def test_comma_separated_plain_properties(self):
        assert parse_node_specs('mmi_dc#clocks,clock-names') == [
            {'node': 'mmi_dc', 'properties': ['clocks', 'clock-names']}]

    def test_a_single_property_needs_no_separator_at_all(self):
        assert parse_node_specs('mmi_dc#clocks') == [
            {'node': 'mmi_dc', 'properties': ['clocks']}]

    def test_comma_form_combines_with_semicolons(self):
        assert parse_node_specs('/amba_pl;mmi_dc#clocks,clock-names') == [
            {'node': '/amba_pl', 'properties': None},
            {'node': 'mmi_dc', 'properties': ['clocks', 'clock-names']}]


class TestTheAmbiguousReadingIsReported:
    """'a,b' is two properties or one prefixed name, and nothing says which.

    Taking a reading silently is how a selection that named one real property
    came out as two that do not exist, emitted nothing, and reported nothing.
    """

    def test_a_comma_list_warns(self, caplog):
        parse_node_specs('mmi_dc#clocks,clock-names')
        assert "separate properties with ':'" in caplog.text, \
            f"the comma reading was taken without saying so:\n{caplog.text}"

    def test_the_warning_names_the_reading_it_took(self, caplog):
        parse_node_specs('mmi_dc#xlnx,dc-operating-mode')
        assert "'xlnx,dc-operating-mode' read as 2" in caplog.text, \
            f"warning did not report what it did:\n{caplog.text}"

    def test_a_colon_list_does_not_warn(self, caplog):
        parse_node_specs('mmi_dc#xlnx,dc-operating-mode:clocks')
        assert "separate properties with ':'" not in caplog.text, \
            f"unambiguous spec warned anyway:\n{caplog.text}"

    def test_a_single_property_does_not_warn(self, caplog):
        parse_node_specs('mmi_dc#clocks')
        assert "separate properties with ':'" not in caplog.text, \
            f"single property warned anyway:\n{caplog.text}"
