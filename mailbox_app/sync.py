import argparse
import base64
import json
from datetime import datetime, timezone
from pathlib import Path

import frontmatter
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .parse import parse_eml
from .paths import Paths
from .config import load_config

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly", "https://www.googleapis.com/auth/gmail.send"]


class GmailAuthorizationError(RuntimeError):
    """An actionable OAuth error whose message contains no provider/private data."""


def _check_scopes(creds):
    granted = creds.granted_scopes if creds.granted_scopes is not None else creds.scopes
    if not granted or set(granted) != set(SCOPES):
        raise GmailAuthorizationError('Saved Gmail permissions do not match readonly and send. Revoke the old Mailbox grant, remove the saved token, and authorize again with --test-auth.')


def get_credentials(paths: Paths) -> Credentials:
    paths.ensure()
    creds: Credentials | None = None
    if paths.checked(paths.token_path).exists():
        try:
            creds = Credentials.from_authorized_user_file(str(paths.token_path))
        except Exception:
            raise GmailAuthorizationError('Saved Gmail authorization is invalid. Remove the saved token and authorize again with --test-auth.') from None
        _check_scopes(creds)
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _check_scopes(creds)
            paths.atomic_write(paths.token_path, creds.to_json().encode('utf8'))
            return creds
        except Exception:
            raise GmailAuthorizationError('Gmail authorization could not be refreshed. Check connectivity; if access was revoked, remove the saved token and authorize again with --test-auth.') from None
    if not paths.client_secrets_path.exists():
        raise GmailAuthorizationError(
            "OAuth client configuration is missing. Download a Desktop OAuth client JSON from Google Cloud Console and set oauth_client_secrets."
        )
    try:
        flow = InstalledAppFlow.from_client_secrets_file(str(paths.client_secrets_path), SCOPES)
        creds = flow.run_local_server(port=0)
        _check_scopes(creds)
        paths.atomic_write(paths.token_path, creds.to_json().encode('utf8'))
    except Exception:
        raise GmailAuthorizationError('Gmail consent failed. Check the Desktop OAuth configuration and consent-screen test users, then authorize again with --test-auth.') from None
    return creds


def build_service(paths: Paths):
    return build("gmail", "v1", credentials=get_credentials(paths), cache_discovery=False)


def _list_message_ids(service, query: str):
    page_token: str | None = None
    while True:
        resp = (
            service.users()
            .messages()
            .list(userId="me", q=query, maxResults=500, pageToken=page_token)
            .execute()
        )
        for m in resp.get("messages", []):
            yield m["id"]
        page_token = resp.get("nextPageToken")
        if not page_token:
            break


def _fetch_raw(service, msg_id: str) -> dict:
    return (
        service.users()
        .messages()
        .get(userId="me", id=msg_id, format="raw")
        .execute()
    )


def _write_md(paths: Paths, parsed: dict) -> Path:
    existing = paths.find_md_by_id(parsed['frontmatter']['id'])
    if existing is not None:
        return existing
    target_dir = paths.sent_dir if parsed["is_sent"] else paths.inbox_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    md_path = paths.user_path(f'{target_dir.name}/{parsed["filename"]}')
    if md_path.exists():
        raise ValueError('A different note already uses the import filename.')
    post = frontmatter.Post(content=parsed["body"])
    post.metadata = parsed["frontmatter"]
    paths.atomic_write(md_path, frontmatter.dumps(post).encode("utf-8") + b"\n")
    return md_path


def _save_attachments(paths: Paths, gmail_id: str, attachments) -> None:
    if not attachments:
        return
    adir = paths.attachment_dir(gmail_id)
    adir.mkdir(parents=True, exist_ok=True)
    for name, data in attachments:
        from .paths import safe_component
        paths.atomic_write(paths.checked(adir / safe_component(name)), data)


def import_message(paths: Paths, raw_bytes: bytes, gmail_id: str, label_ids: list[str], internal_ms=None) -> Path:
    """Commit a recoverable import. The marker is written only after every output."""
    with paths.mutation_lock():
        paths.atomic_write(paths.original_path(gmail_id), raw_bytes)
        parsed = parse_eml(raw_bytes, gmail_id, label_ids, internal_ms)
        _save_attachments(paths, gmail_id, parsed['attachments'])
        md_path = _write_md(paths, parsed)
        state = {'id': gmail_id, 'attachments': parsed['frontmatter']['attachments']}
        paths.atomic_write(paths.completion_path(gmail_id), json.dumps(state).encode('utf8'))
        return md_path


def sync(paths: Paths, *, days: int, on_progress=None) -> dict:
    if type(days) is not int or days < 1 or days > 36500:
        raise ValueError('Days must be between 1 and 36500.')
    with paths.mutation_lock():
        return _sync_locked(paths, days=days, on_progress=on_progress)


def _sync_locked(paths: Paths, *, days: int, on_progress=None) -> dict:
    paths.ensure()
    service = build_service(paths)

    query = f"newer_than:{days}d"
    new_count = 0
    skipped = 0
    failures: list[dict] = []

    ids = list(_list_message_ids(service, query))
    total = len(ids)
    if on_progress:
        on_progress(f"Found {total} messages in last {days} days")

    for i, gmail_id in enumerate(ids, 1):
        try:
            if paths.has_message(gmail_id):
                skipped += 1
                continue
            resp = _fetch_raw(service, gmail_id)
            raw_b64 = resp.get("raw", "")
            raw_bytes = base64.urlsafe_b64decode(raw_b64.encode("ascii"))
            label_ids = resp.get("labelIds", [])
            internal_ms = int(resp.get("internalDate", 0))

            import_message(paths, raw_bytes, gmail_id, label_ids, internal_ms)
            new_count += 1
            if on_progress and (new_count % 5 == 0 or i == total):
                on_progress(f"Imported {i}/{total} messages")
        except HttpError as e:
            failures.append({"id": gmail_id, "error": "Gmail request failed. Check connectivity and authorization, then retry."})
        except Exception as e:
            failures.append({"id": gmail_id, "error": "Local import failed. Check data-folder access and available disk space, then retry."})

    summary = {
        "total_listed": total,
        "new": new_count,
        "skipped": skipped,
        "failures": failures,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    paths.last_sync_path.parent.mkdir(parents=True, exist_ok=True)
    paths.atomic_write(paths.last_sync_path, json.dumps(summary, indent=2).encode('utf8'))
    if on_progress:
        on_progress(
            f"Done. New: {new_count}, skipped: {skipped}, failures: {len(failures)}"
        )
    return summary


def main():
    parser = argparse.ArgumentParser(description="Sync Gmail to local .md files.")
    parser.add_argument('--config', type=Path, help='Configuration TOML path.')
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="How many days back to fetch. Defaults to config.backfill_days.",
    )
    parser.add_argument(
        "--test-auth",
        action="store_true",
        help="Verify OAuth and print the authenticated email address.",
    )
    args = parser.parse_args()

    paths = Paths(load_config(args.config))
    paths.ensure()

    if args.test_auth:
        service = build_service(paths)
        profile = service.users().getProfile(userId="me").execute()
        print(f"Authenticated as: {profile['emailAddress']}")
        return

    days = args.days if args.days is not None else int(paths.config.get("backfill_days", 30))
    sync(paths, days=days, on_progress=lambda m: print(m, flush=True))


if __name__ == "__main__":
    main()
