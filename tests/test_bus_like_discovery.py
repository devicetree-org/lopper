"""
Tests for bus-like node discovery in the device inventory.

A bus child can act as a bus without declaring "simple-bus". The glue layer
wrapper is the common shape: a vendor node owning the clocks, resets and
power-domains for an IP block, with the generic controller as its addressed
child -- e.g. "xlnx,versal2-mmi-dwc3" wrapping an "snps,dwc3". Its compatible
is correct and should not become simple-bus; it has a driver and real device
properties.

Device tree already expresses "I translate addresses for my children" as
'ranges' plus '#address-cells', so discovery uses that rather than a
heuristic. Without it the wrapper is skipped for having no unit address, and
the addressed child underneath it is never reached, because only direct bus
children are scanned.

The negative cases matter as much as the positive one. Reg-less bus children
are common -- fixed clocks, mailboxes, capture wrappers -- and none of them
should become discoverable as a side effect.

Copyright (C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.

SPDX-License-Identifier: BSD-3-Clause

Author:
    Bruce Ashfield <bruce.ashfield@amd.com>
"""

import os
import pytest

from lopper import LopperSDT
from lopper.assists.sdt_devices import SDTDevices


SDT = """/dts-v1/;
/ {
    #address-cells = <2>;
    #size-cells = <2>;
    compatible = "test";

    amba: amba {
        compatible = "simple-bus";
        #address-cells = <2>;
        #size-cells = <2>;
        ranges;

        /* baseline: an ordinary addressed device */
        plain: serial@ff000000 {
            compatible = "test,uart";
            reg = <0x0 0xff000000 0x0 0x1000>;
        };

        /*
         * The glue layer wrapper. No unit address, but it declares that it
         * translates for its children, so it is a bus whatever its
         * compatible says.
         */
        wrapper: mmi-usb {
            compatible = "test,vendor-dwc3";
            ranges;
            #address-cells = <2>;
            #size-cells = <2>;
            clocks = <&refclk>;

            inner: usb@edec0000 {
                compatible = "snps,dwc3";
                reg = <0x0 0xedec0000 0x0 0x10000>;
            };

            /* a clock underneath the wrapper is still a clock */
            wrapclk: clk@edec8000 {
                compatible = "fixed-clock";
                #clock-cells = <0>;
                reg = <0x0 0xedec8000 0x0 0x100>;
            };
        };

        /* reg-less, but NOT a bus: no ranges, no #address-cells */
        refclk: misc_clk_0 {
            compatible = "fixed-clock";
            #clock-cells = <0>;
        };

        /* reg-less composite wired by an endpoint graph, not by address */
        vcap: vcap_ss_00 {
            compatible = "xlnx,video";

            ports {
                #address-cells = <1>;
                #size-cells = <0>;
                port@0 {
                    reg = <0>;
                    vcap_ep: endpoint { };
                };
            };
        };
    };
};
"""


@pytest.fixture
def devices(tmp_path):
    """Run bus discovery over the fixture SDT and return the device list."""
    p = os.path.join(str(tmp_path), "buslike.dts")
    with open(p, "w") as f:
        f.write(SDT)

    sdt = LopperSDT(p)
    sdt.dryrun = False
    sdt.verbose = 0
    sdt.werror = False
    sdt.output_file = os.path.join(str(tmp_path), "out.dts")
    sdt.cleanup_flag = True
    sdt.save_temps = False
    sdt.enhanced = True
    sdt.outdir = str(tmp_path)
    sdt.setup(sdt.dts, [], "", True, libfdt=True)

    return [d["dev"] for d in SDTDevices(sdt).discover_bus_devices()]


class TestBusLikeDiscovery:
    """A node with ranges + #address-cells is scanned as a bus."""

    def test_child_of_bus_like_wrapper_is_discovered(self, devices):
        """The addressed child under a reg-less wrapper is reachable.

        This is the case that was missing: the wrapper is skipped for having
        no unit address, and discovery only walks direct bus children, so
        nothing ever reached the controller underneath it.
        """
        assert "usb@edec0000" in devices, \
            "the addressed child of a bus-like wrapper was not discovered"

    def test_wrapper_itself_is_not_a_device(self, devices):
        """The wrapper is a bus, not an assignable device.

        It is deliberately not added. domain_access refs the parent chain of
        anything claimed, so the wrapper survives once its child is claimed,
        without becoming claimable itself.
        """
        assert "mmi-usb" not in devices, \
            "the bus-like wrapper was added as a device; it is a bus"

    def test_ordinary_device_still_discovered(self, devices):
        """Baseline: normal addressed bus children are unaffected."""
        assert "serial@ff000000" in devices


class TestBusLikeExclusions:
    """Reg-less bus children must not become discoverable as a side effect."""

    def test_regless_without_ranges_still_excluded(self, devices):
        """A fixed clock has no ranges, so it is not a bus and not scanned."""
        assert "misc_clk_0" not in devices, \
            "a reg-less node without ranges was treated as a bus"

    def test_endpoint_graph_composite_still_excluded(self, devices):
        """A capture wrapper is reg-less but declares no translation.

        It has addressed descendants (port@0), so a rule keyed on 'has an
        addressed child' would wrongly pull it in. Keying on ranges plus
        #address-cells does not.
        """
        assert "vcap_ss_00" not in devices, \
            "an endpoint-graph composite was treated as a bus"

    def test_structural_nodes_never_discovered(self, devices):
        """port@0 is addressed but carries no compatible."""
        assert "port@0" not in devices
        assert "ports" not in devices

    def test_clock_under_wrapper_still_excluded(self, devices):
        """Exclusions apply underneath a wrapper exactly as above it.

        Descent does not bypass _is_actual_device(): a fixed-clock inside the
        wrapper is excluded for the same reason it would be at bus level.
        """
        assert "clk@edec8000" not in devices, \
            "a clock under a bus-like wrapper escaped the clock exclusion"

    def test_all_discovered_devices_are_addressed(self, devices):
        """The existing invariant holds: everything discovered has a unit address."""
        for d in devices:
            assert "@" in d, f"'{d}' has no unit address"
