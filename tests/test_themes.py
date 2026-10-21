"""Themes: neutral Dark, Emerald (the old green Dark), Midnight, Light and Custom."""
import json
from pathlib import Path

import pytest

import themes

SNAPSHOT = json.loads((Path(__file__).parent / "data" / "themes_1.3.0.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", ["emerald", "midnight", "light"])
def test_existing_themes_look_exactly_as_in_1_3_0(name):
    pal = themes.THEMES[name]
    assert themes.base_stylesheet(pal) == SNAPSHOT[name]
    assert themes.tab_stylesheet(pal) == SNAPSHOT["tabs"][name]


def test_every_theme_has_every_colour():
    for name, pal in themes.THEMES.items():
        assert set(pal) == set(themes.ROLE_KEYS), name
        assert all(themes.is_color(v) for v in pal.values()), name


def _saturation(hex_color):
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return max(r, g, b) - min(r, g, b)


def test_dark_is_neutral_grey_not_green():
    dark = themes.THEMES["dark"]
    for role in ("bg", "surface", "button", "border", "bar", "hover", "pressed", "text", "accent", "list_bg"):
        assert _saturation(dark[role]) <= 12, role
    assert themes.base_stylesheet(dark) != themes.base_stylesheet(themes.THEMES["emerald"])
    assert not themes.is_light(dark) and themes.is_light(themes.THEMES["light"])


def test_custom_theme_overrides_its_base():
    pal = themes.custom_palette({"base": "light", "colors": {"bg": "#123456", "text": "red", "nope": "#000000"}})
    assert pal["bg"] == "#123456"
    assert pal["text"] == themes.THEMES["light"]["text"]       # not a colour: ignored
    assert "nope" not in pal
    assert themes.custom_palette(None) == themes.THEMES["dark"]
    assert themes.palette_for({"theme": "custom", "custom_theme": {"base": "midnight", "colors": {}}}) == \
        themes.THEMES["midnight"]
    assert themes.palette_for({"theme": "unknown"}) == themes.THEMES["dark"]


def test_custom_theme_editor(main, qapp, tmp_path):
    seen = []
    dlg = main.CustomThemeDialog(dict(main.DEFAULTS, theme="emerald"), preview=seen.append)
    assert dlg.palette() == themes.THEMES["emerald"]          # starts from the theme in use
    assert dlg.set_color("bg", "#202020") and seen[-1]["bg"] == "#202020"
    assert not dlg.set_color("bg", "nonsense")
    dlg._rows["text"][1].setText("abcdef")                    # typed without "#"
    dlg._typed("text")
    assert dlg.palette()["text"] == "#abcdef"
    saved = dlg.result_theme()
    assert saved["base"] == "emerald" and saved["colors"]["bg"] == "#202020"
    # export → import round trip
    path = tmp_path / "theme.json"
    path.write_text(json.dumps({"pathrenamer_theme": 1, **saved}))
    other = main.CustomThemeDialog(dict(main.DEFAULTS))
    assert other.import_file(str(path)) and other.palette()["bg"] == "#202020"
    # reopening keeps the saved colours
    again = main.CustomThemeDialog(dict(main.DEFAULTS, theme="custom", custom_theme=saved))
    assert again.palette()["text"] == "#abcdef"


def test_choosing_custom_theme_in_the_window(main, make_window, monkeypatch):
    w = make_window(theme="dark")
    assert [a.text() for a in w.act_theme.values()] == [
        "Dark Theme", "Emerald Theme", "Midnight Theme", "Light Theme", "Custom Theme"]

    def fake_exec(self):
        self.set_color("bg", "#2b1f3a")
        return True
    monkeypatch.setattr(main.CustomThemeDialog, "exec", fake_exec)
    w._set_theme("custom")                       # no custom theme yet: the editor opens
    assert w.cfg["theme"] == "custom" and w.cfg["custom_theme"]["colors"]["bg"] == "#2b1f3a"
    assert "#2b1f3a" in w.styleSheet()
    assert w.act_theme["custom"].isChecked()
    w._set_theme("emerald")
    assert "#0b100b" in w.styleSheet() and w.act_theme["emerald"].isChecked()
