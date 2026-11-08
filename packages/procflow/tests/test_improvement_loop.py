"""Unit tests for procflow: Improvement Loop, Autonomous Revision, and Scheduling."""
from __future__ import annotations

import datetime as dt
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from procflow import (
    harvest as HV,
    launch as LN,
    metrics as M,
    notify as NT,
    refactor as RF,
    resume_eval as RE,
    revise as RV,
    rwhen as RW,
    schedule as SC,
    smells as SM,
)


def _init_git_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True, capture_output=True)


def test_metrics_collection_and_aggregation(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()

    # Create dummy run 1 (completed)
    run1 = runs_dir / "SOP-01" / "run_001"
    run1.mkdir(parents=True)
    (run1 / "run.json").write_text(
        json.dumps({
            "sop": "SOP-01",
            "run_id": "run_001",
            "status": "completed",
            "started_at": "2026-11-01T10:00:00Z",
            "ended_at": "2026-11-01T10:05:00Z",
        }),
        encoding="utf-8",
    )
    (run1 / "state.jsonl").write_text(
        json.dumps({"ev": "step_start", "step": "step1", "ts": "2026-11-01T10:00:10Z"}) + "\n"
        + json.dumps({"ev": "step_done", "step": "step1", "ts": "2026-11-01T10:01:10Z"}) + "\n"
        + json.dumps({"ev": "step_start", "step": "step2", "ts": "2026-11-01T10:01:20Z"}) + "\n"
        + json.dumps({"ev": "step_done", "step": "step2", "ts": "2026-11-01T10:04:20Z"}) + "\n",
        encoding="utf-8",
    )

    # Create dummy run 2 (failed)
    run2 = runs_dir / "SOP-01" / "run_002"
    run2.mkdir(parents=True)
    (run2 / "run.json").write_text(
        json.dumps({
            "sop": "SOP-01",
            "run_id": "run_002",
            "status": "failed",
            "started_at": "2026-11-01T11:00:00Z",
            "ended_at": "2026-11-01T11:02:00Z",
        }),
        encoding="utf-8",
    )
    (run2 / "state.jsonl").write_text(
        json.dumps({"ev": "step_start", "step": "step1", "ts": "2026-11-01T11:00:10Z"}) + "\n"
        + json.dumps({"ev": "step_done", "step": "step1", "ts": "2026-11-01T11:00:30Z"}) + "\n"
        + json.dumps({"ev": "step_start", "step": "step2", "ts": "2026-11-01T11:00:40Z"}) + "\n"
        + json.dumps({"ev": "step_fail", "step": "step2", "ts": "2026-11-01T11:01:40Z"}) + "\n",
        encoding="utf-8",
    )

    agg = M.aggregate_metrics(runs_dir)
    assert agg.total_runs == 2
    assert agg.completed_runs == 1
    assert agg.failed_runs == 1
    assert agg.run_success_rate == 0.5
    assert "step1" in agg.step_stats
    assert "step2" in agg.step_stats

    s1 = agg.step_stats["step1"]
    assert s1.invocations == 2
    assert s1.successes == 2
    assert s1.failures == 0
    assert s1.failure_rate == 0.0

    s2 = agg.step_stats["step2"]
    assert s2.invocations == 2
    assert s2.successes == 1
    assert s2.failures == 1
    assert s2.failure_rate == 0.5


def test_smells_detection_and_refactoring_proposals() -> None:
    # Dummy IR with a long action and high failure rate
    ir = {
        "id": "SOP-TEST",
        "nodes": [
            {
                "id": "heavy_step",
                "type": "task",
                "action": "A" * 200,  # exceeds default 150 chars
            },
            {
                "id": "normal_step",
                "type": "task",
                "action": "Normal short action",
            },
        ],
    }

    static_smells = SM.detect_static_smells(ir)
    assert any(s.smell_id == "LONG_ACTION_TEXT" and s.step_id == "heavy_step" for s in static_smells)

    # Runtime smell detection
    agg = M.AggregatedMetrics()
    agg.step_stats["failing_step"] = M.StepMetric(
        step_id="failing_step",
        invocations=10,
        successes=5,
        failures=5,
    )
    agg.step_stats["slow_step"] = M.StepMetric(
        step_id="slow_step",
        invocations=4,
        successes=4,
        durations=[4000.0, 4200.0],
    )

    runtime_smells = SM.detect_runtime_smells("SOP-TEST", agg)
    assert any(s.smell_id == "HIGH_FAILURE_RATE" and s.step_id == "failing_step" for s in runtime_smells)
    assert any(s.smell_id == "LONG_DURATION_STEP" and s.step_id == "slow_step" for s in runtime_smells)

    # Generate refactoring proposals
    sop_def = {
        "id": "SOP-TEST",
        "steps": [
            {"id": "slow_step", "type": "code", "action": "Wait long"},
            {"id": "failing_step", "type": "code", "action": "Do unstable task"},
        ],
    }
    all_smells = static_smells + runtime_smells
    proposals = RF.generate_refactor_proposals(sop_def, all_smells)

    assert len(proposals) >= 2
    types = {p.refactoring_type for p in proposals}
    assert "INTRODUCE_TIMEOUT" in types
    assert "ADD_PRECONDITION" in types


def test_harvest_and_draft_synthesis(tmp_path: Path) -> None:
    log_file = tmp_path / "traces.jsonl"
    events = [
        {"step": "Prepare Environment", "action": "Set up sandbox"},
        {"step": "Run Tests", "action": "Execute test command"},
        {"step": "Publish Report", "action": "Generate markdown summary"},
    ]
    with log_file.open("w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")

    trace = HV.parse_trace_from_jsonl(log_file)
    assert trace is not None
    assert len(trace.steps) == 3

    candidates = HV.cluster_step_patterns([trace], min_frequency=1, n_gram=2)
    assert len(candidates) >= 1

    draft_yaml = HV.synthesize_draft_yaml("Automated Test Flow", candidates[0], sop_id="SOP-AUTO-01")
    parsed = yaml.safe_load(draft_yaml)
    assert parsed["id"] == "SOP-AUTO-01"
    assert parsed["name"] == "Automated Test Flow"
    assert len(parsed["steps"]) >= 2


def test_autonomous_revision_with_safety_net(tmp_path: Path) -> None:
    repo_dir = tmp_path / "user_sop_repo"
    _init_git_repo(repo_dir)

    sop_file = repo_dir / "test_sop.yaml"
    initial_def = {
        "id": "SOP-REVISE-01",
        "name": "Revision Test",
        "version": "1.0.0",
        "inputs": ["param_a"],
        "outputs": ["result_data"],
        "steps": [
            {
                "id": "step1",
                "type": "code",
                "action": "Initial action",
            }
        ],
    }
    sop_file.write_text(yaml.dump(initial_def), encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "Initial procedure commit"], cwd=repo_dir, check=True)

    # 1. Valid non-breaking revision -> should succeed and commit
    valid_revision = {
        "id": "SOP-REVISE-01",
        "name": "Revision Test Updated",
        "version": "1.1.0",
        "inputs": ["param_a"],
        "outputs": ["result_data"],
        "steps": [
            {
                "id": "step1",
                "type": "code",
                "action": "Enhanced action with timeout",
                "timeout_sec": 60,
            }
        ],
    }

    res = RV.apply_revision_with_safety_net(
        sop_file_path=sop_file,
        revised_def=valid_revision,
        commit_message="feat: enhance step1 timeout",
    )
    assert res.success is True
    assert res.status == "APPLIED"
    assert res.commit_sha is not None

    # Verify committed content
    curr_def = yaml.safe_load(sop_file.read_text(encoding="utf-8"))
    assert curr_def["version"] == "1.1.0"
    assert curr_def["steps"][0]["timeout_sec"] == 60

    # 2. Breaking revision (removes mandatory output) -> verification should reject and rollback
    breaking_revision = {
        "id": "SOP-REVISE-01",
        "name": "Revision Breaking",
        "version": "1.2.0",
        "inputs": ["param_a"],
        "outputs": [],  # Removed required result_data output!
        "steps": [
            {
                "id": "step1",
                "type": "code",
                "action": "Action without output",
            }
        ],
    }

    res_breaking = RV.apply_revision_with_safety_net(
        sop_file_path=sop_file,
        revised_def=breaking_revision,
    )
    assert res_breaking.success is False
    assert res_breaking.status == "ROLLED_BACK"

    # Verify file rolled back to valid state (version 1.1.0)
    curr_def2 = yaml.safe_load(sop_file.read_text(encoding="utf-8"))
    assert curr_def2["version"] == "1.1.0"


def test_resume_predicates_and_evaluation(tmp_path: Path) -> None:
    test_file = tmp_path / "data.txt"
    test_file.write_text("hello world", encoding="utf-8")

    ctx = RW.ResumeContext(
        run_id="run_100",
        suspended_at="2026-11-01T12:00:00Z",
        attempts=2,
        state_variables={"status": "ready", "count": 5},
        workspace_root=tmp_path,
        approval_decision="approved",
    )

    assert RW.eval_resume_predicate({"exists": "data.txt"}, ctx) is True
    assert RW.eval_resume_predicate({"exists": "nonexistent.txt"}, ctx) is False
    assert RW.eval_resume_predicate({"file_not_empty": "data.txt"}, ctx) is True
    assert RW.eval_resume_predicate({"approval": "approved"}, ctx) is True
    assert RW.eval_resume_predicate({"var_eq": {"key": "status", "val": "ready"}}, ctx) is True
    assert RW.eval_resume_predicate({"var_eq": {"key": "status", "val": "pending"}}, ctx) is False
    assert RW.eval_resume_predicate({"and": [{"exists": "data.txt"}, {"approval": "approved"}]}, ctx) is True

    # Test resume_eval module
    run_dir = tmp_path / "runs" / "SOP-01" / "run_100"
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(
        json.dumps({
            "sop": "SOP-01",
            "run_id": "run_100",
            "status": "suspended",
            "resume_when": {"exists": "data.txt"},
            "suspended_at": "2026-11-01T12:00:00Z",
        }),
        encoding="utf-8",
    )

    eval_res = RE.evaluate_run_resumption(run_dir, workspace_root=tmp_path)
    assert eval_res is not None
    assert eval_res.can_resume is True
    assert (run_dir / "resume_eval.jsonl").exists()


def test_cron_schedule_and_tick(tmp_path: Path) -> None:
    # Test cron parser
    cs = SC.CronSchedule.parse("*/15 9-17 * * 1-5")
    # Mon 10:15 matches
    t_match = dt.datetime(2026, 11, 2, 10, 15, tzinfo=dt.timezone.utc)  # 2026-11-02 is Monday
    assert cs.matches(t_match) is True

    # Sun 10:15 does not match
    t_nomatch = dt.datetime(2026, 11, 1, 10, 15, tzinfo=dt.timezone.utc)  # 2026-11-01 is Sunday
    assert cs.matches(t_nomatch) is False

    # Next fire time calculation
    next_t = cs.next_fire_time(dt.datetime(2026, 11, 1, 12, 0, tzinfo=dt.timezone.utc))
    assert next_t is not None
    assert next_t.hour == 9
    assert next_t.minute == 0
    assert next_t.day == 2

    # Test scheduler tick & notify
    sched_file = tmp_path / "schedules.yaml"
    sched_file.write_text(
        yaml.dump({
            "schedules": [
                {
                    "id": "daily_report",
                    "sop": "SOP-REPORT",
                    "cron": "* * * * *",  # matches any minute
                    "enabled": True,
                }
            ]
        }),
        encoding="utf-8",
    )

    runs_root = tmp_path / "runs"
    runs_root.mkdir()

    tick_res = LN.run_scheduler_tick(
        schedules_file=sched_file,
        runs_root=runs_root,
        now_dt=dt.datetime(2026, 11, 2, 10, 0, tzinfo=dt.timezone.utc),
    )
    assert tick_res.schedules_checked == 1
    assert "daily_report" in tick_res.schedules_triggered

    # Check notification event was appended
    notifs = NT.read_notifications(runs_root)
    assert len(notifs) >= 1
    assert any(n.kind == "schedule_triggered" for n in notifs)


def test_lines_of_defense_safeguards(tmp_path: Path) -> None:
    """Verify that autonomous revision strictly enforces lines-of-defense safeguards."""
    repo_dir = tmp_path / "defense_sop_repo"
    _init_git_repo(repo_dir)

    sop_file = repo_dir / "safe_sop.yaml"
    initial_def = {
        "id": "SOP-SAFE-01",
        "name": "Safety Baseline",
        "version": "1.0.0",
        "inputs": ["task_input"],
        "outputs": ["final_result"],
        "steps": [
            {
                "id": "step_human",
                "type": "human",
                "action": "Sign off by operator",
                "evidence": ["operator_signature.png"],
            },
            {
                "id": "step_exec",
                "type": "code",
                "action": "Run operation",
            },
        ],
    }
    sop_file.write_text(yaml.dump(initial_def), encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "Initial safe procedure commit"], cwd=repo_dir, check=True)

    # 1. Attempt to remove human step -> must fail and rollback
    remove_human_def = {
        "id": "SOP-SAFE-01",
        "name": "Safety Baseline",
        "version": "1.1.0",
        "inputs": ["task_input"],
        "outputs": ["final_result"],
        "steps": [
            {
                "id": "step_exec",
                "type": "code",
                "action": "Run operation without human gate",
            },
        ],
    }
    res = RV.apply_revision_with_safety_net(sop_file, remove_human_def)
    assert res.success is False
    assert res.status == "ROLLED_BACK"
    assert any("human_removed" in str(e).lower() for e in (res.judgement.errors if res.judgement else []))

    # 2. Attempt to automate human step -> must fail and rollback
    automate_human_def = {
        "id": "SOP-SAFE-01",
        "name": "Safety Baseline",
        "version": "1.1.0",
        "inputs": ["task_input"],
        "outputs": ["final_result"],
        "steps": [
            {
                "id": "step_human",
                "type": "code",  # changed from human to code
                "action": "Automated simulated sign off",
            },
            {
                "id": "step_exec",
                "type": "code",
                "action": "Run operation",
            },
        ],
    }
    res2 = RV.apply_revision_with_safety_net(sop_file, automate_human_def)
    assert res2.success is False
    assert res2.status == "ROLLED_BACK"
    assert any("kind_from_human" in str(e).lower() for e in (res2.judgement.errors if res2.judgement else []))

    # 3. Attempt revision with dirty working tree -> must abort to preserve uncommitted work
    sop_file.write_text("# uncommitted edit\n", encoding="utf-8")
    res_dirty = RV.apply_revision_with_safety_net(sop_file, initial_def)
    assert res_dirty.success is False
    assert res_dirty.status == "ABORTED_DIRTY"


def test_cron_edge_cases_and_parsing() -> None:
    """Test cron parsing errors and special syntax."""
    with pytest.raises(SC.CronParseError):
        SC.CronSchedule.parse("too few fields")

    with pytest.raises(SC.CronParseError):
        SC.CronSchedule.parse("60 * * * *")  # minute 60 is out of bounds (0-59)

    with pytest.raises(SC.CronParseError):
        SC.CronSchedule.parse("* 25 * * *")  # hour 25 is out of bounds (0-23)

    with pytest.raises(SC.CronParseError):
        SC.CronSchedule.parse("* * 32 * *")  # day 32 is out of bounds (1-31)

    # Valid step expression
    c = SC.CronSchedule.parse("0 */4 * * *")
    assert c.hours == {0, 4, 8, 12, 16, 20}
    assert c.minutes == {0}


def test_cli_improvement_commands(tmp_path: Path) -> None:
    """Test invoking improvement-loop CLI subcommands."""
    from procflow.cli import main

    # 1. Test stats command on empty runs root
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    with pytest.raises(SystemExit) as exc:
        main(["stats", "--runs-root", str(runs_dir), "--json"])
    assert exc.value.code == 0

    # 2. Test harvest command
    trace_file = tmp_path / "sample.jsonl"
    trace_file.write_text(json.dumps({"step": "Init", "action": "Setup"}) + "\n" + json.dumps({"step": "Run", "action": "Test"}) + "\n")
    with pytest.raises(SystemExit) as exc:
        main(["harvest", "--traces-file", str(trace_file), "--json"])
    assert exc.value.code == 0

    # 3. Test tick command
    with pytest.raises(SystemExit) as exc:
        main(["tick", "--runs-root", str(runs_dir), "--json"])
    assert exc.value.code == 0

    # 4. Test notify command
    with pytest.raises(SystemExit) as exc:
        main(["notify", "--runs-root", str(runs_dir), "--json"])
    assert exc.value.code == 0

