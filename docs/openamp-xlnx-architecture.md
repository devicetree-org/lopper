# OpenAMP Xilinx Assist Architecture

## Table of contents

1. [Purpose](#purpose)
2. [Workflow overview](#workflow-overview)
3. [Shared OpenAMP processing](#shared-openamp-processing)
4. [Zephyr remote workflow](#zephyr-remote-workflow)
5. [Libmetal workflow](#libmetal-workflow)
6. [Responsibility boundaries](#responsibility-boundaries)
7. [Validation](#validation)

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
