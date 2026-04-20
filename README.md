# 🏢 Axiom Outbound: Complete System Framework v4

---

## ⚙️ Phase 0: System Bootstrap & Configuration

Before any pipeline execution, the runtime initializes all credentials, model configs, and persistent state.

- **Environment Config** — `.env` manages all credentials: SMTP, Twilio, Ollama endpoint, OpenClaw session params, Coolify API key, VPS SSH credentials, and future voice API keys. Never hardcoded.
- **Schema Initialization** — On first run, the runtime auto-creates the SQLite database using the schema defined in Phase 11. All tables are created with `IF NOT EXISTS` guards so re-runs are safe.
- **Deduplication Registry** — Checks `leads` and `clients` tables on startup. Previously contacted, active, or delivered clients are never re-entered into the acquisition pipeline.
- **Channel & Infrastructure Availability Check** — Validates SMTP credentials, Twilio credentials, Ollama endpoint reachability, and Coolify server reachability before execution begins. Hard fails loudly on misconfiguration. No silent degradation mid-run.
- **⚠️ Weakpoint Patched** — No startup health check for hosting infrastructure in earlier versions. A broken Coolify connection discovered mid-build after a client has already been onboarded is a trust-destroying failure state. This check prevents that entirely.

---

## 📡 Phase 1: Data Acquisition & Enrichment *(Powered by OpenClaw)*

Targets the "invisible market" — businesses with physical footprints but missing or fragmented digital presences.

- **Initial Extraction** — Overpass API queries OpenStreetMap data, filtering for local businesses missing the `website` tag.
- **Deep Web Verification (OpenClaw)** — Navigates search engines and directory listings dynamically to confirm whether the business genuinely lacks web infrastructure, or operates solely through a Facebook page, Yelp profile, or Google Business listing. This distinction is critical — the Strategist frames pitches differently for each scenario.
- **Contact Vector Extraction** — OpenClaw simultaneously scrapes for all available contact points: phone numbers, emails, owner names, physical address, and social profile URLs.
- **Data Sanitization** — A pre-processing script deduplicates, normalizes formatting, and outputs schema-validated JSON ready for agent ingestion.
- **⚠️ Weakpoint Patched** — OpenClaw is the single most likely point of external failure. Search engines actively block scraping bots. Mitigation: rotating user agents, randomized request delays (2–8 seconds), and a configurable fallback to cached directory data (Yelp API, YellowPages API) when OpenClaw receives repeated 429/503 responses. Every OpenClaw failure is logged with HTTP status code and timestamp — never silently swallowed.

---

## 🧠 Phase 2: Local Inference Engine

All intelligence runs on local compute for data privacy, zero API costs, and low-latency handoffs.

- **Backend** — Ollama serves as the inference engine.
- **Primary Model** — `lfm2` handles the Organizer and Researcher agents (speed-priority tasks with structured output).
- **Secondary Model** — `deepseek-coder-v2` handles the Developer Agent (code generation quality takes priority). `llama3.1:8b` is the recommended alternative for the Closer if `lfm2` copy quality proves inconsistent. Model selection is configured per-agent in `config.json`, not hardcoded.
- **⚠️ Weakpoint Patched** — Earlier versions used a single model for all agents. The Closer and Developer Agent require meaningfully stronger reasoning than the Organizer. Model routing per agent is now a first-class system concern.
- **System Runtime** (`main.py` / `exec.py`) — Orchestrates all agent handoffs, owns the context window, manages model routing, and handles retry logic. This is infrastructure, not an agent.
- **Schema Middleware** — A validation layer sits between every agent handoff. If a model outputs malformed JSON, the middleware intercepts it, logs the failure with full context, and issues an automatic retry prompt. After 3 consecutive failures, the lead is skipped and logged as `SCHEMA_FAILURE`. Execution continues.

### 📋 Inter-Agent JSON Contracts *(Placeholder — Legal Review Pending)*

> **Note:** Full JSON contract schemas will be finalized with legal aid prior to production deployment. These contracts govern data passing between agents and may carry obligations around data retention, PII handling, and business contact consent depending on jurisdiction. The placeholder structure below defines the intended fields only — field-level validation rules, required vs. optional designations, and PII handling annotations are deferred.

**Contract 0 → 1: Data Acquisition Output → Organizer Input**
```json
{
  "contract_id": "C0-1",
  "status": "PLACEHOLDER",
  "fields": [
    "osm_id", "business_name", "category", "address", "city",
    "state", "zip", "coordinates", "phone", "email",
    "social_profiles", "has_website", "website_url", "data_source"
  ],
  "pii_fields": ["phone", "email"],
  "legal_review_required": true
}
```

**Contract 1 → 2: Organizer Output → Researcher Input**
```json
{
  "contract_id": "C1-2",
  "status": "PLACEHOLDER",
  "fields": [
    "lead_id", "business_name", "industry", "address", "contact_vectors",
    "unroutable_flag", "duplicate_flag"
  ],
  "pii_fields": ["contact_vectors"],
  "legal_review_required": true
}
```

**Contract 2 → 3: Researcher Output → Strategist Input**
```json
{
  "contract_id": "C2-3",
  "status": "PLACEHOLDER",
  "fields": [
    "lead_id", "owner_name", "local_news_mentions", "competitor_sites",
    "review_sentiment", "revenue_tier", "enrichment_sources"
  ],
  "pii_fields": ["owner_name"],
  "legal_review_required": true
}
```

**Contract 3 → 4: Strategist Output → Closer Input**
```json
{
  "contract_id": "C3-4",
  "status": "PLACEHOLDER",
  "fields": [
    "lead_id", "value_proposition", "viability_score", "revenue_tier",
    "hook_angle", "discard_flag", "discard_reason"
  ],
  "pii_fields": [],
  "legal_review_required": false
}
```

**Contract 4 → Delivery: Closer Output → Delivery Engine Input**
```json
{
  "contract_id": "C4-D",
  "status": "PLACEHOLDER",
  "fields": [
    "lead_id", "email_copy", "sms_copy", "call_script",
    "channel_priority", "revenue_tier", "viability_score"
  ],
  "pii_fields": [],
  "legal_review_required": true
}
```

**Contract Brief → Developer: Requirements Parser Output → Developer Agent Input**
```json
{
  "contract_id": "CB-DEV",
  "status": "PLACEHOLDER",
  "fields": [
    "client_id", "project_id", "business_name", "tagline", "industry",
    "services", "target_audience", "colors", "reference_sites",
    "uploaded_assets", "cta_type", "preferred_domain", "revenue_tier"
  ],
  "pii_fields": ["uploaded_assets"],
  "legal_review_required": true
}
```

---

## 🤖 Phase 3: The 4-Agent Outreach Loop

Agents operate in a strict sequential pipeline. Each agent receives a validated JSON payload and returns one. All handoffs pass through Schema Middleware.

**Agent 1 — The Organizer**
Ingests sanitized JSON from Phase 1. Maps the full business profile: industry category, location, and all known contact vectors. Cross-references the `clients` table to catch leads that are already paying clients from a different acquisition channel. Flags leads with zero email and zero SMS contact as `UNROUTABLE` — discarded before consuming further compute.

**Agent 2 — The Researcher**
Triggers targeted OpenClaw deep-dives against the Organizer's profile. Looks for: business owner name, local news mentions, active competitor websites in the same zip code with web presence, review count and sentiment, and estimated revenue tier (inferred from review volume, photos, hours, and category). Builds a localized context payload for the Strategist. Revenue tier directly informs how the pitch is priced and framed.

**Agent 3 — The Strategist**
Analyzes the Researcher's payload using microeconomic framing: lost Google Maps search visibility, competitor web presence on the same street, inability to accept online bookings or reservations, friction in customer contact. Generates a value proposition specific to this business's gap. Assigns a **Viability Score (1–10)** and a **Revenue Tier** (Budget / Mid-Market / Premium). Leads scoring below 4 (configurable) are discarded with reason logged. Premium-tier leads are flagged for priority queue handling.

**Agent 4 — The Closer**
Translates the Strategist's payload into channel-specific outreach copy: a primary email, an SMS variant (under 160 characters, high-urgency), and a cold call talk-track script stored for future voice use. Tone is professional, hyper-personalized, and action-oriented. Every output variant is tagged with `lead_id`, `channel`, and `revenue_tier` for the Delivery Engine.

---

## 🛡️ Phase 4: Integrity & Validation

- **JSON Schema Enforcement** — Schema Middleware handles validation at every agent boundary. Not an agent responsibility.
- **Terminal Sandboxing** — All OpenClaw executions and script calls run in isolated containers. No process has host filesystem access or unrestricted network access outside its defined scope.
- **Viability Score Gateway** — Hard gate before the Closer runs. Sub-threshold leads written to `leads` table as `DISCARDED` with reason and timestamp.
- **Cross-Table Deduplication Check** — Mandatory cross-reference of `leads`, `clients`, and `suppression_list` before the Delivery Engine fires. Prevents re-outreach to existing clients and suppressed contacts.
- **⚠️ Weakpoint Patched** — Earlier versions had no check for a contact existing in both the `leads` pipeline and the `clients` table simultaneously (possible if a referral came in during an active outreach sequence). This check is now enforced at Phase 4.

---

## 📤 Phase 5: Delivery Engine

- **Channel Router** — Selects send channel based on available contact vectors and Viability Score. High-score leads with both email and SMS receive simultaneous sends. Budget-tier leads receive email only.
- **Email Dispatch (SMTP)** — Sends via configured SMTP. Embeds a 1x1 tracking pixel for open detection and unique click-through links for reply-intent signals. Includes a CAN-SPAM-compliant unsubscribe footer.
- **SMS Dispatch (Twilio)** — Sends the Closer's SMS variant. `STOP` keyword handling writes the number to `suppression_list` immediately via Twilio webhook.
- **Send Scheduling** — Rate-limited with randomized 30–120 second stagger between individual sends. Configurable daily send cap per sending domain to protect deliverability reputation.
- **Compliance Layer** — `suppression_list` table is checked at the Channel Router before every send attempt, every time.
- **Delivery Logging** — Every send attempt (success or failure) is written to `outreach_log` with timestamp, channel, message ID, lead ID, and HTTP/SMTP response code.

---

## 🔁 Phase 6: Response & Follow-Up Engine

- **Reply Monitoring** — IMAP polling checks the outreach inbox on a configurable interval (default: every 15 minutes) for email replies. Twilio webhooks handle inbound SMS responses in real time.
- **Response Classifier** — Categorizes inbound responses as: `INTERESTED`, `NOT_INTERESTED`, `AUTO_REPLY`, `BOUNCE`, or `UNSUBSCRIBE`. Implemented as a single-prompt `lfm2` call with a deterministic output schema — not a full agent.
- **Follow-Up Drip Sequence** — Leads with no response receive a 4-touch sequence:
  - Touch 2 (Day 3): Shorter, softer re-engagement variant
  - Touch 3 (Day 7): Different angle — social proof or local competitor framing
  - Touch 4 (Day 14): Final low-pressure message with high-curiosity close
  - After Touch 4: Lead marked `SEQUENCE_COMPLETE`. No further outreach without manual reactivation.
- **Hot Lead Escalation** — `INTERESTED` classifications automatically trigger Phase 7 (Client Onboarding). This handoff is fully automated — no manual step required.
- **⚠️ Weakpoint Patched** — Earlier versions flagged hot leads for "human follow-through" with no defined automated next step. That gap caused leads to go cold during the manual delay. Phase 7 now fires immediately on `INTERESTED` classification.
- **Bounce & Suppression Handling** — Hard bounces are auto-added to `suppression_list`. Soft bounces are retried once on the next run cycle before suppression.

---

## 🤝 Phase 7: Client Onboarding & Requirements Gathering

Triggered automatically on `INTERESTED` classification. The pipeline transitions the contact from prospect to client and extracts everything the Developer Agent needs.

- **Onboarding Message** — The Closer generates a tailored onboarding email and/or SMS that acknowledges their interest, briefly explains the next step, and delivers the intake form link. Tone and channel match the original outreach that received the response.
- **Intake Form (Formbricks — open source, self-hosted)** — Collects:
  - Business name and tagline
  - Services or products offered
  - Target audience description
  - Preferred colors and any existing branding assets
  - 3–5 reference websites they like
  - Photos and/or logo upload
  - Primary call-to-action type (call, book, order, contact, directions)
  - Preferred domain name if they have one in mind
- **Requirements Parser** — On form submission, a single-prompt `lfm2` call ingests the response and outputs a structured `project_brief.json` conforming to Contract CB-DEV (see Phase 2). Stored in the `projects` table with status `BRIEFED`.
- **Domain Availability Check** — Runtime automatically performs a WHOIS lookup on the preferred domain. If unavailable, 3 alternatives are generated and included in the onboarding confirmation email.
- **Client Record Creation** — Lead is promoted from `leads` to `clients` table. A unique `client_id` is assigned. All subsequent pipeline activity references this ID.
- **Intake Abandonment Handling** — If the form is not submitted within 3 days, a reminder sequence fires (2 touches, Day 3 and Day 6). If no submission by Day 7, the project is marked `STALLED` and flagged in the run summary for human review.

---

## 💳 Phase 7.5: Payment Gate *(Stripe — Implementation Deferred)*

> **Status: Placeholder.** Payment integration is scoped for a future development sprint. The architectural position is confirmed: payment must be collected and confirmed before the Developer Agent begins any build work in Phase 8. No exceptions.

**Intended Flow (Stripe):**
1. On `project_brief.json` creation, the runtime generates a Stripe Payment Link via the Stripe API, configured with the appropriate price tier based on `revenue_tier` from the lead profile.
2. The payment link is delivered to the client in the onboarding confirmation email alongside their domain check results.
3. A Stripe webhook listens for `payment_intent.succeeded`. On confirmation, the `projects` table record is updated from `BRIEFED` to `PAYMENT_CONFIRMED` and Phase 8 is triggered.
4. If payment is not received within 5 days, a reminder fires. After 10 days with no payment, the project is marked `PAYMENT_LAPSED` and flagged in the run summary.

**Tables affected:** `projects` (add `payment_status`, `stripe_payment_intent_id`, `payment_confirmed_at` columns — defined in schema, nullable until implemented).

---

## 👨‍💻 Phase 8: Developer Agent

Receives a confirmed `project_brief.json` and autonomously produces a deployable website. This is the most complex component in the pipeline.

**Model:** `deepseek-coder-v2` via Ollama — chosen specifically for code generation quality.

**Build Stack (fully open source):**
- **Astro** — Static site generator. Ideal for local business sites: fast build times, zero JS overhead by default, exceptional Core Web Vitals out of the box, and trivially deployable as a static `/dist` directory.
- **Tailwind CSS** — Utility-first styling baked into the Astro build pipeline.
- **Template Library** — A local library of 8–12 Astro starter templates organized by business category (restaurant, trades, medical, retail, beauty, fitness, etc.). Stored in `/templates` within the project repo. The Developer Agent selects the closest match on `industry` and `revenue_tier` from the project brief, then customizes from the template rather than generating from scratch.

**Developer Agent Sub-Tasks (sequential):**

1. **Template Selection** — Matches `industry` and `revenue_tier` to the most appropriate base template. Premium-tier clients receive more complex multi-page templates. Selection logged to `projects` table.

2. **Content Generation** — Populates all template content slots: headline, subheadline, about section, services list, CTA copy, contact section, footer. Drawn directly from the client's intake form responses. Any blank fields fall back to `lfm2`-generated placeholder copy that matches the business category and tone.

3. **Asset Integration** — Processes uploaded photos (resize, compress, convert to WebP via `sharp`). If no photos were uploaded, sources license-free stock images from the Unsplash API matched to the business category and injects them automatically.

4. **SEO Layer** — Auto-generates meta title, meta description, Open Graph tags, canonical URLs, and a `sitemap.xml` based on business name, physical location, and services list.

5. **Content Diff Validation** — Before build is triggered, a validation step compares generated content fields against the original intake form responses. If more than 20% of content fields cannot be traced back to intake data, the project is flagged `CONTENT_REVIEW` and held for human inspection rather than auto-deploying. This prevents hallucinated business details from going live.

6. **Build Execution** — Runs `astro build`. Build errors are caught, logged with full stderr output, and the Developer Agent attempts self-correction by re-prompting with the error appended to context (max 2 retries). Persistent build failure marks the project `BUILD_FAILED` and raises a human review flag in the run summary.

7. **Output** — A complete, built `/dist` directory. Stored locally at `/builds/{client_id}/`, path referenced in the `projects` table. Status updated to `BUILD_COMPLETE`.

---

## 🖥️ Phase 9: Deployment & Hosting Infrastructure

**Core Platform: Coolify (open source, self-hosted)**
Coolify is a self-hosted platform that manages Docker containers, reverse proxying, SSL certificates, and deployments via a programmable API. The runtime triggers all deployments without manual intervention.

**Infrastructure Stack:**
- **VPS — Hetzner Cloud** — CAX21 ARM instance. Cost-effective, EU-based (GDPR-relevant), and capable of serving dozens of static sites concurrently. Scale horizontally as client volume grows by adding nodes to the Coolify cluster.
- **Coolify** — Installed on the VPS. Manages all client site deployments as isolated containers, configures Nginx reverse proxy per site automatically, and provisions SSL certificates via Let's Encrypt. Each client site gets its own staging subdomain on Axiom's hosting domain (e.g., `clientname.axiomhosting.com`) before go-live.
- **Nginx** — Managed by Coolify. No manual Nginx config required.
- **Umami (open source, self-hosted)** — Analytics platform installed once on the VPS. Each client site receives a unique Umami tracking script injected at build time. Gives clients a privacy-respecting alternative to Google Analytics — a genuine value-add included in the deliverable at no extra infrastructure cost.
- **Backups** — Coolify handles automated daily backups of all deployments to a Hetzner Object Storage bucket.

**Deployment Flow:**
1. Runtime calls the Coolify API with the `/dist` build output path and the client's staging subdomain configuration.
2. Coolify provisions the container, assigns the subdomain, configures Nginx, and issues an SSL certificate automatically.
3. Staging URL is written to the `deployments` table and included in the client-facing approval email (Phase 10).

---

## ✅ Phase 10: QA, Client Approval & Go-Live

- **Staging Delivery** — Client receives an email with their staging URL, a brief plain-language description of what was built, and a Formbricks approval form with two options: approve as-is, or submit revision notes.
- **Revision Handling** — Revision requests re-enter the Developer Agent (Phase 8) with the feedback text appended to the original `project_brief.json` as a `revision_notes` field. Maximum 2 rounds of revision are included in the standard delivery. A 3rd revision request is flagged in the run summary for human review and potential upsell conversation.
- **Approval Abandonment Handling** — If the client does not respond to the staging link within 3 days, a reminder fires. After 7 days with no response, the project is marked `APPROVAL_STALLED` and flagged for human review.
- **Go-Live Trigger** — On approval, the runtime calls the Coolify API to redeploy the site against the client's custom domain. DNS configuration instructions are emailed to the client with a registrar-agnostic step-by-step guide.
- **DNS Propagation Check** — Runtime polls DNS resolution for the custom domain every 30 minutes. On successful resolution, a go-live confirmation email is sent to the client containing: live URL, Umami analytics login credentials, and hosting and maintenance contact details.
- **Project Closure** — `projects` table status updated to `DELIVERED`. `clients` table record updated with `site_url` and `go_live_at` timestamp.

---

## 🗄️ Phase 11: Persistence, State & Reporting

All runtime state, lead data, sequence tracking, project history, and deployment records live in a single local SQLite database. Schema is migration-safe — no SQLite-specific syntax that would break on a move to DuckDB or Postgres.

### SQLite Schema

```sql
-- ============================================================
-- LEADS
-- All inbound acquisition candidates from Phase 1
-- ============================================================
CREATE TABLE IF NOT EXISTS leads (
    lead_id             TEXT PRIMARY KEY,
    osm_id              TEXT,
    business_name       TEXT NOT NULL,
    industry            TEXT,
    address             TEXT,
    city                TEXT,
    state               TEXT,
    zip                 TEXT,
    coordinates         TEXT,
    phone               TEXT,
    email               TEXT,
    social_profiles     TEXT,       -- JSON array stored as text
    has_website         INTEGER DEFAULT 0,
    website_url         TEXT,
    data_source         TEXT,
    viability_score     REAL,
    revenue_tier        TEXT,       -- 'Budget' | 'Mid-Market' | 'Premium'
    status              TEXT DEFAULT 'NEW',
                                    -- NEW | PROCESSING | DISCARDED | CONTACTED
                                    -- INTERESTED | UNROUTABLE | SCHEMA_FAILURE
    discard_reason      TEXT,
    created_at          TEXT DEFAULT (datetime('now')),
    updated_at          TEXT DEFAULT (datetime('now'))
);

-- ============================================================
-- CLIENTS
-- Promoted from leads on INTERESTED classification
-- ============================================================
CREATE TABLE IF NOT EXISTS clients (
    client_id           TEXT PRIMARY KEY,
    lead_id             TEXT NOT NULL REFERENCES leads(lead_id),
    business_name       TEXT NOT NULL,
    owner_name          TEXT,
    email               TEXT,
    phone               TEXT,
    address             TEXT,
    city                TEXT,
    state               TEXT,
    zip                 TEXT,
    revenue_tier        TEXT,
    site_url            TEXT,
    status              TEXT DEFAULT 'ONBOARDING',
                                    -- ONBOARDING | PAYMENT_PENDING | ACTIVE
                                    -- DELIVERED | CHURNED
    onboarded_at        TEXT DEFAULT (datetime('now')),
    go_live_at          TEXT,
    updated_at          TEXT DEFAULT (datetime('now'))
);

-- ============================================================
-- OUTREACH LOG
-- Every send attempt across all channels
-- ============================================================
CREATE TABLE IF NOT EXISTS outreach_log (
    log_id              TEXT PRIMARY KEY,
    lead_id             TEXT REFERENCES leads(lead_id),
    channel             TEXT NOT NULL,  -- 'email' | 'sms' | 'voice'
    message_id          TEXT,
    status              TEXT,           -- 'SENT' | 'FAILED' | 'BOUNCED'
    response_code       TEXT,
    sent_at             TEXT DEFAULT (datetime('now')),
    opened_at           TEXT,
    clicked_at          TEXT
);

-- ============================================================
-- SEQUENCES
-- Drip sequence state per lead
-- ============================================================
CREATE TABLE IF NOT EXISTS sequences (
    sequence_id         TEXT PRIMARY KEY,
    lead_id             TEXT NOT NULL REFERENCES leads(lead_id),
    current_touch       INTEGER DEFAULT 1,
    next_send_at        TEXT,
    status              TEXT DEFAULT 'ACTIVE',
                                    -- ACTIVE | COMPLETE | PAUSED | CANCELLED
    created_at          TEXT DEFAULT (datetime('now')),
    updated_at          TEXT DEFAULT (datetime('now'))
);

-- ============================================================
-- REPLIES
-- All inbound responses across channels
-- ============================================================
CREATE TABLE IF NOT EXISTS replies (
    reply_id            TEXT PRIMARY KEY,
    lead_id             TEXT REFERENCES leads(lead_id),
    client_id           TEXT REFERENCES clients(client_id),
    channel             TEXT,           -- 'email' | 'sms'
    raw_content         TEXT,
    classification      TEXT,           -- INTERESTED | NOT_INTERESTED |
                                        -- AUTO_REPLY | BOUNCE | UNSUBSCRIBE
    received_at         TEXT DEFAULT (datetime('now'))
);

-- ============================================================
-- SUPPRESSION LIST
-- Contacts that must never be contacted again
-- ============================================================
CREATE TABLE IF NOT EXISTS suppression_list (
    suppression_id      TEXT PRIMARY KEY,
    contact_value       TEXT NOT NULL UNIQUE,  -- email address or phone number
    contact_type        TEXT,                  -- 'email' | 'phone'
    reason              TEXT,                  -- 'UNSUBSCRIBE' | 'HARD_BOUNCE' | 'MANUAL'
    suppressed_at       TEXT DEFAULT (datetime('now'))
);

-- ============================================================
-- PROJECTS
-- One record per client website build
-- ============================================================
CREATE TABLE IF NOT EXISTS projects (
    project_id          TEXT PRIMARY KEY,
    client_id           TEXT NOT NULL REFERENCES clients(client_id),
    project_brief       TEXT,           -- full project_brief.json stored as text
    template_selected   TEXT,
    revision_count      INTEGER DEFAULT 0,
    build_path          TEXT,           -- local path to /dist directory
    staging_url         TEXT,
    status              TEXT DEFAULT 'BRIEFED',
                                        -- BRIEFED | PAYMENT_CONFIRMED
                                        -- PAYMENT_LAPSED | BUILD_IN_PROGRESS
                                        -- BUILD_COMPLETE | CONTENT_REVIEW
                                        -- BUILD_FAILED | DELIVERED
                                        -- APPROVAL_STALLED | STALLED
    payment_status      TEXT,           -- NULL | 'PENDING' | 'CONFIRMED' | 'LAPSED'
    stripe_payment_intent_id  TEXT,     -- NULL until Stripe implemented
    payment_confirmed_at      TEXT,     -- NULL until Stripe implemented
    briefed_at          TEXT DEFAULT (datetime('now')),
    build_started_at    TEXT,
    build_completed_at  TEXT,
    updated_at          TEXT DEFAULT (datetime('now'))
);

-- ============================================================
-- DEPLOYMENTS
-- Coolify deployment records per project
-- ============================================================
CREATE TABLE IF NOT EXISTS deployments (
    deployment_id       TEXT PRIMARY KEY,
    project_id          TEXT NOT NULL REFERENCES projects(project_id),
    client_id           TEXT NOT NULL REFERENCES clients(client_id),
    coolify_app_id      TEXT,
    staging_url         TEXT,
    production_url      TEXT,
    ssl_provisioned     INTEGER DEFAULT 0,
    dns_resolved        INTEGER DEFAULT 0,
    status              TEXT DEFAULT 'STAGING',
                                        -- STAGING | LIVE | FAILED | ROLLED_BACK
    deployed_at         TEXT DEFAULT (datetime('now')),
    went_live_at        TEXT,
    updated_at          TEXT DEFAULT (datetime('now'))
);

-- ============================================================
-- RUN STATE
-- Checkpoint data for recovery on interrupted runs
-- ============================================================
CREATE TABLE IF NOT EXISTS run_state (
    run_id              TEXT PRIMARY KEY,
    phase               TEXT,           -- last completed phase
    last_lead_id        TEXT,
    status              TEXT DEFAULT 'RUNNING',
                                        -- RUNNING | COMPLETE | INTERRUPTED
    started_at          TEXT DEFAULT (datetime('now')),
    completed_at        TEXT,
    summary             TEXT            -- JSON summary blob of run metrics
);
```

### Checkpoint & Recovery
The runtime writes a checkpoint to `run_state` after every successfully completed phase. If a run is interrupted at any point, execution resumes from the last clean checkpoint rather than restarting from scratch. The `last_lead_id` field ensures the lead cursor is preserved across restarts.

### Run Summary Report
On run completion, the runtime generates a structured plain-text summary written to `run_state.summary` and printed to terminal. Covers: leads ingested, discarded (with reason breakdown), sent, opened, replied, hot leads escalated, projects briefed, builds completed, builds failed, sites deployed, sites live, schema failures, and human review flags raised.

### Migration Path
Schema avoids all SQLite-specific syntax. Designed for clean migration to DuckDB (for analytical query performance at scale) or Postgres (for multi-process write concurrency) without schema changes. Migration trigger: sustained SQLite write contention during high-volume runs, or the need for concurrent writes from multiple pipeline processes.

---

## 🔮 Placeholder: AI Voice Layer *(Future Phase)*

Cold call talk-track scripts generated by the Closer are stored in the `outreach_log` and `projects` tables now, before voice is implemented.

When implemented, the Voice Engine slots between Phase 5 (Delivery) and Phase 6 (Response), routing high-score or non-responsive leads to an AI voice call using the stored script.

**Secondary use case worth noting:** The voice layer also has a strong application in Phase 7 (Requirements Gathering). Instead of an intake form, an AI voice agent could call the client and extract the project brief conversationally. This is a significant UX upgrade for less tech-savvy business owners who are unlikely to complete a form — and is directly aligned with the target market of this pipeline.

---

## 🔍 Full Weakpoint Audit

| # | Weakpoint | Phase | Status |
|---|-----------|-------|--------|
| 1 | OpenClaw rate limiting and bot-blocking | Phase 1 | ✅ Patched — rotating agents + directory API fallback |
| 2 | Single model for all agent tasks | Phase 2 | ✅ Patched — per-agent model routing |
| 3 | No hosting infrastructure health check on startup | Phase 0 | ✅ Patched — Coolify reachability check in bootstrap |
| 4 | Lead and client table cross-contamination | Phase 4 | ✅ Patched — cross-table dedup check enforced |
| 5 | Hot lead handoff was manual with no automated next step | Phase 6 | ✅ Patched — auto-triggers Phase 7 on INTERESTED |
| 6 | Intake form abandonment stalls pipeline indefinitely | Phase 7 | ✅ Patched — reminder sequence + STALLED flag |
| 7 | AI-generated copy not validated against intake data | Phase 8 | ✅ Patched — content diff validation before build |
| 8 | Client staging approval stall | Phase 10 | ✅ Patched — reminder sequence + APPROVAL_STALLED flag |
| 9 | No payment gate before build work begins | Phase 7.5 | ⚠️ Deferred — architectural position confirmed, Stripe implementation pending |
| 10 | SQLite write contention at scale | Phase 11 | ⚠️ Noted — migration path designed in, not yet needed |
| 11 | JSON contracts between agents lack legal review | Phase 2 | ⚠️ Deferred — placeholders in place, legal review required pre-production |

---

*Framework Version: 4.0 — Last Updated: April 2026*
