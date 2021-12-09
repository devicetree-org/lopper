Domain Specific YAML Simplifications
====================================

For simplicity and ease of use, System Device Tree comes with an
alternative representation in YAML, see simplified-yaml.md.

This document introduces further simplifications for the YAML
representation of System Device Tree domains (/domains).


Hierarchy
---------

Domains are under /domains.

All domains, even nested domains, are specified under a "domain" key.


Access
------

The access property of domain nodes is specified with the following key:
value pairs:

- dev: device reference
- flags: flags


Example:

~~~
  access:
      - dev: serial0
        flags: {read-only: true}
~~~


Memory and Sram
---------------

The memory and sram properties to specify the memory and sram
allocations to a domain are specified in YAML using start and size key:
value pairs to increase readability.

Example:

~~~
  sram:
      - start: 0xfffc0000
        size: 0x1000
        flags: {read-only: true}
~~~


Cpus
----

The cpus property of domain nodes is specified with the following key:
value pairs:

- cluster: cpu cluster reference
- cpumask: cpumask in hex
- mode: unordered key: value pairs specifying the cpu mode
    - secure: true/false
    - el: the execution level


Example:

~~~
  cpus:
      - cluster: cpus_a72
        cpumask: 0x3
        mode:
            secure: true
            el: 0x3
~~~


Flags
-----

In YAML the following simplifications are used for access, memory, and
sram flags definitions and usage:

- To define flags  use key: value pairs

- When defining flags values, give individual flags setting a name
  rather than just a number, e.g. use read-only instead of (1<<2). The
  name and corresponding numeric values should be specified in lopper.

- no \*-flags-cells

~~~

  access:
      - dev: can0
        flags: {requested: true, read-only: true}

~~~


Bus Firewalls
-------------

In YAML the following simplifications are used to represent firewallconf
and firewallconf-default:

- no "block-desireable", instead use the priority number directly as
  value of the block key

- no "allow", instead use "never" as value of the block key

- no "firewallconf-default" property, instead use firewallconf with a
  single value and no domain references


Example:

~~~

  firewallconf:
    - domain: bm1
      block: 10
    - domain: bm2
      block: never
    - block: 5

~~~


Full Example
------------

~~~
domains:
    xen:
        compatible: openamp,domain-v1

        id: 0xffff
        cpus:
            - cluster: cpus_a72
              cpumask: 0x3
              mode:
                  secure: false
                  el: 0x2
        memory:
            - start: 0x500000
              size: 0x7fb00000

        access:
            - dev: serial0
              flags: { xen-flag-example1: true }
            - dev: mmc0
              flags: { xen-flag-example1: true }

        domains:
            linux1:
                compatible: openamp,domain-v1

                id: 0x0
                cpus:
                    - cluster: cpus_a72
                      cpumask: 0x3
                      mode:
                          secure: false
                          el: 0x1
                memory:
                    - size: 1G
                access:
                    - dev: mmc0
                sram:
                    - start: 0xfffc0000
                      size: 0x1000
                      flags: { read-only: true }
                firewallconf:
                    domain: bm1
                    block: 0x12

            bm1:
                compatible: openamp,domain-v1

                id: 0x1
                cpus:
                    - cluster: cpus_a72
                      cpumask: 0x3
                      mode:
                          secure: false
                          el: 0x1
                memory:
                    - size: 512M
                access:
                    - dev: ethernet0
                firewallconf:
                    domain: linux1
                    block: always

domains:
    freertos1:
        compatible: openamp,domain-v1

        id: 0x5
        cpus:
            - cluster: cpus_r5
              cpumask: 0x3
              mode: {secure: true, el: 1}
        memory:
            - size: 2M
        access:
            - dev: can0

    bm2:
        compatible: openamp,domain-v1

        id: 0x6
        cpus:
            - cluster: microblaze0
              cpumask: 0x1
              mode: {}
        memory:
            - size: 1M
        access:
            - dev: serial1
        sram:
            - start: 0xfffc0000
              size: 0x1000
              flags: { read-only: true }
~~~
