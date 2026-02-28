-- Auth whitelist: only these emails can log in (email or Google).
-- Run in Supabase SQL Editor. Then add emails: insert into public.allowed_emails (email) values ('user@example.com');

-- Table: allowed emails (no RLS select so client cannot read the list)
create table if not exists public.allowed_emails (
  email text primary key
);

alter table public.allowed_emails enable row level security;

-- No select/insert/update/delete for anon/authenticated: only service role / dashboard can manage.
-- RPC will read as definer.

-- RPC: is the current user's email in the whitelist? (If whitelist is empty, allow everyone.)
create or replace function public.check_user_allowed()
returns boolean
language plpgsql
security definer
set search_path = public, auth
as $$
declare
  user_email text;
  whitelist_count int;
begin
  select coalesce(u.email, u.raw_user_meta_data->>'email')
    into user_email
  from auth.users u
  where u.id = auth.uid();

  if user_email is null then
    return false;
  end if;

  select count(*) into whitelist_count from public.allowed_emails;

  if whitelist_count = 0 then
    return true;
  end if;

  return exists (
    select 1 from public.allowed_emails a
    where lower(trim(a.email)) = lower(trim(user_email))
  );
end;
$$;

grant execute on function public.check_user_allowed() to authenticated;
grant execute on function public.check_user_allowed() to anon;
