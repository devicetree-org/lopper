
.. _chapter2-device_tree_generation:

Linux Device Tree Generation
==============================

Overview
---------

This section provides steps involved in generating Linux device trees for AMD SOCs. It includes usage instructions, examples for specific platforms, and a description of the key lops and assists involved in the process.

Usage
------

Set below LOPPER_DTC_FLAGS over shell for dtc (called from lopper) to process symbols.

.. code-block:: bash

   export LOPPER_DTC_FLAGS="-b 0 -@"

Sample Lopper command:

.. code-block:: bash

   lopper -f --enhanced [-i <imux lops>] <system device tree> <output linux device tree structure/blob> -- gen_domain_dts <processor instance> linux_dt


Examples
--------

For Zynq
~~~~~~~~~

.. code-block:: bash

   LOPPER_DTC_FLAGS="-b 0 -@" lopper -f --enhanced /home/abc/zynq_sdt/system-top.dts system.dtb -- gen_domain_dts ps7_cortexa9_0 linux_dt

For ZynqMP US+
~~~~~~~~~~~~~~

.. code-block:: bash

   LOPPER_DTC_FLAGS="-b 0 -@" lopper -f --enhanced -i lop-a53-imux.dts /home/abc/zynqmp_sdt/system-top.dts system.dtb -- gen_domain_dts psu_cortexa53_0 linux_dt

For Versal ACAP
~~~~~~~~~~~~~~~


.. code-block:: bash

   LOPPER_DTC_FLAGS="-b 0 -@" lopper -f --enhanced -i lop-a72-imux.dts /home/abc/versal_sdt/system-top.dts system.dtb -- gen_domain_dts psv_cortexa72_0 linux_dt

For Versal Net
~~~~~~~~~~~~~~

.. code-block:: bash

   LOPPER_DTC_FLAGS="-b 0 -@" lopper -f --enhanced -i lop-a78-imux.dts /home/abc/versal_net_sdt/system-top.dts system.dtb -- gen_domain_dts psx_cortexa78_0 linux_dt

For Versal 2VE and 2VM devices
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   LOPPER_DTC_FLAGS="-b 0 -@" lopper -f --enhanced -i lop-a78-imux.dts /home/abc/versal_2ve_2vm_sdt/system-top.dts system.dtb -- gen_domain_dts cortexa78_0 linux_dt

.. note::
   system.dtb (linux dtb output) can be replaced with system.dts (linux dts output) in all the above commands.

Description
------------

Lopper takes system device tree as an input, generates linux device tree in dts/dtb format. It uses imux lops and gen_domain_dts assist file to prune system device tree. Two arguments: 1) the target processor instance for linux 2) linux_dt (fixed argument) are fed as arguments to the gen_domain_dts assist.

imux lops
~~~~~~~~~

In heterogenous AMD platforms, there are multiple gics, one for APU and one for RPU. System Device Tree represents the presence of two gics as interrupt-multiplex node. But, the linux device tree expects only one target processor specific gic.

**Usage:**

- Prunes the imux node and maintains a single gic node catering to the target processor.
- Updates the interrupt parent property in the peripheral node accordingly.

**Available imux lops files:**

- ``lop-a53-imux.dts`` : For ZynqMP US+
- ``lop-a72-imux.dts`` : For Versal ACAP
- ``lop-a78-imux.dts`` : For Versal Net, Versal 2VE and 2VM devices

gen_domain_dts
~~~~~~~~~~~~~~

**Arguments:**

- Takes target processor as the first argument.
- Possible second positional arguments are: 1) linux_dt 2) zephyr_dt
- In absence of second positional argument, create the processor specific domain device tree for baremetal

**List of available target processors:**

- ``ps7_cortexa9_0`` : For Zynq
- ``psu_cortexa53_0`` : For ZynqMP US+
- ``psv_cortexa72_0`` : For Versal ACAP
- ``psx_cortexa78_0`` : For Versal Net
- ``cortexa78_0`` : For Versal 2VE and 2VM

**Usage:**

- Creates domain specific device tree for the target processor-os combination.
- Removes the nodes which are not mapped to the target processor.
- Removes nodes which are mapped to the target processor in system device tree but are not needed for linux device tree to keep the linux device tree size in check.
- Removes non-DDR memory nodes (e.g. Bram, linear SPI) which are not needed in linux device trees.
- Updates memory nodes as per memory allocation done to the target processor.
