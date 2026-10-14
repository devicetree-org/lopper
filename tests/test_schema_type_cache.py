# Copyright (c) 2026 Advanced Micro Devices, Inc. All Rights Reserved.
# Author: Bruce Ashfield <bruce.ashfield@amd.com>
# SPDX-License-Identifier: BSD-3-Clause
"""
get_property_type() answers are cached, and the cache must not change them.

Working out a property's type walks the override tables, then the
compatible table, then -- for anything not named explicitly -- four tables
of regular expressions, in priority order. That tail is linear, and the
routine is called for every property of every node on every resolve. On a
Versal2 openamp expansion it accounted for 18.7 million regular expression
matches, 96% of every match in the run.

Every table it consults is built in __init__ and not written to afterwards,
so the answer for a given question cannot change for a given resolver. These
pin that: the cached answer matches the uncached one, the priority order
still decides, and a second resolver does not inherit the first one's
answers.
"""

import pytest

from lopper import LopperFmt
from lopper.schema.learned import DTSPropertyTypeResolver


def _resolver(schema=None):
    return DTSPropertyTypeResolver(schema if schema is not None else {})


class TestTheCacheDoesNotChangeTheAnswer:
    def test_cached_matches_uncached(self):
        r = _resolver()
        for name in ("reg", "compatible", "interrupts", "xlnx,dc-operating-mode",
                     "#address-cells", "status", "a-name-nothing-knows"):
            uncached = r._resolve_property_type(name)
            cached = r.get_property_type(name)
            assert cached == uncached, \
                f"{name}: cache returned {cached}, direct lookup {uncached}"

    def test_a_repeated_question_gets_the_same_answer(self):
        r = _resolver()
        first = r.get_property_type("reg", "/amba/serial@ff000000")
        for _ in range(5):
            assert r.get_property_type("reg", "/amba/serial@ff000000") == first

    def test_the_node_path_is_part_of_the_question(self):
        """A property typed by path must not be answered from another path."""
        r = _resolver()
        r._override_paths["/__symbols__"] = {"*": LopperFmt.STRING}
        r._type_cache.clear()
        assert r.get_property_type("anything", "/__symbols__") == LopperFmt.STRING
        other = r.get_property_type("anything", "/amba/serial@ff000000")
        assert other != LopperFmt.STRING or \
               r._resolve_property_type("anything", "/amba/serial@ff000000") == LopperFmt.STRING, \
            "a path-scoped answer leaked to a different path"

    def test_a_list_compatible_is_accepted(self):
        """compatible arrives as a list often enough to matter, and a list
        cannot key a dict."""
        r = _resolver()
        got = r.get_property_type("reg", "/amba/serial@ff000000",
                                  ["xlnx,uart", "ns16550a"])
        assert got == r._resolve_property_type("reg", "/amba/serial@ff000000",
                                               ["xlnx,uart", "ns16550a"])


class TestTheCacheIsPerResolver:
    def test_a_fresh_resolver_starts_empty(self):
        r = _resolver()
        assert r._type_cache == {}, "a new resolver inherited cached answers"

    def test_two_resolvers_do_not_share(self):
        """A different schema is a different set of answers."""
        a = _resolver()
        a.get_property_type("reg")
        b = _resolver()
        assert b._type_cache == {}, \
            "the cache is shared between resolvers; a schema change would be ignored"

    def test_an_answer_is_actually_remembered(self):
        r = _resolver()
        assert not r._type_cache
        r.get_property_type("reg", "/amba")
        assert r._type_cache, "nothing was cached; the lookup repeats every call"
