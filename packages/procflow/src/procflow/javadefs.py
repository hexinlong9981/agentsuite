"""Backward-compatible facade for procflow Java authoring tools.

Aliases procflow.java.* for seamless invocation.
"""
from __future__ import annotations

from .java import (
    ApiViolation,
    JavaStepError,
    check_directory,
    check_source_content,
    extract_from_file,
    extract_from_source,
    find_jdk,
    find_maven,
    get_child_java_env,
    ir2java_interface,
    resolve_step_command,
    scan_and_extract_directory,
    stub,
    to_class_name,
    to_method_name,
    yaml2java,
)

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
