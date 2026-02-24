"""
Pytest tests for assist file matching in lopper.

This module tests the BitBake-style assist matching functionality that
finds lop files based on input file patterns.

Copyright (c) 2019,2020 Xilinx Inc. All rights reserved.
Copyright (C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.

SPDX-License-Identifier: BSD-3-Clause

Author:
    Bruce Ashfield <bruce.ashfield@amd.com>
"""

import os
import pytest
import tempfile
import shutil


class TestFindAnyMatchingAssists:
    """Test the find_any_matching_assists() method."""

    @pytest.fixture
    def temp_lops_dir(self):
        """Create a temporary directory with test lop files."""
        tmpdir = tempfile.mkdtemp()
        lops_dir = os.path.join(tmpdir, "lops")
        os.makedirs(lops_dir)

        # Create test lop files
        test_lops = [
            "%.yaml.lop",           # suffix match for .yaml files
            "domain%.lop",          # prefix match for domain* files
            "%.json.lop",           # suffix match for .json files
            "lop-xlate-yaml.dts",   # legacy xlate pattern
            "lop-xlate-foo.dts",    # legacy xlate for .foo files
            "exact-match.yaml.lop", # exact match test
        ]
        for lop in test_lops:
            open(os.path.join(lops_dir, lop), 'w').close()

        yield lops_dir

        # Cleanup
        shutil.rmtree(tmpdir)

    def test_suffix_match_yaml(self, lopper_sdt, temp_lops_dir):
        """Test that %.yaml.lop matches .yaml files."""
        matches = lopper_sdt.find_any_matching_assists(
            ["test.yaml"],
            local_search_paths=[temp_lops_dir]
        )
        basenames = [os.path.basename(m) for m in matches]
        assert "%.yaml.lop" in basenames, "%.yaml.lop should match test.yaml"

    def test_suffix_match_json(self, lopper_sdt, temp_lops_dir):
        """Test that %.json.lop matches .json files."""
        matches = lopper_sdt.find_any_matching_assists(
            ["config.json"],
            local_search_paths=[temp_lops_dir]
        )
        basenames = [os.path.basename(m) for m in matches]
        assert "%.json.lop" in basenames, "%.json.lop should match config.json"

    def test_prefix_match_domain(self, lopper_sdt, temp_lops_dir):
        """Test that domain%.lop matches domain* files."""
        matches = lopper_sdt.find_any_matching_assists(
            ["domain-test.yaml"],
            local_search_paths=[temp_lops_dir]
        )
        basenames = [os.path.basename(m) for m in matches]
        assert "domain%.lop" in basenames, "domain%.lop should match domain-test.yaml"
        # Should also match %.yaml.lop
        assert "%.yaml.lop" in basenames, "%.yaml.lop should also match domain-test.yaml"

    def test_no_match_without_fallback(self, lopper_sdt, temp_lops_dir):
        """Test that unknown extensions don't match without xlate_fallback."""
        matches = lopper_sdt.find_any_matching_assists(
            ["test.foo"],
            local_search_paths=[temp_lops_dir],
            xlate_fallback=False
        )
        # No %.foo.lop exists, and fallback is disabled
        assert len(matches) == 0, "test.foo should not match without fallback"

    def test_xlate_fallback_finds_legacy(self, lopper_sdt, temp_lops_dir):
        """Test that xlate_fallback finds lop-xlate-{ext}.dts."""
        matches = lopper_sdt.find_any_matching_assists(
            ["test.foo"],
            local_search_paths=[temp_lops_dir],
            xlate_fallback=True
        )
        basenames = [os.path.basename(m) for m in matches]
        assert "lop-xlate-foo.dts" in basenames, \
            "xlate_fallback should find lop-xlate-foo.dts for test.foo"

    def test_bitbake_preferred_over_fallback(self, lopper_sdt, temp_lops_dir):
        """Test that BitBake match is used even when fallback is enabled."""
        matches = lopper_sdt.find_any_matching_assists(
            ["test.yaml"],
            local_search_paths=[temp_lops_dir],
            xlate_fallback=True
        )
        basenames = [os.path.basename(m) for m in matches]
        # Should find %.yaml.lop (BitBake match)
        assert "%.yaml.lop" in basenames, "Should find BitBake match %.yaml.lop"
        # Should NOT include lop-xlate-yaml.dts since BitBake matched
        assert "lop-xlate-yaml.dts" not in basenames, \
            "Should not fall back to legacy when BitBake matches"

    def test_exact_match(self, lopper_sdt, temp_lops_dir):
        """Test exact filename matching."""
        matches = lopper_sdt.find_any_matching_assists(
            ["exact-match.yaml.lop"],
            local_search_paths=[temp_lops_dir]
        )
        basenames = [os.path.basename(m) for m in matches]
        assert "exact-match.yaml.lop" in basenames, "Should find exact match"

    def test_multiple_input_files(self, lopper_sdt, temp_lops_dir):
        """Test matching multiple input files."""
        matches = lopper_sdt.find_any_matching_assists(
            ["test.yaml", "config.json", "other.txt"],
            local_search_paths=[temp_lops_dir]
        )
        basenames = [os.path.basename(m) for m in matches]
        assert "%.yaml.lop" in basenames, "Should match .yaml file"
        assert "%.json.lop" in basenames, "Should match .json file"
        # .txt has no match
        assert len([b for b in basenames if "txt" in b]) == 0, \
            "Should not match .txt file"

    def test_no_duplicate_matches(self, lopper_sdt, temp_lops_dir):
        """Test that duplicate matches are deduplicated."""
        # Clear load_paths to only search temp dir
        orig_paths = lopper_sdt.load_paths
        lopper_sdt.load_paths = []
        try:
            matches = lopper_sdt.find_any_matching_assists(
                ["file1.yaml", "file2.yaml", "file3.yaml"],
                local_search_paths=[temp_lops_dir]
            )
            # All three should match %.yaml.lop, but it should only appear once
            # Filter to only matches in our temp dir
            temp_matches = [m for m in matches if temp_lops_dir in m]
            yaml_lop_count = sum(1 for m in temp_matches if m.endswith("%.yaml.lop"))
            assert yaml_lop_count == 1, "%.yaml.lop should only appear once"
        finally:
            lopper_sdt.load_paths = orig_paths


class TestBitBakePatternParsing:
    """Test BitBake-style pattern parsing edge cases."""

    @pytest.fixture
    def temp_lops_dir(self):
        """Create a temporary directory with edge case lop files."""
        tmpdir = tempfile.mkdtemp()
        lops_dir = os.path.join(tmpdir, "lops")
        os.makedirs(lops_dir)

        # Edge case lop files
        test_lops = [
            "%.lop",                # match anything (empty prefix and suffix)
            "prefix%.suffix.lop",   # both prefix and suffix
            "%special-chars@.lop",  # special characters in suffix
        ]
        for lop in test_lops:
            open(os.path.join(lops_dir, lop), 'w').close()

        yield lops_dir
        shutil.rmtree(tmpdir)

    def test_empty_prefix_and_suffix(self, lopper_sdt, temp_lops_dir):
        """Test %.lop matches any file."""
        matches = lopper_sdt.find_any_matching_assists(
            ["anything.xyz"],
            local_search_paths=[temp_lops_dir]
        )
        basenames = [os.path.basename(m) for m in matches]
        assert "%.lop" in basenames, "%.lop should match any file"

    def test_prefix_and_suffix_combined(self, lopper_sdt, temp_lops_dir):
        """Test prefix%.suffix.lop pattern."""
        matches = lopper_sdt.find_any_matching_assists(
            ["prefix-middle.suffix"],
            local_search_paths=[temp_lops_dir]
        )
        basenames = [os.path.basename(m) for m in matches]
        assert "prefix%.suffix.lop" in basenames, \
            "prefix%.suffix.lop should match prefix-middle.suffix"

    def test_prefix_and_suffix_no_match(self, lopper_sdt, temp_lops_dir):
        """Test prefix%.suffix.lop doesn't match wrong prefix."""
        matches = lopper_sdt.find_any_matching_assists(
            ["wrong-middle.suffix"],
            local_search_paths=[temp_lops_dir]
        )
        basenames = [os.path.basename(m) for m in matches]
        # %.lop should still match, but prefix%.suffix.lop should not
        assert "prefix%.suffix.lop" not in basenames, \
            "prefix%.suffix.lop should not match wrong prefix"
