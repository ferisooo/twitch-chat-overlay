from overlay.emotes import Emote
from overlay.model import Message, build_segments, mention_regex, plain_text


def lookup(word):
    return {"catJAM": Emote("7tv:1", "catJAM", "https://x/1.gif", "https://x/3.webp")}.get(word)


def test_plain_text():
    segs, mentioned = build_segments("hello world")
    assert segs == [{"t": "text", "v": "hello world"}]
    assert not mentioned


def test_twitch_and_third_party_emotes():
    segs, _ = build_segments("Kappa so good catJAM !", "25:0-4", lookup)
    kinds = [s["t"] for s in segs]
    assert kinds == ["emote", "text", "emote", "text"]
    assert segs[0]["key"] == "tw:25"
    assert segs[2]["key"] == "7tv:1"
    assert plain_text(segs) == "Kappa so good catJAM !"


def test_emote_names_must_match_whole_word():
    segs, _ = build_segments("catJAMMING", "", lookup)
    assert segs == [{"t": "text", "v": "catJAMMING"}]


def test_mentions():
    rx = mention_regex(["StreamerBob", "", "abc"])  # short names are ignored
    segs, mentioned = build_segments("hey @streamerbob and streamerbobby", "", None, rx)
    assert mentioned
    assert segs == [
        {"t": "text", "v": "hey "},
        {"t": "mention", "v": "@streamerbob"},
        {"t": "text", "v": " and streamerbobby"},
    ]
    assert mention_regex(["abc"]) is None


def test_message_to_dict():
    msg = Message(1, "twitch", "chat", name="Bob", alias_key="bob", color="#fff", segments=[{"t": "text", "v": "hi"}])
    d = msg.to_dict("Bobby")
    assert d["u"] == {"key": "bob", "name": "Bob", "show": "Bobby", "color": "#fff"}
    assert d["seg"] == [{"t": "text", "v": "hi"}]
    assert "text" not in d
    sysmsg = Message(2, "tiktok", "system", text="Connected").to_dict()
    assert sysmsg == {"id": 2, "p": "tiktok", "k": "system", "text": "Connected"}
