"""Тести обгортки run_headless.py: межі автономного запуску задані до старту."""

import pytest

import run_headless


def _values_after(command, flag):
    """Значення прапорця зі списком: усе до наступного `--щось`."""
    start = command.index(flag) + 1
    values = []
    for item in command[start:]:
        if item.startswith("--"):
            break
        values.append(item)
    return values


def test_read_profile_has_no_writing_or_shell_tools():
    command = run_headless.build_command("list files")
    allowed = _values_after(command, "--allowedTools")
    assert allowed == ["Read", "Glob", "Grep"]


def test_unlisted_tools_are_denied_not_prompted():
    command = run_headless.build_command("x")
    assert _values_after(command, "--permission-mode") == ["dontAsk"]


def test_limits_are_always_present():
    command = run_headless.build_command("x", max_turns=7, budget_usd=0.5)
    assert _values_after(command, "--max-turns") == ["7"]
    assert _values_after(command, "--max-budget-usd") == ["0.5"]


def test_edit_profile_allows_shell_only_by_prefix():
    allowed = _values_after(
        run_headless.build_command("x", profile="edit"), "--allowedTools"
    )
    assert "Edit" in allowed
    assert "Bash" not in allowed
    assert all(tool.endswith(":*)") for tool in allowed if tool.startswith("Bash"))


def test_push_and_network_denied_in_every_profile():
    for profile in run_headless.PROFILES:
        denied = _values_after(
            run_headless.build_command("x", profile=profile), "--disallowedTools"
        )
        assert "Bash(git push:*)" in denied
        assert "WebFetch" in denied


def test_never_bypasses_hooks_or_permissions():
    for profile in run_headless.PROFILES:
        joined = " ".join(run_headless.build_command("x", profile=profile))
        assert "--bare" not in joined
        assert "bypassPermissions" not in joined
        assert "--dangerously-skip-permissions" not in joined


def test_prompt_comes_before_variadic_tool_lists():
    command = run_headless.build_command("delete the README")
    assert command.index("delete the README") < command.index("--allowedTools")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"profile": "everything"},
        {"max_turns": 0},
        {"max_turns": run_headless.MAX_TURNS_CEILING + 1},
        {"budget_usd": 0},
    ],
)
def test_invalid_limits_are_rejected(kwargs):
    with pytest.raises(ValueError):
        run_headless.build_command("x", **kwargs)


def test_empty_prompt_is_rejected():
    with pytest.raises(ValueError):
        run_headless.build_command("   ")


def test_dry_run_prints_without_launching(capsys, monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("dry-run не має запускати claude")

    monkeypatch.setattr(run_headless.subprocess, "run", fail)
    assert run_headless.main(["--dry-run", "hello"]) == 0
    assert "dontAsk" in capsys.readouterr().out
