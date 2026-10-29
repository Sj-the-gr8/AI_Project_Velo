# Velo

Velo is a local course project that finds recurring subscription charges in your email, flags the ones that deserve a second look, and shows how a browser agent could carry out a cancellation once you decide to cancel.

It has two phases:

1. **Detect and review.** Read billing emails from Gmail (read-only), extract structured charges with Gemini, store them in SQLite, run pure rule checks for alerts, and show everything in a Streamlit dashboard.
2. **Act (sandbox only).** When you click an alert notification, a Playwright + Gemini vision agent works through a cancellation flow on a local mock site, and every step is logged.

> **Status:** The full pipeline, from Gmail to cancellation, works end-to-end as a demo. Phase 2 runs only against the bundled mock site. See [Why this is a demo and not a deployed product](#why-this-is-a-demo-and-not-a-deployed-product) for the reason.

---

## Contents

- [Architecture](#architecture)
- [Project layout](#project-layout)
- [Requirements](#requirements)
- [Setup](#setup)
- [Phase 1: Gmail to review dashboard](#phase-1-gmail-to-review-dashboard)
- [Phase 2: Sandbox cancellation agent](#phase-2-sandbox-cancellation-agent)
- [Alert rules](#alert-rules)
- [Data model](#data-model)
- [Configuration](#configuration)
- [Tests](#tests)
- [Security model](#security-model)
- [Why this is a demo and not a deployed product](#why-this-is-a-demo-and-not-a-deployed-product)
- [Known limitations](#known-limitations)

---

## Architecture

```text
 Gmail (readonly)                                                  Local mock site
      │                                                                  ▲
      ▼                                                                  │ label-based clicks
 ingestion/gmail_client.py ──► ingestion/email_parser.py                 │ same-origin guard
      │  billing-like emails         plain text body                     │ max 15 steps
      ▼                                                                  │ backtracks on wrong turns
 extraction/regex_extractor.py (default) ──► extraction/schema.py       │
   or llm_extractor.py (Gemini, optional)    validated BillingEvent      │
      ▼                                                                  │
 db/db.py (SQLite) ──► rules/trigger_engine.py ──► notify/notifier.py ───┘
      │                  price_hike / dormant /       desktop notification
      │                  trial_convert                 (click = human handoff)
      ▼                                                      │
 dashboard/app.py (Streamlit, read-only)          agent/vision_agent.py
                                                  Set-of-Marks overlay + Gemini vision
                                                  every step ──► agent_actions table
```

`scripts/run_pipeline_once.py` runs all of Phase 1 once. Phase 2 starts only when a person clicks a notification, or when it is run by hand.

## Project layout

| Path | Purpose |
| --- | --- |
| `config.py` | Loads settings from `.env` into a frozen `Settings` dataclass |
| `ingestion/gmail_client.py` | OAuth (read-only scope), search query, pagination, incremental cursor |
| `ingestion/email_parser.py` | MIME walk, prefers `text/plain`, strips HTML as a fallback, skips attachments |
| `extraction/regex_extractor.py` | Rule-based extraction (no API calls): merchant, amount, currency, billing date, trial status |
| `extraction/llm_extractor.py` | Optional Gemini extraction with prompt-injection guard, JSON parse, validation |
| `extraction/extractor.py` | Picks the extractor from `EXTRACTION_MODE` (`regex`, `hybrid`, or `gemini`) |
| `extraction/schema.py` | Pydantic models `Merchant` and `BillingEvent` with bounds and currency normalisation |
| `db/schema.sql`, `db/db.py` | SQLite schema, migration, connection helper, agent action logging |
| `rules/trigger_engine.py` | Pure, side-effect-free alert rules |
| `notify/notifier.py` | Desktop notification whose click callback starts Phase 2 |
| `dashboard/app.py`, `dashboard/report.py` | Streamlit dashboard and the report calculations behind it |
| `agent/mock_site/app.py` | Flask sandbox: the classic flow plus four dark-pattern scenarios |
| `agent/som_overlay.js` | Set-of-Marks overlay that numbers visible interactive elements |
| `agent/vision_agent.py` | Playwright loop: overlay, screenshot, Gemini choice, act, log, and backtracking |
| `agent/sandbox.py` | Starts the local mock site for an agent run if it is not already running |
| `agent/action_log.py` | Writes each agent step to the `agent_actions` table |
| `scripts/run_pipeline_once.py` | End-to-end Phase 1 run |
| `scripts/watch_inbox.py` | Runs the pipeline every `POLL_INTERVAL_SECONDS` so new mail is processed as it arrives |
| `scripts/cancel_subscription.py` | Runs the cancellation agent for one open alert (what the dashboard's button runs) |
| `agent/run_lock.py` | Lets only one cancellation agent run at a time |
| `scripts/evaluate_agent.py` | Runs the real Gemini agent on every sandbox scenario and prints a scorecard |
| `scripts/send_test_notification.py` | Sends one sample alert notification with its buttons; pressing them only prints the choice |
| `rules/test_trigger_engine.py`, `tests/` | Test suite |

## Requirements

- Python 3.11+
- A Google Cloud project with the Gmail API enabled and an OAuth **Desktop app** client
- A Gemini API key for the cancellation agent (the free tier is enough; extraction needs none by default)
- Chromium for Playwright

## Setup

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

python -m pip install -r requirements.txt
python -m playwright install chromium

cp .env.example .env        # then set GEMINI_API_KEY
```

Download your OAuth client JSON from Google Cloud Console and save it as `client_secret.json` in the project root. It is git-ignored.

## Phase 1: Gmail to review dashboard

### Run the pipeline

```bash
python scripts/run_pipeline_once.py
```

The first run opens a browser for Google consent and saves `token.json`. The only scope requested is:

```text
https://www.googleapis.com/auth/gmail.readonly
```

What one run does:

1. **Fetch.** Searches Gmail for `receipt OR invoice OR subscription OR trial` after a start time. The first run looks back `GMAIL_LOOKBACK_DAYS` (default 90). Later runs continue from the Gmail `internalDate` of the last processed message, saved in `.velo_gmail_cursor`. Messages are processed oldest first, and the cursor advances only after each one is handled, so a crashed run picks up where it stopped.
2. **Parse.** Pulls a plain-text body out of each MIME message.
3. **Extract.** Reads the merchant, amount, currency, billing date, and trial status with regular expressions (see [Extraction](#extraction)). This makes no API calls. Anything it cannot read confidently is logged and skipped, never guessed.
4. **Store.** Upserts the subscription by `canonical_id` and inserts the billing event. `message_id` is unique, so re-running is idempotent.
5. **Evaluate.** After all messages are stored, rebuilds each affected subscription's full history and runs `evaluate_subscription` once per subscription.
6. **Notify.** Saves any new alert (skipping ones that already have an open alert with the same reason), then shows a desktop notification per alert and waits up to `NOTIFY_CLICK_TIMEOUT` seconds. Each notification shows the facts behind the alert (old and new price, yearly cost, months charged, next charge date) and has two buttons. **Cancel subscription** starts the mock site if it is not already running and runs Phase 2 against it. **Do nothing** closes the alert as *kept*: the subscription stays, and the same alert is not raised again at that price (a new price raises a fresh one). Clicking the body of the notification, or not answering, leaves the alert open on the dashboard.

### Keep it running on live mail

```bash
python scripts/watch_inbox.py
```

The watcher checks Gmail every `POLL_INTERVAL_SECONDS` (default 60) and runs the pipeline on whatever is new. New receipts are extracted, stored, and evaluated, and alerts are notified within about one interval of arriving. Temporary network, Gmail, or Gemini errors are logged and retried on the next check. While a notification is waiting for an answer, or the agent is running, checking pauses and then resumes. Stop it with Ctrl+C.

### View the dashboard

```bash
streamlit run dashboard/app.py --server.address 127.0.0.1
```

The `--server.address` flag keeps the dashboard on this machine. Streamlit binds to every network interface by default, which would expose your billing data to the local network.

The dashboard is read-only and shows everything Velo stores in SQLite. It re-reads the database every 30 seconds, so mail processed by the watcher appears without a manual refresh.

- **Summary:** monthly and projected yearly spend, active and cancelled subscriptions, open alerts, total paid to date, savings from cancellations, and price increases detected. Amounts in different currencies are never added together.
- **Overview:** upcoming charges in the next 30 days, expected charges whose date passed with no new receipt, monthly cost by subscription, total charged per month, and price per charge. The next charge is the date the latest receipt states (or a month after it), and is never projected past a missing receipt. The charts draw every amount in `DISPLAY_CURRENCY`, converted at the day's European Central Bank reference rates from frankfurter.dev (only currency codes are sent), cached for 6 hours, with built-in rates as an offline fallback; tables keep each charge in its own currency.
- **Subscriptions:** one row per subscription with first and current price, price change, number of charges, total paid, first and last charge, average days between charges, next expected charge, open alerts, and whether the agent cancelled it. Pick one to see each charge and the original email text.
- **Alerts:** open alerts with the same explanation the notification gave, each with a **Cancel subscription** button and a **Keep subscription** button (the dashboard's Do nothing), plus the full alert history showing each alert as open, kept, or cancelled. The button asks for confirmation, then runs the same agent as the notification's button in the background; a banner shows while it works. Only one cancellation runs at a time, whether it was started from a notification or the dashboard.
- **Billing history:** every recorded charge with its Gmail message id.
- **Cancellation agent:** every agent run with its scenario, outcome, steps, wrong turns, backtracks, and Gemini calls. Pick a run to see its step-by-step trace and the screenshot from each step.
- **Report:** a written summary of all of the above, downloadable as Markdown, plus CSV exports of subscriptions and charges.

### Extraction

`EXTRACTION_MODE` chooses how emails are read:

| Mode | What it does | Gemini calls |
| --- | --- | --- |
| `regex` (default) | Rule-based extraction in `extraction/regex_extractor.py` | None |
| `hybrid` | Regex first; Gemini only for emails regex could not read | Only for unreadable emails |
| `gemini` | Gemini for every email (the original behaviour) | One per email |

The regex extractor reads:

- **Amount and currency:** `$`, `€`, `£`, `₹`, `Rs.`, and ISO codes before or after the number (`USD 1,240.00`, `$9.99 USD`). It picks the amount in a sentence that describes a charge (*charged*, *paid*, *total*, *receipt*, *billed*, ...) and ignores offers, discounts, refunds, and future charges (*only*, *save*, *refund*, *will be charged*, ...).
- **Billing date:** `July 1, 2026`, `1 Jul 2026`, `2026-07-01`, and `01/07/2026` (read day-first unless the numbers make the order unambiguous). Dates after words like *next*, *renews*, or *until* become the next-billing estimate, not the charge date. With no date, it uses the date the email arrived, with lower confidence.
- **Merchant:** from the subject (`Acme Video receipt`, `Your receipt from Apple`), then the body (`Thanks for your Acme Video Premium subscription`), then the sender, unless the sender is a personal mailbox such as Gmail. Plan words (*Premium*, *Pro*, *Plan*, ...) are dropped, so every email from the same merchant maps to the same subscription.
- **Trial conversion:** *trial* together with *ended*, *expired*, or *converted*.

Newsletters, retention offers, refunds, and payment reminders are skipped.

### Test data

For the demo, a separate seeding script (not in this repository) uses `gmail.insert` to load synthetic receipts into a **dedicated test Gmail account**. Velo itself only ever reads.

## Phase 2: Sandbox cancellation agent

Start the mock site in one terminal:

```bash
python -m agent.mock_site.app
```

It serves five sandbox scenarios at `http://127.0.0.1:5000`. Each is built around a real dark pattern and has deliberate wrong turns:

| Scenario | URL | Dark pattern | Wrong turns the agent must recover from |
| --- | --- | --- | --- |
| `classic` | `/` | Small low-contrast cancel link, retention modal, confirmation page | "Keep my membership" |
| `maze` | `/s/maze/` | Roach motel: cancel is under Settings → Billing → Manage membership | Help article that only says "contact support", an upgrade offer, a Save button that does nothing |
| `trick` | `/s/trick/` | Confirmshaming ("No thanks, I prefer paying full price"), then a **Cancel** button that keeps the membership | The 50%-off trap, and the misleading Cancel that loops back to the start |
| `pause_trap` | `/s/pause_trap/` | Pause or downgrade offered instead of cancelling, then a survey that blocks Continue | "Pause for 3 months" and "Switch to Basic", which look finished but are not |
| `buried` | `/s/buried/` | Cancel link in the footer below long promotional content | Catalog pages that lead nowhere; Privacy and Terms pages with no links |

`AGENT_SCENARIO` chooses which scenario a **Cancel subscription** click opens. With the default, `auto`, each subscription gets its own sandbox when its first alert is raised, in turn: `classic`, `maze`, `trick`, `pause_trap`, `buried`, then the rotation repeats. The choice is stored with the subscription, so a second cancellation opens the same sandbox, and the dashboard shows it. Naming a scenario instead (for example `trick`) sends every cancellation there. Whichever sandbox opens, it shows the subscription being cancelled: its name, price, and currency, with the retention offers worked out from that price. To score the real Gemini agent on all of them:

```bash
python scripts/evaluate_agent.py            # or name some: python scripts/evaluate_agent.py maze trick
```

Run the agent in a second terminal (a notification click does both steps for you):

```bash
python -c "from agent.vision_agent import run_agent; print(run_agent())"
```

A headed Chromium window opens, and each step does the following:

1. Checks the page is still on the configured mock origin, and aborts if not.
2. **Perceive.** Injects `som_overlay.js`, which puts a numbered badge on every link, button, or input that is in the viewport and not covered by something else, and returns a registry mapping each label to a selector. Then it takes a screenshot.
3. **Reason.** Sends the screenshot, the goal, the labels it may choose (with their visible text), any labels it must avoid, and the history of earlier steps to Gemini vision. Gemini must answer `{"action": "click" | "dismiss_modal" | "go_back" | "scroll_down", "label": int, "reasoning": str}`, and the answer is validated against a schema.
4. **Act.** Clicks the element that the registry maps to that label, goes back a page, or scrolls.
5. **Detect wrong turns.** After each action, the agent checks the page's title and headings:
   - **Loop:** the click led back to a page earlier on the current path, like the trick scenario's misleading Cancel button.
   - **No-op:** the click changed nothing, like a Save button that does nothing.
   - **Dead end:** the page has nothing left to try. The agent scrolls if there is more page below, otherwise it goes back automatically, without a Gemini call.
   - **Trap:** a page that looks finished but is not, like "paused" or "discount applied". Gemini recognises these and chooses `go_back`.

   A move that turned out to be wrong is remembered for the page it was made from. On a later visit, that label is withheld from Gemini, and refused if Gemini picks it anyway (`repeat_wrong_turn`).
6. Logs every step (run id, step number, screenshot path, label, action, reasoning) to `agent_actions`. An invalid response, a label that is not on the page, or a click that fails is logged as a failed step (`invalid_response`, `failed_label`, `failed_click`), and perception runs again. Backtracking is logged as `go_back`, `auto_back`, or `auto_scroll`.
7. Ends early once the page says the membership is cancelled, or stops after `AGENT_MAX_STEPS` (default 15) in any case. A final row with action `success` or `stopped` records the outcome, and the run's totals go to `agent_runs`. If Gemini's daily quota runs out, the run stops cleanly; a per-minute rate limit is waited out and retried.

`run_agent()` returns the `run_id`. Screenshots go to `agent_screenshots/`. To audit a run:

```sql
SELECT step_number, chosen_element_label, action, reasoning_text, screenshot_path
FROM agent_actions WHERE run_id = '<run_id>' ORDER BY step_number;
```

## Alert rules

`rules/trigger_engine.py` is pure Python with no I/O. It returns at most one alert per subscription:

| Reason | Fires when |
| --- | --- |
| `trial_convert` | There is exactly one receipt, and its text mentions a trial together with *converted*, *ended*, *paid*, or *charge* |
| `price_hike` | The history is recurring and the latest amount is higher than the previous one by more than the tolerance |
| `dormant` | The history is recurring, has at least 6 cycles, and none of the last 6 has a positive usage signal |

Definitions:

- **Recurring**: at least 2 charges, 25 to 35 days apart, with earlier amounts steady.
- **Tolerance**: the larger of $0.01 and 1% of the smaller amount, so rounding and small FX drift do not count as a hike.
- **Usage signal**: optional. When no usage signal is available, cycle count is used as a conservative stand-in for dormancy.

Price decreases, non-monthly gaps, single ordinary receipts, and steady subscriptions produce no alert.

## Data model

| Table | Key columns |
| --- | --- |
| `subscriptions` | `canonical_id` (unique), `merchant_name`, `current_amount` (> 0), `currency`, `cadence`, `status`, `first_seen`, `last_seen`, `sandbox` |
| `billing_events` | `subscription_id` → `subscriptions`, `message_id` (unique), `amount` (> 0), `billing_date`, `next_billing_date`, `received_date`, `raw_snippet` |
| `alerts` | `subscription_id`, `reason` in (`price_hike`, `dormant`, `trial_convert`), `created_at`, `resolved`, `resolution` (`kept` or `cancelled`), `price` (when raised) |
| `agent_actions` | `run_id`, `step_number`, `screenshot_path`, `chosen_element_label`, `action`, `reasoning_text`, `timestamp` |
| `agent_runs` | `run_id`, `scenario`, `subscription_id`, `started_at`, `finished_at`, `outcome`, `steps`, `wrong_turns`, `backtracks`, `model_calls` |

Foreign keys are enforced (`PRAGMA foreign_keys = ON`). The schema is in `db/schema.sql` and is applied by `Database.migrate()`.

## Configuration

All settings come from `.env` (see `.env.example`):

| Variable | Default | Meaning |
| --- | --- | --- |
| `GMAIL_CREDENTIALS_FILE` | `client_secret.json` | OAuth client file |
| `GMAIL_TOKEN_FILE` | `token.json` | Saved user token |
| `GMAIL_CURSOR_FILE` | `.velo_gmail_cursor` | Incremental fetch cursor |
| `GMAIL_USER` | `me` | Gmail user id |
| `GEMINI_API_KEY` | *(empty)* | Required for the agent, and for extraction only in `hybrid` or `gemini` mode |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Gemini model for the agent and optional extraction |
| `EXTRACTION_MODE` | `regex` | `regex`, `hybrid`, or `gemini` (see [Extraction](#extraction)) |
| `DATABASE_PATH` | `velo.db` | SQLite file |
| `GMAIL_LOOKBACK_DAYS` | `90` | First-run lookback window |
| `MOCK_SITE_URL` | `http://127.0.0.1:5000` | The only origin the agent may visit |
| `AGENT_MAX_STEPS` | `15` | Hard cap on agent steps, including backtracks |
| `DISPLAY_CURRENCY` | `USD` | Currency the dashboard's charts are drawn in, for example `INR` |
| `AGENT_SCENARIO` | `auto` | Sandbox a Cancel subscription click opens: `auto` (each alerted subscription gets the next one in turn), or always `classic`, `maze`, `trick`, `pause_trap`, or `buried` |
| `AGENT_SCREENSHOT_DIR` | `agent_screenshots` | Where step screenshots are saved |
| `NOTIFY_CLICK_TIMEOUT` | `120` | Seconds the pipeline waits for a notification click |
| `POLL_INTERVAL_SECONDS` | `60` | How often `watch_inbox.py` checks Gmail |

## Tests

```bash
pytest -q
```

There are 120 tests, and all pass. They cover:

- Every alert rule, including boundaries: tolerance, cadence, unordered history, precedence, and trial wording
- MIME parsing: plain-text preference, HTML fallback, attachments, raw Gmail resources
- Regex extraction on receipts in several formats and currencies, skipped non-charges (newsletters, offers, refunds, reminders), and the extraction modes, including that `regex` never calls Gemini
- Gemini extraction validation: bad JSON, blocked responses, out-of-range amounts, blank merchants, bad dates and currencies, all logged with the message id
- The Gmail client against a fake service: readonly scope, pagination, oldest-first ordering, and the cursor
- The watcher retrying transient errors and stopping on unexpected ones
- The pipeline on synthetic mail: stored rows, cursor advance, idempotent re-runs, out-of-order mail, and the notification-to-agent handoff
- Notifications: alert details, and the agent starting only on **Cancel subscription**, never on **Do nothing**, a body click, or a timeout
- Schema migration, including adding the `action` column to older databases
- The agent end-to-end in headless Chromium against the mock site, using a scripted model: recovery from invalid output and unknown labels, a completed cancellation, and the step cap
- Every dark-pattern scenario, with a stand-in model that deliberately takes wrong turns: the agent backs out of traps, loops, no-op buttons, and dead ends, refuses to repeat a known wrong turn, and still cancels
- The dashboard's Cancel subscription button (confirm, then launch), the one-at-a-time lock, and the background cancel script
- Dashboard report calculations: price history, totals per currency, savings, next-charge dates (stated, upcoming, or late), currency conversion with its offline fallback, and the written report

The Gemini calls in `extract_billing_event` and `_ask_vision` accept an injected `client`, so they can be tested with a stub instead of the live API.

## Security model

- **Least privilege.** Gmail access is `gmail.readonly`. Velo cannot send, delete, or change mail.
- **Secrets stay local.** `.env`, `client_secret.json`, `token.json`, `*.db`, the cursor file, and `agent_screenshots/` are all git-ignored.
- **Untrusted input.** Email bodies and web pages are treated as data. Both prompts tell the model to ignore instructions in the content, and all model output is validated before use.
- **Human in the loop.** The agent never runs on its own. It starts only from a notification click or a manual command.
- **Contained agent.** A fresh browser context with no saved credentials, a same-origin check before every step, a 15-step cap, and only four actions: clicking a visible numbered element, getting past a modal, going back, or scrolling. There is no typing and no free-form navigation.
- **Full audit trail.** Every agent decision is saved with its screenshot and the model's reasoning.

These controls reduce the blast radius but do **not** fully solve indirect prompt injection. On-page text can still try to steer a vision model, which is why the local-only boundary and the audit log matter.

## Why this is a demo and not a deployed product

The design of Velo is correct and can be implemented. Detection, validation, rules, storage, notification, human handoff, and a step-limited agent with an audit trail are the same building blocks a production version would use. The demo runs that whole pipeline end-to-end: real Gmail ingestion, real Gemini extraction, real alerts, and a real vision agent completing a cancellation flow with realistic dark patterns.

What cannot be done legitimately, and especially not on free tooling, is pointing that agent at real subscription sites:

- **Terms of service.** Most streaming, SaaS, and telecom providers forbid automated access to account pages. Scripting their cancellation flows would break those terms whatever the intent.
- **Anti-automation defences.** Real sites use CAPTCHAs, bot detection, device fingerprinting, and step-up authentication (OTP, 2FA) specifically to stop scripted account actions. Getting past them means working around the site's own security controls, which this project will not do.
- **Credentials and liability.** Acting on a real account means holding the user's password or session. Storing and using those safely needs security, legal, and compliance work that a free, local course project cannot provide.
- **Free-tier limits.** Free Gemini quotas, a personal OAuth client in Google's "testing" mode (limited to listed test users, with no verified production scope approval), and a local SQLite database are fine for a demo but not for serving real users.

**If a real version of this is worth pursuing later, the legitimate path is official APIs or partnership access, not automating around a site's own defences.** Some card issuers already offer features that cancel or block a subscription on the cardholder's behalf. Merchant billing APIs, open-banking data, and card-network tools are how a deployed version would take action safely. In that design, Velo's Phase 1 (detect, explain, and alert) stays exactly as it is. Only the action step in Phase 2 would change, from a browser agent to an authorised API call.

So the mock site is not a shortcut. It is the correct boundary for this project. It proves the agent logic and the safety controls without touching any system Velo is not authorised to automate.

## Known limitations

- Only monthly cadence is detected. Annual and weekly plans are not flagged as recurring.
- Usage signals are not ingested yet, so dormancy uses cycle count as a stand-in.
- The Gmail search is keyword-based and may miss receipts with unusual wording, or fetch non-billing emails (which extraction then rejects).
- Regex extraction only recognises the receipt wordings and formats listed under [Extraction](#extraction). An unusual receipt is skipped rather than misread; `hybrid` mode sends those to Gemini.
- Gemini's element selection comes from a model and can be wrong. Validation, skipping, and logging limit the impact but cannot remove it.
- The dashboard is read-only. Alerts cannot be resolved from the UI yet.
- No production cancellation integration is included or supported.
#   A I _ P r o j e c t _ V e l o  
 