"""Nearby-store comparison: observation, not proof of a provider bug.

No network calls happen in this module. Use the main CLI for read-only sampling.
"""
from __future__ import annotations
from decimal import Decimal, InvalidOperation
from itertools import combinations
from datetime import datetime, timezone
from .core import unwrap, business_ok, money_cents, diagnose, named_foods


def extract_menu(raw):
    d = unwrap(raw)
    data = d.get('data') if isinstance(d.get('data'), dict) else {}
    meals = data.get('meals')
    if not isinstance(meals, dict):
        return {}
    out = {}
    for key, val in meals.items():
        if not isinstance(val, dict):
            continue
        code = str(val.get('code') or key)
        if code:
            out[code] = {'name': str(val.get('name') or ''),
                         'menu_price_cents': money_cents(val.get('currentPrice'))}
    return out


def extract_stores(raw):
    """Best-effort extraction of official nearby-store list, no geolocation guessing."""
    d = unwrap(raw)
    data = d.get('data')
    if isinstance(data, dict):
        for key in ('stores','storeList','list','items'):
            if isinstance(data.get(key), list):
                data = data[key]
                break
    if not isinstance(data, list):
        return []
    stores, seen = [], set()
    for entry in data:
        if not isinstance(entry, dict):
            continue
        code = str(entry.get('storeCode') or entry.get('code') or '').strip()
        if not code or code in seen:
            continue
        seen.add(code)
        stores.append({'storeCode':code,'storeName':str(entry.get('storeName') or entry.get('name') or code)})
    return stores


def shared_skus(menus, requested=None, sample_count=5):
    if not menus: return []
    common = set.intersection(*(set(m) for m in menus.values()))
    common = {code for code in common if all(
        (menus[s][code]['menu_price_cents'] or 0)>0 for s in menus)}
    if requested:
        return [str(c) for c in requested if str(c) in common][:sample_count]
    return sorted(common)[:sample_count]


def money_price(raw):
    d = unwrap(raw)
    if business_ok(raw) is False:
        return None
    data = d.get('data') if isinstance(d.get('data'), dict) else {}
    val = data.get('price')
    try:
        value = Decimal(str(val))
        return int(value) if value.is_finite() and value>=0 and value==int(value) else None
    except (TypeError, ValueError, InvalidOperation):
        return None


def compare(snapshot):
    """Use snapshots from one consistent time window and channel; no false price equality assertions."""
    stores=snapshot.get('stores') or []
    metas={str(s['storeCode']):s for s in stores}
    menus={c:extract_menu(s.get('menu')) for c,s in metas.items()}
    selected=shared_skus(menus,snapshot.get('requested_skus'),snapshot.get('sample_count',5))
    global_nutrition=snapshot.get('nutrition')
    names,_=named_foods(global_nutrition) if global_nutrition is not None else (set(),'unknown')
    menu_sets=[set(m) for m in menus.values()]
    union=set.union(*menu_sets) if menu_sets else set()
    intersection=set.intersection(*menu_sets) if menu_sets else set()
    pairs=[]
    for a,b in combinations(menus,2):
        both=set(menus[a])&set(menus[b]); either=set(menus[a])|set(menus[b])
        pairs.append({'a':a,'b':b,'shared_skus':len(both),'jaccard':round(len(both)/len(either),4) if either else None})
    # Full cross-store price table over every common SKU, not just the sample.
    # This is what makes "which store is cheapest for what I want" answerable.
    price_table=[]
    for code in sorted(intersection):
        prices={};name='';ok=True
        for sid in metas:
            item=menus[sid].get(code)
            if not item or item['menu_price_cents'] is None:
                ok=False;break
            prices[sid]=item['menu_price_cents']
            name=name or item['name']
        if ok and len(prices)==len(metas):
            vals=list(prices.values())
            price_table.append({'sku':code,'name':name,'by_store':prices,
                                'spread_cents':max(vals)-min(vals)})
    spread=[r for r in price_table if r['spread_cents']>0]
    spread.sort(key=lambda r:(-r['spread_cents'],r['sku']))
    common_priced=len(price_table)
    identical=common_priced-len(spread)

    rows=[]
    for code in selected:
        per=[]
        for sid,store in metas.items():
            item=menus[sid][code]
            prices=store.get('prices') or {}
            raw=prices.get(code)
            mc=item['menu_price_cents']
            quote=money_price(raw) if raw is not None else None
            quote_data=unwrap(raw).get('data') if raw is not None else None
            quote_data=quote_data if isinstance(quote_data,dict) else {}
            products=quote_data.get('productList')
            # Zero alone is not a provider bug: no response != empty vs 0.
            status='UNKNOWN' if quote is None else ('WARN' if quote==0 and mc and products==[] else 'PASS')
            per.append({'store_code':sid,'store_name':store.get('storeName',sid),
                        'menu_price_cents':mc,'quoted_cents':quote,'status':status,
                        'name':item['name'],
                        'comment':'报价可能包含自动优惠，不能要求与菜单价相等' if status=='PASS' else
                                  '核价0且 productList 为空，需要检查商品映射、时段和业务响应' if status=='WARN' else '未返回可解析的应付价'})
        amounts=[x['menu_price_cents'] for x in per if x['menu_price_cents'] is not None]
        rows.append({'sku':code,'name':per[0]['name'] if per else '',
                     'menu_spread_cents':max(amounts)-min(amounts) if amounts else None,
                     'by_store':per})
    reports=[]
    for sid,store in metas.items():
        prices=store.get('prices') or {}
        # Existing single-store checks use first priced common SKU; additional quotes are in comparison.
        first=selected[0] if selected else None
        diagnostic=diagnose({'mode':snapshot.get('mode','multi-store'),
                             'calls':store.get('calls',[]),
                             'tools':snapshot.get('tools'),
                             'menu':store.get('menu'),
                             'nutrition':global_nutrition,
                             'product_code':first,
                             'price':prices.get(first) if first else None})
        reports.append({'storeCode':sid,'storeName':store.get('storeName',sid),
                        'sku_count':len(menus[sid]),
                        'nutrition_exact_match_count':len({x['name'] for x in menus[sid].values() if x['name'] in names}),
                        'nutrition_menu_named_count':len({x['name'] for x in menus[sid].values() if x['name']}),
                        'diagnostics':diagnostic['findings']})
    return {'meta':{'mode':snapshot.get('mode','fixture'),
                    'scope':'read-only multi-store, same channel and observation window',
                    'sample_count':len(selected),'num_stores':len(metas),
                    'recorded_at_utc':snapshot.get('recorded_at_utc'),
                    'business_channel':snapshot.get('business_channel',{'beType':1,'orderType':1}),
                    'notes':'Different stores may legitimately have different menus, prices, discounts, or availability.'},
            'intersection_count':len(intersection),'union_count':len(union),
            'pair_overlap':pairs,'sampled_skus':selected,'sku_rows':rows,'stores':reports,
            'menu_price_table':price_table,'price_spread':spread,
            'price_summary':{'common_priced':common_priced,'identical':identical,
                             'differing':len(spread),
                             'max_spread_cents':spread[0]['spread_cents'] if spread else 0}}


def markdown(report):
    meta=report['meta']
    out=['# McD MCP Doctor｜附近门店对照报告','',
         '> 仅用于开发者数据质量诊断。门店价格不同 ≠ MCP 错误；PASS ≠ 全面正确；UNKNOWN ≠ 通过。',
         '',f"- 数据模式：`{meta['mode']}`",
         f"- 门店数：{meta['num_stores']}；共同抽样 SKU：{meta['sample_count']}",
         f"- 采样时间（UTC）：{meta.get('recorded_at_utc') or '未记录'}",
         f"- 统一条件：`{meta['business_channel']}`，未主动使用优惠券",'',
         '## 菜单整体情况','',
         f"- 三店交集 SKU：{report['intersection_count']}；三店并集 SKU：{report['union_count']}",
         '- 交集只代表在本次查询中均被列出，不表示未来时段仍可供应。','',
         '## SKU 跨店配对核价','',
         '| SKU | 商品 | 门店 | 菜单价（元） | 官方应付（元） | 诊断 |',
         '|---|---|---|---:|---:|---|']
    def fmt(value): return '未知' if value is None else f'{value/100:.2f}'
    for r in report['sku_rows']:
        for s in r['by_store']:
            out.append(f"| {r['sku']} | {r['name']} | {s['store_name']} ({s['store_code']}) | {fmt(s['menu_price_cents'])} | {fmt(s['quoted_cents'])} | {s['status']} |")
    if not report['sku_rows']:
        out.append('| — | 未找到所有店均有、菜单正价且可核价的 SKU | — | — | — | UNKNOWN |')
    out+=['','## 各门店诊断摘要','']
    for s in report['stores']:
        out.append(f"### {s['storeName']} ({s['storeCode']})")
        out.append(f"- 菜单 SKU：{s['sku_count']}；菜单名称精确匹配营养库：{s['nutrition_exact_match_count']}/{s['nutrition_menu_named_count']}（下界）")
        for f in s['diagnostics']:
            if f['severity'] in ('FAIL','WARN','UNKNOWN'):
                out.append(f"- **{f['severity']} {f['id']}**：{f['summary']}。{f['detail']}")
        out.append('')
    out+=['## 结果解释','',
          '- 同一商品跨门店价格不同，只作为观察信息；不自动判定为 Bug。',
          '- 自动优惠可能使官方核价低于菜单展示价；不要求两者相等。',
          '- 正价商品核价为0、商品列表为空是可疑信号；应首先检查入参与供应条件，再复测。',
          '- 商品营养“精确名称匹配率”是下界，不证明营养真实性或套餐营养完整。',
          '- 样例数据属于模拟；真实模式仍需你自己的 Token 和本机调用。',
          '- 本项目不会创建订单、领券、花积分、读取个人地址或历史订单。','']
    return '\n'.join(out)
