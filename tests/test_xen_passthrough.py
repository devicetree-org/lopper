"""
Pytest tests for the shared xen_passthrough helper module.

Covers the SDT->Xen passthrough conversion primitives used by both
image-builder --gen-config (single pass) and the extract-xen CLI:
stream-id extraction, reg->xen,reg, no-iommu fallback, and the
base-tree-untouched guarantee of build_passthrough_overlay.

Copyright (C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.

SPDX-License-Identifier: BSD-3-Clause

Author:
    Bruce Ashfield <bruce.ashfield@amd.com>
"""

import os
import sys
import pytest
from pathlib import Path

from lopper import LopperSDT

# xen_passthrough lives in lopper/assists; import it the way the assists do.
_ASSISTS = Path(__file__).resolve().parents[1] / "lopper" / "assists"
sys.path.insert(0, str(_ASSISTS))
import xen_passthrough  # noqa: E402
import lopper_lib  # noqa: E402

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
    sdt.output_file = str(Path(test_outdir) / "xpt-output.dts")
    sdt.cleanup_flag = True
    sdt.save_temps = False
    sdt.enhanced = True
    sdt.outdir = str(test_outdir)
    sdt.use_libfdt = libfdt
    sdt.setup(sdt.dts, [], "", True, libfdt=libfdt)
    return sdt


@pytest.fixture
def pt_sdt(test_outdir):
    return _build_sdt(FIXTURES / "xen-sdt-passthrough.dts", test_outdir)


def _find(sdt, name_substr):
    for n in sdt.tree:
        if name_substr in n.name:
            return n
    return None


class TestGuestGicConstant:
    def test_value(self):
        # Historical literal from extract-xen; must stay 0xfde8.
        assert xen_passthrough.GUEST_GIC_PHANDLE == 0xfde8


class TestXenifyIommus:
    def test_stream_id_extracted(self, pt_sdt):
        if not _have_libfdt():
            pytest.skip("libfdt not available")
        dma = _find(pt_sdt, "dma@ffa80000")
        assert dma is not None
        emitted, smmu_paths = xen_passthrough.xenify_iommus(dma, pt_sdt)
        assert emitted is True
        assert dma.propval("xen,smmu-stream-ids") == [0x210]
        # the host smmu was referenced and reported for pruning
        assert any("smmu" in p for p in smmu_paths)
        # host iommus property removed
        assert dma.propval("iommus") == ['']

    def test_no_iommus_returns_false(self, pt_sdt):
        if not _have_libfdt():
            pytest.skip("libfdt not available")
        uart = _find(pt_sdt, "serial@ff010000")
        assert uart is not None
        emitted, smmu_paths = xen_passthrough.xenify_iommus(uart, pt_sdt)
        assert emitted is False
        assert smmu_paths == []


class TestBuildPassthroughOverlay:
    def test_devices_converted(self, pt_sdt, test_outdir):
        if not _have_libfdt():
            pytest.skip("libfdt not available")
        guest = pt_sdt.tree["/domains/subsystem@1/hypervisor@2/domain@4"]
        devs = lopper_lib.node_accesses(pt_sdt.tree, guest)
        names = set(n.name for n in devs)
        assert "serial@ff010000" in names and "dma@ffa80000" in names

        ov = xen_passthrough.build_passthrough_overlay(
            pt_sdt, devs, "guest1", target_names=names)

        # write it so we can assert on the rendered content
        out = Path(test_outdir) / "guest1-passthrough.dts"
        pt_sdt.write(ov, str(out), True, True)
        content = out.read_text()

        # uart -> force-assign; dma -> stream-ids; both -> xen,reg + xen,path
        assert 'xen,force-assign-without-iommu' in content
        assert 'xen,smmu-stream-ids = <0x210>' in content
        assert 'xen,reg = <0x0 0xff010000 0x0 0x1000 0x0 0xff010000>' in content
        assert 'xen,reg = <0x0 0xffa80000 0x0 0x1000 0x0 0xffa80000>' in content
        assert 'interrupt-parent = <0xfde8>' in content
        # host smmu node must NOT be in the guest fragment
        assert 'smmu@fd800000' not in content

    def test_base_tree_untouched(self, pt_sdt):
        if not _have_libfdt():
            pytest.skip("libfdt not available")
        guest = pt_sdt.tree["/domains/subsystem@1/hypervisor@2/domain@4"]
        devs = lopper_lib.node_accesses(pt_sdt.tree, guest)

        # snapshot the source device's reg + presence before building overlay
        uart_before = pt_sdt.tree["/bus@f1000000/serial@ff010000"]
        reg_before = list(uart_before.propval("reg"))

        xen_passthrough.build_passthrough_overlay(
            pt_sdt, devs, "guest1", target_names=set(n.name for n in devs))

        # device still present in the base tree, reg unchanged, not xenified
        uart_after = pt_sdt.tree["/bus@f1000000/serial@ff010000"]
        assert list(uart_after.propval("reg")) == reg_before
        assert uart_after.propval("xen,reg") == ['']
        assert uart_after.propval("xen,smmu-stream-ids") == ['']


class TestMarkSourcePassthrough:
    def test_marks_device(self, pt_sdt):
        if not _have_libfdt():
            pytest.skip("libfdt not available")
        path = "/bus@f1000000/dma@ffa80000"
        xen_passthrough.mark_source_passthrough(pt_sdt, [path])
        node = pt_sdt.tree[path]
        # property present (empty value)
        assert "xen,passthrough" in [p.name for p in node]

    def test_idempotent(self, pt_sdt):
        if not _have_libfdt():
            pytest.skip("libfdt not available")
        path = "/bus@f1000000/dma@ffa80000"
        xen_passthrough.mark_source_passthrough(pt_sdt, [path])
        xen_passthrough.mark_source_passthrough(pt_sdt, [path])
        node = pt_sdt.tree[path]
        marks = [p.name for p in node if p.name == "xen,passthrough"]
        assert len(marks) == 1
