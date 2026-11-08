from agent_policy_kit import SecretMasker


def test_mask_text_url_and_kv():
    m = SecretMasker()
    out = m.mask_text("db = postgres://user:supersecret@host/db\nAPI_TOKEN = abc123xyz")
    assert "supersecret" not in out
    assert "abc123xyz" not in out
    assert "***MASKED***" in out


def test_mask_text_private_key():
    m = SecretMasker()
    pem = "-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\n-----END OPENSSH PRIVATE KEY-----"
    assert "AAAA" not in m.mask_text(pem)


def test_mask_obj_recurses_and_redacts_keys():
    m = SecretMasker()
    obj = {
        "name": "alice",
        "password": "hunter2",
        "nested": {"api_key": "k-123", "note": "token=PLAIN"},
        "items": ["postgres://u:pw@h/db"],
    }
    out = m.mask_obj(obj)
    assert out["name"] == "alice"
    assert out["password"] == "***MASKED***"
    assert out["nested"]["api_key"] == "***MASKED***"
    assert "pw" not in out["items"][0]


def test_extra_pattern():
    m = SecretMasker(builtin=False, extra_patterns=[r"ZZ[0-9]+"])
    assert m.mask_text("code ZZ999 here") == "code ***MASKED*** here"
