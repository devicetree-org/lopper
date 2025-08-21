====================================================
Introduction
====================================================

This document provides an overview of the process for generating Linux device trees and DTB overlay files for AMD SOCs using the Lopper tool.
The linux device tree generation process is typically performed after the hardware design is completed in Vivado and the system device tree (SDT) is generated using SDTGen.
The DTB overlay file facilitates dynamic FPGA reconfiguration after the linux system is brought up.


More info on SDTGen and hardware handoff file can be found in the `SDTGen README <https://github.com/Xilinx/system-device-tree-xlnx/blob/xlnx_rel_v2025.1/README.md>`_.


The next chapter covers the process of generating Linux device trees for different AMD SOCs.

Chapter 3 covers the process of generating DTB overlay files. Users working with DFX and segmented flow solutions will find this chapter particularly relevant.

More info on DFX configurations can be found in the `DFX User Guide <https://docs.amd.com/r/en-US/ug909-vivado-partial-reconfiguration>`_.

For segmented flow solutions, refer to the Segmented Flow details `here <https://docs.amd.com/r/en-US/ug1273-versal-acap-design/Segmented-Configuration>`_.