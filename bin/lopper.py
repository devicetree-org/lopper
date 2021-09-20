#!/usr/bin/env python3

import sys
import subprocess

subprocess.run([sys.executable, '-m', 'lopper'] + sys.argv[1:])
