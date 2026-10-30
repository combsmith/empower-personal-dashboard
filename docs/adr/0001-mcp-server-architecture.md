# ADR-0001: Model Context Protocol (MCP) Server Architecture & Implementation

- **Status:** Accepted
- **Date:** 2026-09-30
- **Deciders:** Don Petry
- **Consulted:** Antigravity AI, open-source community architectural precedents
- **Informed:** Contributors, agentic runtime integrators

---

## 1. Context & Problem Statement

Users of **Empower Personal Dashboard** (formerly Personal Capital) require automated, programmatic access to their aggregated financial data—including account balances, asset-class allocations, investment holdings, and historical transactions. With the rise of agentic coding environments and personal AI assistants (Claude Desktop, Antigravity CLI, Cursor, Windsurf, Claude Code), there is significant demand to expose this data via Anthropic's **Model Context Protocol (MCP)**.

However, financial aggregators present unique technical and security challenges:
1. **Interactive Multi-Factor Authentication (2FA):** Empower requires interactive two-factor SMS/Email verification and device binding. Routing interactive authentication loops or raw credentials through an LLM tool-calling context creates severe prompt-injection risks and credential-exfiltration vulnerabilities.
2. **Vendor Ecosystem Constraints:** Retail financial platforms prioritize consumer web portals and wealth management advisory sales over public developer APIs. A multi-tenant, hosted cloud MCP server (e.g. for consumer web Gemini Gems or Claude.ai web) is legally and operationally unfeasible without an official vendor-managed OAuth2 infrastructure.
3. **Context Window Limits:** Financial institution feeds can return megabytes of historical transaction records and hundreds of position lots, risking context overflow and degraded model reasoning if uncurated.

We need a standardized architectural pattern for exposing Empower data via MCP while guaranteeing absolute user data privacy, security, and developer ergonomics.

---

## 2. Decision Outcome

We decided to implement **Option 1: Co-located Native FastMCP Server with Out-of-Band Authentication**:

1. **Native Python FastMCP Integration:**
   Implement the MCP server directly within `empower-personal-dashboard` using the official Python MCP SDK (`from mcp.server.fastmcp import FastMCP`).
2. **Optional Extra Packaging:**
   Package MCP dependencies under an optional extra (`pip install "empower-personal-dashboard[mcp]"`), maintaining the core library's lean dependency footprint (`requests` only) for non-MCP users.
3. **Out-of-Band Authentication, In-Band Consumption:**
   - **Authentication (Out-of-Band):** The user performs the initial interactive login and 2FA challenge via the standard terminal CLI (`empower --login`). This binds the device and writes session cookies and CSRF tokens to `~/.empower_personal_dashboard_session.json` with POSIX `0600` permissions.
   - **Consumption (In-Band):** The MCP server runs headlessly, consuming the existing session file. If the session is missing or expired, tools fail fast with structured error responses instructing the user to re-authenticate via the terminal. Passwords and 2FA verification codes are **never** routed through the LLM context.
4. **Dual Transport Support (`stdio` + `sse`):**
   - **`stdio` (Default):** Zero-overhead local subprocess execution for desktop agent runtimes (Claude Desktop, Antigravity CLI, Cursor, Windsurf).
   - **`sse` (Networked):** Built-in Server-Sent Events HTTP server for self-hosted container environments (e.g. home NAS, local server behind Tailscale Funnel / Cloudflare Tunnel). Client authentication is mandatory off loopback; see the amendment below.

### Amendment (2026-10-27): HTTP transport client authentication (issue #136)

**Context.** Every MCP client acts with the owner's Empower session. `stdio` is authenticated by construction (only the parent process holds the pipe). The `sse` HTTP app from the SDK had no client authentication, so binding it to `0.0.0.0`, which the CLI help used to suggest, exposed balances, holdings and transactions to anything that could reach the port (CWE-306).

**Decision.**
1. `--host` stays `127.0.0.1` by default. Unauthenticated HTTP is allowed **only** on a loopback bind (`localhost`, `127.0.0.0/8`, `::1`).
2. A non-loopback bind **refuses to start** (exit code 2, clear message on stderr) unless `EMPOWER_MCP_AUTH_TOKEN` holds a random secret of at least 32 printable ASCII characters. `--require-auth` extends that requirement to loopback for tunnel, reverse-proxy and multi-user setups. Once a token is configured, it is enforced on every HTTP request whatever the bind address.
3. Enforcement is a small pure-ASGI `BearerTokenAuthMiddleware` around the SDK's `sse_app()`. It uses `hmac.compare_digest`, returns `401` with `WWW-Authenticate: Bearer`, closes websockets with 1008, and passes lifespan events through. The token comes only from the environment (never argv) and is never logged.
4. `main()` builds the app and runs uvicorn itself instead of calling `server.run(transport="sse", ...)`. mcp 1.x `FastMCP.run()` does not accept `host`/`port`, so the HTTP path is now version-portable too.

**Why middleware instead of the SDK's `auth=` / `token_verifier=`.** Those hooks are OAuth 2.1 resource-server plumbing. They require `AuthSettings` (issuer URL, resource server URL) and have different signatures in mcp 1.x and 2.x (and none in early 1.x), and a single-user static token does not fit them well. Wrapping the returned ASGI app behaves identically from mcp 1.x through 2.x and can be tested hermetically by driving the ASGI app directly.

**DNS rebinding.** For unauthenticated loopback binds, the SDK's own Host/Origin validation (`TransportSecuritySettings`, auto-enabled for `127.0.0.1`/`localhost`/`::1` in recent 1.x and in 2.x) is kept. That validation only allows localhost names. It is therefore disabled whenever a bearer token is configured, so a tunnel or reverse proxy can forward a public `Host` header to a loopback bind under `--require-auth`. It is also disabled on non-loopback binds, matching mcp 2.x defaults. In both cases the bearer token is the control, because a rebinding page cannot supply it. Very old mcp 1.x releases without `TransportSecuritySettings` have no rebinding protection on loopback. Use `--require-auth` there.

**Consequences.** This is a breaking change: `--transport sse --host 0.0.0.0` without a token no longer starts. TLS is still out of scope. Operators exposing the port beyond the machine should terminate TLS at a proxy or tunnel.

---

## 3. Architecture & Interface Design

### Sequence Flow

```mermaid
sequenceDiagram
    autonumber
    actor User as User / LLM
    participant Host as MCP Host (Claude Desktop / Antigravity)
    participant Server as empower-mcp (FastMCP)
    participant Disk as ~/.empower_personal_dashboard_session.json
    participant API as Empower API (pc-api.empower-retirement.com)

    Note over User, Disk: Pre-requisite: Terminal login executed once via 'empower --login'
    
    User->>Host: "What is my current net worth and top 5 holdings?"
    Host->>Server: Call Tool: get_net_worth_summary()
    Server->>Disk: Read session cookies (mode 0600)
    Server->>API: POST /newaccount/getAccounts
    API-->>Server: Balances Payload
    Server-->>Host: { net_worth: $X, cash: $Y, investments: $Z }
    
    Host->>Server: Call Tool: get_holdings(limit=5)
    Server->>API: POST /invest/getHoldings
    API-->>Server: Holdings Payload
    Server-->>Host: Top 5 Holdings Table / JSON
    Host-->>User: Formatted financial analysis & answer
```

### Core MCP Tools Specification

| Tool Name | Parameters | Return Schema | Purpose |
| :--- | :--- | :--- | :--- |
| `get_net_worth_summary` | `format: str = "json"` | `{ net_worth, total_cash, total_investment, total_credit, total_mortgage, total_loan, formatted_output? }` | Lightweight summary (~100 tokens) minimizing context consumption. Supports Markdown and Table formats. |
| `get_balances` | `account_types: list[str] = None`<br>`include_inactive: bool = False`<br>`format: str = "json"` | `{ accounts: [...], totals_by_type: {...}, formatted_output? }` | Full per-account balance breakdown with masked account numbers and formatted rendering. |
| `get_holdings` | `ticker: str = None`<br>`account_id: int = None`<br>`aggregate_by_ticker: bool = False`<br>`sort_by: str = "value"`<br>`min_value: float = 0.0`<br>`limit: int = 50`<br>`format: str = "json"` | `{ total_portfolio_value, holdings: [...], matching_count, returned_count, formatted_output? }` | Security positions with quantity, price, cost basis, percentage weight, cross-account aggregation, and sorting. |
| `get_transactions` | `start_date: str = None`<br>`end_date: str = None`<br>`days: int = None`<br>`account_id: int = None`<br>`category: str = None`<br>`spending_only: bool = False`<br>`income_only: bool = False`<br>`transaction_type: str = None`<br>`min_amount: float = None`<br>`max_amount: float = None`<br>`limit: int = 50`<br>`format: str = "json"` | `{ total_transactions, net_cashflow, transactions: [...], formatted_output? }` | Filtered transaction history with relative lookback (`days`), strict spending/income filters, transaction type filtering, and pagination guardrails. |
| `export_data` | `destination_dir: str = "./data"`<br>`scope: str = "all"`<br>`export_csv: bool = True`<br>`start_date: str = None`<br>`end_date: str = None`<br>`limit: int = None` | `{ status, destination_dir, scope, files_count, files: [...], summary }` | Programmatic bulk data export to disk in JSON, JSONL, and companion CSV files (matching CLI `empower --all --csv`). |
| `get_export_options` | None | `{ supported_formats: [...], data_scopes: [...], cli_examples: {...}, mcp_export_tool: str }` | Discovery tool for export capabilities, CLI commands, and destination configurations. |
| `check_auth_status` | None | `{ authenticated: bool, session_path: str, status: str, message: str }` | Healthcheck tool allowing agents to verify session viability before planning. |

### MCP Resources & Prompts

* **Resources:**
  - `empower://balances/summary` (Dynamic application state snapshot)
  - `empower://holdings/portfolio` (Current portfolio asset allocation)
  - `empower://accounts/list` (Linked institution metadata)
  - `empower://export/options` (Documentation of export options, formats, and CLI flags Markdown)
* **Prompts:**
  - `portfolio_review`: Pre-engineered analysis for asset allocation, drift, and cash drag.
  - `spending_audit`: Structured cashflow audit analyzing monthly burn and recurring subscriptions.
  - `recent_purchases_audit`: Retail spending, credit card charges, and subscription audit over past N days.
  - `top_holdings_review`: Consolidates and audits top investment holdings across all accounts.

---

## 4. Alternatives Considered

### Alternative A: Standalone Repository (`mcp-server-empower`)
* **Description:** Separate repo importing `empower-personal-dashboard` from PyPI.
* **Why Rejected:** Introduces dual-repo release synchronization overhead, duplicate CI/CD pipelines, and version drift whenever Empower API quirks require client updates. Co-locating in the core repo provides a single source of truth.

### Alternative B: CLI Subprocess Wrapper
* **Description:** Wrapper script executing `empower --all --format json` on each invocation.
* **Why Rejected:** Incurs high latency (spawning a full Python runtime per tool call), relies on brittle text/stdout parsing, and lacks support for MCP Resources and native type validation.

### Alternative C: Multi-Tenant Hosted SaaS MCP Server
* **Description:** Cloud-hosted server allowing public users to connect their Empower accounts in web interfaces (e.g. consumer Claude.ai or Gemini Gems).
* **Why Rejected:** Without official vendor OAuth APIs, a hosted multi-tenant service would require storing retail banking credentials or session cookies on a centralized server. This introduces massive regulatory (GLBA, SOC 2, CFPB 1033) and security liabilities. Local-first execution is the only ethically and legally sound approach for community reverse-engineered financial APIs.

---

## 5. Security & Privacy Guardrails

1. **Zero-PII Commitment:** All automated tests and mock engines must use 100% synthetic test fixtures. Real bank account numbers, balances, or user names must never enter git.
2. **Account Number Masking:** Tools return masked account numbers by default (e.g., `Chase Checking - 1234`) to prevent full account identifier leakage into conversation transcripts.
3. **In-Memory TTL Caching:** Implement a 5-minute TTL cache on balances and holdings to prevent aggressive LLM loops from triggering upstream rate limits or IP bans.
4. **Context Window Protection:** All transaction and holding queries enforce pagination caps (`default: 50`, `max: 200`).

---

## 6. Implementation Plan & Milestones

1. **Milestone 1:** Add `[project.optional-dependencies] mcp = ["mcp>=1.2.0"]` and `empower-mcp` console script to `pyproject.toml`.
2. **Milestone 2:** Implement `empower_personal_dashboard/mcp_server.py` with `FastMCP` tools, resources, and error handlers.
3. **Milestone 3:** Add comprehensive unit tests in `tests/test_mcp_server.py` using offline synthetic fixtures.
4. **Milestone 4:** Update documentation in `README.md`, `AGENTS.md`, and publish a release.
