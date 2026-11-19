from agents.connectors.local import LocalITSM
from agents.connectors.kb_parse import parse_kb


def test_safe_flag_parsing():
    safe = parse_kb("k", "t", "<h3>Automation</h3><p>Runbook: <b>RB-A</b> (safe to run automatically with approval)</p>"
                              "<h3>Escalation</h3><p>Resolver group: SKR Backup & DR</p>")
    assert safe.runbook == "RB-A" and safe.safe_to_automate and safe.resolver_group == "SKR Backup & DR"
    manual = parse_kb("k", "t", "<h3>Automation</h3><p>Runbook: <b>RB-B</b> (manual / approval required - do NOT auto-run)</p>")
    assert manual.runbook == "RB-B" and not manual.safe_to_automate
    none = parse_kb("k", "t", "<h3>Symptoms</h3><p>x</p>")
    assert none.runbook is None and not none.safe_to_automate


def test_resolver_group_html_entities_unescaped():
    # The PDI returns KB HTML with "&amp;"; a raw match split group votes and routed a
    # Key Vault incident to an unrelated (auto-runnable) runbook.
    a = parse_kb("k", "t", "<h3>Escalation</h3><p>Resolver group: SKR Identity &amp; Access Mgmt</p>")
    assert a.resolver_group == "SKR Identity & Access Mgmt"


def test_dataset_kb():
    kb = LocalITSM().kb_articles()
    assert len(kb) == 20
    assert sum(a.safe_to_automate for a in kb) == 12
