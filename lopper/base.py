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
import struct


lopper_known_types = {
    "compatible" : LopperFmt.STRING,
    ".*mem.*base.*" : LopperFmt.UINT32
}

lopper_discovered_types = {}

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
        preprocessed_name = f"{dts_dirname}/{dts_filename}.pp"

        includes += dts_dirname
        includes += " "
        includes += os.getcwd()

        # try pcpp first
        ppargs = (os.environ.get('LOPPER_CPP') or shutil.which("pcpp") or shutil.which("pcpp-python") or "").split()
        if ppargs and (os.path.basename(ppargs[0]) == "pcpp" or os.path.basename(ppargs[0]) == "pcpp-python"):
            ppargs += "--passthru-comments".split()
        else:
            ppargs = (os.environ.get('LOPPER_CPP') or shutil.which("cpp") or "").split()
            # Note: might drop the -I include later
            ppargs += "-nostdinc -I include -undef -x assembler-with-cpp ".split()

        ppargs += (os.environ.get('LOPPER_PPFLAGS') or "").split()
        for i in includes.split():
            ppargs.append(f"-I{i}")
        ppargs += ["-o", preprocessed_name, dts_file]
        if verbose:
            print( f"[INFO]: preprocessing dts_file: {ppargs}" )

        result = subprocess.run( ppargs, check = True )
        if result.returncode != 0:
            print( f"[ERROR]: unable to preprocess dts file: {ppargs}" )
            print( f"\n{textwrap.indent(result.stderr.decode(), '         ')}" )
            sys.exit(result.returncode)

        return preprocessed_name

    @staticmethod
    def dt_compile( dts_file, i_files ="", includes="", force_overwrite=False, outdir="./",
                    save_temps=False, verbose=0, enhanced = True, symbols = False ):
        return None

    @staticmethod
    def property_value_decode( prop, poffset, ftype=LopperFmt.SIMPLE,
                               encode=LopperFmt.UNKNOWN, verbose=0,
                               schema=None ):
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
            print( f"[DBG+]: decode start: {prop} {ftype}")

        # Note: these could also be nested.
        if ftype == LopperFmt.SIMPLE:
            if encode == LopperFmt.UNKNOWN:
                encode_calculated = lopper_base.property_type_guess( prop )
            else:
                encode_calculated = encode

            val = ""
            if repr(encode_calculated) == repr(LopperFmt.STRING) or \
               repr(encode_calculated) == repr(LopperFmt.EMPTY ):
                if not val:
                    try:
                        val = prop.as_str()
                        decode_msg = f"(string): {val}"
                    except:
                        pass

                if not val:
                    try:
                        # this is getting us some false positives on multi-string. Need
                        # a better test
                        val = prop[:-1].decode('utf-8').split('\x00')
                        #val = ""
                        decode_msg = f"(multi-string): {val}"
                    except:
                        pass
            else:
                val = ""
                decode_msg = ""
                try:
                    val = prop.as_uint32()
                    decode_msg = f"(uint32): {val}"
                except:
                    pass
                if not val and val != 0:
                    try:
                        val = prop.as_uint64()
                        decode_msg = f"(uint64): {val}"
                    except:
                        pass

            if not val and val != 0:
                decode_msg = "** unable to decode value **"
        else:
            # compound format
            decode_msg = ""
            val = ['']

            if encode == LopperFmt.UNKNOWN:
                encode_calculated = lopper_base.property_type_guess( prop )
            else:
                encode_calculated = encode

            # this is for properties like "ranges;", if there's no schema
            # they will show up as EMPTY. But if there's a schema they'll
            # show as a number type, but have no values (length)
            if repr(encode_calculated) == repr(LopperFmt.EMPTY) or \
               ((repr(encode_calculated) == repr(LopperFmt.UINT16) or \
                 repr(encode_calculated) == repr(LopperFmt.UINT32) or \
                 repr(encode_calculated) == repr(LopperFmt.UINT64) or \
                 repr(encode_calculated) == repr(LopperFmt.UINT8)) and len(prop) == 0) :
                return val

            first_byte = prop[0]
            last_byte = prop[-1]

            # TODO: we shouldn't need these repr() wrappers around the enums, but yet
            #       it doesn't seem to work on the calculated variable without them
            if repr(encode_calculated) == repr(LopperFmt.STRING) or \
               repr(encode_calculated) == repr(LopperFmt.MULTI_STRING):
                try:
                    val = prop[:-1].decode('utf-8').split('\x00')
                    decode_msg = f"(multi-string): {val}"
                except:
                    encode_calculated = encode

            if repr(encode_calculated) == repr(LopperFmt.UINT16) or \
               repr(encode_calculated) == repr(LopperFmt.UINT32) or \
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
                    elif encode_calculated == LopperFmt.UINT16:
                        binary_data = False
                        num_nums = 1
                        start_index = 0
                        end_index = 2
                        short_int_size = 2
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
            print( f"[DBG+]: decoding prop: \"{prop}\" ({poffset}) [{prop}] --> {decode_msg}" )

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
                    try:
                        # if this throws an exception, libfdt doesn't agree with
                        # our analysis that this is a string, so we'll try another
                        # couple of checks to be sure that it isn't a string and
                        # we'll switch to bytes for the type
                        prop.as_str()
                    except Exception as e:
                        # this could be a very unluckily encoded number at these
                        # lengths. i.e. mem-ctrl-base-address =  <0x76000000>; manages
                        # to pass all the tests for a string as the first bytes look
                        # like the letter "v" and then we find null characters (the 0's)
                        # and declare it a partial string
                        if len(prop) == 4:
                            try:
                                prop.as_uint32()
                                type_guess = LopperFmt.UINT8
                            except:
                                pass
                        if len(prop) == 8:
                            try:
                                prop.as_uint64()
                                type_guess = LopperFmt.UINT8
                            except:
                                pass

                except Exception as e:
                    # it didn't decode, fall back to numbers ..
                    type_guess = LopperFmt.UINT8
            else:
                type_guess = LopperFmt.UINT8
        else:
            # this catches an empty string of a property:
            #   i.e.:    property = "";
            # otherwise it is picked up as UINT8 and is output
            # as a list of numbers, which isn't what we want
            if first_byte == 0 and len(prop) == 1:
                try:
                    prop.as_str()
                    type_guess = LopperFmt.STRING
                except:
                    pass
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
    def property_type_guess_by_byte(prop):
        """
        Determine the property type based on byte content.

        Args:
            prop: The property object containing name and value data.

        Returns:
            LopperFmt: The guessed format type for the property.
        """
        print(f"guessing type for {prop.name}")

        if len(prop) == 0:
            return LopperFmt.EMPTY

        # Attempt numeric decoding first - priority given based on length and content
        num = lopper_base.property_decode_as_number(prop)
        if num is not None:
            # print(f"Interpreted as a number: {num} (len {len(prop)})")
            string = lopper_base.property_decode_as_string(prop)
            if string:
                # consult our table of known types to break the tie ...
                known_type = lopper_base.property_get_known_type(prop.name)
                if known_type:
                    return known_type
                else:
                    return LopperFmt.STRING

            return LopperFmt.UINT32 if len(prop) == 4 else LopperFmt.UINT64

        # Attempt string decoding - only when number interpretation fails
        string = lopper_base.property_decode_as_string(prop)
        if string is not None:
            return LopperFmt.STRING

        # Default fallthrough to binary type when no match is directly observed
        return LopperFmt.UINT8

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
        if re.search( r"^<", property_string ):
            property_string = re.sub( r"<", "", property_string )
            property_string = re.sub( r">", "", property_string )
            for n in property_string.split():
                base = 10
                if re.search( r"0x", n ):
                    base = 16
                try:
                    n_as_int = int(n,base)
                    n = n_as_int
                except Exception as e:
                    print( f"[ERROR]: cannot convert element {n} to number ({e})" )
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
                if re.search( r"0x", p ):
                    base = 16
                try:
                    p_as_int = int(p,base)
                    p = p_as_int
                    retval.append (p )
                except Exception as e:
                    # it is a string
                    retval.append( p )

        return retval

    @staticmethod
    def property_get_known_type(property_name):
        """
        Determine the known format type for a given property name using predefined patterns.

        Args:
            property_name (str): The name of the property to check against known types.

        Returns:
            LopperFmt: The format type if a known type is found, otherwise None.
        """
        for pattern, ftype in lopper_known_types.items():
            if re.match(pattern, property_name):
                return ftype
        return None

    @staticmethod
    def property_decode_as_string(prop):
        """
        Decode byte array to a string if it is valid and printable.

        Args:
            prop (bytes): The property value as a byte array to decode.

        Returns:
            str: The decoded string if valid and printable, otherwise None.
        """
        try:
            decoded = prop.decode('utf-8', errors='ignore').rstrip('\x00')
            # Strings should be identifiable by non-numeric characteristics
            return decoded if decoded.isprintable() else None
        except UnicodeDecodeError:
            return None

    @staticmethod
    def property_decode_as_number(prop):
        """
        Decode byte sequence as a numeric type when properly formatted.

        Args:
            prop (bytes): The property value as a byte array to decode.

        Returns:
            int: The decoded number if successful, otherwise None.
        """
        len_prop = len(prop)
        if len_prop in [4, 8]:
            try:
                num = struct.unpack('>L', prop)[0] if len_prop == 4 else struct.unpack('>Q', prop)[0]
                return num
            except struct.error:
                pass
        return None

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
                    # As by the system device tree spec, the below should be the
                    # description for address-map, but currently system device trees
                    # are incorrectly using #ranges-address-cells in the node itself
                    #"address-map" : [ '#ranges-address-cells phandle ^:#address-cells #ranges-size-cells', 0 ],
                    "address-map" : [ '#ranges-address-cells phandle #ranges-address-cells #ranges-size-cells', 0 ],
                    "secure-address-map" : [ '#ranges-address-cells phandle ^:#address-cells #ranges-size-cells', 0 ],
                    "interrupt-parent" : [ 'phandle', 0 ],
                    "interrupts-extended" : [ 'phandle field field' ],
                    "iommus" : [ 'phandle field' ],
                    "interrupt-map" : [ '#address-cells #interrupt-cells phandle:#interrupt-cells' ],
                    "access" : [ 'phandle flags' ],
                    "cpus" : [ 'phandle mask mode' ],
                    "clocks" : [ 'phandle:#clock-cells' ],
                    "reset-gpios" : [ 'phandle field field' ],
                    "resets" : [ 'phandle field' ],
                    "assigned-clocks" : [ 'phandle:#clock-cells' ],
                    "cpu-idle-states" : [ 'phandle' ],
                    "power-domains" : [ 'phandle field' ],
                    "operating-points-v2" : [ 'phandle' ],
                    "next-level-cache" : [ 'phandle' ],
                    "interrupt-affinity" : [ 'phandle' ],
                    "fpga-mgr" : [ 'phandle' ],
                    "__phandle_exclude__" : [ '.*lop.*', '/__symbols__', '/aliases' ],
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
            r1 = re.sub( r'\"', '\\"', s )
            #r2 = "lopper-comment-container-{0} {{ lopper-comment-{1} = \"{2}\";}};".format(count,count, r1)
            r2 = f"lopper-comment-{count} = \"{r1}\";"
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
            r1 = re.sub( r':', '', r1 )
            r2 = f"{s}\nlopper-label-{lcount} = \"{r1}\";"
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

    @staticmethod
    def parse_dts_phandles(dts_content):
        """
        Parse device tree source content and extract phandle references.

        Args:
            dts_content: The device tree source content as a string

        Returns:
            Dictionary structure:
            {
                "/path/to/node": {
                    "property_name": [(index_in_property, "&phandle_name", -1), ...]
                }
            }
        """
        # Remove comments and clean up whitespace
        cleaned_content = re.sub(r'//.*$', '', dts_content, flags=re.MULTILINE)
        cleaned_content = re.sub(r'/\*.*?\*/', '', cleaned_content, flags=re.DOTALL)

        result = {}
        node_stack = []  # Stack to track current node path
        current_path = ""

        lines = cleaned_content.split('\n')
        i = 0

        while i < len(lines):
            line = lines[i].strip()

            if not line:
                i += 1
                continue

            # Check for node definition (name { or label: name { or name@address {)
            # Handle both labeled and unlabeled nodes
            node_match = re.match(r'^(?:([a-zA-Z0-9_]+):\s*)?([a-zA-Z0-9_@.-]+)\s*\{', line)
            if node_match:
                label = node_match.group(1)  # Optional label (e.g., "amba")
                node_name = node_match.group(2)  # Node name (e.g., "axi@f1000000")

                # Build the full path
                if current_path == "" or current_path == "/":
                    if node_name == "/":
                        current_path = "/"
                    else:
                        current_path = "/" + node_name
                else:
                    current_path = current_path + "/" + node_name

                node_stack.append(current_path)
                i += 1
                continue

            # Check for closing brace
            if line == '}' or line.endswith('};'):
                if node_stack:
                    node_stack.pop()
                    if node_stack:
                        current_path = node_stack[-1]
                    else:
                        current_path = ""
                i += 1
                continue

            # Check for property definitions with phandles
            # Handle both single-line and multi-line properties
            prop_match = re.match(r'^([a-zA-Z0-9_-]+)\s*=\s*(.*)', line)
            if prop_match and current_path:
                prop_name = prop_match.group(1)
                prop_value = prop_match.group(2)

                # Handle multi-line properties
                if not prop_value.rstrip().endswith(';'):
                    # Continue reading until we find the semicolon
                    j = i + 1
                    while j < len(lines) and not prop_value.rstrip().endswith(';'):
                        next_line = lines[j].strip()
                        if next_line:
                            prop_value += " " + next_line
                        j += 1
                    i = j
                else:
                    i += 1

                # Remove the trailing semicolon
                prop_value = prop_value.rstrip(';').strip()

                # Find all phandle references in the property value
                phandle_refs = lopper_base.find_phandles_in_property(prop_value)

                if phandle_refs:
                    if current_path not in result:
                        result[current_path] = {}
                    result[current_path][prop_name] = phandle_refs
            else:
                i += 1

        return result

    @staticmethod
    def find_phandles_in_property(prop_value):
        """
        Find all phandle references in a property value string.

        Args:
            prop_value: The property value string

        Returns:
            List of tuples containing (index_in_property, phandle_name, -1)
        """
        phandle_refs = []

        # Split the property value into tokens, considering various delimiters
        # This handles cases like: &gpio1 2 3, &gpio2, <&uart0 &spi1>
        tokens = re.findall(r'[^,\s<>]+', prop_value)

        for index, token in enumerate(tokens):
            # Check if token is a phandle reference
            phandle_match = re.match(r'&([a-zA-Z0-9_-]+)', token)
            if phandle_match:
                phandle_name = "&" + phandle_match.group(1)
                phandle_refs.append((index, phandle_name, -1))

        return phandle_refs

    @staticmethod
    def print_phandle_map(phandle_map):
        """Pretty print the phandle map."""
        print("Phandle Reference Map:")
        print("=" * 50)

        for node_path in sorted(phandle_map.keys()):
            print(f"\nNode: {node_path}")
            for prop_name, phandle_refs in phandle_map[node_path].items():
                print(f"  Property: {prop_name}")
                for index, phandle_name, value in phandle_refs:
                    print(f"    {phandle_name} at index {index}, value: {value}")

    @staticmethod
    def update_phandle_values(tree, phandle_map):
        """
        Update the third value in phandle tuples by dereferencing symbolic names.

        Args:
            tree: The tree object with deref() method
            phandle_map: The phandle dictionary to update

        Returns:
            The updated phandle_map with resolved integer values
        """
        for node_path, properties in phandle_map.items():
            for prop_name, phandle_refs in properties.items():
                updated_refs = []
                for index, phandle_name, current_value in phandle_refs:
                    # Remove the '&' prefix for the deref call
                    phandle_label = phandle_name[1:] if phandle_name.startswith('&') else phandle_name

                    # Call tree.deref() to get the integer value
                    deref_value = tree.deref(phandle_label)

                    # If we get a non-None value, use it; otherwise set to -1
                    new_value = deref_value if deref_value is not None else -1
                    updated_refs.append((index, phandle_name, new_value))

                # Update the property with the new references
                properties[prop_name] = updated_refs

        return phandle_map

    @staticmethod
    def encode_phandle_map_to_dts(phandle_map):
        """
        Encode the phandle map into a syntactically correct device tree node
        that can be appended to an existing complete DTS file.

        Args:
            phandle_map: The phandle dictionary to encode

        Returns:
            String containing the "lopper-phandles" device tree node
        """
        lines = []
        lines.append("")
        lines.append("/ {")
        lines.append("\t__lopper-phandles__ {")
        lines.append("\t\tcompatible = \"lopper,phandle-tracker\";")
        lines.append("")

        # Create a counter for unique property names
        prop_counter = 0

        # Sort nodes for consistent output
        for node_path in sorted(phandle_map.keys()):
            properties = phandle_map[node_path]

            # Each property gets its own phandle_entry, even from the same node
            for prop_name in sorted(properties.keys()):
                phandle_refs = properties[prop_name]

                # Create unique property names using counter
                entry_prop_name = f"phandle_entry_{prop_counter:04d}"
                prop_counter += 1

                # Combine path and property name into single string (path/property)
                combined_path = f"{node_path}/{prop_name}"

                # Build the data array as all strings: path, then quartets of (index, symbol, value)
                prop_values = []
                prop_values.append(f'"{combined_path}"')

                # Add phandle data as quartets: index, symbol, value (all as strings)
                for index, phandle_name, value in phandle_refs:
                    prop_values.append(f'"{index}"')        # Index as string
                    prop_values.append(f'"{phandle_name}"')  # Symbolic name as string
                    prop_values.append(f'"{value}"')         # Value as string

                # Join all values - all strings now, no mixed types
                prop_value_str = ", ".join(prop_values)
                lines.append(f"\t\t{entry_prop_name} = {prop_value_str};")

                # Add a human-readable comment
                phandle_names = [phandle_name for _, phandle_name, _ in phandle_refs]
                comment = " ".join(phandle_names)
                lines.append(f"\t\t/* {node_path}.{prop_name}: {comment} */")
                lines.append("")

        lines.append("\t};")
        lines.append("};")
        return "\n".join(lines)

    @staticmethod
    def decode_path_and_property(path_string, prop_string):
        """
        Decode the path and property name from the strings (no decoding needed).

        Args:
            path_string: The path string
            prop_string: The property name string

        Returns:
            Tuple of (path, property_name)
        """
        return path_string, prop_string

    @staticmethod
    def decode_phandle_map_from_dtb(lopper_node_dict):
        """
        Recreate the original phandle dictionary structure from the DTB-parsed data.

        Args:
            lopper_node_dict: The parsed lopper-phandles node dictionary from DTB

        Returns:
            Dictionary structure:
            {
                "/path/to/node": {
                    "property_name": [(index_in_property, "&phandle_name", resolved_value), ...]
                }
            }
        """
        result = {}

        # Find all phandle entries by looking for phandle_entry_XXXX keys
        entry_keys = [key for key in lopper_node_dict.keys() if key.startswith('phandle_entry_')]

        for entry_key in sorted(entry_keys):
            entry_data = lopper_node_dict[entry_key]

            if not entry_data or len(entry_data) < 1:
                continue

            # First element is the combined path/property string
            combined_path = entry_data[0]

            # Split path and property - last component after final '/' is property name
            if '/' not in combined_path:
                continue

            path_parts = combined_path.split('/')
            prop_name = path_parts[-1]  # Last component is property name
            node_path = '/'.join(path_parts[:-1])  # Everything else is the path

            # Handle root path case
            if not node_path:
                node_path = '/'

            # Parse remaining data as triplets: index, symbol, value (all strings)
            phandle_refs = []
            i = 1  # Start after the combined path string
            while i + 2 < len(entry_data):
                index_str = entry_data[i]       # String: "0"
                symbolic_name = entry_data[i + 1]  # String: "&gpio"
                value_str = entry_data[i + 2]   # String: "525" or "-1"

                # Convert strings back to integers
                try:
                    index = int(index_str)
                    phandle_value = int(value_str)
                except ValueError:
                    # Skip invalid entries
                    i += 3
                    continue

                phandle_refs.append((index, symbolic_name, phandle_value))
                i += 3

            # Add to result dictionary
            if node_path not in result:
                result[node_path] = {}
            result[node_path][prop_name] = phandle_refs

        return result

    @staticmethod
    def get_phandle_value(phandle_map, node_path, prop_name, index):
        """
        Get the phandle value for a specific node, property, and index.

        Args:
            phandle_map: The phandle dictionary
            node_path: The full path to the node (e.g., "/axi@f1000000/dma@ffa80000")
            prop_name: The property name (e.g., "iommus")
            index: The index within the property (e.g., 0)

        Returns:
            The phandle value (integer) if found, None if not found
        """
        if not phandle_map:
            return None

        # Check if node exists in the map
        if node_path not in phandle_map:
            return None

        # Check if property exists for this node
        if prop_name not in phandle_map[node_path]:
            return None

        # Search through the phandle references for the matching index
        phandle_refs = phandle_map[node_path][prop_name]
        for ref_index, phandle_name, phandle_value in phandle_refs:
            if ref_index == index:
                return phandle_value

        # Index not found
        return None

    @staticmethod
    def _extract_property_value(dts_content, node_path, prop_name):
        """
        Extract the full property value from DTS content for pattern analysis.
        Handles multi-line properties and complex formatting.

        Args:
            dts_content: The device tree source content
            node_path: Path to the node
            prop_name: Property name

        Returns:
            String containing the property value or None if not found
        """
        # Remove comments first
        cleaned_content = re.sub(r'//.*$', '', dts_content, flags=re.MULTILINE)
        cleaned_content = re.sub(r'/\*.*?\*/', '', cleaned_content, flags=re.DOTALL)

        lines = cleaned_content.split('\n')
        in_target_node = False
        brace_count = 0

        # Extract node name from path for matching
        node_name = node_path.split('/')[-1] if '/' in node_path else node_path
        if node_name == '':
            node_name = '/'

        i = 0
        while i < len(lines):
            line = lines[i].strip()

            # Track if we're in the right node
            if node_name in line and '{' in line:
                in_target_node = True
                brace_count = line.count('{') - line.count('}')
                i += 1
                continue

            if in_target_node:
                # Track braces to know when we exit the node
                brace_count += line.count('{') - line.count('}')
                if brace_count <= 0:
                    break

                # Look for the property - handle both single line and start of multi-line
                prop_pattern = rf'\b{re.escape(prop_name)}\s*='
                if re.search(prop_pattern, line):
                    # Found the property, extract its value
                    value_parts = []

                    # Get the part after the = sign
                    equals_pos = line.find('=')
                    if equals_pos != -1:
                        value_part = line[equals_pos + 1:].strip()
                        value_parts.append(value_part)

                        # If it doesn't end with semicolon, it's multi-line
                        if not value_part.rstrip().endswith(';'):
                            # Continue reading until semicolon
                            i += 1
                            while i < len(lines):
                                next_line = lines[i].strip()
                                value_parts.append(next_line)
                                if next_line.rstrip().endswith(';'):
                                    break
                                i += 1

                    # Join all parts and clean up
                    full_value = ' '.join(value_parts)
                    if full_value.endswith(';'):
                        full_value = full_value[:-1].strip()

                    return full_value

            i += 1

        return None

    @staticmethod
    def _analyze_property_pattern(prop_value, phandle_refs):
        """
        Analyze a property value to determine its phandle pattern.
        Handles multi-record properties and complex structures.

        Args:
            prop_value: The property value string
            phandle_refs: List of phandle references found in this property

        Returns:
            String describing the pattern or None
        """
        if not prop_value or not phandle_refs:
            return None

        # Clean up the property value and split into records
        # Records are typically separated by angle bracket groups: <...>, <...>
        records = []
        current_record = []
        in_brackets = False
        tokens = []

        # First, tokenize respecting angle brackets and commas
        i = 0
        current_token = ""
        while i < len(prop_value):
            char = prop_value[i]

            if char == '<':
                if current_token.strip():
                    tokens.append(current_token.strip())
                    current_token = ""
                in_brackets = True
                tokens.append('<')
            elif char == '>':
                if current_token.strip():
                    tokens.append(current_token.strip())
                    current_token = ""
                in_brackets = False
                tokens.append('>')
            elif char == ',' and not in_brackets:
                if current_token.strip():
                    tokens.append(current_token.strip())
                    current_token = ""
                # Comma outside brackets often separates records
                tokens.append(',')
            elif char.isspace():
                if current_token.strip():
                    tokens.append(current_token.strip())
                    current_token = ""
            else:
                current_token += char

            i += 1

        if current_token.strip():
            tokens.append(current_token.strip())

        # Now group tokens into records based on angle brackets
        current_record = []
        records = []
        in_record = False

        for token in tokens:
            if token == '<':
                in_record = True
                current_record = []
            elif token == '>':
                if in_record and current_record:
                    records.append(current_record[:])
                in_record = False
            elif token == ',' and not in_record:
                # Record separator outside of brackets
                continue
            elif in_record and token not in ['<', '>']:
                current_record.append(token)

        # If no angle brackets found, treat the whole thing as one record
        if not records and tokens:
            # Filter out angle brackets and commas
            clean_tokens = [t for t in tokens if t not in ['<', '>', ',']]
            if clean_tokens:
                records = [clean_tokens]

        # Analyze each record to find the pattern
        if not records:
            return None

        # Create mapping of token positions to phandle names
        # _ is the resolved phandle, which we don't care about for this
        phandle_map = {}
        for index, phandle_name, _ in phandle_refs:
            phandle_map[index] = phandle_name

        # Analyze the first record to establish pattern
        record_patterns = []
        for record in records:
            pattern_parts = []
            token_index = 0

            for token in record:
                if token_index in phandle_map or token.startswith('&'):
                    pattern_parts.append('phandle')
                else:
                    # Classify non-phandle tokens
                    try:
                        int(token, 0)  # Try parsing as number
                        pattern_parts.append('field')
                    except ValueError:
                        if token.startswith('"') and token.endswith('"'):
                            pattern_parts.append('string')
                        else:
                            pattern_parts.append('field')
                token_index += 1

            if pattern_parts:
                record_patterns.append(' '.join(pattern_parts))

        # Find the most common record pattern
        if record_patterns:
            from collections import Counter
            pattern_counts = Counter(record_patterns)
            most_common_pattern = pattern_counts.most_common(1)[0][0]

            # If we have multiple records with the same pattern, it's repeating
            if len(records) > 1 and len(set(record_patterns)) == 1:
                return most_common_pattern  # Will be marked as repeating elsewhere
            else:
                return most_common_pattern

        return None

    @staticmethod
    def _generalize_pattern(patterns):
        """
        Create a generalized pattern from multiple observed patterns.

        Args:
            patterns: List of pattern strings

        Returns:
            Tuple of (pattern_string, repeat_flag) or None
        """
        if not patterns:
            return None

        # Find the most common pattern
        from collections import Counter
        pattern_counts = Counter(patterns)
        most_common = pattern_counts.most_common(1)[0][0]

        # Determine if this is a repeating pattern
        # If we see the same pattern multiple times, it's likely repeating
        repeat_flag = 1 if len(patterns) > 1 and len(set(patterns)) == 1 else 0

        return (most_common, repeat_flag)

    @staticmethod
    def analyze_phandle_patterns(dts_content):
        """
        Analyze DTS content to identify phandle patterns and generate property descriptions.

        Args:
            dts_content: The device tree source content as a string

        Returns:
            Dictionary of property descriptions in the format:
            {
                "property_name": ["pattern_description", repeat_flag],
                ...
            }
            The phandle map (see parse_dts_phandles for structure)
        """
        # Parse the DTS to get phandle references
        phandle_map = lopper_base.parse_dts_phandles(dts_content)

        # Dictionary to collect patterns for each property
        property_patterns = {}

        # Analyze each property that contains phandles
        for node_path, properties in phandle_map.items():
            for prop_name, phandle_refs in properties.items():
                if prop_name not in property_patterns:
                    property_patterns[prop_name] = []

                # Get the full property value to analyze the pattern
                prop_value = lopper_base._extract_property_value(dts_content, node_path, prop_name)
                if prop_value:
                    pattern = lopper_base._analyze_property_pattern(prop_value, phandle_refs)
                    if pattern:
                        property_patterns[prop_name].append(pattern)

        # Generate descriptions from collected patterns
        descriptions = {}
        for prop_name, patterns in property_patterns.items():
            # Find the most common pattern or create a generalized one
            result = lopper_base._generalize_pattern(patterns)
            if result:
                pattern, repeat_flag = result
                descriptions[prop_name] = [pattern, repeat_flag]

        return descriptions, phandle_map

    @staticmethod
    def generate_property_descriptions(dts_content):
        """
        Generate a complete property description dictionary from DTS analysis.

        Args:
            dts_content: The device tree source content

        Returns:
            Dictionary suitable for use as property descriptions
            The phandle map (see parse_dts_phandles for structure)
        """
        # Get the analyzed patterns
        patterns, phandle_map = lopper_base.analyze_phandle_patterns(dts_content)

        # Add DEFAULT entry
        patterns["DEFAULT"] = ["this is the generated phandle map"]

        return patterns, phandle_map

    @staticmethod
    def update_phandle_property_descriptions(analyzed_patterns):
        """
        Update the phandle_possible_prop_dict class variable with new property descriptions
        from a previous analysis. Existing descriptions are left unchanged.

        Args:
            analyzed_patterns: Dictionary from analyze_phandle_patterns() containing
                              {"property_name": ["pattern", repeat_flag], ...}

        Returns:
            Dictionary of newly added property descriptions
        """
        # Check if input is valid
        if not analyzed_patterns or not isinstance(analyzed_patterns, dict):
            return {}

        # Get the current base dictionary (this handles the lazy initialization)
        base_dict = lopper_base.phandle_possible_properties().copy()

        # Track what we add for return value
        newly_added = {}

        # Update only properties we don't already know about
        for prop_name, description in analyzed_patterns.items():
            if prop_name not in base_dict:
                base_dict[prop_name] = description
                newly_added[prop_name] = description

        # Assign the combined dictionary back to the class variable
        lopper_base.phandle_possible_prop_dict = base_dict

        return newly_added

    @staticmethod
    def get_property_description(prop_name):
        """
        Get the property description for a given property name.

        Args:
            prop_name: The property name to look up

        Returns:
            List containing [pattern_description, repeat_flag] or DEFAULT if not found
        """
        return lopper_base.phandle_possible_prop_dict.get(
            prop_name,
            lopper_base.phandle_possible_prop_dict.get("DEFAULT", ["unknown pattern", 0])
        )

    @staticmethod
    def list_known_properties():
        """
        Get a list of all known property names in the descriptions table.

        Returns:
            List of property names (excluding DEFAULT)
        """
        return [prop for prop in lopper_base.phandle_possible_prop_dict.keys() if prop != "DEFAULT"]
