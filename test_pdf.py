# -*- coding: utf-8 -*-
"""테스트 PDF(S36BW-826062613200, LOT 2260102232)로 지폭스케줄 생성."""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, ".")
from jipok.models import Order
from jipok import pack

CHOJI = 4543828
orders = [
    Order(1, 16000, 106, 5010, 120),
    Order(2, 16000, 46, 4990, 120),
    Order(3, 16000, 97, 4940, 120),
    Order(4, 16000, 6, 4940, 120),
    Order(5, 16000, 1, 4930, 120),
    Order(6, 16000, 11, 4930, 120),
]
rows, total = pack.schedule(orders, cp=44, teukgam=False, choji_gongjeong_len=CHOJI)

print("="*92)
print(" 지폭스케줄 (PM23호 생산지폭)  —  한솔감열지 SF 44 / LOT 2260102232")
print("="*92)
print(f"{'원지지폭':>7} {'초지생산실길이':>12} {'스플수':>5}  {'비고':<48} {'생산길이':>11}")
print("-"*92)
for r in rows:
    print(f"{r.width:>7} {r.choji_real:>12,} {r.spools:>5}  {r.bigo():<48} {r.saengsan:>11,}")
print("-"*92)
print(f"{'총생산길이':>7} {total:>54,}   (초지공정길이 {CHOJI:,}, 초과 {total-CHOJI:+,})")
print(f"스플수 합계: {sum(r.spools for r in rows)}")
