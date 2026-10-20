# -*- coding: utf-8 -*-
"""업로드 PDF(S36BW-826062614530, LOT 2260600339, SF55)로 지폭스케줄 생성 — 올바른 값."""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, ".")
from jipok.models import Order
from jipok import pack

CHOJI = 1140590
# 순서: 정길이/권취/싱글규격/보상(폭변경 순9·10=170)
orders = [
    Order(1, 12000, 7, 4990, 120),
    Order(2, 15000, 21, 4980, 120),
    Order(3, 12000, 4, 4980, 120),
    Order(4, 15000, 7, 4920, 120),
    Order(5, 12000, 3, 4900, 120),
    Order(6, 12000, 5, 4900, 120),
    Order(7, 15000, 5, 4880, 120),
    Order(8, 12000, 8, 4880, 120),
    Order(9, 12000, 13, 4880, 170),
    Order(10, 15000, 7, 4880, 170),
]
rows, total = pack.schedule(orders, cp=55, teukgam=False, choji_gongjeong_len=CHOJI)
print("지폭스케줄 — 한솔감열지 SF 55 / LOT 2260600339")
print("-" * 96)
for r in rows:
    print(f"폭{r.width} 초지{r.choji_real:>8,} 스플{r.spools:>3} | {r.bigo():<52} 생산{r.saengsan:>10,}")
print("-" * 96)
print(f"총생산길이 {total:,}  (초지공정 {CHOJI:,}, 초과 {total-CHOJI:+,})  스플합 {sum(r.spools for r in rows)} / 권취합 {sum(o.kwonchwi for o in orders)}")
