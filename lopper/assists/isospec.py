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
import os
import getopt
import re
from pathlib import Path
from pathlib import PurePath
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

def debug_exit( message = None ):
    if message:
        _info( message )
    _info( "debug exit" )
    os._exit(1)

class domain_yaml(object):

    # static / class viriable
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

    def __init__( self, sdt = None ):
        self.tree = LopperTree()
        self.sdt = sdt


    def subsystem_add( self, subsystem_name="default-subsystem", subsystem_id=0 ):
        subsystems_node = LopperNode( abspath=f"/domains/{subsystem_name}", name=subsystem_name )
        subsystems_node["compatible"] = "xilinx,subsystem"
        subsystems_node["id"] = subsystem_id

        self.tree = self.tree + subsystems_node

        return subsystems_node

    def node_add( self, name, parent ):
        new_node = LopperNode( name=name )
        parent + new_node

        return new_node

    def domain_add( self, domain_name="default", parent_domain = None, id=0 ):

        if not parent_domain:
            domain_node = LopperNode( abspath=f"/domains/{domain_name}", name=domain_name )
            domain_node["compatible"] = "openamp,domain-v1"
            domain_node["id"] = id
            self.tree = self.tree + domain_node
        else:
            domain_node = LopperNode( name=domain_name )
            domain_node["compatible"] = "openamp,domain-v1"
            domain_node["id"] = id
            _debug( f"               adding domain '{domain_name}' parent: {parent_domain}" )
            parent_domain + domain_node

        return domain_node

    def cpu_map( self, cpu_name ):
        cpu_map = {}
        for n,dn in domain_yaml.iso_cpus_to_device_tree_map.items():
            if re.search( n, cpu_name ):
                cpu_map = dn

        return cpu_map

    def device_flags_map( self, device_name, access ):
        domain_flag_dict = {}

        if type(access) == dict:
            # _info( f"device_flags_map: {access}" )
            try:
                flags = access["flags"]
                for flag,value in flags.items():
                    if value:
                        domain_flag_dict[flag] = True
            except:
                return domain_flag_dict
        else:
            # try 1: is it a property ?
            flags = access.propval( "flags" )

            # try 2: is it a subnode ?
            if not flags[0]:
                for n in access.children():
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

        # _info( "isospec_device_flags: %s %s" % (device_name,domain_flag_dict) )

        return domain_flag_dict

    def cpu_add( self, domain_or_subsystem, cpu ):
        # this should wrap the addition of an access entry
        _info( f"cpu_add: {domain_or_subsystem}: {cpu}" )

        # is there an cpu list already in the yaml node ?
        # better than this: add a routine to the yaml node to
        # abstract an cpu addition (via device)
        try:
            cpu_list = domain_or_subsystem["cpus"]
        except:
            cpu_list = LopperProp( "cpus", -1, domain_or_subsystem, [] )
            ## TODO: we shouldn't need to do this, as the node is passed
            ##       to the init. This is a lopper tree bug
            domain_or_subsystem + cpu_list

        cpu_map = self.cpu_map( cpu["name"] )
        if cpu_map:
            compat_string = cpu_map["compatible"]
            device_tree_compat = compat_string
            cpu_name = cpu["name"]
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
            compatible_nodes = self.sdt.tree.cnodes( device_tree_compat )
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
                            cluster_cpu_label = c.label
                else:
                    cluster_cpu_label = ''
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
                    cpu_entry = { "dev": cluster_name,    # remove before writing to yaml (if no roundtrip)
                                  "spec_name": cpu_name,  # remove before writing to yaml (if no roundtrip)
                                  "cluster" : cluster_name,
                                  "cluster_cpu" : cluster_cpu_label,
                                  "cpumask" : hex(cluster_mask),
                                  "mode" : { "secure": secure,
                                             "el": hex(mode_mask)
                                            }
                                 }
                else:
                    cpu_entry = { "dev": cluster_name,    # remove before writing to yaml (if no roundtrip)
                                  "spec_name": cpu_name,  # remove before writing to yaml (if no roundtrip)
                                  "cluster" : cluster_name,
                                  "cluster_cpu" : cluster_cpu_label,
                                  "cpumask" : hex(cluster_mask),
                                  "mode" : { "secure": secure }
                                 }

                cpu_list.value.append( cpu_entry )
        else:
            _warning( f"cpus entry {cpus_info[c]} has no device tree mapping" )

    def memory_add( self, domain_or_subsystem, memory ):
        _info( f"memory_add: {domain_or_subsystem}: {memory}" )

        debug = False
        try:
            # is it explicitly tagged as memory ?
            mem_dest_flag = memory["mem"]
            memory_dest = "memory@.*"

            # does it have a nodeid ? if it does, it is not just DRAM
            # tag it via the regex.
            try:
                nodeid = memory["nodeid"]
                memory_dest = isospec.memory_dest( memory["name"] )
                memory_type = isospec.memory_type( memory["name"] )
            except:
                memory_type = "memory"
        except:
            # if it isn't, we have a regex match to figure
            # out what type of memory it may be
            memory_dest = isospec.memory_dest( memory["name"] )
            memory_type = isospec.memory_type( memory["name"] )

        # if memory_type == "sram":
        #     _info( "debug: sram found" )
        # _info( f"memory type: {memory_type}" )

        # is there an memory list already in the yaml node ?
        try:
            memory_list = domain_or_subsystem[memory_type]
        except:
            memory_list = LopperProp( memory_type, -1, domain_or_subsystem, [] )
            ## TODO: we shouldn't need to do this, as the node is passed
            ##       to the init. This is a lopper tree bug
            domain_or_subsystem + memory_list

        try:
            memory_dest_address = int(memory["addr"],16)
            memory_dest_size = memory["size"]
        except:
            memory_dest_address = None
            memory_dest_size = None

        possible_mem_nodes = []
        if memory_type == "memory":
            # we have a node to lookup in the device tree
            try:
                # Q: here's the problem. For SRAM this is called because
                #    there wasn't an address match when we looked up the
                #    memory. BUT, there are memory nodes in the tree, so
                #    this will return options. The SRAM may fall into one
                #    of the ranges, so we use that start/end address which
                #    then updates in the yaml. But that means the start
                #    address, etc, are lost (as is the type).
                #
                # Q: should we only declare a match if the start address
                #    matches, versus just being in the start + size of
                #    memory ? Only for SRAM or for all types of memory ?
                #
                possible_mem_nodes = self.sdt.tree.nodes(memory_dest)
                if debug:
                    _info( f"possible mem nodes: {possible_mem_nodes}" )
            except Exception as e:
                _info( f"Exception looking for memory: {e}" )

        # if there's no possible device nodes, then we double
        # check the type mapping
        if not possible_mem_nodes:
            memory_dest = isospec.memory_dest( memory["name"] )
            memory_type = isospec.memory_type( memory["name"] )


        ## Note: when we start to consider the found memory nodes
        ##       from above, the "dev" value is going to change
        ##       the name to that, instead of the name from the spec.

        # Q: do we need to check if the memory is fully contained ?
        #    We really only need the address for now, since we don't
        #    care if this is fully contained in a memory range .. but
        #    we might in the future if we don't end up adjusting the
        #    device tree, or if there are multiple possible memory
        #    nodes, we could find the best fit.

        if memory_dest_address != None:
            memory_list.value.append( { "dev": memory["name"],       # remove before writing to yaml (if no roundtrip)
                                        "spec_name": memory["name"], # remove before writing to yaml (if no roundtrip)
                                        "start": hex(memory_dest_address),
                                        "size":  memory_dest_size
                                      }
                                    )

    def device_add( self, domain_or_subsystem, device, flags ):
        # this should wrap the addition of an access entry
        _info( f"device_add: {domain_or_subsystem}: {device}" )

        # is there an access list already in the yaml node ?
        # better than this: add a routine to the yaml node to
        # abstract an access addition (via device)
        try:
            access_list = domain_or_subsystem["access"]
        except:
            access_list = LopperProp( "access", -1, domain_or_subsystem, [] )
            ## TODO: we shouldn't need to do this, as the node is passed
            ##       to the init. This is a lopper tree bug
            domain_or_subsystem + access_list

        try:
            address = device['addr']
            tnodes = self.sdt.tree.addr_node( address )
            if not tnodes:
                raise Exception( f"No node found for: {device}" )

            # we take the first node, as that will be the parent node
            # in any sets of nodes with a 1:1 mapping, and is likely
            # what we want.
            tnode = tnodes[0]

        except Exception as e:
            _info( f"Exception while looking up node by address: {e}" )
            ## TODO: this may mean it is memory, processing should go
            ##        to a dedicated routine
            return

        try:
            access_list.value.append(
                                      {
                                         "dev": tnode.name,
                                         "spec_name": device["name"],
                                         "label": tnode.label,
                                         "flags": flags
                                      }
                                    )
        except Exception as e:
            _info( f"problem adding access: {e}" )

        #_info( f"device_add: access list updated" )


class isospec(object):

    # If something appears in this map, it is a memory entry, and
    # we need to process it as such.
    #
    # This is a static variable!
    #
    iso_memory_device_map = {
            "DDR0" : ["memory", "memory@.*"],
            "OCM.*" : ["sram", None],
            ".*TCM.*" : ["sram", None]
    }

    def __init__( self, json_file = None ):
        self.json = None
        self.json_tree = None
        self.default_settings = {}
        self.subsystems = {}
        self.domains = {}

        self.device_dict = {}
        self.smid_dict = {}
        # trackers are indexed by subsystem or domain name, then by
        # device name
        self.trackers = {}
        if json_file:
            self.json_read( json_file )
            self.setup()


    @classmethod
    def memory_type( cls, name ):
        mem_found = None
        for n,v in isospec.iso_memory_device_map.items():
            if re.search( n, name ):
                mem_found = v

        if mem_found:
            return mem_found[0]

        return "memory"

    @classmethod
    def memory_dest( cls, name ):
        mem_found = None
        for n,v in isospec.iso_memory_device_map.items():
            if re.search( n, name ):
                mem_found = v

        if mem_found:
            return mem_found[1]

        return ""

    def json_read( self, json_file ):
        # convert the spec to a LopperTree for consistent manipulation
        self.json = LopperJSON( json=json_file )
        self.json_tree = self.json.to_tree()

    def setup( self ):
        if self.json_tree:
            # this is the default settings for devices, it also creates
            # a refcount dictionary
            self.default_settings = self.isospec_subsystem( "/default_settings/subsystems/default" )
            # the below are the global list of devices
            self.device_dict, self.smid_dict = self.device_collect()

            s = self.json_tree["/design/subsystems"]

            ## Build a dictionary of subsystems
            for subsystem in self.json_tree["/design/subsystems"].children():
                self.subsystems[subsystem.name] = {}
                # _info( f"subsystem: {subsystem}" )
                try:
                    # does it have access ?
                    subsystem_access = subsystem["access"]
                    for a in subsystem_access:
                        access_dest = self.access_target( a )
                        try:
                            # TODO: these trackers aren't really needed, consider
                            #       dropping them. We have the dedicated "trackers"
                            self.subsystems[subsystem.name][access_dest["name"]] = {
                                                                                     "refcount": 0,
                                                                                     "access": access_dest
                                                                                    }
                        except Exception as e:
                            _info( f"Exception creating tracker: {e}" )
                            continue

                    try:
                        domain_parent = self.json_tree[subsystem.abs_path + "/domains"]
                        for d in domain_parent.children():
                            self.domains[d.name] = {}
                            # this allows quick access to the domains dictionaries, which are kept
                            # in a flat dictionary, also for easy access
                            self.subsystems[subsystem.name]["domain:" + d.name] = self.domains[d.name]

                            domain_access = d["access"]
                            for a in domain_access:
                                access_dest = self.access_target( a )
                                try:
                                    # if we have name collisions, this either needs to
                                    # be nested, or we go with sysystem.name:d.name for
                                    # dictionary index
                                    ## TODO: similarly,we don't likely need the tracker, but
                                    ##       we do look these up later by the dictionary and name
                                    self.domains[d.name][access_dest["name"]] = {
                                                                                   "refcount" : 0,
                                                                                   "subsystem": subsystem.name,
                                                                                   "access": access_dest
                                                                                }
                                except Exception as e:
                                    _info( f"exception while setting up domain tracking: {e}" )

                    except:
                        pass
                except:
                    pass

    def isospec_subsystem( self, name ):
        subsystem_dict = {}

        json_tree = self.json_tree

        try:
            subsystem_node = json_tree[name]
        except:
            _warning( f"no defaults found under: {name}" )

        try:
            default_access = subsystem_node["access"]
            #access_list = []
            for d in range(len(default_access)):
                element =  default_access[d]
                # access_list.append( default_access[d] )
                try:
                    dname = element["name"]
                    try:
                        dtype = element["type"]
                    except:
                        dtype = "device"

                    try:
                        destinations = element["destinations"]
                    except:
                        try:
                            destinations = element["SMIDs"]
                        except:
                            destinations = []

                    try:
                        flags = element["flags"]
                    except:
                        flags = {}

                    _info( f"[{name}/access] device found:" )
                    _info( f"    name: {dname}" )
                    _info( f"    type: {dtype}" )
                    _info( f"    destinations: {destinations}" )
                    _info( f"    flags: {flags}" )

                    subsystem_dict[dname] = {
                        "refcount" : 0,
                        "name":  dname,
                        "type":  dtype,
                        "dests": destinations,
                        "flags": flags,
                        "json": element
                        }
                except Exception as e:
                    _info( f"exception: {e}" )
        except:
            _warning( f"subsystem: {name} has no access" )

        return subsystem_dict

    def device_collect( self ):
        device_dict = {}
        smid_dict = {}
        isospec_json_tree = self.json_tree

        _info( f"collecting all possible devices" )
        try:
            # /design can have destinations as well
            design = isospec_json_tree["/design"]
            # otherwise it is in the cells
            design_cells = isospec_json_tree["/design/cells"]
        except:
            _warning( "no design/cells found in isolation spec" )
            return device_dict

        cell_list = [ design ]
        cell_list.extend( design_cells.children() )
        #cell_list = [ design_cells.children() ]
        for cell in cell_list:
        #for cell in design_cells.children():
            try:
                dests = cell["destinations"]
                _debug( f"processing cell: {cell.name}" )
                _debug( f"           destinations {dests.abs_path} [{len(dests)}]" )
                for d in range(len(dests)):
                    dest = dests[d]
                    _debug( f"                dest: {dest}" )
                    # A device has to have a nodeid for us to consider it, since
                    # otherwise it can't be referenced. The exception to this is
                    # memory, since memory entries never have nodeids.
                    try:
                        nodeid = dest["nodeid"]
                        device_dict[dest["name"]] = {
                                                      "refcount": 0,
                                                      "dest": dest
                                                    }
                    except:
                        try:
                            is_it_mem = dest["mem"]
                            # _info( f"we found memory: {dest}" )
                        except:
                            ## We could do the second regex match on other devices
                            ## to see if they are memory. i.e. DDRxy ..
                            is_it_mem = False

                        # this may be controlled by a command line option
                        # in the future
                        skip_memory = False

                        ## Q: We need to decide if memory always shows up in the global
                        ##    device list, even without a nodeid. since we are only checking
                        ##    for nodeid on non-memory flagged entries below.
                        if is_it_mem:
                            if not skip_memory:
                                device_dict[dest["name"]] = {
                                    "refcount": 0,
                                    "dest": dest
                                }
                            else:
                                _debug( "                memory detected, but skip is set" )
                        else:
                            # no nodeid, skip
                            _debug( f"                   ** destination '{dest}' device has no nodeid, skipping" )
                            # os._exit(1)

                dests = cell["SMIDs"]
                _debug( f"           SMIDs {dests.abs_path} [{len(dests)}]" )
                for d in range(len(dests)):
                    dest = dests[d]
                    _debug( f"                dest: {dest}" )
                    try:
                        name = dest["name"]
                        smid_dict[name] = {
                            "refcount": 0,
                            # Could be renmaed to "dests" to match the subsystem tracker type
                            "dest": dest
                            }
                    except:
                        _debug( f"                    skipping dest {dest} ({e})" )

            except:
                    continue


        return device_dict, smid_dict

    def access_target( self, access_entry ):
        access = {}

        # does it have "same_as_default" ? in that case
        # we look up further, otherwise return what was
        # passed in. This allows the caller to abstract
        # where there access definition comes from
        try:
            name = access_entry["same_as_default"]
            access = self.default_settings[name]["json"]
        except:
            access = access_entry

        return access

    def access_flags( self, access, translate=False ):
        flags = {}
        try:
            flags = access["flags"]
            # placeholder for flag translation / mapping, for
            # now we just return the raw flags
        except:
            pass

        return flags

    ## just returns the names, not the device
    def dests( self, access ):
        destlist = []
        try:
            try:
                destinations = access["destinations"]
                destlist.extend( destinations )
                # for now, we only allow one type of destination
                return destlist
            except:
                pass

            try:
                #_info( f"checking smids: {access}" )
                destinations = access["SMIDs"]
                destlist.extend( destinations )
                # for now, we only allow one type of destination
                return destlist
            except:
                pass
        except:
            pass

        return destlist

    # find a device by access, optionally getting the
    # flags as well
    def access_devices( self, access, return_flags=False ):
        destlist = []
        try:
            try:
                destinations = access["destinations"]
                dests = self.devices( destinations )
                destlist.extend( dests )
            except:
                pass

            try:
                #_info( f"checking smids: {access}" )
                destinations = access["SMIDs"]
                for d in destinations:
                    dests = self.devices( d )
                    destlist.extend( dests )
            except:
                pass
        except:
            pass

        return destlist

    ## find a device by name
    def devices( self, device_name_list ):
        devlist = []
        try:
            if self.device_dict:
                for d in device_name_list:
                    device = self.device_dict[d]
                    devlist.append( device['dest'] )
        except:
            pass

        return devlist

    ## find a cpu by name
    ## TODO: we could just make devices() try this if it
    ##       fails in device lookup. That way we don't push
    ##       the type detection onto the caller
    def cpus( self, cpu_name_list ):
        cpulist = []
        try:
            if self.smid_dict:
                for c in cpu_name_list:
                    cpu = self.smid_dict[c]
                    cpulist.append( cpu['dest'] )
        except:
            pass

        return cpulist

    def is_subsystem( self, name ):
        sub = self.subsystem( name )
        if sub:
            return True

        return false

    def subsystem( self, name = None ):
        """ returns all design subsystems, or a specific design
            subsystem if a name is passed
        """
        if not self.json_tree:
            return []

        try:
            nodes = self.json_tree["/design/subsystems"]
            if name:
                for n in nodes.children():
                    if n.name == name:
                        return [ n ]
                return []
            else:
                return nodes.children()
        except:
            return []


    def subsystem_container( self, spec_node ):
        """ finds the subsystem that contains a given node
        """
        subsystem = None
        spec_parent_node = spec_node.parent
        while spec_parent_node:
            try:
                id = spec_parent_node["id"].value
                subsystem = spec_parent_node
            except Exception as e:
                # no id, it isn't a subsystem
                pass

            spec_parent_node = spec_parent_node.parent

        return subsystem

    def domain( self, subsystem, domain_name = None ):
        """ gets all the domains of a subsystem, if a name is
            passed, return a specific domain of the subsystem
        """
        try:
            # this could be a list comprehension
            domains = subsystem.children()
            if not domain_name:
                for d in domains:
                    return d.children()
            else:
                for d in domains:
                    for dd in d.children():
                        if dd.name == domain_name:
                            return [dd]

                return []
        except:
            return []

        return []

    def tracker_init( self, subsystem_name ):
        # The node indexed dictionary will be device names and a
        # True/False if it is referenced.
        self.trackers[subsystem_name] = {
                                          "mem" : {},
                                          "dev" : {},
                                          "cpu" : {}
                                        }

    def track_ref( self, tracker_name, dev, ttype = "dev", value = True ):
        ## when called with value as the default, we are initializing
        ## the tracking entry for a given name. Call with "true" to
        ## reference a device
        try:
            tracker = self.trackers[tracker_name][ttype]
        except Exception as e:
            _debug( f"No tracker is initialized for {tracker_name}, cannot track device {dev}: {e}" )
            debug_exit()
            return

        # _info( f"tracking!!: {tracker_name} ... {dev}" )
        tracker[dev["name"]] = value

    def tracker_get( self, tracker_name, ttype="dev" ):
        if ttype:
            try:
                tracker = self.trackers[tracker_name][ttype]
                return tracker
            except:
                return {}
        else:
            try:
                trackers = self.trackers[tracker_name]
                return trackers
            except:
                return {}

    def isodomain_convert( self, spec_node, domains_tree ):
        """ converts an isolation spec domain to a yaml domain
        """

        ## TODO: we should add the subsystem name, since there's
        ##       no guarantee at all that the domain names are unique

        yaml_node = domains_tree.tree.nodes( spec_node.name )[0]

        containing_subsystem = self.subsystem_container( spec_node )

        # The node indexed dictionary will be device names and a
        # True/False if it is referenced.
        self.tracker_init( spec_node.name )

        try:
            _info( f"isodomain_convert: {spec_node.name}" )

            domain = spec_node

            try:
                id = spec_node["id"]
                yaml_node + deepcopy( id )
            except Exception as e:
                pass

            try:
                access_list = spec_node["access"]
                for access in access_list:
                    _info( f"           access: ({type(access)} {access}" )
                    access = self.access_target( access )
                    try:
                        access_type = access["type"]
                    except:
                        access_type = "device"

                    # _info( f"               type: {access_type}" )

                    if access_type == "device":
                        try:
                            dests = self.dests( access )
                            # _info( f"                     devie dests: {dests}" )
                            devices = self.devices( dests )
                            # _info( f"                     devices: {devices}" )

                            ## The device might be memory. We need to
                            ## check if if has the "mem": "true" flag,
                            ## or if it matches a regex.
                            for dev in devices:
                                try:
                                    mem_flag = dev["mem"]
                                except:
                                    mem_flag = False

                                if mem_flag:
                                    # _info( f"                   device is memory: {dev}" )
                                    domains_tree.memory_add( yaml_node, dev )
                                    self.track_ref(spec_node.name, dev, "mem", False)
                                else:
                                    # add the devices to the node
                                    flags = domains_tree.device_flags_map( dev, access )
                                    domains_tree.device_add( yaml_node, dev, flags )

                                    # TODO: track memory and cpus as well
                                    #
                                    # initialize ourself to False, any subdomains will toggle this
                                    # to true if they do refernece it (the second call here)
                                    self.track_ref(spec_node.name, dev, "dev", False)
                                    if containing_subsystem:
                                        try:
                                            # this is the containing subsystem, track the reference
                                            # there
                                            self.track_ref(containing_subsystem.name,dev, "dev" )
                                        except:
                                            pass

                            try:
                                mem = yaml_node["memory"]
                                if len(mem) == 1:
                                    # force an empty entry if there's only one memory, since this
                                    # ensures that the yaml will be in list form. If we don't
                                    # do this, then assists down the pipeline have to deal with
                                    # either lists or yaml nodes
                                    mem.value.append( {} )
                            except:
                                pass

                        except:
                            pass
                    elif access_type == "cpu_list":
                        _info( f"processing cpu list" )
                        try:
                            dests = self.dests( access )
                            # _info( f"                     cpus dests: {dests}" )
                            cpus = self.cpus( dests )
                            # _info( f"                     cpus: {cpus}" )

                            for c in cpus:
                                # add the cpus to the node
                                domains_tree.cpu_add( yaml_node, c )
                                self.track_ref(spec_node.name, c, "cpu", False)

                            if len(cpus) == 1:
                                # force an empty entry if there's only one cpu, since this
                                # ensures that the yaml will be in list form. If we don't
                                # do this, then assists down the pipeline have to deal with
                                # either lists or yaml nodes
                                cpu_list = yaml_node["cpus"]
                                cpu_list.value.append( {} )

                        except Exception as e:
                            _info( f"exception procesing cpus: {e}" )

                    elif access_type == "ss_management":
                        _info( f"spec type ss_management: {access}" )
                        _info( f"no action required, skipping" )
                    elif access_type == "ss_permissions":
                        _info( f"spec type ss_permissions: {access}" )
                        _info( f"no action required, skipping" )
                    else:
                        _error( f"unknown spec type: {access_type}" )

            except Exception as e:
                # no access
                # _info( f"no access found: {e}" )
                pass

        except Exception as e:
            _info( f"isosdomain_convert: no domain '{domain_name}' found" )

## kept for reference until SRAM is fixed
# def isospec_process_memory( name, dest, sdt, json_tree, debug = False ):
#     _info( f"isospec_process_memory: {dest}" )

#     try:
#         # is it explicitly tagged as memory ?
#         mem_dest_flag = dest["mem"]
#         memory_dest = "memory@.*"
#         memory_type = "memory"
#     except:
#         # if it isn't, we have a regex match to figure
#         # out what type of memory it may be
#         memory_dest = isospec_memory_dest( name )
#         memory_type = isospec_memory_type( name )
#         if memory_type == "sram":
#             os._exit(1)

#     possible_mem_nodes = []
#     if memory_type == "memory":
#         # we have a node to lookup in the device tree
#         try:
#             # Q: here's the problem. For SRAM this is called because
#             #    there wasn't an address match when we looked up the
#             #    memory. BUT, there are memory nodes in the tree, so
#             #    this will return options. The SRAM may fall into one
#             #    of the ranges, so we use that start/end address which
#             #    then updates in the yaml. But that means the start
#             #    address, etc, are lost (as is the type).
#             #
#             # Q: should we only declare a match if the start address
#             #    matches, versus just being in the start + size of
#             #    memory ? Only for SRAM or for all types of memory ?
#             #
#             possible_mem_nodes = sdt.tree.nodes(memory_dest)
#             if debug:
#                 _info( f"possible mem nodes: {possible_mem_nodes}" )
#         except Exception as e:
#             _info( f"Exception looking for memory: {e}" )

#     # if there's no possible device nodes, then we double
#     # check the type mapping
#     if not possible_mem_nodes:
#         memory_dest = isospec_memory_dest( name )
#         memory_type = isospec_memory_type( name )

#     # Q: do we need to check if it is fully contained ?
#     # We really only need the address for now, since we don't care if
#     # this is fully contained in a memory range .. but we might in the
#     # future if we don't end up adjusting the device tree, or if there
#     # are multiple possible memory nodes, we could find the best fit.
#     dest_start = int(dest['addr'],16)
#     dest_size = dest['size']

#     memory_node = None
#     memory_list = []
#     if memory_type == "memory":
#         _info( f"  memory: {memory_dest}" )

#         ##
#         ## This may no longer be correct. But this is looking at the
#         ## isospec memory, and seeing which system device tree nodes
#         ## it may fall into. Those nodes are then used to create
#         ## entries in the domains.yaml based on what we return here.
#         ##
#         ## If we are trying to adjust the SDT based on what is in the
#         ## isospec, then this maybe not be correct. We need to
#         ## clarify.
#         ##
#         memory_node_found=False
#         for n in possible_mem_nodes:
#             _info( f"  possible_mem_nodes: {n.abs_path} type: {n['device_type']}" )
#             try:
#                 if "memory" in n["device_type"].value:
#                     reg = n["reg"]
#                     _info( f"    reg {reg.value}" )

#                     # we could do this more generically and look it up
#                     # in the parent, but 2 is the default, so doing
#                     # this for initial effort
#                     address_cells = 2
#                     size_cells = 2

#                     reg_chunks = lopper_lib.chunks( reg.value, address_cells + size_cells )
#                     for reg_chunk in reg_chunks:
#                         start = reg_chunk[0:address_cells]
#                         start = lopper.base.lopper_base.encode_byte_array( start )
#                         start = int.from_bytes(start,"big")

#                         size =  reg_chunk[address_cells:]

#                         size = lopper.base.lopper_base.encode_byte_array( size )
#                         size = int.from_bytes(size,"big")

#                         _info( f"    start: {hex(start)} size: {hex(size)}" )

#                         ##
#                         ## Q: Should we be checking if our address
#                         ##    falls into this range ? or should be be
#                         ##    checking if the range should be adjusted
#                         ##    ? Something else ?
#                         ##
#                         ## Checking the range seems correct, and then
#                         ## when we add this to domains.yaml, it will
#                         ## adjust the device during final processing
#                         ##
#                         ## Without this, we get multiple mem entries
#                         ## per isospec target, and that is not useful
#                         ##
#                         ## Q: Should the start/size be the memory
#                         ##    start/size from the device tree, or from
#                         ##    the isospec ?  if they aren't from the
#                         ##    isospec, we don't have the information
#                         ##    to adjust the output devie tree.
#                         ##
#                         if dest_start >= start and dest_start <= start + size:
#                             _info( f"    memory is in range, adding: {name}" )
#                             memory_node_found = True
#                             memory_list.append( { "dev": name,          # remove before writing to yaml (if no roundtrip)
#                                                   "spec_name": name,    # remove before writing to yaml (if no roundtrip)
#                                                   "start": hex(start),
#                                                   "size": hex(size)
#                                                  }
#                                                )

#             except Exception as e:
#                 _debug( f"Exception {e}" )

#         if not memory_node_found:
#             # we could create one to match if this is the case, but for now, we warn.
#             _warning( f"no memory node found that contains '{dest}'" )

#     elif memory_type == "sram":
#         # no memory dest
#         _info( f"sram memory type: {memory_dest}" )
#         address = dest['addr']
#         tnode = sdt.tree.addr_node( address )
#         if tnode:
#             # pull the start and size out of the device tree node
#             # don't have a device tree to test this yet
#             _warning( f"    target node {tnode.abs_path} found, but no processing is implemented" )
#         else:
#             size = dest['size']
#             # size = humanfriendly.parse_size( size, True )
#             start = address
#             _info( f"    sram start: {start} size: {size}" )
#             memory_list.append( {
#                                   "dev": name,        # remove before writing to yaml (if no roundtrip)
#                                   "spec_name": name,  # remove before writing to yaml (if no roundtrip)
#                                   "start": start,
#                                   "size": size
#                                 }
#                               )

#     return memory_list

def isospec_domain( tgt_node, sdt, options ):
    """assist entry point, called from lopper when a node is
       identified, or passed as a command line assist
    """
    try:
        verbose = options['verbose']
    except:
        verbose = 0

    try:
        args = options['args']
    except:
        args = []

    lopper.log._init( __name__ )

    opts,args2 = getopt.getopt( args, "mpvh", [ "help", "audit", "verbose", "permissive", "nomemory" ] )

    if opts == [] and args2 == []:
        usage()
        sys.exit(1)

    audit = False
    memory = True
    for o,a in opts:
        # print( "o: %s a: %s" % (o,a))
        if o in ('-m', "--nomemory" ):
            memory = False
        elif o in ('-v', "--verbose"):
            verbose = verbose + 1
        elif o in ('-c', "--compare"):
            compare_list.append( a )
        elif o in ('-p', "--permissive"):
            permissive = True
        elif o in ("--audit"):
            audit = True
        elif o in ('-h', "--help"):
            # usage()
            sys.exit(1)

    if verbose:
        lopper.log._level( logging.INFO, __name__ )
    if verbose > 1:
        lopper.log._level( logging.DEBUG, __name__ )
        #logging.getLogger().setLevel( level=logging.DEBUG )

    _info( f"cb: isospec_domain( {tgt_node}, {sdt}, {verbose} )" )

    if sdt.support_files:
        isospecf = sdt.support_files.pop()
    else:
        try:
            if not args2[0]:
                _error( "isospec: no isolation specification passed" )
            isospecf = args2.pop(0)
        except Exception as e:
            _error( f"isospec: no isolation specification passed: {e}" )

    domain_yaml_file = "domains.yaml"
    try:
        domain_yaml_file = args2.pop(0)
    except:
        pass

    try:
        iso_file = Path( isospecf )
        iso_file_abs = iso_file.resolve( True )
    except FileNotFoundError as e:
        _error( f"ispec file {isospec} not found" )

    # convert the spec to a LopperTree for consistent manipulation
    json_in = LopperJSON( json=iso_file_abs )
    json_tree = json_in.to_tree()

    spec = isospec( iso_file_abs )

    ## self test block
    # access_node = spec.json_tree["/design/subsystems/default"]["access"]
    # _info( f"access node type: {type(access_node)}" )
    # for a in access_node:
    #     _info( f"a: {a}" )
    #     access_dest = spec.access_target( a )
    #     _info( f"access details: {access_dest}" )
    #     try:
    #         pdests = access_dest["destinations"]
    #         devices = spec.devices(pdests)
    #         _info( f"   devices: {devices}" )
    #     except:
    #         # no destination
    #         pass
    #     dests = spec.access_devices( access_dest )
    #     _info( f"   access destination devices: {dests}" )
    #
    # _info( "\n\n\n\n" )
    #
    # d = spec.json_tree["/design/subsystems/default/domains"]
    # for domain in d.children():
    #     _info( f"domain: {domain}" )
    #     for a in domain["access"]:
    #         access_dest = spec.access_target( a )
    #         #_info( f"access details: {access_dest}" )
    #         try:
    #             pdests = access_dest["destinations"]
    #             devices = spec.devices(pdests)
    #             _info( f"   devices: {devices}" )
    #         except:
    #             # no destinations
    #             pass
    #
    # for d in spec.json_tree["/design/subsystems/default/domains"]:
    #    _info( f"d: {d}" )
    # access_node = spec.json_tree["/design/subsystems/default"]["domains"]
    #
    # for a in range(len(access_node)):
    #     access_entry = access_node[a]
    #     _info( f"a: {access_entry}" )
    #
    # _info( "\n\n" )
    # _info( f"subsystems: {spec.subsystem('default')}" )
    #
    # domains = spec.domain( spec.subsystem('default')[0] )
    # _info( f"domains: {domains}" )
    # apu_domain = spec.domain( spec.subsystem('default')[0], "APU" )
    # _info( f"apu: {apu_domain}" )
    #
    # iso_subsystems = json_tree["/design/subsystems"]
    # try:
    #     iso_domains = json_tree["/design/subsystems/" ]
    # except:
    #     pass
    #
    ## end self test

    # create our domains.yaml tree
    domains = domain_yaml( sdt )

    # process the subsystems in the spec
    for sub in spec.subsystem():
        _info( f"processing: subsystem: {type(sub)} {sub}" )
        subsystem_yaml_node = domains.subsystem_add( sub.name )
        spec.isodomain_convert( sub, domains )

        # does the subsystem have domains ? if so, process them
        sub_domains = spec.domain( sub )
        if sub_domains:
            domain_container_node = domains.node_add( "domains", subsystem_yaml_node )
            for d in sub_domains:
                _info( f"    adding domain: {d.name}" )
                domain_node = domains.domain_add( d.name, domain_container_node, sub["id"] )
                spec.isodomain_convert( d, domains )

    # check our refcounter(s)
    if audit:
        lopper.log._level( logging.INFO, __name__ )
        for sub in spec.subsystem():
            try:
                trackers = spec.tracker_get(sub.name,None)
                _info( "" )
                header = f"Audit for subsystem: {sub.abs_path}"
                header_underline = "=" * len(header)
                _info( header )
                _info( header_underline )
                unrefd = []
                refd = []
                for dev,flag in trackers["dev"].items():
                    if flag:
                        refd.append( dev )
                    else:
                        unrefd.append( dev )

                mrefd = []
                munrefd = []
                for mem,flag in trackers["mem"].items():
                    if flag:
                        mrefd.append( mem )
                    else:
                        munrefd.append( mem )

                _info( f"  referenced devices  : {refd}" )
                _info( f"  unreferenced devices: {unrefd}" )
                _info( f"  referenced memory   : {mrefd}" )
                _info( f"  unreferenced memory : {munrefd}" )
                _info( "" )

                for d in spec.domain( sub ):
                    header = f"    Audit for domain: {d.name}"
                    header_underline = "    " + "-" * (len(header) - 4)
                    _info( header )
                    _info( header_underline )
                    tracker = spec.tracker_get(d.name,None)
                    devs = list(tracker["dev"].keys())
                    mem = list(tracker["mem"].keys())
                    _info( f"      devices: {devs}" )
                    _info( f"      memory : {mem}" )
            except:
                pass

    # write the yaml tree
    _info( f"writing domain file: {domain_yaml_file}" )
    sdt.write( domains.tree, output_filename=domain_yaml_file, overwrite=True )

    return True

