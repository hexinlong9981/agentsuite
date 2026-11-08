"""Java runtime and build environment detection for procflow.

Provides non-invasive detection of JDK and Maven environments through environment
variables (PROCFLOW_JAVA_HOME, PROCFLOW_MVN) or system defaults without modifying
the host's global environment or paths.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path


DEFAULT_JDK_PATHS = (
    Path("/opt/java/jdk-17"),
    Path("/usr/lib/jvm/java-17-openjdk"),
    Path("/usr/lib/jvm/java-17"),
    Path("/usr/lib/jvm/default-java"),
)

DEFAULT_MVN_PATHS = (
    Path("/opt/maven/bin/mvn"),
    Path("/usr/local/bin/mvn"),
    Path("/usr/bin/mvn"),
)


class JavaEnvError(Exception):
    """Raised when Java or Maven environment requirements are not satisfied."""


def find_jdk(min_version: int = 17) -> tuple[Path | None, str]:
    """Locate an acceptable JDK installation (min_version or higher).

    Returns (jdk_home_path | None, reason_or_error_message).
    """
    env_home = os.environ.get("PROCFLOW_JAVA_HOME") or os.environ.get("JAVA_HOME")
    candidates: list[Path] = []
    if env_home:
        candidates.append(Path(env_home))
    candidates.extend(DEFAULT_JDK_PATHS)

    checked: list[str] = []
    for home in candidates:
        java_bin = home / "bin" / "java"
        if not java_bin.is_file():
            checked.append(f"{home} (no bin/java)")
            continue
        try:
            res = subprocess.run(
                [str(java_bin), "-version"],
                capture_output=True,
                timeout=10,
                check=False,
            )
            raw = (res.stderr or b"").decode("utf-8", "replace") + (res.stdout or b"").decode("utf-8", "replace")
            # e.g. openjdk version "17.0.2" or version "21.0.1"
            m = re.search(r'version\s+"(\d+)', raw)
            if not m:
                # Older format e.g. "1.8.0"
                m = re.search(r'version\s+"1\.(\d+)', raw)
            if m:
                ver = int(m.group(1))
                if ver >= min_version:
                    return home, ""
                checked.append(f"{home} (version {ver} < {min_version})")
            else:
                checked.append(f"{home} (could not parse version)")
        except Exception as e:
            checked.append(f"{home} (exec failed: {e})")

    # Also check if 'java' on system PATH meets the requirement
    which_java = shutil.which("java")
    if which_java:
        try:
            res = subprocess.run([which_java, "-version"], capture_output=True, timeout=10, check=False)
            raw = (res.stderr or b"").decode("utf-8", "replace")
            m = re.search(r'version\s+"(\d+)', raw)
            if m and int(m.group(1)) >= min_version:
                # infer parent home
                j_path = Path(which_java).resolve()
                if j_path.parent.name == "bin":
                    return j_path.parent.parent, ""
        except Exception:
            pass

    return None, f"JDK {min_version}+ not found. Checked: {', '.join(checked)}"


def find_maven() -> Path | None:
    """Locate the Maven executable."""
    env_mvn = os.environ.get("PROCFLOW_MVN")
    if env_mvn and Path(env_mvn).is_file():
        return Path(env_mvn)

    for p in DEFAULT_MVN_PATHS:
        if p.is_file():
            return p

    w = shutil.which("mvn")
    return Path(w) if w else None


def get_child_java_env(jdk_home: Path) -> dict[str, str]:
    """Construct an isolated environment dictionary for child processes using this JDK."""
    env = dict(os.environ)
    env["JAVA_HOME"] = str(jdk_home)
    bin_dir = str(jdk_home / "bin")
    current_path = env.get("PATH", "")
    if not current_path.startswith(bin_dir):
        env["PATH"] = f"{bin_dir}{os.pathsep}{current_path}"
    return env
