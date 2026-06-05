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

The pipeline ships with two reference boards exercised end-to-end
in CI: **AMD Versal VCK190** and **NXP i.MX 8M Mini EVK**.

## Concepts

| Term | Meaning |
|---|---|
| **Linux DT** | The board's upstream Linux kernel device tree. Describes Linux's view: the cluster Linux runs on and the peripherals reachable from it. |
| **Zephyr DT** | The board's upstream Zephyr RTOS device tree (when available). Describes the co-processor's view: M-core / R-core CPU, TCM/OCRAM, and the other side of the IPC mailbox. |
| **SoC silicon-facts YAML** | A per-SoC file shipped under `lopper/data/socs/`. Public silicon facts (PM device IDs, cluster shapes, TCM/OCM map, IPI topology). Sourced from kernel headers and the public TRM. One per silicon family. |
| **Board augment YAML** | A small hand-written file under `lopper/data/boards/<board>/augment.yaml`. Carries the integration decisions neither Linux nor Zephyr describes — typically reserved-memory carve-outs for co-processor firmware and the rpmsg shared region. |
| **`compose_non_linux`** | Lopper assist that walks the Zephyr DT (and the per-board augment YAML), captures every node not already present at the same address in the Linux DT, and emits an `openamp,domain-v1,non-linux` YAML carrying the full property set for each kept node. Phandle refs are encoded as canonical `"&label"` strings so they re-resolve against the merged tree at assembly time. |
| **`assemble_sdt`** | Lopper assist that loads the Linux DT as the SDT base, marks `/cpus` with the `cpus,cluster` compatible the SDT spec uses, then overlays the non-linux YAML's clusters / memory / devices on top, producing the `system-top.dts`. |

```
   Linux DT       ────────────────────────────────────────────────┐
                                                                  │
   Zephyr DT      ─┐                                              ├──→ assemble_sdt ──→ system-top.dts
   Augment YAML   ─┴─→ compose_non_linux ──→ non-linux.yaml  ────┘   (Linux DT base
                       (per-node rich props,                            + cpus,cluster wrap
                        phandle refs as "&label")                       + non-Linux overlay)
```

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
# AMD Versal VCK190
scripts/build-board-sdt.py --board versal-vck190 -o /tmp/vck190-build

# NXP i.MX 8M Mini EVK
scripts/build-board-sdt.py --board imx8mm-evk -o /tmp/imx8mm-build
```

Each invocation writes a handful of files into the output directory
and prints a summary. The artifact that matters is
`<output-dir>/<board>-system-top.dts` — the assembled SDT.

Useful flags:

| Flag | Effect |
|---|---|
| `--no-zephyr` | Skip the Zephyr-side flatten and `compose_non_linux` stage. Produces a Linux-only SDT (the Linux DT with `cpus,cluster` wrapping and no non-Linux overlay). |
| `--no-augment` | Suppress the per-board augment overlay during `compose_non_linux`. Useful for diagnostic runs (compare the un-augmented non-linux YAML against the augmented one to see what the board YAML contributed). |
| `-v`, `--verbose` | Print each cpp/dtc/lopper invocation as it runs. |

### Verify the result

`dtc` parses the assembled SDT cleanly:

```bash
dtc -I dts -O dtb -o /tmp/vck190-build/sdt.dtb \
    /tmp/vck190-build/versal-vck190-system-top.dts
```

Goldens for both reference boards live at
`lopper/data/boards/<board>/expected-sdt.dts` and
`lopper/data/boards/<board>/expected-non-linux.yaml`. To
confirm the script reproduces them:

```bash
diff /tmp/vck190-build/versal-vck190-system-top.dts \
     lopper/data/boards/versal-vck190/expected-sdt.dts
```

### Running the integration tests

The same flow is what `tests/test_sdt_from_linux.py` exercises via
the same script. Single command:

```bash
./run_pytest.sh tests/test_sdt_from_linux.py
```

Five tests run end-to-end: `compose_non_linux` and `assemble_sdt`
for each reference board (four tests), plus a Linux-only SDT case
for the `--no-zephyr` degradation path. All diff against the
committed goldens — drift in the pipeline fails the test.

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
   to locate the augment YAML, producing
   `<board>-non-linux.yaml`.
4. **`assemble_sdt`** Lopper assist with `--linux-dt <flat>` and
   `--non-linux <yaml>`, producing `<board>-system-top.dts`. With
   `--non-linux` omitted (the `--no-zephyr` path) it produces a
   Linux-only SDT.

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
   paths) and `augment.yaml` (integration decisions Linux doesn't
   carry — reserved-memory carve-outs for co-processor firmware,
   rpmsg shared regions, etc.). Copy from the closest reference
   board to start.

Once those are in place, the four-stage flow above works
unchanged with `--board <your-board>`.

## References

- `scripts/sync-upstream.py` — vendoring upstream Linux/Zephyr
  into `lopper/data/upstream/` from local clones at pinned tags
- `scripts/extract-pm-ids.py` — bootstrap a starter SoC YAML
  from a public PM-ID dt-binding header
- `scripts/upstream-manifest.yaml` — declarative list of files to
  vendor from each upstream source
- `lopper/data/socs/README.md` — schema for SoC silicon-facts files
- `lopper/data/boards/<board>/source.yaml` — declarative pipeline
  inputs for one board (header comments document the schema)
- `lopper/data/boards/<board>/augment.yaml` — hand-written
  integration decisions per board (header comments include
  inline examples)
- `tests/test_sdt_from_linux.py` — integration tests that exercise
  the four-stage flow end-to-end on both reference boards
