## Omni API Integration Spec — MBR Automation

### Connection
- **Base URL:** `https://moxie.omniapp.co/api`
- **Auth:** Bearer token (`Authorization: Bearer <OMNI_API_KEY>`)

### Dashboards
We pull from 3 Omni dashboards:

| Dashboard | ID | Purpose |
|---|---|---|
| Main KPIs | `bfd963dd` | Revenue, appointments, AOV, utilization, memberships, services (35 queries) |
| Staff Performance | `955002e5` | Employee Sales Metrics, Rebooking Rate |
| Retention | `59ca3051` | 180-day repeat appointment retention |

### How Queries Work
1. **Fetch query definitions:** `GET /v1/documents/{dashboard_id}/queries` → returns all saved query objects
2. **Execute a query:** `POST /v1/query/run` with `{"query": <query_body>}` — we inject filters into the saved query before running
3. **Response format:** NDJSON stream → look for `status: "COMPLETE"` line → decode `result` field as base64 → Arrow IPC → pyarrow table → Python dict

### Filters

**Practice filter** (all queries):
```json
"dbt__moxie_medspas_mart.medspa_name": {
  "kind": "EQUALS",
  "type": "string",
  "values": ["<practice_name>"],
  "is_negative": false
}
```
- Practice names have **no city suffix** (e.g. "The Vanity Bar" not "The Vanity Bar - West Fargo")

**Date filter** (critical — `BETWEEN` is silently ignored by Omni):
```json
"<date_field>": {
  "kind": "TIME_FOR_INTERVAL_DURATION",
  "type": "date",
  "ui_type": "PAST",
  "left_side": "YYYY-MM-01",
  "right_side": "1 months",
  "is_negative": false
}
```

**Date fields per topic:**

| Topic | Date Field | Used By |
|---|---|---|
| Invoices | `dbt__moxie_invoices_mart.invoice_issued_date` | Net Revenue, Paid Appointments, AOV, Client Counts, Membership Revenue, Gross Revenue Breakdown, Retail to Service, Service Mix |
| Utilization | `dbt__moxie_utilization_daily_mart.series_date` | Utilization |
| Appointments | `dbt__moxie_appointments_mart.start_time` | Rebooking Rate |
| Memberships (new) | `dbt__moxie_client_memberships_mart.started_at` | New Memberships |
| Memberships (churned) | `dbt__moxie_client_memberships_mart.ended_at` | Churned Memberships |
| Memberships (active) | **None** — point-in-time count, no date filter needed | Active Members |

**Retention dashboard** uses a different filter:
```json
"dbt__moxie_medspas_mart.medspa_name_with_id": {
  "kind": "CONTAINS",
  "type": "string",
  "values": ["<practice_name>"]
}
```

### Queries & Metrics Pulled

**From Main Dashboard (`bfd963dd`):**
- **KPI: Net Revenue** → `net_revenue_sum`, `revenue_goal_sum` → monthly net revenue, % of goal
- **KPI: Paid Appointments** → `paid_appointments` → total appointments
- **KPI: AOV** → `aov`, `aov_goal` → average order value, % of goal
- **Utilization** → `column_b_divided_by_column_a` (or `total_appointment_hours / total_available_hours`) → practice-level utilization rate
- **Client Counts** → `count_new_client`, `count_existing_client`
- **Active Members** → member count (no date filter)
- **New Memberships** → count + `mrr_sum` (no date filter)
- **Churned Memberships** → cancelled count (no date filter)
- **Total Membership Revenue** → `subtotal__membership_sum`
- **Gross Revenue Breakdown Summary** → service, retail, package, custom item, fees
- **Retail to Service Revenue** → `calc_1` ratio
- **Gross Revenue By Official Service Type** → service name, gross revenue, % of total

**From Staff Dashboard (`955002e5`):**
- **Employee Sales Metrics** → per-provider: name, net revenue, AOV, retail revenue
- **Rebooking Rate** → per-provider rebooking rate

**Per-Provider Utilization** (from main dashboard):
- We re-run the Utilization query with an added dimension: `dbt__moxie_utilization_daily_mart.provider_name` to get per-staff utilization. Rates are capped at 1.0 (overbooking shows as 100%).

**From Retention Dashboard (`59ca3051`):**
- First query → `pct_has_repeat_completed_appointments_180d`

### Practice List
- Query: `Medspa Name` from main dashboard with `limit: 50000`
- Returns `medspa_name` + `medspa_id`, filtered to exclude `(DEACTIVATED...)` entries
- Limit needs to stay high as Moxie adds new practices over time (currently 1156+)

### Known Gotchas
1. **`BETWEEN` date filters are silently ignored** — always use `TIME_FOR_INTERVAL_DURATION`
2. **Membership date fields**: New Memberships uses `started_at`, Churned uses `ended_at`. Active Members has no date filter (point-in-time count).
3. **Utilization rates can exceed 1.0** (overbooking) — we cap at 1.0
4. **Retention dashboard uses `medspa_name_with_id`** not `medspa_name` — must use `CONTAINS`
