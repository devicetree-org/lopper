# sdt-from-linux: Architecture

This document describes how the components fit together to turn an
upstream Linux device tree (plus optional Zephyr DT and a small
per-board YAML) into a System Device Tree.

For the user-facing how-to — commands, reference boards, running the
pipeline — see `docs/sdt-from-linux.md`. The material here is the
architectural breakdown for contributors who need to extend the
pipeline (add a new SoC, add a new capability, modify the schema, or
understand why a component is shaped the way it is).

## Why this exists

A Linux device tree typically describes one OS's view of one CPU
cluster on a heterogeneous SoC. Producing the per-OS device trees,
OpenAMP / rpmsg configuration, baremetal BSPs, hypervisor configs,
and other downstream artifacts a multi-OS integration needs is done
today by a variety of means — vendor-specific tooling, hand-curation
per OS, or downstream BSP forks. We suggest a **System Device Tree**
is a better approach: a single whole-SoC description that captures
every cluster, every memory region, the IPC fabric, and the
partitioning intent in one place, with a single source of truth that
downstream tooling slices into per-OS views.

Existing SDT-generation flows typically require vendor-specific
hardware description files (XSA for AMD, MCUXpresso configs for NXP,
etc.) — inputs you may not have or may not want to depend on. The
sdt-from-linux pipeline produces a similar artifact from material
the OS communities already maintain: the upstream Linux DT, the
upstream Zephyr DT (when available), and a small hand-written
per-board YAML. Some content that comes only from full hardware
descriptions (complete address-map traceability, signal-level
routing, certain vendor-specific properties) will not appear in the
resulting SDT; the trade-off is that the pipeline runs against
material any OSS contributor can obtain.

## Pipeline overview

```
   Linux DT (upstream)         ─────────────────────────────┐
                                                            │
   Zephyr DT (upstream)        ─┐                           │
   Per-board augment YAML      ─┴─→ compose_non_linux ──┐   │
                                    (rich props,         │   │
                                     "&label" phandles)  │   │
                                                         ▼   ▼
                                                    non-linux.yaml ──→ assemble_sdt
                                              (openamp,domain-v1,non-linux)         │
                                                                                    ▼
                                                                            system-top.dts
                                                                              (Linux DT base
                                                                               + cpus,cluster wrap
                                                                               + non-Linux overlay)
                                                                                    │
                  User partition intent ───────────────────────────────┐            │
                  (domains.yaml,                                       │            │
                   openamp,domain-v1)                                  ▼            ▼
                                                                  existing downstream tools
                                                           (per-OS DT slicing, BSP gen, OpenAMP,
                                                            FPGA overlays, hypervisor configs,
                                                            cross-OS validation, …)
```

The pipeline is two-stage: extraction (`compose_non_linux`)
captures everything the Linux DT does not already carry — co-
processor clusters, TCM/OCRAM regions, the co-processor side of the
IPC fabric, augment-derived reserved-memory carve-outs — into a
rich-property YAML where every kept node retains its full DT
property set (with phandle refs encoded as `"&label"` strings).
Assembly (`assemble_sdt`) then loads the Linux DT as the SDT base
and overlays the non-Linux content on top, so Linux-side nodes
round-trip verbatim (preserving the bootability of the SDT after
downstream per-OS slicing) and non-Linux content joins the merged
tree as siblings of the Linux clusters.

### Where the user's two YAML inputs fit

Two different kinds of user-supplied YAML appear in the flow above
and it is worth distinguishing them:

- **`augment.yaml`** — per-board *integration decisions* (reserved-
  memory carve-outs for co-processor firmware, rpmsg shared regions,
  board peripherals upstream trees omit). Ships in
  `lopper/data/boards/<board>/`, uses the
  `openamp,domain-v1,board-augment` compatible, and enters the flow
  at the **extraction stage** so the resulting SDT carries the
  integration facts as first-class nodes.
- **`domains.yaml`** — per-deployment *partition intent* (which
  devices, clusters, and memory belong to which OS). User-written
  per deployment, uses the existing `openamp,domain-v1` compatible,
  and enters the flow at the **downstream-tooling stage** where
  existing Lopper assists (`gen_domain_dts`, `openamp`, the audit
  framework, etc.) consume `system-top.dts` + `domains.yaml`
  together to produce per-OS artifacts.

The two compatibles deliberately share a schema family so a single
parser handles both. They are kept as separate inputs because their
lifecycles differ: integration facts are properties of the board
and travel with the board configuration; partition intent is a
property of a deployment and travels with the deployment.

## Components

### Inputs

#### Upstream device trees (`lopper/data/upstream/`)

This directory holds vendored copies of the Linux kernel and Zephyr
DT material the pipeline reads. Its layout mirrors upstream
(`linux/include/dt-bindings/`, `linux/arch/arm64/boot/dts/<vendor>/`,
`zephyr/dts/`, `zephyr/boards/`). Each source's directory carries a
`.source` file recording the pinned upstream tag and commit SHA.

The contents are synced into the repo via `scripts/sync-upstream.py`,
driven by `scripts/upstream-manifest.yaml`. The script reads local
clones the maintainer provides via `LINUX_SRC`/`LINUX_XLNX_SRC`/
`ZEPHYR_SRC` env vars — no network access. Refresh is a deliberate
human action. The mechanism follows U-Boot's `dts/upstream/` pattern.

#### Per-SoC silicon-facts (`lopper/data/socs/<family>.yaml`)

This directory holds one YAML per SoC family, each carrying the
silicon facts neither the Linux DT nor the Zephyr DT contain — PM
device IDs, cluster shape templates, TCM/OCM memory map, IPI
topology, GIC layout. The schema is documented in
`lopper/data/socs/README.md`.

The contents are sourced from public material only: kernel
dt-binding headers, public TRMs, and the upstream Zephyr SoC dtsi
files. Reference SDTs and any internal hardware-description files
are explicitly forbidden as sources — using internal information
would defeat the purpose.

A starter file can be bootstrapped from a PM-ID dt-binding header via
`scripts/extract-pm-ids.py`; the remainder (cluster templates, TCM
addresses, etc.) is hand-curated from public docs.

#### Per-board configurations (`lopper/data/boards/<board>/`)

Each per-board directory contains the following files:

- `source.yaml` — declares the Linux DT input and Zephyr DT input
  (paths into `lopper/data/upstream/`) and the cpp include paths
  needed to flatten each.
- `augment.yaml` — hand-written integration decisions Linux DT
  doesn't carry: reserved-memory carve-outs for co-processor
  firmware, rpmsg shared memory, MU agent assignments, any board
  peripherals not in the upstream trees.
- `zephyr-input.dts` — a small lopper-authored wrapper that includes
  the upstream Zephyr SoC dtsi and adds minimum root metadata.
  Sidesteps Zephyr board files that depend on west-managed modules
  we don't vendor.
- `expected-non-linux.yaml` / `expected-sdt.dts` — golden outputs
  the integration tests diff against.

### Extraction: `compose_non_linux`

`lopper/assists/compose_non_linux.py` is the Lopper assist that
walks the Zephyr DT, captures every node not already present in
the Linux DT at the same address, merges the per-board augment
overlay, and emits the `openamp,domain-v1,non-linux` YAML.

The assist takes the Zephyr DT as its main Lopper input and the
Linux DT as a secondary `LopperSDT` via `--linux-dt` (for address
dedup). For each kept node it captures the complete property set:

- Plain int / int-array properties → HexInt-wrapped for hex output.
- Strings → passed through as-is.
- Boolean-present properties → emitted as YAML `true`.
- Phandle-bearing properties (looked up via Lopper's
  `phandle_possible_properties()` registry) → encoded as canonical
  `"&label"` string refs (matching what `LopperTree.label_to_phandle`
  accepts on the consumer side). Single-phandle/no-cells properties
  collapse to a bare `"&label"` scalar; phandle-plus-cells become a
  flat list mixing the `"&label"` string with HexInt cells.
- Memory nodes → reg cell-arrays normalised to the `{start, size}`
  convention used in `domains.yaml` and the SDT spec, so all memory
  entries (Zephyr-mined and augment-derived) speak one form.

Augment overlay: loads `augment.yaml`, finds the
`openamp,domain-v1,board-augment` block, merges its cpus / memory /
sram / access lists into the corresponding non-linux buckets with
`source: augment` tags. HexInt-normalises user-written numbers so
the output formats them consistently with the Zephyr-derived
content.

### Shared extraction core: `_devices_core.DevicesCore`

The module `lopper/assists/_devices_core.py` is input-agnostic
library code that the existing `sdt_devices` assist (which operates
on a pre-existing SDT to produce the thin partitioning inventory)
calls into. It provides device categorisation, walking, and per-OS
discovery primitives that `sdt_devices` uses to produce the
`openamp,domain-v1,devices` inventory consumed by the domain
allocator and downstream slicer.

`compose_non_linux` is intentionally separate: it captures
*rich-property* node content for the SDT base, where
`_devices_core` produces *thin* inventory entries for partitioning.
The two outputs serve different stages of the larger flow.

### Assembly: `assemble_sdt`

`lopper/assists/assemble_sdt.py` is the Lopper assist that produces
the `system-top.dts`. Inputs: `--linux-dt <flat>` (loaded as the SDT
base, so its nodes/properties/phandles/labels round-trip verbatim)
and `--non-linux <yaml>` (the compose_non_linux output, overlaid on
top). Output emission is via `LopperTreePrinter` over the merged
in-memory tree.

The assembler proceeds in stages:

1. Load the Linux DT as a mutable `LopperSDT` base tree.
2. Mark `/cpus` with the SDT spec's `cpus,cluster` compatible and
   add a `cpus_<arch>` label. The node name stays unchanged so that
   any Linux DT references into `/cpus/cpu@N` still resolve.
3. Attach each non-linux cluster entry as a sibling root-level
   `cpus,cluster` node (e.g. `cpus_r5: cpus-r5@0`) with its cpu
   children.
4. Attach each non-linux memory entry: `no-map` carve-outs land
   under `/reserved-memory/<name>`; the rest become `/memory@<addr>`
   nodes at root. `{start, size}` is unpacked into a `reg`
   cell-array at the parent's address/size cell counts.
5. Attach each non-linux peripheral under a `/non_linux_soc`
   simple-bus wrapper — the co-processor's reg values are
   bus-relative and would either clash with Linux-side absolute
   addresses or break unit-address uniqueness if hoisted to root.
6. Walk the merged tree and resolve `"&label"` phandle refs via
   `LopperTree.label_to_phandle()` against the merged label space.
7. Emit via `LopperTreePrinter`. As a final pass, the Lopper-
   synthesised `&invalid_phandle` sentinel (which Lopper emits for
   literal-zero phandle slots, a valid DT idiom meaning "no phandle
   here") is substituted with `0x0` so dtc accepts the output.

Every added node carries a `lopper-source` tag (`zephyr` /
`augment` / `non-linux`) so the downstream slicer can split the
merged tree back into per-OS DTs based on provenance.

### Pipeline runner: `scripts/build-board-sdt.py`

This script is the canonical entry point that runs the four-stage
pipeline end-to-end for one shipped board. Given `--board <name>`,
it reads `lopper/data/boards/<name>/source.yaml`, preprocesses the
upstream Linux DT (and Zephyr DT when present) with cpp + dtc using
the include paths declared in the board config, then invokes
`compose_non_linux` and `assemble_sdt` in sequence. With
`--no-zephyr` it skips the `compose_non_linux` stage entirely and
calls `assemble_sdt` without `--non-linux`, producing a Linux-only
SDT. Users running the pipeline drive it through this script; the
integration tests in `tests/test_sdt_from_linux.py` invoke the same
script so there is one canonical implementation of the
orchestration.

This also explains why both assists accept pre-flattened `.dts`
inputs rather than running cpp/dtc themselves: preprocessing
belongs in the orchestration layer (this script), keeping the
assists read-only on already-flat trees so they can be exercised
in isolation by tests or by external tools that preprocess their
input differently.

### Bootstrap tooling: `scripts/`

These additional scripts bootstrap and maintain the data the
pipeline consumes:

- `scripts/sync-upstream.py` — vendors upstream files into
  `lopper/data/upstream/` from local clones. Validates each source
  is a clean git tree, records the resolved tag + SHA in the
  per-source `.source` file. Accepts any clean tree (refusing only
  dirty working copies); the SHA is the canonical pin, the tag is
  documentation.
- `scripts/upstream-manifest.yaml` — declarative list of which files
  to vendor from each upstream source, with per-source tag-pattern
  hints.
- `scripts/extract-pm-ids.py` — reads a public PM-ID dt-binding
  header (Xilinx `PM_DEV_*`, TI `K3_DEV_*`, ST `STM32MP1_*`) and
  emits a starter `lopper/data/socs/<family>.yaml`. Bootstraps the
  mechanical 80% of new SoC support; the human fills in the
  `matches:` list and the source citation.

## The unified schema

Every YAML the pipeline produces or consumes has the same outer
shape: a `domains:` block at the root with named child blocks, each
discriminated by a `compatible:` string in the `openamp,domain-v1`
family. The loader dispatches on the suffix.

| compatible | Purpose | Producer | Consumer |
|---|---|---|---|
| `openamp,domain-v1` | User partition intent (per-OS device assignment) | hand-written by user | existing Lopper domain processing |
| `openamp,domain-v1,devices` | Generated device inventory (thin shape used for partitioning) | `sdt_devices` | audit framework, domain-processing tools |
| `openamp,domain-v1,non-linux` | Generated rich-property non-Linux content (Zephyr-mined + augment-derived) | `compose_non_linux` | `assemble_sdt` |
| `openamp,domain-v1,soc-facts` | Per-SoC silicon facts (PM IDs, cluster shapes, TCM/OCM map) | shipped under `lopper/data/socs/`, hand-curated from public docs | `_devices_core` PM-ID decoder; future cluster-template consumers |
| `openamp,domain-v1,board-augment` | Per-board integration overlay | shipped under `lopper/data/boards/<board>/augment.yaml`, hand-written | `compose_non_linux` augment merger |

The thin (`devices`) and rich (`non-linux`) intermediates target
different stages: the inventory shape feeds partitioning logic that
only needs to know what is reachable, while the non-linux shape
preserves the full DT property set needed to reconstitute a
bootable per-OS DT after slicing.

## Data flow

```
Linux DT  ────→ flatten (cpp + dtc) ────→ flat .dts ──────────────────────────────┐
                                                                                   │
Zephyr DT ────→ flatten (cpp + dtc) ────→ flat .dts ────→ LopperSDT ──┐            │
                                                                       │            │
Augment YAML   (read for the --board the user picks; merged last) ────┴─→ compose_non_linux
                                                                                   │
                                                                          non-linux.yaml
                                                                                   │
                                                                                   ▼
                                                          assemble_sdt ◄──── Linux flat .dts (re-loaded as base)
                                                                                   │
                                                                                   ▼
                                                                            system-top.dts
```

The cpp + dtc steps and the chained Lopper invocations are
orchestrated by `scripts/build-board-sdt.py` for the shipped
reference boards (see `docs/sdt-from-linux.md`). The diagram shows
the conceptual flow; the assists themselves are read-only on
already-flat `.dts` inputs.

Source provenance is preserved across both stages: every node
contributed by Zephyr-side extraction carries `source: zephyr`; every
node contributed by the augment overlay carries `source: augment`;
Linux contributions are the default (no source tag). The provenance
round-trips to the final SDT as `lopper-source` properties.

## Source-of-truth rules

These rules are load-bearing for the pipeline (see
`lopper/data/socs/README.md` for the sourcing rule on SoC YAML; the
same principles apply throughout):

1. **No internal information as input.** Reference SDTs, vendor
   hardware-design files, and internal-only headers are forbidden.
   The pipeline must work from material any OSS contributor can
   obtain. Reference SDTs may serve as validation oracles
   (post-generation comparison) but never as inputs.
2. **User-supplied facts enter as tracked YAML inputs**, never as
   hand-edits to generated outputs. Regeneration must preserve user
   work — that only holds when the work lives in a versioned input
   file rather than a post-hoc edit.
3. **The intermediate format is the existing
   `openamp,domain-v1,devices` shape.** No bespoke formats.
   Downstream consumers (assemblers, audit, domain expansion) work
   unchanged.
4. **Determinism / idempotency.** Same inputs → byte-identical
   output. Regeneration with no input change is a safe no-op. CI
   diffs against committed golden outputs to catch drift.

## Extension points

### Adding a new SoC family

1. Run `scripts/extract-pm-ids.py` against the SoC's PM-ID
   dt-binding header (Xilinx kernel header, TI sysfw, etc.) →
   starter `lopper/data/socs/<family>.yaml`.
2. Fill in `matches:` (root compatible strings) and the source
   citation in the file's header.
3. Optionally extend the SoC YAML with `cluster_templates`,
   `tcm_map`, `ocm_map`, `ipi`, `gic` blocks from the public TRM
   (see `lopper/data/socs/versal.yaml` for a worked example).
4. No code changes required — the loader picks up the new file on
   the next run.

### Adding a new board

1. Vendor the relevant upstream Linux DT into
   `lopper/data/upstream/linux/` (or the appropriate vendor fork
   subdirectory) by adding it to `scripts/upstream-manifest.yaml`
   and re-running `scripts/sync-upstream.py`.
2. (Optional) Vendor the upstream Zephyr DT similarly.
3. Create `lopper/data/boards/<your-board>/` with:
   - `source.yaml` declaring `linux:` and (optionally) `zephyr:`
     blocks pointing at the vendored files. Copy from a reference
     board to start.
   - `augment.yaml` with the integration decisions (reserved-memory
     carve-outs for co-processor firmware, rpmsg regions, etc.).
   - `zephyr-input.dts` if a Zephyr side is being merged.
4. Add an integration test in `tests/test_sdt_from_linux.py`
   following the existing patterns; capture the first clean output
   as `expected-*` goldens.

### Adding a new pipeline capability

A new merge-time signal (the kind of per-device tag the pipeline
attaches as it walks the tree) belongs in
`_devices_core._augment_device_entry`. A new extraction source for
non-Linux content (e.g. a Xen-side hypervisor DT, an FPGA partial
overlay) belongs in `compose_non_linux` alongside the existing
Zephyr-side walker. A new downstream output shape belongs in a new
assist that consumes `openamp,domain-v1,non-linux` (or
`openamp,domain-v1,devices`, depending on whether it needs rich or
thin input) alongside the existing `assemble_sdt`.

## See also

- `docs/sdt-from-linux.md` — user-facing how-to (commands, reference
  boards, running the pipeline)
- `lopper/data/socs/README.md` — SoC YAML schema and sourcing rules
- `lopper/data/boards/<board>/source.yaml` — declarative pipeline
  inputs per board (header comments document the schema)
- `tests/test_sdt_from_linux.py` — integration tests; the real
  end-to-end commands the pipeline runs
