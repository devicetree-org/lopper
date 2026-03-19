# Memory Audit and Overlap Detection

Lopper provides memory validation and overlap detection to catch configuration
errors in device trees before they cause runtime failures.

## Background

This framework replaces ad-hoc memory validation scattered across assists
(e.g., `xlnx_validate_carveouts()` in openamp_xlnx.py) with a unified,
phased validation system. It provides:

- **Modular checks** - Fine-grained validation flags instead of monolithic checks
- **Phased execution** - Checks run at appropriate pipeline stages (EARLY, POST_YAML, POST_DOMAIN)
- **Visual debugging** - ASCII memory map rendering to see layout at a glance
- **Intentional overlap handling** - Distinguishes shared memory (allowed) from conflicts (errors)

The framework addresses the "Final Tree Consistency Checks" and "Inter-Domain
Memory Cross-Checking" requirements, replacing a previous monolithic approach
(PR #705) with modular, testable components.

## Quick Start

### Check for Reserved Memory Overlaps

```bash
# Enable overlap detection during lopper processing
lopper -W memory_overlap system-device-tree.dts -- lop-file.dts output.dts

# Enable all memory checks
lopper -W memory_all system-device-tree.dts -- lop-file.dts output.dts
```

### Generate a Memory Map Visualization

```bash
# Output memory map to a file
lopper --memmap=memmap.txt system-device-tree.dts -- lop-file.dts output.dts

# Output to stdout
lopper --memmap=- system-device-tree.dts -- lop-file.dts output.dts
```

## Warning Flags

| Flag | Description |
|------|-------------|
| `-W memory_cells` | Validate #address-cells and #size-cells properties |
| `-W memory_reg` | Validate reg property format matches cell counts |
| `-W memory_overlap` | Detect overlaps in reserved-memory regions |
| `-W reserved_bounds` | Check reserved-memory fits within domain bounds |
| `-W domain_overlap` | Detect overlaps within a single domain |
| `-W cross_domain` | Detect overlaps across different domains |
| `-W memory_all` | Enable all memory checks |
| `-W all` | Enable all warnings (including memory) |

## What Gets Checked

### Reserved Memory Overlap Detection (`-W memory_overlap`)

Detects when two reserved-memory regions occupy the same address space:

```
WARNING: Reserved memory overlap detected:
  Region 1: /reserved-memory/buffer@10000000 [0x10000000 - 0x10100000]
  Region 2: /reserved-memory/pool@10080000 [0x10080000 - 0x10180000]
  Overlap: 512 KB at 0x10080000
```

**Intentional overlaps are allowed:**
- `shared-dma-pool` regions (designed to be shared)
- `restricted-dma-pool` regions
- Reserved memory contained within physical memory ranges

### Cell Property Validation (`-W memory_cells`)

Ensures memory nodes have valid #address-cells and #size-cells:

```
WARNING: Invalid #address-cells in /memory@0: expected 2, found 1
```

### Reg Property Format (`-W memory_reg`)

Validates that reg properties match the cell count declarations:

```
WARNING: Malformed reg in /reserved-memory/buffer:
  expected 4 values (2 addr + 2 size), found 3
```

## Memory Map Visualization

The `--memmap` option generates an ASCII visualization of the memory layout:

```
============================================================
                    Memory Map: System
============================================================

Physical Memory:
  0x0000000000000000 [================================================] 2 GB
                     /memory@0

Reserved Memory:
  0x0000000010000000 [################                                ] 256 MB
                     /reserved-memory/buffer@10000000
  0x0000000020000000 [########                                        ] 128 MB
                     /reserved-memory/firmware@20000000

Legend:
  [=] Physical Memory    [#] Reserved Memory
  [-] Domain Memory      [@] Carveout
  [X] OVERLAP (problem!)
```

## Validation Phases

Memory validation runs at different phases of lopper processing:

| Phase | Checks | When |
|-------|--------|------|
| EARLY | cell properties, reg format | Before YAML processing |
| POST_YAML | reserved-memory overlaps | After domains.yaml applied |
| POST_DOMAIN | domain overlaps, cross-domain | After domain extraction |

## Example: Full Validation Run

```bash
lopper -W memory_all --memmap=memory-layout.txt \
    system-device-tree.dts \
    -- domains.yaml lop-domain.dts \
    output-device-tree.dts
```

This will:
1. Validate cell properties and reg formats (EARLY phase)
2. Check for reserved-memory overlaps (POST_YAML phase)
3. Check domain and cross-domain overlaps (POST_DOMAIN phase)
4. Generate a visual memory map to `memory-layout.txt`

## Programmatic API

For custom tools, the audit module can be used directly:

```python
from lopper.audit import (
    MemoryMap,
    collect_memory_regions,
    check_reserved_memory_overlaps,
    MemoryVisualizer,
)

# Collect all memory regions from a tree
memory_map = collect_memory_regions(tree)

# Check for overlaps
overlaps = check_reserved_memory_overlaps(tree)
for overlap in overlaps:
    if not overlap.is_intentional:
        print(f"Problem: {overlap.region1.source_path} overlaps "
              f"{overlap.region2.source_path}")

# Visualize
viz = MemoryVisualizer()
print(viz.render(memory_map))
```

## Use Cases

### Detecting Reserved-Memory Conflicts

When multiple teams define reserved-memory regions, conflicts can occur.
The audit framework catches these before they cause runtime failures:

```bash
lopper -W memory_overlap system-device-tree.dts -- domains.yaml output.dts
```

If two reserved-memory regions overlap:
```
WARNING: Reserved memory overlap detected:
  Region 1: /reserved-memory/team_a_buffer@10000000 [0x10000000-0x10200000] (2 MB)
  Region 2: /reserved-memory/team_b_pool@10100000 [0x10100000-0x10300000] (2 MB)
  Overlap: 1 MB at 0x10100000
```

### Validating OpenAMP Carveouts

For OpenAMP flows, the memory audit replaces the older `xlnx_validate_carveouts()`
with more comprehensive checks:

```bash
lopper -W memory_all -i openamp-spec.yaml system.dts linux.dts
```

This validates:
- Carveouts don't overlap (unless shared-dma-pool)
- Carveouts fit within reserved-memory
- Cross-domain memory assignments are consistent

### Debugging Memory Layout

When something isn't working, visualize the memory map:

```bash
lopper --memmap=- system-device-tree.dts -- domains.yaml /dev/null
```

The ASCII map shows all memory regions with overlap highlighting, making it
easy to spot configuration issues.

## Implementation Details

The audit framework is implemented as a package in `lopper/audit/`:

| Module | Purpose |
|--------|---------|
| `__init__.py` | Re-exports for backwards compatibility |
| `base.py` | ValidationPhase, ValidationResult, BaseValidator |
| `core.py` | Phandle validation (existing audit functions) |
| `memory.py` | MemoryRegion, MemoryMap, validation checks |
| `memviz.py` | ASCII memory map visualization |

Test coverage: 99 tests across `test_audit_base.py`, `test_audit_memory.py`,
and `test_audit_memviz.py`.

## Related

- [Lopper Audit Framework](audit-framework.md) - Base audit infrastructure
- [Domain Processing](domains.md) - How domains.yaml affects memory
- [Reserved Memory Handling](reserved-memory.md) - DT spec and SDT extensions
