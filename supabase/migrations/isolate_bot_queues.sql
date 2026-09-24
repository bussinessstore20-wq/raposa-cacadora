-- Isolate Raposa Caçadora queues by bot while preserving legacy rows.
alter table public.produtos_fila
  add column if not exists bot_id text;

update public.produtos_fila
set bot_id = case
  when fila_origem = 'raposa-cacadora' then 'raposa-cacadora'
  when fila_origem = 'raposa-cacadora-bot' then 'raposa-cacadora-bot'
  else bot_id
end
where bot_id is null
  and fila_origem is not null;

create unique index if not exists produtos_fila_bot_id_link_key
  on public.produtos_fila (bot_id, link);

create index if not exists produtos_fila_bot_id_status_created_at_idx
  on public.produtos_fila (bot_id, status, created_at);

create index if not exists produtos_fila_bot_id_status_processing_idx
  on public.produtos_fila (bot_id, status, processing_at);

create or replace function public.sync_produtos_fila_bot_id()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
begin
  if new.bot_id is null and new.fila_origem is not null then
    new.bot_id := new.fila_origem;
  end if;
  return new;
end;
$$;

drop trigger if exists trg_sync_produtos_fila_bot_id on public.produtos_fila;

create trigger trg_sync_produtos_fila_bot_id
before insert on public.produtos_fila
for each row
execute function public.sync_produtos_fila_bot_id();

drop index if exists public.produtos_fila_link_unique;
