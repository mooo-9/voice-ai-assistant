"""Settings → Skills switches a surface off for real.

The gate lives in tool *selection*: a disabled skill's tools are never sent
with the request, so the model cannot call one. These tests hold that line —
if the filter is ever dropped, a toggle in Settings becomes decoration.
"""
import json

import pytest


@pytest.fixture
def settings(tmp_path, monkeypatch):
    """brain reads data/settings.json relative to the working directory."""
    (tmp_path / "data").mkdir()
    monkeypatch.chdir(tmp_path)

    def write(**keys):
        (tmp_path / "data" / "settings.json").write_text(
            json.dumps(keys), encoding="utf-8")

    return write


def _offered(message: str) -> set[str]:
    from core.brain import _select_tools
    return {t["name"] for t in _select_tools(message)}


class TestGate:
    def test_a_disabled_skill_is_not_offered_to_the_model(self, settings):
        settings(skills_disabled=["gmail"])
        assert "send_email" not in _offered("email ali the notes")

    def test_the_other_skills_are_untouched(self, settings):
        settings(skills_disabled=["gmail"])
        offered = _offered("whatsapp omar that I'm late")
        assert "prepare_whatsapp_message" in offered

    def test_a_core_tool_is_gated_too(self, settings):
        # analyze_screen sits in _CORE_NAMES, so it is offered on every turn
        # unless the gate applies after the core set is assembled.
        settings(skills_disabled=["screen"])
        assert "analyze_screen" not in _offered("what's on my screen")

    def test_nothing_is_gated_by_default(self, settings):
        settings()
        assert "send_email" in _offered("email ali the notes")

    def test_a_missing_settings_file_gates_nothing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert "send_email" in _offered("email ali the notes")

    def test_every_named_skill_owns_at_least_one_tool(self):
        from core.brain import SKILL_TOOLS
        assert set(SKILL_TOOLS) == {
            "gmail", "whatsapp", "calendar", "todoist", "browser", "screen"}
        assert all(SKILL_TOOLS.values())


class TestSkillLabels:
    """SKILL_LABELS is what the Cockpit calls each connected app. A new skill
    surface has to be named there too."""

    def test_every_skill_is_named_and_described(self):
        import core.brain as brain
        assert set(brain.SKILL_LABELS) == set(brain.SKILL_TOOLS)
        for name, what in brain.SKILL_LABELS.values():
            assert name[0].isupper() and what
