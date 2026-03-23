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

