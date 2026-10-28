"""Breaking-news alert bot: RSS -> LLM draft (tweet + opinion) -> Telegram.

Runs once per invocation (designed for GitHub Actions on a schedule).
You review and post manually; nothing is auto-posted to X.
"""
import difflib
import hashlib
import html
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import feedparser
import requests

# ---------------------------------------------------------------- config
# Test each URL in a browser; edit freely. Feeds that fail are skipped.
FEEDS = [
    "https://www.thehindu.com/news/national/feeder/default.rss",
    "https://indianexpress.com/section/political-pulse/feed/",
    "https://www.hindustantimes.com/feeds/rss/india-news/rssfeed.xml",
    "https://feeds.feedburner.com/ndtvnews-india-news",
    "https://timesofindia.indiatimes.com/rssfeedstopstories.cms",
]

KEYWORDS = [
    "election commission", "cec", "special intensive revision", "electoral roll",
    "supreme court", "high court", "parliament", "lok sabha", "rajya sabha",
    "one nation one election", "delimitation", "ordinance", "constitution",
    "modi", "amit shah", "rahul gandhi", "kharge", "congress", "bjp",
    "india bloc", "opposition", "cabinet", "chief minister", "governor",
    "assembly election", "bypoll", "by-election", "reform", "gst", "budget",
]

MAX_AGE_HOURS = 3          # ignore older items
MAX_ALERTS_PER_RUN = 3     # cap to avoid flooding / API limits
SEEN_FILE = Path("seen.json")
UA = "Mozilla/5.0 (compatible; news-alert-bot/1.0)"

PROVIDER = os.getenv("LLM_PROVIDER", "gemini").lower()   # "gemini" (free tier) or "claude" (paid)
MODEL = os.getenv("LLM_MODEL") or (
    "claude-haiku-5-5" if PROVIDER == "claude" else "gemini-2.5-flash"
)
DRY_RUN = os.getenv("DRY_RUN") == "1"

SYSTEM = """You draft posts for an X (Twitter) account about Indian politics.
The account owner's stance: PRO-REFORM and SKEPTICAL OF THE GOVERNMENT. The opinion
tweet should reflect that stance: scrutinise government claims and policies, ask
for evidence and accountability, and back institutional, electoral and economic reform.

Hard rules:
- Use ONLY facts in the news item provided. Never invent facts, numbers or quotes.
- Attribute claims ("X alleges", "X says"); do not state allegations as fact.
- Critique policies, actions and institutions, not people's identity. No communal,
  casteist, or hateful content, and no personal insults.
- If the item is a claim that may be unverified or developing, say so in "check".
- Keep the headline factual and neutral. Put the opinion ONLY in the opinion tweet.

Return ONLY a JSON object with these keys:
"headline": factual breaking-news tweet text, max 230 characters, no URL, may start with an emoji,
"opinion": opinion tweet reflecting the stance, max 270 characters, no URL, standalone,
"counterpoint": one sentence with the strongest opposing view (for the owner's awareness),
"check": one short sentence on what to verify before posting.
"""

# ---------------------------------------------------------------- helpers
KW_RE = re.compile(r"\b(?:" + "|".join(re.escape(k) for k in KEYWORDS) + r")\b", re.I)


def load_seen():
    try:
        return json.loads(SEEN_FILE.read_text())
    except Exception:
        return []


def save_seen(seen):
    SEEN_FILE.write_text(json.dumps(seen[-3000:]))


def clean(text):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text or ""))).strip()


def item_id(link, title):
    return hashlib.sha1(f"{link}|{title}".encode()).hexdigest()[:16]


def fetch_items():
    items = []
    for url in FEEDS:
        try:
            feed = feedparser.parse(url, agent=UA)
        except Exception as exc:
            print(f"feed error {url}: {exc}", file=sys.stderr)
            continue
        source = clean(feed.feed.get("title", "")) or url
        for e in feed.entries:
            title, link = clean(e.get("title")), e.get("link", "")
            if not title or not link:
                continue
            ts = e.get("published_parsed") or e.get("updated_parsed")
            dt = datetime(*ts[:6], tzinfo=timezone.utc) if ts else None
            items.append({
                "id": item_id(link, title), "title": title, "link": link,
                "summary": clean(e.get("summary"))[:800], "source": source, "dt": dt,
            })
    return items


def is_fresh(item):
    if item["dt"] is None:
        return True
    age_h = (datetime.now(timezone.utc) - item["dt"]).total_seconds() / 3600
    return age_h <= MAX_AGE_HOURS


def parse_json(text):
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start:end + 1])


def tweet_len(text, has_link=False):
    return len(text) + (24 if has_link else 0)  # X counts any link as 23 chars


# ---------------------------------------------------------------- LLM
def call_llm(prompt):
    if PROVIDER == "claude":
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"],
                     "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": MODEL, "max_tokens": 700, "system": SYSTEM,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=60,
        )
        r.raise_for_status()
        return r.json()["content"][0]["text"]
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent",
        headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"], "content-type": "application/json"},
        json={"systemInstruction": {"parts": [{"text": SYSTEM}]},
              "contents": [{"role": "user", "parts": [{"text": prompt}]}],
              "generationConfig": {"responseMimeType": "application/json", "temperature": 0.6}},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["candidates"][0]["content"]["parts"][0]["text"]


def draft(item):
    prompt = (f"News item\nSource: {item['source']}\nHeadline: {item['title']}\n"
              f"Summary: {item['summary'] or '(none)'}\nLink: {item['link']}")
    return parse_json(call_llm(prompt))


# ---------------------------------------------------------------- Telegram
def build_message(item, d):
    headline, opinion = d.get("headline", item["title"]).strip(), d.get("opinion", "").strip()
    warn = []
    if tweet_len(headline, True) > 280:
        warn.append("headline tweet is over 280 chars, trim it")
    if tweet_len(opinion) > 280:
        warn.append("opinion tweet is over 280 chars, trim it")
    parts = [
        f"🔴 BREAKING · {item['source']}", item["title"], "",
        "📰 HEADLINE TWEET", f"{headline}\n{item['link']}", "",
        "💬 OPINION TWEET (pro-reform, skeptical of govt)", opinion, "",
        f"⚖️ Strongest counterpoint: {d.get('counterpoint', '-')}",
        f"✅ Check before posting: {d.get('check', '-')}",
    ]
    if warn:
        parts.append("⚠️ " + "; ".join(warn))
    return "\n".join(parts)


def fallback_message(item, err):
    return (f"🔴 BREAKING · {item['source']}\n{item['title']}\n{item['link']}\n\n"
            f"(Draft failed: {str(err)[:150]})")


def send_telegram(text):
    if DRY_RUN:
        print("-" * 60 + "\n" + text + "\n")
        return
    r = requests.post(
        f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/sendMessage",
        json={"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": text[:4000],
              "disable_web_page_preview": True},
        timeout=30,
    )
    r.raise_for_status()


# ---------------------------------------------------------------- main
def main():
    seen = load_seen()
    seen_set = set(seen)
    items = fetch_items()
    print(f"fetched {len(items)} items")

    candidates = [i for i in items if i["id"] not in seen_set]
    seen.extend(i["id"] for i in candidates)   # mark everything seen, alert on top few only

    hits = [i for i in candidates if is_fresh(i) and KW_RE.search(f"{i['title']} {i['summary']}")]
    hits.sort(key=lambda i: i["dt"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)

    chosen = []
    for i in hits:  # skip near-duplicate stories from different outlets
        if all(difflib.SequenceMatcher(None, i["title"].lower(), c["title"].lower()).ratio() < 0.75
               for c in chosen):
            chosen.append(i)
        if len(chosen) >= MAX_ALERTS_PER_RUN:
            break
    print(f"{len(hits)} matches, sending {len(chosen)}")

    for item in chosen:
        try:
            msg = build_message(item, draft(item))
        except Exception as exc:
            print(f"draft failed: {exc}", file=sys.stderr)
            msg = fallback_message(item, exc)
        send_telegram(msg)
        time.sleep(3)  # stay under free-tier rate limits

    save_seen(seen)


if __name__ == "__main__":
    main()
