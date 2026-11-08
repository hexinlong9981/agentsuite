import pytest

from procflow import defschema, guard


def test_parse_refs_and_literals():
    n = guard.parse("(a.b == 'x' or not c) and d in [1, 2]")
    assert guard.refs(n) == {("a", "b"), ("c",), ("d",)}


@pytest.mark.parametrize("text", ["f(x)", "a +", "", "a.", "__import__('os')", "a == b ="])
def test_unreadable_expressions_are_rejected(text):
    with pytest.raises(guard.GuardError):
        guard.parse(text)


def test_evaluate_without_eval():
    n = guard.parse("round < 3 and state == 'ok'")
    ctx = {("round",): 2, ("state",): "ok"}
    assert guard.evaluate(n, lambda p: ctx[p]) is True
    with pytest.raises(guard.GuardError):  # ordering a missing value is refused, not guessed
        guard.evaluate(guard.parse("missing < 3"), lambda p: ctx[p])


def test_parse_default_forms():
    assert guard.parse_default("${config.limits.max_depth}") == ("config", "limits.max_depth")
    assert guard.parse_default("${today:YYYY-MM}") == ("today", "YYYY-MM")
    assert guard.parse_default(5) == ("static", 5)
    with pytest.raises(guard.GuardError):
        guard.parse_default("x ${now}")
    with pytest.raises(guard.GuardError):
        guard.parse_default("${os.environ}")


def test_type_system():
    assert defschema.valid_type("list[list[int]]") and not defschema.valid_type("list[nope]")
    assert defschema.type_compatible("int", "number") and not defschema.type_compatible("number", "int")
    assert defschema.type_compatible("any", "bool") and not defschema.type_compatible("list[int]", "int")
    assert defschema.value_matches([1, 2], "list[int]") and not defschema.value_matches("x", "int")
