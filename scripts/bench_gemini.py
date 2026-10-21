"""用真實 Gemini 比較模型／thinking 等級的解析準確度與延遲（只呼叫 NLP，不碰日曆與 Firestore）。

用法（repo 根目錄）：
    GEMINI_API_KEY=$(gcloud secrets versions access latest --secret=GEMINI_API_KEY) \\
        uv run python scripts/bench_gemini.py [--reps 3] [--models gemini-3.5-flash-lite gemini-3.8-flash:LOW]

模型後面加 `:LOW`／`:MEDIUM`／`:HIGH` 指定 thinking 等級（3.8-flash 不支援 MINIMAL）。
2026-10-21 的結果：3.8-flash 50/54、p50 6.1 s；3.8-flash:LOW 51/54、3.4 s；3.5-flash-lite 51/54、1.4 s。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import sys
import time
from datetime import date, timedelta
from pathlib import Path

for _k, _v in {"LINE_CHANNEL_SECRET": "x", "LINE_CHANNEL_ACCESS_TOKEN": "x",
               "GOOGLE_CALENDAR_ID": "bench", "GCP_PROJECT_ID": "bench"}.items():
    os.environ.setdefault(_k, _v)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from google.genai import types

from app.config import settings
from app.models.user import ConversationMessage
from app.services import nlp
from app.utils.datetime_utils import now_local

NOW = now_local()
TODAY = NOW.date()
TOMORROW = TODAY + timedelta(days=1)
_NEXT_MON = TODAY + timedelta(days=7 - TODAY.weekday())
WED_AFTER_NEXT = _NEXT_MON + timedelta(days=7 + 2)  # 下下週三


def _act(i):
    return getattr(i.action, "value", i.action)


def targets(act: str, n: int, any_all: bool = False, kw: str | None = None):
    def f(i):
        ts = i.targets or []
        n_eff = len(ts) or (1 if i.search_keyword else 0)  # 單筆可能只填頂層 search_keyword
        ok = _act(i) == act and n_eff == n and (any(t.match_all for t in ts) if any_all else True)
        if ok and kw:
            ok = any(k and kw in k for k in [t.search_keyword for t in ts] + [i.search_keyword])
        return ok
    return f


def create_at(day: date, hour: int, reminder: int | None = None):
    def f(i):
        d = i.event_details or (i.events or [None])[0]
        if _act(i) != "create" or not d or not d.start_time:
            return False
        ok = d.start_time.date() == day and d.start_time.hour == hour
        return ok and (reminder is None or d.reminder_minutes == reminder)
    return f


def create_n(n: int):
    return lambda i: _act(i) == "create" and len(i.events or []) == n


def action(act: str):
    return lambda i: _act(i) == act


def clarify(i):
    return i.confidence < 0.5 or _act(i) == "unknown"


_DENTIST = [
    ConversationMessage(role="user", content="明天下午兩點看牙醫", timestamp=NOW),
    ConversationMessage(role="assistant", content="已新增：📌 牙醫 🕐 明天 14:00–15:00", timestamp=NOW),
]

# (訊息, 檢查, 對話歷史)
INTENT_CASES = [
    ("取消明天的牙醫和週五的晚餐", targets("delete", 2), None),
    ("牙醫改到下午三點，週五晚餐改到七點", targets("update", 2), None),
    ("把這週的會議都延後一小時", targets("update", 1, any_all=True), None),
    ("下週所有的瑜珈課都取消", targets("delete", 1, any_all=True), None),
    ("取消明天的牙醫", targets("delete", 1), None),
    ("明天的讀書會改到晚上八點，順便把後天的健身也延後半小時", targets("update", 2), None),
    ("明天下午三點到五點開會", create_at(TOMORROW, 15), None),
    ("明天早上十點看牙醫，提前 30 分鐘提醒我", create_at(TOMORROW, 10, 30), None),
    ("這週有什麼安排", action("query"), None),
    ("明天幾點有空", action("query"), None),
    ("下下週三晚上七點跟大學同學吃飯", create_at(WED_AFTER_NEXT, 19), None),
    ("明天早上九點晨跑、中午十二點午餐、晚上八點看電影", create_n(3), None),
    ("幫我弄一下", clarify, None),
    ("那個改一下", clarify, None),
    ("把它改到四點", targets("update", 1, kw="牙"), _DENTIST),
]

# 原行程刻意不在今天，才分得出相對日期是從今天還是從原行程起算
_EV_DAY = TODAY + timedelta(days=14)
EVENT = {"id": "bench", "summary": "牙醫",
         "start": {"dateTime": f"{_EV_DAY}T10:00:00+08:00"},
         "end": {"dateTime": f"{_EV_DAY}T11:00:00+08:00"}}
# (指令, 預期日期, 預期 (時, 分))
UPDATE_CASES = [
    ("牙醫延後 30 分鐘", _EV_DAY, (10, 30)),
    ("牙醫提早一個半小時", _EV_DAY, (8, 30)),
    ("牙醫改到明天", TOMORROW, (10, 0)),
    ("牙醫改到後天同一時間", TODAY + timedelta(days=2), (10, 0)),
]

_orig_config = nlp._config


def _use(model: str, level: str | None) -> None:
    settings.gemini_model = model
    if not level:
        nlp._config = _orig_config
        return

    def cfg(prompt, schema):
        c = _orig_config(prompt, schema)
        c.thinking_config = types.ThinkingConfig(thinking_level=level)
        return c

    nlp._config = cfg


async def bench(spec: str, reps: int) -> None:
    model, _, level = spec.partition(":")
    _use(model, level or None)
    lat: list[float] = []
    ok = errs = 0
    wrong: set[str] = set()

    async def timed(coro, label):
        nonlocal errs
        t = time.monotonic()
        try:
            return await coro
        except Exception as e:
            errs += 1
            wrong.add(f"{label}（{type(e).__name__}）")
            return None
        finally:
            lat.append(time.monotonic() - t)

    for r in range(reps):
        for i, (msg, chk, hist) in enumerate(INTENT_CASES):
            intent = await timed(nlp.parse_intent(msg, hist, user_id=f"bench-{r}-{i}"), msg)
            good = intent is not None and chk(intent)
            ok += good
            if intent is not None and not good:
                wrong.add(msg)
        for msg, day, hm in UPDATE_CASES:
            d = await timed(nlp.parse_update_details(msg, EVENT, user_id=f"bench-u-{r}"), msg)
            st = d.start_time if d else None
            good = bool(st and st.date() == day and (st.hour, st.minute) == hm)
            ok += good
            if d is not None and not good:
                wrong.add(f"[update] {msg} → {st}（預期 {day} {hm[0]:02d}:{hm[1]:02d}）")

    n = reps * (len(INTENT_CASES) + len(UPDATE_CASES))
    lat.sort()
    p90 = lat[int(0.9 * (len(lat) - 1))]
    print(f"{spec:28s} 準確 {ok}/{n}  p50 {statistics.median(lat):4.1f}s  p90 {p90:4.1f}s  "
          f"max {lat[-1]:4.1f}s  err {errs}", flush=True)
    for w in sorted(wrong):
        print(f"    ✗ {w}", flush=True)


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--models", nargs="+", default=[settings.gemini_model])
    args = ap.parse_args()
    print(f"今天 {TODAY}；每個設定 {args.reps} 輪 × {len(INTENT_CASES) + len(UPDATE_CASES)} 題")
    for spec in args.models:
        await bench(spec, args.reps)


if __name__ == "__main__":
    asyncio.run(main())
