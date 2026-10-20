"""Gemini 2.5로 스캔 스케줄 PDF를 파싱해 주문 입력값(헤더+RE공정)을 추출.

google-genai SDK. 고해상도 이미지 전송 + 교차검증 자동교정:
  - 권취 ≈ 길이 ÷ 정길이  (원지폭을 권취로 읽는 오독 교정)
  - 싱글규격 ≈ 원지폭 − 80 (원지폭을 싱글규격으로 읽는 오독 교정)
API 키는 인자 또는 환경변수 GEMINI_API_KEY/GOOGLE_API_KEY.
"""
import os
import io
import re
import json
import time

# 2026-08: gemini-2.5-flash는 신규 키에 미제공(은퇴 중) → 3.x/최신 별칭 사용.
# gemini-flash-latest = '항상 최신 flash'를 가리키는 롤링 별칭(다음 은퇴에도 안 깨짐).
MODELS = ["gemini-flash-latest", "gemini-3.5-flash", "gemini-3.5-flash-lite",
          "gemini-3.6-flash", "gemini-2.5-pro"]
FALLBACK = ["gemini-3.5-flash", "gemini-flash-latest", "gemini-3.5-flash-lite"]  # 실패 시 대체 순서
DEFAULT_MODEL = "gemini-flash-latest"
WONJI_GAP = 80   # RE 원지폭 ≈ 싱글규격 + 80 (경험값, CP44·55 공통)


_KEY_PAT = re.compile(r"AIza[0-9A-Za-z_\-]{10,}")
_KEYPARAM_PAT = re.compile(r"(?i)\b(key|api[_-]?key|token|access_token)=([^&\s\"'\\]+)")


def redact(text) -> str:
    """사용자에게 보여줄 문자열에서 API 키로 보이는 부분을 가린다(2026-09 신규).

    공개 앱에서는 예외 메시지에 요청 URL(`...?key=AIza...`)이 섞여 나올 수 있어,
    화면·경고로 나가는 모든 경로에서 한 번 더 지운다. 두 가지를 잡는다:
      1) Google API 키 형식(AIza로 시작하는 토큰)
      2) URL 쿼리의 key/api_key/token 파라미터 값(형식 무관)
    """
    s = str(text)
    s = _KEY_PAT.sub("AIza***REDACTED***", s)
    s = _KEYPARAM_PAT.sub(r"\1=***REDACTED***", s)
    return s


def _is_quota_error(msg: str) -> bool:
    return "RESOURCE_EXHAUSTED" in msg or "429" in msg


def _is_model_error(msg: str) -> bool:
    """모델이 없음/미제공(404 NOT_FOUND, 'no longer available' 등) → 다른 모델로 폴백."""
    u = msg.upper()
    return ("NOT_FOUND" in u or "404" in u or "NO LONGER AVAILABLE" in u
            or "NOT AVAILABLE" in u or "NOT SUPPORTED" in u)


def _is_transient_error(msg: str) -> bool:
    """일시적 서버 혼잡/오류(503 등) — 잠깐 기다렸다 재시도하면 풀린다."""
    u = msg.upper()
    return ("503" in u or "UNAVAILABLE" in u or "INTERNAL" in u or "OVERLOAD" in u
            or "HIGH DEMAND" in u or "TRY AGAIN" in u)


def _retry_delay(msg: str) -> float:
    """에러 메시지에서 재시도 권장 지연(초) 추출. 없으면 0."""
    m = re.search(r"retry in ([\d.]+)s", msg) or re.search(r"retryDelay'?:?\s*'?(\d+)s", msg)
    return float(m.group(1)) if m else 0.0


def _generate(client, types, model, parts):
    # max_output_tokens는 두지 않는다(모델 기본값). 2페이지 JSON절삭은 페이지 분리로 해결됨 —
    # 상한을 걸면 오히려 큰 LOT 응답을 자를 수 있어 1페이지 동작을 예전과 동일하게 둔다.
    cfg = types.GenerateContentConfig(response_mime_type="application/json", temperature=0)
    resp = client.models.generate_content(model=model, contents=parts, config=cfg)
    return (resp.text or "").strip()


_TOP_KEYS = {"jong", "cp", "lot", "choji", "teukgam", "orders", "wanjeong"}
_FENCE_HEAD = re.compile(r"^\s*```[A-Za-z0-9_+-]*\s*")
_FENCE_TAIL = re.compile(r"\s*```\s*$")


def _strip_fence(t: str) -> str:
    """```json … ``` 껍질 제거(언어 태그 유무 무관)."""
    return _FENCE_TAIL.sub("", _FENCE_HEAD.sub("", t)).strip()


def _row_key(key, row):
    """병합 시 중복 판정 키. 주문은 순서번호, 완정공정은 (가로,길이)로 본다."""
    if not isinstance(row, dict):
        return ("raw", json.dumps(row, sort_keys=True, ensure_ascii=False))
    if key == "orders" and row.get("sunseo") not in (None, ""):
        return ("sunseo", str(row.get("sunseo")).strip())
    if key == "wanjeong":
        return ("wj", str(row.get("garo")), str(row.get("gil")))
    return ("raw", json.dumps(row, sort_keys=True, ensure_ascii=False))


def _merge_json(base: dict, extra: dict) -> None:
    """뒤따라온 JSON 객체를 첫 객체에 병합한다.
    목록(orders/wanjeong)은 '없는 항목만' 이어 붙이고(모델이 같은 응답을 두 번 써도
    주문이 두 배가 되지 않음), 스칼라는 첫 객체가 비어 있을 때만 채운다."""
    for k, v in extra.items():
        if isinstance(v, list):
            cur = base.get(k)
            if not isinstance(cur, list):
                base[k] = list(v)
                continue
            seen = {_row_key(k, r) for r in cur}
            for r in v:
                rk = _row_key(k, r)
                if rk not in seen:
                    seen.add(rk)
                    cur.append(r)
        elif not base.get(k) and v:
            base[k] = v


def _tail_objects(t: str, pos: int):
    """첫 JSON 뒤에 남은 텍스트에서 '최상위 스키마 키를 가진' JSON 객체만 골라 낸다.
    주문 1건짜리 조각이나 설명문은 무시해 엉뚱한 병합을 막는다."""
    dec = json.JSONDecoder()
    out = []
    n = len(t)
    while pos < n:
        j = t.find("{", pos)
        if j < 0:
            break
        try:
            val, end = dec.raw_decode(t, j)
        except ValueError:
            pos = j + 1
            continue
        pos = end
        if isinstance(val, dict) and (set(val) & _TOP_KEYS):
            out.append(val)
    return out


def _loads(text, notes=None):
    """JSON 파싱 — 모델이 코드블록·설명문을 덧붙이거나 JSON 객체를 두 개 이상
    내보내도 깨지지 않게 방어한다(json.loads의 'Extra data: line N column 1' 오류).

    규칙
      1) ``` 껍질을 벗기고 첫 '{'부터 raw_decode로 '첫 객체 하나'만 끊어 읽는다.
      2) 뒤에 남은 텍스트에 최상위 스키마 키를 가진 객체가 더 있으면 병합한다
         (분할 출력 대응). 중복 주문/완정행은 버린다(반복 출력 대응).
      3) 그 외 잡문은 무시하고 경고만 남긴다.
      4) 첫 객체조차 못 읽으면(=응답 절삭 등) 원인을 담아 예외를 올린다."""
    t = _strip_fence((text or "").strip())
    start = t.find("{")
    if start < 0:
        raise ValueError(f"Gemini 응답에 JSON 객체가 없습니다. 응답 앞부분: {t[:200]!r}")
    try:
        data, end = json.JSONDecoder().raw_decode(t, start)
    except ValueError as e:
        raise ValueError(
            f"Gemini 응답 JSON을 읽지 못했습니다({e}). 응답이 중간에 잘렸을 수 있습니다 — "
            f"다시 시도하거나 다른 모델을 선택하세요. 응답 앞부분: {t[:200]!r}") from e
    if not isinstance(data, dict):
        raise ValueError(f"Gemini 응답 최상위가 JSON 객체가 아닙니다({type(data).__name__}).")

    rest = t[end:].strip()
    if rest:
        extras = _tail_objects(t, end)
        for ex in extras:
            _merge_json(data, ex)
        if notes is not None:
            notes.append(
                f"모델이 JSON 뒤에 추가 출력을 붙임 — 추가 객체 {len(extras)}건 병합 후 진행"
                if extras else "모델이 JSON 뒤에 설명문을 붙임 — 무시하고 진행")
    return data


def _generate_with_fallback(client, types, model, parts):
    """요청 모델 → 폴백 모델 순으로 시도. 반환: (text, used_model, notes).
      - 503/혼잡(일시적): 같은 모델 백오프 재시도(2·4초) 후 다음 모델.
      - 429(쿼터): flash 분당한도면 한 번 대기 후 재시도, 아니면 다음 모델.
      - 그 외(400/인증 등): 즉시 표면화."""
    candidates = [model] + [m for m in FALLBACK if m != model]
    notes, last, kind = [], None, None
    for idx, m in enumerate(candidates):
        for attempt in range(3):   # 최대 3회 시도(일시적 오류 백오프용)
            try:
                return _generate(client, types, m, parts), m, notes
            except Exception as e:  # noqa: BLE001
                msg = str(e)
                last = e
                if _is_transient_error(msg):
                    kind = "transient"
                    if attempt < 2:
                        time.sleep(2 * (attempt + 1))   # 2초, 4초 백오프
                        continue
                    break   # 재시도 소진 → 다음 모델
                if _is_quota_error(msg):
                    kind = "quota"
                    delay = _retry_delay(msg)
                    # flash 계열 '분당 한도'는 잠깐 기다리면 풀림 → 같은 모델 1회 대기 재시도
                    if attempt == 0 and "flash" in m and 0 < delay <= 25:
                        time.sleep(delay + 1)
                        continue
                    break   # 다음 후보 모델로
                if _is_model_error(msg):
                    kind = "model"
                    break   # 이 모델은 못 씀(404/미제공) → 다음 후보 모델로
                raise   # 재시도 불가 오류는 즉시 표면화
        if idx < len(candidates) - 1:
            notes.append(f"{m} 실패 → 대체 모델 시도")
    # 모든 후보 실패 — 원인별 안내
    if kind == "transient":
        raise RuntimeError(
            "Gemini 서버가 일시적으로 혼잡합니다(503). 잠시(수십 초~몇 분) 뒤 다시 시도하세요."
            f"\n원본: {redact(last)}")
    if kind == "model":
        raise RuntimeError(
            "선택한 모델을 이 API 키로 쓸 수 없습니다(404/미제공). 모델을 'gemini-flash-latest' "
            "또는 'gemini-3.5-flash'로 바꿔 다시 시도하세요."
            f"\n원본: {redact(last)}")
    raise RuntimeError(
        "Gemini 모든 모델 쿼터 초과입니다. 잠시 후 재시도하거나 결제(billing) 설정을 확인하세요."
        f"\n원본: {redact(last)}")

PROMPT = """너는 한솔제지 PM23 '스케줄'(주문) 스캔 이미지를 읽어 지폭스케줄 입력값을 뽑는 도구다.
한국어 표가 들어 있다. 상단 헤더, 'RE 공정' 재단표, '완정공정(물류입고)' 표를 정밀하게 읽어라.
너는 '읽기'만 한다. 보상길이 등 판정은 코드가 하니, 보이는 숫자를 정확히 옮기는 데만 집중하라.

[RE 공정 재단표 컬럼 순서(왼→오른)]: 순서 | 규격1~9 | 지폭 | 재단량 | 길이(m) | 권취 | 원지폭
- '지폭' 칸에는 두 숫자가 있다: 앞(위)=jipok(지폭값, 예 4915), 뒤(아래)=jeong(정길이, 예 12000).
- 'jipok'(지폭)    = '지폭' 칸의 **앞(위)** 숫자 (예 '4915 / 12,000' → 4915).
- 'jeong'(정길이)  = '지폭' 칸의 **뒤(아래)** 숫자 (예 12000). 보통 12000/15000 같은 만 단위 둥근 수.
- 'gil'(길이)      = '길이(m)' 칸 두 숫자 중 **아래(작은)** 값 (예 '90,020 / 84,952' → 84952).
- 'kwonchwi'(권취) = '권취' 열 숫자(작은 정수). **반드시 검산: kwonchwi = round(gil ÷ jeong).**
- 'wonji'(원지폭)  = 맨 오른쪽 '원지폭' 열 숫자 (보통 jipok+80).
- 'gyukyeok'(규격) = '규격1~9' 칸들. 각 칸을 {"w":앞숫자,"n":x뒤 배수}로.
    예 "403x4"→{"w":403,"n":4}, "1114x1"→{"w":1114,"n":1}, "1585"(x없음)→{"w":1585,"n":1}. 빈 칸 제외.
  ★ 규격 칸은 **2단**이다: 위(큰 글씨)=여러 장을 합친 '합계폭', 아래(작은 글씨, 네모 박스)=
    "앞폭x배수". **반드시 아래 작은 박스를 읽어라.** 위의 큰 숫자는 절대 w로 쓰지 마라.
    예) 위 1480 / 아래 485x3 → {"w":485,"n":3}  (1480을 쓰면 틀림)
       위 1625 / 아래 400x4 → {"w":400,"n":4}
    아래 박스가 정말 안 보이는 칸만 위 숫자를 w로 쓰고 n=1로 둔다.

★★ 절대 혼동 금지 ★★
- jipok(지폭, 앞 숫자)은 jeong(정길이, 뒤 숫자)·wonji(맨오른쪽 원지폭)와 다르다. 보통 jipok ≈ wonji−80, jipok < wonji.
- jipok 값은 순서가 커질수록 **점점 작아진다(내림차순)**.
- jeong(정길이)는 '지폭' 칸 뒤 숫자뿐이다. 왼쪽 규격칸(1120,960,810,800 등)이나
  표 오른쪽의 '규격/지관수' 미니표(1486/12, 1120/21 …)를 jeong·kwonchwi로 쓰지 마라.
- kwonchwi(권취)는 '길이(m)'와 '원지폭' 칸 **사이**의 좁은 열이다. 옆 미니표 '지관수'가 아니다.

[완정공정(물류입고) 표] — 컬럼에 '가로'와 '길이'가 있다. 각 행을 {"garo":가로,"gil":길이}로 모두 출력하라.
  가로 칸이 비어 있는 행은 바로 위 행의 가로 값을 그대로 쓴다(같은 가로의 연속). 표가 페이지 아래에서 잘려도 보이는 만큼 읽어라.

다음 JSON만 출력한다(설명·코드블록 금지):
{
  "jong": "지종 문자열",
  "cp": 평량 앞 정수 (예 '55(43)' → 55),
  "lot": "계획 lot번호 문자열",
  "choji": 초지공정 길이 정수 (상단 '초지공정' 행 '길이', 콤마 제거),
  "teukgam": 특감(SFH/SL)이면 true 아니면 false,
  "orders": [
    {"sunseo": 정수, "jeong": 정수, "kwonchwi": 정수, "jipok": 정수, "gil": 정수,
     "wonji": 정수, "gyukyeok": [{"w": 정수, "n": 정수}, ...]}
  ],
  "wanjeong": [{"garo": 정수, "gil": 정수}, ...]
}

모든 숫자는 콤마 없이 정수. orders는 순서 오름차순.
출력은 **JSON 객체 정확히 한 개**다 — 같은 내용을 두 번 쓰거나, 표를 나눠 객체를 여러 개 내보내거나,
닫는 중괄호 뒤에 어떤 문자(설명·코드블록·추가 JSON)도 덧붙이지 마라."""


WANJEONG_PROMPT = """이 이미지들은 '완정공정(물류입고)' 표의 (앞 페이지에서 이어지는) 연속 부분이다.
표에서 '가로'와 '길이' 두 컬럼만 읽어라. 가로 칸이 비어 있으면 바로 위 행의 가로 값을 그대로 쓴다(같은 가로 연속).
헤더·다른 컬럼은 무시. 같은 (가로,길이) 쌍은 한 번만.
JSON만 출력(설명·코드블록 금지): {"wanjeong":[{"garo":정수,"gil":정수}, ...]}. 숫자는 콤마 없이 정수.
페이지가 여러 장이어도 **JSON 객체는 정확히 한 개**로 합쳐서 출력하고, 닫는 중괄호 뒤에 아무것도 붙이지 마라."""


SPEC_TRIM = 25   # 규격 칸 합계폭 ≈ 앞폭×배수 + 25 (LOT2260701966 역산: 485×3+25=1480 등)


def _respec(w, garos):
    """규격 칸의 '합계폭'을 (앞폭, 배수)로 역산한다.

    RE 재단표의 규격 칸은 2단이다 — 위=합계폭(예 1480), 아래 작은 박스=앞폭×배수(485x3).
    모델이 위 숫자만 읽고 배수를 놓치면 폭변경(170)·길이변경(240) 판정이 전멸한다
    (2026-09 LOT2260701966에서 순1·순2 보상이 170/240 → 120으로 떨어짐).
    완정공정 '가로' 목록을 사전처럼 써서 합계폭 = 가로×배수(+25)를 만족하는 조합을 찾는다.
    반환: (앞폭, 배수) / 후보가 여럿이면 (None, 배수) — 배수는 같으므로 폭변경 판정만 쓰고
    길이변경 판정에서는 제외한다 / 못 풀면 None."""
    hits = []
    for g in garos:
        if g <= 0:
            continue
        for pad in (SPEC_TRIM, 0):
            if (w - pad) > 0 and (w - pad) % g == 0:
                n = (w - pad) // g
                if n >= 2:
                    hits.append((g, n))
                break
    if not hits:
        return None
    ns = {n for _g, n in hits}
    if len(hits) == 1:
        return hits[0]
    return (None, min(ns))          # 앞폭 모호 → 배수(폭변경)만 신뢰


def _fix_gyukyeok(data, wanjeong, warns):
    """규격 배수 오독 자동 보정. '그 주문의 모든 규격 배수가 1'일 때만 동작한다
    (배수를 하나라도 읽었다면 모델이 아래 박스를 제대로 본 것이므로 손대지 않는다)."""
    garos = sorted({w["garo"] for w in wanjeong if w.get("garo")}, reverse=True)
    if not garos:
        return
    for o in data.get("orders", []) or []:
        gy = o.get("gyukyeok") or []
        if not gy or any((g.get("n") or 1) >= 2 for g in gy):
            continue
        if all(g.get("w") in garos for g in gy):     # 전부 실제 가로값 → 오독 아님
            continue
        fixed, changed = [], []
        for g in gy:
            r = _respec(int(g.get("w") or 0), garos)
            if r is None:
                fixed.append(g)
                continue
            gw, gn = r
            fixed.append({"w": gw, "n": gn})
            changed.append(f"{g.get('w')}→{gw or '?'}x{gn}")
        if changed:
            o["gyukyeok"] = fixed
            warns.append(f"순서{o.get('sunseo', '?')} 규격 배수 보정: "
                         f"{', '.join(changed)} (보상 재확인 권장)")


def _int(v, default=0):
    try:
        return int(round(float(str(v).replace(",", "").strip())))
    except (ValueError, TypeError):
        return default


def parse_schedule(pdf_bytes: bytes, api_key: str = None, model: str = DEFAULT_MODEL) -> dict:
    """PDF 바이트 -> dict. 권취·지폭 오독을 교차검증으로 자동교정,
    보상길이는 규격·완정공정으로 코드가 결정(calc.decide_bosang).
    무료등급 pro(쿼터0) 등 쿼터초과 시 flash로 자동 폴백."""
    from google import genai
    from google.genai import types
    from . import pdf_extract
    from . import calc

    key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        raise ValueError("Gemini API 키가 필요합니다 (사이드바 입력 또는 GEMINI_API_KEY 환경변수).")

    # 고품질 렌더(400 DPI + deskew + 표영역 크롭). 전처리 실패 시 원본 렌더로 폴백.
    try:
        imgs = pdf_extract.render_pages_hq(pdf_bytes, dpi=400)
    except Exception:
        imgs = pdf_extract.render_pages(pdf_bytes, dpi=300)

    def _png(img):
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return types.Part.from_bytes(data=buf.getvalue(), mime_type="image/png")

    client = genai.Client(api_key=key)
    # ── 1페이지: 헤더+RE재단표(주문)+완정공정 시작 ── (주문은 항상 첫 장에만 있음)
    #    2페이지 이상이어도 첫 장만 메인 프롬프트로 보내 혼동/JSON절삭을 막는다.
    text, used_model, fb_notes = _generate_with_fallback(
        client, types, model, [_png(imgs[0]), PROMPT])
    if not text:
        raise ValueError("Gemini 응답이 비었습니다.")
    json_notes = []
    data = _loads(text, json_notes)

    # ── 2페이지 이후: 완정공정 연속(가로/길이)만 별도 추출해 병합 ──
    page2_note = []
    if len(imgs) > 1:
        try:
            wtext, _, _ = _generate_with_fallback(
                client, types, used_model, [_png(im) for im in imgs[1:]] + [WANJEONG_PROMPT])
            extra = (_loads(wtext, json_notes) or {}).get("wanjeong", []) if wtext else []
            if extra:
                data["wanjeong"] = (data.get("wanjeong") or []) + extra
        except Exception as e:   # noqa: BLE001
            page2_note.append(f"2페이지 완정공정 추출 실패(보상 판정 영향 가능): {redact(e)}")

    # 특감 판정은 '지종'으로 결정형(모델 추측 무시). 특감 = SFH/SL 등급뿐.
    # 일반 감열지 SF/PF는 특감 아님 → 코타+트림 +75 (특감이면 +150).
    jong_u = re.sub(r"\s+", "", str(data.get("jong") or "")).upper()
    if jong_u:
        data["teukgam"] = ("SFH" in jong_u) or ("SL" in jong_u)
    else:
        data["teukgam"] = bool(data.get("teukgam", False))
    data["cp"] = _int(data.get("cp"), 0)
    data["choji"] = _int(data.get("choji"), 0)
    warns = list(fb_notes) + list(page2_note) + list(json_notes)
    if used_model != model:
        warns.append(f"{model} 사용불가 → {used_model}로 파싱함")
    if len(imgs) > 1:
        warns.append(f"{len(imgs)}페이지 문서 — 주문=1페이지, 완정공정=전 페이지 병합")
    # 완정공정 표 정규화 + 중복 제거((가로,길이) 유니크)
    wanjeong, seen = [], set()
    for w in data.get("wanjeong", []) or []:
        key = (_int(w.get("garo")), _int(w.get("gil")))
        if key[0] and key[1] and key not in seen:
            seen.add(key)
            wanjeong.append({"garo": key[0], "gil": key[1]})
    data["wanjeong"] = wanjeong

    for o in data.get("orders", []):
        o["jeong"] = _int(o.get("jeong"))
        o["jipok"] = _int(o.get("jipok"))
        o["kwonchwi"] = _int(o.get("kwonchwi"))
        o["gil"] = _int(o.get("gil"))
        o["wonji"] = _int(o.get("wonji"))
        gy = []
        for g in o.get("gyukyeok", []) or []:
            w_, n_ = _int(g.get("w")), _int(g.get("n"), 1)
            if w_:
                gy.append({"w": w_, "n": max(n_, 1)})
        o["gyukyeok"] = gy
        sn = o.get("sunseo", "?")
        # 권취 교정: 길이÷정길이로 오독 보정. 공식은 ±1 오차라, 50% 이상 벗어나면 교정.
        # (단 gil·jeong 둘 다 옳을 때만 신뢰 가능 — jeong까지 오독되면 못 잡음)
        if o["gil"] and o["jeong"]:
            est = round(o["gil"] / o["jeong"])
            k = o["kwonchwi"]
            off = est >= 1 and (k > 500 or k > est * 1.5 or k < est * 0.5)
            if off and k != est:
                warns.append(f"순서{sn} 권취 {k}→{est}(길이÷정길이)")
                o["kwonchwi"] = est
        # 지폭 교정: 원지폭과 비슷/크면(혼동) jipok = wonji − 80
        if o["wonji"] and o["jipok"] and o["jipok"] >= o["wonji"] - 20:
            warns.append(f"순서{sn} 지폭 {o['jipok']}→{o['wonji'] - WONJI_GAP}")
            o["jipok"] = o["wonji"] - WONJI_GAP
        # 지폭 한 자리 오독 의심 플래그(자동교정 X, 검수 유도): 지폭 ↔ 원지폭−80 불일치
        elif o["wonji"] and o["jipok"] and 0 < abs(o["jipok"] - (o["wonji"] - WONJI_GAP)) <= 30:
            warns.append(f"순서{sn} 지폭 확인 필요: 지폭 {o['jipok']} ↔ 원지폭−80={o['wonji'] - WONJI_GAP}")
        # 필수값 누락 플래그
        miss = [n for n, v in (("정길이", o["jeong"]), ("권취", o["kwonchwi"]), ("지폭", o["jipok"])) if not v]
        if miss:
            warns.append(f"순서{sn} 판독 누락: {', '.join(miss)} — 검수 필요")
    # 규격 배수 오독(합계폭만 읽고 xN을 놓침) 자동 보정 — 반드시 보상 판정 전(2026-09)
    _fix_gyukyeok(data, wanjeong, warns)
    for o in data.get("orders", []):
        # 보상길이 = 규칙형 코드 결정(규격 배수 + 완정공정 가로/길이 매칭)
        o["bosang"] = calc.decide_bosang(o["jeong"], o["gyukyeok"], wanjeong,
                                         data["cp"], data["teukgam"])
    # 지폭 내림차순 위반 경고
    jipoks = [o["jipok"] for o in data.get("orders", []) if o.get("jipok")]
    if any(jipoks[i] < jipoks[i + 1] for i in range(len(jipoks) - 1)):
        warns.append("지폭이 내림차순이 아님 — 확인 필요")
    data["_warnings"] = warns
    return data
