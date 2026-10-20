# -*- coding: utf-8 -*-
"""_generate_with_fallback: pro(쿼터0) → flash 자동 폴백 검증 (실제 API 호출 없음)."""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, ".")
from jipok import gemini_parse as G

PRO_429 = ("429 RESOURCE_EXHAUSTED ... limit: 0, model: gemini-2.5-pro ... "
           "Please retry in 19.8s")
FLASH_429 = "429 RESOURCE_EXHAUSTED per-minute ... Please retry in 2s, model: gemini-2.5-flash"


class FakeModels:
    def __init__(self, behavior):
        self.behavior = behavior      # model -> 'ok' | Exception
        self.calls = []

    def generate_content(self, model, contents, config):
        self.calls.append(model)
        b = self.behavior.get(model)
        if isinstance(b, Exception):
            raise b
        class R: text = '{"ok": true}'
        return R()


class FakeClient:
    def __init__(self, behavior):
        self.models = FakeModels(behavior)


class FakeTypes:
    @staticmethod
    def GenerateContentConfig(**k):
        return None


def run(name, requested, behavior, expect_used, expect_calls_contains):
    client = FakeClient(behavior)
    try:
        text, used, notes = G._generate_with_fallback(client, FakeTypes, requested, ["img", "PROMPT"])
        ok = (used == expect_used) and all(c in client.models.calls for c in expect_calls_contains)
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: used={used} calls={client.models.calls} notes={notes}")
    except Exception as e:
        print(f"[RAISE] {name}: {type(e).__name__}: {str(e)[:60]}")


# 1) 무료등급: pro 요청 → pro 429(쿼터0) → flash 성공
run("pro→flash 폴백", "gemini-2.5-pro",
    {"gemini-2.5-pro": RuntimeError(PRO_429), "gemini-2.5-flash": "ok"},
    "gemini-2.5-flash", ["gemini-2.5-pro", "gemini-2.5-flash"])

# 2) flash 요청 → 바로 성공
run("flash 즉시성공", "gemini-2.5-flash",
    {"gemini-2.5-flash": "ok"}, "gemini-2.5-flash", ["gemini-2.5-flash"])

# 3) flash 분당한도(2s) → 대기 후 재시도... 여기선 flash 계속 실패, flash-lite 성공
run("flash→flash-lite", "gemini-2.5-flash",
    {"gemini-2.5-flash": RuntimeError(FLASH_429), "gemini-2.5-flash-lite": "ok"},
    "gemini-2.5-flash-lite", ["gemini-2.5-flash", "gemini-2.5-flash-lite"])

# 4) 쿼터 외 에러는 즉시 표면화(폴백 안 함)
run("비쿼터 에러 즉시raise", "gemini-2.5-flash",
    {"gemini-2.5-flash": ValueError("bad request 400")}, None, [])

# 5) 전부 쿼터초과 → 친절한 RuntimeError
run("전부실패", "gemini-2.5-pro",
    {"gemini-2.5-pro": RuntimeError(PRO_429),
     "gemini-2.5-flash": RuntimeError(FLASH_429),
     "gemini-2.5-flash-lite": RuntimeError(FLASH_429)}, None, [])

print("\n_retry_delay 추출:", G._retry_delay(PRO_429), G._retry_delay(FLASH_429))
print("MODELS 기본(첫번째):", G.MODELS[0])
