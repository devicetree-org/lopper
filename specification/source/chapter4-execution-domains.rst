Execution Domains
=================

An execution domain is a collection of software, firmware, and board
configurations that enable an operating system or an application to run
a cpus cluster. With multiple cpus clusters in a system it is natural to
have multiple execution domains, at least one per cpus cluster. There
can be more than one execution domain for each cluster, with
virtualization or non-lockstep execution (for cpus clusters that support
it). Execution domains are configured and added at a later stage by a
software architect.

Execution domains are expressed by a new node "openamp,domain"
compatible. Being a configuration rather than a description, their
natural place is under a new top-level node /domains:

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

An openamp,domain node contains information about:

- cpus: physical cpus on which the software is running on
- #access-flags-cells (optional): how many cells to specify special access for each
  device, if absent the default is zero
- access: any devices configured to be only accessible by a domain
- #memory-flags-cells (optional): how many cells to specify special
  access flags for each memory range, if absent the default is zero
- memory: memory assigned to the domain
- #sram-flags-cells (optional): how many cells to specify special
  access flags for each memory range, if absent the default is zero
- sram (optional): sram regions assigned to the domain
- id: a 32bit integer that identifies a domain

cpus is in the format: link-to-cluster cpu-mask execution-level

Where the cpu-mask is a bitfield indicating the relevant CPUs in the
cluster, and execution-level is the execution level which is
cluster-specific (e.g. EL2 for ARMv8).

For Cortex-R5 CPUs, execution-level is:

- bit 31: secure (1) / non-secure (0)
- bit 30: lockstep (1) / split (0)

For Cortex-A53/A72 CPUs, execution-level is:

- bit 31: secure (1) / non-secure (0)
- bits 0-1: EL0 (0x0), EL1 (0x1), or EL2 (0x2)

The execution level is the most privileged level that the domain can
make use of. If the execution level is secure, then "secure-reg"
addresses (when specified) are used when the domain accesses device
memory mapped regions.

access is list of links to devices. The links are to devices that are
configured to be only accessible by an execution domain, using bus
firewalls or similar technologies. Each link to a device can be followed
by one or more cells that defined access flags. The number of cells is
defined by #access-flags-cells and can be zero of no flags are to be
specified.

memory is a sequence of start, size, flags tuples. #address-cells and
#size-cells express how many cells are used to specify start and size
respectively. #memory-flags-cells specifies how many cells are used to
specify access flags and can be zero.

sram, like memory, is a sequence of start, size, flags tuples. However,
the sram ranges should be subsets or matching mmio-sram ranges.
#sram-flags-cells specifies how many cells are used to specify access
flags and can be zero.

Access flags are domain specific and have default values defined in
the System Device Tree specification for each domain type. Different
domains types have different compatible strings, in addition to
"openamp,domain".

The memory range assigned to an execution domain is expressed by the
memory property. It needs to be a subset of the physical memory in the
system. The memory property can also be used to express memory sharing
between domains:

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

In a system device tree without a default cpus cluster (no top-level
cpus node), lopper figures out memory assignment for each domain by
looking at the memory property under each "openamp,domain" node. In a
device tree with a top-level cpus cluster, and potentially a legacy OS
running on it, we might want to "hide" the memory reservation for other
clusters from /cpus. We can do that with /reserved-memory:

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

The purpose of memory\_r5@0 is to let the default execution domain know
that it shouldn't use the 0x0-0x8000000 memory range because it is
reserved for use by other domains.

/reserved-memory and /chosen are top-level nodes dedicated to
configurations, rather than hardware description. Each execution domain
might need similar configurations, hence, chosen and reserved-memory are
also specified under each openamp,domain node for domains specific
configurations. The top-level /reserved-memory and /chosen nodes remain in
place for the default execution domain. As an example:

.. code-block:: none

   /chosen -> configuration for a legacy OS running on /cpus
   /reserved-memory -> reserved memory for a legacy OS running on /cpus

   /domains/openamp_r5/chosen -> configuration for the domain "openamp_r5"
   /domains/openamp_r5/reserved-memory -> reserved memory for "openamp_r5"

os,type
=======

Execution domains can have an optional os,type property.

Execution domains can have an optional "os,type" property to capture one
or more operating systems that may run on the domain. The field may be
used by automated tooling for activities, such as verifying that the
domain is capable of running the operating system, configuring a build
system to produce the proper operating system, configure a storage
mechanism to include the specified operating system, or other purposes.

The value of "os,type" is a string defined in the format:

.. code-block:: none

	OS_TYPE[,TYPE_ID[,TYPE_ID_VERSION]]

OS\_TYPE is mandatory and explains what the type of the operating system
will be. The values for this are defined as follows. In order to add
additional types, the specification should be updated.

.. code-block:: none

	OS_TYPE:
	   baremetal
	   linux
	   freertos
	   zephyr
	   custom
	   x-<vendor>[-os]

*baremetal* refers to a direct application that executes on the system
with no conventional operating system. Examples of this may include
first stage boot loader, second stage boot loader, u-boot,
arm-trusted-firmware, etc.

*linux* refers to a Linux based operating system. Examples of this may
include Yocto Project derived, Red Hat, Ubuntu, etc.

*freertos* refers to FreeRTOS real-time operating system.

*zephyr* refers to Zephyr operating system.

*custom* refers to a user specific operating system. Custom is to be
used only by the group providing the custom implementation. Each usage
of custom will be different.

*x-\<vendor\>[-os]* refers to an extension of a non-registered vendor
specific operating system.  The 'x' refers to extension, which is
attempts to avoid namespace collisions by convention. At a minimum the
name space must be x-\<vendor\>, such as x-xilinx.  However, the vendor
name may not be a specific enough namespace to avoid collision, so an
optional "-os" is allowed as well.  The \<vendor\> controls the
namespace of "os" values, if they are used.  For instance Wind River
VxWorks could be specified using: x-windriver-vxworks.

It is recommended that a vendor register their operating system in the
official named list, only using the extension format until it is
official.

The *TYPE_ID* is specific to each OS\_TYPE, but is not currently
formalized. The purpose of this is to further clarify details on the
OS\_TYPE if desired. For instance, to specify Ubuntu Linux, use:
linux,ubuntu

As *TYPE_ID* is not yet formalized it is open for different usages by
different parties. It's recommended that groups work together to define
common values where appropriate.

The *TYPE_ID_VERSION* is an optional parameter that is allowed, only if
the TYPE\_ID is used, and it's purpose is to specify the version of the
TYPE\_ID.  In the prior example of "linux,ubuntu", it may be specified
"linux,ubuntu,18.04".

As with *TYPE_ID*, this may be open to namespace collisions, and is
again recommended that groups work together to define common values
where appropriate.

Examples:

.. code-block:: none

	os,type = "linux"

	os,type = "linux,ubuntu,18.04"

	os.type = "linux,ubuntu,18.04.01"

	os,type = "linux,yocto"

	os,type = "linux,yocto,gatesgarth"

	os.type = "baremetal"

	os.type = "baremetal,fsbl"

	os.type = "baremetal,newlib,3.3.0"
