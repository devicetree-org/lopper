#/*
# * Copyright (c) 2019,2020 Xilinx Inc. All rights reserved.
# *
# * Author:
# *       Bruce Ashfield <bruce.ashfield@xilinx.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

import struct
import sys
import types
import unittest
import os
import getopt
import re
import subprocess
import shutil
from pathlib import Path
from pathlib import PurePath
from io import StringIO
import contextlib
import importlib
from lopper import Lopper
import lopper
from lopper_tree import *

sys.path.append(os.path.dirname(__file__))
from xlnx_versal_power import *

def props():
    return ["id", "file_ext"]

def id():
    return "xlnx,output,cdo"

def file_ext():
    return ".cdo"

def is_compat( node, compat_id ):
    if re.search( "xlnx,output,cdo", compat_id):
        return cdo_write
    return ""

current_subsystem_id = 0x1c000003
subsystems = {}

def add_subsystem(domain_node, sdt, output):

  global current_subsystem_id
  global subsystems

  # read cpus property
  if domain_node.propval("cpus") == [""]:
    print("ERROR: add_subsystem: ",str(domain_node), "missing cpus property.")
    return -1
  else:
    cpu_node = sdt.tree.pnode(domain_node.propval("cpus")[0])
    if cpu_node == None:
      print("add_subsystem: could not find corresponding core node")
      return -1

    subsystem_num = current_subsystem_id & 0xF

    # if cpus_a72, then APU subsystem
    if "a72" in cpu_node.name or "r5" in cpu_node.name:

      print("# subsystem_"+str(subsystem_num), file=output)
      print("pm_add_subsystem "+ hex(current_subsystem_id), file=output)
      subsystems[domain_node.name] = current_subsystem_id
      current_subsystem_id += 1
    else:
      print("ERROR: add_subsystem: cpu  not supported ",str(cpu_node))
      return -1

  # cpu_node can be used later, as some
  # subsystems will have hard-coded requirements
  return cpu_node


def cdo_write_command(sub_num, sub_id, dev_str, dev_val, flag1, flag2, output):
  print("# subsystem_"+str(sub_num)+" "+dev_str,file=output)
  print("pm_add_requirement "+hex(sub_id)+" "+dev_val+" "+hex(flag1)+" "+hex(flag2),file=output)

def add_subsystem_permission_requirement(output, cpu_node, domain_node, device_node, target_pnode, operation):
  root_node = domain_node.tree['/']
  subsystem_id = subsystems[domain_node.name]
  subsystem_num = subsystem_id & 0xF

  target_domain_node = root_node.tree.pnode(target_pnode)
  target_cpu_node_phandle = target_domain_node.propval("cpus")[0]

  target_cpu_node = root_node.tree.pnode(target_cpu_node_phandle)
  target_sub_id = subsystems[target_domain_node.name]
  target_sub_num = target_sub_id & 0xF

  cdo_comment = "# subsystem_"+str(subsystem_num)+ " can enact "
  if operation > 7:
    cdo_comment += "secure and "
  cdo_comment += "non-secure ops upon subsystem_" + str(target_sub_num)
  cdo_cmd = "pm_add_requirement "+hex(subsystem_id)+" "+hex(target_sub_id)+" "+hex(operation)

  print(cdo_comment,file=output)
  print(cdo_cmd,file=output)


# add requirements that link devices to subsystems
def add_requirements(domain_node, cpu_node, sdt, output):
  subsystem_id = subsystems[domain_node.name]
  subsystem_num = subsystem_id & 0xF

  if "a72" in cpu_node.name:
    cdo_write_command(subsystem_num, subsystem_id,"dev_l2_bank_0",hex(existing_devices["dev_l2_bank_0"]),0x4, 0xfffff, output)
    cdo_write_command(subsystem_num, subsystem_id,"dev_ams_root",hex(existing_devices["dev_ams_root"]),0x4, 0xfffff, output)

  # here parse the subsystem for device requirements
  device_list = domain_node.propval("xilinx,subsystem-config")
  num_params = domain_node.propval("#xilinx,config-cells")[0]

  root_node = domain_node.tree['/']

  for index,device_phandle in enumerate(device_list):
    if index % 3 != 0:
      continue
    device_node = root_node.tree.pnode(device_phandle)
    if device_node == None:
      print("ERROR: add_requirements: invalid phandle: ",str(device_phandle), index, device_node)
      return -1

    # there are multiple cases to handle
    if "cpu" in device_node.name:
      if "a72" in device_node.abs_path:
        key = "dev_acpu_" + device_node.name[len(device_node.name)-1]
      elif "r5" in  device_node.abs_path:
        key = "dev_rpu0_"
        if (domain_node.propval("cpus")[1] & 0x1) == 1:
          # if cpus second arg has rightmost bit on, then this is either lockstep or r5-0
          key += "0"
        else:
          key += "1"
      else:
        print("add_requirements: cores: not covered: ",str(device_node))
        return -1
    elif "domain" in device_node.name:
      add_subsystem_permission_requirement(output, cpu_node, domain_node, device_node,
                                           device_list[index], device_list[index+1])
      continue
    elif device_node.propval("power-domains") != [""]:
      cdo_write_command(subsystem_num, subsystem_id, 
                        xilinx_versal_device_names[device_node.propval("power-domains")[1]],
                        hex(device_node.propval("power-domains")[1]),
                        device_list[index+1],device_list[index+2],output)
      continue
    elif "mailbox" in device_node.name:
      key = mailbox_devices[device_node.name]
    elif "memory" in device_node.name or "tcm" in device_node.name :
      if 0xfffc0000 == device_node.propval("reg")[1]:
        for key in ocm_bank_names:
          cdo_write_command(subsystem_num, subsystem_id, key, hex(existing_devices[key]),
                            device_list[index+1],device_list[index+2],output)

      elif device_node.propval("reg")[1] in memory_range_to_dev_name.keys():
        key = memory_range_to_dev_name[device_node.propval("reg")[1]]
      else:
        print("add_requirements: memory: not covered: ",str(device_node),hex(device_node.propval("reg")[1]))
        return -1
    else:
      print("add_requirements: not covered: ",str(device_node))
      return -1
    cdo_write_command(subsystem_num, subsystem_id, key, hex(existing_devices[key]),
                      device_list[index+1],device_list[index+2],output)


  return 0

def cdo_write( domain_node, sdt, options ):
    try:
        verbose = options['verbose']
    except:
        verbose = 0

    if (len(options["args"]) > 0):
      outfile = options["args"][0]
    else:
      print("cdo header file name not provided.")
      return -1

    # todo: we could have a force flag and not overwrite this if it exists
    if outfile != sys.stdout:
        output = open( outfile, "w")
    else:
      print("stdout provided as outfile")

    if verbose > 1:
        print( "[INFO]: cdo write: {}".format(outfile) )

    print( "# Lopper CDO export", file=output )
    print( "version 2.0", file=output )

    # given root domain node do the following:
    # add subsystem
    domain_nodes = []
    cpu_nodes = []
    for n in domain_node.subnodes():
      if n.propval('xilinx,subsystem-config') != ['']:
        cpu_node = add_subsystem(n, sdt, output)
        cpu_nodes.append(cpu_node)
        domain_nodes.append(n)
    # only parse requirements after all subsystems are loade
    for i in range(len(domain_nodes)):
      add_requirements(domain_nodes[i], cpu_nodes[i], sdt, output)

    return True



