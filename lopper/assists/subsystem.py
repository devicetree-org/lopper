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
    if re.search( "module,subsystem", compat_string_to_test):
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

def firewall_expand( tree, subnode, verbose = 0 ):
    if verbose:
        print( "[DBG]: firewall_expand: %s" % subnode.abs_path )

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
                print( "[WARNING]: unrecognized node passed for firewallconf expansion: %s" % subnode.abs_path )
                return
        except:
            print( "[WARNING]: unrecognized node passed for firewallconf expansion: %s" % subnode.abs_path )
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
            print( "[DBG]: firewall expand: %s cfg: domain: %s block: %s" % (subnode.abs_path,firewall_domain,firewall_block))

        if firewall_block and not firewall_domain:
            if verbose:
                print( "[DBG]: firewall: block and no domain, generating firewallconf-default" )

            firewall_block_priority = 0
            firewall_block_type = 0
            try:
                firewall_block_priority = int(firewall_block)
            except:
                # it is a string
                if re.search( "always", firewall_block ):
                    # aka "block"
                    firewall_block_type = 0
                elif re.search( "never", firewall_block ):
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
                    print( "[DBG]: WARNING: could not find node %s" % firewall_domain )
                tgt_node = None

            firewall_priority = 0

            tgt_node_phandle = 0xdeadbeef
            if tgt_node:
                if tgt_node.phandle == 0:
                    tgt_node_phandle = tgt_node.phandle_or_create()
                    if verbose:
                        print( "[DBG]: generated phandle %s for node: %s" % (tgt_node.phandle,tgt_node.abs_path ))
                else:
                    tgt_node_phandle = tgt_node.phandle

            if firewall_block:
                if type(firewall_block) == int:
                    # priority was passed
                    firewall_priority = firewall_block
                    # block
                    firewall_block = 0
                else:
                    if re.search( "always", firewall_block ):
                        firewall_block = 0
                    elif re.search( "never", firewall_block ):
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

    if type(access_props[0].value) == list:
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

        dev_handle = 0xdeadbeef
        if dev:
            try:
                dev_node = tree.deref( dev )
                if verbose:
                    print( "[DBG]: found dev node %s" % dev_node )
            except:
                if verbose:
                    print( "[DBG]: WARNING: could not find node %s" % dev )
                dev_node = None

            if dev_node:
                if dev_node.phandle == 0:
                    dev_node.phandle_or_create()
                    if verbose:
                        print( "[DBG]: generated phandle %s for node: %s" % (dev_node.phandle,dev_node.abs_path ))

                dev_handle = dev_node.phandle
            else:
                dev_handle = 0xdeadbeef

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
        print( '[DBG] setting access: [{}]'.format(', '.join(hex(x) for x in access_list)) )

    ap.value = access_list

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

            #print( "memory expand: start/size as read: %s/%s" % (start,size))
            start = humanfriendly.parse_size( start, True )
            size = humanfriendly.parse_size( size, True )
            #print( "memory expand: start/size as converted: %s/%s" % (start,size))

            mem_list.append(int(start))
            mem_list.append(int(size))

    except Exception as e:
        mem_list = [0xdead, 0xffff ]

    if verbose:
        # dump the memory as hex
        print( '[DBG] memory: [{}]'.format(', '.join(hex(x) for x in mem_list)) )

    property_set( prop_name, mem_list, subnode )


def cpu_expand( tree, subnode, verbose = 0):
    ## cpu processing
    cpus = subnode.props( "cpus" )
    if not cpus:
        return

    cpus_chunks = [cpus[0][0]]
    cpus_list = []
    for c in cpus_chunks:
        if verbose:
            print( "[DBG]: cpu: %s" % c )
            if type(c) == dict:
                print( "         cluster: %s" % c['cluster'] )
                print( "         cpumask: %s" % c['cpumask'] )
                print( "         mode: %s" % c['mode'] )

        if type(c) == dict:
            cluster = c['cluster']
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
                    print( "[DBG]: subsystem assist: generated phandle %s for node: %s" % (cluster_node.phandle,cluster_node.abs_path ))

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
            print( "[DBG]:  cluster handle: %s" % hex(cluster_handle) )
            print( "        cpu mask: %s" % hex(mask) )
            print( "        mode mask: %s" % hex(mode_mask) )

        cpus_list.extend( [cluster_handle, int(mask), mode_mask ] )

    if cpus_list:
        # property_set( "cpus", cpus_list, subnode )
        cpus[0].value = cpus_list

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
        print( "[INFO]: cb: subsystem( %s, %s, %s, %s )" % (tgt_node, sdt, verbose, args))

    if "generate" in args or "--generate" in args:
        subsystem_generate( tgt_node, sdt, verbose )
    else:
        subsystem_expand( tgt_node, sdt, verbose )

    return True


# sdt: is the system device tree
def subsystem_generate( tgt_node, sdt, verbose = 0):
    if verbose:
        print( "[INFO]: cb: subsystem_generate( %s, %s )" % (tgt_node, sdt))

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

    return True

def subsystem_expand( tgt_node, sdt, verbose = 0 ):
    if verbose:
        print( "[INFO]: cb: subsystem_expand( %s, %s )" % (tgt_node, sdt))

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
                print( "[DBG] yaml input dectected, expanding %s to full device tree domain" % subnode.abs_path )

            # we flip the name and the label, since the yaml name does not
            # follow device tree conventions.
            name = subnode.name
            subnode.name = "domain@{}".format( domain_count )
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
