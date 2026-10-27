# Backroom

A back-office agent for a restaurant group: it reads supplier invoices, emails and delivery-platform payout statements with an LLM, matches messy names, reconciles the POS against delivery payouts and the ledger, flags e-invoicing data problems and answers questions about the numbers. Synthetic data; work in progress. See `SPEC.md`.

Name checks replay offline by default:

```powershell
python -m backroom.names.run
```

The senior engineer can record model checks with `python -m backroom.names.run --record`,
then rerun the offline pipeline and eval. The runner covers observed name proposals,
the 40 evaluation variants and both directions of the five hard-negative pairs.
It stores scored suggestions and model recommendations; review proposals still require
human approval. Missing recordings preserve review suggestions. Names with weak spelling
evidence can receive a recorded master suggestion and a separate binary identity check;
synonyms are not hardcoded. Offline replay opens the cache read-only.
