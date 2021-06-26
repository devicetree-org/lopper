#/*
# * Copyright (c) 2020 Xilinx Inc. All rights reserved.
# *
# * Author:
# *       Bruce Ashfield <bruce.ashfield@xilinx.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

from flask import Flask
from flask import jsonify
from flask import send_file
from flask_restful import Resource, Api, reqparse
import pandas as pd
import ast
import tempfile
import json

from collections import OrderedDict
import lopper

app = Flask(__name__)
api = Api(app)

sdt = None

class Domains(Resource):

    def get(self):
        try:
            domains = sdt.tree.nodes( "/domains/[^/]*$" )
        except:
            domains = []

        domain_names = ""
        if domains:
            # this just dumps the domain names comma separated. We loop
            # up to the last element, adding the ",". We then add the
            # last element after the loop, no "," .. we could also just
            # remove the trailing "," after the loop, but the list slicing
            # seems a bit faster.
            for d in domains[:-1]:
                domain_names += d.abs_path + ","
            domain_names = domain_names + domains[-1].abs_path

        return domain_names, 200

class Tree(Resource):
    def get(self):
        fpp = tempfile.NamedTemporaryFile( delete=True )

        if sdt:
            lp = lopper.LopperTreePrinter( sdt.tree.fdt, False, fpp.name )
            lp.exec()

            with open(fpp.name, 'r') as f:
                filecontents = f.read()

            # json = json.dumps(filecontents, ensure_ascii=False)
            return filecontents, 200
        else:
            return "", 204

class Nodes(Resource):
    def get(self):
        parser = reqparse.RequestParser()

        parser.add_argument('path', required=True)
        parser.add_argument('details', required=False)
        args = parser.parse_args()

        try:
            details = args['details']
            if details == "True":
                details = True
            else:
                details = False
        except:
            details = False

        node_data = OrderedDict()

        node_list = sdt.tree.nodes( args['path'] )

        if not details:
            for n in node_list:
                node_data[n.abs_path] = None
        else:
            for n in node_list:
                prop_dict = OrderedDict()
                for p in n.__props__:
                    prop_dict[p] = n.__props__[p].string_val

                node_data[n.abs_path] = prop_dict

        return node_data, 200

api.add_resource(Domains, '/domains')  # '/domains' is an entry point
api.add_resource(Tree, '/tree')  # '/tree' is an entry point
api.add_resource(Nodes, '/nodes')  # '/tree' is an entry point
