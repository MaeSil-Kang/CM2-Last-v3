# -*- coding: utf-8 -*-
"""검증 케이스: LOT 2260600339 (한솔감열지 SF55, 초지공정 1,140,590).

작업자 답지(2026/05/19) 구조와 일치해야 한다:
  - 순번 건너뛰기 병합 없음(인접 병합만, 그것도 권취<CP중량11일 때만).
  - 순서5+6 인접 병합, 순서9(13마끼)는 7+6 균등 분할.
  - 각 스풀 CP중량 11~33, 총생산길이 >= 초지공정길이.
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, ".")
from jipok.models import Order
from jipok import pack, calc

CP, CHOJI = 55, 1140590
# (순서, 정길이, 권취, 23요청폭, 보상)
DATA = [(1,12000,7,4990,240), (2,15000,21,4980,170), (3,12000,4,4980,120),
        (4,15000,7,4950,170), (5,12000,3,4900,240), (6,12000,5,4900,240),
        (7,15000,5,4920,240), (8,12000,8,4880,120), (9,12000,13,4880,240),
        (10,15000,7,4880,170)]

orders = [Order(*d) for d in DATA]
rows, total = pack.schedule(orders, CP, False, CHOJI)

print("=== 생성 지폭스케줄 (LOT 2260600339) ===")
for r in rows:
    print(f"  {r.width}  {r.choji_real:>8,}  ×{r.spools}  {r.bigo()}")
print(f"총생산길이 {total:,}  (초지 {CHOJI:,} 대비 {total-CHOJI:+,})")

# --- 불변식 검증 ---
fails = []
# 1) 권취 보존
got = {}
for r in rows:
    for o, _j, _b, c in r.contribs:
        got[o] = got.get(o, 0) + c
# schedule()이 내부에서 폭 내림차순으로 재번호(rank_orders)하므로, 비교 기준도
# 같은 재번호 매핑을 써야 한다(원본 idx가 아니라).
want = {o.idx: o.kwonchwi for o in pack.rank_orders(orders)}
if got != want:
    fails.append(f"권취 불일치: {got} != {want}")
# 2) 모든 스풀 CP중량 11~33
for r in rows:
    js = calc.wonji_jeonsan(r.jeong, r.bosang, r.N, CP, False)
    w = calc.cp_weight(CP, js, r.width)
    if not (11.0 <= w <= 33.0):
        fails.append(f"CP중량 위반 {r.width}/{r.jeong}: {w:.1f}")
# 3) 총생산 >= 초지
if total < CHOJI:
    fails.append(f"총생산({total:,}) < 초지({CHOJI:,})")
# 4) 순번 건너뛰기 병합 없음 — 각 행의 기여 순번은 연속이어야
for r in rows:
    ids = [o for o, _j, _b, _c in r.contribs]
    if any(ids[i+1] - ids[i] != 1 for i in range(len(ids)-1)):
        fails.append(f"건너뛰기 병합 발견: {ids}")

print("\n불변식:", "ALL PASS" if not fails else "FAIL")
for f in fails:
    print("  -", f)
