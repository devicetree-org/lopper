#/*
# * Copyright (c) 2022 AMD Inc. All rights reserved.
# *
# * Author:
# *       Bruce Ashfield <bruce.ashfield@amd.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

import sys
import types
import os
import getopt
import re
import copy
import subprocess
import textwrap

def is_compat( node, compat_string_to_test ):
    if re.search( "module,image-builder", compat_string_to_test):
        return image_builder
    return ""

def usage():
    print( """
   Usage: image-builder [--uboot] -o <output dir> --imagebuilder <path to imagebuilder>
          image-builder --gen-config <output-file> [--target <domain-path>]
                                                   [--invoke <imagebuilder-path> -o <outdir>]

    wrapper around imagebuilder (https://gitlab.com/xen-project/imagebuilder)

      --uboot       execute imagebuilder's "uboot-script-gen", with the
                    options: -t tftp -c ./config, and the supplied output directory
      --gen-config  walk the system device tree, locate the Xen container or flat
                    domain (compatible openamp,hypervisor-v1 or xen,domain-v*) and
                    emit an imagebuilder config file (xen.cfg) describing dom0 and
                    any dom0less guests. No silent fallbacks: keys whose source
                    xen,* property is unset are omitted from the output.
      --target      restrict --gen-config to the named domain path (e.g.
                    /domains/APU_Linux); useful when the SDT has multiple Xen
                    domains. If omitted, the first Xen-shaped node is used.
      --invoke      after writing the cfg, run uboot-script-gen against it
      -i            path to imagebuilder clone (uboot mode only)
      -v            enable verbose debug/processing
      -o            output directory for files (uboot mode and --invoke)

    """)


# ----------------------------------------------------------------------
# Helpers (intentionally module-level so tests can exercise them directly)
# ----------------------------------------------------------------------

# Compatible strings that mark a Xen-related domain node.
_HYPERVISOR_COMPAT = "openamp,hypervisor-v1"
_VM_COMPAT_RE = re.compile( r"xen,domain-v\d+" )


def _popcount( n ):
    return bin( int(n) ).count( "1" )


def _read_memory( prop_value ):
    """Decode a memory = <addr_high addr_low size_high size_low ...> property.

    Returns a list of (start, size) pairs. Empty list if the property is
    absent or shorter than one full pair.
    """
    if not prop_value or prop_value == ['']:
        return []
    pairs = []
    for i in range( 0, len(prop_value) - 3, 4 ):
        start = (int(prop_value[i]) << 32) | int(prop_value[i + 1])
        size  = (int(prop_value[i + 2]) << 32) | int(prop_value[i + 3])
        pairs.append( (start, size) )
    return pairs


def _count_vcpus( prop_value ):
    """Sum popcount(cpumask) across all <&cluster cpumask mode> triplets."""
    if not prop_value or prop_value == ['']:
        return 0
    total = 0
    # each entry: phandle (1 cell), cpumask (1 cell), mode (1 cell)
    for i in range( 1, len(prop_value), 3 ):
        total += _popcount( prop_value[i] )
    return total


def _node_compat( node ):
    """Return the compatible list for a node, or [] if absent."""
    c = node.propval( "compatible", list )
    if not c or c == ['']:
        return []
    return [s for s in c if s]


def _is_hypervisor( node ):
    return _HYPERVISOR_COMPAT in _node_compat( node )


def _is_vm( node ):
    return any( _VM_COMPAT_RE.search( c ) for c in _node_compat( node ) )


def _find_xen_node( sdt, target_path=None ):
    """Locate the Xen entry point in the SDT.

    Returns (kind, node) where kind is "container" (Shape B — has VM
    children under it) or "flat" (Shape A — single xen,domain-v* node
    treated as both container and dom0).

    If ``target_path`` is provided, only that exact path is considered.
    Otherwise the first Xen-shaped node found is used.
    """
    candidates = []
    for node in sdt.tree:
        if _is_hypervisor( node ):
            candidates.append( ("container", node) )
        elif _is_vm( node ):
            # Flat shape only counts if the parent isn't itself a hypervisor
            # container (otherwise this node is a VM child, not the entry).
            try:
                parent = node.parent
            except AttributeError:
                parent = None
            if parent is None or not _is_hypervisor( parent ):
                candidates.append( ("flat", node) )

    if target_path is not None:
        for kind, node in candidates:
            if node.abs_path == target_path:
                return (kind, node)
        return (None, None)

    if candidates:
        return candidates[0]
    return (None, None)


def _vm_children( container ):
    """Immediate child nodes of the container that look like VM nodes."""
    return [
        child for child in container.subnodes( children_only=True )
        if _is_vm( child )
    ]


def _propagate_lines( node ):
    """Return the verbatim lines from xen,propagate-config on a node."""
    lines = node.propval( "xen,propagate-config", list )
    if not lines or lines == ['']:
        return []
    return [s for s in lines if s]


def _emit_vm_section( lines, vm, idx ):
    """Append DOMU_*[idx] keys for a single dom0less guest."""
    k = vm.propval( "xen,kernel", list )
    if k and k[0]:
        lines.append( f'DOMU_KERNEL[{idx}]="{k[0]}"' )
    kc = vm.propval( "xen,kernel-cmdline", list )
    if kc and kc[0]:
        lines.append( f'DOMU_CMD[{idx}]="{kc[0]}"' )
    vcpus = _count_vcpus( vm.propval( "cpus" ) )
    if vcpus:
        lines.append( f'DOMU_VCPUS[{idx}]={vcpus}' )
    mem_pairs = _read_memory( vm.propval( "memory" ) )
    if mem_pairs:
        lines.append( f'DOMU_MEM[{idx}]={mem_pairs[0][1] // (1024 * 1024)}' )
    lines.extend( _propagate_lines( vm ) )


def _gen_xen_config( sdt, output_path, target_path, verbose ):
    """Walk the SDT, build a Xen ImageBuilder config, write to output_path."""
    kind, entry = _find_xen_node( sdt, target_path )
    if entry is None:
        if target_path:
            print( f"[ERROR][image-builder]: no Xen node at {target_path}" )
        else:
            print( "[ERROR][image-builder]: no Xen container "
                   "(openamp,hypervisor-v1) or flat xen,domain-v* node "
                   "found in SDT" )
        return False

    if kind == "container":
        container = entry
        vms = _vm_children( container )
        if not vms:
            print( "[ERROR][image-builder]: Xen container has no VM children "
                   "(compatible xen,domain-v*)" )
            return False
        dom0 = vms[0]
        extras = vms[1:]
    else:
        # Shape A: the flat node IS the dom0; no DOMU children.
        container = entry
        dom0 = entry
        extras = []

    boot_mode_p = container.propval( "xen,boot-mode", list )
    boot_mode = boot_mode_p[0] if boot_mode_p and boot_mode_p[0] else "dom0"

    lines = []

    # Container memory → MEMORY_START / MEMORY_END (covers full range)
    mem_pairs = _read_memory( container.propval( "memory" ) )
    if mem_pairs:
        start = mem_pairs[0][0]
        end = mem_pairs[-1][0] + mem_pairs[-1][1]
        lines.append( f'MEMORY_START="{hex(start)}"' )
        lines.append( f'MEMORY_END="{hex(end)}"' )

    # xen,binary / xen,cmdline / xen,device-tree (omit if unset)
    xb = container.propval( "xen,binary", list )
    if xb and xb[0]:
        lines.append( f'XEN="{xb[0]}"' )
    xc = container.propval( "xen,cmdline", list )
    if xc and xc[0]:
        lines.append( f'XEN_CMD="{xc[0]}"' )
    dt = container.propval( "xen,device-tree", list )
    if dt and dt[0]:
        lines.append( f'DEVICE_TREE="{dt[0]}"' )

    # dom0 keys (omit if unset)
    k = dom0.propval( "xen,kernel", list )
    if k and k[0]:
        lines.append( f'DOM0_KERNEL="{k[0]}"' )
    kc = dom0.propval( "xen,kernel-cmdline", list )
    if kc and kc[0]:
        lines.append( f'DOM0_CMD="{kc[0]}"' )
    vcpus = _count_vcpus( dom0.propval( "cpus" ) )
    if vcpus:
        lines.append( f'DOM0_VCPUS={vcpus}' )
    dom0_mem = _read_memory( dom0.propval( "memory" ) )
    if dom0_mem:
        lines.append( f'DOM0_MEM={dom0_mem[0][1] // (1024 * 1024)}' )
    rd = dom0.propval( "xen,initrd", list )
    if rd and rd[0]:
        lines.append( f'DOM0_RAMDISK="{rd[0]}"' )

    # Container-level propagate-config (after derived container/dom0 keys,
    # before the per-VM section). For Shape A, we still emit the dom0 node's
    # propagate lines here since there are no VM extras.
    if kind == "container":
        lines.extend( _propagate_lines( container ) )
        # Shape A's dom0 IS the container, so its propagate lines are emitted
        # only once; for Shape B, also emit dom0's per-VM propagate lines:
        if dom0 is not container:
            lines.extend( _propagate_lines( dom0 ) )
    else:
        lines.extend( _propagate_lines( container ) )

    # Always-emit constants
    lines.append( f'NUM_DOMUS={len(extras)}' )

    # DOMU sections (only meaningful in dom0less mode, but we already
    # gate by len(extras) which is 0 for Shape A and dom0-only Shape B)
    for i, vm in enumerate( extras, start=1 ):
        _emit_vm_section( lines, vm, i )

    # Constant footer
    lines.append( 'UBOOT_SOURCE="boot.source"' )
    lines.append( 'UBOOT_SCRIPT="boot.scr"' )

    with open( output_path, 'w' ) as f:
        f.write( "\n".join( lines ) + "\n" )

    if verbose:
        print( f"[INFO][image-builder]: wrote {len(lines)} keys to {output_path}",
               file=sys.stderr )

    print( f"[INFO][image-builder]: wrote {output_path}", file=sys.stderr )
    print( f"[INFO][image-builder]: to generate a U-Boot boot.scr from this config:",
           file=sys.stderr )
    print( f"  <imagebuilder>/scripts/uboot-script-gen -t <load-cmd> -d <outdir> "
           f"-c {output_path}", file=sys.stderr )
    print( f"[INFO][image-builder]: or re-invoke with --invoke "
           f"<imagebuilder-path> to run it now", file=sys.stderr )

    return True


def _invoke_uboot_script_gen( imagebuilder_path, cfg_path, outdir ):
    """Run imagebuilder's uboot-script-gen against an already-written cfg."""
    if not outdir:
        print( "[ERROR][image-builder]: --invoke requires -o <outdir>" )
        return False
    cmd = [
        f'{imagebuilder_path}/scripts/uboot-script-gen',
        '-t', 'tftp',
        '-d', outdir,
        '-c', cfg_path,
    ]
    result = subprocess.run( cmd, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, timeout=10 )
    if result.returncode != 0:
        print( "[ERROR][image-builder]: uboot-script-gen failed" )
        print( "\n%s" % textwrap.indent( result.stderr.decode(), '         ' ) )
        return False
    return True


def image_builder( tgt_node, sdt, options ):
    try:
        verbose = options['verbose']
    except:
        verbose = 0

    try:
        args = options['args']
    except:
        args = []

    opts, args2 = getopt.getopt(
        args, "vpt:o:i:c:",
        [ "uboot", "verbose", "imagebuilder=",
          "gen-config=", "target=", "invoke=" ]
    )

    image_type = "uboot"
    output = None
    cfg_path = None
    target_path = None
    imagebuilder_path = None
    invoke_path = None
    for o, a in opts:
        if o in ('-o',):
            output = a
        elif o in ('-v', "--verbose"):
            verbose = verbose + 1
        elif o in ("--uboot",):
            image_type = "uboot"
        elif o in ("-c", "--gen-config"):
            image_type = "config"
            cfg_path = a
        elif o in ("--target",):
            target_path = a
        elif o in ("--invoke",):
            invoke_path = a
        elif o in ("-t",):
            # legacy short -t in uboot mode is unused; keep accepted for compat
            pass
        elif o in ("-i", "--imagebuilder"):
            imagebuilder_path = a

    if image_type == "config":
        if not cfg_path:
            print( "[ERROR][image-builder]: --gen-config requires an output path" )
            return False
        ok = _gen_xen_config( sdt, cfg_path, target_path, verbose )
        if not ok:
            return False
        if invoke_path:
            return _invoke_uboot_script_gen( invoke_path, cfg_path, output )
        return True

    # uboot path (existing behavior, unchanged)
    try:
        xen_tree = sdt.subtrees["extracted"]
    except:
        print( "[ERROR]: no extracted tree detected, returning" )
        return False

    if not imagebuilder_path:
        print( "[ERROR][imagebuilder]: path to image builder not passed" )
        sys.exit(1)

    if image_type == "uboot":
        print( "[INFO][imagebuilder]: generating uboot" )

        if not output:
            print( "[ERROR]: path to imagebuilder missing" )
            sys.exit(1)

        result = subprocess.run(['%s/scripts/uboot-script-gen' % imagebuilder_path,
                                 '-t', 'tftp', '-d', '%s' % output,
                                 '-c', '%s/config' % output ],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=3)
        if result.returncode != 0:
            print( "[ERROR]: unable to generate uboot scripts" )
            print( "\n%s" % textwrap.indent(result.stderr.decode(), '         ') )
            sys.exit(result.returncode)

    return True
