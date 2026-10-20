# -*- coding: utf-8 -*-
"""답지 엑셀 전체(280 LOT)로 스케줄 모드를 벤치마킹한다.

validate_pack.py가 Case 1~20(구 학습셋)만 보던 것을 2026-08 답지 전체로 확장한 것.
규칙을 고치기 전에 '한 LOT에 과적합하지 않았는지'를 이 수치로 확인한다.

방법
  1) 답지 '생산지폭' 블록에서 LOT별 정답행(폭·초지실·스플수·비고·생산길이)을 읽는다.
  2) 비고 토큰으로 주문을 역복원한다 — 정길이=토큰 길이, 권취=회수 합,
     폭=그 주문이 등장한 행들의 최소폭, 보상=단독행에서 역산(없으면 120).
     (폭에 답지 원지지폭을 그대로 쓰면 기종상수 차이가 중립화돼 병합/여유만 비교된다)
  3) 초지공정 길이는 답지에 없다 → 답지 총생산 × (1 - 중앙초과율 0.62%)로 대체한다.
     총길이 변수를 제거해 '구조(행/스풀 구성)'만 비교하기 위한 것.
  4) 모드별로 행수차·정확일치행·CP중량 위반을 집계한다.

사용: python bench_modes.py [모드1 모드2 ...]
"""
import sys, io, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, ".")
import pandas as pd
from jipok import calc, pack
from jipok.models import Order

XLS = r"C:\Users\Hansol\Desktop\CM2 Final\CM2 최종학습\답지\2026년+지폭변경_260806+.xls"
LOT = re.compile(r"226\d{7}")
ORD = re.compile(r"\(순서(\d+)번\)\s*([\d,]+)\s*m\s*용\s*\*\s*(\d+)\s*회")
YEOYU = re.compile(r"여유분\s*(\d+)\s*회")
MEDIAN_OVER = 0.0062          # 답지 총생산의 초지 대비 중앙 초과율(Case1~20 실측)

MODES = sys.argv[1:] or ["loss", "answer", "answer_o1", "answer_v2", "answer_v3", "answer_v4"]


def _cell(v):
    return "" if (v is None or (isinstance(v, float) and pd.isna(v))) else str(v)


def _anyrow(df, i, kw):
    return any(isinstance(df.iat[i, k], str) and kw in df.iat[i, k]
               for k in range(df.shape[1]))


def _num(s):
    return int(str(s).replace(",", "").strip())


def load_lots():
    """답지 전체 → [(시트, LOT, cp, teukgam, [(폭, 초지실, 스플수, 비고, 생산길이), ...])]"""
    xl = pd.ExcelFile(XLS)
    out = []
    for sh in xl.sheet_names:
        if not sh.startswith("26년"):
            continue
        m = re.search(r"(SFH|SL|HL|SF|PF)\s*(\d{2,3})", sh)
        if not m:
            continue
        teukgam = m.group(1) in ("SFH", "SL", "HL")
        cp = int(m.group(2))
        df = xl.parse(sh, header=None, dtype=object)
        n = len(df)
        h1 = next((i for i in range(n) if _anyrow(df, i, "생산지폭")), None)
        if h1 is None:
            continue
        h2 = next((i for i in range(h1 + 1, n) if _anyrow(df, i, "싱글드럼")), n)
        col1 = [_cell(df.iat[i, 1]) if df.shape[1] > 1 else "" for i in range(n)]
        starts = [i for i in range(h1, h2) if LOT.search(col1[i])]
        for si, st in enumerate(starts):
            end = starts[si + 1] if si + 1 < len(starts) else h2
            rows = []
            for i in range(st, end):
                w = _cell(df.iat[i, 3]).strip().replace(",", "")
                bigo = _cell(df.iat[i, 6]).strip() if df.shape[1] > 6 else ""
                if not re.fullmatch(r"\d{4}", w) or not bigo:
                    continue
                saeng = 0
                for j in range(7, df.shape[1]):
                    v = _cell(df.iat[i, j]).replace(",", "").strip()
                    if re.fullmatch(r"\d{4,}", v):
                        saeng = int(v)
                        break
                cr = _cell(df.iat[i, 4]).strip().replace(",", "")
                sp = _cell(df.iat[i, 5]).strip().replace(",", "")
                rows.append((int(w), _num(cr) if cr.isdigit() else 0,
                             int(sp) if sp.isdigit() else 0, bigo, saeng))
            if len(rows) >= 2:
                out.append((sh, LOT.search(col1[st]).group(), cp, teukgam, rows))
    return out


def build_orders(rows, cp, teukgam):
    """답지 비고에서 주문 역복원. 실패(정보부족)하면 None."""
    od = {}
    for w, _cr, _sp, bigo, _sa in rows:
        for m in ORD.finditer(bigo):
            idx, jeong, cnt = int(m.group(1)), _num(m.group(2)), int(m.group(3))
            d = od.setdefault(idx, {"jeong": jeong, "kwon": 0, "w": []})
            if d["jeong"] != jeong:          # 한 순서에 정길이가 둘 → 복원 불가
                return None
            d["kwon"] += cnt
            d["w"].append(w)
    if not od:
        return None

    def derive_bosang(idx, jeong):
        if jeong < calc.C.SHORT_LEN and not teukgam:
            return calc.C.BOSANG_ROLL
        for w, cr, sp, bigo, _sa in rows:
            ids = {int(m.group(1)) for m in ORD.finditer(bigo)}
            tot = sum(int(m.group(3)) for m in ORD.finditer(bigo))
            tot += sum(int(m.group(1)) for m in YEOYU.finditer(bigo))
            if ids == {idx} and sp and tot % sp == 0 and cr:
                N = tot // sp
                for b in (120, 170, 240):
                    if calc.choji_saengsan_real(jeong, b, N, cp, teukgam) == cr:
                        return b
        return 120

    return [Order(i, od[i]["jeong"], od[i]["kwon"], min(od[i]["w"]),
                  derive_bosang(i, od[i]["jeong"]))
            for i in sorted(od)]


def key_rows(rows):
    """답지 행 → 비교키 (폭, 단일정길이(혼합=0), 스플수, ((순서,회수),...))"""
    out = []
    for w, _cr, sp, bigo, _sa in rows:
        toks = [(int(m.group(1)), int(m.group(3))) for m in ORD.finditer(bigo)]
        js = {_num(m.group(2)) for m in ORD.finditer(bigo)}
        out.append((w, js.pop() if len(js) == 1 else 0, sp, tuple(sorted(toks))))
    return out


def key_mine(rows):
    out = []
    for r in rows:
        agg = {}
        for o, _j, _b, c in r.contribs:
            agg[o] = agg.get(o, 0) + c
        out.append((r.width, r.jeong, r.spools, tuple(sorted(agg.items()))))
    return out


def main():
    lots = load_lots()
    print(f"답지 LOT {len(lots)}건 로드\n")
    agg = {m: {"exact": 0, "rowdiff": 0, "bad": 0, "n": 0, "ansrows": 0, "terr": []}
           for m in MODES}
    skipped = 0
    for sh, lot, cp, teukgam, rows in lots:
        orders = build_orders(rows, cp, teukgam)
        if not orders:
            skipped += 1
            continue
        ans_total = sum(r[4] for r in rows)
        choji = int(ans_total * (1 - MEDIAN_OVER))
        ans_keys = key_rows(rows)
        for m in MODES:
            try:
                mine, tot = pack.schedule(orders, cp, teukgam, choji, mode=m)
            except Exception:                      # noqa: BLE001
                agg[m]["n"] += 1
                continue
            if ans_total:
                agg[m]["terr"].append((tot - ans_total) / ans_total * 100)
            mk = key_mine(mine)
            a = agg[m]
            a["n"] += 1
            a["ansrows"] += len(rows)
            a["exact"] += sum(1 for k in ans_keys if k in mk)
            a["rowdiff"] += abs(len(mine) - len(rows))
            a["bad"] += sum(1 for r in mine
                            if not (calc.C.CP_WEIGHT_MIN
                                    <= calc.cp_weight(cp, r.saengsan / r.spools, r.width)
                                    <= calc.C.CP_WEIGHT_MAX))
    import statistics
    print(f"{'모드':<12} {'LOT':>5} {'정확일치행':>12} {'정확률':>7} {'행수차합':>8} "
          f"{'CP위반':>7} {'총생산오차(중앙)':>16} {'|오차|평균':>10} {'>+5%':>6}")
    for m in MODES:
        a = agg[m]
        pct = a["exact"] / a["ansrows"] * 100 if a["ansrows"] else 0
        te = a["terr"] or [0]
        med = statistics.median(te)
        mae = statistics.mean(abs(x) for x in te)
        over = sum(1 for x in te if x > 5)
        print(f"{m:<12} {a['n']:>5} {a['exact']:>6}/{a['ansrows']:<5} {pct:>6.1f}% "
              f"{a['rowdiff']:>8} {a['bad']:>7} {med:>+15.2f}% {mae:>9.2f}% {over:>6}")
    print(f"\n주문 복원 실패로 건너뜀: {skipped}건")


if __name__ == "__main__":
    main()
