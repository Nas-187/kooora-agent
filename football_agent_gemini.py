"""
إيجنت كورة القدم - يجيب النتائج ويصيغها كخبر جاهز للنشر
====================================================
النسخة المجانية بالكامل: API-Football + Google Gemini

المتطلبات:
    pip install requests google-generativeai --break-system-packages

قبل التشغيل، عبّي المتغيرات (الأفضل كمتغيرات بيئة، مو بالكود مباشرة):
    API_FOOTBALL_KEY -> مفتاح من https://www.api-football.com/
    GEMINI_API_KEY    -> مفتاح من https://aistudio.google.com/
"""

import requests
import json
import os
from datetime import datetime
import google.generativeai as genai

# ============ الإعدادات ============
# دايماً اقرأ المفاتيح من متغيرات البيئة، لا تحطها بالكود مباشرة
API_FOOTBALL_KEY = os.environ.get("API_FOOTBALL_KEY")
API_FOOTBALL_HOST = "v3.football.api-sports.io"

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

if not API_FOOTBALL_KEY or not GEMINI_API_KEY:
    raise SystemExit(
        "لازم تحط المفاتيح كمتغيرات بيئة أول:\n"
        "  export API_FOOTBALL_KEY=مفتاحك\n"
        "  export GEMINI_API_KEY=مفتاحك\n"
    )

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-3.6-flash")


def get_todays_fixtures(league_id: int = None, date: str = None):
    """
    يجيب مباريات اليوم (أو تاريخ محدد) من API-Football.
    league_id مثال: 39 = الدوري الإنجليزي، 307 = دوري روشن السعودي
    """
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

    response = requests.get(url, headers=headers, params=params)
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


def build_publish_payload(match_summary: dict, article_text: str) -> dict:
    """يجهز البيانات بصيغة جاهزة للنشر (مثلاً عبر REST API لـ WordPress)"""
    title = f"{match_summary['home_team']} {match_summary['home_score']} - " \
            f"{match_summary['away_score']} {match_summary['away_team']}"

    return {
        "title": title,
        "content": article_text,
        "category": match_summary["league"],
        "status": "publish",  # أو "draft" لو تبي تراجعها قبل النشر
        "meta": {
            "home_team": match_summary["home_team"],
            "away_team": match_summary["away_team"],
            "score": f"{match_summary['home_score']}-{match_summary['away_score']}",
        },
    }


def run_pipeline(league_id: int = None):
    """يشغّل الدورة الكاملة: جلب -> صياغة -> تجهيز للنشر"""
    print("جاري جلب المباريات...")
    fixtures = get_todays_fixtures(league_id=league_id)

    if not fixtures:
        print("ما فيه مباريات اليوم لهذا الدوري.")
        return []

    results = []
    for fixture in fixtures:
        summary = format_fixture_summary(fixture)

        # تجاهل المباريات اللي ما بدأت بعد
        if summary["status"] not in ("Match Finished", "Halftime", "In Play"):
            continue

        print(f"معالجة: {summary['home_team']} vs {summary['away_team']}")
        article = generate_article(summary)
        payload = build_publish_payload(summary, article)
        results.append(payload)

    return results


if __name__ == "__main__":
    # جرّب أول كل مباريات اليوم بدون تحديد دوري، عشان تتأكد إن الـ API شغّال
    # بعدها لو تبي تحدد دوري معين، حط رقمه هنا، مثلاً:
    #   307 = دوري روشن السعودي
    #   39  = الدوري الإنجليزي الممتاز
    #   140 = الدوري الإسباني
    articles = run_pipeline(league_id=None)

    # حفظ النتائج بملف JSON (بدل النشر المباشر، للمراجعة أول)
    with open("matches_ready_to_publish.json", "w", encoding="utf-8") as f:
        json.dump(articles, f, ensure_ascii=False, indent=2)

    print(f"\nتم تجهيز {len(articles)} خبر، محفوظين في matches_ready_to_publish.json")
