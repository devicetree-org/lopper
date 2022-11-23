Introduction
============

System Device Trees extends traditional Device Trees to handle
heterogeneous SoCs with multiple CPUs and Execution Domains. An
Execution Domain can be seen as an address space that is running a
software image, whether an operating system, a hypervisor or firmware
that has a set of cpus, memory and devices attached to it. I.e. Each
individual CPU/core that is not part of an SMP cluster is a separate
Execution Domain as is the different Execution Levels on an ARMv8-A
architecture. Trusted and not trusted environment can also be viewed as
separate Execution Domains.

A design goal of System Device Trees is that no current client of Device
Trees should have to change at all, unless it wants to take advantage of
the extra information. This means that Linux in particular does not need
to change since it will see a Device Tree that it can handle with the
current implementation, potentially with some extra information it can
ignore.

System Device Trees must handle two types of heterogeneous additions:

1. Being able to specify different cpu clusters and the actual memory
   and devices hard-wired to them

    - This is done through the new Hardware Descriptions, such as
      "cpu,cluster" and "indirect-bus"
    - This information is provided by the SoC vendor and is typically
      fixed for a given SoC/board

2. Being able to assign hardware resources that can be configured by
   software to be used by one or more Execution Domains

    - This is done through the Execution Domain configuration
    - This information is provided by a System Architect and will be
      different for different use cases, even for the same board

        - E.g. How much memory and which devices goes to Linux vs. an
          RTOS can be different from one boot to another

    - This information should be separated from the hard-wired
      information for two reasons

        - A different persona will add and edit the information
        - Configuration should be separated from specification since it
          has a different rate of change

The System Device Trees and Execution Domain information are used in two
major use cases:

1. Exclusively on the host by using a tool like Lopper that will "prune"
   the System Device Tree

    - Each domain will get its own "traditional" Device Tree that only
      sees one address space and has one "cpus" node, etc.
    - Lopper has pluggable backends to it can also generate information
      for clients that is using a different format

        - E.g. It can generate a bunch of "#defines" that can be
          included and compiled in to an RTOS
2. System Device Trees can be used by a "master" target environment that
   manages multiple Execution Domains:

    - a firmware that can set up hardware protection and use it to
      restart individual domains

        - E.g. Protect the Linux memory so the R5 OS can't reach it

    - any other operating system or hypervisor that has sub-domains

        - E.g. Xen can use the Execution Domains to get info about the Xen
          guests (also called domains)
        - E.g. Linux could use the default domain for its own
          configuration and the domains to manage other CPUs
        - Since System Device Trees are backwards compatible with Device
          Trees, the only changes needed in Linux would be any new code
          taking advantage of the Domain information
        - a default master has access to all resources (CPUs, memories,
          devices), it has to make sure it stops using the resource
          itself when it "gives it away" to a sub-domain

There is a concept of a default Execution Domain in System Device Trees,
which corresponds to /cpus. The default domain is compatible with the
current traditional Device Tree. It is useful for a couple of reasons:

1. As a way to specify the default place to assign added hardware (see
   use case #1)

    - A default domain does not have to list the all the HW resources
      allocated to it. It gets everything not allocated elsewhere by
      Lopper.
    - This minimizes the amount of information needed in the Domain
      configuration.
    - This is also useful for dynamic hardware such as add-on boards and
      FPGA images that are adding new devices.

2. The default domain can be used to specify what a master environment
   sees (see use case #2)

    - E.g. the default domain is what is configuring Linux or Xen, while
      the other domains specify domains to be managed by the master
