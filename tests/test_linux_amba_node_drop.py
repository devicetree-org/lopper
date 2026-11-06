"""Linux DT removal of PS amba nodes whose label equals xlnx,name."""

import pytest

from lopper.assists.gen_domain_dts import _drop_xlnx_name_amba_nodes
from lopper.tree import LopperNode, LopperTree


def test_linux_dt_drops_amba_nodes_whose_label_equals_xlnx_name():
    """A new PS amba node is removed. Kernel labels, PL, and memory stay."""
    tree = LopperTree()
    amba = LopperNode(-1, "/axi")
    amba.label = "amba"
    tree + amba

    extra = LopperNode(-1, "/axi/versal_cips_0_pspmc_0_psv_pmc_tap")
    extra.label = "versal_cips_0_pspmc_0_psv_pmc_tap"
    extra["xlnx,name"] = "versal_cips_0_pspmc_0_psv_pmc_tap"
    tree + extra

    kernel = LopperNode(-1, "/axi/iomodule@f0280000")
    kernel.label = "iomodule0"
    kernel["xlnx,name"] = "versal_cips_0_pspmc_0_psv_pmc_iomodule_0"
    tree + kernel

    pl = LopperNode(-1, "/amba_pl/gpio@80010000")
    pl.label = "axi_gpio_0"
    pl["xlnx,name"] = "axi_gpio_0"
    tree + pl

    memory = LopperNode(-1, "/memory@fffc0000")
    memory.label = "versal_cips_0_pspmc_0_psv_ocm_ram_0_memory"
    memory["xlnx,name"] = "versal_cips_0_pspmc_0_psv_ocm_ram_0_memory"
    tree + memory

    removed = _drop_xlnx_name_amba_nodes(tree)

    assert removed == 1
    with pytest.raises(KeyError):
        tree["/axi/versal_cips_0_pspmc_0_psv_pmc_tap"]
    assert tree["/axi/iomodule@f0280000"].label == "iomodule0"
    assert tree["/amba_pl/gpio@80010000"].label == "axi_gpio_0"
    assert tree["/memory@fffc0000"].label == "versal_cips_0_pspmc_0_psv_ocm_ram_0_memory"
