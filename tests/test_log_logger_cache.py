# Copyright (c) 2026 Advanced Micro Devices, Inc. All Rights Reserved.
# Author: Bruce Ashfield <bruce.ashfield@amd.com>
# SPDX-License-Identifier: BSD-3-Clause
"""
__logger__() caches its answer, and registering a logger has to undo that.

Which logger a module gets is decided by its filename and by whether _init()
has been called for it, so the answer does not change for the life of the
process. It was being recomputed on every message -- a stack walk, a
basename, a dict lookup and a getLogger -- including for the messages thrown
away because debug is off. A Versal2 openamp expansion makes 1.27 million of
those calls.

The one case where the right answer does change is _init(): a module that
logged beforehand was answered with the root logger, and registering one
makes that stale. These pin both halves.
"""

import logging

import pytest

import lopper.log as L


@pytest.fixture
def clean_cache():
    """Leave the cache as it was found; it is process-global."""
    saved = dict(L.__logger_cache__)
    L.__logger_cache__.clear()
    yield L.__logger_cache__
    L.__logger_cache__.clear()
    L.__logger_cache__.update(saved)


class TestTheAnswerIsRemembered:
    def test_a_lookup_is_cached(self, clean_cache):
        L._debug("a message")
        assert clean_cache, "nothing cached; the lookup repeats on every message"

    def test_repeated_lookups_give_the_same_logger(self, clean_cache):
        first = L.__logger__()
        for _ in range(5):
            assert L.__logger__() is first

    def test_it_is_keyed_on_the_full_path(self, clean_cache):
        """Two modules of the same basename in different packages must not
        share an entry."""
        L.__logger__()
        assert all("/" in k or "\\" in k or k.endswith(".py")
                   for k in clean_cache), \
            f"cache keys are not file paths: {list(clean_cache)}"


class TestRegisteringALoggerInvalidates:
    def test_init_clears_the_cache(self, clean_cache):
        """A module that logged before _init() was answered with the root
        logger. Registering one changes the right answer."""
        L._debug("warm the cache")
        assert clean_cache, "precondition: something should be cached"
        L._init("a.test.logger.name")
        assert not clean_cache, \
            "_init() left a stale answer cached; that module keeps the root logger"

    def test_the_right_logger_is_returned_after_init(self, clean_cache):
        name = "lopper_test_logger_cache"
        L._init(name)
        lg = logging.getLogger(name)
        assert lg.handlers, "precondition: _init() should have attached a handler"
        # the module under test here is this file, which has no logger of its
        # own, so it must fall back rather than pick up someone else's
        assert L.__logger__() is not lg, \
            "an unrelated module was handed a logger registered for another"
