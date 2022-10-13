System Device Tree Concepts
===========================

System Device Trees extends traditional Device Trees to handle
heterogeneous SoCs with multiple CPUs and Execution Domains. An
Execution Domain can be seen as an address space that is running a
software image, whether an operating system, a hypervisor or firmware
that has a set of cpus, memory and devices attached to it. I.e. Each
individual CPU/core that is not part of an SMP cluster is a separate
Execution Domain as is the different Execution Levels on an ARMv8-A
architecture. Trusted and not trusted environment can also be viewed as
separate Execution Domains.

A design goal of System Device Trees is that no current client of Device
Trees should have to change at all, unless it wants to take advantage of
the extra information. This means that Linux in particular does not need
to change since it will see a Device Tree that it can handle with the
current implementation, potentially with some extra information it can
ignore.

System Device Trees must handle two types of heterogeneous additions:

1. Being able to specify different cpu clusters and the actual memory
   and devices hard-wired to them
    - This is done through the new Hardware Descriptions, such as
      "cpu,cluster" and "indirect-bus"
    - This information is provided by the SoC vendor and is typically
      fixed for a given SoC/board
2. Being able to assign hardware resources that can be configured by
   software to be used by one or more Execution Domains
    - This is done through the Execution Domain configuration
    - This information is provided by a System Architect and will be
      different for different use cases, even for the same board
        - E.g. How much memory and which devices goes to Linux vs. an
          RTOS can be different from one boot to another
    - This information should be separated from the hard-wired
      information for two reasons
        - A different persona will add and edit the information
        - Configuration should be separated from specification since it
          has a different rate of change

The System Device Trees and Execution Domain information are used in two
major use cases:

1. Exclusively on the host by using a tool like Lopper that will "prune"
   the System Device Tree
    - Each domain will get its own "traditional" Device Tree that only
      sees one address space and has one "cpus" node, etc.
    - Lopper has pluggable backends to it can also generate information
      for clients that is using a different format
        - E.g. It can generate a bunch of "#defines" that can be
          included and compiled in to an RTOS
2. System Device Trees can be used by a "master" target environment that
   manages multiple Execution Domains:
    - a firmware that can set up hardware protection and use it to
      restart individual domains
        - E.g. Protect the Linux memory so the R5 OS can't reach it
    - any other operating system or hypervisor that has sub-domains
        - E.g. Xen can use the Execution Domains to get info about the Xen
          guests (also called domains)
        - E.g. Linux could use the default domain for its own
          configuration and the domains to manage other CPUs
        - Since System Device Trees are backwards compatible with Device
          Trees, the only changes needed in Linux would be any new code
          taking advantage of the Domain information
        - a default master has access to all resources (CPUs, memories,
          devices), it has to make sure it stops using the resource
          itself when it "gives it away" to a sub-domain

There is a concept of a default Execution Domain in System Device Trees,
which corresponds to /cpus. The default domain is compatible with the
current traditional Device Tree. It is useful for a couple of reasons:

1. As a way to specify the default place to assign added hardware (see
   use case #1)
    - A default domain does not have to list the all the HW resources
      allocated to it. It gets everything not allocated elsewhere by
      Lopper.
    - This minimizes the amount of information needed in the Domain
      configuration.
    - This is also useful for dynamic hardware such as add-on boards and
      FPGA images that are adding new devices.
2. The default domain can be used to specify what a master environment
   sees (see use case #2)
    - E.g. the default domain is what is configuring Linux or Xen, while
      the other domains specify domains to be managed by the master


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
- An "address-map" property to express the different address mappings of
  the different cpus clusters and to map indirect-buses.

The following is a brief example to show how they can be used together:


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
- an indirect-bus "amba_rpu" which is not visible to the top-level cpus
  node
- the cpus_r5 cluster can see amba_rpu because it is explicitly mapped
  using the address-map property


Devices only physically accessible from one of the two clusters should
be placed under an indirect-bus as appropriate. For instance, in the
following example we can see how interrupts controllers are expressed:


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


gic_a72 is accessible by /cpus, but not by cpus_r5, because amba_apu is
not present in the address-map of cpus_r5.

gic_r5 is visible to cpus_r5, because it is present in the address map
of cpus_r5.  gic_r5 is not visible to /cpus because indirect-bus doesn't
automatically map to the parent address space, and /cpus doesn't have an
address-map property in the example.

Relying on the fact that each interrupt controller is correctly visible
to the right cpus cluster, it is possible to express interrupt routing
from a device to multiple clusters. For instance:


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

In this example, all devices under amba, including can@ff060000, have
their interrupts routed to both gic_r5 and gic_a72.

Memory only physically accessible by one of the clusters can be placed
under an indirect-bus like any other device types. However, normal
memory is usually physically accessible by all clusters. It is just a
software configuration that splits memory into ranges and assigns a
range for each execution domain. Software configurations are explained
below.


Secure Addresses
================

It is possible for a single device to be accessible at different
addresses whether the transaction is marked as secure or non-secure.

A new type of bus, compatible to "secure-bus", is used in cases where
devices have multiple different addresses depending on the execution
mode.

When "secure-bus" is used, the reg property of children nodes has one
extra cell at the beginning to specify the execution mode. Currently the
following execution modes are supported:

- 0x0: normal world
- 0x1: secure world

Example:

	amba {
		compatible = "secure-bus";

			timer@ff110000 {
				compatible = "cdns,ttc";
				status = "okay";

				       /* normal world addresses */
				reg = <0x0 0xff110000 0x0 0x1000
				       /* secure world addresses */
				       0x1 0xff110000 0x00 0x1000>;
			};


CPU clusters have an optional property "secure-address-map" which allows
to specify the address map of the CPU cluster, including the execution
mode. The format of "secure-address-map" is similar to "address-map",
but with one additional cell: the first cell specifies the execute mode
in the same format of "secure-bus". Example:


	/* additional R5 cluster */
	cpus_r5: cpus-cluster@0 {
		compatible = "cpus,cluster";

		/* first cell: execution mode. 0x1 means "secure world" */
		secure-address-map = <0x1 0x1 0xf9000000 &amba_rpu 0x1 0xf9000000 0x0 0x10000>;
	};


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


	domains {
		openamp_r5 {
			compatible = "openamp,domain-v1";
			cpus = <&cpus_r5 0x2 0x80000000>;
			#memory-flags-cells = <0>;
			memory = <0x0 0x0 0x0 0x8000000>;
			#access-flags-cells = <1>;
			access = <&can@ff060000 0x3 &ethernet@ff0c0000 0x7>;
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

	/chosen -> configuration for a legacy OS running on /cpus
	/reserved-memory -> reserved memory for a legacy OS running on /cpus

	/domains/openamp_r5/chosen -> configuration for the domain "openamp_r5"
	/domains/openamp_r5/reserved-memory -> reserved memory for "openamp_r5"

Execution domains can have an optional "os,type" property, see
os,types.md.


Implicit flags
==============

It is possible to specify default flags values at the domain level using
thei following properties:

- #access-implicit-default-cells
- access-implicit-default

- #memory-implicit-default-cells
- memory-implicit-default

- #sram-implicit-default-cells
- sram-implicit-default

Each property specifies the default value for the access, memory and
sram flags for their domain. The number of cells to use is provided by
the #access-implicit-default-cells, #memory-implicit-default-cells, and
#sram-implicit-default-cells properties.

Example:

~~~
	#access-implicit-default-cells = <1>;
	access-implicit-default = <0xff00ff>;
	#access-flags-cells = <0x0>;
	access = <&mmc0>;
~~

YAML Example:

~~~
    access-implicit-default: {secure: true, allow-secure: true, requested: true, coherent: false, virtualized: true, qos:99}
~~~
