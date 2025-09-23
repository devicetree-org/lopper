#/*
# * Copyright (c) 2020 Xilinx Inc. All rights reserved.
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
import ast
from pathlib import Path
from pathlib import PurePath
from io import StringIO
import contextlib
import importlib
from lopper import Lopper
from lopper import LopperFmt
from lopper.tree import LopperAction
from lopper.tree import LopperProp
from lopper.tree import LopperNode
from lopper.tree import LopperTree
from lopper.yaml import LopperYAML
import lopper
import json
import humanfriendly

sys.path.append(os.path.dirname(__file__))
from openamp import is_openamp_d_to_d
from openamp import openamp_d_to_d_expand


def is_compat( node, compat_string_to_test ):
    if re.search( r"module,subsystem", compat_string_to_test):
        return subsystem
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

def chunks(l, n):
    # For item i in a range that is a length of l,
    for i in range(0, len(l), n):
        # Create an index range for l of n items:
        yield l[i:i+n]

def property_set( property_name, property_val, node, fdt=None ):
    newprop = LopperProp( property_name, -1, None, property_val )
    node += newprop
    if fdt:
        node.sync()

def val_as_bool( val ):
    if val == "False":
        return False
    elif val == "True":
        return True

def is_glob_pattern(string):
    """
    Determines if a string contains glob-like wildcards.

    Args:
        string (str): The string to be checked for glob pattern characters.

    Returns:
        bool: True if the string contains glob pattern characters, otherwise False.
    """
    # Check for presence of glob-related characters (*) and (?)
    return '*' in string or '?' in string

def glob_to_regex(glob_pattern):
    """
    Convert a glob pattern to a regex pattern.

    Args:
        glob_pattern (str): The glob pattern to be converted.

    Returns:
        str: The converted regex pattern.
    """
    # Escape all regex-specific characters except * and ?
    regex_pattern = re.escape(glob_pattern)
    # Replace \* with .*
    regex_pattern = regex_pattern.replace(r'\*', '.*')
    # Replace \? with .
    regex_pattern = regex_pattern.replace(r'\?', '.')
    # Anchor pattern to match the whole string
    return '^' + regex_pattern + '$'

def domain_parent( domain ):
    try:
        parent_name = domain["parent"]
        return parent_name
    except Exception as e:
        return None

def domain_access(node, new_access=None):
    """
    Get or set the access value of a node.

    Args:
        node: The node from which to get or set access.
        new_access (optional): New access value to set. If not provided, will return the current access.

    Returns:
        If new_access is None: the current access value.
        If new_access is provided: None (as it sets the value).
    """
    try:
        access_prop = node.props("access")[0]
    except:
        if new_access:
            # There wasn't an access property in the node, create it
            # and continue processing
            access_prop = LopperProp( "access", -1, node )
            access_prop.pclass = "json"
            node + access_prop
        else:
            return []

    try:
        if isinstance(access_prop.value, list):
            access_prop_string = ','.join(access_prop.value)
        else:
            access_prop_string = access_prop.value
    except Exception as e:
        # it wasn't json, struggle along a bit
        pass

    if new_access is None:
        # If no new access is provided, return the current access value
        access_chunks = json.loads(access_prop_string)
        return access_chunks
    else:
        # Set the new access; assuming new_access is a list of device dictionaries
        try:
            if new_access:
                # we assign to the base python object to avoid the array
                # processing of LopperNode
                access_prop.__dict__["value"] = json.dumps(new_access)
            else:
                access_prop = node['access']
                node - access_prop
        except Exception as e:
            print( "Exception during domain access update: {e}" )

        return None  # No value is returned when setting

from enum import Enum

class Action(Enum):
    ADD = 'add'
    GET = 'get'
    REMOVE = 'remove'

def domain_devices(devices, device_name_or_regex, action: Action):
    """
    Manipulates a list of devices based on a regex match or a list of devices.

    Args:
        devices (list of dict or LopperNode): The list of device dictionaries or a LopperNode object.
        device_name_or_regex (str or list of dict): The regex pattern to match devices
                                                     or a list of device dicts to add/remove.
        action (Action): The action to perform (ADD, GET, REMOVE).

    Returns:
        list: List of matched devices on GET or the remaining devices after REMOVE.
              None for ADD.
    """
    # Handle LopperNode by extracting 'access' property
    if isinstance(devices, LopperNode):
        devices = domain_access(devices)

    if action == Action.GET:
        matched_devices = []
        try:
            matched_devices = [device for device in devices if re.match(device_name_or_regex, device['dev'])]
        except TypeError as te:
            print(f"GET operation failed due to a TypeError: {te}")
        return matched_devices

    elif action == Action.ADD:
        try:
            if isinstance(device_name_or_regex, list):
                devices.extend(device_name_or_regex)
        except TypeError as te:
            print(f"ADD operation failed due to a TypeError: {te}")
        return devices

    elif action == Action.REMOVE:
        try:
            if isinstance(device_name_or_regex, list):
                to_remove = [device['dev'] for device in device_name_or_regex]
                devices = [device for device in devices if device['dev'] not in to_remove]
            else:
                devices = [device for device in devices if not re.match(device_name_or_regex, device['dev'])]
        except TypeError as te:
            print(f"REMOVE operation encountered a TypeError: {te}")

        return devices

    else:
        raise ValueError("Invalid action. Use Action.ADD, Action.GET, or Action.REMOVE.")


# this is called from a lop or assist to move/copy wildcard devices to
# a domain that is using a glob
def wildcard_devices( tree, domains_node ):
    verbose = True

    for domain in domains_node.subnodes():
        if verbose:
            print( f"[DBG]: wildcard device expansion: processing {domain.abs_path}" )

        try:
            access_chunks = domain_access( domain )
            remove_list = []
            access_list_new = []
            for a in access_chunks:
                # display the access element
                if verbose:
                    print( f"[DBG]: wildcard: processing: {a}" )

                try:
                    dev = a["dev"]
                    if is_glob_pattern( dev ):
                        # is there a parent domain ? (it is required for wildcards)
                        # The yaml input validation should have found any misses, but
                        # dts inputs are also possible, so we double check here
                        d_parent = domain_parent( domain )
                        d_parent_name = d_parent.value.split('/')[-1]
                        d_parent_path = d_parent.value

                        if d_parent_path.startswith('/'):
                            # if the path isn't valid, an exception will be raised, which
                            # we catch and indicate the parent cannot be found
                            try:
                                parent_domain = tree[d_parent_path]
                            except:
                                parent_domain = None
                        else:
                            try:
                                parent_domain = tree.nodes( d_parent_path + "$" )
                                parent_domain = parent_domain[0]
                            except:
                                parent_domain = None

                        if parent_domain:
                            ## We need to get the access devices from
                            ## the parent and copy them into our
                            ## domain
                            try:
                                parent_access = domain_access( parent_domain )
                            except Exception as e:
                                print( f"[WARNING]: parent domain ({parent_domain.abs_path}) has no devices")

                            # the spec says globs, but if we convert to a regex, the access
                            # search is easy
                            regex = glob_to_regex( dev )
                            devs = domain_devices( parent_access, regex, Action.GET )
                            remaining_devs = domain_devices( parent_access, devs, Action.REMOVE )

                            # update the parent, since we aren't iterating it, we are ok doing this
                            # immediately.
                            if verbose:
                                print( f"[DEBUG]: after access: remaining devs: {remaining_devs}" )

                            domain_access( parent_domain, remaining_devs )

                            if verbose:
                                print( f"[INFO]: parent domain ({parent_domain.abs_path}) matched devices: {devs}" )

                            # we can't modify the chunks while iterating, so we
                            # queue the glob (what got is in here) to be deleted
                            remove_list.append( a )
                            # And the parent domain devices (what the glob matched) to be added
                            access_list_new.extend( devs )
                        else:
                            # no parent domain, exit
                            print( f"[ERROR]: glob detected, but no parent domain was found" )
                            os._exit(1)
                    else:
                        # Non-glob device, just copy it or inspect it.
                        # Currently We aren't doing any checking.
                        if verbose:
                           print( f"[INFO]: non wildcard access, copying: {dev}")
                        access_list_new.append( a )

                except Exception as e:
                    # This catches an error if there is no "dev" in:
                    #     dev = a["dev"]
                    # We just move onto the next item in this case
                    pass

            if remove_list:
                # remove the collected devices from the access json dictionary, these
                # are currently only the wildcard dev: that was found
                if verbose:
                    print( f"[INFO]: domain (domain.abs_path): removing: {remove_list}" )

                remaining_access = domain_devices( access_chunks, remove_list, Action.REMOVE )
                # and then store the updated list into the domain
                domain_access( domain, remaining_access )

            if access_list_new:
                # Use this if there's no collection of all elements while processing
                # the devices.
                # if access_list_new != access_chunks:
                #     # if our device list is different than the one we started with, we
                #     # need to store it
                #     # We could probably just collect the new devices that we brought in from
                #     # the glob an extend the list
                #     if verbose:
                #         print( f"[INFO]: domain (domain.abs_path): updating: {access_list_new}" )

                #     # we may have just updated it above with the remove, so fetch it again
                #     existing_access = domain_access( domain )
                #     # add the new items to the front
                #     existing_access[:0] = access_list_new
                #     domain_access( domain, existing_access )
                #     domain.resolve()
                if access_list_new != access_chunks:
                    # if our device list is different than the one we started with, we
                    # need to store it
                    # We could probably just collect the new devices that we brought in from
                    # the glob an extend the list
                    if verbose:
                        print( f"[INFO]: domain ({domain.abs_path}): updating: {access_list_new}" )

                    domain_access( domain, access_list_new )

        except Exception as e:
            print( f"[ERROR]: Exception: {e}")
            os._exit(1)

def firewall_expand( tree, subnode, verbose = 0 ):
    if verbose:
        print( f"[DBG]: firewall_expand: {subnode.abs_path}" )

    firewall_conf_list = []
    if subnode.name == "firewallconf":
        # we got a node that is the firewallconf
        try:
            firewall_domain = subnode["domain"][0]
        except:
            firewall_domain = None

        try:
            firewall_block = subnode["block"][0]
            try:
                firewall_block = int(firewall_block)
            except Exception as e:
                pass
        except:
            firewall_block = 0

        firewall_conf_list.append( { 'block': firewall_block, 'domain': firewall_domain } )

        # delete the node, it will be converted to a property
        subnode.tree - subnode

        firewall_target_node = subnode.parent
    else:
        # we have a node with a firewallconf property
        try:
            if subnode["firewallconf"]:
                prop = subnode["firewallconf"]
                for i in range(len(prop)):
                    firewall_conf_list.append( prop[i] )

                # delete the property, it has been replaced
                subnode.delete( 'firewallconf' )

                firewall_target_node = subnode
            else:
                print( f"[WARNING]: unrecognized node passed for firewallconf expansion: {subnode.abs_path}" )
                return
        except:
            print( f"[WARNING]: unrecognized node passed for firewallconf expansion: {subnode.abs_path}" )
            return
    #
    # The first cell is a link to a node of a bus mastering device (or a domain).
    #
    # The second cell is the action, values can be allow (1), block (0), and block-desirable (2):
    #
    #  block [0]: access is blocked
    #  allow [1]: access is allowed
    #  block-desirable [2]: "block if you can"
    #
    # The third cell is a priority number: the priority of the rule when block-desirable is specified, otherwise unused.

    firewall_conf_generated_list = []
    for item in firewall_conf_list:
        try:
            firewall_block = item['block']
        except:
            firewall_block = 0
        try:
            firewall_domain = item['domain']
        except:
            firewall_domain = None

        if verbose:
            print( f"[DBG]: firewall expand: {subnode.abs_path} cfg: domain: {firewall_domain} block: {firewall_block}")

        if firewall_block and not firewall_domain:
            if verbose:
                print( "[DBG]: firewall: block and no domain, generating firewallconf-default" )

            firewall_block_priority = 0
            firewall_block_type = 0
            try:
                firewall_block_priority = int(firewall_block)
            except:
                # it is a string
                if re.search( r"always", firewall_block ):
                    # aka "block"
                    firewall_block_type = 0
                elif re.search( r"never", firewall_block ):
                    # aka "allow"
                    firewall_block_type = 1

            # the first item is "block" (0) and the second is the priority (default 0), so
            # we use <firewall_block_type firewall_block_priority>
            firewall_prop = LopperProp( "firewallconf-default", -1, subnode.parent, [ firewall_block_type, firewall_block_priority ] )

            firewall_target_node + firewall_prop

        elif firewall_block and firewall_domain:
            if verbose:
                print( "[DBG]: firewall: block and domain, generating firewallconf" )

            try:
                tgt_node = tree.lnodes( firewall_domain )[0]
            except:
                if verbose:
                    print( f"[DBG]: WARNING: could not find node {firewall_domain}" )
                tgt_node = None

            firewall_priority = 0

            tgt_node_phandle = 0xdeadbeef
            if tgt_node:
                if tgt_node.phandle == 0:
                    tgt_node_phandle = tgt_node.phandle_or_create()
                    if verbose:
                        print( f"[DBG]: generated phandle {tgt_node.phandle} for node: {tgt_node.abs_path}")
                else:
                    tgt_node_phandle = tgt_node.phandle

            if firewall_block:
                if type(firewall_block) == int:
                    # priority was passed
                    firewall_priority = firewall_block
                    # block
                    firewall_block = 0
                else:
                    if re.search( r"always", firewall_block ):
                        firewall_block = 0
                    elif re.search( r"never", firewall_block ):
                        firewall_block = 1

            firewall_conf_generated_list.append( tgt_node_phandle )
            firewall_conf_generated_list.append( firewall_block )
            firewall_conf_generated_list.append( firewall_priority )

    # we can have more than one firewallconf, so we must append the new list if the property
    # already exists
    if firewall_conf_generated_list:
        firewall_prop = LopperProp( "firewallconf", -1, firewall_target_node, firewall_conf_generated_list )
        firewall_target_node + firewall_prop

def access_expand( tree, subnode, verbose = 0 ):
    ## access processing
    # /*
    # * Access specifies which resources this domain
    # * has access to.
    # *
    # * Link to resource | flags
    # *
    # * The "flags" field is mapping specific
    # *
    # * For memory, reserved-memory, and sram:
    # *   bit 0: 0/1: RO/RW
    # *
    # * Other cases: unused
    # *
    # * In this example we are assigning:
    # * - memory range 0x0-0x8000000 RW
    # * - tcm RW
    # * - ethernet card at 0xff0c0000
    # */
    access_props = subnode.props( "access" )

    if not access_props:
        return

    if not access_props[0]:
        return

    if not access_props[0].value:
        return

    if type(access_props[0].value) == list:
        if not access_props[0].value[0]:
            return
        access_prop_string = access_props[0].value.join()
    else:
        access_prop_string = access_props[0].value

    access_chunks = json.loads(access_prop_string)
    access_list = []

    ap = access_props[0]
    x,field_count = ap.phandle_params()

    try:
        # is there a property that gives us a field count hint ?
        access_field_count = subnode['#access-flags-cells']
        # if it isn't a list, make it one (using the fact that any direct
        # assignming to a property.value is made into a list
        if not type(access_field_count.value) == list:
            access_field_count.value = access_field_count.value

        access_field_count = access_field_count.value[0]
    except Exception as e:
        access_field_count = field_count

    access_field_count = 1
    flag_list = []

    for a in access_chunks:
        dev = a['dev']
        try:
            flags = a['flags']
        except:
            flags = None

        dev_handle = 0xFFFFFFFF
        if dev:
            # this catches missed processing in the other assists
            if dev == "*":
                if verbose:
                    print( f"[WARNING]: found unxpanded glob {dev} in node {subnode.abs_path}" )

            try:
                dev_node = tree.deref( dev )
                if verbose:
                    if dev_node:
                        print( f"[DBG]: found dev node {dev_node} with phandle: {dev_node.phandle}" )
                    else:
                        print( f"[DBG]: WARNING: could not find device: {dev} in device tree" )
            except:
                if verbose:
                    print( f"[DBG]: WARNING: could not find device: {dev} in device tree" )
                dev_node = None

            if dev_node:
                if dev_node.phandle == 0 or dev_node.phandle == -1:
                    dev_node.phandle_or_create()
                    if verbose:
                        print( f"[DBG]: access_expand: generated phandle {dev_node.phandle} for node: {dev_node.abs_path}")
                        dev_node.print()

                dev_handle = dev_node.phandle
            else:
                dev_handle = 0xFFFFFFFF

        flags_value = 0
        if flags:
            try:
                fval = flags['timeshare']
                if fval:
                    flags_value = set_bit( flags_value, 0 )
            except:
                pass

            try:
                fval = flags['allow-secure']
                if fval:
                    flags_value = set_bit( flags_value, 2 )
            except:
                pass

            try:
                fval = flags['read-only']
                if fval:
                    flags_value = set_bit( flags_value, 4 )
            except:
                pass

            try:
                fval = flags['requested']
                if fval:
                    flags_value = set_bit( flags_value, 6 )
            except:
                pass

            try:
                fval = flags['requested-secure']
                if fval:
                    flags_value = set_bit( flags_value, 36 )

            except:
                pass

            try:
                fval = flags['coherent']
                if fval:
                    flags_value = set_bit( flags_value, 37 )
            except:
                pass

            try:
                fval = flags['virtualized']
                if fval:
                    flags_value = set_bit( flags_value, 38 )
            except:
                pass

            # todo: definition: values [0-100] expressed using bits [64-95]
            # try:
            #     fval = flags['qos']
            #     if fval:
            #         flags_value = set_bit( flags_value, 37 )
            # except:
            #     pass


        # save the <phandle> <flags> to the list of values to write
        access_list.append( dev_handle )
        access_list.append( flags_value )
        flag_list.append(flags)


    if verbose:
        # dump the memory as hex
        print( f"[DBG] ({subnode.abs_path}) setting access: [{', '.join(hex(x) for x in access_list)}]" )

    access_orig_prop = LopperProp( "access-json", -1, subnode, ap.value )
    access_orig_prop.pclass = "json"

    subnode + access_orig_prop

    ap.value = access_list

def chosen_expand( tree, chosen_node ):
    # there isn't any specific processing required for chosen
    # at the moment. We just copy it to the main tree as-is. If a
    # chosen node exists, they should be merged.

    # make a deep copy of the node
    chosen_node_copy = chosen_node()
    chosen_node_copy.abs_path = "/chosen"
    chosen_node_copy.resolve()
    tree.add( chosen_node_copy, merge=True )

def reserved_memory_expand( tree, reserved_memory_node ):
    # there isn't any specific processing required for reserved memory
    # at the moment. We just copy it to the main tree as-is. If a
    # reserved-memory node exists, they should be merged.

    # make a deep copy of the node
    reserved_memory_node_copy = reserved_memory_node()
    reserved_memory_node_copy.abs_path = "/reserved-memory"
    reserved_memory_node_copy.resolve()
    tree.add( reserved_memory_node_copy, merge=True )

# handle either sram or memory with use of prop_name arg
def memory_expand( tree, subnode, memory_start = 0xbeef, prop_name = 'memory', verbose = 0 ):
    # /*
    # * 1:1 map, it should match the memory regions
    # * specified under access below.
    # *
    # * It is in the form:
    # * memory = <address size address size ...>
    # */
    try:
        mem = []
        prop = subnode[prop_name]
        for i in range(0,len(prop)):
            mem.append( prop[i] )

        mem_list = []
        for m in mem:

            # skip empty memory entries
            if not m:
                continue

            try:
                start = str(m['start'])
            except:
                start = str(int(memory_start))
            try:
                size = str(m['size'])
            except:
                size = str(int(0xbeef))

            if 'flags' in m.keys():
                flags = str(m['flags'])
                flags_names = LopperProp(prop_name+'-flags-names',value = str(flags))
                subnode + flags_names

            if verbose:
                print( f"memory expand: start/size as read: {start}/{size}")
            try:
                start = humanfriendly.parse_size( start, True )
            except:
                try:
                    start = int(start,16)
                except:
                    start = int(start)
            try:
                size = humanfriendly.parse_size( size, True )
            except:
                try:
                    size = int(size,16)
                except:
                    size = int(size)

            if verbose:
                print( f"memory expand: start/size as converted: {start}/{size}")

            mem_list.append(int(start))
            mem_list.append(int(size))

    except Exception as e:
        # print( "Exception expanding memory: %s" % e )
        mem_list = [0xdead, 0xffff ]

    if verbose:
        # dump the memory as hex
        print( f"[DBG] memory: [{', '.join(hex(x) for x in mem_list)}]" )

    property_set( prop_name, mem_list, subnode )


def cpu_expand( tree, subnode, verbose = 0):
    ## cpu processing
    cpus = subnode.props( "cpus" )
    if not cpus:
        return

    verbose = 0
    cpus_list = []
    cluster_cpu = None
    for c in cpus[0]:
        # empty dict ? if so, skip
        if not c:
            continue
        if verbose:
            print( f"[DBG]: cpu: {c}" )
            if type(c) == dict:
                print( f"         cluster: {c['cluster']}" )
                print( f"         cpumask: {c['cpumask']}" )
                print( f"         mode: {c['mode']}" )

        if type(c) == dict:
            cluster = c['cluster']
            if 'cluster_cpu' in c.keys():
                cluster_cpu = c['cluster_cpu']
        else:
            cluster = c

        try:
            cluster_node = tree.lnodes( cluster )[0]
        except:
            try:
                cluster_node = tree.nodes( cluster )[0]
            except:
                cluster_node = None

        if cluster_node:
            if cluster_node.phandle == 0:
                ph = cluster_node.phandle_or_create()
                if verbose:
                    print( f"[DBG]: subsystem assist: generated phandle {cluster_node.phandle} for node: {cluster_node.abs_path}")

            cluster_handle = cluster_node.phandle
        else:
            cluster_handle = 0xdeadbeef

        # /*
        # * cpus specifies on which CPUs this domain runs
        # * on
        # *
        # * link to cluster | cpus-mask | execution-mode
        # *
        # * execution mode for ARM-R CPUs:
        # * bit 30: lockstep (lockstep enabled == 1)
        # * bit 31: secure mode / normal mode (secure mode == 1)
        # */
        if type(c) == dict:
            mode_mask = 0
            mode = c['mode']
            if mode:
                try:
                    secure = mode['secure']
                    if secure:
                        mode_mask = set_bit( mode_mask, 31 )
                except:
                    pass

                try:
                    lockstep = mode['lockstep']
                    if lockstep:
                        mode_mask = set_bit( mode_mask, 30 )
                except:
                    pass

                try:
                    el = mode['el']
                    if el:
                        mode_mask = set_bit( mode_mask, 0 )
                        mode_mask = set_bit( mode_mask, 1 )
                except:
                    pass

            mask = c['cpumask']
            try:
                mask = int(mask,16)
            except:
                pass
        else:
            mode_mask = 0x0
            mask = 0x0

        # cpus is <phandle> <mask> <mode>
        if verbose:
            print( f"[DBG]:  cluster handle: {hex(cluster_handle)}" )
            print( f"        cpu mask: {hex(mask)}" )
            print( f"        mode mask: {hex(mode_mask)}" )

        cpus_list.extend( [cluster_handle, int(mask), mode_mask ] )

    if cpus_list:
        cpus[0].value = cpus_list

    if cluster_cpu != None:
        for n in subnode.subnodes():
            if n.name == "domain-to-domain":
                n + LopperProp(name="cluster_cpu", value=cluster_cpu)

    pd_prop_node = [ n for n in cluster_node.subnodes() if n.propval("power-domains") != [''] ]
    if len(pd_prop_node) == 1:
        subnode + LopperProp(name="rpu_pd_val", value=pd_prop_node[0].propval("power-domains"))

    if cluster_node != None and "r5" in cluster_node.name:
        subnode + LopperProp(name="cpu_config_str", value="split" if subnode.propval("cpus")[1] == 1 else "lockstep")
        subnode + LopperProp(name="core_num", value=cluster_node.name[-1])

# sdt: is the system device tree
def subsystem( tgt_node, sdt, options ):
    try:
        verbose = options['verbose']
    except:
        verbose = 0

    try:
        args = options['args']
    except:
        args = []

    if verbose:
        print( f"[INFO]: cb: subsystem( {tgt_node}, {sdt}, {verbose}, {args} )")

    if "generate" in args or "--generate" in args:
        subsystem_generate( tgt_node, sdt, verbose )
    else:
        subsystem_expand( tgt_node, sdt, verbose )

    return True


# sdt: is the system device tree
def subsystem_generate( tgt_node, sdt, verbose = 0):
    if verbose:
        print( f"[INFO]: cb: subsystem_generate( {tgt_node}, {sdt} )")

    tree = sdt.tree
    domain_tree = LopperTree()

    try:
        domain_node = tree["/domains"]
    except:
        domain_node = LopperNode( -1, "/domains" )

    domain_tree.__dbg__ = 4
    domain_tree = domain_tree + domain_node

    subsystem_node = LopperNode( -1 )
    subsystem_node.name = "subsystem1"

    domain_node + subsystem_node

    cpu_prop = None
    for node in sdt.tree:
        try:
            compatibility = node['compatible']
        except:
            compatibility = None

        if compatibility:
            cpu_compat = re.findall(r"(?=("+'|'.join(compatibility.value)+r"))", "cpus,cluster")
            if cpu_compat:
                if not cpu_prop:
                    # Note: The "mask" and "secure" entries are currently placeholders and will be
                    #       calculated differently in the future.
                    cpu_prop = LopperProp( "cpus", -1, subsystem_node,
                                           [ json.dumps( { 'cluster': node.label, "cpu_mask" : 0x3, "mode": { 'secure': True } }) ])
                    cpu_prop.pclass = "json"
                    subsystem_node = subsystem_node + cpu_prop
                else:
                    cpu_prop.value.append( json.dumps( { 'cluster': node.label, "cpu_mask" : 0x3, "mode": { 'secure': True } } ) )

    if verbose > 3:
        tree.__dbg__ = 4

    tree = tree + domain_node

    if verbose > 2:
        print( "[DBG++]: dumping yaml generated default subystem" )
        yaml = LopperYAML( None, domain_tree )
        yaml.to_yaml()

    return True


def do_expand_cdo_flags(tgt_node):
    if 'xilinx,subsystem-v1' in tgt_node.propval("compatible"):
        return True
    return False


def expand_cdo_flags(tgt_node):
    flags_names = []
    flags = []
    # find default flags
    for n in tgt_node.subnodes():
        if n.depth == tgt_node.depth + 2 \
                and n.abs_path == tgt_node.abs_path + "/flags/default":
            default_flags = n
            break

    # for each flag reference
    # update flags' bits along with using default
    for n in tgt_node.subnodes():
        if n.depth == tgt_node.depth + 2:
            #    set all bits according to what is provided
            flags.extend(expand_cdo_flags_bits(n, default_flags))
            flags_names.append(n.name)

    flags_cells = 4
    return [flags_names, flags, flags_cells]


def expand_cdo_flags_bits_first_word(flags_node, ref_flags,
                                     default_flags_node):
    first_keywords = {
        'allow-secure': 2,
        'read-only': 4,
        'requested': 6
    }
    allow_sec = False
    read_only = False
    for key in (list(first_keywords.keys())):

        # determine which node to read flags info from for this bit
        in_flags = flags_node.propval(key) != [''] or \
            key in flags_node.__props__.keys()
        in_default = key in default_flags_node.__props__.keys()
        node = None
        if in_flags == True:
            node = flags_node
        elif in_flags == False and in_default == True:
            node = default_flags_node
        else:
            continue

        if key in node.__props__.keys() and node.propval(key) == [1]:
            if key == 'allow-secure' or key == 'read-only':
                # if true then set to low, as per spec: "0: secure master only."
                # write should be low if read-only
                ref_flags[0] = ref_flags[0] & ~(0x1 << first_keywords[key])
                if key == 'allow-secure':
                    allow_sec = True
                    continue
                elif key == 'read-only':
                    read_only = True
                    continue

            ref_flags[0] |= (0x1 << first_keywords[key])

    # 1st word read policy always allowed
    #ref_flags[0] = ref_flags[0] | (0x1 << 3)

    # 1st word: if allow-secure is false, then raise corresponding bit
    if allow_sec == False:
        ref_flags[0] |= (0x1 << first_keywords['allow-secure'])
    # 1st word: if read only  is false, then raise corresponding bit
    if read_only == False:
        ref_flags[0] |= (0x1 << first_keywords['read-only'])

    # 1st word time share
    if flags_node.propval('timeshare') != ['']:
        ref_flags[0] |= 0x3


def expand_cdo_flags_bits_third_word(flags_node, ref_flags,
                                     default_flags_node):
    third_keywords = {
        'access': 0,
        'context': 1,
        'wakeup': 2,
        'unusable': 3,
        'requested-secure': 4,
        'coherent': 5,
        'virtualized': 6
    }

    for key in third_keywords.keys():
        # determine which node to read flags info from for this bit
        in_flags = flags_node.propval(key) != [''] \
            or key in flags_node.__props__.keys()
        in_default = key in default_flags_node.__props__.keys()

        node = None
        if in_flags == True:
            node = flags_node
        elif in_flags == False and in_default == True:
            node = default_flags_node
        else:
            continue

        if node.propval(key) != [0]:

            # this can only be set if prealloc is set too
            if key == 'requested-secure':
                if (ref_flags[0] & (0x1 << first_keywords['requested'])) == 0:
                    continue

            ref_flags[2] |= 0x1 << third_keywords[key]


# given flags node, return int that is bitmask of relevant bits
def expand_cdo_flags_bits(flags_node, default_flags_node):
    ref_flags = [0x0, 0x0, 0x0, 0x0]

    expand_cdo_flags_bits_first_word(flags_node, ref_flags, default_flags_node)

    # 2nd word is derived
    ref_flags[1] = 0xfffff

    expand_cdo_flags_bits_third_word(flags_node, ref_flags, default_flags_node)

    # 4th word
    qos = 0x0
    if flags_node.propval('qos') != [''] and isinstance(qos, int):
        qos = flags_node.propval('qos')
    elif default_flags_node.propval('qos') != [''] and isinstance(qos, int):
        qos = default_flags_node.propval('qos')
    ref_flags[3] = qos

    return ref_flags


def flags_expand(tree, tgt_node, verbose = 0 ):

    default_flags = None
    flags_names = []
    flags = []
    flags_cells = 1

    if do_expand_cdo_flags(tgt_node):
        [flags_names, flags, flags_cells]  = expand_cdo_flags(tgt_node)
    else:
        # default expansion of flags
        for n in tgt_node.subnodes():
            if n.depth == tgt_node.depth + 2:
                flags_names.append(n.name)
                flags.append(0x0)

    property_set( "flags-cells", flags_cells, tgt_node, )
    property_set( "flags", flags, tgt_node )
    property_set( "flags-names", flags_names, tgt_node )

    tree - tree[tgt_node.abs_path + "/flags"]

    return True

def domain_to_domain_expand(tree, tgt_node, verbose = 0 ):

    if 'openamp,domain-to-domain-v1' not in tgt_node.propval("compatible"):
        return False

    # loop through subnodes that describe various relations between domains
    for n in tgt_node.subnodes():
        if n.depth == tgt_node.depth + 1:
            if verbose:
                print("domain_to_domain_expand: ", tgt_node, n)
            if is_openamp_d_to_d(tree, tgt_node, verbose):
                ret = openamp_d_to_d_expand(tree, n, verbose)

    # remove definitions
    try:
        defn_node =  tree["/definitions"]
        tree - defn_node
    except:
        return True

    return True

def subsystem_expand( tgt_node, sdt, verbose = 0 ):
    if verbose:
        print( f"[INFO]: cb: subsystem_expand( {tgt_node}, {sdt} )")

    tree = sdt.tree

    try:
        domain_node = tree["/domains"]
    except:
        domain_node = LopperNode( -1, "/domains" )

    # add the cells properties
    property_set( "#address-cells", 2, domain_node )
    property_set( "#size-cells", 2, domain_node )
    domain_node.sync()

    #domains = sdt.tree.nodes( "/domains/[^/]*$" )
    domain_count = 0
    for subnode in domain_node.child_nodes.values():
        if subnode._source == "yaml":
            if verbose:
                print( f"[DBG] yaml input dectected, expanding {subnode.abs_path} to full device tree domain" )

            # we flip the name and the label, since the yaml name does not
            # follow device tree conventions.
            name = subnode.name
            subnode.name = f"domain@{domain_count}"
            subnode.label = name
            property_set( "lopper-label-gen1", subnode.label, subnode )

            # set the compatibility
            property_set( "compatible", "openamp,domain-v1", subnode )

            # ensure that the xilinx,subsystem is set property
            property_set( "xilinx,subsystem", 1, subnode )

            ## cpu processing
            cpu_expand( tree, subnode, verbose )

            ## memory processing
            memory_expand( tree, subnode, verbose )

            ## access processing
            access_expand( tree, subnode, verbose )

            subnode.sync()

        domain_count += 1

    return True
