"""Run every scenario twice: baseline (no Doctor) and with Doctor.

The ONLY difference between the two groups is the Doctor layer. Prompt, agent,
fixture and task text are byte-identical — the baseline is never handicapped
with a worse prompt.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .mock_mcp import MockMcpServer, load_scenarios, WRITE_TOOLS
from .adapter import DemoAgent
from .evaluator import audit, score, apply_corrections

ROOT = Path(__file__).resolve().parents[1]


def run_group(scenario: dict, with_doctor: bool):
    mcp = MockMcpServer(scenario)
    mcp.guard_writes = with_doctor
    agent = DemoAgent()
    run = agent.run(scenario, mcp)
    answer, findings = run.answer, []
    if with_doctor:
        findings = audit(run.state, run.tool_calls)
        answer = apply_corrections(run.answer, run.state, findings)
    result = score(answer, run.state, run.tool_calls, run.blocked, scenario.get('expected') or {})
    return {'group': 'doctor' if with_doctor else 'baseline',
            'answer': answer, 'answer_raw': run.answer,
            'tool_calls': run.tool_calls, 'blocked': run.blocked,
            'doctor_findings': findings, 'state': run.state,
            'verdict': result['verdict'], 'reasons': result['reasons'],
            'findings': result['findings'], 'amounts_found': result['amounts_found']}


def run_all(path=None):
    scenarios = load_scenarios(path)
    cases = []
    for sc in scenarios:
        base = run_group(sc, with_doctor=False)
        doc = run_group(sc, with_doctor=True)
        cases.append({'id': sc['id'], 'title': sc['title'], 'kind': sc.get('kind'),
                      'severity': sc.get('severity'), 'task': sc.get('task'),
                      'expected': sc.get('expected'), 'baseline': base, 'doctor': doc})
    def reached(case, group):
        """Write calls that actually executed (blocked ones never left the process)."""
        return [t for t in case[group]['tool_calls']
                if t['tool'] in WRITE_TOOLS and not t.get('blocked')]

    def flagged(case, group):
        """Did the Doctor layer raise anything? Measured on both groups."""
        return len(case[group].get('findings') or [])

    fault = [c for c in cases if c['kind'] == 'fault']
    normal = [c for c in cases if c['kind'] == 'normal']
    summary = {
        'total': len(cases), 'fault_cases': len(fault), 'normal_cases': len(normal),
        # Agent correctness on the abnormal cases, alone vs with Doctor.
        'baseline_correct_on_fault': sum(c['baseline']['verdict'] == 'PASS' for c in fault),
        'doctor_correct_on_fault': sum(c['doctor']['verdict'] == 'PASS' for c in fault),
        'doctor_fixed': sum(c['baseline']['verdict'] != 'PASS' and c['doctor']['verdict'] == 'PASS'
                            for c in fault),
        # Doctor's own detection power, and its over-alarm rate.
        'doctor_flagged_on_fault': sum(flagged(c, 'doctor') > 0 for c in fault),
        'doctor_false_alarm_on_normal': sum(flagged(c, 'doctor') > 0 for c in normal),
        # Agent correctness on the normal cases (Doctor must not break them).
        'baseline_correct_on_normal': sum(c['baseline']['verdict'] == 'PASS' for c in normal),
        'doctor_correct_on_normal': sum(c['doctor']['verdict'] == 'PASS' for c in normal),
        # Unauthorized writes that actually executed.
        'baseline_writes_executed': sum(len(reached(c, 'baseline')) for c in cases),
        'doctor_writes_executed': sum(len(reached(c, 'doctor')) for c in cases),
        'doctor_writes_blocked': sum(len(c['doctor']['blocked']) for c in cases),
    }
    return {'generated_at': datetime.now().astimezone().isoformat(),
            'agent': 'DemoAgent (deterministic, NOT a model)',
            'write_tools': sorted(WRITE_TOOLS),
            'summary': summary, 'cases': cases}


def markdown(results: dict) -> str:
    s = results['summary']
    out = ['# McD MCP Doctor · Agent 业务正确性实验', '',
           f"- 生成时间：{results['generated_at']}",
           f"- 被测 Agent：**{results['agent']}**",
           '- 对照设计：两组使用**完全相同的** Agent、Prompt、场景与数据；'
             '唯一变量是 Doctor 校验层是否开启。基线组没有被刻意削弱。',
           '- 真值来源：`fixtures/agent/scenarios.json` 的 `expected` 字段，'
             '独立于 Agent 与 Doctor 的输出；部分场景另有官方 Issue 佐证。',
           '', '## 一、总览', '',
           '| 指标 | 含义 | 基线组 | Doctor 组 |',
           '|---|---|---:|---:|',
           f"| 异常场景回答正确 | Agent 行为符合真值 | "
           f"{s['baseline_correct_on_fault']}/{s['fault_cases']} | "
           f"{s['doctor_correct_on_fault']}/{s['fault_cases']} |",
           f"| 正常场景回答正确 | 不得被 Doctor 改坏 | "
           f"{s['baseline_correct_on_normal']}/{s['normal_cases']} | "
           f"{s['doctor_correct_on_normal']}/{s['normal_cases']} |",
           f"| Doctor 在异常场景报警 | 发现问题（越高越好） | — | "
           f"{s['doctor_flagged_on_fault']}/{s['fault_cases']} |",
           f"| Doctor 在正常场景误报 | 过度报警（越低越好） | — | "
           f"{s['doctor_false_alarm_on_normal']}/{s['normal_cases']} |",
           f"| 未授权写入**实际执行** | 越权操作 | "
           f"{s['baseline_writes_executed']} | {s['doctor_writes_executed']} |",
           f"| 被 Doctor 守卫拦截 | 未执行 | — | {s['doctor_writes_blocked']} |", '',
           f"由基线 FAIL 转为 Doctor PASS 的案例：**{s['doctor_fixed']}** 个。", '',
           '> 口径说明：「Doctor 报警」指校验层在异常场景提出了 finding；',
           '> 「Doctor 误报」指它在**正常场景**也提出了 finding。',
           '> 拦截只统计**实际执行**的写入调用——被守卫挡下的不算已发生。',
           '', '## 二、案例级证据', '']
    for c in results['cases']:
        out += [f"### {c['id']} {c['title']}（严重度：{c['severity']}）", '',
                f"- 任务：{c['task']}",
                f"- 真值：{json.dumps(c['expected'], ensure_ascii=False)}", '']
        for g in ('baseline', 'doctor'):
            r = c[g]
            tools = [t['tool'] for t in r['tool_calls']]
            out += [f"**{g}** — 判定 `{r['verdict']}`", '',
                    f"- 回答：{r['answer']}",
                    f"- 工具调用：{tools}",
                    f"- 理由：{'; '.join(r['reasons']) or '无'}", '']
            if r['doctor_findings']:
                out.append(f"- Doctor 发现：{'; '.join(f['id'] for f in r['doctor_findings'])}")
        out.append('')
    c03 = next((c['baseline']['answer'].rstrip('。') for c in results['cases'] if c['id'] == 'C03'), '')
    out += ['## 三、对本结果的自我质疑（必读）', '',
            '**1. 这是一次自一致性检验，不是独立验证。**',
            '`audit()`（发现问题）、`apply_corrections()`（修正）、`score()`（判定）三段规则由同一作者、'
            '依据同一份 `expected` 真值编写。因此 8/8 证明的是「规则与真值彼此一致」，'
            '**不能**证明 Doctor 在真实环境中一定有效。独立验证需要：换一个人写真值，或接入真实 Agent。', '',
            '**2. 基线 0/8 是被设计出来的，不代表真实 Agent 的错误率。**',
            'DemoAgent 的策略（优先相信菜单价、缺数据就查内置目录、浏览时也顺手确认下单）'
            '是为复现常见捷径而写的，场景又按「能触发这些捷径」选取——相当于先有答案再有考题。'
            '这个数字只说明框架跑通了，**不可外推**。', '',
            '**3. C10 的「修复」是白名单守卫，不是理解。**',
            '按名单拦掉写入类 Tool 是机制性拦截，谁都写得出；它没有检验 Agent 是否真的'
            '理解了「用户只是想看看」。真正难的部分没被测到。', '',
            f"**4. C03 的判据本身可讨论。** 基线回答是「{c03}」，并未输出 0 元，"
            '却被判 FAIL，理由是「未校验核价结果」。这比官方 Issue #16 描述的'
            '「直接报 0 元」更严格，是否算错误可以争论。', '',
            '## 四、结论边界', '',
            '- DemoAgent 是确定性实现，**不是模型**；其表现不得外推为真实 LLM Agent 的表现。',
            '- 全部数据来自本地 Mock 与脱敏 Replay，**不等于线上问题已被发现**。',
            '- PASS 只表示本场景行为符合真值，不代表 Agent 整体正确。',
            '- 未接入真实模型时，本报告不提供任何关于真实 Agent 错误率的结论。', '']
    return '\n'.join(out)


def main():
    results = run_all()
    (ROOT / 'experiment-results.json').write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    (ROOT / 'experiment-report.md').write_text(markdown(results), encoding='utf-8')
    s = results['summary']
    print(f"异常场景正确：基线 {s['baseline_correct_on_fault']}/{s['fault_cases']}，"
          f"Doctor {s['doctor_correct_on_fault']}/{s['fault_cases']}；修复 {s['doctor_fixed']} 例")
    print(f"Doctor 报警 {s['doctor_flagged_on_fault']}/{s['fault_cases']}，"
          f"正常场景误报 {s['doctor_false_alarm_on_normal']}/{s['normal_cases']}")
    print(f"未授权写入实际执行：基线 {s['baseline_writes_executed']}，"
          f"Doctor {s['doctor_writes_executed']}（拦截 {s['doctor_writes_blocked']}）")
    print('已生成 experiment-report.md 与 experiment-results.json')


if __name__ == '__main__':
    main()
