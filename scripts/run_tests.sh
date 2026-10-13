#!/bin/bash
set -euo pipefail

PYTEST_FAILED=0
LEGACY_FAILED=0

# Run pytest tests first
echo "========================================="
echo "Running pytest test suite..."
echo "========================================="

# -n auto distributes across cores. pytest-xdist is already a declared
# dependency in requirements.txt; it was simply never asked for here, and the
# suite ran serially at roughly four times the wall clock as a result.
#
PYTEST_FAILED=0
if pytest -v tests/ -n auto --tb=short --junitxml=pytest-results.xml 2>&1 | tee pytest_output.txt; then
    echo "✅ Pytest tests passed!"
else
    PYTEST_FAILED=1
    echo "❌ Pytest tests failed!"
fi

echo ""
echo "========================================="
echo "Running lopper_sanity.py legacy test suite..."
echo "========================================="

# Run tests and capture output
LEGACY_FAILED=0
python3 lopper_sanity.py --all 2>&1 | tee test_output.txt
TEST_EXIT_CODE=${PIPESTATUS[0]}

if [ $TEST_EXIT_CODE -ne 0 ]; then
    LEGACY_FAILED=1
fi

echo ""
echo "========================================="
echo "Analyzing test results..."

# Parse legacy test results
PASSED=$(grep -c "\[TEST PASSED\]:" test_output.txt || true)
FAILED=$(grep -c "\[TEST FAILED\]:" test_output.txt || true)

echo "Legacy tests passed: $PASSED"
echo "Legacy tests failed: $FAILED"

# Parse pytest results from the junit xml rather than the console output.
# Matching "^tests/.*PASSED" only works for serial -v output: under xdist each
# line is prefixed with its worker ("[gw3] [ 12%] PASSED tests/..."), so the
# anchored pattern silently counts zero and the run looks empty rather than
# broken. The xml carries the counts directly and does not change shape.
read -r PYTEST_PASSED PYTEST_FAILED_COUNT <<< "$(python3 - <<'PYEOF'
import xml.etree.ElementTree as ET
try:
    root = ET.parse('pytest-results.xml').getroot()
    suites = [root] if root.tag == 'testsuite' else root.findall('testsuite')
    total = sum(int(s.get('tests', 0)) for s in suites)
    bad = sum(int(s.get('failures', 0)) + int(s.get('errors', 0)) for s in suites)
    skipped = sum(int(s.get('skipped', 0)) for s in suites)
    print(total - bad - skipped, bad)
except Exception:
    print(0, 0)
PYEOF
)"

echo "Pytest tests passed: $PYTEST_PASSED"
echo "Pytest tests failed: $PYTEST_FAILED_COUNT"

# Create GitHub Actions annotations for failures (only in CI environment)
if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  echo "Creating GitHub Actions annotations..."

  # Pytest failures
  while IFS= read -r line; do
    if [[ $line =~ FAILED\ (.+) ]]; then
      echo "::error::Pytest Failed: ${BASH_REMATCH[1]}"
    fi
  done < pytest_output.txt

  # Legacy test failures
  while IFS= read -r line; do
    if [[ $line =~ \[TEST\ FAILED\]:\ (.+) ]]; then
      echo "::error::Legacy Test Failed: ${BASH_REMATCH[1]}"
    fi
  done < test_output.txt

  # Generate job summary
  echo "## Test Results" >> $GITHUB_STEP_SUMMARY
  echo "" >> $GITHUB_STEP_SUMMARY
  echo "### Pytest (New Framework)" >> $GITHUB_STEP_SUMMARY
  echo "- ✅ Passed: $PYTEST_PASSED" >> $GITHUB_STEP_SUMMARY
  echo "- ❌ Failed: $PYTEST_FAILED_COUNT" >> $GITHUB_STEP_SUMMARY
  echo "" >> $GITHUB_STEP_SUMMARY
  echo "### Legacy Tests (lopper_sanity.py)" >> $GITHUB_STEP_SUMMARY
  echo "- ✅ Passed: $PASSED" >> $GITHUB_STEP_SUMMARY
  echo "- ❌ Failed: $FAILED" >> $GITHUB_STEP_SUMMARY
  echo "" >> $GITHUB_STEP_SUMMARY

  if [ $PYTEST_FAILED_COUNT -gt 0 ]; then
    echo "### Failed Pytest Tests" >> $GITHUB_STEP_SUMMARY
    echo "" >> $GITHUB_STEP_SUMMARY
    grep "FAILED" pytest_output.txt | while read -r line; do
      echo "- $line" >> $GITHUB_STEP_SUMMARY
    done
    echo "" >> $GITHUB_STEP_SUMMARY
  fi

  if [ $FAILED -gt 0 ]; then
    echo "### Failed Legacy Tests" >> $GITHUB_STEP_SUMMARY
    echo "" >> $GITHUB_STEP_SUMMARY
    grep "\[TEST FAILED\]:" test_output.txt | while read -r line; do
      echo "- $line" >> $GITHUB_STEP_SUMMARY
    done
  fi
fi

# Exit with failure if either test suite failed
if [ $PYTEST_FAILED -eq 1 ] || [ $LEGACY_FAILED -eq 1 ]; then
  echo ""
  echo "========================================="
  echo "❌ Some tests failed!"
  exit 1
fi

echo ""
echo "========================================="
echo "✅ All tests passed!"
exit 0
