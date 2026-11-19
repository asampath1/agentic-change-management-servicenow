"""The interface the agents depend on. Swap the implementation (local CSVs, ServiceNow), keep the agents.

Changes still to be decided or verified MUST be redacted (see ``REDACTED_OPEN_FIELDS``): only closed changes may
carry an outcome. The evaluation answer key itself is never read by any connector.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional

from ..models import Change, ConfigItem, Incident, KBArticle, Problem, ScheduleSpan

REDACTED_OPEN_FIELDS = ("close_code", "close_notes")


def redact_open(ch: Change) -> Change:
    if ch.state != "3":
        ch.close_code, ch.close_notes = None, ""
    return ch


class ITSMClient(ABC):
    """Read context from, and write assessments back to, the ITSM tool."""

    @abstractmethod
    def changes(self) -> list[Change]:
        """Every change request (history, calendar and the ones to decide), open ones redacted."""

    @abstractmethod
    def incidents(self) -> list[Incident]: ...

    @abstractmethod
    def problems(self) -> list[Problem]: ...

    @abstractmethod
    def kb_articles(self) -> list[KBArticle]: ...

    @abstractmethod
    def schedules(self) -> list[ScheduleSpan]: ...

    @abstractmethod
    def config_item(self, name: str) -> Optional[ConfigItem]: ...

    @abstractmethod
    def dependents(self, name: str) -> list[str]:
        """CIs that depend on ``name`` (they are affected if it breaks)."""

    @abstractmethod
    def dependencies(self, name: str) -> list[str]:
        """CIs that ``name`` depends on."""

    @abstractmethod
    def update_change(self, ch: Change, fields: dict[str, Any], work_note: str) -> dict[str, Any]:
        """Write the assessment back. Returns a record of what was (or would be) written."""

    # ------------------------------------------------------------------ conveniences
    def changes_in_state(self, *states: str) -> list[Change]:
        return [c for c in self.changes() if c.state in states]

    def related(self, name: str) -> set[str]:
        return set(self.dependents(name)) | set(self.dependencies(name))
