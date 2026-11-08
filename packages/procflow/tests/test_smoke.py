def test_package_imports():
    import procflow

    assert procflow.__version__ == "0.0.1"


def test_shared_core_resolves():
    import agent_policy_kit

    assert hasattr(agent_policy_kit, "Approval")
