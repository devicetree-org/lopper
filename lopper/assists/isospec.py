#/*
# * Copyright (c) 2023 Advanced Micro Devices, Inc. All Rights Reserved.
# *
# * Author:
# *       Bruce Ashfield <bruce.ashfield@amd.com>
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
from lopper import LopperFmt
from lopper.yaml import LopperJSON
from lopper.tree import LopperAction
from lopper.tree import LopperTree
from lopper.tree import LopperNode
from lopper.tree import LopperProp
import lopper
import lopper_lib
from itertools import chain
import json
import humanfriendly

from lopper.log import _init, _warning, _info, _error, _debug
import logging

def is_compat( node, compat_string_to_test ):
    if re.search( "isospec,isospec-v1", compat_string_to_test):
        return isospec_domain
    if re.search( "module,isospec", compat_string_to_test):
        return isospec_domain
    return ""

# tests for a bit that is set, going fro 31 -> 0 from MSB to LSB
def check_bit_set(n, k):
    if n & (1 << (k)):
        return True

    return False

def set_bit(value, bit):
    return value | (1<<bit)

def clear_bit(value, bit):
    return value & ~(1<<bit)


def destinations( tree ):
    """returns all nodes with a destinations property in a tree
    """
    nodes_with_dests = []

    # find all the nodes with destinations in a tree. We are walking
    # all the nodes, and checking for a destinations property
    for n in tree:
        try:
            dests = n["destinations"]
            nodes_with_dests.append( n )
        except:
            pass

    return nodes_with_dests


iso_cpus_to_device_tree_map = {
                                "APU*": {
                                          "compatible": "arm,cortex-a72",
                                          "el": 3
                                        },
                                "RPU*": {
                                          "compatible": "arm,cortex-r5",
                                          "el": None
                                        }
                              }

def isospec_process_cpus( cpus_info, sdt, json_tree ):
    """ returns a list of dictionaries that represent the structure
        of any found cpus. These can be converted to json for future
        encoding into device tree properties
    """
    _info( f"isospec_process_cpus: {cpus_info} [{type(cpus_info)}] [{cpus_info}]" )

    cpus_list = []
    cpus = cpus_info["SMIDs"]
    for cpu_name in cpus:
        _info( f"    processing cpu: {cpu_name}" )

        cpu_map = {}
        for n,dn in iso_cpus_to_device_tree_map.items():
            if re.search( n, cpu_name ):
                cpu_map = dn

        if cpu_map:
            compat_string = cpu_map["compatible"]
            device_tree_compat = compat_string
        else:
            _error( f"unrecognized cpu {cpu_name}" )

        # did we have a mapped compatible string in the device tree ?
        if device_tree_compat:
            # is there a number in the isospec name ? If so, that is our
            # mask, if not, we set the cpu mask to 0x3 (them all)
            m = re.match( r'.*?(\d+)', cpu_name )
            if m:
                cpu_number = m.group(1)
            else:
                cpu_number = -1

            # look in the device tree for a node that matches the
            # mapped compatible string
            compatible_nodes = sdt.tree.cnodes( device_tree_compat )
            if compatible_nodes:
                # we need to find the cluster name / label, that's the parent
                # of the matching nodes, any node will do, so we take the first
                cpu_cluster = compatible_nodes[0].parent
                if not cpu_cluster:
                    _warning( f"no cluster found for cpus, returning" )
                    return None

                # take the label if set, otherwise take the node name
                cluster_name = cpu_cluster.label if cpu_cluster.label else cpu_cluster.name

                # we have the name, now we need the cluster mask. If
                # there's a cpu number. Confirm that the node exists,
                # and set the bit. If there's no number, our mask is
                # 0xf
                cluster_mask = 0
                if cpu_number != -1:
                    for c in compatible_nodes:
                        if re.search( "cpu@" + cpu_number, c.name ):
                            cluster_mask = set_bit( cluster_mask, int(cpu_number) )
                else:
                    cluster_mask = 0xf

                # cpu mode checks.
                #    secure
                #    el
                try:
                    cpu_flags = cpus_info["flags"]
                except:
                    cpu_flags = {}

                secure = False
                mode_mask = 0
                try:
                    secure_val = cpu_flags["secure"]
                    secure = secure_val
                except Exception as e:
                    pass

                try:
                    mode = cpu_flags["mode"]
                    if mode == "el":
                        mode_mask = set_bit( mode_mask, 0 )
                        mode_mask = set_bit( mode_mask, 1 )
                except:
                    # no passed mode, use the el level from the cpu_map
                    if cpu_map:
                        mode_mask = cpu_map["el"]

                if mode_mask:
                    cpu_entry = { "cluster" : cluster_name,
                                  "cpumask" : hex(cluster_mask),
                                  "mode" : { "secure": secure,
                                             "el": hex(mode_mask)
                                            }
                                 }
                else:
                    cpu_entry = { "cluster" : cluster_name,
                                  "cpumask" : hex(cluster_mask),
                                  "mode" : { "secure": secure }
                                 }

                cpus_list.append( cpu_entry )
        else:
            _warning( f"cpus entry {cpus_info[c]} has no device tree mapping" )

    _info( "cpus_list: %s" % cpus_list )

    return cpus_list

def isospec_device_flags( device_name, defs, json_tree ):

    domain_flag_dict = {}

    if type(defs) == dict:
        _info( f"isospec_device_flags: {defs}" )
        try:
            flags = defs["flags"]
            for flag,value in flags.items():
                if value:
                    domain_flag_dict[flag] = True
        except:
            return domain_flag_dict
    else:
        # try 1: is it a property ?
        flags = defs.propval( "flags" )

        # try 2: is it a subnode ?
        if not flags[0]:
            for n in defs.children():
                if n.name == "flags":
                    for p in n:
                        flags.append( p )

        # map the flags to something domains.yaml can output
        # create a flags dictionary, so we can next it into the access
        # structure below, which will then be transformed into yaml later.
        for flag in flags:
            try:
                if flag.value != '':
                    # if a flag is present, it means it was set to "true", it
                    # won't even be here in the false case.
                    domain_flag_dict[flag.name] = True
            except:
                pass

    _info( "isospec_device_flags: %s %s" % (device_name,domain_flag_dict) )

    return domain_flag_dict

# if something appears in this map, it is a memory entry, and
# we need to process it as such.
iso_memory_device_map = {
                          "DDR0" : ["memory", "memory@.*"],
                          "OCM.*" : ["sram", None]
                        }

def isospec_memory_type( name ):
    mem_found = None
    for n,v in iso_memory_device_map.items():
        if re.search( n, name ):
            mem_found = v

    if mem_found:
        return mem_found[0]

    return ""

def isospec_memory_dest( name ):
    mem_found = None
    for n,v in iso_memory_device_map.items():
        if re.search( n, name ):
            mem_found = v

    if mem_found:
        return mem_found[1]

    return ""

def isospec_process_memory( name, dest, sdt, json_tree ):
    _info( f"isospec_process_memory: {dest}" )
    memory_dest = isospec_memory_dest( name )
    memory_type = isospec_memory_type( name )
    memory_node = None
    memory_list = []
    if memory_type == "memory":
        _info( f"  memory {memory_dest}" )
        # we have a node to lookup in the device tree
        try:
            possible_mem_nodes = sdt.tree.nodes(memory_dest)
        except Exception as e:
            possible_mem_nodes = []
            _info( f"Exception looking for memory: {e}" )

        for n in possible_mem_nodes:
            _info( f"  possible_mem_nodes: {n.abs_path} type: {n['device_type']}" )
            try:
                if "memory" in n["device_type"].value:
                    reg = n["reg"]
                    _info( f"  reg {reg.value}" )

                    # we could do this more generically and look it up in the
                    # parent, but 2 is the default, so doing this for initial
                    # effort
                    address_cells = 2
                    size_cells = 2

                    reg_chunks = lopper_lib.chunks( reg.value, address_cells + size_cells )
                    for reg_chunk in reg_chunks:
                        start = reg_chunk[0:address_cells]
                        start = lopper.base.lopper_base.encode_byte_array( start )
                        start = int.from_bytes(start,"big")

                        size =  reg_chunk[address_cells:]

                        size = lopper.base.lopper_base.encode_byte_array( size )
                        size = int.from_bytes(size,"big")

                        _info( f"  start: {hex(start)} size: {hex(size)}" )

                        memory_list.append( {
                                              "start": hex(start),
                                              "size": hex(size)
                                            }
                                           )

            except Exception as e:
                _debug( f"Exception {e}" )

    elif memory_type == "sram":
        # no memory dest
        _info( f"sram memory type" )
        address = dest['addr']
        tnode = sdt.tree.addr_node( address )
        if tnode:
            # pull the start and size out of the device tree node
            # don't have a device tree to test this yet
            _warning( f"target node {tnode.abs_path} found, but no processing is available" )
        else:
            size = dest['size']
            # size = humanfriendly.parse_size( size, True )
            start = address
            _info( f"sram start: {start} size: {size}" )
            memory_list.append( {
                                  "start": start,
                                  "size": size
                                }
                              )

    return memory_list

#### TODO: make this take a "type" and only return that type, versus the
####       current multiple list return
def isospec_process_access( access_node, sdt, json_tree ):
    """processes the access values in an isospec subsystem
    """
    access_list = []
    memory_list = []
    sram_list = []
    cpu_list = []

    # access_node is a chunked json string
    _info( f"=======> isospec_process_access: {access_node}" )

    for a in range(len(access_node)):
        access = access_node[a]
        _info( f"process_access: {access}" )
        try:
            try:
                same_as_default = access["same_as_default"]
                _info( f"{access} has default settings for '{same_as_default}', looking up" )
                # same_as_default was set, we need to locate it
                defs = isospec_device_defaults( same_as_default, json_tree )
                if not defs:
                    _error( "cannot find default settings" )
            except:
                same_as_default = None
                # inline values
                defs = access

            _info( f"found device defaults: {defs}", defs )

            # look at the type of access. that dictates where we find
            # the destinations / target.
            try:
                access_type = defs["type"]
            except:
                access_type = "device"

            if access_type == "cpu_list":
                iso_cpus = defs["SMIDs"]
                iso_cpu_list = isospec_process_cpus( defs, sdt, json_tree )
                cpu_list.extend( iso_cpu_list )
                _info( f"isospec_process_access: cpus list collected: {cpu_list}")
            elif access_type == "device":
                _info( f"ispospec_process_actions: device with destinations: {defs['destinations']}" )

                flag_mapping = isospec_device_flags( defs["name"], defs, json_tree )

                try:
                    device_requested = flag_mapping["requested"]
                except:
                    _info( f'device \"{defs["name"]}\" was found, but not requested. adding to domain' )

                # find the destinations in the isospec json tree
                dests = isospec_device_destination( defs["destinations"], json_tree )

                # we now need to locate the destination device in the device tree, all
                # we have is the address to use for the lookup
                for d in dests:
                    try:
                        address = d['addr']
                        name = d['name']
                        tnode = sdt.tree.addr_node( address )
                        if tnode:
                            _info( f"    found node at address {address}: {tnode}", tnode )
                            access_list.append( {
                                                  "dev": tnode.name,
                                                  "label": tnode.label,
                                                  "flags": flag_mapping
                                                }
                                              )
                        else:
                            raise Exception( f"no node found for {name} => {d}" )
                    except Exception as e:
                        mem_found = None
                        for n,v in iso_memory_device_map.items():
                            if re.search( n, d['name'] ):
                                _info( f"    device is memory: {n} matches {d['name']}" )
                                mem_found = v

                        # no warning if we failed on memory in the try clause
                        if mem_found:
                            ml = isospec_process_memory( d['name'], d, sdt, json_tree )
                            if "memory" == isospec_memory_type(d['name']):
                                memory_list.extend( ml )
                            if "sram" == isospec_memory_type(d['name']):
                                sram_list.extend( ml )

                            # no warning for memory
                            continue

                        # it was something other than a dict returned as a dest
                        _warning( f"isospec: process_access: {e}" )

        except Exception as e:
            pass

    return access_list, cpu_list, memory_list, sram_list

def isospec_device_defaults( device_name, isospec_json_tree ):
    """
    returns the default settings for the named device
    """

    default_settings = isospec_json_tree["/default_settings"]
    if not default_settings:
        return None

    default_subsystems = isospec_json_tree["/default_settings/subsystems"]
    if not default_subsystems:
        return None

    default_subsystem = None
    for s in default_subsystems.children():
        if s.name == "default":
            default_subsystem = s

    # _info( " default settings, default subsystem found!" )
    if not default_subsystem:
        return None

    ### Note: we should probably be matching up the "id" that is part
    ### of this subsystem the requestor, since not all
    ### "same_as_default" values must be in the subsysystem named
    ### "default"

    # we now (finally) have the default subsystem. The subnodes and
    # properties of this node contain our destinations with default
    # values for the various settings

    # if we end up with large domains, we may want to run this once
    # and construct a dictionary to consult later.

    try:
        default_access = default_subsystem["access"]
        access_list = []
        for d in range(len(default_access)):
            access_list.append( default_access[d] )

        device_default = [d for d in access_list if d["name"] == device_name][0]
    except Exception as e:
        # no settings, return none
        _info( f"exception while doing default settings {e}" )
        return None

    return device_default

def isospec_device_destination( destination_list, isospec_json_tree ):
    """Look for the isospec "destinations" that match the passed
       list of destinations.

       returns a list of the isospec destinatino that matches
    """

    destination_result = []

    # locate all nodes in the tree that have a destinations property
    dnodes = destinations( isospec_json_tree )

    for destination in destination_list:
        for n in dnodes:
            try:
                dests = n["destinations"]
            except Exception as e:
                pass

            if dests.pclass == "json":
                _debug( f"node {n.abs_path} has json destinations property: {dests.name}" )
                # _info( f"raw dests: {dests.value} ({type(dests.value)})" )
                try:
                    for i in range(len(dests)):
                        x = dests[i]
                        if x["name"] == destination:
                            destination_result.append( x )
                except Exception as e:
                    # it wsn't a dict, ignore
                    pass
            else:
                pass
                # for i in dests.value:
                #     if i == destination:
                #         destination_result.append( i )

    _info( f"destinations found: {destination_result}" )

    return destination_result

def domains_tree_start():
    """ Start a device tree to represent a system device tree domain
    """
    domains_tree = LopperTree()
    domain_node = LopperNode( abspath="/domains", name="domains" )

    return domains_tree

def domains_tree_add_subsystem( domains_tree, subsystem_name="default-subsystem", subsystem_id=0 ):

    subsystems_node = LopperNode( abspath=f"/domains/{subsystem_name}", name=subsystem_name )
    subsystems_node["compatible"] = "xilinx,subsystem"
    subsystems_node["id"] = subsystem_id
    domains_tree = domains_tree + subsystems_node

    return domains_tree

def domains_tree_add_domain( domains_tree, domain_name="default", parent_domain = None, id=0 ):

    if not parent_domain:
        domain_node = LopperNode( abspath=f"/domains/{domain_name}", name=domain_name )
        domain_node["compatible"] = "openamp,domain-v1"
        domain_node["id"] = id
        domains_tree = domains_tree + domain_node
    else:
        domain_node = LopperNode( name=domain_name )
        domain_node["compatible"] = "openamp,domain-v1"
        domain_node["id"] = id
        parent_domain + domain_node

    return domain_node

def process_domain( domain_node, iso_node, json_tree, sdt ):
    _info( f"infospec_domain: process_domain: processing: {iso_node.name}" )
    # iso_node.print()

    # access and memory AND now cpus
    try:
        iso_access = json_tree[f"{iso_node.abs_path}"]["access"]
        _info( f"access: {iso_access}" )

        access_list,cpus_list,memory_list,sram_list = isospec_process_access( iso_access, sdt, json_tree )
        if cpus_list:
            domain_node["cpus"] = json.dumps(cpus_list)
            domain_node.pclass = "json"
        if memory_list:
            _info( f"memory: {memory_list}" )
            domain_node["memory"] = json.dumps(memory_list)
        if sram_list:
            _info( f"sram: {memory_list}" )
            domain_node["sram"] = json.dumps(sram_list)
        domain_node["access"] = json.dumps(access_list)
    except KeyError as e:
        _error( f"no access list in {iso_node.abs_path}" )
    except Exception as e:
        _error( f"problem during subsystem processing: {e}" )

    return domain_node


def isospec_domain( tgt_node, sdt, options ):
    """assist entry point, called from lopper when a node is
       identified, or passed as a command line assist
    """
    try:
        verbose = options['verbose']
    except:
        verbose = 0

    lopper.log._init( __name__ )

    if verbose:
        lopper.log._level( logging.INFO, __name__ )
    if verbose > 1:
        lopper.log._level( logging.DEBUG, __name__ )
        #logging.getLogger().setLevel( level=logging.DEBUG )

    _info( f"cb: isospec_domain( {tgt_node}, {sdt}, {verbose} )" )

    if sdt.support_files:
        isospec = sdt.support_files.pop()
    else:
        try:
            args = options['args']
            if not args:
                _error( "isospec: no isolation specification passed" )
            isospec = args.pop(0)
        except Exception as e:
            _error( f"isospec: no isolation specification passed: {e}" )
            sys.exit(1)

    domain_yaml_file = "domains.yaml"
    try:
        args = options['args']
        domain_yaml_file = args.pop(0)
    except:
        pass

    try:
        iso_file = Path( isospec )
        iso_file_abs = iso_file.resolve( True )
    except FileNotFoundError as e:
        _error( f"ispec file {isospec} not found" )

    # convert the spec to a LopperTree for consistent manipulation
    json_in = LopperJSON( json=iso_file_abs )
    json_tree = json_in.to_tree()

    # TODO: make the tree manipulations and searching a library function
    domains_tree = domains_tree_start()
    iso_subsystems = json_tree["/design/subsystems"]
    try:
        iso_domains = json_tree["/design/subsystems/" ]
    except:
        pass

    #iso_subsystems.print()
    #print( iso_subsystems.children() )

    for iso_node in iso_subsystems.children():
        isospec_domain_node = json_tree[f"{iso_node.abs_path}"]
        domain_id = iso_node["id"]
        domain_node = domains_tree_add_domain( domains_tree, iso_node.name, None, domain_id )
        ## these are subsystems, which have nested domains
        domain_node = process_domain( domain_node, iso_node, json_tree, sdt )

        try:
            sub_domains = json_tree[f"{iso_node.abs_path}" + "/domains"]
            # sub_domains.print()
            sub_domain_node = LopperNode( name="domains" )
            domain_node = domain_node + sub_domain_node
            for s in sub_domains.children():
                try:
                    domain_id = s["id"]
                except:
                    # copy the subsystem's id
                    domain_id = iso_node["id"]
                sub_domain_node_new = domains_tree_add_domain( domains_tree, s.name, sub_domain_node, domain_id )
                sub_domain_node_new = process_domain( sub_domain_node_new, s, json_tree, sdt )

                domain_node.print()
        except:
            pass


    # domains_tree.print()

    # write the yaml tree
    _info( f"writing domain file: {domain_yaml_file}" )
    sdt.write( domains_tree, output_filename=domain_yaml_file, overwrite=True )

    return True

