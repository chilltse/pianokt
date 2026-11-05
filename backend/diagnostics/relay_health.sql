-- Read-only preflight/monitoring. Run manually against the intended environment.
-- No payload, tokens, user/Attempt IDs or connection information are selected.
BEGIN READ ONLY;
SET LOCAL statement_timeout = '10s';

-- Require PRIMARY KEY(event_id) and UNIQUE(event_type,aggregate_id).
SELECT conname, pg_get_constraintdef(oid) AS definition
FROM pg_constraint WHERE conrelid='public.piano_outbox'::regclass;

-- Check migration 017 fields and the status constraint before any rollout.
SELECT column_name FROM information_schema.columns
WHERE table_schema='public' AND table_name='piano_attempts'
  AND column_name IN ('accepted_at','validated_at','file_generations',
      'processing_attempts','next_retry_at','validation_error','lease_token','lease_until')
ORDER BY column_name;
SELECT pg_get_constraintdef(oid) AS definition FROM pg_constraint
WHERE conrelid='public.piano_attempts'::regclass AND conname='piano_attempts_status_check';

SELECT event_type, count(*) AS pending,
       max(clock_timestamp()-created_at) AS oldest_pending_age
FROM public.piano_outbox WHERE published_at IS NULL GROUP BY event_type;

SELECT count(*) FILTER (WHERE status='RETRY' AND next_retry_at<=now()) AS due_retries,
       count(*) FILTER (WHERE status IN ('VALIDATING','PROCESSING') AND lease_until<now()) AS expired_leases,
       count(*) FILTER (WHERE status IN ('ACCEPTED','UPLOADED') AND updated_at<now()-interval '2 minutes') AS unclaimed,
       count(*) FILTER (WHERE status='FAILED') AS failed
FROM public.piano_attempts;

SELECT count(*) AS accepted_without_outbox FROM public.piano_attempts a
WHERE a.status='ACCEPTED' AND NOT EXISTS (
    SELECT 1 FROM public.piano_outbox o
    WHERE o.aggregate_id=a.id::text AND o.event_type='performance.submitted'
);
COMMIT;
