from overlay.sources.twitch_irc import (
    format_twitch_usernotice,
    irc_event,
    message_segments,
    parse_irc,
    twitch_unescape,
)


def test_parse_privmsg_with_tags():
    line = (
        "@badge-info=;color=#FF0000;display-name=Cool_Guy;emotes=25:0-4;id=abc-123;user-id=42 "
        ":cool_guy!cool_guy@cool_guy.tmi.twitch.tv PRIVMSG #chan :Kappa hello there"
    )
    tags, command, params = parse_irc(line)
    assert command == "PRIVMSG"
    assert params == ["#chan", "Kappa hello there"]
    assert tags["display-name"] == "Cool_Guy"
    assert tags["_nick"] == "cool_guy"
    ev = irc_event(tags, command, params)
    assert ev["kind"] == "chat"
    assert ev["login"] == "cool_guy"
    assert ev["id"] == "abc-123"
    assert ev["action"] is False


def test_action_message():
    tags, command, params = parse_irc(":bob!bob@bob PRIVMSG #chan :\x01ACTION waves\x01")
    ev = irc_event(tags, command, params)
    assert ev["action"] is True
    assert ev["text"] == "waves"


def test_unescape_is_single_pass():
    assert twitch_unescape(r"a\sb\:c") == "a b;c"
    assert twitch_unescape("x\\\\sy") == "x\\sy"


def test_usernotice_sub_uses_system_message():
    tags = {"msg-id": "resub", "display-name": "Fan", "system-msg": r"Fan\ssubscribed\sfor\s3\smonths!"}
    assert format_twitch_usernotice(tags, "love it") == "Fan subscribed for 3 months! — love it"


def test_usernotice_raid_without_system():
    tags = {"msg-id": "raid", "msg-param-displayName": "BigStreamer", "msg-param-viewerCount": "50"}
    assert format_twitch_usernotice(tags, "") == "RAID · BigStreamer is raiding with 50 viewers"


def test_usernotice_announcement():
    tags = {"msg-id": "announcement", "display-name": "Mod"}
    assert format_twitch_usernotice(tags, "Be nice") == "ANNOUNCEMENT · Mod: Be nice"
    assert format_twitch_usernotice(tags, "") is None


def test_usernotice_event_has_no_trailing_channel():
    tags, command, params = parse_irc(
        "@msg-id=raid;msg-param-displayName=X;msg-param-viewerCount=5 :tmi.twitch.tv USERNOTICE #chan"
    )
    assert irc_event(tags, command, params)["text"] == "RAID · X is raiding with 5 viewers"


def test_emote_segments_with_emoji_before_emote():
    # Twitch counts the emoji as 2 UTF-16 units.
    text = "😀 Kappa hi"
    segs = message_segments(text, "25:3-7")
    assert segs == [("😀 ", None), ("Kappa", "25"), (" hi", None)]


def test_bad_emote_ranges_are_ignored():
    assert message_segments("hello", "25:0-2,1-3") == [("hel", "25"), ("lo", None)]


def test_moderation_events():
    tags, command, params = parse_irc("@target-msg-id=m1 :tmi.twitch.tv CLEARMSG #chan :bad words")
    assert irc_event(tags, command, params) == {"src": "twitch", "kind": "delete", "id": "m1"}
    tags, command, params = parse_irc("@target-user-id=9 :tmi.twitch.tv CLEARCHAT #chan :Spammer")
    assert irc_event(tags, command, params)["login"] == "spammer"
    tags, command, params = parse_irc(":tmi.twitch.tv CLEARCHAT #chan")
    assert irc_event(tags, command, params)["kind"] == "clear_all"
    tags, command, params = parse_irc("@room-id=1234;slow=0 :tmi.twitch.tv ROOMSTATE #chan")
    assert irc_event(tags, command, params) == {"src": "twitch", "kind": "room", "room_id": "1234"}
