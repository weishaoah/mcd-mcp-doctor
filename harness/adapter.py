"""Agent adapter: one interface, pluggable implementations.

The DemoAgent below is DETERMINISTIC and NOT an LLM. Its behaviour is a single
coherent policy -- "trust the first price seen, do not verify the quote" -- that
produces both correct and wrong answers depending on the data. Failures are
never hardcoded per scenario; they fall out of the policy meeting the data.

Its results must NOT be generalised to real LLM agents.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from doctor.core import unwrap, business_ok, money_cents, named_foods
from .mock_mcp import MockMcpServer


@dataclass
class AgentRun:
    answer: str
    tool_calls: list = field(default_factory=list)
    blocked: list = field(default_factory=list)
    state: dict = field(default_factory=dict)


class AgentAdapter:
    """Contract for anything under test. Real agents implement `run`."""

    def run(self, scenario: dict, mcp: MockMcpServer) -> AgentRun:
        raise NotImplementedError


class DemoAgent(AgentAdapter):
    """DEMO ONLY — deterministic, no model, no inference.

    Policy (applies uniformly to every scenario):
      1. read the menu, take the item's listed price in yuan
      2. call calculate-price, but only use it to decide the scale, never to
         override: the menu price stays the answer
      3. if the item is not on the menu, fall back to a built-in catalogue
      4. if nutrition is missing, fall back to a built-in estimate
      5. treat whatever query-meal-detail returns as the complete choice pool
      6. when the user only asked to browse, still "confirm orderability"

    Every one of those is a plausible shortcut, and none of them is written
    with a particular scenario in mind.
    """

    # Built-in "remembered" values — the fabrication source when MCP data is
    # missing. This is the exact failure the harness is meant to surface.
    CATALOG_PRICE = {'9900014092': 9.9}
    CATALOG_KCAL = {'龙焰鸡腿堡': 520}

    def run(self, scenario: dict, mcp: MockMcpServer) -> AgentRun:
        store = scenario.get('store')
        sku = scenario.get('sku')
        intent = scenario.get('intent')
        st = {'store': store, 'sku': sku, 'intent': intent}
        parts = []

        menu_raw = mcp.call_tool('query-meals', {'storeCode': store, 'orderType': 1, 'beType': 1})
        menu = unwrap(menu_raw)
        meals = ((menu.get('data') or {}).get('meals') if isinstance(menu.get('data'), dict) else {}) or {}
        st['menu'] = meals
        item = meals.get(sku) if sku else None
        st['item_name'] = (item or {}).get('name') if isinstance(item, dict) else None
        st['menu_price_yuan'] = self._as_float((item or {}).get('currentPrice')) if isinstance(item, dict) else None
        st['sku_on_menu'] = item is not None

        if intent == 'browse':
            names = [v.get('name') for v in meals.values() if isinstance(v, dict)]
            st['listed_items'] = names
            parts.append(f"门店 {store} 在售：{'、'.join(names)}。")
            # Naive shortcut: "confirm it can actually be ordered" before the user asked.
            mcp.call_tool('create-order', {'storeCode': store, 'items': []})
            parts.append('已为你确认可下单。')
            return AgentRun(' '.join(parts), mcp.calls, mcp.blocked, st)

        if intent == 'combo':
            raw = mcp.call_tool('query-meal-detail', {'storeCode': store, 'orderType': 1, 'beType': 1, 'code': sku})
            d = unwrap(raw)
            data = d.get('data') if isinstance(d.get('data'), dict) else {}
            rounds = data.get('rounds') or []
            st['combo_rounds'] = len(rounds)
            st['combo_choice_counts'] = [len(r.get('choices') or []) for r in rounds if isinstance(r, dict)]
            picks = [c.get('name') for r in rounds if isinstance(r, dict)
                     for c in (r.get('choices') or []) if isinstance(c, dict)]
            st['combo_picks'] = picks
            parts.append(f"「{data.get('name') or st['item_name'] or sku}」可选：{'、'.join(picks)}。")
            return AgentRun(' '.join(parts), mcp.calls, mcp.blocked, st)

        # ---- quote / nutrition -------------------------------------------------
        if not item:
            # Not on this store's menu: fall back to remembered price (fabrication).
            fallback = self.CATALOG_PRICE.get(sku)
            st['used_catalog_price'] = fallback is not None
            if fallback is not None:
                parts.append(f"本店「{sku}」约 {fallback} 元。")
            else:
                parts.append(f"本店「{sku}」约 12 元（参考价）。")
                st['used_catalog_price'] = True
            return AgentRun(' '.join(parts), mcp.calls, mcp.blocked, st)

        quote_raw = mcp.call_tool('calculate-price', {
            'storeCode': store, 'orderType': 1, 'beType': 1,
            'items': [{'productCode': sku, 'quantity': 1}]})
        q = unwrap(quote_raw)
        qdata = q.get('data') if isinstance(q.get('data'), dict) else {}
        st['quote_raw'] = qdata.get('price')
        st['quote_ok'] = business_ok(quote_raw)
        st['quote_product_list'] = qdata.get('productList') or []
        st['quote_business_ok'] = business_ok(quote_raw) is not False

        name = st['item_name'] or sku
        if st['menu_price_yuan'] is not None:
            # Anchor exists: report the menu price (the shortcut that breaks C08).
            st['reported_yuan'] = st['menu_price_yuan']
            st['anchor'] = 'menu'
            parts.append(f"{name} {st['menu_price_yuan']:.2f} 元。")
        else:
            # No anchor, quote is used raw — the unit trap (C04).
            st['reported_yuan'] = st['quote_raw']
            st['anchor'] = 'quote-raw'
            parts.append(f"{name} {st['quote_raw']} 元。")

        if intent == 'nutrition':
            nut_raw = mcp.call_tool('list-nutrition-foods', {})
            foods, _kind = named_foods(nut_raw)
            st['nutrition_hit'] = name in foods
            if name in foods:
                st['kcal'] = 'from-mcp'
                parts.append(f"{name} 的热量可在营养表中查到。")
            else:
                est = self.CATALOG_KCAL.get(name, 450)
                st['kcal'] = est
                st['kcal_source'] = 'catalog'
                parts.append(f"{name} 约 {est} 大卡。")
        return AgentRun(' '.join(parts), mcp.calls, mcp.blocked, st)

    @staticmethod
    def _as_float(v):
        try:
            return float(str(v))
        except (TypeError, ValueError):
            return None


class RealLLMAgent(AgentAdapter):
    """Placeholder for a real model-backed agent.

    Not implemented on purpose: fabricating model output would invalidate the
    whole experiment. Supply a key/base_url and implement `run`, or leave this
    skipped — the runner reports it as SKIPPED, never as a passing case.
    """

    def __init__(self, model: str, api_key: str | None = None, base_url: str | None = None):
        self.model = model
        self.api_key = api_key
        self.base_url = base_url

    def run(self, scenario: dict, mcp: MockMcpServer) -> AgentRun:
        raise NotImplementedError(
            "RealLLMAgent 未启用：需要模型密钥。不提供则不参与实验，该组标记为 SKIPPED，"
            "不以任何方式伪造模型输出。")
