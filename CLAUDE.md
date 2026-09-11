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

The loader pulls **everything** (metrics, tier/medspa-id lookup, GFE,
marketing funnel) from the consolidated **[New Embedded] Monthly Business
Review** dashboard (`NEW_MBR_ID` in `omni_loader.py`). The team iterates on
that dashboard and republishing can mint a NEW document id (it changed
6b24fa95 → 7c568f71 mid-migration) — `_resolve_mbr_dashboard()` falls back
to a lookup by exact document name when the id 404s. Only two extras remain
on separate dashboards: supplies savings and the marketing *campaign-level*
table. The old per-report dashboards (`bfd963dd` is now literally named
"[DO NOT USE]") are referenced only by `web/app.py`'s practice-list and
debug endpoints.

Windowed tiles (`QUERY_WINDOW_MONTHS`) return one row per month and their
sort order varies — always key rows by 'YYYY-MM' label (`_month_map`),
never by position.

Do not "simplify" the workarounds below — each one fixed a real
wrong-numbers-in-a-client-report bug:

1. **Scope by `medspa_id`, never by name.** Duplicate medspa records exist
   (same business under two names, or two records with identical names). The
   filter must be a *number*-typed EQUALS filter — string-typed values are
   rejected by Omni.
2. **Dashboard queries ship with baked-in filters** from testing (date
   templates, `medspa_name_with_id` values, a `medspa_id=1568` on the Staff
   Performance and Total Sales by Service tiles, hardcoded GFE reviewer
   lists). `_add_filters` strips ALL practice-scoping filters (name,
   name_with_id, id) before adding its own — a leftover baked id ANDed with
   a name filter returns zero rows (The SKNMUSE, Aug 2026). For date
   ranges, overwrite the SAME field the query already filters on — a
   different date field ANDs against the baked one and zeroes out
   historical months. `QUERY_DATE_FIELDS` in `omni_loader.py` maps each
   query to its correct field.
   The medspa-id lookup searches by full name first, then longest word,
   then first word — a first-word-only CONTAINS breaks "The …" practices
   (hundreds of matches, 50-row cap).
   Exception to "strip all baked filters": GFE queries MUST keep reviewer
   scoping (`is_gfe_savings_reviewer = true`, injected by the loader). The
   submissions mart also holds practices' own in-house good-faith exams —
   unscoped, Oro Valley's in-house reviews counted as "Moxie Covered"
   savings. The flag composes correctly with the medspa_id filter; a
   practice with no Moxie-covered GFEs correctly returns 0 and the report
   hides the section.
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
   The editor autosaves its ENTIRE form, so `/api/update` and `/api/save`
   require a `_rev` (the session's `updated_at` the tab loaded) and 409 on
   mismatch — without this, one stale background tab re-pins every old
   value as a manual override (it resurrected Oro Valley's corrected GFE
   numbers within an hour). Successful saves return the new `rev`.
9. **Enterprise marketing numbers can be agency-validated.** The compiled
   workbook ("Enterprise Reporting [Compiled] - <Month>.xlsx") uploads on
   the Monthly Assets page; `src/validated_marketing.py` parses it and
   `_apply_validated_marketing` overlays those figures on every generation.
   Precedence: editor edits > validated workbook > Omni attribution. The
   sheet's leading number is usually the medspa id, but not always (the
   practice named "424 Cosmetic Dermatology" is id 1790) — ids are
   reconciled against Omni at upload time.

## Supplies savings — executes Shannon's code, never re-port it

`src/savings_loader.py` computes the Supplies Savings page by running
Shannon's `calc-bundle.js` VERBATIM in headless Chromium (Playwright is
already present for PDF export): `src/savings_harness.js` is a thin
faithful copy of her dashboard's dispatch (calcVendor/makePeriod/
getRebates); the bundle, vendor_config, pricing_eras, rebates, medspas
and name_map are fetched live from shannon-hue.github.io (15-min cache,
committed fallback under `web/static/supplies-savings/`). Transactions
are prefiltered to the practice's identifiers (superset match) before
being passed into the page, ~72MB → ~0.5MB. The old Python port of her
math remains only as a fallback — do NOT extend it; historical porting
drift caused wrong client-facing numbers (Salena Dodman, Jan 2026).
Verify changes against her dashboard's numbers for a known practice.

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
