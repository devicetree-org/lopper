# Copyright (c) 2021 Nordic Semiconductor ASA
#
# SPDX-License-Identifier: BSD-3-Clause

import os
from pathlib import Path

import setuptools

here = Path(__file__).parent

with open(here / 'README.md', 'r') as f:
    long_description = f.read()

with open(here / 'lopper' / 'VERSION', 'r') as f:
    # This is option 3 in:
    # https://packaging.python.org/guides/single-sourcing-package-version/
    version = f.read().strip()

setuptools.setup(
    name='lopper',
    version=version,
    author='Bruce Ashfield',
    author_email='bruce.ashfield@gmail.com',
    description='A Device Tree Manipulation Framework',
    license='BSD',
    long_description=long_description,
    long_description_content_type='text/plain',
    url='https://github.com/devicetree-org/lopper',
    classifiers=[
        'Programming Language :: Python :: 3',
        'License :: OSI Approved :: BSD License',
        'Operating System :: POSIX :: Linux',
    ],
    packages=setuptools.find_packages(include=('lopper', 'lopper.*')),
    python_requires='>=3.10',
    include_package_data=True,
    # yaml is a hard requirement, not an extra. Lopper learns a schema from the
    # tree to type properties, and without it some types cannot be recovered at
    # all -- a four-byte value that is a string is indistinguishable from a
    # uint32 by its bytes, so a run with no schema is not merely reduced, it is
    # wrong in ways that cannot be fixed downstream. A core install must carry
    # the parser. (PyYAML, not "pyaml" -- the latter is a different package and
    # does not provide `import yaml`.)
    install_requires=[ "humanfriendly","configparser",
                       "PyYAML","ruamel.yaml","anytree","packaging" ],
    extras_require={ "server": ["flask>=1.1.2","flask_restful>=0.3.8","pandas"],
                     # Kept as a no-op alias so `pip install lopper[yaml]` still
                     # resolves; the packages it named are now always installed.
                     "yaml": [],
                     "dt" : ["devicetree"],
                     "pcpp" : ["pcpp"],
                     # Device tree binding schemas, for assists that prune a
                     # node down to the properties its binding allows. Supplies
                     # the core and generic schemas that device bindings $ref
                     # (types.yaml, graph.yaml, dt-core.yaml and friends) from
                     # their upstream home, under a permissive license, so they
                     # do not have to be carried here.
                     "bindings" : ["dtschema>=2026.9"],
                    },
    namespace_packages=[ ],
    entry_points={'console_scripts': ('lopper = lopper.__main__:main',)},
)
