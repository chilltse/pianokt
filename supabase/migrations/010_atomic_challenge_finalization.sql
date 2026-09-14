-- Allow the API role to commit a validated challenge cycle in the same
-- transaction as piano_attempts and challenge_recordings.
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'pianokt_backend') then
    grant select, insert on public.play_events_raw to pianokt_backend;
    grant select, insert, update on public.user_play_logs to pianokt_backend;

    drop policy if exists backend_play_events on public.play_events_raw;
    create policy backend_play_events on public.play_events_raw
      to pianokt_backend using (true) with check (true);

    drop policy if exists backend_play_logs on public.user_play_logs;
    create policy backend_play_logs on public.user_play_logs
      to pianokt_backend using (true) with check (true);
  end if;
end $$;
