-- No result backfill. Existing business history remains visible and explicitly legacy.
begin;
alter table public.piano_attempts add column data_architecture_version text not null default 'legacy';
alter table public.piano_attempts alter column data_architecture_version set default '3';
create table public.piano_alignment_runs (
 alignment_run_id text primary key,
 attempt_id uuid not null references public.piano_attempts(id) on delete cascade,
 learner_key uuid not null,
 song_id text not null, segment_id uuid, segment_index integer, midi_sha256 text,
 reference_hash text not null, performance_hash text not null, telemetry_hash text,
 result_key text not null unique, result_digest text not null,
 result_schema_version integer not null,
 alignment_version text not null, matcher_version text not null,
 reference_grouping_version text not null, kc_labeler_version text not null,
 config_digest text not null, source_environment text not null,
 quality_valid boolean not null, quality_reason text not null,
 processing_purpose text not null check(processing_purpose in ('online','research')),
 data_architecture_version text not null default '3' check(data_architecture_version='3'),
 summary jsonb not null,
 committed_at timestamptz not null default clock_timestamp(),
 unique(attempt_id,alignment_run_id),
 check(result_key='silver/alignment/' || alignment_run_id || '/result.json'),
 check(result_digest ~ '^[a-f0-9]{64}$')
);
create function public.piano_v3_run_input_check() returns trigger language plpgsql as $$
begin
 if not exists (select 1 from public.piano_attempts p where p.id=new.attempt_id
  and p.learner_key=new.learner_key and p.source_environment=new.source_environment
  and p.song_id=new.song_id and p.segment_id is not distinct from new.segment_id
  and p.segment_index is not distinct from new.segment_index and p.midi_sha256 is not distinct from new.midi_sha256
  and p.reference_hash=new.reference_hash and p.performance_hash=new.performance_hash
  and p.performance_events_hash is not distinct from new.telemetry_hash)
 then raise exception 'Run input identity mismatch'; end if;
 return new;
end $$;
create trigger piano_v3_run_input_check before insert on public.piano_alignment_runs for each row execute function public.piano_v3_run_input_check();
create index piano_alignment_runs_selection on public.piano_alignment_runs(source_environment,kc_labeler_version,alignment_version,committed_at);
create index piano_alignment_runs_attempt on public.piano_alignment_runs(attempt_id);
create function public.piano_v3_immutable_run() returns trigger language plpgsql as $$
begin raise exception 'Committed run is immutable'; end $$;
create trigger piano_v3_immutable_run before update on public.piano_alignment_runs for each row execute function public.piano_v3_immutable_run();
-- Conditional constraint preserves legacy pointers without registering them as V3.
create function public.piano_v3_pointer_check() returns trigger language plpgsql as $$
begin
 if new.data_architecture_version='3' and new.status='READY' and not exists (
  select 1 from public.piano_alignment_runs r where r.attempt_id=new.id
   and r.alignment_run_id=new.alignment_run_id and r.result_key=new.result_key
   and r.result_digest=new.result_digest and r.learner_key=new.learner_key
   and r.source_environment=new.source_environment and r.processing_purpose='online'
 ) then raise exception 'V3 READY requires matching committed online run'; end if;
 return new;
end $$;
create constraint trigger piano_v3_pointer_check after insert or update on public.piano_attempts
 deferrable initially deferred for each row execute function public.piano_v3_pointer_check();
-- Tombstones deliberately have no cascading FK: removing facts must not remove exclusions.
create table public.piano_data_exclusions (
 source_environment text not null,
 scope text not null check(scope in ('learner','attempt','song','segment','dataset')),
 identity text not null, reason text not null,
 created_at timestamptz not null default clock_timestamp(),
 primary key(source_environment,scope,identity)
);
alter table public.piano_alignment_runs enable row level security;
alter table public.piano_data_exclusions enable row level security;
revoke all on public.piano_alignment_runs,public.piano_data_exclusions from anon,authenticated;
do $$ begin
 if exists(select 1 from pg_roles where rolname='pianokt_backend') then
  grant select,insert on public.piano_alignment_runs to pianokt_backend;
  grant select on public.piano_data_exclusions to pianokt_backend;
  create policy backend_runs_read on public.piano_alignment_runs for select to pianokt_backend using(true);
  create policy backend_runs_insert on public.piano_alignment_runs for insert to pianokt_backend with check(true);
  create policy backend_exclusions_read on public.piano_data_exclusions for select to pianokt_backend using(true);
 end if;
end $$;
-- Browser roles have no registry or exclusion policies.
commit;
