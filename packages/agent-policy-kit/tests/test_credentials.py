from dataclasses import fields

import pytest

from agent_policy_kit import (
    CredentialHandle,
    EnvPassthroughStore,
    FileCredentialStore,
    NullCredentialStore,
)


def test_null_store_is_empty():
    assert NullCredentialStore().resolve("anything").env == {}


def test_env_passthrough(monkeypatch):
    monkeypatch.setenv("SRC_TOKEN", "a-secret-value")
    handle = EnvPassthroughStore({"DEST_TOKEN": "SRC_TOKEN"}).resolve("x")
    assert handle.env == {"DEST_TOKEN": "a-secret-value"}


def test_env_passthrough_skips_missing(monkeypatch):
    monkeypatch.delenv("NOT_SET", raising=False)
    assert EnvPassthroughStore({"D": "NOT_SET"}).resolve("x").env == {}


def test_handle_exposes_only_env():
    # The handle must not grow a field that returns a bare secret to the caller.
    assert [f.name for f in fields(CredentialHandle)] == ["env"]


def test_file_credential_store_key_value(tmp_path):
    cred_file = tmp_path / "secrets.env"
    cred_file.write_text("DB_PASS=secret123\nAPI_KEY=tok_abc\n# comment\n", encoding="utf-8")

    store = FileCredentialStore(cred_file)
    handle = store.resolve("target1")
    assert handle.env == {"DB_PASS": "secret123", "API_KEY": "tok_abc"}


def test_file_credential_store_json(tmp_path):
    cred_file = tmp_path / "secrets.json"
    cred_file.write_text('{"AWS_SECRET": "sec456", "PORT": "5432"}', encoding="utf-8")

    store = FileCredentialStore(cred_file)
    handle = store.resolve("target2")
    assert handle.env == {"AWS_SECRET": "sec456", "PORT": "5432"}


def test_file_credential_store_with_target_map(tmp_path):
    cred_file = tmp_path / "secrets.env"
    cred_file.write_text("RAW_TOKEN=xyz_token\nIGNORED=foo\n", encoding="utf-8")

    store = FileCredentialStore(
        cred_file,
        target_map={
            "app": {"AUTH_BEARER": "RAW_TOKEN"},
        },
    )
    handle = store.resolve("app")
    assert handle.env == {"AUTH_BEARER": "xyz_token"}


def test_file_credential_store_strict_permissions(tmp_path):
    cred_file = tmp_path / "insecure.env"
    cred_file.write_text("KEY=val\n", encoding="utf-8")
    cred_file.chmod(0o666)  # world readable

    store = FileCredentialStore(cred_file, strict_permissions=True)
    with pytest.raises(PermissionError, match="insecure permissions"):
        store.resolve("target")

