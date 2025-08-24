
DTB Overlay Generation
======================

Overview
---------


Dynamic FPGA reconfiguration using Device Tree Overlays (.dtbo) enables the reprogramming of Field-Programmable Gate Arrays (PL) while a Linux operating system is running.
This method is particularly relevant for systems where the FPGA interacts closely with the software environment.

The Device Tree overlay specifies the changes to be applied to the base Device Tree and typically includes:

- **Target FPGA Region**: Defines the specific area of the FPGA that will be reconfigured.
- **FPGA Image Firmware**: Specifies the name of the bitstream file (``.bit`` or ``.bin``) that contains the new FPGA configuration, using the ``firmware-name`` property.
- **Child Devices**: Defines any new hardware blocks or peripherals that are part of the new FPGA configuration and require corresponding device nodes in the Device Tree.

The system device tree (SDT) generated using SDTGen contains all the necessary information about the hardware design, including the reconfigurable regions and their associated properties.
The PL-related information is kept in the pl.dtsi file within the SDT folder. This chapter describes how Lopper is used to convert the existing pl.dtsi inside SDT into a DT overlay file. The generated overlay file can then be used to reconfigure the FPGA at runtime.

Usage
------


To create a DT Overlay file and the base Linux device tree (without PL), follow these steps:



1. Set the following LOPPER_DTC_FLAGS in the shell for Lopper to process symbols:

.. code-block:: bash

   export LOPPER_DTC_FLAGS="-b 0 -@"



2. Sample Lopper command to generate a DT Overlay file (.dtsi):

.. code-block:: bash

   lopper -O <output directory> -f --enhanced <system device tree> <linux device tree targeting APU (0th core) without PL> -- xlnx_overlay_pl_dt <config options> <pl.dtsi path from system device tree> [--firmware-name=<bit/bin file name>]



3. Sample Lopper command to generate a Linux Device Tree without the reprogrammable region (PL):

.. code-block:: bash

   lopper -O <output directory> -f --enhanced [-i <imux lops>] <system device tree without PL generated in step 2> <output base linux device tree structure/blob> -- gen_domain_dts <processor instance> linux_dt



4. Generate a dtbo file for the PL overlay file using dtc:

.. code-block:: bash

   dtc -I dts -O dtb -o <output dtbo file> <input dtsi file>


Examples
--------

Segmented PL configuration for Versal 2VE and 2VM devices
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   # Set LOPPER_DTC_FLAGS
   export LOPPER_DTC_FLAGS="-b 0 -@"
   # Generate an APU core-specific device tree without PL, and generate a PL overlay (dtsi) file for segmented flow in the given output directory.
   lopper -O . -f --enhanced /home/abc/versal_2ve_2vm_sdt/system-top.dts ./linux_system.dts -- xlnx_overlay_pl_dt segmented /home/abc/versal_2ve_2vm_sdt/pl.dtsi
   # Generate a Linux Device Tree blob without PL
   lopper -O . -f --enhanced -i lop-a78-imux.dts ./linux_system.dts system.dtb -- gen_domain_dts cortexa78_0 linux_dt
   # Generate a DTBO file for the PL overlay
   dtc -I dts -O dtb -o pl_overlay.dtbo ./pl.dtsi


DFX mode for partial reconfiguration in Versal ACAP
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   # Set LOPPER_DTC_FLAGS
   export LOPPER_DTC_FLAGS="-b 0 -@"
   # Generate an APU core-specific device tree without PL, and generate a PL overlay (dtsi) file for DFX flow in the given output directory.
   lopper -O . -f --enhanced /home/abc/versal_sdt/system-top.dts ./linux_system.dts -- xlnx_overlay_pl_dt dfx /home/abc/versal_sdt/pl.dtsi
   # Generate a Linux Device Tree without PL
   lopper -O . -f --enhanced -i lop-a72-imux.dts ./linux_system.dts system.dtb -- gen_domain_dts psv_cortexa72_0 linux_dt
   # Generate a DTBO file for the PL overlay
   dtc -I dts -O dtb -o pl_overlay.dtbo ./pl.dtsi


.. note::
   Steps 1, 3, and 4 remain the same for all PL overlay use cases (DFX, segmented, full). Only step 2 changes based on the use case.
   The following examples showcase only the Step 2 usage; the rest of the steps remain unchanged.
   For more information on the details of Step 3, refer to :ref:`Chapter 2 <chapter2-device_tree_generation>` in the documentation.


Custom firmware name inside PL overlay file
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   export LOPPER_DTC_FLAGS="-b 0 -@"
   lopper -O . -f --enhanced /home/abc/versal_sdt/system-top.dts ./linux_system.dts -- xlnx_overlay_pl_dt dfx /home/abc/versal_sdt/pl.dtsi --firmware-name=custom_firmware.bit


Complete PL configuration for ZynqMP US+ SoC
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   export LOPPER_DTC_FLAGS="-b 0 -@"
   lopper -O . -f --enhanced /home/abc/zynqmp_sdt/system-top.dts ./linux_system.dts -- xlnx_overlay_pl_dt full /home/abc/zynqmp_sdt/pl.dtsi --firmware-name=custom_firmware.bit


Configuration using an external FPGA manager for Zynq
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   export LOPPER_DTC_FLAGS="-b 0 -@"
   lopper -O . -f --enhanced /home/abc/zynq_sdt/system-top.dts ./linux_system.dts -- xlnx_overlay_pl_dt external-fpga-config /home/abc/zynq_sdt/pl.dtsi --firmware-name=custom_firmware.bit


Description
-----------


The purpose of the overlay solution is to facilitate PL configuration and reconfiguration once the Linux system is up and running.
As part of the solution, Lopper first segregates the PL configuration from the system device tree using the xlnx_overlay_pl_dt assist, leaving an APU core-specific device tree without PL as the output.
During this process, it manipulates the pl.dtsi file to create a new overlay dtsi file that contains only the required PL-related information in an overlay format.
The system device tree without PL can then be used to generate the base Linux device tree (without PL) using Lopper, as described in :ref:`Chapter 2 <chapter2-device_tree_generation>`.
The PL overlay dtsi file can be converted to a dtbo file using dtc (Device Tree Compiler).


xlnx_overlay_pl_dt
~~~~~~~~~~~~~~~~~~

**Arguments:**


- **Mandatory arguments:**
   - **First argument:** Configuration name
      - ``full``: Complete PL configuration
      - ``segmented``: Segmented PL configuration
      - ``dfx``: Dynamic Function eXchange (DFX) mode for partial reconfiguration
      - ``external-fpga-config``: Configuration using an external FPGA manager
   - **Second argument:** ``pl.dtsi`` file path from system device tree
- **Optional argument:**
   - ``--firmware-name=<bit/bin file name>``: Specifies the name of the bitstream file for FPGA configuration. If not provided, it defaults to the file name in the system device tree.


**Usage:**

- Segregates the PL configuration from the system device tree, leaving an APU core-specific device tree without PL as the output.
- Manipulates the ``pl.dtsi`` file to create a new overlay ``dtsi`` file that contains only the required PL-related information in an overlay format.
- Supports multiple use cases, including DFX, segmented, and full PL configurations.