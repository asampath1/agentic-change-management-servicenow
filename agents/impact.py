"""Impact agent: what else breaks if this CI breaks (CMDB blast radius) and how critical it is."""
from __future__ import annotations

from .connectors.base import ITSMClient
from .models import Change


class ImpactAgent:
    name = "impact"

    def __init__(self, itsm: ITSMClient):
        self.itsm = itsm

    def run(self, ch: Change) -> dict:
        ci = self.itsm.config_item(ch.ci) if ch.ci else None
        if not ci:
            return {"ci": ch.ci, "known_ci": False, "tier": 3, "service": None, "dependents": [], "services": []}
        deps = self.itsm.dependents(ci.name)
        dep_items = [self.itsm.config_item(d) for d in deps]
        services = sorted({x.service for x in [ci, *dep_items] if x and x.service})
        return {"ci": ci.name, "known_ci": True, "description": ci.description, "service": ci.service, "tier": ci.tier,
                "dependents": deps, "services": services,
                "max_tier": min([ci.tier, *[x.tier for x in dep_items if x]]), "support_group": ci.support_group}
