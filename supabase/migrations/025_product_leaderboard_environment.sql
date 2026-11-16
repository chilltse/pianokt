-- Existing single-argument RPC remains production-scoped for compatibility.
begin;
create function public.get_product_leaderboard(sort_by text, target_environment text)
returns table(rank bigint,user_id uuid,display_name text,avatar_url text,challenge_count bigint,succeeded_songs bigint,max_difficulty numeric)
language sql stable security definer set search_path='' as $$
 with counts as (
 select r.user_id,
 count(*) filter(where (payload->>'is_completed')::boolean) as challenge_count,
 count(distinct payload->>'segment_id') filter(where (payload->>'is_succeeded')::boolean) as succeeded_songs,
 max(difficulty) filter(where (payload->>'is_succeeded')::boolean) as max_difficulty
 from public.challenge_results r where source_environment=target_environment group by r.user_id
 ), ranked as (
 select row_number() over(order by case sort_by when 'succeeded' then c.succeeded_songs when 'difficulty' then c.max_difficulty else c.challenge_count end desc nulls last,c.user_id) as rank,c.* from counts c
 ) select r.rank,r.user_id,p.display_name,p.avatar_url,r.challenge_count,r.succeeded_songs,r.max_difficulty
 from ranked r left join public.profiles p on p.id=r.user_id order by r.rank
$$;
revoke all on function public.get_product_leaderboard(text,text) from public;
grant execute on function public.get_product_leaderboard(text,text) to anon,authenticated;

commit;
