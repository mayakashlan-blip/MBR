# Dashboard Bug & Error Log

_Last updated: 2026-04-01_

---

## FIXED

### F-01 — Evolus: Jeaveau column name typo
**Symptom:** Historical data used `Jeaveau Vials` (missing 'u'); new uploads used `Jeuveau Vials`. Spend calculations silently broke for historical rows.  
**Root cause:** Typo in original data column, not caught at upload time.  
**Fix:** Dashboard formula now checks both spellings. Upstream data unchanged (both spellings coexist in file).  
**Status:** ✅ Fixed

---

### F-02 — Evolus: Evolysse Total showing $0
**Symptom:** March 2026 Evolysse rows showed $0 spend.  
**Root cause:** New upload file had no `Evolysse Total` column. Dashboard was reading that column directly.  
**Fix:** Dashboard now computes `qty × $160` as fallback when `Evolysse Total` is null/missing.  
**Status:** ✅ Fixed

---

### F-03 — Replace All checkbox hidden behind file input overlay
**Symptom:** User could not click the "Replace All Existing Data" checkbox on the upload panel.  
**Root cause:** `input[type=file]` had `position:absolute; inset:0` covering the entire slot div, including the checkbox.  
**Fix:** Moved checkbox HTML outside the slot div into a wrapper above it.  
**Status:** ✅ Fixed

---

### F-04 — Previously-skipped flags re-appearing after upload
**Symptom:** Items the user had manually marked "Skip" in the review queue re-appeared as pending after each upload.  
**Root cause:** Replace mode was force-setting all new flags to `pending`, overwriting `skipped` status from localStorage.  
**Fix:** Upload logic now checks existing flag status and preserves `skipped` entries.  
**Status:** ✅ Fixed

---

### F-05 — Admin page showing "0 total rows" for large vendor files
**Symptom:** Galderma transaction count showed 0 on the admin page; file had 12,000+ rows.  
**Root cause:** Admin page pre-loaded all transaction files at init via GitHub blob/CDN API. Files >1–2MB timed out or failed silently.  
**Fix:** Removed all pre-loading at init. Transaction files are now fetched fresh only at upload time via authenticated GitHub API (never CDN).  
**Status:** ✅ Fixed

---

### F-06 — Galderma upload: wrong header row count
**Symptom:** After uploading March 2026 Galderma file, column names were wrong — data values appeared as headers.  
**Root cause:** `vendor_config.json` had `skip_header_rows: 4` (matching old file format). New monthly exports have exactly 1 header row.  
**Fix:** Updated `vendor_config.json`: `skip_header_rows: 4 → 0`.  
**Status:** ✅ Fixed

---

### F-07 — Galderma CDN cache data loss (critical)
**Symptom:** After uploading March data, historical file shrank from 11,668 rows to 673. Over 11,000 rows appeared lost.  
**Root cause:** `fetchVendorHistory` was reading from `raw.githubusercontent.com` (GitHub CDN), which served a cached/stale version of the file — not the actual current content. Upload logic then merged new data against the stale read and saved, destroying history.  
**Fix:** `fetchVendorHistory` now always uses the authenticated GitHub Contents API (or blob API for large files). CDN is never used for reads.  
**History restored:** Full 11,668-row history was restored from git before the overwrite was confirmed gone.  
**Status:** ✅ Fixed

---

### F-08 — Galderma: SHIP TO leading zero MKID mismatch
**Symptom:** Good Juju (MKID `0100864632` in Omni) was showing as unmatched in the review queue despite correct data in Omni.  
**Root cause:** Galderma SHIP TO IDs sometimes have leading zeros. Code was stripping leading zeros from the CSV value but not from Omni `mk` field values during lookup — so `0100864632` in Omni never matched `100864632` from CSV.  
**Fix:** Leading zeros now stripped on **both sides** of the comparison during matching.  
**Status:** ✅ Fixed

---

### F-09 — Review queue resolutions not saving to backend (Galderma `id_not_in_omni`)
**Symptom:** User resolved 8 Galderma flags in the review queue. Transaction rows for those medspas never received `_moxie_id`, so dashboard showed no Galderma data for them.  
**Root cause:** `resolveFlag()` only triggered retroactive `_moxie_id` backfill for `name_not_in_map` type flags. `id_not_in_omni` flags (used by Galderma) were resolved in-memory but no backend update was made.  
**Fix:** `resolveFlag()` now handles both flag types. Backfill runs for both: fetches current transaction file, sets `_moxie_id` on all matching rows, pushes updated file to GitHub.  
**Backfill run:** All 12,345 Galderma rows were retroactively backfilled (10,543 auto by MKID, 74 fuzzy match, 8 manual, 4 ZZ-prefixed skips).  
**Status:** ✅ Fixed

---

### F-10 — Galderma: SHIP TONAME not shown in review queue
**Symptom:** Review queue showed raw SHIP TO numeric IDs (e.g., `100921909`) with no readable name, making it impossible to identify which medspa to map.  
**Root cause:** `csv_name_field` config key didn't exist. Flag creation only stored the numeric join field value.  
**Fix:** Added `csv_name_field: "SHIP TONAME"` to Galderma vendor config. Flag creation now reads that column and stores `ship_to_name`. Review queue UI displays the name with fuzzy-match suggestions.  
**Status:** ✅ Fixed

---

### F-11 — Barbarino Surgical Arts (Texas): no Galderma data on dashboard
**Symptom:** Barbarino Texas had transactions in the file and a name mapping, but dashboard showed $0 Galderma spend/savings.  
**Root cause:** `medspas.json` `mk` field for Barbarino Texas was `"100921909\u202c"` — an invisible unicode LEFT-TO-RIGHT EMBEDDING character was appended during a Python write operation. Dashboard string comparison failed silently.  
**Also found:** Barbarino Redondo Beach had `mk: "\u202d"` (unicode only, no actual ID) — cleaned to empty string.  
**Fix:** Regex-cleaned all `mk` values in `medspas.json` to strip non-ASCII characters. Committed and pushed 2026-04-01.  
**Status:** ✅ Fixed — pushed, deploy in ~2 min

---

## KNOWN LIMITATIONS (not bugs, by design)

### L-01 — Galderma dedup skips rows with no DELIVERY #
Rows missing a `DELIVERY #` value skip the dedup check entirely (controlled by `skip_dedup_when_empty`). This is intentional — those rows have no reliable dedup key. However it means duplicate uploads of such rows will result in duplicates. **User confirmed this logic is correct.**

### L-02 — 3 Galderma accounts intentionally unmatched
Analytica Medical Inc, Aria Winter Park, and Get Toxd were skipped by user in review queue. Their rows have no `_moxie_id` and will not appear on dashboard.

---

### F-12 — Large vendor file uploads failing with "error fetching existing data"
**Symptom:** Uploading any large vendor file (Revance, Galderma) showed "error fetching existing data — upload aborted".
**Root cause:** The blob API fetch used `Accept: application/vnd.github.v3.raw` with an Authorization header. Browsers enforce CORS rules and GitHub doesn't support CORS preflight for the blob raw endpoint. Python tests passed because Python has no CORS restrictions — this masked the bug.
**Fix:** Changed blob fetch to use standard JSON accept header and decode the base64 content manually. Browser-compatible, same result.
**Status:** ✅ Fixed

---

## OPEN / UNVERIFIED

### O-01 — Barbarino dashboard display (post-fix)
Resolved. Three fixes pushed 2026-04-01:
1. `medspas.json` unicode cleaned (root cause identification — but this file is not read by dashboard at runtime)
2. `dashboard.html` inline MEDSPAS array unicode cleaned (actual fix for mk matching)
3. `calcGalderma` now matches by `_moxie_id` first, mk/SHIP TO as fallback — same pattern as all other vendors

### O-02 — Merz monthly uploads
Merz has no recent upload history confirmed. Monthly data may be missing.

---

## SYSTEMIC RISKS TO WATCH

| Risk | Mitigation in place |
|------|-------------------|
| CDN cache serving stale data on reads | ✅ All reads now use authenticated GitHub API |
| Large file timeouts at page load | ✅ No pre-loading — fetch only at upload time |
| Review queue resolutions not persisting | ✅ Both flag types now backfill backend |
| Unicode/invisible characters in data fields | ⚠️ No automated sanitization at write time — manual cleanup needed if data is copy-pasted from Excel |
| MEDSPAS list hardcoded in dashboard.html | ⚠️ Any medspa property update (mk, al, etc) requires editing dashboard.html directly — medspas.json alone is not enough |
| Leading zeros stripped inconsistently | ✅ Both sides stripped during MKID lookup |
| Vendor config format changes (new file format) | ⚠️ Manual update to `vendor_config.json` required when vendor changes export format |
