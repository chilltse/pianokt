#!/usr/bin/env python3
"""Run production alignment scenarios and write a self-contained timeline report."""
from __future__ import annotations

import argparse
import html
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pianokt_backend.alignment.visual_cases import CASES, run_case  # noqa: E402

REJECT_REASON = {0: 'correct', 1: 'missed', 2: 'wrong pitch', 3: 'timing error'}


def report_case(case, result, result_href):
    notes = result['notes']; diagnostics = result['diagnostics']
    maximum = max([1, *(float(n.get('ref_onset_sec') or 0) for n in notes), *(float(n.get('performance_onset_sec') or 0) for n in notes)])
    rows = []
    for note in notes:
        ref, perf = note.get('ref_onset_sec'), note.get('performance_onset_sec')
        status = note['alignment_type']
        timing = note.get('raw_timing_deviation_sec')
        def marker(value, lane, label):
            if value is None: return ''
            return f'<span class="note {lane} {status}" style="left:{100*float(value)/maximum:.2f}%" title="{html.escape(label)}"></span>'
        reason = REJECT_REASON.get(note.get('reject_reason'), 'unknown')
        label = f"{status}; {reason}; ref={ref}; performance={perf}; raw timing={timing}"
        rows.append(f'<div class="case-row"><div class="case-meta"><b>{status}</b> · {reason}<br>R {note.get("ref_pitch", "–")} → P {note.get("performance_pitch", "–")}<br>{"" if timing is None else f"{float(timing)*1000:+.0f} ms"}</div><div class="track">{marker(ref, "reference", label)}{marker(perf, "performance", label)}</div></div>')
    quality = result['summary']['alignment_quality_valid']
    return f'''<section><h2>{html.escape(case.title)} <small class="{'ok' if quality else 'bad'}">{'有效' if quality else '拒绝'}</small></h2><p>{html.escape(case.intent)} <a href="{html.escape(result_href)}">查看真实 result.json</a></p><p class="diagnostics">window={diagnostics['observed_end_sec']:.3f}s · ref observed/total={diagnostics['reference_notes_observed']}/{diagnostics['reference_notes_total']} · raw median={diagnostics['raw_timing_median_sec']} · raw MAD={diagnostics['raw_timing_mad_sec']} · {html.escape(result['summary']['alignment_quality_reason'])}</p><div class="legend"><i class="reference"></i> reference <i class="performance"></i> performance；<b>match = 时间路径配对</b>，音高正确性见后面的 correct / wrong pitch；marker border: match / deletion / insertion</div>{''.join(rows)}</section>'''


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--output', type=Path, default=Path('artifacts/alignment-cases.html')); args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='pianokt-alignment-cases-') as directory:
        results = [(case, run_case(case, Path(directory))) for case in CASES]
    results_dir = args.output.parent / 'alignment-results'
    for case, result in results:
        destination = results_dir / case.id / 'result.json'
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    body = ''.join(report_case(case, result, f'alignment-results/{case.id}/result.json') for case, result in results)
    page = f'''<!doctype html><meta charset="utf-8"><title>PianoKT Alignment Cases</title><style>body{{font:14px system-ui;margin:32px;max-width:1200px;color:#202124}}h1{{font-size:26px}}section{{border-top:1px solid #ddd;padding:18px 0}}h2{{margin:0}}small{{font-size:12px;padding:3px 6px;border-radius:4px}}.ok{{background:#d9f5df}}.bad{{background:#ffe1df}}.diagnostics{{color:#555}}.legend{{font-size:12px;margin:12px 0}}i{{display:inline-block;width:12px;height:12px;margin:0 4px -2px 12px}}i.reference,.note.reference{{background:#2563eb}}i.performance,.note.performance{{background:#d97706}}.case-row{{display:flex;min-height:34px;margin:3px 0}}.case-meta{{width:190px;font-size:12px}}.track{{position:relative;flex:1;border-bottom:1px solid #ddd;background:repeating-linear-gradient(90deg,#fafafa 0,#fafafa calc(10% - 1px),#e5e5e5 calc(10% - 1px),#e5e5e5 10%);min-height:30px}}.note{{position:absolute;top:7px;width:12px;height:16px;transform:translateX(-50%);border:2px solid #111;box-sizing:border-box}}.note.deletion{{border-style:dashed}}.note.insertion{{border-radius:50%}}</style><h1>PianoKT 对齐场景实验室</h1><p>由生产 <code>align()</code> 生成。横轴为 snapshot-relative 秒；蓝色为 reference，橙色为 performance。悬停标记可查看原始 timing。</p>{body}'''
    args.output.parent.mkdir(parents=True, exist_ok=True); args.output.write_text(page, encoding='utf-8')
    print(args.output.resolve()); print(results_dir.resolve())

if __name__ == '__main__': main()
