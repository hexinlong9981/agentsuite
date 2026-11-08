"""Unit tests for procflow: Java authoring surface.

Tests Java code generation (yaml2java, ir2java, stub), allowed-API static checking,
AST/regex extraction, environment detection, and java-step execution resolution.
"""
from __future__ import annotations

import json
from pathlib import Path
import pytest
import yaml

from procflow import java as J
from procflow import javadefs as JD
from procflow import programs as PG
from procflow.cli import main as cli_main


SAMPLE_PROCEDURE_YAML = """
id: SOP-JAVA-01
name: Java Authoring Test Flow
version: 1.0.0
sop:
  id: SOP-JAVA-01
  name: Java Authoring Test Flow
  version: 1.0.0
inputs:
  - param_x
outputs:
  - final_report
steps:
  - id: start
    type: start
  - id: fetch_data
    type: task
    name: Fetch Input Data
    action: Retrieve raw data for processing
    inputs: ["param_x"]
    outputs: ["raw_data"]
    timeout_sec: 120
  - id: process_data
    type: task
    name: Process Records
    action: Parse and analyze records
    inputs: ["raw_data"]
    outputs: ["final_report"]
  - id: finish
    type: end
    outcome: success
transitions:
  - id: t1
    from: start
    to: fetch_data
  - id: t2
    from: fetch_data
    to: process_data
  - id: t3
    from: process_data
    to: finish
"""

SAMPLE_INTERFACE_YAML = """
id: IF-DATA-PIPELINE
name: Data Pipeline Contract
version: 1.0.0
description: Standard input/output contract for pipelines
inputs:
  dataset_id:
    type: string
    required: true
  batch_size:
    type: int
outputs:
  processed_count:
    type: int
  success_rate:
    type: number
"""


def test_environment_detection() -> None:
    jdk_home, why = J.find_jdk(min_version=17)
    # May or may not have JDK 17 installed in this container; function should return cleanly
    if jdk_home is not None:
        assert jdk_home.is_dir()
        env = J.get_child_java_env(jdk_home)
        assert env["JAVA_HOME"] == str(jdk_home)
        assert str(jdk_home / "bin") in env["PATH"]
    else:
        assert isinstance(why, str)

    mvn = J.find_maven()
    if mvn is not None:
        assert mvn.is_file()


def test_codegen_yaml2java() -> None:
    sop_def = yaml.safe_load(SAMPLE_PROCEDURE_YAML)
    java_code = J.yaml2java(sop_def, package="com.example.procedures")

    assert "package com.example.procedures;" in java_code
    assert "@Procedure(id = \"SOP-JAVA-01\", name = \"Java Authoring Test Flow\", version = \"1.0.0\")" in java_code
    assert "public class SopJava01 implements Procedure" in java_code
    assert "@Step(id = \"fetch_data\", name = \"Fetch Input Data\", action = \"Retrieve raw data for processing\"" in java_code
    assert "protected void fetchData() {" in java_code
    assert "b.start(\"start\");" in java_code
    assert "b.step(\"fetch_data\");" in java_code
    assert "b.transition(\"t1\", \"start\", \"fetch_data\");" in java_code


def test_codegen_ir2java_and_stub() -> None:
    if_def = yaml.safe_load(SAMPLE_INTERFACE_YAML)
    if_code = J.ir2java_interface(if_def, package="com.example.generated")

    assert "package com.example.generated;" in if_code
    assert "@ProcedureInterface(" in if_code
    assert "id = \"IF-DATA-PIPELINE\"" in if_code
    assert "public interface IfDataPipeline" in if_code
    assert "record In(" in if_code
    assert "@Field(name = \"dataset_id\")" in if_code
    assert "String datasetId" in if_code
    assert "record Out(" in if_code
    assert "Long processedCount" in if_code

    # Abstract stub generation
    sop_def = yaml.safe_load(SAMPLE_PROCEDURE_YAML)
    stub_code = J.stub(sop_def, package="com.example.generated")
    assert "package com.example.generated;" in stub_code
    assert "@ProcedureStub(id = \"SOP-JAVA-01\", version = \"1.0.0\")" in stub_code
    assert "public abstract class SopJava01Stub" in stub_code
    assert "protected abstract void fetchData();" in stub_code


def test_allowed_api_checker(tmp_path: Path) -> None:
    # 1. Clean Java code -> 0 violations
    clean_code = """
    package procflow.defs;
    import java.util.List;
    import java.util.Map;
    import java.time.Instant;
    import procflow.api.Procedure;

    public class SafeProcedure implements Procedure {
        public void doWork() {
            List<String> items = List.of("a", "b");
        }
    }
    """
    v_clean = J.check_source_content(clean_code)
    assert len(v_clean) == 0

    # 2. Code with disallowed networking and reflection
    dirty_code = """
    package procflow.defs;
    import java.net.Socket;
    import java.lang.reflect.Method;
    import java.io.FileWriter;

    public class DangerousProcedure {
        public void execute() {
            Runtime.getRuntime().exec("ls");
            new ProcessBuilder("echo", "bad");
            System.exit(1);
        }
    }
    """
    v_dirty = J.check_source_content(dirty_code, file_label="Dangerous.java")
    assert len(v_dirty) >= 5

    rules = {v.rule_description for v in v_dirty}
    assert any("Networking" in r for r in rules)
    assert any("Reflection" in r for r in rules)
    assert any("Direct file write" in r for r in rules)
    assert any("Runtime" in r for r in rules)
    assert any("ProcessBuilder" in r for r in rules)

    # Test directory check
    f = tmp_path / "Dangerous.java"
    f.write_text(dirty_code, encoding="utf-8")
    dir_violations = J.check_directory(tmp_path)
    assert len(dir_violations) >= 5


def test_extract_from_java_source() -> None:
    java_src = """
    package com.example.procedures;

    import procflow.api.Procedure;
    import procflow.api.ProcedureBuilder;
    import procflow.api.Step;

    @Procedure(id = "SOP-EXTRACT-01", name = "Extract Test", version = "2.0.0")
    public class ExtractSample implements Procedure {

        @Step(id = "prep_step", name = "Prepare", action = "Prepare workspace", inputs = {"config_id"}, outputs = {"workspace_path"}, timeoutSec = 60)
        protected void prepStep() {
        }

        @Step(id = "run_step", name = "Run Engine", action = "Execute work", inputs = {"workspace_path"}, outputs = {"result"})
        protected void runStep() {
        }

        @Override
        public void configure(ProcedureBuilder b) {
            b.start("start");
            b.step("prep_step");
            b.step("run_step");
            b.end("finish", "success");

            b.transition("t1", "start", "prep_step");
            b.transition("t2", "prep_step", "run_step").guard("ready == true");
            b.transition("t3", "run_step", "finish").byDefault();
        }
    }
    """
    extracted = J.extract_from_source(java_src)
    assert extracted is not None
    assert extracted["id"] == "SOP-EXTRACT-01"
    assert extracted["name"] == "Extract Test"
    assert extracted["version"] == "2.0.0"

    steps = extracted["steps"]
    step_ids = {s["id"] for s in steps}
    assert "start" in step_ids
    assert "prep_step" in step_ids
    assert "run_step" in step_ids
    assert "finish" in step_ids

    prep = next(s for s in steps if s["id"] == "prep_step")
    assert prep["name"] == "Prepare"
    assert prep["action"] == "Prepare workspace"
    assert prep["inputs"] == ["config_id"]
    assert prep["outputs"] == ["workspace_path"]
    assert prep["timeout_sec"] == 60

    transitions = extracted["transitions"]
    assert len(transitions) == 3
    t2 = next(t for t in transitions if t["id"] == "t2")
    assert t2["from"] == "prep_step"
    assert t2["to"] == "run_step"
    assert t2["guard"] == "ready == true"


def test_runner_and_programs_integration(tmp_path: Path) -> None:
    # 1. Invalid step name fails resolution
    argv, env, err = J.resolve_step_command(["bad-name-with-dashes!"])
    assert err != ""
    assert argv == []

    # 2. Classification in programs.py for java-step
    roots = [tmp_path]
    classified = PG.classify("java-step SampleStep param1", values={}, roots=roots, impl_root=tmp_path)
    # Depending on whether JDK is found on the machine, either local or command with explanation
    assert classified["kind"] in ("local", "command")
    assert "java-step" in classified["line"]


def test_cli_java_commands(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    def run_cli(*args: str) -> tuple[int, str, str]:
        with pytest.raises(SystemExit) as exc:
            cli_main(list(args))
        out, err = capsys.readouterr()
        return exc.value.code, out, err

    sop_file = tmp_path / "proc.yaml"
    sop_file.write_text(SAMPLE_PROCEDURE_YAML, encoding="utf-8")

    out_java = tmp_path / "SopJava01.java"

    # 1. yaml2java
    rc, out, _ = run_cli("java", "yaml2java", str(sop_file), "--out", str(out_java))
    assert rc == 0
    assert out_java.exists()
    assert "public class SopJava01" in out_java.read_text(encoding="utf-8")

    # 2. check-api
    rc, out, _ = run_cli("java", "check-api", str(out_java))
    assert rc == 0  # Generated file is safe

    # 3. extract
    rc, out, _ = run_cli("java", "extract", str(out_java), "--json")
    assert rc == 0
    parsed_json = json.loads(out)
    assert len(parsed_json) == 1
    assert parsed_json[0]["id"] == "SOP-JAVA-01"
