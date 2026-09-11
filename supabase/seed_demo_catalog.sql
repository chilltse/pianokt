-- Optional demo-only difficulty labels, NOT calibrated ability estimates.
insert into public.piano_song_catalog(song_id,title,difficulty,genres) values
 ('ode-to-joy.mid','Ode to Joy',0.25,array['classical']),
 ('gymnopedie-no1.mid','Gymnopedie No. 1',0.5,array['classical']),
 ('canon-in-d.mid','Canon in D',0.7,array['classical'])
on conflict(song_id) do nothing;
