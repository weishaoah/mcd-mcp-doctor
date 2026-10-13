---
name: mcd-mcp-doctor
description: Read-only McDonald's China MCP integration quality diagnostic, report generator, and business response verifier.
---
# McD MCP Doctor

For a user asking to diagnose McDonald's MCP integrations:
1. Confirm the user has authorization to use their personal MCP Token, never ask them to post it publicly.
2. Default to fixture mode. Run `python -m doctor --fixture fixtures/sample.json --output report.md`.
3. If the user explicitly requests live checking and supplies their own store code, run the read-only `--live --store CODE` mode using the environment variable `MCD_MCP_TOKEN`. Live mode talks to the MCP endpoint directly (built-in transport, standard library only) — no external CLI needs to be installed.
4. Never call order creation, cancellation, coupon collection, points redemption, address changes, lottery, or any other mutating MCP tool.
5. Distinguish FAIL (business error) vs WARN (suspicious) vs UNKNOWN (not checked), and include the scope and source in the report.
6. Redact credentials and personal data; do not publish raw real-account responses.
7. Do not assert menu-price and payable-price equality as coupons or promotions may change the actual amount.
