# McD MCP Doctor — multi-store business-data consistency diagnostics

> Independent, unofficial developer tool. **Live-verified 2026-10-13** against the official MCP endpoint
> (3 Shanghai stores, 21 read-only calls). Prototype scope: this is a developer diagnostic, not a security audit.

A read-only diagnostic tool for McDonald's China MCP integrations. It checks whether the data a store returns
is **internally consistent** — not whether a JSON-RPC connection works, and **not** which store is cheaper.

## The distinction this tool is built on

| | Phenomenon | Verdict |
|---|---|---|
| **A. Differences between stores** | Store A sells at ¥25, store B at ¥27 | Normal pricing / ranging. **INFO only — never a fault.** |
| **B. Inconsistency within one store** | The menu lists a product with a valid price, but passing its code to the pricing tool yields an unexplained ¥0 quote or an empty product list | Broken data path. **WARN / FAIL — this is what the tool diagnoses.** |

A price-comparison tool blurs these together. This one keeps them apart: **A is recorded as an observation,
B is the diagnosis.** The report is structured accordingly (§1 diagnosis, §2 observation).

**The core claim: a successful JSON-RPC call is not the same as business success, and business success is not
the same as parseable data.** All three layers have to be checked separately — and two of the three findings
below were discovered because the checks were actually run.

## What live verification found (2026-10-13)

Running against real traffic exposed three parsing traps that no offline fixture would have caught. Each one is
covered by a regression test in `tests/test_core.py::RealResponseRegression`.

| # | Trap | Symptom | Fix |
|---|---|---|---|
| 1 | Response glossary embeds `{code}` placeholders | `data.meals.{code}.currentPrice` breaks brace matching, so `json.loads` fails and the menu looks empty → `MCD-101 = UNKNOWN` | `core.scan_balanced_json`: try every `{` as a start, walk forward with a string-state machine |
| 2 | Nutrition payload is a custom text format, not JSON | `[160]{productName,...}:` followed by CSV rows. Markdown-table parsing yields **0** names | `core.parse_official_csv`: read the header, then map rows by column index |
| 3 | Brace-scan can latch onto the glossary | A stray `{...}` in the description decodes first | Prefer the `Original Response` marker, and require the result to be a `dict` |

Before the fix, every core check returned `UNKNOWN`. After it: 115 / 121 / 105 SKUs parsed across three stores,
and nutrition name coverage went from `0/115` to `19/115`.

**The first patient of a tool that checks "can this data be parsed correctly" was the tool itself.**

## Start: offline, no token and no dependency

Requires Python 3.10+.

```bash
python -m doctor --fixture fixtures/sample.json --output report.md
python -m unittest discover -s tests -v
```

Open `report.md` and `report.json` for the test findings. The fixture uses **fictional products, fictional codes and a deliberately abnormal price of 0**, to illustrate WARN vs PASS/FAIL/UNKNOWN. It is *not* a captured official response.

## Live check — verified 2026-10-13, zero external dependencies

Live mode talks to the MCP endpoint directly over streamable HTTP via `doctor/transport.py`.
**No external CLI, no third-party packages** — Python 3.10+ standard library only.

Get your personal Token at `https://open.mcd.cn/mcp`, then:

```bash
export MCD_MCP_TOKEN='YOUR_PRIVATE_TOKEN' # macOS/Linux, never commit it
python -m doctor --live --store YOUR_STORE_CODE --output report.md
```

For Windows PowerShell use `$env:MCD_MCP_TOKEN = 'YOUR_PRIVATE_TOKEN'`. Unset it locally after use. The single-store
mode is a smoke test: `tools/list`, 3 read-only tool calls plus one optional read-only detail call. Store code is
provided explicitly so nothing reads your accounts or addresses. The first SKU with a positive menu price is
selected unless `--sku` is given.

Optional known combo inspection:

```bash
python -m doctor --live --store YOUR_STORE_CODE --detail-code YOUR_MENU_CODE --output report.md
```

**Check the code and run it locally with your own Token before claiming production compatibility.**

## Why this is different

Generic MCP Inspectors and `mcd-cli` validate connectivity and tool parameters. This project adds: business success vs transport success, meal-code-to-price-code verification, positive-price/zero-price anomaly alerts, price-unit awareness, and nutritional name coverage as *an observation only*. A PASS does not prove accuracy, a WARN is not a server bug, and UNKNOWN never silently passes.

## MVP limits

- No source analysis or vulnerability scanning
- No guarantees about financial price invariants, nutritional matching, or stock levels
- No personal data, orders, vouchers, points, or account queries
- No mutation, no automatic retries, no stress testing, no guessing missing fields
- Tool-list/schema checks run against the live `tools/list` snapshot when reachable; if unavailable they stay UNKNOWN
- Nutrition coverage is an **observation of exact name matches**, not evidence that nutrient values are correct
- Cross-store price comparison uses menu display prices only: no coupons, delivery fees, or distance

## Proposed follow-up (not implemented)

- Voucher → menu code mapping check (`query-store-coupons` returns `productCode`s that are absent from the
  menu SKU set; a first probe showed `calculate-price` returning `price=0` with an empty `productList` for one
  such code, which the current logic would rate PASS. Deferred: the sample was 2 vouchers on a new account.)
- Optional redacted traceId/latency and CI report annotations
- One-click HTML report / public demo with explicitly fictional inputs
- Repeated snapshot diffs to measure how fast menus actually change

## Contest submission checklist (after live validation)

Read the latest official rules: https://github.com/M-China/mcd-developer-innovation-challenge/blob/main/activityGuidelines.md

- README.md
- MCP_INTEGRATION.md with *actual* live evidence
- mcp-config.example.json, environment variable only
- Official CONTEST_DECLARATION.md, verbatim, review it before submission
- Source code & documented license
- SKILL.md
- Optional workbuddy.md only if WorkBuddy was truly used

Do not publish personal tokens or full member-account responses. Not affiliated with McDonald's.

## Nearby-store experiment — updated 2026-10-12

The multi-store mode checks **within-store product-code-to-price behavior** and reports **cross-store observations** separately. Differences in menu availability or price between stores are not called provider bugs.

**Demo without token (entirely simulated, not real store prices):**

```bash
python -m doctor --fixture fixtures/nearby-stores.simulated.json --output comparison.md
python -m unittest discover -s tests -v
```

**Live mode (verified 2026-10-13; own Token required, `mcd-cli` not required):**

```bash
# Prefer explicitly selected official store codes to control the sample
python -m doctor --live --stores STORE_A,STORE_B,STORE_C --samples 5 --output nearby-report.md

# Alternatively discover candidates by city and district/landmark
python -m doctor --live --city "某城市" --keyword "某商圈" --limit 3 --samples 5 --output nearby-report.md

# Optional: choose up to five common SKUs with known codes
python -m doctor --live --stores STORE_A,STORE_B,STORE_C --skus SKU_1,SKU_2 --output nearby-report.md

# Save the raw snapshot so it can be replayed offline with --fixture
python -m doctor --live --city "某城市" --keyword "某商圈" --save-snapshot fixtures/my-run.json
```

A captured live snapshot is committed at `fixtures/live-2026-10-13.json` — replay it with
`python -m doctor --fixture fixtures/live-2026-10-13.json` to reproduce the report without a Token.

## Shopping-basket comparison: there is no universally cheapest store

Measured across 3 stores and 96 common positive-priced SKUs: **75 are priced identically, 21 differ.**
The differences are **item-level, not store-level** — so a "best value store ranking" cannot be produced honestly.

Two baskets, same three stores, opposite winners:

| Basket | Cheapest | Runner-up |
|---|---|---|
| 巨无霸 + 薯条 | 华旭 / 福州路 — **39.00** | 食品一店 41.00 |
| 锡兰红茶 + 麦香鸡 | **食品一店 — 27.00** | 华旭 / 福州路 28.50 |

So the feature is the reverse question: *given what you want, which store wins this time.*

```bash
python -m doctor --fixture fixtures/live-2026-10-13.json --basket 1100,4810 --output basket.md
```

The report also prints the theoretical floor (every item bought at its own cheapest store), which is usually
lower than buying everything in one place — and almost never worth the extra trips. Caveats are stated in the
report itself: no coupons, no delivery fees, no distance cost. **Cheaper is not the same as better value.**

Use a **mainland-China city or store codes** (the official MCP is mainland-focused). `--city` discovery picks the first records returned; this is not guaranteed to be geographical-nearest ordering. All live requests are limited to read-only `query-nearby-stores`, `query-meals`, `list-nutrition-foods`, `calculate-price` and `tools/list`. The code does **not** create orders, redeem points, claim coupons or read private account data.

Sampling: one menu call per store, one nutrition call per batch, and at most five quote calls per store (19 calls with three stores plus discovery and tool listing). For a short comparison, keep orderType=1 and beType=1, and finish within the same meal period. If the common positive-price SKU set is empty, the report displays an explicit unknown rather than inventing a match. Reports store only selected item names, codes and findings, not raw personal account responses.

Read [VALIDATION_PLAN.md](VALIDATION_PLAN.md) for the full causal controls, rejection criteria, and interpretation.
