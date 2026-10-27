"""Exercise source generation and planted facts without opening held-out source outputs."""

from collections import Counter
from decimal import Decimal
from email import policy
from email.parser import BytesParser
from pathlib import Path
from tempfile import TemporaryDirectory

import pdfplumber
import pytest

from backroom import ROOT
from backroom.models import GroundTruth, InvoiceRecord, money
from backroom.synth.generate import generate
from backroom.synth.render_pdf import arabic_label
from backroom.synth.scenario import PROBLEM_COUNTS, SEED, build
from backroom.verify import check_dataset, file_hashes


@pytest.fixture(scope="session")
def corpus():
    temporary = ROOT / ".local" / "temp"
    temporary.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="m1-tests-", dir=temporary) as directory:
        root = Path(directory) / "data"
        truth = generate(root)
        yield root, truth


def test_seed_controls_whole_scenario():
    first, second = build(), build()
    assert first.truth == second.truth
    assert first.pos == second.pos and first.ledger == second.ledger
    assert first.pos != build(SEED + 1).pos
    assert Counter(p.kind for p in first.truth.problems) == PROBLEM_COUNTS


def test_decimal_rounding_and_json_roundtrip(corpus):
    root, truth = corpus
    assert money(Decimal("1.005")) == Decimal("1.01")
    assert money(Decimal("-1.005")) == Decimal("-1.01")
    recovered = GroundTruth.model_validate_json((root / "ground_truth.json").read_bytes())
    assert recovered == truth
    records = [doc.truth for doc in recovered.documents if isinstance(doc.truth, InvoiceRecord)]
    assert all(isinstance(record.total, Decimal) for record in records)
    assert all(isinstance(line.unit_price, Decimal) for record in records for line in record.lines)
    assert all(record.quotes for record in records)
    assert all(
        f"lines.{i}.line_net" in record.quotes
        for record in records
        for i, _ in enumerate(record.lines)
    )


def test_regeneration_removes_stale_files_and_is_byte_identical(corpus):
    root, _ = corpus
    with TemporaryDirectory(prefix="m1-repeat-", dir=ROOT / ".local/temp") as directory:
        other = Path(directory) / "data"
        (other / "generated").mkdir(parents=True)
        (other / "generated/stale.txt").write_text("must disappear", encoding="utf-8")
        (other / "preserved.txt").write_text("outside generated", encoding="utf-8")
        generate(other)
        assert not (other / "generated/stale.txt").exists()
        assert (other / "preserved.txt").read_text(encoding="utf-8") == "outside generated"
        hashes = file_hashes(other)
        hashes.pop("preserved.txt")
        assert hashes == file_hashes(root)


def test_generator_rejects_unapproved_directory():
    with pytest.raises(ValueError, match="Generation output"):
        generate(ROOT / "backroom")


def test_arabic_labels_have_correct_joined_visual_order():
    assert arabic_label("المورد") == "ﺩﺭﻮﻤﻟﺍ"


def test_all_dataset_checks_pass(corpus):
    root, _ = corpus
    results = check_dataset(root)
    assert results and all(passed for _, passed, _ in results), results


def test_email_attachments_are_real_pdf_copies_and_ids_are_frozen(corpus):
    root, truth = corpus
    docs = {doc.doc_id: doc for doc in truth.documents}
    for doc in truth.documents:
        if doc.kind != "email_with_attachment":
            continue
        message = BytesParser(policy=policy.default).parsebytes((root / doc.path).read_bytes())
        attachments = list(message.iter_attachments())
        assert len(attachments) == 1 and attachments[0].get_content_type() == "application/pdf"
        assert message["Message-ID"] == f"<{doc.doc_id.lower()}@backroom.example>"
        assert message.get_boundary() == f"backroom-m1-{doc.doc_id}"
        payload = attachments[0].get_payload(decode=True)
        assert payload.startswith(b"%PDF-")
        if doc.attachment_doc_id:
            assert payload == (root / docs[doc.attachment_doc_id].path).read_bytes()


def test_pdf_metadata_is_fixed(corpus):
    root, truth = corpus
    doc = next(doc for doc in truth.documents if doc.layout == "L1" and doc.kind == "invoice")
    with pdfplumber.open(root / doc.path) as pdf:
        assert "20000101000000" in pdf.metadata["CreationDate"]
        assert pdf.metadata["ModDate"] == pdf.metadata["CreationDate"]
        assert len(pdf.pages) == 1


def test_business_and_security_plants_are_separate(corpus):
    _, truth = corpus
    assert len(truth.problems) == 31 and len(truth.security_plants) == 2
    for plant in truth.security_plants:
        doc = next(doc for doc in truth.documents if doc.doc_id == plant.doc_id)
        assert doc.truth.suspicious_instruction and doc.truth.total > 0
        assert doc.truth.notes == plant.text
    assert len(truth.name_variants) == 40 and len(truth.hard_negatives) == 5
