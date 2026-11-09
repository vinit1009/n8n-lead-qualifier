-- Tables the workflows read and write. Loaded once into the Postgres container (docker-entrypoint-initdb.d).
CREATE TABLE leads (
  id          bigserial PRIMARY KEY,
  received_at timestamptz NOT NULL DEFAULT now(),
  email       text NOT NULL,
  name        text NOT NULL,
  company     text,
  message     text NOT NULL,
  source      text,
  fit_score   int CHECK (fit_score BETWEEN 0 AND 100),
  segment     text,
  reasons     jsonb,
  route       text NOT NULL CHECK (route IN ('hot', 'nurture', 'archive', 'review')),
  problems    jsonb,
  model       text
);
CREATE INDEX leads_email_recent ON leads (lower(email), received_at DESC);

-- Drafts written by the model wait here until a person approves them.
CREATE TABLE outbox (
  lead_id     bigint PRIMARY KEY REFERENCES leads (id),
  subject     text NOT NULL,
  body        text NOT NULL,
  status      text NOT NULL DEFAULT 'pending_approval' CHECK (status IN ('pending_approval', 'sent')),
  token       text NOT NULL DEFAULT replace(gen_random_uuid()::text, '-', ''),
  created_at  timestamptz NOT NULL DEFAULT now(),
  approved_at timestamptz
);

CREATE TABLE follow_ups (
  id      bigserial PRIMARY KEY,
  lead_id bigint NOT NULL REFERENCES leads (id),
  step    int NOT NULL,
  due_at  timestamptz NOT NULL,
  status  text NOT NULL DEFAULT 'scheduled'
);

-- Filled by the error workflow whenever a node fails after its retries.
CREATE TABLE errors (
  id           bigserial PRIMARY KEY,
  at           timestamptz NOT NULL DEFAULT now(),
  workflow     text,
  node         text,
  message      text,
  execution_id text
);
