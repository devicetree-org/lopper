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

from .lopper_lib import check_bit_set, clear_bit, chunks, property_set, set_bit, expand_start_size_to_reg

sys.path.append(os.path.dirname(__file__))

# tests for a bit that is set, going fro 31 -> 0 from MSB to LSB
def is_glob_pattern(string):
    """Determine whether ``string`` contains glob-style wildcards.

    Args:
        string (str): Text to be inspected.

    Returns:
        bool: True when ``*`` or ``?`` is present, otherwise False.
    """
    # Check for presence of glob-related characters (*) and (?)
    return '*' in string or '?' in string

def glob_to_regex(glob_pattern):
    """Convert a glob pattern into an anchored regular expression.

    Args:
        glob_pattern (str): Glob expression using ``*`` and ``?`` wildcards.

    Returns:
        str: Regular-expression string that matches the same set of inputs.
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
    """Return the parent reference recorded by a domain node.

    Args:
        domain (LopperNode): Domain node being queried.

    Returns:
        LopperProp | None: Node reference property when present, otherwise None.
    """
    try:
        parent_name = domain["parent"]
        return parent_name
    except Exception as e:
        return None

def domain_access(node, new_access=None):
    """Get or set the JSON-encoded access property for a domain node.

    Args:
        node (LopperNode): Domain node whose access property will be read or
            mutated.
        new_access (list[dict] | None): When provided, updates the property with
            the supplied access list. When omitted, the current access definition
            is returned.

    Returns:
        list[dict]: Current access configuration when ``new_access`` is None.
        None: When the property is updated or removed.
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
    """Manipulate domain device lists based on regex or explicit matches.

    Args:
        devices (list[dict] | LopperNode): Source of device entries to inspect.
        device_name_or_regex (str | list[dict]): Pattern or device list used for
            the requested action.
        action (Action): Action specifying whether to add, find, or remove devices.

    Returns:
        list[dict] | None: Matched or remaining devices for GET/REMOVE actions,
        or None when adding devices.
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
    """Expand wildcard device entries for the supplied domains.

    Args:
        tree (LopperTree): Device tree containing the domain hierarchy.
        domains_node (LopperNode): Root node that aggregates domain definitions.

    Returns:
        None
    """
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
    """Expand firewall helper nodes into generated firewall properties.

    Args:
        tree (LopperTree): Device tree being modified.
        subnode (LopperNode): Node containing firewall configuration details.
        verbose (int): Verbosity level for debug logging.

    Returns:
        None
    """
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
    """Normalize access helper nodes into standard domain properties.

    Args:
        tree (LopperTree): Device tree containing access configuration.
        subnode (LopperNode): Node with a JSON-encoded ``access`` property.
        verbose (int): Verbosity level for diagnostic output.

    Returns:
        None
    """
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

#def chosen_expand( tree, chosen_node ):
#    # there isn't any specific processing required for chosen
#    # at the moment. We just copy it to the main tree as-is. If a
#    # chosen node exists, they should be merged.
#
#    # make a deep copy of the node
#    chosen_node_copy = chosen_node()
#    chosen_node_copy.abs_path = "/chosen"
#    chosen_node_copy.resolve()
#    tree.add( chosen_node_copy, merge=True )

# NOTE TODO reserved_memory_expand note that this present
# implementation does not account for anchors.
# Previously, if an anchor was used, a deep copy of the node
# was copied in, but this made accounting for new nodes and reconciling
# this with existing nodes a very complex. In order to simplify tracking,
# only consider reserved memory if a domain explicitly mentions it
# via 'reserved-memory:' as a property and then pull in referenced nodes.
# anchors are presently marked as a gap in this current implementation.
def reserved_memory_expand( tree, reserved_memory_node ):
    """Generate carveout nodes for the subsystem reserved-memory section.

    Args:
        tree (LopperTree): Device tree being modified.
        reserved_memory_node (LopperNode): Reserved-memory parent node.

    Returns:
        None
    """
    # for each domain calling into this:
    # 1. look up to see if reserved memory already exists
    # 2. store old nodes
    # 3. transform domain's reserved memory list of strings to phandles
    #     where each string matches a rserved memory entry name
    # 4. If a reserved memory node has a start/size tuple then transform
    #    this to a reg property
    try:
        res_mem_node = tree["/reserved-memory"]
        # get reg vals from pre-existing reserved memory
        pre_existing_res_mem_nodes = [ n for n in res_mem_node.subnodes(children_only=True) ]
    except:
        pre_existing_res_mem_nodes = []
        res_mem_node = LopperNode(-1, "/reserved-memory")
        tree.add(res_mem_node)

    new_res_mem_nodes = reserved_memory_node.subnodes(children_only=True)

    resmem_list = reserved_memory_node.props( "reserved-memory" )

    if not resmem_list:
        return

    if not resmem_list[0]:
        return

    if not resmem_list[0].value:
        return

    if type(resmem_list[0].value) == list:
        if not resmem_list[0].value[0]:
            return
        resmem_prop_string = resmem_list[0].value
    else:
        resmem_prop_string = resmem_list[0].value

    # will set to list of phandles instead
    new_res_mem_pval = []

    for dev in resmem_prop_string:
        dev_node = [ n for n in pre_existing_res_mem_nodes if dev == n.name ]
        dev_node = dev_node[0] if len(dev_node) == 1 else None
        if dev_node == None:
            print( f"[DBG]: WARNING: could not find node {dev}" )
        else:
            if dev_node.phandle == 0:
                dev_node.phandle_or_create()
            if dev_node.props("phandle") == []:
               dev_node + LopperProp(name="phandle", value=dev_node.phandle)
            new_res_mem_pval.append(dev_node.phandle)

    # save phandles in domain
    resmem_list[0].value = new_res_mem_pval

     # read start and size. then form 'reg' property for the node.
     # then remove start and size
    for n in pre_existing_res_mem_nodes:
        # handle no map
        if n.propval("no-map") == 1:
            n.delete("no-map")
            n + LopperProp(name="no-map")

        if n.propval("reg") != ['']:
            continue

        raw_start = n.propval("start")
        raw_size = n.propval("size")
        missing_keys = []
        if raw_start == [''] or raw_start is None:
            missing_keys.append("start")
        if raw_size == [''] or raw_size is None:
            missing_keys.append("size")

        reg_cells, _, _ = expand_start_size_to_reg(
            {"start": raw_start, "size": raw_size},
            address_cells=2,
            size_cells=2,
            default_start=0xbeef,
            default_size=0xbeef
        )

        for key in ("start", "size"):
            try:
                n.delete(key)
            except Exception:
                pass

        for key in missing_keys:
            print("WARNING: reserved memory expand: carveout provided without property: ", key, n.abs_path)

        n + LopperProp(name="reg", value=reg_cells)

# handle either sram or memory with use of prop_name arg
def memory_expand( tree, subnode, memory_start = 0xbeef, prop_name = 'memory', verbose = 0 ):
    """Expand helper nodes describing memory regions.

    Args:
        tree (LopperTree): Device tree being modified.
        subnode (LopperNode): Node containing memory description metadata.
        memory_start (int): Default address used when the source omits a value.
        prop_name (str): Property name created on the target node.
        verbose (int): Verbosity level for diagnostic output.

    Returns:
        None
    """
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

            if 'flags' in m.keys():
                flags = str(m['flags'])
                flags_names = LopperProp(prop_name+'-flags-names',value = str(flags))
                subnode + flags_names

            raw_start = m.get('start', memory_start)
            raw_size = m.get('size', 0xbeef)

            if verbose:
                print(f"memory expand: start/size as read: {raw_start}/{raw_size}")

            reg_cells, start_val, size_val = expand_start_size_to_reg(
                m,
                address_cells=1,
                size_cells=1,
                default_start=memory_start,
                default_size=0xbeef
            )

            if verbose:
                print(f"memory expand: start/size as converted: {start_val}/{size_val}")

            mem_list.extend(reg_cells)

    except Exception as e:
        # print( "Exception expanding memory: %s" % e )
        mem_list = [0xdead, 0xffff ]

    if verbose:
        # dump the memory as hex
        print( f"[DBG] memory: [{', '.join(hex(x) for x in mem_list)}]" )

    property_set( prop_name, mem_list, subnode )

def openamp_remote_cpu_expand( tree, subnode, cluster_cpu, cluster_node, verbose = 0):
    """ Routine to add OpenAMP specific information for later processing as the remote CPU
        node will be removed before OpenAMP processing can be called.
    Args:
        tree (LopperTree): Device tree being modified.
        subnode (LopperNode): Node describing CPU resources to attach.
        cluster_cpu (arr): None or array to check for cluster CPU information
        cluster_node (LopperNode): Node for core CPU information
        verbose (int): Verbosity level for diagnostic output.

    Returns:
        None
    """
    if cluster_cpu == None:
        return

    for n in subnode.subnodes():
        if n.name == "domain-to-domain":
            n + LopperProp(name="cluster_cpu", value=cluster_cpu)

    pd_prop_node = [ n for n in cluster_node.subnodes() if n.propval("power-domains") != [''] ]
    if len(pd_prop_node) == 1:
        subnode + LopperProp(name="rpu_pd_val", value=pd_prop_node[0].propval("power-domains"))

    if cluster_node != None and "r5" in cluster_node.name:
        subnode + LopperProp(name="cpu_config_str", value="split" if subnode.propval("cpus")[1] == 1 else "lockstep")
        subnode + LopperProp(name="core_num", value=cluster_node.name[-1])


def cpu_expand( tree, subnode, verbose = 0):
    """Expand compact CPU descriptors into fully fledged domain properties.

    Args:
        tree (LopperTree): Device tree being modified.
        subnode (LopperNode): Node describing CPU resources to attach.
        verbose (int): Verbosity level for diagnostic output.

    Returns:
        None
    """
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

    # This is a no-op if cluster_cpu and cluster_node are not set up.
    openamp_remote_cpu_expand(tree, subnode, cluster_cpu, cluster_node, verbose)

# sdt: is the system device tree
def subsystem( tgt_node, sdt, options ):
    """Entry point for subsystem assist processing.

    Args:
        tgt_node (LopperNode): Target node supplied by the dispatcher.
        sdt (LopperSDT): Structured device tree wrapper containing the domain data.
        options (dict[str, Any]): Assist invocation parameters.

    Returns:
        bool: True after expansion or generation completes successfully.

    Algorithm:
        Inspects the options to determine whether to create a template subsystem
        or expand an existing YAML description into a full OpenAMP domain tree.
    """
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
    """Generate a template subsystem description within ``/domains``.

    Args:
        tgt_node (LopperNode): Target node supplied by the dispatcher.
        sdt (LopperSDT): Structured device tree wrapper containing the domain data.
        verbose (int): Verbosity flag for diagnostic logging.

    Returns:
        bool: True when the template subsystem is populated.
    """
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
    """Determine whether a subsystem requires CDO flag expansion.

    Args:
        tgt_node (LopperNode): Subsystem node under evaluation.

    Returns:
        bool: True when the subsystem declares ``xilinx,subsystem-v1`` compatibility.
    """
    if 'xilinx,subsystem-v1' in tgt_node.propval("compatible"):
        return True
    return False


def expand_cdo_flags(tgt_node):
    """Expand a CDO-derived flag description into flattened lists.

    Args:
        tgt_node (LopperNode): Subsystem node whose flags will be expanded.

    Returns:
        list: Tuple-style list containing flag names, flag values, and cell count.
    """
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
    """Populate the first 32-bit word of a flag descriptor.

    Args:
        flags_node (LopperNode): Node providing per-flag overrides.
        ref_flags (list[int]): Mutable list of four integers capturing bitfields.
        default_flags_node (LopperNode): Node containing default flag values.

    Returns:
        None
    """
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
    """Populate the third 32-bit word of a flag descriptor.

    Args:
        flags_node (LopperNode): Node providing per-flag overrides.
        ref_flags (list[int]): Mutable list of four integers capturing bitfields.
        default_flags_node (LopperNode): Node containing default flag values.

    Returns:
        None
    """
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
    """Expand a flag node into four 32-bit words describing policy.

    Args:
        flags_node (LopperNode): Node providing per-flag overrides.
        default_flags_node (LopperNode): Node containing default flag values.

    Returns:
        list[int]: Four-element list representing the expanded flag words.
    """
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
    """Materialize flattened flag properties for a subsystem relation.

    Args:
        tree (LopperTree): Device tree being modified.
        tgt_node (LopperNode): Subsystem relation node.
        verbose (int): Verbosity level for diagnostic output.

    Returns:
        bool: True once the ``flags`` properties have been generated.
    """

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
    """Expand domain-to-domain relations into device tree references.

    Args:
        tree (LopperTree): Device tree containing OpenAMP relations.
        tgt_node (LopperNode): Domain-to-domain container node.
        verbose (int): Verbosity level for diagnostic output.

    Returns:
        bool: True when expansion finishes successfully or no action is required.
    """

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

def resolve_carveouts( tree, subnode, carveout_prop_name, verbose = 0 ):
    """Resolve carveout node references within a relation.

    Args:
        tree (LopperTree): Device tree that contains the reserved/AXI nodes.
        subnode (LopperNode): Relation container node being expanded.
        carveout_prop_name (str): Property name, such as ``carveouts`` or ``elfload``.
        verbose (int): Verbosity flag for diagnostic logging.

    Returns:
        bool: True when all carveout names resolve to phandles, False otherwise.

    Algorithm:
        Iterates relation children, searches reserved-memory and AXI subtrees for
        matching node names or labels, ensures phandles exist, and replaces the
        string references with numeric phandle lists.
    """
    prop = None
    domain_node = None

    subnodes_to_check = subnode.tree["/reserved-memory"].subnodes(children_only=True) + subnode.tree["/axi"].subnodes(children_only=True)
    for relation in subnode.subnodes(children_only=True):
        if relation.props(carveout_prop_name) == []:
            print("WARNING: resolve_carveouts: ", subnode, relation, "missing property", carveout_prop_name)
            return False
        carveoutlist = relation.propval(carveout_prop_name)
        new_prop_val = []

        for carveout_str in carveoutlist:
            current_node = [ n for n in subnodes_to_check if carveout_str == n.name or carveout_str == n.label ]

            # there can be tcm in / and not /axi
            if "tcm" in carveout_str and current_node == []:
                current_node = [ n for n in subnode.tree["/"].subnodes(children_only=True) if carveout_str == n.name or carveout_str == n.label ]

            if current_node == []:
                print("ERROR: Unable to find referenced node name: ", carveout_str, current_node, relation)
                return False
            current_node = current_node[0]

            if current_node.phandle == 0:
                current_node.phandle_or_create()

            if current_node.props("phandle") == []:
               current_node + LopperProp(name="phandle", value=current_node.phandle)

            new_prop_val.append(current_node.phandle)

        relation + LopperProp(name=carveout_prop_name, value = new_prop_val)

    return True

def resolve_rpmsg_mbox( tree, subnode, verbose = 0 ):
    """Replace RPMsg mailbox string identifiers with phandles.

    Args:
        tree (LopperTree): Device tree used to locate mailbox nodes.
        subnode (LopperNode): Relation container node that references mailboxes.
        verbose (int): Verbosity flag for diagnostic logging.

    Returns:
        bool: True when mailbox references resolve successfully, False otherwise.

    Algorithm:
        Validates the presence of ``mbox`` properties, searches the AXI subtree for
        nodes whose name or label matches the mailbox string, and writes the located
        phandle back into the relation.
    """
    for relation in subnode.subnodes(children_only=True):
        if relation.props("mbox") == []:
            print("WARNING:", "rpmsg relation does not have mbox")
            return False

        mbox = relation.propval("mbox")

        # if the node name or label matches then save it
        new_prop_val = [ n.phandle for n in subnode.tree["/axi"].subnodes(children_only=True) if n.name == mbox or n.label == mbox ]
        if new_prop_val == []:
            print("WARNING: could not find ", mbox)

        relation.props("mbox")[0].value = new_prop_val[0]

    return True

def resolve_host_remote( tree, subnode, verbose = 0 ):
    """Resolve host/remote references within a relation description.

    Args:
        tree (LopperTree): Device tree containing ``/domains`` children.
        subnode (LopperNode): Relation container node with host/remote properties.
        verbose (int): Verbosity flag for diagnostic logging.

    Returns:
        bool: True when exactly one role resolves to a domain node, False otherwise.

    Algorithm:
        Checks each relation child to ensure exactly one of ``host`` or ``remote`` is
        provided, searches ``/domains`` for the named node, ensures that node has a
        phandle, and replaces the role property with the corresponding phandle.
    """
    for relation in subnode.subnodes(children_only=True):
        roles_dict = {'host': [], 'remote': []}
        # save host and remote info for relation
        [ roles_dict[role].append(relation.propval(role)) for role in roles_dict.keys() if relation.propval(role) != [''] ]

        if all(roles_dict.values()):
            print("WARNING: relation has both host and remote", relation)
            return False
        if not any(roles_dict.values()):
            print("WARNING: could not find host or remote for ", relation)
            return False

        role = [ k for k, v in roles_dict.items() if v ][0]

        # find each matching domain node in tree for the role
        relevant_node = tree["/domains"].subnodes(children_only=True,name=roles_dict[role][0]+"$")
        if relevant_node == []:
            print("WARNING: could not find relevant node for ", prop_val)
            return False

        relevant_node = relevant_node[0]

        # give matching node phandle if needed
        if relevant_node.phandle == 0:
            relevant_node.phandle_or_create()

        if relevant_node.props("phandle") == []:
            relevant_node + LopperProp(name="phandle", value=relevant_node.phandle)

        relation[role] = relevant_node.phandle

    return True


def xlnx_openamp_rpmsg_expand(tree, subnode, verbose = 0 ):
    """Expand RPMsg YAML specialization into full device tree references.

    Args:
        tree (LopperTree): Device tree to update.
        subnode (LopperNode): RPMsg YAML subnode being expanded.
        verbose (int): Verbosity flag for diagnostics.

    Returns:
        bool: True when all references are resolved successfully.

    Algorithm:
        Resolves host/remote phandles, hydrates carveout references, and assigns
        mailbox definitions using shared helper functions.
    """
    # Xilinx-specific YAML expansion of RPMsg description.
    if not resolve_host_remote( tree, subnode, verbose):
        return False
    if not resolve_carveouts(tree, subnode, "carveouts", verbose):
        return False

    return resolve_rpmsg_mbox( tree, subnode, verbose)

def xlnx_openamp_remoteproc_expand(tree, subnode, verbose = 0 ):
    """Expand remoteproc YAML specialization into device tree references.

    Args:
        tree (LopperTree): Device tree to mutate.
        subnode (LopperNode): Remoteproc YAML subnode being expanded.
        verbose (int): Verbosity flag for diagnostics.

    Returns:
        bool: True when host/remote and carveout references resolve.

    Algorithm:
        Resolves host/remote references and materializes ELFLOAD carveout phandles
        into the tree using common resolver helpers.
    """
    # Xilinx-specific YAML expansion of Remoteproc description.
    if not resolve_host_remote( tree, subnode, verbose):
        return False

    return resolve_carveouts(tree, subnode, "elfload", verbose)

def openamp_remoteproc_expand(tree, subnode, verbose = 0 ):
    """Delegate remoteproc expansion to the appropriate vendor handler.

    Args:
        tree (LopperTree): Device tree that may contain vendor identifiers.
        subnode (LopperNode): Remoteproc relation node requiring expansion.
        verbose (int): Verbosity level for diagnostic output.

    Returns:
        bool: Result of the vendor-specific expansion routine.
    """
    # Generic OpenAMP expansion subroutine which selects the applicable
    # vendor method to use for Remoteproc YAML expansion
    for i in tree["/"]["compatible"].value:
        for j in ['amd', 'xlnx']:
            if j in i:
                return xlnx_openamp_remoteproc_expand(tree, subnode, verbose)
    return True


def openamp_rpmsg_expand(tree, subnode, verbose = 0 ):
    """Delegate RPMsg expansion to the appropriate vendor handler.

    Args:
        tree (LopperTree): Device tree that may contain vendor identifiers.
        subnode (LopperNode): RPMsg relation node requiring expansion.
        verbose (int): Verbosity level for diagnostic output.

    Returns:
        bool: Result of the vendor-specific expansion routine.
    """
    # Generic OpenAMP expansion subroutine which selects the applicable
    # vendor method to use for RPMsg YAML expansion
    for i in tree["/"]["compatible"].value:
        for j in ['amd','xlnx']:
            if j in i:
                return xlnx_openamp_rpmsg_expand(tree, subnode, verbose)

    return True

openamp_d_to_d_compat_strings = {
    "openamp,rpmsg-v1" : openamp_rpmsg_expand,
    "openamp,remoteproc-v2" : openamp_remoteproc_expand,
}

def is_openamp_d_to_d(tree, subnode, verbose = 0 ):
    """Check whether a relation is an OpenAMP domain-to-domain description.

    Args:
        tree (LopperTree): Device tree containing the relation nodes.
        subnode (LopperNode): Candidate relation node.
        verbose (int): Verbosity flag retained for interface consistency.

    Returns:
        bool: True when a known OpenAMP compatibility string is detected.
    """
    for n in subnode.subnodes():
        if len(n["compatible"]) == 1 and n["compatible"][0]  in openamp_d_to_d_compat_strings.keys():
            return True
    return False

def openamp_d_to_d_expand(tree, subnode, verbose = 0 ):
    """Expand domain-to-domain OpenAMP nodes using compatibility dispatch.

    Args:
        tree (LopperTree): Device tree being modified.
        subnode (LopperNode): Domain-to-domain relation node.
        verbose (int): Verbosity level for diagnostic output.

    Returns:
        bool: Result of the dispatched expansion routine.
    """
    # landing function for generic YAML expansion of
    # domain-to-domain property
    for n in subnode.subnodes():
        if len(n["compatible"]) == 1 and n["compatible"][0]  in openamp_d_to_d_compat_strings.keys():
            return openamp_d_to_d_compat_strings[n["compatible"][0]](tree, n, verbose)

    return False


def subsystem_expand( tgt_node, sdt, verbose = 0 ):
    """Expand YAML subsystem descriptions into full device tree domains.

    Args:
        tgt_node (LopperNode): Target node supplied by the dispatcher.
        sdt (LopperSDT): Structured device tree wrapper containing the domain data.
        verbose (int): Verbosity flag for diagnostic logging.

    Returns:
        bool: True when YAML nodes are expanded successfully.
    """
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
