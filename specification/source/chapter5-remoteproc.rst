OpenAMP RemoteProc (Under Discussion)
=====================================

.. include:: under-discussion.txt

[OpenAMP]_ RemoteProc is a framework for remote processor communication and
lifecycle management between Linux and other OSes.

[Lopper]_ can generate OpenAMP RemoteProc nodes for Linux and other Operating
Systems starting from the System Device Tree representation of the same
information.

Lopper comes with plugins and one of them is to generate the Xilinx
RemoteProc device tree nodes. Other silicon vendors can introduce
similar plugins to generate their RemoteProc device tree nodes.



System Device Tree
------------------

At the System Device Tree level, no special nodes or properties are
needed to represent RemoteProc information. Typically, the main cluster
and the remote cluster are separate domains. Any shared resources are
repeated under all domains that can access them.

Special vendor-specific properties can be represented using the access
list flag fields, as usual with system device tree.



System Device Tree Example
~~~~~~~~~~~~~~~~~~~~~~~~~~

This is a System Device Tree example that can be used as input for
lopper to generate RemoteProc nodes for Linux.

Example in YAML:


.. code-block:: YAML

   definitions:
       OpenAMP:

           openamp-channel-0-access-srams: &openamp-channel0-access-srams # used for access in each domain
               - dev: psu_r5_0_atcm_global
               - dev: psu_r5_0_btcm_global

           openamp-channel-1-access-srams: &openamp-channel1-access-srams # used for access in each domain
               - dev: psu_r5_1_atcm_global
               - dev: psu_r5_1_btcm_global


           rpu1vdev0vring0: &rpu1vdev0vring0
               compatible: xilinx,openamp-ipc-1.0
               no-map: 1
               reg:
                   - start: 0x3ef40000
                     size: 0x4000

           rpu1vdev0vring1: &rpu1vdev0vring1
               compatible: xilinx,openamp-ipc-1.0
               no-map: 1
               reg:
                   - start: 0x3ef44000
                     size: 0x4000

           rpu1vdev0buffer: &rpu1vdev0buffer
               compatible: xilinx,openamp-ipc-1.0
               no-map: 1
               reg:
                   - start: 0x3ef48000
                     size: 0x100000

           rproc_reserved1: &rproc_reserved1
               compatible: xilinx,openamp-ipc-1.0
               no-map: 1
               reg:
                   - start: 0x3ef00000
                     size: 0x40000

           rproc_reserved0: &rproc_reserved0
               compatible: xilinx,openamp-ipc-1.0
               no-map: 1
               reg:
                   - start: 0x3ed00000
                     size: 0x40000

           rpu0vdev0vring: &rpu0vdev0vring0
               compatible: xilinx,openamp-ipc-1.0
               no-map: 1
               reg:
                   - start: 0x3ed40000
                     size: 0x4000

           rpu0vdev0vring1: &rpu0vdev0vring1
               compatible: xilinx,openamp-ipc-1.0
               no-map: 1
               reg:
                   - start: 0x3ed44000
                     size: 0x4000

           rpu0vdev0buffer: &rpu0vdev0buffer
               compatible: xilinx,openamp-ipc-1.0
               no-map: 1
               reg:
                   - start: 0x3ed48000
                     size: 0x100000


   domains:
       openamp_a72_0_cluster: # host in channel from a72-0 to r5-1 over channel 0
           compatible: openamp,domain-v1
           cpus:
               - cluster: cpus_a72
                 cpumask: 0x1
                 mode:
                    secure: false
                    el: 0x1
           access:
               # if we want to have a list merge, it should be in a list
               - dev: ipi0  # used for Open AMP RPMsg IPC
               - dev: ipi1  # same as ipi0
               - <<+: [ *openamp-channel0-access-srams, *openamp-channel1-access-srams ]

           reserved-memory:
               ranges: true
               # if we want an object / node merge, it should be like this (a map)
               label-references: { rpu0vdev0vring0, rpu0vdev0vring0, rpu0vdev0buffer, rproc_reserved0 }
               label-references: { rpu1vdev0vring0, rpu1vdev0vring1, rpu1vdev0buffer, rproc_reserved1 }

           domain-to-domain:
               compatible: openamp,domain-to-domain-v1

               remoteproc0:
                   compatible: openamp,remoteproc-v1
                   remote: openamp_r5_0_cluster
                   elfload:
                        - rproc_reserved0
                        - openamp-channel-0-access-srams

               remoteproc1:
                   compatible: openamp,remoteproc-v1
                   remote: openamp_r5_1_cluster
                   elfload:
                        - rproc_reserved1
                        - openamp-channel-1-access-srams

               rpmsg0:
                   compatible: openamp,rpmsg-v1
                   openamp-xlnx-native: true # use native OpenAMP implementation
                   remote:  openamp_r5_0_cluster
                   mbox: ipi0
                   carveouts:
                      - rpu0vdev0buffer
                      - rpu0vdev0vring0
                      - rpu0vdev0vring1


               rpmsg1:
                   compatible: openamp,rpmsg-v1
                   openamp-xlnx-native: true # use native OpenAMP implementation
                   remote:  openamp_r5_1_cluster
                   mbox: ipi1
                   carveouts:
                      - rpu1vdev0buffer
                      - rpu1vdev0vring0
                      - rpu1vdev0vring1

       openamp_r5_0_cluster:
           compatible: openamp,domain-v1
           cpus:
               - cluster: cpus_r5
                 cpumask: 0x1
                 mode:
                 secure: true
           access:
               - dev: ipi2
               - <<+: *openamp-channel0-access-srams # TCM banks used for firmware memory
           reserved-memory:
               ranges: true
                label-references: { rpu0vdev0vring0, rpu0vdev0vring0, rpu0vdev0buffer, rproc_reserved0 }
           domain-to-domain:
                compatible: openamp,domain-to-domain-v1
                rpmsg0:
                    compatible: openamp,rpmsg-v1
                    host: openamp_a72_0_cluster
                    mbox: ipi2
                    carveouts:
                       - rpu0vdev0buffer
                       - rpu0vdev0vring0
                       - rpu0vdev0vring1

       openamp_r5_1_cluster:
           compatible: openamp,domain-v1
           cpus:
               - cluster: cpus_r5
                 cpumask: 0x2
                 mode:
                 secure: true
           access:
               - dev: ipi3
               - <<+: *openamp-channel1-access-srams # TCM banks used for firmware memory
           reserved-memory:
               ranges: true
               label-references: { rpu1vdev0vring0, rpu1vdev0vring1, rpu1vdev0buffer, rproc_reserved1 }
           domain-to-domain:
                compatible: openamp,domain-to-domain-v1
                relation0:
                    compatible: openamp,rpmsg-v1
                    host: openamp_a72_0_cluster
                    mbox: ipi3
                    carveouts:
                       - rpu1vdev0buffer
                       - rpu1vdev0vring0
                       - rpu1vdev0vring1

Device Tree Conversion and Example
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The corresponding Device Tree for Linux generated by lopper is the
following. (It is compliant to the latest RemoteProc Xilinx bindings
upstream.)

- the reserved-memory regions are copied from the reserved-memory subnode of a domain.
- r5ss\@f9a00000:
    - xlnx,cluster-mode comes from the cpus property of the RPU domain
    - memory-region and sram properties come from the shared regions
    - mboxes and mbox-names come from the access property and the 0x13 flag


.. code-block:: DTS

	reserved-memory {
		#address-cells = <1>;
		#size-cells = <1>;
		ranges;

		rpu0vdev0vring0: rpu0vdev0vring0@3ed40000 {
			compatible = "xilinx,openamp-ipc-1.0";
			no-map;
			reg = <0x3ed40000 0x4000>;
		};
		rpu0vdev0vring1: rpu0vdev0vring1@3ed44000 {
			compatible = "xilinx,openamp-ipc-1.0";
			no-map;
			reg = <0x3ed44000 0x4000>;
		};
		rpu0vdev0buffer: rpu0vdev0buffer@3ed48000 {
			compatible = "xilinx,openamp-ipc-1.0";
			no-map;
			reg = <0x3ed48000 0x100000>;
		};
		rproc_0_reserved: rproc@3ed000000 {
			compatible = "xilinx,openamp-ipc-1.0";
			no-map;
			reg = <0x3ed00000 0x40000>;
		};

		rpu1vdev0vring0: rpu1vdev0vring0@3ef40000 {
			compatible = "xilinx,openamp-ipc-1.0";
			no-map;
			reg = <0x3ef40000 0x4000>;
		};
		rpu1vdev0vring1: rpu1vdev0vring1@3ef44000 {
			compatible = "xilinx,openamp-ipc-1.0";
			no-map;
			reg = <0x3ef44000 0x4000>;
		};
		rpu1vdev0buffer: rpu1vdev0buffer@3ef48000 {
			compatible = "xilinx,openamp-ipc-1.0";
			no-map;
			reg = <0x3ef48000 0x100000>;
		};
		rproc_1_reserved: rproc@3ef000000 {
			compatible = "xilinx,openamp-ipc-1.0";
			no-map;
			reg = <0x3ef00000 0x40000>;
		};
	};

	r5ss@f9a00000 {
		compatible = "xlnx,zynqmp-r5-remoteproc";
		#address-cells = <2>;
		#size-cells = <2>;
		ranges;
		reg = <0x0 0xff9a0000 0x0 0x10000>;
		xlnx,cluster-mode = <0>;

		r5f_0 {
			compatible = "xilinx,r5f";
			memory-region = <&rproc_0_reserved0>,
					<&rpu0vdev0vring0>,
					<&rpu0vdev0vring1>,
					<&rpu0vdev0buffer>;
			sram = <&psu_r5_0_atcm_global>, <&psu_r5_0_btcm_global>;
			mboxes = <&ipi_mailbox_rpu0 0x0 &ipi_mailbox_rpu0 0x1>;
			mbox-names = "tx", "rx";
			power-domain = <0x7>;
		};

		r5f_1 {
			compatible = "xilinx,r5f";
			memory-region = <&rproc_1_reserved1>,
					<&rpu1vdev0vring0>,
					<&rpu1vdev0vring1>,
					<&rpu1vdev0buffer>;
			sram = <&psu_r5_1_atcm_global>, <&psu_r5_1_btcm_global>;
			mboxes = <&ipi_mailbox_rpu1 0x0 &ipi_mailbox_rpu1 0x1>;
			mbox-names = "tx", "rx";
			power-domain = <0x8>;
		};
	};
	psu_r5_0_atcm_global: psu_tcm_global@ffe00000 {
		compatible = "xlnx,psu-tcm-global";
		status = "okay";
		reg = <0x0 0xffe00000 0x0 0x10000>;
	};
	psu_r5_0_btcm_global: psu_tcm_global@ffe20000 {
		compatible = "xlnx,psu-tcm-global";
		status = "okay";
		reg = <0x0 0xffe20000 0x0 0x10000>;
	};

	psu_r5_1_atcm_global: psu_tcm_global@ffe90000 {
		compatible = "xlnx,psu-tcm-global";
		status = "okay";
		reg = <0x0 0xffe90000 0x0 0x10000>;
	};
	psu_r5_1_btcm_global: psu_tcm_global@ffeb0000 {
		compatible = "xlnx,psu-tcm-global";
		status = "okay";
		reg = <0x0 0xffeb0000 0x0 0x10000>;
	};

	zynqmp_ipi@0 {
		compatible = "xlnx,zynqmp-ipi-mailbox";
		interrupt-parent = <&gic>;
		interrupts = <0 29 4>;
		xlnx,ipi-id = <7>;
		#address-cells = <1>;
		#size-cells = <1>;
		ranges;

		 /* APU<->RPU0 IPI mailbox controller */
		 ipi_mailbox_rpu0: mailbox@ff90000 {
			reg = <0xff990600 0x20>,
			      <0xff990620 0x20>,
			      <0xff9900c0 0x20>,
			      <0xff9900e0 0x20>;
			reg-names = "local_request_region",
				    "local_response_region",
				    "remote_request_region",
				    "remote_response_region";
			#mbox-cells = <1>;
			xlnx,ipi-id = <1>;
		 };

		 /* APU<->RPU1 IPI mailbox controller */
		 ipi_mailbox_rpu1: mailbox@ff90000 {
			reg = <0xff990800 0x20>,
			      <0xff990820 0x20>,
			      <0xff990ec0 0x20>,
			      <0xff990ee0 0x20>;
			reg-names = "local_request_region",
				    "local_response_region",
				    "remote_request_region",
				    "remote_response_region";
			#mbox-cells = <1>;
			xlnx,ipi-id = <2>;
		 };

	};
