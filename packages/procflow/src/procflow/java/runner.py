"""Runtime execution helper for Java code steps (java-step).

Provides secure, allow-listed invocation of Java step classes using isolated child process
environments without shell interpretation.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from .env import find_jdk, get_child_java_env


STEP_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


class JavaStepError(Exception):
    """Raised when a java-step invocation cannot be resolved or fails validation."""


def resolve_step_command(
    args: list[str],
    steps_root: Path | str | None = None,
    classpath: str | None = None,
) -> tuple[list[str], dict[str, str], str]:
    """Resolve a `java-step <StepName> [args...]` command into an argv and isolated environment.

    Returns (argv, env, error_message). On error, argv is empty.
    """
    if not args:
        return [], {}, "java-step requires a step class name as the first argument"

    step_name = args[0]
    if not STEP_NAME_RE.match(step_name):
        return [], {}, f"Invalid java-step name format: {step_name}"

    jdk_home, why = find_jdk(min_version=17)
    if jdk_home is None:
        return [], {}, f"Cannot run java-step: {why}"

    java_bin = str(jdk_home / "bin" / "java")
    child_env = get_child_java_env(jdk_home)

    # Classpath resolution
    cp_parts: list[str] = []
    if classpath:
        cp_parts.append(classpath)
    if steps_root:
        sr = Path(steps_root).resolve()
        classes_dir = sr / "target" / "classes"
        if classes_dir.is_dir():
            cp_parts.append(str(classes_dir))
        else:
            cp_parts.append(str(sr))

    final_cp = os.pathsep.join(cp_parts) if cp_parts else "."

    argv = [
        java_bin,
        "-cp",
        final_cp,
        "procflow.steps.runtime.StepMain",
        step_name,
        *args[1:],
    ]

    return argv, child_env, ""
