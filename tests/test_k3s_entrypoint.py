from __future__ import annotations

import pytest


@pytest.mark.parametrize(
    ("role", "tail"),
    [
        ("api", ["uvicorn", "titanium.web.live_app:app", "--host", "0.0.0.0"]),
        ("deepseek-worker", ["tools/analystes.py"]),
        (
            "mt5-demo-adapter",
            ["uvicorn", "titanium.web.mt5_adapter:app", "--host", "0.0.0.0"],
        ),
    ],
)
def test_role_commands_are_explicit_and_bounded(role, tail):
    from deploy.k3s.entrypoint import command_for_role

    command = command_for_role(role)
    assert command[0]
    for part in tail:
        assert part in command


def test_unknown_role_is_rejected():
    from deploy.k3s.entrypoint import command_for_role

    with pytest.raises(ValueError, match="role K3s inconnu"):
        command_for_role("real-trader")

