-- Transactional integration check. Requires an administrator connection, at least
-- two existing auth users, and one segment unmarked by both. No fixtures persist.
begin;
set local lock_timeout = '5s';
set local statement_timeout = '15s';
do $$
declare users uuid[]; segment uuid;
begin
  select array_agg(id) into users from (select id from auth.users order by id limit 2) u;
  if coalesce(array_length(users,1),0) < 2 then raise exception 'Need two auth users'; end if;
  select s.segment_id into segment from public.piano_song_segments s
    where not exists (select 1 from public.piano_segment_marks m where m.segment_id=s.segment_id and m.user_id=any(users)) limit 1;
  if segment is null then raise exception 'Need an unmarked segment'; end if;
  perform set_config('test.mark_user_a', users[1]::text, true);
  perform set_config('test.mark_user_b', users[2]::text, true);
  perform set_config('test.mark_segment', segment::text, true);
  if not (select relrowsecurity from pg_class where oid='public.piano_segment_marks'::regclass) then raise exception 'RLS disabled'; end if;
  if has_table_privilege('anon','public.piano_segment_marks','SELECT,INSERT,UPDATE,DELETE') then raise exception 'Anonymous access allowed'; end if;
  if has_table_privilege('authenticated','public.piano_segment_marks','UPDATE') then raise exception 'UPDATE allowed'; end if;
end $$;
set local role authenticated;
select set_config('request.jwt.claim.sub', current_setting('test.mark_user_a'), true) is not null as user_a_claim_set;
insert into public.piano_segment_marks(user_id,segment_id)
values(current_setting('test.mark_user_a')::uuid,current_setting('test.mark_segment')::uuid) on conflict do nothing;
insert into public.piano_segment_marks(user_id,segment_id)
values(current_setting('test.mark_user_a')::uuid,current_setting('test.mark_segment')::uuid) on conflict do nothing;
do $$ begin
 if (select count(*) from public.piano_segment_marks where segment_id=current_setting('test.mark_segment')::uuid) <> 1 then raise exception 'Own/idempotent insert failed'; end if;
end $$;
select set_config('request.jwt.claim.sub', current_setting('test.mark_user_b'), true) is not null as user_b_claim_set;
do $$ declare affected integer; begin
 if exists(select 1 from public.piano_segment_marks where user_id=current_setting('test.mark_user_a')::uuid) then raise exception 'Other user rows exposed'; end if;
 delete from public.piano_segment_marks where user_id=current_setting('test.mark_user_a')::uuid and segment_id=current_setting('test.mark_segment')::uuid;
 get diagnostics affected = row_count;
 if affected <> 0 then raise exception 'Other user deletion allowed'; end if;
 begin
  insert into public.piano_segment_marks(user_id,segment_id) values(current_setting('test.mark_user_a')::uuid,current_setting('test.mark_segment')::uuid);
  raise exception 'Other user insertion allowed';
 exception when insufficient_privilege then null;
 end;
end $$;
insert into public.piano_segment_marks(user_id,segment_id)
values(current_setting('test.mark_user_b')::uuid,current_setting('test.mark_segment')::uuid);
delete from public.piano_segment_marks where user_id=current_setting('test.mark_user_b')::uuid and segment_id=current_setting('test.mark_segment')::uuid;
select set_config('request.jwt.claim.sub', current_setting('test.mark_user_a'), true) is not null as user_a_claim_restored;
do $$ begin
 if (select count(*) from public.piano_segment_marks where segment_id=current_setting('test.mark_segment')::uuid) <> 1 then raise exception 'Other user altered own mark'; end if;
end $$;
delete from public.piano_segment_marks where user_id=current_setting('test.mark_user_a')::uuid and segment_id=current_setting('test.mark_segment')::uuid;
do $$ begin
 if exists(select 1 from public.piano_segment_marks where segment_id=current_setting('test.mark_segment')::uuid) then raise exception 'Own delete failed'; end if;
end $$;
rollback;
