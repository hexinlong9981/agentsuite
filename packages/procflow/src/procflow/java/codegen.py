"""Java code generator for procflow procedures and interfaces.

Generates Java classes, interfaces, and abstract stubs from YAML procedure/interface
definitions (yaml2java, ir2java, stub).
"""
from __future__ import annotations

import datetime as _dt
import re
from typing import Any


DEFAULT_PACKAGE = "procflow.defs"
GENERATED_PACKAGE = "procflow.defs.generated"

JAVA_KEYWORDS = {
    "abstract", "assert", "boolean", "break", "byte", "case", "catch", "char", "class", "const", "continue",
    "default", "do", "double", "else", "enum", "extends", "final", "finally", "float", "for", "goto", "if",
    "implements", "import", "instanceof", "int", "interface", "long", "native", "new", "package", "private",
    "protected", "public", "return", "short", "static", "strictfp", "super", "switch", "synchronized", "this",
    "throw", "throws", "transient", "try", "void", "volatile", "while", "true", "false", "null", "record", "var",
    "yield", "flow", "contract",
}

BUILTIN_EXCEPTIONS = {"SystemError", "NoImplementation", "LimitReached"}

STEP_STR_ATTRS = {
    "name": "name",
    "actor": "actor",
    "mode": "mode",
    "action": "action",
    "completion": "completion",
}

STEP_ARR_ATTRS = {
    "inputs": "inputs",
    "tools": "tools",
    "outputs": "outputs",
    "evidence": "evidence",
}

AUTO_STR_ATTRS = {"reason": "reason"}
AUTO_ARR_ATTRS = {"models": "models", "programs": "programs", "procedure": "procedure"}

LLM_STR_ATTRS = {
    "task_class": "taskClass",
    "difficulty": "difficulty",
    "data_class": "dataClass",
    "service": "service",
    "model": "model",
    "effort": "effort",
    "timeout": "timeout",
}
LLM_ARR_ATTRS = {"fallback": "fallback"}

AGENT_STR_ATTRS = {
    "task_class": "taskClass",
    "difficulty": "difficulty",
    "data_class": "dataClass",
    "agent": "agent",
    "model": "model",
    "write_to": "writeTo",
}
AGENT_ARR_ATTRS = {"needs": "needs", "tools": "tools", "checks": "checks", "fallback": "fallback"}

KIND_MAP = {"code": "CODE", "llm": "LLM", "agent": "AGENT", "human": "HUMAN"}
VIS_MAP = {"protected": "PROTECTED", "private": "PRIVATE"}
SUPER_MAP = {"before": "BEFORE", "after": "AFTER"}

JTYPE_MAP = {
    "string": "String",
    "bool": "Boolean",
    "int": "Long",
    "number": "Double",
    "path": "ProcedurePath",
    "commit_ref": "CommitRef",
    "date": "String",
    "datetime": "String",
    "any": "Object",
    "json": "Object",
    "map": "java.util.Map<String, Object>",
}


def to_class_name(ident: str, suffix: str = "") -> str:
    """Convert an identifier like 'SOP-01-REPORT' into 'Sop01Report'."""
    parts = [p for p in re.split(r"[^A-Za-z0-9]+", str(ident)) if p]
    if not parts:
        return f"Procedure{suffix}"
    return "".join(p[:1].upper() + p[1:].lower() for p in parts) + suffix


def to_method_name(step_id: str) -> str:
    """Convert a step ID like 'FETCH-LOG' or 's3_check' into a valid Java method name."""
    parts = [p for p in re.split(r"[^A-Za-z0-9]+", str(step_id)) if p]
    if not parts:
        raise ValueError(f"Cannot generate method name from step ID: {step_id}")
    name = parts[0].lower() + "".join(p[:1].upper() + p[1:].lower() for p in parts[1:])
    if name[0].isdigit():
        name = "step" + name
    return name + "Step" if name in JAVA_KEYWORDS else name


def to_camel_case(name: str) -> str:
    parts = [p for p in re.split(r"[^A-Za-z0-9]+", str(name)) if p]
    if not parts:
        return "field"
    s = parts[0].lower() + "".join(p[:1].upper() + p[1:].lower() for p in parts[1:])
    return s + "_" if s in JAVA_KEYWORDS else s


def java_str(s: Any) -> str:
    """Escape and quote a string for Java source code."""
    res = []
    for ch in str(s):
        if ch == "\\":
            res.append("\\\\")
        elif ch == '"':
            res.append('\\"')
        elif ch == "\n":
            res.append("\\n")
        elif ch == "\r":
            res.append("\\r")
        elif ch == "\t":
            res.append("\\t")
        elif ord(ch) < 0x20:
            res.append("\\u%04x" % ord(ch))
        else:
            res.append(ch)
    return '"' + "".join(res) + '"'


def java_array(items: list[str]) -> str:
    return "{" + ", ".join(java_str(x) for x in items) + "}"


def java_value(v: Any, indent: int = 0) -> str:
    """Convert a Python structure into D (fluent data builder) expressions in Java."""
    pad = " " * indent
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return f"{v}L" if not -2**31 <= v < 2**31 else str(v)
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, (_dt.date, _dt.datetime)):
        return java_str(v.isoformat())
    if isinstance(v, str):
        return java_str(v)
    if isinstance(v, list):
        inner = [java_value(x, indent + 4) for x in v]
        one_line = "D.list(" + ", ".join(inner) + ")"
        if len(one_line) + indent <= 100 and "\n" not in one_line:
            return one_line
        return "D.list(\n" + ",\n".join(pad + "    " + x for x in inner) + ")"
    if isinstance(v, dict):
        inner_pairs = [f"{java_str(k)}, {java_value(x, indent + 4)}" for k, x in v.items()]
        one_line = "D.map(" + ", ".join(inner_pairs) + ")"
        if len(one_line) + indent <= 100 and "\n" not in one_line:
            return one_line
        return "D.map(\n" + ",\n".join(pad + "    " + x for x in inner_pairs) + ")"
    return java_str(str(v))


def _is_str_list(v: Any) -> bool:
    return isinstance(v, list) and all(isinstance(x, str) for x in v)


def yaml2java(
    sop_def: dict[str, Any],
    cls_name: str | None = None,
    package: str = DEFAULT_PACKAGE,
) -> str:
    """Generate a Java class implementing Procedure from a YAML definition dictionary."""
    sop = sop_def.get("sop") or {}
    sop_id = sop.get("id") or sop_def.get("id", "PROCEDURE-01")
    sop_name = sop.get("name") or sop_def.get("name", "Unnamed Procedure")
    sop_version = sop.get("version") or sop_def.get("version", "1.0.0")

    cls = cls_name or to_class_name(sop_id)
    imports = {
        "procflow.api.D",
        "procflow.api.Procedure",
        "procflow.api.ProcedureBuilder",
        "procflow.api.Step",
    }

    methods: list[str] = []
    flow_lines: list[str] = []

    # SOP metadata lines
    for k, v in sop.items():
        if k in ("id", "name", "version", "abstract", "extends", "implements"):
            continue
        flow_lines.append(f"        b.metadata({java_str(k)}, {java_value(v, 8)});")

    flow_lines.append("")

    # Steps
    steps = sop_def.get("steps") or []
    for s in steps:
        if not isinstance(s, dict):
            continue
        sid = s.get("id")
        stype = s.get("type", "task")
        if stype == "task" or stype == "code":
            mname = to_method_name(sid)
            attrs = [f"id = {java_str(sid)}"]
            if "name" in s:
                attrs.append(f"name = {java_str(s['name'])}")
            if "action" in s:
                attrs.append(f"action = {java_str(s['action'])}")
            if "inputs" in s and _is_str_list(s["inputs"]):
                attrs.append(f"inputs = {java_array(s['inputs'])}")
            if "outputs" in s and _is_str_list(s["outputs"]):
                attrs.append(f"outputs = {java_array(s['outputs'])}")
            if "timeout_sec" in s:
                attrs.append(f"timeoutSec = {s['timeout_sec']}")

            step_annot = "    @Step(" + ", ".join(attrs) + ")"
            method_code = f"{step_annot}\n    protected void {mname}() {{\n    }}"
            methods.append(method_code)

            flow_lines.append(f"        b.step({java_str(sid)});")
        elif stype == "start":
            flow_lines.append(f"        b.start({java_str(sid)});")
        elif stype == "end":
            outcome = s.get("outcome", "success")
            flow_lines.append(f"        b.end({java_str(sid)}, {java_str(outcome)});")
        elif stype == "call":
            flow_lines.append(f"        b.call({java_str(sid)});")
        elif stype == "exclusive_choice" or stype == "choice":
            flow_lines.append(f"        b.choice({java_str(sid)});")
        else:
            flow_lines.append(f"        b.node({java_str(sid)}, {java_str(stype)});")

    # Transitions
    flow_lines.append("")
    transitions = sop_def.get("transitions") or []
    for t in transitions:
        if not isinstance(t, dict):
            continue
        tid = t.get("id", "")
        tfrom = t.get("from", "")
        tto = t.get("to", "")
        line = f"        b.transition({java_str(tid)}, {java_str(tfrom)}, {java_str(tto)})"
        if "guard" in t:
            line += f".guard({java_str(t['guard'])})"
        if t.get("default") is True:
            line += ".byDefault()"
        flow_lines.append(line + ";")

    # Assemble class file
    annot = f'@Procedure(id = {java_str(sop_id)}, name = {java_str(sop_name)}, version = {java_str(sop_version)})'
    decl = f'public class {cls} implements Procedure'

    imports_src = "\n".join(f"import {x};" for x in sorted(imports))

    return (
        f"package {package};\n\n"
        f"{imports_src}\n\n"
        f"/*\n * Generated by procflow yaml2java for procedure: {sop_id}\n */\n"
        f"{annot}\n"
        f"{decl} {{\n\n"
        + "\n\n".join(methods) + ("\n\n" if methods else "")
        + "    @Override\n"
        + "    public void configure(ProcedureBuilder b) {\n"
        + "\n".join(flow_lines) + "\n"
        + "    }\n"
        + "}\n"
    )


def ir2java_interface(
    spec: dict[str, Any],
    cls_name: str | None = None,
    package: str = GENERATED_PACKAGE,
) -> str:
    """Generate a Java interface representing a procedure contract."""
    iid = spec.get("id", "IF-GENERIC")
    version = spec.get("version", "1.0.0")
    name = spec.get("name", iid)
    desc = spec.get("description", "")

    cls = cls_name or to_class_name(iid)
    imports = {"procflow.api.Field", "procflow.api.ProcedureInterface"}

    attrs = [f"id = {java_str(iid)}", f"name = {java_str(name)}", f"version = {java_str(version)}"]
    if desc:
        attrs.append(f"description = {java_str(desc)}")

    parts: list[str] = []

    # In / Out records
    inputs = spec.get("inputs") or {}
    if inputs:
        comps = []
        for fname, fspec in inputs.items():
            ftype = JTYPE_MAP.get(str(fspec if isinstance(fspec, str) else fspec.get("type", "string")), "String")
            comps.append(f"        @Field(name = {java_str(fname)})\n        {ftype} {to_camel_case(fname)}")
        parts.append("    record In(\n" + ",\n".join(comps) + "\n    ) {}")

    outputs = spec.get("outputs") or {}
    if outputs:
        comps = []
        for fname, fspec in outputs.items():
            ftype = JTYPE_MAP.get(str(fspec if isinstance(fspec, str) else fspec.get("type", "string")), "String")
            comps.append(f"        @Field(name = {java_str(fname)})\n        {ftype} {to_camel_case(fname)}")
        parts.append("    record Out(\n" + ",\n".join(comps) + "\n    ) {}")

    imports_src = "\n".join(f"import {x};" for x in sorted(imports))

    return (
        f"package {package};\n\n"
        f"{imports_src}\n\n"
        f"/*\n * Generated by procflow ir2java for interface: {iid}\n */\n"
        f"@ProcedureInterface(\n    " + ",\n    ".join(attrs) + "\n)\n"
        f"public interface {cls} {{\n\n"
        + "\n\n".join(parts) + ("\n\n" if parts else "")
        + "}\n"
    )


def stub(
    sop_def: dict[str, Any],
    cls_name: str | None = None,
    package: str = GENERATED_PACKAGE,
) -> str:
    """Generate an abstract Java stub class for a procedure."""
    sop = sop_def.get("sop") or {}
    sop_id = sop.get("id") or sop_def.get("id", "SOP-ABSTRACT")
    version = sop.get("version") or sop_def.get("version", "1.0.0")

    cls = cls_name or to_class_name(sop_id, "Stub")
    imports = {"procflow.api.ProcedureStub", "procflow.api.Step"}

    methods: list[str] = []
    for s in sop_def.get("steps") or []:
        if not isinstance(s, dict) or s.get("type") not in ("task", "code", None):
            continue
        sid = s.get("id", "")
        m = to_method_name(sid)
        is_abstract = s.get("abstract", True)
        if is_abstract:
            decl = f"    @Step(id = {java_str(sid)})\n    protected abstract void {m}();"
        else:
            decl = f"    @Step(id = {java_str(sid)})\n    protected void {m}() {{\n    }}"
        methods.append(decl)

    imports_src = "\n".join(f"import {x};" for x in sorted(imports))

    return (
        f"package {package};\n\n"
        f"{imports_src}\n\n"
        f"/*\n * Generated by procflow stub for procedure: {sop_id}\n */\n"
        f"@ProcedureStub(id = {java_str(sop_id)}, version = {java_str(version)})\n"
        f"public abstract class {cls} {{\n\n"
        + "\n\n".join(methods) + "\n\n"
        + "}\n"
    )
