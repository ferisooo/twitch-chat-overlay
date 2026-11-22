import json

from overlay.settings import MAX_SAVED_NAME_COLORS, Settings, parse_tk_geometry


def test_parse_tk_geometry():
    assert parse_tk_geometry("380x520+80+90") == [80, 90, 380, 520]
    assert parse_tk_geometry("380x520+-10+5") == [-10, 5, 380, 520]
    assert parse_tk_geometry("junk") is None


def test_imports_old_settings_once(tmp_path):
    legacy = tmp_path / "old.json"
    legacy.write_text(
        json.dumps(
            {
                "platform": "merge",
                "channel": "#MyChannel",
                "tiktok_user": "@tok",
                "geometry": "400x600+10+20",
                "twitch_oauth": "secret-should-not-copy",
                "name_aliases": {"LongName": "Shorty", "": "x"},
                "name_colors": {"twitch:bob": "#FF7B7B", "twitch:bad": "red"},
                "opacity": 3,
            }
        ),
        encoding="utf-8",
    )
    path = tmp_path / "new" / "settings.json"
    s = Settings(path, legacy)
    assert s.imported_legacy
    assert s["platform"] == "merge"
    assert s["channel"] == "mychannel"
    assert s["tiktok_user"] == "tok"
    assert s["geometry"] == [10, 20, 400, 600]
    assert s["opacity"] == 1.0
    assert s["name_aliases"] == {"longname": "Shorty"}
    assert s["name_colors"] == {"twitch:bob": "#FF7B7B"}
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert "twitch_oauth" not in saved
    # Second start reads the new file, not the old one.
    again = Settings(path, legacy)
    assert not again.imported_legacy


def test_defaults_and_bad_file(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("not json", encoding="utf-8")
    s = Settings(path, tmp_path / "missing.json")
    assert s["platform"] == "twitch"
    assert s["font_size"] == 11
    assert not s.imported_legacy


def test_save_trims_name_colors(tmp_path):
    s = Settings(tmp_path / "s.json", tmp_path / "none.json")
    s.set("name_colors", {f"twitch:u{i}": "#FFFFFF" for i in range(MAX_SAVED_NAME_COLORS + 50)})
    assert s.save()
    data = json.loads((tmp_path / "s.json").read_text(encoding="utf-8"))
    assert len(data["name_colors"]) == MAX_SAVED_NAME_COLORS
    assert f"twitch:u{MAX_SAVED_NAME_COLORS + 49}" in data["name_colors"]
    assert not s.dirty
