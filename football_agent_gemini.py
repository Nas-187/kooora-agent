"""
إيجنت كورة القدم - نتائج + ترتيب + أخبار، جاهزة للنشر على الموقع
====================================================
مصادر البيانات: TheSportsDB (بيانات المباريات - مجاني، يدعم الموسم
الحالي كامل بدون قيد) + Google Gemini (المقالات) + Pexels (صور توضيحية)

تحوّلنا من API-Football لـ TheSportsDB لأن API-Football (أ) يرفض أي
طلب فيه season=2026 على الخطة المجانية، و(ب) الحساب صار معلّق فجأة.
TheSportsDB يدعم الموسم الحالي وحتى الأرشيف التاريخي كامل بمفتاحه
التجريبي المجاني العام "3" بدون أي قيد من هذا النوع.

المتطلبات:
    pip install requests google-genai

المفاتيح (كمتغيرات بيئة أو GitHub Secrets):
    GEMINI_API_KEY       -> مفتاح من https://aistudio.google.com/
    PEXELS_API_KEY       -> اختياري، من https://www.pexels.com/api/
    THESPORTSDB_API_KEY  -> اختياري، الافتراضي "3" (مفتاح تجريبي عام مجاني)
"""

import re
import requests
import json
import os
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from google import genai

# ============ الإعدادات ============
THESPORTSDB_API_KEY = os.environ.get("THESPORTSDB_API_KEY", "3")
THESPORTSDB_BASE = f"https://www.thesportsdb.com/api/v1/json/{THESPORTSDB_API_KEY}"
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY")

# دوري روشن السعودي - يُستخدم لجدول الترتيب تحديداً (معرّف TheSportsDB)
STANDINGS_LEAGUE_ID = 4668

# أول تاريخ نبدأ منه أرشفة نتائج الموسم الحالي - يغطي بداية الدوريات الستة
SEASON_START_DATE = "2026-08-01"

# أرشيف نتائج محلي تراكمي، يتزامن مع TheSportsDB (يدعم الموسم الحالي
# والتاريخ كامل - أول تشغيل يعبّي الموسم من بدايته، وبعدها يتحقق من
# الأيام الجديدة بس عشان يوفر عدد الطلبات)
RESULTS_ARCHIVE_FILE = "results_archive.json"
SYNC_STATE_FILE = "sync_state.json"

# الدوريات اللي نتابعها (معرّفات TheSportsDB)
TARGET_LEAGUES = {
    4668: "دوري روشن السعودي",
    4328: "الدوري الإنجليزي",
    4335: "الدوري الإسباني",
    4332: "الدوري الإيطالي",
    4331: "الدوري الألماني",
    4334: "الدوري الفرنسي",
}

if not GEMINI_API_KEY:
    raise SystemExit(
        "لازم تحط مفتاح Gemini كمتغير بيئة أول:\n"
        "  export GEMINI_API_KEY=مفتاحك\n"
    )

GEMINI_MODEL = "gemini-3.6-flash"
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

# تحويل رموز حالة المباراة من TheSportsDB لنفس الصيغة اللي يفهمها الموقع
STATUS_MAP = {
    "NS": "Not Started",
    "FT": "Match Finished",
    "AET": "Match Finished",
    "PEN": "Match Finished",
    "1H": "In Play",
    "2H": "In Play",
    "ET": "In Play",
    "HT": "Halftime",
}


# ============================================================
# 1) المباريات
# ============================================================

def fetch_league_day_events(league_id: int, date: str) -> list:
    """يجيب مباريات دوري معيّن بتاريخ محدد من TheSportsDB."""
    url = f"{THESPORTSDB_BASE}/eventsday.php"
    response = requests.get(url, params={"d": date, "l": league_id}, timeout=20)
    response.raise_for_status()
    return response.json().get("events") or []


def format_event_summary(event: dict, league_id: int) -> dict:
    """يستخرج المعلومات المهمة من بيانات مباراة خام من TheSportsDB."""
    status = STATUS_MAP.get(event.get("strStatus"), event.get("strStatus") or "Not Started")
    home_score = event.get("intHomeScore")
    away_score = event.get("intAwayScore")

    date_str = event.get("strTimestamp") or f"{event['dateEvent']}T{event.get('strTime', '00:00:00')}"
    if "+" not in date_str and not date_str.endswith("Z"):
        date_str += "Z"

    return {
        "event_id": event["idEvent"],
        "league_id": league_id,
        "league_logo": event.get("strLeagueBadge"),
        "round": event.get("intRound"),
        "home_team": event["strHomeTeam"],
        "away_team": event["strAwayTeam"],
        "home_id": event.get("idHomeTeam"),
        "away_id": event.get("idAwayTeam"),
        "home_logo": event.get("strHomeTeamBadge"),
        "away_logo": event.get("strAwayTeamBadge"),
        "home_score": int(home_score) if home_score not in (None, "") else None,
        "away_score": int(away_score) if away_score not in (None, "") else None,
        "status": status,
        "league": TARGET_LEAGUES.get(league_id, event.get("strLeague")),
        "date": date_str,
        "day": event["dateEvent"],
    }


# نطاق الأيام اللي نجيب مبارياتها لشريط "مباريات اليوم" (يومين للخلف
# وثلاثة أيام للقدام حول اليوم الحالي)، عشان المستخدم يقدر يتصفح
# بالتاريخ زي المواقع الرياضية المعروفة
DAYS_BEFORE = 2
DAYS_AFTER = 3


def get_matches_window() -> list:
    """يجيب مباريات نافذة الأيام (أمس/اليوم/غداً وأبعد) لكل الدوريات الستة."""
    today = datetime.now().date()
    days = [
        (today + timedelta(days=offset)).strftime("%Y-%m-%d")
        for offset in range(-DAYS_BEFORE, DAYS_AFTER + 1)
    ]

    results = []
    for day in days:
        for league_id in TARGET_LEAGUES:
            try:
                events = fetch_league_day_events(league_id, day)
            except Exception as e:
                print(f"تعذّر جلب مباريات {day} لدوري {league_id} ({e}).")
                continue
            results.extend(format_event_summary(ev, league_id) for ev in events)
            time.sleep(0.1)
    return results


def fallback_article_text(match_summary: dict) -> str:
    """
    نص بديل بسيط بدون ذكاء اصطناعي - يُستخدم لو Gemini فشل (تجاوز الحصة
    المجانية مثلاً) أو للمباريات خارج الدوريات المستهدفة، عشان المباراة
    تظهر بالموقع دايماً حتى لو بدون مقال مولّد بالذكاء الاصطناعي.
    """
    home, away = match_summary["home_team"], match_summary["away_team"]
    home_score, away_score = match_summary["home_score"], match_summary["away_score"]

    if home_score is None or away_score is None:
        return f"مباراة {home} و{away} ضمن {match_summary['league']}."

    if home_score > away_score:
        result = f"فوز {home} على {away}"
    elif away_score > home_score:
        result = f"فوز {away} على {home}"
    else:
        result = f"تعادل {home} و{away}"

    return f"{result} بنتيجة {home_score}-{away_score} ضمن {match_summary['league']}."


def generate_article(match_summary: dict) -> str:
    """يستخدم Gemini عشان يصيغ خبر طبيعي عن نتيجة المباراة"""
    prompt = f"""اكتب خبر رياضي قصير (3-4 جمل) بالعربية الفصحى الإخبارية
عن هذه المباراة، بأسلوب طبيعي يناسب موقع أخبار رياضي:

الفريق المضيف: {match_summary['home_team']}
الفريق الضيف: {match_summary['away_team']}
النتيجة: {match_summary['home_score']} - {match_summary['away_score']}
الحالة: {match_summary['status']}
البطولة: {match_summary['league']}

اكتب الخبر مباشرة بدون مقدمات أو عناوين."""

    try:
        response = gemini_client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        return response.text.strip()
    except Exception as e:
        print(f"تعذّر توليد المقال عبر Gemini ({e}) - استخدام نص بديل تلقائي.")
        return fallback_article_text(match_summary)


def fetch_cover_image(query: str):
    """
    يجيب صورة ستوك زخرفية من Pexels (مجانية الترخيص) لتحسين شكل بطاقة
    الخبر. هذي صورة توضيحية عامة (ملعب/أجواء رياضية) مو صورة حقيقية من
    المباراة نفسها - الواجهة تعرض وسم "صورة توضيحية" فوقها عشان توضيح
    كذا للقارئ. ترجع None لو ما فيه مفتاح API أو ما لقى نتيجة.
    """
    if not PEXELS_API_KEY:
        return None

    url = "https://api.pexels.com/v1/search"
    headers = {"Authorization": PEXELS_API_KEY}
    params = {"query": query, "per_page": 1, "orientation": "landscape"}

    try:
        response = requests.get(url, headers=headers, params=params, timeout=15)
        response.raise_for_status()
        photos = response.json().get("photos", [])
        if not photos:
            return None
        return photos[0]["src"]["large2x"]
    except Exception as e:
        print(f"تعذّر جلب صورة من Pexels ({e}).")
        return None


COVER_IMAGES_FILE = "cover_images.json"
_cover_images_cache = None


def get_cached_cover_image(league_name: str):
    """
    نجيب صورة توضيحية وحدة لكل دوري ونخزّنها بشكل دائم (بدل استدعاء
    Pexels لكل مباراة على حدة) - عدد المباريات صار كبير بعد ما وسّعنا
    نافذة الأيام، وهذا يحافظ على حصة Pexels المجانية.
    """
    global _cover_images_cache
    if _cover_images_cache is None:
        _cover_images_cache = load_json_file(COVER_IMAGES_FILE, {})

    if league_name not in _cover_images_cache:
        _cover_images_cache[league_name] = fetch_cover_image(f"{league_name} football stadium")
        save_json_file(COVER_IMAGES_FILE, _cover_images_cache)

    return _cover_images_cache[league_name]


def build_match_payload(match_summary: dict, article_text: str) -> dict:
    """
    يجهز بيانات المباراة بنفس التنسيق اللي يقرأه index.html مباشرة
    (home_team, away_team, home_logo, away_logo, home_score, away_score,
    status, league, content)
    """
    if match_summary["home_score"] is None or match_summary["away_score"] is None:
        title = f"{match_summary['home_team']} ضد {match_summary['away_team']}"
    else:
        title = f"{match_summary['home_team']} {match_summary['home_score']} - " \
                f"{match_summary['away_score']} {match_summary['away_team']}"

    return {
        "event_id": match_summary["event_id"],
        "title": title,
        "league_id": match_summary["league_id"],
        "league_logo": match_summary["league_logo"],
        "round": match_summary.get("round"),
        "home_team": match_summary["home_team"],
        "away_team": match_summary["away_team"],
        "home_logo": match_summary["home_logo"],
        "away_logo": match_summary["away_logo"],
        "home_score": match_summary["home_score"],
        "away_score": match_summary["away_score"],
        "status": match_summary["status"],
        "league": match_summary["league"],
        "date": match_summary["date"],
        "day": match_summary["day"],
        "content": article_text,
        "cover_image": get_cached_cover_image(match_summary["league"]),
    }


def run_matches_pipeline():
    """يشغّل دورة المباريات كاملة: جلب -> صياغة -> تجهيز"""
    print("جاري جلب مباريات نافذة الأيام (أمس/اليوم/غداً وأبعد)...")
    fixtures = get_matches_window()
    today = datetime.now().strftime("%Y-%m-%d")

    if not fixtures:
        print("ما فيه مباريات بالدوريات المتابعة بهالنافذة الزمنية.")
        return []

    results = []
    for summary in fixtures:
        # نتجاهل بس الحالات الملغاة/المؤجلة - نعرض المنتهية والجارية
        # والمجدولة (عشان شريط تصفح الأيام يكون كامل)
        if summary["status"] not in ("Match Finished", "Halftime", "In Play", "Not Started"):
            continue

        print(f"معالجة: {summary['home_team']} vs {summary['away_team']} ({summary['day']}, {summary['status']})")

        if summary["status"] == "Not Started":
            # ما بدأت بعد - ما فيه نتيجة نبني منها مقال، نكتفي بموعدها
            article = ""
        elif summary["day"] == today:
            # نستخدم Gemini بس لمباريات اليوم عشان نحافظ على الحصة
            # المجانية - باقي الأيام (أمس وقبلها) تاخذ نص واقعي بديل
            article = generate_article(summary)
        else:
            article = fallback_article_text(summary)

        results.append(build_match_payload(summary, article))

    return results


# ============================================================
# 2) جدول الترتيب
# ============================================================

def load_json_file(path: str, default):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json_file(path: str, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_results_archive() -> list:
    return load_json_file(RESULTS_ARCHIVE_FILE, [])


def save_results_archive(archive: list):
    save_json_file(RESULTS_ARCHIVE_FILE, archive)


def daterange(start_str: str, end_str: str):
    d = datetime.strptime(start_str, "%Y-%m-%d")
    end = datetime.strptime(end_str, "%Y-%m-%d")
    while d <= end:
        yield d.strftime("%Y-%m-%d")
        d += timedelta(days=1)


def update_results_archive() -> list:
    """
    يزامن الأرشيف المحلي مع TheSportsDB. أول تشغيل يمسح الموسم كامل من
    SEASON_START_DATE (TheSportsDB يدعم الموسم الحالي والتاريخ كامل بدون
    قيد)، وبعدها كل تشغيل يتحقق بس من الأيام الجديدة اللي ما مسحناها بعد
    (نتتبعها بـ sync_state.json) عشان نوفر عدد الطلبات.
    """
    archive = load_results_archive()
    state = load_json_file(SYNC_STATE_FILE, {})
    existing_ids = {m["event_id"] for m in archive}
    today = datetime.now().strftime("%Y-%m-%d")

    added = 0
    for league_id in TARGET_LEAGUES:
        date_from = state.get(str(league_id), SEASON_START_DATE)
        for day in daterange(date_from, today):
            try:
                events = fetch_league_day_events(league_id, day)
            except Exception as e:
                print(f"تعذّر جلب مباريات {day} لدوري {league_id} ({e}).")
                continue

            for ev in events:
                if ev.get("strStatus") != "FT":
                    continue
                if ev["idEvent"] in existing_ids:
                    continue

                summary = format_event_summary(ev, league_id)
                if summary["home_score"] is None or summary["away_score"] is None:
                    continue

                archive.append({
                    "event_id": summary["event_id"],
                    "league_id": league_id,
                    "league_name": TARGET_LEAGUES[league_id],
                    "date": day,
                    "home_id": summary["home_id"],
                    "home": summary["home_team"],
                    "home_logo": summary["home_logo"],
                    "away_id": summary["away_id"],
                    "away": summary["away_team"],
                    "away_logo": summary["away_logo"],
                    "home_goals": summary["home_score"],
                    "away_goals": summary["away_score"],
                })
                existing_ids.add(summary["event_id"])
                added += 1

            time.sleep(0.12)

        state[str(league_id)] = today

    save_json_file(SYNC_STATE_FILE, state)
    if added:
        save_results_archive(archive)
        print(f"أُضيفت {added} نتيجة جديدة للأرشيف المحلي (الإجمالي: {len(archive)}).")
    else:
        print("ما فيه نتائج جديدة.")

    return archive


POSITION_AR = {
    "Goalkeeper": "حارس مرمى",
    "Defender": "مدافع",
    "Midfielder": "وسط",
    "Attacker": "مهاجم",
}

PLAYERS_BIO_FILE = "players_bio.json"
# نحدّ عدد اللاعبين الجدد اللي نجيب تفاصيلهم/نولّد قصتهم كل تشغيل، عشان
# ما نستهلك حصة TheSportsDB وGemini المجانية دفعة وحدة على آلاف اللاعبين -
# القصة تُبنى تدريجياً وتُخزّن بشكل دائم لأنها ما تتغير غالباً
MAX_NEW_PLAYER_BIOS_PER_RUN = 12


def fetch_player_detail(player_id: str):
    """يجيب التفاصيل الكاملة للاعب وحيد (جنسية، طول، وزن، تاريخ توقيعه...) من TheSportsDB."""
    url = f"{THESPORTSDB_BASE}/lookupplayer.php"
    response = requests.get(url, params={"id": player_id}, timeout=20)
    response.raise_for_status()
    players = response.json().get("players") or []
    return players[0] if players else None


def build_player_facts(detail: dict) -> dict:
    """يستخرج الحقائق الرقمية فقط (بدون أي نص سيرة ذاتية منسوخ) من بيانات TheSportsDB."""
    age = None
    if detail.get("dateBorn"):
        try:
            born = datetime.strptime(detail["dateBorn"], "%Y-%m-%d")
            age = (datetime.now() - born).days // 365
        except ValueError:
            pass

    transfermarkt_id = detail.get("idTransferMkt")
    return {
        "nationality": detail.get("strNationality"),
        "birth_place": detail.get("strBirthLocation"),
        "birth_date": detail.get("dateBorn"),
        "age": age,
        "height": detail.get("strHeight"),
        "weight": detail.get("strWeight"),
        "position": detail.get("strPosition"),
        "team": detail.get("strTeam"),
        "joined": detail.get("dateSigned"),
        "transfermarkt_url": (
            f"https://www.transfermarkt.com/-/profil/spieler/{transfermarkt_id}"
            if transfermarkt_id else None
        ),
    }


def fallback_player_bio(name: str, facts: dict) -> str:
    """جملة وصفية بسيطة بدون ذكاء اصطناعي - تُستخدم لو Gemini فشل أو تجاوزنا الحصة."""
    parts = [name]
    if facts.get("position"):
        parts.append(f"يلعب في مركز {POSITION_AR.get(facts['position'], facts['position'])}")
    if facts.get("team"):
        parts.append(f"مع {facts['team']}")
    if facts.get("nationality"):
        parts.append(f"وهو لاعب {facts['nationality']}")
    if facts.get("age"):
        parts.append(f"يبلغ من العمر {facts['age']} عاماً")
    return " ".join(parts) + "."


def generate_player_bio(name: str, facts: dict) -> str:
    """
    يستخدم Gemini عشان يصيغ فقرة قصيرة أصلية عن اللاعب، معتمدة حصراً على
    الحقائق الرقمية المُعطاة له (بدون نسخ أي نص سيرة ذاتية جاهز من مصدر
    خارجي، وبدون اختلاق أي معلومة غير موجودة بالحقائق).
    """
    facts_lines = "\n".join(f"- {k}: {v}" for k, v in facts.items() if v)
    prompt = f"""اكتب فقرة قصيرة (2-3 جمل) بالعربية الفصحى تعرّف بهذا اللاعب،
معتمداً حصراً على الحقائق التالية بدون إضافة أي معلومة غير مذكورة هنا
وبدون تخمين أي رقم أو حدث غير موجود:

الاسم: {name}
{facts_lines}

اكتب الفقرة مباشرة بدون مقدمات أو عناوين."""

    try:
        response = gemini_client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        return response.text.strip()
    except Exception as e:
        print(f"تعذّر توليد قصة اللاعب {name} عبر Gemini ({e}) - استخدام نص بديل تلقائي.")
        return fallback_player_bio(name, facts)


def update_players_bio_cache(squads: dict) -> dict:
    """
    يجيب تفاصيل وقصة كل لاعب جديد ظهر بأي تشكيلة وما عندنا بياناته مخزّنة
    بعد، بحد أقصى MAX_NEW_PLAYER_BIOS_PER_RUN لكل تشغيل عشان نحافظ على
    حصص TheSportsDB وGemini المجانية - القصص تتراكم تدريجياً وتُخزّن بشكل
    دائم لأنها ما تتغير كل يوم.
    """
    bios = load_json_file(PLAYERS_BIO_FILE, {})
    all_player_ids = [p["id"] for players in squads.values() for p in players if p.get("id")]
    new_ids = [pid for pid in all_player_ids if str(pid) not in bios][:MAX_NEW_PLAYER_BIOS_PER_RUN]

    for player_id in new_ids:
        print(f"جاري جلب تفاصيل وبناء قصة اللاعب رقم {player_id}...")
        try:
            detail = fetch_player_detail(player_id)
            if not detail:
                continue
            facts = build_player_facts(detail)
            bio_text = generate_player_bio(detail.get("strPlayer", ""), facts)
            bios[str(player_id)] = {"facts": facts, "bio": bio_text}
        except Exception as e:
            print(f"تعذّر بناء قصة اللاعب {player_id} ({e}).")
        time.sleep(0.15)

    if new_ids:
        save_json_file(PLAYERS_BIO_FILE, bios)
        print(f"أُضيفت قصص {len(new_ids)} لاعب جديد (الإجمالي: {len(bios)}).")

    return bios


SQUADS_FILE = "squads.json"


def load_squads() -> dict:
    """يقرأ تشكيلات الأندية المخزّنة محلياً (لو موجودة)."""
    return load_json_file(SQUADS_FILE, {})


def save_squads(squads: dict):
    save_json_file(SQUADS_FILE, squads)


def fetch_team_squad(team_id: str):
    """يجيب تشكيلة نادي واحد من TheSportsDB (لاعبين حقيقيين: اسم، صورة، مركز، رقم)."""
    url = f"{THESPORTSDB_BASE}/lookup_all_players.php"
    response = requests.get(url, params={"id": team_id}, timeout=20)
    response.raise_for_status()
    players = response.json().get("player") or []

    squad = []
    for p in players:
        age = None
        if p.get("dateBorn"):
            try:
                born = datetime.strptime(p["dateBorn"], "%Y-%m-%d")
                age = (datetime.now() - born).days // 365
            except ValueError:
                pass

        squad.append({
            "id": p.get("idPlayer"),
            "name": p.get("strPlayer"),
            "age": age,
            "number": p.get("strNumber"),
            "position": p.get("strPosition"),
            "photo": p.get("strCutout") or p.get("strThumb"),
        })
    return squad


def update_squads_cache(archive: list) -> dict:
    """
    يجيب تشكيلة أي نادي ظهر بالأرشيف وما عندنا تشكيلته مخزّنة بعد.
    نخزّن كل تشكيلة مرة وحدة بس (ما تتغير كل يوم) عشان نوفر حصة الـ API -
    فقط الأندية الجديدة تُضاف تدريجياً كل ما تلعب مباراتها الأولى بأرشيفنا.
    """
    squads = load_squads()
    team_ids = {m["home_id"] for m in archive} | {m["away_id"] for m in archive}
    new_ids = [tid for tid in team_ids if str(tid) not in squads]

    for team_id in new_ids:
        print(f"جاري جلب تشكيلة النادي رقم {team_id}...")
        try:
            squads[str(team_id)] = fetch_team_squad(team_id)
        except Exception as e:
            print(f"تعذّر جلب تشكيلة النادي {team_id} ({e}).")

    if new_ids:
        save_squads(squads)
        print(f"أُضيفت تشكيلات {len(new_ids)} نادي جديد (الإجمالي: {len(squads)}).")

    return squads


def calculate_standings_from_results(league_matches: list):
    """يحسب جدول الترتيب من مباريات دوري واحد مأخوذة من الأرشيف المحلي."""
    teams = {}
    for m in league_matches:
        for team_id, team_name, gf, ga in (
            (m["home_id"], m["home"], m["home_goals"], m["away_goals"]),
            (m["away_id"], m["away"], m["away_goals"], m["home_goals"]),
        ):
            stats = teams.setdefault(team_id, {
                "team": team_name,
                "played": 0, "wins": 0, "draws": 0, "losses": 0,
                "gf": 0, "ga": 0, "points": 0,
            })
            stats["played"] += 1
            stats["gf"] += gf
            stats["ga"] += ga
            if gf > ga:
                stats["wins"] += 1
                stats["points"] += 3
            elif gf == ga:
                stats["draws"] += 1
                stats["points"] += 1
            else:
                stats["losses"] += 1

    table = sorted(
        teams.values(),
        key=lambda t: (-t["points"], -(t["gf"] - t["ga"]), -t["gf"]),
    )

    return [
        {"rank": i, "team": t["team"], "played": t["played"], "points": t["points"]}
        for i, t in enumerate(table, start=1)
    ]


def run_standings_pipeline():
    print("جاري تحديث أرشيف نتائج الدوريات المحلي...")
    archive = update_results_archive()
    squads = update_squads_cache(archive)
    update_players_bio_cache(squads)

    all_standings = {}
    for league_id, league_name in TARGET_LEAGUES.items():
        league_matches = [m for m in archive if m["league_id"] == league_id]
        standings = calculate_standings_from_results(league_matches)
        all_standings[str(league_id)] = {"name": league_name, "standings": standings}
        print(f"  {league_name}: ترتيب {len(standings)} فريق من {len(league_matches)} نتيجة مؤرشفة.")

    return all_standings


# ============================================================
# 3) الأخبار (مبنية على بيانات حقيقية فقط - بدون توليد حر بالذكاء
#    الاصطناعي، عشان نضمن دقتها 100%)
# ============================================================

def _match_result_headline(m: dict) -> str:
    """عنوان خبر واقعي 100% من نتيجة مباراة فعلية."""
    home, away = m["home_team"], m["away_team"]
    hs, as_ = m["home_score"], m["away_score"]

    if hs is None or as_ is None:
        return f"{home} يواجه {away} ضمن {m['league']}"
    if hs > as_:
        return f"{home} يفوز على {away} {hs}-{as_} ضمن {m['league']}"
    if as_ > hs:
        return f"{away} يفوز على {home} {as_}-{hs} ضمن {m['league']}"
    return f"تعادل {home} و{away} {hs}-{as_} ضمن {m['league']}"


def build_news_items(matches: list, standings: dict) -> dict:
    """
    يبني عناصر إخبارية من بيانات المباريات والترتيب الحقيقية فقط
    (بدون أي توليد حر بالذكاء الاصطناعي قد يختلق تفاصيل غير صحيحة)،
    مقسّمة لقسمين: دوري روشن السعودي، والدوريات الأوروبية.
    """
    saudi_items, europe_items = [], []

    for league_id, info in standings.items():
        league_id = int(league_id)
        if not info["standings"]:
            continue
        leader = info["standings"][0]
        item = {
            "tag": info["name"],
            "title": f"{leader['team']} يتصدر ترتيب {info['name']} برصيد {leader['points']} نقطة",
        }
        (saudi_items if league_id == STANDINGS_LEAGUE_ID else europe_items).append(item)

    for m in matches:
        if m.get("league_id") not in TARGET_LEAGUES:
            continue
        item = {"tag": m["league"], "title": _match_result_headline(m)}
        (saudi_items if m["league_id"] == STANDINGS_LEAGUE_ID else europe_items).append(item)

    return {"saudi": saudi_items, "europe": europe_items}


# ============================================================
# 4) أخبار حقيقية (انتقالات، شائعات، تكتيك...) من مصدر صحفي فعلي عبر RSS
#    - بدون أي ذكاء اصطناعي وبدون أي مفتاح API أو فوترة، العناوين
#    والصور والنص كلها منشورة أصلاً من "الرياضية" وننسبها لهم بوضوح
#    مع رابط مباشر للمقال الكامل عندهم، بدل ما ننسخ محتواهم كامل
# ============================================================

RSS_FEED_URL = "https://www.arriyadiyah.com/rss"
RSS_SOURCE_NAME = "الرياضية"
MAX_RSS_ITEMS = 20

# كلمات مفتاحية عشان نفلتر بس الأخبار المتعلقة بكرة القدم من فيد رياضي
# عام (فيه أخبار تنس ورياضات ثانية أحياناً) - فلترة على العناوين
# الحقيقية نفسها، بدون أي تعديل أو توليد
FOOTBALL_KEYWORDS = [
    "كرة القدم", "الدوري", "نادي", "الاتحاد", "النصر", "الهلال", "الأهلي",
    "الاتفاق", "الفتح", "التعاون", "الرائد", "الخلود", "الفيحاء", "ضمك",
    "القادسية", "نيوم", "الدرعية", "الطائي", "الوحدة",
    "ريال مدريد", "برشلونة", "أتلتيكو", "مانشستر", "ليفربول", "تشيلسي",
    "أرسنال", "توتنهام", "بايرن", "دورتموند", "يوفنتوس", "ميلان",
    "إنتر", "باريس سان جيرمان", "صفقة", "الدوري السعودي", "دوري روشن",
    "كأس العالم", "كأس الأمم", "الفيفا", "دوري أبطال أوروبا",
    "انتقال إلى", "المنتخب السعودي", "كرة قدم",
]


def strip_html(text: str) -> str:
    """يشيل وسوم HTML من نص الوصف ويرجع مقتطف نصي نظيف."""
    if not text:
        return ""
    clean = re.sub(r"<[^>]+>", " ", text)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


def is_football_related(title: str, description: str) -> bool:
    text = f"{title} {description}"
    return any(kw in text for kw in FOOTBALL_KEYWORDS)


def fetch_real_news_from_rss() -> list:
    """
    يجيب آخر الأخبار الحقيقية (انتقالات/شائعات/تكتيك) من فيد RSS عام
    ومجاني بالكامل لصحيفة "الرياضية" - بدون أي مفتاح API وبدون فوترة.
    العنوان والصورة والمقتطف كلها منشورة فعلياً من المصدر نفسه، ونحتفظ
    برابط المقال الأصلي عشان القارئ يقرأ الخبر كامل عند مصدره الحقيقي.
    """
    try:
        response = requests.get(RSS_FEED_URL, timeout=20)
        response.raise_for_status()
        root = ET.fromstring(response.content)
    except Exception as e:
        print(f"تعذّر جلب فيد الأخبار الحقيقية ({e}).")
        return []

    items = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        description = strip_html(item.findtext("description") or "")
        if not title or not link:
            continue
        if not is_football_related(title, description):
            continue

        excerpt = description[:220] + ("…" if len(description) > 220 else "")

        items.append({
            "title": title,
            "link": link,
            "image": item.findtext("main_image") or None,
            "excerpt": excerpt,
            "source": RSS_SOURCE_NAME,
            "author": item.findtext("{http://purl.org/dc/elements/1.1/}creator") or RSS_SOURCE_NAME,
            "published": item.findtext("pubDate") or "",
        })

        if len(items) >= MAX_RSS_ITEMS:
            break

    return items


# ============================================================
# التشغيل الرئيسي
# ============================================================

if __name__ == "__main__":
    matches = run_matches_pipeline()
    standings = run_standings_pipeline()
    news = build_news_items(matches, standings)

    print("جاري جلب الأخبار الحقيقية (انتقالات/شائعات) من الرياضية...")
    real_news = fetch_real_news_from_rss()

    with open("matches.json", "w", encoding="utf-8") as f:
        json.dump(matches, f, ensure_ascii=False, indent=2)

    with open("standings.json", "w", encoding="utf-8") as f:
        json.dump(standings, f, ensure_ascii=False, indent=2)

    with open("news.json", "w", encoding="utf-8") as f:
        json.dump(news, f, ensure_ascii=False, indent=2)

    with open("real_news.json", "w", encoding="utf-8") as f:
        json.dump(real_news, f, ensure_ascii=False, indent=2)

    total_teams = sum(len(l["standings"]) for l in standings.values())
    total_news = len(news["saudi"]) + len(news["europe"])
    print(f"\nتم حفظ: {len(matches)} مباراة، ترتيب {len(standings)} دوريات ({total_teams} فريق إجمالاً)، "
          f"{total_news} خبر نتائج، {len(real_news)} خبر حقيقي من الرياضية.")
