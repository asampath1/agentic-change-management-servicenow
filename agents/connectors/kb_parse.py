"""Parse the structured KB article HTML (same format in the CSVs and in ServiceNow)."""
from __future__ import annotations

import html
import re

from ..models import KBArticle

_TAG = re.compile(r"<[^>]+>")


def _section(text: str, name: str) -> str:
    m = re.search(rf"<h3>{name}</h3>(.*?)(?=<h3>|$)", text, re.S | re.I)
    return strip_html(m.group(1)) if m else ""


def strip_html(s: str) -> str:
    s = re.sub(r"</(li|p|h3)>", "\n", s or "", flags=re.I)
    return html.unescape(_TAG.sub(" ", s)).replace("  ", " ").strip()


def parse_kb(key: str, title: str, text: str, meta: str = "") -> KBArticle:
    automation = re.search(r"<h3>Automation</h3>\s*<p>(.*?)</p>", text, re.S | re.I)
    runbook, safe = None, False
    if automation:
        block = automation.group(1)
        rb = re.search(r"\b(RB-[A-Za-z0-9-]+)", block)
        runbook = rb.group(1) if rb else None
        low = block.lower()
        # Only an explicit "safe to run automatically" marks a runbook as automatable;
        # anything ambiguous or saying "do NOT auto-run" is treated as manual.
        safe = "safe to run automatically" in low and "not auto" not in low
    grp = re.search(r"Resolver group:\s*([^<\n]+)", text)
    return KBArticle(key=key, title=title, text=strip_html(text),
                     symptoms=_section(text, "Symptoms"), cause=_section(text, "Cause"),
                     # ServiceNow stores the HTML entity-escaped ("SKR Backup &amp; DR"); the CSVs don't.
                     resolver_group=html.unescape(grp.group(1)).strip() if grp else None,
                     runbook=runbook, safe_to_automate=bool(runbook) and safe, meta=meta or "")
