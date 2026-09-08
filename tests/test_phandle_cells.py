"""
Tests for phandle property cell lookups.

Validates that phandle_possible_properties correctly handles properties
with variable cell counts by looking up #*-cells properties on the
referenced nodes.

Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: BSD-3-Clause

Author:
    Bruce Ashfield <bruce.ashfield@amd.com>
"""

import pytest
import tempfile
from pathlib import Path

from lopper import Lopper, LopperSDT
from lopper.tree import LopperTree, LopperNode, LopperProp
from lopper.base import lopper_base


# Path to the test DTS file
SELFTEST_DIR = Path(__file__).parent.parent / "lopper" / "selftest"
PHANDLE_CELL_TEST_DTS = SELFTEST_DIR / "phandle-cell-test.dts"


@pytest.fixture
def phandle_cell_tree(tmp_path):
    """Load the phandle cell test device tree."""
    if not PHANDLE_CELL_TEST_DTS.exists():
        pytest.skip(f"Test file not found: {PHANDLE_CELL_TEST_DTS}")

    # Check if libfdt is available
    libfdt_available = False
    try:
        import libfdt
        libfdt_available = True
    except ImportError:
        pass

    sdt = LopperSDT(str(PHANDLE_CELL_TEST_DTS))
    sdt.dryrun = False
    sdt.verbose = 0
    sdt.werror = False
    sdt.output_file = str(tmp_path / "output.dts")
    sdt.cleanup_flag = True
    sdt.save_temps = False
    sdt.enhanced = True
    sdt.outdir = str(tmp_path)
    sdt.libfdt = libfdt_available

    # Setup the device tree
    sdt.setup(str(PHANDLE_CELL_TEST_DTS), [], "", True, libfdt=libfdt_available)

    return sdt.tree


class TestPhandlePossiblePropertiesDefinitions:
    """Test that phandle_possible_properties has correct definitions."""

    def test_interrupts_extended_uses_cell_lookup(self):
        """interrupts-extended should use #interrupt-cells lookup."""
        props = lopper_base.phandle_possible_properties()
        assert "interrupts-extended" in props
        assert "#interrupt-cells" in props["interrupts-extended"][0]

    def test_iommus_uses_cell_lookup(self):
        """iommus should use #iommu-cells lookup."""
        props = lopper_base.phandle_possible_properties()
        assert "iommus" in props
        assert "#iommu-cells" in props["iommus"][0]

    def test_resets_uses_cell_lookup(self):
        """resets should use #reset-cells lookup."""
        props = lopper_base.phandle_possible_properties()
        assert "resets" in props
        assert "#reset-cells" in props["resets"][0]

    def test_power_domains_uses_cell_lookup(self):
        """power-domains should use #power-domain-cells lookup."""
        props = lopper_base.phandle_possible_properties()
        assert "power-domains" in props
        assert "#power-domain-cells" in props["power-domains"][0]

    def test_mboxes_uses_cell_lookup(self):
        """mboxes should use #mbox-cells lookup."""
        props = lopper_base.phandle_possible_properties()
        assert "mboxes" in props
        assert "#mbox-cells" in props["mboxes"][0]

    def test_dmas_uses_cell_lookup(self):
        """dmas should use #dma-cells lookup."""
        props = lopper_base.phandle_possible_properties()
        assert "dmas" in props
        assert "#dma-cells" in props["dmas"][0]

    def test_phys_uses_cell_lookup(self):
        """phys should use #phy-cells lookup."""
        props = lopper_base.phandle_possible_properties()
        assert "phys" in props
        assert "#phy-cells" in props["phys"][0]

    def test_thermal_sensors_uses_cell_lookup(self):
        """thermal-sensors should use #thermal-sensor-cells lookup."""
        props = lopper_base.phandle_possible_properties()
        assert "thermal-sensors" in props
        assert "#thermal-sensor-cells" in props["thermal-sensors"][0]


class TestInterruptsExtendedCellLookup:
    """Test interrupts-extended property with varying #interrupt-cells."""

    def test_resolves_1cell_interrupt_controller(self, phandle_cell_tree):
        """Test resolving phandle to intc with #interrupt-cells = <1>."""
        device = phandle_cell_tree["/device@100000"]
        assert device is not None

        prop = device["interrupts-extended"]
        assert prop is not None

        # resolve_phandles should find all three interrupt controllers
        phandles = prop.resolve_phandles()
        assert len(phandles) == 3, f"Expected 3 phandles, got {len(phandles)}"

    def test_phandle_map_varying_cells(self, phandle_cell_tree):
        """Test phandle_map correctly accounts for varying cell counts."""
        device = phandle_cell_tree["/device@100000"]
        prop = device["interrupts-extended"]

        pmap = prop.phandle_map()
        # phandle_map returns a list of lists, where nodes indicate phandle positions
        assert len(pmap) > 0, "phandle_map should not be empty"

        # Flatten and count phandle positions (non-zero entries)
        flat = [x for xs in pmap for x in xs]
        phandle_count = sum(1 for x in flat if x)
        assert phandle_count == 3, f"Expected 3 phandle positions, got {phandle_count}"


class TestIommusCellLookup:
    """Test iommus property with varying #iommu-cells."""

    def test_resolves_varying_iommu_cells(self, phandle_cell_tree):
        """Test resolving iommus with 0, 1, and 2 cell controllers."""
        device = phandle_cell_tree["/device@200000"]
        assert device is not None

        prop = device["iommus"]
        assert prop is not None

        phandles = prop.resolve_phandles()
        assert len(phandles) == 3, f"Expected 3 phandles, got {len(phandles)}"

    def test_phandle_map_iommus(self, phandle_cell_tree):
        """Test phandle_map for iommus with varying cells."""
        device = phandle_cell_tree["/device@200000"]
        prop = device["iommus"]

        pmap = prop.phandle_map()
        assert len(pmap) > 0

        flat = [x for xs in pmap for x in xs]
        phandle_count = sum(1 for x in flat if x)
        assert phandle_count == 3


class TestResetsCellLookup:
    """Test resets property with varying #reset-cells."""

    def test_resolves_varying_reset_cells(self, phandle_cell_tree):
        """Test resolving resets with 0, 1, and 2 cell controllers."""
        device = phandle_cell_tree["/device@300000"]
        assert device is not None

        prop = device["resets"]
        assert prop is not None

        phandles = prop.resolve_phandles()
        assert len(phandles) == 3, f"Expected 3 phandles, got {len(phandles)}"


class TestPowerDomainsCellLookup:
    """Test power-domains property with varying #power-domain-cells."""

    def test_resolves_varying_power_domain_cells(self, phandle_cell_tree):
        """Test resolving power-domains with 0 and 1 cell controllers."""
        device = phandle_cell_tree["/device@400000"]
        assert device is not None

        prop = device["power-domains"]
        assert prop is not None

        phandles = prop.resolve_phandles()
        assert len(phandles) == 2, f"Expected 2 phandles, got {len(phandles)}"


class TestMboxesCellLookup:
    """Test mboxes property with varying #mbox-cells."""

    def test_resolves_varying_mbox_cells(self, phandle_cell_tree):
        """Test resolving mboxes with 1 and 2 cell controllers."""
        device = phandle_cell_tree["/device@500000"]
        assert device is not None

        prop = device["mboxes"]
        assert prop is not None

        phandles = prop.resolve_phandles()
        assert len(phandles) == 2, f"Expected 2 phandles, got {len(phandles)}"


class TestDmasCellLookup:
    """Test dmas property with varying #dma-cells."""

    def test_resolves_varying_dma_cells(self, phandle_cell_tree):
        """Test resolving dmas with 1 and 2 cell controllers."""
        device = phandle_cell_tree["/device@600000"]
        assert device is not None

        prop = device["dmas"]
        assert prop is not None

        phandles = prop.resolve_phandles()
        assert len(phandles) == 2, f"Expected 2 phandles, got {len(phandles)}"


class TestPhysCellLookup:
    """Test phys property with varying #phy-cells."""

    def test_resolves_varying_phy_cells(self, phandle_cell_tree):
        """Test resolving phys with 1 and 3 cell controllers."""
        device = phandle_cell_tree["/device@700000"]
        assert device is not None

        prop = device["phys"]
        assert prop is not None

        phandles = prop.resolve_phandles()
        assert len(phandles) == 2, f"Expected 2 phandles, got {len(phandles)}"


class TestThermalSensorsCellLookup:
    """Test thermal-sensors property with varying #thermal-sensor-cells."""

    def test_resolves_0cell_thermal_sensor(self, phandle_cell_tree):
        """Test resolving thermal-sensors with #thermal-sensor-cells = <0>."""
        zone = phandle_cell_tree["/thermal-zones/cpu-thermal"]
        assert zone is not None

        prop = zone["thermal-sensors"]
        assert prop is not None

        phandles = prop.resolve_phandles()
        assert len(phandles) == 1, f"Expected 1 phandle, got {len(phandles)}"

    def test_resolves_1cell_thermal_sensor(self, phandle_cell_tree):
        """Test resolving thermal-sensors with #thermal-sensor-cells = <1>."""
        zone = phandle_cell_tree["/thermal-zones/gpu-thermal"]
        assert zone is not None

        prop = zone["thermal-sensors"]
        assert prop is not None

        phandles = prop.resolve_phandles()
        assert len(phandles) == 1, f"Expected 1 phandle, got {len(phandles)}"


class TestExistingSystemTopProperties:
    """Test phandle resolution against existing system-top.dts properties."""

    @pytest.fixture
    def system_top_tree(self, tmp_path):
        """Load the system-top.dts test file."""
        system_top = SELFTEST_DIR / "system-top.dts"
        if not system_top.exists():
            pytest.skip(f"system-top.dts not found")

        # Check if libfdt is available
        libfdt_available = False
        try:
            import libfdt
            libfdt_available = True
        except ImportError:
            pass

        sdt = LopperSDT(str(system_top))
        sdt.dryrun = False
        sdt.verbose = 0
        sdt.werror = False
        sdt.output_file = str(tmp_path / "output.dts")
        sdt.cleanup_flag = True
        sdt.save_temps = False
        sdt.enhanced = True
        sdt.outdir = str(tmp_path)
        sdt.libfdt = libfdt_available

        sdt.setup(str(system_top), [], "", True, libfdt=libfdt_available)

        return sdt.tree

    def test_power_domains_in_system_top(self, system_top_tree):
        """Test power-domains resolution in system-top.dts."""
        # Find a node with power-domains property
        for node in system_top_tree:
            prop = node.props("power-domains")
            if prop:
                phandles = prop[0].resolve_phandles()
                # Should resolve without error and find at least one phandle
                assert len(phandles) >= 1
                return

        pytest.skip("No power-domains property found in system-top.dts")

    def test_resets_in_system_top(self, system_top_tree):
        """Test resets resolution in system-top.dts."""
        for node in system_top_tree:
            prop = node.props("resets")
            if prop:
                phandles = prop[0].resolve_phandles()
                assert len(phandles) >= 1
                return

        pytest.skip("No resets property found in system-top.dts")

    def test_mboxes_in_system_top(self, system_top_tree):
        """Test mboxes resolution in system-top.dts."""
        for node in system_top_tree:
            prop = node.props("mboxes")
            if prop:
                phandles = prop[0].resolve_phandles()
                assert len(phandles) >= 1
                return

        pytest.skip("No mboxes property found in system-top.dts")

    def test_dmas_in_system_top(self, system_top_tree):
        """Test dmas resolution in system-top.dts."""
        for node in system_top_tree:
            prop = node.props("dmas")
            if prop:
                phandles = prop[0].resolve_phandles()
                assert len(phandles) >= 1
                return

        pytest.skip("No dmas property found in system-top.dts")

    def test_phys_in_system_top(self, system_top_tree):
        """Test phys resolution in system-top.dts."""
        for node in system_top_tree:
            prop = node.props("phys")
            if prop:
                phandles = prop[0].resolve_phandles()
                assert len(phandles) >= 1
                return

        pytest.skip("No phys property found in system-top.dts")


class TestNodeConditionalDescriptions:
    """A property whose layout depends on the node carrying it.

    gpios is 'phandle:#gpio-cells' when a device points at a controller,
    and <id flags ..> sized by the parent when it is a gpio hog inside
    the controller. See Documentation/devicetree/bindings/gpio/gpio.txt.
    """

    @staticmethod
    def _hog_tree(gpio_cells):
        """Controller with a hog child, and a consumer with a real reference."""
        tree = LopperTree()
        tree.add(LopperNode(-1, "/"))

        tree.add(LopperNode(-1, "/gpio@20"))
        controller = tree["/gpio@20"]
        controller["#gpio-cells"] = [gpio_cells]
        controller["gpio-controller"] = [""]

        tree.add(LopperNode(-1, "/gpio@20/line-hog"))
        hog = tree["/gpio@20/line-hog"]
        hog["gpio-hog"] = [""]
        hog["gpios"] = [0] * gpio_cells

        tree.resolve()
        return tree

    def test_gpios_entry_carries_a_hog_variant(self):
        """The table describes the hog case, not just the common one."""
        props = lopper_base.phandle_possible_properties()
        entry = props["gpios"]

        assert "#gpio-cells" in entry[0]
        assert len(entry) >= 3, "gpios has no conditional variants"

        conditions = [v.get("node-has") for v in entry[2]]
        assert "gpio-hog" in conditions

    def test_description_selected_by_node(self):
        """The node decides which description applies."""
        entry = ["phandle:#gpio-cells", 0,
                 [{"node-has": "gpio-hog", "description": "^:#gpio-cells"}]]

        tree = self._hog_tree(2)
        hog = tree["/gpio@20/line-hog"]
        controller = tree["/gpio@20"]

        assert lopper_base.phandle_description(entry, hog) == "^:#gpio-cells"
        assert lopper_base.phandle_description(entry, controller) == \
            "phandle:#gpio-cells"
        # no node to test against, so the entry's own description stands
        assert lopper_base.phandle_description(entry) == "phandle:#gpio-cells"

    @pytest.mark.parametrize("gpio_cells", [1, 2, 3])
    def test_hog_cells_come_from_the_parent(self, gpio_cells):
        """Cell count is the parent's #gpio-cells, and none of it is a phandle."""
        tree = self._hog_tree(gpio_cells)
        prop = tree["/gpio@20/line-hog"].__props__["gpios"]

        assert prop.phandle_map(tag_invalid=True) == [[0] * gpio_cells]
        assert prop.resolve_phandles(tag_invalid=True) == []

    def test_hog_is_not_an_invalid_phandle(self):
        """A valid hog must not be reported, but a dangling one still is."""
        import lopper.audit

        tree = self._hog_tree(2)
        tree.add(LopperNode(-1, "/consumer"))
        # 0xffffffff is what dtc leaves behind for an unresolved <&label>
        tree["/consumer"]["gpios"] = [0xFFFFFFFF, 1, 0]
        tree.resolve()

        reported = lopper.audit.check_invalid_phandles(
            tree, warn_only_modified=False)
        paths = [path for path, _prop in reported]

        assert "/gpio@20/line-hog" not in paths
        assert "/consumer" in paths
