"""Gmail send with a durable, fail-closed operation journal."""
import base64
import hashlib
import json
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid

from googleapiclient.errors import HttpError

from .paths import Paths, validate_id
from .sync import build_service, import_message


def _result(state: dict, status: str | None = None) -> dict:
    return {'id': state.get('id'), 'path': state.get('path'), 'status': status or state['status']}


def _save(paths: Paths, journal, state):
    paths.atomic_write(journal, json.dumps(state, ensure_ascii=True).encode('utf8'))


def _mirror(paths: Paths, journal, state):
    try:
        md_path = import_message(paths, base64.urlsafe_b64decode(state['raw']),
                                 state['id'], state.get('labels') or ['SENT'])
        state['path'] = md_path.relative_to(paths.data_dir).as_posix()
        state['status'] = 'sent'
        _save(paths, journal, state)
        return _result(state)
    except Exception:
        return _result(state, 'sent_unmirrored')


def send_message(
    paths: Paths,
    *,
    operation_id: str,
    to: str,
    cc: str = '',
    subject: str = '',
    body: str = '',
    in_reply_to: str | None = None,
    references: list[str] | str | None = None,
) -> dict:
    """Send once per operation ID. Uncertain operations require human reconciliation."""
    validate_id(operation_id)
    if not isinstance(body, str) or not isinstance(to, str) or not to.strip():
        raise ValueError('A recipient and text body are required.')
    headers = [to, cc, subject, in_reply_to or '']
    if references is not None and not isinstance(references, (list, str)):
        raise ValueError('Invalid references.')
    headers += references if isinstance(references, list) else [references or '']
    if any(not isinstance(value, str) or '\r' in value or '\n' in value for value in headers):
        raise ValueError('Invalid email header.')
    payload = {'to': to, 'cc': cc, 'subject': subject, 'body': body,
               'in_reply_to': in_reply_to, 'references': references}
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode('utf8')).hexdigest()
    with paths.mutation_lock():
        journal = paths.checked(paths.state_dir / 'sends' / f'{operation_id}.json')
        if journal.exists():
            try:
                state = json.loads(journal.read_text(encoding='utf8'))
            except (OSError, ValueError):
                return {'id': None, 'path': None, 'status': 'unknown'}
            if state.get('fingerprint') != fingerprint:
                raise ValueError('This operation ID belongs to a different message.')
            if state.get('status') in ('accepted', 'sent'):
                return _mirror(paths, journal, state)
            if state.get('status') == 'rejected':
                return _result(state)
            return _result(state, 'unknown')

        service = build_service(paths)
        try:
            from_addr = service.users().getProfile(userId='me').execute()['emailAddress']
        except Exception:
            raise RuntimeError('Could not verify the Gmail account. Check connectivity and authorization before sending.') from None
        msg = MIMEText(body, 'plain', 'utf-8')
        msg['From'] = from_addr
        msg['To'] = to
        if cc:
            msg['Cc'] = cc
        msg['Subject'] = subject
        msg['Date'] = formatdate(localtime=True)
        msg['Message-ID'] = make_msgid()
        if in_reply_to:
            msg['In-Reply-To'] = in_reply_to
            msg['References'] = (' '.join(references) if isinstance(references, list) else references) or in_reply_to
        raw_b64 = base64.urlsafe_b64encode(msg.as_bytes()).decode('ascii')
        state = {'operation_id': operation_id, 'fingerprint': fingerprint,
                 'raw': raw_b64, 'status': 'in_flight', 'id': None, 'path': None}
        # The intent must reach disk before any request capable of sending mail.
        _save(paths, journal, state)
        try:
            accepted = service.users().messages().send(userId='me', body={'raw': raw_b64}).execute()
        except Exception as error:
            status = getattr(getattr(error, 'resp', None), 'status', None)
            rejected = isinstance(error, HttpError) and status in (400, 401, 403, 404, 405, 413, 415, 422, 429)
            state['status'] = 'rejected' if rejected else 'unknown'
            try:
                _save(paths, journal, state)
            except OSError:
                pass  # The persisted in-flight intent still prevents a resend.
            return _result(state)
        # Save acceptance before trying any local mirroring. Crash ambiguity is
        # represented by the preceding durable in-flight record, never a retry.
        state.update(status='accepted', id=accepted.get('id'), labels=accepted.get('labelIds') or ['SENT'])
        try:
            _save(paths, journal, state)
        except Exception:
            return _result(state, 'sent_unmirrored')
        return _mirror(paths, journal, state)
