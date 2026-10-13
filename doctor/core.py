"""Pure validation logic; no network access, no personal-account tools."""
import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any

READ_ONLY_TOOLS = frozenset({
    'query-nearby-stores','query-meals','query-meal-detail',
    'list-nutrition-foods','calculate-price',
})

# Tool input contracts from official MCD README (2026-10-12).
EXPECTED_REQUIRED = {
    'query-meals': {'storeCode', 'orderType', 'beType'},
    'query-meal-detail': {'code', 'orderType', 'beType'},
    'calculate-price': {'storeCode', 'orderType', 'beType', 'items'},
}


def scan_balanced_json(text: str, prefer_after: str = "") -> dict | None:
    """Find the first brace-balanced fragment that parses as a JSON object.

    The official MCP responses embed a field glossary *before* the raw payload,
    and that glossary contains placeholders such as `data.meals.{code}.withOrder`.
    The literal `{code}` breaks naive brace matching, which is why plain
    `json.loads` and fence extraction both fail on real traffic.

    Strategy: try every `{` as a start position, walk forward while tracking
    string state (so braces inside quoted text are ignored), and accept the
    first balanced slice that decodes into a dict.

    `prefer_after` lets callers start from a known marker (e.g. the
    "Original Response" heading) to reduce ambiguity.
    """
    if not isinstance(text, str) or not text:
        return None
    haystack = text
    if prefer_after and prefer_after in text:
        haystack = text.split(prefer_after, 1)[1]

    starts = [i for i, ch in enumerate(haystack) if ch == "{"]
    for start in starts:
        depth = 0
        in_string = False
        escaped = False
        for i in range(start, len(haystack)):
            ch = haystack[i]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth <= 0:
                    chunk = haystack[start:i + 1]
                    try:
                        obj = json.loads(chunk)
                    except json.JSONDecodeError:
                        break  # this start position is a false lead
                    if isinstance(obj, dict):
                        return obj
                    break
    return None


def unwrap(raw: Any) -> dict:
    """Extract embedded business JSON from MCP wrappers without guessing on failure."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        if raw.get('isError') is True:
            return {'_tool_error': True, '_raw_error': str(raw.get('message', 'MCP tool error'))[:200]}
        if isinstance(raw.get('data'), (list, dict)) or 'success' in raw or 'code' in raw:
            return raw
        if isinstance(raw.get('structuredContent'), dict):
            return unwrap(raw['structuredContent'])
        if isinstance(raw.get('content'), list):
            texts = [part.get('text','') for part in raw['content'] if isinstance(part,dict) and part.get('type')=='text']
            for text in texts:
                val=unwrap(text)
                if val: return val
            return {'_unparsed_text': '\n'.join(texts)}
        return raw
    if isinstance(raw, str):
        txt=raw.strip()
        if not txt: return {}
        # Some tools return explanatory Markdown then a fenced Original Response JSON.
        matches=re.findall(r'```(?:json)?\s*([\s\S]*?)```',txt,re.I)
        for text in reversed(matches):
            try:
                obj=json.loads(text)
                if isinstance(obj,dict): return unwrap(obj)
            except json.JSONDecodeError: pass
        try:
            obj=json.loads(txt)
            if isinstance(obj,dict): return unwrap(obj)
        except json.JSONDecodeError: pass
        # Last resort: brace-balanced scan. Plain json.loads fails here because the
        # glossary that precedes the payload contains `{code}` placeholders.
        for marker in ('Original Response','原始响应'):
            obj=scan_balanced_json(txt,marker)
            if obj: return unwrap(obj)
        obj=scan_balanced_json(txt)
        if obj: return unwrap(obj)
        return {'_unparsed_text':txt}
    return {}


def business_ok(value: Any) -> bool | None:
    d=unwrap(value)
    if not d: return None
    if d.get('_tool_error'): return False
    if d.get('success') is False: return False
    if 'code' in d and isinstance(d['code'],int) and d['code'] not in (0,200): return False
    if d.get('success') is True: return True
    return None


def parse_official_csv(text: str) -> tuple[list[str], int | None]:
    """Parse the custom nutrition payload: `[N]{field,field,...}:` then CSV rows.

    `data` for `list-nutrition-foods` is a *string*, not an array. It looks like::

        [160]{productName,nutritionDescription,energyKj,...}:
          猪柳麦满分,null,1288,308,16,16,24,781,213

    Neither JSON nor a Markdown table, so any consumer that assumes either
    silently gets zero rows. Returns (names, declared_count); declared_count is
    the `[N]` header and is used to detect partial parses.
    """
    if not isinstance(text, str) or not text:
        return [], None
    header = re.search(r'\[(\d+)\]\s*\{([^}]*)\}', text)
    if not header:
        return [], None
    declared = int(header.group(1))
    fields = [f.strip() for f in header.group(2).split(',')]
    idx = next((i for i, f in enumerate(fields)
                if f.lower() in ('productname', 'name', 'foodname', '餐品名称')), None)
    if idx is None:
        return [], declared
    # Drop the remainder of the header line (a trailing ':' and anything after it).
    rest = text[header.end():]
    newline = rest.find('\n')
    rest = rest[newline + 1:] if newline >= 0 else ''
    names = []
    for line in rest.splitlines():
        line = line.strip()
        if not line or line.startswith(('[', '{')):
            continue
        parts = line.split(',')
        if len(parts) <= idx:
            continue
        value = parts[idx].strip()
        if value and value.lower() != 'null':
            names.append(value)
    return names, declared


def named_foods(raw: Any) -> tuple[set[str], str]:
    """Best-effort name extraction; never treated as verified nutrient accuracy."""
    d=unwrap(raw)
    data=d.get('data')
    if isinstance(data,dict):
        for key in ('list','items','foods','records'):
            if isinstance(data.get(key),list): data=data[key];break
    if isinstance(data,list):
        names={str(x.get('name') or x.get('productName') or x.get('foodName')).strip() for x in data if isinstance(x,dict) and (x.get('name') or x.get('productName') or x.get('foodName'))}
        return names,'list'
    txt=d.get('_unparsed_text','')
    if isinstance(data,str): txt=data
    txt=txt.replace('\\n','\n')
    # The official nutrition payload is a custom CSV-ish text block, not a table.
    csv_names,declared=parse_official_csv(txt)
    if csv_names:
        suffix='' if declared is None or declared==len(csv_names) else f'（声明 {declared} 条，解析出 {len(csv_names)} 条）'
        return set(csv_names),'official-csv'+suffix
    rows=[line.strip() for line in txt.splitlines() if '|' in line and not re.match(r'^\s*\|?[-:\s|]+$',line)]
    names=set()
    for row in rows:
        fields=[f.strip() for f in row.strip('|').split('|')]
        if len(fields)>=3 and fields[0] not in ('餐品名称','产品名称','商品名称','Food','名称'):
            names.add(fields[0])
    return names,'markdown' if names else 'unknown'


def money_cents(x:Any) -> int | None:
    try:
        v=Decimal(str(x))
        if not v.is_finite() or v<0: return None
        c=v*100
        return int(c) if c==int(c) else None
    except (ValueError,TypeError,InvalidOperation): return None


def issue(code:str,severity:str,summary:str,detail:str,source:str="") -> dict:
    return {'id':code,'severity':severity,'summary':summary,'detail':detail,'source':source}


def diagnose(payload: dict) -> dict:
    """Return findings, never label transient/scope limitations as a confirmed server bug."""
    findings=[]
    meta={'mode':payload.get('mode','fixture'),'scope':'read-only','calls':payload.get('calls',[])}
    tools=payload.get('tools')
    if isinstance(tools,list):
        available={t.get('name'):t for t in tools if isinstance(t,dict) and t.get('name')}
        missing=READ_ONLY_TOOLS-available.keys()
        if missing:
            findings.append(issue('MCP-001','WARN','只读核心工具缺失',f'缺失: {", ".join(sorted(missing))}；可能是权限或版本差异。','tools/list'))
        else:
            findings.append(issue('MCP-001','PASS','核心只读工具可发现',f'{len(available)} 个工具被发现；数量只是观测值，不以33或35做硬性断言。','tools/list'))
        for name,expected in EXPECTED_REQUIRED.items():
            t=available.get(name)
            if not t:continue
            schema=t.get('inputSchema',{}) or {}
            required=set(schema.get('required') or [])
            absent=expected-required
            if absent:
                findings.append(issue('MCP-002','WARN',f'{name} 的 schema 声明需复核',f'官方文档要求 {sorted(expected)}；当前 schema 未声明 required: {sorted(absent)}。可能是文档与运行时约定不同，不能直接认定故障。','tools/list'))
    else:
        findings.append(issue('MCP-001','UNKNOWN','未提供 tools/list 数据','无法判断工具可发现性。'))
    menu=unwrap(payload.get('menu'))
    meals=((menu.get('data') or {}).get('meals') if isinstance(menu.get('data'),dict) else None)
    selected=payload.get('product_code')
    if business_ok(menu) is False:
        findings.append(issue('MCD-101','FAIL','菜单业务响应失败',f"success/code 表示失败：{str(menu.get('code',menu.get('_raw_error','未知')))[:100]}",'query-meals'))
    elif not isinstance(meals,dict):
        findings.append(issue('MCD-101','UNKNOWN','未能解析菜单','未发现 data.meals 映射；检查服务响应格式是否变更。','query-meals'))
    elif not meals:
        findings.append(issue('MCD-101','WARN','菜单当前为空','可能与门店、供餐时段或权限有关，不能推断是服务器故障。','query-meals'))
    else:
        findings.append(issue('MCD-101','PASS','菜单结构可解析',f'取得 {len(meals)} 个 SKU。','query-meals'))
        if not selected: selected=next((code for code,item in meals.items() if isinstance(item,dict) and (money_cents(item.get('currentPrice')) or 0)>0),None)
    meta['product_code']=selected
    detail=unwrap(payload.get('detail')) if payload.get('detail') is not None else None
    if detail is not None:
        rounds=(detail.get('data') or {}).get('rounds') if isinstance(detail.get('data'),dict) else None
        if isinstance(rounds,list):
            findings.append(issue('MCD-103','PASS','套餐组成结构可解析',f'可观察到 {len(rounds)} 个选择轮次；不代表已获完整可选池。','query-meal-detail'))
            if rounds and any(len(r.get('choices',[]))==1 for r in rounds if isinstance(r,dict)):
                findings.append(issue('MCD-104','INFO','部分选择轮次仅有一个选项','这是返回数据的观察，不足以判定其他选择不可用；参见官方 Issue #9。','query-meal-detail'))
        else:
            findings.append(issue('MCD-103','UNKNOWN','无法确定套餐组成','未发现 rounds；可能是单品或响应格式差异。','query-meal-detail'))
    price=unwrap(payload.get('price')) if payload.get('price') is not None else None
    if price is None:
        findings.append(issue('MCD-102','UNKNOWN','未执行单品核价','无核价数据；实时模式需提供门店及一个有效 SKU。','calculate-price'))
    elif business_ok(price) is False:
        findings.append(issue('MCD-102','FAIL','核价业务响应失败',f"success/code 表示失败：{str(price.get('code',price.get('_raw_error','未知')))[:100]}",'calculate-price'))
    else:
        data=price.get('data') if isinstance(price.get('data'),dict) else {}
        price_value=data.get('price')
        try: value=Decimal(str(price_value))
        except (TypeError,InvalidOperation): value=None
        products=data.get('productList')
        current=(meals.get(selected) or {}) if isinstance(meals,dict) and selected in meals else {}
        menu_cents=money_cents(current.get('currentPrice'))
        if value is None:
            findings.append(issue('MCD-102','UNKNOWN','无法验证核价金额','data.price 缺失或不是数值，请检查返回格式。','calculate-price'))
        elif value==0 and menu_cents and (not products):
            findings.append(issue('MCD-102','WARN','可疑零元核价',f'菜单 SKU {selected} 展示正价 {menu_cents} 分，核价返回 0 且商品列表为空。请检查 code → items[].productCode 映射、供餐时段和渠道。','calculate-price'))
        elif value>=0:
            findings.append(issue('MCD-102','PASS','核价金额已返回',f'核价响应 data.price={value}；按官方示例预期为“分”，实际应付可能含优惠，不能强行与菜单标价相等。','calculate-price'))
    nutrition=unwrap(payload.get('nutrition')) if payload.get('nutrition') is not None else None
    if nutrition is None:
        findings.append(issue('MCD-201','UNKNOWN','未获取营养表','未执行营养接口。','list-nutrition-foods'))
    elif business_ok(nutrition) is False:
        findings.append(issue('MCD-201','WARN','营养接口业务失败','接口返回失败；需要记录时间并在间隔后重试，单次不能认定永久故障。','list-nutrition-foods'))
    else:
        foods,kind=named_foods(nutrition)
        if not foods:
            findings.append(issue('MCD-201','WARN','营养数据为空或格式不可识别','可能是瞬时空响应或格式差异；默认不连续多次调用。','list-nutrition-foods'))
        elif isinstance(meals,dict) and meals:
            menu_names={str(v.get('name','')).strip() for v in meals.values() if isinstance(v,dict) and v.get('name')}
            matched=menu_names & foods
            ratio=100*len(matched)/len(menu_names) if menu_names else 0
            findings.append(issue('MCD-201','INFO','营养名称可关联覆盖率（下界）',f'菜单名称精确命中 {len(matched)}/{len(menu_names)}（{ratio:.1f}%），数据解析格式：{kind}。不代表真实营养覆盖率；套餐拆解后可能提高。','query-meals + list-nutrition-foods'))
        else:
            findings.append(issue('MCD-201','INFO','营养列表可解析',f'识别到 {len(foods)} 个名称。','list-nutrition-foods'))
    meta['counts']={k:sum(x['severity']==k for x in findings) for k in ('FAIL','WARN','PASS','INFO','UNKNOWN')}
    return {'meta':meta,'findings':findings}


def markdown(report:dict) -> str:
    m=report['meta']; out=['# McD MCP Doctor 诊断报告','','> **说明：** 本报告为开发者接入质量检查，不是官方接口安全审计；示例数据不得当作线上验证。', '',f"- 模式：`{m['mode']}`",f"- 范围：`{m['scope']}`",f"- SKU：`{m.get('product_code') or '未选择'}`",f"- 统计：{', '.join(f'{k}={v}' for k,v in m['counts'].items())}",'','## 检查结果','']
    for x in report['findings']:
        out += [f"### [{x['severity']}] {x['id']} — {x['summary']}", '',x['detail'], '',f"数据来源：`{x['source'] or '本地逻辑'}`",'']
    out += ['## 重要边界','','- PASS 表示本项可观察的条件满足，不代表整个服务零缺陷。','- WARN 表示需要排查的风险，不应直接断言官方有 Bug。','- UNKNOWN 表示数据不足；不得当作 PASS。','- 价格与营养没有相互独立的完整真值时，不进行准确性认证。','- 未调用任何创建订单、领券、积分抽奖、修改地址或取消订单的操作。','']
    return '\n'.join(out)
