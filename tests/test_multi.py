import json
import unittest
from pathlib import Path
from doctor.multi import compare,extract_stores,shared_skus,extract_menu,money_price,markdown

FIXTURE=Path(__file__).resolve().parents[1]/'fixtures'/'nearby-stores.simulated.json'

class MultiStoreDoctorTests(unittest.TestCase):
    def setUp(self):
        self.data=json.loads(FIXTURE.read_text(encoding='utf-8'))
    def test_common_skus(self):
        menus={s['storeCode']:extract_menu(s['menu']) for s in self.data['stores']}
        self.assertEqual(shared_skus(menus,None,5),['DEMO-001','DEMO-002'])
    def test_stores_accept_official_list(self):
        raw={'success':True,'data':[{'storeCode':'123','storeName':'A'}, {'storeCode':'456','storeName':'B'}, {'storeCode':'123','storeName':'A'}]}
        self.assertEqual(len(extract_stores(raw)),2)
    def test_not_a_bug_when_stores_differ_in_price(self):
        rep=compare(self.data)
        self.assertEqual(rep['intersection_count'],2)
        self.assertEqual(rep['union_count'],5)
        self.assertEqual(rep['sku_rows'][0]['menu_spread_cents'],50)
        self.assertEqual(rep['sku_rows'][0]['by_store'][0]['status'],'PASS')
        self.assertEqual(rep['sku_rows'][0]['by_store'][1]['status'],'PASS')
    def test_anomalous_zero_quote_is_warning(self):
        rep=compare(self.data)
        self.assertEqual(rep['sku_rows'][0]['by_store'][2]['status'],'WARN')
        self.assertIn('可疑零元核价',markdown(rep))
    def test_missing_price_is_unknown(self):
        self.data['stores'][0]['prices'].pop('DEMO-002')
        rep=compare(self.data)
        self.assertEqual(rep['sku_rows'][1]['by_store'][0]['status'],'UNKNOWN')
    def test_no_comparable_products(self):
        self.data['stores'][2]['menu']={'success':True,'code':200,'data':{'meals':{'X':{'name':'OTHER','currentPrice':'3.00'}}}}
        rep=compare(self.data)
        self.assertEqual(rep['sku_rows'],[])
        self.assertIn('未找到所有店均有',markdown(rep))

if __name__=='__main__':unittest.main()
