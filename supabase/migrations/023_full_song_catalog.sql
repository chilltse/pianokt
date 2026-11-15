begin;
create table public.piano_song_full_midis (
 song_id uuid not null references public.piano_song_catalog(song_id),
 midi_sha256 text not null check(midi_sha256 ~ '^[a-f0-9]{64}$'),
 storage_path text not null check(storage_path='midi/' || midi_sha256 || '.mid'),
 duration_ms integer not null check(duration_ms>0),
 note_count integer not null check(note_count>0), track_count integer not null check(track_count>0),
 midi_metadata jsonb not null default '{}', license text not null, source_url text not null,
 created_at timestamptz not null default now(), primary key(song_id,midi_sha256)
);
create trigger piano_full_midi_immutable before update or delete on public.piano_song_full_midis
 for each row execute function public.piano_song_midi_immutable();
alter table public.piano_song_catalog add column active_full_midi_sha256 text;
alter table public.piano_song_catalog add constraint piano_song_active_full_midi
 foreign key(song_id,active_full_midi_sha256) references public.piano_song_full_midis(song_id,midi_sha256);
-- Archived segments retain their UUID, content and historical foreign keys.
alter table public.piano_song_segments drop constraint piano_song_segments_song_id_segment_index_key;
create unique index piano_active_segment_position on public.piano_song_segments(song_id,segment_index) where status<>'archived';
alter table public.piano_song_segments add column segmentation_identity text;
alter table public.piano_song_full_midis enable row level security;
create policy full_midi_public on public.piano_song_full_midis for select to anon,authenticated using(exists(
 select 1 from public.piano_song_catalog s where s.song_id=piano_song_full_midis.song_id and s.status='published' and s.active_full_midi_sha256=piano_song_full_midis.midi_sha256));
grant select on public.piano_song_full_midis to anon,authenticated;
create function public.piano_public_full_song_catalog() returns jsonb language sql stable security invoker set search_path='' as $$
 select coalesce(jsonb_agg(jsonb_build_object('song_id',s.song_id,'title',s.title,'composer',s.composer,'difficulty',s.difficulty,'midi_sha256',m.midi_sha256,'storage_path',m.storage_path,'duration_ms',m.duration_ms) order by s.composer,s.title,s.song_id),'[]'::jsonb)
 from public.piano_song_catalog s join public.piano_song_full_midis m on m.song_id=s.song_id and m.midi_sha256=s.active_full_midi_sha256 where s.status='published'
$$;
revoke all on function public.piano_public_full_song_catalog() from public;
grant execute on function public.piano_public_full_song_catalog() to anon,authenticated;
commit;
