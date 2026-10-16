import json
import os
import re
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

import frontmatter
from yaml import YAMLError
from .config import load_config

_MUTATION_LOCK = threading.RLock()
_HELD = threading.local()
_RESERVED = re.compile(r'^(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|$)', re.I)
_ID = re.compile(r'^[A-Za-z0-9_-]{1,128}$')


def safe_component(value: str) -> str:
    if (not isinstance(value, str) or not value or value in ('.', '..')
            or value.startswith('.') or value.endswith((' ', '.'))
            or any(ord(c) < 32 or c in '/\\:<>"|?*' for c in value)
            or _RESERVED.match(value)):
        raise ValueError('Invalid path component.')
    return value


def validate_id(value: str) -> str:
    safe_component(value)
    if not _ID.fullmatch(value):
        raise ValueError('Invalid message or operation ID.')
    return value


class Paths:
    def __init__(self, config: dict | None = None):
        self.config = config if config is not None else load_config()
        self.data_dir = Path(self.config['data_dir']).absolute()
        self.auth_dir = self.data_dir / '.auth'
        self.state_dir = self.data_dir / '.state'
        self.originals_dir = self.data_dir / '.originals'
        self.attachments_dir = self.data_dir / 'attachments'
        self.inbox_dir = self.data_dir / 'inbox'
        self.sent_dir = self.data_dir / 'sent'
        self.token_path = self.auth_dir / 'token.json'
        self.client_secrets_path = Path(self.config['oauth_client_secrets'])
        self.last_sync_path = self.state_dir / 'last_sync.json'

    def checked(self, path: Path) -> Path:
        """Reject links/reparse points, including links that currently stay in root."""
        path = Path(path).absolute()
        try:
            relative = path.relative_to(self.data_dir)
        except ValueError:
            raise ValueError('Path is outside the data directory.') from None
        current = self.data_dir
        for part in (None, *relative.parts):
            if part is not None:
                if part in ('.', '..'):
                    raise ValueError('Invalid path.')
                current = current / part
            try:
                info = current.lstat()
            except FileNotFoundError:
                continue
            if current.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise ValueError('Linked paths are not allowed.')
        if not path.resolve().is_relative_to(self.data_dir.resolve()):
            raise ValueError('Path is outside the data directory.')
        return path

    def user_path(self, rel: str, *, allow_root=False) -> Path:
        if not isinstance(rel, str):
            raise ValueError('Invalid path.')
        if rel == '' and allow_root:
            return self.checked(self.data_dir)
        if not rel or '\\' in rel:
            raise ValueError('Invalid path.')
        parts = rel.split('/')
        for part in parts:
            safe_component(part)
            if part.casefold() == 'attachments':
                raise ValueError('Internal paths are not accessible.')
        return self.checked(self.data_dir.joinpath(*parts))

    def ensure(self) -> None:
        for directory in (self.data_dir, self.auth_dir, self.state_dir,
                          self.originals_dir, self.attachments_dir, self.inbox_dir, self.sent_dir):
            self.checked(directory).mkdir(parents=True, exist_ok=True)

    def attachment_dir(self, gmail_id: str) -> Path:
        return self.checked(self.attachments_dir / validate_id(gmail_id))

    def original_path(self, gmail_id: str) -> Path:
        return self.checked(self.originals_dir / f'{validate_id(gmail_id)}.eml')

    def completion_path(self, gmail_id: str) -> Path:
        return self.checked(self.state_dir / 'imports' / f'{validate_id(gmail_id)}.json')

    def has_message(self, gmail_id: str) -> bool:
        marker = self.completion_path(gmail_id)
        if not marker.is_file() or not self.original_path(gmail_id).is_file():
            return False
        try:
            state = json.loads(marker.read_text(encoding='utf8'))
            if state.get('id') != gmail_id or self.find_md_by_id(gmail_id) is None:
                return False
            return all(self.checked(self.attachment_dir(gmail_id) / safe_component(n)).is_file()
                       for n in state['attachments'])
        except (OSError, ValueError, KeyError, TypeError):
            return False

    def find_md_by_id(self, gmail_id: str) -> Path | None:
        validate_id(gmail_id)
        if not self.data_dir.exists():
            return None
        self.checked(self.data_dir)
        for root, dirs, files in os.walk(self.data_dir, followlinks=False):
            valid = []
            for name in dirs:
                try:
                    self.user_path((Path(root) / name).relative_to(self.data_dir).as_posix())
                    valid.append(name)
                except ValueError:
                    pass
            dirs[:] = valid
            for name in files:
                if not name.endswith('.md'):
                    continue
                try:
                    md = self.user_path((Path(root) / name).relative_to(self.data_dir).as_posix())
                    if frontmatter.load(md).metadata.get('id') == gmail_id:
                        return md
                except (OSError, ValueError, TypeError, YAMLError):
                    continue
        return None

    def user_folders(self) -> list[Path]:
        result = []
        for directory in self.data_dir.iterdir():
            try:
                self.user_path(directory.name)
                if directory.is_dir():
                    result.append(directory)
            except ValueError:
                continue
        return result

    def atomic_write(self, path: Path, data: bytes) -> None:
        path = self.checked(path)
        self.checked(path.parent).mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix='.mailbox-', dir=path.parent)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            self.checked(path)
            os.replace(temporary, path)
            if os.name != 'nt':
                # Persist the directory entry as well as the file contents.
                directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @contextmanager
    def mutation_lock(self):
        """Serialize threads and independent processes; nesting uses the same lock."""
        with _MUTATION_LOCK:
            held = getattr(_HELD, 'roots', set())
            key = str(self.data_dir.resolve()).casefold()
            if key in held:
                yield
                return
            self.ensure()
            lock_path = self.checked(self.state_dir / 'mutation.lock')
            with open(lock_path, 'a+b') as stream:
                if stream.tell() == 0:
                    stream.write(b'0')
                    stream.flush()
                stream.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    try:
                        msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
                    except OSError:
                        raise RuntimeError('Mailbox is busy in another process. Try again shortly.') from None
                else:
                    import fcntl
                    fcntl.flock(stream, fcntl.LOCK_EX)
                _HELD.roots = held | {key}
                try:
                    yield
                finally:
                    _HELD.roots = held
                    stream.seek(0)
                    if os.name == 'nt':
                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(stream, fcntl.LOCK_UN)
