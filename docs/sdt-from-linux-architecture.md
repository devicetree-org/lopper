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

The same user-edited `domains.yaml` feeds the pipeline at two
points: integration declarations (no-map memory carve-outs, etc.)
get pulled out at SDT-build time, and partition intent (which
device belongs to which OS) gets consumed by the downstream
domain-processing tools after the SDT exists. The diagram below
shows that same file (`USER's domains.yaml`) wired into both
stages — one physical file, two consumers.

```
   Linux DT (upstream)         ────────────────────────────┐
                                                           │
   Zephyr DT (upstream)        ─┐                          │
                                ├─→ compose_non_linux ─┐   │
   USER's domains.yaml ──┐──────┘   (rich props,       │   │
   (one file,            │          "&label" phandles) │   │
    edit-then-use,       │                             ▼   ▼
    both stages          │                          non-linux.yaml ──→ assemble_sdt
    consume it)          │                    (openamp,domain-v1,non-linux)   │
                         │                                                    │
                         │                                                    ▼
                         │                                            system-top.dts
                         │                                              (Linux DT base
                         │                                               + cpus,cluster wrap
                         │                                               + non-Linux overlay)
                         │                                                    │
                         └─────────────────────────────────────────┐          │
                                                                   ▼          ▼
                                                              existing downstream tools
                                                       (per-OS DT slicing, BSP gen, OpenAMP,
                                                        FPGA overlays, hypervisor configs,
                                                        cross-OS validation, …)
```

The pipeline is two-stage: extraction (`compose_non_linux`)
captures everything the Linux DT does not already carry — co-
processor clusters, TCM/OCRAM regions, the co-processor side of the
IPC fabric, board-declared reserved-memory carve-outs — into a
rich-property YAML where every kept node retains its full DT
property set (with phandle refs encoded as `"&label"` strings).
Assembly (`assemble_sdt`) then loads the Linux DT as the SDT base
and overlays the non-Linux content on top, so Linux-side nodes
round-trip verbatim (preserving the bootability of the SDT after
downstream per-OS slicing) and non-Linux content joins the merged
tree as siblings of the Linux clusters.

### The user's domains.yaml

One user-supplied YAML drives the flow: a per-deployment
`domains.yaml` in standard `openamp,domain-v1` shape (matching the
existing system device tree convention). It carries two kinds of
content, both consumed by the pipeline:

- **Integration declarations** — facts the upstream Linux DT and
  Zephyr DT do not carry that need to become first-class nodes in
  the SDT. Two kinds:
    - *Reserved-memory carve-outs* — memory / sram entries flagged
      `no-map: true` (or `reusable: true`): co-processor firmware
      regions, the rpmsg shared region, etc. `assemble_sdt` injects
      each as a child of the SDT's `/reserved-memory`.
    - *Board-only peripherals* — access-list entries that carry
      their own properties (a real declaration, not a bare
      reference or glob): devices physically present on the board
      but absent from both upstream trees. `assemble_sdt` injects
      each into the SDT alongside the other non-Linux nodes.

  `compose_non_linux` reads both kinds at SDT-build time. In a full
  hardware-description-driven SDT flow (e.g. AMD's XSA-based one)
  the SDT already contains these nodes, so a `domains.yaml` only
  references them; in this pipeline the Linux DT carries none of
  them, so the user declares them here and the pipeline injects
  them.

  *Where these facts come from.* They are integration choices, not
  silicon facts, so they live in the project's co-processor bring-up
  plan rather than in any one upstream tree. In practice you source
  them from: the board's OpenAMP / remoteproc integration plan or
  reference demo (which fixes the firmware load address and the
  rpmsg ring / shared-memory region — e.g. the addresses in the
  vendor's `openamp`/`rpmsg` example DTs); the SoC TRM and board
  memory map (to pick carve-outs that don't collide with the OS's
  usable DRAM); and, for board-only peripherals, the board
  schematic / datasheet (for a device the upstream Linux and Zephyr
  trees simply don't describe). The shipped per-board
  `domains.yaml` templates encode one known-good set of these
  choices for the reference boards — a concrete worked example to
  copy from.

- **Partition intent** — which device / memory / cluster belongs to
  which OS, expressed in the standard domain-block shape (cpus,
  memory, access lists). Consumed by the downstream Lopper
  domain-processing tools (`gen_domain_dts`, `openamp`, the audit
  framework, etc.) after the SDT exists.

#### Template + overlay (no copy, no sync)

The board's integration facts live in a **shipped template**; the
user's deployment-specific changes live in a small **overlay**.
The two are deep-merged at SDT-build time — the user never copies
the template, so a `git pull` that updates the template flows
through without disturbing the overlay.

- `lopper/data/boards/<board>/domains.yaml` — **shipped template**
  inside this repo. Pre-populated with the board's integration
  declarations and a skeleton partition. Auto-located via `--board`.
  Not meant to be edited in place (a `git pull` would clobber the
  edits).
- **User's overlay** — a YAML file you create, name, and place
  anywhere you like (it is not part of this repo, follows no naming
  convention, and never has to live under the lopper tree). It holds
  only the entries that differ from the template: extra carve-outs,
  a resized region, refined cpumasks, narrowed access lists. You
  point the pipeline at it with `--domains <path>`;
  `build-board-sdt.py` resolves that path against your current
  directory (so relative or absolute both work) before handing the
  absolute path to the lopper subprocess, where `compose_non_linux`
  opens it directly.

`compose_non_linux` loads both and deep-merges them with the shared
`LopperYAML.deep_merge` (the same helper the core Lopper YAML path
uses): dicts merge recursively, lists append, scalars are
last-write-wins. Because the overlay is merged *second*, its list
entries land after the template's; the extraction step below then
assigns each declaration into a dict keyed by `dev` (last-wins), so
the net effect from the user's point of view is "overlay overrides
the template entry with the same `dev`, and adds entries with a new
`dev`" — without `compose_non_linux` needing its own merge code.

The merged result is consumed at two points in the flow:

1. At SDT-build time, by `compose_non_linux` — the integration
   declarations (no-map memory, property-bearing access) get
   extracted into the non-linux YAML.
2. After the SDT exists, by the downstream Lopper domain-processing
   assists — they read the partition intent from the same overlay
   (the user's `domains.yaml`) alongside `system-top.dts`.

If `--domains` is omitted the template alone is used (handy for
first-run / reference builds — it's what the integration tests do).
`--no-template` does the inverse: ignore the shipped template and
use only the user's overlay (bring-your-own-template / diagnostic
runs).

Because the pipeline reads YAML directly (not through Lopper's core
`-i` YAML path), it never runs `cpp` on these files. A future
enhancement could `cpp`-preprocess them so an overlay could
`#include` the template and use `#define`s for parametric values;
that would compose cleanly with the merge described here (cpp
expands each file first, then the merge runs).

#### What `sdt-domains.yaml` actually is

`sdt-domains.yaml` is a generated file in the same
`openamp,domain-v1` shape as the user's `domains.yaml`, produced by
the `sdt_domains` assist (see the post-SDT components below) by
walking the *assembled* SDT. It is **a candidate partition, not a
device list** — it proposes one domain per `cpus,cluster` node in
the SDT and makes a first-guess assignment of resources to each,
based on the `lopper-source` tags `assemble_sdt` stamped on every
node:

- The Linux cluster (untagged nodes) becomes an `APU` domain whose
  access list is a single `dev: '*'` glob — the default assumption
  is that Linux claims every peripheral not explicitly handed to a
  co-processor. It is deliberately *not* an enumeration of each
  Linux device; that flat enumeration is `sdt-devices.yaml`'s job
  (the glob vocabulary), a different file with a different purpose.
- Each co-processor cluster (e.g. `cpus_r5`, tagged `zephyr`)
  becomes an `RPU` / `MCU` domain that *does* enumerate its
  resources: the `/non_linux_soc` peripherals carrying the matching
  source tag, plus the reserved-memory carve-outs whose names match
  that cluster (`rpu*` → R5, `m4*` → M4; shared `rpmsg*` / `shmem*`
  go to every co-processor domain).

So it reflects everything that ended up in the SDT — the Linux
base, the Zephyr-mined nodes, and the carve-outs your `domains.yaml`
integration declarations contributed — re-expressed as a starting
partition. It differs from the per-board **template** (which is a
hand-authored *input*: integration declarations plus a skeleton
partition) in that `sdt-domains.yaml` is a machine-derived *output*
regenerated on every run from the finished SDT.

How you use it: read it to see how `sdt_domains` would split the
chip by default, then copy the useful bits into your own
`domains.yaml` (the hand-edited overlay you pass with `--domains` —
"your overlay" and "your `domains.yaml`" are the same file), or use
it as the starting point for a brand-new deployment. `sdt-domains.yaml`
itself is never edited in place (it is regenerated on every run), is
never the same file as your `domains.yaml`, and is never consumed by
the downstream domain-processing tools — only your `domains.yaml`
is. See
[Relationship to the user's `domains.yaml`](#relationship-to-the-users-domainsyaml)
under the post-SDT starter for the side-by-side comparison.

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

The manifest is **board-scoped**: a `sources:` block holds the
per-upstream metadata (env var, tag pattern, target subdir) and a
`boards:` block lists each board's files grouped by source. Files
shared across boards (e.g. the Versal `versal.dtsi` used by both
vck190 and vek280) are listed under each board; `sync-upstream.py`
flattens `board × source → file set` and dedups, so a shared file
is copied once and one `.source` provenance record is written per
upstream tree. The Versal material is pinned at `xilinx-v2026.1`.

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

**Keeping SoC files in your own repo.** You are not limited to the
shipped `lopper/data/socs/` directory. The loader
(`_devices_core._load_soc_data`) also scans every lopper include
directory — the dirs you pass with `-I` / `--input-dirs`, plus
`LOPPER_INPUT_DIRS` — looking for SoC YAML at the **same relative
layout as the shipped tree**, i.e. `<include-dir>/data/socs/*.yaml`.
The structure is enforced (files can't be just *anywhere* — they
must sit at `data/socs/` under an include dir), and your include
dirs are searched **before** the built-in location, so a file you
supply can override a shipped SoC of the same `matches:` or add an
entirely new one. For example, with
`my-repo/data/socs/my-soc.yaml` present:

```bash
lopper -I my-repo ... -- sdt_devices -o devices.yaml
```

picks up `my-soc.yaml` without your ever editing the lopper tree.
The canonical runner forwards `-I` too, so the same works through
the whole pipeline:

```bash
scripts/build-board-sdt.py --board my-board -I my-repo -o /tmp/build
```

`build-board-sdt.py` resolves each `-I` dir against your current
directory and passes it to every lopper invocation in the run. This
mirrors the `domains.yaml` template+overlay story: the shipped files
are a starting point, your repo carries the customisations.

A starter file can be bootstrapped from a PM-ID dt-binding header via
`scripts/extract-pm-ids.py`; the remainder (cluster templates, TCM
addresses, etc.) is hand-curated from public docs.

**What a PM-ID dt-binding header is, and where to get one.** On SoCs
with a power-management controller, the device tree refers to each
controllable device by a numeric *power-management ID* rather than
by address — e.g. `power-domains = <&firmware PM_DEV_UART_0>`. Those
`PM_DEV_*` symbols are `#define`d in a C header that ships in the
upstream Linux kernel tree under
`include/dt-bindings/power/<soc>.h`, for example:

- `include/dt-bindings/power/xlnx-versal-power.h` (AMD Versal)
- `include/dt-bindings/power/xlnx-zynqmp-power.h` (AMD ZynqMP)

Each line is a plain `#define NAME 0xVALUE` macro. Other vendors
follow the same pattern with a different prefix — TI K3 uses
`K3_DEV_*` (in their sysfw / `ti,sci` bindings), ST uses
`STM32MP1_*`. `extract-pm-ids.py` parses those `#define`s into the
SoC YAML's `pm_devices` table, which the pipeline later uses to
decode a device's `power-domains` reference back into a
human-readable PM name (e.g. tagging `serial@ff000000` with
`pm_node: PM_DEV_UART_0`). You obtain the header by checking out
the upstream kernel (or your vendor kernel fork) and pointing the
script at the file — it is public kernel source, not an internal
artifact. SoCs without a PM controller (e.g. the i.MX 8M Mini)
have no such header; their SoC YAML simply ships an empty
`pm_devices: {}` and the decode step is a no-op.

**How the parser recognises the IDs.** `extract-pm-ids.py` does not
infer the symbol convention — it matches `#define <SYMBOL> 0xVALUE`
lines where `<SYMBOL>` begins with one of a fixed built-in set of
prefixes: `PM_DEV` (AMD), `K3_DEV` (TI), `STM32MP1` (ST). A vendor
whose header uses a different convention is handled without editing
the script via two escape hatches:

- `--prefix <P>` adds a literal prefix; the parser then also matches
  `<P>_NAME` (e.g. `--prefix MTK_PD`).
- `--prefix-regex <R>` splices a raw regex fragment into the
  name-matching alternation, for conventions a fixed prefix can't
  express — a number-variable family (`STM32MP\d+_[A-Za-z0-9_]+`)
  or a suffix-discriminated one (`[A-Z0-9_]+_POWER_DOMAIN`).

Both flags are repeatable and append to the built-in set. (They
cover the *symbol-name* convention only; a header whose *value*
syntax differs — not hex — still needs a tweak to the regex in
`_build_define_re`.)

#### Per-board configurations (`lopper/data/boards/<board>/`)

Three reference boards ship today: **versal-vck190** (Versal AI
Core) and **versal-vek280** (Versal AI Edge) — two generations of
the same A72-APU + R5-RPU silicon, sharing the Versal SoC dtsi,
the Zephyr R5 wrapper, and `versal.yaml` SoC-facts — plus
**imx8mm-evk** (NXP i.MX 8M Mini, A53 + M4).

Each per-board directory contains the following files:

- `source.yaml` — declares the Linux DT input and Zephyr DT input
  (paths into `lopper/data/upstream/`) and the cpp include paths
  needed to flatten each.
- `domains.yaml` — hand-written integration decisions Linux DT
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
the Linux DT at the same address, merges integration declarations
from the per-board `domains.yaml`, and emits the
`openamp,domain-v1,non-linux` YAML.

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
  entries (Zephyr-mined and board-declared) speak one form.

Integration overlay: loads the per-board `domains.yaml`, walks every
sub-domain block under the conventional `domains.<root>.domains.<name>`
shape, and pulls out:

- Reserved-memory carve-outs (memory / sram entries flagged with
  `no-map: true` or `reusable: true`) → tagged `source: domain` in
  the non-linux YAML; `assemble_sdt` later injects each under the
  SDT's `/reserved-memory`.
- Board-only peripherals in access lists that carry their own
  properties (i.e. declarations, not bare references) → same
  treatment, surfaced under the devices bucket.

Pure partition-intent entries (memory entries that just reference
an existing SDT node without `no-map`, access entries that are
globs or bare label references) are ignored at this stage — they're
consumed later by the domain-processing tools, not by SDT assembly.
HexInt-normalises user-written numbers so the output formats them
consistently with the Zephyr-derived content.

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
`domain` / `non-linux`) so the downstream slicer can split the
merged tree back into per-OS DTs based on provenance.

### Post-SDT enumeration: `sdt_devices` (existing)

`lopper/assists/sdt_devices.py` is a pre-existing assist that
walks a finished SDT and emits an `openamp,domain-v1,devices`
inventory of every node it found. Run after `assemble_sdt`, its
output (`<board>-sdt-devices.yaml`) is the vocabulary a user-
written `domains.yaml` can glob against — Lopper's existing
domain-processing tooling expands `dev: "*pattern*"` access
entries against a parent enumeration loaded via `-i`. The
pipeline runs this so users have the vocabulary file ready
without a separate manual step.

### Post-SDT starter: `sdt_domains`

`lopper/assists/sdt_domains.py` walks the assembled SDT,
finds every `cpus,cluster` node, and emits one starter domain per
cluster — partitioned by the `lopper-source` tags `assemble_sdt`
attached during assembly:

- **Untagged cluster** (the Linux side) → an `APU` domain whose
  access list is a single `dev: "*"` glob, since the Linux side
  typically claims everything not explicitly assigned elsewhere.
- **Tagged cluster** (`zephyr` / `non-linux`) → an `RPU` / `MCU`
  domain enumerating the children of `/non_linux_soc` and the
  board-declared carve-outs whose names suggest this cluster
  (`rpu*` → R5, `m4*` → M4, shared `rpmsg*` / `shmem*` land in
  every non-Linux domain).

The result (`<board>-sdt-domains.yaml`) is an `openamp,domain-v1`
YAML that defaults to obvious choices the user refines. The starter
saves the user from typing out the obvious split by hand.

#### Relationship to the user's `domains.yaml`

`sdt-domains.yaml` and the user's hand-edited `domains.yaml`
(described under [The user's domains.yaml](#the-users-domainsyaml))
are **different files**:

|                                        | `sdt-domains.yaml`                                            | the user's `domains.yaml`                             |
|----------------------------------------|---------------------------------------------------------------|-------------------------------------------------------|
| Producer                               | `sdt_domains` assist (regenerated every pipeline run)         | hand-edited by the user                               |
| Lives where                            | output directory alongside the SDT                            | the user's deployment workspace                       |
| Authoritative?                         | No — disposable reference / snapshot                          | Yes — the file downstream tools consume               |
| Carries integration declarations?      | No — only mirrors the SDT's `/reserved-memory` it found       | Yes — `no-map` memory entries the user wants injected |
| Edited by the user?                    | No — read for ideas, copy fragments into the hand-edited file | Yes — both integration and partition                  |
| Survives `git pull` / pipeline re-run? | Regenerated each run                                          | Yes (it lives outside the repo)                       |

The intended workflow: after a pipeline run, read
`sdt-domains.yaml` to see what the SDT actually contains and how
`sdt_domains` would partition it by default; pull the useful bits
into your own hand-edited `domains.yaml` (or use it as a starting
template when first setting up a deployment).

### Pipeline runner: `scripts/build-board-sdt.py`

This script is the canonical entry point that runs the full
pipeline end-to-end for one shipped board. Given `--board <name>`,
it reads `lopper/data/boards/<name>/source.yaml`, preprocesses the
upstream Linux DT (and Zephyr DT when present) with cpp + dtc using
the include paths declared in the board config, then runs
`compose_non_linux`, `assemble_sdt`, `sdt_devices`, and
`sdt_domains` in sequence. With `--no-zephyr` it skips
`compose_non_linux` and calls `assemble_sdt` without `--non-linux`,
producing a Linux-only SDT. Users running the pipeline drive it
through this script; the integration tests in
`tests/test_sdt_from_linux.py` invoke the same script so there is
one canonical implementation of the orchestration.

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
| `openamp,domain-v1` | User-supplied integration + partition (per-board `domains.yaml` and per-deployment derivatives) | hand-written by user | `compose_non_linux` (integration declarations); downstream Lopper domain processing (partition intent) |
| `openamp,domain-v1,devices` | Generated device inventory (thin shape used for partitioning) | `sdt_devices` | audit framework, domain-processing tools |
| `openamp,domain-v1,non-linux` | Generated rich-property non-Linux content (Zephyr-mined + board-declared) | `compose_non_linux` | `assemble_sdt` |
| `openamp,domain-v1,soc-facts` | Per-SoC silicon facts (PM IDs, cluster shapes, TCM/OCM map) | shipped under `lopper/data/socs/`, hand-curated from public docs | `_devices_core` PM-ID decoder; future cluster-template consumers |

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
Board domains.yaml (read for the --board the user picks; integration entries) ┴─→ compose_non_linux
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
node contributed by the domains.yaml overlay carries `source: domain`;
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
   `lopper/data/upstream/` by adding a `boards.<your-board>` entry
   to `scripts/upstream-manifest.yaml` (list the board's files under
   the source they come from — reuse the existing `sources:`
   metadata), then re-run `scripts/sync-upstream.py`. Shared files
   (e.g. a SoC `.dtsi` another board already vendors) can be listed
   again under your board; the sync dedups them.
2. (Optional) Vendor the upstream Zephyr DT similarly, under the
   board's `zephyr:` file list.
3. Create `lopper/data/boards/<your-board>/` with:
   - `source.yaml` declaring `linux:` and (optionally) `zephyr:`
     blocks pointing at the vendored files. Copy from a reference
     board to start.
   - `domains.yaml` with the integration decisions (reserved-memory
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
