ALTER TABLE sync_queue ADD COLUMN lease_owner TEXT;
CREATE INDEX idx_sync_claim ON sync_queue(claim_token);
