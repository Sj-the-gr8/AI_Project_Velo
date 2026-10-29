CREATE TABLE IF NOT EXISTS subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    merchant_name TEXT NOT NULL,
    canonical_id TEXT NOT NULL UNIQUE,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    current_amount REAL NOT NULL CHECK (current_amount > 0),
    currency TEXT NOT NULL DEFAULT 'USD',
    cadence TEXT NOT NULL DEFAULT 'monthly',
    status TEXT NOT NULL DEFAULT 'active',
    sandbox TEXT
);

CREATE TABLE IF NOT EXISTS billing_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subscription_id INTEGER NOT NULL REFERENCES subscriptions(id),
    amount REAL NOT NULL CHECK (amount > 0),
    currency TEXT NOT NULL DEFAULT 'USD',
    message_id TEXT NOT NULL UNIQUE,
    received_date TEXT NOT NULL,
    billing_date TEXT NOT NULL,
    next_billing_date TEXT,
    raw_snippet TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subscription_id INTEGER NOT NULL REFERENCES subscriptions(id),
    reason TEXT NOT NULL CHECK (reason IN ('price_hike', 'dormant', 'trial_convert')),
    created_at TEXT NOT NULL,
    resolved INTEGER NOT NULL DEFAULT 0 CHECK (resolved IN (0, 1)),
    resolution TEXT,
    price REAL,
    UNIQUE(subscription_id, reason, created_at)
);

CREATE TABLE IF NOT EXISTS agent_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    step_number INTEGER NOT NULL,
    screenshot_path TEXT NOT NULL,
    chosen_element_label TEXT,
    action TEXT,
    reasoning_text TEXT NOT NULL,
    timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_runs (
    run_id TEXT PRIMARY KEY,
    scenario TEXT NOT NULL,
    subscription_id INTEGER REFERENCES subscriptions(id),
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    outcome TEXT NOT NULL,
    steps INTEGER NOT NULL,
    wrong_turns INTEGER NOT NULL DEFAULT 0,
    backtracks INTEGER NOT NULL DEFAULT 0,
    model_calls INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_billing_subscription_date ON billing_events(subscription_id, billing_date);
CREATE INDEX IF NOT EXISTS idx_alerts_open ON alerts(resolved, created_at);
