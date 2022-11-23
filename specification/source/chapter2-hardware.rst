System Device Tree Hardware Description
=======================================

To turn system device tree into a reality we are introducing a few new
concepts. They enable us to describe a system with multiple cpus
clusters and potentially different address mappings for each of them
(i.e. a device could be seen at different addresses from different cpus
clusters).

The new concepts are:

- Multiple top level "cpus,cluster" nodes to describe heterogeneous CPU
  clusters.
- "indirect-bus": a new type of bus that does not automatically map to
  the parent address space (i.e. not automatically visible).
- An address-map property to express the different address mappings of
  the different cpus clusters and to map indirect-buses.

The following is a brief example to show how they can be used together:

.. code-block:: dts

   /* default cluster */
   cpus {
           cpu@0 {
           };
           cpu@1 {
           };
   };

   /* additional R5 cluster */
   cpus_r5: cpus-cluster@0 {
           compatible = "cpus,cluster";

           /* specifies address mappings */
           address-map = <0xf9000000 &amba_rpu 0xf9000000 0x10000>;

           cpu@0 {
           };

           cpu@1 {
           };
   };

   amba_rpu: rpu-bus@f9000000 {
           compatible = "indirect-bus";
   };

In this example we can see:

- two cpus clusters, one of them is the default top-level cpus node
- an indirect-bus amba_rpu which is not visible to the top-level cpus
  node
- the cpus_r5 cluster can see amba_rpu because it is explicitly mapped
  using the address-map property

Devices only physically accessible from one of the two clusters should
be placed under an "indirect-bus" as appropriate. For instance, in the
following example we can see how interrupts controllers are expressed:

.. code-block:: dts

   /* default cluster */
   cpus {
   };

   /* additional R5 cluster */
   cpus_r5: cpus-cluster@0 {
           compatible = "cpus,cluster";

           /* specifies address mappings */
           address-map = <0xf9000000 &amba_rpu 0xf9000000 0x10000>;
   };

   /* bus only accessible by cpus */
   amba_apu: apu-bus@f9000000 {
           compatible = "simple-bus";

           gic_a72: interrupt-controller@f9000000 {
           };
   };

   /* bus only accessible by cpus_r5 */
   amba_rpu: rpu-bus@f9000000 {
           compatible = "indirect-bus";

           gic_r5: interrupt-controller@f9000000 {
           };
   };


gic_a72 is accessible by /cpus, but not by cpus_r5, because
amba_apu is not present in the address-map of cpus_r5.

gic_r5 is visible to cpus_r5, because it is present in the address map
of cpus_r5. gic_r5 is not visible to /cpus because
indirect-bus doesn't automatically map to the parent address space,
and /cpus doesn't have an address-map property in the example.

Relying on the fact that each interrupt controller is correctly visible
to the right cpus cluster, it is possible to express interrupt routing
from a device to multiple clusters. For instance:

.. code-block:: dts

   amba: axi@f1000000 {
           compatible = "simple-bus";
           ranges;

           #interrupt-cells = <3>;
           interrupt-map-pass-thru = <0xffffffff 0xffffffff 0xffffffff>;
           interrupt-map-mask = <0x0 0x0 0x0>;
           interrupt-map = <0x0 0x0 0x0 &gic_a72 0x0 0x0 0x0>,
                           <0x0 0x0 0x0 &gic_r5 0x0 0x0 0x0>;

           can@ff060000 {
                   compatible = "xlnx,canfd-2.0";
                   reg = <0x0 0xff060000 0x0 0x6000>;
                   interrupts = <0x0 0x14 0x1>;
           };
   };

In this example, all devices under amba, including can\@ff060000, have
their interrupts routed to both gic_r5 and gic_a72.

Memory only physically accessible by one of the clusters can be placed
under an indirect-bus like any other device types. However, normal
memory is usually physically accessible by all clusters. It is just a
software configuration that splits memory into ranges and assigns a
range for each execution domain. Software configurations are explained
below.
