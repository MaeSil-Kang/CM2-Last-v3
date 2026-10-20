# -*- coding: utf-8 -*-
"""calc.decide_bosang 규칙 검증 (폭변경/길이변경/롤포장/둘다/짧은롤)."""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, ".")
from jipok import calc

WJ = [  # LOT339 완정공정(가로,길이) 일부
    {"garo": 390, "gil": 12000}, {"garo": 390, "gil": 6000},
    {"garo": 460, "gil": 12000}, {"garo": 480, "gil": 8000}, {"garo": 480, "gil": 12000},
    {"garo": 487, "gil": 15000}, {"garo": 520, "gil": 15000}, {"garo": 403, "gil": 8000},
]

cases = [
    # (설명, 정길이, 규격, 기대보상)
    ("순서9 390x4 (완정 390→12000有)",  12000, [{"w": 390, "n": 4}], 170),   # 폭변경
    ("순서10 520x3 (완정 520→15000有)", 15000, [{"w": 520, "n": 3}], 170),   # 폭변경
    ("롤포장 1114x1 (완정 매칭 길이同)", 12000, [{"w": 1114, "n": 1}], 120),  # 그 외
    ("길이변경 403x1 (완정 403→8000뿐)", 12000, [{"w": 403, "n": 1}], 240),   # 길이변경
    ("둘다 403x3 (배수≥2 & 길이다름)",   12000, [{"w": 403, "n": 3}], 240),   # max(170,240)
    ("짧은롤 8000",                      8000,  [{"w": 403, "n": 4}], 120),    # <10000
    ("완정 매칭 없음(보수적 120)",       12000, [{"w": 9999, "n": 1}], 120),
]

ok = True
for desc, jeong, gy, exp in cases:
    got = calc.decide_bosang(jeong, gy, WJ)
    mark = "PASS" if got == exp else "FAIL"
    if got != exp:
        ok = False
    print(f"[{mark}] {desc:<34} → {got} (기대 {exp})")
print("\n전체:", "ALL PASS" if ok else "FAIL")
