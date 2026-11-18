-- Add midi_keyboard_used column to challenge_recordings and update save_challenge_recording RPC

alter table public.challenge_recordings
add column if not exists midi_keyboard_used boolean not null default false;

create or replace function public.save_challenge_recording(
  recording_id uuid,
  p_song_source text,
  p_song_id text,
  p_song_title text,
  p_duration_sec numeric,
  p_midi_storage_path text,
  p_accuracy_pct numeric default 0,
  p_difficulty numeric default 0,
  p_midi_keyboard_used boolean default false
)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.challenge_recordings (
    id, user_id, song_source, song_id, song_title,
    duration_sec, midi_storage_path, accuracy_pct, difficulty, midi_keyboard_used
  ) values (
    recording_id, auth.uid(), p_song_source, p_song_id, p_song_title,
    p_duration_sec, p_midi_storage_path, p_accuracy_pct, p_difficulty, p_midi_keyboard_used
  );
end;
$$;

grant execute on function public.save_challenge_recording(uuid, text, text, text, numeric, text, numeric, numeric, boolean) to authenticated;
