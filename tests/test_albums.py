from backup import albums, config
from backup.albums import albumflow as real_albumflow  # conftest replaces albums.albumflow by a fake


def test_missing_albumflow_is_an_error(capfd):
    assert not albums.albumflow_path().exists()
    assert real_albumflow("update-cloud") == 1
    assert "albumflow update-cloud not run: albumflow not found" in capfd.readouterr().err


def test_albumflow_path_in_the_project():
    assert albums.albumflow_path().parts[-5:] == ("src", "albumflow", ".venv", "Scripts", "albumflow.exe")
    assert str(albums.albumflow_path()).startswith(config.HOME)


def test_albumflow_pyz_on_the_primary_drive_when_not_installed(env, monkeypatch):
    env.init_primary()
    assert albums.albumflow_command() is None
    pyz = albums.albumflow_pyz()
    pyz.write_text("")
    assert albums.albumflow_command()[1:] == [str(pyz)]
