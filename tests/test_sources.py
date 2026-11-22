import json
from types import SimpleNamespace

from overlay.colors import NameColors, UNIQUE_NAME_COLORS, readable_name_color
from overlay.emotes import EmoteIndex, parse_7tv, parse_bttv, parse_ffz_sets
from overlay.sources.automod import parse_automod_hold
from overlay.sources.giftspawner import GiftSpawnerEvents, parse_points_redeem
from overlay.sources.tiktok import expand_tiktok_emojis, tiktok_comment_text, tiktok_idle_warning
from overlay.sources.twitch_api import looks_like_oauth_token, normalize_oauth


# ----- colours -----
def test_name_colors_unique_then_generated():
    colors = NameColors()
    seen = [colors.color_for("twitch", f"user{i}") for i in range(len(UNIQUE_NAME_COLORS) + 5)]
    assert seen[: len(UNIQUE_NAME_COLORS)] == list(UNIQUE_NAME_COLORS)
    assert len(set(seen)) == len(seen)
    assert colors.color_for("twitch", "USER0") == seen[0]


def test_readable_name_color():
    assert readable_name_color("#000000") == "#9146FF"
    assert readable_name_color("nonsense") == "#ADADB8"
    assert readable_name_color("#FFD43B") == "#FFD43B"


# ----- emotes -----
def test_parse_emote_services():
    bttv = parse_bttv([{"id": "b1", "code": "catJAM"}, {"id": "", "code": "x"}])
    assert [(e.key, e.name) for e in bttv] == [("bttv:b1", "catJAM")]
    ffz = parse_ffz_sets(
        {"3": {"emoticons": [{"id": 9, "name": "LUL", "urls": {"1": "//cdn.ffz/9/1", "2": "//cdn.ffz/9/2"}}]}},
        [3],
    )
    assert ffz[0].url == "https://cdn.ffz/9/2"
    seventv = parse_7tv(
        [{"id": "s1", "name": "Clap", "data": {"host": {"url": "//cdn.7tv.app/emote/s1",
          "files": [{"name": "2x.webp"}, {"name": "3x.webp"}, {"name": "2x.gif"}]}}}]
    )
    assert seventv[0].url == "https://cdn.7tv.app/emote/s1/2x.gif"
    assert seventv[0].url_hd == "https://cdn.7tv.app/emote/s1/3x.webp"


def test_emote_index_channel_wins_and_can_be_disabled():
    responses = {
        "https://api.betterttv.net/3/cached/emotes/global": [{"id": "g", "code": "Same"}],
        "https://api.frankerfacez.com/v1/set/global": {"default_sets": [], "sets": {}},
        "https://7tv.io/v3/emote-sets/global": {"emotes": []},
        "https://api.betterttv.net/3/cached/users/twitch/42": {"channelEmotes": [{"id": "c", "code": "Same"}], "sharedEmotes": []},
    }

    def fetch(url):
        if url not in responses:
            raise OSError("offline")
        return responses[url]

    index = EmoteIndex(fetch)
    index.load_global()
    assert index.lookup("Same").key == "bttv:g"
    index._room = "42"
    index.load_room("42")
    assert index.lookup("Same").key == "bttv:c"
    index.enabled = False
    assert index.lookup("Same") is None


# ----- AutoMod / Gift Spawner -----
def test_parse_automod_hold_v2():
    held = parse_automod_hold({
        "message_id": "m1", "user_login": "Rude", "user_name": "Rude",
        "message": {"text": "bad   words"}, "reason": "automod", "automod": {"category": "swearing"},
    })
    assert held == {"id": "m1", "login": "rude", "name": "Rude", "text": "bad words", "why": "swearing"}
    assert parse_automod_hold({"message": "x"}) is None


def test_points_redeem_parse_and_dedupe():
    payload = {"entry": {"id": "r1", "kind": "points", "status": "spawned",
               "gift": {"name": "Hydrate", "points": 500}, "viewer": {"uniqueId": "v", "nickname": "V"}}}
    info = parse_points_redeem(payload)
    assert info == {"id": "r1", "login": "v", "name": "V", "reward": "Hydrate", "cost": 500, "test": False}
    assert parse_points_redeem({"entry": {**payload["entry"], "status": "failed"}}) is None
    got = []
    gs = GiftSpawnerEvents(got.append, 1)
    gs.handle("activity", json.dumps(payload))
    gs.handle("activity", json.dumps(payload))
    gs.handle("hello", json.dumps(payload))
    assert len(got) == 1 and got[0]["kind"] == "points"


# ----- TikTok text -----
def test_tiktok_emoji_codes():
    assert expand_tiktok_emojis("lol [laugh] [unknown]") == "lol 😝 [unknown]"


def test_tiktok_comment_with_sticker():
    event = SimpleNamespace(
        content="hi  there",
        emotes=[SimpleNamespace(index=3, emote=SimpleNamespace(emote_id="lmao"))],
        emote_list=[],
    )
    assert tiktok_comment_text(event) == "hi 🤣there"


def test_tiktok_idle_warning():
    event = SimpleNamespace(toast=SimpleNamespace(display_text="Your LIVE will end soon due to no activity"))
    assert "no activity" in tiktok_idle_warning(event)
    assert tiktok_idle_warning(SimpleNamespace(content="hello")) is None


# ----- tokens -----
def test_token_cleanup():
    assert normalize_oauth("oauth:abc123\nmore") == "abc123"
    assert looks_like_oauth_token("a" * 30)
    assert not looks_like_oauth_token("https://twitchapps.com/tmi/" + "a" * 10)
