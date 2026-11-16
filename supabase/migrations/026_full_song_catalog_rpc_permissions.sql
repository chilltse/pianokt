-- Match the existing public catalog read boundary: expose only published,
-- active resources through the fixed, schema-qualified RPC query.
begin;
alter function public.piano_public_full_song_catalog() security definer;
revoke all on function public.piano_public_full_song_catalog() from public;
grant execute on function public.piano_public_full_song_catalog() to anon,authenticated;
notify pgrst, 'reload schema';
commit;
