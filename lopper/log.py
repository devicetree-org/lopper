#/*
# * Copyright (c) 2023 Advanced Micro Devices, Inc. All Rights Reserved.
# *
# * Author:
# *       Bruce Ashfield <bruce.ashfield@amd.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

import logging
import os
import sys

logging.basicConfig( format='[%(levelname)s]: %(message)s' )
root_logger = logging.getLogger()

def init( verbose ):
    # iterate registered loggers and set their default level to a
    # consistent value.
    # This loop is repeatidly setting the root logger, but that is
    # easier than making a second conditional just to make the root
    # deafult match.
    loggers = logging.root.manager.loggerDict.items()
    for lname,logger in loggers:
        if type(logger) == logging.Logger:
            # default to showing WARNING and above
            logger.setLevel( logging.WARNING )
            logging.getLogger().setLevel( logging.WARNING )
            if verbose == 1:
                logger.setLevel( logging.INFO )
                logging.getLogger().setLevel( logging.INFO )
            elif verbose == 2:
                logger.setLevel( logging.DEBUG )
                logging.getLogger().setLevel( logging.DEBUG )
            elif verbose == 3:
                logger.setLevel( logging.DEBUG )
                logging.getLogger().setLevel( logging.DEBUG )
            elif verbose > 3:
                logger.setLevel( logging.DEBUG )
                logging.getLogger().setLevel( logging.DEBUG )
            else:
                logger.setLevel( logging.WARNING )
                logging.getLogger().setLevel( logging.WARNING )


def _init( name ):
    """
    Initalize a logger for a given name.

    This is typically called with __name__ to intialize a logger
    for a given file (subsystem).

    When called, a formatter is setup that includes the passed name
    and then the standard level and messages.

    All calls using that name (configured logger) will use that
    formatting.

    If no logger is intialized for name the default root logger and
    format will be used.

    Args:
       None

    Returns:
       Nothing
    """
    if name:
        l = logging.getLogger( name )
        formatter = logging.Formatter('[%(name)s][%(levelname)s]: %(message)s' )
        ch = logging.StreamHandler()
        ch.setFormatter( formatter )
        l.addHandler( ch )
        l.propagate = False

def _level( level, name = None ):
    """
    Set the logging level of a named logger

    This sets the logging level (as per python logging) for a
    named logger.

    If no name is passed, the root logger is used

    Args:
        level (logging.<level>): the level set
        name (string,optonal): the name of the logger, "root" if not passed

    Returns:
        Nothing
    """
    if name:
        logger = logging.getLogger(name)
    else:
        logger = root_logger

    logger.setLevel( level=level )

def _warning( message, logger = None ):
    """
    output a warning mesage

    A logger looked up by __logger__() is used to output a message.
    If no specific named logger has been initialized, the default
    root logger is used.

    Args:
        message (string): the string to output
        logger (Logger,optional): the logger to use, otherwise, look it up

    Returns:
        None
    """
    if not logger:
        logger = __logger__()
    logger.warning( message )

def _info( message, output_if_true = True, logger = None ):
    """
    output a warning mesage

    A logger looked up by __logger__() is used to output a message.
    If no specific named logger has been initialized, the default
    root logger is used.

    Args:
        message (string): the string to output
        output_if_true (bool,optonal): flag indicating if the message should be output
        logger (Logger,optional): the logger to use, otherwise, look it up

    Returns:
        None
    """
    if not logger:
        logger = __logger__()

    if output_if_true:
        logger.info( message )

def _error( message, also_exit = True, logger = None ):
    """
    output a warning mesage

    A logger looked up by __logger__() is used to output a message.
    If no specific named logger has been initialized, the default
    root logger is used.

    Args:
        message (string): the string to output
        also_exit (bool,optonal): flag indicating if exit should be called after the message
        logger (Logger,optional): the logger to use, otherwise, look it up

    Returns:
        None
    """
    if not logger:
        logger = __logger__()

    logger.error( message )

    if also_exit:
        sys.exit(1)

def _debug( message, object_to_print = None, logger = None ):
    """
    output a debug mesage

    A logger looked up by __logger__() is used to output a message.
    If no specific named logger has been initialized, the default
    root logger is used.

    Args:
        message (string): the string to output
        object_to_pring (object,optonal): if not None, print() is called on the passed object
        logger (Logger,optional): the logger to use, otherwise, look it up

    Returns:
        None
    """
    if not logger:
        logger = __logger__()

    logger.debug( message )

    if object_to_print:
        if logger.isEnabledFor( logging.DEBUG ):
            object_to_print.print()


## internal calls only, since this pokes at the call stack and is
## expected to find the caller two deep.
def __logger__():
    """
    look for a configured logger

    This is used as a convenience function for looking up an initalized
    logger based on the __name__ of a module.

    Rather than requiring all calls to the utility functions to pass a
    logger or a name (which would typically be __name__), we look at the
    call stack to find the name of the calling python module, and check
    to see if _init() has been called for that module.

    if _init() has been called, we return the logger, otherwise, we return
    the root logger and use the defaults.

    Args:
        None

    Returns:
        Logger
    """
    try:
        # look two levels back for the calling module, and see if it
        # has a logger
        x = os.path.basename(sys._getframe().f_back.f_back.f_code.co_filename)
        # print( "available loggers %s" % logging.root.manager.loggerDict )
        logger = logging.root.manager.loggerDict[x]
        logger = logging.getLogger(x)
    except Exception as e:
        logger = root_logger

    return logger
