"""Shopping-basket comparison: which store is cheapest *for what you actually want*.

Key finding that motivates this module (measured 2026-10-12, 3 stores / 91 common
SKUs): price differences are **item-level, not store-level**. Some items are
cheaper at store A, others at store B, so no store is universally cheapest and a
"best value store ranking" cannot be produced honestly.

The honest shape is therefore the reverse question — given a basket, which store
wins *this time* — plus a per-item cheapest map so the trade-off is visible.
"""
from __future__ import annotations


def _fmt(cents):
    return '未知' if cents is None else f'{cents / 100:.2f}'


_CITY_PREFIXES = ('上海', '北京', '广州', '深圳', '杭州', '南京', '成都', '武汉', '西安', '重庆', '苏州', '天津')


def _short(name: str) -> str:
    """Trim the official store name for table columns; full names stay in totals."""
    s = str(name)
    if s.startswith('麦当劳'):
        s = s[3:]
    for city in _CITY_PREFIXES:
        if s.startswith(city):
            s = s[len(city):]
            break
    return (s or str(name))[:12]


def build_basket_table(report: dict, requested: list | None = None) -> dict | None:
    """Build the basket comparison from a `multi.compare()` report.

    `requested` is a list of SKU codes. When omitted we fall back to the items
    with the largest cross-store spread, because those are the ones where the
    choice of store actually matters.
    """
    stores = {str(s['storeCode']): s.get('storeName') or str(s['storeCode'])
              for s in report.get('stores', [])}
    table = {r['sku']: r for r in (report.get('menu_price_table') or [])}
    if not table or not stores:
        return None

    if requested:
        picks = [c for c in requested if c in table]
        missing = [c for c in requested if c not in table]
    else:
        picks = [r['sku'] for r in (report.get('price_spread') or [])[:3]]
        missing = []

    if not picks:
        return None

    totals = {sid: 0 for sid in stores}
    per_item = []
    for sku in picks:
        row = table[sku]
        by_store = row['by_store']
        best = min(by_store.values())
        winners = sorted(sid for sid, v in by_store.items() if v == best)
        for sid in stores:
            totals[sid] += by_store[sid]
        per_item.append({
            'sku': sku,
            'name': row['name'],
            'by_store': by_store,
            'cheapest_cents': best,
            'dearest_cents': max(by_store.values()),
            'winners': winners,
            'spread_cents': row['spread_cents'],
        })

    best_total = min(totals.values())
    worst_total = max(totals.values())
    totals_rows = [{'store_code': sid, 'store_name': stores[sid], 'total_cents': cents,
                    'is_winner': cents == best_total} for sid, cents in totals.items()]
    totals_rows.sort(key=lambda r: r['total_cents'])

    # Buying each item at its own cheapest store is the theoretical floor.
    split_total = sum(item['cheapest_cents'] for item in per_item)

    return {
        'split_total_cents': split_total,
        'split_saving_cents': best_total - split_total,
        'picks': [{'sku': p, 'name': table[p]['name']} for p in picks],
        'missing_skus': missing,
        'per_item': per_item,
        'totals': totals_rows,
        'best_total_cents': best_total,
        'worst_total_cents': worst_total,
        'saving_cents': worst_total - best_total,
        'store_names': stores,
        'no_universal_winner': len({w for item in per_item for w in item['winners']}) > 1,
        'summary': (report.get('price_summary') or {}),
    }


def markdown(basket: dict) -> str:
    stores = basket['store_names']
    codes = list(stores.keys())
    out = ['', '### 3.1 指定购物篮：去哪家店买更便宜', '',
           '> 价差是**商品级**的，不是门店级的。下面这张表只对这个购物篮成立；',
           '> 换一个篮子，赢家完全可能是另一家店。**不存在"全面最便宜"的门店。**', '']

    if basket['missing_skus']:
        out += [f"- 注意：{len(basket['missing_skus'])} 个 SKU 不在所有门店的共同菜单内，已跳过："
                f"`{', '.join(basket['missing_skus'])}`", '']

    out += ['| 商品 | ' + ' | '.join(_short(stores[c]) for c in codes) + ' | 最便宜 | 价差 |',
            '|---|' + '---:|' * len(codes) + '---|---:|']
    for item in basket['per_item']:
        cells = []
        for c in codes:
            v = item['by_store'][c]
            mark = '**' if v == item['cheapest_cents'] else ''
            cells.append(f"{mark}{_fmt(v)}{mark}")
        winners = ' / '.join(_short(stores[w]) for w in item['winners'])
        out.append(f"| {item['name']} | " + ' | '.join(cells) +
                   f" | {winners} | {_fmt(item['spread_cents'])} |")

    out += ['', '**合计**', '', '| 门店 | 合计（元） |', '|---|---:|']
    for row in basket['totals']:
        flag = ' 🥇' if row['is_winner'] else ''
        out.append(f"| {row['store_name']}{flag} | {_fmt(row['total_cents'])} |")

    winner_names = ' / '.join(r['store_name'] for r in basket['totals'] if r['is_winner'])
    out += ['', f"本购物篮最划算：**{winner_names}**，"
                f"合计 {_fmt(basket['best_total_cents'])} 元，"
                f"比最贵的店省 {_fmt(basket['saving_cents'])} 元。", '']

    if basket['saving_cents'] == 0:
        out += ['> 这个篮子在各店总价相同——换一个包含有价差商品的篮子才会出现差异。', '']

    # Theoretical floor: every item bought at its own cheapest store.
    if basket['split_saving_cents'] > 0:
        out += [f"> 理论下限：每件都去它最便宜的店买是 {_fmt(basket['split_total_cents'])} 元，"
                f"比在一家店买齐再省 {_fmt(basket['split_saving_cents'])} 元。"
                f"代价是要跑多家店，通常不值得。", '']

    summary = basket['summary']
    if summary:
        out += ['### 3.3 跨店价格总览（观察）', '',
                f"- 三店共同且都有正价的商品：{summary.get('common_priced', 0)} 个",
                f"- 价格完全一致：{summary.get('identical', 0)} 个",
                f"- 存在价差：{summary.get('differing', 0)} 个"
                f"（最大价差 {_fmt(summary.get('max_spread_cents', 0))} 元）", '']

    out += ['### 3.2 购物篮部分的解释', '',
            '- 以上价格均来自 `query-meals` 的菜单展示价，同一采样窗口、同一渠道。',
            '- 门店定价不同属于正常商业行为，**不作为 MCP 故障**。',
            '- 未计入优惠券、配送费与距离成本；更便宜不等于更划算。',
            '- 菜单价只代表本次查询时该店列出该商品；供应时段变化后可能不可买。', '']
    return '\n'.join(out)
