#!/bin/bash
#
# Copyright (c) 2026 AMD Inc. All rights reserved.
#
# Author:
#       Bruce Ashfield <bruce.ashfield@amd.com>
#
# SPDX-License-Identifier: BSD-3-Clause
#
# Simple wrapper for running pytest tests
# Usage: ./run_pytest.sh [options]
#

set -euo pipefail

# Activate venv if it exists
if [ -d "venv-lopper" ]; then
    source venv-lopper/bin/activate
elif [ -d ".venv" ]; then
    source .venv/bin/activate
fi

# Default: run all tests with verbose output
if [ $# -eq 0 ]; then
    echo "Running all pytest tests..."
    pytest tests/ -v
    exit $?
fi

# Handle common commands
case "$1" in
    -h|--help|help)
        cat << 'EOF'
Pytest Wrapper - Common Commands:

  ./run_pytest.sh              Run all tests (verbose)
  ./run_pytest.sh quick        Run all tests (quiet, fast)
  ./run_pytest.sh tree         Run tree tests only
  ./run_pytest.sh yaml         Run YAML/domain tests (glob, assist matching)
  ./run_pytest.sh failed       Re-run only failed tests
  ./run_pytest.sh coverage     Run with coverage report
  ./run_pytest.sh parallel     Run tests in parallel (fast)
  ./run_pytest.sh debug        Run with maximum verbosity and debugging
  ./run_pytest.sh <pattern>    Run tests matching pattern

Examples:
  ./run_pytest.sh tree                    # Run tests/test_tree.py
  ./run_pytest.sh TestNodeAccess          # Run specific test class
  ./run_pytest.sh test_tree_walk          # Run tests matching name
  ./run_pytest.sh tests/test_tree.py::TestBasicTreeWalking::test_tree_walk_no_exceptions

For more options, run: pytest --help
EOF
        ;;

    quick)
        echo "Running tests (quiet mode)..."
        pytest tests/ -q
        ;;

    tree)
        echo "Running tree tests only..."
        pytest tests/test_tree.py -v
        ;;

    yaml)
        echo "Running YAML/domain tests..."
        pytest tests/test_glob_access.py tests/test_assist_matching.py -v
        ;;

    failed)
        echo "Re-running failed tests..."
        pytest tests/ -v --lf
        ;;

    coverage)
        echo "Running tests with coverage..."
        pytest tests/ -v --cov=lopper --cov-report=term-missing
        ;;

    parallel)
        echo "Running tests in parallel..."
        pytest tests/ -n auto
        ;;

    debug)
        echo "Running tests with maximum verbosity..."
        pytest tests/ -vv -s --tb=long
        ;;

    *)
        # Pass through to pytest
        echo "Running: pytest tests/ -v -k '$1'"
        pytest tests/ -v -k "$1"
        ;;
esac
