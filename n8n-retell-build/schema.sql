-- Separate from the custom-coded LeadPilot schema. Apply only to an approved Supabase project.
create table if not exists public.retell_call_logs (
  id bigint generated always as identity primary key,
  call_id text not null,
  call_status text,
  from_number text,
  to_number text,
  start_timestamp bigint,
  end_timestamp bigint,
  summary text,
  in_voicemail boolean not null default false,
  caller_name text,
  service_type text,
  address text,
  urgency text,
  call_outcome text,
  transcript text,
  outcome text not null,
  jobber_client_id text,
  jobber_request_id text,
  created_at timestamptz not null default now()
);

create index if not exists retell_call_logs_caller_time_idx
  on public.retell_call_logs (from_number, created_at desc);
create index if not exists retell_call_logs_call_id_idx
  on public.retell_call_logs (call_id);

create table if not exists public.retell_call_errors (
  id bigint generated always as identity primary key,
  workflow_name text,
  node_name text,
  error_message text,
  execution_id text,
  created_at timestamptz not null default now()
);

alter table public.retell_call_logs enable row level security;
alter table public.retell_call_errors enable row level security;
