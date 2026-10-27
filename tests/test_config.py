import pytest

from backup import config

# The exclude list of the original single-file backup.py, to prove the move kept it exactly.
ORIGINAL_EXCLUDES = [
    R"C:\Users\Senso\Large\Pictures\Pictures\new\**\*.arw",
    R"C:\Users\Senso\Large\Pictures\Pictures\new-old\**\*.arw",
    R"C:\Users\senso\Large\Pictures\Rejected\**\*.arw",
    R"S:\Large\Pictures\Pictures\new\**\*.arw",
    R"S:\Large\Pictures\Pictures\new-old\**\*.arw",
    R"S:\Large\Pictures\Rejected\**\*.arw",
    R"**\thumbnails-digikam.db",
    R"**\*.acr",
    R"**\.git\**",
    R"C:\Users\Senso\src\**\build*\**",
    R"C:\Users\Senso\NTUSER.DAT*",
    R"**\.dtrash\**",
    R"C:\Users\Senso\Large\Pictures\Temp\**",
    R"C:\Users\Senso\Large\Videos-store\**",  # added since: movies
    R"C:\Users\Senso\Large\Videos\*\*",
    R"C:\Users\Senso\Large\Pictures\Lightroom\Lightroom Catalog Previews.lrdata\**",
    R"C:\Users\Senso\Large\Pictures\Lightroom\Lightroom Catalog Smart Previews.lrdata\**",
    R"C:\Users\Senso\CrossDevice\**",
    R"C:\Users\Senso\OneDrive\**",
    R"C:\Users\Senso\Downloads\**",
    R"C:\Users\Senso\src\vcpkg\**",
    R"C:\Users\Senso\src\external\**",
    R"C:\Users\Senso\.vscode\extensions\**",
    R"S:\rejected\**",
    R"S:\$RECYCLE.BIN\**",
    R"C:\Users\Senso\AppData\Local\*\*.db-snp-*",
    R"C:\Users\Senso\AppData\Local\*\*.db-pre-*",
    R"C:\Users\Senso\AppData\Roaming\Adobe\CameraRaw\ModelZoo\**",
    R"C:\Users\Senso\AppData\Roaming\Adobe\CameraRaw\Logs\**",
    R"C:\Users\Senso\AppData\Roaming\syntevo\SmartGit\updates\**",
    R"C:\Users\Senso\AppData\Roaming\syntevo\SmartGit\*\logs\**",
    R"C:\Users\Senso\AppData\Roaming\syntevo\SmartGit\*\*lock*",
    R"C:\Users\Senso\AppData\Roaming\Mozilla\Firefox\Profiles\*\parent.lock",
]


def production(monkeypatch):
    monkeypatch.setattr(config, "HOME", R"C:\Users\Senso")
    monkeypatch.setattr(config, "SD", "S:\\")


def test_excludes_match_original_script(monkeypatch):
    production(monkeypatch)
    # restic matches them case-insensitively (--iexclude)
    assert [p.lower() for p in config.excludes()] == [p.lower() for p in ORIGINAL_EXCLUDES]


def test_production_paths(monkeypatch):
    production(monkeypatch)
    assert config.source_paths() == [R"C:\Users\Senso", "S:\\"]
    assert config.sd_marker() == R"S:\.backup-source-sd"
    assert config.appdata() == R"C:\Users\Senso\AppData"
    assert config.git_search_roots() == [R"C:\Users\Senso\src", R"C:\Users\Senso\bin", R"C:\Users\Senso\Documents"]
    assert config.git_repos() == [R"C:\Users\Senso", R"C:\Users\Senso\Large\Pictures"]


def test_paths_follow_home_and_sd(env):
    assert config.source_paths() == [str(env.home), str(env.sd)]
    assert all(str(env.root) in pattern for pattern in config.excludes() if not pattern.startswith("**"))


def test_settings_default_when_missing(env):
    assert config.settings() == config.DEFAULT_SETTINGS


def test_settings_merged_with_defaults(env):
    env.write_settings(backup_all_includes_update_cloud=True)
    assert config.settings() == {"backup_all_includes_update_cloud": True}


def test_unknown_setting_is_an_error(env):
    env.write_settings(backup_all_includes_delta=True)  # the old name
    with pytest.raises(SystemExit, match="Unknown settings in .*: backup_all_includes_delta"):
        config.settings()


def test_invalid_settings_file_is_a_clear_error(env):
    env.settings.parent.mkdir(parents=True, exist_ok=True)
    env.settings.write_text('{\n  "backup_all_includes_update_cloud": true,\n}\n', encoding="utf-8")
    with pytest.raises(SystemExit, match="is not valid JSON: Illegal trailing comma"):
        config.settings()
