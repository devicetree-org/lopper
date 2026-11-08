Domain Specific YAML Simplifications (Under Discussion)
=======================================================

.. include:: under-discussion.txt

For simplicity and ease of use, System Device Tree comes with an
alternative representation in YAML, see :ref:`simplified-yaml`.

This document describes the domain-oriented YAML conventions currently
used by lopper, including the OpenAMP and Libmetal overlay patterns
shipped in ``meta-xilinx-standalone-sdt/conf/domainyaml``.


Current Reference Overlays
--------------------------

The current reference overlays are grouped into two families:

- OpenAMP overlays:
  ``openamp-overlay-zynqmp.yaml``,
  ``openamp-overlay-versal.yaml``,
  ``openamp-overlay-versal-net.yaml``, and
  ``openamp-overlay-versal-2ve-2vm.yaml``
- Libmetal overlays:
  ``libmetal-overlay-zynqmp.yaml``,
  ``libmetal-overlay-versal.yaml``,
  ``libmetal-overlay-versal-net.yaml``, and
  ``libmetal-overlay-versal-2ve-2vm.yaml``

These files all use the same three top-level sections:

- ``reserved-memory`` for named carveouts
- optional ``axi`` for UIO or MMIO helper nodes
- ``domains`` for execution domains and domain-to-domain relations


Hierarchy
---------

Domains are still represented under ``/domains``.

In YAML this appears as a top-level ``domains:`` mapping whose keys are
the domain names:

.. code-block:: YAML

   domains:
     APU_Linux:
       compatible: openamp,domain-v1

     RPU_Zephyr:
       compatible: openamp,domain-v1


Parent
------

Optionally, the name of the parent node can be explicitly specified
using the ``parent`` key. This remains useful when domain information is
spread across multiple YAML files.

.. code-block:: YAML

   domains:
     parent-domain:
       compatible: openamp,domain-v1

     child-domain:
       parent: parent-domain
       compatible: openamp,domain-v1


Reserved Memory
---------------

Current OpenAMP and Libmetal overlays define carveouts at the top level
under ``reserved-memory``. Each entry is named using the final device
tree node name, typically ``name@address``.

.. code-block:: YAML

   reserved-memory:
     ranges: true
     "#size-cells": 2
     "#address-cells": 2

     rproc0@9800000:
       start: 0x9800000
       size: 0x60000
       no-map: 1

     vdev0vring0@9860000:
       start: 0x9860000
       size: 0x4000
       no-map: 1

Two reference styles are used from domains and relations:

- a domain's ``reserved-memory`` property is a list of node names
- relation properties such as ``elfload`` and ``carveouts`` also use
  node names

Example:

.. code-block:: YAML

   domains:
     APU_Linux:
       reserved-memory:
         - vdev0buffer@9868000
         - vdev0vring1@9864000
         - vdev0vring0@9860000
         - rproc0@9800000

       domain-to-domain:
         remoteproc-relation:
           compatible: openamp,remoteproc-v2
           relation0:
             elfload:
               - psu_r5_0_atcm_global@ffe00000
               - psu_r5_0_btcm_global@ffe20000
               - rproc0@9800000

         rpmsg-relation:
           compatible: openamp,rpmsg-v1
           relation0:
             carveouts:
               - vdev0vring0@9860000
               - vdev0vring1@9864000
               - vdev0buffer@9868000


AXI Helper Nodes
----------------

Libmetal overlays add an ``axi`` section for nodes that should appear in
the generated device tree as MMIO helper devices, typically
``compatible = "uio"`` timer, mailbox, and shared-memory descriptors.

.. code-block:: YAML

   axi:
     libmetal_uio_desc0@99c8000:
       start: 0x99c8000
       size: 0x4000
       compatible: "uio"

     timer@ff130000:
       compatible: "uio"

     mailbox@ff350000:
       compatible: "uio"


Access
------

The access property of domain nodes is specified with the following key:
value pairs:

- dev: device reference
- flags: flags


Example:

.. code-block:: YAML

   access:
       - dev: serial0
         flags: {read-only: true}

The device references specified using the 'dev' key has to be a subset
of the parent domain's access list of devices.

Instead of a device reference, it is also possible to pass a GLOB
pattern matching string. It will be matched against the parent domain's
access list. Please refer to [GLOB]_ for more details.

Example:

.. code-block:: YAML

    access:
        - dev: "*"


Memory and Sram
---------------

The memory and sram properties to specify the memory and sram
allocations to a domain are specified in YAML using start and size key:
value pairs to increase readability.

Example:

.. code-block:: YAML

   sram:
       - start: 0xfffc0000
         size: 0x1000
         flags: {read-only: true}

Instead of a start/size pair, a GLOB pattern matching string may be given as a
``memory`` entry. It is matched against the physical memory present in the
system; ``"*"`` selects all physical memory, so the domain keeps the full
memory map rather than a specific allocation. Please refer to [GLOB]_ for more
details.

Example:

.. code-block:: YAML

   memory:
       - "*"

A ``sram`` entry may use the same ``"*"`` pattern to select all sram rather
than a specific carveout. Note that the sram nodes themselves are ordinary
devices, so they are retained through the domain's ``access`` list (for
example ``access: - dev: "*"``); ``sram: "*"`` only indicates that no specific
sram allocation should be carved out for the domain. Please refer to [GLOB]_
for more details.

Example:

.. code-block:: YAML

   sram:
       - "*"


Cpus
----

The cpus property of domain nodes is specified with the following key:
value pairs:

- cluster: cpu cluster reference
- cpumask: cpumask in hex
- mode: unordered key: value pairs specifying the cpu mode
    - secure: true/false
    - el: the execution level


Example:

.. code-block:: YAML

   cpus:
       - cluster: cpus_a72
         cpumask: 0x3
         mode:
             secure: true
             el: 0x3


Domain-to-Domain Relations
--------------------------

Current overlays use ``domain-to-domain`` with one of three relation
compatibles:

- ``openamp,remoteproc-v2`` for Linux remoteproc generation
- ``openamp,rpmsg-v1`` for RPMsg channels
- ``libmetal,ipc-v1`` for Libmetal IPC and UIO helper generation

Each relation block contains one or more ``relationN`` children. The
common keys are:

- ``remote`` or ``host``: the peer domain
- ``elfload``: firmware or TCM memory references for remoteproc
- ``carveouts``: reserved-memory or AXI helper references
- ``mbox``: mailbox device reference
- ``timer``: timer device reference or list of timer references

Example:

.. code-block:: YAML

   domain-to-domain:
     compatible: openamp,domain-to-domain-v1

     remoteproc-relation:
       compatible: openamp,remoteproc-v2
       relation0:
         remote: RPU_Zephyr
         elfload:
           - r52_0a_atcm_global
           - r52_0a_btcm_global
           - r52_0a_ctcm_global
           - ddrboot@9800100
           - rsctbl@9800000

     rpmsg-relation:
       compatible: openamp,rpmsg-v1
       relation0:
         remote: RPU_Zephyr
         mbox: ipi_nobuf3_to_ipi_nobuf1
         carveouts:
           - vdev0vring0@9860000
           - vdev0vring1@9864000
           - vdev0buffer@9868000

     libmetal-relation:
       compatible: libmetal,ipc-v1
       relation0:
         remote: RPU_1_BM
         mbox: ipi_5_to_ipi_2
         timer: ttc2
         carveouts:
           - libmetal_uio_desc0@99c8000
           - libmetal_uio_desc1@99cc000
           - libmetal_uio_data@99d0000


Conditional Properties and ``lopper,activate``
----------------------------------------------

A single YAML file can describe multiple OS or configuration variants using
**conditional property sigils**.  A sigil is appended to a property name or
node name using ``!`` as a delimiter:

.. code-block:: YAML

   property-name!condition!merge-scheme: value
   node-name!condition:
     child-key: value

Supported merge schemes are ``replace`` (default), ``append``, ``prepend``,
and ``delete``.  The condition name may be any string (e.g. ``linux``,
``zephyr``, ``baremetal``).

Sigils may appear on any node in the tree, including nodes that are not under
``/domains/``.  This is the common pattern for per-domain driver binding:

.. code-block:: YAML

   axi:
     timer@f1e90000:
       compatible: "cdns,ttc"          # base — all domains without activation
       compatible!linux: "uio"         # linux overlay replaces with UIO binding

   domains:
     APU_Linux:
       compatible: openamp,domain-v1
       lopper,activate: linux          # selects overlay_tree('linux')
       cpus: ...
       memory: ...

     RPU1_BM:
       compatible: openamp,domain-v1
       # no lopper,activate — base tree used, compatible stays "cdns,ttc"
       cpus: ...
       memory: ...

When ``domain_access`` processes ``APU_Linux`` it reads ``lopper,activate``,
calls ``overlay_tree('linux')``, and uses that merged tree for all subsequent
processing.  The resulting tree has ``compatible = "uio"`` at the timer node.
When it processes ``RPU1_BM`` the base tree is used unchanged.

The ``lopper,activate`` property replaces and supersedes ``os,type`` for
overlay selection.  If ``lopper,activate`` is absent, ``os,type`` is used as
a fallback so existing domain YAML files work without modification.

For full syntax reference and API documentation see
``docs/conditional-properties.md``.


OS and Vendor Extensions
------------------------

The shipped overlays make regular use of the generic :ref:`domains-os-type`
property:

.. code-block:: YAML

   os,type: linux
   os,type: freertos
   os,type: baremetal
   os,type: zephyr

The current AMD Xilinx OpenAMP overlays also use these vendor-specific
keys on remote domains:

- ``xlnx,ddr-boot``: boolean flag indicating that firmware is loaded
  from DDR
- ``xlnx,zephyr,mems``: list of memory nodes that should be treated as
  Zephyr memory regions

Example:

.. code-block:: YAML

   RPU_Zephyr:
     compatible: openamp,domain-v1
     os,type: zephyr
     xlnx,ddr-boot: true
     xlnx,zephyr,mems: [ ddrboot@9800100 ]


RPU Sleep and Tick Timers
~~~~~~~~~~~~~~~~~~~~~~~~~

A baremetal or FreeRTOS domain may name the timers that its xiltimer library
uses for sleep and for the operating system tick:

``xlnx,sleep-timer``
   Sleep timer: a timer node label and a counter.  Supported for
   ``os,type: baremetal`` and ``os,type: freertos``.

``xlnx,tick-timer``
   Tick timer: a timer node label and a counter.  Supported for
   ``os,type: freertos`` only.

An explicit timer property for an unsupported ``os,type`` or role is an
error. In particular, ``xlnx,tick-timer`` is not supported for baremetal.

.. code-block:: YAML

   domains:
     RPU0_FREERTOS:
       os,type: freertos
       xlnx,sleep-timer: [ ttc3, 0 ]
       xlnx,tick-timer: [ ttc3, 1 ]

     RPU1_BAREMETAL:
       os,type: baremetal
       xlnx,sleep-timer: [ ttc1, 0 ]

YAML expansion turns the label into a phandle, which is the form a DTS
author writes directly:

.. code-block:: DTS

   xlnx,sleep-timer = <&ttc3 0>;
   xlnx,tick-timer = <&ttc3 1>;

If a domain sets neither property, the ``timer`` of its own RPMsg relation
is used: counter 0 for sleep and, for FreeRTOS, counter 1 for tick.  See
:ref:`Relation timers <remoteproc-relation-timers>`.  Explicit properties
win.

Lopper checks the timers on the assembled tree, so the checks apply to YAML
and DTS input alike.  The checks are selected by the timer node's
``compatible``.  The supported compatible is ``cdns,ttc`` (Cadence TTC).
A TTC has counters 0, 1 and 2; counter *k* starts at the TTC base address
plus 4 × *k*, which is the address of the counter node that
``lop-ttc-split.dts`` creates for the baremetal drivers.  Each of the
following is an error:

- the label or phandle does not resolve to a node;
- the timer node has no handler for its ``compatible``;
- the counter is not valid for the timer;
- the domain has an ``access`` list that does not include the timer;
- another domain uses the timer, through ``access``, a timer property or a
  relation, or the domain's own Libmetal relation uses it;
- the timer is a Linux UIO device (``compatible = "uio"``), as a Libmetal
  relation timer is;
- the sleep and tick timers are the same counter.

When the timer comes from the RPMsg relation instead, a failed check is a
warning and the BSP timers apply, so existing descriptions keep working.

For the RPU application, the ``openamp`` assist writes the selection as a C
file, ``amd_timer_config.c``, that defines the ``XSleepTimer_Instance`` and
``XTickTimer_Instance`` variables.  xiltimer defines both as weak variables
holding the counter base address, so the file selects the timers of one
application without changing the BSP.  The file is written only when the
domain names a timer::

   lopper system.dts -- openamp --openamp_header_only \
       --openamp_output_filename=amd_platform_info.h \
       --openamp_remote=psv_cortexr5_0 \
       --rpu-timer-config=amd_timer_config.c \
       --bsp-sleep-timer=0xff110000 --bsp-tick-timer=0xff100000

The optional ``--bsp-sleep-timer`` and ``--bsp-tick-timer`` give the
addresses the BSP uses.  When the domain leaves a role to the BSP and that
timer is used by another domain or relation, Lopper warns.

For Linux device tree output, a timer named by either property is set to
``status = "disabled"``, because it belongs to the remote domain.


Zephyr Linker and MPU Policy
----------------------------

Zephyr Cortex-R execution domains may describe linker placement and MPU
permissions directly in domain YAML.  This policy is a Zephyr build policy;
it does not require an OpenAMP relation, remoteproc, RPMsg, reserved-memory,
or an ``openamp,domain-v1`` compatible.  The selected domain shall have
``os,type: zephyr``, a resolvable Cortex-R5 or Cortex-R52 CPU reference, and
owned memories in ``sram`` and, when needed, ``reserved-memory``.

The standalone assists are invoked after the YAML has been expanded to a
system devicetree::

   lopper input.dts output.dts -- zephyr_mpu \
       --domain=/domains/RPU_Zephyr --zephyr-version=4.3

   lopper input.dts unused.dts -- zephyr_linker \
       --domain=/domains/RPU_Zephyr --zephyr-version=4.3

Memory policy
~~~~~~~~~~~~~

Each physical memory selected by the Zephyr domain carries an ``mpu-policy``
string list.  The accepted values are:

``readable``
   Permit reads from the region.

``writable``
   Permit writes to the region.

``executable``
   Permit instruction execution from the region.

``cacheable``
   Use the normal cacheable memory type.  If omitted, the generated Zephyr
   metadata marks the memory explicitly non-cacheable.

``shareable``
   Use architecture-defined shareability.

``userspace``
   Permit unprivileged access.  This is normally used with readable,
   writable, and shareable IPC memory.

``static``
   The SoC's static MPU table already maps the region.  The Zephyr MPU assist
   still emits linker memory metadata but does not emit a
   ``zephyr,memory-attr`` property for that region.  Thus ``static`` means
   "retain the static MPU mapping"; it does not mean that the memory or linker
   placement is immutable.

The supported permission combinations are read/write cacheable, read-only
cacheable, read/execute cacheable, read/write/execute cacheable, and
read/write/shareable/userspace non-cacheable.  The assist rejects unsupported
combinations and overlapping dynamic MPU regions.  Cortex-R5 DDR ranges are
expanded to a naturally aligned power-of-two MPU aperture; Cortex-R52 uses
base/limit regions and does not require that expansion.

Example physical-memory policy:

.. code-block:: YAML

   axi:
     r52_0a_atcm_global:
       mpu-policy!zephyr!append:
         [ readable, writable, executable, cacheable, static ]

     r52_0a_btcm_global:
       mpu-policy!zephyr!append:
         [ readable, writable, cacheable, static ]

   reserved-memory:
     ipc_shm@9860000:
       start: 0x9860000
       size: 0x80000
       mpu-policy!zephyr!append:
         [ readable, writable, shareable, userspace ]

The MPU assist converts this policy to conventional Zephyr properties:
``compatible = "zephyr,memory-region"``, ``zephyr,memory-region``, and, for
non-static entries, ``zephyr,memory-attr``.  It selects the vector-table
memory as ``/chosen/zephyr,sram`` and selects an IPC shared-memory node as
``/chosen/zephyr,ipc_shm`` when one is present.  Transformation-only
``mpu-policy`` properties are removed from the output.

Linker policy
~~~~~~~~~~~~~

The ``linker`` mapping is a child of the Zephyr domain in YAML.  It has the
following keys:

``linker_file_output_name``
   Required output path for the generated primary linker script.

``linker_memories``
   Required list of domain-owned memory references available to the linker.
   References may use a node name, label, path, phandle, or vendor IP name.
   If otherwise identical fallback names collide, the processor-visible
   ``reg`` origin is appended to form a unique GNU linker memory name.

``entry``
   Optional ELF entry symbol.  The current Cortex-R profiles require
   ``_vector_table`` and use it by default.

``user_content``
   Optional path to linker content appended after the generated script.

``sections``
   Required mapping of Zephyr logical section groups to memory regions.

The required logical groups are ``vector_table``, ``text``, ``rodata``,
``data``, ``bss``, ``noinit``, ``heap``, and ``stack``.  Each group contains a
``region`` reference.  ``vector_table`` and ``text`` may additionally contain
an ``offset``.  The selected permissions must allow each section's use, and
``stack`` and ``noinit`` shall use the same memory for the Zephyr 4.3 linker
ABI.

Example standalone Zephyr linker policy:

.. code-block:: YAML

   domains:
     RPU_Zephyr:
       os,type: zephyr
       sram: [ r52_0a_atcm_global, r52_0a_btcm_global ]
       linker:
         linker_file_output_name: RPU_ZEPHYR.ld
         linker_memories: [ r52_0a_atcm_global, r52_0a_btcm_global ]
         entry: _vector_table
         sections:
           vector_table: { region: r52_0a_atcm_global, offset: 0 }
           text:         { region: r52_0a_atcm_global }
           rodata:       { region: r52_0a_atcm_global }
           data:         { region: r52_0a_btcm_global }
           bss:          { region: r52_0a_btcm_global }
           noinit:       { region: r52_0a_btcm_global }
           heap:         { region: r52_0a_btcm_global }
           stack:        { region: r52_0a_btcm_global }

YAML expansion flattens this hierarchy into domain properties such as
``linker_memories``, ``linker-entry``, ``linker-section-text``, and
``linker-section-vector-table-offset``.  The flattened representation is an
intermediate Lopper ABI; authors should use the hierarchical YAML form.

The generator supports Cortex-R5 TCM boot, Cortex-R52 TCM boot, and
Cortex-R52 DDR boot.  It validates local TCM addresses and Cortex-R52 vector
alignment, then emits a versioned Zephyr primary linker script and a
``.layout.txt`` report.

OpenAMP resource-table extension
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

OpenAMP firmware may add a ``resource_table`` entry to ``sections``.  It has a
``region`` and optional ``offset`` and causes the linker generator to emit an
explicit ``.resource_table`` output section with
``__resource_table_start`` and ``__resource_table_end`` symbols.

.. code-block:: YAML

   linker:
     linker_memories: [ atcm, btcm, rsctbl ]
     sections:
       resource_table: { region: rsctbl, offset: 0x0 }

This entry is the only OpenAMP-specific logical section.  It is optional and
shall be omitted for standalone Zephyr firmware that does not contain an
OpenAMP resource table.


Baremetal and FreeRTOS Section Placement
----------------------------------------

Baremetal and FreeRTOS domains on a Cortex-R5 or Cortex-R52 RPU core may
carry a ``linker`` mapping that places each logical section in a TCM bank or
in DDR.  It is read by the ``baremetallinker_xlnx`` assist, which writes the
linker region of each section as CMake variables for the ESW and
openamp-system-reference linker templates.  The domain shall have ``os,type``
``baremetal`` or ``freertos`` (optionally followed by further comma-separated
fields, such as ``baremetal,newlib``).  A domain without a ``linker``
mapping gets the same linker script as before this mapping existed.

The ``linker`` mapping has the following keys:

``layout``
   Optional preset: ``tcm``, ``tcm-data`` or ``ddr``.  Without it, sections
   that ``sections`` does not place use ``ddr``.

``linker_memories``
   Optional list of memory references.  When present, every section region
   shall be one of them, and a preset may only use the TCM banks it lists.

``sections``
   Optional mapping of logical sections to regions; it overrides the preset.
   At least one of ``layout`` and ``sections`` is required.

The logical sections are ``vector_table``, ``text``, ``rodata``, ``data``,
``bss``, ``heap``, ``stack`` and ``resource_table``.  Each has a ``region``:
a TCM bank or memory node reference (node name, label, path or vendor IP
name), or the keyword ``ddr`` for the default DDR region of the target
(the lowest DDR, or the OpenAMP ELF-load carveout when the assist runs in
OpenAMP mode).  Only ``vector_table`` takes an ``offset``, and it shall be
0.

The presets place the sections as follows.  On Cortex-R52 the heap and
stack use the CTCM; on Cortex-R5 they use the BTCM.

.. list-table::
   :header-rows: 1

   * - Preset
     - vector_table
     - text, rodata
     - data, bss, resource_table
     - heap, stack
   * - ``tcm``
     - ATCM
     - ATCM
     - BTCM
     - BTCM (R5) or CTCM (R52)
   * - ``tcm-data``
     - ATCM
     - DDR
     - BTCM
     - BTCM (R5) or CTCM (R52)
   * - ``ddr``
     - ATCM
     - DDR
     - DDR
     - DDR

The assist supports RPU cores in split mode and uses these core-local TCM
addresses.  It rejects a domain whose ``cpus`` mode cell selects lockstep
(bit 30 set): placement for a cluster in lockstep is not supported yet.

.. list-table::
   :header-rows: 1

   * - Mode
     - Cortex-R5 (ZynqMP, Versal)
     - Cortex-R52 (Versal NET, Versal Gen 2)
   * - Split
     - ATCM 64 KB at 0x0, BTCM 64 KB at 0x20000
     - ATCM 64 KB at 0x0, BTCM 32 KB at 0x10000, CTCM 32 KB at 0x18000

The assist checks the assembled tree, whatever the input route, and
rejects a placement when:

- ``vector_table`` is not in the ATCM, or has a non-zero offset;
- a TCM bank named by ``sram``, ``linker_memories`` or a section belongs
  to another core, or is one of the ``*_atcm_lockstep`` and
  ``*_btcm_lockstep`` banks;
- a region a section uses overlaps an IPC carveout: a carveout that an
  OpenAMP or libmetal relation lists in ``carveouts``, or a shared OpenAMP
  ``/reserved-memory`` node such as a vring or vdev buffer.

The assist does not check that the sections fit in their regions.  A
placement that does not fit fails when the application is linked, with
the linker's region overflow error.

Example:

.. code-block:: YAML

   domains:
     RPU0_BAREMETAL:
       os,type: baremetal
       cpus:
         - cluster: cpus_r5_0
           cpumask: 0x1
           mode: { secure: true, el: 0x3 }
       sram: [ { start: 0xffe00000, size: 0x10000 },
               { start: 0xffe20000, size: 0x10000 } ]
       linker:
         layout: tcm
         linker_memories: [ psu_r5_0_atcm_global, psu_r5_0_btcm_global ]
         sections:
           vector_table:   { region: psu_r5_0_atcm_global, offset: 0 }
           resource_table: { region: psu_r5_0_btcm_global }

The assist writes these CMake variables, each naming a ``MEMORY`` region of
the generated script:

.. list-table::
   :header-rows: 1

   * - Variable
     - Sections
     - Without a ``linker`` mapping
   * - ``VECTORS``
     - ``.vectors``, ``.bootdata``
     - The ATCM region the templates used before (``psu_r5_0_atcm_MEM_0``,
       ``psv_r5_0_atcm_MEM_0``, ``r52_tcm_alias``, ``psx_r52_tcm_alias``)
   * - ``CODE``
     - ``.text`` and other code
     - DDR, as before
   * - ``RODATA``
     - ``.rodata``, ``.rodata1``, ``.sdata2``, ``.sbss2``
     - ``DATA``
   * - ``DATA``
     - ``.data`` and other data
     - DDR, as before
   * - ``BSS``, ``HEAP``, ``STACK``
     - ``.bss``, ``.heap``, ``.stack``
     - ``DATA``
   * - ``RSC_TABLE_REGION``
     - ``.resource_table``
     - ``DATA``

``RSC_TABLE_REGION`` is separate from ``RSC_TABLE``, which in OpenAMP mode
holds the ELF-load carveout address.  With a ``linker`` mapping, the
``MEMORY`` section also gets the core's TCM regions, and on Cortex-R52 the
assist writes
``TCM_A_REGION``, ``TCM_B_REGION`` and ``TCM_C_REGION``: the words
standalone ``boot.S`` loads from ``_TCM_A_REGION``, ``_TCM_B_REGION`` and
``_TCM_C_REGION`` and writes to the TCM region registers.  A used BTCM or
CTCM gets its core-local base with the enable bit set; the ATCM word and
unused banks are 0, which leaves them as boot firmware configured them.
``TCM_REGIONS`` holds the linker script lines that define these words.

ELF load in TCM only
~~~~~~~~~~~~~~~~~~~~

An OpenAMP ``remoteproc`` relation or a ``libmetal`` relation may list only
TCM banks of the remote core in its ``elfload``.  When the assist runs in
OpenAMP mode and no ``linker`` mapping selects the core, it then places the
sections in the listed banks, with the same checks and core-local
addresses as a ``linker`` mapping:

.. list-table::
   :header-rows: 1

   * - Sections
     - Bank
   * - ``vector_table``, ``text``
     - ATCM
   * - ``rodata``, ``data``, ``bss``, ``resource_table``
     - BTCM, else ATCM
   * - ``heap``, ``stack``
     - CTCM (R52), else BTCM, else ATCM

The ATCM shall be listed.  Of several banks of one type, the first listed
is used, and a cluster in lockstep is rejected.  Read-only data goes
with the data, unlike the ``tcm`` preset, so that the 64 KB ATCM holds only
the vectors and code.

There is then no DDR ELF-load carveout: the assist writes neither
``RSC_TABLE`` nor ``LIBMETAL_DDR``, and no DDR ``reserved-memory`` node is
needed for the firmware.  An ``elfload`` that also lists a DDR carveout
keeps the DDR placement; its first DDR entry is the ELF-load carveout, and
its TCM banks are not used as DDR.  A ``linker`` mapping takes precedence
over the ``elfload`` banks.

Whenever the sections come from a ``linker`` mapping or a TCM-only
``elfload``, the assist also writes ``RPU_SECTION_PLACEMENT`` as ``ON``.

Example, libmetal demo on ZynqMP RPU 1:

.. code-block:: YAML

   R5_1_BAREMETAL:
     os,type: baremetal
     cpus:
       - cluster: cpus_r5_1
         cpumask: 0x1
         mode: { secure: true, el: 0x3 }
     sram: [ { start: 0xffe90000, size: 0x10000 },
             { start: 0xffeb0000, size: 0x10000 } ]
     domain-to-domain:
       compatible: openamp,domain-to-domain-v1
       libmetal-relation:
         compatible: libmetal,ipc-v1
         relation0:
           host: APU_Linux
           carveouts: [ libmetal_uio_desc0@99c8000, libmetal_uio_desc1@99cc000,
                        libmetal_uio_data@99d0000 ]
           mbox: ipi_2_to_ipi_8
           timer: ttc2
           elfload: [ psu_r5_1_atcm_global, psu_r5_1_btcm_global ]


Flags
-----

In YAML the following simplifications are used for access, memory, and
sram flags definitions and usage:

- To define flags  use key: value pairs

- When defining flags values, give individual flags setting a name
  rather than just a number, e.g. use read-only instead of (1<<2). The
  name and corresponding numeric values should be specified in lopper.

- no \*-flags-cells

.. code-block:: YAML

   access:
       - dev: can0
         flags: {requested: true, read-only: true}


Standalone and FreeRTOS MPU Table
---------------------------------

A Cortex-R5 or Cortex-R52 domain with ``os,type: baremetal`` or
``os,type: freertos`` may use the same ``mpu-policy`` property on its
memories.  The ``xlnx_rpu_mpu`` assist then writes ``amd_mpu_config.c``, which
defines the standalone BSP's ``InitialMpu_Config`` table and so replaces the
BSP's weak default.  The application builds this file with its other sources.
The assist runs on the tree assembled from the system device tree and the
domain YAML::

   lopper -f --enhanced --auto -i domain.yaml system-top.dts out.dts
   lopper -f --enhanced -O <app src> out.dts unused.dts -- xlnx_rpu_mpu \
       --domain=/domains/RPU0 [--output=amd_mpu_config.c] \
       [--free-regions=N] [--aie]

When the domain uses ``lopper,activate`` or conditional properties, the assist
reads the overlay tree selected by ``lopper,activate`` or, if absent,
``os,type``.  Thus ``mpu-policy!baremetal`` and ``mpu-policy!freertos`` apply
to their own domains only.

Memories considered
~~~~~~~~~~~~~~~~~~~

The assist considers the memories in the domain's ``sram`` and
``reserved-memory`` and the ``carveouts`` and ``elfload`` memories of the
domain's relations.  If none of them has ``mpu-policy``, nothing is written
and the BSP default table applies unchanged.  Otherwise:

- A memory with ``mpu-policy`` gets one MPU region.  The values and permitted
  combinations are those of `Memory policy`_.
- A memory whose ``mpu-policy`` includes ``static``, and every TCM bank, gets
  no region.  The BSP already maps the TCM.
- An OpenAMP or libmetal carveout without ``mpu-policy`` gets the attributes
  the RPU demos give it at run time: ``NORM_SHARED_NCACHE`` for vrings, the
  vdev buffer and libmetal shared memory, and ``NORM_NSHARED_NCACHE`` for a
  resource-table carveout (a carveout whose name contains ``rsc`` or
  ``resource``).

``mpu-policy`` maps to BSP attributes as follows.  ``cacheable`` selects the
cacheable type of the BSP's DDR region, ``NORM_NSHARED_WB_WA`` on Cortex-R5
and ``NORM_NSHARED_WT_NWA`` on Cortex-R52; without it the memory is
``NORM_NSHARED_NCACHE``.  ``shareable`` selects the ``NORM_SHARED_*``
variant.  A ``writable`` memory is ``PRIV_RW_USER_RW`` and a read-only memory
``PRIV_RO_USER_RO``.  Standalone and FreeRTOS run privileged and the BSP sets
no execute-never bit, so ``userspace`` and ``executable`` do not change the
attributes.  Run-time ``Xil_MemMap()`` calls then use the same attribute
values as the generated regions.

Generated table
~~~~~~~~~~~~~~~

The table starts from the BSP's own default for the SoC: the table that
``Init_MPU()`` computes on ZynqMP and Versal Cortex-R5, with its DDR region
computed from the system device tree's DDR as the BSP does, and the weak
``InitialMpu_Config`` on Versal NET and Versal Gen 2 Cortex-R52.  A domain
region whose attributes equal those of the default region covering it is
dropped.  Contiguous regions with equal attributes are merged.  Then:

- Cortex-R5: each region shall be a power of two in size, at least 32 bytes,
  and aligned to its size.  The regions follow the default ones, so they take
  priority.
- Cortex-R52: each region's base and size shall be multiples of 64 bytes.
  Regions may not overlap, so default regions are split around the domain's
  regions.  The output does not define ``_TCM_A_REGION``, ``_TCM_B_REGION``
  or ``_TCM_C_REGION``.

At most 16 regions exist, and ``--free-regions`` of them (default 5 on
Cortex-R5, 3 on Cortex-R52) shall stay free for run-time mappings.  On
Cortex-R5 libmetal maps every I/O region with a new MPU region; the OpenAMP
and libmetal demos map 5.  On Cortex-R52 libmetal reuses a region that already
covers a mapping with the same attributes, and a mapping inside a larger region
splits it into 3.  ``--aie`` matches a Versal BSP that defines
``XPAR_AIE_NUM_INSTANCES``.

The assist fails, listing every region, when a region cannot be represented
or the table does not fit.  To replace the whole table, omit ``mpu-policy``
and define ``InitialMpu_Config`` in the application.

Example:

.. code-block:: YAML

   reserved-memory:
     rproc0@9800000:
       start: 0x9800000
       size: 0x60000
       mpu-policy: [ readable, writable, executable, cacheable ]
     vdev0vring0@9880000: { start: 0x9880000, size: 0x4000 }
     vdev0vring1@9884000: { start: 0x9884000, size: 0x4000 }
     vdev0buffer@9888000: { start: 0x9888000, size: 0x78000 }

   domains:
     R5_0_FREERTOS:
       os,type: freertos
       reserved-memory: [ rproc0@9800000 ]
       domain-to-domain:
         rpmsg-relation:
           relation0:
             carveouts: [ vdev0vring0@9880000, vdev0vring1@9884000,
                          vdev0buffer@9888000 ]

``rproc0`` opts the domain in and is already cacheable DDR in the default
table, so it gets no region.  The vrings and the buffer form one 512 KB
``NORM_SHARED_NCACHE | PRIV_RW_USER_RW`` region at ``0x9880000``.


Implicit Flags Example
----------------------

The Implicit Flags Properties in the system devicetree specification
can also be defined in YAML. For example:

.. code-block:: YAML

   access-implicit-default:
     secure: true
     allow-secure: true
     requested: true
     coherent: false
     virtualized: true
     qos: 99


Bus Firewalls
-------------

In YAML the following simplifications are used to represent firewallconf
and firewallconf-default:

- no "block-desireable", instead use the priority number directly as
  value of the block key

- no "allow", instead use "never" as value of the block key

- no "firewallconf-default" property, instead use firewallconf with a
  single value and no domain references


Example:

.. code-block:: YAML

   firewallconf:
     - domain: bm1
       block: 10
     - domain: bm2
       block: never
     - block: 5

Full Example
------------

.. code-block:: YAML

   domains:
       xen:
           compatible: openamp,domain-v1

           id: 0xffff
           cpus:
               - cluster: cpus_a72
                 cpumask: 0x3
                 mode:
                     secure: false
                     el: 0x2
           memory:
               - start: 0x500000
                 size: 0x7fb00000

           access:
               - dev: serial0
                 flags: { xen-flag-example1: true }
               - dev: mmc0
                 flags: { xen-flag-example1: true }

           domains:
               linux1:
                   compatible: openamp,domain-v1

                   id: 0x0
                   cpus:
                       - cluster: cpus_a72
                         cpumask: 0x3
                         mode:
                             secure: false
                             el: 0x1
                   memory:
                       - size: 1G
                   access:
                       - dev: mmc0
                   sram:
                       - start: 0xfffc0000
                         size: 0x1000
                         flags: { read-only: true }
                   firewallconf:
                       domain: bm1
                       block: 0x12

               bm1:
                   compatible: openamp,domain-v1

                   id: 0x1
                   cpus:
                       - cluster: cpus_a72
                         cpumask: 0x3
                         mode:
                             secure: false
                             el: 0x1
                   memory:
                       - size: 512M
                   access:
                       - dev: ethernet0
                   firewallconf:
                       domain: linux1
                       block: always

   domains:
       freertos1:
           compatible: openamp,domain-v1

           id: 0x5
           cpus:
               - cluster: cpus_r5
                 cpumask: 0x3
                 mode: {secure: true, el: 1}
           memory:
               - size: 2M
           access:
               - dev: can0

       bm2:
           compatible: openamp,domain-v1

           id: 0x6
           cpus:
               - cluster: microblaze0
                 cpumask: 0x1
                 mode: {}
           memory:
               - size: 1M
           access:
               - dev: serial1
           sram:
               - start: 0xfffc0000
                 size: 0x1000
                 flags: { read-only: true }
