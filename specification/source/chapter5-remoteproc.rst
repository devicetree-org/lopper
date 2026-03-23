OpenAMP RemoteProc (Under Discussion)
=====================================

.. include:: under-discussion.txt

[OpenAMP]_ RemoteProc is a framework for remote processor lifecycle
management and inter-processor communication between Linux and other
operating systems.

[Lopper]_ can translate System Device Tree domain descriptions into the
Linux remoteproc representation used by AMD Xilinx platforms.


Overview
--------

The current Xilinx remoteproc binding is
``xlnx,zynqmp-r5fss.yaml``. It covers three subsystem compatibles:

- ``xlnx,zynqmp-r5fss``
- ``xlnx,versal-r5fss``
- ``xlnx,versal-net-r52fss``

The generated Linux node is a remoteproc subsystem node with one or
more processor children:

- the subsystem node carries ``compatible``, ``#address-cells``,
  ``#size-cells``, ``ranges``, ``xlnx,cluster-mode``, and, for ZynqMP
  and Versal R5F, ``xlnx,tcm-mode``
- each processor child carries ``compatible``, ``reg``,
  ``reg-names``, ``power-domains``, and optionally ``memory-region``,
  ``mboxes``, ``mbox-names``, and ``sram``

In the current domain YAML flow, Linux remoteproc generation is driven
by a ``domain-to-domain`` relation compatible with
``openamp,remoteproc-v2``. A matching ``openamp,rpmsg-v1`` relation can
then extend the same processor child with RPMsg carveouts and mailbox
properties.


System Device Tree Representation
---------------------------------

The System Device Tree input does not need a dedicated ``remoteproc``
node. Instead, the required information is distributed across:

- top-level ``reserved-memory`` nodes for DDR carveouts
- the remote domain ``cpus`` entry for the target RPU core
- the remote domain ``sram`` list for TCM, local SRAM, or DDR boot
  staging regions used during image loading
- ``domain-to-domain`` relations for ``remoteproc-v2`` and ``rpmsg-v1``

The current shipped domain YAML overlays in
``meta-xilinx-standalone-sdt/conf/domainyaml`` follow this model.


System Device Tree Example
~~~~~~~~~~~~~~~~~~~~~~~~~~

The following YAML matches the current ZynqMP overlay style and contains
enough information for lopper to generate a Linux remoteproc node
conforming to the current binding.

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

     vdev0vring1@9864000:
       start: 0x9864000
       size: 0x4000
       no-map: 1

     vdev0buffer@9868000:
       start: 0x9868000
       size: 0x100000
       no-map: 1

   domains:
     APU_Linux:
       compatible: openamp,domain-v1
       cpus:
         - cluster: cpus_a53
           cpumask: 0xf
           mode:
             secure: true
             el: 0x3
       os,type: linux
       reserved-memory:
         - vdev0buffer@9868000
         - vdev0vring1@9864000
         - vdev0vring0@9860000
         - rproc0@9800000

       domain-to-domain:
         compatible: openamp,domain-to-domain-v1

         remoteproc-relation:
           compatible: openamp,remoteproc-v2
           relation0:
             remote: R5_0_FREERTOS
             elfload:
               - psu_r5_0_atcm_global@ffe00000
               - psu_r5_0_btcm_global@ffe20000
               - rproc0@9800000

         rpmsg-relation:
           compatible: openamp,rpmsg-v1
           relation0:
             remote: R5_0_FREERTOS
             mbox: ipi_7_to_ipi_1
             carveouts:
               - vdev0vring0@9860000
               - vdev0vring1@9864000
               - vdev0buffer@9868000
             timer: [ ttc0, ttc1 ]

     R5_0_FREERTOS:
       compatible: openamp,domain-v1
       cpus:
         - cluster: cpus_r5_0
           cluster_cpu: psu_cortexr5_0
           cpumask: 0x1
           mode:
             secure: true
             el: 0x3
       os,type: freertos
       sram:
         - dev: rproc0@9800000
           start: 0x9800000
           size: 0x60000
         - dev: psu_r5_0_atcm_global@ffe00000
           start: 0xffe00000
           size: 64K
         - dev: psu_r5_0_btcm_global@ffe20000
           start: 0xffe20000
           size: 64K
       reserved-memory:
         - vdev0buffer@9868000
         - vdev0vring1@9864000
         - vdev0vring0@9860000
         - rproc0@9800000


Conversion Rules
~~~~~~~~~~~~~~~~

Lopper derives the Linux remoteproc node as follows:

- The platform selects the subsystem compatible:
  ``xlnx,zynqmp-r5fss``, ``xlnx,versal-r5fss``, or
  ``xlnx,versal-net-r52fss``.
- The remote domain ``cpus`` entry selects the target processor child
  and the ``xlnx,cluster-mode`` value.
- On ZynqMP and Versal R5F, ``xlnx,tcm-mode`` is emitted and follows the
  same split or lockstep setting as ``xlnx,cluster-mode``.
- ``elfload`` entries that refer to TCM memories become the processor
  child's ``reg`` and ``reg-names`` entries.
- ``elfload`` entries that refer to DDR carveouts become the first
  ``memory-region`` entries.
- ``carveouts`` from the matching ``openamp,rpmsg-v1`` relation are
  appended to ``memory-region`` in Linux order:
  firmware image, ``vdev0buffer``, ``vring0``, ``vring1``.
- The RPMsg mailbox endpoint becomes ``mboxes`` and ``mbox-names =
  "tx", "rx"`` on the processor child.
- ``power-domains`` are derived from the target core and the referenced
  TCM memories.

The binding-specific memory layout differs by platform:

- ZynqMP and Versal R5F use ATCM and BTCM banks. In split mode each
  child typically exposes ``atcm0`` and ``btcm0``. In lockstep mode the
  binding expects the combined ATCM and BTCM banks and
  ``xlnx,tcm-mode = <1>``.
- Versal Net and Versal2 R52F use ATCM, BTCM, and CTCM banks. These
  platforms do not use ``xlnx,tcm-mode`` in the binding.


Generated Linux Device Tree Example
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The following Linux DTS fragment is representative of the output that
lopper generates for the previous YAML example. Provider nodes are
omitted for brevity.

.. code-block:: DTS

   reserved-memory {
       #address-cells = <2>;
       #size-cells = <2>;
       ranges;

       rproc0_reserved: rproc0@9800000 {
           no-map;
           reg = <0x0 0x09800000 0x0 0x00060000>;
       };

       vdev0vring0: vdev0vring0@9860000 {
           no-map;
           reg = <0x0 0x09860000 0x0 0x00004000>;
       };

       vdev0vring1: vdev0vring1@9864000 {
           no-map;
           reg = <0x0 0x09864000 0x0 0x00004000>;
       };

       vdev0buffer: vdev0buffer@9868000 {
           compatible = "shared-dma-pool";
           no-map;
           reg = <0x0 0x09868000 0x0 0x00100000>;
       };
   };

   remoteproc@ffe00000 {
       compatible = "xlnx,zynqmp-r5fss";
       #address-cells = <2>;
       #size-cells = <2>;
       ranges = <0x0 0x0 0x0 0xffe00000 0x0 0x10000>,
                <0x0 0x20000 0x0 0xffe20000 0x0 0x10000>;
       xlnx,cluster-mode = <0>;
       xlnx,tcm-mode = <0>;

       r5f@0 {
           compatible = "xlnx,zynqmp-r5f";
           reg = <0x0 0x0 0x0 0x10000>,
                 <0x0 0x20000 0x0 0x10000>;
           reg-names = "atcm0", "btcm0";
           power-domains = <&zynqmp_firmware 0x7>,
                           <&zynqmp_firmware 0xf>,
                           <&zynqmp_firmware 0x10>;
           memory-region = <&rproc0_reserved>,
                           <&vdev0buffer>,
                           <&vdev0vring0>,
                           <&vdev0vring1>;
           mboxes = <&ipi_mailbox_rpu0 0>, <&ipi_mailbox_rpu0 1>;
           mbox-names = "tx", "rx";
       };
   };


Notes
~~~~~

- The old ``xlnx,zynqmp-r5-remoteproc`` and ``xilinx,r5f`` examples are
  stale and should not be used as the reference form.
- ``memory-region`` is for DDR reserved-memory carveouts. TCM banks are
  described by the processor child's ``reg`` and ``reg-names`` entries.
- The optional ``sram`` property in the binding remains available for
  additional on-chip SRAM regions outside the core-local TCM layout.
