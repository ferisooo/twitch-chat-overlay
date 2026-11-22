import pytest

from overlay.hub import AUTOMOD_CARD_S, AUTOMOD_MAX_CARDS, Hub
from overlay.settings import Settings


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class FakeTwitch:
    def __init__(self):
        self.sent = []
        self.connected = []

    def connect(self, channel):
        self.connected.append(channel)

    def send_chat(self, text):
        if text == "fail":
            return "Twitch is not connected."
        self.sent.append(text)
        return ""

    def disconnect(self):
        pass


class FakeAutoMod:
    def __init__(self):
        self.resolved = []
        self.connected = []

    def connect(self, channel):
        self.connected.append(channel)

    def stop(self):
        self.connected.append("")

    def resolve(self, msg_id, allow):
        self.resolved.append((msg_id, allow))


class FakeAccount:
    logged_in = True
    login = "me"

    def logout(self):
        self.logged_in = False


@pytest.fixture
def hub(tmp_path):
    s = Settings(tmp_path / "s.json", tmp_path / "none.json")
    s.update({"channel": "mychan", "twitch_nick": "mychan"})
    h = Hub(s, twitch=FakeTwitch(), automod=FakeAutoMod(), account=FakeAccount(), clock=Clock())
    h.events = []
    h.subscribe(lambda e, d: h.events.append((e, d)))
    return h


def kinds(hub, event):
    return [d for e, d in hub.events if e == event]


def chat(hub, name="Bob", text="hi", **extra):
    hub.post({"src": "twitch", "kind": "chat", "name": name, "login": name.lower(), "text": text, **extra})
    hub.drain()


def test_chat_goes_to_tab_and_merge(hub):
    chat(hub)
    hub.post({"src": "tiktok", "kind": "chat", "name": "tok", "text": "yo"})
    hub.drain()
    assert len(hub.history["twitch"]) == 1
    assert len(hub.history["tiktok"]) == 1
    assert len(hub.history["merge"]) == 2
    msg = kinds(hub, "message")[0]
    assert msg["tabs"] == ["twitch", "merge"]
    assert msg["msg"]["u"]["name"] == "Bob"
    assert kinds(hub, "activity")


def test_same_chatter_keeps_color(hub):
    chat(hub)
    chat(hub, text="again")
    a, b = [m["msg"]["u"]["color"] for m in kinds(hub, "message")]
    assert a == b
    chat(hub, name="Other")
    assert kinds(hub, "message")[-1]["msg"]["u"]["color"] != a


def test_mention_marks_line(hub):
    chat(hub, text="hey @MyChan")
    assert kinds(hub, "message")[-1]["msg"]["mention"] is True


def test_moderator_delete_and_ban_remove_lines(hub):
    chat(hub, name="Bad", text="bad", id="m1")
    chat(hub, name="Bad", text="worse", id="m2")
    chat(hub, name="Good", text="ok", id="m3")
    hub.post({"src": "twitch", "kind": "delete", "id": "m1"})
    hub.drain()
    assert [m.twitch_id for m in hub.history["twitch"]] == ["m2", "m3"]
    hub.post({"src": "twitch", "kind": "clear_user", "login": "bad"})
    hub.drain()
    assert [m.twitch_id for m in hub.history["twitch"]] == ["m3"]
    assert [m.twitch_id for m in hub.history["merge"]] == ["m3"]
    assert len(kinds(hub, "remove")) == 2


def test_status_noise_is_not_a_chat_line(hub):
    hub.post({"src": "twitch", "kind": "status", "text": "Connected to #mychan"})
    hub.post({"src": "twitch", "kind": "status", "text": "Something odd happened"})
    hub.drain()
    assert hub.status["twitch"] == "Something odd happened"
    assert [m.text for m in hub.history["twitch"]] == ["Something odd happened"]


def test_warn_beeps_and_shows(hub):
    hub.post({"src": "tiktok", "kind": "warn", "text": "LIVE may end"})
    hub.drain()
    assert kinds(hub, "alert")
    assert hub.history["tiktok"][-1].kind == "warn"


def test_rename_emits_alias_and_persists(hub):
    chat(hub, name="LongName")
    hub.rename("longname", "Shorty", "LongName")
    assert hub.settings["name_aliases"] == {"longname": "Shorty"}
    assert kinds(hub, "alias")[-1] == {"key": "longname", "show": "Shorty"}
    assert hub.render(hub.history["twitch"][0])["u"]["show"] == "Shorty"
    hub.rename("longname", "", "LongName")
    assert hub.settings["name_aliases"] == {}
    assert kinds(hub, "alias")[-1] == {"key": "longname", "show": ""}


def test_send_chat_echoes_own_message(hub):
    assert hub.send_chat("/me dances") == ""
    assert hub.twitch.sent == ["/me dances"]
    last = hub.history["twitch"][-1]
    assert last.kind == "action"
    assert last.name == "me"
    assert hub.send_chat("fail") == "Twitch is not connected."


def test_points_redeem_uses_nickname(hub):
    hub.rename("viewer1", "V1")
    hub.post({"src": "twitch", "kind": "points", "info": {"login": "Viewer1", "name": "Viewer1", "reward": "Hydrate", "cost": 1500}})
    hub.drain()
    assert hub.history["twitch"][-1].text == "✨ V1 redeemed Hydrate (1,500 points)"


def held(i):
    return {"src": "twitch", "kind": "automod_hold", "held": {"id": f"h{i}", "name": f"U{i}", "text": "x"}}


def test_automod_cards_queue_and_expire(hub):
    for i in range(AUTOMOD_MAX_CARDS + 2):
        hub.post(held(i))
    hub.post(held(0))  # duplicate is ignored
    hub.drain()
    assert [c["id"] for c in hub.automod_cards] == ["h0", "h1"]
    assert len(hub.automod_waiting) == 2
    hub.clock.now += AUTOMOD_CARD_S + 1
    hub.tick()
    assert [c["id"] for c in hub.automod_cards] == ["h2", "h3"]
    assert hub.automod_waiting == []


def test_automod_resolve_flow(hub):
    hub.post(held(1))
    hub.drain()
    hub.automod_resolve("h1", True)
    assert hub.automod.resolved == [("h1", True)]
    assert hub.automod_cards[0]["busy"]
    hub.clock.now += AUTOMOD_CARD_S + 5
    hub.tick()
    assert hub.automod_cards, "busy cards don't expire"
    hub.post({"src": "twitch", "kind": "automod_result", "result": {"id": "h1", "allow": True, "ok": False, "error": "nope"}})
    hub.drain()
    assert hub.automod_cards == []
    assert "Couldn't allow U1's message: nope" in hub.history["twitch"][-1].text


def test_connect_clears_and_restarts(hub):
    chat(hub)
    hub.connect("twitch", "#NewChan")
    assert hub.settings["channel"] == "newchan"
    assert hub.twitch.connected == ["newchan"]
    assert hub.automod.connected == ["newchan"]
    assert len(hub.history["twitch"]) == 0


def test_snapshot_shape(hub):
    chat(hub)
    snap = hub.snapshot()
    assert set(snap) == {"tab", "status", "settings", "history"}
    assert len(snap["history"]["merge"]) == 1
