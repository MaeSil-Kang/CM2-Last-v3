# -*- coding: utf-8 -*-
"""테스트 PDF2 (S36BW-826062613530, LOT 2260102234, SF48)로 지폭스케줄 생성."""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, ".")
from jipok.models import Order
from jipok import pack

CHOJI = 4782977
orders = [
    Order(1, 15000, 4, 5020, 120),
    Order(2, 15000, 14, 4990, 170),
    Order(3, 15000, 4, 4980, 170),
    Order(4, 16700, 131, 4960, 120),
    Order(5, 8000, 39, 4940, 120),
    Order(6, 15000, 43, 4940, 120),
    Order(7, 15000, 30, 4930, 120),
    Order(8, 8000, 14, 4900, 120),
    Order(9, 15000, 15, 4880, 120),
    Order(10, 15000, 16, 4880, 120),
]
rows, total = pack.schedule(orders, cp=48, teukgam=False, choji_gongjeong_len=CHOJI)

print("=" * 104)
print(" 지폭스케줄 (PM23호 생산지폭)  —  한솔감열지 SF 48 / LOT 2260102234")
print("=" * 104)
print(f"{'원지지폭':>7} {'초지생산실길이':>12} {'스플수':>5}  {'비고':<58} {'생산길이':>11}")
print("-" * 104)
for r in rows:
    print(f"{r.width:>7} {r.choji_real:>12,} {r.spools:>5}  {r.bigo():<58} {r.saengsan:>11,}")
print("-" * 104)
print(f"{'총생산길이':>7} {total:>66,}   (초지공정길이 {CHOJI:,}, 초과 {total-CHOJI:+,})")
print(f"스플수 합계 {sum(r.spools for r in rows)}  /  권취합 {sum(o.kwonchwi for o in orders)}")
