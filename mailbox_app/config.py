import tomllib
from pathlib import Path

def load_config(path: Path | str | None = None) -> dict:
    """Read explicit/cwd config; resolve its paths relative to that file."""
    path = Path(path or "config.toml").expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(
            "Config not found. Copy config.example.toml to config.toml, edit it, "
            "then run from that directory or pass --config."
        )
    try:
        with path.open("rb") as f:
            config = tomllib.load(f)
    except tomllib.TOMLDecodeError:
        raise ValueError("Invalid TOML in configuration; check quoting and values.") from None
    except OSError:
        raise ValueError("Configuration cannot be read; check its permissions.") from None
    for key in ("data_dir", "oauth_client_secrets"):
        value = config.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Configuration requires a nonempty {key} path.")
        value_path = Path(value).expanduser()
        config[key] = str((path.parent / value_path).resolve())
    for key, default, maximum in (("server_port", 5000, 65535), ("backfill_days", 30, 36500)):
        value = config.get(key, default)
        if type(value) is not int or not 1 <= value <= maximum:
            raise ValueError(f"Configuration {key} must be an integer from 1 to {maximum}.")
        config[key] = value
    return config
