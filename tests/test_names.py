"""Proposals cannot change identity without an explicit approval action."""

from types import SimpleNamespace

import pytest

from backroom.db import connect, migrate
from backroom.names.match import Entity, Matcher, approve, approved_names, propose
from backroom.names.normalise import exact_key, normalise
from backroom.settings import CacheMiss


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (" The Gulf & Fresh TRDG L.L.C. ", "gulf and fresh trading llc"),
        ("Pearl F.Z.E.", "pearl fze"),
        ("Pearl F.Z.C.O.", "pearl fzco"),
        ("L.L.C Trading", "llc trading"),
        ("THE_BOWL-LID", "bowl lid"),
    ],
)
def test_normalisation(raw, expected):
    assert normalise(raw) == expected
    assert normalise(expected) == expected
    assert exact_key("Roma Tomato") == exact_key("Tomato, Roma")


def test_exact_ambiguous_grey_new_and_fuzzy_auto():
    matcher = Matcher(
        [
            Entity("supplier", "S1", ("Gulf Fresh Produce",)),
            Entity("supplier", "S2", ("Gulf Fresh Fish",)),
            Entity("item", "I1", ("Kraft Bowl Lid",)),
        ]
    )
    assert matcher.match("supplier", "Gulf Fresh Produce").decision == "auto_match"
    assert matcher.match("supplier", "Gulf Fresh").decision == "review"
    assert matcher.resolve("supplier", "Gulf Fresh") is None
    assert matcher.match("item", "Kraft Bowl").decision == "review"
    assert matcher.match("outlet", "Kraft Bowl Lid").decision == "new"
    assert matcher.match("supplier", "").decision == "new"
    fuzzy = Matcher([Entity("supplier", "S", ("Copper Harbour Wholesale Trading",))])
    assert fuzzy.match("supplier", "Copper Harbour Wholesale Tradin").decision == "auto_match"
    assert fuzzy.resolve("supplier", "Copper Harbour Wholesale Tradin") is None
    assert (
        Matcher([Entity("item", "A", ("Same",)), Entity("item", "B", ("Same",))]).resolve(
            "item", "Same"
        )
        is None
    )


def test_model_only_grey_recommends_but_cannot_approve():
    class Fake:
        def __init__(self, miss=False):
            self.calls = 0
            self.miss = miss

        def complete(self, messages, **kwargs):
            self.calls += 1
            assert kwargs["schema"]["additionalProperties"] is False
            if self.miss:
                raise CacheMiss("missing")
            return SimpleNamespace(text='{"same_entity":true,"reason":"Check address"}')

    matcher = Matcher(
        [
            Entity("supplier", "S", ("Gulf Fresh Produce",)),
            Entity("supplier", "F", ("Gulf Fresh Fish",)),
        ]
    )
    fake = Fake()
    proposal = matcher.match("supplier", "Gulf Fresh", llm=fake, model_check=True)
    # Fits both masters: a person decides; the model is not asked to pick one.
    assert proposal.decision == "review" and proposal.recommendation is None
    assert "Ambiguous" in proposal.reason and fake.calls == 0
    assert proposal.status == "proposed" and matcher.resolve("supplier", "Gulf Fresh") is None
    matcher.match("supplier", "Gulf Fresh Produce", llm=fake, model_check=True)
    matcher.match("outlet", "zzzzzz", llm=fake, model_check=True)
    assert fake.calls == 0
    missing = matcher.match("supplier", "Gulf Fresh", llm=Fake(True), model_check=True)
    assert missing.decision == "review" and missing.recommendation is None


def test_approval_rejection_persistence_and_parameterised_names():
    with connect(":memory:") as repo:
        migrate(repo)
        repo.upsert(
            "suppliers",
            {
                "id": "S",
                "legal_name": "Gulf Fresh Produce",
                "trade_name": "Gulf Fresh Produce",
                "trn": "100000000000003",
                "payload": "{}",
            },
        )
        entities = [Entity("supplier", "S", ("Gulf Fresh Produce",))]
        matcher = Matcher(entities)
        variant = "Gulf'; DROP TABLE suppliers;--"
        proposal = matcher.match("supplier", variant)
        propose(repo, proposal)
        assert Matcher(entities, approved_names(repo)).resolve("supplier", variant) is None
        approve(repo, "supplier", variant, "S")
        propose(repo, proposal)
        assert Matcher(entities, approved_names(repo)).resolve("supplier", variant) == "S"
        approve(repo, "supplier", variant, "S", approved=False)
        propose(repo, proposal)
        assert repo.rows("SELECT status FROM name_map")[0]["status"] == "rejected"
        assert Matcher(entities, approved_names(repo)).resolve("supplier", variant) is None
        assert len(repo.rows("SELECT id FROM suppliers")) == 1
        with pytest.raises(ValueError, match="Unknown canonical"):
            approve(repo, "supplier", variant, "DOES-NOT-EXIST")
        with pytest.raises(ValueError, match="No name proposal"):
            approve(repo, "supplier", "absent", "S")


@pytest.mark.parametrize(
    "variant",
    [
        "TOMATO-ROMA 5KG",
        "Roma Tomato (5kg crate)",
        "Tomato Roma - CHILLED",
        "Roma Tomatoes / KG",
        "Tomato Roma FRESH",
    ],
)
def test_item_annotations_and_plurals_are_context_specific(variant):
    matcher = Matcher([Entity("item", "T", ("Tomato Roma",))])
    assert matcher.match("item", variant).canonical_id == "T"
    assert matcher.match("item", variant).decision == "auto_match"
    assert normalise("Fresh Trading", "supplier") == "fresh trading"


def test_containment_requires_unique_evidence_in_the_field_type():
    entities = [
        Entity("outlet", "J", ("Saffron & Salt JBR Walk",)),
        Entity("outlet", "D", ("Saffron & Salt DIFC",)),
        Entity("supplier", "S", ("Gulf Fresh Produce Trading LLC",)),
        Entity("supplier", "F", ("Gulf Fresh Fish Trading LLC",)),
        Entity("item", "T", ("Roma Tomato",)),
    ]
    matcher = Matcher(entities)
    for variant in ("JBR", "JBR Walk", "S&S JBR"):
        assert matcher.match("outlet", variant).canonical_id == "J"
        assert matcher.match("outlet", variant).decision == "auto_match"
    assert matcher.match("supplier", "Gulf Fresh").decision == "review"
    assert matcher.match("supplier", "GULF PRODUCE FRESH TRDG").decision == "auto_match"
    assert matcher.match("item", "JBR").decision == "new"
    location = Matcher(
        [
            Entity("outlet", "K", ("Central Kitchen Al Quoz",)),
            Entity("supplier", "P", ("Al Quoz Pantry",)),
        ]
    )
    proposal = location.match("outlet", "Al Quoz")
    assert proposal.decision == "review" and proposal.suggested_id == "K"
    assert {c.canonical_id for c in proposal.candidates} == {"K"}


@pytest.mark.parametrize("accessory", ["Lid", "Cover", "Refill"])
def test_containment_does_not_merge_an_item_with_its_accessory(accessory):
    matcher = Matcher([Entity("item", "L", (f"Kraft Bowl {accessory}",))])
    assert matcher.match("item", "Kraft Bowl").decision == "review"


def test_supplier_identity_requires_unique_valid_master_trn_then_name_evidence():
    matcher = Matcher(
        [
            Entity("supplier", "A", ("Harbour Produce Trading LLC",), "123456789012303"),
            Entity("supplier", "B", ("Harbour Fish Trading LLC",), "123456789012403"),
        ]
    )
    assert matcher.supplier_identity("unrecognised trading name", "123456789012303") == "A"
    assert matcher.supplier_identity("Harbour Fish Trading LLC", "123456789012303") == "A"
    assert matcher.supplier_identity("Harbour Produce", "BAD") == "A"
    assert matcher.supplier_identity("Unknown, Supplier!", "BAD") == "printed:unknown supplier"
    assert matcher.supplier_identity(None, None) is None
    shared = Matcher(
        [
            Entity("supplier", "A", ("Harbour Produce Trading LLC",), "123456789012303"),
            Entity("supplier", "B", ("Harbour Fish Trading LLC",), "123456789012303"),
        ]
    )
    assert shared.supplier_identity("Harbour Produce", "123456789012303") == "A"
    assert shared.supplier_identity("Harbour Fish", "123456789012303") == "B"
    assert shared.supplier_identity("Harbour", "123456789012303") == "printed:harbour"
    invalid = Matcher([Entity("supplier", "A", ("Harbour",), "BAD")])
    assert invalid.supplier_identity("Unknown", "BAD") == "printed:unknown"
    rejected = Matcher(matcher.entities, rejected={("supplier", exact_key("Harbour Produce"))})
    assert rejected.supplier_identity("Harbour Produce") == "printed:harbour produce"
    assert rejected.supplier_identity("Harbour Produce", "123456789012303") == "A"


def test_persisted_auto_and_approved_names_resolve_but_grey_recommendations_do_not():
    from backroom.names.match import Proposal, automatic_names

    with connect(":memory:") as repo:
        migrate(repo)
        entities = [Entity("supplier", "A", ("Harbour Produce",))]
        propose(repo, Proposal("supplier", "legacy alias", "A", "auto_match"))
        propose(repo, Proposal("supplier", "review alias", "A", "review", recommendation=True))
        matcher = Matcher(entities, approved_names(repo), automatic=automatic_names(repo))
        assert matcher.supplier_identity("legacy alias") == "A"
        assert matcher.supplier_identity("review alias") == "printed:review alias"
        # Refreshes may update proposals, but never overwrite a human's chosen ID.
        repo.execute(
            "UPDATE name_map SET status = ?, canonical_id = ? WHERE variant = ?",
            ("approved", "HUMAN", "review alias"),
        )
        propose(repo, Proposal("supplier", "review alias", "A", "review"))
        assert approved_names(repo)[("supplier", exact_key("review alias"))] == "HUMAN"


def test_names_runner_defaults_to_readonly_offline_replay_and_keeps_suggestions(monkeypatch):
    from backroom.names.run import run_names

    class Fake:
        store = SimpleNamespace(close=lambda: None)

        def complete(self, *args, **kwargs):
            raise CacheMiss("unrecorded")

    monkeypatch.setattr("backroom.names.run.offline_llm", lambda: Fake())
    monkeypatch.setattr("backroom.names.run.build_llm", lambda **kw: pytest.fail("Network"))
    source = {
        "suppliers": [
            {
                "id": "A",
                "legal_name": "Harbour Produce",
                "trade_name": "Harbour Produce",
                "trn": "BAD",
            },
            {"id": "B", "legal_name": "Harbour Fish", "trade_name": "Harbour Fish", "trn": "BAD"},
        ],
        "outlets": [],
        "items": [],
        "name_variants": [{"entity_type": "supplier", "variant": "Harbour", "canonical_id": "A"}],
    }
    with connect(":memory:") as repo:
        result = run_names(repo=repo, source=source)
        row = repo.rows("SELECT * FROM name_map")[0]
        import json

        payload = json.loads(row["payload"])
        assert result["mode"] == "offline" and row["decision"] == "review"
        assert payload["suggested_id"] and payload["score"] > 0
        assert payload["recommendation"] is None and "Ambiguous" in payload["reason"]


def test_semantic_names_need_a_recorded_selection_and_pair_check_and_never_auto_merge():
    class Fake:
        def __init__(self, *, missing_pair=False, suggested_id="A"):
            self.calls = 0
            self.missing_pair = missing_pair
            self.suggested_id = suggested_id

        def complete(self, messages, **kwargs):
            self.calls += 1
            if "suggested_id" in kwargs["schema"]["properties"]:
                import json

                prompt = json.loads(messages[1]["content"])
                assert {m["id"] for m in prompt["masters"]} == {"A", "B"}
                return SimpleNamespace(
                    text=json.dumps({"suggested_id": self.suggested_id, "reason": "same vegetable"})
                )
            if self.missing_pair:
                raise CacheMiss("pair missing")
            assert "Aubergine" in messages[1]["content"]
            return SimpleNamespace(text='{"same_entity":true,"reason":"same vegetable"}')

    matcher = Matcher(
        [
            Entity("item", "A", ("Aubergine",)),
            Entity("item", "B", ("Laban",)),
            Entity("supplier", "S", ("Eggplant Trading",)),
        ]
    )
    assert matcher.match("item", "Eggplant").decision == "new"
    fake = Fake()
    proposal = matcher.match("item", "Eggplant", llm=fake, model_check=True)
    assert fake.calls == 2 and proposal.decision == "review"
    assert proposal.recommendation is True and proposal.suggested_id == "A"
    assert matcher.resolve("item", "Eggplant") is None
    missing = matcher.match("item", "Eggplant", llm=Fake(missing_pair=True), model_check=True)
    assert missing.decision == "review" and missing.suggested_id == "A"
    assert missing.recommendation is None and "No recorded" in missing.reason
    invalid = matcher.match("item", "Eggplant", llm=Fake(suggested_id="S"), model_check=True)
    assert invalid.decision == "new" and invalid.recommendation is None
