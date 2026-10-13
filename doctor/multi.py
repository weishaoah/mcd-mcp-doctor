"""Nearby-store comparison: observation, not proof of a provider bug.

No network calls happen in this module. Use the main CLI for read-only sampling.
"""
from __future__ import annotations
import re
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
    """Best-effort extraction of official nearby-store list, no geolocation guessing.

    Only fields the API actually returns are kept. The API has **no** store-type
    field (no type/tag/category), so no such label is invented here.
    `latitude`/`longitude` are always 0.0 in practice, so they are dropped rather
    than carried around as if they were usable coordinates.
    """
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
        stores.append({
            'storeCode':code,
            'storeName':str(entry.get('storeName') or entry.get('name') or code),
            # Real fields only. Used as observation labels, never as proof of a fault.
            'businessStartTime':entry.get('businessStartTime'),
            'businessEndTime':entry.get('businessEndTime'),
            'businessStatus':entry.get('businessStatus'),
            'distance':entry.get('distance'),
            'address':entry.get('address'),
        })
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


def _official_hhmm(value):
    """Parse 'HH:MM' from the store API into minutes since midnight; None if unusable."""
    if not isinstance(value, str):
        return None
    m = re.match(r'^\s*(\d{1,2}):(\d{2})\s*$', value)
    if not m:
        return None
    hh, mm = int(m.group(1)), int(m.group(2))
    if not (0 <= hh <= 24 and 0 <= mm < 60):
        return None
    return hh * 60 + mm


def check_store_status(store, sku_count, official_hhmm=None):
    """MCD-304: does the store's own status/hours agree with menu availability?

    This is a *within-store* consistency check (class B), unlike price gaps
    between stores (class A). Nothing here is inferred: when a field is absent
    the result is UNKNOWN, never a guess.
    """
    out = []
    status = store.get('businessStatus')
    start = _official_hhmm(store.get('businessStartTime'))
    end = _official_hhmm(store.get('businessEndTime'))

    # 304a — stated open/closed vs whether the menu actually has products.
    if status is None:
        out.append({'id':'MCD-304a','severity':'UNKNOWN','summary':'门店营业状态字段缺失',
                    'detail':'接口未返回 businessStatus，无法与菜单可得性核对。',
                    'tool':'query-nearby-stores + query-meals'})
    elif status is True and sku_count == 0:
        out.append({'id':'MCD-304a','severity':'WARN','summary':'门店标记为营业中，但菜单为空',
                    'detail':f'businessStatus=true 而 query-meals 解析出 0 个 SKU。'
                             f'需复核门店、供餐时段与渠道入参；先自查，不直接判定为服务端故障。',
                    'tool':'query-nearby-stores + query-meals'})
    elif status is False and sku_count > 0:
        out.append({'id':'MCD-304a','severity':'INFO','summary':'门店标记为休息中，但菜单仍有商品',
                    'detail':f'businessStatus=false 而菜单解析出 {sku_count} 个 SKU。'
                             f'可能是打烊前仍可下单，属观察信息。',
                    'tool':'query-nearby-stores + query-meals'})
    elif status is False and sku_count == 0:
        out.append({'id':'MCD-304a','severity':'INFO','summary':'门店休息中且菜单为空，两者一致',
                    'detail':'businessStatus=false 与菜单为空互相印证，未见不一致。',
                    'tool':'query-nearby-stores + query-meals'})
    else:
        out.append({'id':'MCD-304a','severity':'PASS','summary':'营业状态与菜单可得性一致',
                    'detail':f'businessStatus=true，菜单解析出 {sku_count} 个 SKU。',
                    'tool':'query-nearby-stores + query-meals'})

    # 304b — stated business hours vs stated open/closed flag, needs official time.
    if official_hhmm is None or start is None or end is None:
        out.append({'id':'MCD-304b','severity':'UNKNOWN','summary':'无法核对营业时段',
                    'detail':'缺少官方时间（now-time-info）或门店营业时段字段，跳过核对。',
                    'tool':'now-time-info + query-nearby-stores'})
    else:
        # Treat 00:00-23:59 and 00:00-00:00 as effectively all-day.
        all_day = (start == 0 and end >= 23 * 60 + 59) or (start == 0 and end == 0)
        within = all_day or (start <= official_hhmm <= end)
        if status is False and within:
            out.append({'id':'MCD-304b','severity':'WARN',
                        'summary':'营业时段内，但门店状态为休息中',
                        'detail':f'官方时间落在 {store.get("businessStartTime")}-'
                                 f'{store.get("businessEndTime")} 之内，'
                                 f'但 businessStatus=false。两个字段彼此不一致（同一接口内部）。',
                        'tool':'now-time-info + query-nearby-stores'})
        elif status is True and not within:
            out.append({'id':'MCD-304b','severity':'WARN',
                        'summary':'营业时段外，但门店状态为营业中',
                        'detail':f'官方时间不在 {store.get("businessStartTime")}-'
                                 f'{store.get("businessEndTime")} 之内，'
                                 f'但 businessStatus=true。两个字段彼此不一致（同一接口内部）。',
                        'tool':'now-time-info + query-nearby-stores'})
        else:
            out.append({'id':'MCD-304b','severity':'PASS','summary':'营业时段与状态标记一致',
                        'detail':f'官方时间 {official_hhmm//60:02d}:{official_hhmm%60:02d} 与 '
                                 f'{store.get("businessStartTime")}-{store.get("businessEndTime")}、'
                                 f'businessStatus={status} 三者一致。',
                        'tool':'now-time-info + query-nearby-stores'})
    return out


def nutrition_match_tiers(menu_names, food_names):
    """Split menu names into exact / partial / unmatched against the nutrition list.

    'Partial' means the name matches only after normalisation (stripping brand
    prefixes, parenthesised specs, whitespace). It is a *linkability* measure,
    never evidence about nutritional accuracy.
    """
    exact, partial, missing = set(), set(), set()
    norm_food = {re.sub(r'[\s（）()]+', '', f): f for f in food_names if f}
    for n in menu_names:
        if not n:
            continue
        if n in food_names:
            exact.add(n); continue
        k = re.sub(r'[\s（）()]+', '', n)
        if k in norm_food:
            exact.add(n); continue
        hit = None
        for fk, fv in norm_food.items():
            if len(k) >= 2 and (k in fk or fk in k):
                hit = fv; break
        if hit:
            partial.add(n)
        else:
            missing.add(n)
    return exact, partial, missing


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
    # Which SKUs appear in only some stores. Observation (class A): stores
    # legitimately stock different ranges, so this is never a fault verdict.
    presence={}
    for sid in metas:
        for code in menus[sid]:
            presence.setdefault(code,[]).append(sid)
    def _name_of(code):
        for sid in metas:
            item=menus[sid].get(code)
            if item and item.get('name'):
                return item['name']
        return code
    exclusive=[{'sku':c,'name':_name_of(c),'stores':s,'store_names':[metas[x].get('storeName',x) for x in s]}
               for c,s in presence.items() if len(s)<len(metas)]
    exclusive.sort(key=lambda r:(len(r['stores']),r['sku']))
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
    official_time=snapshot.get('official_time')
    official_hhmm=None
    if isinstance(official_time,dict):
        for key in ('datetime','formatted'):
            official_hhmm=_official_hhmm(str(official_time.get(key,''))[11:16])
            if official_hhmm is not None:
                break

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
        menu_names={x['name'] for x in menus[sid].values() if x['name']}
        exact,partial,missing=nutrition_match_tiers(menu_names,names)
        reports.append({'storeCode':sid,'storeName':store.get('storeName',sid),
                        'sku_count':len(menus[sid]),
                        'businessStartTime':store.get('businessStartTime'),
                        'businessEndTime':store.get('businessEndTime'),
                        'businessStatus':store.get('businessStatus'),
                        'distance':store.get('distance'),
                        'nutrition_exact_match_count':len({x['name'] for x in menus[sid].values() if x['name'] in names}),
                        'nutrition_menu_named_count':len(menu_names),
                        'nutrition_tiers':{'exact':len(exact),'partial':len(partial),'missing':len(missing),
                                           'partial_examples':sorted(partial)[:5],
                                           'missing_examples':sorted(missing)[:5]},
                        'status_checks':check_store_status(store,len(menus[sid]),official_hhmm),
                        'diagnostics':diagnostic['findings']})
    return {'meta':{'mode':snapshot.get('mode','fixture'),
                    'scope':'read-only multi-store, same channel and observation window',
                    'sample_count':len(selected),'num_stores':len(metas),
                    'recorded_at_utc':snapshot.get('recorded_at_utc'),
                    'business_channel':snapshot.get('business_channel',{'beType':1,'orderType':1}),
                    'notes':'Different stores may legitimately have different menus, prices, discounts, or availability.'},
            'intersection_count':len(intersection),'union_count':len(union),
            'coverage':{'exclusive_count':len(exclusive),'exclusive':exclusive},
            'official_time':official_time,
            'pair_overlap':pairs,'sampled_skus':selected,'sku_rows':rows,'stores':reports,
            'menu_price_table':price_table,'price_spread':spread,
            'price_summary':{'common_priced':common_priced,'identical':identical,
                             'differing':len(spread),
                             'max_spread_cents':spread[0]['spread_cents'] if spread else 0}}


def markdown(report):
    meta=report['meta']
    official=report.get('official_time') or {}
    official_str=official.get('formatted') or official.get('datetime') or '未取得（now-time-info）'
    out=['# McD MCP Doctor｜多门店业务数据一致性诊断','',
         '> 诊断对象是**同一家门店内部**各接口是否彼此对得上。',
         '> 门店与门店之间的差异属正常商业行为，记为观察，不判故障。',
         '> PASS ≠ 全面正确；UNKNOWN ≠ 通过。','',
         f"- 数据模式：`{meta['mode']}`",
         f"- 门店数：{meta['num_stores']}；共同抽样 SKU：{meta['sample_count']}",
         f"- **官方时间基准**：{official_str}",
         f"- 本机 UTC：{meta.get('recorded_at_utc') or '未记录'}",
         f"- 统一条件：`{meta['business_channel']}`，未主动使用优惠券",'',
         '',
         '## 一、诊断：同店数据自洽性（本项目结论）','',
         '> 只判断**同一家门店内部**各接口是否彼此对得上。',
         '> 门店与门店之间的差异不在这里判故障，见第二节。','',
         '### 1.1 同店核价比对：菜单价 vs 官方核价','',
         '这是本项目的核心判据：菜单说有货且价格有效，把编码送进核价接口后是否还能成立。','',
         '| SKU | 商品 | 门店 | 菜单价（元） | 官方核价（元） | 判定 |',
         '|---|---|---|---:|---:|---|']
    def fmt(value): return '未知' if value is None else f'{value/100:.2f}'
    for r in report['sku_rows']:
        for s in r['by_store']:
            out.append(f"| {r['sku']} | {r['name']} | {s['store_name']} ({s['store_code']}) | {fmt(s['menu_price_cents'])} | {fmt(s['quoted_cents'])} | {s['status']} |")
    if not report['sku_rows']:
        out.append('| — | 未找到所有店均有、菜单正价且可核价的 SKU | — | — | — | UNKNOWN |')
    # ── 诊断区（B 类）其余部分：同店数据是否自洽。这是本项目的结论所在。
    # Findings that are identical across stores are environment-level, not
    # per-store: collapse them so one schema issue does not read as three bugs.
    grouped, seen = [], {}
    for s in report['stores']:
        for f in (s.get('status_checks') or []) + s['diagnostics']:
            if f['severity'] not in ('FAIL','WARN'):
                continue
            key=(f['id'],f['summary'])
            if key in seen:
                seen[key]['stores'].append(s['storeName'])
            else:
                item={'f':f,'stores':[s['storeName']]}
                seen[key]=item
                grouped.append(item)
    out+=['','### 1.2 其他 FAIL / WARN 级发现','']
    if grouped:
        for item in grouped:
            f=item['f']
            if len(item['stores'])==len(report['stores']):
                scope='全部 %d 家门店' % len(report['stores'])
            else:
                scope='、'.join(item['stores'])
            out.append(f"- **{f['severity']} {f['id']}** ｜ {scope}：{f['summary']}")
            out.append(f"  - {f['detail']}")
    else:
        out.append('- 本次采样未发现 FAIL/WARN 级的不自洽项。')
        out.append('- 这不代表接口没有问题：只说明**在这些门店、这个时段、这些 SKU 上**未触发判据。')
    out+=['','### 1.3 营业状态与菜单可得性（逐店）','',
          '| 门店 | 营业时段 | 状态 | 菜单 SKU | MCD-304a | MCD-304b |',
          '|---|---|---|---:|---|---|']
    for s in report['stores']:
        sc={x['id']:x['severity'] for x in (s.get('status_checks') or [])}
        hours=f"{s.get('businessStartTime') or '?'}-{s.get('businessEndTime') or '?'}"
        out.append(f"| {s['storeName']} | {hours} | {s.get('businessStatus')} | {s['sku_count']} "
                   f"| {sc.get('MCD-304a','-')} | {sc.get('MCD-304b','-')} |")

    # ── 观察区（A 类）：跨店差异、覆盖、营养。记录，不判错。
    cov=report.get('coverage') or {}
    out+=['','## 二、观察：门店之间的差异（不作故障判定）','',
          '> 不同门店备货范围、定价不同属正常商业行为。以下一律为 INFO。','',
          '### 2.1 菜单覆盖差异','',
          f"- 三店交集 SKU：**{report['intersection_count']}**；并集：**{report['union_count']}**",
          '- 交集只代表本次查询中均被列出，不表示未来时段仍可供应。',
          f"- **只在部分门店出现**的商品：{cov.get('exclusive_count',0)} 个"]
    ex=(cov.get('exclusive') or [])[:15]
    if ex:
        out+=['','| 商品 | SKU | 出现在 |','|---|---|---|']
        for r in ex:
            out.append(f"| {r['name']} | `{r['sku']}` | {'、'.join(r['store_names'])}（{len(r['stores'])}/{meta['num_stores']} 家）|")
        if cov.get('exclusive_count',0)>len(ex):
            out.append(f"| … | 另有 {cov['exclusive_count']-len(ex)} 个未列出 |")

    out+=['','### 2.2 营养关联（可关联性，非营养真实性）','',
          '| 门店 | 精确匹配 | 部分匹配 | 无法匹配 | 菜单命名商品 |','|---|---:|---:|---:|---:|']
    for s in report['stores']:
        t=s.get('nutrition_tiers') or {}
        out.append(f"| {s['storeName']} | {t.get('exact',0)} | {t.get('partial',0)} "
                   f"| {t.get('missing',0)} | {s.get('nutrition_menu_named_count',0)} |")
    for s in report['stores']:
        t=s.get('nutrition_tiers') or {}
        if t.get('partial_examples'):
            out.append(f"- 部分匹配示例（{s['storeName']}）：{'、'.join(t['partial_examples'])}")
        if t.get('missing_examples'):
            out.append(f"- 无法匹配示例（{s['storeName']}）：{'、'.join(t['missing_examples'])}")
    out.append('')
    out.append('> 「无法匹配」不等于数据错误：营养表覆盖范围有限，且只取一次（全局）。')
    out.append('> 匹配率随采样时段变化，跨次比较需注明时间。')

    return '\n'.join(out)


def explanations():
    """Reading rules. Appended last so optional sections can precede it."""
    return '\n'.join(['','## 四、结果解释','',
          '- **同店不自洽**（菜单有货但核价 0 元且列表为空、营业时段与状态标记矛盾）才进入诊断结论。',
          '- 同一商品跨门店价格不同，只作观察；不自动判定为 Bug。',
          '- 自动优惠可能使核价低于菜单展示价；不要求两者相等。',
          '- 门店接口**不提供**门店类型字段，本报告不使用商场店/社区店一类标签。',
          '- 商品营养匹配率只是「名称可关联」的下界，不证明营养真实或套餐营养完整。',
          '- 本项目不会创建订单、领券、花积分、读取个人地址或历史订单。',''])
