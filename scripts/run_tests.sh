#!/bin/bash
set -euo pipefail

echo "Running lopper_sanity.py test suite..."
echo "========================================="

# Run tests and capture output
python3 lopper_sanity.py --all 2>&1 | tee test_output.txt
TEST_EXIT_CODE=${PIPESTATUS[0]}

echo ""
echo "========================================="
echo "Analyzing test results..."

# Parse test results
PASSED=$(grep -c "\[TEST PASSED\]:" test_output.txt || true)
FAILED=$(grep -c "\[TEST FAILED\]:" test_output.txt || true)

echo "Tests passed: $PASSED"
echo "Tests failed: $FAILED"

# Create GitHub Actions annotations for failures (only in CI environment)
if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  echo "Creating GitHub Actions annotations..."

  while IFS= read -r line; do
    if [[ $line =~ \[TEST\ FAILED\]:\ (.+) ]]; then
      echo "::error::Test Failed: ${BASH_REMATCH[1]}"
    fi
  done < test_output.txt

  # Generate job summary
  echo "## Test Results" >> $GITHUB_STEP_SUMMARY
  echo "" >> $GITHUB_STEP_SUMMARY
  echo "- ✅ Passed: $PASSED" >> $GITHUB_STEP_SUMMARY
  echo "- ❌ Failed: $FAILED" >> $GITHUB_STEP_SUMMARY
  echo "" >> $GITHUB_STEP_SUMMARY

  if [ $FAILED -gt 0 ]; then
    echo "### Failed Tests" >> $GITHUB_STEP_SUMMARY
    echo "" >> $GITHUB_STEP_SUMMARY
    grep "\[TEST FAILED\]:" test_output.txt | while read -r line; do
      echo "- $line" >> $GITHUB_STEP_SUMMARY
    done
  fi
fi

# Exit with the test suite's exit code
if [ $TEST_EXIT_CODE -eq 0 ]; then
  echo "✅ All tests passed!"
else
  echo "❌ Tests failed with exit code $TEST_EXIT_CODE"
fi

exit $TEST_EXIT_CODE
