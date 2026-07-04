# SimpleClaw Page Reader Chrome Extension

This unpacked Chrome extension sends the **current active tab's visible text** to the
local SimpleClaw runtime through Chrome Native Messaging after an explicit user click.

## Install

1. Open `chrome://extensions`.
2. Enable **Developer mode**.
3. Click **Load unpacked** and select `browser_extensions/simpleclaw_reader`.
4. Copy the extension ID.
5. From the SimpleClaw repo, run:

   ```bash
   HOME=/Users/simplist .venv/bin/python scripts/browser_handoff/install_native_host.py --extension-id <extension-id>
   ```

## Privacy boundary

- Only the active tab is read.
- The user must click **Send current page to SimpleClaw**.
- Cookies, passwords, form values, localStorage, and browsing history are not sent.
- Pages with password fields are rejected by the native host.
