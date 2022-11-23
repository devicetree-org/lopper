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

.. code-block:: dts

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

CPU clusters have an optional property secure-address-map which allows
to specify the address map of the CPU cluster, including the execution
mode. The format of secure-address-map is similar to address-map,
but with one additional cell: the first cell specifies the execute mode
in the same format of secure-bus. Example:

.. code-block:: dts

   /* additional R5 cluster */
   cpus_r5: cpus-cluster@0 {
           compatible = "cpus,cluster";

           /* first cell: execution mode. 0x1 means "secure world" */
           secure-address-map = <0x1 0x1 0xf9000000 &amba_rpu 0x1 0xf9000000 0x0 0x10000>;
   };
