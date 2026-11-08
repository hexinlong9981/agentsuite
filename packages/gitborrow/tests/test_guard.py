"""Pillar 2 regression tests. Do not loosen a REJECT to make it pass."""

import pytest

from gitborrow.core import guard
from gitborrow.core.errors import GuardViolation, RefNotAllowed


def test_push_forbidden_when_enabled():
    with pytest.raises(GuardViolation):
        guard.validate_git_command(["push", "origin", "HEAD"], enforce=True)


def test_push_allowed_when_disabled():
    # Disabling the guard turns off write protection (documented trade-off).
    guard.validate_git_command(["push", "origin", "HEAD"], enforce=False)


def test_option_injection_blocked_even_when_disabled():
    for argv in (
        ["clone", "--upload-pack=touch x", "url", "dir"],
        ["clone", "-c", "core.sshCommand=evil", "url", "dir"],
        ["fetch", "--exec=evil"],
    ):
        with pytest.raises(GuardViolation):
            guard.validate_git_command(argv, enforce=False)
        with pytest.raises(GuardViolation):
            guard.validate_git_command(argv, enforce=True)


def test_unknown_subcommand_rejected_when_enabled():
    with pytest.raises(GuardViolation):
        guard.validate_git_command(["frobnicate"], enforce=True)


def test_allowed_clone_and_log():
    guard.validate_git_command(["clone", "--depth", "1", "url", "dir"], enforce=True)
    guard.validate_git_command(["log", "--grep", "TICKET-1"], enforce=True)
    guard.validate_git_command(["rev-list", "--count", "HEAD"], enforce=True)


def test_ref_leading_dash_rejected():
    with pytest.raises(RefNotAllowed):
        guard.validate_ref("--upload-pack=evil")
    assert guard.validate_ref("feature/main-1.0") == "feature/main-1.0"


def test_bad_url_rejected():
    with pytest.raises(GuardViolation):
        guard.validate_url("--upload-pack=evil")
