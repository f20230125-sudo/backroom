"""Rebuild data deterministically; temporary runs use a data root inside .local/temp."""

import argparse
import shutil
from collections import Counter
from pathlib import Path

from backroom import DATA, ROOT
from backroom.models import GroundTruth
from backroom.synth.render_csv import payout_csv, records_csv
from backroom.synth.render_eml import email_bytes
from backroom.synth.render_pdf import invoice_pdf, payout_pdf
from backroom.synth.scenario import SEED, build


def generate(output: Path = DATA, *, seed: int = SEED) -> GroundTruth:
    """Only a verified repository data root or .local/temp child can be replaced."""
    output = output.resolve()
    temporary = (ROOT / ".local" / "temp").resolve()
    if not output.is_relative_to(ROOT.resolve()) or (
        output != DATA.resolve() and not output.is_relative_to(temporary)
    ):
        raise ValueError("Generation output must be data/ or a child of .local/temp/")
    generated = output / "generated"
    if generated.is_symlink() or (generated.exists() and generated.resolve() != generated):
        raise ValueError("Refusing to replace a redirected generated directory")
    if generated.exists():
        shutil.rmtree(generated)
    generated.mkdir(parents=True)
    scenario = build(seed)
    truth = scenario.truth

    def write(path: str, content: bytes) -> None:
        target = output / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    for index in range(0, len(scenario.pos), 4):
        write(truth.feeds[index // 4].path, records_csv(scenario.pos[index : index + 4]))
    write("generated/ledger/ledger.csv", records_csv(scenario.ledger))
    for doc in truth.documents:
        if doc.kind.startswith("email_"):
            content = email_bytes(doc)
        elif doc.kind == "payout_statement":
            content = payout_pdf(doc.truth) if doc.layout == "PDF_C" else payout_csv(doc.truth)
        else:
            content = invoice_pdf(doc)
        write(doc.path, content)
    (output / "ground_truth.json").write_text(
        truth.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    return truth


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DATA)
    args = parser.parse_args()
    truth = generate(args.output)
    print(
        f"Generated {len(truth.documents)} documents, {len(truth.feeds)} feeds, "
        f"{len(truth.problems)} business problems, {len(truth.security_plants)} security plants."
    )
    print("Layouts:", dict(sorted(Counter(doc.layout for doc in truth.documents).items())))


if __name__ == "__main__":
    main()
