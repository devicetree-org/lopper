.. _execution-domains:

Execution Domains
=================

An execution domain is a node which describes a software or firmware
image running on a CPU cluster, along with a collection of configuration
values that enable an operating system or an application to run on CPU
cores in the cluster.

For example, execution domains can be defined for:

- software or firmware images running at different execution levels on
  an Arm v8-A architecture CPU

- firmware images running in the secure and non-secure CPU states on
  an Arm v8-M architecture CPU with TrustZone

- other trusted and untrusted environments

- software or firmware running on individual CPUs that are not part of
  SMP clusters

- a kernel or RTOS application running on a subset of CPUs within an SMP
  cluster

Each CPU cluster node can have one or more associated execution domains.
Multiple execution domains associated with the same cluster can result,
for example:

- from virtualization or non-lockstep execution on CPU clusters that
  support it

- from a multi-stage boot on a microcontroller, where an execution
  domain for a bootloader permanently yields control of a CPU to an
  execution domain running a later stage firmware image

- when partitioning the CPUs in an SMP cluster into subsets, each of
  which has its own distinct software or firwmare

Example
-------

Execution domains are expressed by a new binding for the
"openamp,domain-v1" value for the *compatible* property. Domains are
placed under a new top-level node within a system devicetree:
``/domains``. Here is an example:

.. code-block:: dts

   domains {
           openamp_r5 {
                   compatible = "openamp,domain-v1";
                   cpus = <&cpus_r5 0x2 0x80000000>;

                   #memory-flags-cells = <0>;
                   memory = <0x0 0x0 0x0 0x8000000>;

                   #access-flags-cells = <1>;
                   access = <&can0 0x3 &ethernet0 0x7>;

                   id = <0x1>;
           };
   };

As shown above, ``openamp_r5`` contains information about:

- the physical CPUs the software is running on, via *cpus*
- memories assigned to the domain, via *memory*
- devices that should only be accessible by the domain, via *access*

Domains can be nested recursively within other nodes under ``/domains``.

Execution Domain Binding, v1
----------------------------

.. tabularcolumns:: | p{6cm} p{0.75cm} p{3cm} p{6cm} |
.. table:: Execution Domain v1 Properties

   =================================== ===== ===================== ===============================================
   Property Name                       Usage Value Type            Definition
   =================================== ===== ===================== ===============================================
   ``compatible``                      R     <string list>         Value shall include "openamp,domain-v1".
                                                                   See [DTSpec]_ §2.3.1.
   ``cpus``                            SD    ``<prop encoded       See :numref:`domains-cpus`. Specifies the
                                             array>``              CPU cluster on which the domain software runs.
   ``#access-flags-cells``             O     ``<u32>``             Specifies the number of ``<u32>`` cells used
                                                                   to represent the access flags for each
                                                                   device in the *access* property. If absent,
                                                                   the default value is zero.
   ``access``                          SD    ``<prop encoded       See :numref:`domains-access`. Specifies
                                             array>``              devices configured to only be accessible
                                                                   by this domain (the node in which the
                                                                   *access* property appears).
   ``#memory-flags-cells``             O     ``<u32>``             Specifies the number of ``<u32>`` cells used
                                                                   to represent the flags for each memory
                                                                   range in the *memory* property. If absent,
                                                                   the default value is zero.
   ``memory``                          SD    ``<prop encoded       See :numref:`domains-memory`. Specifies
                                             array>``              the memory assigned to the domain.
   ``#sram-flags-cells``               O     ``<u32>``             Specifies the number of ``<u32>`` cells used
                                                                   to represent the flags for each SRAM
                                                                   range in the *sram* property. If absent,
                                                                   the default value is zero.
   ``sram``                            SD    ``<prop encoded       See :numref:`domains-sram`. Specifies
                                             array>``              the MMIO SRAM assigned to the domain.
   ``id``                              R     ``<u32>``             A 32-bit integer that uniquely
                                                                   identifies the domain
   ``os,type``                         SD    ``<string>``          See :numref:`domains-os-type`
   ``#access-implicit-default-cells``  SD    ``<u32>``             See :numref:`domains-implicit-flags`
   ``access-implicit-default``         SD    array                 See :numref:`domains-implicit-flags`
   ``#memory-implicit-default-cells``  SD    ``<u32>``             See :numref:`domains-implicit-flags`
   ``memory-implicit-default``         SD    array                 See :numref:`domains-implicit-flags`
   ``#sram-implicit-default-cells``    SD    ``<u32>``             See :numref:`domains-implicit-flags`
   ``sram-implicit-default``           SD    array                 See :numref:`domains-implicit-flags`

   Usage legend: R=Required, O=Optional, OR=Optional but Recommended, SD=See Definition
   ===============================================================================================================

.. note:: The system devicetree bindings which define execution domains
          are separate from the bindings used for hardware description
          (see :numref:`hardware-bindings`) for two main reasons:

          - A different persona will add and edit the information
          - configuration should be separated from hardware description,
            since it has a different rate of change

.. _domains-cpus:

cpus Property
~~~~~~~~~~~~~

.. tabularcolumns:: | l J |
.. table:: ``cpus`` Property

   =========== ==============================================================
   Property    ``cpus``
   =========== ==============================================================
   Value type  ``<prop-encoded-array>`` encoded as a
               (*cpu-cluster*, *cpu-mask*, *execution-level*) triplet.

   Description Required; defines the physical CPUs this domain (the domain
               in which the *cpus* property appears) runs on.
   Example     ``cpus = <&cluster 0xF 0x80000000>;``
   =========== ==============================================================

Within the triplet:

- *cpu-cluster* is a phandle to a CPU cluster node
- *cpu-mask* is a bitfield indicating the subset of CPUs in the cluster which
  the domain runs on
- *execution-level* is a cluster-specific execution level for the domain

The execution level is the most privileged level that the domain can
make use of. The permissible values for the *execution-level* cell in a
*cpus* property depend on the CPU cluster hardware. The following
permissible values are provided for some CPU architectures. To add other
CPU architectures, this specification should be amended.

For Arm Cortex-R5 CPUs, *execution-level* is a bit map
where:

- bit 31: secure (1) / non-secure (0)
- bit 30: lockstep (1) / split (0)
- bits 1 through 29: reserved, must be zero

For Arm Cortex-A53 and -A72 CPUs, *execution-level* is
a bit map where:

- bit 31: secure (1) / non-secure (0)
- bits 2 through 30: reserved, must be zero
- bits 0-1: EL0 (0x0), EL1 (0x1), or EL2 (0x2)

.. _domains-access:

access Property
~~~~~~~~~~~~~~~

.. FIXME: specify content of flags:
   https://github.com/devicetree-org/lopper/issues/137

.. tabularcolumns:: | l J |
.. table:: ``access`` Property

   =========== ==============================================================
   Property    ``access``
   =========== ==============================================================
   Value type  Optional ``<prop-encoded-array>`` encoded as an arbitrary
               number of (*device*, *flags*) pairs.

   Description A list of devices the domain shall have exclusive access to,
               using bus firewalls or other similar technologies.
   Example     ``access = <&mmc0>;``
   =========== ==============================================================

Within each pair:

- *device* is a phandle to the device node
- *flags* contains domain-specific flags. The number of cells in each flag is
  defined by the *#access-flags-cells* property of this domain (the domain in
  which the *access* property appears).

.. _domains-memory:

memory Property
~~~~~~~~~~~~~~~

.. FIXME: start and size #cells are unclear:
   https://github.com/devicetree-org/lopper/issues/138

.. FIXME: specify content of flags:
   https://github.com/devicetree-org/lopper/issues/137

.. tabularcolumns:: | l J |
.. table:: ``memory`` Property

   =========== ==============================================================
   Property    ``memory``
   =========== ==============================================================
   Value type  Optional ``<prop-encoded-array>`` encoded as an arbitrary
               number of (*start*, *size*, *flags*) triplets.

   Description An array of memory ranges assigned to the execution domain
               (the node in which the *memory* property appears). This must
               be a subset of the physical memory present in the system.
   Example     ``memory = <0x0 0x0 0x0 0x8000000 0x8 0x0 0x0 0x10000 0x0>;``
   =========== ==============================================================

Within each triplet:

- *start* is the physical address of the start of the memory range. The
  number of cells used to represent the start address is determined by
  the *#address-cells* property.
- *size* is the size of the memory range, in bytes. The number of cells
  used to represent the size is determined by the *#size-cells*
  property.
- *flags* contains domain-specific flags. The number of cells in each flag is
  defined by the *#memory-flags-cells* property of the execution domain.

.. FIXME this example could use more context

Note that the *memory* property can also be used to express memory
sharing between domains. For example:

.. code-block:: dts

   domains {
           openamp_r5 {
                   compatible = "openamp,domain-v1";
                   memory = <0x0 0x0 0x0 0x8000000 0x8 0x0 0x0 0x10000 0x0>;
                   id = <0x2>;
           };
           openamp_a72 {
                   compatible = "openamp,domain-v1";
                   memory = <0x0 0x8000000 0x0 0x80000000 0x8 0x0 0x0 0x10000 0x0>;
                   id = <0x3>;
           };
   };

In this example, a 16 pages range starting at 0x800000000 is shared
between two domains.

.. _domains-sram:

sram Property
~~~~~~~~~~~~~

.. FIXME: start and size #cells are unclear:
   https://github.com/devicetree-org/lopper/issues/138

.. FIXME: specify content of flags:
   https://github.com/devicetree-org/lopper/issues/137

.. tabularcolumns:: | l J |
.. table:: ``sram`` Property

   =========== ==============================================================
   Property    ``sram``
   =========== ==============================================================
   Value type  Optional ``<prop-encoded-array>`` encoded as an arbitrary
               number of (*start*, *size*, *flags*) triplets.

   Description An array of sram ranges assigned to the execution domain
               (the node in which the *sram* property appears). This must
               be a subset of the physical SRAM memory present in the system.

   Example     ``sram = <0x0 0x0 0x0 0x8000000 0x8 0x0 0x0 0x10000 0x0>;``
   =========== ==============================================================

Within each triplet:

- *start* is the physical address of the start of the memory range. The
  number of cells used to represent the start address is determined by
  the *#address-cells* property.
- *size* is the size of the memory range, in bytes. The number of cells
  used to represent the size is determined by the *#size-cells*
  property.
- *flags* contains domain-specific flags. The number of cells in each flag is
  defined by the *#sram-flags-cells* property of the execution domain.

.. _domains-os-type:

os,type Property
~~~~~~~~~~~~~~~~

Execution domains can have an optional "os,type" property, which
describes one or more operating systems that may run on the domain.

The field may be used by automated tooling for activities such as
verifying that the domain is capable of running the operating system,
configuring a build system to produce the proper operating system,
configuring a storage mechanism to include the specified operating
system, or other purposes.

The value of *os,type* is a string defined in the format:

.. code-block:: none

	OS_TYPE[,TYPE_ID[,TYPE_ID_VERSION]]

``OS_TYPE`` is mandatory. It defines the operating system's type. Its
value must match one of the following:

.. code-block:: none

	OS_TYPE:
	   baremetal
	   linux
	   freertos
	   zephyr
	   custom
	   x-<vendor>[-os]

This specification should be updated if additional types are required.

- ``baremetal`` refers to a direct application that executes on the system
  with no conventional operating system. Examples of this may include a
  first stage boot loader, a second stage boot loader, U-Boot [U-Boot]_,
  Trusted Firmware-A [TF-A]_, etc.

- ``linux`` refers to a Linux based operating system. Examples of this may
  include Yocto Project [Yocto]_ derived distributions, Red Hat
  Enterprise Linux [RHEL]_, Ubuntu [Ubuntu]_ distributions, etc.

- ``freertos`` refers to the FreeRTOS [FreeRTOS]_ real-time operating system

- ``zephyr`` refers to the Zephyr [Zephyr]_ real-time operating system

- ``custom`` refers to a user specific operating system. Custom must
  only be used by the group providing the operating system
  implementation. Each usage of ``custom`` will be different.

- ``*x-<vendor>[-os]`` refers to an extension of a non-registered vendor
  specific operating system. The 'x' refers to extension, which is
  attempts to avoid namespace collisions by convention. The mandatory
  ``<vendor>`` component identifies the operating system vendor, for
  example ``x-xilinx``. However, the vendor name may not be a specific
  enough namespace to avoid collision, so an optional ``-os`` is allowed
  as well. The ``<vendor>`` controls the namespace of ``-os`` values, if
  they are used. For instance, Wind River VxWorks could be specified
  using ``x-windriver-vxworks``.

  It is recommended that a vendor register their operating system in the
  official named list, only using this extension format until it is
  official.

``TYPE_ID`` is specific to each ``OS_TYPE``, but is not currently
formalized. The purpose of this is to further clarify details on the
``OS_TYPE`` if desired. For instance, to specify Ubuntu Linux, use:
"linux,ubuntu".

As ``TYPE_ID`` is not yet formalized, it is open for different usages by
different parties. It is recommended that groups work together to define
common values where appropriate.

``TYPE_ID_VERSION`` is optional parameter which may appear after a
``TYPE_ID`` value. Its purpose is to specify the version of the
operating system identified by ``TYPE_ID``. Extending the prior example
of "linux,ubuntu", version 18.04 of that operating system may be
specified using "linux,ubuntu,18.04".

As with ``TYPE_ID``, this may be open to namespace collisions, and it is
again recommended that groups work together to define common values
where appropriate.

Here are some example *os,type* values:

.. code-block:: none

	os,type = "linux"

	os,type = "linux,ubuntu,18.04"

	os,type = "linux,ubuntu,18.04.01"

	os,type = "linux,yocto"

	os,type = "linux,yocto,gatesgarth"

	os,type = "baremetal"

	os,type = "baremetal,fsbl"

	os,type = "baremetal,newlib,3.3.0"

.. _domains-implicit-flags:

Implicit Flags Properties
~~~~~~~~~~~~~~~~~~~~~~~~~

It is possible to specify default flags values at the domain level using
the following properties:

- *#access-implicit-default-cells*
- *access-implicit-default*

- *#memory-implicit-default-cells*
- *memory-implicit-default*

- *#sram-implicit-default-cells*
- *sram-implicit-default*

Each property specifies the default value for the *access*, *memory* and
*sram* flags for the execution domain (the node in which the implicit
flags properties appear).

The number of cells to use in each case is provided by the
*#access-implicit-default-cells*, *#memory-implicit-default-cells*, and
*#sram-implicit-default-cells* properties.

Here is an example:

.. code-block:: dts

   #access-implicit-default-cells = <1>;
   access-implicit-default = <0xff00ff>;
   #access-flags-cells = <0x0>;
   access = <&mmc0>;

Default Execution Domain
------------------------

There is a concept of a default execution domain in system devicetree.
This corresponds to an execution domain running on the default CPU
cluster, ``/cpus`` (see :numref:`default-cpu-cluster`). This default
domain is compatible with the current base specification.

Here are some use cases for this domain:

1. As a way to specify the default place to assign added hardware (see
   usage environment #1 in :numref:`usage-environments`)

   The default domain does not have to list the all the hardware
   resources allocated to it. It gets everything not explicitly
   allocated to other domains.

   This minimizes the amount of information needed in ``/domains``.

   This can also be useful for managing dynamic hardware, such as add-on
   boards and FPGA images that add new devices.

2. The default domain can be used to specify what a master environment
   sees (see usage environment #2)

   For example, the default domain can be the entity configuring a
   master environment like Linux or Xen, while the other domains are to
   be managed by the master.

In a system device tree without a default CPU cluster, the memory
assignment for each domain is specified using the *memory* property in
each "openamp,domain-v1" node. In a devicetree with a default domain and
software running on it that is not aware of the system devicetree's
semantics, it may be convenient to "hide" the memory assignments for
non-default execution domains from that software.

This is possible using ``/reserved-memory``. Here is an example:

.. code-block:: dts

   reserved-memory {
           #address-cells = <0x2>;
           #size-cells = <0x2>;
           ranges;

           memory_r5@0 {
                   compatible = "openamp,domain-memory-v1";
                   reg = <0x0 0x0 0x0 0x8000000>;
           };
   };

The purpose of ``memory_r5@0`` is to let the default execution domain
know that it shouldn't use the 0x0-0x8000000 memory range, because it is
reserved for use by other domains.

Per-Domain Reserved Memory and Chosen Nodes
-------------------------------------------

``/reserved-memory`` and ``/chosen`` are top-level nodes defined in the
base specification which are dedicated to configuration of the default
execution domain, rather than hardware description of that domain.

Each execution domain in a system devicetree might need similar
configuration. To enable this, domain nodes may have ``chosen`` and
``reserved-memory`` child nodes with the same semantics, but which apply
to this domain. The top-level ``/reserved-memory`` and ``/chosen`` nodes
remain in place for the default execution domain.

Here is an example:

.. code-block:: dts

   / {
           /* chosen settings for /cpus */
           chosen {
           };

           /* reserved memory for /cpus */
           reserved-memory {
           };

           domains {
                   openamp_r5 {
                           compatible = "openamp,domain-v1";

                           /* chosen for "openamp_r5" */
                           chosen {
                           };

                           /* reserved memory for "openamp_r5" */
                           reserved-memory {
                           };
                   };
           };
   };
