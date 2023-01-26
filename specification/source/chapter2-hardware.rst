.. _hardware-bindings:

Hardware Description
====================

System devicetrees can describe SoCs with multiple CPUs, possibly of
different architectures, and possibly multiple processors in SMP
configurations. System devicetrees additionally describe the address
maps for each CPU in the SoC. This is necessary, for example, because
a single device's registers could be mapped to different addresses in
different CPU memory maps. As another example, a device may only be
accessible by a subset of the CPUs in the SoC.

This description is done using additional devicetree bindings defined
in this section. The new bindings allow defining:

- Multiple top level nodes which describe the CPUs on the SoC
- Buses containing devices that do not automatically map to the parent
  address space (that is, may not be present in all CPU memory maps).
- Interrupt mappings to multiple CPU clusters

See :numref:`hardware-examples` for system devicetrees using these
bindings.

CPU Cluster Binding
-------------------

A CPU cluster is a node which describes one or more CPUs on the SoC
that share an address space and other attributes. Typically, a CPU
cluster node that describes multiple CPUs reflects multiple processors
in an SMP configuration on the SoC.

See :numref:`example-cpu-clusters` for examples.

CPU Cluster Properties
~~~~~~~~~~~~~~~~~~~~~~

CPU clusters should be represented in a system devicetree in top-level
nodes using the following properties.

.. TODO: this is present in system-device-tree.dts and is mentioned in
   chapter4-bus-firewalls.rst, but may not be needed after all.

   ``bus-master-id``         SD    ``<u32>``             ...

.. tabularcolumns:: | p{4cm} p{0.75cm} p{4cm} p{6.5cm} |
.. table:: CPU Cluster Properties

   ========================= ===== ===================== ===============================================
   Property Name             Usage Value Type            Definition
   ========================= ===== ===================== ===============================================
   ``compatible``            R     <string list>         Value shall include "cpus,cluster".
                                                         See [DTSpec]_ §2.3.1.
   ``#address-cells``        R     ``<u32>``             Shall be 1. See [DTSpec]_ §2.3.5.
   ``#size-cells``           R     ``<u32>``             Shall be 0. See [DTSpec]_ §2.3.5.
   ``address-map``           SD    ``<prop encoded       See :numref:`address-map`. Specifies the
                                   array>``              addresses of hardware resources within the CPU
                                                         cluster's memory map.
   ``#ranges-address-cells`` SD    ``<u32>``             See :numref:`ranges-address-cells`. Specifies
                                                         the number of ``<u32>`` cells used to represent
                                                         a physical address within the CPU cluster.
   ``#ranges-size-cells``    SD    ``<u32>``             See :numref:`ranges-size-cells`. Specifies the
                                                         number of ``<u32>`` cells used to represent the
                                                         size of a physical address range within the
                                                         CPU cluster.

   Usage legend: R=Required, O=Optional, OR=Optional but Recommended, SD=See Definition
   =====================================================================================================

.. note:: The following additional standard properties defined in the
          base specification are allowed but optional: ``model``,
          ``phandle``, ``status``.

CPU Node Properties
~~~~~~~~~~~~~~~~~~~

The child nodes of a CPU cluster node describe the individual CPUs
within the cluster. They are represented identically to the
``/cpus/cpu*`` nodes in a standard devicetree. See [DTSpec]_ §3.8 and
§3.9 for details.

.. _address-map:

``address-map`` Property
~~~~~~~~~~~~~~~~~~~~~~~~

.. tabularcolumns:: | l J |
.. table:: ``address-map`` Property

   =========== ==============================================================
   Property    ``address-map``
   =========== ==============================================================
   Value type  ``<prop-encoded-array>`` encoded as an arbitrary number of
               (*node-address*, *ref-node*, *root-node-address*, *length*)
               quartets.

   Description Provides a means of defining a translation between the
               address space of a CPU cluster and the address space of
               the root node (recall that the root node is the parent
               node of the CPU cluster).

               The *address-map* property can be used to create a
               mapping between the address space of a CPU cluster node
               and the address spaces of hardware resources such as
               memory, devices, and buses containing other resources as
               child nodes.

               If a hardware resource in the system devicetree
               is not explicitly mapped into the CPU cluster's
               address space using this property, it should be treated
               as if it is not addressable by the CPUs in the cluster.

               The address ranges defined by multiple quartets within
               a single *address-map* property may overlap.
   Example     See :numref:`example-cpu-clusters`.
   =========== ==============================================================

The format of the value of the *address-map* property is an arbitrary
number of quartets, each of which specifies a mapping between the CPU
cluster's address space and another address space.

Within each quartet:

- *node-address* is a physical address within the CPU cluster's address
  space (the CPU cluster is the node in which the *address-map* property
  appears). This is the starting address within the CPU cluster's memory
  map that the resources described by the quartet appear.

  The number of cells used to represent the address is determined by the
  *#ranges-address-cells* property of the CPU cluster node (see
  :numref:`ranges-address-cells`).

- *ref-node* is a phandle to the node describing the resources whose
  addresses are mapped into the CPU cluster's address space. This
  describes the resources whose addresses are being mapped into the CPU
  cluster, either directly as a memory or device node, or indirectly as
  a bus node containing these.

- *root-node-address* is a physical address within the root node's
  address space. The number of cells used to represent the address is
  determined by the *#address-cells* property of the root node. This is
  the starting address, within the root node's address space, of the
  resources whose addresses are being mapped in.

- *length* is the size of the range in the CPU cluster's address space.
  This is the length of the address range being mapped in.

  The number of cells used to represent the size of the range is
  determined by the *#ranges-size-cells* property of the CPU cluster
  node (see :numref:`ranges-size-cells`).

  Any resources with register block addresses fall in the range starting
  at *root-node-address* and ending *length* bytes later are visible to
  all CPUs within the cluster at the addresses specified by the mapping
  entry. Register blocks which appear after the end of the range are not
  visible. A register block which starts within the range but extends
  past the range's end is truncated to fit within the range in the
  memory map of the CPU cluster node.

.. _ranges-address-cells:

``#ranges-address-cells`` Property
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. tabularcolumns:: | l J |
.. table:: ``#ranges-address-cells`` Property

   =========== ==============================================================
   Property    ``#ranges-address-cells``
   =========== ==============================================================
   Value type  ``<u32>``
   Description The number of cells used to represent an address within
               the memory map of a CPU cluster node (the node in which the
               *#ranges-address-cells* property appears). This should
               be large enough to represent the maximum size of an address
               in the data model of the cluster's CPU nodes.
   Example     CPUs have 64-bit addresses: ``#ranges-address-cells = <2>;``
   =========== ==============================================================

.. _ranges-size-cells:

``#ranges-size-cells`` Property
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. tabularcolumns:: | l J |
.. table:: ``#ranges-size-cells`` Property

   =========== ==============================================================
   Property    ``#ranges-size-cells``
   =========== ==============================================================
   Value type  ``<u32>``
   Description The number of cells used to represent the size of a range of
               addresses in the memory map of a CPU cluster node (the node
               in which the *#ranges-size-cells* property appears), in bytes.

               This must be large enough to specify all address ranges
               within the CPU cluster node's *address-map* property.
   Example     32-bit address range sizes: ``#ranges-size-cells = <1>;``
   =========== ==============================================================

.. _example-cpu-clusters:

Example CPU Clusters
--------------------

.. _single-core-arm-cortex-m3:

Single-core Arm Cortex-M3
~~~~~~~~~~~~~~~~~~~~~~~~~

Here is a simplified example of a single CPU cluster with one CPU.

The root node has *#address-cells* set to 1.

.. code-block:: dts

   cpu-cluster-arm {
           #address-cells = <0x1>;
           #size-cells = <0x0>;
           compatible = "cpus,cluster";

           #ranges-address-cells = <0x1>;
           #ranges-size-cells = <0x1>;

           address-map = <0x0 &code 0x0 0x40000>,
                         <0x20000000 &sram 0x0 0x10000>,
                         <0x40000000 &peripherals 0x1000 0x4000>;

           cpu@0 {
                   compatible = "arm,cortex-m3";
                   device_type = "cpu";
                   reg = <0x0>;
           };
   };

The CPU's address map contains:

- a 256 KB code range, starting at address 0x0
- a 128 KB SRAM range, starting at address 0x20000000
- a 16 KB peripheral range, starting at address 0x40000000

The phandles to ``code``, ``sram``, and ``peripherals`` refer to other
nodes in the devicetree which contain the resources of interest. Their
contents are not shown in this example.

Dual-core Arm Cortex-R5
~~~~~~~~~~~~~~~~~~~~~~~

Here is an example CPU cluster node with two CPU child nodes. This
represents two Arm Cortex-R5 cores with shared memory and
device access.

The root node has *#address-cells* set to 1.

.. code-block:: dts

   cpus-cluster-r5 {
           #address-cells = <0x1>;
           #size-cells = <0x0>;
           compatible = "cpus,cluster";

           #ranges-address-cells = <0x1>;
           #ranges-size-cells = <0x1>;

           address-map = <0xf1000000 &amba 0xf1000000 0xeb00000>,
                         <0x0 &memory 0x0 0x80000000>;

           cpu@0 {
                   compatible = "arm,cortex-r5";
                   device_type = "cpu";
                   reg = <0x0>;
           };

           cpu@1 {
                   compatible = "arm,cortex-r5";
                   device_type = "cpu";
                   reg = <0x1>;
           };
   };

Each of the two CPU's address maps contains:

- a 235 MiB range containing resources within an ``amba`` bus node
- a 2 GiB memory range, starting at address 0x0

The addressable resources for each CPU are identical.

Again, the phandles to ``amba`` and ``memory`` refer to nodes elsewhere
in the devicetree that are not shown in this example.

.. _indirect-bus:

Indirect Bus Binding
--------------------

An *indirect bus* is a node in the system devicetree which acts as a
resource container. This is similar to the "simple-bus" compatible value
defined in [DTSpec]_ §4.5.

However, unlike "simple-bus" nodes, the resources inside an indirect bus
do *not* map into the parent node's address space. The devices on the
bus can only be accessed directly by CPUs within CPU clusters whose
*address-map* properties explicitly include the devices.

Indirect Bus Properties
~~~~~~~~~~~~~~~~~~~~~~~

CPU clusters should be represented in a system devicetree in top-level
nodes using the following properties.

.. tabularcolumns:: | p{4cm} p{0.75cm} p{4cm} p{6.5cm} |
.. table:: CPU Cluster Properties

   ========================= ===== ===================== ===============================================
   Property Name             Usage Value Type            Definition
   ========================= ===== ===================== ===============================================
   ``compatible``            R     <string list>         Value shall include "indirect-bus".
   ``#address-cells``        R     ``<u32>``             See [DTSpec]_ §2.3.5.
   ``#size-cells``           R     ``<u32>``             See [DTSpec]_ §2.3.5.

   Usage legend: R=Required, O=Optional, OR=Optional but Recommended, SD=See Definition
   =====================================================================================================

.. note:: Additional standard properties defined in the base
          specification §2.3 are allowed but optional.

.. _default-cpu-cluster:

The Default Cluster, ``/cpus``
------------------------------

Within a system devicetree, the ``/cpus`` node is the default CPU
cluster. As in a standard devicetree, this node can access the resources
contained in any "simple-bus" node directly. However, this node does not
have direct access to any resources defined within any "indirect-bus"
nodes by default. Within a system devicetree, the default cluster can
contain an *address-map* property if resources from indirect bus nodes
are visible to the corresponding CPUs.

.. _hardware-examples:

Example System Devicetree Hardware Descriptions
-----------------------------------------------

Simple example
~~~~~~~~~~~~~~

Here is a simplified example involving a single-core CPU cluster with
three resource nodes. This is based on
:numref:`single-core-arm-cortex-m3`, but was extended to show the
resource nodes.

.. code-block:: dts

   cpu-cluster-arm {
           #address-cells = <0x1>;
           #size-cells = <0x0>;
           compatible = "cpus,cluster";

           #ranges-size-cells = <0x1>;
           #ranges-address-cells = <0x1>;

           address-map = <0x0 &code 0x0 0x40000>,
                         <0x20000000 &sram 0x0 0x10000>,
                         <0x40000000 &peripherals 0x1000 0x4000>;

           cpu@0 {
                   compatible = "arm,cortex-m3";
                   device_type = "cpu";
                   reg = <0x0>;
           };
   };

   code: code-bus {
           compatible = "indirect-bus";
           #address-cells = <1>;
           #size-cells = <1>;

           flash@0 {
                   compatible = "...";
                   reg = <0x0 0x40000>;
           };
   };

   sram: sram-bus {
           compatible = "indirect-bus";
           #address-cells = <1>;
           #size-cells = <1>;

           sram@0 {
                   compatible = "mmio-sram";
                   reg = <0x0 0x10000>;
           };

           sram@10000 {
                   compatible = "mmio-sram";
                   reg = <0x10000 0x10000>;
           };
   };

   peripherals: peripheral-bus {
           compatible = "indirect-bus";
           #address-cells = <1>;
           #size-cells = <1>;

           serial@0 {
                   compatible = "...";
                   reg = <0x0 0x1000>;
           };

           serial@2000 {
                   compatible = "...";
                   reg = <0x2000 0x1000>;
           };
   };

In this example:

- the on-chip NOR flash device ``flash@0`` is visible starting at
  address 0x0 in the CPU cluster's address space

- the SRAM ``sram@0`` is visible starting at 0x20000000

- the SRAM ``sram@10000`` is not visible to the CPU cluster,
  because its *address-map* property constrains the ``sram``
  address range to 0x10000 bytes in size

- the serial ports ``serial@0`` and ``serial@2000`` are visible
  starting at 0x40001000 and 0x40003000, respectively

More complex example
~~~~~~~~~~~~~~~~~~~~

Here is another example. Some properties have been omitted for brevity.

.. code-block:: dts

   /* default cluster */
   cpus {
           #address-cells = <1>;
           #size-cells = <0>;

           cpu@0 {
                   reg = <0>;
           };
           cpu@1 {
                   reg = <1>;
           };
   };

   /* additional R5 cluster */
   cpus_r5: cpus-cluster-r5 {
           compatible = "cpus,cluster";
           #address-cells = <1>;
           #size-cells = <0>;

           /* specifies address mappings */
           address-map = <0xf9000000 &amba_rpu 0xf9000000 0x10000>;

           cpu@0 {
                   reg = <0>;
           };

           cpu@1 {
                   reg = <1>;
           };
   };

   amba_rpu: rpu-bus {
           compatible = "indirect-bus";
   };

In this example, there are:

- two CPU cluster nodes; one of them is the default cluster, ``/cpus``,
  and the other is ``cpus_r5``
- an indirect bus, ``amba_rpu`` which is not visible to the default cluster
- the ``cpus_r5`` cluster can see the ``amba_rpu`` bus, because it is
  explicitly mapped using the *address-map* property

As discussed above, devices only physically accessible from one of the
two clusters should be placed under an "indirect-bus" appropriately.

For instance, we can extend the above to show how the interrupt tree and
interrupt mapping can be described for multiple CPU clusters using the
definitions in [DTSpec]_ §2.4:

.. code-block:: dts

   /* default cluster */
   cpus {
   };

   /* additional R5 cluster */
   cpus_r5: cpus-cluster-r5 {
           compatible = "cpus,cluster";

           /* specifies address mappings */
           address-map = <0xf9000000 &amba_rpu 0xf9000000 0x10000>;
   };

   /* bus only accessible by cpus */
   amba_apu: apu-bus {
           compatible = "simple-bus";

           gic_a72: interrupt-controller@f9000000 {
           };
   };

   /* bus only accessible by cpus_r5 */
   amba_rpu: rpu-bus {
           compatible = "indirect-bus";

           gic_r5: interrupt-controller@f9000000 {
           };
   };

Note that:

- ``gic_a72`` is visible to ``/cpus``, but not to ``cpus_r5``, because
  ``amba_apu`` is not present in the *address-map* property of ``cpus_r5``.

- ``gic_r5`` is visible to ``cpus_r5``, because it is present in the
  *address-map* property of ``cpus_r5``

- ``gic_r5`` is not visible to ``/cpus`` because indirect bus nodes do
  not automatically map to the parent address space, and ``/cpus``
  doesn't have an *address-map* property

Relying on the fact that each interrupt controller is visible to its CPU
cluster node, it is possible to express interrupt routing from a device
to multiple clusters. For instance:

.. code-block:: dts

   amba: axi-bus {
           compatible = "simple-bus";
           #address-cells = <2>;
           #size-cells = <2>;
           ranges;

           #interrupt-cells = <3>;
           interrupt-map-pass-thru = <0xffffffff 0xffffffff 0xffffffff>;
           interrupt-map-mask = <0x0 0x0 0x0>;
           interrupt-map = <0x0 0x0 0x0 &gic_a72 0x0 0x0 0x0>,
                           <0x0 0x0 0x0 &gic_r5 0x0 0x0 0x0>;

           can0: can@ff060000 {
                   compatible = "xlnx,canfd-2.0";
                   reg = <0x0 0xff060000 0x0 0x6000>;
                   interrupts = <0x0 0x14 0x1>;
           };
   };

In this example, all devices under ``amba``, including ``can@ff060000``,
have their interrupts routed to both ``gic_r5`` and ``gic_a72``.
