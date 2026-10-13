# McD MCP Doctor — Research → MVP Design

Date: 2026-10-12. This is a proposal and unverified live integration, not proof of upstream bugs.

## Thesis

Generic inspectors validate protocol connectivity; a McDonald's-domain doctor should validate **business meaning and cross-tool consistency**. A successful tool response is not necessarily a valid price or a complete nutrition source.

## Priority test matrix

| ID | Tool(s) | Test | Oracle | Severity policy | MVP |
|---|---|---|---|---|---|
| MCP-001 | tools/list | Are read-only core tools advertised? | Live tool list | Missing WARN | yes |
| MCP-002 | tools/list | Documented input required fields | Runtime schema + doc | Schema drift WARN | yes |
| MCD-101 | query-meals | Success vs code, data.meals shape | Business result | Failure FAIL; empty WARN | yes |
| MCD-102 | query-meals, calculate-price | Same item code mapped to productCode, zero-price anomaly | Both tool responses; check empty productList | Suspicious WARN | yes |
| MCD-103 | query-meal-detail | Are rounds parseable? | Tool response | Unknown if unparseable | yes (optional) |
| MCD-104 | query-meal-detail | Single-choice rounds | Tool response alone can't prove availability | INFO only | yes (optional) |
| MCD-201 | list-nutrition-foods, query-meals | Exact-name join coverage | Matches are lower bound | INFO; empty WARN | yes |
| MCD-202 | list-nutrition-foods | Retry and volatility across time | Consistent sampled windows | Transient WARN, not FAIL | deferred |
| MCD-203 | query-meal-detail, list-nutrition-foods | Full combo nutrition coverage with child matching | Every component parsed and weighted | UNKNOWN unless complete | deferred |
| MCD-301 | order-list schema only | Paging/date parameters documented or missing | tools/list + docs | INFO | deferred |
| MCD-401 | calculate-price | Two store/channel comparisons | Pricing conditions and snapshots | Differences are not inherently errors | deferred |

## Live run

- User supplies their own storeCode and MCP Token locally; no personal account queries or write calls.
- `tools/list` via the built-in transport (`doctor/transport.py`) — tool list; failure -> UNKNOWN.
- `query-meals(storeCode, orderType=1, beType=1)`.
- `list-nutrition-foods()`.
- `calculate-price(storeCode, orderType=1, beType=1, items=[{productCode: menuSku, quantity: 1}])`.
- Optional `query-meal-detail(storeCode, orderType=1, beType=1, code: menuSku)`.
- Save Markdown and JSON findings **only**, never raw real-account output.

## Gate 0 / Gate 1 / Gate 2

0. Local fixtures and adversarial cases: 9 unit tests, 0 credential dependencies, no claims of live testing.
1. Run read-only live scan with personally authorized token, attach redacted evidence plus SHA/date/tool version; verify if code→productCode mapping, zero-price handling, and nutrition parsing are actually accurate.
2. Package as a small Skill, make a clean README, offline demo GIF/HTML, GitHub Actions offline tests, submit only after a verified real-MCP call.

## Similar projects

- https://github.com/modelcontextprotocol/inspector
- https://github.com/xm6661885/mcd-cli
- https://github.com/alexliuxiaoting/mealpr
- https://github.com/Monekey/mcd-meal-optimizer

## Evidence sources

- https://github.com/M-China/mcd-mcp-server/issues/16
- https://github.com/M-China/mcd-mcp-server/issues/11
- https://github.com/M-China/mcd-mcp-server/issues/9
- https://github.com/M-China/mcd-mcp-server/issues/13
- https://github.com/M-China/mcd-developer-innovation-challenge/blob/main/activityGuidelines.md

## Non-goals

- Scanning security vulnerabilities
- Testing payment/order creation
- Stress testing or exceeding API limits
- Predicting all store menu options or nutritional information from names
- Calling other agents without their authorization
- Publishing any Token, account content or unredacted personal logs
