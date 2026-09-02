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
import re
from datetime import datetime
import google.generativeai as genai

# ============ الإعدادات ============
API_FOOTBALL_KEY = os.environ.get("API_FOOTBALL_KEY")
API_FOOTBALL_HOST = "v3.football.api-sports.io"
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# دوري روشن السعودي - يُستخدم لجدول الترتيب تحديداً
# غيّره لأي دوري ثاني لو تحب (39 = الإنجليزي، 140 = الإسباني ... إلخ)
STANDINGS_LEAGUE_ID = 307

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
        "home_team": teams["home"]["name"],
        "away_team": teams["away"]["name"],
        "home_score": goals["home"],
        "away_score": goals["away"],
        "status": status,
        "league": league,
        "date": fixture["fixture"]["date"],
    }


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

    response = model.generate_content(prompt)
    return response.text.strip()


def build_match_payload(match_summary: dict, article_text: str) -> dict:
    """
    يجهز بيانات المباراة بنفس التنسيق اللي يقرأه index.html مباشرة
    (home_team, away_team, home_score, away_score, status, league, content)
    """
    title = f"{match_summary['home_team']} {match_summary['home_score']} - " \
            f"{match_summary['away_score']} {match_summary['away_team']}"

    return {
        "title": title,
        "home_team": match_summary["home_team"],
        "away_team": match_summary["away_team"],
        "home_score": match_summary["home_score"],
        "away_score": match_summary["away_score"],
        "status": match_summary["status"],
        "league": match_summary["league"],
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

        # تجاهل المباريات اللي ما بدأت بعد
        if summary["status"] not in ("Match Finished", "Halftime", "In Play"):
            continue

        print(f"معالجة: {summary['home_team']} vs {summary['away_team']}")
        article = generate_article(summary)
        results.append(build_match_payload(summary, article))

    return results


# ============================================================
# 2) جدول الترتيب
# ============================================================

def resolve_saudi_league_id() -> int | None:
    """
    يبحث عن دوري روشن السعودي بالاسم مباشرة من API-Football
    بدل الاعتماد على رقم ثابت قد يكون غير دقيق.
    """
    url = "https://v3.football.api-sports.io/leagues"
    headers = {
        "x-rapidapi-key": API_FOOTBALL_KEY,
        "x-rapidapi-host": API_FOOTBALL_HOST,
    }
    params = {"country": "Saudi-Arabia"}

    response = requests.get(url, headers=headers, params=params, timeout=30)
    response.raise_for_status()
    leagues = response.json().get("response", [])

    for entry in leagues:
        name = entry["league"]["name"]
        if entry["league"]["type"] == "League" and "Pro League" in name:
            print(f"لقيت الدوري: {name} (id={entry['league']['id']})")
            return entry["league"]["id"]

    print("ما لقيت دوري روشن السعودي بالبحث التلقائي.")
    return None


def get_standings(league_id: int):
    """يجيب جدول ترتيب دوري معين من API-Football"""
    url = "https://v3.football.api-sports.io/standings"
    headers = {
        "x-rapidapi-key": API_FOOTBALL_KEY,
        "x-rapidapi-host": API_FOOTBALL_HOST,
    }
    params = {"league": league_id, "season": datetime.now().year}

    response = requests.get(url, headers=headers, params=params, timeout=30)
    response.raise_for_status()
    data = response.json().get("response", [])

    if not data:
        return []

    # الشكل: response[0].league.standings[0] = قائمة الفرق بترتيبها
    try:
        table = data[0]["league"]["standings"][0]
    except (KeyError, IndexError):
        return []

    standings = []
    for team_row in table:
        standings.append({
            "rank": team_row["rank"],
            "team": team_row["team"]["name"],
            "played": team_row["all"]["played"],
            "points": team_row["points"],
        })
    return standings


def run_standings_pipeline():
    league_id = resolve_saudi_league_id() or STANDINGS_LEAGUE_ID
    print(f"جاري جلب جدول الترتيب (دوري رقم {league_id})...")
    standings = get_standings(league_id)
    print(f"تم جلب ترتيب {len(standings)} فريق." if standings else "ما قدر يجيب جدول الترتيب.")
    return standings


# ============================================================
# 3) الأخبار (مولّدة من نفس بيانات اليوم)
# ============================================================

def generate_news(matches: list, standings: list) -> list:
    """
    يستخدم Gemini يولّد 3 عناوين أخبار قصيرة مبنية على أبرز نتائج
    وترتيب اليوم، بصيغة JSON جاهزة للموقع: [{tag, title}, ...]
    """
    if not matches and not standings:
        return []

    matches_brief = "\n".join(
        f"- {m['home_team']} {m['home_score']}-{m['away_score']} {m['away_team']} ({m['league']})"
        for m in matches[:8]
    ) or "لا توجد مباريات اليوم."

    standings_brief = ""
    if standings:
        leader = standings[0]
        standings_brief = f"\nصدارة الترتيب حالياً: {leader['team']} برصيد {leader['points']} نقطة."

    prompt = f"""أنت محرر أخبار رياضية. بناءً على بيانات اليوم التالية، اكتب 3 عناوين
أخبار رياضية قصيرة وجذابة بالعربية (كل عنوان تحت 12 كلمة)، كل واحد له تصنيف قصير (وسم).

نتائج اليوم:
{matches_brief}
{standings_brief}

أرجع النتيجة بصيغة JSON فقط بدون أي نص إضافي، بالضبط بهذا الشكل:
[
  {{"tag": "التصنيف", "title": "نص العنوان"}},
  {{"tag": "التصنيف", "title": "نص العنوان"}},
  {{"tag": "التصنيف", "title": "نص العنوان"}}
]"""

    response = model.generate_content(prompt)
    raw = response.text.strip()

    # تنظيف احتمال وجود ```json فواصل من الرد
    raw = re.sub(r"^```json\s*|\s*```$", "", raw.strip(), flags=re.MULTILINE)

    try:
        news = json.loads(raw)
        if isinstance(news, list):
            return news
    except json.JSONDecodeError:
        print("تعذّر تحليل رد الأخبار كـ JSON، بيتم تجاهله.")

    return []


# ============================================================
# التشغيل الرئيسي
# ============================================================

if __name__ == "__main__":
    matches = run_matches_pipeline(league_id=None)
    standings = run_standings_pipeline()
    news = generate_news(matches, standings)

    with open("matches.json", "w", encoding="utf-8") as f:
        json.dump(matches, f, ensure_ascii=False, indent=2)

    with open("standings.json", "w", encoding="utf-8") as f:
        json.dump(standings, f, ensure_ascii=False, indent=2)

    with open("news.json", "w", encoding="utf-8") as f:
        json.dump(news, f, ensure_ascii=False, indent=2)

    print(f"\nتم حفظ: {len(matches)} مباراة، {len(standings)} فريق بالترتيب، {len(news)} خبر.")
