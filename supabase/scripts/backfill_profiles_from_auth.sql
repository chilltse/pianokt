-- 一次性补全：把 auth.users 里已有用户同步到 public.profiles
-- 在 Supabase Dashboard → SQL Editor 中执行（仅执行一次）
-- 若提示读取 auth.users 有风险，以项目管理员身份确认后执行即可

insert into public.profiles (id, email, display_name, avatar_url, created_at, updated_at)
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
  coalesce(u.created_at, now()),
  now()
from auth.users u
on conflict (id) do update set
  email = coalesce(excluded.email, profiles.email),
  display_name = coalesce(excluded.display_name, profiles.display_name),
  avatar_url = coalesce(excluded.avatar_url, profiles.avatar_url),
  updated_at = now();
