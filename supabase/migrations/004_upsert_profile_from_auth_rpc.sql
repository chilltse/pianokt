-- 登录后若 profiles 无当前用户（例如触发器未执行），可由前端调用此 RPC 补写/更新 profile
-- 使用 auth.uid()，仅操作当前用户一行

create or replace function public.upsert_profile_from_auth()
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.profiles (id, email, display_name, avatar_url, updated_at)
  select
    u.id,
    coalesce(u.raw_user_meta_data->>'email', u.email),
    coalesce(
      u.raw_user_meta_data->>'full_name',
      u.raw_user_meta_data->>'name',
      u.raw_user_meta_data->>'given_name',
      split_part(coalesce(u.raw_user_meta_data->>'email', u.email), '@', 1)
    ),
    coalesce(u.raw_user_meta_data->>'avatar_url', u.raw_user_meta_data->>'picture'),
    now()
  from auth.users u
  where u.id = auth.uid()
  on conflict (id) do update set
    email = coalesce(excluded.email, profiles.email),
    display_name = coalesce(excluded.display_name, profiles.display_name),
    avatar_url = coalesce(excluded.avatar_url, profiles.avatar_url),
    updated_at = now();
end;
$$;
