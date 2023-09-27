#/*
# * Copyright (c) 2021 Xilinx Inc. All rights reserved.
# *
# * Author:
# *       Bruce Ashfield <bruce.ashfield@xilinx.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

import re
import os
import shutil
import subprocess
from lopper.fmt import LopperFmt
from string import printable
from pathlib import Path
from pathlib import PurePath

class lopper_base:
    """Class representing the common device tree front / backend interface

    The functions that make up this class represent the interface between the
    core of processing and the front/back end for import and export of source
    files.

    This class implements:
       - dt_preprocess:
       - property_value_decode:
       - property_type_guess:
       - property_convert
       - phandle_possible_properties
       - phandle_safe_name
       - encode_byte_array
       - encode_byte_array_from_strings
       - string_test
       - input_file_type
       - _comment_replacer
       - _comment_translate
       - _label_replacer
       - _label_translate

    This class has the base function for (and subclasses must implement):
       - export
       - sync
       - node_properties_as_dict
       - dt_compile

    Attributes:
       - phandle_possible_prop_dict: class variable holding the phandle
                                     locations in properties

    """

    ### --- class variables
    phandle_possible_prop_dict = {}

    ### --- base methods
    def dt_preprocess( dts_file, includes, outdir="./", verbose=0 ):
        """Compile a dts file to a dtb

        This routine takes a dts input file, include search path and then
        uses standard tools (cpp, etc) to expand references.

        Environment variables can be used tweak the execution of the various
        tools and stages:

           LOPPER_CPP: set if a different cpp than the standard one should
                       be used, or if cpp is not on the path
           LOPPER_PPFLAGS: flags to be used when calling cpp

        Args:
           dts_file (string): path to the dts file to be preprocessed
           includes (list): list of include directories (translated into -i <foo>
                            for cpp calls)
           outdir (string): directory to place all output and temporary files
           verbose (bool,optional): verbosity level

        Returns:
           string: Name of the preprocessed dts

        """
        # TODO: might need to make 'dts_file' absolute for the cpp call below
        dts_filename = os.path.basename( dts_file )
        dts_filename_noext = os.path.splitext(dts_filename)[0]

        #
        # step 1: preprocess the file with CPP (if available)
        #

        # NOTE: we are putting the .pp file into the same directory as the
        #       system device tree. Without doing this, dtc cannot resolve
        #       labels from include files, and will throw an error. If we get
        #       into a mode where the system device tree's directory is not
        #       writeable, then we'll have to either copy everything or look
        #       into why dtc can't handle the split directories and include
        #       files.

        # if outdir is left as the default (current dir), then we can respect
        # the dts directory. Otherwise, we need to follow where outdir has been
        # pointed. This may trigger the issue mentioned in the prvious comment,
        # but we'll cross that bridge when we get to it
        dts_dirname = outdir
        if outdir == "./":
            dts_file_dir = os.path.dirname( dts_file )
            if dts_file_dir:
                dts_dirname = dts_file_dir
        preprocessed_name = "{0}/{1}.pp".format(dts_dirname,dts_filename)

        includes += dts_dirname
        includes += " "
        includes += os.getcwd()

        # try pcpp first
        ppargs = (os.environ.get('LOPPER_CPP') or shutil.which("pcpp") or "").split()
        if ppargs:
            ppargs += "--passthru-comments".split()
        else:
            ppargs = (os.environ.get('LOPPER_CPP') or shutil.which("cpp") or "").split()
            # Note: might drop the -I include later
            ppargs += "-nostdinc -I include -undef -x assembler-with-cpp ".split()

        ppargs += (os.environ.get('LOPPER_PPFLAGS') or "").split()
        for i in includes.split():
            ppargs.append("-I{0}".format(i))
        ppargs += ["-o", preprocessed_name, dts_file]
        if verbose:
            print( "[INFO]: preprocessing dts_file: %s" % ppargs )

        result = subprocess.run( ppargs, check = True )
        if result.returncode != 0:
            print( "[ERROR]: unable to preprocess dts file: %s" % ppargs )
            print( "\n%s" % textwrap.indent(result.stderr.decode(), '         ') )
            sys.exit(result.returncode)

        return preprocessed_name

    @staticmethod
    def dt_compile( dts_file, i_files ="", includes="", force_overwrite=False, outdir="./",
                    save_temps=False, verbose=0, enhanced = True ):
        return None

    @staticmethod
    def property_value_decode( prop, poffset, ftype=LopperFmt.SIMPLE, encode=LopperFmt.UNKNOWN, verbose=0 ):
        """Decodes a property

        Decode a property into a common data type (string, integer, list of
        strings, etc).

        This is a robust wrapper around the decode facilities provided via
        libfdt. This routine tries multiple encode formats and uses
        heuristics to determine the best format for the decoded property.

        The format type (ftype) and encod arguments can be used to help
        decode properly when the type of a property is known.

        The format and encoding options are in the following enum type:

           class LopperFmt(Enum):
              SIMPLE = 1 (format)
              COMPOUND = 2 (format)
              HEX = 3 (encoding)
              DEC = 4 (encoding)
              STRING = 5 (encoding)
              MULTI_STRING = 5 (encoding)

        Args:
           prop (libfdt or byte property): property to decode
           poffset (int): offset of the property in the node (unused)
           ftype (LopperFmt,optional): format hint for the property. default is SIMPLE
           encode (LopperFmt,optional): encoding hint. default is DEC
           verbose (int,optional): verbosity level, default is 0

        Returns:
           (string): if SIMPLE. The property as a string
           (list): if COMPOUND. The property as a list of strings / values

        """
        if verbose > 3:
            print( "[DBG+]: decode start: %s %s" % (prop,ftype))

        # Note: these could also be nested.
        if ftype == LopperFmt.SIMPLE:
            encode_calculated = lopper_base.property_type_guess( prop )

            val = ""
            if repr(encode_calculated) == repr(LopperFmt.STRING) or \
               repr(encode_calculated) == repr(LopperFmt.EMPTY ):
                if not val:
                    try:
                        val = prop.as_str()
                        decode_msg = "(string): {0}".format(val)
                    except:
                        pass

                if not val:
                    try:
                        # this is getting us some false positives on multi-string. Need
                        # a better test
                        val = prop[:-1].decode('utf-8').split('\x00')
                        #val = ""
                        decode_msg = "(multi-string): {0}".format(val)
                    except:
                        pass
            else:
                val = ""
                decode_msg = ""
                try:
                    val = prop.as_uint32()
                    decode_msg = "(uint32): {0}".format(val)
                except:
                    pass
                if not val and val != 0:
                    try:
                        val = prop.as_uint64()
                        decode_msg = "(uint64): {0}".format(val)
                    except:
                        pass

            if not val and val != 0:
                decode_msg = "** unable to decode value **"
        else:
            # compound format
            decode_msg = ""
            val = ['']
            encode_calculated = lopper_base.property_type_guess( prop )

            if repr(encode_calculated) == repr(LopperFmt.EMPTY):
                return val

            first_byte = prop[0]
            last_byte = prop[-1]

            # TODO: we shouldn't need these repr() wrappers around the enums, but yet
            #       it doesn't seem to work on the calculated variable without them
            if repr(encode_calculated) == repr(LopperFmt.STRING):
                try:
                    val = prop[:-1].decode('utf-8').split('\x00')
                    decode_msg = "(multi-string): {0}".format(val)
                except:
                    encode_calculated = encode

            if repr(encode_calculated) == repr(LopperFmt.UINT32) or \
               repr(encode_calculated) == repr(LopperFmt.UINT64) or \
               repr(encode_calculated) == repr(LopperFmt.UINT8) :
                try:
                    decode_msg = "(multi-number)"
                    num_bits = len(prop)
                    if encode_calculated == LopperFmt.UINT8:
                        binary_data = True
                        start_index = 0
                        end_index = 1
                        short_int_size = 1
                        num_nums = num_bits
                    else:
                        binary_data = False
                        num_nums = num_bits // 4
                        start_index = 0
                        end_index = 4
                        short_int_size = 4

                    val = []
                    while end_index <= (num_nums * short_int_size):
                        short_int = prop[start_index:end_index]
                        if repr(encode) == repr(LopperFmt.HEX):
                            converted_int = hex(int.from_bytes(short_int,'big',signed=False))
                        else:
                            converted_int = int.from_bytes(short_int,'big',signed=False)

                        start_index = start_index + short_int_size
                        end_index = end_index + short_int_size
                        val.append(converted_int)

                except Exception as e:
                    decode_msg = "** unable to decode value **"


        if verbose > 3:
            print( "[DBG+]: decoding prop: \"%s\" (%s) [%s] --> %s" % (prop, poffset, prop, decode_msg ) )

        return val

    @staticmethod
    def property_type_guess( prop ):
        """utility routine to guess the type of a property

        Often the type of a property is not know, in particular if there isn't
        access to markers via a support library.

        This routine looks at the data of a libfdt or byte property and returns
        the best guess for the type. The logic behind the guesses is documented
        in the code itself

        Args:
           prop (libfdt or byte property): the property to process

        Returns:
           LopperFmt description of the property. Default is UINT8 (binary)
                       LopperFmt.STRING: string
                       LopperFmt.UINT32 1: uint32
                       LopperFmt.UINT64 2: uint64
                       LopperFmt.UINT8 3: uint8 (binary)
                       LopperFmt.EMPTY 4: empty (just a name)

        """
        type_guess = LopperFmt.UINT8

        if len(prop) == 0:
            return LopperFmt.EMPTY

        first_byte = prop[0]
        last_byte = prop[-1]

        # byte array encoded strings, start with a non '\x00' byte (i.e. a character), so
        # we test on that for a hint. If it is not \x00, then we try it as a string.
        # Note: we may also test on the last byte for a string terminator.
        if first_byte != 0 and len(prop) > 1:
            if last_byte == 0:
                type_guess = LopperFmt.STRING
                try:
                    val = prop[:-1].decode('utf-8').split('\x00')
                    # and a 2nd opinion
                    if not lopper_base.string_test( prop ):
                        # change our mind
                        type_guess = LopperFmt.UINT8

                except Exception as e:
                    # it didn't decode, fall back to numbers ..
                    type_guess = LopperFmt.UINT8
            else:
                type_guess = LopperFmt.UINT8

        if type_guess == LopperFmt.UINT8:
            num_bits = len(prop)
            num_divisible = num_bits % 4
            if num_divisible != 0:
                # If it isn't a string and isn't divisible by a uint32 size, then it
                # is binary formatted data. So we return uint8
                type_guess = LopperFmt.UINT8
            else:
                # we can't easily guess the difference between a uint64 and uint32
                # until we get access to the marker data. So we default to the smaller
                # sized number. We could possibly
                type_guess = LopperFmt.UINT32

        return type_guess


    @staticmethod
    def property_convert( property_string ):
        """utility command to convert a string to a list of property values

        Takes a string formatted in device tree notation, and returns a list
        of property values.

        Formats of the following types will work, and be converted to their
        base types in the returned list.

              <0x1 0x2 0x3>
              <0x1>
              "string value"
              "string value1","string value2"
              10

        Args:
           property_string (string): device tree "style" string

        Returns:
           list: converted property values, empty string if cannot convert
        """
        retval = []
        # if it starts with <, it is a list of numbers
        if re.search( "^<", property_string ):
            property_string = re.sub( "<", "", property_string )
            property_string = re.sub( ">", "", property_string )
            for n in property_string.split():
                base = 10
                if re.search( "0x", n ):
                    base = 16
                try:
                    n_as_int = int(n,base)
                    n = n_as_int
                except Exception as e:
                    print( "[ERROR]: cannot convert element %s to number (%s)" % (n,e) )
                    sys.exit(1)

                retval.append( n )

        else:
            # if it is quoted "" and separated by "," it is a list of numbers
            quoted_regex = re.compile( r"(?P<quote>['\"])(?P<string>.*?)(?<!\\)(?P=quote)")
            strings = quoted_regex.findall( property_string )

            # we get a list of tuples back from the findall ( <type>, <value> ),
            # where 'type' is the type of quotation used
            if len( strings ) > 1:
                for s in strings:
                    sval = s[1]
                    retval.append( sval )
            else:
                # single number or string
                p = property_string
                base = 10
                if re.search( "0x", p ):
                    base = 16
                try:
                    p_as_int = int(p,base)
                    p = p_as_int
                    retval.append (p )
                except Exception as e:
                    # it is a string
                    retval.append( p )

        return retval

    @classmethod
    def phandle_possible_properties(cls):
        """Get the diectionary of properties that can contain phandles

        dictionary of possible properties that can have phandles.
        To do the replacement, we map out the properties so we can locate any
        handles and do replacement on them with symbolic values. This format is
        internal only, and yes, could be the schema for the fields, but for now,
        this is easier.

        Each key (property name) maps to a list of: 'format', 'flag'
        flag is currently unused, and format is the following:

           - field starting with #: is a size value, we'll look it up and add 'x'
             number of fields based on it. If we can't find it, we'll just use '1'
           - phandle: this is the location of a phandle, size is '1'
           - anything else: is just a field we can ignore, size is '1'

        Args:
            None

        Returns:
            The phandle property dictionary
        """
        try:
            # todo: change to class variable access ....
            if cls.phandle_possible_prop_dict:
                return cls.phandle_possible_prop_dict
            else:
                return {
                    "DEFAULT" : [ 'this is the default provided phandle map' ],
                    "address-map" : [ '#ranges-address-cells phandle #ranges-address-cells #ranges-size-cells', 0 ],
                    "secure-address-map" : [ '#address-cells phandle #address-cells #size-cells', 0 ],
                    "interrupt-parent" : [ 'phandle', 0 ],
                    "iommus" : [ 'phandle field' ],
                    "interrupt-map" : [ '#interrupt-cells phandle #interrupt-cells' ],
                    "access" : [ 'phandle flags' ],
                    "cpus" : [ 'phandle mask mode' ],
                    "clocks" : [ 'phandle:#clock-cells:+1' ],
                }
        except:
            return {}

    @staticmethod
    def phandle_safe_name( phandle_name ):
        """Make the passed name safe to use as a phandle label/reference

        Args:
            phandle_name (string): the name to use for a phandle

        Returns:
            The modified phandle safe string
        """

        safe_name = phandle_name.replace( '@', '' )
        safe_name = safe_name.replace( '-', "_" )
        safe_name = safe_name.replace( '/', "_" )

        return safe_name


    @staticmethod
    def encode_byte_array( values, byte_count_hint = 4 ):
        """utility to encode a list of values into a bytearray

        Args:
           values (list): integer (numeric) values to encode
           byte_count_hint (int,optional): how many bytes to use for each entry (1-4)

        Returns:
           byte array: the encoded byte array

        """
        barray = b''
        for i in values:
            byte_count = byte_count_hint
            try:
                barray = barray + i.to_bytes(byte_count,byteorder='big')
            except OverflowError:
                byte_count += 1
        return barray

    @staticmethod
    def encode_byte_array_from_strings( values ):
        """utility to encode a list of strings into a bytearray

        Args:
           values (list): string values to encode

        Returns:
           byte array: the encoded byte array

        """
        barray = b''
        if len(values) > 1:
            for i in values:
                barray = barray + i.encode() + b'\x00'
        else:
            barray = barray + values[0].encode()

        return barray

    @staticmethod
    def string_test( prop, allow_multiline = True, debug = False ):
        """ Check if a property (byte array) is a string

        Args:
           prop: (libfdt or byte property)

        Returns:
           boolean: True if the property looks like a string
        """
        if not len( prop ):
            return False

        if prop[-1] != 0:
            return False

        byte = 0
        while byte < len( prop ):
            bytei = byte
            while byte < len( prop ) and \
                  prop[byte] != 0 and \
                  prop[byte] in printable.encode() and \
                  prop[byte] not in (ord('\r'), ord('\n')):

                byte += 1

            if prop[byte] in (ord('\r'), ord('\n')):
                if allow_multiline:
                    byte += 1
                    continue

            # if we broke walking through the positions, and
            # we aren't on a null (multiple strings) or are
            # where we started, then this isn't a string.
            if prop[byte] != 0 or byte == bytei:
                if byte + 3 < len(prop):
                    if prop[byte:byte+3] == b'\xe2\x80\x9c' or prop[byte:byte+3] == b'\xe2\x80\x9d':
                        byte += 3
                        continue
                else:
                    if byte == bytei and prop[byte] == 0:
                        # null termination on the string
                        byte += 1
                        continue

                return False

            byte += 1

        return True


    ## TODO: find callers, and just make this call directly. This should
    ##       be deleted
    @staticmethod
    def input_file_type(infile):
        """utility to return the "type" of a file, aka the extension

        Args:
           infile (string): path of the file

        Returns:
           string: the extension of the file

        """
        return PurePath(infile).suffix


    @staticmethod
    def export( dt, start_node = "/", verbose = False, strict = False ):
        """export a device tree to a description / nested dictionary

        This routine takes a DT, a start node, and produces a nested dictionary
        that describes the nodes and properties in the tree.

        The dictionary contains a set of internal properties, as well as
        a list of standand properties to the node. Internal properties have
        a __ suffix and __ prefix.

        Child nodes are indexed by their absolute path. So any property that
        starts with "/" and is a dictionary, represents another node in the
        tree.

        In particular:
            - __path__ : is the absolute path fo the node, and is used to lookup
                         the target node
            - __fdt_name__ : is the name of the node and will be written to the
                             fdt name property
            - __fdt_phandle__ : is the phandle for the node

        All other "standard" properties are returned as entries in the dictionary.

        if strict is enabled, structural issues in the input tree will be
        flagged and an error triggered. Currently, this is duplicate nodes, but
        may be extended in the future

        Args:
            dt (dt): device tree object
            start_node (string,optional): the starting node
            verbose (bool,optional): verbosity level
            strict (bool,optional): toggle validity checking

        Returns:
            OrderedDict describing the tree
        """
        pass

    @staticmethod
    def sync( dt, dct, verbose = False ):
        """sync (write) a tree dictionary to a backend file

        This routine takes an input dictionary, and writes the details to
        the passed dt.

        The dictionary contains a set of internal properties, as well as
        a list of standand properties to the node. Internal properties have
        a __ suffix and __ prefix.

        Child nodes are indexed by their absolute path. So any property that
        starts with "/" and is a dictionary, represents another node in the
        tree.

        In particular:
            - __path__ : is the absolute path fo the node, and is used to lookup
                         the target node
            - __fdt_name__ : is the name of the node and will be written to the
                             fdt name property
            - __fdt_phandle__ : is the phandle for the node

        All other non  '/' leading, or '__' leading properties will be written to
        the FDT as node properties.

        Passed nodes will be synced via the node_sync() function, and will
        be created if they don't exist. Existing nodes will have their properties
        deleted if they are not in the corresponding dictionary.

        All of the existing nodes in the FDT are read, if they aren not found
        in the passed dictionary, they will be deleted.

        Args:
            dt (dt): device tree object
            node_in: (dictionary): Node description dictionary
            parent (dictionary,optional): parent node description dictionary
            verbose (bool,optional): verbosity level

        Returns:
            Nothing
        """
        pass


    @staticmethod
    def node_properties_as_dict( node, type_hints=True, verbose=0 ):
        """Create a dictionary populated with the nodes properties.

        Builds a dictionary that is propulated with a node's properties as
        the keys, and their values. Used as a utility routine to avoid
        multiple calls to check if a property exists, and then to fetch its
        value.

        Args:
            node (int or string): either a node number or node path
            type_hints  (bool,optional): flag indicating if type hints should be returned
            verbose (int,optional): verbosity level. default is 0.

        Returns:
            dict: dictionary of the properties, if successfull, otherwise and empty dict
        """

        return {}

    @staticmethod
    def _comment_replacer(match):
        """private function to translate comments to device tree attributes"""
        s = match.group(0)
        if s.startswith('/'):
            global count
            count = count + 1
            r1 = re.sub( '\"', '\\"', s )
            #r2 = "lopper-comment-container-{0} {{ lopper-comment-{1} = \"{2}\";}};".format(count,count, r1)
            r2 = "lopper-comment-{0} = \"{1}\";".format(count, r1)
            return r2
        else:
            return s

    @staticmethod
    def _comment_translate(text):
        """private function used to match (and replace) comments in DTS files"""
        global count
        count = 0
        pattern = re.compile(
                r'//.*?$|/\*.*?\*/|\'(?:\\.|[^\\\'])*\'|"(?:\\.|[^\\"])*"',
                re.DOTALL | re.MULTILINE
            )
        return re.sub(pattern, lopper_base._comment_replacer, text)

    @staticmethod
    def _label_replacer(match):
        """private function to translate labels to device tree attributes"""
        s = match.group(0)
        s1 = match.group(1)
        s2 = match.group(2)
        #print( "   label group 0: %s" % s )
        #print( "   label group 1: %s" % s1 )
        #print( "   label group 2: %s" % s2 )
        if s1 and s2:
            #print( "      label match" )
            global lcount
            lcount = lcount + 1
            r1 = s1.lstrip()
            r1 = re.sub( ':', '', r1 )
            r2 = "{0}\nlopper-label-{1} = \"{2}\";".format(s, lcount, r1)
            return r2
        else:
            return s

    @staticmethod
    def _label_translate(text):
        """private function used to match (and replace) labels in DTS files"""
        global lcount
        lcount = 0
        pattern2 = re.compile(
            r'^\s*?\w*?\s*?\:', re.DOTALL
        )
        pattern = re.compile(
            r'^\s*?(\w*?)\s*?\:(.*?)$', re.DOTALL | re.MULTILINE
        )
        return re.sub(pattern, lopper_base._label_replacer, text)

