def test_package_imports():
    import reportsite

    assert reportsite.__version__ == "0.0.1"


def test_shared_core_resolves():
    import agent_policy_kit

    assert hasattr(agent_policy_kit, "SecretMasker")
