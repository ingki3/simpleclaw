# Browser Handoff

SimpleClaw browser handoff handles pages where `web_fetch` and headless browser fetching
are blocked by 403, Cloudflare, or human verification pages.

## What it does

1. SimpleClaw opens the requested URL in local Google Chrome.
2. The user completes browser-side verification or login if needed.
3. The user clicks the **SimpleClaw Page Reader** extension button.
4. The extension sends only the current tab's visible text to the local Native Messaging host.
5. SimpleClaw reads that text and continues the answer.

The user does **not** copy and paste page text.

## What it does not do

- It does not bypass Cloudflare.
- It does not read all Chrome tabs.
- It does not send cookies, passwords, form values, localStorage, or browsing history.
- It rejects pages with password fields.
- It is disabled for cron/background runs.

## Re-authentication policy

The user should not need to authenticate on every request if the same Chrome profile keeps
valid cookies/session state for the site. Re-authentication can still happen when Cloudflare
or the site expires cookies, changes risk scoring, or the browser profile is reset.

MVP requires a per-page extension approval click for privacy. A future domain trust policy
can allow short-lived auto-send for approved domains.

## Setup

1. Enable config:

   ```yaml
   agent:
     browser_handoff:
       enabled: true
   ```

2. Load the unpacked Chrome extension from `browser_extensions/simpleclaw_reader`.
3. Copy the extension ID from `chrome://extensions`.
4. Install the native host:

   ```bash
   HOME=/Users/simplist .venv/bin/python scripts/browser_handoff/install_native_host.py --extension-id <extension-id>
   ```

5. Restart SimpleClaw.

## Troubleshooting

- **Native host not found:** rerun the installer with the exact extension ID.
- **Empty text:** wait until the page has loaded, then click the extension again.
- **Password page blocked:** this is intentional; do not use browser handoff for sensitive login pages.
- **Pending timeout:** the URL remains open in Chrome. Finish verification and click the extension, then ask SimpleClaw to read again.
