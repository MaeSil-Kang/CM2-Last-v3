# -*- coding: utf-8 -*-
"""CP중량(11~33.3) 절대규칙 회귀 테스트 — 실사용 중 발견된 버그 재발 방지.

2026-08: 사용자가 앱에서 실제 LOT(2260802339, CP44, 16000m x6)을 돌렸을 때
"CP중량 위반 2건"이 떴음. 원인은 두 가지, 둘 다 여기서 검증한다.
  1) app.py 표시버그: 여러 스풀이 합쳐진 행(spools>1)의 CP중량을 '누적 contribs'로
     재계산해 실제보다 spools배 부풀려 보임(엔진 자체는 정상). → r.saengsan/r.spools로
     스풀 1개분만 계산해야 함(이 테스트가 정확한 방법을 강제).
  2) pack.py 실제 버그: topup_yeoyu가 '새 여유 스풀'을 yeoyu=1로 시작해 놓고, 그 1개로
     이미 목표길이를 채우면 루프가 곧장 끝나 CP중량<11인 '고아 스풀'이 실제로 남았음
     (mode="answer_v2"에서 재현). → 답지 전수조사(1,169행)로 확인한 대로 '주문 없는
     순수 여유 행'은 답지에 절대 없음이 확정돼, 그런 스풀을 아예 새로 만들지 않고
     기존 미완 스풀만 채우도록 수정(모두 꽉 찼으면 총생산 하한만 지키고 멈춤 —
     CP중량 우선순위가 총생산 소프트목표보다 위).
  3) pack.py 부수 버그: 위 2)를 고치며 make_spools_crossjeong의 교차-정길이 스풀이
     N(완스풀 용량)을 0으로 방치해 topup의 under() 판정이 '항상 꽉 참'으로 오판 →
     총생산이 초지 밑으로 떨어짐(answer_v2/v3에서 재현). → close()에서 N을 대표
     정길이 기준으로 정확히 계산하도록 수정.
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, ".")
from jipok.models import Order
from jipok import pack, calc

CP, CHOJI = 44, 2220280
DATA = [(1, 16000, 31, 4921, 120), (2, 16000, 12, 4847, 120), (3, 16000, 76, 4846, 120),
        (4, 16000, 3, 4840, 120), (5, 16000, 3, 4764, 120), (6, 16000, 6, 4760, 120)]
orders = [Order(*d) for d in DATA]

def cp_weight_per_spool(r, cp):
    """행(Row) 1개는 spools개의 '동일 구성' 물리 스풀을 나타낸다 — CP중량은 스풀 1개분으로
    검증해야 한다(r.contribs를 그대로 쓰면 spools배 부풀려짐)."""
    return calc.cp_weight(cp, r.saengsan / r.spools, r.width)

fails = []
for mode in ("answer_v3", "answer_v2", "answer_o1", "answer", "loss", "convenience"):
    rows, total = pack.schedule(orders, CP, False, CHOJI, mode=mode)
    for r in rows:
        w = cp_weight_per_spool(r, CP)
        if not (calc.C.CP_WEIGHT_MIN <= w <= calc.C.CP_WEIGHT_MAX):
            fails.append(f"[{mode}] CP중량 위반: 폭{r.width} spools={r.spools} "
                         f"weight={w:.2f} bigo={r.bigo()!r}")
        if not r.contribs:
            fails.append(f"[{mode}] 주문 없는 순수 여유 행(답지에 없는 패턴): 폭{r.width} {r.bigo()!r}")
    if total < CHOJI:
        fails.append(f"[{mode}] 총생산({total:,}) < 초지({CHOJI:,})")
    print(f"{mode:<12} 총생산={total:,}  행수={len(rows)}  스플합={sum(r.spools for r in rows)}")

print("\n검증:", "ALL PASS" if not fails else "FAIL")
for f in fails:
    print("  -", f)
if fails:
    sys.exit(1)
