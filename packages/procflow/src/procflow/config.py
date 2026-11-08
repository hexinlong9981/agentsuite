"""7-layer runtime config: merge + validation, driven by ``schema/runtime_keys.yaml``.

See that file's header for the layers. Hard caps, an L2 ``caps:`` block and a definition's
``locked:`` list can only narrow; set-valued keys can only shrink; keys may only be written
by the layers their declaration allows. Values that look like a credential, an IP address,
a hostname or a connection string are rejected and **never echoed** (messages name the key
and the reason only). This module merges, validates and
resolves (``resolve`` / ``render_table`` back ``procflow config show``).
"""

from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path

import yaml
from agent_policy_kit import SecretMasker

from . import guard
from .errors import ConfigError  # re-exported
from .repo import blob_sha

__all__ = ["ConfigError", "KeySpec", "load_keys", "specs", "match_key", "known_key", "check_value",
           "resolve", "render_table", "mask_result", "check_config_files", "check_with_config",
           "layer3_from_ir", "CALL_NARROW_KEYS"]

KEYS_PATH = Path(__file__).resolve().parent / "schema" / "runtime_keys.yaml"
LAYER_MARK = {i: f"L{i}" for i in range(1, 8)}
DEFAULT_LAYERS = [2, 3, 4, 5, 6, 7]
SEG_RE = r"[A-Za-z0-9_\-]+"
DUR_RE = re.compile(r"^(\d+)(s|m|h|d)$")
DUR_UNIT = {"s": 1, "m": 60, "h": 3600, "d": 86400}
TIMERANGE_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d-([01]\d|2[0-3]):[0-5]\d$")
UTC = _dt.timezone.utc

IPV4_RE = re.compile(r"(?<![\d.])(\d{1,3}\.){3}\d{1,3}(?![\d.])")
CONN_RE = re.compile(r"(?i)(://|^jdbc:|\bjdbc:|@[A-Za-z0-9.\-]+:\d+|\bhost\s*=|\bserver\s*=)")
FQDN_RE = re.compile(r"(?i)^(?=.*[a-z])[a-z0-9\-]+(\.[a-z0-9\-]+){2,}$|^[a-z0-9\-]+\.(com|net|org|local|internal|io|lan|corp|intra)$")
SECRETISH_RE = re.compile(r"^[A-Za-z0-9+/=_\-]{32,}$|-----BEGIN")
SECRET_WORD_RE = re.compile(r"(?i)(password|passwd|secret|token|api[_-]?key|private[_-]?key)")
_MASKER = SecretMasker()


class KeySpec:
    def __init__(self, name, d):
        self.name = name
        self.type = d["type"]
        self.default = d.get("default")
        self.min = d.get("min")
        self.cap = d.get("cap")
        self.values = d.get("values")
        self.lockable = bool(d.get("lockable"))
        self.layers = d.get("layers", DEFAULT_LAYERS)
        self.doc = d.get("doc", "")
        self.pattern = "<" in name
        self.regex = re.compile("^" + re.sub(r"<[^>]+>", SEG_RE, re.escape(name).replace(r"\<", "<").replace(r"\>", ">")) + "$")


_KEYS = None


def load_keys(path=None):
    global _KEYS
    if path is None and _KEYS is not None:
        return _KEYS
    raw = yaml.safe_load(Path(path or KEYS_PATH).read_text(encoding="utf-8"))
    keys = {name: KeySpec(name, d) for name, d in raw["keys"].items()}
    keys["__reasons__"] = raw.get("reasons", [])
    if path is None:
        _KEYS = keys
    return keys


def specs():
    return {k: v for k, v in load_keys().items() if k != "__reasons__"}


def match_key(dotted):
    sp = specs()
    if dotted in sp and not sp[dotted].pattern:
        return sp[dotted]
    for s in sp.values():
        if s.pattern and s.regex.match(dotted):
            return s
    return None


def known_key(dotted):
    return match_key(dotted) is not None


def duration_seconds(v):
    m = DUR_RE.match(v.strip()) if isinstance(v, str) else None
    return int(m.group(1)) * DUR_UNIT[m.group(2)] if m else None


def _num(spec, v):
    return duration_seconds(v) if spec.type == "duration" else v


def forbidden_reason(v):
    """Why a value must not be configured (credential-like / IP / connection string / hostname), or None.

    The value itself is never part of the result."""
    if isinstance(v, str):
        vals = [v]
    elif isinstance(v, list):
        vals = [x for x in v if isinstance(x, str)]
    elif isinstance(v, dict):
        vals = [x for x in v.values() if isinstance(x, str)] + [str(k) for k in v]
    else:
        vals = []
    for s in vals:
        if _MASKER.mask_text(s) != s or SECRET_WORD_RE.search(s) or SECRETISH_RE.search(s):
            return "credential-like value"
        if IPV4_RE.search(s):
            return "IP address"
        if CONN_RE.search(s):
            return "connection string"
        if FQDN_RE.match(s.strip()):
            return "hostname"
    return None


def check_value(spec, key, v, where):
    """Validate one value (type, range, enum, forbidden shapes). Messages never include the value."""
    errs = []
    e = errs.append
    why = forbidden_reason(v)
    if why:
        return [f"{where}: {key} must not contain a {why} (value not shown; use a logical name, never the thing itself)"]
    if v is None:
        return errs
    t = spec.type
    if t == "int":
        if isinstance(v, bool) or not isinstance(v, int):
            e(f"{where}: {key} must be an integer")
    elif t == "number":
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            e(f"{where}: {key} must be a number")
    elif t == "bool":
        if not isinstance(v, bool):
            e(f"{where}: {key} must be true or false")
    elif t == "string":
        if not isinstance(v, str) or not v.strip():
            e(f"{where}: {key} must be a non-empty string")
    elif t == "enum":
        if v not in spec.values:
            e(f"{where}: {key} must be one of {spec.values}")
    elif t == "set":
        if not isinstance(v, list) or len(set(map(str, v))) != len(v):
            e(f"{where}: {key} must be a list without duplicates")
        else:
            extra = [x for x in v if x not in (spec.default or [])]
            if extra:
                e(f"{where}: {key} cannot be widened ({extra} is outside the built-in allowed set {spec.default})")
    elif t == "names":
        if not isinstance(v, list) or any(not isinstance(x, str) or not x.strip() for x in v) or len(set(v)) != len(v):
            e(f"{where}: {key} must be a list of distinct non-empty names")
    elif t == "duration":
        if duration_seconds(v) is None:
            e(f"{where}: {key} must be <number><s|m|h|d> (e.g. 2h)")
    elif t == "timerange":
        if not isinstance(v, str) or not TIMERANGE_RE.match(v):
            e(f"{where}: {key} must be HH:MM-HH:MM")
    elif t == "path":
        if not isinstance(v, str) or not v.strip() or ".." in v.split("/"):
            e(f"{where}: {key} must be a path without '..'")
    elif t == "expr":
        try:
            guard.parse(v)
        except (guard.GuardError, TypeError) as x:
            e(f"{where}: {key} is not a readable expression: {x}")
    if errs or t not in ("int", "number", "duration"):
        return errs
    n = _num(spec, v)
    if spec.min is not None and n < _num(spec, spec.min):
        e(f"{where}: {key} {v} is below the minimum {spec.min}")
    if spec.cap is not None and n > _num(spec, spec.cap):
        e(f"{where}: {key} {v} exceeds the hard cap {spec.cap}")
    return errs


def flatten(obj, where, errs, prefix=""):
    """Nested config -> {dotted.key: value}. Unknown keys are appended to errs."""
    out = {}
    if not isinstance(obj, dict):
        errs.append(f"{where}: config must be a map")
        return out
    for k, v in obj.items():
        key = f"{prefix}.{k}" if prefix else str(k)
        spec = match_key(key)
        if spec is not None and not isinstance(v, dict):
            out[key] = v
        elif isinstance(v, dict):
            out.update(flatten(v, where, errs, key))
        else:
            errs.append(f"{where}: {key} is an unknown key (declared keys: schema/runtime_keys.yaml)")
    if not prefix:
        for key in out:
            if key.startswith("resume_when."):
                reason = key.split(".", 1)[1]
                if reason not in load_keys()["__reasons__"]:
                    errs.append(f"{where}: {key}: reason must be one of {load_keys()['__reasons__']}")
    return out


def parse_set(items, errs):
    """``--set key=value`` items -> {key: value} (values read as YAML scalars)."""
    out = {}
    for it in items or []:
        if "=" not in it:
            errs.append(f"L7 --set: expected <key>=<value>")
            continue
        k, v = it.split("=", 1)
        k = k.strip()
        try:
            val = yaml.safe_load(v) if v.strip() != "" else None
        except yaml.YAMLError:
            val = v
        if match_key(k) is None:
            errs.append(f"L7 --set: {k} is an unknown key")
            continue
        out[k] = val
    return out


def _load_yaml(path, errs, label):
    try:
        d = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as x:
        errs.append(f"{label}: cannot read: {type(x).__name__}")
        return None
    return {} if d is None else d


def read_layer2(path, errs):
    """_config/runtime.yaml -> (defaults, caps, env_blocks)."""
    if not Path(path).exists():
        return {}, {}, {}
    label = f"L2 {Path(path).name}"
    d = _load_yaml(path, errs, label)
    if d is None:
        return {}, {}, {}
    if not isinstance(d, dict):
        errs.append(f"{label}: top level must be a map of defaults / caps / env")
        return {}, {}, {}
    for k in d:
        if k not in ("defaults", "caps", "env"):
            errs.append(f"{label}: top-level {k} is unknown (allowed: defaults, caps, env)")
    defaults = flatten(d.get("defaults") or {}, f"{label} defaults", errs)
    caps = flatten(d.get("caps") or {}, f"{label} caps", errs)
    for k, v in caps.items():
        sp = match_key(k)
        if sp.type not in ("int", "number", "duration"):
            errs.append(f"{label} caps: {k} is not numeric and cannot be capped (narrow sets in defaults)")
            continue
        errs.extend(check_value(sp, k, v, f"{label} caps"))
    return defaults, caps, _env_blocks(d.get("env"), label, errs)


def _env_blocks(env, label, errs):
    out = {}
    if env is None:
        return out
    if not isinstance(env, dict):
        errs.append(f"{label}: env must be a map of <environment>: {{...}}")
        return out
    envs = match_key("env").values
    for name, blk in env.items():
        if name not in envs:
            errs.append(f"{label}: env.{name} is not a known environment ({envs})")
            continue
        flat = flatten(blk or {}, f"{label} env.{name}", errs)
        if "env" in flat:
            errs.append(f"{label}: env.{name} cannot change env itself")
            flat.pop("env")
        out[name] = flat
    return out


def read_layer4(path, sop_id, errs):
    """_config/procedures/<ID>.yaml -> (defaults, env_blocks)."""
    if not Path(path).exists():
        return {}, {}
    label = f"L4 {Path(path).name}"
    d = _load_yaml(path, errs, label)
    if d is None:
        return {}, {}
    if not isinstance(d, dict):
        errs.append(f"{label}: top level must be a map of sop / defaults / env")
        return {}, {}
    for k in d:
        if k not in ("sop", "defaults", "env"):
            errs.append(f"{label}: top-level {k} is unknown (allowed: sop, defaults, env)")
    if d.get("sop") != sop_id:
        errs.append(f"{label}: inner sop does not match the file name {sop_id}")
    return flatten(d.get("defaults") or {}, f"{label} defaults", errs), _env_blocks(d.get("env"), label, errs)


def layer3_from_ir(ir, errs):
    """L3 entries from the IR (base -> self) plus flow.execution. Returns ([(key, value, source)], locked{key: source})."""
    entries, locked = [], {}
    ex = ir.execution or {}
    if isinstance(ex.get("max_rounds"), int) and not isinstance(ex.get("max_rounds"), bool):
        entries.append(("limits.max_rounds", ex["max_rounds"], "L3 flow.execution.max_rounds"))
    tp = ex.get("timeout_policy")
    if isinstance(tp, str) and duration_seconds(tp) is not None:
        entries.append(("timeout.step", tp.strip(), "L3 flow.execution.timeout_policy"))
    for origin, rt in ir.runtime_layers or []:
        label = "L3 runtime:" if origin == ir.sop.id else f"L3 runtime of base {origin}:"
        if not isinstance(rt, dict):
            continue
        rt = dict(rt)
        lk = rt.pop("locked", [])
        for k, v in flatten(rt, label, errs).items():
            if k in locked and locked[k] != label:
                errs.append(f"{label}: {k} is locked by {locked[k]} (a child cannot change it)")
                continue
            entries.append((k, v, label))
        if not isinstance(lk, list):
            errs.append(f"{label}: locked must be a list of keys")
            continue
        for k in lk:
            sp = match_key(str(k))
            if sp is None:
                errs.append(f"{label}: locked key {k} is unknown")
            elif not sp.lockable:
                errs.append(f"{label}: {k} cannot be locked")
            else:
                locked[str(k)] = label
    return entries, locked


def resolve(ir, repo, env=None, config_file=None, sets=None, params=None, now=None, strict_inputs=False,
            call_params=None):
    """Merge L1..L7. Returns {"sop", "env", "values": {key: {value, source, layer}}, "inputs", "input_errors",
    "files", "locked", "caps"}. Configuration errors raise ConfigError (code 1); input errors are
    collected (and raise code 2 when strict_inputs). ``call_params`` are the typed values a parent's ``call`` passes
    (``with``); they are inputs only, so no run-level layer of the parent reaches the child."""
    errs = []
    sp_all = specs()
    sop_id = ir.sop.id
    if ir.sop.abstract:
        raise ConfigError([f"{sop_id} is abstract (or has abstract steps left) and cannot be run, so config is not resolved"])
    files = []

    def note_file(layer, path):
        p = Path(path)
        if p.exists():
            files.append({"layer": LAYER_MARK[layer], "path": str(p), "blob_sha": blob_sha(p.read_bytes())})

    l2_def, l2_caps, l2_env = read_layer2(repo.runtime_path, errs)
    note_file(2, repo.runtime_path)
    l3, locked = layer3_from_ir(ir, errs)
    l4_path = repo.sop_config_path(sop_id)
    l4_def, l4_env = read_layer4(l4_path, sop_id, errs)
    note_file(4, l4_path)
    l6, l6_params = {}, {}
    if config_file:
        label6 = f"L6 {Path(config_file).name}"
        d6 = _load_yaml(config_file, errs, label6)
        if d6 is not None:
            if not isinstance(d6, dict):
                errs.append(f"{label6}: top level must be a map")
            else:
                d6 = dict(d6)
                l6_params = d6.pop("params", None) or {}
                if not isinstance(l6_params, dict):
                    errs.append(f"{label6}: params must be a map of name -> value")
                    l6_params = {}
                l6 = flatten(d6, label6, errs)
        note_file(6, config_file)
    l7 = parse_set(sets, errs)

    env_src = None
    if env is not None:
        env_src = "--env"
    else:
        for src, layer in (("L7 --set", l7), ("L6", l6), ("L4", l4_def), ("L2", l2_def)):
            if layer.get("env") is not None:
                env, env_src = layer["env"], src
                break
        else:
            for k, v, s in reversed(l3):
                if k == "env":
                    env, env_src = v, s
                    break
    if env is not None and env not in sp_all["env"].values:
        errs.append(f"env is not one of {sp_all['env'].values}")
    if env is not None and env_src == "--env":
        l7.setdefault("env", env)

    seq = [(2, "L2 runtime.yaml", l2_def)]
    seq += [(3, s, {k: v}) for k, v, s in l3]
    seq.append((4, f"L4 procedures/{sop_id}.yaml", l4_def))
    if env is not None:
        seq.append((5, f"L5 L2 env.{env}", l2_env.get(env, {})))
        seq.append((5, f"L5 L4 env.{env}", l4_env.get(env, {})))
    seq.append((6, "L6 --config" if config_file else "L6", l6))
    seq.append((7, "L7 --set", l7))
    extra_env = [(5, f"L5 L2 env.{n}", b) for n, b in l2_env.items() if n != env]
    extra_env += [(5, f"L5 L4 env.{n}", b) for n, b in l4_env.items() if n != env]

    caps = dict(l2_caps)
    values = {name: {"value": s.default, "source": "L1 default", "layer": 1}
              for name, s in sp_all.items() if not s.pattern}
    layer3_values = {}
    for layer, source, kv in seq + extra_env:
        apply = (layer, source, kv) in seq
        for k, v in kv.items():
            s = match_key(k)
            if s is None:
                continue
            if layer not in s.layers:
                errs.append(f"{source}: {k} cannot be written in this layer (allowed: {', '.join(LAYER_MARK[x] for x in s.layers)})")
                continue
            errs.extend(check_value(s, k, v, source))
            if k in caps and s.type in ("int", "number", "duration") and v is not None \
                    and _num(s, v) is not None and _num(s, caps[k]) is not None and _num(s, v) > _num(s, caps[k]):
                errs.append(f"{source}: {k} {v} exceeds the L2 cap {caps[k]} (cannot be widened)")
            if layer == 3:
                layer3_values[k] = v
            if layer >= 4 and k in locked and v != layer3_values.get(k, values.get(k, {}).get("value")):
                errs.append(f"{source}: {k} is locked by {locked[k]} (L4..L7 cannot change it)")
                continue
            if apply:
                values[k] = {"value": v, "source": source, "layer": layer}
    if errs:
        raise ConfigError(errs, 1)

    inputs, ierrs = resolve_inputs(ir, values, l6_params, params, now, call_params)
    res = {"sop": sop_id, "env": values.get("env", {}).get("value"), "values": dict(sorted(values.items())),
           "pattern_keys": sorted(n for n, s in sp_all.items() if s.pattern),
           "inputs": inputs, "input_errors": ierrs, "files": files, "locked": sorted(locked), "caps": caps}
    if ierrs and strict_inputs:
        raise ConfigError(ierrs, 2)
    return res


def _coerce_param(raw, typ):
    if isinstance(raw, str) and typ not in ("string", "path", "commit_ref", "date", "datetime", "any"):
        try:
            return yaml.safe_load(raw)
        except yaml.YAMLError:
            return raw
    return raw


def resolve_inputs(ir, values, l6_params, cli_params, now=None, call_params=None):
    from .defschema import value_matches
    errs = []
    specs_in = ir.interface.inputs or {}
    given = {n: (v, "L6 params") for n, v in (l6_params or {}).items()}
    for it in cli_params or []:
        if "=" not in it:
            errs.append("L7 --param: expected <name>=<value>")
            continue
        n, v = it.split("=", 1)
        given[n.strip()] = (v, "L7 --param")
    for n, v in (call_params or {}).items():
        given[n] = (v, "call with")
    for n in given:
        if n not in specs_in:
            errs.append(f"input {n} is not in {ir.sop.id}'s interface.inputs (available: {sorted(specs_in)})")
    now = now or _dt.datetime.now(UTC)
    out = {}
    for name, sp in specs_in.items():
        typ = sp.get("type", "any")
        if name in given:
            raw, src = given[name]
            val = raw if src == "call with" else _coerce_param(raw, typ)
            if not value_matches(val, typ):
                errs.append(f"input {name}: value does not fit type {typ}")
                continue
            out[name] = {"value": val, "source": f"given ({src})"}
        elif "default" in sp:
            try:
                kind, arg = guard.parse_default(sp["default"])
            except guard.GuardError as x:
                errs.append(f"input {name}: {x}")
                continue
            if kind == "static":
                val = arg
            elif kind == "config":
                if arg not in values:
                    errs.append(f"input {name}: default references config key {arg} which is not resolved")
                    continue
                val = values[arg]["value"]
            elif kind == "today":
                val = guard.format_today(arg, now)
            elif kind == "now":
                val = now.isoformat(timespec="seconds")
            elif kind == "env":
                val = values.get("env", {}).get("value")
            else:
                val = "<decided when the run starts>"
            out[name] = {"value": val, "source": f"default {sp['default']}"}
        elif sp.get("required"):
            errs.append(f"required input {name} is missing (give --param {name}=... or params: in the L6 file)")
            out[name] = {"value": None, "source": "missing (required)"}
        else:
            out[name] = {"value": None, "source": "absent"}
    return out, errs


# ---- display (secrets masked) ---------------------------------------------------
def mask_result(res):
    """Copy of a resolve() result with every value passed through SecretMasker (config echoes)."""
    out = dict(res)
    out["values"] = {k: {**x, "value": _MASKER.mask_obj(x["value"])} for k, x in res["values"].items()}
    out["inputs"] = {k: {**x, "value": _MASKER.mask_obj(x["value"])} for k, x in res["inputs"].items()}
    return out


def _fmt(v):
    if v is None:
        return "(none)"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (list, dict)):
        return yaml.safe_dump(v, default_flow_style=True, width=1000).strip()
    return str(v)


def render_table(res):
    res = mask_result(res)
    rows = [("key", "value", "source")]
    for k, x in res["values"].items():
        rows.append((k + ("  [locked]" if k in res["locked"] else ""), _fmt(x["value"]), x["source"]))
    sp_all = specs()
    for k in res["pattern_keys"]:
        if not any(match_key(n) is sp_all[k] for n in res["values"]):
            rows.append((k, "(none)", "L1 default"))
    for n, x in res["inputs"].items():
        rows.append((f"input {n}", _fmt(x["value"]), x["source"]))
    w0, w1 = max(len(r[0]) for r in rows), max(len(r[1]) for r in rows)
    lines = [f"# resolved config for {res['sop']} (env={_fmt(res['env'])}; no run was created)"]
    for r in rows:
        lines.append(r[0].ljust(w0) + "  " + r[1].ljust(w1) + "  " + r[2])
    if res["files"]:
        lines += ["", "# layer files (git blob sha)"]
        lines += [f"{f['layer']} {f['path']}  {f['blob_sha']}" for f in res["files"]]
    return "\n".join(lines)


def check_config_files(repo, compile_fn):
    """Validate _config/runtime.yaml and _config/procedures/*.yaml. ``compile_fn(id)`` -> (IR|None, errs)."""
    out = []
    errs = []
    read_layer2(repo.runtime_path, errs)
    out.append((str(repo.runtime_path), errs))
    for p in repo.sop_config_paths():
        sid = p.stem
        e = []
        if repo.path(sid) is None:
            e.append(f"no procedure {sid} (the file name must be <procedure-id>.yaml)")
            out.append((str(p), e))
            continue
        ir, cerrs = compile_fn(sid)
        if ir is None:
            e.append(f"{sid} does not compile, so its config cannot be checked: {'; '.join(cerrs[:3])}")
        elif ir.sop.abstract:
            e.append(f"{sid} is abstract; per-procedure config is only for runnable procedures")
        else:
            try:
                resolve(ir, repo)
            except ConfigError as x:
                e.extend(x.errors if x.code == 1 else [])
        out.append((str(p), e))
    return out


# ---- call narrowing (static) -----------------------------------------------------
CALL_CLAMP_KEYS = ("limits.max_rounds", "limits.max_depth", "limits.max_iterations", "limits.parallel",
                   "retry.code", "retry.llm", "timeout.step", "human_wait.timeout")
CALL_BUDGET_KEYS = ("budget.max_calls_per_run",)
CALL_SET_KEYS = ("step_kinds.allowed",)
CALL_NARROW_KEYS = CALL_CLAMP_KEYS + CALL_BUDGET_KEYS + CALL_SET_KEYS


def check_with_config(where, wc):
    """A call's ``with_config`` may only carry keys that narrow the child's budget."""
    errs = []
    for k, v in (wc or {}).items():
        if k not in CALL_NARROW_KEYS:
            errs.append(f"{where}.with_config: {k} cannot be passed to a child (only narrowing keys {list(CALL_NARROW_KEYS)})")
            continue
        errs.extend(check_value(match_key(k), k, v, f"{where}.with_config"))
    return errs


# ---- call narrowing (runtime) ----------------------------------------------------
def _cmp_val(key, v):
    s = match_key(key)
    if s is not None and s.type == "duration":
        return duration_seconds(v)
    return v


def _set_value(values, key, new, why):
    old = values.get(key) or {}
    values[key] = {"value": new, "source": f"{old.get('source', 'L1 default')}; narrowed ({why})",
                   "layer": old.get("layer", 1), "narrowed": True}


def _narrow_one(values, parent_value, key, limit, why, notes):
    """Bring ``values[key]`` down to ``limit`` (never up). Records one note when it changed."""
    cur = (values.get(key) or {}).get("value")
    s = match_key(key)
    if s is not None and s.type == "set":
        want = list(limit) if isinstance(limit, (list, tuple, set)) else []
        base = list(cur) if isinstance(cur, (list, tuple)) else list(s.default or [])
        new = [x for x in base if x in want]
        if new != base:
            _set_value(values, key, new, why)
            notes.append({"key": key, "from": base, "to": new, "by": why})
        return
    a, b = _cmp_val(key, cur), _cmp_val(key, limit)
    if b is None:
        return
    if a is None or a > b:
        _set_value(values, key, limit, why)
        notes.append({"key": key, "from": cur, "to": limit, "by": why})


def _names_narrow(values, key, parent_value, why, notes):
    """``names`` keys (null = everything): intersect with the parent's list when the parent restricts it."""
    if parent_value is None:
        return
    cur = (values.get(key) or {}).get("value")
    new = list(parent_value) if cur is None else [x for x in cur if x in parent_value]
    if cur is None or new != list(cur):
        _set_value(values, key, new, why)
        notes.append({"key": key, "from": cur, "to": new, "by": why})


def narrow_for_child(child_values, parent_values, used=None, with_config=None, step=None):
    """Narrow a child's resolved values (``{key: {value, source, layer}}``) in place. A child never widens its parent.

    * limits / retry / timeout / human-wait keys are rounded DOWN to the parent's resolved values;
    * ``budget.max_calls_per_run`` / ``budget.max_cost_per_run`` are capped to what the parent has left (``used`` =
      ``{"llm.max_calls_per_run": n, ...}`` is read as ``{"calls": n, "cost": x}``);
    * ``step_kinds.allowed``, ``ai.allowed`` and ``ai.unattended_allowed`` are intersected with the parent's, and
      ``run.unattended`` stays on once it is on;
    * the call's ``with_config`` is then applied the same way (a value that would widen is ignored, and noted).

    Returns the list of notes ``{"key", "from", "to", "by"}`` (``by`` = ``parent`` or ``with_config:<step>``).
    """
    notes = []
    pv = {k: (v or {}).get("value") for k, v in (parent_values or {}).items()}
    used = used or {}
    for key in CALL_CLAMP_KEYS:
        if pv.get(key) is not None:
            _narrow_one(child_values, pv, key, pv[key], "parent", notes)
    if pv.get("budget.max_calls_per_run") is not None:
        left = max(0, int(pv["budget.max_calls_per_run"]) - int(used.get("calls") or 0))
        _narrow_one(child_values, pv, "budget.max_calls_per_run", left, "parent", notes)
    if pv.get("budget.max_cost_per_run") is not None:
        left = max(0.0, float(pv["budget.max_cost_per_run"]) - float(used.get("cost") or 0))
        _narrow_one(child_values, pv, "budget.max_cost_per_run", left, "parent", notes)
    if pv.get("step_kinds.allowed") is not None:
        _narrow_one(child_values, pv, "step_kinds.allowed", pv["step_kinds.allowed"], "parent", notes)
    for key in ("ai.allowed", "ai.unattended_allowed"):
        _names_narrow(child_values, key, pv.get(key), "parent", notes)
    if pv.get("run.unattended") is True and (child_values.get("run.unattended") or {}).get("value") is not True:
        _set_value(child_values, "run.unattended", True, "parent")
        notes.append({"key": "run.unattended", "from": False, "to": True, "by": "parent"})
    for key, val in (with_config or {}).items():
        if key not in CALL_NARROW_KEYS:
            notes.append({"key": key, "from": None, "to": None, "by": f"with_config:{step}", "ignored": "not a narrowing key"})
            continue
        before = len(notes)
        if key == "budget.max_calls_per_run" or key == "budget.max_cost_per_run" or key in CALL_CLAMP_KEYS \
                or key in CALL_SET_KEYS:
            _narrow_one(child_values, pv, key, val, f"with_config:{step}", notes)
        if len(notes) == before:
            notes.append({"key": key, "from": (child_values.get(key) or {}).get("value"), "to": None,
                          "by": f"with_config:{step}", "ignored": "would not narrow"})
    return notes
