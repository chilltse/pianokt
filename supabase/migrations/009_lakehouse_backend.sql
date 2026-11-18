begin;
create table public.piano_attempts (
 id uuid primary key, user_id uuid not null references auth.users(id) on delete cascade,
 learner_key uuid not null, song_id text not null, song_source text not null, song_title text,
 metadata jsonb not null default '{}', performance_key text not null, reference_key text not null,
 performance_hash text not null, reference_hash text not null,
 status text not null default 'CREATED' check(status in ('CREATED','UPLOADED','PROCESSING','READY','FAILED')),
 result_key text, alignment_run_id text, summary jsonb, error_code text, lease_until timestamptz,
 created_at timestamptz not null default now(), updated_at timestamptz not null default now()
);
create index piano_attempts_user on public.piano_attempts(user_id,created_at desc);
create table public.piano_learner_keys (
 user_id uuid primary key references auth.users(id) on delete cascade,
 learner_key uuid not null unique default gen_random_uuid()
);
create table public.piano_outbox (
 event_id uuid primary key default gen_random_uuid(), event_type text not null, aggregate_id text not null,
 payload jsonb not null, published_at timestamptz, created_at timestamptz not null default now(),
 unique(event_type,aggregate_id)
);
create index piano_outbox_pending on public.piano_outbox(created_at) where published_at is null;
create table public.piano_recommendations (
 id uuid primary key default gen_random_uuid(), user_id uuid not null references auth.users(id) on delete cascade,
 attempt_id uuid references public.piano_attempts(id) on delete cascade, model_version text not null,
 response jsonb not null, created_at timestamptz not null default now()
);
create table public.piano_recommendation_feedback (
 event_id uuid primary key, recommendation_id uuid not null references public.piano_recommendations(id) on delete cascade,
 user_id uuid not null references auth.users(id) on delete cascade, song_id text not null,
 event_type text not null check(event_type in ('impression','click')), created_at timestamptz not null default now()
);
create table public.piano_song_catalog (
 song_id text primary key, title text not null, difficulty double precision not null check(difficulty between 0 and 1),
 genres text[] not null default '{}', enabled boolean not null default true
);
create table public.piano_preferences (
 user_id uuid primary key references auth.users(id) on delete cascade, genres text[] not null default '{}'
);
alter table public.piano_attempts enable row level security;
alter table public.piano_learner_keys enable row level security;
alter table public.piano_outbox enable row level security;
alter table public.piano_recommendations enable row level security;
alter table public.piano_recommendation_feedback enable row level security;
alter table public.piano_song_catalog enable row level security;
alter table public.piano_preferences enable row level security;
create policy attempts_read on public.piano_attempts for select to authenticated using(user_id=auth.uid());
create policy recommendations_read on public.piano_recommendations for select to authenticated using(user_id=auth.uid());
create policy catalog_read on public.piano_song_catalog for select to authenticated using(enabled);
create policy preferences_own on public.piano_preferences for all to authenticated using(user_id=auth.uid()) with check(user_id=auth.uid());
revoke all on public.piano_outbox,public.piano_learner_keys,public.piano_recommendation_feedback from anon,authenticated;
grant select on public.piano_attempts,public.piano_recommendations,public.piano_song_catalog to authenticated;
grant select,insert,update on public.piano_preferences to authenticated;
create function public.piano_emit_play_event() returns trigger language plpgsql security definer set search_path=public,pg_temp as $$
declare learner uuid;
begin
 insert into public.piano_learner_keys(user_id) values(new.user_id) on conflict do nothing;
 select learner_key into learner from public.piano_learner_keys where user_id=new.user_id;
 insert into public.piano_outbox(event_id,event_type,aggregate_id,payload)
 values(new.event_id,'practice.event',new.event_id::text,
 jsonb_build_object('event_id',new.event_id,'event_type','practice.event','learner_key',learner,
 'session_id',new.session_id,'song_id',new.song_id,'action',new.event_type,'play_mode',new.play_mode,
 'song_time_sec',new.song_time_sec,'client_ts',new.client_ts,'received_at',new.created_at)) on conflict do nothing;
 return new;
end $$;
revoke all on function public.piano_emit_play_event() from public;
create trigger piano_play_outbox after insert on public.play_events_raw for each row execute function public.piano_emit_play_event();
commit;
