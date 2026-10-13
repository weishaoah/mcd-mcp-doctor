"""CLI: offline fixture by default; live calls go through the built-in transport.

Live mode needs only MCD_MCP_TOKEN and the Python standard library — no external
`mcd-cli` binary, no third-party packages.
"""
import argparse
import json
import sys
from pathlib import Path
from .core import diagnose,markdown,READ_ONLY_TOOLS,unwrap,money_cents
from .multi import extract_stores,extract_menu,shared_skus,compare,markdown as multi_markdown
from .basket import build_basket_table,markdown as basket_markdown
from datetime import datetime, timezone

_TRANSPORT = None
_TOOLS_CACHE: dict = {}


def get_transport():
    """Lazily build the shared transport so --fixture needs no token."""
    global _TRANSPORT
    if _TRANSPORT is None:
        from .transport import McdMcpTransport
        _TRANSPORT = McdMcpTransport()
    return _TRANSPORT


def invoke(name,arguments):
    if name not in READ_ONLY_TOOLS:
        raise PermissionError(f'Forbidden tool: {name}')
    return get_transport().call_tool(name,arguments)


def get_tool_definitions():
    """Read-only MCP tools/list; cached because one run needs it once."""
    if 'value' in _TOOLS_CACHE:
        return _TOOLS_CACHE['value']
    value = get_transport().list_tools()
    _TOOLS_CACHE['value'] = value
    return value


def live_snapshot(store,sku=None,detail_code=None):
    data={'mode':'live-unverified','calls':[]}
    data['tools']=get_tool_definitions()
    def call(name,args):
        obj=invoke(name,args);data['calls'].append(name);return obj
    # Intentionally no account/personal data and no mutating calls.
    menu=call('query-meals',{'storeCode':store,'orderType':1,'beType':1})
    data['menu']=menu
    meals=unwrap(menu).get('data',{}).get('meals',{})
    if not sku and isinstance(meals,dict):
        sku=next((code for code,item in meals.items() if isinstance(item,dict) and (money_cents(item.get('currentPrice')) or 0)>0),None)
    data['product_code']=sku
    data['nutrition']=call('list-nutrition-foods',{})
    if sku:
        data['price']=call('calculate-price',{'storeCode':store,'orderType':1,'beType':1,'items':[{'productCode':str(sku),'quantity':1}]})
    if detail_code:
        data['detail']=call('query-meal-detail',{'storeCode':store,'orderType':1,'beType':1,'code':detail_code})
    return data


def discover_nearby(city,keyword,limit=3):
    if not city or not keyword:
        raise ValueError('自动搜店需同时提供 --city 和 --keyword（商圈或地标，无需精确住址）。')
    raw=invoke('query-nearby-stores',{'beType':1,'searchType':2,'city':city,'keyword':keyword})
    stores=extract_stores(raw)
    if len(stores)<2:
        raise ValueError('此次查询得到少于2家可识别门店。请调整 --keyword，或使用 --stores 手动指定门店编码。')
    return stores[:limit]


def live_multi(stores,requested_skus=None,sample_count=5):
    if len(stores)<2 or len(stores)>5:
        raise ValueError('请提供2–5家门店，建议先用3家。')
    data={'mode':'live-unverified','recorded_at_utc':datetime.now(timezone.utc).isoformat(),
          'business_channel':{'beType':1,'orderType':1},'tools':get_tool_definitions(),'stores':[],'sample_count':sample_count}
    for store in stores:
        code=str(store['storeCode'])
        menu=invoke('query-meals',{'storeCode':code,'orderType':1,'beType':1})
        data['stores'].append({'storeCode':code,'storeName':store.get('storeName',code),
                               'menu':menu,'prices':{},'calls':['query-meals']})
    nutrition=invoke('list-nutrition-foods',{})
    data['nutrition']=nutrition
    menus={str(s['storeCode']):extract_menu(s['menu']) for s in data['stores']}
    selected=shared_skus(menus,requested_skus,sample_count)
    data['requested_skus']=selected
    for s in data['stores']:
        for code in selected:
            s['prices'][code]=invoke('calculate-price',{
                'storeCode':s['storeCode'],'orderType':1,'beType':1,
                'items':[{'productCode':code,'quantity':1}]})
            s['calls'].append('calculate-price')
    return data


def main():
    parser=argparse.ArgumentParser(description='MCD MCP Doctor: offline first, read-only checks')
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--fixture',help='Local test snapshot JSON; does not access network')
    mode.add_argument('--live',action='store_true',help='Read-only live scan using MCD_MCP_TOKEN and the built-in transport')
    # Target selection is its own group: --city is meant to be combined with --keyword,
    # so they must not share the mutually exclusive mode group.
    parser.add_argument('--store',help='Single-store code (legacy live scan)')
    parser.add_argument('--stores',help='Comma-separated store codes, e.g. 1001,1002,1003')
    parser.add_argument('--city',help='City for official nearby-store lookup, China mainland')
    parser.add_argument('--keyword',help='District or landmark for official nearby-store lookup')
    parser.add_argument('--limit',type=int,default=3,help='Store limit for nearby lookup, 2 to 5')
    parser.add_argument('--samples',type=int,default=5,help='Common SKUs to quote across stores, 1 to 5')
    parser.add_argument('--skus',help='Optional comma-separated preferred common SKU codes')
    parser.add_argument('--sku',help='Optional known menu SKU')
    parser.add_argument('--detail-code',help='Optional menu product code for query-meal-detail')
    parser.add_argument('--output',default='report.md',help='Markdown report path')
    parser.add_argument('--basket',help='Optional comma-separated SKUs: which store is cheapest for this exact order')
    parser.add_argument('--save-snapshot',help='Write the live snapshot to JSON so it can be replayed offline with --fixture')
    args=parser.parse_args()
    try:
        if not 1<=args.samples<=5:
            parser.error('--samples must be 1..5')
        if not 2<=args.limit<=5:
            parser.error('--limit must be 2..5')
        if args.fixture:
            data=json.loads(Path(args.fixture).read_text(encoding='utf-8'))
            data.setdefault('mode','fixture')
        elif args.stores or (args.city and args.keyword):
            if args.stores:
                codes=[c.strip() for c in args.stores.split(',') if c.strip()]
                stores=[{'storeCode':c,'storeName':c} for c in dict.fromkeys(codes)]
            else:
                stores=discover_nearby(args.city,args.keyword,args.limit)
            requests=[c.strip() for c in (args.skus or '').split(',') if c.strip()] or None
            data=live_multi(stores,requests,args.samples)
            if args.save_snapshot:
                Path(args.save_snapshot).write_text(
                    json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
                print(f"快照已保存：{args.save_snapshot}（可脱敏后用 --fixture 离线重放）")
        else:
            if not args.store:parser.error('Use --store, --stores, or --city with --keyword in --live mode')
            data=live_snapshot(args.store,args.sku,args.detail_code)
        multi=isinstance(data.get('stores'),list)
        report=compare(data) if multi else diagnose(data)
        text=multi_markdown(report) if multi else markdown(report)
        if multi:
            wanted=[c.strip() for c in (args.basket or '').split(',') if c.strip()] or None
            report['basket']=build_basket_table(report,wanted)
            if report['basket']:
                text+=basket_markdown(report['basket'])
        out=Path(args.output);out.parent.mkdir(parents=True,exist_ok=True)
        out.write_text(text,encoding='utf-8')
        out.with_suffix('.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(f"生成诊断报告：{out}，{out.with_suffix('.json')}")
        if multi:
            s=report.get('price_summary') or {}
            print('门店:',report['meta']['num_stores'],'共同SKU:',report['meta']['sample_count'],
                  '| 跨店同价 %s / 有价差 %s' % (s.get('identical'),s.get('differing')))
            b=report.get('basket')
            if b:
                print('购物篮最划算:',' / '.join(r['store_name'] for r in b['totals'] if r['is_winner']),
                      '省 %.2f 元' % (b['saving_cents']/100))
            return 2 if any(f['severity']=='FAIL' for s in report['stores'] for f in s['diagnostics']) else 0
        print(' / '.join(f'{k}={v}' for k,v in report['meta']['counts'].items()))
        return 2 if report['meta']['counts']['FAIL'] else 0
    except (RuntimeError,PermissionError,ValueError,OSError) as e:
        print(str(e),file=sys.stderr);return 3

if __name__=='__main__':sys.exit(main())
