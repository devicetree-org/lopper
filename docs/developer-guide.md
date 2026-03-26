# Lopper Developer's Guide

This guide covers how to extend lopper with custom lopper operations (lops) and
assist modules. It is intended for developers, ecosystem contributors, and
third-party integrators who need to add custom device tree transformations.

## Table of Contents

- [Overview](#overview)
- [Understanding Lopper Processing](#understanding-lopper-processing)
- [Writing Lopper Operations (Lops)](#writing-lopper-operations-lops)
  - [Lop File Structure](#lop-file-structure)
  - [Available Lop Types](#available-lop-types)
  - [Classification and Organization](#classification-and-organization)
  - [Where to Put Lops](#where-to-put-lops)
- [Writing Assist Modules](#writing-assist-modules)
  - [Assist Structure](#assist-structure)
  - [The is_compat Function](#the-is_compat-function)
  - [The Processing Function](#the-processing-function)
  - [Working with LopperTree](#working-with-loppertree)
- [Contributing Lops and Assists](#contributing-lops-and-assists)
- [Best Practices](#best-practices)
- [Examples](#examples)

## Overview

Lopper is a data-driven device tree processor. It takes a system device tree
and optionally supporting input files (YAML, overlays, device tree fragments)
as input, applies a series of operations, and generates output artifacts.
While device trees are the most common output, lopper can produce any type of
artifact: modified device trees, configuration files, linker scripts, header
files, or raw data. The output format is determined by the operations and
assists applied during processing.

There are two primary extension mechanisms:

1. **Lopper Operations (lops)** - Declarative rules written in DTS format that
   describe tree modifications
2. **Assist Modules** - Python scripts that perform complex transformations
   and generate arbitrary output

Many use cases can be addressed with lops alone. Assists are used when the
transformation requires programmatic logic that cannot be expressed declaratively,
or when generating non-device-tree output formats.

## Understanding Lopper Processing

Lopper processes inputs in the following order:

1. **Setup** - Load the system device tree and supporting input files
2. **Input Normalization** - Preprocess and compile all inputs
3. **Operation Execution** - Run lops and assists in priority order
4. **Finalization** - Validate and write output

The audit framework runs validation checks at key points throughout processing
(early, post-YAML, post-domain). See [memory-audit.md](memory-audit.md) for
details on available validation flags.

Assists can be invoked in several ways:
- **Via lops** - An assist lop triggers the assist during operation execution,
  intermixed with other lops based on priority and order
- **Via command line** - Assists specified with `-a` or after `--` are chained
  and run after lop processing completes
- **Automatically** - When `--auto` is passed, lopper searches for `.lop` files
  that match input filenames (using glob patterns) and queues them for execution

This allows assists to be pipelined, with the output of one assist feeding into
the next, or interleaved with lop operations as needed.

**Important:** Lops and assists operate on a shared device tree representation.
Any modifications made by a lop or assist are immediately visible to all
subsequent operations in the pipeline. This is both powerful and requires care:

- **Leverage it:** Add properties or nodes that downstream assists depend on
- **Be careful:** Unintended modifications affect everything that follows
- **Document it:** Note what tree changes your lop/assist makes

Lops are processed by priority (1 = highest, 10 = lowest). Within a priority
level, lops execute in the order they appear in the file. This allows you to:

- Load modules before using them (priority 1)
- Process domains (priority 2-5)
- Generate output (priority 6-10)

## Writing Lopper Operations (Lops)

### Lop File Structure

A lop file is a DTS file with a specific structure:

```dts
/dts-v1/;

/ {
    compatible = "system-device-tree-v1";
    // Optional: priority = <1>;  (1=highest, 10=lowest)

    lops {
        lop_0 {
            compatible = "system-device-tree-v1,lop,<lop-type>";
            // lop-specific properties
        };
        lop_1 {
            compatible = "system-device-tree-v1,lop,<lop-type>";
            // lop-specific properties
        };
    };
};
```

Key points:
- The root `compatible` string identifies this as a lopper operations file
- The `lops` node contains individual operations
- Operations are named `lop_<number>` by convention (numbering for readability only)
- Operations execute in the order they appear, NOT by their number

### Available Lop Types

Lopper provides many lop types for different operations. The most commonly used
when adding custom transformations are:

| Lop Type | Purpose |
|----------|---------|
| `load` | Load a Python assist module |
| `modify` | Change, add, or remove nodes and properties |
| `select` | Build sets of nodes for subsequent operations |
| `code` | Execute inline Python code |
| `assist` | Call a loaded assist module |
| `output` | Write nodes to an output file |
| `add` | Insert new nodes into the tree |

For complete documentation of all lop types including `conditional`, `exec`,
`tree`, `xlate`, and `meta`, see [README-architecture.md](../README-architecture.md).

### Classification and Organization

Lops and assists are organized by purpose. Understanding the categories helps
you decide whether your code belongs in the lopper repository or an external
location, and what naming conventions to follow.

#### Lop Categories

| Category | Purpose | Examples |
|----------|---------|----------|
| **infrastructure** | Setup, module loading | `lop-load.dts` |
| **domain** | Domain generation/processing | `lop-domain-*.dts` |
| **architecture** | CPU architecture specific | `lop-a72-imux.dts`, `lop-microblaze.dts` |
| **platform** | Board/SoC specific | `lop-versal-vck190_*.dts` |
| **os** | Operating system specific | `lop-domain-linux.dts`, `lop-domain-zephyr.dts` |
| **transform** | Input format translation | `lop-xlate-yaml.dts` |
| **utility** | General purpose operations | `lop-delete-chosen.dts` |

#### Assist Categories

| Category | Purpose | Examples |
|----------|---------|----------|
| **baremetal** | Bare-metal BSP/config generation | `baremetalconfig_xlnx.py` |
| **domain** | Domain extraction/isolation | `gen_domain_dts.py`, `domain_access.py` |
| **openamp** | Multi-processing framework support | `openamp.py`, `openamp_xlnx.py` |
| **output** | Non-DTS artifact generation | `baremetallinker_xlnx.py` (linker scripts) |
| **utility** | Generic operations | `extract.py`, `grep.py`, `compare.py` |
| **library** | Shared code (no `is_compat`) | `lopper_lib.py`, `common_utils.py` |

#### Naming Conventions

Follow this pattern for consistency:

- **Lops**: `lop-<category>-<target>.dts`
  - `lop-domain-linux.dts` - Domain lop for Linux
  - `lop-a72-imux.dts` - Architecture lop for A72 interrupt mux
  - `lop-xlate-yaml.dts` - Transform lop for YAML translation

- **Assists**: `<target>_<category>.py` or `<category>_<vendor>.py`
  - `openamp_xlnx.py` - OpenAMP assist with Xilinx extensions
  - `baremetalconfig_xlnx.py` - Baremetal configuration for Xilinx

- **Vendor-specific**: Include vendor prefix in the name
  - `xlnx_` prefix for Xilinx-specific code
  - Vendor compatible strings: `xlnx,<device>` or `<vendor>,<device>`

### Where to Put Lops

Deciding where to place lops and assists depends on their scope and reusability.
Use this decision tree:

```
Is it reusable across multiple platforms/projects?
├─ Yes → Consider contributing to lopper repository
│        ├─ Generic (no vendor-specific code) → lopper/lops/ or lopper/assists/
│        └─ Vendor-specific but broadly useful → lopper/lops/xlnx/ (future)
└─ No → Keep in external location
         ├─ Proprietary or tightly coupled to vendor tooling → External repo
         └─ Single project use → Project directory
```

#### Location Options

| Location | Use Case | Search Priority |
|----------|----------|-----------------|
| External directory (`-A`) | Vendor/project-specific code | First (highest) |
| `lopper/lops/` | Core lopper operations | After external |
| `lopper/assists/` | Core assist modules | After external |
| Project directory | Single-project transformations | Via `-i` |

#### External and Internal Paths Work Together

Lopper searches external paths **before** internal paths. This allows you to:
- Override built-in lops/assists with your own implementations
- Keep proprietary code separate from the open-source repository
- Develop and test new assists before contributing upstream

The search order (from `lopper/__init__.py`):
1. Paths specified with `-A` / `--assist-paths` (colon-separated)
2. `lopper/` directory
3. `lopper/assists/` directory
4. `lopper/lops/` directory
5. Directories from `LOPPER_INPUT_DIRS` environment variable
6. Current working directory

**Example: Using external assists alongside built-in ones**

```bash
# External assists are found first, then built-in
lopper -A /opt/vendor/assists:/home/user/my-assists \
       -i my-lops/lop-load.dts system.dts output.dts
```

#### Project Directory Structure

For custom hardware or software, create a directory structure like:

```
my-project/
  lops/
    lop-load-my-assists.dts   # Load assists first (priority 1)
    lop-my-platform.dts       # Platform-specific operations
  assists/
    my_platform.py            # Processing logic
```

Run with:
```bash
lopper -A my-project/assists -i my-project/lops/lop-load-my-assists.dts \
       -i my-project/lops/lop-my-platform.dts system.dts output.dts
```

#### Generic vs Vendor-Specific

| Scope | Criteria | Recommended Location |
|-------|----------|----------------------|
| **Generic** | Reusable across platforms, no vendor prefixes, follows DT spec | `lopper/lops/`, `lopper/assists/` |
| **Vendor (in-repo)** | Platform-specific but open-sourceable, broadly useful | `lopper/lops/xlnx/` (pending directory creation) |
| **Vendor (external)** | Proprietary, tightly coupled to vendor tooling | External repository, loaded via `-A` |
| **Project-specific** | Single project use, not reusable | Project's own directory |

**Examples:**

- `lop-domain-linux.dts` - Generic: creates Linux domain from any SDT
- `lop-xlate-yaml.dts` - Generic: translates YAML to DT nodes
- `openamp_xlnx.py` - Vendor: OpenAMP with Xilinx-specific remoteproc handling
- `lop-versal-vck190-pcie.dts` - Platform: fixups for specific board

## Writing Assist Modules

Assists are Python modules that perform complex tree transformations.

### Assist Structure

```python
#
# Copyright (c) 2024 Your Company. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause
#

import sys
import re

# Import lopper utilities
sys.path.append(os.path.dirname(__file__))
from lopper import Lopper
import lopper_lib

def is_compat(node, compat_string_to_test):
    """Return the processing function if this assist handles the given id."""
    if re.search("my-vendor,my-processor-v1", compat_string_to_test):
        return process_domain
    return ""

def process_domain(target_node, sdt, options):
    """Process the target domain node."""
    verbose = options.get('verbose', 0)
    outdir = options.get('outdir', '.')
    args = options.get('args', [])

    tree = sdt.tree

    # Find nodes
    for node in tree:
        if node.propval('compatible') and 'my-device' in str(node.propval('compatible')):
            if verbose:
                print(f"Found device: {node.abs_path}")
            # Modify the node
            node['status'] = 'okay'

    return True
```

### The is_compat Function

Every assist must implement `is_compat()`:

```python
def is_compat(node, compat_string_to_test):
    """
    Check if this assist handles the given compatibility string.

    Args:
        node: The node being processed (may be None for command-line assists)
        compat_string_to_test: The id string from the lop or command line

    Returns:
        The processing function if compatible, empty string otherwise
    """
    if re.search("my-vendor,my-processor-v1", compat_string_to_test):
        return my_processing_function
    return ""
```

The returned function will be called with `(target_node, sdt, options)`.

### The Processing Function

```python
def my_processing_function(target_node, sdt, options):
    """
    Process the device tree.

    Args:
        target_node: The node specified in the lop (or None)
        sdt: LopperSDT object containing the system device tree
        options: Dictionary with:
            - 'verbose': Verbosity level (int)
            - 'outdir': Output directory path
            - 'args': Additional arguments from command line

    Returns:
        True on success, False on failure
    """
    tree = sdt.tree

    # Your processing logic here

    return True
```

### Working with LopperTree

The LopperTree provides Pythonic access to the device tree:

```python
tree = sdt.tree

# Iterate all nodes
for node in tree:
    print(node.abs_path)

# Access node by path
cpu_node = tree['/cpus/cpu@0']

# Check if node exists
if '/memory' in tree:
    mem_node = tree['/memory']

# Access properties
compat = node['compatible'].value
reg = node['reg'].value

# Modify properties
node['status'] = 'disabled'
node['my-property'] = [0x1, 0x2, 0x3]

# Delete properties
del node['unwanted-property']

# Access children
for child in node.child_nodes:
    print(child.name)

# Find parent
parent = node.parent

# Check node type
if node.type == 'cpu':
    # ...
```

Property values are returned as lists. Common patterns:

```python
# String property
model = node['model'].value[0]  # "my-board"

# Integer property
num_cells = node['#address-cells'].value[0]  # 2

# Phandle property
clocks = node['clocks'].value  # [phandle1, arg1, phandle2, arg2, ...]

# Check if property exists
if 'reg' in node:
    # ...
```

## Contributing Lops and Assists

When contributing lops or assists to the lopper repository, follow these
standards to ensure consistency and maintainability.

### Documentation Requirements

Every lop and assist must include documentation before being merged:

#### Lop File Header

```dts
/*
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * lop-<category>-<target>.dts - Brief one-line description
 *
 * Purpose:
 *   Explain what this lop does and when to use it.
 *
 * Usage:
 *   lopper -i lop-<category>-<target>.dts system.dts output.dts
 *
 * Prerequisites:
 *   - List any lops that must run first (e.g., lop-load.dts)
 *   - List any input file requirements
 *
 * Output:
 *   Describe what changes are made to the tree or what files are generated.
 */
```

#### Assist Module Header

Assists should provide help text accessible via `--help` on the command line.
This allows users to discover motivation, inputs, and outputs without reading
source code.

```python
#
# SPDX-License-Identifier: BSD-3-Clause
#
"""
Brief one-line description.

Purpose:
    Explain what this assist does and when to use it.

Compatible strings:
    - "vendor,processor-v1" - Via lop assist node
    - "module,my_assist" - Via command line invocation

Usage:
    # Via lop
    lop_0 {
        compatible = "system-device-tree-v1,lop,assist-v1";
        id = "vendor,processor-v1";
    };

    # Via command line
    lopper -a my_assist.py system.dts -- my_assist output.dts

Arguments (after --):
    output.dts    Output file path
    --flag        Optional flag description

Output:
    Describe generated artifacts.
"""
```

**Implementing --help support:**

```python
def my_processing_function(target_node, sdt, options):
    args = options.get('args', [])

    if '--help' in args or '-h' in args:
        print("""
my_assist - Brief description

Purpose:
    Explain motivation and use case.

Usage:
    lopper ... -- my_assist [OPTIONS] output.dts

Options:
    -h, --help     Show this help message
    --verbose      Enable verbose output

Inputs:
    Requires system device tree with <specific nodes/properties>.

Outputs:
    - output.dts: Modified device tree with <changes>
    - output.h: Generated header file (if applicable)

Notes:
    This assist modifies the shared device tree. Changes are visible
    to all subsequent assists in the pipeline.
""")
        return True

    # Normal processing...
```

For lops, the file header comment serves as the help text. The entire lop file
documents its motivation, prerequisites, and effects.

### Required Elements

| Element | Lops | Assists | Description |
|---------|------|---------|-------------|
| SPDX license | Required | Required | Use BSD-3-Clause unless otherwise agreed |
| Purpose comment | Required | Required | Explain why this exists |
| Compatible string | In lop nodes | In `is_compat()` docstring | How to invoke |
| Usage example | Required | Required | Show command line or lop invocation |
| Prerequisites | If applicable | If applicable | Dependencies on other lops/assists |
| Help text | File header | `--help` handler | Allow users to discover usage without reading source |
| Tree modifications | Document in header | Document in help | What nodes/properties are added, modified, or removed |

### Testing Expectations

Before submitting:

1. **Test with the lopper test suite** - Run `pytest tests/` and ensure no regressions
2. **Test with representative device trees** - Don't assume a specific structure
3. **Test edge cases** - Missing nodes, empty properties, malformed input
4. **Document tested configurations** - Note which platforms/boards were tested

### Review Checklist

Reviewers will check:

- [ ] Header comment with purpose, usage, and compatible strings
- [ ] SPDX license identifier present
- [ ] Follows naming conventions (`lop-<category>-<target>.dts`)
- [ ] No hardcoded paths that assume specific directory structure
- [ ] Uses `lopper_lib` utilities where appropriate
- [ ] Handles missing nodes gracefully (no crashes on unexpected input)
- [ ] Verbose output controlled by verbosity level
- [ ] Compatible strings documented in `is_compat()` or lop nodes
- [ ] Assists implement `--help` support for discoverability
- [ ] Tree modifications documented (what nodes/properties change)

### Determining Generic vs Vendor-Specific

Ask these questions to determine classification:

1. **Does it use vendor-specific compatible strings?** (e.g., `xlnx,zynqmp`)
   - Yes → Vendor-specific
   - No → Potentially generic

2. **Does it depend on vendor tooling or proprietary information?**
   - Yes → Keep external or in vendor subdirectory
   - No → May be suitable for core lopper

3. **Would other vendors benefit from this code?**
   - Yes → Consider making it generic or parameterizing vendor details
   - No → Vendor-specific location is appropriate

4. **Is the implementation tied to a specific SoC/board?**
   - Yes → Platform-specific (`lop-<platform>-*.dts`)
   - No → May be architecture-level or generic

## Best Practices

### Lop Design

1. **Keep lops focused** - Each lop file should have a single purpose
2. **Follow naming conventions** - Use `lop-<category>-<target>.dts`:
   - `lop-domain-linux.dts` - Domain category, Linux target
   - `lop-arch-a72.dts` - Architecture category, A72 target
   - `lop-platform-vck190.dts` - Platform category, VCK190 target
3. **Document with comments** - Explain why, not just what
4. **Use select + modify** - More maintainable than hardcoded paths
5. **Test incrementally** - Add one lop at a time and verify
6. **Set appropriate priority** - Load modules at priority 1, process at 2-5, output at 6-10

### Assist Design

1. **Check verbose levels** - Only print debug info when requested
2. **Handle missing nodes gracefully** - Don't crash on unexpected input
3. **Return meaningful errors** - Help users diagnose problems
4. **Use lopper_lib utilities** - Don't reinvent common operations
5. **Document the id string** - Users need to know what to put in lops
6. **Follow naming conventions** - Use `<target>_<vendor>.py` for vendor-specific, descriptive names for generic
7. **Implement --help** - Users should be able to discover usage without reading source
8. **Remember the shared tree** - Your modifications are visible to all downstream operations; document what you change and be mindful of side effects

### General

1. **Test with multiple device trees** - Don't assume specific structure
2. **Use regex carefully** - Overly broad patterns cause subtle bugs
3. **Version your lops** - Track changes as hardware evolves
4. **Consider priority** - Load before use, transform before output

## Examples

### Example 1: Disable Ethernet on Custom Board

```dts
/dts-v1/;

/ {
    compatible = "system-device-tree-v1";

    lops {
        // Find and disable all ethernet controllers
        lop_0 {
            compatible = "system-device-tree-v1,lop,select-v1";
            select_1;
            select_2 = "/.*:compatible:.*ethernet.*";
        };

        lop_1 {
            compatible = "system-device-tree-v1,lop,modify";
            modify = ":status:disabled";
        };
    };
};
```

### Example 2: Add Reserved Memory Region

```dts
/dts-v1/;

/ {
    compatible = "system-device-tree-v1";

    lops {
        lop_0 {
            compatible = "system-device-tree-v1,lop,add";
            node_src = "my_reserved";
            node_dest = "/reserved-memory/my_reserved";

            my_reserved {
                compatible = "shared-dma-pool";
                reg = <0x0 0x70000000 0x0 0x10000000>;
                no-map;
            };
        };
    };
};
```

### Example 3: Platform-Specific Assist

```python
"""
Platform fixups for My Custom Board.

Usage:
    lopper -a my_platform.py system.dts -- my_platform output.dts

Or via lop:
    lop_0 {
        compatible = "system-device-tree-v1,lop,assist-v1";
        id = "my-company,my-board-v1";
    };
"""

import sys
import re

def is_compat(node, compat_string_to_test):
    if re.search("my-company,my-board-v1", compat_string_to_test):
        return apply_fixups
    # Command line invocation
    if re.search("module,my_platform", compat_string_to_test):
        return apply_fixups
    return ""

def apply_fixups(target_node, sdt, options):
    verbose = options.get('verbose', 0)
    tree = sdt.tree

    # Add board-specific property to root
    tree['/']['board-revision'] = 'rev-b'

    # Fix clock frequencies for this board
    for node in tree:
        if 'clock-frequency' in node:
            # Board runs clocks 10% slower
            freq = node['clock-frequency'].value[0]
            node['clock-frequency'] = int(freq * 0.9)
            if verbose:
                print(f"Adjusted clock for {node.abs_path}")

    return True
```

### Example 4: Conditional Processing

```dts
/dts-v1/;

/ {
    compatible = "system-device-tree-v1";

    lops {
        // Check if this is a ZynqMP platform
        lop_0 {
            compatible = "system-device-tree-v1,lop,select-v1";
            select_1;
            select_2 = "/:compatible:.*zynqmp.*";
        };

        // Only run this modify if ZynqMP was found
        lop_1 {
            compatible = "system-device-tree-v1,lop,modify";
            noexec = "not __selected__";
            modify = "/:platform-type:zynqmp";
        };

        // Alternative: check for Versal
        lop_2 {
            compatible = "system-device-tree-v1,lop,select-v1";
            select_1;
            select_2 = "/:compatible:.*versal.*";
        };

        lop_3 {
            compatible = "system-device-tree-v1,lop,modify";
            noexec = "not __selected__";
            modify = "/:platform-type:versal";
        };
    };
};
```

## Related Documentation

- [README-architecture.md](../README-architecture.md) - Detailed processing flow and all lop types
- [memory-audit.md](memory-audit.md) - Memory validation framework
- [README.md](../README.md) - Installation and basic usage
