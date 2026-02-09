# Pytest Migration Guide

This document describes the ongoing migration from `lopper_sanity.py` to pytest-based testing.

## Current Status

### ✅ Completed
- **Tree tests** (`tests/test_tree.py`) - **42 tests migrated** (100% complete)
  - Complete 1:1 migration of `tree_sanity_test()` from lopper_sanity.py (lines 1260-1985)
  - All test classes migrated:
    - `TestBasicTreeWalking` - Tree iteration and node counting
    - `TestTreePrint` - Tree printing with /memreserve/
    - `TestNodeAccess` - Node access by path, number, phandle
    - `TestCustomNodeLists` - Custom iterations and restricted walks
    - `TestSubnodeCalls` - Subnode access methods
    - `TestResolveReferences` - Reference resolution
    - `TestNodeStringRepresentation` - Node `__str__` methods
    - `TestNodeRegexFind` - Regex-based node searching
    - `TestPropertyRegexFind` - Property regex matching
    - `TestTreeManipulation` - Node creation and addition
    - `TestTreeResolveAndExport` - Tree resolve and export
    - `TestNodeDeepCopy` - Node deep copying
    - `TestPropertyManipulation` - Property operations
    - `TestPropertyAccess` - Property index and dict access
    - `TestAliases` - Alias lookups

- **YAML tests** (`tests/test_yaml.py`) - **6 tests migrated** (100% complete)
  - Complete 1:1 migration of `yaml_sanity_test()` from lopper_sanity.py (lines 2534-2569)
  - All test classes migrated:
    - `TestYAMLReadWrite` - YAML load and write operations
    - `TestYAMLToTree` - YAML to tree conversion and DTS writing
    - `TestSDTToYAML` - Device tree to YAML conversion
    - `TestComplexPropertyAccess` - Complex nested property access from YAML

- **FDT tests** (`tests/test_fdt.py`) - **15 tests migrated** (100% complete)
  - Complete 1:1 migration of `fdt_sanity_test()` from lopper_sanity.py (lines 2412-2533)
  - All test classes migrated:
    - `TestFDTExport` - FDT export to dictionary
    - `TestTreeLoadFromFDT` - Loading tree from exported FDT
    - `TestTreeSync` - Syncing tree changes back to FDT
    - `TestNodeDeletion` - Node and property deletion
    - `TestNodeAddition` - Adding nodes and properties
    - `TestNodeIteration` - Tree and subnode iteration
    - `TestStringTypeDetection` - String decoding in node printing

### 📋 TODO - High Priority
- **Schema tests** - Migrate `schema_type_sanity_test()`

### 📋 TODO - Medium Priority
- **Lops tests** - Migrate `lops_sanity_test()` and `lops_code_test()`
- **Format tests** - Migrate `format_sanity_test()`

### 📋 TODO - Lower Priority
- **Assists tests** - Migrate `assists_sanity_test()`
- **OpenAMP tests** - Migrate `openamp_sanity_test()`
- **Domain generation tests** - Migrate `xlnx_gen_domain_sanity_test()`

## Migration Process

### Step 1: Choose a Test Suite

Pick a test function from `lopper_sanity.py` to migrate (e.g., `yaml_sanity_test`).

### Step 2: Create Test File

Create `tests/test_<feature>.py` with test classes:

```python
"""
Tests for <feature> functionality.

Migrated from lopper_sanity.py's <feature>_sanity_test() function.
"""

class Test<FeatureName>:
    """Tests for <specific aspect>."""

    def test_something(self, lopper_tree):
        """Test that something works."""
        # Your test code here
        assert expected == actual
```

### Step 3: Use Fixtures

Available fixtures in `tests/conftest.py`:

- `test_outdir` - Temporary directory (session-scoped)
- `system_device_tree` - Path to test DTS file (session-scoped)
- `compiled_fdt` - Compiled FDT object (session-scoped)
- `lopper_tree` - Fresh LopperTree instance (function-scoped)

### Step 4: Convert Assertions

**Before (lopper_sanity.py)**:
```python
if node_count != 22:
    test_failed(f"node count is incorrect ({node_count} expected 22)")
else:
    test_passed("node walk passed")
```

**After (pytest)**:
```python
assert node_count == 22, f"Expected 22 nodes, got {node_count}"
```

### Step 5: Run Tests

```bash
# Run your new tests
pytest tests/test_<feature>.py -v

# Run all tests (pytest + legacy)
./scripts/run_tests.sh
```

### Step 6: Update Documentation

Update this file's "Current Status" section and `tests/README.md`.

## Benefits of Pytest

1. **Clear failure messages**: See exactly what went wrong
   ```
   AssertionError: Expected 22 nodes, got 20
   assert 20 == 22
   ```

2. **Better organization**: Test classes group related tests
   ```python
   class TestNodeAccess:
       def test_by_path(self): ...
       def test_by_number(self): ...
   ```

3. **Reusable setup**: Fixtures eliminate duplicate setup code
   ```python
   @pytest.fixture
   def configured_tree(lopper_tree):
       # Setup code runs once, used by many tests
       return tree
   ```

4. **Parametrization**: Test multiple inputs easily
   ```python
   @pytest.mark.parametrize("path,expected", [
       ("/cpus", "cpus"),
       ("/memory@0", "memory@0"),
   ])
   def test_node_names(lopper_tree, path, expected):
       assert lopper_tree[path].name == expected
   ```

5. **Parallel execution**: Run tests faster
   ```bash
   pytest -n auto  # Uses all CPU cores
   ```

## Common Patterns

### Checking File Contents

**Before**:
```python
with open(output_file) as fp:
    content = fp.read()
    if "expected string" not in content:
        test_failed("String not found")
```

**After**:
```python
with open(output_file) as fp:
    content = fp.read()
    assert "expected string" in content
```

### Counting Occurrences

**Before**:
```python
count = 0
for line in file:
    if pattern in line:
        count += 1
if count != expected:
    test_failed(f"Found {count}, expected {expected}")
```

**After**:
```python
with open(file) as fp:
    count = sum(1 for line in fp if pattern in line)
    assert count == expected, f"Expected {expected} matches, found {count}"
```

### Testing Exceptions

**Before**:
```python
try:
    risky_operation()
    test_failed("Should have raised an exception")
except ExpectedException:
    test_passed("Correctly raised exception")
```

**After**:
```python
import pytest

with pytest.raises(ExpectedException):
    risky_operation()
```

## CI Integration

Both pytest and legacy tests run in CI:

1. **Pytest tests** run first (faster feedback)
2. **Legacy tests** run second (comprehensive coverage)
3. **Both must pass** for CI to succeed

See `.github/workflows/ci.yml` and `scripts/run_tests.sh`.

## Questions?

- See `tests/test_tree.py` for working examples
- Check `tests/README.md` for running tests
- Review `tests/conftest.py` for available fixtures
- Refer to [pytest documentation](https://docs.pytest.org/)

## Timeline

- **Phase 1** (Complete): Tree tests migrated, CI updated
- **Phase 2** (2-3 weeks): Core tests (YAML, FDT, Schema)
- **Phase 3** (3-4 weeks): Complex tests (Assists, OpenAMP)
- **Phase 4** (1 week): Remove legacy lopper_sanity.py (optional)
