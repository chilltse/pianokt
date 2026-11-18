begin;
-- Refuse reruns before deleting anything from an already-installed segment catalog.
do $$ begin
 if to_regclass('public.piano_song_midis') is not null then
 raise exception 'Segment catalog already installed; do not rerun this reset migration';
 end if;
end $$;
-- Catalog reset explicitly requested: no legacy filename identities or aliases.
-- This resets catalog data only; it does not delete practice/GCS objects.
drop view if exists public.piano_public_song_catalog;
alter table public.piano_song_catalog drop constraint if exists piano_catalog_active_version;
drop table if exists public.piano_song_versions;
drop table public.piano_song_catalog;

create table public.piano_song_catalog (
 song_id uuid primary key default gen_random_uuid(),
 slug text not null unique check(length(slug) between 1 and 200),
 title text not null check(length(title) between 1 and 300),
 composer text not null check(length(composer) between 1 and 300),
 difficulty double precision not null default 0.5 check(difficulty between 0 and 1),
 genres text[] not null default '{}',
 license text not null check(length(license)>0),
 source_url text not null check(source_url ~ '^https?://'),
 status text not null default 'draft' check(status in ('draft','published','archived')),
 created_at timestamptz not null default now(), updated_at timestamptz not null default now()
);
create table public.piano_song_segments (
 segment_id uuid primary key default gen_random_uuid(),
 song_id uuid not null references public.piano_song_catalog(song_id),
 segment_index integer not null check(segment_index>0),
 title text,
 status text not null default 'draft' check(status in ('draft','published','archived')),
 active_midi_sha256 text check(active_midi_sha256 ~ '^[a-f0-9]{64}$'),
 created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
 unique(song_id,segment_index), unique(song_id,segment_id)
);
create table public.piano_song_midis (
 song_id uuid not null,
 segment_id uuid not null,
 midi_sha256 text not null check(midi_sha256 ~ '^[a-f0-9]{64}$'),
 storage_path text not null check(storage_path='midi/' || midi_sha256 || '.mid'),
 duration_ms integer not null check(duration_ms>0),
 note_count integer not null check(note_count>0),
 track_count integer not null check(track_count>0),
 midi_metadata jsonb not null default '{}',
 license text not null, source_url text not null,
 created_at timestamptz not null default now(),
 primary key(segment_id,midi_sha256),
 foreign key(song_id,segment_id) references public.piano_song_segments(song_id,segment_id)
);
alter table public.piano_song_segments add constraint piano_segment_active_midi
 foreign key(segment_id,active_midi_sha256) references public.piano_song_midis(segment_id,midi_sha256);
create index piano_published_segments on public.piano_song_segments(song_id,segment_index) where status='published';
create or replace function public.piano_song_midi_immutable() returns trigger
 language plpgsql set search_path=public,pg_temp as $$ begin
 raise exception 'MIDI content records are immutable; import new bytes to create a new hash';
 end $$;
revoke all on function public.piano_song_midi_immutable() from public;
create trigger piano_song_midi_immutable before update or delete on public.piano_song_midis
 for each row execute function public.piano_song_midi_immutable();
create or replace function public.piano_catalog_updated_at() returns trigger
 language plpgsql set search_path=public,pg_temp as $$ begin new.updated_at=now(); return new; end $$;
revoke all on function public.piano_catalog_updated_at() from public;
create trigger piano_catalog_updated_at before update on public.piano_song_catalog
 for each row execute function public.piano_catalog_updated_at();
create trigger piano_segment_updated_at before update on public.piano_song_segments
 for each row execute function public.piano_catalog_updated_at();

alter table public.piano_song_catalog enable row level security;
alter table public.piano_song_segments enable row level security;
alter table public.piano_song_midis enable row level security;
revoke all on public.piano_song_catalog,public.piano_song_segments,public.piano_song_midis from anon,authenticated;
-- Owner view is the ONLY anonymous/client catalog read surface.
create view public.piano_public_song_catalog as
 select c.song_id,c.slug,c.title,c.composer,c.difficulty,c.genres,
 s.segment_id,s.segment_index,s.title as segment_title,
 v.midi_sha256,v.storage_path,
 v.duration_ms,v.note_count,v.track_count,v.midi_metadata,v.license,v.source_url
 from public.piano_song_catalog c
 join public.piano_song_segments s on s.song_id=c.song_id
 join public.piano_song_midis v on v.midi_sha256=s.active_midi_sha256 and v.segment_id=s.segment_id
 where c.status='published' and s.status='published';
grant select on public.piano_public_song_catalog to anon,authenticated;
-- Administrator import only; no client write policies on catalog or Storage.
grant all on public.piano_song_catalog,public.piano_song_segments,public.piano_song_midis to service_role;
do $$ begin
 if exists(select 1 from pg_roles where rolname='pianokt_backend') then
 grant select on public.piano_song_catalog,public.piano_song_segments,public.piano_song_midis,public.piano_public_song_catalog to pianokt_backend;
 create policy backend_catalog on public.piano_song_catalog for select to pianokt_backend using(true);
 create policy backend_segments on public.piano_song_segments for select to pianokt_backend using(true);
 create policy backend_midis_read on public.piano_song_midis for select to pianokt_backend using(true);
 end if;
end $$;
alter table public.piano_recommendation_feedback add column if not exists segment_id uuid;
insert into storage.buckets(id,name,public,file_size_limit,allowed_mime_types)
 values('piano-midi','piano-midi',true,10485760,array['audio/midi','audio/x-midi'])
 on conflict(id) do nothing;
commit;
