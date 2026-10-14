import json
import unittest
from pathlib import Path
from doctor.multi import (compare,extract_stores,shared_skus,extract_menu,money_price,markdown,
                          check_store_status,nutrition_match_tiers,
                          parse_meal_detail,bundle_skus,unmatched_reason_counts)

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


def _detail_payload(name, rounds, success=True):
    """Shape mirrors the real endpoint: instructions, then Original Response JSON."""
    import json as _json
    body = {'success': success, 'code': 200 if success else 400,
            'message': '请求成功' if success else '参数错误',
            'data': {'code': 'X1', 'name': name, 'rounds': rounds,
                     'supportModify': True}}
    return ('# API Response Information\n\n## Response Structure\n\n'
            '## Original Response\n\n' + _json.dumps(body, ensure_ascii=False))


class ComboCompositionTests(unittest.TestCase):
    """MCD-303: combo defaults should be shared; choice counts are store-level."""

    def round(self, name, choices, defaults):
        return {'name': name, 'quantity': 1, 'maxQuantity': 1, 'choices': [
            {'name': c, 'isDefault': 1 if c in defaults else 0} for c in choices]}

    def test_parses_rounds_and_defaults(self):
        raw = _detail_payload('测试三件套', [
            self.round('选择饮料', ['可乐中杯', '雪碧中杯'], ['可乐中杯'])])
        parsed = parse_meal_detail(raw)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed['name'], '测试三件套')
        self.assertEqual(parsed['rounds'][0]['defaults'], ['可乐中杯'])
        self.assertEqual(parsed['rounds'][0]['choice_count'], 2)

    def test_failed_response_is_not_a_finding(self):
        # Our own wrong argument (productCode) once made this return 400; the tool
        # must surface "unreadable", never invent a provider fault.
        self.assertIsNone(parse_meal_detail(_detail_payload('X', [], success=False)))
        self.assertIsNone(parse_meal_detail(None))

    def test_single_item_has_no_rounds(self):
        parsed = parse_meal_detail(_detail_payload('巨无霸', []))
        self.assertEqual(parsed['rounds'], [])

    def test_identical_defaults_across_stores(self):
        snap = {'mode': 'fixture', 'stores': [], 'sample_count': 0}
        menus = {}
        # Same defaults everywhere, differing number of drink options: an expected
        # stocking-range difference, reported as identical_defaults=True.
        variants = [['可乐中杯', '雪碧中杯'], ['可乐中杯'], ['可乐中杯', '雪碧中杯', '橙汁']]
        for i, opts in enumerate(variants):
            code = str(1000 + i)
            snap['stores'].append({'storeCode': code, 'storeName': f'店{i}',
                                   'menu': {'success': True, 'data': {'meals': {'C1': {
                                       'code': 'C1', 'name': '测试套餐', 'currentPrice': 30.0}}}},
                                   'details': {'C1': _detail_payload('测试套餐', [
                                       self.round('选择饮料', opts, ['可乐中杯'])])},
                                   'prices': {}})
            menus[code] = extract_menu(snap['stores'][-1]['menu'])
        rep = compare(snap)
        row = rep['combo_rows'][0]
        self.assertTrue(row['is_combo'])
        self.assertTrue(row['identical_defaults'])
        self.assertTrue(row['choice_range_differs'])
        self.assertIn('默认选件跨店完全一致', markdown(rep))

    def test_bundle_skus_prefers_combo_names(self):
        menus = {'a': {'S1': {'name': '巨无霸', 'menu_price_cents': 2500},
                       'C1': {'name': '麦香鱼套餐', 'menu_price_cents': 3000},
                       'C2': {'name': '开心乐园餐', 'menu_price_cents': 2500}},
                 'b': {'S1': {'name': '巨无霸', 'menu_price_cents': 2500},
                       'C1': {'name': '麦香鱼套餐', 'menu_price_cents': 3100},
                       'C2': {'name': '开心乐园餐', 'menu_price_cents': 2500}}}
        self.assertEqual(bundle_skus(menus, 3), ['C1', 'C2'])

    def test_unmatched_reasons_are_split(self):
        missing = {'某某套餐', '联名棒球帽', '限定新品堡'}
        b = unmatched_reason_counts(missing)
        self.assertEqual((b['bundle'], b['merch'], b['other']), (1, 1, 1))
        self.assertEqual(b['other_examples'], ['限定新品堡'])


if __name__=='__main__':unittest.main()
