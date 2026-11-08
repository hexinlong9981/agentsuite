def test_package_imports():
    import knowledgebase

    assert knowledgebase.__version__


def test_shared_core_resolves():
    # agent-policy-kit must be importable.
    import agent_policy_kit

    assert hasattr(agent_policy_kit, "AuditLog")
