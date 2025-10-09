Microblaze RISC-V Zephyr Device-Tree Generation
===============================================

Overview
--------

Microblaze RISC-V Zephyr-specific device-tree generation is a multi-step process that transforms a system device-tree into a Zephyr-compatible format. This process involves generating domain-specific device-trees, creating Zephyr-specific configurations, and handling processor-specific requirements for interrupt controllers.

Generation Process
------------------

The complete process consists of three main steps:

Step 1: Generate Domain-Specific Device-Tree
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

This initial step processes the system device-tree and generates a processor/domain-specific device-tree tailored for the target processor.

**Purpose**: Extract and configure processor-specific components from the system device-tree.

**Input Parameters**:

- ``{workspace}``: Output directory for generated files
- ``lop-microblaze-riscv.dts``: Hardware configuration file containing tuning flags in YAML format for the assist
- ``{sdt}``: System device-tree input file
- ``{proc}``: Processor name (refer to the processor name in the ``pl.dtsi`` file - this varies based on the processor configuration in the Vivado design)

**Output**: ``system-domain.dts`` - Domain-specific device-tree file

**Command**:
::

    lopper -f --enhanced -O {workspace} -i lop-microblaze-riscv.dts {sdt} {workspace}/system-domain.dts -- gen_domain_dts {proc}

Step 2: Generate Zephyr-Specific Device-Tree
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

This step transforms the domain-specific device-tree from Step 1 into a Zephyr-compatible device-tree format.

**Purpose**: Convert the domain-specific device-tree into Zephyr RTOS compatible format with appropriate bindings and configurations.

**Input Parameters**:

- ``{workspace}``: Output directory for generated files
- ``lop-microblaze-riscv.dts``: Hardware configuration file containing tuning flags in YAML format
- ``system-domain.dts``: Domain-specific device-tree file generated in Step 1
- ``{proc}``: Processor name (same as Step 1)
**Optional**: - ``{zephyr_board_dts}``: Zephyr board-specific device-tree configuration. When provided, it will be compared against the domain-specific device-tree and removes unneeded nodes as per design configuration or mapping requirements

**Output**: ``system-zephyr.dts`` - Zephyr-specific device-tree file

**Command**:
::

    lopper -f --enhanced -O {workspace} -i lop-microblaze-riscv.dts {workspace}/system-domain.dts {workspace}/system-zephyr.dts -- gen_domain_dts {proc} zephyr_dt {zephyr_board_dts}

Step 3: Generate MBV32 Generic Board-Specific Device-Tree
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

This final step addresses a specific requirement for RISC-V code compatibility by adding interrupt controller configurations.

**Purpose**: Handle the architectural difference where RISC-V code expects the interrupt controller to be part of the processor node, while AMD Microblaze RISC-V designs place the interrupt controller outside the processor. This step adds a dummy interrupt controller node that references the actual interrupt controller in the design.

**Input Parameters**:

- ``{workspace}``: Output directory for generated files
- ``lop-mbv-zephyr-intc.dts``: Configuration file that generates interrupt controller node under the processor
- ``system-zephyr.dts``: Zephyr-specific device-tree file from Step 2

**Output**: ``mbv32.dts`` - Zephyr MBV32 generic board device-tree

**Command**:
::

    lopper -f --enhanced -O {workspace} -i lop-mbv-zephyr-intc.dts {workspace}/system-zephyr.dts {workspace}/mbv32.dts

Prerequisites
-------------

Before running these commands, ensure you have:

1. **System Device-Tree**: Generated from Vivado design using SDTGen tool
2. **Processor Name**: Identified from the ``pl.dtsi`` file in your design
3. **Lopper Tool**: Installed and accessible in your environment
4. **Configuration Files**: Required ``.dts`` configuration files:

   - ``lop-microblaze-riscv.dts``
   - ``lop-mbv-zephyr-intc.dts``

Example Workflow
----------------

Basic Workflow (without board-specific device-tree):
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

::

    # Step 1: Generate domain-specific device-tree
    lopper -f --enhanced -O ./output -i lop-microblaze-riscv.dts system.dts ./output/system-domain.dts -- gen_domain_dts microblaze_0

    # Step 2: Generate Zephyr-specific device-tree  
    lopper -f --enhanced -O ./output -i lop-microblaze-riscv.dts ./output/system-domain.dts ./output/system-zephyr.dts -- gen_domain_dts microblaze_0 zephyr_dt

    # Step 3: Generate MBV32 board-specific device-tree
    lopper -f --enhanced -O ./output -i lop-mbv-zephyr-intc.dts ./output/system-zephyr.dts ./output/mbv32.dts

Advanced Workflow (with board-specific device-tree optimization):
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

::

    # Step 1: Generate domain-specific device-tree
    lopper -f --enhanced -O ./output -i lop-microblaze-riscv.dts system.dts ./output/system-domain.dts -- gen_domain_dts microblaze_0

    # Step 2: Generate Zephyr-specific device-tree with board optimization
    lopper -f --enhanced -O ./output -i lop-microblaze-riscv.dts ./output/system-domain.dts ./output/system-zephyr.dts -- gen_domain_dts microblaze_0 zephyr_dt board.dts

    # Step 3: Generate MBV32 board-specific device-tree
    lopper -f --enhanced -O ./output -i lop-mbv-zephyr-intc.dts ./output/system-zephyr.dts ./output/mbv32.dts

Output Files
------------

After completing all three steps, you will have:

- ``system-domain.dts``: Domain-specific device-tree
- ``system-zephyr.dts``: Zephyr-compatible device-tree
- ``mbv32.dts``: Final board-specific device-tree for Zephyr

Known Issues
------------

**Design Support Limitations**:

Currently, the Microblaze RISC-V Zephyr device-tree generation supports only **pure PL (Programmable Logic) based designs**.

**Unsupported Configurations**:
- **PS + PL hybrid designs**: Designs that combine PS (Processing System) processors with PL Microblaze processors are not currently supported
- **Mixed processor architectures**: Systems that include both PS processors (e.g., Cortex-A78, Cortex-R52) and PL-based Microblaze RISC-V processors in the same design

**Impact**:
If your design includes both PS and PL processors, the device-tree generation process may not produce correct results or may fail entirely. For such designs, consider:
- Implementing pure PL-based designs for Microblaze RISC-V targets

**Future Support**: Support for PS + PL hybrid designs may be added in future releases.

Troubleshooting
---------------

**Common Issues**:

1. **Processor Name Mismatch**: Verify the processor name in your ``pl.dtsi`` file matches the ``{proc}`` parameter
2. **Missing Configuration Files**: Ensure all required ``.dts`` configuration files are available
3. **Output Directory**: Verify the workspace directory exists and has write permissions

**Next Steps**: After generating the device-tree files, integrate them into your Zephyr project following the Zephyr documentation guidelines.
