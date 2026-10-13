# MCP Integration — live-verified 2026-10-13

Official server: `https://mcp.mcd.cn` (Streamable HTTP, Bearer token).

**Evidence status: live-verified.** Executed against the official endpoint on 2026-10-13 with a personal Token.
Sample: 3 Shanghai stores (人民广场商圈), 21 read-only calls, no mutation, no account/order access.
Raw snapshot committed at `fixtures/live-2026-10-13.json`; replay it offline with
`python -m doctor --fixture fixtures/live-2026-10-13.json`.

## Transport

`doctor/transport.py` speaks MCP over streamable HTTP directly — `initialize` → `tools/call` → `tools/list`,
with SSE (`data:` lines) unwrapped. Python standard library only; **no external CLI, no third-party packages**.
The endpoint accepts session-less calls, so a failed handshake degrades gracefully instead of aborting.

`README.md` and `DESIGN.md` still mention `mcd-cli` in a few historical notes — that dependency has been
replaced and is no longer required.

## Read-only tools used

| Tool | Arguments | Purpose | Live result |
|---|---|---|---|
| `query-nearby-stores` | `beType=1, searchType=2, city, keyword` | Store discovery | 3 stores resolved |
| `query-meals` | `storeCode, orderType=1, beType=1` | Menu SKU map | 115 / 121 / 105 SKUs parsed |
| `calculate-price` | `storeCode, orderType=1, beType=1, items[{productCode, quantity}]` | Cross-check menu price | 15/15 PASS |
| `list-nutrition-foods` | — | Name coverage observation | 160 rows parsed |
| `tools/list` | — | Schema contract check | 5 core read-only tools found |

No `create-order`, `auto-bind-coupons`, `draw-lottery`, address or order-history tool is ever called.
`doctor/core.py::READ_ONLY_TOOLS` is an allowlist; anything else raises `PermissionError`.

## What live traffic revealed

Three parsing traps, all invisible to offline fixtures (regression tests in
`tests/test_core.py::RealResponseRegression`):

1. **The response glossary breaks JSON parsing.** The server prepends a field glossary that contains
   `data.meals.{code}.currentPrice`. The literal `{code}` destroys brace matching, so `json.loads` fails and the
   menu appears empty → `MCD-101` reported `UNKNOWN`. Fixed with `core.scan_balanced_json`, which tries every
   `{` as a start position and walks forward with a string-state machine.

2. **The nutrition payload is not JSON and not Markdown.** `data` is a *string* in a custom format:
   `[160]{productName,nutritionDescription,energyKj,...}:` followed by CSV rows. Markdown-table parsing
   extracted **0** names. Fixed with `core.parse_official_csv`, which reads the header and maps rows by column
   index. Coverage went from `0/115` to `19/115`, and `MCD-201` moved from WARN to INFO.

3. **Brace scanning can latch onto the wrong object.** Mitigated by preferring the `Original Response` marker and
   requiring the decoded value to be a `dict`.

Before the fix every core check returned `UNKNOWN`; the tool that checks "can this data be parsed" could not
parse its own input. **That is the finding, and it is only obtainable by running live.**

## Business-layer checks

- **Transport success ≠ business success.** `code=200` with an empty `data` and an empty `productList` is still a
  possible outcome; it is reported as WARN for human review, never asserted as a provider bug.
- **Unit awareness.** `calculate-price` returns cents (`price=1590` → ¥15.90) while the menu displays yuan.
  `core.money_cents` uses `Decimal` throughout to avoid float drift.
- **Cross-store price differences are not faults.** 21 of 96 common SKUs differ in price; the quoting endpoint
  faithfully reflects each store's own pricing. Reported as INFO/observation only.
- **Nutrition coverage is a lower bound** on exact name matches, not evidence of nutritional accuracy.

## Honest limits

- Without an independent ground truth this tool verifies **consistency between endpoints**, not real-world
  accuracy. If two endpoints are wrong in the same way, the check still passes.
- Prices reflect the sampling window only; menus change with the meal period.
- Cross-store comparison ignores coupons, delivery fees and distance.

Related upstream Issues:
- https://github.com/M-China/mcd-mcp-server/issues/16
- https://github.com/M-China/mcd-mcp-server/issues/11
- https://github.com/M-China/mcd-mcp-server/issues/9
- https://github.com/M-China/mcd-mcp-server/issues/13
