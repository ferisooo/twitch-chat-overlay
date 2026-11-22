# Live Chat Overlay

A small always-on-top chat window for streamers on Windows. It shows Twitch and TikTok LIVE chat in one see-through overlay you can keep over your game, and it gives OBS its own chat page to put on stream.

> **Version 2.** A full rebuild. Coming from version 1? See [Install](#install).

- **Twitch, TikTok and Merge tabs.** Merge shows both chats together, with a colored bar marking where each message came from.
- **Truly see-through.** The window has real transparency, so there is no color-key trick and no flicker.
- **Click-through.** Clicks pass to your game. Hold **Alt** or **Shift**, or click the title bar, to use the overlay.
- **Its own OBS page.** Add one Browser Source and your viewers see clean chat with rounded text boxes. No Color Key filter needed.
- **Twitch emotes plus BTTV, FFZ and 7TV emotes.** In OBS they even move.
- **Moderator deletes and bans** remove those messages from the overlay and from OBS.
- **AutoMod messages** you can allow or deny right in the overlay (never shown on stream).
- **Channel point redeems in chat** when you also run Gift Spawner.
- **Nicknames for chatters,** and every chatter keeps their own name color.
- **Mentions** of your name are highlighted.
- **Hides itself when chat is quiet** and comes back on the next message or when you hover over its spot.
- **Tray icon** to show the overlay, copy the OBS link, or quit.
- Optional: close together with OBS, and open "I'm live" posts for X and Facebook when your stream starts.

## Requirements

- Windows 10 or 11
- [Python](https://www.python.org/downloads/) 3.10 or newer

## Install

1. Get the files: download and unzip them, or `git clone` this repository.
2. Double-click **`install requirements.bat`**. It checks your Python, asks which chat you use, and installs what that needs:
   - **Everyone:** PySide6, which draws the overlay window (about 80 MB, one time).
   - **TikTok or Both:** also TikTokLive.

   It's safe to run again, for example if you add TikTok later.
3. Double-click **`start.bat`**. If something is missing, it runs the installer for you first.

On first start the ⚙ settings panel is open. Type your Twitch channel on the Twitch tab and press **Connect**. Do the same with your TikTok username on the TikTok tab.

**Coming from version 1?** Double-click `update.bat` in your old overlay folder, then `start.bat`. Your channels, nicknames, name colors and Twitch login come over by themselves.

## OBS setup

1. In OBS, add a **Browser** source.
2. For the URL, use the link from ⚙ → **Copy OBS link**. It's normally `http://127.0.0.1:4848/`
3. Set the width and height to the size you want the chat on stream, for example 400 × 600.

That's it. The page is already see-through. It shows the same tab as your overlay.

Extra options go at the end of the link:

| Add this | What it does |
| --- | --- |
| `?tab=merge` | Always show one tab (`twitch`, `tiktok` or `merge`), whatever the overlay shows |
| `?size=20` | Text size in pixels |
| `?boxes=0` | No dark boxes behind messages (text gets a shadow instead) |
| `?fade=0` | Never fade out when chat is quiet |
| `?system=1` | Also show connection notes and warnings (normally only you see those) |

Combine them with `&`, for example `http://127.0.0.1:4848/?tab=merge&size=18`.

The page only works on your own PC and only ever gets chat, never your login.

## Twitch login (optional)

Reading Twitch chat needs no login. Logging in lets you **send** chat, [approve AutoMod messages](#automod), and notice when you go live.

Open ⚙ → **Log in to Twitch**. There are two ways:

- **Quick way:** click **Get token**. That opens [twitchtokengenerator.com](https://twitchtokengenerator.com), a site made by someone else, with the right permissions picked. Authorize, copy the **Access token**, paste it, and press **Save & log in**. Only use a token site you trust. You can remove its access any time under Twitch **Settings → Connections**.
- **Safer way (no third-party site):** make your own free app at [dev.twitch.tv/console/apps](https://dev.twitch.tv/console/apps). Pick **Category: Chat Bot**, **Client Type: Public**, and **OAuth Redirect URL: `http://localhost`**. Copy its **Client ID** into the login window and press **Log in with Twitch**. Twitch shows a short code to type in. This login renews itself, so you rarely log in again.

Your login is kept in **Windows Credential Manager**, not in a file.

## TikTok

TikTok chat uses [TikTokLive](https://github.com/isaackogan/TikTokLive), an unofficial library that TikTok doesn't support. It can break when TikTok changes things. When it does, Twitch keeps working.

Each connection goes through TikTokLive's signing server, which has a rate limit. If you hit it, the overlay says so in chat and waits before trying again.

## Using it

| To | Do |
| --- | --- |
| Move the window | Drag the title bar |
| Resize | Click the overlay, then drag the right or bottom edge, or the corner |
| Open settings | ⚙, or double-click the title |
| Click the chat while it's see-through | Hold **Alt** or **Shift** with the mouse over the overlay |
| Give a chatter a nickname | Click their name (hold **Shift** if the chat is see-through) |
| Send a Twitch message | Click the overlay, then start typing |
| Log out of Twitch | Right-click **Send**, or use the login window |
| Hide it | Click **−**. It comes back on the next message, or when you move the mouse away and back over its spot |
| Bring it back | Hover over its spot, wait for the next message, or click the tray icon |
| Change text size | ⚙ → **A−** / **A+** |

## Nicknames for chatters

Click a name in chat to change how it shows, in the overlay and in OBS. It's handy for long usernames, or for regulars you know by another name. Leave the box empty (or press **Clear**) to go back to their real name.

Every chatter also gets their own name color, which stays the same between streams.

## AutoMod

When Twitch AutoMod holds a message, it shows at the top of the overlay with **Allow** and **Deny** buttons and a 30 second timer. Held messages never appear on the OBS page.

This needs a Twitch login from the streamer or one of their mods, with the AutoMod permission. Both login ways in the login window ask for it.

## Channel point redeems

If you also run Gift Spawner on the same PC, every channel point redeem it handles shows in the Twitch chat, like `✨ Viewer redeemed Hydrate (500 points)`. Nothing to set up.

## Updating

Double-click **`update.bat`**. It closes the overlay if it's open, gets the newest version, and starts the overlay again.

It works in a downloaded zip folder too, no Git needed: it downloads the newest zip from GitHub and copies it over your folder. Files you added yourself are left alone. A folder made with `git clone` is updated with Git instead.

## Where things are kept

| What | Where |
| --- | --- |
| Settings | `%APPDATA%\LiveChatOverlay\settings.json` |
| Twitch login | Windows Credential Manager (`LiveChatOverlay/twitch`) |
| Log file (for bug reports) | `%APPDATA%\LiveChatOverlay\overlay.log` |
| Emote pictures | `%LOCALAPPDATA%\LiveChatOverlay\cache` (safe to delete) |

## For developers

```
main.py            starts the app
overlay/
  hub.py           the middle: turns events into chat lines, tells the window and OBS
  model.py         one chat line (text, emotes, mentions)
  settings.py      settings file, import from version 1
  credstore.py     Windows Credential Manager
  emotes.py        Twitch, BTTV, FFZ and 7TV emotes
  obs_server.py    the local OBS page server (HTTP + WebSocket, built-in modules only)
  sources/         Twitch chat, Twitch login, AutoMod, live check, TikTok, Gift Spawner
  desktop/         the Qt window, dialogs, Windows helpers
  web/             the OBS page
tests/
```

Run the tests with `pip install -r requirements-dev.txt` and then `python -m pytest`.
