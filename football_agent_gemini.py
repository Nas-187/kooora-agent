"""
إيجنت كورة القدم - نتائج + ترتيب + أخبار، جاهزة للنشر على الموقع
====================================================
النسخة المجانية بالكامل: API-Football + Google Gemini

المتطلبات:
    pip install requests google-generativeai

المفاتيح (كمتغيرات بيئة أو GitHub Secrets):
    API_FOOTBALL_KEY -> مفتاح من https://www.api-football.com/
    GEMINI_API_KEY    -> مفتاح من https://aistudio.google.com/
"""

import requests
import json
import os
from datetime import datetime
import google.generativeai as genai

# ============ الإعدادات ============
API_FOOTBALL_KEY = os.environ.get("API_FOOTBALL_KEY")
API_FOOTBALL_HOST = "v3.football.api-sports.io"
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# دوري روشن السعودي - يُستخدم لجدول الترتيب تحديداً
# غيّره لأي دوري ثاني لو تحب (39 = الإنجليزي، 140 = الإسباني ... إلخ)
STANDINGS_LEAGUE_ID = 307

# أرشيف نتائج محلي تراكمي - نبنيه يوم بيوم لأن الخطة المجانية بـ API-Football
# ما تسمح بجلب مباريات الموسم الحالي (2026) دفعة وحدة، بس تسمح بنافذة تواريخ
# قريبة من اليوم. لازم يُحفظ ويُرفع للمستودع بعد كل تشغيل عشان يتراكم.
RESULTS_ARCHIVE_FILE = "results_archive.json"

# الدوريات اللي نبني لها جدول ترتيب من الأرشيف المحلي
TARGET_LEAGUES = {
    307: "دوري روشن السعودي",
    39: "الدوري الإنجليزي",
    140: "الدوري الإسباني",
    135: "الدوري الإيطالي",
    78: "الدوري الألماني",
    61: "الدوري الفرنسي",
}

if not API_FOOTBALL_KEY or not GEMINI_API_KEY:
    raise SystemExit(
        "لازم تحط المفاتيح كمتغيرات بيئة أول:\n"
        "  export API_FOOTBALL_KEY=مفتاحك\n"
        "  export GEMINI_API_KEY=مفتاحك\n"
    )

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-3.6-flash")


# ============================================================
# 1) المباريات
# ============================================================

def get_todays_fixtures(league_id: int = None, date: str = None):
    """يجيب مباريات اليوم (أو تاريخ محدد) من API-Football."""
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    url = "https://v3.football.api-sports.io/fixtures"
    headers = {
        "x-rapidapi-key": API_FOOTBALL_KEY,
        "x-rapidapi-host": API_FOOTBALL_HOST,
    }
    params = {"date": date}
    if league_id:
        params["league"] = league_id
        params["season"] = datetime.now().year

    response = requests.get(url, headers=headers, params=params, timeout=30)
    response.raise_for_status()
    return response.json().get("response", [])


def format_fixture_summary(fixture: dict) -> dict:
    """يستخرج المعلومات المهمة من بيانات المباراة الخام"""
    teams = fixture["teams"]
    goals = fixture["goals"]
    status = fixture["fixture"]["status"]["long"]
    league = fixture["league"]["name"]

    return {
        "league_id": fixture["league"]["id"],
        "league_logo": fixture["league"]["logo"],
        "home_team": teams["home"]["name"],
        "away_team": teams["away"]["name"],
        "home_logo": teams["home"]["logo"],
        "away_logo": teams["away"]["logo"],
        "home_score": goals["home"],
        "away_score": goals["away"],
        "status": status,
        "league": league,
        "date": fixture["fixture"]["date"],
    }


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
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        print(f"تعذّر توليد المقال عبر Gemini ({e}) - استخدام نص بديل تلقائي.")
        return fallback_article_text(match_summary)


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
        "title": title,
        "league_id": match_summary["league_id"],
        "league_logo": match_summary["league_logo"],
        "home_team": match_summary["home_team"],
        "away_team": match_summary["away_team"],
        "home_logo": match_summary["home_logo"],
        "away_logo": match_summary["away_logo"],
        "home_score": match_summary["home_score"],
        "away_score": match_summary["away_score"],
        "status": match_summary["status"],
        "league": match_summary["league"],
        "date": match_summary["date"],
        "content": article_text,
    }


def run_matches_pipeline(league_id: int = None):
    """يشغّل دورة المباريات كاملة: جلب -> صياغة -> تجهيز"""
    print("جاري جلب المباريات...")
    fixtures = get_todays_fixtures(league_id=league_id)

    if not fixtures:
        print("ما فيه مباريات اليوم.")
        return []

    results = []
    for fixture in fixtures:
        summary = format_fixture_summary(fixture)

        # نعرض بس مباريات الدوريات الستة المتابعة - غير كذا الصفحة تطول
        # بمئات المباريات من دوريات كل العالم بلا فايدة
        if summary["league_id"] not in TARGET_LEAGUES:
            continue

        # نتجاهل بس الحالات الملغاة/المؤجلة - نعرض المنتهية والجارية
        # والمجدولة لاحقاً اليوم (عشان صفحة "مباريات اليوم" تكون كاملة)
        if summary["status"] not in ("Match Finished", "Halftime", "In Play", "Not Started"):
            continue

        print(f"معالجة: {summary['home_team']} vs {summary['away_team']} ({summary['status']})")

        if summary["status"] == "Not Started":
            # ما بدأت بعد - ما فيه نتيجة نبني منها مقال، نكتفي بموعدها
            article = ""
        else:
            # كلهم الآن ضمن الدوريات المستهدفة، فنستخدم Gemini للجميع
            article = generate_article(summary)

        results.append(build_match_payload(summary, article))

    return results


# ============================================================
# 2) جدول الترتيب
# ============================================================

def load_results_archive() -> list:
    """يقرأ أرشيف النتائج المحلي المتراكم (لو موجود)."""
    if os.path.exists(RESULTS_ARCHIVE_FILE):
        with open(RESULTS_ARCHIVE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_results_archive(archive: list):
    with open(RESULTS_ARCHIVE_FILE, "w", encoding="utf-8") as f:
        json.dump(archive, f, ensure_ascii=False, indent=2)


def update_results_archive(league_ids) -> list:
    """
    يجيب مباريات اليوم المنتهية (FT) لكل الدوريات المستهدفة (TARGET_LEAGUES)
    ويضيف الجديد منها لأرشيف محلي دائم. نطلب بالتاريخ بس (بدون league/season)
    لأن أي طلب فيه season=2026 مرفوض من الخطة المجانية حتى لو التاريخ ضمن
    النافذة المسموحة - وطلب واحد بالتاريخ يرجع كل الدوريات دفعة وحدة.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    fixtures = get_todays_fixtures(date=today)

    archive = load_results_archive()
    existing_ids = {m["fixture_id"] for m in archive}

    added = 0
    for fx in fixtures:
        league_id = fx["league"]["id"]
        if league_id not in league_ids:
            continue
        if fx["fixture"]["status"]["short"] != "FT":
            continue

        fixture_id = fx["fixture"]["id"]
        if fixture_id in existing_ids:
            continue

        home_goals = fx["goals"]["home"]
        away_goals = fx["goals"]["away"]
        if home_goals is None or away_goals is None:
            continue

        archive.append({
            "fixture_id": fixture_id,
            "league_id": league_id,
            "league_name": TARGET_LEAGUES.get(league_id, fx["league"]["name"]),
            "date": fx["fixture"]["date"][:10],
            "home_id": fx["teams"]["home"]["id"],
            "home": fx["teams"]["home"]["name"],
            "home_logo": fx["teams"]["home"]["logo"],
            "away_id": fx["teams"]["away"]["id"],
            "away": fx["teams"]["away"]["name"],
            "away_logo": fx["teams"]["away"]["logo"],
            "home_goals": home_goals,
            "away_goals": away_goals,
        })
        existing_ids.add(fixture_id)
        added += 1

    if added:
        save_results_archive(archive)
        print(f"أُضيفت {added} نتيجة جديدة للأرشيف المحلي (الإجمالي: {len(archive)}).")

    return archive


SQUADS_FILE = "squads.json"


def load_squads() -> dict:
    """يقرأ تشكيلات الأندية المخزّنة محلياً (لو موجودة)."""
    if os.path.exists(SQUADS_FILE):
        with open(SQUADS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_squads(squads: dict):
    with open(SQUADS_FILE, "w", encoding="utf-8") as f:
        json.dump(squads, f, ensure_ascii=False, indent=2)


def fetch_team_squad(team_id: int):
    """يجيب تشكيلة نادي واحد (لاعبين حقيقيين: اسم، صورة، مركز، رقم)."""
    url = "https://v3.football.api-sports.io/players/squads"
    headers = {
        "x-rapidapi-key": API_FOOTBALL_KEY,
        "x-rapidapi-host": API_FOOTBALL_HOST,
    }
    response = requests.get(url, headers=headers, params={"team": team_id}, timeout=30)
    response.raise_for_status()
    data = response.json().get("response", [])
    if not data:
        return []

    return [
        {
            "id": p["id"],
            "name": p["name"],
            "age": p["age"],
            "number": p["number"],
            "position": p["position"],
            "photo": p["photo"],
        }
        for p in data[0].get("players", [])
    ]


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
    archive = update_results_archive(set(TARGET_LEAGUES.keys()))
    update_squads_cache(archive)

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
# التشغيل الرئيسي
# ============================================================

if __name__ == "__main__":
    matches = run_matches_pipeline(league_id=None)
    standings = run_standings_pipeline()
    news = build_news_items(matches, standings)

    with open("matches.json", "w", encoding="utf-8") as f:
        json.dump(matches, f, ensure_ascii=False, indent=2)

    with open("standings.json", "w", encoding="utf-8") as f:
        json.dump(standings, f, ensure_ascii=False, indent=2)

    with open("news.json", "w", encoding="utf-8") as f:
        json.dump(news, f, ensure_ascii=False, indent=2)

    total_teams = sum(len(l["standings"]) for l in standings.values())
    total_news = len(news["saudi"]) + len(news["europe"])
    print(f"\nتم حفظ: {len(matches)} مباراة، ترتيب {len(standings)} دوريات ({total_teams} فريق إجمالاً)، {total_news} خبر.")
