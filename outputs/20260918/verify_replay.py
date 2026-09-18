import json
import re
from pathlib import Path
from game_solving.visualization import generate_report

root = Path(__file__).resolve().parent
lines = ['# 标准策略与逐轮状态', '', '带宽单位 kbps。精确参考仅指固定环境下有限候选集合中的总 H 最优。', '', '[4 人交互回放](strategy_exact_detail.html) · [100 人交互回放](strategy_qoe_detail.html)', '']
for kind in ('exact', 'qoe'):
    source = root / f'strategy_{kind}_solve'
    read = lambda name: [json.loads(x) for x in (source / name).read_text(encoding='utf-8').splitlines()]
    results = read('solve_results.jsonl')
    refs = read('reference_results.jsonl')
    snapshots = read('solver_inputs.jsonl')
    traces = read('iteration_trace.jsonl')
    for result, ref, snapshot in zip(results, refs, snapshots):
        assert ref['policy_witness_validated']
        assert abs(sum(a['h'] for a in ref['policy_decisions']) - ref['H_reference']) < 1e-9
        turns = [t for t in traces if t['scene_id'] == result['scene_id']]
        assert len(turns) == result['iterations']
        for t in turns:
            assert abs(t['H'] - sum(u['allocated']['H'] for u in t['users'])) < 1e-9
            for u in t['users']:
                winner = max(u['candidates'], key=lambda a: (a['selection_score'], a['anchor'], -a['bandwidth']['ul'] - a['bandwidth']['dl'], a['action_id']))
                assert winner['action_id'] == u['requested']['action_id']
            # Replay coordination changes to prove the recorded path leads to allocation.
            state = {u['user_id']: u['requested']['action_id'] for u in t['users']}
            for event in t['coordination_events']:
                assert abs(event['net_benefit'] - sum(c['after']['h'] - c['before']['h'] for c in event['changes'])) < 1e-9
                for c in event['changes']:
                    assert state[c['user_id']] == c['before']['action_id']
                    state[c['user_id']] = c['after']['action_id']
            assert state == {u['user_id']: u['allocated']['action_id'] for u in t['users']}
        if kind != 'exact':
            continue
        assert ref['complete'] and ref['status'] == 'exact_discrete'
        lines += [f'## {result["scene_id"]}', '', f'候选数量：{ref["candidate_counts"]}；暴搜 H={ref["H_reference"]:.9f}；算法 H={ref["H_algorithm"]:.9f}；差距={ref["H_gap"]:.9f}。', '', '| 用户 | 套餐 | 初始下行 | 算法下行 | 暴搜下行 | 算法 MOS | 暴搜 MOS | 暴搜个人 H |', '|---|---|---:|---:|---:|---:|---:|---:|']
        for u,a,b in zip(snapshot['users'],result['decisions'],ref['policy_decisions']):
            lines.append(f'| {u["user_id"]} | {u["package"]} | {u["current"]["dl"]:.0f} | {a["bandwidth"]["dl"]:.0f} | {b["bandwidth"]["dl"]:.0f} | {a["mos"]:.6f} | {b["mos"]:.6f} | {b["h"]:.9f} |')
        lines += ['', '以上各用户的上行均为 100 kbps。下表的数组按 user_00000～user_00003 排列。', '', '| 轮次 | 请求下行数组 | 协调后下行数组 | 本轮 H | 历史最好 H | 下行价格 | 稳定计数 |', '|---:|---|---|---:|---:|---:|---:|']
        for t in turns:
            requested = [int(u['requested']['bandwidth']['dl']) for u in t['users']]
            allocated = [int(u['allocated']['bandwidth']['dl']) for u in t['users']]
            lines.append(f'| {t["iteration"]} | {requested} | {allocated} | {t["H"]:.9f} | {t["best_H"]:.9f} | {t["prices"][1]:.9f} | {t["stable_count"]} |')
        lines.append('')
    report = root / f'strategy_{kind}_detail.html'
    generate_report(source, report)
    document = report.read_text(encoding='utf-8')
    scripts = re.findall(r'<script>(.*?)</script>', document, re.S)
    (root / f'check_{kind}.js').write_text('\n'.join(scripts), encoding='utf-8')
    browser_test = '''<script>
try {
 let rounds=0;
 for(let i=0;i<scenes.length;i++){
   $('scene').selectedIndex=i;show();
   if($('strategy-answer').querySelectorAll('tbody tr').length!==scenes[i].users.length*4)throw Error('missing strategies');
   for(let k=0;k<scenes[i].detail_trace.length;k++){
     $('round-select').selectedIndex=k;$('round-select').onchange();
     if($('round-users').querySelectorAll('tbody tr').length!==scenes[i].users.length)throw Error('missing users');
     if(!$('candidate-actions').querySelector('tbody tr'))throw Error('missing candidates');
     rounds++;
   }
 }
 $('scene').selectedIndex=0;show();
 $('round-next').onclick();if($('round-select').selectedIndex!==1)throw Error('next round');
 $('round-prev').onclick();if($('round-select').selectedIndex!==0)throw Error('previous round');
 $('candidate-user').selectedIndex=1;$('candidate-user').onchange();
 $('next').onclick();if($('scene').selectedIndex!==1)throw Error('next scene');
 $('prev').onclick();if($('scene').selectedIndex!==0)throw Error('previous scene');
 document.body.dataset.replayTest='PASS '+rounds+' rounds';
} catch(e) {document.body.dataset.replayTest='FAIL '+e.stack;}
</script>'''
    (root / f'browser_check_{kind}.html').write_text(document.replace('</body>', browser_test+'</body>'), encoding='utf-8')
    print(kind, len(traces), 'rounds: all request winners, coordination transitions, utility totals and reference witnesses validated')
(root / 'strategy_walkthrough.md').write_text('\n'.join(lines), encoding='utf-8')
