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

    matcher = Matcher([Entity("supplier", "S", ("Gulf Fresh Produce",))])
    fake = Fake()
    proposal = matcher.match("supplier", "Gulf Fresh", llm=fake, model_check=True)
    assert proposal.decision == "review" and proposal.recommendation is True
    assert proposal.status == "proposed" and matcher.resolve("supplier", "Gulf Fresh") is None
    matcher.match("supplier", "Gulf Fresh Produce", llm=fake, model_check=True)
    matcher.match("outlet", "zzzzzz", llm=fake, model_check=True)
    assert fake.calls == 1
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
