#/*
# * Copyright (c) 2023 - 2025 Advanced Micro Devices, Inc. All Rights Reserved.
# *
# * Author:
# *       Onkar Harsh <onkar.harsh@amd.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

import os
import sys
import re
import glob
import yaml
from typing import Any, List, Optional, Dict, Union
import shutil
import logging
from lopper.log import _init, _warning

_init(__name__)

def to_cmakelist(pylist):
    cmake_list = ';'.join(pylist)
    cmake_list = '"{}"'.format(cmake_list)

    return cmake_list

def is_file(filepath: str, silent_discard: bool = True) -> bool:
    """Return True if the file exists Else returns False and raises Not Found Error Message.
    
    Args:
        filepath: File Path.
    Raises:
        FileNotFoundError: Raises exception if file not found.
    Returns:
        bool: True, if file is found Or False, if file is not found.
    """
   
    if os.path.isfile(filepath):
        return True
    elif not silent_discard:
        err_msg = f"No such file exists: {filepath}"
        raise FileNotFoundError(err_msg) from None
    else:
        return False

def is_dir(dirpath: str, silent_discard: bool = True) -> bool:
    """Checks if directory exists.
    
    Args:
        dirpath: Directory Path.
    Raises:
        ValueError (Exception): Raises exception if directory not found.
    Returns:
        bool: True, if directory is found Or False, if directory is not found.
    """

    if os.path.isdir(dirpath):
        return True
    elif not silent_discard:
        err_msg = f"No such directory exists: {dirpath}"
        raise ValueError(err_msg) from None
    else:
        return False

def get_base_name(fpath):
    """
    This api takes rel path or full path and returns base name
    Args:
        fpath: Path to get the base name from.
    Returns:
        string: Base name of the path
    """
    return os.path.basename(fpath.rstrip(os.path.sep))

def get_dir_path(fpath):
    """
    This api takes file path and returns it's directory path
    
    Args:
        fpath: Path to get the directory path from.
    Returns:
        string: Full Directory path of the passed path
    """
    return os.path.dirname(fpath.rstrip(os.path.sep))

def get_abs_path(fpath):
    """
    This api takes file path and returns it's absolute path
    Args:
        fpath: Path to get the absolute path from.
    Returns:
        string: Absolute location of the passed path
    """
    return os.path.abspath(fpath)

def load_yaml(filepath: str) -> Optional[dict]:
    """Read yaml file data and returns data in a dict format.
    
    Args:
        filepath: Path of the yaml file.
    Returns:
        dict: Return Python dict if the file reading is successful.
    """

    if is_file(filepath):
        try:
            with open(filepath) as f:
                data = yaml.safe_load(f)
            return data
        except:
            print("%s file reading failed" % filepath)
            return {}
    else:
        return {}

def copy_file(src: str, dest: str, follow_symlinks: bool = False, silent_discard: bool = True) -> None:
    """
    copies the file from source to destination.
    Args:
        | src: source file path
        | dest: destination file path
        | follow_symlinks: maintain the symlink while copying
        | silent_discard: Dont raise exception if the source file doesnt exist 
    """
    is_file(src, silent_discard)
    shutil.copy2(src, dest, follow_symlinks=follow_symlinks)
    os.chmod(dest, 0o644)

def find_files(search_pattern, search_path):
    """
    This api find the files matching regex directories and returns absolute
    path of files, if file exists

    Args:
        | search_pattern: The regex pattern to be searched in file names
        | search_path: The directory that needs to be searched
    Returns:
        string: All the file paths that matches the pattern in the searched path.

    """

    return glob.glob(f"{search_path}{os.path.sep}{search_pattern}")
    
def log_setup(options):
  
    """
    Sets up the log level based on the verbosity given by the user.
    
    Args:
        options (dict): Dictionary containing command-line arguments.
    
    Returns:
        int: Logging level.
    """
    verbose = [i for i in options.get("args", []) if i.startswith('-v')]
    verbose_level = 1 if verbose else 0 
    # Adjust logging level based on verbose level
    level = logging.DEBUG if verbose_level >= 1 else logging.WARNING
    #print(f"[LOG_SETUP] Verbose level = {verbose_level}, Final level = {level}")
    return level

def run_exec(yaml_condition, proc_ip_name, family, variant=None, return_list="examples", yaml_file=""):
    """
    Executes a Python condition string in a restricted local scope and returns a specified list.

    Args:
        yaml_condition (str): Python code to execute, typically from a YAML file.
        proc_ip_name (str): Name of the processor IP available in the local scope as 'proc'.
        family (str): Platform family available in the local scope as 'platform'.
        variant (str, optional): Variant available in the local scope as 'variant'.
        return_list (str, optional): Name of the list to return from the local scope. Defaults to "examples".
        yaml_file (str, optional): YAML file name for error reporting.

    Returns:
        list: The list named by `return_list` from the local scope after executing the condition.

    Notes:
        Any exceptions during execution are caught and logged as warnings.
    """
    local_scope = {
        "proc": proc_ip_name,
        "platform": family,
        "variant": variant,
        return_list: []
    }
    try:
        exec(yaml_condition, {"__builtins__": {}}, local_scope)
    except Exception as e:
        _warning(f"The condition in the {yaml_file} file has failed. -> {e}")
    finally:
        return local_scope[return_list]
