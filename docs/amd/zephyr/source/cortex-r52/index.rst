Cortex-R52 Zephyr Device-Tree Generation
========================================

Overview
--------

Cortex-R52 Zephyr-specific device-tree generation follows a multi-step process to transform a system device-tree into a Zephyr-compatible format. In the first step, we generate a domain-specific device-tree from the system device-tree while handling the complex interrupt controller configurations specific to heterogeneous AMD platforms. In the second step, we generate the Zephyr-specific device-tree from the domain-specific device-tree created in step one.

Generation Process
------------------

The complete process consists of two main steps:

Step 1: Generate Domain-Specific Device-Tree with Interrupt Controller Handling
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

This initial step processes the system device-tree and generates a processor/domain-specific device-tree while addressing the complex interrupt controller architecture in heterogeneous AMD platforms.

**Purpose**: Extract and configure Cortex-R52 processor-specific components from the system device-tree and handle multiple GIC (Generic Interrupt Controller) configurations.

**Platform-Specific Interrupt Controller Handling**:

In heterogeneous AMD platforms, there are multiple GICs:
- **One GIC for APU** (Application Processing Unit)
- **One GIC for RPU** (Real-time Processing Unit)

The system device-tree represents these multiple GICs as an interrupt-multiplex node. However, the target processor device-tree expects only one processor-specific GIC. This step:

1. **Prunes the interrupt-multiplex node** from the device-tree
2. **Maintains a single GIC node** specific to the target Cortex-R52 processor
3. **Updates interrupt parent properties** in peripheral nodes to reference the correct GIC

**Input Parameters**:

- ``{workspace}``: Output directory for generated files
- ``lop-r52-imux.dts``: Configuration file that handles interrupt-multiplex node processing and GIC simplification
- ``{sdt}``: System device-tree input file (points to the ``system-top.dts`` file)
- ``{proc}``: Processor name (platform-specific):

  - **Versal Gen 2 platform**: ``cortexr52_0``
  - **Versal Net platform**: ``psx_cortexr52_0``

**Output**: ``system-domain.dts`` - Domain-specific device-tree file with simplified interrupt controller configuration

**Command**:
::

    lopper -f --enhanced -O {workspace} -i lop-r52-imux.dts {sdt} {workspace}/system-domain.dts -- gen_domain_dts {proc}

Step 2: Generate Zephyr-Specific Device-Tree
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

This step transforms the domain-specific device-tree from Step 1 into a Zephyr-compatible device-tree format.

**Purpose**: Convert the domain-specific device-tree into Zephyr RTOS compatible format with appropriate bindings and configurations.

**Input Parameters**:

- ``{workspace}``: Output directory for generated files
- ``system-domain.dts``: Domain-specific device-tree file generated in Step 1
- ``{proc}``: Processor name (refer to the processor name in the ``pl.dtsi`` file - this varies based on the processor configuration in the Vivado design)
**Optional**: - ``{zephyr_board_dts}``: Zephyr board-specific device-tree configuration. When provided, it will be compared against the domain-specific device-tree and removes unneeded nodes as per design configuration or mapping requirements

**Output**: ``system-zephyr.dts`` - Zephyr-specific device-tree file

**Command**:
::

    lopper -f --enhanced -O {workspace} {workspace}/system-domain.dts {workspace}/system-zephyr.dts -- gen_domain_dts {proc} zephyr_dt {zephyr_board_dts}

Platform-Specific Processor Names
---------------------------------

+----------------+--------------------+
| Platform       | Processor Name     |
+================+====================+
| Versal Gen 2   | ``cortexr52_0``    |
+----------------+--------------------+
| Versal Net     | ``psx_cortexr52_0`` |
+----------------+--------------------+

Prerequisites
-------------

Before running these commands, ensure you have:

1. **System Device-Tree**: Generated from Vivado design using SDTGen tool, with access to ``system-top.dts``
2. **Platform Identification**: Know whether you're targeting Versal Gen 2 or Versal Net platform
3. **Lopper Tool**: Installed and accessible in your environment
4. **Configuration Files**: Required ``.dts`` configuration files:

   - ``lop-r52-imux.dts``

5. **Processor Information**: Processor name from the ``pl.dtsi`` file
6. **Zephyr Board Configuration**: Appropriate board-specific device-tree file

Example Workflow
----------------

For Versal Gen 2 Platform:
~~~~~~~~~~~~~~~~~~~~~~~~~~

Basic Workflow (without board-specific device-tree):
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

::

    # Step 1: Generate domain-specific device-tree with interrupt handling
    lopper -f --enhanced -O ./output -i lop-r52-imux.dts system-top.dts ./output/system-domain.dts -- gen_domain_dts cortexr52_0

    # Step 2: Generate Zephyr-specific device-tree
    lopper -f --enhanced -O ./output ./output/system-domain.dts ./output/system-zephyr.dts -- gen_domain_dts cortexr52_0 zephyr_dt

Advanced Workflow (with board-specific device-tree optimization):
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

::

    # Step 1: Generate domain-specific device-tree with interrupt handling
    lopper -f --enhanced -O ./output -i lop-r52-imux.dts system-top.dts ./output/system-domain.dts -- gen_domain_dts cortexr52_0

    # Step 2: Generate Zephyr-specific device-tree with board optimization
    lopper -f --enhanced -O ./output ./output/system-domain.dts ./output/system-zephyr.dts -- gen_domain_dts cortexr52_0 zephyr_dt board.dts

For Versal Net Platform:
~~~~~~~~~~~~~~~~~~~~~~~~

Basic Workflow (without board-specific device-tree):
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

::

    # Step 1: Generate domain-specific device-tree with interrupt handling
    lopper -f --enhanced -O ./output -i lop-r52-imux.dts system-top.dts ./output/system-domain.dts -- gen_domain_dts psx_cortexr52_0

    # Step 2: Generate Zephyr-specific device-tree
    lopper -f --enhanced -O ./output ./output/system-domain.dts ./output/system-zephyr.dts -- gen_domain_dts psx_cortexr52_0 zephyr_dt

Advanced Workflow (with board-specific device-tree optimization):
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

::

    # Step 1: Generate domain-specific device-tree with interrupt handling
    lopper -f --enhanced -O ./output -i lop-r52-imux.dts system-top.dts ./output/system-domain.dts -- gen_domain_dts psx_cortexr52_0

    # Step 2: Generate Zephyr-specific device-tree with board optimization
    lopper -f --enhanced -O ./output ./output/system-domain.dts ./output/system-zephyr.dts -- gen_domain_dts psx_cortexr52_0 zephyr_dt board.dts

Output Files
------------

After completing both steps, you will have:

- ``system-domain.dts``: Cortex-R52 domain-specific device-tree with simplified interrupt controller configuration
- ``system-zephyr.dts``: Zephyr-compatible device-tree ready for integration

Key Features
------------

**Interrupt Controller Optimization**:
- Simplifies complex multi-GIC architecture for single-processor compatibility
- Ensures proper interrupt routing for Cortex-R52 processor
- Maintains peripheral interrupt parent relationships
- Handles heterogeneous platform interrupt multiplexing

**Platform Support**:
- Compatible with both Versal Gen 2 and Versal Net platforms
- Handles platform-specific processor naming conventions
- Optimized for real-time Cortex-R52 architecture

Troubleshooting
---------------

**Common Issues**:

1. **Platform Identification**: Ensure you're using the correct processor name for your target platform
2. **Missing Configuration Files**: Verify ``lop-r52-imux.dts`` is available and accessible
3. **System Device-Tree Path**: Confirm the path to ``system-top.dts`` is correct
4. **Interrupt Controller Issues**: Check that the original system device-tree contains the expected interrupt-multiplex nodes
5. **Output Directory**: Verify the workspace directory exists and has write permissions

**Verification**:
- Check that the generated ``system-domain.dts`` contains simplified GIC configuration
- Verify that ``system-zephyr.dts`` is compatible with Zephyr device-tree format
- Ensure peripheral nodes have correct interrupt parent references
- Validate that Cortex-R52 specific configurations are properly maintained

**Next Steps**: After generating the device-tree files, integrate them into your Zephyr project following the Zephyr documentation guidelines for Cortex-R52 platforms.
