"""Exercise the built wheel from a separate directory and interpreter."""
import json
import os
from pathlib import Path
import subprocess
import sys
import venv
import zipfile


def checked(command, cwd, env=None):
    result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, (result.stdout + result.stderr)[-6000:]
    return result.stdout


def test_wheel_installed_outside_checkout_serves_assets(tmp_path):
    repository = Path(__file__).resolve().parents[1]
    wheel_dir = tmp_path / "wheels"
    checked([sys.executable, "-m", "build", "--wheel", "--no-isolation", "--outdir", str(wheel_dir)], repository)
    wheel, = wheel_dir.glob("*.whl")
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        assert "mailbox_app/templates/index.html" in names
        assert "mailbox_app/static/vendor/marked.min.js" in names
        assert "mailbox_app/static/vendor/purify.min.js" in names
        assert not any(name.startswith("mailbox/") for name in names)
        assert not any(name.endswith((".eml", "token.json", "config.toml", "credentials.json")) for name in names)
    environment = tmp_path / "installed"
    # Reuse dependency distributions, while installing the actual wheel into a new prefix.
    venv.EnvBuilder(with_pip=True, system_site_packages=True).create(environment)
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    outside = tmp_path / "outside-checkout"
    outside.mkdir()
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["PYTHONNOUSERSITE"] = "1"
    # A venv created by another venv shares the base interpreter, not its packages.
    dependencies = next(path for path in sys.path if path.endswith("site-packages"))
    site_packages = environment / ("Lib/site-packages" if os.name == "nt" else f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages")
    (site_packages / "test-dependencies.pth").write_text(dependencies + "\n", encoding="utf8")
    checked([str(python), "-m", "pip", "install", "--no-deps", "--ignore-installed", str(wheel)], outside, env)
    config = outside / "explicit.toml"
    config.write_text('data_dir = "./isolated-data"\noauth_client_secrets = "./unused-client.json"\nserver_port = 5000\n', encoding="utf8")
    script = r'''
import json, pathlib, re, sys, threading, urllib.request
import mailbox
import mailbox_app
from mailbox_app.config import load_config
from mailbox_app.paths import Paths
from mailbox_app.server import create_app
from werkzeug.serving import make_server
assert hasattr(mailbox, 'mbox'), mailbox.__file__
assert pathlib.Path(mailbox_app.__file__).is_relative_to(pathlib.Path(sys.prefix))
paths = Paths(load_config(pathlib.Path('explicit.toml').resolve()))
paths.ensure()
assert paths.data_dir == pathlib.Path.cwd() / 'isolated-data'
app = create_app(paths)
http = make_server('127.0.0.1', 0, app)
paths.config['server_port'] = http.server_port
thread = threading.Thread(target=http.serve_forever, daemon=True)
thread.start()
url = f'http://127.0.0.1:{http.server_port}'
try:
    with urllib.request.urlopen(url) as response:
        html = response.read().decode()
        assert response.status == 200
        assert 'mailbox-token' in html
    for asset in ['/static/app.js', '/static/app.css', '/static/vendor/marked.min.js', '/static/vendor/purify.min.js']:
        with urllib.request.urlopen(url + asset) as response:
            assert response.status == 200
            assert len(response.read()) > 1000
    assert 'cdn.jsdelivr.net' not in html
    print(json.dumps({'installed': str(mailbox_app.__file__), 'assets': 4, 'stdlib_mailbox': mailbox.__file__}))
finally:
    http.shutdown()
    thread.join()
    http.server_close()
'''
    output = checked([str(python), "-c", script], outside, env)
    assert json.loads(output)["assets"] == 4
    assert not (outside / "unused-client.json").exists()
