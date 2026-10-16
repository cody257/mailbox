"""Extract only regular files from local build artifacts for secret scanning."""
from pathlib import Path
import sys
import tarfile
import zipfile

root = Path(__file__).resolve().parent.parent
destination = Path(sys.argv[1]).resolve()


def write(name, data, base):
    target = (base / name).resolve()
    if not target.is_relative_to(base):
        raise ValueError("Archive member escapes extraction directory")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


for path in sorted((root / "dist").iterdir()):
    base = (destination / path.name).resolve()
    base.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            for item in archive.infolist():
                if not item.is_dir():
                    write(item.filename, archive.read(item), base)
    elif path.name.endswith(".tar.gz"):
        with tarfile.open(path) as archive:
            for item in archive:
                if item.isfile():
                    write(item.name, archive.extractfile(item).read(), base)
