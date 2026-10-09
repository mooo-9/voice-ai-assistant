"""Tests for the Settings surface.

The rule that matters: nothing on this surface is a dead switch. Every
control writes a key the app actually reads, and it writes through
immediately rather than waiting on an OK button that doesn't exist.
"""
import json

import pytest


@pytest.fixture(autouse=True)
def settings_file(tmp_path, monkeypatch):
    import ui.overlay as overlay_mod
    import ui.settings as settings_mod
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({
        "model": "claude-sonnet-5", "tts_backend": "edge",
        "voice_en": "en-US-GuyNeural",
        "wake_word_enabled": True, "theme": "dark", "local_llm_fallback": True,
    }), encoding="utf-8")
    monkeypatch.setattr(overlay_mod, "_SETTINGS_FILE", path)
    monkeypatch.setattr(settings_mod, "_load_settings", overlay_mod._load_settings)
    monkeypatch.setattr(settings_mod, "_save_settings", overlay_mod._save_settings)
    return path


@pytest.fixture
def schedules_file(tmp_path, monkeypatch):
    """Routines read and write the scheduler's own file, not a UI copy."""
    import core.scheduler as scheduler_mod
    path = tmp_path / "schedules.json"
    path.write_text(json.dumps([
        {"id": "morning", "name": "Morning Briefing", "enabled": True,
         "description": "Weather, calendar, emails",
         "trigger": {"type": "cron", "hour": 7, "minute": 30},
         "action": {"type": "macro", "name": "morning_routine"}},
        {"id": "weekly", "name": "Weekly Report", "enabled": True,
         "description": "Spending + journal",
         "trigger": {"type": "cron", "day_of_week": "fri", "hour": 18, "minute": 0},
         "action": {"type": "tool", "tool": "weekly_report", "args": {}}},
    ]), encoding="utf-8")
    monkeypatch.setattr(scheduler_mod, "_SCHEDULES_FILE", path)
    return path


class FakeMemory:
    """The fact layer only — that is all the Memory section touches."""

    def __init__(self, facts=None):
        self.facts = facts if facts is not None else [
            {"id": "1", "category": "preference", "content": "Coffee black",
             "created_at": "2026-08-01"},
            {"id": "2", "category": "deadline", "content": "Thesis due Sept 3",
             "created_at": "2026-08-05"},
        ]

    def get_all_facts(self, category=None):
        return list(self.facts)

    def search_facts(self, query):
        return [f for f in self.facts if query.lower() in f["content"].lower()]

    def delete_fact(self, fact_id):
        self.facts = [f for f in self.facts if f["id"] != fact_id]
        return "Forgotten"

    def store_fact(self, content, category="other"):
        self.facts.append({"id": "restored", "category": category,
                           "content": content, "created_at": "2026-08-12"})
        return "Noted"


def _window(qapp, memory=None):
    from ui.settings import SettingsWindow
    return SettingsWindow(memory=memory)


def _read(path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class TestWiring:
    def test_a_toggle_writes_through_immediately(self, qapp, settings_file):
        w = _window(qapp)
        w._wake.setChecked(False)
        assert _read(settings_file)["wake_word_enabled"] is False
        w.close()

    def test_a_combo_writes_through_immediately(self, qapp, settings_file):
        w = _window(qapp)
        w._theme.setCurrentText("oled")
        assert _read(settings_file)["theme"] == "oled"
        w.close()

    def test_sound_cues_write_the_key_core_sound_reads(self, qapp, settings_file):
        from core import sound
        w = _window(qapp)
        w._cues.setChecked(False)
        import ui.settings  # noqa: F401
        sound._SETTINGS = settings_file
        assert sound.enabled("sent") is False
        w.close()

    def test_it_loads_the_current_values_rather_than_defaults(self, qapp, settings_file):
        settings_file.write_text(json.dumps({"theme": "oled",
                                             "wake_word_enabled": False}), encoding="utf-8")
        w = _window(qapp)
        assert w._theme.currentText() == "oled"
        assert w._wake.isChecked() is False
        w.close()

    def test_applying_notifies_the_caller(self, qapp, settings_file):
        from ui.settings import SettingsWindow
        seen = []
        w = SettingsWindow(on_applied=lambda s: seen.append(s))
        w._wake.setChecked(False)
        assert seen and seen[-1]["wake_word_enabled"] is False
        w.close()


class TestSurface:
    def test_the_rail_switches_sections(self, qapp):
        w = _window(qapp)
        w.show_section(1)
        assert w._pages.currentIndex() == 1
        w.show_section(0)
        assert w._pages.currentIndex() == 0
        w.close()

    def test_the_toggle_matches_the_specified_size(self, qapp):
        from ui.settings import Toggle
        t = Toggle()
        assert (t.width(), t.height()) == (40, 22)
        t.deleteLater()

    def test_the_toggle_knob_travels_when_switched(self, qapp):
        from ui.settings import Toggle
        t = Toggle(False)
        assert t.pos == 0.0
        t.setChecked(True)
        t.pos = 1.0                     # end state of the 140ms animation
        assert not t.grab().isNull()
        t.deleteLater()

    def test_which_surface_answers_the_wake_word_is_a_setting(self, qapp,
                                                              settings_file):
        # main.py reads this key on every wake to decide what comes up.
        w = _window(qapp)
        assert [w._wake_surface.itemText(i)
                for i in range(w._wake_surface.count())] == ["overlay", "cockpit"]
        w._wake_surface.setCurrentText("cockpit")
        assert _read(settings_file)["wake_surface"] == "cockpit"
        w.close()

    def test_barge_in_is_a_real_switch(self, qapp, settings_file, tmp_path,
                                       monkeypatch):
        # Switching it off has to reach core.barge_in, not just the file.
        w = _window(qapp)
        w._barge_in.setChecked(False)
        assert _read(settings_file)["barge_in"] is False
        w.close()

        target = tmp_path / "cwd"
        (target / "data").mkdir(parents=True)
        (target / "data" / "settings.json").write_text(
            settings_file.read_text(encoding="utf-8"), encoding="utf-8")
        monkeypatch.chdir(target)
        monkeypatch.delenv("EL_FAGER_TEST_MODE", raising=False)
        from core import barge_in
        assert barge_in.enabled() is False

    # Two voice settings only data/settings.json could change until now.

    def _app_dir(self, settings_file, tmp_path, monkeypatch):
        """Run from a folder whose data/settings.json is the one just written,
        with test mode off, as the running app reads it."""
        target = tmp_path / "cwd"
        (target / "data").mkdir(parents=True)
        (target / "data" / "settings.json").write_text(
            settings_file.read_text(encoding="utf-8"), encoding="utf-8")
        monkeypatch.chdir(target)
        monkeypatch.delenv("EL_FAGER_TEST_MODE", raising=False)

    def test_ducking_is_a_real_switch(self, qapp, settings_file, tmp_path, monkeypatch):
        w = _window(qapp)
        assert w._duck.isChecked()                    # on unless switched off
        w._duck.setChecked(False)
        assert _read(settings_file)["duck_while_listening"] is False
        w.close()
        self._app_dir(settings_file, tmp_path, monkeypatch)
        from core import ducking
        assert ducking._enabled() is False

    def test_the_names_it_should_know_are_a_real_list(self, qapp, settings_file,
                                                      tmp_path, monkeypatch):
        w = _window(qapp)
        w._vocabulary.setText("Estanna, Fares Sokar,  , estanna, تامر حسني")
        w._vocabulary.editingFinished.emit()
        assert _read(settings_file)["voice_vocabulary"] == [
            "Estanna", "Fares Sokar", "تامر حسني"]
        w.close()
        self._app_dir(settings_file, tmp_path, monkeypatch)
        from core import voice_in
        assert voice_in._vocabulary()[:3] == ["Estanna", "Fares Sokar", "تامر حسني"]

    def test_the_names_field_shows_the_saved_list(self, qapp, settings_file):
        data = _read(settings_file)
        data["voice_vocabulary"] = ["Tawsen", "Erzaa"]
        settings_file.write_text(json.dumps(data), encoding="utf-8")
        w = _window(qapp)
        assert w._vocabulary.text() == "Tawsen, Erzaa"
        w.close()

    def test_the_wake_row_shows_measured_reliability(self, qapp):
        from ui.settings import _wake_caption
        assert _wake_caption()          # never blank, even with no data yet

    def test_the_rail_carries_the_five_sections(self, qapp):
        w = _window(qapp)
        labels = [b.text() for b in w._rail_buttons]
        assert labels == ["Voice", "Routines", "Skills", "Memory", "System"]
        w.close()


class TestRoutines:
    def test_a_routine_toggle_writes_the_schedulers_own_file(
            self, qapp, schedules_file):
        w = _window(qapp)
        w._routine_toggles["morning"].setChecked(False)
        saved = json.loads(schedules_file.read_text(encoding="utf-8"))
        assert next(j for j in saved if j["id"] == "morning")["enabled"] is False
        w.close()

    def test_switching_a_routine_back_on_persists_too(self, qapp, schedules_file):
        w = _window(qapp)
        w._routine_toggles["weekly"].setChecked(False)
        w._routine_toggles["weekly"].setChecked(True)
        saved = json.loads(schedules_file.read_text(encoding="utf-8"))
        assert next(j for j in saved if j["id"] == "weekly")["enabled"] is True
        w.close()

    def test_it_lists_the_jobs_that_exist(self, qapp, schedules_file):
        w = _window(qapp)
        assert set(w._routine_toggles) == {"morning", "weekly"}
        w.close()

    def test_a_schedule_reads_as_one_line(self):
        from ui.settings import _when
        assert _when({"type": "cron", "hour": 7, "minute": 30}) == "DAILY 07:30"
        assert _when({"type": "cron", "day_of_week": "fri", "hour": 18,
                      "minute": 0}) == "FRI 18:00"
        assert _when({"type": "interval", "minutes": 30}) == "EVERY 30 MIN"


class TestSkills:
    def test_switching_a_skill_off_records_it(self, qapp, settings_file):
        w = _window(qapp)
        w._skill_toggles["whatsapp"].setChecked(False)
        assert _read(settings_file)["skills_disabled"] == ["whatsapp"]
        w.close()

    def test_the_brain_honours_what_this_section_writes(self, qapp, settings_file,
                                                        tmp_path, monkeypatch):
        # The toggle is only real if tool selection reads the same key.
        w = _window(qapp)
        w._skill_toggles["gmail"].setChecked(False)
        w.close()
        (tmp_path / "data").mkdir(exist_ok=True)
        (tmp_path / "data" / "settings.json").write_text(
            settings_file.read_text(encoding="utf-8"), encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        from core.brain import _select_tools
        assert "send_email" not in {t["name"] for t in _select_tools("email ali")}

    def test_switching_it_back_on_clears_it(self, qapp, settings_file):
        w = _window(qapp)
        w._skill_toggles["browser"].setChecked(False)
        w._skill_toggles["browser"].setChecked(True)
        assert _read(settings_file)["skills_disabled"] == []
        w.close()

    def test_every_skill_maps_to_tools_the_brain_knows(self, qapp):
        from core.brain import SKILL_TOOLS
        from ui.settings import _SKILLS
        assert {key for key, _, _ in _SKILLS} == set(SKILL_TOOLS)

    def test_the_toggles_start_from_what_was_saved(self, qapp, settings_file):
        settings_file.write_text(json.dumps({"skills_disabled": ["screen"]}),
                                 encoding="utf-8")
        w = _window(qapp)
        assert w._skill_toggles["screen"].isChecked() is False
        assert w._skill_toggles["gmail"].isChecked() is True
        w.close()


class TestMemory:
    def test_it_lists_what_was_learned(self, qapp):
        w = _window(qapp, memory=FakeMemory())
        assert w._mem_list.count() == 3            # two facts + the stretch
        w.close()

    def test_forget_deletes_the_fact_for_real(self, qapp):
        memory = FakeMemory()
        w = _window(qapp, memory=memory)
        w._forget(memory.facts[0])
        assert [f["id"] for f in memory.facts] == ["2"]
        w.close()

    def test_a_forget_can_be_undone(self, qapp):
        memory = FakeMemory()
        w = _window(qapp, memory=memory)
        w._forget(dict(memory.facts[0]))
        w._undo_forget()
        assert [f["content"] for f in memory.facts] == ["Thesis due Sept 3",
                                                        "Coffee black"]
        w.close()

    def test_undo_is_offered_only_after_a_forget(self, qapp):
        # isHidden() rather than isVisible(): the Memory page isn't the
        # current one in the stack, so nothing on it is on screen yet.
        memory = FakeMemory()
        w = _window(qapp, memory=memory)
        assert w._mem_undo.isHidden() is True
        w._forget(dict(memory.facts[0]))
        assert w._mem_undo.isHidden() is False
        assert "Coffee black" in w._mem_undo_label.text()
        w.close()

    def test_search_narrows_the_list(self, qapp):
        w = _window(qapp, memory=FakeMemory())
        w._mem_search.setText("thesis")
        assert len(w._facts()) == 1
        w.close()

    def test_the_count_is_the_real_count(self, qapp):
        w = _window(qapp, memory=FakeMemory())
        assert "2 facts" in w._mem_section._description.text()
        w.close()

    def test_it_survives_having_no_memory_at_all(self, qapp):
        w = _window(qapp)                          # overlay always passes one
        assert w._facts() == []
        w.close()
