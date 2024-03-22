"""Wording stays subordinate to facts; no cache/network is needed for templates."""

import json

from backroom.brief import generate_brief
from backroom.db.repo import dumps
from backroom.drafts import TEMPLATE, generate_drafts, make_draft, valid_wording
from backroom.settings import CacheMiss
from eval.m4 import hours_saved
from tests.test_ask import Fake

pytest_plugins = ["tests.test_ask"]


class Missing:
    def complete(self, *a, **kwargs):
        raise CacheMiss("fake missing recording")


def test_offline_templates_and_invoice_identity(repo):
    drafts = generate_drafts(repo, llm=Missing())
    assert len(drafts) == 1
    assert drafts[0]["provenance"] == TEMPLATE
    assert "FAKE/26/001" in drafts[0]["body"]
    assert "0.50" in drafts[0]["body"]
    assert drafts[0]["status"] == "draft"
    brief = generate_brief(repo, 1, llm=Missing())
    assert brief["provenance"] == TEMPLATE
    assert len(brief["body"].split()) <= 120
    assert json.loads(repo.rows("SELECT payload FROM briefs")[0]["payload"]) == brief
    generate_brief(repo, 2, llm=Missing())
    assert len(repo.rows("SELECT * FROM briefs")) == 2
    assert not valid_wording("Please amend invoice OTHER/26/001.", drafts[0]["facts"])
    assert not valid_wording("Correct VAT to 99.00.", drafts[0]["facts"])
    assert valid_wording(drafts[0]["body"], drafts[0]["facts"])
    assert generate_drafts(repo, llm=Missing()) == drafts


def test_model_wording_verified_and_facts_appended(repo):
    fake = Fake(
        {
            "to_role": "supplier_accounts",
            "subject": "Please clarify VAT",
            "body": "Please check the invoice and confirm the correct VAT.",
        }
    )
    draft = generate_drafts(repo, llm=fake)[0]
    assert draft["provenance"] == "model"
    assert "FAKE/26/001" in draft["body"] and "0.50" in draft["body"]
    assert "<facts>" in fake.messages[0][1]["content"]
    for body in ["VAT is 9999", "Invoice OTHER/26/001 is wrong"]:
        draft = generate_drafts(
            repo, llm=Fake({"to_role": "supplier_accounts", "subject": "Check", "body": body})
        )[0]
        assert draft["provenance"] == "template (model wording failed fact check)"


def test_review_and_einvoice_drafts_and_stale_cleanup(repo):
    repo.upsert(
        "review_queue",
        {
            "id": "review",
            "document_id": "FAKE-1",
            "reason": "Missing address",
            "status": "open",
            "payload": "{}",
        },
    )
    repo.upsert(
        "einvoice_findings",
        {
            "id": "trn",
            "record_type": "document",
            "record_id": "FAKE-1",
            "rule": "IBR-132-AE",
            "message": "Invalid TRN",
            "payload": "{}",
        },
    )
    drafts = generate_drafts(repo, llm=Missing())
    assert len(drafts) == 3
    assert {d["to_role"] for d in drafts} == {"supplier_accounts", "review_owner"}
    repo.execute("DELETE FROM review_queue")
    repo.execute("DELETE FROM einvoice_findings")
    assert len(generate_drafts(repo, llm=Missing())) == 1
    assert len(repo.rows("SELECT * FROM drafts")) == 1


def test_brief_number_word_count_and_model_failures(repo):
    generate_drafts(repo, llm=Missing())
    good = "There is one exception, zero review items and one draft. VAT difference: AED 0.50."
    assert generate_brief(repo, 1, llm=Fake({"body": good}))["provenance"] == "model"
    for body in [
        "There are 9999 exceptions",
        "word " * 121,
        "There are no issues",
        "One exception, zero review items and one draft: invoice OTHER/26/001 has AED 0.50 VAT.",
    ]:
        brief = generate_brief(repo, 2, llm=Fake({"body": body}))
        assert brief["provenance"].startswith("template")
        assert len(brief["body"].split()) <= 120

    class Broken:
        def complete(self, *a, **kw):
            raise RuntimeError("unavailable")

    assert generate_drafts(repo, llm=Broken())[0]["provenance"].startswith("template")
    assert generate_brief(repo, 3, llm=Broken())["provenance"].startswith("template")


def test_hours_use_counts_assumptions_and_measured_run(repo):
    generate_drafts(repo, llm=Missing())
    assert hours_saved(repo)["duration_state"] == "not run"
    repo.upsert(
        "run_log",
        {
            "id": 1,
            "started": "fixed",
            "finished": "fixed",
            "days": 92,
            "documents": 1,
            "model_calls": 0,
            "cache_hits": 0,
            "exceptions_by_kind": "{}",
            "review_items": 0,
            "duration_ms": 60000,
            "watermark": "2026-09-30",
            "payload": dumps({"status": "completed", "not_run": 0}),
        },
    )
    result = hours_saved(repo)
    assert result["counts"] == {
        "auto_accepted_document": 1,
        "review_document_overhead": 0,
        "outlet_day_reconciled": 2,
        "draft_produced": 1,
    }
    assert result["manual_minutes_saved"] == "17"
    assert result["estimated_hours_saved"] == "0.27"
    assert result["full_pipeline_duration_ms"] == 60000
    assert all(a["basis"] == "assumption" for a in result["assumptions"])


def test_ambiguous_source_identifier_cannot_block_template_storage():
    facts = {
        "source_type": "exception",
        "kind": "VAT_WRONG",
        "amount": "0.50",
        "invoice_numbers": ["TI 12"],
        "message": "Invoice TI 1234 is mentioned in source prose.",
    }
    draft = make_draft(facts, llm=Missing())
    assert draft["provenance"] == TEMPLATE
    assert "TI 12" in draft["body"] and "TI 1234" not in draft["body"]
    assert valid_wording(draft["body"], facts)
