# MBR — Monthly Business Review generator

Internal Moxie tool that generates Monthly Business Review reports for medspa
practices. Pulls metrics from Omni Analytics, generates AI narratives
(Anthropic API), renders an editable HTML report, and exports PDF/PPTX.

**Deploys:** every push to `main` auto-deploys to Render at
`https://mbr-4hbe.onrender.com` (~2–3 min). There is no staging environment —
treat pushes to `main` as production releases.

## Running

```bash
pip install -r requirements.txt
playwright install chromium          # needed for PDF export

# Web app (the primary interface)
python web/app.py                    # http://localhost:5000

# CLI (one-off generation)
python mbr.py generate --practice "Name" --month 7 --year 2026
```

Key env vars (loaded from `.env` at the project root if present; never commit it):

| Var | Purpose |
|---|---|
| `OMNI_API_KEY` | Omni Analytics API — the main data source. Lives only as a Render env var. |
| `ANTHROPIC_API_KEY` | AI narratives (falls back to rule-based text if unset) |
| `MBR_API_KEY` | Auth for `/api/v1/mbr` and the debug endpoints (`X-Api-Key` header) |
| `SUPABASE_URL` + `SUPABASE_SERVICE_KEY` | Session/asset persistence; falls back to `data/` files if unset |
| `PERSISTENT_DIR` | File-storage base on Render (defaults to `data/` locally) |
| `BACKUP_REPO_URL` + `BACKUP_TOKEN` | Optional off-site git mirror of saved reports |
| `GMAIL_*`, `GOOGLE_*` | Tox Club email drafts |

## Layout

- `web/app.py` — Flask app, ~3k lines, all routes. Report generation
  (`/api/generate`), editor autosave (`/api/update`, `/api/save`), versioning,
  exports, batch jobs, monthly assets, Tox Club, supplies-savings.
- `src/omni_loader.py` — everything Omni: dashboard/query loading, filter
  injection, quirk workarounds. Most data bugs live (and get fixed) here.
- `src/data_schema.py` — `MBRData` dataclass, the single shape passed around.
- `src/narrative.py` — AI + rule-based narrative generation.
- `src/html_renderer.py` + `templates/report.html.j2` — report rendering;
  PDF via Playwright/Chromium.
- `src/slide_builder.py` — legacy PPTX export.
- `web/templates/` + `web/static/` — dashboard/editor UI (vanilla JS).
- `web/static/supplies-savings/` — embedded sub-app with its own CLAUDE.md.
- `docs/MBR_Tool_Instructions.md` — end-user guide.
- `docs/omni-api-spec.md` — Omni API notes.

## Omni data model — hard-won quirks

The loader pulls queries from the consolidated **[New Embedded] Monthly
Business Review** dashboard (`NEW_MBR_ID = 6b24fa95`), the single source of
truth for report metrics, plus a legacy dashboard for tier/medspa-id lookup.
Do not "simplify" the workarounds below — each one fixed a real
wrong-numbers-in-a-client-report bug:

1. **Scope by `medspa_id`, never by name.** Duplicate medspa records exist
   (same business under two names, or two records with identical names). The
   filter must be a *number*-typed EQUALS filter — string-typed values are
   rejected by Omni.
2. **Dashboard queries ship with baked-in filters** from testing (date
   templates, `medspa_name_with_id` values, hardcoded GFE reviewer lists).
   For date ranges, overwrite the SAME field the query already filters on —
   a different date field ANDs against the baked one and zeroes out
   historical months. `QUERY_DATE_FIELDS` in `omni_loader.py` maps each query
   to its correct field.
3. **Per-type breakdowns of invoice-level measures overlap** (an invoice with
   multiple item types counts fully in every type row). Additive breakdowns
   must use the line-items mart (`dbt__moxie_invoice_line_items_mart`).
4. **Staff Sales Summary is a pivot with subtotal rows** — summing raw rows
   triple-counts. The loader flattens it (clears pivots/sorts/totals).
5. **Omni rate-limits aggressively (429s).** Dashboard definitions are cached
   ~10 min; the loader hard-fails on zero queries rather than silently saving
   a $0 report.
6. **Revenue identity** (all on `transaction_date_et`,
   `dbt__moxie_invoice_transactions_mart`): Total Sales
   (`total_invoice_revenue_sum`) − wallet redemptions = Gross Revenue
   (`gross_revenue_sum`) − discounts = Net Revenue (`net_revenue_sum`).
   The seven `subtotal__*_sum` category fields + `fee_amount_sum` sum to
   Total Sales exactly.
7. **Goals live in `dbt__moxie_medspa_monthly_summary_mart`** (own topic, not
   joinable from invoices/medspas topics; month grain is `series_month`).
8. **Manual editor edits are persisted as `manual_overrides`** on the session
   and re-applied after regeneration — a regenerate must never silently wipe
   human corrections (`discard_edits: true` resets to pure Omni values). The
   parity check (`_compute_parity`) runs on raw Omni values *before*
   overrides are re-applied.

## Debugging Omni queries

Auth-gated endpoints (send `X-Api-Key: $MBR_API_KEY`):

- `GET /api/debug-query?dashboard=<id>&name=<query>&run=1&practice=..&month=..&year=..`
  — inspect/run any dashboard query with the loader's filters applied.
- `GET /api/debug-get?path=/v1/...` — raw Omni API proxy (e.g. list documents
  to find which dashboard backs a Suite screen).
- `GET /api/verify-parity` — field-by-field comparison against the
  Suite-embedded dashboard.

## Conventions

- Data files (`*.csv`, `data/sessions/`, `data/monthly/`) are gitignored —
  they can contain client info. Never commit them or `.env`.
- No test suite. Verify loader changes with `/api/debug-query` +
  `/api/verify-parity` against a known practice/month before pushing.
