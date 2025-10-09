Zephyr Device-Tree Generation for AMD SoCs using Lopper
========================================================

Introduction
------------

This document provides the process of generating the Zephyr device-trees and board overlay files for AMD SoCs using the Lopper tool.

The Zephyr device-tree generation typically happens after the hardware design is compiled in Vivado and the system device-tree is generated using the SDTGen tool.

Lopper consumes the system device-tree and generates the Zephyr specific device-tree files that are compatible with the Zephyr RTOS framework.

Overview
--------

The workflow for generating Zephyr device-trees involves the following steps:

1. **Hardware Design Compilation**: The hardware design is compiled in Vivado
2. **System Device-Tree Generation**: SDTGen tool generates the system device-tree from the compiled hardware design  
3. **Zephyr Device-Tree Generation**: Lopper processes the system device-tree to create Zephyr-specific device-tree files

For more information on SDTGen and hardware handoff file can be found in the `Xilinx System Device Tree Repository <https://github.com/Xilinx/system-device-tree-xlnx/blob/xlnx_rel_v2025.1/README.md>`_.

::

    Hardware Design (Vivado) → SDTGen → System Device-Tree → Lopper → Zephyr Device-Tree

Supported Processors
--------------------

Currently, the Zephyr device-tree generation supports the following AMD processor architectures:

- **Microblaze RISC-V**: RISC-V based soft processor core
- **Cortex-A78**: ARM high-performance processor  
- **Cortex-R52**: ARM real-time processor

Detailed Instructions
---------------------

The following sections provide comprehensive instructions for generating Zephyr device-trees for each supported processor type.

Prerequisites
~~~~~~~~~~~~~

Before proceeding with the device-tree generation, ensure you have:

- Lopper tool installed and accessible as per lopper prerequisite
- System device-tree files generated from your hardware design

Processor-Specific Documentation
---------------------------------

.. toctree::
   :maxdepth: 2
   :caption: Processor Guides:

   microblaze-riscv/index
   cortex-a78/index
   cortex-r52/index

Additional Resources
--------------------

- `Zephyr Project Documentation <https://docs.zephyrproject.org/>`_
- `AMD Embedded Documentation <https://www.amd.com/en/products/embedded>`_

Support
-------

For issues and questions related to Zephyr device-tree generation for AMD SoCs, please refer to the project documentation or contact the development team.
