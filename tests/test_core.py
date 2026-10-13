import json
import unittest
from pathlib import Path
from doctor.core import (diagnose,unwrap,business_ok,named_foods,money_cents,
                         scan_balanced_json,READ_ONLY_TOOLS)

FIXTURE=Path(__file__).resolve().parents[1]/'fixtures'/'sample.json'

class DoctorTests(unittest.TestCase):
    def setUp(self):self.p=json.loads(FIXTURE.read_text(encoding='utf-8'))
    def test_warning_zero_price(self):
        r=diagnose(self.p);self.assertTrue(any(x['id']=='MCD-102' and x['severity']=='WARN' for x in r['findings']))
    def test_price_positive_pass(self):
        self.p['price']['data']={'price':2550,'productList':[{'productCode':'DUMMY-1001'}]}
        self.assertTrue(any(x['id']=='MCD-102' and x['severity']=='PASS' for x in diagnose(self.p)['findings']))
    def test_true_business_failure(self):
        self.p['menu']={'success':False,'code':400,'message':'invalid'}
        self.assertTrue(any(x['id']=='MCD-101' and x['severity']=='FAIL' for x in diagnose(self.p)['findings']))
    def test_empty_nutrition(self):
        self.p['nutrition']={'success':True,'data':''}
        self.assertTrue(any(x['id']=='MCD-201' and x['severity']=='WARN' for x in diagnose(self.p)['findings']))
    def test_markdown_and_json_wrapper(self):
        s='## Original Response\n```json\n{"success":false,"code":400}\n```'
        self.assertEqual(business_ok({'content':[{'type':'text','text':s}]}),False)
    def test_money_precision(self):
        self.assertEqual(money_cents('25.50'),2550)
        self.assertEqual(money_cents('8'),800)
        self.assertIsNone(money_cents('0.001'))
    def test_mutating_calls_not_allowlisted(self):
        self.assertNotIn('create-order',READ_ONLY_TOOLS)
        self.assertNotIn('auto-bind-coupons',READ_ONLY_TOOLS)
        self.assertNotIn('cancel-order',READ_ONLY_TOOLS)
    def test_missing_is_unknown_not_pass(self):
        self.p.pop('price')
        self.assertTrue(any(x['id']=='MCD-102' and x['severity']=='UNKNOWN' for x in diagnose(self.p)['findings']))
    def test_nutrition_names_list(self):
        self.assertEqual(named_foods(self.p['nutrition'])[0],{'示例汉堡','示例饮料'})

class RealResponseRegression(unittest.TestCase):
    """Regression samples captured from live read-only calls.

    These are the responses that broke the naive parsers:
      1. the glossary embeds `{code}` placeholders, which breaks brace matching
      2. `list-nutrition-foods.data` is a custom CSV-ish string, not an array
    """

    GLOSSARY_MENU = ('## Response Structure\n\n'
                     '- **data.meals**: 餐品详情映射表 (Type: object)\n'
                     '  - data.meals.{code}.currentPrice: 餐品现价（销售价）(Type: string)\n\n'
                     '## Original Response\n\n'
                     '{"success":true,"code":200,"data":{"meals":{'
                     '"9900016076":{"code":"9900016076","name":"鸡薯双全盒","currentPrice":"33.5"},'
                     '"507411":{"code":"507411","name":"麦咖啡美式","currentPrice":"11.9"}}}}')

    NUTRITION_CSV = ('{"success":true,"code":200,"data":"'
                     '[3]{productName,nutritionDescription,energyKj}:\\n'
                     '  猪柳麦满分,null,1288\\n  薯条,null,1420\\n  巨无霸,null,2133"}')

    def test_glossary_placeholders_do_not_break_parsing(self):
        d = unwrap(self.GLOSSARY_MENU)
        self.assertEqual(d.get('code'), 200)
        meals = (d.get('data') or {}).get('meals')
        self.assertIsInstance(meals, dict)
        self.assertEqual(len(meals), 2)
        self.assertEqual(meals['507411']['name'], '麦咖啡美式')

    def test_placeholder_alone_is_not_mistaken_for_json(self):
        self.assertIsNone(scan_balanced_json('data.meals.{code}.currentPrice: 餐品现价'))

    def test_official_csv_nutrition(self):
        names, kind = named_foods(self.NUTRITION_CSV)
        self.assertEqual(names, {'猪柳麦满分', '薯条', '巨无霸'})
        self.assertIn('official-csv', kind)

    def test_official_csv_reports_partial_parse(self):
        truncated = self.NUTRITION_CSV.replace('[3]{', '[9]{')
        _, kind = named_foods(truncated)
        self.assertIn('声明 9 条', kind)

    def test_menu_diagnoses_as_parseable_not_unknown(self):
        report = diagnose({'menu': self.GLOSSARY_MENU, 'mode': 'fixture'})
        statuses = {f['id']: f['severity'] for f in report['findings']}
        self.assertEqual(statuses.get('MCD-101'), 'PASS')

if __name__=='__main__':unittest.main()
