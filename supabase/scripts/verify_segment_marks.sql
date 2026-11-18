-- Read-only: run in the Supabase SQL editor or with psql -f.
select c.oid::regclass as table_name, c.relrowsecurity as rls_enabled,
       pg_get_userbyid(c.relowner) as owner
from pg_class c where c.oid = to_regclass('public.piano_segment_marks');
select column_name, data_type, is_nullable, column_default
from information_schema.columns where table_schema='public' and table_name='piano_segment_marks'
order by ordinal_position;
select conname, pg_get_constraintdef(oid) as definition
from pg_constraint where conrelid=to_regclass('public.piano_segment_marks');
select grantee, privilege_type from information_schema.role_table_grants
where table_schema='public' and table_name='piano_segment_marks' order by grantee, privilege_type;
select policyname, roles, cmd, qual, with_check from pg_policies
where schemaname='public' and tablename='piano_segment_marks';
-- Expected: authenticated SELECT/INSERT/DELETE=true; UPDATE=false; anon all=false.
select role_name, privilege,
       has_table_privilege(role_name, 'public.piano_segment_marks', privilege) as allowed
from (values ('anon'), ('authenticated')) roles(role_name)
cross join (values ('SELECT'), ('INSERT'), ('DELETE'), ('UPDATE')) permissions(privilege);
