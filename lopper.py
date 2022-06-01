#!/usr/bin/env python3

import sys
import subprocess
import os
from os.path import dirname, abspath, join

THIS_DIR = dirname(__file__)

my_env = os.environ.copy()
my_env["PYTHONPATH"] = THIS_DIR

subprocess.run([sys.executable, '-m', 'lopper'] + sys.argv[1:], env=my_env )
