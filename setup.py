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
    description='A devicetree pruner',
    license='BSD',
    long_description=long_description,
    long_description_content_type='text/plain',
    url='https://github.com/devicetree-org/lopper',
    classifiers=[
        'Programming Language :: Python :: 3',
        'License :: OSI Approved :: BSD License',
        'Operating System :: POSIX :: Linux',
    ],
    packages=setuptools.find_packages(include=('lopper',)),
    python_requires='>=3.5',
    include_package_data=True,
    install_requires=[ "humanfriendly","configparser" ],
    extras_require={ "server": ["flask>=1.1.2","flask_restful>=0.3.8","pandas"],
                     "yaml": ["pyaml","ruamel.yaml","anytree"],
                     "dt" : ["devicetree"],
                     "pcpp" : ["pcpp"],
                    },
    namespace_packages=[ ],
    entry_points={'console_scripts': ('lopper = lopper.__main__:main',)},
)
