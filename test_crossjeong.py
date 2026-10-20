# -*- coding: utf-8 -*-
"""교차-정길이 병합 회귀 테스트(answer_v2/answer_v3) — 2026-08 CM2최종학습 실제 LOT로 검증.

LOT 2260200347 (SF55, 1월 130.pdf): 답지 구조
  5010 x1 순서1×5+여유1
  4960 x2 순서2×2+순서3×12+여유2
  4920 x1 순서4×6
  4880 x1 순서5×4+순서6(15000m)×4   <- 교차-정길이 병합(정12000+정15000 한 스풀)
  4880 x1 순서6×7
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, ".")
from jipok.models import Order
from jipok import pack, calc

CP, TEUKGAM, CHOJI = 55, False, 549043
# (idx, jeong, kwon, jipok원폭)
RAW = [(1, 12000, 5, 4938), (2, 12000, 2, 4880), (3, 12000, 12, 4860),
       (4, 12000, 6, 4824), (5, 12000, 4, 4800), (6, 15000, 11, 4768)]

raw_w = [calc.request_width(jp, CP, TEUKGAM) for _, _, _, jp in RAW]
widths = calc.smooth_request_widths(raw_w)
orders = [Order(idx, jeong, kwon, w, 120) for (idx, jeong, kwon, _), w in zip(RAW, widths)]

print("23요청폭:", widths)

all_fails = []
for mode in ("answer_v2", "answer_v3"):
    rows, total = pack.schedule(orders, CP, TEUKGAM, CHOJI, mode=mode)
    print(f"\n=== {mode} ===")
    for r in rows:
        w = calc.cp_weight(CP, r.saengsan / r.spools, r.width)
        print(f"  {r.width}  x{r.spools:<2} {r.bigo()}  (혼합={r.mixed}, weight={w:.1f})")
    print(f"총생산길이 {total:,}  (초지 {CHOJI:,} 대비 {total-CHOJI:+,})")

    fails = []
    # 1) 권취 보존
    got = {}
    for r in rows:
        for o, _j, _b, c in r.contribs:
            got[o] = got.get(o, 0) + c
    want = {o.idx: o.kwonchwi for o in pack.rank_orders(orders)}
    if got != want:
        fails.append(f"권취 불일치: {got} != {want}")
    # 2) 순서5·순서6이 정길이 다른데 한 행(혼합)으로 병합됐는지(★핵심★)
    mixed_rows = [r for r in rows if r.mixed]
    has_5_6_mix = any({c[0] for c in r.contribs} >= {5, 6} for r in mixed_rows)
    if not has_5_6_mix:
        fails.append(f"순서5+6 교차-정길이 병합 재현 실패: 혼합행={[(r.width, r.bigo()) for r in mixed_rows]}")
    # 3) 모든 스풀 CP중량 11~33.3(행이 여러 스풀 병합이면 saengsan/spools = 스풀 1개당 전산길이)
    for r in rows:
        w = calc.cp_weight(CP, r.saengsan / r.spools, r.width)
        if not (11.0 <= w <= calc.C.CP_WEIGHT_MAX):
            fails.append(f"CP중량 위반 {r.width}: {w:.1f}")
    # 4) 총생산 >= 초지
    if total < CHOJI:
        fails.append(f"총생산({total:,}) < 초지({CHOJI:,})")

    print(f"[{mode}] 검증:", "PASS" if not fails else "FAIL")
    for f in fails:
        print("  -", f)
    all_fails += [f"[{mode}] {f}" for f in fails]

print("\n전체 검증:", "ALL PASS" if not all_fails else "FAIL")
if all_fails:
    sys.exit(1)
