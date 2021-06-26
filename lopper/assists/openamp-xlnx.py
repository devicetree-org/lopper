#/*
# * Copyright (c) 2019,2020 Xilinx Inc. All rights reserved.
# *
# * Author:
# *       Bruce Ashfield <bruce.ashfield@xilinx.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

import copy
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
from lopper import LopperFmt
import lopper
from lopper.tree import *
from re import *

sys.path.append(os.path.dirname(__file__))
from openamp_xlnx_common import *

RPU_PATH = "/rpu@ff9a0000"

def is_compat( node, compat_string_to_test ):
    if re.search( "openamp,xlnx-rpu", compat_string_to_test):
        return xlnx_openamp_rpu
    return ""

def update_mbox_cntr_intr_parent(sdt):
  # find phandle of a72 gic for mailbox controller
  a72_gic_node = sdt.tree["/amba_apu/interrupt-controller@f9000000"]
  # set mailbox controller interrupt-parent to this phandle
  mailbox_cntr_node = sdt.tree["/zynqmp_ipi1"]
  mailbox_cntr_node["interrupt-parent"].value = a72_gic_node.phandle
  sdt.tree.sync()
  sdt.tree.resolve()

# 1 for master, 0 for slave
# for each openamp channel, return mapping of role to resource group
def determine_role(sdt, domain_node):
    rsc_groups = []
    current_rsc_group = None

    for value in domain_node.propval('include'):
        current_rsc_group = sdt.tree.pnode(value)
        if domain_node.propval(HOST_FLAG) != ['']: # only for openamp master
            if current_rsc_group == None:
                print("invalid resource group phandle: ", value)
                return -1
            rsc_groups.append(current_rsc_group)
        else:
            print("only do processing in host openamp channel domain ", value)
            return -1

    return rsc_groups


# in this case remote is rpu
# find node that is other end of openamp channel
def find_remote(sdt, domain_node, rsc_group_node):
  domains = sdt.tree["/domains"]
  # find other domain including the same resource group
  remote_domain = None
  for node in domains.subnodes():
    # look for other domains with include
    if node.propval("include") != [''] and node != domain_node:
      # if node includes same rsc group, then this is remote
      for i in node.propval("include"):
        included_node = sdt.tree.pnode(i)
        if included_node != None and included_node == rsc_group_node:
           return node

  return -1

# tests for a bit that is set, going fro 31 -> 0 from MSB to LSB
def check_bit_set(n, k):
    if n & (1 << (k)):
        return True

    return False

# return rpu cluster configuration
# rpu cpus property fields: Cluster | cpus-mask | execution-mode
#
#execution mode ARM-R CPUs:
#bit 30: lockstep (lockstep enabled == 1)
#bit 31: secure mode / normal mode (secure mode == 1)
# e.g. &cpus_r5 0x2 0x80000000>
# this maps to arg1 as rpu_cluster node
# arg2: cpus-mask: 0x2 is r5-1, 0x1 is r5-0, 0x3 is both nodes
#        if 0x3/both nodes and in split then need to openamp channels provided,
#        otherwise return error
#        if lockstep valid cpus-mask is 0x3 needed to denote both being used
#  
def construct_carveouts(sdt, rsc_group_node, core, openamp_app_inputs):
  # static var that persists beyond lifetime of first function call
  # this is needed as there may be more than 1 openamp channel
  # so multiple carveouts' phandles are required
  if not hasattr(construct_carveouts,"carveout_phandle"):
    # it doesn't exist yet, so initialize it
    construct_carveouts.carveout_phandle = 0x5ed0

  # carveouts each have addr,range
  mem_regions = [[0 for x in range(2)] for y in range(4)] 
  mem_region_names = {
    0 : "elfload",
    1 : "vdev0vring0",
    2 : "vdev0vring1",
    3 : "vdev0buffer",
  }
  carveout_phandle_list = []

  for index,value in enumerate(rsc_group_node["memory"].value):
    if index % 2 == 1:
      continue

    region_name = mem_region_names[index/2]

    name = "rpu"+str(core)+region_name
    addr = value
    length = rsc_group_node["memory"].value[index + 1]

    openamp_app_inputs[rsc_group_node.name + region_name + '_base'] = hex(value)
    openamp_app_inputs[rsc_group_node.name + region_name + '_size'] = hex(length)

    new_node = LopperNode(-1, "/reserved-memory/"+name)
    new_node + LopperProp(name="no-map", value=[])
    new_node + LopperProp(name="reg",value=[0,addr,0,length])
    new_node + LopperProp(name="phandle",value=construct_carveouts.carveout_phandle)
    new_node.phandle = new_node

    sdt.tree.add(new_node)
    print("added node: ",new_node)

    carveout_phandle_list.append(construct_carveouts.carveout_phandle)
    construct_carveouts.carveout_phandle += 1

  return carveout_phandle_list

def construct_mem_region(sdt, domain_node, rsc_group_node, core, openamp_app_inputs):
  # add reserved mem if not present
  res_mem_node = None
  carveout_phandle_list = None
  try:
    res_mem_node = sdt.tree["/reserved-memory"]
    print("found pre-existing reserved mem node")
  except:
    res_mem_node = LopperNode(-1, "/reserved-memory")
    res_mem_node + LopperProp(name="#address-cells",value=2)
    res_mem_node + LopperProp(name="#size-cells",value=2)
    res_mem_node + LopperProp(name="ranges",value=[])

    sdt.tree.add(res_mem_node)
    print("added reserved mem node ", res_mem_node)

  return construct_carveouts(sdt, rsc_group_node, core, openamp_app_inputs)


# set pnode id for current rpu node
def set_rpu_pnode(sdt, r5_node, rpu_config, core, platform, remote_domain):
  if r5_node.propval("power-domain") != ['']:
    print("pnode id already exists for node ", r5_node)
    return -1

  rpu_pnodes = {}
  if platform == SOC_TYPE.VERSAL:
    rpu_pnodes = {0 : 0x18110005, 1: 0x18110006}
  else:
    print("only versal supported for openamp domains")
    return -1
  rpu_pnode = None
  # rpu config : true is split
  if rpu_config == "lockstep":
    rpu_pnode = rpu_pnodes[0]
  else:
     rpu_pnode = rpu_pnodes[core]

  r5_node + LopperProp(name="power-domain", value = rpu_pnodes[core])
  r5_node.sync(sdt.FDT)

  return

def setup_mbox_info(sdt, domain_node, r5_node, mbox_ctr):
  mbox_ctr.phandle = sdt.tree.phandle_gen()

  if mbox_ctr.propval("reg-names") == [''] or mbox_ctr.propval("xlnx,ipi-id") == ['']:
    print("invalid mbox ctr")
    return -1
  mbox_ctr_phandle = mbox_ctr.propval("phandle")
  r5_node + LopperProp(name="mboxes",value=[mbox_ctr_phandle,0,mbox_ctr_phandle,1])
  r5_node + LopperProp(name="mbox-names", value = ["tx", "rx"]);
  sdt.tree.sync()
  r5_node.sync(sdt.FDT)
  return
  
# based on rpu_cluster_config + cores determine which tcm nodes to use
# add tcm nodes to device tree
def setup_tcm_nodes(sdt, r5_node, platform, rsc_group_node):
  # determine which tcm nodes to use based on access list in rsc group
  r5_node + LopperProp(name="sram", value = rsc_group_node["access"].value.copy() )

  return 0

def setup_r5_core_node(rpu_config, sdt, domain_node, rsc_group_node, core, remoteproc_node, platform, remote_domain, mbox_ctr, openamp_app_inputs):
  carveout_phandle_list = None
  r5_node = None
  # add r5 node if not present
  try:
    r5_node = sdt.tree["/rpu@ff9a0000/r5_"+str(core)]
    print("node already exists: ", r5_node)
  except:
    r5_node = LopperNode(-1, "/rpu@ff9a0000/r5_"+str(core))
    r5_node + LopperProp(name="#address-cells",value=2)
    r5_node + LopperProp(name="#size-cells",value=2)
    r5_node + LopperProp(name="ranges",value=[])
    r5_node + LopperProp(name="compatible",value="xilinx,r5f")
    sdt.tree.add(r5_node)
    print("added r5 node ", r5_node)
    print("add props for ",str(r5_node))
  # props
  ret = set_rpu_pnode(sdt, r5_node, rpu_config, core, platform, remote_domain)
  if ret == -1:
    print("set_rpu_pnode failed")
    return ret
  ret = setup_mbox_info(sdt, domain_node, r5_node, mbox_ctr)
  if ret == -1:
    print("setup_mbox_info failed")
    return ret

  carveout_phandle_list = construct_mem_region(sdt, domain_node, rsc_group_node, core, openamp_app_inputs)
  if carveout_phandle_list == -1:
    print("construct_mem_region failed")
    return ret

  if carveout_phandle_list != None:
    print("adding prop memory-region to ",r5_node)
    r5_node + LopperProp(name="memory-region",value=carveout_phandle_list)

  #tcm nodes
  for i in r5_node.subnodes():
    if "tcm" in i.abs_path:
      "tcm nodes exist"
      return -1

  # tcm nodes do not exist. set them up
  setup_tcm_nodes(sdt, r5_node, platform, rsc_group_node)
           
# add props to remoteproc node
def set_remoteproc_node(remoteproc_node, sdt, rpu_config):
  props = []
  props.append(LopperProp(name="reg", value =   [0x0, 0xff9a0000, 0x0, 0x10000]))
  props.append(LopperProp(name="#address-cells",value=2))
  props.append(LopperProp(name="ranges",value=[]))
  props.append(LopperProp(name="#size-cells",value=2))
  if rpu_config == "split":
      rpu_config = 0x1
  else:
      rpu_config = 0x0
  props.append(LopperProp(name="xlnx,cluster-mode",value=rpu_config))
  props.append(LopperProp(name="compatible",value="xlnx,zynqmp-r5-remoteproc"))
  for i in props:
    remoteproc_node + i
  # 

def determine_core(remote_domain):
  cpus_prop_val = remote_domain.propval("cpus")
  rpu_config = None # split or lockstep

  if cpus_prop_val != ['']:
    if len(cpus_prop_val) != 3:
      print("rpu cluster cpu prop invalid len")
      return -1
    rpu_config = "lockstep" if  check_bit_set(cpus_prop_val[2], 30)==True else "split"
    if rpu_config == "lockstep":
      core = 0
    else:
      core_prop_val = remote_domain.propval("cpus")
      if core_prop_val == ['']:
        print("no cpus val for core ", remote_domain)
      else:
        if core_prop_val[1] == 2:
          core  = 1
        elif core_prop_val[1] == 1:
          core = 0
        else:
          print("invalid cpu prop for core ", remote_domain, core_prop_val[1])
          return -1
  return [core, rpu_config]


#core = []
# this should only add nodes  to tree
# openamp_app_inputs: dictionary to fill with openamp header info for openamp code base later on
def construct_remoteproc_node(remote_domain, rsc_group_node, sdt, domain_node,  platform, mbox_ctr, openamp_app_inputs):
  rpu_config = None # split or lockstep
  core = 0

  [core, rpu_config] = determine_core(remote_domain)



  # only add remoteproc node if mbox is present in access list of domain node
  # check domain's access list for mbox
  has_corresponding_mbox = False
  if domain_node.propval("access") != ['']:
    for i in domain_node.propval("access"):
      possible_mbox = sdt.tree.pnode(i)
      if possible_mbox != None:
        if possible_mbox.propval("reg-names") != ['']:
          has_corresponding_mbox = True

  # setup remoteproc node if not already present
  remoteproc_node = None
  try:
    remoteproc_node = sdt.tree["/rpu@ff9a0000"]
  except:
    print("remoteproc node not present. now add it to tree")
    remoteproc_node = LopperNode(-1, "/rpu@ff9a0000")
    set_remoteproc_node(remoteproc_node, sdt, rpu_config)
    sdt.tree.add(remoteproc_node, dont_sync = True)
    remoteproc_node.sync(sdt.FDT)
    remoteproc_node.resolve_all_refs()
    sdt.tree.sync()

  return setup_r5_core_node(rpu_config, sdt, domain_node, rsc_group_node, core, remoteproc_node, platform, remote_domain, mbox_ctr, openamp_app_inputs)


def validate_ipi_node(ipi_node):
    if ipi_node == None:
        print("invalid "+role+" IPI - invalid phandle from access property.")
        return False

    if 'xlnx,zynqmp-ipi-mailbox' not in ipi_node.propval("compatible"):
        print("invalid "+role+" IPI - wrong compatible string")
        return False

    ipi_base_addr = ipi_node.propval("reg")
    if len(ipi_base_addr) != 4:
        print("invalid "+role+" IPI - incorrect reg property of ipi", ipi_node)
        return False

    if ipi_base_addr[1] not in openamp_supported_ipis:
        return False

    return True


def parse_ipi_info(sdt, domain_node, remote_domain, current_rsc_group, openamp_app_inputs):
    host_ipi_node = None
    remote_ipi_node = None
    domains_to_process = {
        'host': domain_node,
        'remote' : remote_domain,
    }

    for role in domains_to_process.keys():
        domain = domains_to_process[role]

        access_pval = domain.propval("access")
        if len(access_pval) == 0:
            print("invalid "+role+" IPI - no access property")
            return False
        ipi_node = sdt.tree.pnode(access_pval[0])
        if validate_ipi_node(ipi_node) != True:
            return False

        ipi_base_addr = ipi_node.propval("reg")[1]
        agent = ipi_to_agent[ipi_base_addr]
        bitmask = agent_to_ipi_bitmask[agent]

        prefix = current_rsc_group.name + '-' + role + '-'
        openamp_app_inputs[prefix+'bitmask'] = hex(agent_to_ipi_bitmask[agent])
        openamp_app_inputs[prefix+'ipi'] = hex(ipi_base_addr)
        openamp_app_inputs[prefix+'ipi-irq-vect-id'] = ipi_to_irq_vect_id[ipi_base_addr]


def construct_mbox_ctr(sdt, openamp_app_inputs, remote_domain, host_ipi, remote_ipi):
    controller_parent = None
    try:
        controller_parent = sdt.tree["/zynqmp_ipi1"]
        print("zynqmp_ipi1 already present.")
    except:

        controller_parent = LopperNode(-1, "/zynqmp_ipi1")
        controller_parent + LopperProp(name="compatible",value="xlnx,zynqmp-ipi-mailbox")
        gic_node_phandle = sdt.tree["/amba_apu/interrupt-controller@f9000000"].phandle
        controller_parent + LopperProp(name="interrupt-parent", value = [gic_node_phandle])
        controller_parent + LopperProp(name="interrupts",value= host_ipi.propval("interrupts").copy()   )
        controller_parent + LopperProp(name="xlnx,ipi-id",value= host_ipi.propval("xlnx,ipi-id")[0]  )
        controller_parent + LopperProp(name="#address-cells",value=2)
        controller_parent + LopperProp(name="#size-cells",value=2)
        controller_parent + LopperProp(name="ranges")
        controller_parent + LopperProp(name="phandle",value=sdt.tree.phandle_gen())
        sdt.tree.add(controller_parent)
        print("added node ",controller_parent)



    # for each channel, add agent info to zynqmp_ipi1
    # find resource group per channel
    # map group to host + remote ipi info
    controller_idx = 0
    for key in openamp_app_inputs.keys():
        if '_to_group' in key:
            group_to_channel_record = openamp_app_inputs[key]
            group_name = group_to_channel_record.split('-to-')[1]
            group = sdt.tree["/domains/"+group_name]
            host_prefix = group.name + '-host-'
            remote_prefix = group.name + '-remote-'
            controller_node = LopperNode(-1, "/zynqmp_ipi1/controller" + str(controller_idx))
            controller_node + LopperProp(name="reg-names",value=["local_request_region", "local_response_region", "remote_request_region", "remote_response_region"])
            controller_node + LopperProp(name="#mbox-cells",value=1)
            controller_node + LopperProp(name="phandle",value=sdt.tree.phandle_gen()+1)

            # construct host mbox ctr xlnx,ipi-id from remote's ipi
            access_pval = remote_domain.propval("access")
            if len(access_pval) == 0:
                print("invalid remote IPI - no access property")
                return False
            ipi_node = sdt.tree.pnode(access_pval[0])
            if validate_ipi_node(ipi_node) != True:
                return False
            remote_ipi_id_val = ipi_node.propval('xlnx,ipi-id')
            controller_node + LopperProp(name="xlnx,ipi-id",value=remote_ipi_id_val[0])

            remote_ipi = int(openamp_app_inputs[group.name+'-'+'remote'+'-ipi'], 16)
            host_ipi   = int(openamp_app_inputs[group.name+'-'+  'host'+'-ipi'], 16)

            remote_agent = ipi_to_agent[remote_ipi]
            host_agent = ipi_to_agent[host_ipi]

            remote_offset = ipi_msg_buf_dest_agent_request_offsets[remote_ipi]
            host_offset = ipi_msg_buf_dest_agent_request_offsets[host_ipi]

            ipi_msg_buf_base = 0xff3f0000
            response_offset = 0x20

            local_request_region = ipi_msg_buf_base | host_agent | remote_offset
            remote_request_region = ipi_msg_buf_base | remote_agent | host_offset

            vals = [
                local_request_region,
                local_request_region | response_offset,
                remote_request_region,
                remote_request_region | response_offset
            ]

            reg_vals = []
            for i in vals:
                reg_vals.append(0x0)
                reg_vals.append(i)
                reg_vals.append(0x0)
                reg_vals.append(0x20)

            controller_node + LopperProp(name="reg",value=reg_vals)

            sdt.tree.add(controller_node)
            controller_idx += 1

    # if needed, will have to remove the existing mailbox
    for i in sdt.tree["/amba"].subnodes():
        if i.propval("compatible") == ['xlnx,zynqmp-ipi-mailbox'] and i.propval('xlnx,ipi-bitmask') != ['']:
                sdt.tree - i
                sdt.tree.sync()


def setup_userspace_nodes(sdt, domain_node, current_rsc_group, remote_domain, openamp_app_inputs):
    [core, rpu_config] = determine_core(remote_domain)
    construct_mem_region(sdt, domain_node, current_rsc_group, core, openamp_app_inputs)
    base = int(openamp_app_inputs[current_rsc_group.name+'elfload_base'],16)
    end_base = int(openamp_app_inputs[current_rsc_group.name+'vdev0buffer_base'],16)
    end_size = int(openamp_app_inputs[current_rsc_group.name+'vdev0buffer_size'],16)

    carveout_size = end_base - base + end_size

    amba_node = None
    try:
        amba_node = sdt.tree["/amba"]
    except:
        amba_node = LopperNode(-1,"/amba")
        sdt.tree.add(amba_node)

    carveout_node = LopperNode(-1, "/amba/shm@0")
    carveout_node + LopperProp(name="compatible",value="none")
    carveout_node + LopperProp(name="reg",value=[0x0, base, 0x0, carveout_size])
    sdt.tree.add(carveout_node)

    host_ipi = int(openamp_app_inputs[current_rsc_group.name+'-host-ipi'],16)

    userspace_host_ipi_node = LopperNode(-1, "/amba/ipi@0")
    userspace_host_ipi_node + LopperProp(name="compatible",value="none")

    # construct host ipi interrupts property
    access_pval = domain_node.propval("access")
    if len(access_pval) == 0:
        print("invalid "+role+" IPI - no access property")
        return False
    ipi_node = sdt.tree.pnode(access_pval[0])
    if validate_ipi_node(ipi_node) != True:
        return False
    host_ipi_interrupts_val = ipi_node.propval('interrupts')

    userspace_host_ipi_node + LopperProp(name="interrupts",value=host_ipi_interrupts_val)
    userspace_host_ipi_node + LopperProp(name="interrupt-parent",value=[sdt.tree["/amba_apu/interrupt-controller@f9000000"].phandle])
    userspace_host_ipi_node + LopperProp(name="phandle",value=sdt.tree.phandle_gen())
    userspace_host_ipi_node + LopperProp(name="reg",value=[0x0, host_ipi , 0x0,  0x1000])
    sdt.tree.add(userspace_host_ipi_node)

    openamp_app_inputs[current_rsc_group.name+'-tx'] = openamp_app_inputs[current_rsc_group.name+'vdev0vring0_base']
    openamp_app_inputs[current_rsc_group.name+'-rx'] = openamp_app_inputs[current_rsc_group.name+'vdev0vring1_base']


def parse_openamp_domain(sdt, options, tgt_node):
  print("parse_openamp_domain")
  domain_node = sdt.tree[tgt_node]
  root_node = sdt.tree["/"]
  platform = SOC_TYPE.UNINITIALIZED
  openamp_app_inputs = {}
  kernelcase = False

  if 'versal' in str(root_node['compatible']):
      platform = SOC_TYPE.VERSAL
  elif 'zynqmp' in str(root_node['compatible']):
      platform = SOC_TYPE.ZYNQMP
  else:
      print("invalid input system DT")
      return False

  rsc_groups = determine_role(sdt, domain_node)
  if rsc_groups == -1:
    print("failed to find rsc_groups")
    return rsc_groups

  # if host, find corresponding remote
  # if none report error
  channel_idx = 0
  for current_rsc_group in rsc_groups:
    # each openamp channel's remote/slave should be different domain
    # the domain can be identified by its unique combination of domain that includes the same resource group as the
    # openamp remote domain in question
    remote_domain = find_remote(sdt, domain_node, current_rsc_group)
    if remote_domain == -1:
      print("failed to find_remote")
      return remote_domain

    # parse IPI base address, bitmask, vect ID, agent information
    parse_ipi_info(sdt, domain_node, remote_domain, current_rsc_group, openamp_app_inputs)

    # determine if userspace or kernelspace flow
    print(current_rsc_group)
    if 'openamp-xlnx-kernel' in current_rsc_group.__props__:
        kernelcase = True

    openamp_app_inputs['channel'+ str(channel_idx)+  '_to_group'] = str(channel_idx) + '-to-' + current_rsc_group.name
    openamp_app_inputs[current_rsc_group.name] = channel_idx

    if kernelcase:
        host_ipi = sdt.tree.pnode(domain_node.propval("access")[0])
        remote_ipi = sdt.tree.pnode(remote_domain.propval("access")[0])

        construct_mbox_ctr(sdt, openamp_app_inputs, remote_domain, host_ipi, remote_ipi)
        mbox_ctr = sdt.tree["/zynqmp_ipi1/controller"+str(channel_idx)]
        construct_remoteproc_node(remote_domain, current_rsc_group, sdt, domain_node,  platform, mbox_ctr, openamp_app_inputs)
        openamp_app_inputs[current_rsc_group.name+'-tx'] = 'FW_RSC_U32_ADDR_ANY'
        openamp_app_inputs[current_rsc_group.name+'-rx'] = 'FW_RSC_U32_ADDR_ANY'

    else:
        setup_userspace_nodes(sdt, domain_node, current_rsc_group, remote_domain, openamp_app_inputs)

    # update channel for openamp group
    channel_idx += 1

  lines = ""
  for i in openamp_app_inputs.keys():
      lines += i.upper().replace('@','_') + "=\""

      val = openamp_app_inputs[i]
      if isinstance(val, int):
          lines += hex(openamp_app_inputs[i])
      else:
          lines += openamp_app_inputs[i]

      lines += "\"\n"

  with open('openamp-channel-info.txt', 'w') as the_file:
    the_file.write(lines)

  return True

# this is what it needs to account for:
#
# identify ipis, shared pages (have defaults but allow them to be overwritten
# by system architect
#
#
# kernel space case
#   linux
#   - update memory-region
#   - mboxes
#   - zynqmp_ipi1::interrupt-parent
#   rpu
#   - header
# user space case
#   linux
#   - header
#   rpu
#   - header
def xlnx_openamp_rpu( tgt_node, sdt, options ):
    print("xlnx_openamp_rpu")
    try:
        verbose = options['verbose']
    except:
        verbose = 0

    if verbose:
        print( "[INFO]: cb: xlnx_openamp_rpu( %s, %s, %s )" % (tgt_node, sdt, verbose))

    root_node = sdt.tree["/"]
    platform = SOC_TYPE.UNINITIALIZED
    if 'versal' in str(root_node['compatible']):
        platform = SOC_TYPE.VERSAL
    elif 'zynqmp' in str(root_node['compatible']):
        platform = SOC_TYPE.ZYNQMP
    else:
        print("invalid input system DT")
        return False

    try:
        domains_node = sdt.tree["/domains"]

        for node in domains_node.subnodes():
            if "openamp,domain-v1" in node.propval("compatible"):
                if node.propval( HOST_FLAG ) != ['']:
                    return parse_openamp_domain(sdt, options, node)
    except:
        print("ERR: openamp-xlnx rpu: no domains found")

