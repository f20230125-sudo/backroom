"""Read untrusted files in a killable worker so PDF parsing has a real deadline."""

import io
import multiprocessing as mp
import re
from dataclasses import dataclass, field
from email import policy
from email.parser import BytesParser
from html.parser import HTMLParser
from pathlib import Path

import pdfplumber

MAX_BYTES = 5 * 1024 * 1024
MAX_PAGES = 10
DEFAULT_TIMEOUT = 20.0


class DocumentError(ValueError):
    pass


@dataclass
class DocumentText:
    text: str
    media_type: str = "application/pdf"
    headers: dict[str, str] = field(default_factory=dict)
    attachments: list[str] = field(default_factory=list)
    pages: int = 0


class _HTMLText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "template"}:
            self.hidden += 1
        if tag in {"p", "div", "br", "tr", "li"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "template"}:
            self.hidden = max(0, self.hidden - 1)
        if tag in {"p", "div", "tr", "li"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _HTMLText()
    parser.feed(html)
    return "".join(parser.parts)


def pdf_text(raw: bytes, max_pages: int = MAX_PAGES) -> tuple[str, int]:
    if len(raw) > MAX_BYTES:
        raise DocumentError("File exceeds 5 MB")
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        if len(pdf.pages) > max_pages:
            raise DocumentError("Document exceeds 10 PDF pages in total")
        texts = [_page_text(page) for page in pdf.pages]
        return "\n\n".join(texts), len(pdf.pages)


def _page_text(page) -> str:
    """Join description fragments above numeric cells into the same logical table row."""
    rows = []
    for word in sorted(
        page.extract_words(x_tolerance=2, y_tolerance=3), key=lambda word: (word["top"], word["x0"])
    ):
        if not rows or abs(word["top"] - rows[-1][0]["top"]) > 3:
            rows.append([])
        rows[-1].append(word)
    output = []
    for row in rows:
        row.sort(key=lambda word: word["x0"])
        numbers = [word for word in row if re.fullmatch(r"[-+]?\d[\d,]*(?:\.\d+)?", word["text"])]
        if output and len(numbers) >= 3:
            previous = output[-1]
            gap = row[0]["top"] - previous[0]["top"]
            if 0 < gap <= max(word["height"] for word in row) * 2.5 and max(
                word["x1"] for word in previous
            ) < min(word["x0"] for word in numbers):
                output.pop()
                row = previous + row
        output.append(row)
    return "\n".join(" ".join(word["text"] for word in row) for row in output)


def parse_bytes(raw: bytes, suffix: str) -> DocumentText:
    """Internal synchronous parser; public file reads enforce the timeout in a subprocess."""
    if len(raw) > MAX_BYTES:
        raise DocumentError("File exceeds 5 MB")
    if suffix.lower() == ".pdf":
        text, pages = pdf_text(raw)
        return DocumentText(text, pages=pages)
    if suffix.lower() == ".csv":
        return DocumentText(raw.decode("utf-8-sig"), "text/csv")
    if suffix.lower() != ".eml":
        raise DocumentError("Supported files are PDF, EML and payout CSV")
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    headers = {key: str(msg.get(key, "")) for key in ("From", "To", "Subject", "Date")}
    parts = [f"{key}: {value}" for key, value in headers.items()]

    # Prefer plain text for alternatives, but retain independent mixed body parts.
    def body(part):
        if part.get_content_disposition() == "attachment":
            return
        if part.get_content_type() == "multipart/alternative":
            selected = part.get_body(preferencelist=("plain", "html"))
            if selected:
                body(selected)
        elif part.is_multipart():
            for child in part.iter_parts():
                body(child)
        elif part.get_content_type() in {"text/plain", "text/html"}:
            content = part.get_content()
            parts.append(html_to_text(content) if part.get_content_subtype() == "html" else content)

    body(msg)
    attachments = []
    pages = 0
    for part in msg.walk():
        name = part.get_filename() or ""
        if part.get_content_type() == "application/pdf" or name.lower().endswith(".pdf"):
            data = part.get_payload(decode=True) or b""
            text, count = pdf_text(data, MAX_PAGES - pages)
            pages += count
            attachments.append(name or "attachment.pdf")
            parts.extend([f"PDF attachment: {name or 'attachment.pdf'}", text])
    return DocumentText("\n".join(parts), "message/rfc822", headers, attachments, pages)


def _worker(connection, raw, suffix):
    try:
        connection.send((True, parse_bytes(raw, suffix)))
    except Exception as exc:
        message = (
            str(exc)
            if isinstance(exc, DocumentError)
            else f"Cannot read document ({type(exc).__name__})"
        )
        connection.send((False, message))
    finally:
        connection.close()


def read_document(path: Path | str, *, timeout: float = DEFAULT_TIMEOUT) -> DocumentText:
    path = Path(path)
    if path.stat().st_size > MAX_BYTES:
        raise DocumentError("File exceeds 5 MB")
    if timeout <= 0:
        raise DocumentError("Document timeout")
    context = mp.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    process = context.Process(target=_worker, args=(send, path.read_bytes(), path.suffix))
    try:
        process.start()
        send.close()
        if not receive.poll(timeout):
            raise DocumentError("Document timeout")
        try:
            ok, result = receive.recv()
        except EOFError as exc:
            raise DocumentError("Document parser exited without a result") from exc
        if not ok:
            raise DocumentError(result)
        return result
    finally:
        if process.pid is not None:
            if process.is_alive():
                process.terminate()
            process.join()
        receive.close()
        send.close()
