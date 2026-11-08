"""The closed key set and value rules for a procedure definition (YAML).

Every key is optional unless stated; a definition without extension keys has the
plain meaning (nodes == steps, transitions 1:1). **Unknown keys are rejected** so a
typo is never silently ignored. Only the contents of ``flow`` are open.

Value rules that need other procedures (inheritance, LSP, calls, ...) live in
``check.py``; this module validates one definition in isolation.
"""

from __future__ import annotations

import re

from . import guard

# ---- key sets --------------------------------------------------------------------
TOP_BASE = ["sop", "flow", "steps", "transitions"]
TOP_EXT = ["interface", "runtime", "context", "finally"]
SOP_BASE = ["id", "name", "version", "description", "purpose", "scope", "roles", "human_only",
            "references", "frequency", "trigger"]
SOP_EXT = ["extends", "abstract", "implements"]
STEP_COMMON = ["id", "type", "name"]
STEP_BASE = {
    "start": ["condition"],
    "end": ["outcome"],
    "task": ["actor", "mode", "inputs", "action", "tools", "outputs", "completion", "evidence",
             "constraints", "description"],
    "exclusive_choice": [],
    "inclusive_choice": [],
    "parallel_split": ["branches"],
    "join": ["mode", "wait_for"],
    "loop": [],
    "call": [],
}
TASK_EXT = ["kind", "visibility", "abstract", "hook", "final", "override", "super", "raises",
            "retry", "io", "impl", "llm", "agent"]
STEP_EXT = {
    "task": TASK_EXT,
    "exclusive_choice": ["decide_by", "final"],
    "inclusive_choice": ["decide_by", "final"],
    "end": ["final"],
    "loop": ["foreach", "while", "as", "max_iterations", "body", "on_limit", "final"],
    "call": ["sop", "interface", "dispatch", "with", "returns", "max_depth", "allow_recursion",
             "catch", "reason", "with_config", "final"],
}
TR_BASE = ["id", "from", "to", "data", "when", "default", "on_take"]
TR_EXT = ["guard", "final"]

# ---- value rules -----------------------------------------------------------------
KINDS = ["code", "llm", "agent", "human"]
MODES = ["manual", "auto", "agent"]
# mode -> kinds that may be written explicitly; mode -> derived kind
MODE_KINDS = {"manual": {"human"}, "auto": {"code"}, "agent": {"agent", "llm"}}
MODE_DEFAULT_KIND = {"manual": "human", "auto": "code", "agent": "agent"}
VISIBILITY = ["protected", "private"]  # "public" belongs to the procedure itself, never a step
SUPER = ["before", "after"]
BUILTIN_EXCEPTIONS = ["SystemError", "NoImplementation", "LimitReached"]
EXC_RE = re.compile(r"^[A-Z][A-Za-z0-9]*$")
_ID = r"[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*"
SOP_ID_RE = re.compile(rf"^{_ID}$")
EXTENDS_RE = re.compile(rf"^({_ID})@(\d+)$")
IF_ID_RE = re.compile(r"^IF-[A-Z0-9][A-Z0-9\-]*$")
IMPLEMENTS_RE = re.compile(r"^(IF-[A-Z0-9][A-Z0-9\-]*)@(\d+)$")
OVERRIDE_RE = re.compile(rf"^({_ID})\.([A-Za-z0-9_\-]+)$")
STEP_ID_RE = re.compile(r"^[A-Za-z0-9_\-]+$")
VERSION_RE = re.compile(r"^\d+\.\d+$")
NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
BASE_TYPES = {"any", "string", "int", "number", "bool", "path", "date", "datetime",
              "commit_ref", "json", "map"}
LIST_TYPE_RE = re.compile(r"^list\[(.+)\]$")
IO_SPEC_KEYS = ["type", "required", "default", "from", "pattern", "description"]
CONTEXT_SPEC_KEYS = ["type", "init", "description"]
LLM_KEYS = ["task_class", "difficulty", "data_class", "service", "model", "effort",
            "temperature", "timeout", "max_tokens", "fallback"]
AGENT_KEYS = ["task_class", "difficulty", "needs", "data_class", "agent", "model", "tools",
              "write_to", "checks", "fallback"]
RETRY_KEYS = ["max", "interval"]
IO_KEYS = ["input_schema", "output_schema"]
DISPATCH_KEYS = ["when", "sop"]
HARD_MAX_DEPTH = 5         # same as limits.max_depth cap in schema/runtime_keys.yaml
HARD_MAX_ITERATIONS = 200  # same as limits.max_iterations cap
HARD_MAX_RETRY = 5
INTERFACE_FILE_KEYS = ["id", "version", "name", "inputs", "outputs", "raises", "pre", "post",
                       "safety", "default_impl", "description"]
SAFETY_KEYS = ["human_steps_required", "forbid"]
HINTS = {"overrides": "to replace a base step write override: <base-id>.<step-id>"}


def valid_type(t):
    if not isinstance(t, str):
        return False
    m = LIST_TYPE_RE.match(t.strip())
    if m:
        return valid_type(m.group(1))
    return t.strip() in BASE_TYPES


def type_compatible(provided, required):
    """Can a value of type ``provided`` be received as ``required``? (``any`` matches everything.)"""
    if provided == "any" or required == "any" or provided is None or required is None:
        return True
    mp, mr = LIST_TYPE_RE.match(provided), LIST_TYPE_RE.match(required)
    if mp and mr:
        return type_compatible(mp.group(1), mr.group(1))
    if bool(mp) != bool(mr):
        return False
    if (provided, required) in {("int", "number"), ("path", "string"), ("commit_ref", "string")}:
        return True
    return provided == required


def value_matches(value, t):
    """Does a static value fit type ``t``?"""
    if t == "any" or value is None:
        return True
    m = LIST_TYPE_RE.match(t)
    if m:
        return isinstance(value, list) and all(value_matches(v, m.group(1)) for v in value)
    if t == "bool":
        return isinstance(value, bool)
    if t == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if t == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if t == "map":
        return isinstance(value, dict)
    if t in ("string", "path", "commit_ref", "date", "datetime"):
        return isinstance(value, (str, int)) and not isinstance(value, bool)
    return True


def _unknown(where, obj, allowed, errs, hint=None):
    for k in obj:
        if k not in allowed:
            extra = (hint or {}).get(k)
            errs.append(f"{where}.{k} is an unknown key" + (f" ({extra})" if extra else ""))


def check_io_specs(where, specs, errs, allow_default=True):
    if not isinstance(specs, dict):
        errs.append(f"{where} must be a map of name -> {{type, required, ...}}")
        return
    for name, spec in specs.items():
        w = f"{where}.{name}"
        if not NAME_RE.match(str(name)):
            errs.append(f"{w}: names may only use letters, digits and _")
        if not isinstance(spec, dict):
            errs.append(f"{w} must be {{type, required, ...}}")
            continue
        _unknown(w, spec, IO_SPEC_KEYS if allow_default else [k for k in IO_SPEC_KEYS if k != "default"], errs)
        t = spec.get("type", "any")
        if not valid_type(t):
            errs.append(f"{w}.type {t} must be one of {sorted(BASE_TYPES)} or list[...]")
        if "required" in spec and not isinstance(spec["required"], bool):
            errs.append(f"{w}.required must be true or false")
        if "default" in spec:
            try:
                kind, val = guard.parse_default(spec["default"])
            except guard.GuardError as x:
                errs.append(f"{w}.default: {x}")
            else:
                if kind == "static" and valid_type(t) and not value_matches(val, t):
                    errs.append(f"{w}.default does not fit type {t}")
        if "pattern" in spec:
            try:
                re.compile(str(spec["pattern"]))
            except re.error as x:
                errs.append(f"{w}.pattern is not a valid regular expression: {x}")


def check_raises(where, v, errs):
    if not isinstance(v, list):
        errs.append(f"{where} must be a list of exception type names")
        return
    for x in v:
        if not isinstance(x, str) or not EXC_RE.match(x):
            errs.append(f"{where}: {x!r} is not an exception type name (capitalised alphanumeric)")


def check_guard(where, text, errs):
    try:
        guard.parse(text)
    except guard.GuardError as x:
        errs.append(f"{where}: unreadable guard expression: {x}")


def _bool(where, v, errs):
    if not isinstance(v, bool):
        errs.append(f"{where} must be true or false")


def _int_range(where, v, lo, hi, errs):
    if isinstance(v, bool) or not isinstance(v, int):
        errs.append(f"{where} must be an integer")
    elif v < lo or v > hi:
        errs.append(f"{where} {v} is outside {lo}..{hi} (upper bound is a hard cap)")


def check_task_ext(sid, s, errs):
    e = errs.append
    mode = s.get("mode")
    if "mode" in s and mode not in MODES:
        e(f"{sid}: mode {mode} must be one of {MODES}")
    if "kind" in s:
        k = s["kind"]
        if k not in KINDS:
            e(f"{sid}: kind {k} must be one of {KINDS}")
        elif mode in MODE_KINDS and k not in MODE_KINDS[mode]:
            if mode == "manual":
                e(f"{sid}: a manual step cannot be kind {k}: human steps are fixed (automating a human step is refused)")
            else:
                e(f"{sid}: kind {k} conflicts with mode {mode} (allowed: {sorted(MODE_KINDS[mode])})")
        if k == "llm" and "agent" in s:
            e(f"{sid}: a kind llm step cannot have agent:")
        if k == "agent" and "llm" in s:
            e(f"{sid}: a kind agent step cannot have llm:")
        if k in ("code", "human") and ("llm" in s or "agent" in s):
            e(f"{sid}: a kind {k} step cannot have llm: / agent:")
    for k in ("abstract", "hook", "final"):
        if k in s:
            _bool(f"{sid}.{k}", s[k], errs)
    if s.get("abstract") and s.get("final"):
        e(f"{sid}: abstract and final cannot be combined (an abstract step must be overridden)")
    if s.get("hook") and s.get("final"):
        e(f"{sid}: hook and final cannot be combined (a hook is an extension point)")
    if s.get("abstract") and s.get("hook"):
        e(f"{sid}: abstract and hook cannot be combined")
    if "visibility" in s:
        v = s["visibility"]
        if v == "public":
            e(f"{sid}: visibility public cannot be written on a step (public is the procedure run itself)")
        elif v not in VISIBILITY:
            e(f"{sid}: visibility {v} must be one of {VISIBILITY}")
        elif v == "private" and (s.get("abstract") or s.get("hook")):
            e(f"{sid}: an abstract or hook step cannot be private (children could not override it)")
    if "override" in s:
        if not isinstance(s["override"], str) or not OVERRIDE_RE.match(s["override"]):
            e(f"{sid}: override must be <base-id>.<step-id>")
    if "super" in s:
        if s["super"] not in SUPER:
            e(f"{sid}: super must be one of {SUPER}")
        if "override" not in s:
            e(f"{sid}: super may only be written on an overriding step")
    if "raises" in s:
        check_raises(f"{sid}.raises", s["raises"], errs)
    if "retry" in s:
        r = s["retry"]
        if not isinstance(r, dict):
            e(f"{sid}.retry must be {{max, interval}}")
        else:
            _unknown(f"{sid}.retry", r, RETRY_KEYS, errs)
            if "max" in r:
                _int_range(f"{sid}.retry.max", r["max"], 0, HARD_MAX_RETRY, errs)
    if "io" in s:
        if not isinstance(s["io"], dict):
            e(f"{sid}.io must be {{input_schema, output_schema}}")
        else:
            _unknown(f"{sid}.io", s["io"], IO_KEYS, errs)
            for k, v in s["io"].items():
                if not isinstance(v, str) or not v.endswith(".json") or "/" in v:
                    e(f"{sid}.io.{k} must be a plain *.json file name")
    if "impl" in s and not isinstance(s["impl"], str):
        e(f"{sid}.impl must be a string")
    for blk, keys in (("llm", LLM_KEYS), ("agent", AGENT_KEYS)):
        if blk in s:
            if not isinstance(s[blk], dict):
                e(f"{sid}.{blk} must be a map")
            else:
                _unknown(f"{sid}.{blk}", s[blk], keys, errs)
                d = s[blk].get("difficulty")
                if d is not None and d not in ("low", "medium", "high"):
                    e(f"{sid}.{blk}.difficulty must be low / medium / high")


def check_loop(sid, s, errs):
    e = errs.append
    if "max_iterations" not in s:
        e(f"{sid}: a loop requires max_iterations (unbounded loops cannot be written)")
    else:
        _int_range(f"{sid}.max_iterations", s["max_iterations"], 1, HARD_MAX_ITERATIONS, errs)
    has_fe, has_wh = "foreach" in s, "while" in s
    if has_fe == has_wh:
        e(f"{sid}: a loop needs exactly one of foreach / while")
    if has_fe:
        try:
            node = guard.parse(str(s["foreach"]))
            if node[0] != "ref":
                e(f"{sid}.foreach must be a reference to a list variable")
        except guard.GuardError as x:
            e(f"{sid}.foreach is unreadable: {x}")
    if has_wh:
        check_guard(f"{sid}.while", s["while"], errs)
    if "as" in s and (not isinstance(s["as"], str) or not NAME_RE.match(s["as"])):
        e(f"{sid}.as must be an identifier")
    body = s.get("body")
    if not isinstance(body, list) or not body or not all(isinstance(x, str) for x in body):
        e(f"{sid}: loop body must be a non-empty list of step ids")


def check_call(sid, s, errs):
    e = errs.append
    has_sop, has_if = "sop" in s, "interface" in s
    if has_sop == has_if:
        e(f"{sid}: a call needs exactly one of sop: / interface:")
    if has_sop:
        if not isinstance(s["sop"], str) or not SOP_ID_RE.match(s["sop"]):
            e(f"{sid}.sop must be a procedure id")
        if not str(s.get("reason") or "").strip():
            e(f"{sid}: calling a concrete procedure by name requires reason: (prefer interface calls)")
        if "dispatch" in s:
            e(f"{sid}: dispatch may only be written on an interface call")
    if has_if:
        if not isinstance(s["interface"], str) or not IMPLEMENTS_RE.match(s["interface"]):
            e(f"{sid}.interface must be IF-<name>@<major>")
        disp = s.get("dispatch", [])
        if not isinstance(disp, list):
            e(f"{sid}.dispatch must be a list of {{when, sop}}")
        else:
            for i, d in enumerate(disp):
                if not isinstance(d, dict):
                    e(f"{sid}.dispatch[{i}] must be {{when, sop}}")
                    continue
                _unknown(f"{sid}.dispatch[{i}]", d, DISPATCH_KEYS, errs)
                if "when" not in d or "sop" not in d:
                    e(f"{sid}.dispatch[{i}] needs both when and sop")
                if "when" in d:
                    check_guard(f"{sid}.dispatch[{i}].when", d["when"], errs)
                if "sop" in d and not SOP_ID_RE.match(str(d["sop"])):
                    e(f"{sid}.dispatch[{i}].sop must be a procedure id")
    for k in ("with", "returns", "catch", "with_config"):
        if k in s and not isinstance(s[k], dict):
            e(f"{sid}.{k} must be a map")
    if isinstance(s.get("with"), dict):
        for k, v in s["with"].items():
            if isinstance(v, str):
                try:
                    guard.parse(v)
                except guard.GuardError as x:
                    e(f"{sid}.with.{k}: unreadable expression: {x}")
    if "max_depth" in s:
        _int_range(f"{sid}.max_depth", s["max_depth"], 1, HARD_MAX_DEPTH, errs)
    if "allow_recursion" in s:
        _bool(f"{sid}.allow_recursion", s["allow_recursion"], errs)
        if s["allow_recursion"] and "max_depth" not in s:
            e(f"{sid}: allow_recursion requires max_depth")
    if isinstance(s.get("catch"), dict):
        for exc in s["catch"]:
            if not EXC_RE.match(str(exc)):
                e(f"{sid}.catch key {exc} is not an exception type name")


def check_definition(d):
    """Validate one definition dict in isolation. Returns a list of error strings."""
    errs = []
    e = errs.append
    if not isinstance(d, dict):
        return ["the definition is not a map"]
    _unknown("(top level)", d, TOP_BASE + TOP_EXT, errs)
    sop = d.get("sop") if isinstance(d.get("sop"), dict) else {}
    _unknown("sop", sop, SOP_BASE + SOP_EXT, errs)
    if "extends" in sop:
        if isinstance(sop["extends"], list):
            e("sop.extends takes exactly one base (no multiple inheritance; use implements for interfaces)")
        elif not isinstance(sop["extends"], str) or not EXTENDS_RE.match(sop["extends"]):
            e("sop.extends must be <id>@<major>")
    if "abstract" in sop:
        _bool("sop.abstract", sop["abstract"], errs)
    if "implements" in sop:
        imp = sop["implements"]
        if not isinstance(imp, list) or not all(isinstance(x, str) and IMPLEMENTS_RE.match(x) for x in imp):
            e("sop.implements must be a list of IF-<name>@<major>")
        elif len(set(imp)) != len(imp):
            e("sop.implements has duplicates")
    if "human_only" in sop and (not isinstance(sop["human_only"], list)
                                or not all(isinstance(x, str) for x in sop["human_only"])):
        e("sop.human_only must be a list of strings")
    used = [k for k in SOP_EXT if k in sop] + [k for k in TOP_EXT if k in d]
    if used and not VERSION_RE.match(str(sop.get("version", ""))):
        e(f"sop.version must be major.minor when extension keys are used ({', '.join(used)})")
    if "interface" in d:
        itf = d["interface"]
        if not isinstance(itf, dict):
            e("interface must be {inputs, outputs, raises}")
        else:
            _unknown("interface", itf, ["inputs", "outputs", "raises"], errs)
            if "inputs" in itf:
                check_io_specs("interface.inputs", itf["inputs"], errs)
            if "outputs" in itf:
                check_io_specs("interface.outputs", itf["outputs"], errs, allow_default=False)
            if "raises" in itf:
                check_raises("interface.raises", itf["raises"], errs)
    if "context" in d:
        ctx = d["context"]
        if not isinstance(ctx, dict):
            e("context must be a map of name -> {type, init}")
        else:
            for name, spec in ctx.items():
                if not NAME_RE.match(str(name)):
                    e(f"context.{name}: names may only use letters, digits and _")
                if not isinstance(spec, dict):
                    e(f"context.{name} must be {{type, init}}")
                    continue
                _unknown(f"context.{name}", spec, CONTEXT_SPEC_KEYS, errs)
                t = spec.get("type", "any")
                if not valid_type(t):
                    e(f"context.{name}.type {t} is not a type")
                elif "init" in spec and not value_matches(spec["init"], t):
                    e(f"context.{name}.init does not fit type {t}")
    if "runtime" in d and not isinstance(d["runtime"], dict):
        e("runtime must be a map (keys are declared in schema/runtime_keys.yaml)")
    if "finally" in d:
        f = d["finally"]
        if not isinstance(f, list) or not all(isinstance(x, str) for x in f):
            e("finally must be a list of step ids")
    for s in d.get("steps") or []:
        if not isinstance(s, dict):
            e("every step must be a map")
            continue
        sid, typ = s.get("id"), s.get("type")
        if typ not in STEP_BASE:
            e(f"step {sid}: type {typ!r} must be one of {sorted(STEP_BASE)}")
            continue
        _unknown(str(sid), s, STEP_COMMON + STEP_BASE[typ] + STEP_EXT.get(typ, []), errs, HINTS)
        if sid is None or not STEP_ID_RE.match(str(sid)):
            e(f"step id {sid!r} may only use letters, digits, _ and -")
        if typ == "task":
            check_task_ext(sid, s, errs)
        elif typ == "loop":
            check_loop(sid, s, errs)
        elif typ == "call":
            check_call(sid, s, errs)
        if "decide_by" in s:
            try:
                node = guard.parse(str(s["decide_by"]))
                if node[0] != "ref" or len(node[1]) < 2:
                    e(f"{sid}.decide_by must be a reference <step-id>.<output>")
            except guard.GuardError as x:
                e(f"{sid}.decide_by is unreadable: {x}")
        if "final" in s and typ != "task":
            _bool(f"{sid}.final", s["final"], errs)
    for t in d.get("transitions") or []:
        if not isinstance(t, dict):
            e("every transition must be a map")
            continue
        _unknown(f"transition {t.get('id')}", t, TR_BASE + TR_EXT, errs)
        if "guard" in t:
            check_guard(f"transition {t.get('id')}", t["guard"], errs)
        if "final" in t:
            _bool(f"transition {t.get('id')}.final", t["final"], errs)
    return errs


def check_interface_file(d, filename=None):
    """Validate an ``<IF-ID>.yaml`` interface contract."""
    errs = []
    e = errs.append
    if not isinstance(d, dict):
        return ["the interface is not a map"]
    _unknown("(interface)", d, INTERFACE_FILE_KEYS, errs)
    for k in ("id", "version", "name", "inputs", "outputs"):
        if k not in d:
            e(f"the interface has no {k}")
    iid = d.get("id")
    if iid is not None and (not isinstance(iid, str) or not IF_ID_RE.match(iid)):
        e("id must be IF-<UPPERCASE/digits/->")
    if filename and iid and filename != f"{iid}.yaml":
        e(f"file name {filename} differs from id {iid} (use <IF-ID>.yaml)")
    if "version" in d and not VERSION_RE.match(str(d["version"])):
        e("version must be major.minor")
    if "inputs" in d:
        check_io_specs("inputs", d["inputs"], errs)
    if "outputs" in d:
        check_io_specs("outputs", d["outputs"], errs, allow_default=False)
    if "raises" in d:
        check_raises("raises", d["raises"], errs)
    for k in ("pre", "post"):
        if k in d:
            if not isinstance(d[k], list):
                e(f"{k} must be a list of guard expressions")
            else:
                for i, x in enumerate(d[k]):
                    if not isinstance(x, str):
                        e(f"{k}[{i}] must be a string")
                    else:
                        check_guard(f"{k}[{i}]", x, errs)
    if "safety" in d:
        sf = d["safety"]
        if not isinstance(sf, dict):
            e("safety must be {human_steps_required, forbid}")
        else:
            _unknown("safety", sf, SAFETY_KEYS, errs)
            for k in SAFETY_KEYS:
                if k in sf and (not isinstance(sf[k], list) or not all(isinstance(x, str) and x.strip() for x in sf[k])):
                    e(f"safety.{k} must be a list of non-empty strings")
    if d.get("default_impl") is not None and not SOP_ID_RE.match(str(d["default_impl"])):
        e("default_impl must be a procedure id or null")
    return errs
