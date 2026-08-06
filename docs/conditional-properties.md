# Conditional Properties and Overlay Trees

Lopper supports **conditional properties** and **conditional nodes** in YAML
system device tree files using a sigil syntax.  These allow a single YAML file
to describe multiple OS or configuration variants; each variant produces a
separate, fully-resolved device tree at processing time without modifying the
base tree.

---

## Sigil Syntax

Property keys and node names may carry one or more `!`-delimited sigils:

```
property-name!condition1!condition2!merge-scheme
node-name!condition
```

### Sigil segments

Each segment after the first `!` is classified independently:

| Segment | Meaning |
|---------|---------|
| Any name that is **not** a merge scheme keyword | Condition name (e.g. `linux`, `zephyr`, `secure`) |
| `replace` | Merge scheme: replace the base value (default) |
| `append` | Merge scheme: append overlay value to base value |
| `prepend` | Merge scheme: prepend overlay value before base value |
| `delete` | Merge scheme: remove the property from the overlay tree |

Multiple conditions and a merge scheme may appear together in any order.
`!` is not a legal character in DT property or node names, so it is
unambiguous as a delimiter.

### Examples

```yaml
cpus:
  cpu@0:
    compatible: base-cpu

chosen:
  # 'bootargs' is present in both overlay trees; scheme is append
  bootargs: "console=ttyPS0,115200"
  bootargs!linux!append: "root=/dev/mmcblk0p2 rw"
  bootargs!zephyr!append: "CONFIG_DEBUG=y"

  # 'stdout-path' is unconditional — same in all trees
  stdout-path: "serial0:115200n8"

  # A whole node present only in the linux overlay tree
  linux-only-node!linux:
    status: "okay"
```

After parsing, the base tree contains `bootargs: "console=ttyPS0,115200"` and
`stdout-path`.  The `linux` overlay tree additionally has
`bootargs: "console=ttyPS0,115200 root=/dev/mmcblk0p2 rw"` and
`linux-only-node`.  The `zephyr` overlay tree has
`bootargs: "console=ttyPS0,115200 CONFIG_DEBUG=y"`.

---

## Accessing Overlay Trees

The `overlay_tree(name)` method on `LopperTree` returns a fully merged,
self-contained copy of the base tree with the named condition applied:

```python
linux_tree  = sdt.tree.overlay_tree('linux')
zephyr_tree = sdt.tree.overlay_tree('zephyr')
```

The result is a normal `LopperTree` — no hidden state, no view contexts.
The base tree is never modified.  Results are lazy-built and cached on first
call.

If no overlay has been registered for the given name, `overlay_tree()` returns
`None`.

---

## Activating Conditions Without Tree Pruning

Use the standalone `conditional_properties` assist when conditional
properties are wanted without the domain filtering performed by
`domain_access`:

```bash
# Select a condition directly; no /domains node is required.
lopper system-top.dts linux.dts -- conditional_properties -c linux

# Or read lopper,activate from a node (with os,type as a fallback).
lopper system-top.dts linux.dts -- \
    conditional_properties -t /domains/linux-domain
```

The assist replaces the current working tree with the fully merged result of
`overlay_tree(name)`. It does not ref-count, filter memory, inject a chosen
node, or prune anything. Use `--no-os-fallback` if only an explicit
`lopper,activate` property should be accepted.

This also makes it possible to activate a condition before another assist:

```bash
lopper system-top.dts output.dts -- \
    conditional_properties -c linux -- some_transform
```

## Automatic Domain Activation via `lopper,activate`

When using the `domain_access` assist, add a `lopper,activate` property to
the domain node to automatically select the correct overlay tree before any
domain processing begins:

```yaml
domains:
  linux-domain:
    compatible: openamp,domain-v1
    lopper,activate: linux
    cpus: ...
    memory: ...

  zephyr-domain:
    compatible: openamp,domain-v1
    lopper,activate: zephyr
    cpus: ...
    memory: ...
```

`core_domain_access()` delegates to the same activation helper used by the
standalone assist, and uses the merged result for all subsequent
ref-counting, memory filtering, chosen node injection, and pruning.  If
`lopper,activate` is absent, `os,type` is used as a fallback so existing
domain YAML files work without modification.

---

## Per-Domain Driver Binding ("OpenAMP Pattern")

Sigils may appear on any node in the tree, not just nodes under `/domains/`.
This is the common case when a device needs different kernel bindings depending
on which OS owns it:

```yaml
# system-device-tree.yaml

axi:
  timer@f1e90000:
    compatible: "cdns,ttc"          # base — RPU baremetal and default domains
    compatible!linux: "uio"         # linux overlay replaces with UIO binding

domains:
  APU_Linux:
    compatible: openamp,domain-v1
    lopper,activate: linux          # selects overlay_tree('linux')
    cpus: ...
    memory: ...

  RPU1_BM:
    compatible: openamp,domain-v1
    # no lopper,activate — base tree used; compatible stays "cdns,ttc"
    cpus: ...
    memory: ...
```

When `domain_access` processes `APU_Linux` it calls `overlay_tree('linux')`;
the resulting tree has `compatible = "uio"` at `/axi/timer@f1e90000`.
When it processes `RPU1_BM` the base tree is used and `compatible` is still
`"cdns,ttc"`.

The base tree is never modified.  Each domain gets a clean, independent view
of the same source YAML.

---

## Multi-Pass Workflows

In many build pipelines the YAML-to-DTS translation and the domain processing
are separate lopper invocations.  By default, lopper automatically preserves
sigil overlay data across exactly two passes:

**Pass 1 — YAML translation:**
```bash
lopper -f --permissive --enhanced \
    -i base.yaml -i overrides.yaml \
    system-top.dts intermediate.dts
```

Lopper embeds the condition registry as a `/__lopper-overlays__` node inside
`intermediate.dts`.  No extra files or flags are needed.

**Pass 2 — domain processing:**
```bash
lopper -f --permissive \
    intermediate.dts APU_Linux.dts \
    -- domain_access -t /domains/APU_Linux
```

Lopper deserialises `/__lopper-overlays__` on load, making
`overlay_tree('linux')` available to `domain_access`.  The node is removed
before `APU_Linux.dts` is written so the final output is clean.

### Preserving overlays across more than two passes

For pipelines with additional intermediate steps, pass `--emit-embedded-overlays`
to any non-final invocation to re-embed the overlay registry into that pass's
output:

```bash
lopper -f --permissive --emit-embedded-overlays \
    intermediate.dts intermediate2.dts \
    -- some_transform

lopper -f --permissive \
    intermediate2.dts APU_Linux.dts \
    -- domain_access -t /domains/APU_Linux
```

### Alternative: YAML sidecar

`--emit-overlay-sidecar` writes `<out>.overlays.yaml` alongside the output DTS.
Feed it with `-i` on subsequent passes.  Unlike the embedded node, the sidecar
preserves merge schemes (`append`/`prepend`/`delete`) with full fidelity since
it round-trips through the sigil parser:

```bash
# Pass 1: emit intermediate.dts + intermediate.overlays.yaml
lopper -f --permissive --enhanced --emit-overlay-sidecar \
    -i base.yaml -i overrides.yaml \
    system-top.dts intermediate.dts

# Pass 2: feed the sidecar explicitly
lopper -f --permissive \
    -i intermediate.overlays.yaml \
    intermediate.dts APU_Linux.dts \
    -- domain_access -t /domains/APU_Linux
```

### Alternative: per-condition DTS overlay files

`--emit-overlay-dtso` writes one `<out>.<condition>.dtso` file per condition.
These are standard DTS overlay files and can be inspected or fed with `-i`.
Note that merge schemes are not representable in DTS overlays — the output is
always replace-mode.

### Summary of overlay emit options

| Flag | Output | Merge schemes preserved | Notes |
|------|--------|------------------------|-------|
| *(default)* | `/__lopper-overlays__` in output DTS | Yes | Automatic two-pass; removed in final output |
| `--emit-embedded-overlays` | `/__lopper-overlays__` in output DTS | Yes | Re-embeds on every pass; use for >2-pass pipelines |
| `--emit-overlay-sidecar` | `<out>.overlays.yaml` | Yes | Full sigil fidelity; feed with `-i` |
| `--emit-overlay-dtso` | `<out>.<cond>.dtso` per condition | No (replace only) | Human-readable; standard DTS overlay format |

All emit options only affect DTS (`.dts`/`.dtsi`) output; YAML and JSON output
formats are unaffected.

---

## DTS Overlay Files

DTS overlay files (containing `&label { ... }` syntax) are handled by the same
mechanism.  Lopper compiles each overlay file as a standalone plugin, registers
its nodes under `overlay_subtrees[stem]` (where `stem` is the filename without
extension), and makes them accessible via `sdt.tree.overlay_tree(stem)`.

```bash
lopper system-top.dts output.dts -i my-overlay.dtso
```

```python
# Inside an assist:
pl_tree = sdt.tree.overlay_tree('my-overlay')
```

---

## Complete YAML Example

```yaml
# system-device-tree.yaml

cpus:
  cpu@0:
    compatible: "arm,cortex-a53"
    device_type: "cpu"

chosen:
  # Base bootargs — in every tree
  bootargs: "earlycon clk_ignore_unused"

  # Linux appends its specific args
  bootargs!linux!append: "root=/dev/mmcblk0p2 rootwait rw"

  # Zephyr uses a completely different value (replace is default)
  bootargs!zephyr: "zephyr.hello"

  # stdout-path is unconditional
  stdout-path: "serial0:115200n8"

domains:
  linux-domain:
    compatible: "openamp,domain-v1"
    lopper,activate: linux
    cpus:
      - cluster: cpu0
        cpumask: 0xf
        mode:
          secure: false
          el: 0x1
    memory:
      - start: 0x40000000
        size: 0x40000000

  zephyr-domain:
    compatible: "openamp,domain-v1"
    lopper,activate: zephyr
    cpus:
      - cluster: cpu0
        cpumask: 0x1
        mode:
          secure: false
          el: 0x1
    memory:
      - start: 0x10000000
        size: 0x10000000
```

Running lopper with `gen_domain_dts` for the `linux-domain` will produce a
tree with `bootargs: "earlycon clk_ignore_unused root=/dev/mmcblk0p2 rootwait rw"`.
The `zephyr-domain` run will produce `bootargs: "zephyr.hello"`.  Neither
run modifies the original system device tree.
