Bus Firewalls: Hardware Description
===================================

This extension is still a DRAFT.

Bus Firewall Controllers
------------------------

Bus Firewalls Controllers are hardware blocks like Xilinx XMPU and XPPU
that allow for the configuration of system-wide DMA transactions
blacklists and whitelists.

The controllers are described using regular device tree nodes such as:


	amba_xppu: indirect-bus@1 {
		compatible = "indirect-bus";
		#address-cells = <0x2>;
		#size-cells = <0x2>;

		lpd_xppu: xppu@ff990000 {
			compatible = "xlnx,xppu"
			#firewall-cells = <0x0>;
			reg = <0x0 0xff990000 0x0 0x1000>;
		};

		pmc_xppu: xppu@f1310000 {
			compatible = "xlnx,xppu"
			#firewall-cells = <0x0>;
			reg = <0x0 0xf1310000 0x0 0x1000>;
		};
	};


Where the compatible string "xlnx,xppu" indicates the type of firewall
controller, reg is the MMIO address of the controller, and #firewall-cells
indicates the presence of firewall-specific extra information (none in
this example.)


Device Protection
-----------------

Each device node protected by a firewall links to the relevant firewall
controller, for instance can0 is protected by lpd_xppu:


	amba {
		can0: can@ff060000 {
			firewall-0 = <&lpd_xppu>;
		};


Bus mastering devices are identified by bus firewalls using IDs. Their
transactions are marked with a device ID. These IDs are used to
configure bus firewalls and are called "Bus Master IDs". They are
advertised using a new property "bus-master-id":


	bus-master-id = <&controller u32>


Where &controller is the link to the bus firewall controller and u32 is
the Bus Master ID of the device:


	dev0: device@0 {
		bus-master-id = <&lpd_xppu 0x212>;


Full Example
------------

	amba_xppu: indirect-bus@1 {
		compatible = "indirect-bus";
		#address-cells = <0x2>;
		#size-cells = <0x2>;

		lpd_xppu: xppu@ff990000 {
			compatible = "xlnx,xppu"
			#firewall-cells = <0x0>;
			reg = <0x0 0xff990000 0x0 0x1000>;
		};

		pmc_xppu: xppu@f1310000 {
			compatible = "xlnx,xppu"
			#firewall-cells = <0x0>;
			reg = <0x0 0xf1310000 0x0 0x1000>;
		};
	};

	cpus_r5: cpus-cluster@0 {
		#address-cells = <0x1>;
		#size-cells = <0x0>;
		#cpus-mask-cells = <0x1>;
		compatible = "cpus,cluster";

		bus-master-id = <&lpd_xppu 0x0 &pmc_xppu 0x0 &lpd_xppu 0x1 &pmc_xppu 0x1>;
	};

	amba {
		ethernet0: ethernet@ff0c0000 {
			bus-master-id = <&lpd_xppu 0x234 &pmc_xppu 0x234>;
			firewall-0 = <&lpd_xppu>;
		};

		can0: can@ff060000 {
			firewall-0 = <&lpd_xppu>;
		};

		mmc0: sdhci@f1050000 {
			bus-master-id = <&lpd_xppu 0x243 &pmc_xppu 0x243>;
			firewall-0 = <&pmc_xppu>;
		};

		serial0: serial@ff000000 {
			firewall-0 = <&lpd_xppu>;
		};
	};


Bus Firewalls: Configuration
============================

Bus firewalls configuration is based on Execution Domains. They are the
natural place to describe the desired firewalls configurations because
they already specify device assignments. We only need to add protection
to the assignments. To do that, we add new node "resource-group" and a
two properties "firewallconf" and "firewallconf-default".


resource-group
--------------

A resource-group node is used to group together resources shared across
multiple Execution Domains. Instead of "access", we use the new property
"include" to link to a resource-group from a domain node:


	domains {
		#address-cells = <0x1>;
		#size-cells = <0x1>;
	
		resource_group_1: resource_group_1 {
			compatible = "openamp,resource-group-v1";
			access = <&ethernet, &serial0>;
			access-flags-names = "dev_rw", "dev_rw";
		};

		domain@0 {
			compatible = "openamp,domain-v1";
			#flags-cells = <1>;
			flags = <0x0 0x1>;
			flags-names = "dev_rw", "dev_ro";
			access = <&mmc0>;
			access-flags-names = "dev_rw";
			include = <&resource_group_1>;
		};

		domain@1 {
			compatible = "openamp,domain-v1";
			#flags-cells = <1>;
			flags = <0x3 0x0>;
			flags-names = "dev_rw", "dev_ro";
			access = <&can0>;
			access-flags-names = "dev_rw";
			include = <&resource_group_1>;
		};


In this example, resource_group_1 is shared between domain@0 and
domain@1, hence, both ethernet and serial0 are accessible by both
domains.

include is a list of links pointing to resource groups.

access under resource groups, much like access under domains, is a list
of links to peripherals.  
  

firewallconf
------------

firewallconf is a new property that can be used in a resource-group or a
domain node. It applies to all address ranges in the resource-group or
domain it appears in.


			firewallconf = <&domain0 block 0>;


The first cell is a link to a node of a bus mastering device (or a
domain). Lopper retrieves the bus-master-ids of the linked node for the
relevant controllers. If the linked node is a domain, lopper retrieves
the bus-master-id of every device in the domain access list and the
bus-master-id of the CPU cluster of the domain.

The second cell is the action, values can be allow (1), block (0), and
block-desirable (2):

- block [0]: access is blocked
- allow [1]: access is allowed
- block-desirable [2]: "block if you can"

The third cell is a priority number: the priority of the rule when
block-desirable is specified, otherwise unused.

block-desirable is useful because in many cases bus firewall controllers
only support few configuration entries, thus not everything can be
protected. With block-desirable we can let lopper compute the best
configuration to protect as much as possible according to the priorities
we set.


firewallconf-default
--------------------

firewallconf-default applies to all bus-master-ids except for the ones
listed in the firewallconf property:


		firewallconf-default = <block-desirable 8>,
		firewallconf = <&domain@0 allow 0>,
			       <&domain@1 allow 0>;


In this example, we want to block all bus-master-ids except for the ones
of domain@0 and domain@1.


Full Example
------------

Two domains sharing two key resources. The two resources are accessible
by the two domains using them, but everybody else is prevented from
accessing them. The two domains also want to protect themselves against
foreign access but at a lower priority.

		domains {
			#address-cells = <0x1>;
			#size-cells = <0x1>;
		
			resource_group_1: resource_group_1 {
				compatible = "openamp,group-v1";
				access = <&ethernet>, <&serial0>;
				firewallconf-default = <block 0>;
			};

			domain0: domain@0 {
				compatible = "openamp,domain-v1";
				id = <0x0>;
				memory = <0x100000 0x100000>;
				access = <&mmc0>;
				include = <&resource_group_1>;
				firewallconf-default = <block-desirable 8>;
			};

			domain1: domain@1 {
				compatible = "openamp,domain-v1";
				id = <0x1>;
				memory = <0x0 0x100000>;
				access = <&can0>;
				include = <&resource_group_1>;
				firewallconf-default = <block-desirable 8>;
			};
