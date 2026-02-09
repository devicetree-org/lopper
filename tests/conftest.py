"""
Pytest configuration and shared fixtures for Lopper tests.
"""

import pytest
import tempfile
import shutil
from pathlib import Path

from lopper import Lopper, LopperSDT
from lopper.tree import LopperTree

# Import the device tree setup function from lopper_sanity
# This ensures we use the exact same test data
import lopper_sanity


@pytest.fixture(scope="session")
def test_outdir(tmp_path_factory):
    """
    Create a temporary output directory for all tests in the session.

    This directory persists for the entire test session and is cleaned up
    automatically by pytest afterward.
    """
    outdir = tmp_path_factory.mktemp("lopper_test")
    yield str(outdir)
    # Cleanup is automatic with tmp_path_factory


@pytest.fixture(scope="session")
def system_device_tree(test_outdir):
    """
    Setup a system device tree for testing.

    Uses the same device tree from lopper_sanity.py's setup_device_tree()
    to ensure consistency with existing tests.
    """
    # Use the actual device tree from lopper_sanity.py
    dt_path = lopper_sanity.setup_device_tree(test_outdir)
    return dt_path


@pytest.fixture(scope="session")
def compiled_fdt(system_device_tree, test_outdir):
    """
    Compile the system device tree to FDT format.

    Returns the compiled FDT object ready for testing.
    Uses the same compilation logic as lopper_sanity.py's setup_fdt()
    """
    # Check if libfdt is available
    libfdt_available = False
    try:
        import libfdt
        libfdt_available = True
    except ImportError:
        pass

    try:
        # Compile the device tree - dt_compile returns full path WITH .dtb extension
        dt, _ = Lopper.dt_compile(system_device_tree, "", "", True, test_outdir)

        # Use libfdt if available, otherwise use the DT path
        if libfdt_available:
            # dt already includes .dtb extension, use it directly
            fdt = Lopper.dt_to_fdt(dt)
        else:
            # Without libfdt, use the path returned by dt_compile
            fdt = dt

        return fdt
    except Exception as e:
        pytest.fail(f"Failed to compile device tree: {e}")


@pytest.fixture
def lopper_tree(compiled_fdt):
    """
    Create a LopperTree from the compiled FDT.

    This is a function-scoped fixture, so each test gets a fresh tree.
    """
    tree = LopperTree()
    tree.load(Lopper.export(compiled_fdt))
    return tree


@pytest.fixture(scope="session")
def yaml_test_file(test_outdir):
    """
    Setup a YAML test file for testing.

    Uses the same YAML content from lopper_sanity.py's setup_yaml()
    to ensure consistency with existing tests.
    """
    yaml_path = lopper_sanity.setup_yaml(test_outdir)
    return yaml_path


@pytest.fixture
def lopper_sdt(system_device_tree, test_outdir):
    """
    Create a LopperSDT instance for FDT testing.

    This is a function-scoped fixture, so each test gets a fresh LopperSDT.
    Configured the same way as in lopper_sanity.py's fdt_sanity_test.
    """
    # Check if libfdt is available
    libfdt_available = False
    try:
        import libfdt
        libfdt_available = True
    except ImportError:
        pass

    sdt = LopperSDT(system_device_tree)
    sdt.dryrun = False
    sdt.verbose = 0
    sdt.werror = False
    sdt.output_file = test_outdir + "/fdt-output.dts"
    sdt.cleanup_flag = True
    sdt.save_temps = False
    sdt.enhanced = True
    sdt.outdir = test_outdir
    sdt.libfdt = libfdt_available

    # Setup the device tree
    sdt.setup(system_device_tree, [], "", True, libfdt=libfdt_available)

    return sdt
