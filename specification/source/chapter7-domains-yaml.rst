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
