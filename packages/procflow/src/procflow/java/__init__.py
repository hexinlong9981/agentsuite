"""Java authoring and runtime support for procflow procedures.

Annotations, code generation (yaml2java, ir2java, stub), static allowed-API
checking, AST/regex extraction, and secure java-step execution.
"""
from __future__ import annotations

from .allowed_api import ApiViolation, check_directory, check_source_content
from .codegen import ir2java_interface, stub, to_class_name, to_method_name, yaml2java
from .env import find_jdk, find_maven, get_child_java_env
from .extract import extract_from_file, extract_from_source, scan_and_extract_directory
from .runner import JavaStepError, resolve_step_command

__all__ = [
    "find_jdk",
    "find_maven",
    "get_child_java_env",
    "yaml2java",
    "ir2java_interface",
    "stub",
    "to_class_name",
    "to_method_name",
    "ApiViolation",
    "check_source_content",
    "check_directory",
    "extract_from_source",
    "extract_from_file",
    "scan_and_extract_directory",
    "resolve_step_command",
    "JavaStepError",
]
