"""Mock MCP server: replays fixtures and injects controlled faults.

Responses are wrapped in the *same* envelope the real service uses — glossary
first, then "## Original Response" — so every scenario exercises the real
unwrap() path instead of a convenient shortcut.

No token and no network are needed to reproduce any case.
"""
from __future__ import annotations

import json
from pathlib import Path

from doctor.core import READ_ONLY_TOOLS

# Side-effect tools. Taken from the official tools/list, by semantics, not by name prefix.
WRITE_TOOLS = frozenset({
    'create-order', 'cancel-order', 'mall-create-order', 'party-order-create',
    'draw-lottery', 'auto-bind-coupons', 'delivery-create-address',
})


def envelope(business: dict) -> str:
    """Wrap a business payload the way the real endpoint does.

    The glossary deliberately contains a `{code}` placeholder: that is what
    breaks naive brace matching in production, and the mock must reproduce it.
    """
    head = ("# API Response Information\n\n"
            "Below is the response from an API call.\n\n"
            "## Response Structure\n\n"
            "> Content-Type: application/json\n\n"
            "- **data.meals.{code}.currentPrice**: 当前价格\n"
            "- **data.meals.{code}.name**: 餐品名称\n\n")
    return head + "## Original Response\n\n" + json.dumps(business, ensure_ascii=False)


class MockMcpServer:
    def __init__(self, scenario: dict):
        self.scenario = scenario
        self.mocks = scenario.get('mocks') or {}
        self.calls: list[dict] = []
        self.blocked: list[dict] = []
        # When True, write tools are refused before reaching the "server".
        # This is the only mechanism that can actually prevent an unauthorized
        # call; a post-hoc check can only report it after the fact.
        self.guard_writes = False

    # -- same public surface as doctor.transport.McdMcpTransport -------------
    def list_tools(self):
        names = ['query-nearby-stores', 'query-meals', 'query-meal-detail',
                 'calculate-price', 'list-nutrition-foods', 'now-time-info',
                 'create-order', 'cancel-order', 'auto-bind-coupons']
        return [{'name': n, 'inputSchema': {'type': 'object'}} for n in names]

    def call_tool(self, name: str, arguments: dict | None = None) -> str:
        arguments = dict(arguments or {})
        if self.guard_writes and name in WRITE_TOOLS:
            # Recorded as attempted but blocked: it never reached the "server".
            self.calls.append({'tool': name, 'arguments': arguments, 'blocked': True})
            self.blocked.append({'tool': name, 'arguments': arguments,
                                 'reason': 'Doctor 只读守卫拒绝写入类调用'})
            return json.dumps({'isError': True,
                               'message': f'Doctor guard blocked write tool: {name}'},
                              ensure_ascii=False)
        self.calls.append({'tool': name, 'arguments': arguments, 'blocked': False})
        payload = self.mocks.get(name)
        if payload is None:
            return json.dumps({'isError': True,
                               'message': f'mock: 未定义的工具响应 {name}'}, ensure_ascii=False)
        return envelope(payload)


def load_scenarios(path: str | Path | None = None) -> list[dict]:
    p = Path(path) if path else Path(__file__).resolve().parents[1] / 'fixtures' / 'agent' / 'scenarios.json'
    return json.loads(p.read_text(encoding='utf-8'))['scenarios']
