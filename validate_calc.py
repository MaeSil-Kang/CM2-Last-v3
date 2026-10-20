# -*- coding: utf-8 -*-
"""해답 20개로 calc.py 결정형 공식을 검증.

각 행(단일 정길이인 경우)에 대해:
  N = total_makki / spools 가 정수인지
  보상 in {120,170,240} 중 초지생산실길이를 재현하는 값 탐색
  생산길이 = round100(원지전산)*spools 재현 여부
재현 안 되면 *** 표시(작업자 오타 또는 보상/N 특수 후보).
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, ".")
from jipok import calc, constants as C
from jipok.answers import load_answers

PATH = r"C:\Users\Hansol\Desktop\CM2 Final\학습\Case+1~20+문제.xlsx"

cases = load_answers(PATH)
ok = bad = skip = 0
typo_lines = []

for c in cases:
    for i, r in enumerate(c.rows, 1):
        jl = r.jeong_lens
        tm = r.total_makki
        if r.spools <= 0 or tm <= 0:
            skip += 1; continue
        if len(jl) != 1:
            skip += 1; continue          # 병합(혼합 정길이) 행은 길이검증 생략
        jeong = jl[0]
        if tm % r.spools != 0:
            skip += 1; continue
        N = tm // r.spools
        # 보상 후보 탐색
        match_b = None
        for b in (C.BOSANG_ROLL, C.BOSANG_WIDTH, C.BOSANG_LEN):
            if jeong < C.SHORT_LEN and b != C.BOSANG_ROLL:
                continue
            cr = calc.choji_saengsan_real(jeong, b, N, c.cp, c.teukgam)
            if cr == r.choji_real:
                match_b = b; break
        if match_b is None:
            # 가장 가까운 보상으로 차이 보고
            best = min((C.BOSANG_ROLL, C.BOSANG_WIDTH, C.BOSANG_LEN),
                       key=lambda b: abs(calc.choji_saengsan_real(jeong, b, N, c.cp, c.teukgam) - r.choji_real))
            exp = calc.choji_saengsan_real(jeong, best, N, c.cp, c.teukgam)
            typo_lines.append(f"  C{c.no} r{i} CP{c.cp} {jeong}m N{N}x{r.spools}: "
                              f"초지실 해답={r.choji_real} vs 계산(보상{best})={exp} (차 {r.choji_real-exp:+d})")
            bad += 1
            continue
        # 생산길이 검증
        exp_s = calc.saengsan_len(jeong, match_b, N, r.spools, c.cp, c.teukgam)
        if exp_s == r.saengsan:
            ok += 1
        else:
            typo_lines.append(f"  C{c.no} r{i} CP{c.cp} {jeong}m N{N}x{r.spools} 보상{match_b}: "
                              f"생산길이 해답={r.saengsan} vs 계산={exp_s} (차 {r.saengsan-exp_s:+d})")
            bad += 1

print(f"=== calc.py 검증 (단일정길이 행만) ===")
print(f"OK={ok}  불일치={bad}  생략(병합/혼합)={skip}\n")
print("불일치 행(작업자 오타 또는 보상/N 특수 후보):")
for ln in typo_lines:
    print(ln)
