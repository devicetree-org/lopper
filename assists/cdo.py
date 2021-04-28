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
from cdo_topology import *

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

subsystems = {}


# db where key is node + subsystem and value is the requirement which contains
# flags
requirement_map = {}

def add_subsystem(domain_node, sdt, output):

  # read cpus property
  if domain_node.propval("cpus") == [""]:
    if "resource" in domain_node.name and "group" in domain_node.name:
      return 0
    print("ERROR: add_subsystem: ",str(domain_node), "missing cpus property.")
    return -1
  else:
    cpu_node = sdt.tree.pnode(domain_node.propval("cpus")[0])
    if cpu_node == None:
      print("add_subsystem: could not find corresponding core node")
      return -1

    current_subsystem_id = 0
    cpu_key = ""
    # based on APU, RPU domain, add subsystem CDO command
    if "a72" in cpu_node.name:
      cpu_key = "a72"
    elif "r5" in cpu_node.name:
      cpu_key = "r5_"
      if (domain_node.propval("cpus")[1] & 0x3) == 0x3:
        cpu_key += "lockstep"
      elif (domain_node.propval("cpus")[1] & 0x2) == 0x2:
        cpu_key += "1"
      elif (domain_node.propval("cpus")[1] & 0x1) == 0x1:
        cpu_key += "0"
      else:
        print ("add_subsystem: invalid cpu config for rpu: ",domain_node.propval("cpus"))
    else:
      print( "unsupported domain node ", domain_node)
      return -1

    subsystem_compat = False
    for compat_str in domain_node.propval("compatible"):
      if "xilinx,subsystem" in compat_str:
        subsystem_compat = True
        break
    if subsystem_compat == False:
      print("subsystem missing compat string.")
      return -1

    if domain_node.propval("id") == [""]:
      print("domain does not have subsystem ID")
      return -1
    # For now, take in subsystem as 0x1 - 0xE ID. will update this later
    # TODO fix this
    subsystem_num = domain_node.propval("id")[0]
    if subsystem_num >= 0xF:
      print("invalid subsystem string for ", str(domain_node))
      return -1
    current_subsystem_id = 0x1C000000 + subsystem_num
    print("# subsystem_"+str(subsystem_num), file=output)
    print("pm_add_subsystem "+ hex(current_subsystem_id), file=output)
    subsystems[domain_node.name] = current_subsystem_id

  # cpu_node can be used later, as some
  # subsystems will have hard-coded requirements coming in from SDT
  return cpu_node

# write CDO requirement to file
def cdo_write_command(sub_num, sub_id, dev_str, dev_val, flags, output):
  print("# subsystem_"+str(sub_num)+" "+dev_str,file=output)
  print("pm_add_requirement "+hex(sub_id)+" "+dev_val+" "+hex(flags)+" "+hex(0xfffff),file=output)

# docment requirement bits
def validate_and_document_req_flags(subsystem_name, sub_num, sub_id, node_id, dev_str, output, flags, mem_regn):
  print("#", file=output)
  print("#", file=output)
  print("# subsystem:", file=output)
  print("#    name: "+ subsystem_name, file=output)
  print("#    ID: "+hex(sub_id), file=output)
  print("#", file=output)
  print("# node:", file=output)
  print("#    name: "+dev_str, file=output)
  print("#    ID: "+hex(node_id), file=output)
  print("#", file=output)
  print("# requirements: arg 1: "+hex(flags), file=output)

  print(usage(flags),       file=output)
  print(security(flags),    file=output)
  print(capability_policy(flags),    file=output)
  print(prealloc_policy(flags), file=output)


  if mem_regn == 1:
    print(read_policy(flags), file=output)
    print(write_policy(flags), file=output)
    print(nsregn_policy(flags),file=output)
  print("#", file=output)

  req = Requirement(sub_id, node_id,
                    ((flags & prealloc_mask) >> prealloc_offset),
                    ((flags & capability_mask) >> capability_offset),
                    ((flags & nsregn_check_mask) >> nsregn_check_offset),
                    ((flags & rd_policy_mask) >> rd_policy_offset),
                    ((flags & wr_policy_mask) >> wr_policy_offset),
                    ((flags & security_mask) >> security_offset),
                    (flags & usage_mask))
  key = (sub_id, node_id)
  # if there is already key report error
  if key in requirement_map:
    print ("ERROR: subsystem ",hex(sub_id)," has link with node ", hex(node_id) , "twice")
    return -1

  requirement_map[key] = req
  return 0

# subsystem to subsystem perimssions CDO requirement
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

def add_requirements_mem(subsystem_num, subsystem_id, output, device_node, domain_node, requirement):
  base_addr = device_node.propval("reg")[1]

  # check if base addr is in list of xilpm mem nodes 
  if 0xfffc0000 == base_addr:
    for key in ocm_bank_names:
      if -1 == validate_and_document_req_flags(domain_node.name, subsystem_num,subsystem_id,
                                         existing_devices[key], key, output, requirement, 1):
        return -1
      cdo_write_command(subsystem_num, subsystem_id, key, hex(existing_devices[key]), requirement,output)
    return 0
  elif base_addr in memory_range_to_dev_name.keys():
    key = memory_range_to_dev_name[base_addr]
    if -1 == validate_and_document_req_flags(domain_node.name, subsystem_num,subsystem_id,
                                       existing_devices[key], key, output, requirement,1):
      return -1
    cdo_write_command(subsystem_num, subsystem_id, key, hex(existing_devices[key]), requirement,output)
    return 0
  else:
    print("add_requirements: memory: not covered: ",str(device_node),hex(base_addr))
    return -1

# map device tree node to xilpm core node (r5-X, a72-x, etc)
def add_requirements_cpu(subsystem_num, subsystem_id, output, device_node, domain_node, requirement):
  # apu cores
  if "a72" in device_node.name:
    for key in apu_specific_reqs.keys():
      if -1 == validate_and_document_req_flags(domain_node.name, subsystem_num, subsystem_id, existing_devices[key], key, output, requirement, 0):
        return -1
      cdo_write_command(subsystem_num, subsystem_id, key, hex(existing_devices[key]), apu_specific_reqs[key], output)
    return 0
  elif "r5" in  device_node.abs_path:
    # r5 cores
    key = "dev_rpu0_"
    # based on spec, r5 configuration (r5-0, r5-1, lockstep) based on second arg
    if (domain_node.propval("cpus")[1] & 0x1) == 1:
      # if cpus second arg has rightmost bit on, then this is either lockstep or r5-0
      key += "0"
    else:
      key += "1"
    if -1 == validate_and_document_req_flags(domain_node.name, subsystem_num,subsystem_id, existing_devices[key], key, output, requirement, 0):
      return -1
    cdo_write_command(subsystem_num, subsystem_id, key, hex(existing_devices[key]), requirement, output)
    return 0
  else:
    print("add_requirements: cores: not covered: ",str(device_node))
    return -1

# add requirements that link devices to subsystems
def add_requirements_internal(domain_node, cpu_node, sdt, output, device_list, num_params):
  subsystem_id = subsystems[domain_node.name]
  subsystem_num = subsystem_id & 0xF
  root_node = domain_node.tree['/']

  # for now this is done with access list of syntax <dev reqs>
  # so here just iterate based on the dev arg
  for index,device_phandle in enumerate(device_list):
    if index % 2 != 0:
      continue
    device_node = root_node.tree.pnode(device_phandle)
    if device_node == None:
      print("ERROR: add_requirements: invalid phandle: ",str(device_phandle), index, device_node)
      return -1
    if "domain" in device_node.name: # subsystem - subsytem permissions
      add_subsystem_permission_requirement(output, cpu_node, domain_node, device_node, device_list[index], device_list[index+1])
      continue
    # there are multiple cases to handle
    # handle mem and core xilpm dev nodes
    elif "cpu" in device_node.name or "memory" in device_node.name or "tcm" in device_node.name or "_ram_" in device_node.name:
      if "cpu" in device_node.name:
        # core nodes
        ret = add_requirements_cpu(subsystem_num, subsystem_id, output, device_node, domain_node, device_list[index+1])
      else:
        # mem nodes
        ret = add_requirements_mem(subsystem_num, subsystem_id, output, device_node, domain_node, device_list[index+1])

      if ret == 0:
        continue
      else:
        return ret 
    # here is the 'cleanest' case
    # there exists a power-domains property in this node that has the xilpm ID
    # use this if present
    # then reverse lookup the xilpm node ID and get the name for documentation/debugging purposes
    elif device_node.propval("power-domains") != [""]:
      xilpm_nodeid = device_node.propval("power-domains")[1]
      xilpm_dev_name = xilinx_versal_device_names[xilpm_nodeid]
      if -1 == validate_and_document_req_flags(domain_node.name, subsystem_num, subsystem_id, xilpm_nodeid, xilpm_dev_name,
                                          output, device_list[index+1], 0):
        return -1
      # write CDO command to output
      cdo_write_command(subsystem_num, subsystem_id, xilpm_dev_name,
                        hex(xilpm_nodeid),
                        device_list[index+1],
                        output)
      continue
    elif "mailbox" in device_node.name:
      # in input, mailbox nodes correspond to xilpm dev IPI nodes
      key = mailbox_devices[device_node.name]
    else:
      print("add_requirements: not covered: ",str(device_node))
      return -1
    # report error if there is misalignment of requirements. E.g. timeshared and exclusive between 2 reqs
    if -1 == validate_and_document_req_flags(domain_node.name,
                                        subsystem_num, subsystem_id,
                                        existing_devices[key], key, output,
                                        device_list[index+1], 0):
      return -1
    cdo_write_command(subsystem_num, subsystem_id, key, hex(existing_devices[key]), device_list[index+1],output)
  return 0

# add requirements that link devices to subsystems
def add_requirements(domain_node, cpu_node, sdt, output):
  subsystem_id = subsystems[domain_node.name]
  subsystem_num = subsystem_id & 0xF

  # here parse the subsystem for device requirements
  device_list = domain_node.propval("access")
  num_params = domain_node.propval("#xilinx,config-cells")[0]
  if num_params != 1:
    print("add_requirements: #xilinx,config-cells should be 1", domain_node.name, str(num_params))
    return -1


  # here check for included resource groups
  include_list = domain_node.propval("include")
  root_node = domain_node.tree['/']

  if include_list != [""]:
    for phandle in include_list:
      rsc_group_node = root_node.tree.pnode(phandle)
      rsc_group_node_num_params = rsc_group_node.propval("#xilinx,config-cells")[0]
      if rsc_group_node_num_params != num_params:
        print("mismatch in requirements list between ", domain_node.name, " and ", rsc_group_node.name)
        return -1

      rsc_group_device_list = rsc_group_node.propval("access")
      for i in rsc_group_device_list:
        device_list.append(i)

  return add_requirements_internal(domain_node, cpu_node, sdt, output, device_list, num_params)

def validate_requirements():



  # generate maps for each susbsystem
  subsystem_requirements = {}
  
  for s_key,s_value in subsystems.items():
    current_subsystem_reqs = []
    subsystem_key = s_value
    
    for r_key,r_value in requirement_map.items():
      if r_key[0] == subsystem_key:
        current_subsystem_reqs.append(r_value)
    subsystem_requirements[subsystem_key] = current_subsystem_reqs

  # check other subsystem maps for mis-aligned req's
  for current_sub, current_reqs in subsystem_requirements.items():
    for other_sub, other_reqs in subsystem_requirements.items():
      # do not check intra requirements. that was already validated
      if current_sub == other_sub:
        continue
      for current_req in current_reqs:
        for other_req in other_reqs:
          if current_req.node == other_req.node:
            # if exclusive or non-shared then make sure only 1 req. is present
            # for that node
            if current_req.usage == REQ_USAGE.REQ_NONSHARED:
              print("current requirement with node ",hex(current_req.node),
                    "is linked between 2 subsystems: ", hex(current_req.subsystem), " and ", hex(other_req.subsystem),
                    "and one requirement has this as non shared.")
              return False


            # timeshared — all req’s have to be time shared.
            if current_req.usage == REQ_USAGE.REQ_TIME_SHARED and other_req.usage != REQ_USAGE.REQ_TIME_SHARED:
              print("current requirement with node ",hex(current_req.node),
                    "is linked between 2 subsystems: ", hex(current_req.subsystem), " and ", hex(other_req.subsystem),
                    "and only one has usage as time shared")
              return False

            # Same for simultaneously shared
            if current_req.usage == REQ_USAGE.REQ_SHARED and other_req.usage != REQ_USAGE.REQ_SHARED:
              print("current requirement with node ",hex(current_req.node),
                    "is linked between 2 subsystems: ", hex(current_req.subsystem), " and ", hex(other_req.subsystem),
                    "and only one has usage as shared")
              return False

  return True

def cdo_write( domain_node, sdt, options ):
    try:
        verbose = options['verbose']
    except:
        verbose = 0

    if (len(options["args"]) > 0):
      if re.match(options["args"][0], "regulator"):
        outfile = options["args"][1]
      else:
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
    if re.match(options["args"][0], "regulator"):
        gen_board_topology( domain_node, sdt, output )
    else:
        for n in domain_node.subnodes():
            if n.propval('access') != ['']:
                cpu_node = add_subsystem(n, sdt, output)
                if cpu_node == -1:
                    print("invalid cpu node for add_subsystem")
                    return False
                elif cpu_node == 0:
                    continue

                cpu_nodes.append(cpu_node)
                domain_nodes.append(n)
    for i in range(len(domain_nodes)):
      ret = add_requirements(domain_nodes[i], cpu_nodes[i], sdt, output)
      if ret != 0:
        return ret

    return validate_requirements()
