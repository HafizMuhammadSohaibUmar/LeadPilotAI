-- 001_init.sql — Supabase (PostgreSQL + pgvector) schema for the voice agent.
-- Multi-tenant: every table carries business_id and every index includes it.

create extension if not exists vector;      -- pgvector, for future transcript embeddings
create extension if not exists pgcrypto;    -- gen_random_uuid()

-- ---------------------------------------------------------------------------
-- calls: one row per inbound phone call
-- ---------------------------------------------------------------------------
create table if not exists calls (
    id                text primary key,               -- Twilio CallSid
    business_id       text not null,
    caller_number     text not null,
    caller_name       text,
    service_type      text,
    urgency           text,
    address           text,
    zip_code          text,
    in_service_area   boolean,
    outcome           text not null default 'in_progress',
    transcript        text not null default '',
    fsm_lead_id       text,
    duration_seconds  integer not null default 0,
    llm_provider_used text,
    created_at        timestamptz not null default now()
);

create index if not exists idx_calls_business_created
    on calls (business_id, created_at desc);
create index if not exists idx_calls_business_caller
    on calls (business_id, caller_number);

-- ---------------------------------------------------------------------------
-- leads: qualified leads created by the agent
-- ---------------------------------------------------------------------------
create table if not exists leads (
    id           uuid primary key default gen_random_uuid(),
    business_id  text not null,
    call_id      text references calls (id),
    name         text,
    phone        text not null,
    service_type text,
    urgency      text,
    address      text,
    notes        text not null default '',
    fsm_system   text not null default 'generic',
    fsm_id       text,
    status       text not null default 'new',
    created_at   timestamptz not null default now()
);

create index if not exists idx_leads_business_created
    on leads (business_id, created_at desc);
create index if not exists idx_leads_business_phone
    on leads (business_id, phone);

-- ---------------------------------------------------------------------------
-- duplicate_check: fast dedup lookups (same phone within 60 minutes)
-- ---------------------------------------------------------------------------
create table if not exists duplicate_check (
    business_id text not null,
    phone       text not null,
    last_seen   timestamptz not null default now(),
    primary key (business_id, phone)                 -- enables upsert on conflict
);

create index if not exists idx_duplicate_check_last_seen
    on duplicate_check (business_id, last_seen desc);
