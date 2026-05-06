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
import os
import getopt
import re
from pathlib import Path
from pathlib import PurePath
from io import StringIO
import contextlib
import importlib
import copy
from itertools import chain

sys.path.append(os.path.dirname(__file__))

from lopper import Lopper
from lopper import LopperFmt
from lopper.tree import LopperAction
import lopper
import lopper_lib
from lopper_lib import chunks
from lopper.log import _init, _warning, _info, _error, _debug
import logging

_init(__name__)
_init("domain_access.py")

# ---------------------------------------------------------------------------
# Reserved-memory survival tables
#
# These drive the compatible-string-based survival logic in core_domain_access().
# Extend these tables to add new binding types without touching the logic.
#
# RESMEM_ALWAYS_SURVIVE
#   Compatible strings whose nodes survive unconditionally in any domain output.
#   Use for nodes whose purpose is to communicate information to *any* consumer
#   of the device tree (e.g. "hide this range from the default OS").
#
# RESMEM_SURVIVE_IF_CLAIMED
#   Compatible strings whose nodes survive only when the target domain's
#   reserved-memory property explicitly lists their phandle.  Use for
#   domain-owned carveouts that have no OS-side device reference.
#
# Both tables are checked before the reference-gating scan.  Nodes that match
# neither table fall through to normal reference-gating (memory-region from a
# device node, caught by step 1a).
#
# Last-resort fallback: add lopper,no-ref-required to any individual node to
# force survival regardless of compatible string or reference.  The property is
# consumed and stripped here; it is never emitted to the output DTS.
# ---------------------------------------------------------------------------

RESMEM_ALWAYS_SURVIVE = {
    "openamp,domain-memory-v1",   # SDT spec: hide range from default OS
}

RESMEM_SURVIVE_IF_CLAIMED = {
    "openamp,xlnx,mem-carveout",  # vendor: coprocessor/RPU carveout
}

def is_compat( node, compat_string_to_test ):
    if re.search( "access-domain,domain-v1", compat_string_to_test):
        return core_domain_access
    if re.search( "module,.*domain_access", compat_string_to_test):
        return core_domain_access
    return ""

# tree: is the lopper system device-tree
def domain_get_subnodes(tree):
    try:
        domain_node = tree['/domains']
    except:
        domain_node = None

    direct_node_refs = []

    if domain_node:
        for node in domain_node.subnodes():
            # 1) memory access/node = <> nodes
            try:
                mem_node = node["memory"].value
                direct_node_refs.append( node )
            except:
                pass
            # 2) direct access = <> nodes
            a_nodes = lopper_lib.node_accesses( tree, node )
            if a_nodes:
                direct_node_refs.append( node )
            # 3) include = <> nodes
            try:
                i_nodes = lopper_lib.includes( tree, node['include'])
                if i_nodes:
                    direct_node_refs.append( node )
            except:
                pass

    # Remove duplicate entries
    direct_node_refs = list(dict.fromkeys(direct_node_refs))
    return direct_node_refs

# searches memory nodes and_ returns the size that
# matches the start address
def memory_get_size(mem_node, start_address):
    ac = mem_node.parent['#address-cells'][0]
    sc = mem_node.parent['#size-cells'][0]

    matching_memory = []

    reg_val = mem_node["reg"].value
    memory_chunks = chunks( reg_val, ac + sc )
    for memory_entry in memory_chunks:
        start_address_value,cells = lopper_lib.cell_value_get( memory_entry, ac )
        size_value,cells = lopper_lib.cell_value_get(memory_entry, sc, ac )

        if start_address_value == start_address:
            return size_value

    return None

# node: is the domain node number
# mem_val: Memory node address and size value to be updated
# This api takes the memory value(address and size) and creates
# a new memory node value for memory node reg property based on the
# address-cells and size-cells property.
def update_mem_node(node, mem_val):
    ac = node.parent['#address-cells'][0]
    sc = node.parent['#size-cells'][0]

    new_mem_val = []
    mem_reg_pairs = len(mem_val)/2
    addr_list = mem_val[::2]
    size_list = mem_val[1::2]
    for i in range(0, int(mem_reg_pairs)):
        for j in range(0, ac):
            high_addr = 0
            val = str(hex(addr_list[i]))[2:]
            if len(val) > 8:
                high_addr = 1
            if j == ac-1:
                if len(val) > 8:
                    pad = len(val) - 8
                    upper_val = val[:pad]
                    lower_val = val[pad:]
                    new_mem_val.append(int(upper_val, base=16))
                    new_mem_val.append(int(lower_val, base=16))
                else:
                     new_mem_val.append(addr_list[i])
            elif high_addr != 1:
                new_mem_val.append(0)
        for j in range(0, sc):
            high_size = 0
            val = str(hex(size_list[i]))[2:]
            if len(val) > 8:
                high_size = 1
            if j == sc-1:
                if len(val) > 8:
                    pad = len(val) - 8
                    upper_val = val[:pad]
                    lower_val = val[pad:]
                    new_mem_val.append(int(upper_val, base=16))
                    new_mem_val.append(int(lower_val, base=16))
                else:
                     new_mem_val.append(size_list[i])
            elif high_size != 1:
                new_mem_val.append(0)
    return new_mem_val

def usage():
    print( """
   Usage: domain_access [OPTION]

      -p       permissive matching on target node (regex)
      -v       enable verbose debug/processing

    """)


def validate_reserved_memory_in_memory_ranges(sdt, domain_node, verbose=0):
    """Validate that all domain reserved-memory regions fall within domain memory.

    This is a compatibility wrapper that delegates to lopper.audit.

    Raises RuntimeError (via _error) if any reserved-memory region is outside
    the domain's memory ranges.

    Args:
        sdt: The system device tree (LopperSDT)
        domain_node: The domain node being processed
        verbose: Verbosity level (unused, kept for API compatibility)

    Returns:
        None. Raises error if validation fails.
    """
    import lopper.audit
    # The audit version uses werror=True to match the original behavior
    # of calling _error(..., True) which exits on error
    lopper.audit.validate_reserved_memory_in_memory_ranges(
        sdt.tree, domain_node, werror=True
    )


# tgt_node: is the domain node number
# sdt: is the system device tree
def core_domain_access( tgt_node, sdt, options ):
    try:
        verbose = options['verbose']
    except:
        verbose = 0

    try:
        args = options['args']
    except:
        args = []

    # --permissive means that non-SMID devices/memory will be consulted
    opts,args2 = getopt.getopt( args, "vt:p", [ "verbose", "target=", "permissive" ] )

    permissive = False
    command_line_target=""
    for o,a in opts:
        if o in ('-v', "--verbose"):
            verbose = verbose + 1
        elif o in ('-p', "--permissive"):
            permissive = True
        elif o in ("-t", "--target"):
            command_line_target = a
        elif o in ('-h', "--help"):
            # usage()
            sys.exit(1)

    if verbose:
        lopper.log._level( logging.INFO, __name__ )
    if verbose > 1:
        lopper.log._level( logging.DEBUG, __name__ )
    if verbose > 2:
        lopper.log._level( lopper.log.TRACE, __name__ )
    if verbose > 3:
        lopper.log._level( lopper.log.TRACE2, __name__ )

    # "/" indicates we were run from the command line as an autorun, not
    # triggered from a lop file associated to a node
    if tgt_node.abs_path == "/":
        if sdt.target_domain:
            try:
                # Note: this works to find a node by name only when yaml
                #       expansion has been done. This is because the default
                #       yaml expansion takes nodes of type domain-v1 and makes
                #       their yaml name the node label, and then assigns them
                #       a unique domain@<> node name.
                #
                #       When a node is looked up by tree subscript [], one of
                #       the elements searched is the node label, hence why we
                #       can find non-absolute domains in some scenarios.
                #
                #       if you really want to be sure to find your domain for
                #       processing, pass it by absolute path.
                tgt_node = sdt.tree[sdt.target_domain]
            except Exception as e:
                try:
                    domains_dump = sdt.tree['/domains'].print( as_string=True )
                except Exception:
                    domains_dump = "(no /domains node found)"
                _error( f"domain_access: target domain {sdt.target_domain} cannot be found in input.\n"
                        f"Available domains:\n{domains_dump}", True )

        else:
            if command_line_target:
                try:
                    tgt_node = sdt.tree[command_line_target]
                except Exception as e:
                    try:
                        domains_dump = sdt.tree['/domains'].print( as_string=True )
                    except Exception:
                        domains_dump = "(no /domains node found)"
                    _error( f"domain_access: target domain '{command_line_target}' cannot be found in input.\n"
                            f"Available domains:\n{domains_dump}", True )
            else:
                try:
                    tgt_node = sdt.tree["/domains/default"]
                except:
                    pass

    # reset the treewide ref counting
    sdt.tree.ref = 0
    domain_node = sdt.tree[tgt_node]

    _info( f"cb: core_domain_access( {domain_node}, {sdt}, {verbose} )")

    # If the domain node carries a 'lopper,activate' property, use the named
    # overlay tree so that conditional properties (sigil syntax) are visible
    # during processing.  Fall back to 'os,type' if lopper,activate is absent.
    _activate = domain_node.propval('lopper,activate')
    if not _activate or _activate == ['']:
        _activate = domain_node.propval('os,type')
    # propval may return a bare string or a list; normalise to list so the
    # for-loop below iterates over names, not characters.
    if isinstance(_activate, str):
        _activate = [_activate]
    for _ov_name in (_activate or []):
        if _ov_name and _ov_name.strip():
            _ov_tree = sdt.tree.overlay_tree(_ov_name.strip())
            if _ov_tree is not None:
                _info( f"domain_access: activating overlay tree '{_ov_name.strip()}'" )
                sdt.tree = _ov_tree
                domain_node = sdt.tree[tgt_node]
            break

    direct_node_refs = []

    # 1) direct access = <> nodes
    a_nodes = lopper_lib.node_accesses( sdt.tree, domain_node )
    for anode in a_nodes:
        # add a refcount to the node and it's parents
        sdt.tree.ref_all( anode, True )
        _info( f"domain_access: adding reference to: {anode}" )
        direct_node_refs.append( anode )

    # 1a) indirect references. Any phandles referenced the direct
    # nodes should be ref'd as well.
    indirect_refs = []
    for d in direct_node_refs:
        # if we allow parent references nothing will be deleted
        # since ref_all does the node and all subnodes. So if
        # we want to get all parent refs, we can't use ref_all()
        # to increment the refcount
        all_refs = d.resolve_all_refs(parents=True)
        for r in all_refs:
            if r not in indirect_refs and r not in direct_node_refs:
                indirect_refs.append( r )

    for d in indirect_refs:
        _info( f"domain_access: adding indirect reference to: {d}" )
        # ref_all will also reference count subnodes, we
        # don't want that, so we go directly are the refcount
        # field
        # sdt.tree.ref_all( d, False )
        d.ref = 1

    # 2) are there resource group includes ?, they can have access = <> as well
    try:
        includes = domain_node["include"]
    except:
        includes = None

    if includes:
        include_nodes = lopper_lib.includes( sdt.tree, domain_node["include"] )

        for i in include_nodes:
            a_nodes = lopper_lib.node_accesses( sdt.tree, i )
            for anode in a_nodes:
                sdt.tree.ref_all( anode, True )
                direct_node_refs.append( anode )

    # 2b) reserved-memory survival pre-pass
    #
    # ALL /reserved-memory children survive unconditionally.
    #
    # Why: every node in /reserved-memory at this point is either an original
    # SDT node (hardware/firmware truth) or was merged from a top-level YAML
    # declaration (an explicit, global system statement by the user).  Neither
    # category is domain-specific — they represent platform-wide reservations
    # (PLM space, TF-A, carveouts, etc.) that every OS output should see.
    #
    # The original concern that motivated filtering here was cross-domain
    # contamination: RPU carveouts bleeding into the Linux DTS.  However,
    # investigation of the YAML expansion path (reserved_memory_expand in
    # yaml_to_dts_expansion.py) shows that it operates only on nodes that
    # ALREADY EXIST in /reserved-memory — it resolves names to phandles and
    # fixes up reg properties, but never synthesises new nodes from domain
    # subnodes.  There is therefore no mechanism today by which a carveout
    # declared inside one domain's YAML spec ends up in /reserved-memory
    # without the user having also declared it at the top level.  Every node
    # in /reserved-memory is a global declaration by construction.
    #
    # If domain-embedded synthesis is added in the future (nodes created
    # directly from /domains/*/reserved-memory/ children and injected into
    # the global /reserved-memory), a clean filter trigger is available
    # without tagging at origin: walk /domains at step-2 time, collect the
    # names of structural children under each domain's reserved-memory subnode,
    # and apply compatible-driven rules only to nodes whose names appear in
    # that set.  Everything else passes through as today.
    #
    # The compatible checks below are ADVISORY ONLY — survival is already
    # decided.  They exist to:
    #   - log informational context for well-known compatible strings
    #   - warn when a survive-if-claimed node is present but the target domain
    #     has not listed it in its reserved-memory property (possible
    #     misconfiguration: the user may have forgotten to claim ownership)
    #
    # lopper,no-ref-required is still consumed and stripped so it never
    # appears in the output DTS, for forward compatibility.
    #
    # RESMEM_ALWAYS_SURVIVE and RESMEM_SURVIVE_IF_CLAIMED are defined at
    # module level so new compatible strings can be added without touching
    # this logic.
    try:
        # Collect phandles the target domain explicitly claims
        domain_claimed_phandles = set()
        try:
            resmem_prop = domain_node['reserved-memory'].value
            if resmem_prop and resmem_prop != ['']:
                domain_claimed_phandles = {ph for ph in resmem_prop if isinstance(ph, int)}
        except:
            pass

        resmem_parent = sdt.tree['/reserved-memory']
        if resmem_parent:
            for child in resmem_parent.subnodes(children_only=True):

                # All nodes survive — set ref unconditionally.
                child.ref = 1

                # --- lopper,no-ref-required: consume and strip ---
                # propval() cannot distinguish absent from present-but-empty
                # boolean properties; use __props__ membership instead.
                if "lopper,no-ref-required" in child.__props__:
                    try:
                        child.delete("lopper,no-ref-required")
                    except Exception:
                        pass  # already stripped on a previous call
                    _info(f"domain_access: {child.abs_path} has lopper,no-ref-required "
                          f"(stripped; node survives as all /reserved-memory nodes do)")
                    continue

                compat = child.propval("compatible")

                # --- always-survive compatibles: log for traceability ---
                if any(c in RESMEM_ALWAYS_SURVIVE for c in compat):
                    _info(f"domain_access: {child.abs_path} survives "
                          f"(always-survive compatible: {compat})")
                    continue

                # --- survive-if-claimed compatibles: advisory warning if unclaimed ---
                if any(c in RESMEM_SURVIVE_IF_CLAIMED for c in compat):
                    ph = child.propval("phandle")
                    claimed = ph and ph[0] in domain_claimed_phandles
                    if claimed:
                        _info(f"domain_access: {child.abs_path} survives "
                              f"(claimed by domain, compatible: {compat})")
                    else:
                        _warning(f"domain_access: {child.abs_path} has compatible "
                                 f"{compat} but is not listed in "
                                 f"{domain_node.abs_path} reserved-memory property — "
                                 f"verify this is intentional")
                    continue

            # Keep the parent alive so the subtree is not dropped by filter #1.
            resmem_parent.ref = 1

    except Exception as e:
        _warning(f"domain_access: exception in reserved-memory survival pre-pass: {e}")

    # 2c) domain subnode phandle references
    #
    # Scan all subnodes of the domain (including the domain node itself) for
    # genuine phandle references and refcount their targets. This keeps
    # carveouts, elfload, mbox, and other phandle-referenced nodes alive for
    # later processing by assists like openamp.
    #
    # We use prop.resolve_phandles() rather than blindly calling pnode() on
    # every integer value. resolve_phandles() consults the cells-specifier
    # infrastructure (#xxx-cells) to identify which positions in a property
    # are actual phandle slots, so address/size cells in properties like
    # 'memory' or 'reg' are never mistaken for phandle references.
    #
    # IMPORTANT: use ref_node.ref = 1 (not ref_all(ref_node, True)) here.
    # ref_all(node, True) calls resolve_all_refs() which is fully recursive —
    # it follows all phandle refs from the target node transitively, marking
    # every reachable node and its entire subtree. This causes cross-domain
    # contamination: a domain-to-domain.remote = <&openamp_r5> reference would
    # transitively pull in the RPU domain's carveouts (via its reserved-memory
    # property) and mark them as surviving in the Linux output, even though no
    # Linux device consumes them.
    #
    # ref_node.ref = 1 marks only the directly-referenced node. We also walk
    # up the parent chain and mark each ancestor, so that the simple-bus filter
    # (step 5) does not drop a bus node before its refcounted children can be
    # pruned. This mirrors the parent=True behaviour of resolve_all_refs() used
    # in step 1a, without the unwanted transitive phandle following.
    try:
        for subnode in domain_node.subnodes():
            for prop in subnode:
                for ref_node in prop.resolve_phandles():
                    ref_node.ref = 1
                    _info(f"domain_access: refcounting domain subnode phandle: {ref_node.abs_path}")
                    p = ref_node.parent
                    while p and p.abs_path != "/":
                        p.ref = 1
                        p = p.parent
    except Exception as e:
        _warning(f"domain_access: exception in domain subnode refcounting: {e}")

    # 3) cpus access
    try:
        cpu_prop = domain_node['cpus']
    except:
        _warning( "domain_access: core_domain: domain node does not have a cpu link" )
        cpu_prop = None

    if cpu_prop:
        refd_cpus, unrefd_cpus = lopper_lib.cpu_refs( sdt.tree, cpu_prop, verbose )
        if refd_cpus:
            ref_nodes = sdt.tree.refd( "/cpus.*/cpu.*" )

            # now we do two types of refcount delete
            #   - betweenthe cpu clusters
            #   - on the cpus within a cluster

            # The following filter code will check for nodes that are compatible to
            # cpus,cluster and if they haven't been referenced, delete them.
            xform_path = "/"
            prop = "cpus,cluster"
            code = f"""
                     p = node.propval('compatible')
                     if p and "{prop}" in p:
                         if node.ref <= 0:
                             return True

                     return False
                   """

            _info( f"domain_access: core_domain_access: filtering on:\n------{code}\n-------\n" )

            # the action will be taken if the code block returns 'true'
            # Lopper.node_filter( sdt, xform_path, LopperAction.DELETE, code, verbose )
            sdt.tree.filter( xform_path, LopperAction.DELETE, code, None, verbose )
            for s in unrefd_cpus:
                try:
                    _info( f"domain_access: core_domain_access: deleting unreferenced subcpu: {s.abs_path}" )
                    sdt.tree.delete( s )
                except Exception as e:
                    _warning( f"{e}" )

    # 4) directly accessed nodes. Check their type. If they are busses,
    #    we have some seecondary processing to do.
    nodes_to_filter = []
    for anode in direct_node_refs:
        node_types = lopper_lib.node_ancestor_types( anode )
        simple_bus = lopper_lib.node_ancestors_of_type( anode, "simple-bus" )
        if simple_bus:
            for i,s in enumerate(simple_bus):
                if not s in nodes_to_filter:
                    _info( f"core_domain_access: simple bus processing for: {anode.name}" )

                    nodes_to_filter.append( s )

        reserved_memory = "reserved-memory" in chain(*node_types)
        if reserved_memory:
            _info( f"core_domain_access: reserved memory processing for: {anode.name}" )
            nodes_to_filter.append( anode.parent )

    # 4b) Add /reserved-memory to filter list if domain references reserved-memory
    # This enables pruning of unreferenced reserved-memory children.
    # Mark the parent node ref=1 so it survives the simple-bus filter in step 5.
    try:
        resmem_prop = domain_node['reserved-memory'].value
        if resmem_prop and resmem_prop != ['']:
            try:
                resmem_parent = sdt.tree['/reserved-memory']
                if resmem_parent and resmem_parent not in nodes_to_filter:
                    resmem_parent.ref = 1
                    nodes_to_filter.append(resmem_parent)
                    _info(f"core_domain_access: adding /reserved-memory to filter list for pruning")
            except:
                pass
    except:
        pass

    # 5) filter nodes that don't have refcounts
    #
    # filter #1:
    #    - starting at /
    #    - drop any unreferenced nodes that are of type simple-bus
    prop = "simple-bus"
    code = f"""
           p = node.propval( 'compatible' )
           if p and "{prop}" in p:
               r = node.ref
               if r <= 0:
                   return True
               else:
                   return False
           else:
               return False
           """

    _info( f"core_domain_access: filtering on:\n------{code}\n-------\n" )

    sdt.tree.filter( "/", LopperAction.DELETE, code, None, verbose )

    # filter #2:
    #    - starting at simple-bus nodes
    #    - drop any unreferenced elements

    # filter #3:
    #    - starting at reserved memory parent
    #    - drop any unreferenced elements

    for n in nodes_to_filter:
        code = """
               p = node.ref
               if p <= 0:
                   return True
               else:
                   return False
               """
        _info( f"core_domain_access ({n}): filtering on:\n------{code}\n-------\n" )

        sdt.tree.filter( n + "/", LopperAction.DELETE, code, None, verbose )

    # 6) memory node processing
    try:
        memory_int = domain_node['memory'].int()
        memory_hex = domain_node['memory'].hex()
    except Exception as e:
        _info( f"the target domain had no memory specification, using default (0)" )
        memory_hex = 0x0
        memory_int = 0

    # This may be moved to the top of the domain process and then when
    # we are processing cpus and bus nodes, we can apply the memory to
    # ranges <>, etc, and modify them accordingly.
    _info( f"core_domain_access: domain memory values: {memory_hex}" )

    # Get all top level memory nodes, we'll be checking them for any
    # required size adjustments
    memory_nodes = sdt.tree.nodes("/memory@.*")

    # Get address-cells and size-cells from root node (standard DT inheritance)
    # These must match what memory_expand() used when creating the property
    try:
        root_ac = sdt.tree['/']['#address-cells'][0]
    except:
        root_ac = 2  # default per DT spec
    try:
        root_sc = sdt.tree['/']['#size-cells'][0]
    except:
        root_sc = 1  # default per DT spec

    # Build a list of memory node information. We do this so we can
    # update the reg, but also keep the original value around. Otherwise
    # multiple entries in domains.yaml couldn't operate on a memory
    # node as the start/size would change, and we'd have no way to
    # match and trigger processing (unless it for some reason fell into
    # the new range)
    memory_node_collector = {}
    try:
        for mem_node in memory_nodes:
            memory_node_collector[mem_node.abs_path] = { "node" : mem_node,
                                                         "reg_val_orig" : copy.deepcopy(mem_node["reg"].value),
                                                         "reg_val" : [],
                                                         "ac" : mem_node.parent['#address-cells'][0],
                                                         "sc" : mem_node.parent['#size-cells'][0],
                                                        }
    except Exception as e:
        _error( f"domain_access: cannot collect memory nodes: {e}" )

    # The memory chunks are what we've built up from the yaml and .iss
    # file. Check them against the memory nodes in the tree to see if
    # anything needs to be adjusted. They are in (address, size) tuples
    # where each component uses the appropriate number of cells (root_ac
    # for address, root_sc for size).
    try:
        domain_memory_chunks = list(chunks( domain_node['memory'].value, root_ac + root_sc ))
    except:
        domain_memory_chunks = []

    # Deduplicate memory chunks. When multiple YAML input files each describe
    # the same domain's memory, the YAML merge concatenates both lists, resulting
    # in identical (start, size) entries. We can only detect this here where we
    # have DT context (root_ac + root_sc) to know the chunk boundary. Identical
    # tuples are always redundant; warn and remove them.
    seen_chunks = []
    deduped_chunks = []
    for chunk in domain_memory_chunks:
        key = tuple(chunk)
        if key not in seen_chunks:
            seen_chunks.append(key)
            deduped_chunks.append(chunk)
        else:
            _warning( f"domain_access: duplicate memory region detected and skipped: {[hex(x) for x in chunk]}" )
    domain_memory_chunks = deduped_chunks

    modified_memory_nodes = []
    for domain_memory_entry in domain_memory_chunks:
        # NOTE: if the domain either does not have a memory field described OR the
        # the memory field is just 'memory:' then domain_memory_entry will show up as ['']
        if domain_memory_entry == ['']:
            continue

        # Extract start address and size using proper cell sizes
        # The domain memory entry is encoded with root_ac cells for address
        # and root_sc cells for size
        domain_memory_start_addr, _ = lopper_lib.cell_value_get( domain_memory_entry, root_ac )
        domain_memory_size, _ = lopper_lib.cell_value_get( domain_memory_entry, root_sc, root_ac )

        _info("domain_access: processing domain memory entry: start: %s, size: %s" %
              (hex(domain_memory_start_addr),hex(domain_memory_size) ) )

        for item in memory_node_collector.values():

            mem_node = item["node"]
            ac = item['ac']
            sc = item['sc']

            # "reg" is the memory node record list, we'll process it
            # to see if it needs to be udpated.
            reg_val = item["reg_val_orig"] # mem_node["reg"].value

            # the reg is of size "address cells" + "size cells"
            sdt_memory_node_chunks = chunks( reg_val, ac + sc )

            mem_reg_val_new = []
            mem_changed_flag = False
            mem_matched_val_flag = False
            for sdt_memory_entry in sdt_memory_node_chunks:
                start_address_value, cells = lopper_lib.cell_value_get( sdt_memory_entry, ac )

                # don't do this ... it can change below now.
                # mem_reg_val_new.extend( cells )

                size_value, size_cells  = lopper_lib.cell_value_get( sdt_memory_entry, sc, ac )
                _info( "domain_access:   node: %s memory entry: address: %s size: %s" %
                       (mem_node,hex(start_address_value),hex(size_value) ))

                if domain_memory_start_addr >= start_address_value and \
                      domain_memory_start_addr + domain_memory_size <= start_address_value + size_value:

                    mem_matched_val_flag = True
                    _info( "domain_access:     %s/%s falls inside memory range %s/%s" %
                           (hex(domain_memory_start_addr),hex(domain_memory_size),
                            hex(start_address_value), hex(size_value)) )

                    if domain_memory_start_addr != start_address_value:
                        _info( f"domain_access:      start value differs: {hex(domain_memory_start_addr)} vs {hex(start_address_value)}")

                        mem_reg_val_new.extend( lopper_lib.cell_value_split( domain_memory_start_addr, ac ) )
                        mem_changed_flag = True
                    else:
                        mem_reg_val_new.extend( cells )

                    # Now to check the size ...
                    if domain_memory_size != size_value:
                        _info(f"domain_access:      size value differs: {hex(domain_memory_size)} vs {hex(size_value)}")

                        mem_reg_val_new.extend( lopper_lib.cell_value_split( domain_memory_size, sc ) )
                        mem_changed_flag = True
                    else:
                        mem_reg_val_new.extend( size_cells )
                else:
                    # not collecting these entries as they fall
                    # outside of the range. Since we will only write
                    # the memory node when something is modified
                    # .. and if we modify any part of the memory node
                    # we expect it to be fully specified, so we'll
                    # throw out all existing values by not adding them
                    # here.
                    pass
                    # mem_reg_val_new.extend( cells )
                    # mem_reg_val_new.extend( size_cells )

            if mem_changed_flag:
                _info( "domain_access:      *** changed value(s) detected, extending memory node with: %s" % [hex(i) for i in mem_reg_val_new])
                item["reg_val"].extend( mem_reg_val_new )
                # refcount it
                sdt.tree.ref_all( item["node"], True )

            if mem_matched_val_flag and not mem_changed_flag:
                _info( "domain_access:      *** matched memory value(S) detected, extending memory node with: %s" % [hex(i) for i in mem_reg_val_new])
                item["reg_val"].extend( mem_reg_val_new )
                # refcount it
                sdt.tree.ref_all( item["node"], True )


    # if any memory was modified, we have to update the address-maps to be
    # consistent.
    for mn_entry in memory_node_collector.values():
        # if there's no reg val, we have nothing to do since it wasn't
        # modified.
        if not mn_entry["reg_val"]:
            continue

        # update the memory node by assigning it the collected reg value
        mn_entry["node"]["reg"].value = mn_entry["reg_val"]

        mn = mn_entry["node"]

        _info( f"domain_access: processing modified memory node: {mn} (phandle: {mn.phandle})")

        # get all references to the modified memory node, we'll see
        # if they need to be removed and re-added based on what we
        # calcuated above.
        all_memory_prop_refs = lopper_lib.all_refs( sdt.tree, mn )
        for ref_prop in all_memory_prop_refs:
            # we are are only updating address-map properties with
            # the new memory node values
            if ref_prop.name != "address-map":
                continue

            cpu_node = ref_prop.node
            try:
                acells = cpu_node['#ranges-address-cells'][0]
                scells = cpu_node['#ranges-size-cells'][0]
                ### ****************** This is not what is being used to construct the address-map
                ###                    in the system-device-tree. For r5 cpus, the root node has
                ###                    an address-cells size of '2', but we only have a single entry
                ###                    in the address-map.
                ###
                ###                    It looks like the ranges-address-cells is being used for both
                ###                    of the address entries in the address-map
                root_node_acells = sdt.tree['/']['#address-cells'][0]
                ## TEMP. TEMP. TEMP.
                # clobber the looked up value for testing purposes, since the SDT seems to
                # not be using this correctly
                root_node_acells = acells
                _info( "domain_access:    cpu: %s addr cells: %s size cells: %s root address cells: %s" %
                       (cpu_node,acells,scells,root_node_acells))
            except Exception as e:
                _error( f"domain_access: could not determine cell sizes for address-map fixup: {e}", True )

            _info( f"domain_access:      updating address-map for node: {cpu_node}" )

            phandle_idx = acells
            # The address map entry is "adddress cells" + phandle + "root node address size" + "size cells"
            address_map_chunks = chunks( cpu_node["address-map"].value, acells + 1 + root_node_acells + scells )

            ## To do the update, we must:
            ##   - remove the entire entry for a matching phandle to the modified memory node
            ##   - create new address-map entries for however many memory entries there are in the
            ##     modified memory node

            address_map_new = []
            skip_handles = []
            for achunk in address_map_chunks:
                if achunk[phandle_idx] == mn.phandle:
                    if mn.phandle in skip_handles:
                        _info( f"domain_access: address-map for phandle {mn.phandle} has been updated, skipping existing entry" )
                        continue

                    _info( f"domain_access:      matching phandle ({mn.phandle}) found in the address-map" )

                    # Add the new values, we'll delete all existing ones after (by skipping them)

                    # We need to loop through the memory node reg entries and generate new
                    # address map entries.

                    reg_val = mn_entry["reg_val"]
                    # the reg is of size "address cells" + "size cells"
                    sdt_memory_node_chunks = chunks( reg_val, mn_entry["ac"] + mn_entry["sc"] )
                    for chunk in sdt_memory_node_chunks:
                        node_address, cells = lopper_lib.cell_value_get( chunk, mn_entry["ac"], 0 )

                        address_map_new.extend( lopper_lib.cell_value_split( node_address, acells ) )
                        address_map_new.append( mn.phandle )
                        address_map_new.extend( lopper_lib.cell_value_split( node_address, root_node_acells ) )

                        node_size, cells = lopper_lib.cell_value_get( chunk, mn_entry["sc"], mn_entry["ac"] )
                        address_map_new.extend( lopper_lib.cell_value_split( node_size, scells ) )

                    # the rest of the address-map entries for this phandle
                    # should be deleted (skipped)
                    skip_handles.append( mn.phandle )

                else:
                    address_map_new.extend( achunk )

            # put our rebuilt address-map back into the node, we'll repeat this
            # for all modified memory nodes
            cpu_node["address-map"].value = address_map_new

    # 7) want our domains node last, just for readability
    try:
        reserved_memory_node = domain_node.subnodes(children_only=True,name="reserved-memory$")
        if reserved_memory_node:
            sdt.tree['/'].reorder_child( "/domains", "/reserved-memory", after=True )
    except Exception as e:
        lopper.log._warning( f"exception while processing reserved-memory: {e}")

    # 8) chosen node processing
    try:
        chosen_node = domain_node.subnodes(children_only=True,name="chosen$")
        if chosen_node:
            lopper.log._debug( "processing chosen node" )

            # we want our domains node last, just for readability
            sdt.tree['/'].reorder_child( "/domains", "/chosen", after=True )
    except Exception as e:
        lopper.log._warning( f"exception while processing chosen: {e}")

    # delete unreferenced memory nodes
    prop = "memory"
    code = f"""
           p = node.propval( 'device_type' )
           if p and "{prop}" in p:
               r = node.ref
               if r <= 0:
                   return True
               else:
                   return False
           else:
               return False
           """

    _info( f"domain_access: core_domain_access: deleting unreferenced memory:\n------{code}\n-------\n" )

    sdt.tree.filter( "/", LopperAction.DELETE, code, None, verbose )

    # final) deal with unreferenced nodes
    refd_nodes = sdt.tree.refd()
    return True
