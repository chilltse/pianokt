-- Coordinated API/Worker/frontend upgrade required. No GCS mutations.
begin;
alter table public.piano_attempts drop constraint piano_attempts_status_check;
alter table public.piano_attempts add constraint piano_attempts_status_check check(status in
 ('CREATED','ACCEPTED','VALIDATING','UPLOADED','PROCESSING','RETRY','INVALID','READY','FAILED'));
alter table public.piano_attempts
 add column accepted_at timestamptz,
 add column validated_at timestamptz,
 add column file_generations jsonb,
 add column processing_attempts integer not null default 0,
 add column next_retry_at timestamptz,
 add column validation_error text;
-- Previously finalized rows already passed synchronous file validation.
update public.piano_attempts set accepted_at=created_at,validated_at=updated_at
 where finalization is not null and status<>'CREATED';
create index piano_attempts_recovery on public.piano_attempts(status,next_retry_at,lease_until);
-- Accepted-but-unverified recordings must not contribute to public statistics.
create or replace function public.get_leaderboard(sort_by text default 'challenges')
returns table (
  rank bigint,
  user_id uuid,
  display_name text,
  avatar_url text,
  challenge_count bigint,
  accuracy_avg numeric,
  max_difficulty numeric
)
language plpgsql
security definer
set search_path = public
as $$
begin
  return query
  with agg as (
    select
      cr.user_id,
      count(*)::bigint as challenge_count,
      avg(cr.accuracy_pct) filter (where cr.score_source = 'aligned') as accuracy_avg,
      coalesce(max(cr.difficulty), 0)::numeric as max_difficulty
    from public.challenge_recordings cr
    where cr.source_environment='production' and exists (select 1 from public.piano_attempts pa where pa.id=cr.id and pa.validated_at is not null)
    group by cr.user_id
  ),
  ordered as (
    select
      a.user_id,
      p.display_name,
      p.avatar_url,
      a.challenge_count,
      a.accuracy_avg,
      a.max_difficulty,
      row_number() over (order by
        case sort_by
          when 'accuracy' then a.accuracy_avg
          when 'difficulty' then a.max_difficulty
          else a.challenge_count
        end desc nulls last
      ) as rn
    from agg a
    left join public.profiles p on p.id = a.user_id
  )
  select o.rn as rank, o.user_id, o.display_name, o.avatar_url, o.challenge_count, o.accuracy_avg, o.max_difficulty
  from ordered o
  order by o.rn;
end;
$$;

grant execute on function public.get_leaderboard(text) to anon;
grant execute on function public.get_leaderboard(text) to authenticated;

commit;
