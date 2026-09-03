"""
تحديث السكور المباشر - سكربت خفيف ومستقل
====================================================
يجيب بس حالة ونتيجة المباريات الجارية حالياً (In Play / Halftime) باليوم
عبر الدوريات الستة المتابعة، بدون أي شي ثاني (بدون مقالات Gemini، بدون
صور، بدون أرشيف)، عشان يشتغل بسرعة ويقدر ينتشر كل دقيقة أو دقيقتين عبر
cron-job.org، ويعطي إحساس "لايف" حقيقي بدون ما نلمس السكربت الرئيسي
الثقيل اللي يشتغل كل نص ساعة.

المتطلبات:
    pip install requests
"""

import json
import os
import time
import requests
from datetime import datetime

THESPORTSDB_API_KEY = os.environ.get("THESPORTSDB_API_KEY", "3")
THESPORTSDB_BASE = f"https://www.thesportsdb.com/api/v1/json/{THESPORTSDB_API_KEY}"

TARGET_LEAGUES = [4668, 4328, 4335, 4332, 4331, 4334]

STATUS_MAP = {
    "1H": "In Play",
    "2H": "In Play",
    "ET": "In Play",
    "HT": "Halftime",
}


def fetch_league_day_events(league_id: int, date: str) -> list:
    url = f"{THESPORTSDB_BASE}/eventsday.php"
    response = requests.get(url, params={"d": date, "l": league_id}, timeout=15)
    response.raise_for_status()
    return response.json().get("events") or []


def fetch_live_scores() -> list:
    today = datetime.now().strftime("%Y-%m-%d")
    live = []

    for league_id in TARGET_LEAGUES:
        try:
            events = fetch_league_day_events(league_id, today)
        except Exception as e:
            print(f"تعذّر جلب مباريات دوري {league_id} ({e}).")
            continue

        for ev in events:
            status = STATUS_MAP.get(ev.get("strStatus"))
            if not status:
                continue

            home_score = ev.get("intHomeScore")
            away_score = ev.get("intAwayScore")
            live.append({
                "event_id": ev["idEvent"],
                "home_score": int(home_score) if home_score not in (None, "") else None,
                "away_score": int(away_score) if away_score not in (None, "") else None,
                "status": status,
            })

        time.sleep(0.1)

    return live


if __name__ == "__main__":
    live_matches = fetch_live_scores()
    with open("live_scores.json", "w", encoding="utf-8") as f:
        json.dump(live_matches, f, ensure_ascii=False, indent=2)
    print(f"تم حفظ {len(live_matches)} مباراة جارية حالياً.")
