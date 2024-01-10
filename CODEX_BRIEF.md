# Standing orders for Codex (read before every milestone)

You are the implementing engineer on **Backroom**. `SPEC.md` is the contract; read the sections for your milestone fully before writing code. A senior engineer (Claude) reviews every line, re-runs every test and recounts every number, so correctness beats speed and honesty beats a good-looking result.

## Rules

1. **Work only inside this repository** (`D:\claude-workspace\backroom`). Never write outside it. Temporary files go in `.local/temp/`.
2. **No network.** Do not install packages, call APIs or download anything. The venv `.venv` already has: pydantic, pdfplumber, reportlab, rapidfuzz, httpx, psycopg, flask, python-dotenv, pytest, ruff. If you truly need another package, stop and write the request in your report.
3. **No secrets.** Never read, print or create `.env`. Never write a real-looking API key anywhere. Model calls in tests use fake providers (see `tests/test_llm.py`, `tests/test_schema_output.py`). Code that calls the model goes through `backroom.settings.build_llm(...)`; pipelines and evals default to `offline=True`.
4. **Synthetic data only.** No real company, restaurant, delivery platform, person or brand names. The client is "Saffron & Salt Group"; platforms are "Platform A/B/C".
5. **Held-out data is sacred.** Never open, print or tune against layout `L4` or email style `E2` outputs when writing prompts or rules. Only the generator writes them and only the eval scores them.
6. **Money is `Decimal`.** No floats for money anywhere.
7. **SQL is always parameterised.** No f-strings or concatenation with values.
8. **Never commit, push, deploy, or run git commands that change history.** Claude does git.
9. Run, from the repo root, with the venv:
   - `.venv\Scripts\python.exe -m pytest -q`
   - `.venv\Scripts\python.exe -m ruff check .` and `.venv\Scripts\python.exe -m ruff format .`
   - `.venv\Scripts\python.exe -m backroom.verify m<N>` (add your milestone's checks to `backroom/verify.py`)

   All must pass before you finish.
10. Keep code readable: small modules, docstrings that say *why*, no dead code, no placeholder TODOs in shipped code.
11. **Report:** finish by writing `reports/M<N>.md` with what you built (files), test and gate output (pasted, real), any deviation from SPEC and why, anything you are unsure of, and exact counts (documents per layout, problems per kind, and so on). Do not claim anything you did not run.

## Reuse (read these before writing your own)

- PDF and .eml generation style, seeded scenario, ground truth: `D:\claude-workspace\order-desk\synth\` (read-only reference; copy ideas, not branding).
- PDF word extraction and .eml parsing: `D:\claude-workspace\order-desk\backend\extract\pdf.py`, `email.py`.
- Check-message style: `D:\claude-workspace\order-desk\backend\check\engine.py`.
- E-invoicing rules: `D:\claude-workspace\invoice-ready\src\lib\validate.ts`, `compute.ts`.
- Flask security pattern: `D:\codex\loopdesk\loopdesk\security.py` (`client_address`).

(Those paths are outside this repo: **read only**; never modify them.)
