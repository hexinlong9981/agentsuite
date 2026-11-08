"""Interface contracts: load ``<IF-ID>.yaml`` files and validate them."""

from __future__ import annotations

from pathlib import Path

import yaml

from .defschema import check_interface_file, valid_type  # noqa: F401  (valid_type re-exported: the type system)
from .errors import DefinitionError
from .model import InterfaceSpec

__all__ = ["load_interface", "load_interfaces", "validate_interface", "InterfaceSpec", "valid_type"]


def load_interface(path) -> dict:
    p = Path(path)
    try:
        d = yaml.safe_load(p.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as x:
        raise DefinitionError(f"{p.name}: cannot read: {x}") from x
    if not isinstance(d, dict):
        raise DefinitionError(f"{p.name}: the interface is not a map")
    return d


def validate_interface(d, filename=None) -> list:
    return check_interface_file(d, filename)


def load_interfaces(directory):
    """Load every ``IF-*.yaml`` in ``directory``. Returns ({id: InterfaceSpec}, [(file, [errors])])."""
    out, findings = {}, []
    d = Path(directory)
    if not d.is_dir():
        return out, findings
    for p in sorted(d.glob("IF-*.yaml")):
        try:
            raw = load_interface(p)
        except DefinitionError as x:
            findings.append((str(p), [str(x)]))
            continue
        errs = validate_interface(raw, p.name)
        if errs:
            findings.append((str(p), errs))
        out[p.stem] = InterfaceSpec.from_dict(raw)
    return out, findings
