"""Offline release checks: pinned assets, reviewed runtime licenses and artifact contents."""
import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parent.parent


def main():
    vendor = ROOT / "mailbox_app/static/vendor"
    for filename, record in json.loads((vendor / "manifest.json").read_text()).items():
        assert hashlib.sha256((vendor / filename).read_bytes()).hexdigest() == record["sha256"], filename
    approved = json.loads((ROOT / "licenses.json").read_text())
    runtime = dict(re.findall(r"^([\w.-]+)==([^ ;\\\n]+)", (ROOT / "requirements.lock").read_text(), re.M))
    assert runtime.keys() == approved.keys(), "Runtime dependencies changed; review licenses and notices."
    for name, version in runtime.items():
        assert approved[name]["version"] == version, f"Review license after updating {name}."
        try:
            dist = importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError:
            # Platform-specific pins are still checked against the reviewed manifest.
            continue
        assert dist.version == version, f"Install locked {name} before checking release."
        md = dist.metadata
        license_text = md.get("License-Expression") or md.get("License") or "; ".join(
            c.split(" :: ")[-1] for c in md.get_all("Classifier", []) if c.startswith("License ::")
        )
        assert license_text == approved[name]["license"], f"License metadata changed for {name}."
    forbidden = {"config.toml", "token.json", "credentials.json", "AGENTS.md", "email-crm-spec.md", "csi-audit-template.html"}
    artifacts = list((ROOT / "dist").glob("*.whl")) + list((ROOT / "dist").glob("*.tar.gz"))
    assert artifacts, "Build wheel and source archive before checking release."
    for artifact in artifacts:
        if artifact.suffix == ".whl":
            with zipfile.ZipFile(artifact) as archive:
                names = archive.namelist()
        else:
            with tarfile.open(artifact) as archive:
                names = archive.getnames()
        for name in names:
            path = Path(name)
            assert not (set(path.parts) & forbidden), f"Private file in {artifact.name}: {name}"
            assert not path.name.startswith("client_secret"), name
            assert not (set(path.parts) & {".auth", ".state", ".originals", ".git", ".venv", ".audit"}), name
        for suffix in ("mailbox_app/templates/index.html", "mailbox_app/static/app.js", "mailbox_app/static/vendor/purify.min.js"):
            assert any(name.endswith(suffix) for name in names), f"Missing packaged {suffix}"
    print(f"Verified browser hashes, {len(runtime)} reviewed runtime licenses, and {len(artifacts)} artifacts.")


if __name__ == "__main__":
    main()
