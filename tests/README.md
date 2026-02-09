# Lopper Pytest Test Suite

This directory contains the pytest-based test suite for Lopper. This is a work-in-progress migration from the legacy `lopper_sanity.py` test framework.

## Running Tests

### Run all pytest tests
```bash
pytest tests/
```

### Run with verbose output
```bash
pytest -v tests/
```

### Run specific test file
```bash
pytest tests/test_tree.py
```

### Run specific test class or function
```bash
pytest tests/test_tree.py::TestTreeWalking
pytest tests/test_tree.py::TestTreeWalking::test_tree_walk_no_exceptions
```

### Run in parallel (faster)
```bash
pytest -n auto tests/
```

### Run with coverage
```bash
pytest --cov=lopper --cov-report=html tests/
```

## Test Structure

- `conftest.py` - Shared fixtures and test configuration
- `test_tree.py` - Tree walking and manipulation tests (migrated from `tree_sanity_test`)
- `test_yaml.py` - YAML input/output and conversion tests (migrated from `yaml_sanity_test`)
- `test_fdt.py` - FDT abstraction layer tests (migrated from `fdt_sanity_test`)
- More test files will be added as migration continues...

## Fixtures

Common fixtures available to all tests:

- `test_outdir` - Temporary directory for test outputs (session-scoped)
- `system_device_tree` - Path to compiled test device tree (session-scoped)
- `compiled_fdt` - Compiled FDT object ready for testing (session-scoped)
- `lopper_tree` - Fresh LopperTree instance (function-scoped, one per test)
- `lopper_sdt` - Fresh LopperSDT instance (function-scoped, one per test)
- `yaml_test_file` - Path to YAML test file (session-scoped)

## Migration Status

### ✅ Migrated (Complete 1:1 migrations)
- **Tree tests** (`test_tree.py`) - **42 tests** covering complete `tree_sanity_test()` from lopper_sanity.py
  - Basic tree walking and node counting
  - Tree printing with /memreserve/ preservation
  - Node access (by path, number, phandle)
  - Custom node lists and restricted walks
  - Subnode access methods
  - Reference resolution
  - Node string representation
  - Regex-based node and property finding
  - Tree manipulation (add/remove nodes and properties)
  - Tree resolve and export
  - Node deep copying
  - Property manipulation and access
  - Alias lookups

- **YAML tests** (`test_yaml.py`) - **6 tests** covering complete `yaml_sanity_test()` from lopper_sanity.py
  - YAML load and write operations
  - YAML to tree conversion
  - Tree to DTS writing from YAML
  - Device tree to YAML conversion
  - Complex nested property access

- **FDT tests** (`test_fdt.py`) - **15 tests** covering complete `fdt_sanity_test()` from lopper_sanity.py
  - FDT export to dictionary representation
  - Loading tree from exported FDT
  - Tree sync and round-trip operations
  - Node and property deletion
  - Node and property addition
  - Tree and subnode iteration
  - String type detection in node printing

### 📋 TODO
- Schema tests (`test_schema.py`) - Migrate `schema_type_sanity_test()`
- Lops tests (`test_lops.py`) - Migrate `lops_sanity_test()` and `lops_code_test()`
- Assists tests (`test_assists.py`) - Migrate `assists_sanity_test()`
- OpenAMP tests (`test_openamp.py`) - Migrate `openamp_sanity_test()`

## Benefits of Pytest

1. **Better assertions**: Clear failure messages showing expected vs actual
2. **Fixtures**: Reusable test setup without global state
3. **Parametrization**: Easy to test multiple inputs
4. **Parallel execution**: Run tests faster with `-n auto`
5. **Standard tooling**: Works with coverage tools, IDEs, CI systems
6. **Test discovery**: Just add files, no manual registration

## Contributing

When adding new tests:

1. Create test file: `tests/test_<feature>.py`
2. Use fixtures from `conftest.py` for common setup
3. Follow naming convention: `test_<what_it_tests>`
4. Add docstrings explaining what each test validates
5. Run locally before committing: `pytest tests/`

See `TODO.md` in the root directory for the full migration plan.
