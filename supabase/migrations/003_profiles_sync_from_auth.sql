-- Google / 邮箱登录时自动在 public.profiles 创建或更新账号
-- Run in Supabase: SQL Editor → New query → paste → Run
--
-- 说明：SECURITY DEFINER 为 Supabase 官方推荐写法，用于在 auth 触发器内写入 public 表；
-- 本脚本仅创建表与触发器，不包含任意用户输入，仅由数据库在 insert/update auth.users 时自动执行。

-- 1) 用户资料表（与 auth.users 同步）
create table if not exists public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  email text,
  display_name text,
  avatar_url text,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

-- 2) 新用户（含 Google 首次登录）→ 插入 profile（仅由触发器调用，不使用用户输入）
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.profiles (id, email, display_name, avatar_url)
  values (
    new.id,
    coalesce(new.raw_user_meta_data->>'email', new.email),
    coalesce(
      new.raw_user_meta_data->>'full_name',
      new.raw_user_meta_data->>'name',
      new.raw_user_meta_data->>'given_name',
      split_part(coalesce(new.raw_user_meta_data->>'email', new.email), '@', 1)
    ),
    coalesce(
      new.raw_user_meta_data->>'avatar_url',
      new.raw_user_meta_data->>'picture'
    )
  );
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute procedure public.handle_new_user();

-- 3) auth.users 更新（如 Google 头像/昵称变更）→ 更新 profile（仅由触发器调用）
create or replace function public.handle_user_updated()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  update public.profiles
  set
    email = coalesce(new.raw_user_meta_data->>'email', new.email, email),
    display_name = coalesce(
      new.raw_user_meta_data->>'full_name',
      new.raw_user_meta_data->>'name',
      new.raw_user_meta_data->>'given_name',
      display_name
    ),
    avatar_url = coalesce(
      new.raw_user_meta_data->>'avatar_url',
      new.raw_user_meta_data->>'picture',
      avatar_url
    ),
    updated_at = now()
  where id = new.id;
  return new;
end;
$$;

drop trigger if exists on_auth_user_updated on auth.users;
create trigger on_auth_user_updated
  after update on auth.users
  for each row execute procedure public.handle_user_updated();

-- 4) RLS
alter table public.profiles enable row level security;

drop policy if exists "Users can view own profile" on public.profiles;
create policy "Users can view own profile"
  on public.profiles for select using (auth.uid() = id);

drop policy if exists "Users can update own profile" on public.profiles;
create policy "Users can update own profile"
  on public.profiles for update using (auth.uid() = id);

-- 允许插入自己的 profile（触发器以 definer 执行时通常以 owner 身份绕过 RLS；此策略供部分环境或后续扩展）
drop policy if exists "Users can insert own profile" on public.profiles;
create policy "Users can insert own profile"
  on public.profiles for insert with check (auth.uid() = id);

-- 5) 【可选】为已有 auth 用户补全 profile（仅执行一次，且需在 Dashboard 以项目身份运行）
-- 若有“潜在风险”提示，多因此处读取 auth.users；此为一次性数据同步，仅管理员在 SQL Editor 执行。
-- 若跳过本段，新用户仍会通过上面触发器自动写入；仅“之前已登录但尚无 profile”的用户需补跑本段。
/*
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
*/
