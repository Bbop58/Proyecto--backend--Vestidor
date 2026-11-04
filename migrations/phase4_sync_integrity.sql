-- Run on a backup/copy. Does not guess hashes/owners of historical operations.
BEGIN;
LOCK TABLE sync_operations IN SHARE ROW EXCLUSIVE MODE;
ALTER TABLE sync_operations ALTER COLUMN branch_id DROP NOT NULL;
ALTER TABLE sync_operations ADD COLUMN IF NOT EXISTS request_fingerprint VARCHAR(64);
ALTER TABLE sync_operations ADD COLUMN IF NOT EXISTS supersedes_operation_id UUID;
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conname='uq_sync_client_operation' AND conrelid='sync_operations'::regclass) THEN
  IF EXISTS(SELECT 1 FROM pg_indexes WHERE tablename='sync_operations' AND indexname='ix_sync_operations_client_operation_id') THEN
   ALTER TABLE sync_operations ADD CONSTRAINT uq_sync_client_operation UNIQUE USING INDEX ix_sync_operations_client_operation_id;
  ELSE
   ALTER TABLE sync_operations ADD CONSTRAINT uq_sync_client_operation UNIQUE(client_operation_id);
  END IF;
 END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conname='uq_sync_successor' AND conrelid='sync_operations'::regclass) THEN
  ALTER TABLE sync_operations ADD CONSTRAINT uq_sync_successor UNIQUE(supersedes_operation_id);
 END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conname='fk_sync_supersedes' AND conrelid='sync_operations'::regclass) THEN
  ALTER TABLE sync_operations ADD CONSTRAINT fk_sync_supersedes FOREIGN KEY(supersedes_operation_id) REFERENCES sync_operations(client_operation_id);
 END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conname='ck_sync_not_self' AND conrelid='sync_operations'::regclass) THEN
  ALTER TABLE sync_operations ADD CONSTRAINT ck_sync_not_self CHECK(supersedes_operation_id IS NULL OR supersedes_operation_id<>client_operation_id);
 END IF;
END $$;
COMMIT;
