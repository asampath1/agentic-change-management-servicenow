"""Interfaces the agents depend on. Swap the implementation, keep the agents.

* ``ITSMClient``        - ServiceNow (live) or the local synthetic dataset (offline)
* ``LogAnalyticsClient``- Azure Monitor Log Analytics (live) or a synthetic workspace
* ``AutomationClient``  - Azure Automation runbooks (live) or a recording mock

Open incidents returned by any ``ITSMClient`` MUST be redacted (see ``REDACTED_OPEN_FIELDS``):
the evaluation set is generated with answer-bearing fields (``caused_by``, ``problem_id``)
already filled in, and agents must work those out themselves.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Iterable, Optional

from ..models import Change, ConfigItem, Incident, KBArticle, Problem

# Fields an agent must never see on an OPEN incident. They are either the answer
# (caused_by, problem_id, assignment_group) or only exist after resolution.
REDACTED_OPEN_FIELDS = ("assignment_group", "work_notes", "close_notes", "caused_by",
                        "problem_id", "resolved_at")


def redact_open(inc: Incident) -> Incident:
    for f in REDACTED_OPEN_FIELDS:
        setattr(inc, f, "" if f in ("work_notes", "close_notes") else None)
    return inc


class ITSMClient(ABC):
    """Read context from, and write triage results back to, the ITSM tool."""

    @abstractmethod
    def open_incidents(self) -> list[Incident]:
        """New/unassigned incidents to triage (redacted)."""

    @abstractmethod
    def resolved_incidents(self) -> list[Incident]:
        """Historical resolved/closed incidents (with work notes) for retrieval."""

    @abstractmethod
    def kb_articles(self) -> list[KBArticle]: ...

    @abstractmethod
    def problems(self) -> list[Problem]: ...

    @abstractmethod
    def changes(self, cis: Iterable[str], start: datetime, end: datetime) -> list[Change]: ...

    @abstractmethod
    def config_item(self, name: str) -> Optional[ConfigItem]: ...

    @abstractmethod
    def related_cis(self, name: str) -> list[str]:
        """CIs directly upstream/downstream of ``name`` in the CMDB (excluding services)."""

    @abstractmethod
    def groups(self) -> list[str]: ...

    @abstractmethod
    def update_incident(self, inc: Incident, fields: dict[str, Any], work_note: str) -> dict[str, Any]:
        """Write the triage result back. Returns a record of what was (or would be) written."""


class LogAnalyticsClient(ABC):
    @abstractmethod
    def query(self, kql: str, resource: Optional[str], hours: int = 24,
              end: Optional[datetime] = None) -> list[dict[str, Any]]:
        """Run a KQL query over the ``hours`` before ``end`` (default: now)."""


class AutomationClient(ABC):
    @abstractmethod
    def start_runbook(self, runbook: str, parameters: dict[str, Any]) -> dict[str, Any]:
        """Start an Azure Automation runbook job. Returns ``{"job_id", "status", ...}``."""
