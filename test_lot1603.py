# -*- coding: utf-8 -*-
"""answer_v5(답지 폭반올림) 회귀 테스트 — LOT 2261001603 답지 전행 재현.

이 모드가 존재하는 이유(답지 PDF '261014 Test/9.pdf' 역산):
  · 기본 폭 규칙은 10단위 **내림**이라 순서1(지폭4870→4955→4950)과
    순서2(지폭4866→4951→4950)가 같은 폭이 된다 → 한 스트림으로 병합되고
    121+31=152가 완스풀 8로 딱 나누어떨어져 **여유분이 아예 필요 없어진다**.
  · 답지는 두 주문을 4960/4950으로 갈라 각자 완스풀을 만들었다
    (121%8=1 → 여유7, 31%8=7 → 여유1).
  · 또 순서2 자투리 7마끼(4950)와 순서4 자투리 5마끼(4940)를 섞지 않았다.
고정하는 것: ①23요청폭 반올림 ②폭이 같을 때만 자투리 병합 ③완스풀까지 1마끼 이하
남은 자투리만 여유분으로 채움 ④총생산 상한을 초지 비례(0.62%)로.
"""
import sys
sys.path.insert(0, ".")
from jipok import calc, pack
from jipok.models import Order

CP, TG, CHOJI = 48, False, 3694223
JIPOK = [4870, 4866, 4859, 4859, 4850]
JEONG = [16700, 16700, 18500, 16700, 13500]
KWON = [121, 31, 7, 37, 16]
ANS_TOTAL = 3713200
ANS = [                      # (폭, 스플수, [(순서, 회수), ...], 여유분)
    (4960, 16, [(1, 121)], 7),
    (4950, 4, [(2, 31)], 1),
    (4940, 1, [(3, 7)], 0),
    (4940, 4, [(4, 32)], 0),
    (4940, 1, [(4, 5)], 0),
    (4940, 2, [(5, 16)], 0),
]

raw = [calc.request_width(j, CP, TG) for j in JIPOK]
orders = [Order(i + 1, JEONG[i], KWON[i], w, 120, raw[i], JIPOK[i])
          for i, w in enumerate(calc.smooth_request_widths(raw))]

fails = []


def key(rows):
    out = []
    for r in rows:
        agg = {}
        for oid, _j, _b, c in r.contribs:
            agg[oid] = agg.get(oid, 0) + c
        out.append((r.width, r.spools, sorted(agg.items()), r.yeoyu))
    return out


rows, total = pack.schedule(orders, CP, TG, CHOJI, mode="answer_v5")
got = key(rows)
want = [(w, s, sorted(c), y) for w, s, c, y in ANS]

print(f"{'':2}{'기대(답지)':<44}{'실제(answer_v5)':<44}")
for i in range(max(len(want), len(got))):
    a = want[i] if i < len(want) else None
    b = got[i] if i < len(got) else None
    ok = a == b
    if not ok:
        fails.append(f"행{i+1}: 기대 {a} / 실제 {b}")
    print(f"{'OK' if ok else '!!':<2}{str(a):<44}{str(b):<44}")

if total != ANS_TOTAL:
    fails.append(f"총생산 {total:,} != 답지 {ANS_TOTAL:,}")
print(f"\n총생산 {total:,} (답지 {ANS_TOTAL:,}, 초지 {total-CHOJI:+,})")

# 불변식: CP중량 11~33.3, 총생산 >= 초지
for r in rows:
    w = calc.cp_weight(CP, r.saengsan / r.spools, r.width)
    if not (calc.C.CP_WEIGHT_MIN <= w <= calc.C.CP_WEIGHT_MAX):
        fails.append(f"CP중량 위반 {r.width}: {w:.2f}")
if total < CHOJI:
    fails.append("총생산 < 초지공정길이")

# 다른 모드는 이 모드의 폭 규칙에 오염되면 안 된다(내림 유지 → 순서1 폭 4950)
for m in ("answer", "answer_o1", "answer_v2", "answer_v3", "answer_v4", "loss"):
    r2, _ = pack.schedule(orders, CP, TG, CHOJI, mode=m)
    if max(x.width for x in r2) != 4950:
        fails.append(f"{m}: 최대폭 {max(x.width for x in r2)} (내림이면 4950이어야 함)")
print("다른 모드 폭 오염 없음:", all("최대폭" not in f for f in fails))

print("\n전체:", "ALL PASS" if not fails else "FAIL")
for f in fails:
    print("  -", f)
if fails:
    sys.exit(1)
