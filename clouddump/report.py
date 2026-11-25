"""HTML rendering for the report emails.

Email clients ignore stylesheets and most modern CSS, so the layout is nested
tables with inline styles — the one markup Outlook, Gmail and Apple Mail all
render alike. The logo travels inside the message (``cid:logo``): remote images
are blocked by default in most clients, and data URIs are stripped by Gmail.
"""

import os
import re
from html import escape

LOGO_PATH = os.path.join(os.path.dirname(__file__), "assets", "logo.png")
LOGO_CID = "logo"

REPO_URL = "https://github.com/ralftar/CloudDump"

# Where a late notice (attachments dropped for size) goes, above the sections.
NOTICE_MARK = "<!--notice-->"

# Status → banner colour. Warning and Degraded share the amber.
STATUS_COLORS = {
    "Success": "#15803d",
    "Warning": "#b45309",
    "Failure": "#b91c1c",
    "Started": "#1d4ed8",
    "Degraded": "#b45309",
}

_NAVY = "#0b1430"
_INK = "#1f2937"
_MUTED = "#6b7280"
_LINE = "#e5e7eb"
_FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
_MONO = "ui-monospace,SFMono-Regular,Menlo,Consolas,'Liberation Mono',monospace"

# A line from a tool's or CloudDump's output that says something went wrong.
_ERROR_RE = re.compile(
    r"\b(error|fatal|fail(ed|ure)?|denied|refused|timed out|timeout|cannot|unable)\b",
    re.IGNORECASE,
)
_TIMESTAMP_RE = re.compile(r"^\[\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\]\s*")


def error_lines(texts, limit=15):
    """The distinct error lines across *texts* (log contents), in order.

    Returns ``(lines, more)``: at most *limit* lines, and how many more there
    were. The timestamp is stripped, so the same error from three attempts
    shows once.
    """
    seen = []
    for text in texts:
        for raw in text.splitlines():
            line = _TIMESTAMP_RE.sub("", raw).strip()
            if line and _ERROR_RE.search(line) and line not in seen:
                seen.append(line)
    return seen[:limit], max(0, len(seen) - limit)


def badge(ok):
    """A small OK/FAIL pill."""
    color, text = ("#15803d", "OK") if ok else ("#b91c1c", "FAIL")
    return (f'<span style="display:inline-block;padding:2px 8px;border-radius:10px;'
            f'background:{color};color:#ffffff;font-size:11px;font-weight:700;'
            f'letter-spacing:.04em">{text}</span>')


def section(title, inner):
    """A titled block in the card body."""
    return (f'<tr><td style="padding:20px 28px 0 28px">'
            f'<div style="font-size:11px;font-weight:700;letter-spacing:.08em;'
            f'text-transform:uppercase;color:{_MUTED};padding-bottom:8px">{escape(title)}</div>'
            f'{inner}</td></tr>')


def facts(rows):
    """Label/value rows. Values are escaped."""
    cells = "".join(
        f'<tr><td style="padding:5px 16px 5px 0;color:{_MUTED};white-space:nowrap;'
        f'vertical-align:top">{escape(label)}</td>'
        f'<td style="padding:5px 0;color:{_INK}">{escape(str(value))}</td></tr>'
        for label, value in rows
    )
    return f'<table role="presentation" cellpadding="0" cellspacing="0" style="font-size:14px">{cells}</table>'


def table(header, rows):
    """A ruled table. Cells are raw HTML: escape text before passing it in."""
    th = "".join(
        f'<th align="left" style="padding:8px 12px 8px 0;font-size:12px;color:{_MUTED};'
        f'font-weight:600;border-bottom:1px solid {_LINE}">{escape(h)}</th>'
        for h in header
    )
    body = "".join(
        "<tr>" + "".join(
            f'<td style="padding:8px 12px 8px 0;font-size:13px;color:{_INK};'
            f'border-bottom:1px solid {_LINE};vertical-align:top;word-break:break-word">{c}</td>'
            for c in row
        ) + "</tr>"
        for row in rows
    )
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
            f'<tr>{th}</tr>{body}</table>')


def pre(text, tone="neutral"):
    """A monospace block; *tone* ``"error"`` tints it red."""
    bg, border = ("#fef2f2", "#b91c1c") if tone == "error" else ("#f6f8fb", "#cbd5e1")
    return (f'<div style="background:{bg};border-left:3px solid {border};border-radius:4px;'
            f'padding:12px 14px;font-family:{_MONO};font-size:12px;line-height:1.5;'
            f'color:{_INK};white-space:pre-wrap;word-break:break-word">{escape(text)}</div>')


def note(text):
    return f'<div style="font-size:13px;color:{_MUTED}">{escape(text)}</div>'


def page(status, title, subtitle, host, version, sections):
    """The whole message: header with logo, status banner, *sections*, footer."""
    color = STATUS_COLORS.get(status, _NAVY)
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light"><title>{escape(status)} · {escape(title)}</title></head>
<body style="margin:0;padding:0;background:#eef1f7">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#eef1f7;font-family:{_FONT}">
<tr><td align="center" style="padding:24px 12px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:640px;background:#ffffff;border-radius:12px;overflow:hidden;border:1px solid #dde3ee">
<tr><td style="background:{_NAVY};padding:18px 28px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
<td width="44" style="vertical-align:middle"><img src="cid:{LOGO_CID}" width="40" height="40" alt="" style="display:block;border:0;border-radius:9px"></td>
<td style="vertical-align:middle;padding-left:12px;color:#ffffff;font-size:20px;font-weight:700;letter-spacing:.01em">CloudDump</td>
<td align="right" style="vertical-align:middle;color:#9fb0d9;font-size:13px">{escape(host)}</td>
</tr></table></td></tr>
<tr><td style="background:{color};padding:18px 28px;color:#ffffff">
<div style="font-size:12px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;opacity:.85">{escape(status)}</div>
<div style="font-size:22px;font-weight:700;padding-top:2px">{escape(title)}</div>
<div style="font-size:14px;padding-top:4px;opacity:.92">{escape(subtitle)}</div>
</td></tr>
{NOTICE_MARK}
{"".join(sections)}
<tr><td style="height:24px"></td></tr>
<tr><td style="padding:16px 28px 20px 28px;border-top:1px solid {_LINE}">
<div style="font-size:12px;color:{_MUTED}">CloudDump v{escape(version)} · <a href="{REPO_URL}" style="color:{_MUTED}">github.com/ralftar/CloudDump</a></div>
</td></tr>
</table></td></tr></table></body></html>"""
