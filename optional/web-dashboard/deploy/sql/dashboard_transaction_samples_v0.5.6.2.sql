-- BF4 Status Web Dashboard v0.5.6.2: sampled PostgreSQL transaction telemetry.
BEGIN;

CREATE TABLE IF NOT EXISTS dashboard_transaction_samples (
    sampled_at timestamptz PRIMARY KEY,
    transactions bigint NOT NULL CHECK (transactions >= 0)
);

GRANT SELECT ON dashboard_transaction_samples TO bf4_dashboard_readonly;
GRANT SELECT, INSERT, UPDATE ON dashboard_transaction_samples TO bf4_dashboard_sampler;

COMMIT;
