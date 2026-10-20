# -*- coding: utf-8 -*-
"""gemini_parse._loads 방어 파싱 회귀 테스트 (실제 API 호출 없음).

재현 대상 버그: 모델이 JSON 뒤에 내용을 더 붙여 보내면
  json.decoder.JSONDecodeError: Extra data: line 121 column 1 (char 2477)
로 파싱 전체가 실패했다. 첫 객체만 끊어 읽고, 뒤 객체는 병합(중복 제거)한다.
"""
import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, ".")
from jipok import gemini_parse as G

BASE = {
    "jong": "한솔감열지 SF", "cp": 48, "lot": "2260102234", "choji": 4782977,
    "teukgam": False,
    "orders": [
        {"sunseo": 1, "jeong": 15000, "kwonchwi": 4, "jipok": 4940, "gil": 60000,
         "wonji": 5020, "gyukyeok": [{"w": 483, "n": 3}]},
        {"sunseo": 2, "jeong": 15000, "kwonchwi": 14, "jipok": 4910, "gil": 210000,
         "wonji": 4990, "gyukyeok": [{"w": 483, "n": 3}]},
    ],
    "wanjeong": [{"garo": 483, "gil": 15000}, {"garo": 390, "gil": 12000}],
}
J = json.dumps(BASE, ensure_ascii=False, indent=2)

# 분할 출력: 2번째 객체에 주문 1건 + 완정 1행이 더 들어옴
PART2 = json.dumps({
    "orders": [{"sunseo": 3, "jeong": 8000, "kwonchwi": 39, "jipok": 4860,
                "gil": 312000, "wonji": 4940, "gyukyeok": [{"w": 403, "n": 1}]}],
    "wanjeong": [{"garo": 403, "gil": 8000}],
}, ensure_ascii=False, indent=2)

fails = []


def check(name, text, want_orders, want_wanjeong, want_note=None):
    notes = []
    try:
        d = G._loads(text, notes)
    except Exception as e:                      # noqa: BLE001
        fails.append(f"{name}: 예외 {type(e).__name__}: {e}")
        print(f"[FAIL] {name}: {type(e).__name__}: {str(e)[:70]}")
        return
    got_o = [o.get("sunseo") for o in d.get("orders", [])]
    got_w = len(d.get("wanjeong", []))
    ok = (got_o == want_orders and got_w == want_wanjeong
          and (want_note is None or any(want_note in n for n in notes)))
    if not ok:
        fails.append(f"{name}: orders={got_o}(기대 {want_orders}) "
                     f"wanjeong={got_w}(기대 {want_wanjeong}) notes={notes}")
    print(f"[{'PASS' if ok else 'FAIL'}] {name:<34} orders={got_o} wanjeong={got_w} "
          f"notes={len(notes)}")


# 1) 정상 JSON — 기존 동작 유지
check("정상 JSON", J, [1, 2], 2)
# 2) 코드블록 감싸짐(언어 태그 포함)
check("```json 감싸짐", f"```json\n{J}\n```", [1, 2], 2)
# 3) ★재현 케이스★ JSON 뒤에 두 번째 객체(분할 출력) → 병합
check("Extra data: 분할 출력", f"{J}\n{PART2}", [1, 2, 3], 3, "병합")
# 4) ★재현 케이스★ 같은 응답을 두 번 출력 → 중복 제거(주문이 두 배가 되면 안 됨)
check("Extra data: 응답 반복", f"{J}\n{J}", [1, 2], 2, "병합")
# 5) JSON 뒤 설명문 → 무시
check("JSON 뒤 설명문", f"{J}\n\n위 표에서 순서 3은 판독이 불확실합니다.", [1, 2], 2, "무시")
# 6) 코드블록 + 뒤 설명문(기존 코드가 못 벗기던 조합)
check("```json + 뒤 설명문", f"```json\n{J}\n```\n설명: 끝.", [1, 2], 2, "무시")
# 7) JSON 뒤에 주문 1건짜리 조각 → 최상위 키가 없으므로 병합하지 않음
check("뒤 조각(주문 1건)", f'{J}\n{{"sunseo": 9, "jeong": 15000}}', [1, 2], 2)

# 8) 응답 절삭 → 조각을 받아들이지 말고 '잘렸을 수 있다'는 예외
try:
    G._loads(J[:len(J) // 2])
    fails.append("절삭 응답: 예외가 나야 하는데 통과함")
    print("[FAIL] 절삭 응답: 예외 없음")
except ValueError as e:
    ok = "잘렸을" in str(e)
    if not ok:
        fails.append(f"절삭 응답: 안내 문구 없음 — {e}")
    print(f"[{'PASS' if ok else 'FAIL'}] {'절삭 응답 → 명확한 예외':<34} {str(e)[:60]}")

# 9) JSON이 아예 없음
try:
    G._loads("죄송합니다. 이미지를 읽을 수 없습니다.")
    fails.append("JSON 없음: 예외가 나야 하는데 통과함")
    print("[FAIL] JSON 없음: 예외 없음")
except ValueError as e:
    ok = "JSON 객체가 없습니다" in str(e)
    if not ok:
        fails.append(f"JSON 없음: 안내 문구 없음 — {e}")
    print(f"[{'PASS' if ok else 'FAIL'}] {'JSON 없음 → 명확한 예외':<34} {str(e)[:60]}")

# ── 10) API 키 마스킹(redact) ────────────────────────────────────────────
# 공개 앱에서 예외 메시지에 요청 URL(...?key=AIza...)이 섞여 화면에 찍히는 것을 막는다.
FAKE = "AIzaSyD" + "x" * 32          # 실제 키 아님 — 형식만 흉내낸 더미
print()
red_cases = [
    ("키 단독",        FAKE),
    ("URL 쿼리",       f"https://generativelanguage.googleapis.com/v1/models?key={FAKE}"),
    ("예외 문구 안",    f"429 RESOURCE_EXHAUSTED for url ...?key={FAKE}&alt=json"),
    ("api_key 파라미터", f"POST /v1?api_key={FAKE} failed"),
    ("token 파라미터",  "auth failed: access_token=ya29.SOMETHING_SECRET&x=1"),
    ("키 없는 평문",     "Gemini 서버가 일시적으로 혼잡합니다(503)."),
]
for name, raw in red_cases:
    out = G.redact(raw)
    leaked = FAKE in out or "ya29.SOMETHING_SECRET" in out
    if leaked:
        fails.append(f"redact 누출: {name} → {out}")
    print(f"[{'FAIL' if leaked else 'PASS'}] redact {name:<18} {out[:70]}")
# 키가 없는 문구는 원문이 그대로 유지돼야 한다(과잉 마스킹 방지)
plain = "Gemini 서버가 일시적으로 혼잡합니다(503)."
if G.redact(plain) != plain:
    fails.append(f"redact 과잉 마스킹: {G.redact(plain)}")

print("\n전체:", "ALL PASS" if not fails else "FAIL")
for f in fails:
    print("  -", f)
if fails:
    sys.exit(1)
