import json
import unittest
from pathlib import Path
from doctor.multi import (compare,extract_stores,shared_skus,extract_menu,money_price,markdown,
                          check_store_status,nutrition_match_tiers)

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

class StoreStatusConsistencyTests(unittest.TestCase):
    """MCD-304: within-store status vs menu availability (class B), never guessed."""

    def _sev(self, checks, suffix):
        return {c['id']: c['severity'] for c in checks}['MCD-304' + suffix]

    def test_open_but_empty_menu_is_warn(self):
        # Tagged open, yet the menu yields nothing: the two disagree.
        c = check_store_status({'businessStatus': True, 'businessStartTime': '06:00',
                                'businessEndTime': '23:00'}, 0, None)
        self.assertEqual(self._sev(c, 'a'), 'WARN')

    def test_closed_and_empty_menu_is_info_not_fault(self):
        # Closed with an empty menu is consistent, and closing is a business fact.
        c = check_store_status({'businessStatus': False, 'businessStartTime': '06:00',
                                'businessEndTime': '23:00'}, 0, None)
        self.assertEqual(self._sev(c, 'a'), 'INFO')

    def test_open_with_menu_is_pass(self):
        c = check_store_status({'businessStatus': True, 'businessStartTime': '06:00',
                                'businessEndTime': '23:00'}, 115, None)
        self.assertEqual(self._sev(c, 'a'), 'PASS')

    def test_missing_status_is_unknown_never_guessed(self):
        c = check_store_status({'businessStartTime': '06:00', 'businessEndTime': '23:00'}, 0, None)
        self.assertEqual(self._sev(c, 'a'), 'UNKNOWN')

    def test_hours_say_open_but_flag_says_closed(self):
        # 23:45 is inside 00:00-23:59, yet the store reports closed.
        c = check_store_status({'businessStatus': False, 'businessStartTime': '00:00',
                                'businessEndTime': '23:59'}, 0, 23 * 60 + 45)
        self.assertEqual(self._sev(c, 'b'), 'WARN')

    def test_hours_and_flag_agree(self):
        c = check_store_status({'businessStatus': True, 'businessStartTime': '00:00',
                                'businessEndTime': '23:59'}, 100, 23 * 60 + 45)
        self.assertEqual(self._sev(c, 'b'), 'PASS')

    def test_no_official_time_means_unknown(self):
        c = check_store_status({'businessStatus': True, 'businessStartTime': '06:00',
                                'businessEndTime': '23:00'}, 10, None)
        self.assertEqual(self._sev(c, 'b'), 'UNKNOWN')

    def test_all_day_store_never_false_alarm(self):
        # 00:00-00:00 should read as all-day, not as a zero-length window.
        c = check_store_status({'businessStatus': True, 'businessStartTime': '00:00',
                                'businessEndTime': '00:00'}, 50, 3 * 60)
        self.assertEqual(self._sev(c, 'b'), 'PASS')


class NutritionTierTests(unittest.TestCase):
    def test_tiers_split_exact_partial_missing(self):
        foods = {'巨无霸', '薯条(大)', '麦香鸡'}
        exact, partial, missing = nutrition_match_tiers(
            {'巨无霸', '薯条（大）', '麦辣鸡翅', ''}, foods)
        self.assertIn('巨无霸', exact)
        # Full-width brackets normalise onto the half-width entry.
        self.assertIn('薯条（大）', exact)
        self.assertNotIn('麦辣鸡翅', exact | partial)
        self.assertIn('麦辣鸡翅', missing)
        # Empty names are dropped, never counted as unmatched noise.
        self.assertNotIn('', missing)

    def test_partial_match_by_containment(self):
        foods = {'麦辣鸡翅（两块）'}
        exact, partial, missing = nutrition_match_tiers({'麦辣鸡翅'}, foods)
        self.assertIn('麦辣鸡翅', partial)
        self.assertEqual(len(exact), 0)

    def test_every_tier_is_accounted_for(self):
        foods = {'A', 'BB'}
        exact, partial, missing = nutrition_match_tiers({'A', 'BB', 'C', 'DDD'}, foods)
        self.assertEqual(len(exact) + len(partial) + len(missing), 4)


if __name__=='__main__':unittest.main()
