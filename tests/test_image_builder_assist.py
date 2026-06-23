"""
Pytest tests for the image-builder assist's --gen-config mode.

Exercises the new domains.yaml/SDT -> Xen ImageBuilder config (xen.cfg)
generation path, both Shape A (flat xen,domain-v1) and Shape B (container +
xen,domain-v2 children). Existing --uboot mode is not covered here (requires
an external imagebuilder checkout).

Copyright (C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.

SPDX-License-Identifier: BSD-3-Clause

Author:
    Bruce Ashfield <bruce.ashfield@amd.com>
"""

import os
import pytest
from importlib.machinery import SourceFileLoader
from pathlib import Path

from lopper import LopperSDT


# image-builder.py uses a hyphen so it can't be imported via
# ``from lopper.assists import image_builder``. Load by path.
_ASSIST_PATH = Path(__file__).resolve().parents[1] / "lopper" / "assists" / "image-builder.py"
image_builder = SourceFileLoader("image_builder_assist", str(_ASSIST_PATH)).load_module()

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _have_libfdt():
    try:
        import libfdt  # noqa: F401
        return True
    except ImportError:
        return False


def _build_sdt(dts_path, test_outdir):
    libfdt = _have_libfdt()
    sdt = LopperSDT(str(dts_path))
    sdt.dryrun = False
    sdt.verbose = 0
    sdt.werror = False
    sdt.output_file = str(Path(test_outdir) / "image-builder-output.dts")
    sdt.cleanup_flag = True
    sdt.save_temps = False
    sdt.enhanced = True
    sdt.outdir = str(test_outdir)
    sdt.use_libfdt = libfdt
    sdt.setup(sdt.dts, [], "", True, libfdt=libfdt)
    return sdt


@pytest.fixture
def xen_flat_sdt(test_outdir):
    return _build_sdt(FIXTURES / "xen-sdt-flat.dts", test_outdir)


@pytest.fixture
def xen_dom0_sdt(test_outdir):
    return _build_sdt(FIXTURES / "xen-sdt-dom0.dts", test_outdir)


@pytest.fixture
def xen_dom0less_sdt(test_outdir):
    return _build_sdt(FIXTURES / "xen-sdt-dom0less.dts", test_outdir)


# ----------------------------------------------------------------------
# is_compat
# ----------------------------------------------------------------------

class TestIsCompat:
    def test_matches_image_builder(self):
        assert image_builder.is_compat(None, "module,image-builder") == image_builder.image_builder

    def test_does_not_match_other(self):
        assert image_builder.is_compat(None, "module,grep") == ""
        assert image_builder.is_compat(None, "module,image-other") == ""


# ----------------------------------------------------------------------
# Pure helpers — no SDT needed
# ----------------------------------------------------------------------

class TestHelpers:
    def test_popcount(self):
        assert image_builder._popcount(0x0) == 0
        assert image_builder._popcount(0x1) == 1
        assert image_builder._popcount(0x3) == 2
        assert image_builder._popcount(0x7) == 3
        assert image_builder._popcount(0xff) == 8

    def test_read_memory_one_pair(self):
        # memory = <0x0 0x40000000 0x0 0x40000000>
        pairs = image_builder._read_memory([0, 0x40000000, 0, 0x40000000])
        assert pairs == [(0x40000000, 0x40000000)]

    def test_read_memory_multi_pair(self):
        # 4 regions like Ben's input
        prop = [0x0, 0x0, 0x0, 0x80000000,
                0x8, 0x0, 0x0, 0x80000000,
                0x500, 0x0, 0x1, 0x0,
                0x600, 0x0, 0x0, 0x80000000]
        pairs = image_builder._read_memory(prop)
        assert len(pairs) == 4
        assert pairs[0] == (0x0, 0x80000000)
        assert pairs[-1] == ((0x600 << 32) | 0x0, 0x80000000)

    def test_read_memory_handles_high_bits(self):
        pairs = image_builder._read_memory([0x1, 0x0, 0x0, 0x80000000])
        assert pairs == [(0x100000000, 0x80000000)]

    def test_read_memory_empty(self):
        assert image_builder._read_memory([]) == []
        assert image_builder._read_memory(['']) == []
        assert image_builder._read_memory([0, 0]) == []  # too short for one pair

    def test_count_vcpus_single_triplet(self):
        assert image_builder._count_vcpus([0xdead, 0x3, 0x80000003]) == 2

    def test_count_vcpus_multi_triplet(self):
        # two triplets, masks 0x3 and 0x4 -> 2 + 1 = 3 vcpus
        assert image_builder._count_vcpus([0x1, 0x3, 0x0, 0x2, 0x4, 0x0]) == 3

    def test_count_vcpus_empty(self):
        assert image_builder._count_vcpus([]) == 0
        assert image_builder._count_vcpus(['']) == 0


# ----------------------------------------------------------------------
# Shape A — flat xen,domain-v1 (Ben's POC pattern)
# ----------------------------------------------------------------------

class TestGenConfigFlat:
    def test_writes_expected_keys(self, xen_flat_sdt, test_outdir):
        if not _have_libfdt():
            pytest.skip("libfdt not available")
        out = Path(test_outdir) / "xen-flat.cfg"
        options = {"verbose": 0, "args": ["--gen-config", str(out)]}

        result = image_builder.image_builder(None, xen_flat_sdt, options)
        assert result is True
        content = out.read_text()

        # Container/flat-derived
        assert 'XEN="xen"' in content
        assert 'XEN_CMD="console=dtuart' in content
        assert 'DEVICE_TREE="mpsoc.dtb"' in content
        assert 'MEMORY_START="0x0"' in content
        assert 'MEMORY_END="0x80000000"' in content

        # Flat node treated as dom0
        assert 'DOM0_KERNEL="Image-dom0"' in content
        assert 'DOM0_RAMDISK="dom0-ramdisk.cpio"' in content
        assert 'DOM0_VCPUS=1' in content

        # Constants always emitted
        assert 'NUM_DOMUS=0' in content
        assert 'UBOOT_SOURCE="boot.source"' in content
        assert 'UBOOT_SCRIPT="boot.scr"' in content

        # No DOMU keys for flat shape
        assert 'DOMU_KERNEL' not in content


# ----------------------------------------------------------------------
# Shape B — container + dom0 only
# ----------------------------------------------------------------------

class TestGenConfigDom0Only:
    def test_writes_expected_keys(self, xen_dom0_sdt, test_outdir):
        if not _have_libfdt():
            pytest.skip("libfdt not available")
        out = Path(test_outdir) / "xen-dom0.cfg"
        options = {"verbose": 0, "args": ["--gen-config", str(out)]}

        result = image_builder.image_builder(None, xen_dom0_sdt, options)
        assert result is True
        content = out.read_text()

        # Container-derived
        assert 'XEN="xen.efi"' in content
        assert 'XEN_CMD="console=dtuart dtuart=serial0 dom0_mem=1024M"' in content
        assert 'DEVICE_TREE="system.dtb"' in content
        assert 'MEMORY_START="0x40000000"' in content
        assert 'MEMORY_END="0x80000000"' in content

        # dom0-derived
        assert 'DOM0_KERNEL="Image"' in content
        assert 'DOM0_CMD="console=hvc0 root=/dev/mmcblk0p2 rw"' in content
        assert 'DOM0_VCPUS=1' in content
        assert 'DOM0_MEM=1024' in content
        assert 'DOM0_RAMDISK="initrd"' in content

        # Constants
        assert 'NUM_DOMUS=0' in content
        assert 'UBOOT_SOURCE="boot.source"' in content
        assert 'UBOOT_SCRIPT="boot.scr"' in content

        # No DOMU keys
        assert 'DOMU_KERNEL' not in content


# ----------------------------------------------------------------------
# Shape B — container + dom0 + 1 dom0less guest
# ----------------------------------------------------------------------

class TestGenConfigDom0less:
    def test_writes_expected_keys(self, xen_dom0less_sdt, test_outdir):
        if not _have_libfdt():
            pytest.skip("libfdt not available")
        out = Path(test_outdir) / "xen-dom0less.cfg"
        options = {"verbose": 0, "args": ["--gen-config", str(out)]}

        result = image_builder.image_builder(None, xen_dom0less_sdt, options)
        assert result is True
        content = out.read_text()

        # Container + dom0 keys
        assert 'XEN="xen.efi"' in content
        assert 'DOM0_KERNEL="Image"' in content
        assert 'DOM0_VCPUS=2' in content   # cpumask 0x3 -> 2 cpus
        assert 'DOM0_MEM=1024' in content

        # DOMU section
        assert 'NUM_DOMUS=1' in content
        assert 'DOMU_KERNEL[1]="zephyr.bin"' in content
        assert 'DOMU_VCPUS[1]=1' in content   # cpumask 0x4 -> 1 cpu
        assert 'DOMU_MEM[1]=64' in content

        # Constants
        assert 'UBOOT_SOURCE="boot.source"' in content
        assert 'UBOOT_SCRIPT="boot.scr"' in content


# ----------------------------------------------------------------------
# Escape hatch: xen,propagate-config
# ----------------------------------------------------------------------

class TestPropagateConfig:
    def test_lines_emitted_verbatim(self, xen_dom0less_sdt, test_outdir):
        if not _have_libfdt():
            pytest.skip("libfdt not available")
        out = Path(test_outdir) / "xen-propagate.cfg"
        options = {"verbose": 0, "args": ["--gen-config", str(out)]}

        result = image_builder.image_builder(None, xen_dom0less_sdt, options)
        assert result is True
        content = out.read_text()

        # Container-level propagate line
        assert 'BITSTREAM="design.bin"' in content

        # VM-level propagate line for the dom0less guest
        assert 'DOMU_PASSTHROUGH_DTB[1]="zephyr-passthrough.dtb"' in content


# ----------------------------------------------------------------------
# No silent fallbacks
# ----------------------------------------------------------------------

# Build a no-xen-properties fixture inline rather than as a static .dts so the
# test is self-contained and the absence is obvious.
_NO_XEN_PROPS_DTS = """/dts-v1/;

/ {
    compatible = "test,xen-no-props";
    #address-cells = <2>;
    #size-cells = <2>;

    cpus_a72: cpus {
        #address-cells = <1>;
        #size-cells = <0>;
        #cpus-mask-cells = <1>;
        compatible = "cpus,cluster";
        cpu@0 { compatible = "arm,cortex-a72"; device_type = "cpu"; reg = <0>; };
    };

    memory@0 {
        device_type = "memory";
        reg = <0x0 0x0 0x0 0x80000000>;
    };

    domains {
        #address-cells = <2>;
        #size-cells = <2>;

        APU_Linux: domain@1 {
            compatible = "openamp,domain-v1", "xen,domain-v1";
            cpus = <&cpus_a72 0x1 0x80000003>;
            memory = <0x0 0x0 0x0 0x80000000>;
            /* Deliberately no xen,binary / xen,kernel / etc. */
        };
    };
};
"""


class TestNoSilentFallbacks:
    def test_missing_props_omit_keys(self, test_outdir):
        if not _have_libfdt():
            pytest.skip("libfdt not available")
        # Materialize the inline DTS into the test outdir
        dts = Path(test_outdir) / "no-xen-props.dts"
        dts.write_text(_NO_XEN_PROPS_DTS)
        sdt = _build_sdt(dts, test_outdir)

        out = Path(test_outdir) / "no-props.cfg"
        options = {"verbose": 0, "args": ["--gen-config", str(out)]}
        result = image_builder.image_builder(None, sdt, options)
        assert result is True
        content = out.read_text()

        # Constants + derivable keys present
        assert 'MEMORY_START="0x0"' in content
        assert 'MEMORY_END="0x80000000"' in content
        assert 'NUM_DOMUS=0' in content
        assert 'UBOOT_SOURCE="boot.source"' in content
        assert 'UBOOT_SCRIPT="boot.scr"' in content
        assert 'DOM0_VCPUS=1' in content

        # xen,* derived keys MUST be absent — no silent fallbacks
        assert 'XEN=' not in content
        assert 'XEN_CMD=' not in content
        assert 'DEVICE_TREE=' not in content
        assert 'DOM0_KERNEL=' not in content
        assert 'DOM0_CMD=' not in content
        assert 'DOM0_RAMDISK=' not in content


# ----------------------------------------------------------------------
# --target selector
# ----------------------------------------------------------------------

_TWO_DOMAINS_DTS = """/dts-v1/;

/ {
    compatible = "test,two-xen";
    #address-cells = <2>;
    #size-cells = <2>;

    cpus_a72: cpus {
        #address-cells = <1>;
        #size-cells = <0>;
        #cpus-mask-cells = <1>;
        compatible = "cpus,cluster";
        cpu@0 { compatible = "arm,cortex-a72"; device_type = "cpu"; reg = <0>; };
    };

    memory@0 {
        device_type = "memory";
        reg = <0x0 0x0 0x0 0x80000000>;
    };

    domains {
        #address-cells = <2>;
        #size-cells = <2>;

        first: domain@1 {
            compatible = "openamp,domain-v1", "xen,domain-v1";
            cpus = <&cpus_a72 0x1 0x80000003>;
            memory = <0x0 0x0 0x0 0x40000000>;
            xen,binary = "xen-first";
            xen,kernel = "kernel-first";
        };

        second: domain@2 {
            compatible = "openamp,domain-v1", "xen,domain-v1";
            cpus = <&cpus_a72 0x1 0x80000003>;
            memory = <0x0 0x40000000 0x0 0x40000000>;
            xen,binary = "xen-second";
            xen,kernel = "kernel-second";
        };
    };
};
"""


class TestTargetSelector:
    def test_picks_named_domain(self, test_outdir):
        if not _have_libfdt():
            pytest.skip("libfdt not available")
        dts = Path(test_outdir) / "two-xen.dts"
        dts.write_text(_TWO_DOMAINS_DTS)
        sdt = _build_sdt(dts, test_outdir)

        out = Path(test_outdir) / "second.cfg"
        options = {
            "verbose": 0,
            "args": ["--gen-config", str(out), "--target", "/domains/domain@2"],
        }
        result = image_builder.image_builder(None, sdt, options)
        assert result is True
        content = out.read_text()

        assert 'XEN="xen-second"' in content
        assert 'DOM0_KERNEL="kernel-second"' in content
        # First domain values must NOT appear
        assert 'xen-first' not in content
        assert 'kernel-first' not in content

    def test_unknown_target_returns_false(self, test_outdir):
        if not _have_libfdt():
            pytest.skip("libfdt not available")
        dts = Path(test_outdir) / "two-xen.dts"
        dts.write_text(_TWO_DOMAINS_DTS)
        sdt = _build_sdt(dts, test_outdir)

        out = Path(test_outdir) / "missing.cfg"
        options = {
            "verbose": 0,
            "args": ["--gen-config", str(out), "--target", "/domains/nonexistent"],
        }
        result = image_builder.image_builder(None, sdt, options)
        assert result is False


# ----------------------------------------------------------------------
# Device passthrough (SSW-9163) — single-pass fragment generation
# ----------------------------------------------------------------------

@pytest.fixture
def xen_passthrough_sdt(test_outdir):
    return _build_sdt(FIXTURES / "xen-sdt-passthrough.dts", test_outdir)


class TestPassthrough:
    def test_fragment_and_cfg_key(self, xen_passthrough_sdt, test_outdir):
        if not _have_libfdt():
            pytest.skip("libfdt not available")
        out = Path(test_outdir) / "pt-xen.cfg"
        ptdir = Path(test_outdir)
        options = {"verbose": 0, "args": [
            "--gen-config", str(out), "--passthrough-dir", str(ptdir)]}

        result = image_builder.image_builder(None, xen_passthrough_sdt, options)
        assert result is True

        cfg = out.read_text()
        # the dom0less guest's passthrough dtb is referenced
        assert 'DOMU_PASSTHROUGH_DTB[1]="guest1-passthrough.dtb"' in cfg

        # the fragment was written, as DTS source
        frag = ptdir / "guest1-passthrough.dts"
        assert frag.exists()
        fc = frag.read_text()
        assert 'xen,force-assign-without-iommu' in fc      # uart, no iommus
        assert 'xen,smmu-stream-ids = <0x210>' in fc        # dma, with iommus
        assert 'interrupt-parent = <0xfde8>' in fc          # guest GIC
        assert 'smmu@fd800000' not in fc                    # host smmu pruned

    def test_no_access_no_generated_fragment(self, xen_dom0less_sdt, test_outdir):
        # the dom0less fixture's guest (zephyr_guest) has no `access` list, so
        # the assist must NOT generate a passthrough fragment file for it.
        # (That fixture does carry a manual DOMU_PASSTHROUGH_DTB line via the
        # xen,propagate-config escape hatch — that's a verbatim user injection,
        # not assist-generated, so we assert on the absence of the file.)
        if not _have_libfdt():
            pytest.skip("libfdt not available")
        ptdir = Path(test_outdir) / "no-access-ptdir"
        ptdir.mkdir()
        out = ptdir / "no-pt-xen.cfg"
        options = {"verbose": 0, "args": [
            "--gen-config", str(out), "--passthrough-dir", str(ptdir)]}
        result = image_builder.image_builder(None, xen_dom0less_sdt, options)
        assert result is True
        # no *-passthrough.dts fragment generated
        frags = list(ptdir.glob("*-passthrough.dts"))
        assert frags == [], f"unexpected fragments: {frags}"


# ----------------------------------------------------------------------
# Errors
# ----------------------------------------------------------------------

class TestGenConfigErrors:
    def test_missing_output_path(self, xen_dom0_sdt):
        if not _have_libfdt():
            pytest.skip("libfdt not available")
        options = {"verbose": 0, "args": ["--gen-config", ""]}
        result = image_builder.image_builder(None, xen_dom0_sdt, options)
        assert result is False

    def test_no_xen_node(self, test_outdir):
        if not _have_libfdt():
            pytest.skip("libfdt not available")
        dts = Path(test_outdir) / "no-xen.dts"
        dts.write_text("""/dts-v1/;
/ {
    compatible = "test,no-xen";
    #address-cells = <2>;
    #size-cells = <2>;
    memory@0 { device_type = "memory"; reg = <0 0 0 0x80000000>; };
};
""")
        sdt = _build_sdt(dts, test_outdir)
        out = Path(test_outdir) / "noxen.cfg"
        options = {"verbose": 0, "args": ["--gen-config", str(out)]}
        result = image_builder.image_builder(None, sdt, options)
        assert result is False
