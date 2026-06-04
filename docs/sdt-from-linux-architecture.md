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
   Linux DT (upstream)         ─┐
   Zephyr DT (upstream)        ─┤   ──→ compose_devices ──┐
   SoC silicon-facts YAML      ─┤                          │
   Per-board augment YAML      ─┘                          │
                                                           ▼
                                                     devices.yaml
                                              (openamp,domain-v1,devices)
                                                           │
                                                           ▼
                                                     assemble_sdt
                                                           │
                                                           ▼
                                                    system-top.dts
                                                           │
                  User partition intent ─────────┐         │
                  (domains.yaml,                 │         │
                   openamp,domain-v1)            ▼         ▼
                                              existing downstream tools
                                       (per-OS DT slicing, BSP gen, OpenAMP,
                                        FPGA overlays, hypervisor configs,
                                        cross-OS validation, …)
```

The pipeline is intentionally two-stage: extraction (`compose_devices`)
produces a canonical inventory YAML; assembly (`assemble_sdt`) turns
that into the SDT. The intermediate format is the same shape that
existing inventory-producing tools emit, so downstream consumers can
treat input from either path interchangeably.

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
- `expected-devices.yaml` / `expected-devices-merged.yaml` /
  `expected-sdt.dts` — golden outputs the integration tests diff
  against.

### Extraction: `compose_devices`

`lopper/assists/compose_devices.py` is the Lopper assist that walks
the Linux DT, optionally walks the Zephyr DT, merges them, applies the
per-board augment overlay, and emits the canonical
`openamp,domain-v1,devices` YAML inventory.

The walking, categorisation, and YAML emit logic is shared with the
existing `sdt_devices` assist via `lopper/assists/_devices_core.py`
(see below). `compose_devices` adds the multi-source orchestration:

- Linux side: the standard Lopper assist input (a pre-flattened .dts).
- Zephyr side (optional, via `--zephyr-dt`): loaded as a secondary
  `LopperSDT`, walked through the same `DevicesCore`.
- Merge: address-keyed dedup. Linux entries win on collision; Zephyr
  entries with addresses not in the Linux side are appended with
  `source: zephyr`. Cluster entries are always appended (different
  cluster types never alias). Linux's SoC identity and `/aliases`
  are authoritative.
- Augment overlay: loads `augment.yaml`, finds the
  `openamp,domain-v1,board-augment` block, merges its cpus / memory /
  sram / access lists into the inventory with `source: augment` tags.
  HexInt-normalises user-written numbers so the output formats them
  consistently.

### Shared extraction core: `_devices_core.DevicesCore`

The module `lopper/assists/_devices_core.py` is input-agnostic
library code that both `sdt_devices` (operates on a pre-existing SDT)
and `compose_devices` (operates on a Linux DT, optionally merged with
Zephyr) call into. It provides:

- `DeviceCategory` enum (BUS / CPU / MEMORY / FIRMWARE / TOPLEVEL).
- Infrastructure-device filter taxonomy (interrupt controllers, SMMU,
  power-management nodes, etc. — devices that can't be split between
  domains).
- Tree walking and per-category discovery (`discover_bus_devices`,
  `discover_cpus`, `discover_memory`, `discover_firmware`,
  `discover_toplevel`).
- Per-device augmentation hook (`_augment_device_entry`) that emits
  the `bootph` tags and decodes the `power-domains` reference via
  the per-SoC `pm_devices` table.
- Root-level extraction: SoC identity from root `compatible` and
  `model`, aliases pass-through from `/aliases`.
- Tree builder (`build_domain_tree`) that takes a pre-built
  inventory dict and emits the `openamp,domain-v1,devices` tree.
  Split from `generate_domain` so callers that merge multiple
  sources (compose_devices) can discover separately, combine, build
  once.

### Assembly: `assemble_sdt`

`lopper/assists/assemble_sdt.py` is the Lopper assist that turns the
devices YAML into the `system-top.dts`. Input is purely the YAML; the
main Lopper sdt argument is unused. Output emission is by string
templating rather than tree manipulation — direct templating gives
exact control over formatting (for byte-stable golden comparison)
and avoids LopperTree resolve/sync mechanics for what is structurally
a write-only transformation.

The assembler emits, in order: root metadata (identity from
inventory), one `cpus,cluster`-wrapped node per `cpus` entry, memory
nodes from the memory list, a `reserved-memory` block containing any
`no-map` / `reusable` entries from sram and memory, an `amba: axi`
simple-bus with per-device stub nodes for the addressable `access`
entries, addressless `access` entries at root (dcc, firmware, …),
aliases pass-through, and a `/domains` placeholder.

Per-device stubs carry the inventory-derived facts (reg, status,
source tag, pm_node, bootph). Full per-device detail (clocks,
interrupts, vendor properties) is not reproduced — those would
require re-loading the source DTs during assembly, which is a future
enhancement. The current output is a structural SDT consumable by
domain-processing tools, not a Linux-bootable DT.

### Pipeline runner: `scripts/build-board-sdt.py`

This script is the canonical entry point that runs the four-stage
pipeline end-to-end for one shipped board. Given `--board <name>`,
it reads `lopper/data/boards/<name>/source.yaml`, preprocesses the
upstream Linux DT (and Zephyr DT when present) with cpp + dtc using
the include paths declared in the board config, then invokes
`compose_devices` and `assemble_sdt` in sequence. Users running the
pipeline drive it through this script; the integration tests in
`tests/test_sdt_from_linux.py` invoke the same script so there is
one canonical implementation of the orchestration.

This also explains why `compose_devices` and `assemble_sdt` accept
pre-flattened `.dts` inputs rather than running cpp/dtc themselves:
preprocessing belongs in the orchestration layer (this script),
keeping the assists read-only on already-flat trees so they can
be exercised in isolation by tests or by external tools that
preprocess their input differently.

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
| `openamp,domain-v1,devices` | Generated device inventory (pipeline intermediate) | `sdt_devices` / `compose_devices` | `assemble_sdt`, audit framework, domain-processing tools |
| `openamp,domain-v1,soc-facts` | Per-SoC silicon facts (PM IDs, cluster shapes, TCM/OCM map) | shipped under `lopper/data/socs/`, hand-curated from public docs | `_devices_core` PM-ID decoder; future cluster-template consumers |
| `openamp,domain-v1,board-augment` | Per-board integration overlay | shipped under `lopper/data/boards/<board>/augment.yaml`, hand-written | `compose_devices` augment merger |

Inventory blocks (`cpus`, `memory`, `sram`, `access`) share the same
shape across all four compatibles, so cross-source merging is a list
append rather than a format translation.

## Data flow

```
Linux DT  ────→ flatten (cpp + dtc) ────→ flat .dts ────→ LopperSDT ──┐
Zephyr DT ────→ flatten (cpp + dtc) ────→ flat .dts ────→ LopperSDT ──┤
                                                                       ├──→ compose_devices ──→ devices.yaml
SoC YAML       (read at import; PM-ID table consulted per device) ────┤
Augment YAML   (read for the --board the user picks; merged last) ────┘

                                                  devices.yaml ──→ assemble_sdt ──→ system-top.dts
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
`_devices_core._augment_device_entry`. A new extraction source (a
new kind of input tree to mine) belongs in a new helper method on
`DevicesCore` plus a call site in `compose_devices`. A new
downstream output shape belongs in a new assist that consumes
`openamp,domain-v1,devices` alongside the existing `assemble_sdt`.

## See also

- `docs/sdt-from-linux.md` — user-facing how-to (commands, reference
  boards, running the pipeline)
- `lopper/data/socs/README.md` — SoC YAML schema and sourcing rules
- `lopper/data/boards/<board>/source.yaml` — declarative pipeline
  inputs per board (header comments document the schema)
- `tests/test_sdt_from_linux.py` — integration tests; the real
  end-to-end commands the pipeline runs
