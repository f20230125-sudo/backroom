"""UAE rule ports preserve exact TRN shape and explain missing master data."""

from backroom import DATA
from backroom.einvoice.rules import check_invoices, check_masters, valid_emirate, valid_trn
from backroom.inputs import manifest
from backroom.models import InvoiceRecord, Supplier
from backroom.recon.types import Invoice


def test_trn_and_emirate_ports():
    assert valid_trn("100000000000003")
    for value in (
        "",
        "200000000000003",
        "100000000000001",
        "10000000000003",
        "١00000000000003",
        "100000000000003\n",
    ):
        assert not valid_trn(value)
    for value in ("AUH", "DXB", "SHJ", "UAQ", "FUJ", "AJM", "RAK", "DU", "AZ"):
        assert valid_emirate(value)
    assert not valid_emirate("dubai") and not valid_emirate("")


def test_missing_fields_zero_values_bad_trns_and_codes():
    record = InvoiceRecord(
        supplier_name="Supplier",
        supplier_trn="BAD",
        buyer_trn="WRONG",
        supplier_address_emirate="ZZ",
        net="0",
        vat="0",
        total="0",
    )
    findings = check_invoices([Invoice("D", record, "S")])
    assert {f.rule for f in findings} == {"MISSING_FIELD", "IBR-132-AE", "IBR-128-AE"}
    missing = next(f for f in findings if f.rule == "MISSING_FIELD")
    assert "lines" in missing.message and "net," not in missing.message
    trn = next(f for f in findings if f.rule == "IBR-132-AE")
    assert "BAD" in trn.message and "WRONG" in trn.message
    assert all("would block an e-invoice because" in f.message for f in findings)


def test_credit_notes_scoped_by_supplier_and_shared_trn_deduplicated():
    original = InvoiceRecord(invoice_number="ORIGINAL", supplier_trn="100000000000003")
    credit = original.model_copy(
        update={
            "invoice_number": "CN",
            "is_credit_note": True,
            "original_invoice_number": "ORIGINAL",
        }
    )
    inputs = [
        Invoice("D1", original, "S1"),
        Invoice("D2", credit, "S1"),
        Invoice("D3", original, "S2"),
    ]
    findings = check_invoices(inputs)
    assert not any(f.rule == "CREDIT_NOTE_UNLINKED" for f in findings)
    assert [f.record_id for f in findings if f.rule == "TRN_SHARED"] == ["D1|D3"]
    inputs[1] = Invoice("D2", credit, "S3")
    assert any(
        f.rule == "CREDIT_NOTE_UNLINKED" and f.record_id == "D2" for f in check_invoices(inputs)
    )
    credit.original_invoice_number = None
    assert any(
        f.rule == "CREDIT_NOTE_UNLINKED" for f in check_invoices([Invoice("D", credit, "S1")])
    )


def test_master_checks_are_separate_from_document_detection():
    supplier = Supplier.model_validate(manifest(DATA)["suppliers"][0])
    duplicate = supplier.model_copy(update={"id": "SUP-OTHER"}, deep=True)
    assert [f.rule for f in check_masters([supplier, duplicate])] == ["TRN_SHARED"]
    duplicate.trn = "BAD"
    duplicate.address.emirate = "ZZ"
    assert {f.rule for f in check_masters([duplicate])} == {"IBR-132-AE", "IBR-128-AE"}
    duplicate.legal_name = ""
    duplicate.address.street = " "
    missing = next(f for f in check_masters([duplicate]) if f.rule == "MISSING_FIELD")
    assert "legal_name" in missing.message and "address.street" in missing.message


def test_unapproved_original_identity_defers_link_assertion():
    original = InvoiceRecord(invoice_number="ORIGINAL")
    credit = InvoiceRecord(
        invoice_number="CN", is_credit_note=True, original_invoice_number="ORIGINAL"
    )
    findings = check_invoices([Invoice("D1", original, None), Invoice("D2", credit, "S")])
    assert not any(f.rule == "CREDIT_NOTE_UNLINKED" for f in findings)
