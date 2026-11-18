# SPDX-License-Identifier: BSD-3-Clause
"""
lopper on a core install, without the "yaml" extra.

`pip install lopper` brings humanfriendly and configparser only; yaml
support is an extra. lopper/__init__.py already treats a missing yaml stack
as support disabled, but tree.py imports the schema package unconditionally,
so the schema modules must not need a yaml parser at import time either, or
a core install cannot even print its help.

The suite runs with the extras installed, so each test starts a child
interpreter that cannot import yaml or ruamel.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# Make the yaml parsers unimportable, as they are on a core install.
_HIDE_YAML = """
import sys
for name in ('yaml', 'ruamel', 'ruamel.yaml'):
    sys.modules[name] = None
"""

_RUN_LOPPER = """
sys.argv = ['lopper'] + sys.argv[1:]
import runpy
runpy.run_module('lopper', run_name='__main__', alter_sys=True)
"""


def _python_without_yaml(code, *args, cwd):
    env = os.environ.copy()
    env['PYTHONPATH'] = str(REPO_ROOT)
    return subprocess.run(
        [sys.executable, '-c', code, *args],
        cwd=cwd, env=env, capture_output=True, text=True, timeout=120)


def _lopper_without_yaml(*args, cwd):
    return _python_without_yaml(_HIDE_YAML + _RUN_LOPPER, *args, cwd=cwd)


def test_help(tmp_path):
    r = _lopper_without_yaml('--help', cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert 'usage' in r.stdout.lower()


@pytest.mark.skipif(shutil.which('dtc') is None, reason='needs dtc')
def test_dts_to_dtb(tmp_path):
    (tmp_path / 'in.dts').write_text(
        '/dts-v1/;\n'
        '/ {\n'
        '\tcompatible = "test,board";\n'
        '\t#address-cells = <1>;\n'
        '\t#size-cells = <1>;\n'
        '\tmemory@0 { device_type = "memory"; reg = <0x0 0x1000>; };\n'
        '};\n')
    r = _lopper_without_yaml('-f', 'in.dts', 'out.dtb', cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    out = subprocess.run(['dtc', '-I', 'dtb', '-O', 'dts', 'out.dtb'],
                         cwd=tmp_path, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert 'reg = <0x00 0x1000>;' in out.stdout


def test_schema_file_needs_yaml_extra(tmp_path):
    # A schema named on the command line cannot be honoured without a
    # parser; say which extra provides one.
    (tmp_path / 'schema.yaml').write_text('overrides:\n  properties: {}\n')
    code = _HIDE_YAML + (
        "import lopper.schema\n"
        "lopper.schema.load_external_schema('schema.yaml')\n")
    r = _python_without_yaml(code, cwd=tmp_path)
    assert r.returncode != 0
    assert 'pip install lopper[yaml]' in r.stdout + r.stderr
