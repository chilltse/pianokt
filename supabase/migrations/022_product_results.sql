begin;
-- Independent product lifecycle. No research-attempt FK or inferred legacy backfill.
create table public.challenge_results (
 attempt_id uuid primary key,
 user_id uuid not null references auth.users(id),
 source_environment text not null check(source_environment in ('production','development','test','staging')),
 payload jsonb not null,
 difficulty numeric,
 created_at timestamptz not null default now()
);
alter table public.challenge_results enable row level security;
create policy product_results_owner_read on public.challenge_results for select to authenticated using(auth.uid()=user_id);
grant select on public.challenge_results to authenticated;
revoke insert,update,delete on public.challenge_results from anon,authenticated;
create index challenge_results_owner on public.challenge_results(user_id,source_environment,attempt_id);
comment on table public.challenge_results is 'Client-measured product achievements; never KT labels. Writes through authenticated backend with immutable idempotency.';

-- Separate RPC keeps legacy leaderboard consumers unambiguous during rollout.
create function public.get_product_leaderboard(sort_by text default 'challenges')
returns table(rank bigint,user_id uuid,display_name text,avatar_url text,challenge_count bigint,succeeded_songs bigint,max_difficulty numeric)
language sql stable security definer set search_path='' as $$
 with counts as (
 select r.user_id,
 count(*) filter(where (payload->>'is_completed')::boolean) as challenge_count,
 count(distinct payload->>'segment_id') filter(where (payload->>'is_succeeded')::boolean) as succeeded_songs,
 max(difficulty) filter(where (payload->>'is_succeeded')::boolean) as max_difficulty
 from public.challenge_results r where source_environment='production' group by r.user_id
 ), ranked as (
 select row_number() over(order by case sort_by when 'succeeded' then c.succeeded_songs when 'difficulty' then c.max_difficulty else c.challenge_count end desc nulls last,c.user_id) as rank,c.* from counts c
 ) select r.rank,r.user_id,p.display_name,p.avatar_url,r.challenge_count,r.succeeded_songs,r.max_difficulty
 from ranked r left join public.profiles p on p.id=r.user_id order by r.rank
$$;
revoke all on function public.get_product_leaderboard(text) from public;
grant execute on function public.get_product_leaderboard(text) to anon,authenticated;

commit;
