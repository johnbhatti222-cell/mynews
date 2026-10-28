# Breaking-news alert bot (free setup)

Every ~10 minutes GitHub Actions checks Indian news RSS feeds, drafts a headline tweet
plus a pro-reform / skeptical-of-government opinion tweet, and sends both to your Telegram.
You copy, check and post manually. Nothing is auto-posted.

## Cost
- GitHub Actions: free and unlimited on a PUBLIC repo (your secrets stay private).
  On a private repo the free quota is 2,000 min/month, so change the cron to */30.
- Telegram bot: free.
- LLM: Google Gemini free tier (default, no card needed). The Claude API is paid
  (a few cents a day with Haiku); to use it, set LLM_PROVIDER=claude.

## Setup (about 10 minutes)
1. **Telegram bot**: in Telegram, message @BotFather, send /newbot, follow the prompts,
   copy the token. Then open your new bot and send it any message ("hi").
2. **Chat ID**: open https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates in a browser.
   Find `"chat":{"id":123456789` and copy that number.
3. **Gemini key (free)**: get one at https://aistudio.google.com/apikey
4. **GitHub**: create a new public repo, then upload these files keeping this structure:
   - monitor.py
   - requirements.txt
   - .github/workflows/news-alerts.yml
5. **Secrets**: repo > Settings > Secrets and variables > Actions > New repository secret:
   - TELEGRAM_BOT_TOKEN
   - TELEGRAM_CHAT_ID
   - GEMINI_API_KEY
6. **Test**: repo > Actions tab > "News alerts" > Run workflow. Alerts should reach
   Telegram within a minute. If nothing arrives, open the run log for errors.

## Tuning
- Edit FEEDS and KEYWORDS at the top of monitor.py.
- MAX_ALERTS_PER_RUN and MAX_AGE_HOURS control volume and freshness.
- Edit the stance and rules in SYSTEM.

## Notes
- GitHub cron can lag by 5-15 minutes, so this is "near real time", not instant.
- GitHub disables scheduled workflows after 60 days with no repo activity. If alerts stop,
  re-enable it from the Actions tab.
- Free-tier Gemini prompts may be used by Google to improve its products. The inputs here
  are public news items, but keep that in mind.
- Always verify before posting. Breaking news changes, and opinions on wrong facts are hard to undo.
