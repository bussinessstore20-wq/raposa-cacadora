-- Fase 2: fundação de identidade e tenant.
-- Aditiva: não altera as tabelas operacionais existentes.

create table if not exists public.organizations (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  slug text not null unique,
  status text not null default 'active'
    check (status in ('active', 'suspended', 'archived')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.saas_users (
  id uuid primary key default gen_random_uuid(),
  auth_user_id uuid unique references auth.users(id) on delete set null,
  telegram_user_id bigint unique,
  username text,
  first_name text,
  last_name text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint saas_users_identity_check
    check (auth_user_id is not null or telegram_user_id is not null)
);

create table if not exists public.organization_members (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id) on delete cascade,
  user_id uuid not null references public.saas_users(id) on delete cascade,
  role text not null default 'member'
    check (role in ('owner', 'admin', 'member')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (organization_id, user_id)
);

create index if not exists organization_members_user_id_idx
  on public.organization_members (user_id);

create index if not exists organization_members_organization_id_role_idx
  on public.organization_members (organization_id, role);

alter table public.organizations enable row level security;
alter table public.saas_users enable row level security;
alter table public.organization_members enable row level security;

revoke all on public.organizations from anon, authenticated;
revoke all on public.saas_users from anon, authenticated;
revoke all on public.organization_members from anon, authenticated;

insert into public.organizations (name, slug, status)
values ('Raposa Caçadora — Organização Legada', 'raposa-cacadora-legado', 'active')
on conflict (slug) do nothing;
