import sys
from pathlib import Path

# Add scripts directory to sys.path
SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from pre_publish_scan import scan_file, run_scan


def test_scan_detects_private_ip(tmp_path):
    p = tmp_path / "bad_ip.txt"
    p.write_text("connect to 192.168.1.50\n", encoding="utf-8")
    violations = scan_file(p)
    assert any(rule == "Private IPv4 Address" for _, rule, _ in violations)


def test_scan_detects_internal_ticket(tmp_path):
    p = tmp_path / "ticket.txt"
    p.write_text("Fix issue in CHO-12345\n", encoding="utf-8")
    violations = scan_file(p)
    assert any(rule == "Internal Project / Ticket Pattern" for _, rule, _ in violations)


def test_scan_detects_internal_path(tmp_path):
    p = tmp_path / "path.txt"
    p.write_text("saved to /05_workspace/tasks/sample\n", encoding="utf-8")
    violations = scan_file(p)
    assert any(rule == "Internal Path Pattern" for _, rule, _ in violations)


def test_scan_detects_private_key(tmp_path):
    p = tmp_path / "key.txt"
    p.write_text("-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n", encoding="utf-8")
    violations = scan_file(p)
    assert any(rule == "Private Key" for _, rule, _ in violations)


def test_scan_passes_clean_repository():
    root_dir = Path(__file__).resolve().parents[3]
    exit_code = run_scan(root_dir)
    assert exit_code == 0
