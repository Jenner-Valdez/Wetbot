import os
import requests

url = os.getenv("https://discord.com/api/webhooks/1470520708923916480/-MfGXrc_rI5CQdPQM0r9WCYD3j2u12c9SKWJNbaqk47RTAZMRg7xY8pT61xruxC79ZvE")
if not url:
    raise RuntimeError("DISCORD_WEBHOOK_URL is not set")

r = requests.post(url, json={"content": "✅ Desktop bot test: Discord webhook works."}, timeout=20)
r.raise_for_status()

print("Posted successfully.")
