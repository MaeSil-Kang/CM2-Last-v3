# -*- coding: utf-8 -*-
"""해답에서 주문 입력을 복원해 packer를 18케이스(일반 감열지) 전체 검증.

각 주문: 정길이=비고, 권취=합, 폭=등장행 최소폭, 보상=단독행에서 역산(없으면 120).
비교: 행수, 총생산길이(창 충족), 주문별 권취 보존, 정확일치 행수.
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, ".")
from jipok import calc
from jipok.models import Order
from jipok import pack
from jipok.answers import load_answers

PATH = r"C:\Users\Hansol\Desktop\CM2 Final\학습\Case+1~20+문제.xlsx"
CHOJI = {1:803303,2:886728,3:2436475,4:619787,5:1387687,6:993121,7:516375,
         8:3184661,9:2856158,10:5798719,11:3202818,12:1048865,13:3237932,
         14:882438,15:596850,16:7355251,17:8467581,18:2978788,19:620367,20:847819}


def derive_bosang(case, idx, jeong):
    if jeong < 10000:
        return 120
    for r in case.rows:
        orders_in = {t.order for t in r.tokens if t.kind == "order"}
        if orders_in == {idx} and r.spools and r.total_makki % r.spools == 0:
            N = r.total_makki // r.spools
            for b in (120, 170, 240):
                if calc.choji_saengsan_real(jeong, b, N, case.cp, case.teukgam) == r.choji_real:
                    return b
    return 120


def build_orders(case):
    od = {}
    for r in case.rows:
        for t in r.tokens:
            if t.kind != "order":
                continue
            d = od.setdefault(t.order, {"jeong": t.jeong_len, "kwon": 0, "w": []})
            d["kwon"] += t.count
            d["w"].append(r.width)
    orders = []
    for idx in sorted(od):
        d = od[idx]
        orders.append(Order(idx, d["jeong"], d["kwon"], min(d["w"]),
                            derive_bosang(case, idx, d["jeong"])))
    return orders


def contrib_key(contribs):
    return tuple(sorted((o, c) for o, *_rest, c in contribs))


cases = load_answers(PATH)
print(f"{'Case':>4} {'행수(내/답)':>10} {'총길이(내/답)':>20} {'창':>3} {'권취보존':>6} {'정확행':>7}")
for c in cases:
    if c.teukgam or c.cp >= 70:
        continue
    orders = build_orders(c)
    rows, total = pack.schedule(orders, c.cp, c.teukgam, CHOJI.get(c.no, 0))
    # 권취 보존 체크
    mine_kwon = {}
    for r in rows:
        for o, _j, _b, cc in r.contribs:
            mine_kwon[o] = mine_kwon.get(o, 0) + cc
    # schedule()이 내부에서 폭 내림차순으로 재번호(rank_orders)하므로 비교 기준도 맞춘다.
    ans_kwon = {o.idx: o.kwonchwi for o in pack.rank_orders(orders)}
    conserved = (mine_kwon == ans_kwon)
    # 정확 일치 행수
    mine_set = [(r.width, r.jeong, r.spools, contrib_key(r.contribs)) for r in rows]
    ans_rows = [(r.width, r.jeong_lens[0] if len(r.jeong_lens) == 1 else 0, r.spools,
                 contrib_key([[t.order, t.count] for t in r.tokens if t.kind == "order"]))
                for r in c.rows]
    exact = sum(1 for a in ans_rows if a in mine_set)
    choji = CHOJI.get(c.no, 0)
    in_win = "OK" if total > choji else "X"
    print(f"{c.no:>4} {len(rows):>4}/{len(c.rows):<5} {total:>10,}/{c.total:<9,} {in_win:>3} "
          f"{('OK' if conserved else 'X'):>6} {exact:>3}/{len(c.rows):<3}")
