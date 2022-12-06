Introduction
============

Purpose and Scope
-----------------

This document, the System Devicetree Specification, extends the
Devicetree Specification to handle heterogeneous SoCs with
multiple CPUs, possibly of different architectures, as well as the
*execution domains* running on the CPUs.

An execution domain can be seen as an address space that is running a
software image, whether an operating system, a hypervisor, or firmware
that has a set of CPUs, memory and devices attached to it.

Relationship to the Devicetree Specification
--------------------------------------------

The System Devicetree Specification is an extension of the Devicetree
Specification [DTSpec]_. A system devicetree is written in the DTS
format defined by the Devicetree Specification, but contains extra
information and enhanced semantics in order to address the use cases
introduced above.

This document uses the terms *base specification* to refer to the
Devicetree Specification, and *standard devicetree* to refer to a
devicetree that complies with the base specification and does not
include any of the extensions defined in the System Devicetree
Specification.

A design goal of this specification is that it should be possible to
adopt it in ways that do not require existing devicetree clients to
change, while also allowing clients that are aware of this specification
to take advantage of the extra information present in a system
devicetree. In particular, Linux's [Linux]_ devicetree implementation
will not require changes as a result of this document, since a running
Linux kernel will be provided with a DTB that it can handle with
the current implementation, potentially with some extra information it
can ignore.

Summary of Extensions
---------------------

This document defines the following main extensions to the base
specification:

1. Additional *bindings* for describing multiple distinct CPU clusters
   in a single heterogeneous SoC, as well as the memories and devices
   connected to them.

   This information is usually provided by the SoC vendor, and
   is typically fixed for a given SoC.

2. Additional *nodes* which define the execution domains running on the
   SoC and assign hardware resources to them. This is done through a new
   node, ``/domains``, and additional bindings related to it.

   This information is usually provided by the board designer or another
   user of the SoC, and typically differs by use case. For example, the
   memory allocated to a general purpose operating system and an RTOS
   running on separate CPU cores on an SoC can be described via this
   node. This allocation may differ across designs based on the SoC, or
   between boots on the same design.

.. _usage-environments:

Usage Environments
------------------

The concepts defined in this specification are intended to be used in
two main environments:

1. Exclusively on the host system in a cross-compilation development
   environment targeting a heterogeneous SoC as the target device.

   In this use case, a tool like Lopper [Lopper]_ running on the host
   converts the system devicetree into one or more standard devicetrees.
   Using Lopper, a standard devicetree can be created for each execution
   domain, with a single address space, one ``/cpus`` node instead of
   multiple CPU cluster nodes, etc. Lopper also has pluggable backends,
   so it can also generate information derived from the devicetree in
   other formats, such as a C header file defining macros that can be
   included and compiled in to an RTOS.

2. In a "master" target environment that manages multiple execution
   domains.

   Such an environment typically has access to all hardware resources
   (CPUs, memories, devices, etc.) on the SoC. It will typically assign
   these resources to the other execution domains it manages, then
   prevent itself from accessing them.

   An example of such a target environment is firmware running on the
   SoC may consume the system devicetree in order to set up hardware
   protection and use it to restart individual domains. For example, the
   firmware may protect a general purpose operating system domain's
   memory, so an RTOS running on different CPUs cannot access it.

   Other examples are other operating systems or hypervisors that
   manage execution domains:

     - A Xen hypervisor [Xen]_ can use ``/domains`` to get information
       about the Xen guests (also called domains)
     - A Linux kernel could use the default domain for its own
       configuration and other domains to manage additional CPUs on the
       SoC. Since system devicetrees are backwards compatible with
       standard devicetrees, the only changes needed in Linux would be
       any new code taking advantage of the information in ``/domains``.

Definition of Terms
-------------------

.. glossary::

   base specification
     The Devicetree Specification [DTSpec]_, which this document extends.

   binding
     Devicetree binding. See [DTSpec]_.

   DTS
     Devicetree syntax. See [DTSpec]_.

   DTB
     Devicetree blob. See [DTSpec]_.

   execution domain
     a collection of software, firmware, and board configurations that
     enable an operating system or an application to run a cpus cluster

   node
     Devicetree node. See [DTSpec]_.

   SoC
     System on chip.

   SMP
     Symmetric multiprocessing.

   standard devicetree
     A devicetree that complies with the base Devicetree Specification and
     does not include any of the extensions defined in the System Devicetree
     Specification.
