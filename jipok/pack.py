"""스케줄러(패커) v3 — 권취를 스풀로 묶고 인접 병합/균등분할/여유분 처리.

규칙(답지 LOT 2260600339 등 역산 + 2026-08 CM2최종학습 108LOT 전수 대조로 갱신):
  - **주문은 항상 23요청지폭 내림차순으로 재번호매김한 뒤 패킹한다**(rank_orders, 신규).
    답지의 '(순서N번)' 라벨은 PDF 원본 표의 순서 컬럼과 무관하게 이 규칙(폭 내림차순
    순위, 동률은 입력 순서 유지)을 따른다 — 108LOT 전수 검증으로 확정.
  - 각 주문은 원칙적으로 '자기 순서'로 스풀을 만든다(순번 건너뛰기 병합 금지, mode=loss/convenience).
  - answer/answer_o1: 같은 정길이는 다른 정길이를 건너뛰어도 병합(skip-merge)한다.
  - **answer_v2(신규)**: 폭이 같으면 정길이가 달라도 한 스풀에 섞는 '교차-정길이 병합'을
    지원한다(자투리만, forward로 순서(폭) 인접에 따라 누적). 108LOT에서 반복 확인된
    핵심 패턴(예 LOT2260200347·2260201111·2260600337 등).
  - 권취가 완스풀 N을 넘으면 ceil(권취/N) 개 스풀로 '균등' 분할(욕심껏 N 채우기 X).
      예) 13마끼·N9 → 7+6,  21마끼·N7 → 7+7+7.
  - 모든 스풀은 CP중량 11~33.3(2026-08 상향). 부족분은 여유분(min_n)으로 채워 확정.
  - 같은 (폭,정길이,실채움) 스풀들을 한 행으로 집계.
  - 총생산길이는 초지공정길이 이상이 되도록 여유분으로 보충(상한은 느슨).
주의: 여유분 정밀배치는 작업자 판단 영역 → '유효하고 답지 구조에 일치'를 목표.
"""
import math
from . import calc
from . import constants as C
from .models import Order, Spool, Row

WIDTH_TOL = 20          # 병합 허용 폭차(기본). 평균이하 폭은 50.
LONG_ROLL_N = 7         # 완스풀 N이 이 값 이하인 '긴 롤'은 단독 부분스풀을 완스풀까지 채움
TOPUP_HI = 5000         # 총생산길이 상한 = 초지공정길이 + 이 값. 여유분은 이 선을 넘지 않는다.
V5_TOPUP_RATE = 0.0062  # answer_v5 전용 총생산 상한 = 초지 × (1+이 비율), 최소 TOPUP_HI.
                        # 0.62% = 답지 실측 총생산의 초지 대비 중앙 초과율. 고정 5,000은
                        # 큰 LOT에서 지나치게 빡빡해 선행구간이 완스풀을 못 채웠다
                        # (LOT 2261001603: 답지가 +18,977인데 상한이 +5,000).
                        # 202LOT 스윕에서 0~1.2% 구간 정확률·오차 차이는 0.1%p 이내.
V5_PAD_GAP = 1          # answer_v5 전용: 자투리 부분스풀을 완스풀까지 올리는 데 드는 여유분이
                        # 이 마끼수 이하면 채우고, 넘으면 부분스풀로 둔다. 0이면 끄기.
                        # 답지 LOT 2261001603 역산: 순서2 자투리 7마끼(+1)는 채웠고
                        # 순서4 자투리 5마끼(+3)는 채우지 않았다 → 경계는 1~2 사이.


def _v5_hi(choji_len):
    """answer_v5의 총생산 상한 여유분(초지 + 이 값). 고정 5,000 대신 초지 비례.
    답지 실측 초과율 중앙값이 +0.62%라 LOT이 커질수록 고정 5,000은 지나치게 빡빡하다
    (3.7M LOT에서 +0.135%) — 그래서 선행구간 스풀이 완스풀을 못 채우고 잘린다."""
    return max(TOPUP_HI, int(choji_len * V5_TOPUP_RATE))


def reround_widths(orders, cp, teukgam):
    """**answer_v5 전용** — 23요청폭을 10단위 '반올림'으로 다시 계산한 주문 목록을 돌려준다.

    기본 파이프라인은 request_width()의 10단위 **내림**을 쓴다. 이 함수는 그 대신
    request_width_round()로 다시 계산한 뒤 인접 평활화(30)를 똑같이 한 번 건다.
    Order.jipok(원본 지폭)이 0인 주문은 재계산하지 않고 기존 width를 **그대로** 쓴다
    (사용자가 23요청폭을 손으로 고쳤거나 지폭을 모르는 경우). 평활화가 그 값을 끌어올리는
    것도 막는다 — 손으로 넣은 폭은 어떤 경우에도 바뀌면 안 된다. 다만 이웃을 끌어올리는
    '기준'으로는 그대로 참여한다.
    평활화는 **순서(idx) 오름차순**으로 건다. 호출자가 넘기는 리스트가 순서대로라는
    보장이 없기 때문이다(app.build_orders는 DataFrame 행 순서로 만든다).
    rank_orders 이전에 호출해야 한다. 원본 리스트는 건드리지 않고 새 Order를 만들어 반환."""
    raws = {id(o): (calc.request_width_round(o.jipok, cp, teukgam) if o.jipok > 0
                    else o.width) for o in orders}
    seq = sorted(orders, key=lambda o: o.idx)          # 평활화는 순서대로
    sm = calc.smooth_request_widths([raws[id(o)] for o in seq])
    # 수동 폭(jipok==0)은 평활화 결과를 쓰지 않고 원래 값으로 되돌린다
    final = {id(o): (o.width if o.jipok <= 0 else w) for o, w in zip(seq, sm)}
    return [Order(o.idx, o.jeong, o.kwonchwi, final[id(o)], o.bosang, raws[id(o)], o.jipok)
            for o in orders]


def rank_orders(orders):
    """주문을 23요청지폭 내림차순으로 재정렬하고 순서번호(idx)를 1..N으로 다시 부여한다.
    동률(폭이 같음)은 입력 순서를 그대로 유지한다(원표 등장순 — 108LOT 중 일부는 반대로도
    보였으나 다수는 이 순서를 따름, [[jipok-cm2-learning-progress]] 참고).
    2026-08 CM2최종학습(108LOT) 확정 규칙: 답지의 '(순서N번)' 라벨은 PDF 원본 표의 순서
    컬럼과 무관하게 이 규칙(폭 내림차순 순위)으로 매겨진다 — 패킹 전에 항상 이 순서로
    정렬해야 인접 병합·forward carry 로직이 답지 구조와 일치한다."""
    ranked = sorted(orders, key=lambda o: -o.width)   # stable sort: 동률은 입력 순서 유지
    return [Order(i + 1, o.jeong, o.kwonchwi, o.width, o.bosang, o.raw_width)
            for i, o in enumerate(ranked)]


def _N(jeong, width, bosang, cp, teukgam):
    return calc.wanspool_N(jeong, bosang, cp, width, teukgam) or 1


def _count(sp):
    return sum(c[3] for c in sp.contribs)


def _add(sp, order_idx, jeong, bosang, n):
    """contribs=[order_idx, jeong, bosang, count]. 같은 주문(=같은 jeong)이 연속이면 합친다."""
    if sp.contribs and sp.contribs[-1][0] == order_idx and sp.contribs[-1][1] == jeong:
        sp.contribs[-1][3] += n
    else:
        sp.contribs.append([order_idx, jeong, bosang, n])


def _spool_items(s):
    """스풀의 실제 (정길이,보상,수량) 구성 — 여유분은 스풀 대표 jeong/bosang으로 포함."""
    items = [(j, b, c) for _, j, b, c in s.contribs]
    if s.yeoyu:
        items.append((s.jeong, s.bosang, s.yeoyu))
    return items


def _spool_saengsan(s, cp, teukgam):
    return calc.round100(calc.wonji_jeonsan_items(_spool_items(s), cp, teukgam))


def _cp_weight(s, cp, teukgam, extra=0):
    """스풀 1개의 실제 CP중량. extra>0이면 여유분 extra마끼를 더 넣었을 때의 값.
    혼합-정길이 스풀도 실제 구성(items)으로 계산하므로 대표 정길이 오차가 없다."""
    items = _spool_items(s)
    if extra:
        items = items + [(s.jeong, s.bosang, extra)]
    return calc.cp_weight(cp, calc.wonji_jeonsan_items(items, cp, teukgam), s.width)


def _room(s, cp, teukgam, n=1):
    """여유분 n마끼를 더 넣어도 CP중량 상한(33.3)을 넘지 않는가.
    2026-09 버그수정: 교차-정길이 스풀의 s.N은 '대표 정길이' 기준이라 실제 무게를
    반영하지 못한다(16,000m가 섞인 스풀을 8,000m 기준 N까지 채워 상한을 넘겼음).
    여유분을 얹는 모든 경로는 filled<N 대신 이 검사를 함께 통과해야 한다."""
    return _cp_weight(s, cp, teukgam, n) <= C.CP_WEIGHT_MAX


def _trim_over_cap(spools, cp, teukgam):
    """최종 안전망: 여유분 때문에 CP중량 상한을 넘은 스풀은 넘지 않을 때까지 여유분을 깎는다.
    권취(실주문)는 절대 건드리지 않는다 — CP중량 11~33.3이 합권·총생산보다 우선이므로,
    이 때문에 짝수 규칙이 깨지면 UI 경고로 검수자에게 넘긴다."""
    for s in spools:
        while s.yeoyu > 0 and _cp_weight(s, cp, teukgam) > C.CP_WEIGHT_MAX:
            s.yeoyu -= 1


def _fill_to_full(targets, allspools, cp, teukgam, choji_len, hi=TOPUP_HI):
    """대상 스풀들을 **완스풀(N)까지** 여유분으로 채운다 — 단 두 한도 안에서만(2026-09 신규).
      · CP중량 상한 33.3
      · 총생산길이 한도 = 초지공정길이 + hi(기본 5,000)
    완스풀까지 못 채우면 채울 수 있는 만큼(N-1, N-2 …)만 채운다. 한 마끼도 못 넣으면
    그대로 둔다. 초지공정길이가 0이면(미지정) 길이 판단이 불가하므로 아무것도 하지 않는다.
    allspools는 총생산길이를 재기 위한 전체 스풀 목록이다."""
    if not choji_len or not targets:
        return
    limit = choji_len + hi
    total = sum(_spool_saengsan(s, cp, teukgam) for s in allspools)
    for s in targets:
        need = s.N - (_count(s) + s.yeoyu)
        while need > 0:
            if not _room(s, cp, teukgam, need):      # CP중량 상한 초과 → 한 칸 줄여 재시도
                need -= 1
                continue
            before = _spool_saengsan(s, cp, teukgam)
            s.yeoyu += need
            after = _spool_saengsan(s, cp, teukgam)
            if total - before + after > limit:       # 길이 한도 초과 → 되돌리고 재시도
                s.yeoyu -= need
                need -= 1
                continue
            total = total - before + after
            break


def _expand_group(spools, cp, teukgam, choji_len, hi=TOPUP_HI):
    """총생산이 초지공정길이에 미달하는데 **채울 미완 스풀이 하나도 없을 때**의 마지막 수단
    (2026-09 신규). 모든 스풀이 완스풀이거나 CP중량 상한까지 차서 여유분을 더 넣을 곳이
    없는 경우다(예 LOT2261101655: 16,000m 290마끼가 33스풀 × 9마끼로 딱 떨어져 -57,565).

    규칙
      · **권취가 가장 많은 (폭, 대표 정길이) 그룹**을 고른다.
      · 그 그룹의 **스풀 수를 1개씩 늘려가며**, 권취를 '완스풀(N) a개 + 작은 슬롯 b개'로
        다시 나눠 담는다. 슬롯이 늘어난 만큼 여유분이 들어가 총생산이 올라간다.
      · 슬롯 구성은 **총생산이 초지공정길이를 최소한으로 넘기는** 조합을 고른다.
      · 모든 스풀은 CP중량 11~33.3을 지킨다(작은 슬롯도 mn 이상).
      · 모든 스풀이 권취를 최소 1마끼는 갖는다(주문 없는 순수 여유 스풀 금지).
    조건을 만족하는 조합이 없으면 아무것도 바꾸지 않고 False를 돌려준다.

    예) 4970폭 9스풀(권취 78) → 9슬롯 5개 + 8슬롯 5개(10스풀)
        총생산 4,847,700(-57,565) → 4,914,100(+8,835), CP중량 28.6~32.4로 고르게."""
    if not choji_len or not spools:
        return False
    total_now = sum(_spool_saengsan(s, cp, teukgam) for s in spools)
    if total_now >= choji_len:
        return False

    groups = {}
    for s in spools:
        if s.mixed:                 # 혼합-정길이 스풀은 슬롯 기준이 모호해 제외
            continue
        groups.setdefault((s.width, s.jeong), []).append(s)
    if not groups:
        return False
    key = max(groups, key=lambda k: sum(_count(s) for s in groups[k]))
    grp = groups[key]
    width, jeong = key
    bosang = max(s.bosang for s in grp)
    N = _N(jeong, width, bosang, cp, teukgam)
    mn = calc.min_n_cp11(jeong, bosang, cp, width, teukgam)
    K = sum(_count(s) for s in grp)                  # 그룹의 실제 권취 합
    other = total_now - sum(_spool_saengsan(s, cp, teukgam) for s in grp)

    def Lm(m):
        return calc.round100(calc.wonji_jeonsan(jeong, bosang, m, cp, teukgam))

    cands = []
    for cnt in range(len(grp) + 1, len(grp) + 6):    # 스풀 수를 1개씩 늘려가며
        if K < cnt:                                  # 모든 스풀에 권취 1마끼씩은 줘야 한다
            break
        for small in range(mn, N + 1):
            for a in range(cnt + 1):                 # a가 클수록 슬롯·총생산 증가 → 첫 유효값이 최소
                b = cnt - a
                if a * N + b * small < K:
                    continue
                t = other + a * Lm(N) + b * Lm(small)
                if t < choji_len:
                    continue
                cands.append((t, a, b, small))
                break
        if cands:
            break                                    # 스풀 수는 적을수록 좋다
    if not cands:
        return False
    # 길이 초과가 '슬롯 하나 길이' 안에서 비슷하면, **중량이 고른 쪽**(작은 슬롯이 N에
    # 가까운 쪽)을 고른다. 예) 9슬롯9개+4슬롯1개(4,914,000·CP14.5 이상치) 대신
    # 9슬롯5개+8슬롯5개(4,914,100·CP28.6~32.1)를 택한다.
    tmin = min(c[0] for c in cands)
    slot_len = Lm(N) - Lm(N - 1) if N > 1 else 0
    near = [c for c in cands if c[0] <= tmin + slot_len]
    best = max(near, key=lambda c: (c[3], -c[0]))

    _t, a, b, small = best
    flat = []
    for s in grp:
        for oi, j, bb, c in s.contribs:
            flat += [(oi, j, bb)] * c
    for s in grp:
        spools.remove(s)
    sizes = [N] * a + [small] * b
    rem, pos = K, 0
    for i, sz in enumerate(sizes):
        left = len(sizes) - i - 1                    # 뒤 스풀들에 최소 1마끼씩 남긴다
        take = max(0, min(sz, rem - left))
        ids = flat[pos:pos + take]; pos += take; rem -= take
        if not ids:
            continue
        sp = Spool(jeong, width, bosang, N, [], 0)
        for oi, j, bb in ids:
            _add(sp, oi, j, bb, 1)
        sp.yeoyu = sz - _count(sp)
        spools.append(sp)
    return True


def _balanced(total, k):
    """total을 k조각으로 균등 분할. 앞쪽이 큼. 예) (13,2)->[7,6], (21,3)->[7,7,7]."""
    base, rem = divmod(total, k)
    return [base + 1 if i < rem else base for i in range(k)]


def make_spools(orders, cp, teukgam):
    """주문 리스트 -> 스풀 리스트.

    핵심(답지 학습):
      - 순번 건너뛰기 병합 금지: carry는 '바로 다음 순서'에서만 합쳐진다.
      - 작은 주문(권취<CP중량11 최소)만 인접 병합 대상. 그 외엔 자기 스풀.
      - 큰 주문은 ceil(권취/N) 스풀로 균등 분할.
    """
    spools = []
    widths = [o.width for o in orders] or [0]
    avg = (max(widths) + min(widths)) / 2

    def tol(w1, w2):
        return 50 if min(w1, w2) < avg else WIDTH_TOL

    def Nof(j, w, b):
        return _N(j, w, b, cp, teukgam)

    def minof(j, w, b):
        return calc.min_n_cp11(j, b, cp, w, teukgam)

    def finalize(s):
        """자체 스풀 확정: CP중량>=11 되도록 여유분 채움."""
        need = minof(s.jeong, s.width, s.bosang)
        if _count(s) + s.yeoyu < need:
            s.yeoyu = need - _count(s)

    carry = None   # 바로 다음 순서와만 합칠 부분스풀(<min). 절대 건너뛰지 않음.
    for i, o in enumerate(orders):
        # 1) 들어온 carry(직전 순서 잔량)와 결합 시도 — 호환되면 합치고, 아니면 단독 확정
        group = []
        gj, gw, gb = o.jeong, o.width, o.bosang
        if carry is not None:
            if carry.jeong == o.jeong and abs(carry.width - o.width) <= tol(carry.width, o.width):
                group = [list(c) for c in carry.contribs]
                gw = max(gw, carry.width)
                gb = max(gb, carry.bosang)
            else:
                finalize(carry); spools.append(carry)   # 인접 불호환 → 단독 확정
            carry = None

        _add_group(group, o.idx, gj, gb, o.kwonchwi)
        M = sum(c[3] for c in group)
        if M == 0:
            continue
        N = Nof(gj, gw, gb)
        mn = minof(gj, gw, gb)

        # 2) 너무 작아 홀로 설 수 없으면(권취<CP중량11) → 바로 다음 순서로만 이월
        if M < mn:
            nxt = orders[i + 1] if i + 1 < len(orders) else None
            if nxt is not None and nxt.jeong == gj and abs(gw - nxt.width) <= tol(gw, nxt.width):
                carry = Spool(gj, gw, gb, N, group, 0)
                continue
            sp = Spool(gj, gw, gb, N, group, 0)
            finalize(sp); spools.append(sp)
            continue

        # 3) 완스풀 N 기준 ceil 분할(균등). 가장 작은 조각도 CP중량11 보장.
        k = max(1, math.ceil(M / N))
        while k > 1 and (M // k) < mn:
            k -= 1
        sizes = _balanced(M, k)
        gi, gcnt = 0, group[0][3]
        for s in sizes:
            sp = Spool(gj, gw, gb, N, [], 0)
            need = s
            while need > 0:
                if gcnt == 0:
                    gi += 1
                    gcnt = group[gi][3]
                take = min(need, gcnt)
                _add(sp, group[gi][0], group[gi][1], group[gi][2], take)
                gcnt -= take
                need -= take
            spools.append(sp)

    if carry is not None:
        finalize(carry); spools.append(carry)
    return spools


def _add_group(group, order_idx, jeong, bosang, n):
    if group and group[-1][0] == order_idx and group[-1][1] == jeong:
        group[-1][3] += n
    else:
        group.append([order_idx, jeong, bosang, n])


def spools_to_rows(spools, cp, teukgam):
    """연속한 동일 (폭,정길이,실채움) 스풀을 한 행으로 집계.
    길이는 완스풀 N이 아니라 '실제 채운 마끼수(실채움=마끼+여유)'로 계산.
    생산길이 계산은 각 행의 '대표 스풀(그룹을 처음 만든 스풀) 1개'의 실제 (정길이,보상)
    구성으로 하고, 이후 병합되는 스풀 수만큼 스플수(spools)를 늘린다(혼합-정길이 스풀도
    정확히 계산됨 — bigo() 표시용 contribs 누적과 별개)."""
    rows = []
    rep_items = {}   # id(row) -> 그 그룹을 만든 첫 스풀 1개의 (jeong,bosang,count) 구성
    for s in spools:
        filled = _count(s) + s.yeoyu
        key = (s.width, s.jeong, filled)
        if rows and (rows[-1].width, rows[-1].jeong, rows[-1].N) == key:
            r = rows[-1]
            r.spools += 1
            for oi, j, b, c in s.contribs:
                if r.contribs and r.contribs[-1][0] == oi and r.contribs[-1][1] == j:
                    r.contribs[-1][3] += c
                else:
                    r.contribs.append([oi, j, b, c])
            r.yeoyu += s.yeoyu
            r.bosang = max(r.bosang, s.bosang)
        else:
            r = Row(s.width, s.jeong, filled, 1, [list(c) for c in s.contribs], s.yeoyu, s.bosang)
            rows.append(r)
            rep_items[id(r)] = _spool_items(s)
    for r in rows:
        items = rep_items[id(r)]
        r.choji_real = calc.choji_saengsan_real_items(items, cp, teukgam)
        r.saengsan = calc.saengsan_len_items(items, r.spools, cp, teukgam)
    return rows


def topup_yeoyu(spools, cp, teukgam, choji_len, lo=1000, hi=TOPUP_HI, concentrate=False):
    """총생산길이를 초지공정길이 이상으로 끌어올리는 여유분 보충(상한은 느슨).
    답지 학습: 순서1은 '필수 여유분 1회'만 받고, 추가 보충 대상은 아래 우선순위로 고른다.
      1) 재단량 비율(=권취 생산량 비중)이 높은 순서의 부분스풀 우선
      2) 같으면 지폭(생산폭)이 넓은 곳, 그다음 긴 정길이
    (순서1은 필수 1회 외 추가 보충에서 제외. 이미 +lo 충족이면 추가 안 함.)
    concentrate=True(순서1 여유분 모드): 추가 여유분을 '대형 권취 순서 한 곳'에 몰아주고
    소형 순서 부분스풀은 건드리지 않는다(예 순5 5/5 유지, 순3에 여유분 집중)."""
    if not choji_len:
        return
    total = lambda: sum(_spool_saengsan(s, cp, teukgam) for s in spools)
    target_lo, target_hi = choji_len + lo, choji_len + hi
    o1 = spools[0].contribs[0][0] if spools and spools[0].contribs else None

    # 재단량(권취=생산량) 비중: 순서별 실제 권취 총합(여유 제외). 스풀 점수는 그 스풀에
    # 기여한 순서들 중 최대 권취량(=생산량 큰 순서에 속한 스풀일수록 우선).
    prod = {}
    for s in spools:
        for oi, _j, _b, c in s.contribs:
            prod[oi] = prod.get(oi, 0) + c

    def prod_weight(s):
        return max((prod.get(oi, 0) for oi, _j, _b, _c in s.contribs), default=0)

    def under(s):
        return (_count(s) + s.yeoyu) < s.N

    def room(s, n=1):
        return _room(s, cp, teukgam, n)

    def fillable(s):
        """여유분을 더 받을 수 있는 스풀: 완스풀 미만 AND CP중량 상한 여유 있음."""
        return under(s) and room(s)

    def is_o1(s):
        return s.contribs and s.contribs[0][0] == o1

    def delta(s):
        items = _spool_items(s)
        plus = [(j, b, c) for j, b, c in items]
        plus.append((s.jeong, s.bosang, 1))
        return calc.round100(calc.wonji_jeonsan_items(plus, cp, teukgam)) - \
               calc.round100(calc.wonji_jeonsan_items(items, cp, teukgam))

    # 순서1 필수 여유분(규칙4-4): 순서1이 '첫 기여자'인 첫 미완 스풀을 채운다.
    o1_part = next((s for s in spools if is_o1(s) and fillable(s)), None)
    if o1_part is not None:
        if concentrate:
            # 2026-09: '여유분 우선'(answer_o1)만 **완스풀(N)까지** 채운다.
            # 단 총생산 한도(초지+hi)를 넘지 않는 선까지만 — 못 채우면 채울 수 있는 만큼.
            _fill_to_full([o1_part], spools, cp, teukgam, choji_len, hi)
        else:
            o1_part.yeoyu += 1          # 다른 모드는 종전대로 1회만

    if concentrate:
        # 추가 여유분을 '대형 권취 순서 한 곳'에 몰아준다. 단 대상은 '부분스풀(미완)이 있는'
        # 순서 중 권취 최대(순서1 제외). 완벽 완스풀로 자투리가 없는 대형 순서(예 순4=180)나
        # 소형 마지막 순서(지폭·권취 작음)에는 여유분을 몰지 않는다(순수 여유 행 방지).
        part_orders = {oi for s in spools if fillable(s) for oi, _j, _b, _c in s.contribs}
        cand = {oi: p for oi, p in prod.items() if oi != o1 and oi in part_orders}
        if not cand:                    # 부분스풀 없음(전부 완스풀) → 부득이 대형 순서 사용
            cand = {oi: p for oi, p in prod.items() if oi != o1}
        co = max(cand, key=cand.get) if cand else o1
        tmpl = next((s for s in spools if any(c[0] == co for c in s.contribs)), None)
        guard = 0
        while total() < target_lo and guard < 4000:
            # co 순서가 든 미완 스풀 우선 → 없으면 co 폭의 순수 여유 스풀(재사용/새로)
            sink = next((s for s in spools if fillable(s) and s.contribs
                         and any(c[0] == co for c in s.contribs)), None)
            if sink is None and tmpl is not None:
                sink = next((s for s in spools if fillable(s) and not s.contribs
                             and s.width == tmpl.width and s.jeong == tmpl.jeong), None)
            # 2026-09: 종전에는 여기서 '주문 없는 빈 스풀'을 새로 만들어 붙였으나,
            # 답지 전수조사(1,169행)로 그런 행은 존재하지 않음이 확정됐다(일반 분기와 동일).
            # 채울 곳이 없으면 목표 미달이어도 멈춘다.
            if sink is None:
                break
            sink.yeoyu += 1
            guard += 1
        # 대형 순서의 '꼬리 부분스풀 + 순수 여유 스풀'을 같은 마끼수 부분스풀로 재분배한다.
        # (완스풀까지 억지로 안 채우고 답지처럼 '순3×1+여유13'을 2스풀(7,7)로 보이게.)
        if tmpl is not None:
            Nb = tmpl.N
            pool = [s for s in spools if s.jeong == tmpl.jeong and s.width == tmpl.width
                    and not (_count(s) == Nb and s.yeoyu == 0)]   # 완스풀 bulk 제외
            if pool:
                flat = []
                for s in pool:
                    for oi, j, b, c in s.contribs:
                        flat += [(oi, j, b)] * c
                tot = sum(_count(s) + s.yeoyu for s in pool)
                for s in pool:
                    spools.remove(s)
                mn = calc.min_n_cp11(tmpl.jeong, tmpl.bosang, cp, tmpl.width, teukgam)
                k = max(1, math.ceil(tot / Nb))
                while k > 1 and math.ceil(tot / k) < mn:
                    k -= 1
                size = math.ceil(tot / k)
                pos = 0
                for _ in range(k):
                    ids = flat[pos:pos + size]; pos += size
                    if not ids:                      # 주문 없는 순수 여유 스풀 금지
                        continue
                    sp = Spool(tmpl.jeong, tmpl.width, tmpl.bosang, Nb, [], 0)
                    for oi, j, b in ids:
                        _add(sp, oi, j, b, 1)
                    if _count(sp) < size:
                        sp.yeoyu = size - _count(sp)
                    if _count(sp) + sp.yeoyu < mn:      # CP중량 최소 보장
                        sp.yeoyu = mn - _count(sp)
                    spools.append(sp)
        # 2026-09: 종전에는 여기서 무조건 return 했다. 이제 빈 스풀을 만들지 않으므로
        # co 한 곳만으로는 목표에 못 미칠 수 있다 → 그때는 일반 우선순위 보충으로 이어받는다.
        if total() >= target_lo:
            return

    guard = 0
    expanded = False        # _expand_group은 LOT당 1회만(스풀이 무한히 늘지 않게)
    while total() < target_lo and guard < 2000:
        cur = total()
        # 추가 보충은 순서1 제외, 넓은 폭 부분스풀 우선
        cand = [s for s in spools if fillable(s) and not is_o1(s)]
        if not cand:
            cand = [s for s in spools if fillable(s)]   # 순서1만 남으면 부득이 사용
        if not cand:
            # 모두 꽉 참(N 이상이거나 CP중량 상한까지 참) → 더 채울 '자연스러운' 미완
            # 스풀이 없다. 2026-08 답지 전수조사(전체 1,169행)로 확인: 어떤 행도
            # "(순서N번)..." 없이 여유분만 단독으로 나오지 않는다(주문 없는 빈 스풀을
            # 새로 만들면 안 됨). 그렇다고 기존 완스풀에 여유분을 그냥 얹으면 그 스풀
            # 하나의 CP중량이 N을 넘어 상한(33.3)을 위반한다. **절대 우선순위(CP중량
            # 11~33.3이 총생산≥초지보다 위)**를 지키기 위해 여유분을 더 얹지는 않는다.
            #
            # 2026-09 추가: 그래도 '진짜 하한(초지공정길이)'에 미달이면, 마지막 수단으로
            # 권취 최대 그룹의 스풀 수를 늘려 슬롯을 만든다(_expand_group). 여유분을
            # 억지로 얹는 게 아니라 '담는 그릇'을 늘리는 것이라 CP중량 상한을 지킨다.
            # 그래도 안 되면 target_lo는 소프트 목표라 여기서 멈추고, 진짜 하한 미만이면
            # UI가 "총생산 부족" 경고로 보여줘 검수자가 직접 확인하게 한다.
            if not expanded and _expand_group(spools, cp, teukgam, choji_len, hi):
                expanded = True
                continue
            break
        safe = [s for s in cand if cur + delta(s) <= target_hi]
        if not safe:
            # 2026-09 변경: target_hi(=초지+5,000)는 이제 '넘으면 안 되는 한도'다.
            # 한도 안에 들어가는 스풀이 없으면 여유분을 더 넣지 않는다 —
            # 단 아직 진짜 하한(초지공정길이)에도 못 미쳤다면 하한이 우선이므로 계속 채운다.
            if cur >= choji_len:
                break
            safe = cand
        pool = safe
        # 재단량 비율(권취 생산량) 큰 순서 우선 → 같으면 넓은 폭 → 그다음 긴 정길이
        pool.sort(key=lambda s: (-prod_weight(s), -s.width, -s.jeong))
        pool[0].yeoyu += 1
        guard += 1


def pad_long_rolls(spools, cp, teukgam):
    """긴 롤(완스풀 N<=LONG_ROLL_N, 예 15,000m·N7) 부분스풀을 완스풀까지 여유분으로 채움.
    답지 학습: 작업자는 15,000m 같은 긴 롤을 부분으로 돌리지 않고 완스풀(N)까지 완성한다.
    단, '주문 전량이 한 스풀에 든 단독 스풀'만 대상(균등 분할된 스풀은 그대로 둠).
    혼합-정길이 스풀(answer_v2)은 N이 단일값이 아니라 대상에서 제외한다.
    topup(길이 보충) 이후에 호출 → 순서별 여유분과 별개로 긴 롤만 추가 패딩."""
    glob = {}
    for s in spools:
        for o, _j, _b, c in s.contribs:
            glob[o] = glob.get(o, 0) + c
    for s in spools:
        if s.mixed or s.N > LONG_ROLL_N or (_count(s) + s.yeoyu) >= s.N:
            continue
        orders_in = {o for o, _j, _b, _c in s.contribs}
        # 단일스풀(분할 아님)이고, 채울 여유분이 현재 권취 이하일 때만 완스풀까지 채운다.
        # (소형 주문을 2배 이상 뻥튀기하지 않음: 예 18,500m·권취3 → 3마끼 그대로 둠.)
        if (orders_in and sum(glob[o] for o in orders_in) <= s.N
                and 2 * _count(s) >= s.N):
            need = s.N - _count(s)
            # CP중량 상한을 넘지 않는 범위까지만 채운다(2026-09).
            while need > 0 and not _room(s, cp, teukgam, need):
                need -= 1
            if need > 0:
                s.yeoyu = need


def enforce_even_short(spools, hapgwon=True, cp=48, teukgam=False):
    """<10,000m 합권은 스풀당 총 마끼(권취+여유)가 짝수여야 한다 → 홀수면 여유분 +1.
    특감(SFH/SL)만 예외 → hapgwon=False(특감)면 짝수화 안 함, 그 외 지종은 적용.
    혼합-정길이 스풀은 어느 정길이 기준인지 모호해 대상에서 제외한다."""
    if not hapgwon:
        return
    for s in spools:
        if s.mixed:
            continue
        if s.jeong < C.SHORT_LEN and (_count(s) + s.yeoyu) % 2 == 1:
            # CP중량 상한이 합권 짝수보다 우선 — 넘기면 홀수로 두고 UI 경고에 맡긴다(2026-09).
            if _room(s, cp, teukgam):
                s.yeoyu += 1


def merge_for_convenience(spools, cp, teukgam):
    """작업편리성 모드: 인접 스풀을 합쳐 스풀 수를 줄인다(완스풀 위주).
    조건: 같은 정길이 & 폭차<=tol & 합산 마끼 <= 완스풀N. **인접 스풀끼리만**(순번
    건너뛰기 없음). 손율최소 결과에 한 번 더 패스 → 멀쩡한 인접 주문도 한 스풀에 합쳐
    스풀 수↓(대신 좁은 주문이 넓은 폭으로 가 폭손실 소폭↑). topup 이전에 호출."""
    if len(spools) <= 1:
        return spools
    widths = [s.width for s in spools]
    avg = (max(widths) + min(widths)) / 2

    def tol(w1, w2):
        return 50 if min(w1, w2) < avg else WIDTH_TOL

    out = [spools[0]]
    for s in spools[1:]:
        p = out[-1]
        w = max(p.width, s.width); b = max(p.bosang, s.bosang)
        n = _N(p.jeong, w, b, cp, teukgam)
        if (p.jeong == s.jeong and abs(p.width - s.width) <= tol(p.width, s.width)
                and _count(p) + _count(s) <= n):
            p.width, p.bosang, p.N = w, b, n
            for oi, j, bb, c in s.contribs:
                _add(p, oi, j, bb, c)
            p.yeoyu += s.yeoyu
        else:
            out.append(s)
    return out


def make_spools_streamed(orders, cp, teukgam):
    """답지 방식: 같은 정길이(폭 호환) 주문을 하나의 스트림으로 이어붙여 완스풀 N마끼로
    연속 충전. 중간에 낀 다른 정길이 주문은 건너뛴다(건너뛰기 병합). 스트림 끝 자투리가
    CP중량 최소 미만이면 직전 스풀과 합쳐 균등 분할(작은 orphan 방지)."""
    if not orders:
        return []
    widths = [o.width for o in orders]
    avg = (max(widths) + min(widths)) / 2

    def tol(w1, w2):
        return 50 if min(w1, w2) < avg else WIDTH_TOL

    # 1) 스트림 구성 — 정길이 같고 폭 호환인 주문을 순서 유지해 모음(다른 정길이는 건너뜀)
    used = [False] * len(orders)
    streams = []
    for i in range(len(orders)):
        if used[i]:
            continue
        o = orders[i]; used[i] = True
        contribs = [[o.idx, o.kwonchwi]]; w = o.width; b = o.bosang; j = o.jeong
        for k in range(i + 1, len(orders)):
            if used[k]:
                continue
            o2 = orders[k]
            if o2.jeong == j and abs(w - o2.width) <= tol(w, o2.width):
                used[k] = True
                contribs.append([o2.idx, o2.kwonchwi])
                w = max(w, o2.width); b = max(b, o2.bosang)
        streams.append((j, w, b, contribs))

    # 2) 스트림별 완스풀 연속 충전 + 자투리 균등화. 스풀 폭/보상은 '그 스풀 기여 주문'의 최대.
    wmap = {o.idx: o.width for o in orders}
    bmap = {o.idx: o.bosang for o in orders}
    spools = []
    for j, w, b, contribs in streams:
        N = _N(j, w, b, cp, teukgam)                # 스트림 최대폭 기준 완스풀(가장 보수적)
        mn = calc.min_n_cp11(j, b, cp, w, teukgam)
        flat = []
        for oid, cnt in contribs:
            flat.extend([oid] * cnt)
        M = len(flat)
        # divmod 욕심충전: 앞에서부터 완스풀 N을 꽉 채우고 마지막에 자투리를 남긴다.
        # (2026-09에 _balanced 균등분할로 바꿨다가 되돌림 — 답지에 '18+18+7'처럼 꽉 채운
        #  행이 많아 균등분할이 정확일치행을 떨어뜨렸다. 202 LOT 실측 17.8% → 16.4%.)
        full, rem = divmod(M, N)
        sizes = [N] * full + ([rem] if rem else [])
        if not sizes:
            sizes = [M]
        if len(sizes) >= 2 and (sizes[-1] < mn or sizes[-1] <= N // 2):
            tot = sizes.pop() + sizes.pop()         # 작은 자투리 → 직전과 합쳐 균등분할
            sizes += [(tot + 1) // 2, tot // 2]
        pos = 0
        for s in sizes:
            ids = flat[pos:pos + s]; pos += s
            if not ids:                             # 주문 없는 순수 여유 스풀 금지
                continue
            sw = max(wmap[o] for o in ids); sb = max(bmap[o] for o in ids)
            sp = Spool(j, sw, sb, _N(j, sw, sb, cp, teukgam), [], 0)
            for oid in ids:
                _add(sp, oid, j, bmap[oid], 1)
            if _count(sp) < calc.min_n_cp11(j, sb, cp, sw, teukgam):
                sp.yeoyu = calc.min_n_cp11(j, sb, cp, sw, teukgam) - _count(sp)
            spools.append(sp)
    return spools


def make_spools_perorder(orders, cp, teukgam, strict_width=False):
    """새 방식(①②④, 답지 LOT2260702206 역산):
      ② 같은 정길이 순서는 각자 완스풀(floor(권취/N))을 '단일 순서'로 만든다
         (한 완스풀 안에 여러 순서 안 섞음 — 답지처럼 순6·순7 각자 완스풀).
      ① 자투리(권취%N)는 '대형 권취 순서'를 기준으로 두 구역으로 처리한다.
         - 대형 순서까지(앞): 자투리를 이어붙여 완스풀(N)을 완성하고, 대형 순서의
           마지막 잔여는 '꼬리 부분스풀'로 격리 → topup 여유분이 그쪽으로 몰린다.
         - 대형 순서 뒤: 인접 자투리를 한 폭(최댓값)으로 '균등 등분'해 한 행으로 묶는다
           (부족분은 여유분으로 채워 같은 마끼수 → 답지처럼 순6×6+순7×5 한 행).
      ④ 단독이며 소형(같은 정길이 1개 & 권취<=2N)인 순서는 완스풀 대신 균등 등분한다
         (지폭·재단량이 작아 여유분을 다른 곳에 쓰기 어려운 순서 → 예 순5 10→5+5).
    같은 정길이는 중간에 다른 정길이가 껴 있어도 한 그룹(자투리 건너뛰기 병합 허용).

    strict_width=True(answer_v5 전용): 자투리를 이어붙일 때 **23요청폭이 정확히 같은
    주문끼리만** 묶는다. 폭이 다르면 병합하지 않고 각자 여유분으로 채운다.
    (기본값 False는 종전대로 폭차 tol 안에서 섞는다 — 다른 모드의 동작은 그대로다.)
    근거: 답지 LOT 2261001603에서 순서2 자투리 7마끼(4950)와 순서4 자투리 5마끼(4940)를
    섞지 않고, 순서2는 여유분1로 완스풀을 만들고 순서4는 5마끼 부분스풀로 남겼다."""
    if not orders:
        return []
    wmap = {o.idx: o.width for o in orders}
    bmap = {o.idx: o.bosang for o in orders}

    def by_width(contribs):
        """자투리를 폭별로 끊어 묶는다(폭 첫 등장 순서 유지, 그 안에서 원래 순서 유지)."""
        runs, seen = [], {}
        for oid, cnt in contribs:
            w = wmap[oid]
            if w not in seen:
                seen[w] = len(runs)
                runs.append([])
            runs[seen[w]].append([oid, cnt])
        return runs

    def spread(fn, jeong, contribs):
        """strict_width면 폭별로 나눠 fn을 따로 돌린다. 아니면 통째로 넘긴다."""
        if not strict_width:
            return fn(jeong, contribs)
        out = []
        for run in by_width(contribs):
            out += fn(jeong, run)
        return out

    def _flat(contribs):
        f = []
        for oid, cnt in contribs:
            f += [oid] * cnt
        return f

    def fill_complete(jeong, contribs):
        """자투리를 이어붙여 완스풀(N)을 채우고 마지막 잔여는 부분스풀(꼬리)로 남긴다.
        스풀 폭/보상=그 스풀 기여 주문의 최대. 마지막 부분스풀은 CP중량 최소까지만 보정."""
        flat = _flat(contribs)
        M = len(flat)
        if M == 0:
            return []
        rw = max(wmap[o] for o in flat); rb = max(bmap[o] for o in flat)
        No = _N(jeong, rw, rb, cp, teukgam)
        out, pos = [], 0
        while pos < M:
            ids = flat[pos:pos + No]; pos += No
            sw = max(wmap[o] for o in ids); sb = max(bmap[o] for o in ids)
            sp = Spool(jeong, sw, sb, _N(jeong, sw, sb, cp, teukgam), [], 0)
            for oid in ids:
                _add(sp, oid, jeong, bmap[oid], 1)
            need = calc.min_n_cp11(jeong, sb, cp, sw, teukgam)
            if _count(sp) < need:
                sp.yeoyu = need - _count(sp)
            # strict_width(answer_v5): 완스풀까지 몇 마끼 안 남은 자투리는 여유분으로 채운다.
            # 병합을 막은 대신 '여유분으로 완스풀을 만든다'는 답지 관행을 살리는 부분.
            gap = sp.N - _count(sp) - sp.yeoyu
            if (strict_width and 0 < gap <= V5_PAD_GAP
                    and _room(sp, cp, teukgam, sp.yeoyu + gap)):
                sp.yeoyu += gap
            out.append(sp)
        return out

    def merge_even(jeong, contribs):
        """인접 자투리를 한 폭(run 최댓값)으로 균등 등분. 부족분은 여유분으로 채워
        모든 스풀 같은 마끼수 → spools_to_rows에서 한 행으로 묶임(순6×6+순7×5)."""
        flat = _flat(contribs)
        M = len(flat)
        if M == 0:
            return []
        rw = max(wmap[o] for o in flat); rb = max(bmap[o] for o in flat)
        No = _N(jeong, rw, rb, cp, teukgam)
        mn = calc.min_n_cp11(jeong, rb, cp, rw, teukgam)
        k = max(1, math.ceil(M / No))
        while k > 1 and math.ceil(M / k) < mn:
            k -= 1
        size = math.ceil(M / k)
        out, pos = [], 0
        for _ in range(k):
            ids = flat[pos:pos + size]; pos += size
            sp = Spool(jeong, rw, rb, No, [], 0)      # 같은 폭(run 최댓값)
            for oid in ids:
                _add(sp, oid, jeong, bmap[oid], 1)
            if _count(sp) < size:                     # 균등화(부족분 여유분)
                sp.yeoyu = size - _count(sp)
            if _count(sp) + sp.yeoyu < mn:             # CP중량 최소 보장
                sp.yeoyu = mn - _count(sp)
            out.append(sp)
        return out

    # 정길이별 그룹(첫 등장 순서 유지)
    groups = []
    for o in orders:
        g = next((x for x in groups if x[0] == o.jeong), None)
        if g is None:
            groups.append([o.jeong, [o]])
        else:
            g[1].append(o)

    spools = []
    for jeong, gords in groups:
        wmax = max(o.width for o in gords); bmax = max(o.bosang for o in gords)
        N = _N(jeong, wmax, bmax, cp, teukgam)
        # ④ 단독 소형(권취<=2N) → 완스풀 대신 균등 등분
        if len(gords) == 1 and gords[0].kwonchwi <= 2 * N:
            spools += merge_even(jeong, [[gords[0].idx, gords[0].kwonchwi]])
            continue
        # ② 각 순서 완스풀(단일순서) + 자투리 수집
        rem = []
        for o in gords:
            No = _N(jeong, o.width, o.bosang, cp, teukgam)
            full = o.kwonchwi // No
            for _ in range(full):
                sp = Spool(jeong, o.width, o.bosang, No, [], 0)
                _add(sp, o.idx, jeong, o.bosang, No)
                spools.append(sp)
            r = o.kwonchwi - full * No
            if r > 0:
                rem.append([o.idx, r])
        if not rem:
            continue
        # ① 대형 권취 순서(자기 완스풀 bulk가 있는 순서)를 '구분선'으로 앞/뒤 자투리를
        #    따로 처리한다(대형 순서를 건너뛰어 병합하지 않음).
        #    - 대형 순서에 자투리가 있으면: 앞 구역은 완스풀 완성 + 대형 순서 꼬리 격리.
        #    - 대형 순서가 완벽 완스풀(자투리 없음)이면: 앞/뒤 구역을 각각 균등병합.
        #    - 대형 순서 자체가 없으면(그룹 전부 권취<N): 자투리 통째 완스풀 완성(순4+순8).
        sink = max(gords, key=lambda o: o.kwonchwi)
        if sink.kwonchwi >= N:
            sp_pos = gords.index(sink)
            if sink.kwonchwi % N != 0:                 # 대형 순서 꼬리 있음
                before = {o.idx for o in gords[:sp_pos + 1]}
                spools += spread(fill_complete,
                                 jeong, [x for x in rem if x[0] in before])
                after = [x for x in rem if x[0] not in before]
                if after:
                    spools += spread(merge_even, jeong, after)
            else:                                      # 완벽 완스풀 → 앞/뒤 각각 균등병합
                before = {o.idx for o in gords[:sp_pos]}
                A = [x for x in rem if x[0] in before]
                B = [x for x in rem if x[0] not in before]
                if A:
                    spools += spread(merge_even, jeong, A)
                if B:
                    spools += spread(merge_even, jeong, B)
        else:
            spools += spread(fill_complete, jeong, rem)
    return spools


def make_spools_crossjeong(orders, cp, teukgam):
    """교차-정길이 병합 지원 패커(신규, 2026-08 CM2최종학습 108LOT 반영).

    ① 각 주문은 먼저 자기 정길이 기준 완스풀(floor(권취/N))을 자기 스풀로 채운다(순서 안 섞임).
    ② 남은 자투리(권취%N)는 **순서(=23요청폭 내림차순 순위) 순서대로** 훑으며, 정길이가
       달라도 폭이 인접(tol 이내)하면 누적 CP중량이 상한(33.3)을 넘기 전까지 forward로
       한 스풀에 채운다. 상한을 넘거나 폭이 인접하지 않으면 스풀을 닫고 새로 시작한다.
    ③ 완성된 자투리 스풀이 CP중량 하한(11) 미만이면, 마지막으로 채운 항목의 정길이/보상을
       반복한 여유분으로 채운다(=답지에서 여유분이 그 스풀의 마지막 주문과 같은 정길이로
       나오는 패턴과 일치).
    학습 근거(108LOT 중 핵심 사례): LOT2260200347(정12000+정15000 혼합, 순5+순6) ·
    LOT2260201111(정6000+정16000 혼합, 순3+순4) · LOT2260600337(정15000+정16700 혼합) ·
    LOT2260701526 등 다수에서 반복 확인된 '폭 같으면 정길이 달라도 한 스풀' 규칙.
    같은 정길이끼리는 make_spools_perorder처럼 각자 완스풀(순서 안 섞임)을 유지하고,
    이 함수는 그 자투리에만 교차-정길이 병합을 적용한다."""
    if not orders:
        return []
    widths = [o.width for o in orders]
    avg = (max(widths) + min(widths)) / 2

    def tol(w1, w2):
        return 50 if min(w1, w2) < avg else WIDTH_TOL

    spools = []
    leftovers = []  # [(order_idx, jeong, bosang, width, count)], 순서(rank) 유지
    for o in orders:
        N = _N(o.jeong, o.width, o.bosang, cp, teukgam)
        full = o.kwonchwi // N
        for _ in range(full):
            sp = Spool(o.jeong, o.width, o.bosang, N, [], 0)
            _add(sp, o.idx, o.jeong, o.bosang, N)
            spools.append(sp)
        r = o.kwonchwi - full * N
        if r > 0:
            leftovers.append((o.idx, o.jeong, o.bosang, o.width, r))

    spools += _crossjeong_forward_merge(leftovers, cp, teukgam, tol)
    return spools


def _crossjeong_forward_merge(leftovers, cp, teukgam, tol):
    """자투리 [(order_idx,jeong,bosang,width,count), ...](순서 유지)를 스풀로 만든다.

    2026-09 변경: 폭 인접 구간을 CP중량 상한까지 '그리디로 꽉 채우기' → **균등 분배**.
      1) 폭이 tol 이내로 인접한 자투리들을 하나의 run으로 묶는다(정길이는 보지 않는다 —
         이것이 '교차-정길이 병합'이다).
      2) run을 k조각으로 균등 분할(_balanced, 앞쪽이 큼)한다. k는 '가장 큰 조각도
         CP중량 33.3 이하'가 되는 최소값이다.
      3) 조각이 CP중량 하한(11) 미만이면 마지막으로 채운 항목의 정길이/보상으로 여유분을 채운다.

    종전 그리디는 스풀을 상한(33.3)까지 꽉 채워 뒤의 topup_yeoyu가 여유분을 넣을 여지를
    없앴다(202 LOT 실측: _room 거부율 24.6%). 균등 분배는 그 여지를 남긴다.
    make_spools_crossjeong·make_spools_faithful 공용 헬퍼."""
    spools = []

    # 1) 폭 인접 run으로 묶기 — flat 항목은 (순서, 정길이, 보상, 폭) 1마끼 단위
    runs, cur, cur_w = [], [], None
    for oi, jeong, bosang, width, cnt in leftovers:
        if cnt <= 0:
            continue
        if cur and abs(cur_w - width) > tol(cur_w, width):
            runs.append(cur); cur, cur_w = [], None
        cur_w = width if cur_w is None else max(cur_w, width)
        cur += [(oi, jeong, bosang, width)] * cnt
    if cur:
        runs.append(cur)

    # 2) run마다 균등 분할
    for flat in runs:
        M = len(flat)

        def fits(chunk):
            if not chunk:
                return True
            w = max(c[3] for c in chunk)
            js = calc.wonji_jeonsan_items([(j, b, 1) for _o, j, b, _w in chunk], cp, teukgam)
            return calc.cp_weight(cp, js, w) <= C.CP_WEIGHT_MAX

        k = 1
        while k < M:
            pos, ok = 0, True
            for sz in _balanced(M, k):
                if not fits(flat[pos:pos + sz]):
                    ok = False
                    break
                pos += sz
            if ok:
                break
            k += 1
        pos = 0
        for sz in _balanced(M, max(1, k)):
            chunk = flat[pos:pos + sz]; pos += sz
            if not chunk:
                continue
            # 대표 정길이/보상 = 마지막으로 채운 항목(여유분·행 그룹핑 기준), 폭 = 조각 최대폭
            last_j, last_b = chunk[-1][1], chunk[-1][2]
            sw = max(c[3] for c in chunk)
            sp = Spool(last_j, sw, last_b, _N(last_j, sw, last_b, cp, teukgam), [], 0)
            for oi, j, b, _w in chunk:
                _add(sp, oi, j, b, 1)
            items = [(j, b, 1) for _o, j, b, _w in chunk]
            extra = calc.extra_needed_for_min(items, last_j, last_b, cp, sw, teukgam)
            if extra:
                sp.yeoyu = extra
            spools.append(sp)

    return spools


def make_spools_faithful(orders, cp, teukgam):
    """답지 그대로 재현 우선 모드(신규, mode="answer_v3", 2026-08 사용자 요청).

    두 학습 결과를 합친다:
      ① make_spools_streamed와 동일하게 같은 정길이를 연속충전(건너뛰기 병합)해
         완스풀 단위로 최대한 채운다 — 답지의 '같은 정길이는 순서를 건너뛰어도
         이어붙인다' 사례를 가장 정확히 재현하는 방식(108LOT에서 반복 확인).
      ② ①에서 완스풀(N)을 다 못 채운 부분스풀(자투리)은 전부 풀어서, 순서(폭)
         순서로 forward 교차-정길이 병합을 다시 시도한다 — 정길이가 달라도 폭이
         인접하면 섞는다(답지 실제 사례: LOT2260200347 순5+순6, LOT2260201111
         순3+순4 등). ①에서 이미 완스풀을 채운 스풀은 건드리지 않는다.
    답지 텍스트 100% 일치는 여전히 보장 못 한다(자투리 처리의 최종 판단은 작업자
    재량 — 108LOT 학습 결론). 이 모드는 '구조적 유사도'를 다른 모드보다 우선한다."""
    if not orders:
        return []
    spools = make_spools_streamed(orders, cp, teukgam)

    widths = [o.width for o in orders]
    avg = (max(widths) + min(widths)) / 2

    def tol(w1, w2):
        return 50 if min(w1, w2) < avg else WIDTH_TOL

    # 완스풀(N)을 못 채운 부분스풀만 '진짜 자투리'로 보고 재처리 대상으로 뽑는다
    # (기존 yeoyu는 버리고 실제 contribs만 재료로 써서 교차-정길이 병합을 새로 시도).
    weak = [s for s in spools if _count(s) + s.yeoyu < s.N]
    if not weak:
        return spools
    keep = [s for s in spools if _count(s) + s.yeoyu >= s.N]

    rank = {o.idx: i for i, o in enumerate(orders)}
    weak.sort(key=lambda s: min((rank.get(c[0], 10**9) for c in s.contribs), default=10**9))

    leftovers = []
    for s in weak:
        w = s.width
        for oi, j, b, c in s.contribs:
            leftovers.append((oi, j, b, w, c))

    keep += _crossjeong_forward_merge(leftovers, cp, teukgam, tol)
    return keep


def make_spools_even(orders, cp, teukgam):
    """답지 형식 패커(mode='answer_v4', 2026-09 신규).

    답지에서 전수로 확인된 두 가지를 그대로 규칙화한다:
      ① **한 행의 모든 스풀은 마끼수가 같다** — LOT2260102234 11행, Case8 10행,
         LOT2260701966 4행 전부 (주문 회수합 + 여유분)이 스플수로 정확히 나누어떨어진다.
      ② 그래서 스트림을 '완스풀 N까지 욕심껏 채우기'가 아니라 **k개로 균등 분할**한다.
         예) 8,000m 43마끼·N18 → _balanced(43,3) = 15+14+14.
         완스풀 상한(33.3)은 그대로 지키되 '상한까지 채우는 것'을 목표로 삼지 않는다
         — 답지 CP44 195행의 CP중량은 중앙 28.9 / 최대 32.5로 상한에 붙지 않는다.
         (2026-09부터 answer·answer_o1·answer_v2/v3도 같은 균등 분할을 쓴다. 이 모드가
          여전히 다른 점은 폭을 스트림 최댓값으로 통일하고, schedule()에서 행 균등화
          equalize_row_makki + 행 단위 폭 평활화 smooth_row_widths를 추가로 받는 것이다.)
    스트림 구성(같은 정길이 + 폭 호환, 순번 건너뛰기 병합)은 make_spools_streamed와 같다."""
    if not orders:
        return []
    # 폭은 '평활화 전 원시 23요청폭'에서 출발한다 — 평활화는 뒤에서 행 단위로 한 번만 건다.
    rawof = {o.idx: (o.raw_width or o.width) for o in orders}
    widths = [rawof[o.idx] for o in orders]
    avg = (max(widths) + min(widths)) / 2

    def tol(w1, w2):
        return 50 if min(w1, w2) < avg else WIDTH_TOL

    used = [False] * len(orders)
    streams = []
    for i in range(len(orders)):
        if used[i]:
            continue
        o = orders[i]; used[i] = True
        contribs = [[o.idx, o.kwonchwi]]; w = rawof[o.idx]; b = o.bosang; j = o.jeong
        for k in range(i + 1, len(orders)):
            if used[k]:
                continue
            o2 = orders[k]
            if o2.jeong == j and abs(w - rawof[o2.idx]) <= tol(w, rawof[o2.idx]):
                used[k] = True
                contribs.append([o2.idx, o2.kwonchwi])
                w = max(w, rawof[o2.idx]); b = max(b, o2.bosang)
        streams.append((j, w, b, contribs))

    bmap = {o.idx: o.bosang for o in orders}
    spools = []
    for j, w, b, contribs in streams:
        flat = []
        for oid, cnt in contribs:
            flat.extend([oid] * cnt)
        M = len(flat)
        if M == 0:
            continue
        N = _N(j, w, b, cp, teukgam)                 # 상한(33.3) 기준 최대 마끼수
        mn = calc.min_n_cp11(j, b, cp, w, teukgam)
        k = max(1, math.ceil(M / N))                 # 스풀 수는 상한이 정한다
        while k > 1 and math.ceil(M / k) < mn:       # 조각이 CP중량 하한 미만이면 줄인다
            k -= 1
        sizes = _balanced(M, k)                      # 균등 분할(앞쪽이 큼) — 답지 16/14/14 패턴
        pos = 0
        for size in sizes:
            ids = flat[pos:pos + size]; pos += size
            if not ids:                              # 주문 없는 순수 여유 스풀 금지
                continue
            # 폭/보상은 '그 스풀'이 아니라 '스트림(run) 최댓값'으로 통일한다 —
            # 그래야 같은 마끼수 스풀들이 spools_to_rows에서 한 행으로 묶인다(답지 구조).
            sw, sb = w, b
            sp = Spool(j, sw, sb, _N(j, sw, sb, cp, teukgam), [], 0)
            for oid in ids:
                _add(sp, oid, j, bmap[oid], 1)
            if _count(sp) < size:                    # 균등화 — 부족분은 여유분
                sp.yeoyu = size - _count(sp)
            need = calc.min_n_cp11(j, sb, cp, sw, teukgam)
            if _count(sp) + sp.yeoyu < need:         # CP중량 하한 보장
                sp.yeoyu = need - _count(sp)
            _trim_over_cap([sp], cp, teukgam)        # CP중량 상한 보장
            spools.append(sp)
    return spools


def equalize_row_makki(spools, cp, teukgam, choji_len, hi=5000):
    """답지 형식(answer_v4) 전용, 2026-09 신규.

    같은 (폭, 대표 정길이) 그룹의 스풀 마끼수를 **그룹 최댓값에 맞춘다**(부족분은 여유분).
    답지의 '한 행의 모든 스풀은 마끼수가 같다'를 실현하는 단계다.
      예) 15 + 14 + 14 → 15 + 15 + 15   (뒤에 합권 짝수화가 걸리면 16 + 16 + 16)

    단 두 가지를 넘지 않는 선에서만 한다:
      - CP중량 상한 33.3 (한 스풀이라도 넘으면 그 그룹은 건드리지 않음)
      - 총생산길이 한도 = 초지공정길이 + hi(기본 5,000). 넘으면 그 그룹은 되돌린다.
    초지공정길이가 0이면 길이 한도는 보지 않는다."""
    if not spools:
        return
    limit = (choji_len + hi) if choji_len else None
    total = sum(_spool_saengsan(s, cp, teukgam) for s in spools)
    groups = {}
    for s in spools:
        groups.setdefault((s.width, s.jeong), []).append(s)
    for g in groups.values():
        if len(g) < 2:
            continue
        target = max(_count(s) + s.yeoyu for s in g)
        plan = [(s, target - (_count(s) + s.yeoyu)) for s in g]
        plan = [(s, n) for s, n in plan if n > 0]
        if not plan:
            continue
        if any(not _room(s, cp, teukgam, n) for s, n in plan):   # CP중량 상한 위반
            continue
        before = sum(_spool_saengsan(s, cp, teukgam) for s, _n in plan)
        for s, n in plan:
            s.yeoyu += n
        after = sum(_spool_saengsan(s, cp, teukgam) for s, _n in plan)
        if limit is not None and total - before + after > limit:
            for s, n in plan:                                    # 길이 한도 초과 → 되돌림
                s.yeoyu -= n
            continue
        total = total - before + after


def smooth_row_widths(spools, cp, teukgam, cap=calc.ADJ_MAX_DROP):
    """답지 형식 폭 배치(2026-09 신규). 폭 평활화(인접 하락 cap=30 제한)를 '주문' 단위가
    아니라 '행' 단위로 다시 건다.

    근거: 답지는 폭을 주문이 아니라 행에 매긴다(LOT2260701966 답지 폭 5010/4990/4960/4950
    — 같은 순서3이 4990 행과 4960 행에 서로 다른 폭으로 들어감). 주문 단위로 평활화하면
    한 주문의 모든 스풀이 같은 폭으로 묶여 답지 구조와 어긋난다.

    행 경계는 spools_to_rows와 같은 키(폭, 대표 정길이, 실채움)로 잡는다 — 폭이 같아도
    마끼수가 다르면 다른 행이므로 각각 한 계단씩 내려간다(2026-09 수정: 폭만으로 묶어
    8,000m 두 행이 한 덩어리로 올라가던 것을 바로잡음).
    폭을 넓히면 CP중량이 올라가므로 상한(33.3)을 넘기는 폭까지는 올리지 않는다.
    호출 전제: spools가 폭 내림차순으로 정렬돼 있어야 한다."""
    if not spools:
        return

    def rowkey(s):
        return (s.width, s.jeong, _count(s) + s.yeoyu)

    prev = None
    i, n = 0, len(spools)
    while i < n:
        key = rowkey(spools[i])
        j = i
        while j < n and rowkey(spools[j]) == key:
            j += 1
        w = key[0]
        nw = w if prev is None else max(w, prev - cap)
        while nw > w:                      # CP중량 상한을 넘기면서까지 넓히지 않는다
            ok = True
            for k in range(i, j):
                orig = spools[k].width
                spools[k].width = nw
                over = _cp_weight(spools[k], cp, teukgam) > C.CP_WEIGHT_MAX
                spools[k].width = orig
                if over:
                    ok = False
                    break
            if ok:
                break
            nw -= 10                       # 23요청폭은 10단위
        for k in range(i, j):
            spools[k].width = nw
        prev = nw
        i = j


def make_spools_answer_o1(orders, cp, teukgam, choji_len=0, strict_width=False,
                          hi=TOPUP_HI):
    """'여유분 우선' 방식(mode='answer_o1', 2026-09 개정).

    ① 선행구간 범위: 순서1부터 시작해 **'권취 % N != 0'인 첫 주문까지**(그 주문 포함).
       권취가 완스풀 N의 배수로 딱 떨어지는 동안은 다음 순서로 계속 간다.
       (202 LOT 실측: 93%가 순서1 하나뿐, 최대 3개)
    ② 선행구간 안에서는 **같은 정길이 + 폭 인접(tol)이면 한 스풀에 병합**한다.
       (2026-09: 종전 '어떤 병합도 하지 않는다' 규칙 삭제)
    ③ 분할은 _balanced 균등. 스풀 수 k = ceil(권취/N)이며, 조각이 CP중량 하한 미만이면
       k를 줄인다(실측상 발동하지 않는 안전장치).
    ④ 여유분은 **총생산 한도(초지공정길이 + TOPUP_HI) 안에서 완스풀(N)까지** 채운다.
       한도에 걸리면 채울 수 있는 만큼만, 최소한 CP중량 하한(11)까지는 채운다.
       (2026-09: 종전 'CP중량 하한까지만'에서 변경)
    ⑤ split 이후 주문은 make_spools_perorder(순서별 완스풀 + 자투리 병합)로 넘긴다.

    strict_width=True(answer_v5 전용): ②의 선행구간 병합과 ⑤의 자투리 병합 모두
    **23요청폭이 정확히 같을 때만** 허용한다(폭차 tol 무시). 기본값 False면 종전 동작."""
    if not orders:
        return []
    # ① 선행구간 범위 결정
    split = 0
    for i, o in enumerate(orders):
        N = _N(o.jeong, o.width, o.bosang, cp, teukgam)
        split = i + 1
        if o.kwonchwi % N != 0:
            break
    head = orders[:split]

    widths = [o.width for o in orders] or [0]
    avg = (max(widths) + min(widths)) / 2

    def tol(w1, w2):
        if strict_width:
            return 0                       # 폭이 정확히 같을 때만 병합(answer_v5)
        return 50 if min(w1, w2) < avg else WIDTH_TOL

    # ② 선행구간 병합 — 같은 정길이 + 폭 인접이면 한 스트림
    used = [False] * len(head)
    streams = []
    for i in range(len(head)):
        if used[i]:
            continue
        o = head[i]; used[i] = True
        items = [(o.idx, o.kwonchwi)]; w = o.width; b = o.bosang; j = o.jeong
        for k2 in range(i + 1, len(head)):
            if used[k2]:
                continue
            o2 = head[k2]
            if o2.jeong == j and abs(w - o2.width) <= tol(w, o2.width):
                used[k2] = True
                items.append((o2.idx, o2.kwonchwi))
                w = max(w, o2.width); b = max(b, o2.bosang)
        streams.append((j, w, b, items))

    bmap = {o.idx: o.bosang for o in orders}
    spools, head_spools = [], []
    for j, w, b, items in streams:
        flat = []
        for oid, cnt in items:
            flat += [oid] * cnt
        M = len(flat)
        if M == 0:
            continue
        N = _N(j, w, b, cp, teukgam)
        mn = calc.min_n_cp11(j, b, cp, w, teukgam)
        # ③ 균등 분할
        k = max(1, math.ceil(M / N))
        while k > 1 and math.ceil(M / k) < mn:
            k -= 1
        pos = 0
        for size in _balanced(M, k):
            ids = flat[pos:pos + size]; pos += size
            if not ids:                              # 주문 없는 순수 여유 스풀 금지
                continue
            sp = Spool(j, w, b, N, [], 0)
            for oid in ids:
                _add(sp, oid, j, bmap[oid], 1)
            if _count(sp) < mn:                      # CP중량 하한 보장
                sp.yeoyu = mn - _count(sp)
            spools.append(sp)
            head_spools.append(sp)

    # ⑤ 나머지 주문
    spools += make_spools_perorder(orders[split:], cp, teukgam,
                                   strict_width=strict_width)
    # ④ 한도 안에서 선행구간 스풀을 완스풀까지 채운다
    _fill_to_full(head_spools, spools, cp, teukgam, choji_len, hi)
    return spools


def _schedule_per_order(orders, cp, teukgam, choji_len=0):
    """순서별 1행: 각 주문을 ceil(권취/N)개의 '동일 크기' 스풀로 나눠 **행수 = 주문수**.
    병합·건너뛰기 없음. 각 스풀은 CP중량 11~33, 합권(<10000m)은 스풀당 짝수.
    총생산길이 < 초지면 넓은 폭 순서부터 스풀당 마끼 +1(여유)로 보충."""
    rows = []
    for o in orders:
        N = _N(o.jeong, o.width, o.bosang, cp, teukgam)
        mn = calc.min_n_cp11(o.jeong, o.bosang, cp, o.width, teukgam)
        M = max(1, o.kwonchwi)
        k = max(1, math.ceil(M / N))          # 스풀 수
        s = max(math.ceil(M / k), mn)         # 스풀당 마끼(동일)
        if o.jeong < C.SHORT_LEN and s % 2 == 1:
            s += 1                            # 합권 짝수
        rows.append(Row(o.width, o.jeong, s, k, [[o.idx, o.jeong, o.bosang, M]], 0, o.bosang))

    def recompute():
        t = 0
        for r in rows:
            r.yeoyu = r.spools * r.N - sum(c for _, _, _, c in r.contribs)
            r.choji_real = calc.choji_saengsan_real(r.jeong, r.bosang, r.N, cp, teukgam)
            r.saengsan = calc.saengsan_len(r.jeong, r.bosang, r.N, r.spools, cp, teukgam)
            t += r.saengsan
        return t

    total = recompute()
    guard = 0
    while choji_len and total < choji_len + 1000 and guard < 1000:
        r = sorted(rows, key=lambda x: (-x.width, x.jeong))[0]
        r.N += 2 if r.jeong < C.SHORT_LEN else 1   # 짧은롤은 짝수 유지
        total = recompute()
        guard += 1
    rows.sort(key=lambda r: (-r.width, r.contribs[0][0] if r.contribs else 999))
    recompute()
    return rows, sum(r.saengsan for r in rows)


def schedule(orders, cp, teukgam, choji_gongjeong_len=0, mode="loss"):
    """mode='answer'(답지식: 완스풀 연속충전+건너뛰기) | 'answer_o1'(순서1 여유분 우선) |
       'answer_v2'(교차-정길이 병합, 자투리 전체를 폭 인접 기준으로 섞음) |
       'answer_v3'(답지 재현 우선 — 정길이별 완스풀 우선 + 그래도 남는 순수여유
       자투리만 교차-정길이로 재시도) |
       'answer_v4'(답지 형식, 2026-09 신규 — 스트림 균등 분할로 '한 행의 스풀은 같은
       마끼수'를 맞추고 폭 평활화를 행 단위로 건다) |
       'answer_v5'(답지 폭반올림, 2026-10 신규 — answer_o1 구조에 ①23요청폭 10단위
       **반올림**(기본은 내림) ②자투리는 **폭이 정확히 같을 때만** 병합, 다르면 여유분
       두 가지를 더한 것. 이 모드에서만 폭 규칙이 다르다) | 'per_order'(순서별 1행)
       | 'loss'(손율 최소, 기본) | 'convenience'(작업 편리성).
    입력 orders는 항상 23요청지폭 내림차순으로 재정렬·재번호(rank_orders)한 뒤 패킹한다
    (2026-08 CM2최종학습 108LOT 확정 규칙 — 답지 순서라벨=폭내림차순순위)."""
    if mode == "answer_v5":
        # 폭 재계산은 순위 재부여(rank_orders) 전에 — 순서 라벨이 폭 내림차순 순위라서
        # 폭이 바뀌면 라벨도 그 폭 기준으로 다시 매겨져야 한다.
        orders = reround_widths(orders, cp, teukgam)
    orders = rank_orders(orders)
    if mode == "per_order":
        return _schedule_per_order(orders, cp, teukgam, choji_gongjeong_len)
    if mode == "answer":
        spools = make_spools_streamed(orders, cp, teukgam)
    elif mode == "answer_o1":
        spools = make_spools_answer_o1(orders, cp, teukgam, choji_gongjeong_len)
    elif mode == "answer_v5":
        spools = make_spools_answer_o1(orders, cp, teukgam, choji_gongjeong_len,
                                       strict_width=True, hi=_v5_hi(choji_gongjeong_len))
    elif mode == "answer_v2":
        spools = make_spools_crossjeong(orders, cp, teukgam)
    elif mode == "answer_v3":
        spools = make_spools_faithful(orders, cp, teukgam)
    elif mode == "answer_v4":
        spools = make_spools_even(orders, cp, teukgam)
        # 행 균등화(같은 폭·정길이 그룹의 마끼수를 최댓값으로) — 길이 한도 안에서만
        equalize_row_makki(spools, cp, teukgam, choji_gongjeong_len)
    else:
        spools = make_spools(orders, cp, teukgam)
    if mode == "convenience":
        spools = merge_for_convenience(spools, cp, teukgam)
    topup_yeoyu(spools, cp, teukgam, choji_gongjeong_len,
                hi=_v5_hi(choji_gongjeong_len) if mode == "answer_v5" else TOPUP_HI,
                concentrate=(mode in ("answer_o1", "answer_v5")))
    pad_long_rolls(spools, cp, teukgam)     # 긴 롤(N<=7) 단독 부분스풀 완스풀 패딩
    enforce_even_short(spools, hapgwon=(not teukgam),  # 특감(SFH/SL)만 짝수화 제외
                       cp=cp, teukgam=teukgam)
    _trim_over_cap(spools, cp, teukgam)     # CP중량 상한 최종 안전망(2026-09)
    # 폭 내림차순, 동폭이면 가장 앞 순서 우선(결과가 순서 흐름대로 보이도록)
    spools.sort(key=lambda s: (-s.width, min((c[0] for c in s.contribs), default=999)))
    if mode == "answer_v4":
        smooth_row_widths(spools, cp, teukgam)   # 폭 평활화를 행 단위로(답지 형식)
        _trim_over_cap(spools, cp, teukgam)  # 폭이 넓어졌으면 중량 재확인
    rows = spools_to_rows(spools, cp, teukgam)
    total = sum(r.saengsan for r in rows)
    return rows, total
