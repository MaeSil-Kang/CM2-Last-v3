# -*- coding: utf-8 -*-
"""LOT 2260701966 (한솔감열지SF 장항 44(34), CP44, 초지 695,848) 회귀 테스트.

원본: 지폭 PDF/PDF Test_260806/7월/5.pdf   답지 시트: 26년7월10일SF44
답지(4행·6스플·총 732,000):
  5010 116,200 x2 (순1)16,000*2+여유3+(순2)16,000*9   231,000
  4990 132,400 x1 (순3) 8,000*16                     131,700
  4960 116,200 x2 (순3) 8,000*25+여유1+(순4)8,000*2    231,000
  4950 139,000 x1 (순5)22,500*6                      138,300

2026-09 수정 4건을 고정한다:
  1) 규격 배수 오독 보정 → 순1=170, 순2=240 (오독 시 120/120이었음)
  2) CP중량 상한 가드 → 어떤 모드에서도 33.3 초과 금지 (기존 answer_v3에서 36.08 발생)
  3) 폭 평활화를 행 단위로(answer_v4)
  4) 답지식 균등 분할 — 한 행의 스풀은 같은 마끼수(answer_v4)
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, ".")
from jipok.models import Order
from jipok import pack, calc
from jipok.gemini_parse import _fix_gyukyeok

CP, TG, CHOJI = 44, False, 695848
# PDF RE공정: (순서, 정길이, 권취, 지폭)
RAW = [(1, 16000, 2, 4925), (2, 16000, 9, 4920), (3, 8000, 41, 4875),
       (4, 8000, 2, 4875), (5, 22500, 6, 4865)]
# PDF 완정공정(물류입고) 가로/길이
WJ = [{"garo": 400, "gil": 8000}, {"garo": 485, "gil": 16000}, {"garo": 800, "gil": 11250},
      {"garo": 1035, "gil": 16000}, {"garo": 1355, "gil": 22500}]
# Gemini가 규격 칸의 '합계폭'만 읽고 배수를 놓친 상태(실제 발생한 오독)
BAD = {1: [1480, 1480, 1965], 2: [1035, 1035, 1625, 1225], 3: [1625, 1625, 1625],
       4: [1625, 1625, 1625], 5: [1355, 1355, 1355, 800]}
WANT_BOSANG = {1: 170, 2: 240, 3: 120, 4: 120, 5: 240}   # 규칙상 정답(짧은롤 3·4는 120)

fails = []

# ── 1) 규격 배수 오독 보정 ────────────────────────────────────────────────
data = {"orders": [{"sunseo": i, "jeong": j,
                    "gyukyeok": [{"w": w, "n": 1} for w in BAD[i]]}
                   for i, j, _k, _p in RAW]}
warns = []
_fix_gyukyeok(data, WJ, warns)
got_b = {}
for o in data["orders"]:
    i = o["sunseo"]
    got_b[i] = calc.decide_bosang(o["jeong"], o["gyukyeok"], WJ, CP, TG)
print("규격 보정 경고:")
for w in warns:
    print("   ", w)
print(f"보정 후 보상: {got_b}  (기대 {WANT_BOSANG})")
if got_b != WANT_BOSANG:
    fails.append(f"보상 보정 실패: {got_b} != {WANT_BOSANG}")

# ── 2~4) 편성 검증 ───────────────────────────────────────────────────────
raw_w = [calc.request_width(p, CP, TG) for _i, _j, _k, p in RAW]
widths = calc.smooth_request_widths(raw_w)
print(f"\n원시 23요청폭 {raw_w} → 평활화 {widths}")
orders = [Order(i, j, k, w, WANT_BOSANG[i], rw)
          for (i, j, k, _p), w, rw in zip(RAW, widths, raw_w)]

for mode in ("answer_v4", "answer_v3", "answer_v2", "answer_o1", "answer", "loss", "convenience"):
    rows, total = pack.schedule(orders, CP, TG, CHOJI, mode=mode)
    print(f"\n=== {mode}  {len(rows)}행 {sum(r.spools for r in rows)}스플  "
          f"총생산 {total:,} ({total - CHOJI:+,})")
    for r in rows:
        w = calc.cp_weight(CP, r.saengsan / r.spools, r.width)
        print(f"   {r.width} {r.choji_real:>8,} x{r.spools:<2} {r.bigo():<58} "
              f"{r.saengsan:>9,} w={w:5.2f}")
        # (2) CP중량 상한 — 절대규칙
        if w > calc.C.CP_WEIGHT_MAX + 1e-9:
            fails.append(f"[{mode}] CP중량 상한 위반 {w:.2f} (폭{r.width} {r.bigo()!r})")
        if w < calc.C.CP_WEIGHT_MIN - 1e-9:
            fails.append(f"[{mode}] CP중량 하한 위반 {w:.2f} (폭{r.width} {r.bigo()!r})")
        if not r.contribs:
            fails.append(f"[{mode}] 주문 없는 순수 여유 행: 폭{r.width} {r.bigo()!r}")
        # (4) 한 행의 스풀은 같은 마끼수 → 총 마끼가 스플수로 나누어떨어져야 한다
        makki = sum(c for _o, _j, _b, c in r.contribs) + r.yeoyu
        if mode == "answer_v4" and makki % r.spools != 0:
            fails.append(f"[{mode}] 행 마끼 {makki}가 스플수 {r.spools}로 안 나뉨")
    # 권취 보존
    got = {}
    for r in rows:
        for o, _j, _b, c in r.contribs:
            got[o] = got.get(o, 0) + c
    want = {o.idx: o.kwonchwi for o in pack.rank_orders(orders)}
    if got != want:
        fails.append(f"[{mode}] 권취 불일치: {got} != {want}")
    if total < CHOJI:
        fails.append(f"[{mode}] 총생산({total:,}) < 초지({CHOJI:,})")

# (3) 행 단위 폭 평활화 — answer_v4의 인접 행 폭 하락은 30 이하
rows_v4, _t = pack.schedule(orders, CP, TG, CHOJI, mode="answer_v4")
ws = [r.width for r in rows_v4]
drops = [ws[i] - ws[i + 1] for i in range(len(ws) - 1)]
print(f"\nanswer_v4 행 폭: {ws}  하락폭: {drops} (허용 <= {calc.ADJ_MAX_DROP})")
if any(d > calc.ADJ_MAX_DROP for d in drops):
    fails.append(f"행 폭 하락 {drops}가 {calc.ADJ_MAX_DROP} 초과")

print("\n전체:", "ALL PASS" if not fails else "FAIL")
for f in fails:
    print("  -", f)
if fails:
    sys.exit(1)
