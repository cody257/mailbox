import argparse
import hmac
import ipaddress
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

import frontmatter
from flask import Blueprint, Flask, abort, current_app, jsonify, render_template, request, send_file
from werkzeug.exceptions import HTTPException
from yaml import YAMLError

from .paths import Paths
from .sync import GmailAuthorizationError

PKG_ROOT = Path(__file__).resolve().parent
routes = Blueprint('mailbox', __name__)


def P() -> Paths:
    paths = current_app.extensions.get('mailbox_paths')
    if paths is None:
        paths = Paths()
        paths.ensure()
        current_app.extensions['mailbox_paths'] = paths
    return paths


def create_app(paths: Paths | None = None) -> Flask:
    application = Flask(__name__, template_folder=str(PKG_ROOT / 'templates'), static_folder=str(PKG_ROOT / 'static'))
    application.config.update(SEND_FILE_MAX_AGE_DEFAULT=0, MAILBOX_TOKEN=secrets.token_urlsafe(32), MAX_CONTENT_LENGTH=10 * 1024 * 1024)
    if paths is not None:
        application.extensions['mailbox_paths'] = paths
    application.register_blueprint(routes)

    @application.before_request
    def local_access():
        try:
            local = ipaddress.ip_address(request.remote_addr or '').is_loopback
        except ValueError:
            local = False
        if not local:
            abort(403)
        port = int(P().config.get('server_port', 5000))
        hosts = {f'localhost:{port}', f'127.0.0.1:{port}'}
        if port == 80:
            hosts.update({'localhost', '127.0.0.1'})
        if request.host.lower() not in hosts or request.scheme != 'http':
            abort(403)
        if request.path.startswith('/api/'):
            supplied = request.headers.get('X-Mailbox-Token', '')
            if not hmac.compare_digest(supplied.encode(), application.config['MAILBOX_TOKEN'].encode()):
                abort(403)
            if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
                if request.headers.get('Origin') != f'http://{request.host}':
                    abort(403)
                if request.mimetype != 'application/json':
                    abort(415)
                if not isinstance(request.get_json(silent=True), dict):
                    abort(400)

    @application.after_request
    def security_headers(response):
        response.headers['Content-Security-Policy'] = "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self'; connect-src 'self'; font-src 'self'; base-uri 'none'; object-src 'none'; frame-src 'none'; frame-ancestors 'none'; form-action 'none'"
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Cache-Control'] = 'no-store, private'
        return response

    @application.errorhandler(Exception)
    def safe_error(error):
        if isinstance(error, GmailAuthorizationError):
            return jsonify(error=str(error), code='authorization_required'), 409
        if isinstance(error, ValueError):
            return jsonify(error='Invalid request or path.'), 400
        if isinstance(error, HTTPException):
            return jsonify(error=error.name), error.code
        return jsonify(error='Request could not be completed.'), 500

    return application


def _safe_relpath(rel: str, allow_root=False) -> Path:
    if not isinstance(rel, str):
        raise ValueError('Invalid path')
    return P().user_path(rel, allow_root=allow_root)


def _json():
    return request.get_json()


def _text(data, key, default=''):
    value = data.get(key, default)
    if not isinstance(value, str):
        raise ValueError('Invalid field')
    return value


def _md_to_summary(md_path: Path) -> dict | None:
    try:
        md_path = _safe_relpath(md_path.relative_to(P().data_dir).as_posix())
        post = frontmatter.load(str(md_path))
    except (ValueError, OSError, YAMLError):
        return None
    fm = post.metadata
    content = post.content or ''
    labels = fm.get('gmail-labels') or []
    return {'id': fm.get('id', ''), 'path': md_path.relative_to(P().data_dir).as_posix(), 'from': fm.get('from', ''), 'to': fm.get('to', []), 'subject': fm.get('subject', '(no subject)'), 'date': str(fm.get('date', '')), 'snippet': re.sub(r'\s+', ' ', content).strip()[:240], 'unread': 'UNREAD' in labels, 'has_attachments': bool(fm.get('attachments'))}


def _list_folders():
    out = []
    root = P().data_dir

    def walk(parent, depth):
        children = []
        for child in parent.iterdir():
            try:
                child = _safe_relpath(child.relative_to(root).as_posix())
                if child.is_dir():
                    children.append(child)
            except ValueError:
                continue
        children.sort(key=lambda d: ({'inbox': 0, 'sent': 1}.get(d.name, 2) if depth == 0 else 0, d.name.lower()))
        for child in children:
            count = sum(1 for md in child.glob('*.md') if _md_to_summary(md) is not None)
            out.append({'name': child.name, 'path': child.relative_to(root).as_posix(), 'count': count, 'depth': depth})
            walk(child, depth + 1)
    walk(root, 0)
    return out


@routes.get('/')
def index():
    return render_template('index.html', mailbox_token=current_app.config['MAILBOX_TOKEN'])


@routes.get('/api/folders')
def api_folders():
    return jsonify(_list_folders())


@routes.get('/api/messages')
def api_messages():
    folder = _safe_relpath(request.args.get('folder', 'inbox'))
    if not folder.is_dir():
        abort(404)
    messages = [summary for md in folder.glob('*.md') if (summary := _md_to_summary(md))]
    messages.sort(key=lambda m: m['date'], reverse=True)
    return jsonify(messages)


def _attachment_file(gmail_id, filename):
    if not isinstance(filename, str) or not filename or Path(filename).name != filename or any(c in filename for c in '/\\:') or filename in {'.', '..'}:
        raise ValueError('Invalid attachment')
    directory = P().attachment_dir(gmail_id)
    candidate = P().checked(directory / filename)
    resolved = candidate.resolve()
    if resolved.parent != directory.resolve():
        raise ValueError('Invalid attachment')
    return resolved


@routes.get('/api/message')
def api_message():
    rel = request.args.get('path', '')
    if not rel.endswith('.md'):
        abort(400)
    md_path = _safe_relpath(rel)
    if not md_path.is_file():
        abort(404)
    post = frontmatter.load(str(md_path))
    fm = post.metadata
    attachments = []
    gmail_id = fm.get('id', '')
    if gmail_id:
        directory = P().attachment_dir(gmail_id)
        if directory.is_dir():
            for candidate in sorted(directory.iterdir()):
                try:
                    candidate = _attachment_file(gmail_id, candidate.name)
                    if candidate.is_file():
                        attachments.append({'filename': candidate.name, 'size': candidate.stat().st_size})
                except ValueError:
                    continue
    return jsonify(frontmatter=fm, body=post.content or '', attachments=attachments, path=md_path.relative_to(P().data_dir).as_posix(), folder=md_path.parent.relative_to(P().data_dir).as_posix())


@routes.get('/api/attachment/<gmail_id>/<path:filename>')
def api_attachment(gmail_id, filename):
    path = _attachment_file(gmail_id, filename)
    if not path.is_file():
        abort(404)
    return send_file(str(path), as_attachment=True, download_name=path.name, mimetype='application/octet-stream')


@routes.post('/api/folders')
def api_create_folder():
    data = _json()
    name = _text(data, 'name').strip()
    parent = _text(data, 'parent').strip()
    if not name or not re.fullmatch(r'[\w\- .]+', name) or name.startswith('.') or name.lower() == 'attachments':
        abort(400)
    with P().mutation_lock():
        parent_path = _safe_relpath(parent, allow_root=True)
        new_dir = _safe_relpath(f'{parent}/{name}' if parent else name)
        if not parent_path.is_dir():
            abort(404)
        if new_dir.exists():
            abort(409)
        new_dir.mkdir()
    return jsonify(path=new_dir.relative_to(P().data_dir).as_posix())


def _open_native(path):
    if sys.platform == 'win32':
        os.startfile(str(path))
    elif sys.platform == 'darwin':
        subprocess.Popen(['open', str(path)])
    else:
        subprocess.Popen(['xdg-open', str(path)])


@routes.post('/api/open-folder')
def api_open_folder():
    folder = _safe_relpath(_text(_json(), 'path'))
    if not folder.is_dir():
        abort(404)
    _open_native(folder)
    return jsonify(opened=folder.relative_to(P().data_dir).as_posix())


@routes.post('/api/open-file')
def api_open_file():
    rel = _text(_json(), 'path')
    if not rel.endswith('.md'):
        abort(400)
    path = _safe_relpath(rel)
    if not path.is_file():
        abort(404)
    _open_native(path)
    return jsonify(opened=path.relative_to(P().data_dir).as_posix())


@routes.post('/api/move')
def api_move():
    data = _json()
    rel = _text(data, 'path')
    if not rel.endswith('.md'):
        abort(400)
    with P().mutation_lock():
        src = _safe_relpath(rel)
        target = _safe_relpath(_text(data, 'target_folder'))
        if not src.is_file() or not target.is_dir():
            abort(404)
        new_path = _safe_relpath((target / src.name).relative_to(P().data_dir).as_posix())
        if src == new_path:
            return jsonify(path=rel)
        if new_path.exists():
            abort(409)
        shutil.move(str(src), str(new_path))
    return jsonify(path=new_path.relative_to(P().data_dir).as_posix())


@routes.post('/api/sync')
def api_sync():
    from . import sync as sync_mod
    return jsonify(sync_mod.sync(P(), days=int(P().config.get('backfill_days', 30))))


@routes.post('/api/send')
def api_send():
    from .send import send_message
    data = _json()
    return jsonify(send_message(P(), operation_id=_text(data, 'operation_id'), to=_text(data, 'to'), cc=_text(data, 'cc'), subject=_text(data, 'subject'), body=_text(data, 'body'), in_reply_to=data.get('in_reply_to') or None, references=data.get('references') or None))


app = create_app()


def main():
    from .config import load_config
    parser = argparse.ArgumentParser(description='Run the local Mailbox UI.')
    parser.add_argument('--config', type=Path)
    parser.add_argument('--port', type=int)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    config = load_config(args.config)
    if args.port is not None:
        config['server_port'] = args.port
    paths = Paths(config)
    paths.ensure()
    application = create_app(paths)
    port = int(config.get('server_port', 5000))
    if not 1 <= port <= 65535:
        parser.error('port must be between 1 and 65535')
    url = f'http://localhost:{port}'
    if not args.no_browser:
        timer = threading.Timer(1.0, lambda: webbrowser.open(url))
        timer.daemon = True
        timer.start()
    print(f'Mailbox UI at {url}', flush=True)
    application.run(host='127.0.0.1', port=port, debug=False)


if __name__ == '__main__':
    main()
