os,type
=======

Execution domains can have an optional "os,type" property to capture one
or more operating systems that may run on the domain. The field may be
used by automated tooling for activities, such as verifying that the
domain is capable of running the operating system, configuring a build
system to produce the proper operating system, configure a storage
mechanism to include the specified operating system, or other purposes.

The value of "os,type" is a string defined in the format:

	OS_TYPE[,TYPE_ID[,TYPE_ID_VERSION]]

OS\_TYPE is mandatory and explains what the type of the operating system
will be. The values for this are defined as follows. In order to add
additional types, the specification should be updated.

	OS_TYPE:
	   baremetal
	   linux
	   freertos
	   zephyr
	   custom
	   x-<vendor>[-os]

*baremetal* refers to a direct application that executes on the system
with no conventional operating system. Examples of this may include
first stage boot loader, second stage boot loader, u-boot,
arm-trusted-firmware, etc.

*linux* refers to a Linux based operating system. Examples of this may
include Yocto Project derived, Red Hat, Ubuntu, etc.

*freertos* refers to FreeRTOS real-time operating system.

*zephyr* refers to Zephyr operating system.

*custom* refers to a user specific operating system. Custom is to be
used only by the group providing the custom implementation. Each usage
of custom will be different.

*x-\<vendor\>[-os]* refers to an extension of a non-registered vendor
specific operating system.  The 'x' refers to extension, which is
attempts to avoid namespace collisions by convention. At a minimum the
name space must be x-\<vendor\>, such as x-xilinx.  However, the vendor
name may not be a specific enough namespace to avoid collision, so an
optional "-os" is allowed as well.  The \<vendor\> controls the
namespace of "os" values, if they are used.  For instance Wind River
VxWorks could be specified using: x-windriver-vxworks.

It is recommended that a vendor register their operating system in the
official named list, only using the extension format until it is
official.

The *TYPE_ID* is specific to each OS\_TYPE, but is not currently
formalized. The purpose of this is to further clarify details on the
OS\_TYPE if desired. For instance, to specify Ubuntu Linux, use:
linux,ubuntu

As *TYPE_ID* is not yet formalized it is open for different usages by
different parties. It's recommended that groups work together to define
common values where appropriate.

The *TYPE_ID_VERSION* is an optional parameter that is allowed, only if
the TYPE\_ID is used, and it's purpose is to specify the version of the
TYPE\_ID.  In the prior example of "linux,ubuntu", it may be specified
"linux,ubuntu,18.04".

As with *TYPE_ID*, this may be open to namespace collisions, and is
again recommended that groups work together to define common values
where appropriate.


Examples:

	os,type = "linux"
	
	os,type = "linux,ubuntu,18.04"
	
	os.type = "linux,ubuntu,18.04.01"
	
	os,type = "linux,yocto"
	
	os,type = "linux,yocto,gatesgarth"
	
	os.type = "baremetal"
	
	os.type = "baremetal,fsbl"
	
	os.type = "baremetal,newlib,3.3.0"

