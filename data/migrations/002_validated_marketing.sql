-- Run this once in the Supabase SQL Editor for the MBR project.
--
-- Adds per-month agency-validated Enterprise marketing data (parsed from
-- the "Enterprise Reporting [Compiled]" workbook) to monthly assets.
-- {medspa_id: {practice_name, ad_spend, leads, booked, completed, revenue,
--  total_revenue_all_clients, first_visit_roi, lead_to_booking_rate,
--  first_visit_aov, campaigns: [...]}}

ALTER TABLE monthly_assets
    ADD COLUMN IF NOT EXISTS validated_marketing JSONB;
