# Generating a System Device Tree from a Linux Device Tree

This guide walks through producing a complete System Device Tree
(SDT) for a heterogeneous SoC starting from material the OS
communities already maintain — the upstream Linux device tree, the
upstream Zephyr device tree (when available), and a small
hand-written board YAML for integration choices Linux doesn't
carry (reserved-memory carve-outs for co-processor firmware, etc.).

The resulting SDT is the input that downstream Lopper tooling
consumes to produce per-OS device trees, baremetal/RTOS BSPs,
OpenAMP / rpmsg configuration, hypervisor configs, FPGA overlays,
and the like.

The pipeline ships with three reference boards exercised end-to-end
in CI: **AMD Versal VCK190** (AI Core), **AMD Versal VEK280** (AI
Edge — the newer Versal generation), and **NXP i.MX 8M Mini EVK**.

## Concepts

### Inputs

- **Linux DT** — the board's upstream Linux kernel device tree.
  Describes Linux's view: the cluster Linux runs on and the
  peripherals reachable from it.

- **Zephyr DT** — the board's upstream Zephyr RTOS device tree (when
  available). Describes the co-processor's view: M-core / R-core CPU,
  TCM/OCRAM, and the other side of the IPC mailbox.

- **SoC silicon-facts YAML** — a per-SoC file shipped under
  `lopper/data/socs/` (one per silicon family). Public silicon facts
  — PM device IDs, cluster shapes, TCM/OCM map, IPI topology —
  sourced from kernel headers and the public TRM.

- **Board domains.yaml** — a hand-written `openamp,domain-v1` file
  under `lopper/data/boards/<board>/domains.yaml`, matching the
  existing system device tree `domains.yaml` conventions. It carries
  two kinds of content:
    - *Integration declarations* — facts Linux and Zephyr don't
      carry that must become first-class SDT nodes: reserved-memory
      carve-outs (memory entries with `no-map: true`) for
      co-processor firmware / rpmsg regions, and board-only
      peripherals (access entries with their own properties) absent
      from both upstream trees. `assemble_sdt` injects each into the
      SDT.
    - *Partition intent* — which device / memory / cluster belongs to
      which OS. Consumed by the downstream domain-processing tools
      after the SDT exists.

### Pipeline assists

- **`compose_non_linux`** — walks the Zephyr DT (and the per-board
  `domains.yaml`), captures every node not already present at the
  same address in the Linux DT, and emits an
  `openamp,domain-v1,non-linux` YAML carrying the full property set
  for each kept node. Phandle refs are encoded as canonical
  `"&label"` strings so they re-resolve against the merged tree at
  assembly time.

- **`assemble_sdt`** — loads the Linux DT as the SDT base, marks
  `/cpus` with the `cpus,cluster` compatible the SDT spec uses, then
  overlays the non-linux YAML's clusters / memory / devices on top —
  producing the `system-top.dts`.

- **`sdt_devices`** — run *post-SDT* to enumerate every device in the
  assembled SDT into a YAML inventory. This becomes the vocabulary a
  user-written `domains.yaml` can glob against.

- **`sdt_domains`** — walks the assembled SDT and partitions devices
  / memory across one starter domain per `cpus,cluster` (using the
  `lopper-source` tags `assemble_sdt` attached), emitting a
  `sdt-domains.yaml` for the user to edit.

```
   Linux DT       ────────────────────────────────────────────────┐
                                                                  │
   Zephyr DT            ─┐                                        ├──→ assemble_sdt ──→ system-top.dts
   USER's domains.yaml ──┴─→ compose_non_linux ──→ non-linux.yaml ┘   (Linux DT base
        │                    (per-node rich props,                      + cpus,cluster wrap
        │                     phandle refs as "&label")                 + non-Linux overlay)
        │
        └──────────────────────────────────────────────────────→ downstream domain-processing tools
                                                                  (per-OS DT slicing, etc., after
                                                                   the SDT exists)
```

The user's `domains.yaml` (matching the existing system device tree
`openamp,domain-v1` conventions) is the same physical file at both
points: `compose_non_linux` pulls *integration declarations*
(reserved-memory carve-outs, board-only peripherals) out of it at
SDT-build time, and the downstream domain-processing tools consume
its *partition intent* (which device belongs to which OS) after the
SDT exists. See
[Bringing your own domains.yaml](#bringing-your-own-domainsyaml)
below.

## Running it on a board we ship

The repo includes everything needed to reproduce both reference
boards end-to-end through a single script:
`scripts/build-board-sdt.py`. The script reads the board's
`source.yaml`, preprocesses the upstream Linux DT (and Zephyr DT,
when present), invokes `compose_non_linux` to build the
rich-property non-linux YAML, then invokes `assemble_sdt` to load
the Linux DT as the SDT base and overlay the non-linux content on
top. Users do not need to invoke `cpp`, `dtc`, or `lopper.py`
themselves for the shipped reference boards.

### Prerequisites

```bash
# Activate the venv that ships with the repo
source venv-lopper/bin/activate

# The pipeline uses cpp + dtc (standard distro packages)
sudo apt-get install -y cpp device-tree-compiler   # Debian/Ubuntu
```

### Build the SDT for a shipped board

```bash
# AMD Versal VCK190 (AI Core)
scripts/build-board-sdt.py --board versal-vck190 -o /tmp/vck190-build

# AMD Versal VEK280 (AI Edge)
scripts/build-board-sdt.py --board versal-vek280 -o /tmp/vek280-build

# NXP i.MX 8M Mini EVK
scripts/build-board-sdt.py --board imx8mm-evk -o /tmp/imx8mm-build
```

Each invocation writes the following files into the output directory:

| File | Stage | Purpose |
|---|---|---|
| `<board>-linux.flat.dts` / `<board>-zephyr.flat.dts` | flatten | cpp + dtc preprocessed inputs |
| `<board>-non-linux.yaml` | `compose_non_linux` | rich-property co-processor extract |
| `<board>-system-top.dts` | `assemble_sdt` | **the SDT** — Linux DT base + non-Linux overlay |
| `<board>-sdt-devices.yaml` | `sdt_devices` | full device enumeration of the SDT, for glob-driven `domains.yaml` |
| `<board>-sdt-domains.yaml` | `sdt_domains` | one starter domain per `cpus,cluster`, partitioned by source tag — edit-then-use |

Useful flags:

| Flag | Effect |
|---|---|
| `--no-zephyr` | Skip the Zephyr-side flatten and `compose_non_linux` stage. Produces a Linux-only SDT (the Linux DT with `cpus,cluster` wrapping and no non-Linux overlay). |
| `--domains PATH` | User's per-deployment domains.yaml overlay, deep-merged on top of the shipped per-board template (overlay wins by `dev` key; new entries added). See [Bringing your own domains.yaml](#bringing-your-own-domainsyaml). |
| `--no-template` | Skip the shipped per-board template; use only `--domains` (or nothing) as the integration source. Diagnostic / bring-your-own-template use. |
| `-I`, `--input-dirs DIR` | Lopper include directory, forwarded to every lopper invocation in the run. Used to find SoC-facts YAML you keep in your own repo at `<DIR>/data/socs/` (searched before the shipped `lopper/data/socs/`). Repeatable. See [Keeping SoC files in your own repo](#keeping-soc-files-in-your-own-repo). |
| `-v`, `--verbose` | Print each cpp/dtc/lopper invocation as it runs. |

### Verify the result

`dtc` parses the assembled SDT cleanly:

```bash
dtc -I dts -O dtb -o /tmp/vck190-build/sdt.dtb \
    /tmp/vck190-build/versal-vck190-system-top.dts
```

Goldens for each reference board live at
`lopper/data/boards/<board>/expected-sdt.dts` and
`lopper/data/boards/<board>/expected-non-linux.yaml`. To
confirm the script reproduces them:

```bash
diff /tmp/vck190-build/versal-vck190-system-top.dts \
     lopper/data/boards/versal-vck190/expected-sdt.dts
```

### Bringing your own domains.yaml

The `--board <name>` invocation above auto-locates the
**shipped template** under
`lopper/data/boards/<board>/domains.yaml` (inside this repo). That
template is pre-populated with the board's integration declarations
and a skeleton partition, and is enough for first-run / reference
builds.

For an actual deployment you do **not** copy the template. Instead,
write a small **overlay** file — name it whatever you like, put it
wherever you like (it never has to live under the lopper tree) —
containing only your edits, and pass its path with `--domains`.
`build-board-sdt.py` resolves the path against your current
directory, so relative or absolute both work. The pipeline
deep-merges your overlay on top of the shipped template (your
entries override the template's by `dev` key; new entries are
added), so `git pull` keeps the template fresh underneath without
disturbing your overlay.

```bash
# Your overlay holds only what differs from the template.
cat > ~/work/my-vck190-overrides.yaml <<'EOF'
domains:
  default:
    compatible: openamp,domain-v1
    domains:
      RPU:
        compatible: openamp,domain-v1
        memory:
          # shrink the template's rpu0_reserved carveout
          - dev: rpu0_reserved
            start: 0x3e000000
            size: 0x100000
            no-map: true
EOF

scripts/build-board-sdt.py --board versal-vck190 \
    --domains ~/work/my-vck190-overrides.yaml \
    -o /tmp/vck190-build
```

The merged result is the same `openamp,domain-v1` content the
downstream Lopper domain-processing tools consume when they slice
`system-top.dts` — one overlay file, two consumers (SDT-build
extracts integration declarations from the merged view; the
partitioner reads partition intent from it).

The shipped template is **not** meant to be edited in place; `git
pull` will clobber edits. Keep your changes in the overlay.

To ignore the shipped template entirely (bring-your-own-template,
or a diagnostic run), add `--no-template` — then only your
`--domains` file feeds the integration overlay.

#### `sdt-domains.yaml` vs. your `domains.yaml`

`sdt-domains.yaml` is a generated file in the same
`openamp,domain-v1` shape as your `domains.yaml`, produced by
`sdt_domains` walking the assembled SDT. It is a **candidate
partition, not a device list**: one domain per `cpus,cluster`, with
a first-guess assignment of resources by `lopper-source` tag — the
Linux/`APU` domain gets a single `dev: '*'` access glob (Linux
claims everything by default), and each co-processor domain
(`RPU` / `MCU`) enumerates its source-tagged peripherals plus the
reserved-memory carve-outs whose names match it. (The flat
per-device enumeration you glob *against* is the separate
`sdt-devices.yaml`.) Read it to see how the chip would split by
default, then copy what you want into your own overlay.

It is a different file from your hand-edited `domains.yaml`:

|                                        | `<board>-sdt-domains.yaml`                   | your `domains.yaml`                             |
|----------------------------------------|----------------------------------------------|-------------------------------------------------|
| Producer                               | `sdt_domains` assist (regenerated every run) | hand-edited by you                              |
| Lives where                            | output directory alongside the SDT           | your deployment workspace                       |
| Authoritative?                         | No — disposable reference / snapshot         | Yes — downstream tools consume this             |
| Carries integration declarations?      | No — only mirrors what's in the SDT          | Yes — `no-map` memory entries you want injected |
| Edited?                                | No — read for ideas, copy fragments out      | Yes — both integration and partition            |
| Survives `git pull` / pipeline re-run? | Regenerated each run                         | Yes (lives outside the repo)                    |

The intended workflow: after a pipeline run, read the regenerated
`sdt-domains.yaml` to see what the SDT contains and how
`sdt_domains` would partition it by default, then pull useful bits
into your own hand-edited `domains.yaml` (or use it as a starting
template when first setting up a deployment).

### Globbing against `sdt-devices.yaml`

The other generated file, `<board>-sdt-devices.yaml`, is a flat
enumeration of every device in the assembled SDT (tagged
`openamp,domain-v1,devices`). Its job is to be the **vocabulary**
your `domains.yaml` globs against: instead of listing every
peripheral by name in an access list, you write a glob like
`dev: "*serial*"` and let Lopper expand it against the enumeration.

Load the enumeration and your domains file together as inputs — the
enumeration is the parent the patterns resolve against:

```bash
lopper -f --permissive --enhanced --auto \
    -i /tmp/vck190-build/versal-vck190-sdt-devices.yaml \
    -i ~/work/my-vck190-domains.yaml \
    /tmp/vck190-build/versal-vck190-system-top.dts  out.dts
```

A `domains.yaml` access list can then mix globs with explicit
entries — for example, hand everything matching `*serial*` and
`*i2c*` to one domain:

```yaml
access:
  - dev: "*serial*"
  - dev: "*i2c*"
```

Globs only resolve when the device-enumeration parent is present on
the command line; without the `-i <board>-sdt-devices.yaml` input
there is nothing for the patterns to expand against. (This is
distinct from `sdt-domains.yaml`, which is a candidate partition,
not a glob target.)

### Keeping SoC files in your own repo

The per-SoC silicon-facts files (PM device IDs, cluster shapes,
TCM/OCM map — see [Concepts](#concepts)) normally ship under
`lopper/data/socs/`. You are not limited to that in-tree directory:
pass `-I <dir>` to `build-board-sdt.py` and the pipeline also looks
for SoC YAML at the **same relative layout** under your directory,
i.e. `<dir>/data/socs/*.yaml`. Your directory is searched **before**
the shipped one, so a file you provide can override a shipped SoC
(same `matches:`) or add a brand-new one — without ever editing the
lopper tree:

```bash
# my-soc-repo/data/socs/my-soc.yaml describes a new/!customised SoC
scripts/build-board-sdt.py --board my-board \
    -I my-soc-repo \
    -o /tmp/my-build
```

`-I` is repeatable, takes a relative or absolute path (resolved
against your current directory), and is forwarded to every lopper
invocation in the run. The enforced `data/socs/` subpath means
files can't be just *anywhere* — they must mirror the shipped
layout. This parallels the `domains.yaml` template+overlay story:
shipped files are a starting point, your repo carries the
customisations.

To bootstrap a starter SoC YAML for a new family, see
`scripts/extract-pm-ids.py` (it reads a public PM-ID dt-binding
header; `--prefix` / `--prefix-regex` handle vendor conventions the
built-in set doesn't cover).

### Running the integration tests

The same flow is what `tests/test_sdt_from_linux.py` exercises via
the same script. Single command:

```bash
./run_pytest.sh tests/test_sdt_from_linux.py
```

It runs `compose_non_linux`, `assemble_sdt`, and `sdt_domains` for
each reference board (VCK190, VEK280, i.MX 8MM), plus `sdt_devices`
enumeration and a Linux-only `--no-zephyr` degradation case. All
diff against committed goldens — drift in the pipeline fails the
test.

`tests/test_sdt_roundtrip.py` goes one step further: it generates
the SDT, partitions it back into per-OS device trees, and checks the
slices (see [Per-OS device tree slicing](#per-os-device-tree-slicing)).

### Driving the stages manually (for reference)

The script encodes the right cpp include paths and chained Lopper
invocations for each shipped board. If you need to drive the
individual stages directly (e.g. to debug a single stage or
integrate with another build system), the breakdown is:

1. **`cpp` + `dtc`** on the Linux DT — flatten it, with the cpp
   include paths declared in the board's `source.yaml`.
2. **`cpp` + `dtc`** on the Zephyr DT (when present) — same shape,
   honoring `dtc_force: true` for boards that set it.
3. **`compose_non_linux`** Lopper assist on the flat Zephyr DT,
   with `--linux-dt <flat>` for address dedup and `--board <name>`
   to locate the domains.yaml, producing
   `<board>-non-linux.yaml`.
4. **`assemble_sdt`** Lopper assist with `--linux-dt <flat>` and
   `--non-linux <yaml>`, producing `<board>-system-top.dts`. With
   `--non-linux` omitted (the `--no-zephyr` path) it produces a
   Linux-only SDT.
5. **`sdt_devices`** Lopper assist on the assembled SDT with
   `-o <board>-sdt-devices.yaml`, producing the device enumeration
   used as a parent for glob-driven `domains.yaml` files.
6. **`sdt_domains`** Lopper assist on the assembled SDT
   with `-o <board>-sdt-domains.yaml`, producing the starter
   partition the user edits.

Run `scripts/build-board-sdt.py --board <name> -v` to see the exact
commands the script executes for any given board. The script's
source (`scripts/build-board-sdt.py`) is also the reference
implementation if you need to port the orchestration into another
build environment.

## Using the SDT to generate downstream artifacts

Once `system-top.dts` exists, the broader Lopper tooling ecosystem
can consume it to produce per-OS artifacts. The exact assist names
and invocations depend on the target — some capability categories
have production implementations for specific SoC families today,
others are natural future extensions of the same SDT-consuming
pattern. The commands below show the *shape* of the invocation for
each category; treat them as illustrative rather than ready-to-run
one-liners for every SoC.

The general pattern is:

```bash
python3 lopper.py -f <system-top.dts> <output> -- <assist-name> [options]
```

### Per-OS device tree slicing

Given partition intent (which devices belong to which OS), slice
the SDT into one device tree per OS context — one for Linux, one
for the R5 firmware view, one for Zephyr, etc. Each output is
dtc-compilable and contains the right reserved-memory carve-outs
for that OS.

This is the standard Lopper two-step domain flow. First expand the
partition (and its globs) into the SDT, then run `domain_access`
once per domain to filter the tree down to that OS's view:

```bash
# 1. Expand the partition into the SDT (resolve dev: "*" globs
#    against the device enumeration).
lopper -f --permissive --enhanced --auto \
    -i versal-vck190-sdt-devices.yaml \
    -i versal-vck190-sdt-domains.yaml \
    versal-vck190-system-top.dts  expanded.dts

# 2. Slice each domain out (paths are the expanded /domains/<...>
#    node paths; multi-domain trees name them domain@0, domain@1, …).
lopper -f --enhanced expanded.dts linux.dts \
    -- domain_access -t /domains/default/domain@0     # APU / Linux
lopper -f --enhanced expanded.dts rpu.dts \
    -- domain_access -t /domains/default/domain@1     # RPU
```

`domain_access` refcounts the target domain's `cpus` and deletes the
unreferenced `cpus,cluster` nodes, so the Linux slice drops the R5
cluster (and the `/non_linux_soc` co-processor bus), while the RPU
slice drops the A72 cluster and the Linux-only peripheral nodes.
Both slices are dtc-clean. The full `/domains` block is retained in
each slice by design — a breadcrumb of how the per-OS tree was
produced.

`tests/test_sdt_roundtrip.py` runs exactly this flow on the VCK190
SDT and asserts the slices are dtc-clean with the correct cluster
present and the foreign cluster/bus pruned.

### Baremetal / RTOS artifacts aligned with the kernel layout

Generate BSP-shaped artifacts (register-address headers, driver
tables, build-system metadata, linker scripts) consistent with the
kernel's view of the same hardware. Peripheral addresses, IRQ
numbers, TCM/OCRAM regions, and cluster identities all come from
the same SDT, so the co-processor firmware's load address matches
the kernel's reserved-memory carve-out by construction.

### OpenAMP / rpmsg resource alignment

Configure both ends of an A↔M (or A↔R) IPC channel from one
source. The mailbox endpoints, shared-memory region, and ring
buffer layout are all in the SDT; the configuration generator can
emit both sides' config simultaneously so they cannot drift.

### FPGA / PL overlays coordinated with cluster resources

Generate device-tree overlays for dynamically loaded
programmable-logic IP. The overlay generator can read the SDT to
see which addresses the static partition already uses and which
cluster owns each region, and emit overlays guaranteed not to
collide with the existing assignment.

### Hypervisor / emulation configs

Generate Xen domU configs, QEMU machine-memory descriptions, and
image-builder manifests from the same partition declaration the
kernel sees. The SDT becomes the single source of truth feeding
the kernel build, the hypervisor config, and the emulation
descriptor simultaneously.

### Cross-OS validation

Run the audit framework against the SDT and the per-OS DTs to
catch integration errors at build time — devices claimed by two
domains, reserved-memory overlaps, IRQ conflicts, dt-schema
binding violations. Errors that would otherwise surface at boot
or, worse, at runtime become pre-merge CI failures.

## Running it on your own board

The reference boards under `lopper/data/boards/` are templates
for adding your own. The minimum input set for a new board:

1. **Upstream Linux DT** for your board, vendored locally (use
   `scripts/sync-upstream.py` against your own kernel checkout —
   see `scripts/upstream-manifest.yaml` for the manifest format).
2. **Upstream Zephyr DT** for the co-processor, if one exists.
   Optional — the pipeline degrades gracefully without it (you
   get the Linux view only).
3. **SoC silicon-facts YAML** under `lopper/data/socs/<family>.yaml`.
   `scripts/extract-pm-ids.py` bootstraps the PM-ID table from a
   public PM dt-binding header; the rest is hand-curated from
   the SoC's public TRM. New SoC = one new YAML file, no code
   changes.
4. **Per-board directory** under `lopper/data/boards/<your-board>/`
   containing `source.yaml` (declares input paths + cpp include
   paths) and `domains.yaml` (integration decisions Linux doesn't
   carry — reserved-memory carve-outs for co-processor firmware,
   rpmsg shared regions, etc.). Copy from the closest reference
   board to start.

Once those are in place, the pipeline above works unchanged with
`--board <your-board>`.

## References

- `scripts/sync-upstream.py` — vendoring upstream Linux/Zephyr
  into `lopper/data/upstream/` from local clones at pinned tags
- `scripts/extract-pm-ids.py` — bootstrap a starter SoC YAML
  from a public PM-ID dt-binding header (a kernel
  `include/dt-bindings/power/<soc>.h` header of `#define PM_DEV_*`
  power-management IDs; see the architecture doc for details)
- `scripts/upstream-manifest.yaml` — board-scoped list of files to
  vendor (`sources:` metadata + per-board `files:` grouped by source)
- `lopper/data/socs/README.md` — schema for SoC silicon-facts files
- `lopper/data/boards/<board>/source.yaml` — declarative pipeline
  inputs for one board (header comments document the schema)
- `lopper/data/boards/<board>/domains.yaml` — hand-written
  integration decisions per board (header comments include
  inline examples)
- `tests/test_sdt_from_linux.py` — integration tests that exercise
  the generation flow end-to-end on all three reference boards
- `tests/test_sdt_roundtrip.py` — round-trip test: generate the SDT,
  partition it back into per-OS device trees, check the slices
