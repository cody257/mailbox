import email
import re
from datetime import datetime, timezone
from email.message import Message
from email.utils import getaddresses, parseaddr, parsedate_to_datetime

from bs4 import BeautifulSoup
from markdownify import markdownify
from .paths import validate_id


def _decode_part(part: Message) -> str:
    payload = part.get_payload(decode=True) or b""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except (LookupError, TypeError):
        return payload.decode("utf-8", errors="replace")


def _content_parts(part: Message):
    # Attached messages are containers too; keep their contents together instead
    # of treating the forwarded message's body and files as the outer message.
    if part.get_content_disposition() == 'attachment' or part.get_filename():
        yield part
    elif part.is_multipart():
        for child in part.get_payload():
            yield from _content_parts(child)
    else:
        yield part


def _attachment_bytes(part: Message) -> bytes:
    payload = part.get_payload(decode=True)
    if payload is not None:
        return payload
    if part.get_content_type() == 'message/rfc822':
        return b''.join(message.as_bytes() for message in part.get_payload())
    return part.as_bytes() if part.is_multipart() else b''


_SLUG_RE_STRIP = re.compile(r"[^\w\s-]", re.UNICODE)
_SLUG_RE_SPACE = re.compile(r"[\s_-]+")


def slugify(s: str, maxlen: int = 60) -> str:
    s = _SLUG_RE_STRIP.sub("", s).strip().lower()
    s = _SLUG_RE_SPACE.sub("-", s)
    return s[:maxlen] or "untitled"


def _truncate_utf8(value: str, byte_limit: int) -> str:
    return value.encode('utf8')[:byte_limit].decode('utf8', errors='ignore')


def _attachment_name(name: str, index: int) -> str:
    name = re.sub(r'[^\w. -]', '_', name, flags=re.UNICODE).strip(' .') or 'attachment'
    stem, separator, extension = name.rpartition('.')
    suffix = f'.{extension}' if separator and stem and len(extension.encode('utf8')) <= 32 else ''
    if not suffix:
        stem = name
    prefix = f'{index:03d}-'
    budget = 200 - len((prefix + suffix).encode('utf8'))
    stem = _truncate_utf8(stem[:100], budget).rstrip(' .') or 'attachment'
    return prefix + stem + suffix


def parse_eml(
    raw_bytes: bytes,
    gmail_id: str,
    label_ids: list[str],
    internal_date_ms: int | None = None,
) -> dict:
    """Parse raw RFC 822 bytes into the data needed to write a .md file."""
    validate_id(gmail_id)
    msg: Message = email.message_from_bytes(raw_bytes, policy=email.policy.default)

    subject = (msg.get("Subject") or "").strip() or "(no subject)"
    from_addr = parseaddr(msg.get("From") or "")[1] or (msg.get("From") or "")
    to = [a for _, a in getaddresses(msg.get_all("To") or []) if a]
    cc = [a for _, a in getaddresses(msg.get_all("Cc") or []) if a]
    message_id = (msg.get("Message-ID") or "").strip()
    in_reply_to = (msg.get("In-Reply-To") or "").strip()
    references = re.findall(r"<[^>]+>", msg.get("References") or "")

    if internal_date_ms:
        date = datetime.fromtimestamp(internal_date_ms / 1000.0, tz=timezone.utc)
    else:
        try:
            date = parsedate_to_datetime(msg.get("Date") or "")
        except (TypeError, ValueError):
            date = datetime.now(tz=timezone.utc)
    if date.tzinfo is None:
        date = date.replace(tzinfo=timezone.utc)

    text_body = ""
    html_body = ""
    attachments: list[tuple[str, bytes]] = []

    if msg.is_multipart():
        for part in _content_parts(msg):
            ctype = part.get_content_type()
            disposition = (part.get("Content-Disposition") or "").lower()
            if "attachment" in disposition or part.get_filename():
                # A sequence prefix prevents case-insensitive collisions and device names.
                fname = _attachment_name(part.get_filename() or 'attachment', len(attachments) + 1)
                payload = _attachment_bytes(part)
                attachments.append((fname, payload))
            elif ctype == "text/plain" and not text_body:
                text_body = _decode_part(part)
            elif ctype == "text/html" and not html_body:
                html_body = _decode_part(part)
    else:
        ctype = msg.get_content_type()
        if ctype == "text/html":
            html_body = _decode_part(msg)
        else:
            text_body = _decode_part(msg)

    if text_body:
        body_md = text_body
    elif html_body:
        soup = BeautifulSoup(html_body, "html.parser")
        for element in soup.find_all(['head', 'title', 'script', 'style']):
            element.decompose()
        body_md = markdownify(str(soup), heading_style="ATX", wrap=False, bs4_options={"features": "html.parser"})
    else:
        body_md = ""

    is_sent = "SENT" in (label_ids or [])

    fm = {
        "id": gmail_id,
        "message-id": message_id,
        "from": from_addr,
        "to": to,
        "cc": cc,
        "date": date.isoformat(),
        "subject": subject,
        "in-reply-to": in_reply_to,
        "references": references,
        "gmail-labels": label_ids or [],
        "attachments": [name for name, _ in attachments],
    }

    prefix, suffix = f"{date.strftime('%Y-%m-%d')}-", f'-{gmail_id}.md'
    slug = _truncate_utf8(slugify(subject), 240 - len((prefix + suffix).encode('utf8')))
    filename = prefix + slug + suffix

    return {
        "frontmatter": fm,
        "body": body_md if text_body else ((body_md.strip() + "\n") if body_md else ""),
        "attachments": attachments,
        "filename": filename,
        "is_sent": is_sent,
        "date": date,
    }
