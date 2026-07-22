-- Remove legacy/duplicated fields from user_play_logs.
-- Keep this migration idempotent to support mixed environments.

alter table public.user_play_logs
  drop column if exists song_source,
  drop column if exists song_tittle,
  drop column if exists song_title,
  drop column if exists duration_sec,
  drop column if exists midi_storage_path,
  drop column if exists accuracy_pct,
  drop column if exists difficulty;
