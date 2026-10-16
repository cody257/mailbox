from pathlib import Path
import pytest
from mailbox_app.config import load_config


def test_config_resolves_values_from_config_directory(tmp_path, monkeypatch):
    config_dir = tmp_path / "settings"
    config_dir.mkdir()
    path = config_dir / "config.toml"
    path.write_text('data_dir = "../mail"\noauth_client_secrets = "credentials.json"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    result = load_config(path)
    assert Path(result["data_dir"]) == tmp_path / "mail"
    assert Path(result["oauth_client_secrets"]) == config_dir / "credentials.json"
    assert result["server_port"] == 5000
    assert result["backfill_days"] == 30


def test_default_config_in_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.toml").write_text('data_dir = "~/Mailbox"\noauth_client_secrets = "~/client.json"\n', encoding="utf-8")
    assert Path(load_config()["data_dir"]) == Path.home() / "Mailbox"


@pytest.mark.parametrize("content", [
    'data_dir = 123', 'data_dir = ""', 'data_dir = "mail"',
    'data_dir = "mail"\noauth_client_secrets = "client.json"\nserver_port = 0',
    'data_dir = "mail"\noauth_client_secrets = "client.json"\nbackfill_days = -1',
    'data_dir = [',
])
def test_invalid_config_is_actionable_without_private_paths(tmp_path, content):
    path = tmp_path / "private-config.toml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        load_config(path)
    assert str(tmp_path) not in str(exc.value)


def test_missing_config(tmp_path):
    with pytest.raises(FileNotFoundError, match="config.example.toml"):
        load_config(tmp_path / "absent.toml")
