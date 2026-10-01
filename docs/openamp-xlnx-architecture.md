# OpenAMP Xilinx Assist Architecture

## Table of contents

1. [Purpose](#purpose)
2. [Workflow overview](#workflow-overview)
3. [Shared OpenAMP processing](#shared-openamp-processing)
4. [Linux remoteproc nodes](#linux-remoteproc-nodes)
5. [Zephyr remote workflow](#zephyr-remote-workflow)
6. [Libmetal workflow](#libmetal-workflow)
7. [Responsibility boundaries](#responsibility-boundaries)
8. [Validation](#validation)

## Purpose

The OpenAMP Xilinx assists convert a System Device Tree and domain metadata
into coordinated host and remote descriptions. The inputs describe processors,
memory carveouts, signaling, and domain relationships once; separate outputs
then serve Linux, Zephyr, and bare-metal consumers.

The architecture keeps transport processing, Zephyr MPU generation, and
Zephyr linker generation separate. This prevents application-specific linker
policy from leaking into generic domain pruning or OpenAMP relationship code.

## Workflow overview

```text
System Device Tree + domain YAML
                 │
                 ▼
          OpenAMP transform
          - host/remote relation
          - ELF-load carveouts
          - vrings and buffers
          - IPI/mailbox transport
          - Zephyr memory policy
                 │
       ┌─────────┴──────────┐
       │                    │
       ▼                    ▼
Zephyr MPU assist    Zephyr linker assist
       │                    │
       ▼                    ▼
conventional DT       complete linker script
       │                    │
       └─────────┬──────────┘
                 ▼
       processor-domain pruning
                 │
                 ▼
          final Zephyr build
```

The supported RPU cases are:

| Family | Processor | Boot modes |
|---|---|---|
| ZynqMP | Cortex-R5 split core | ATCM |
| Versal | Cortex-R5 split core | ATCM |
| Versal Gen 2 | Cortex-R52 split core | ATCM or DDR |

## Shared OpenAMP processing

The shared transform resolves:

- the selected remote processor and domain;
- remoteproc ELF-load regions;
- resource-table, vring, and buffer carveouts;
- IPI or mailbox signaling endpoints; and
- relations between the Linux host and remote domain.

For Zephyr RPMsg, the three contiguous vring/buffer ranges are represented by
one `zephyr,ipc_shm` node. ZynqMP R5 uses its direct IPM endpoint. Versal R5
and Versal Gen 2 use the mailbox transport.

The OpenAMP transform retains common memory-policy metadata for the dedicated
Zephyr assists. It does not assign application objects to linker regions.

## Linux remoteproc nodes

For a Linux host, each remoteproc relation becomes a core node in a
`remoteproc@<base>` cluster node, as the `xlnx,zynqmp-r5fss` binding
describes. The values come from the SDT and the domain YAML:

| Value | Source |
|---|---|
| RPU core number | Unit address N of the remote's `cpus-r5@N` or `cpus-r52@N` cluster |
| Core node `r5f@<i>` / `r52f@<i>`, bank index | The core's position in its two-core RPU cluster, N % 2 |
| TCM bank global address and size | The bank node's `reg` |
| TCM bank power domain | The bank node's `power-domains`; `xlnx,power-domain` is not used |
| TCM bank type | ATCM, BTCM or CTCM in the bank node's name |
| TCM bank core-local address | The RPU cluster `address-map`, when it maps the bank at a core-local address (ZynqMP SDTs); otherwise the CPU's TCM layout: R5 ATCM 0x0, BTCM 0x20000; R52 ATCM 0x0, BTCM 0x10000, CTCM 0x18000 |
| Cluster base | The aligned TCM span holding the remote's banks (1 MB on R5, 512 KB on R52), which starts at the cluster's core 0 ATCM; for a remote without TCM, the Nth such span among the SDT's TCM banks, N being the core number // 2 |

YAML expansion stores the core number, the cluster's TCM `address-map`
entries and the cluster base on the remote domain (`rpu_core_num`,
`rpu_tcm_view`, `rpu_cluster_base`), because OpenAMP runs on a domain tree
from which the RPU cluster nodes have been removed.

The R52 core-local TCM addresses are set by software at boot. The Zephyr
assists configure them from the same layout (`xlnx_rpu_tcm.py`), so Zephyr
firmware and the Linux remoteproc node agree.

A cluster in lockstep runs one remote, on its core 0. R52 cores do not combine
TCM, so an R52 lockstep remote loads its own banks at the same addresses as in
split mode. In R5 lockstep the two cores' TCMs are combined: the remote lists
core 0's banks and the SDT's lockstep banks at 0xffe10000 and 0xffe30000, all
in bank 0 at their offset in the cluster's TCM span (0x0, 0x20000, 0x10000,
0x30000), named `atcm0`, `btcm0`, `atcm1` and `btcm1`.

Lopper copies each bank's `power-domains` from the SDT. Current SDTs give the
R5 lockstep banks core 0's power domains, where Linux expects core 1's; the
generated node then lists a power domain twice and Lopper warns that the
output is malformed because of the SDT input. The output is correct once the
SDT is fixed.

## Zephyr remote workflow

### Common memory policy

The policy gives stable logical names to physical memories:

```text
ATCM          boot and instruction-side local TCM
BTCM          data-side local TCM
CTCM          additional R52 local TCM
DDR           firmware DDR
DDR_RESOURCE  separate resource-table DDR, when present
```

Each entry has an explicit target and read, write, execute, cache, share, and
userspace policy. Linker metadata assigns Zephyr section groups to those
logical memories and selects `_vector_table` as the entry.

The optional `static` policy flag describes MPU ownership, not storage
duration. It means that Zephyr's linker and architecture code create the
precise MPU regions for sections placed in that memory. The MPU assist still
emits `compatible = "zephyr,memory-region"` and `zephyr,memory-region`, so the
memory remains available to the linker, but it omits the broad
`zephyr,memory-attr` region. Use `static` for boot memories such as ATCM, BTCM,
CTCM, and DDRBOOT whose vector, text, data, and BSS permissions are derived
from linker boundaries. Do not use it for shared regions such as resource
tables and trace buffers that need an explicit DT MPU override of a broad SoC
mapping.

### MPU generation

The MPU assist emits standard Zephyr properties:

```dts
memory@... {
	compatible = "zephyr,memory-region";
	zephyr,memory-region = "DDR";
	zephyr,memory-attr = <...>;
};
```

It also selects the boot memory through an absolute `zephyr,sram` path.

For a memory marked `static`, only the linker metadata above is emitted; the
architecture-managed section mappings remain authoritative. This prevents a
single high-priority DT MPU entry from hiding the more precise executable and
writable regions generated for the image.

R5 regions obey ARMv7-R power-of-two size and natural-alignment rules. A DDR
firmware carveout is expanded to the smallest representable window that
contains it and does not overlap TCM. R52 uses aligned base/limit regions and
rejects overlapping generated ranges.

### Linker generation

The linker assist generates a complete, versioned Zephyr Cortex-R linker
script. It supports independent placement of vectors, text, rodata, data,
BSS, no-init, heap, stack, resource table, and safe user-defined sections.

The profile is inferred:

```text
R5  vectors at ATCM address zero  → R5 TCM boot
R52 vectors at ATCM address zero  → R52 TCM boot
R52 vectors in DDR                → R52 DDR boot
```

The generated ELF entry is `_vector_table`. For TCM boot it resolves to local
address zero; for R52 DDR boot it resolves to the configured DDR vector
address.

### Domain generation

The generic domain assist prunes unrelated devices, normalizes processor-local
addresses, and preserves already generated conventional Zephyr memory nodes.
It does not create MPU policy or linker placement. Existing `zephyr,sram`,
`zephyr,memory-region`, and `zephyr,memory-attr` properties remain authoritative.

## Libmetal workflow

The Libmetal output path consumes OpenAMP relations after optional
domain-access pruning. It selects the timer, IPI, interrupt, bus, and shared
memory described for the requested processor and operating system.

Conditional domain-access metadata can expose a TTC as Linux UIO while
retaining its native binding in the bare-metal R5 domain. Requests that do not
match a relation report the supported processor/OS targets rather than failing
with an internal exception.

## Responsibility boundaries

```text
OpenAMP transform
  owns relations, carveouts, and signaling

Zephyr MPU assist
  owns permissions and hardware-representable MPU ranges

Zephyr linker assist
  owns MEMORY regions, section placement, and ELF entry

gen_domain_dts
  owns generic pruning and address/name normalization

OpenAMP application
  consumes the generated DTS and complete linker script
```

The design intentionally has no AMD-specific DDR chosen property and no
application-owned RPU linker-region convention. Logical memory names are
resolved through the common policy and emitted through standard Zephyr
memory-region properties.

## Validation

Generation rejects missing or ambiguous targets, invalid TCM local addresses,
unsupported boot modes, permission conflicts, invalid R5 MPU geometry, R52
overlap, out-of-range section offsets, and custom sections that consume Zephyr
ABI-owned inputs.

Sanity coverage includes R5 TCM boot, R52 TCM and DDR boot, R5 DDR aperture
alignment, MPU attributes, custom linker sections, Libmetal Linux/R5 domains,
and the complete MPU/linker through final Zephyr-domain pipeline.
