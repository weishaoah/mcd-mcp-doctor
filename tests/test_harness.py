"""Regression tests for the agent-correctness harness.

The most important test here is the reverse one: on the two NORMAL scenarios the
Doctor layer must stay quiet. A tool that catches everything by alarming on
everything is worthless.
"""
import copy
import json
import unittest

from doctor.core import unwrap
from harness.mock_mcp import MockMcpServer, load_scenarios, envelope, WRITE_TOOLS
from harness.adapter import DemoAgent
from harness.evaluator import audit, score, extract_amounts_yuan
from harness.runner import run_all


def scenario(cid):
    return next(s for s in load_scenarios() if s['id'] == cid)


class EnvelopeTests(unittest.TestCase):
    def test_mock_responses_go_through_the_real_parse_path(self):
        # The glossary contains a literal {code} — exactly what breaks naive
        # brace matching in production. Mocks must reproduce it, not bypass it.
        text = envelope({'success': True, 'code': 200, 'data': {'price': 2550}})
        self.assertIn('{code}', text)
        parsed = unwrap(text)
        self.assertEqual((parsed.get('data') or {}).get('price'), 2550)


class GuardTests(unittest.TestCase):
    def test_write_tool_executes_without_guard(self):
        mcp = MockMcpServer(scenario('C10'))
        mcp.call_tool('create-order', {'storeCode': '1450713'})
        self.assertEqual([c for c in mcp.calls if not c['blocked']][0]['tool'], 'create-order')
        self.assertEqual(mcp.blocked, [])

    def test_write_tool_is_blocked_with_guard(self):
        mcp = MockMcpServer(scenario('C10'))
        mcp.guard_writes = True
        out = mcp.call_tool('create-order', {'storeCode': '1450713'})
        self.assertEqual(len(mcp.blocked), 1)
        # Blocked calls are still on record, but flagged as not executed.
        self.assertTrue(mcp.calls[0]['blocked'])
        self.assertIn('blocked', json.loads(out).get('message', ''))


class EvaluatorTests(unittest.TestCase):
    def test_amount_extraction_handles_yuan_and_cents(self):
        self.assertIn(14.0, extract_amounts_yuan('麦乐鸡 14.00 元'))
        self.assertIn(14.0, extract_amounts_yuan('price 1400 分'))

    def test_unit_trap_is_detected(self):
        state = {'intent': 'quote', 'anchor': 'quote-raw', 'quote_raw': 1400,
                 'quote_business_ok': True, 'quote_product_list': [{'productCode': '1401'}]}
        ids = [f['id'] for f in audit(state, [])]
        self.assertIn('UNIT-001', ids)

    def test_silent_zero_quote_is_detected(self):
        state = {'intent': 'quote', 'quote_raw': 0, 'quote_product_list': [],
                 'quote_business_ok': True, 'menu_price_yuan': 25.5}
        ids = [f['id'] for f in audit(state, [])]
        self.assertIn('QUOTE-002', ids)

    def test_business_failure_is_detected(self):
        state = {'intent': 'quote', 'quote_business_ok': False}
        ids = [f['id'] for f in audit(state, [])]
        self.assertIn('QUOTE-001', ids)

    def test_unauthorized_write_is_detected(self):
        ids = [f['id'] for f in audit({}, [{'tool': 'create-order', 'blocked': False}])]
        self.assertIn('AUTH-001', ids)

    def test_blocked_write_does_not_fail_the_case(self):
        res = score('门店有巨无霸、麦乐鸡。', {},
                    [{'tool': 'query-meals', 'blocked': False},
                     {'tool': 'create-order', 'blocked': True}],
                    [{'tool': 'create-order'}],
                    {'forbidden_tools': sorted(WRITE_TOOLS)})
        self.assertEqual(res['verdict'], 'PASS')
        self.assertTrue(any('拦截' in r for r in res['reasons']))


class OverAlarmTests(unittest.TestCase):
    """Reverse tests: on normal scenarios the Doctor must NOT raise anything."""

    def test_normal_scenarios_produce_no_doctor_findings(self):
        for cid in ('C01', 'C02'):
            sc = scenario(cid)
            mcp = MockMcpServer(sc)
            run = DemoAgent().run(sc, mcp)
            self.assertEqual(audit(run.state, run.tool_calls), [], f'{cid} 触发了误报')

    def test_cross_store_price_gap_is_not_a_fault(self):
        # Same SKU, different store, different price: a legitimate difference.
        sc = scenario('C02')
        mcp = MockMcpServer(sc)
        run = DemoAgent().run(sc, mcp)
        res = score(run.answer, run.state, run.tool_calls, [], sc['expected'])
        self.assertEqual(res['verdict'], 'PASS')
        self.assertNotIn('故障', run.answer)


class DeterminismTests(unittest.TestCase):
    def test_two_runs_are_identical(self):
        # Everything except the wall-clock stamp must be byte-identical.
        a, b = run_all(), run_all()
        a.pop('generated_at'), b.pop('generated_at')
        self.assertEqual(json.dumps(a, sort_keys=True, ensure_ascii=False),
                         json.dumps(b, sort_keys=True, ensure_ascii=False))

    def test_no_token_needed(self):
        # Every case must run offline: no network, no credentials.
        results = run_all()
        self.assertEqual(results['summary']['total'], 10)
        self.assertEqual(results['agent'], 'DemoAgent (deterministic, NOT a model)')


class HonestyTests(unittest.TestCase):
    def test_demo_agent_is_not_described_as_a_model(self):
        results = run_all()
        self.assertIn('DEMO', results['agent'].upper())

    def test_scenario_truth_is_independent_of_agent_output(self):
        for sc in load_scenarios():
            self.assertIn('expected', sc, f"{sc['id']} 缺少独立真值")
            self.assertIn('rationale', sc['expected'])


if __name__ == '__main__':
    unittest.main()
