#!/usr/bin/env python3

#/*
# * Copyright (c) 2020 Xilinx Inc. All rights reserved.
# *
# * Author:
# *       Bruce Ashfield <bruce.ashfield@xilinx.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

import requests
import getopt
import sys
import os
import json

# a simple script to replace calls like this:
# curl http://127.0.0.1:5000/domains | python3 -c 'import json,sys;print( json.load(sys.stdin))'

VERSION="0.1-alpha"

def usage():
    prog = os.path.basename(sys.argv[0])
    print('Usage: %s [OPTION] url [<output file>]...' % prog)
    print('  -v, --verbose       enable verbose/debug processing (specify more than once for more verbosity)')
    print('  -j, --json          print unprocessed json response' )
    print('    , --version       output the version and exit')
    print('')
    print(' This is a simple script to replace curl and python on the command line' )
    print('   i.e.: curl http://127.0.0.1:5000/domains | python3 -c \'import json,sys;print( json.load(sys.stdin))\'')
    print('')


def main():
    global verbose
    global json
    global url

    url = None
    verbose = 0
    json = False
    try:
        opts, args = getopt.getopt(sys.argv[1:], "vj", [ "version", "json"])
    except getopt.GetoptError as err:
        print('%s' % str(err))
        usage()
        sys.exit(2)

    if opts == [] and args == []:
        usage()
        sys.exit(1)

    for o, a in opts:
        if o in ('-v', "--verbose"):
            verbose = verbose + 1
        elif o in ('--json'):
            json=True
        elif o in ('--version'):
            print( "%s" % VERSION )
            sys.exit(0)
        else:
            assert False, "unhandled option"

    # any args should be <url> <output file>
    module_name = ""
    module_args= []
    module_args_found = False
    for idx, item in enumerate(args):
        # validate that the system device tree file exists
        if idx == 0:
            url = item
        else:
            if item == "--":
                module_args_found = True

            # the last input is the output file. It can't already exist, unless
            # --force was passed
            if not module_args_found:
                if idx == 1:
                    if output:
                        print( "Error: output was already provided via -o\n")
                        usage()
                        sys.exit(1)
                    else:
                        output = item
                        output_file = Path(output)
                        if output_file.exists():
                            if not force:
                                print( "Error: output file %s exists, and -f was not passed" % output )
                                sys.exit(1)
            else:
                # module arguments
                if not item == "--":
                    if not module_name:
                        module_name = item
                    else:
                        module_args.append( item )

    if not url:
        print( "[ERROR]: no url was supplied\n" )
        usage()
        sys.exit(1)

    if verbose:
        print( "[INFO]: url: %s" % url )

    r = requests.get( url )
    if not r:
        print( r )
    else:
        if json:
            print( r.text )
        else:
            print( r.json() )


if __name__ == "__main__":
    main()

