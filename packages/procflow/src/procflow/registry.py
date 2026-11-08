"""Generic registries for llm / agent targets (loaded from the config dir, pinned into every run).

Files under ``<config dir>`` (all optional; a missing file means "nothing registered")::

    data_classes.yaml   default: <class>; classes: {<name>: {local_only: bool, never: bool}}
    llm_services.yaml   services: {<name>: <entry>}
    agents.yaml         agents:   {<name>: <entry>}
    llm_routing.yaml    needs: {<capability>: <spec>}; llm: {<task_class>: {<low|medium|high>: <spec>}};
                        agents: {<task_class>: {<difficulty>: <spec>}}

An *entry*: ``provider`` (offload | session | disabled; ``fake`` only when ``PROCFLOW_ALLOW_FAKE_PROVIDERS=1``),
``family`` (local | remote | session), ``enabled``, ``disabled_reason``, ``data_classes``, ``models``,
``default_model``, ``capabilities``, ``classes`` (free tags used by ``class=<tag>``), ``cost_per_call``,
``params`` (parameter names the target honours) and, for ``fake`` only, ``script``.
A *spec*: ``{service|agent: <name>, model, class, requires, fallback: [<name>[/<model>]], params}``.

Names are logical. A value that looks like a credential, an IP address, a hostname or a connection string is
rejected (the value is never echoed). A built-in ``session`` target (queue a work ticket for a worker session)
always exists for both kinds and is the last-resort default. A class marked ``local_only`` is only ever served
by a ``family: local`` target; a class marked ``never`` is served by nothing.
"""

from __future__ import annotations

import copy
import os
from pathlib import Path

import yaml

from . import config as cfg
from .errors import ProcflowError

ENV_ALLOW_FAKE = "PROCFLOW_ALLOW_FAKE_PROVIDERS"
PROVIDERS = ("offload", "session", "disabled", "fake")
FAMILIES = ("local", "remote", "session")
DIFFICULTIES = ("low", "medium", "high")
BUILTIN_TARGET = "session"
ENTRY_KEYS = ["provider", "family", "enabled", "disabled_reason", "data_classes", "models", "default_model",
              "capabilities", "classes", "cost_per_call", "params", "script", "description"]
SPEC_KEYS = ["service", "agent", "model", "class", "requires", "fallback", "params"]
FILES = {"classes": "data_classes.yaml", "services": "llm_services.yaml", "agents": "agents.yaml",
         "routing": "llm_routing.yaml"}
DEFAULT_CLASSES = {"public": {}, "internal": {}}
DEFAULT_CLASS = "internal"


class RegistryError(ProcflowError):
    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors[:5]))


def fake_allowed():
    return os.environ.get(ENV_ALLOW_FAKE) == "1"


def parse_target(text, kind=None):
    """``"name"`` / ``"name/model"`` / ``"llm:name/model"`` -> (kind|None, name, model|None)."""
    s = str(text).strip()
    k = None
    if ":" in s and s.split(":", 1)[0] in ("llm", "agent"):
        k, s = s.split(":", 1)
    name, _, model = s.partition("/")
    return k or kind, name.strip(), model.strip() or None


def target_id(kind, name, model=None):
    return f"{kind}:{name}" + (f"/{model}" if model else "")


class Registry:
    def __init__(self, classes=None, default_class=DEFAULT_CLASS, services=None, agents=None, routing=None,
                 configured=False):
        self.classes = classes if classes is not None else {k: dict(v) for k, v in DEFAULT_CLASSES.items()}
        self.default_class = default_class
        self.services = services or {}
        self.agents = agents or {}
        self.routing = routing or {"needs": {}, "llm": {}, "agents": {}}
        self.configured = configured
        self._builtin()

    def _builtin(self):
        open_classes = [c for c, v in self.classes.items() if not v.get("local_only") and not v.get("never")]
        for table in (self.services, self.agents):
            table[BUILTIN_TARGET] = {"provider": "session", "family": "session", "enabled": True,
                                     "disabled_reason": None, "data_classes": list(open_classes), "models": [],
                                     "default_model": None, "capabilities": ["*"], "classes": [],
                                     "cost_per_call": 0, "params": [], "builtin": True}

    # ---- lookups ---------------------------------------------------------------------------
    def entry(self, kind, name):
        return (self.services if kind == "llm" else self.agents).get(name)

    def is_local_only(self, data_class):
        return bool((self.classes.get(data_class) or {}).get("local_only"))

    def is_never(self, data_class):
        return bool((self.classes.get(data_class) or {}).get("never"))

    # ---- (de)serialisation (the pinned snapshot) ----------------------------------------------
    def to_dict(self):
        return {"version": 1, "configured": self.configured, "default_class": self.default_class,
                "classes": copy.deepcopy(self.classes),
                "services": {k: v for k, v in copy.deepcopy(self.services).items() if not v.get("builtin")},
                "agents": {k: v for k, v in copy.deepcopy(self.agents).items() if not v.get("builtin")},
                "routing": copy.deepcopy(self.routing)}

    @classmethod
    def from_dict(cls, d):
        return cls(classes=d.get("classes"), default_class=d.get("default_class") or DEFAULT_CLASS,
                   services=copy.deepcopy(d.get("services") or {}), agents=copy.deepcopy(d.get("agents") or {}),
                   routing=copy.deepcopy(d.get("routing") or {"needs": {}, "llm": {}, "agents": {}}),
                   configured=bool(d.get("configured")))


# ======================================================================================
# loading and validation
# ======================================================================================
def _read(path, errs):
    p = Path(path)
    if not p.exists():
        return None
    try:
        d = yaml.safe_load(p.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as x:
        errs.append(f"{p.name}: cannot read ({type(x).__name__})")
        return None
    if d is None:
        return {}
    if not isinstance(d, dict):
        errs.append(f"{p.name}: the top level must be a map")
        return None
    return d


def _scan_values(where, obj, errs):
    """Credential-like / IP / hostname / connection-string values are refused (never echoed)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            _scan_values(f"{where}.{k}", v, errs)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _scan_values(f"{where}[{i}]", v, errs)
    elif isinstance(obj, str):
        why = cfg.forbidden_reason(obj)
        if why:
            errs.append(f"{where}: must not contain a {why} (value not shown; use logical names only)")


def _str_list(where, v, errs):
    if v is None:
        return []
    if not isinstance(v, list) or any(not isinstance(x, str) or not x for x in v):
        errs.append(f"{where}: must be a list of non-empty strings")
        return []
    return list(v)


def _check_entry(kind, name, e, classes, errs):
    where = f"{'llm_services' if kind == 'llm' else 'agents'}.{name}"
    if not isinstance(e, dict):
        errs.append(f"{where}: an entry must be a map")
        return None
    for k in e:
        if k not in ENTRY_KEYS:
            errs.append(f"{where}: unknown key {k}")
    _scan_values(where, {k: v for k, v in e.items() if k != "script"}, errs)
    if name == BUILTIN_TARGET:
        errs.append(f"{where}: the name {BUILTIN_TARGET!r} is reserved for the built-in session target")
    prov = e.get("provider")
    if prov not in PROVIDERS:
        errs.append(f"{where}.provider must be one of {list(PROVIDERS)}")
    if prov == "fake" and not fake_allowed():
        errs.append(f"{where}: provider fake is only allowed when {ENV_ALLOW_FAKE}=1 (tests); it cannot be registered in real config")
    if "script" in e and prov != "fake":
        errs.append(f"{where}: script is only valid for provider fake")
    fam = e.get("family", "session" if prov == "session" else "remote")
    if fam not in FAMILIES:
        errs.append(f"{where}.family must be one of {list(FAMILIES)}")
    enabled = e.get("enabled", True)
    if not isinstance(enabled, bool):
        errs.append(f"{where}.enabled must be true or false")
    dcs = _str_list(f"{where}.data_classes", e.get("data_classes"), errs)
    for c in dcs:
        if c not in classes:
            errs.append(f"{where}.data_classes: unknown class {c!r}")
        elif (classes[c] or {}).get("never"):
            errs.append(f"{where}.data_classes: class {c!r} is never routed anywhere")
        elif (classes[c] or {}).get("local_only") and fam != "local":
            errs.append(f"{where}.data_classes: class {c!r} is local-only; only a family: local target may accept it")
    cost = e.get("cost_per_call", 0)
    if isinstance(cost, bool) or not isinstance(cost, (int, float)) or cost < 0:
        errs.append(f"{where}.cost_per_call must be a number >= 0")
        cost = 0
    models = _str_list(f"{where}.models", e.get("models"), errs)
    dm = e.get("default_model")
    if dm is not None and (not isinstance(dm, str) or (models and dm not in models)):
        errs.append(f"{where}.default_model must be one of models")
    return {"provider": prov, "family": fam, "enabled": bool(enabled) and prov != "disabled",
            "disabled_reason": e.get("disabled_reason") or ("provider disabled" if prov == "disabled" else None),
            "data_classes": dcs, "models": models, "default_model": dm,
            "capabilities": _str_list(f"{where}.capabilities", e.get("capabilities"), errs),
            "classes": _str_list(f"{where}.classes", e.get("classes"), errs), "cost_per_call": cost,
            "params": _str_list(f"{where}.params", e.get("params"), errs),
            **({"script": e["script"]} if "script" in e else {})}


def _check_spec(where, spec, kind, errs):
    if isinstance(spec, str):
        return
    if not isinstance(spec, dict):
        errs.append(f"{where}: a target spec must be a map or a string")
        return
    for k in spec:
        if k not in SPEC_KEYS:
            errs.append(f"{where}: unknown key {k}")
    key = "service" if kind == "llm" else "agent"
    for other in ({"service", "agent"} - {key}):
        if other in spec:
            errs.append(f"{where}: use {key}: for this kind, not {other}:")
    _str_list(f"{where}.fallback", spec.get("fallback"), errs)
    _str_list(f"{where}.requires", spec.get("requires"), errs)


def _check_routing(d, errs):
    out = {"needs": {}, "llm": {}, "agents": {}}
    for k in d:
        if k not in ("needs", "llm", "agents"):
            errs.append(f"llm_routing.{k}: unknown section (needs / llm / agents)")
    for cap, spec in (d.get("needs") or {}).items():
        _check_spec(f"llm_routing.needs.{cap}", spec, "agent", errs)
        out["needs"][str(cap)] = spec
    for sect, kind in (("llm", "llm"), ("agents", "agent")):
        for tc, by in (d.get(sect) or {}).items():
            if not isinstance(by, dict):
                errs.append(f"llm_routing.{sect}.{tc}: must map difficulty -> spec")
                continue
            for df, spec in by.items():
                if df not in DIFFICULTIES:
                    errs.append(f"llm_routing.{sect}.{tc}.{df}: difficulty must be one of {list(DIFFICULTIES)}")
                    continue
                _check_spec(f"llm_routing.{sect}.{tc}.{df}", spec, kind, errs)
            out[sect][str(tc)] = by
    _scan_values("llm_routing", d, errs)
    return out


def load(config_dir):
    """Read the registry files. Returns ``(Registry, errors)``; a registry with errors must not be used."""
    errs = []
    base = Path(config_dir) if config_dir else None
    raw = {}
    if base is not None:
        for key, fname in FILES.items():
            raw[key] = _read(base / fname, errs)
    classes, default = {k: dict(v) for k, v in DEFAULT_CLASSES.items()}, DEFAULT_CLASS
    dc = raw.get("classes")
    configured = any(v is not None for v in raw.values())
    if dc is not None:
        for k in dc:
            if k not in ("default", "classes"):
                errs.append(f"data_classes.{k}: unknown key (default / classes)")
        classes = {}
        for name, v in (dc.get("classes") or {}).items():
            v = v or {}
            if not isinstance(v, dict) or any(x not in ("local_only", "never", "description") for x in v):
                errs.append(f"data_classes.classes.{name}: keys allowed are local_only / never / description")
                v = {}
            if v.get("local_only") and v.get("never"):
                errs.append(f"data_classes.classes.{name}: local_only and never cannot both be set")
            classes[str(name)] = {k: bool(x) for k, x in v.items() if k in ("local_only", "never")}
        if not classes:
            errs.append("data_classes.classes: at least one class is required")
        default = dc.get("default") or (next(iter(classes), DEFAULT_CLASS))
        if default not in classes:
            errs.append("data_classes.default: not one of the classes")
        elif classes[default].get("never"):
            errs.append("data_classes.default: the default class cannot be a never class")
        _scan_values("data_classes", dc, errs)
    services, agents = {}, {}
    for key, kind, table in (("services", "llm", services), ("agents", "agent", agents)):
        d = raw.get(key)
        if d is None:
            continue
        for k in d:
            if k != key:
                errs.append(f"{FILES[key]}: unknown top-level key {k} (expected {key})")
        ents = d.get(key) or {}
        if not isinstance(ents, dict):
            errs.append(f"{FILES[key]}: {key} must be a map name -> entry")
            continue
        for name, e in ents.items():
            norm = _check_entry(kind, str(name), e, classes, errs)
            if norm is not None:
                if not norm["data_classes"]:
                    norm["data_classes"] = [c for c, v in classes.items() if not v.get("local_only") and not v.get("never")] \
                        if norm["family"] != "local" else [c for c, v in classes.items() if not v.get("never")]
                table[str(name)] = norm
    routing = _check_routing(raw["routing"], errs) if raw.get("routing") else None
    reg = Registry(classes, default, services, agents, routing, configured=configured)
    _check_refs(reg, errs)
    return reg, errs


def _spec_names(spec, kind):
    if isinstance(spec, str):
        return [parse_target(x, kind)[1] for x in spec.split(",") if x.strip() and not x.strip().startswith("class=")]
    names = []
    key = "service" if kind == "llm" else "agent"
    if spec.get(key):
        names.append(parse_target(spec[key], kind)[1])
    names += [parse_target(x, kind)[1] for x in spec.get("fallback") or []]
    return names


def _check_refs(reg, errs):
    """Every name a routing spec mentions must be registered (a typo would silently route nowhere)."""
    rt = reg.routing
    specs = [("needs", c, s, "agent") for c, s in (rt.get("needs") or {}).items()]
    for sect, kind in (("llm", "llm"), ("agents", "agent")):
        for tc, by in (rt.get(sect) or {}).items():
            specs += [(f"{sect}.{tc}", df, s, kind) for df, s in by.items()]
    for sect, key, spec, kind in specs:
        for n in _spec_names(spec, kind):
            if reg.entry(kind, n) is None:
                errs.append(f"llm_routing.{sect}.{key}: {kind} {n!r} is not registered")


def load_checked(config_dir):
    reg, errs = load(config_dir)
    if errs:
        raise RegistryError(errs)
    return reg


def pin(rd, reg):
    """Write ``registry.snapshot.json`` into the run directory (once, at run start)."""
    from . import records as R
    R.write_json(rd.file("registry.snapshot.json"), reg.to_dict())


def from_run(rd):
    """The pinned registry of a run (built-ins only when the run predates registries)."""
    from . import records as R
    d = R.read_json(rd.file("registry.snapshot.json"))
    return Registry.from_dict(d) if d else Registry()
