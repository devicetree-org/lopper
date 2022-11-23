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

.. code-block:: dts

   #access-implicit-default-cells = <1>;
   access-implicit-default = <0xff00ff>;
   #access-flags-cells = <0x0>;
   access = <&mmc0>;

YAML Example:

.. code-block:: yaml

    access-implicit-default: {secure: true, allow-secure: true, requested: true, coherent: false, virtualized: true, qos:99}
