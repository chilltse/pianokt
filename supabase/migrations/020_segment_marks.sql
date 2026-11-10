begin;
create table public.piano_segment_marks (
  user_id uuid not null references auth.users(id) on delete cascade,
  segment_id uuid not null references public.piano_song_segments(segment_id),
  created_at timestamptz not null default now(),
  primary key (user_id, segment_id)
);
alter table public.piano_segment_marks enable row level security;
create policy segment_marks_read on public.piano_segment_marks for select to authenticated using (user_id = auth.uid());
create policy segment_marks_insert on public.piano_segment_marks for insert to authenticated with check (user_id = auth.uid());
create policy segment_marks_delete on public.piano_segment_marks for delete to authenticated using (user_id = auth.uid());
revoke all on public.piano_segment_marks from anon, authenticated;
grant select, insert, delete on public.piano_segment_marks to authenticated;
commit;
