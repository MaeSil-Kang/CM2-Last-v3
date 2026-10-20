# -*- coding: utf-8 -*-
"""CM2최종학습 대조 도구: LOT + 주문(내가 PDF에서 읽은 값)을 주면
답지 엑셀에서 그 LOT의 정답(생산지폭표)을 뽑아, 내 엔진 결과와 나란히 출력한다.
사용: python learn_compare.py '<LOT>' '<orders_json>'
 orders_json = [{"idx":1,"jeong":6000,"kwon":33,"width":4900,"bosang":120}, ...]
 (width는 답지 원지지폭을 그대로 넣으면 기종상수(PM21 등) 차이를 중립화해 병합/여유만 비교됨)
"""
import sys, io, json, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, ".")
import pandas as pd
from jipok.models import Order
from jipok import pack

XLS = r"C:\Users\Hansol\Desktop\CM2 Final\CM2 최종학습\답지\2026년+지폭변경_260806+.xls"
_LOT = re.compile(r"226\d{7}")


def _cell(v):
    return "" if (v is None or (isinstance(v, float) and pd.isna(v))) else str(v)


def _sheet_col1(df):
    return [_cell(df.iat[i, 1]) if df.shape[1] > 1 else "" for i in range(len(df))]


def _anyrow(df, i, kw):
    return any(isinstance(df.iat[i, k], str) and kw in df.iat[i, k] for k in range(df.shape[1]))


def find_answer(lot):
    """답지 '생산지폭(정답)' 블록에서만 lot 찾기(싱글드럼 입력표 제외).
    → (시트명, [(원지지폭,초지실,스플수,비고,생산길이), ...]). 없으면 (None, [])."""
    xl = pd.ExcelFile(XLS)
    for sh in xl.sheet_names:
        if not sh.startswith("26년"):
            continue
        df = xl.parse(sh, header=None, dtype=object)
        n = len(df)
        # 생산지폭 헤더 ~ 싱글드럼 헤더 구간만 정답 영역
        h1 = next((i for i in range(n) if _anyrow(df, i, "생산지폭")), None)
        if h1 is None:
            continue
        h2 = next((i for i in range(h1 + 1, n) if _anyrow(df, i, "싱글드럼")), n)
        col1 = _sheet_col1(df)
        start = next((i for i in range(h1, h2) if lot in col1[i]), None)
        if start is None:
            continue
        rows = []
        for i in range(start, h2):
            if i > start and _LOT.search(col1[i]):     # 다음 LOT 블록 → 끝
                break
            w = _cell(df.iat[i, 3]).strip()
            bigo = _cell(df.iat[i, 6]).strip() if df.shape[1] > 6 else ""
            if not w or not bigo:
                continue
            # col15=이 행의 생산길이, col16=블록 첫 행에만 있는 총생산길이(그랜드토탈).
            # 왼→오 순으로 첫 매치를 취해야 col15(행 생산길이)를 col16(총계)보다 먼저 잡는다.
            saeng = ""
            for j in range(7, df.shape[1]):
                v = _cell(df.iat[i, j]).replace(",", "").strip()
                if re.fullmatch(r"\d{4,}", v):
                    saeng = v
                    break
            rows.append((w, _cell(df.iat[i, 4]).strip(), _cell(df.iat[i, 5]).strip(), bigo, saeng))
        return sh, rows
    return None, []


def build_answer_lots(out_path):
    """정답(생산지폭 블록)에 실제 데이터가 있는 LOT → 시트 맵을 만들어 저장."""
    xl = pd.ExcelFile(XLS)
    amap = {}
    for sh in xl.sheet_names:
        if not sh.startswith("26년"):
            continue
        df = xl.parse(sh, header=None, dtype=object)
        n = len(df)
        h1 = next((i for i in range(n) if _anyrow(df, i, "생산지폭")), None)
        if h1 is None:
            continue
        h2 = next((i for i in range(h1 + 1, n) if _anyrow(df, i, "싱글드럼")), n)
        col1 = _sheet_col1(df)
        i = h1
        while i < h2:
            m = _LOT.search(col1[i])
            if m:
                lot = m.group()
                # 이 블록에 실제 정답행(원지지폭+비고)이 있는지
                has = False
                for j in range(i, h2):
                    if j > i and _LOT.search(col1[j]):
                        break
                    if _cell(df.iat[j, 3]).strip() and _cell(df.iat[j, 6]).strip():
                        has = True
                if has:
                    amap.setdefault(lot, sh)
            i += 1
    json.dump(amap, open(out_path, "w", encoding="utf-8"), ensure_ascii=False)
    return amap


def main():
    from jipok import calc
    lot = sys.argv[1]
    orders = json.loads(sys.argv[2])
    cp = int(sys.argv[3]) if len(sys.argv) > 3 else 48
    teukgam = (sys.argv[4].lower() in ("1", "true", "y")) if len(sys.argv) > 4 else False
    choji = int(sys.argv[5]) if len(sys.argv) > 5 else 0

    # 폭: 지폭(jipok) 주면 23요청폭(내림+평활화) 계산, 아니면 width 직접.
    raw = [calc.request_width(o["jipok"], cp, teukgam) if o.get("jipok") else o.get("width", 4880)
           for o in orders]
    widths = calc.smooth_request_widths(raw)
    print("계산 23요청폭:", widths)
    O = [Order(o["idx"], o["jeong"], o["kwon"], w, o.get("bosang", 120))
         for o, w in zip(orders, widths)]
    sh, ans = find_answer(lot)
    print(f"LOT {lot}  (cp{cp} 특감{teukgam})  답지시트: {sh}")
    print("=== 정답(답지) ===")
    for w, cr, sp, bigo, sa in ans:
        print(f"  {w}  {cr:>8}  x{sp:<3} {bigo}  → {sa}")
    for mode, name in (("answer_o1", "여유분우선"), ("answer", "완스풀우선")):
        rows, total = pack.schedule(O, cp, teukgam, choji, mode=mode)
        print(f"=== 내엔진 [{name}] 총{total:,} ===")
        for r in rows:
            print(f"  {r.width}  x{r.spools:<3} {r.bigo()}")


if __name__ == "__main__":
    main()
