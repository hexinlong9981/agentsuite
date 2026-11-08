"""Tests for secret_filter: PushGuard toggle and SourceCodeFilter tree sanitization."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from procflow import cli
from procflow.secret_filter import FilterResult, PushGuard, SourceCodeFilter


def test_push_guard_toggle(monkeypatch):
    # Default is enabled
    monkeypatch.delenv("PROCFLOW_PUSH_GUARD_ENABLED", raising=False)
    guard = PushGuard()
    assert guard.is_enabled
    allowed, msg = guard.verify_can_push("origin", "main")
    assert not allowed
    assert "blocked" in msg

    # Explicit toggle
    guard.set_enabled(False)
    assert not guard.is_enabled
    allowed, msg = guard.verify_can_push("origin", "main")
    assert allowed

    # Environment variable control: disabled
    monkeypatch.setenv("PROCFLOW_PUSH_GUARD_ENABLED", "0")
    guard2 = PushGuard()
    assert not guard2.is_enabled
    allowed, _ = guard2.verify_can_push()
    assert allowed

    # Environment variable control: enabled
    monkeypatch.setenv("PROCFLOW_PUSH_GUARD_ENABLED", "1")
    guard3 = PushGuard()
    assert guard3.is_enabled
    allowed, _ = guard3.verify_can_push()
    assert not allowed


def test_source_code_filter_sensitive_files(tmp_path: Path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()

    # Create safe files
    (src_dir / "app.py").write_text("print('hello world')\n", encoding="utf-8")
    (src_dir / "README.md").write_text("# Test project\n", encoding="utf-8")

    # Create sensitive files
    (src_dir / ".env").write_text("FOO=bar\n", encoding="utf-8")
    (src_dir / "server.key").write_text("dummy key\n", encoding="utf-8")
    (src_dir / "credentials.json").write_text("{}", encoding="utf-8")

    filt = SourceCodeFilter(enabled=True)
    res = filt.filter_directory(src_dir, block_sensitive_files=True)

    assert res.files_scanned == 5
    assert len(res.sensitive_files_blocked) == 3
    assert not (src_dir / ".env").exists()
    assert not (src_dir / "server.key").exists()
    assert not (src_dir / "credentials.json").exists()
    assert (src_dir / "app.py").exists()


def test_source_code_filter_content_redaction(tmp_path: Path):
    target = tmp_path / "config.py"
    target.write_text(
        "DB_PASS = 'password = \"very_secret_pass_12345\"'\n"
        "NORMAL_VAR = 'public_value'\n",
        encoding="utf-8",
    )

    filt = SourceCodeFilter(enabled=True)
    changed, count = filt.filter_file(target, in_place=True)

    assert changed
    assert count > 0
    content = target.read_text(encoding="utf-8")
    assert "very_secret_pass_12345" not in content
    assert "NORMAL_VAR = 'public_value'" in content


def test_private_key_body_fully_redacted(tmp_path: Path):
    # The whole key block (body included) must be removed, not just the BEGIN line.
    # Assemble the PEM markers at runtime so this test file carries no literal key header.
    kw = "PRIVATE KEY"
    begin = "-----BEGIN RSA " + kw + "-----"
    end = "-----END RSA " + kw + "-----"
    body = begin + "\nAAAABBBBCCCCDDDDEEEEFFFF\nGGGGHHHHIIIIJJJJKKKKLLLL\n" + end + "\n"
    target = tmp_path / "embedded.txt"  # non-sensitive filename so it is filtered, not blocked
    target.write_text("prefix line\n" + body + "suffix line\n", encoding="utf-8")

    filt = SourceCodeFilter(enabled=True)
    changed, count = filt.filter_file(target, in_place=True)
    content = target.read_text(encoding="utf-8")

    assert changed and count > 0
    assert "AAAABBBBCCCCDDDDEEEEFFFF" not in content
    assert "GGGGHHHHIIIIJJJJKKKKLLLL" not in content
    assert "BEGIN RSA PRIVATE KEY" not in content
    assert "prefix line" in content and "suffix line" in content


def test_filter_directory_does_not_follow_symlink_escape(tmp_path: Path):
    # A symlink inside the tree pointing outside must never cause action outside root.
    outside = tmp_path / "outside"
    outside.mkdir()
    secret_outside = outside / "secret.key"
    secret_outside.write_text("do not touch\n", encoding="utf-8")

    root = tmp_path / "tree"
    root.mkdir()
    (root / "ok.py").write_text("x = 1\n", encoding="utf-8")
    link = root / "link.key"
    try:
        link.symlink_to(secret_outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported here")

    filt = SourceCodeFilter(enabled=True)
    filt.filter_directory(root, block_sensitive_files=True)

    # The out-of-tree file must still exist (symlink was not followed/acted on).
    assert secret_outside.exists()


def test_cli_filter_source(tmp_path: Path, capsys):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "main.py").write_text("x = 1\n", encoding="utf-8")
    (src_dir / ".env").write_text("SECRET=1\n", encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        cli.main(["filter-source", str(src_dir), "--json"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["files_scanned"] == 2
    assert len(data["sensitive_files_blocked"]) == 1
