-- Harden get_leaderboard:
-- 1) Ensure ranking source columns exist on challenge_recordings
-- 2) Rank from recordings first, then left-join profiles
--    (so users without a profiles row still appear)

alter table public.challenge_recordings
  add column if not exists accuracy_pct numeric not null default 0;

alter table public.challenge_recordings
  add column if not exists difficulty numeric not null default 0;

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
      coalesce(round(avg(cr.accuracy_pct)::numeric, 2), 0) as accuracy_avg,
      coalesce(max(cr.difficulty), 0)::numeric as max_difficulty
    from public.challenge_recordings cr
    group by cr.user_id
  ),
  ordered as (
    select
      a.user_id,
      coalesce(
        nullif(p.display_name, ''),
        split_part(coalesce(p.email, ''), '@', 1),
        'Player'
      ) as display_name,
      p.avatar_url,
      a.challenge_count,
      a.accuracy_avg,
      a.max_difficulty,
      row_number() over (
        order by
          case sort_by
            when 'accuracy' then a.accuracy_avg
            when 'difficulty' then a.max_difficulty
            else a.challenge_count
          end desc nulls last
      ) as rn
    from agg a
    left join public.profiles p on p.id = a.user_id
  )
  select
    o.rn as rank,
    o.user_id,
    o.display_name,
    o.avatar_url,
    o.challenge_count,
    o.accuracy_avg,
    o.max_difficulty
  from ordered o
  order by o.rn
  limit 50;
end;
$$;

grant execute on function public.get_leaderboard(text) to anon;
grant execute on function public.get_leaderboard(text) to authenticated;
