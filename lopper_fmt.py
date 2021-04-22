from enum import Enum

# used in encode/decode routines
class LopperFmt(Enum):
    """Enum class to define the types and encodings of Lopper format routines
    """
    SIMPLE = 1
    COMPOUND = 2
    HEX = 3
    DEC = 4
    STRING = 5
    MULTI_STRING = 6
    UINT8 = 7
    UINT32 = 8
    UINT64 = 9
    EMPTY = 10
    UNKNOWN = 11

