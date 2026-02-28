# 用户注册与 Challenge 录音持久化

## 架构概览

- **认证**：Supabase Auth（邮箱+密码 注册/登录、Google OAuth）
- **用户信息**：`public.profiles` 表（与 `auth.users` 通过 trigger 同步，含 Google 头像/昵称/邮箱）
- **录音持久化**：MIDI 文件存 Supabase Storage，元数据存 `public.challenge_recordings` 表
- **用户隔离**：除排行榜外，所有数据按 `auth.uid()` 隔离；客户端不传 userId，服务端仅信任 JWT 中的身份。
- **账户相关路由**：`/account/:userId`、`/recordings/:userId`、`/challenge-songs/:userId` 与 Supabase 用户 id 对应；**最终校验使用 `getUser()`**（见 5.2），未登录重定向登录页，访问他人 userId 重定向到自己的路径。**排行榜**：所有用户可查看，数据与 Supabase 实时同步（见 5.3）。

## 1. Supabase 项目配置

1. 在 [supabase.com](https://supabase.com) 创建项目。
2. **Authentication → Providers**：启用 Email，启用 Google（需在 Google Cloud Console 配置 OAuth 客户端 ID/Secret，并在 Supabase 中填写回调 URL）。
3. **Project Settings → API**：复制 `Project URL` 和 `anon public` key，填入本地 `.env`（见下）。

### 1.1 登录白名单（可选）

仅允许「事先加入白名单」的邮箱或 Google 账号登录：在 **SQL Editor** 中执行 `supabase/migrations/002_auth_whitelist.sql`。执行后：

- **未添加任何邮箱**：所有人可正常登录（白名单未启用）。
- **已添加至少一个邮箱**：只有 `public.allowed_emails` 表中的邮箱可以登录；其他用户登录后会被立即登出并提示 "Your account is not on the access list. Contact the administrator."

**添加允许的邮箱**（在 SQL Editor 中执行）：

```sql
insert into public.allowed_emails (email) values ('user@example.com');
```

支持邮箱登录和 Google 登录；Google 账号以该账号的邮箱为准。可多次执行 `insert` 添加多个邮箱（已存在会报唯一约束，可改用 `on conflict (email) do nothing`）。

## 2. 环境变量

在项目根目录创建 `.env`（不要提交到 Git），参考 `.env.example`：

```bash
# Supabase（必填，否则注册/登录与云端录音不可用）
VITE_SUPABASE_URL=https://xxxx.supabase.co
VITE_SUPABASE_ANON_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```

## 3. 数据库与存储

在 Supabase **SQL Editor** 中执行以下 SQL。

### 3.1 用户资料表（与 auth.users 同步，含 Google 头像/昵称/邮箱）

**推荐**：在 SQL Editor 中依次执行：

1. **`supabase/migrations/003_profiles_sync_from_auth.sql`** — 创建 `profiles` 表与触发器（Google/邮箱登录自动创建或更新 profile）。
2. **`supabase/migrations/004_upsert_profile_from_auth_rpc.sql`** — 创建 RPC `upsert_profile_from_auth`；前端在每次登录通过白名单后会调用该 RPC，确保即使用户未由触发器写入，也会在 `profiles` 中有一行（**解决 Google 登录后 profiles 无记录的问题**）。
3. **已有用户补全**：若在加触发器之前就有用户登录过，需在 SQL Editor 中**执行一次** **`supabase/scripts/backfill_profiles_from_auth.sql`**，将 `auth.users` 中已有用户同步到 `profiles`。若提示读取 `auth.users` 有风险，以项目管理员身份确认后执行即可。

**确认触发器已创建**（可选）：在 SQL Editor 中执行  
`select trigger_name, event_object_schema, event_object_table from information_schema.triggers where event_object_table = 'users';`  
应能看到 `on_auth_user_created` 与 `on_auth_user_updated` 挂在 `auth.users` 上。

若需手动执行，可使用下方 SQL：

```sql
-- 用户公开资料（由 auth.users 同步，头像等存 Supabase 供排行榜等使用）
create table public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  email text,
  display_name text,
  avatar_url text,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

-- 新用户注册/登录（含 Google）时插入 profile，兼容 full_name/name/given_name、avatar_url/picture
create or replace function public.handle_new_user()
returns trigger as $$
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
$$ language plpgsql security definer;

create or replace trigger on_auth_user_created
  after insert on auth.users
  for each row execute procedure public.handle_new_user();

-- 登录后更新 profile（如 Google 头像/昵称变更时同步到 profiles）
create or replace function public.handle_user_updated()
returns trigger as $$
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
$$ language plpgsql security definer;

create or replace trigger on_auth_user_updated
  after update on auth.users
  for each row execute procedure public.handle_user_updated();

-- RLS：仅能读/写自己的 profile
alter table public.profiles enable row level security;

create policy "Users can view own profile"
  on public.profiles for select
  using (auth.uid() = id);

create policy "Users can update own profile"
  on public.profiles for update
  using (auth.uid() = id);
```

### 3.2 Challenge 录音元数据表

```sql
create table public.challenge_recordings (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles(id) on delete cascade,
  song_source text not null,
  song_id text not null,
  song_title text,
  duration_sec numeric(10,2) not null,
  midi_storage_path text not null,
  created_at timestamptz default now()
);

create index idx_challenge_recordings_user_created
  on public.challenge_recordings(user_id, created_at desc);

alter table public.challenge_recordings enable row level security;

create policy "Users can insert own recordings"
  on public.challenge_recordings for insert
  with check (auth.uid() = user_id);

create policy "Users can select own recordings"
  on public.challenge_recordings for select
  using (auth.uid() = user_id);

create policy "Users can delete own recordings"
  on public.challenge_recordings for delete
  using (auth.uid() = user_id);
```

**说明**：列表接口不传 `user_id`，仅依赖 RLS（`auth.uid()`）过滤，避免客户端篡改看到他人数据。

### 3.2b 写入录音 RPC（服务端用 auth.uid()，用户隔离）

客户端只传业务参数，不传 `user_id`；插入时由 RPC 使用 `auth.uid()`。

```sql
create or replace function public.save_challenge_recording(
  recording_id uuid,
  p_song_source text,
  p_song_id text,
  p_song_title text,
  p_duration_sec numeric,
  p_midi_storage_path text,
  p_accuracy_pct numeric default 0,
  p_difficulty numeric default 0
)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.challenge_recordings (
    id, user_id, song_source, song_id, song_title, duration_sec, midi_storage_path, accuracy_pct, difficulty
  )
  values (
    recording_id,
    auth.uid(),
    p_song_source,
    p_song_id,
    p_song_title,
    p_duration_sec,
    p_midi_storage_path,
    coalesce(p_accuracy_pct, 0),
    coalesce(p_difficulty, 0)
  );
end;
$$;

grant execute on function public.save_challenge_recording(uuid, text, text, text, numeric, text, numeric, numeric) to authenticated;
revoke execute on function public.save_challenge_recording(uuid, text, text, text, numeric, text, numeric, numeric) from anon;
```

### 3.3 Storage 桶（存 MIDI 文件）

在 Supabase **Storage** 中：

1. 新建 bucket：名称填 `challenge-recordings`，选择 **Private**，创建。
2. 进入该 bucket → **Policies** → **New policy**：
   - **Policy name**：e.g. `Users can manage own recordings`
   - **Allowed operation**：勾选 Insert, Select, Delete
   - **Target roles**：勾选 authenticated
   - **Policy definition** 使用 **Using expression**：
     - **Insert**：`(bucket_id = 'challenge-recordings') and (auth.uid()::text = (storage.foldername(name))[1])`
     - **Select**：同上
     - **Delete**：同上  
   （即只允许用户访问路径为 `{user_id}/xxx.mid` 的文件，且 `{user_id}` 必须等于当前用户 id。）

路径规范：`{user_id}/{recording_id}.mid`

### 3.3b Storage 桶：头像（avatars，可选）

用于账户页头像上传（选择文件或拖拽）。

1. 新建 bucket：名称填 `avatars`，选择 **Public**（头像需公开 URL 展示），创建。
2. 进入该 bucket → **Policies** → **New policy**：
   - **Policy name**：e.g. `Users can manage own avatar`
   - **Allowed operation**：Insert, Select, Update, Delete
   - **Target roles**：authenticated
   - **Policy definition**（Using expression）：
     - **Insert**：`(bucket_id = 'avatars') and (auth.uid()::text = (storage.foldername(name))[1])`
     - **Select**：同上
     - **Update**：同上
     - **Delete**：同上  

路径规范：`{user_id}/avatar.{jpg|png|webp|gif}`，单文件覆盖上传；前端限制 2MB，仅允许 JPEG/PNG/WebP/GIF。

**重要**：头像桶必须为 **Public**。若创建时选了 Private，`getPublicUrl` 返回的地址在浏览器中会返回 403，头像无法在页面和导航栏显示。请到 **Storage → avatars → Configuration** 中勾选 **Public bucket** 并保存。

**排行榜扩展（可选）**：为支持首页排行榜，在 **SQL Editor** 中执行：

```sql
-- 为 challenge_recordings 增加准确率与难度字段（已有表则执行）
alter table public.challenge_recordings
  add column if not exists accuracy_pct numeric(5,2) default 0,
  add column if not exists difficulty numeric(8,2) default 0;

-- 排行榜：公开只读函数，按维度降序返回 (display_name, avatar_url, 统计值)
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
language sql
security definer
set search_path = public
as $$
  with stats as (
    select
      cr.user_id,
      count(*)::bigint as challenge_count,
      coalesce(round(avg(cr.accuracy_pct)::numeric, 2), 0) as accuracy_avg,
      coalesce(max(cr.difficulty), 0)::numeric as max_difficulty
    from public.challenge_recordings cr
    group by cr.user_id
  ),
  joined as (
    select
      p.id as user_id,
      coalesce(p.display_name, split_part(p.email, '@', 1), 'Player') as display_name,
      p.avatar_url,
      s.challenge_count,
      s.accuracy_avg,
      s.max_difficulty
    from public.profiles p
    join stats s on s.user_id = p.id
  )
  select
    row_number() over (
      order by
        case when sort_by = 'accuracy' then j.accuracy_avg
             when sort_by = 'difficulty' then j.max_difficulty
             else j.challenge_count
        end desc nulls last
    ) as rank,
    j.user_id,
    j.display_name,
    j.avatar_url,
    j.challenge_count,
    j.accuracy_avg,
    j.max_difficulty
  from joined j
  limit 50;
$$;

-- 允许匿名调用（仅读排行榜）
grant execute on function public.get_leaderboard(text) to anon;
grant execute on function public.get_leaderboard(text) to authenticated;
```

### 3.4 Google 登录（可选）

在 **Authentication → Providers → Google** 中启用并填写 Client ID 与 Client Secret（从 [Google Cloud Console](https://console.cloud.google.com/) 的 OAuth 2.0 客户端获取）。  
在 **Authentication → URL Configuration** 中把 Site URL 和 Redirect URLs 设好（例如 `http://localhost:5173/**` 用于本地开发）。

## 4. 前端行为

- **未登录**：可正常使用 App；Challenge 结束后仅本地弹窗预览/下载，不写入云端。
- **已登录**：Challenge 结束后将 MIDI 上传至 Storage，并在 `challenge_recordings` 插入一条记录；「我的录音」页展示当前用户的录音列表，可播放/下载。
- **注册/登录**：`/register` 邮箱注册，`/login` 邮箱登录 + Google 登录；成功后跳转首页或原目标页。

## 5. 安全与最佳实践

### 5.1 用户隔离（最佳实践）

- **除排行榜外**，当前用户不能看到其他用户的任何数据。
- **不信任客户端身份**：列表录音不传 `user_id`，仅靠 RLS（`auth.uid()`）过滤；写入录音通过 RPC `save_challenge_recording`，服务端只用 `auth.uid()` 写入，客户端不传 `user_id`。
- **profiles**：RLS 仅允许读/写自己的行。
- **challenge_recordings**：RLS 仅允许插入/查询/删除自己的行；列表接口不传 userId，由 Supabase 根据 JWT 自动限定。
- **Storage**：策略限定只能访问路径前缀为 `auth.uid()/` 的文件；下载链接来自“仅当前用户”的录音列表，不会泄露他人。

### 5.2 受保护路由与 getUser() 最终校验

- **不依赖 getSession() 做放行判断**：受保护页面（账户、我的录音、挑战歌曲）使用 `useRequireAuth(pathKind)`，内部调用 `supabase.auth.getUser()` 作为唯一放行依据；未登录 → 重定向 `/login?redirect=当前路径`，URL 中 userId 与当前用户不一致 → 重定向到自己的路径。
- **数据隔离**：列表/写入均不传 userId，由 RLS（`auth.uid()`）与 RPC 内 `auth.uid()` 保证仅能读写本人数据。

### 5.3 排行榜与实时同步

- **所有人可看**：排行榜（RPC `get_leaderboard`）不区分用户，匿名与已登录均可查看。
- **实时同步**：前端订阅 Supabase Realtime 的 `challenge_recordings` 与 `profiles` 表变更；任意用户完成挑战或资料更新后，榜单自动刷新。需在 Supabase **Database → Replication** 中为 `challenge_recordings`、`profiles` 开启 realtime（加入 `supabase_realtime` publication）。

### 5.4 其他

- 使用 Supabase **anon** key 仅做前端；敏感操作由 RLS 与 Supabase Auth 保护。
- 密码由 Supabase Auth 处理（bcrypt），不落库到 `profiles`。
- Google 登录：头像、昵称、邮箱通过 `handle_new_user` / `handle_user_updated` 同步到 `profiles`，供排行榜等使用。
- 如需邮箱验证，在 Supabase Auth → Email Templates 中开启 “Confirm email”。
- 5.2、5.3 中的“受保护路由”与“排行榜”行为由前端实现；若启用 SSR，可在 loader 中同样用 `getUser()` 做服务端校验。
