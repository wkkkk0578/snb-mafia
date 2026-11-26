# -*- coding: utf-8 -*-
"""S&B 마피아 — 바탕화면에 띄워 두고 동료들과 온라인으로 하는 마피아 게임.

연결: config.json 의 Supabase Realtime(Broadcast) 채널을 방 코드별로 쓴다 (먼작귀펫과 같은 방식).
진행: 방을 만든 사람(방장) PC가 사회자 역할 — 게임 엔진과 AI 플레이어가 방장 PC에서 돈다.
"""
import json
import os
import queue
import random
import secrets
import sys
import threading
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mafia_core as core  # noqa: E402
import desk as deskmod  # noqa: E402

VERSION = "0.9"
# 설정(닉네임·고유 ID·창 위치)은 PC마다 따로 — 게임 폴더를 압축해 나눠줘도 ID가 겹치지 않게
SETTINGS = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "SnBMafia", "settings.json")
OLD_SETTINGS = os.path.join(HERE, "settings.json")


def build_no():
    """자동 업데이트 버전 번호 (version.json). 없으면 0."""
    try:
        with open(os.path.join(HERE, "version.json"), encoding="utf-8") as f:
            return int(json.load(f).get("version", 0))
    except Exception:
        return 0


BUILD = build_no()


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def write_json(path, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


# ---------------------------------------------------------------- 연결 (먼작귀펫 Net 과 같은 프로토콜)

def ws_url(url, key):
    if url.startswith(("ws://", "wss://")):
        return url
    host = url.replace("https://", "").replace("http://", "").rstrip("/")
    return f"wss://{host}/realtime/v1/websocket?apikey={key}&vsn=1.0.0"


class Net:
    """Supabase Realtime Broadcast 채널 하나. 받은 메시지는 q 로, 끊기면 3초 뒤 다시 연결."""

    def __init__(self, url, key, room):
        self.url, self.topic = ws_url(url, key), "realtime:mafia-" + room
        self.q, self.ws, self.ready, self.alive, self.ref = queue.Queue(), None, False, True, 0
        self.lock = threading.Lock()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        import websocket
        while self.alive:
            self.ws = websocket.WebSocketApp(self.url, on_open=self._open, on_message=self._msg,
                                             on_error=lambda ws, e: self.q.put(("error", str(e))))
            self.ws.run_forever()
            self.ready = False
            if self.alive:
                self.q.put(("error", "연결 끊김 — 다시 연결 중"))
                time.sleep(3)

    def _raw(self, topic, event, payload):
        with self.lock:
            self.ref += 1
            self.ws.send(json.dumps({"topic": topic, "event": event, "payload": payload,
                                     "ref": str(self.ref), "join_ref": "1"}))

    def _open(self, ws):
        self.ref = 0
        self._raw(self.topic, "phx_join", {"config": {"broadcast": {"ack": False, "self": False},
                                                       "presence": {"enabled": False}, "private": False}})

    def _msg(self, ws, raw):
        try:
            m = json.loads(raw)
        except ValueError:
            return
        if m.get("topic") != self.topic:
            return
        p = m.get("payload") or {}
        ev = m.get("event")
        if ev == "phx_reply" and m.get("ref") == "1":
            self.ready = p.get("status") == "ok"
            self.q.put(("open", None) if self.ready else ("error", str(p.get("response"))))
        elif ev == "broadcast" and isinstance(p.get("payload"), dict):
            self.q.put(("msg", p["payload"]))
        elif ev in ("phx_error", "phx_close"):
            self.ready = False
            self.q.put(("error", ev))

    def send(self, msg):
        if self.ready:
            try:
                self._raw(self.topic, "broadcast", {"type": "broadcast", "event": "m", "payload": msg})
            except Exception:
                pass

    def heartbeat(self):
        try:
            self.ws and self.ws.sock and self.ws.sock.connected and self._raw("phoenix", "heartbeat", {})
        except Exception:
            pass

    def close(self):
        self.alive = False
        try:
            self.ws and self.ws.close()
        except Exception:
            pass


class Solo:
    """혼자 연습 (AI 들과만)."""
    def __init__(self):
        self.q, self.ready = queue.Queue(), True

    def send(self, msg):
        pass

    def heartbeat(self):
        pass

    def close(self):
        pass


# ---------------------------------------------------------------- 방장 (사회자)

class Host:
    """대기실 인원 관리 + 게임 진행 + 플레이어별 화면 정보 방송."""

    def __init__(self, me, nick, net, stylist=None):
        self.me, self.net, self.stylist = me, net, stylist
        self.chars = deskmod.available_chars() or list(deskmod.ALL_CHARS)
        self.members = {me: dict(nick=nick, seen=time.time(), ch=core.pick_chars(1, self.chars)[0])}
        self.order = [me]
        self.total = 6
        self.game, self.gid = None, ""
        self.last_lobby, self.sent_ver, self.last_views = 0, -1, 0
        self.lobby_chat = []
        self.notices = []

    def humans(self):
        return [m for m in self.order if m in self.members]

    def nick_of(self, mid):
        return self.members.get(mid, {}).get("nick", "?")

    def _unique(self, mid, nick):
        nick = (nick or "손님").strip()[:10] or "손님"
        taken = {v["nick"] for k, v in self.members.items() if k != mid}
        base, k = nick, 2
        while nick in taken:
            nick, k = f"{base}{k}", k + 1
        return nick

    def handle(self, m):
        t, mid = m.get("t"), m.get("id")
        if not mid or mid == self.me:
            return
        if t == "hi":
            if mid not in self.members and m.get("build") is not None and m.get("build") != BUILD:
                self.notices.append(f"⚠ {str(m.get('nick', '?'))[:10]}님은 버전이 달라요 (상대 v{m.get('build')} / 나 v{BUILD}). "
                                    "둘 다 껐다 켜면 최신으로 맞춰져요.")
            if mid not in self.members:
                used = {v.get("ch") for v in self.members.values()}
                self.members[mid] = dict(nick=self._unique(mid, m.get("nick")), seen=time.time(),
                                         ch=core.pick_chars(1, self.chars, used)[0])
                self.order.append(mid)
                self.last_lobby = 0
            self.members[mid]["seen"] = time.time()
        elif t == "bye":
            if mid in self.members and not (self.game and mid in self.game.players):
                self.members.pop(mid, None)
                self.last_lobby = 0
        elif t == "act" and self.game:
            self.act(mid, m.get("k"), m.get("target"), m.get("text"))
        elif t == "lchat" and mid in self.members:
            pass  # 대기실 채팅은 모두에게 직접 방송됨

    def act(self, pid, kind, target=None, text=None):
        g = self.game
        if not g:
            return
        if kind == "night":
            g.night_action(pid, target)
        elif kind == "vote":
            g.vote(pid, target)
        elif kind == "ready":
            g.set_ready(pid)
        elif kind == "judge":
            g.judge_vote(pid, bool(target))
        elif kind == "say":
            g.say(pid, text)

    def start(self):
        hs = self.humans()
        total = max(self.total, len(hs), core.MIN_PLAYERS)
        mem = [dict(id=h, name=self.nick_of(h), ai=False, ch=self.members[h].get("ch")) for h in hs]
        mem += core.ai_members(total - len(hs), {m["name"] for m in mem}, chars=self.chars,
                               used_chars={m["ch"] for m in mem})
        random.shuffle(mem)
        self.gid = uuid.uuid4().hex[:8]
        self.game = core.Game(mem, now=time.time(), stylist=self.stylist)
        self.sent_ver = -1
        self.last_lobby = 0

    def back_to_lobby(self):
        self.game, self.gid = None, ""
        self.last_lobby = 0

    def lobby_msg(self):
        return dict(t="lobby", host=self.me, gid=self.gid, started=bool(self.game), total=self.total, build=BUILD,
                    members=[dict(id=h, nick=self.nick_of(h), ch=self.members[h].get("ch")) for h in self.humans()],
                    plan=core.plan_text(max(self.total, len(self.humans()), core.MIN_PLAYERS)),
                    llm=bool(self.stylist))

    def tick(self, now):
        """→ 방장 화면에 쓸 (lobby, view)."""
        # 대기실에서 10초 넘게 소식 없는 사람 정리
        if not self.game:
            for mid in [k for k, v in self.members.items() if k != self.me and now - v["seen"] > 12]:
                self.members.pop(mid, None)
                self.last_lobby = 0
        if now - self.last_lobby > 2.0:
            self.last_lobby = now
            self.net.send(self.lobby_msg())
        g = self.game
        st = getattr(self.stylist, "status", "") if self.stylist else ""
        if st and st != getattr(self, "_llm_told", ""):
            self._llm_told = st
            self.notices.append("🤖 Claude 연결 OK — AI 가 상황을 보고 말해요." if st == "ok"
                                else f"🤖 Claude 연결 문제: {st} → 기본 대사로 진행해요. (시작 화면 맨 아래 '🤖 AI 대화'에서 키를 다시 넣어 주세요)")
        if g:
            g.tick(now)
            changed = g.version != self.sent_ver
            if changed or now - self.last_views > 2.5:
                self.sent_ver, self.last_views = g.version, now
                for h in self.humans():
                    if h != self.me and h in g.players:
                        self.net.send(dict(t="view", to=h, gid=self.gid, v=g.view(h, now)))
                if any(h not in g.players for h in self.humans()):
                    self.net.send(dict(t="view", to="*", gid=self.gid, v=g.view(None, now)))
        return self.lobby_msg(), (g.view(self.me, now) if g else None)


# ---------------------------------------------------------------- 화면

BG, PANEL, LINE, TXT, SUB = "#16181f", "#20232d", "#2c3040", "#eef0f6", "#9097a8"
ACCENT, RED, GOLD, NIGHT = "#7aa2ff", "#ff5d6c", "#ffd166", "#24264a"
PHASE_TXT = {"intro": "🎲 직업 확인", "selfintro": "🙋 자기소개", "night": "🌙 밤", "morning": "🌅 아침", "day": "☀ 낮 토론",
             "vote": "🗳 투표", "defense": "🎤 최후 변론", "judge": "👍👎 찬반 투표", "verdict": "⚖ 결과",
             "end": "🏁 게임 끝"}
PHASE_BG = {"night": NIGHT, "intro": "#2b2340", "selfintro": "#2b3a2f", "morning": "#3a2f25", "day": "#24303f",
            "vote": "#3a2430", "defense": "#45311f", "judge": "#3a2430", "verdict": "#3a2430", "end": "#23352b"}


class App:
    def __init__(self):
        import tkinter as tk
        from tkinter import font as tkfont
        self.tk = tk
        self.cfg = read_json(os.path.join(HERE, "config.json"), {})
        self.st = read_json(SETTINGS, None)
        if self.st is None:                      # 옛 위치(게임 폴더)의 설정은 닉네임 등만 가져오고 ID는 새로
            self.st = {k: v for k, v in read_json(OLD_SETTINGS, {}).items() if k != "id"}
        self.root = tk.Tk()
        self.root.title("S&B 마피아")
        self.root.configure(bg=BG)
        try:
            self.S = max(1.0, self.root.winfo_fpixels("1i") / 96)
        except Exception:
            self.S = 1.0
        fams = set(tkfont.families())
        self.FF = next((f for f in ("맑은 고딕", "Malgun Gothic", "Noto Sans CJK KR", "NanumGothic") if f in fams), "TkDefaultFont")
        self.EF = "Segoe UI Emoji" if "Segoe UI Emoji" in fams else self.FF
        w, h = self.px(440), self.px(760)
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        x, y = self.st.get("x", sw - w - self.px(24)), self.st.get("y", max(0, sh - h - self.px(70)))
        x, y = min(max(0, x), sw - 100), min(max(0, y), sh - 100)
        self.root.geometry(f"{w}x{h}+{x}+{y}")
        self.root.minsize(self.px(400), self.px(560))
        self.pinned = self.st.get("pin", True)
        self.root.attributes("-topmost", self.pinned)
        self.root.protocol("WM_DELETE_WINDOW", self.quit)
        self.me = self.st.get("id") or uuid.uuid4().hex[:10]
        self.st["id"] = self.me
        self.net = self.host = None
        self.lobby, self.view, self.gid, self.last_seq = None, None, "", 0
        self.view_at, self.last_hi, self.last_hb, self.last_from_host = 0, 0, 0, 0
        self.status = ("", SUB)
        self.room = ""
        self.cells = []
        self.hover = None
        self._last_draw = None
        self.desk = deskmod.Desk(self.root, on_click=self.desk_click)
        self._desk_key = None
        self.prev_phase, self.prev_alive = None, {}
        self._stop_job = None
        self._rel = {}
        self.desk.refocus = self.refocus
        # 먼작귀펫처럼: 입력칸이 비었을 때 ← → 걷기 · Shift+← → 달리기 · ↑ / Space 점프.
        # bind_all → 투명 창이 포커스를 잠깐 가져가도 키가 들어온다
        for d, k in ((-1, "Left"), (1, "Right")):
            self.root.bind_all(f"<KeyPress-{k}>", lambda e, d=d: self.key_walk(d, True, e))
            self.root.bind_all(f"<KeyRelease-{k}>", lambda e, d=d: self.key_walk(d, False, e))
        self.root.bind_all("<KeyPress-Up>", lambda e: self.key_jump())
        self.build_start()
        self.root.after(200, self.loop)
        self.new_build = None
        self.root.after(5000, self.check_update)

    def check_update(self):
        """켜 둔 동안 20분마다 새 버전이 나왔는지 본다 (받는 건 다음에 켤 때)."""
        def run():
            try:
                import launcher
                base, man = launcher.remote_manifest(self.cfg)
                if man and int(man.get("version", 0)) > BUILD:
                    self.new_build = int(man["version"])
            except Exception:
                pass
        threading.Thread(target=run, daemon=True).start()
        self.root.after(20 * 60 * 1000, self.check_update)

    def px(self, v):
        return int(v * self.S)

    def f(self, size, bold=False):
        return (self.FF, size, "bold" if bold else "normal")

    # ------------------------------------------------ 시작 화면
    def clear(self):
        for w in self.root.winfo_children():
            w.destroy()

    def build_start(self):
        tk = self.tk
        self.clear()
        fr = tk.Frame(self.root, bg=BG)
        fr.pack(fill="both", expand=True, padx=self.px(28), pady=self.px(26))
        tk.Label(fr, text="🔪", bg=BG, fg=TXT, font=(self.EF, 40)).pack(pady=(self.px(20), 0))
        tk.Label(fr, text="S&B 마피아", bg=BG, fg=TXT, font=self.f(22, True)).pack()
        tk.Label(fr, text="동료 + 페르소나 AI 들과 함께하는 마피아", bg=BG, fg=SUB, font=self.f(10)).pack(pady=(0, self.px(22)))

        def field(label, val):
            tk.Label(fr, text=label, bg=BG, fg=SUB, font=self.f(10), anchor="w").pack(fill="x")
            e = tk.Entry(fr, font=self.f(13), bg=PANEL, fg=TXT, insertbackground=TXT, relief="flat",
                         highlightthickness=1, highlightbackground=LINE, highlightcolor=ACCENT)
            e.insert(0, val)
            e.pack(fill="x", ipady=self.px(6), pady=(self.px(2), self.px(12)))
            return e
        self.e_nick = field("닉네임", self.st.get("nick", ""))
        self.e_room = field("방 코드 (참가할 때 입력 / 만들 때 비워두면 자동 생성)", self.st.get("room", ""))
        online = bool(self.cfg.get("url") and self.cfg.get("key"))
        self.btn(fr, "방 만들기 (내가 방장)", lambda: self.enter("host"), ACCENT, state=online).pack(fill="x", pady=self.px(3))
        self.btn(fr, "참가하기", lambda: self.enter("join"), "#4a5170", state=online).pack(fill="x", pady=self.px(3))
        self.btn(fr, "혼자 연습 (AI 들과만)", lambda: self.enter("solo"), "#3a3f52").pack(fill="x", pady=self.px(3))
        if not online:
            tk.Label(fr, text="config.json 에 서버 정보가 없어 온라인은 꺼져 있어요.", bg=BG, fg=RED,
                     font=self.f(9)).pack(pady=self.px(6))
        key = self.api_key()
        llm = (f"🤖 AI 대화: Claude 켜짐 (키 …{key[-4:]})  ·  클릭해서 변경" if key
               else "🤖 AI 대화: 기본 대사  ·  클릭해서 Claude API 키 넣기 (방장만 필요)")
        lk = tk.Label(fr, text=llm, bg=BG, fg=GOLD if key else SUB, font=self.f(9), cursor="hand2")
        lk.pack(side="bottom", pady=(self.px(4), 0))
        lk.bind("<Button-1>", lambda e: self.ask_key())
        upd = os.environ.get("SNB_UPDATED")
        tk.Label(fr, text=(f"✨ 새 버전으로 업데이트됨 (v{BUILD})" if upd else f"v{VERSION} · 빌드 {BUILD}"),
                 bg=BG, fg=GOLD if upd else "#555b6e", font=self.f(9 if upd else 8)).pack(side="bottom")
        self.e_nick.focus_set()
        self.e_nick.bind("<Return>", lambda e: self.e_room.focus_set())

    def api_key(self):
        """Claude API 키 — 이 PC 설정(%APPDATA%)에만 저장. config.json 에 적어 둔 것도 읽음."""
        return core.clean_key(self.st.get("claude_api_key") or self.cfg.get("claude_api_key") or "")

    def ask_key(self):
        from tkinter import simpledialog
        k = simpledialog.askstring(
            "Claude API 키",
            "방장 PC에만 넣으면 AI 들이 Claude 로 대화해요.\n"
            "키는 이 PC 설정에만 저장되고, 게임 폴더를 공유해도 같이 가지 않아요.\n"
            "(console.anthropic.com → API Keys 에서 발급 · 비우고 확인하면 끔)",
            initialvalue=self.st.get("claude_api_key", ""), show="*", parent=self.root)
        if k is None:
            return
        k = core.clean_key(k)
        self.st["claude_api_key"] = k
        write_json(SETTINGS, self.st)
        if k:
            self.test_key_ui(k)
        nick, room = self.e_nick.get(), self.e_room.get()
        self.build_start()
        self.e_nick.delete(0, "end")
        self.e_nick.insert(0, nick)
        self.e_room.delete(0, "end")
        self.e_room.insert(0, room)

    def test_key_ui(self, k):
        """넣은 키로 바로 한 번 연결해 보고 결과를 알려줌."""
        from tkinter import messagebox
        res = {}

        def run():
            res["v"] = core.test_key(k, self.cfg.get("claude_model", "claude-haiku-5-5"))
        th = threading.Thread(target=run, daemon=True)
        th.start()

        def wait():
            if th.is_alive():
                self.root.after(150, wait)
                return
            ok, msg = res.get("v", (False, "확인 실패"))
            (messagebox.showinfo if ok else messagebox.showwarning)("Claude 연결 확인", msg, parent=self.root)
        self.root.after(150, wait)

    def btn(self, parent, text, cmd, color, state=True, small=False):
        tk = self.tk
        b = tk.Button(parent, text=text, command=cmd, bg=color, fg="white", activebackground=color,
                      activeforeground="white", relief="flat", bd=0, cursor="hand2",
                      font=self.f(10 if small else 12, True), padx=self.px(10), pady=self.px(3 if small else 7),
                      state="normal" if state else "disabled", disabledforeground="#777")
        return b

    def enter(self, mode):
        nick = self.e_nick.get().strip()[:10]
        if not nick:
            self.e_nick.configure(highlightbackground=RED)
            return
        room = "".join(ch for ch in self.e_room.get().strip().upper() if ch.isalnum())[:12]
        if mode == "join" and not room:
            self.e_room.configure(highlightbackground=RED)
            return
        if mode == "host" and not room:
            room = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(5))
        self.st.update(nick=nick, room=room if mode != "solo" else self.st.get("room", ""))
        write_json(SETTINGS, self.st)
        self.nick, self.mode = nick, mode
        self.room = room if mode != "solo" else "연습"
        if mode == "solo":
            self.net = Solo()
        else:
            self.net = Net(self.cfg["url"], self.cfg["key"], room)
        stylist = None
        if mode != "join" and self.api_key():
            stylist = core.make_claude_stylist(self.api_key(), self.cfg.get("claude_model", "claude-haiku-5-5"))
        self.host = Host(self.me, nick, self.net, stylist) if mode != "join" else None
        if mode == "solo":
            self.host.total = 8
        self.lobby, self.view, self.gid, self.last_seq = None, None, "", 0
        self.lobby_lines = []
        self.last_from_host = time.time()
        self.status = ("연결 중…", GOLD) if mode != "solo" else ("혼자 연습", SUB)
        self.build_game()

    # ------------------------------------------------ 게임 화면
    def build_game(self):
        tk = self.tk
        self.clear()
        self._last_draw = None
        top = tk.Frame(self.root, bg=PANEL)
        top.pack(fill="x")
        self.l_phase = tk.Label(top, text="대기실", bg=PANEL, fg=TXT, font=self.f(15, True), anchor="w")
        self.l_phase.pack(side="left", padx=self.px(12), pady=self.px(8))
        self.l_timer = tk.Label(top, text="", bg=PANEL, fg=GOLD, font=self.f(15, True))
        self.l_timer.pack(side="right", padx=self.px(12))
        bar = tk.Frame(self.root, bg=BG)
        bar.pack(fill="x", padx=self.px(10), pady=(self.px(6), 0))
        self.l_room = tk.Label(bar, text="", bg=BG, fg=SUB, font=self.f(9), anchor="w", cursor="hand2")
        self.l_room.pack(side="left")
        self.l_room.bind("<Button-1>", lambda e: self.copy_room())
        tk.Button(bar, text="나가기", command=self.leave, bg=BG, fg=SUB, relief="flat", bd=0, font=self.f(9),
                  cursor="hand2", activebackground=BG).pack(side="right")
        self.b_pin = tk.Button(bar, text="", command=self.toggle_pin, bg=BG, fg=SUB, relief="flat", bd=0,
                               font=self.f(9), cursor="hand2", activebackground=BG)
        self.b_pin.pack(side="right", padx=self.px(6))
        self.b_size = tk.Button(bar, text="", command=self.cycle_size, bg=BG, fg=SUB, relief="flat", bd=0,
                                font=self.f(9), cursor="hand2", activebackground=BG)
        self.b_size.pack(side="right", padx=self.px(6))
        self.toggle_pin(False)

        self.role_fr = tk.Frame(self.root, bg=PANEL, highlightthickness=1, highlightbackground=LINE)
        self.role_fr.pack(fill="x", padx=self.px(10), pady=self.px(6))
        self.l_role = tk.Label(self.role_fr, text="", bg=PANEL, fg=TXT, font=self.f(12, True), anchor="w")
        self.l_role.pack(fill="x", padx=self.px(10), pady=(self.px(6), 0))
        self.l_desc = tk.Label(self.role_fr, text="", bg=PANEL, fg=SUB, font=self.f(9), anchor="w",
                               justify="left", wraplength=self.px(390))
        self.l_desc.pack(fill="x", padx=self.px(10), pady=(0, self.px(6)))

        self.cv = tk.Canvas(self.root, bg=BG, highlightthickness=0, height=self.px(200))
        self.cv.pack(fill="x", padx=self.px(6))
        self.cv.bind("<Button-1>", self.on_click)
        self.cv.bind("<Motion>", self.on_motion)
        self.cv.bind("<Configure>", lambda e: self.redraw(force=True))

        self.l_hint = tk.Label(self.root, text="", bg=BG, fg=GOLD, font=self.f(10, True), wraplength=self.px(410))
        self.l_hint.pack(fill="x", padx=self.px(10))
        self.act_fr = tk.Frame(self.root, bg=BG)
        self.act_fr.pack(fill="x", padx=self.px(10), pady=self.px(4))
        self._act_key = None

        # 입력칸을 먼저 아래에 고정 → 창이 작아도 채팅 기록 쪽만 줄어든다
        self.l_chat = tk.Label(self.root, text="", bg=BG, fg=SUB, font=self.f(9), anchor="w")
        self.l_chat.pack(side="bottom", fill="x", padx=self.px(12), pady=(0, self.px(6)))
        inp = tk.Frame(self.root, bg=BG)
        inp.pack(side="bottom", fill="x", padx=self.px(10), pady=(self.px(8), self.px(2)))
        chat = tk.Frame(self.root, bg=PANEL)
        chat.pack(fill="both", expand=True, padx=self.px(10), pady=(self.px(2), 0))
        self.txt = tk.Text(chat, bg=PANEL, fg=TXT, font=self.f(10), relief="flat", wrap="char", height=4,
                           padx=self.px(8), pady=self.px(6), state="disabled", cursor="arrow", spacing1=self.px(2))
        sb = tk.Scrollbar(chat, command=self.txt.yview)
        self.txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.txt.pack(side="left", fill="both", expand=True)
        self.txt.tag_configure("sys", foreground="#a9b8ff")
        self.txt.tag_configure("priv", foreground=GOLD)
        self.txt.tag_configure("mafia", foreground="#ff8a8a")
        self.txt.tag_configure("dead", foreground="#8a8f9c")
        self.txt.tag_configure("me", foreground="#ffe9a8")
        self.txt.tag_configure("name", font=self.f(10, True))
        for txt, d in (("◀", -1), ("▶", 1)):
            b = self.btn(inp, txt, None, "#3a3f52", small=True)
            b.configure(command=lambda: None, padx=self.px(6))
            b.bind("<ButtonPress-1>", lambda e, d=d: self.desk.walk(d))
            b.bind("<ButtonRelease-1>", lambda e: self.desk.stop())
            b.pack(side="left", padx=(0, self.px(3)))
        bj = self.btn(inp, "⤒", lambda: self.desk.jump(), "#3a3f52", small=True)
        bj.configure(padx=self.px(6))
        bj.pack(side="left", padx=(0, self.px(6)))
        self.e_chat = tk.Entry(inp, font=self.f(11), bg=PANEL, fg=TXT, insertbackground=TXT, relief="flat",
                               highlightthickness=1, highlightbackground=LINE, highlightcolor=ACCENT)
        self.e_chat.pack(side="left", fill="x", expand=True, ipady=self.px(5))
        self.e_chat.bind("<Return>", lambda e: self.send_chat())
        self.e_chat.bind("<KeyPress-space>", lambda e: self.key_jump())
        self.b_send = self.btn(inp, "전송", self.send_chat, ACCENT, small=True)
        self.b_send.pack(side="right", padx=(self.px(6), 0))
        self._chat_mode = None
        self.e_chat.focus_set()
        self.update_room_label()
        self.apply_size()

    SIZES = ("big", "small", "hide")
    SIZE_TXT = {"big": "▣ 목록 크게", "small": "▤ 목록 작게", "hide": "▢ 목록 숨김"}

    def cycle_size(self):
        cur = self.st.get("size", "big")
        self.st["size"] = self.SIZES[(self.SIZES.index(cur) + 1) % 3] if cur in self.SIZES else "small"
        write_json(SETTINGS, self.st)
        self.apply_size()

    def apply_size(self):
        """참가자 목록 크기: 크게(설명+큰 아바타) / 작게(한 줄 직업 + 작은 아바타) / 숨김(바탕화면 캐릭터로만)."""
        size = self.st.get("size", "big")
        self.b_size.configure(text=self.SIZE_TXT.get(size, self.SIZE_TXT["big"]))
        if size == "big":
            self.l_desc.pack(fill="x", padx=self.px(10), pady=(0, self.px(6)))
            self.l_role.configure(font=self.f(12, True))
        else:
            self.l_desc.pack_forget()
            self.l_role.configure(font=self.f(10, True))
            self.l_role.pack_configure(pady=self.px(4))
        if size == "big":
            self.l_role.pack_configure(pady=(self.px(6), 0))
        if size == "hide":
            self.cv.pack_forget()
        elif not self.cv.winfo_ismapped():
            self.cv.pack(fill="x", padx=self.px(6), after=self.role_fr)
        self._last_draw = None
        self.redraw(force=True)

    def toggle_pin(self, flip=True):
        if flip:
            self.pinned = not self.pinned
            self.st["pin"] = self.pinned
            self.root.attributes("-topmost", self.pinned)
        self.b_pin.configure(text="📌 항상 위" if self.pinned else "📍 고정 해제됨")

    def copy_room(self):
        if self.mode != "solo":
            self.root.clipboard_clear()
            self.root.clipboard_append(self.room)
            self.add_line("sys", f"방 코드 {self.room} 를 복사했어요. 동료에게 보내 주세요.")

    def update_room_label(self):
        txt, col = self.status
        if self.new_build and not getattr(self, "_told_new", False):
            self._told_new = True
            self.add_line("priv", f"✨ 새 버전(v{self.new_build})이 나왔어요. 판이 끝나면 껐다 켜 주세요.")
        if self.mode == "solo":
            self.l_room.configure(text="🧪 혼자 연습 모드", fg=SUB)
        else:
            dot = "🟢" if self.net and self.net.ready else "🔴"
            self.l_room.configure(text=f"{dot} 방 코드 {self.room}  (클릭해서 복사)   {txt}", fg=col)

    # ------------------------------------------------ 채팅
    def add_line(self, kind, text, name="", mine=False):
        t = self.txt
        t.configure(state="normal")
        tag = {"sys": "sys", "mafia": "mafia", "dead": "dead", "priv": "priv"}.get(kind)
        if kind == "mafia":
            t.insert("end", "[마피아] ", ("mafia", "name"))
        if kind == "dead":
            t.insert("end", "[유령] ", ("dead", "name"))
        if name:
            t.insert("end", name + "  ", ("name", "me" if mine else (tag or "")))
        t.insert("end", text + "\n", ("me" if mine else tag) or ())
        t.configure(state="disabled")
        t.see("end")

    def chat_mode(self):
        """→ (입력 가능?, 안내 문구)"""
        v = self.view
        if not v:
            return True, "💬 대기실 채팅"
        me = v.get("me")
        if not me:
            return False, "👀 관전 중에는 채팅할 수 없어요"
        if v["phase"] == "end":
            return True, "💬 전체 채팅"
        if not me["alive"]:
            return True, "👻 유령 채팅 — 죽은 사람끼리만 보여요"
        if v["phase"] == "night":
            if me.get("mchat"):
                return True, "🔪 마피아 채팅 — 마피아 팀만 보여요"
            return False, "🌙 밤에는 말할 수 없어요 (마피아만 몰래 대화)"
        if v["phase"] == "defense":
            if me.get("accused"):
                return True, "🎤 당신의 최후 변론! 지금은 당신만 말할 수 있어요"
            return False, f"🎤 {v.get('accused', '')}님 최후 변론 중 — 들어 주세요"
        return True, "💬 전체 채팅 — AI 이름을 부르거나 질문하면 AI 가 바로 대답해요"

    def update_chat_box(self):
        ok, msg = self.chat_mode()
        if (ok, msg) == self._chat_mode:
            return
        self._chat_mode = (ok, msg)
        self.l_chat.configure(text=msg, fg=SUB if ok else GOLD)
        if ok:
            self.e_chat.configure(state="normal", bg=PANEL)
            self.b_send.configure(state="normal")
            self.e_chat.focus_set()
        else:
            self.e_chat.configure(state="disabled", disabledbackground="#1c1e26")
            self.b_send.configure(state="disabled")

    def send_chat(self):
        if not self.chat_mode()[0]:
            return
        text = self.e_chat.get().strip()
        if not text:
            return
        self.e_chat.delete(0, "end")
        if self.view and self.lobby and self.lobby.get("started"):
            self.do_act("say", text=text)
        else:   # 대기실 채팅
            self.net.send(dict(t="lchat", id=self.me, nick=self.nick, x=text[:200]))
            self.add_line("say", text, self.nick, True)
            self.desk.say(self.me, text[:200])

    def do_act(self, kind, target=None, text=None):
        if self.host:
            self.host.act(self.me, kind, target, text)
        else:
            self.net.send(dict(t="act", id=self.me, k=kind, target=target, text=text))

    # ------------------------------------------------ 루프
    def loop(self):
        try:
            self.step()
        except Exception as e:  # 화면이 멈추지 않게
            import traceback
            traceback.print_exc()
        self.root.after(150, self.loop)

    def step(self):
        if not self.net:
            return
        now = time.time()
        while True:
            try:
                kind, m = self.net.q.get_nowait()
            except queue.Empty:
                break
            if kind == "open":
                self.status = ("연결됨", SUB)
            elif kind == "error":
                self.status = (f"연결 문제: {m}"[:40], RED)
            elif kind == "msg":
                self.on_msg(m, now)
        if now - self.last_hb > 20:
            self.last_hb = now
            self.net.heartbeat()
        if self.host:
            while self.host.notices:
                self.add_line("priv", self.host.notices.pop(0))
            self.lobby, v = self.host.tick(now)
            if v is not None:
                self.set_view(self.host.gid, v, now)
            elif self.view is not None:
                self.view = None
                self.gid = ""
        else:
            if now - self.last_hi > 2:
                self.last_hi = now
                self.net.send(dict(t="hi", id=self.me, nick=self.nick, ver=VERSION, build=BUILD))
            if self.lobby and now - self.last_from_host > 10:
                self.status = ("방장 연결이 끊긴 것 같아요…", RED)
            elif not self.lobby and now - self.last_from_host > 8 and self.net.ready:
                self.status = ("이 방 코드에 방장이 없어요. 코드를 확인하세요.", RED)
        self.update_room_label()
        self.update_chat_box()
        self.sync_desk()
        ps = self.desk.take_pos(now)
        if ps:
            self.net.send(dict(t="pos", ps=ps))
        self.redraw()

    def on_msg(self, m, now):
        t = m.get("t")
        if t == "pos":
            self.desk.on_pos(m.get("ps"))
            return
        if t == "lchat" and not (self.lobby or {}).get("started"):
            self.desk.say(m.get("id"), str(m.get("x", ""))[:200])
        if self.host:
            if t == "lchat":
                self.add_line("say", str(m.get("x", ""))[:200], str(m.get("nick", "?"))[:10])
            else:
                self.host.handle(m)
            return
        if t == "lobby":
            if self.lobby and m.get("host") != self.lobby.get("host") and now - self.last_from_host < 6:
                return          # 다른 방장(중복 방) 무시
            self.lobby, self.last_from_host = m, now
            if m.get("build") is not None and m.get("build") != BUILD and not getattr(self, "_warned_build", False):
                self._warned_build = True
                self.add_line("priv", f"⚠ 방장과 버전이 달라요 (방장 v{m.get('build')} / 나 v{BUILD}). "
                                      "껐다 켜면 최신으로 맞춰져요.")
            if self.status[1] == RED:
                self.status = ("연결됨", SUB)
            if not m.get("started") and self.view is not None:
                self.view, self.gid = None, ""
                self.add_line("sys", "— 대기실로 돌아왔어요 —")
        elif t == "view":
            to, gid = m.get("to"), m.get("gid")
            if to == self.me:
                self.personal = gid
            elif to != "*" or getattr(self, "personal", None) == gid:
                return
            self.last_from_host = now
            self.set_view(gid, m.get("v"), now)
        elif t == "lchat" and not (self.lobby or {}).get("started"):
            self.add_line("say", str(m.get("x", ""))[:200], str(m.get("nick", "?"))[:10])
        elif t == "bye" and self.lobby and m.get("id") == self.lobby.get("host"):
            self.status = ("방장이 나갔어요.", RED)

    def set_view(self, gid, v, now):
        if not isinstance(v, dict):
            return
        fresh = gid != self.gid
        if fresh:
            self.gid, self.last_seq = gid, 0
            self.txt.configure(state="normal")
            self.txt.delete("1.0", "end")
            self.txt.configure(state="disabled")
            self.prev_phase, self.prev_alive = v.get("phase"), {p["id"]: p["a"] for p in v["players"]}
        first = self.last_seq == 0
        self.view, self.view_at = v, now
        for c in v.get("chat", []):
            if c["s"] > self.last_seq:
                self.last_seq = c["s"]
                k = c["k"]
                if k == "sys":
                    self.add_line("priv" if c.get("p") else "sys", c["x"])
                else:
                    self.add_line(k, c["x"], c["n"], c.get("me"))
                    if not first and c.get("w"):
                        self.desk.say(c["w"], c["x"], k)
        self.phase_effects(v)

    # ------------------------------------------------ 그리기
    def layout(self, n):
        W = max(self.cv.winfo_width(), self.px(380))
        if self.st.get("size") == "small":
            cols = 6
            ch = self.px(50)
        else:
            cols = 4 if n > 6 or n == 4 else 3
            ch = self.px(96)
        cw = W / cols
        rows = (n + cols - 1) // cols
        return cols, cw, ch, rows

    def redraw(self, force=False):
        if not hasattr(self, "cv"):
            return
        v, lb = self.view, self.lobby
        now = time.time()
        if v:
            remain = max(0, int(v["remain"] - (now - self.view_at)))
            self.l_timer.configure(text=f"{remain // 60}:{remain % 60:02d}" if v["phase"] != "end" else "")
            ph = v["phase"]
            day = f" {v['day']}" if ph in ("night", "day", "vote") and v["day"] else ""
            label = PHASE_TXT.get(ph, ph) + day
            if ph == "end":
                label = "🎉 시민 팀 승리!" if v["winner"] == "시민" else "😈 마피아 팀 승리!"
            self.l_phase.configure(text=label)
            bgc = PHASE_BG.get(ph, PANEL)
            self.l_phase.master.configure(bg=bgc)
            self.l_phase.configure(bg=bgc)
            self.l_timer.configure(bg=bgc)
        else:
            self.l_timer.configure(text="")
            self.l_phase.configure(text="대기실", bg=PANEL)
            self.l_phase.master.configure(bg=PANEL)
            self.l_timer.configure(bg=PANEL)
        key = (json.dumps(v, sort_keys=True) if v else None, json.dumps(lb, sort_keys=True) if lb else None,
               self.hover, self.cv.winfo_width())
        if key == self._last_draw and not force:
            return
        self._last_draw = key
        self.draw_role()
        self.draw_players()
        self.draw_actions()

    def draw_role(self):
        v = self.view
        me = (v or {}).get("me")
        if me:
            icon = core.ROLE_ICON.get(me["role"], "")
            team = "마피아 팀" if me["team"] == "mafia" else "시민 팀"
            dead = "  ·  💀 사망 (관전 중)" if not me["alive"] else ""
            self.l_role.configure(text=f"{icon} 내 직업: {me['role']}   ({team}){dead}",
                                  fg=RED if me["team"] == "mafia" else TXT)
            self.l_desc.configure(text=me["desc"])
        elif v:
            self.l_role.configure(text="👀 관전 중", fg=TXT)
            self.l_desc.configure(text="이번 판이 끝나면 다음 판부터 참가할 수 있어요.")
        else:
            lb = self.lobby
            if self.host:
                txt = "👑 내가 방장이에요. 인원을 정하고 시작하세요."
            elif lb:
                txt = "방장이 게임을 시작하길 기다리는 중…"
            else:
                txt = "방을 찾는 중…"
            self.l_role.configure(text=txt, fg=TXT)
            plan = lb.get("plan", "") if lb else ""
            llm = "  ·  AI 말투: Claude" if lb and lb.get("llm") else ""
            self.l_desc.configure(text=(f"이번 판 직업: {plan}{llm}" if plan else ""))

    def draw_players(self):
        cv = self.cv
        cv.delete("all")
        v, lb = self.view, self.lobby
        rows = []
        if v:
            rows = v["players"]
        elif lb:
            mem = lb.get("members", [])
            total = max(lb.get("total", 0), len(mem), core.MIN_PLAYERS)
            rows = [dict(id=m["id"], n=m["nick"], ai=False, a=True, c=core.COLORS[i % 12], r=None, tag=None,
                         host=m["id"] == lb.get("host")) for i, m in enumerate(mem)]
            rows += [dict(id=None, n="AI", ai=True, a=True, c="#3a3f52", r=None, tag=None, ghost=True)
                     for _ in range(total - len(mem))]
        n = len(rows)
        if not n:
            cv.configure(height=self.px(40))
            return
        cols, cw, ch, nrow = self.layout(n)
        cv.configure(height=int(nrow * ch + self.px(6)))
        me = (v or {}).get("me") or {}
        targets = set(me.get("targets") or [])
        self.cells = []
        small = self.st.get("size") == "small"
        R = self.px(13 if small else 25)
        for i, p in enumerate(rows):
            cx = (i % cols) * cw + cw / 2
            cy = (i // cols) * ch + self.px(18 if small else 34)
            pid = p.get("id")
            is_t = pid in targets
            alive = p.get("a", True)
            fill = p["c"] if alive else "#3b3e48"
            if p.get("ghost"):
                cv.create_oval(cx - R, cy - R, cx + R, cy + R, outline="#4a4f63", dash=(3, 3), width=2)
                cv.create_text(cx, cy, text="🤖", font=(self.EF, 9 if small else 15), fill=SUB)
                cv.create_text(cx, cy + R + self.px(9 if small else 13), text="AI 자리", font=self.f(8 if small else 9), fill=SUB)
                continue
            if is_t and self.hover == pid:
                cv.create_oval(cx - R - 6, cy - R - 6, cx + R + 6, cy + R + 6, outline=GOLD, width=2)
            if me.get("pick") == pid:
                cv.create_oval(cx - R - 5, cy - R - 5, cx + R + 5, cy + R + 5, outline=GOLD, width=3)
            if p.get("mp"):
                cv.create_oval(cx - R - 9, cy - R - 9, cx + R + 9, cy + R + 9, outline=RED, width=2, dash=(4, 2))
            if p.get("turn"):
                cv.create_oval(cx - R - 7, cy - R - 7, cx + R + 7, cy + R + 7, outline="#7be08b", width=3)
            if p.get("acc") and v and v["phase"] in ("defense", "judge"):
                cv.create_oval(cx - R - 7, cy - R - 7, cx + R + 7, cy + R + 7, outline="#ff9b3d", width=3)
            cv.create_oval(cx - R, cy - R, cx + R, cy + R, fill=fill, outline="#ffffff" if pid == self.me else "",
                           width=2)
            cv.create_text(cx, cy, text=(p["n"][:1] or "?"), font=self.f(9 if small else 15, True),
                           fill="#1b1d24" if alive else "#777")
            if not alive:
                cv.create_text(cx, cy, text="✕", font=self.f(15 if small else 26, True), fill=RED)
            name = p["n"] + ("" if small or pid != self.me else " (나)")
            if small and p.get("ai"):
                name += "·AI"
            cv.create_text(cx, cy + R + self.px(8 if small else 11), text=name[:9], font=self.f(8 if small else 9, True),
                           fill=TXT if alive else "#707585")
            if small:
                if p.get("v"):
                    cv.create_oval(cx + R - self.px(4), cy - R - self.px(3), cx + R + self.px(10), cy - R + self.px(11),
                                   fill=RED, outline="")
                    cv.create_text(cx + R + self.px(3), cy - R + self.px(4), text=str(p["v"]), font=self.f(8, True), fill="white")
                if (v and v["phase"] == "vote" and p.get("voted")) or (v and v["phase"] == "day" and p.get("rd")):
                    cv.create_text(cx - R - self.px(3), cy + R - self.px(3), text="✔", font=self.f(8, True), fill="#7be08b")
                self.cells.append((pid, cx, cy, R + self.px(8)))
                continue
            sub = []
            if p.get("host"):
                sub.append("👑 방장")
            if p.get("r"):
                sub.append(core.ROLE_ICON.get(p["r"], "") + p["r"])
            elif p.get("tag"):
                sub.append(p["tag"])
            if sub:
                col = RED if (p.get("r") in core.MAFIA_TEAM or p.get("tag") == "마피아") else GOLD
                cv.create_text(cx, cy + R + self.px(25), text=" ".join(sub), font=self.f(8, True), fill=col)
            if p.get("ai"):
                bx, by = cx + R - self.px(4), cy - R + self.px(2)
                cv.create_rectangle(bx - self.px(11), by - self.px(7), bx + self.px(11), by + self.px(7),
                                    fill="#3d4466", outline="")
                cv.create_text(bx, by, text="AI", font=self.f(7, True), fill="#cfd6ff")
            if p.get("v"):
                bx, by = cx - R + self.px(2), cy - R + self.px(2)
                cv.create_oval(bx - self.px(10), by - self.px(10), bx + self.px(10), by + self.px(10), fill=RED, outline="")
                cv.create_text(bx, by, text=str(p["v"]), font=self.f(9, True), fill="white")
            if (v and v["phase"] == "vote" and p.get("voted")) or (v and v["phase"] == "day" and p.get("rd")):
                cv.create_text(cx + R + self.px(2), cy + R - self.px(4), text="✔", font=self.f(10, True), fill="#7be08b")
            self.cells.append((pid, cx, cy, R + self.px(10)))

    # ------------------------------------------------ 바탕화면 캐릭터
    def sync_desk(self):
        if not self.net:
            return
        v, lb = self.view, self.lobby
        rows, sims = [], {self.me}
        if v:
            for p in v["players"]:
                role = p.get("r") if p["id"] != self.me else None
                tag = role or (p.get("tag") if p.get("tag") in ("마피아", "마피아 아님") else None)
                if p.get("acc") and v["phase"] in ("defense", "judge"):
                    tag = "🎤 변론"
                if p.get("turn"):
                    tag = "🙋 소개"
                rows.append(dict(id=p["id"], n=p["n"], ch=p.get("ch"), ai=p["ai"], alive=p["a"],
                                 sub=tag or "", mafia=(role in core.MAFIA_TEAM) or tag == "마피아"))
                if self.host and p["ai"]:
                    sims.add(p["id"])
        elif lb:
            rows = [dict(id=m["id"], n=m["nick"], ch=m.get("ch"), ai=False, alive=True,
                         sub="방장" if m["id"] == lb.get("host") else "") for m in lb.get("members", [])]
        elif self.mode == "solo" or self.host:
            rows = [dict(id=self.me, n=self.nick, ch=self.host.members[self.me].get("ch") if self.host else None,
                         ai=False, alive=True)]
        key = json.dumps([rows, sorted(sims)], sort_keys=True)
        if key != self._desk_key:
            self._desk_key = key
            self.desk.sync(rows, self.me, sims)

    def phase_effects(self, v):
        ph = v.get("phase")
        if ph == "night" and self.prev_phase != "night":
            self.desk.banner("Night Time", f"{v.get('day', 0)}번째 밤", color=(214, 226, 255, 255),
                             tint="#00051a", dim=0.5)
        if ph == "defense" and self.prev_phase != "defense":
            self.desk.banner("최후 변론", f"{v.get('accused', '')}님", color=(255, 196, 92, 255), tint="#140a00", dim=0.3)
        alive = {p["id"]: p["a"] for p in v["players"]}
        for p in v["players"]:
            if self.prev_alive.get(p["id"]) and not p["a"]:
                if p["id"] == self.me:
                    sub = "당신은 죽었습니다 · 이제 유령끼리만 대화할 수 있어요"
                else:
                    sub = f"{p['n']}님" + (f" ({p['r']})" if p.get("r") else "")
                self.desk.banner("사망", sub, color=(255, 70, 86, 255), tint="#1a0003", dim=0.5)
        self.prev_phase, self.prev_alive = ph, alive

    def desk_click(self, pid):
        if pid == self.me:                      # 내 캐릭터 → 채팅창을 앞으로
            try:
                self.root.deiconify()
                self.root.lift()
                self.root.focus_force()
                self.e_chat.focus_set()
            except Exception:
                pass
            return
        self.pick_target(pid)

    def chat_ready(self):
        return hasattr(self, "e_chat") and self.e_chat.winfo_exists()

    def can_move_key(self):
        """입력칸이 비었고 한글 조합 중이 아닐 때만 방향키로 조종 (글 쓰는 중엔 커서 이동)."""
        if not self.chat_ready():
            return True
        return not self.e_chat.get() and not ime_busy()

    def key_walk(self, d, press, e=None):
        if not self.net:
            return
        if press:
            if not self.can_move_key():
                return
            if self._stop_job:
                self.root.after_cancel(self._stop_job)
                self._stop_job = None
            run = bool(e is not None and e.state & 0x1) or time.time() - self._rel.get(d, 0) < 0.3
            self.desk.walk(d, run)
            return "break"
        self._rel[d] = time.time()
        if self._stop_job:
            self.root.after_cancel(self._stop_job)
        self._stop_job = self.root.after(60, self._key_stop)

    def _key_stop(self):
        self._stop_job = None
        self.desk.stop()

    def key_jump(self):
        if self.net and self.can_move_key():
            self.desk.jump()
            return "break"

    def refocus(self):
        """투명 창(캐릭터·Night Time 등)이 새로 생기며 가져간 키보드 포커스를 채팅창으로 돌려준다."""
        try:
            f = self.root.focus_get()
        except Exception:
            f = None
        if f is not None and f.winfo_toplevel() is not self.root and self.chat_ready():
            self.e_chat.focus_set()

    def pick_target(self, pid):
        v = self.view
        me = (v or {}).get("me")
        if not me or pid not in (me.get("targets") or []):
            return
        if v["phase"] == "night":
            self.do_act("night", pid)
        elif v["phase"] == "vote":
            self.do_act("vote", pid)

    def hit(self, x, y):
        for pid, cx, cy, r in self.cells:
            if (x - cx) ** 2 + (y - cy) ** 2 <= r * r:
                return pid
        return None

    def on_motion(self, e):
        pid = self.hit(e.x, e.y)
        targets = set(((self.view or {}).get("me") or {}).get("targets") or [])
        h = pid if pid in targets else None
        self.cv.configure(cursor="hand2" if h else "")
        if h != self.hover:
            self.hover = h
            self.redraw()

    def on_click(self, e):
        if str(self.e_chat.cget("state")) == "normal":
            self.e_chat.focus_set()
        v = self.view
        me = (v or {}).get("me")
        if not me:
            return
        self.pick_target(self.hit(e.x, e.y))

    def draw_actions(self):
        tk = self.tk
        v, lb = self.view, self.lobby
        me = (v or {}).get("me") or {}
        ph = v["phase"] if v else "lobby"
        hint = ""
        btns = []
        if v is None:
            if self.host:
                total = self.host.total
                hs = len(self.host.humans())
                hint = f"사람 {hs}명 + AI {max(0, max(total, core.MIN_PLAYERS) - hs)}명  ·  총 인원을 정하세요"
                btns = [("−", lambda: self.set_total(-1), "#3a3f52"), (f"총 {max(total, hs)}명", None, None),
                        ("+", lambda: self.set_total(1), "#3a3f52"), ("게임 시작 ▶", self.host_start, ACCENT)]
            else:
                hint = "채팅으로 인사하며 기다려요 👋"
        elif ph == "night":
            if me.get("act"):
                if me.get("picked") and not (me["role"] in ("마피아", "의사")):
                    hint = "선택 완료. 아침을 기다리세요."
                else:
                    hint = "🌙 " + me["act"] + ("  (바탕화면 캐릭터 클릭)" if self.st.get("size") == "hide" else "  (아바타 클릭)")
                    btns = [("이번 밤은 안 씀", lambda: self.do_act("night", None), "#3a3f52")]
            elif me.get("mchat"):
                hint = "🔪 마피아끼리 몰래 대화할 수 있어요."
            elif me:
                hint = "😴 밤입니다. 아침을 기다리세요." if me.get("alive") else "👻 유령끼리만 대화할 수 있어요."
        elif ph == "day":
            if me.get("alive"):
                if me.get("ready"):
                    hint = "✔ 토론 끝 표시함. 모두 누르면 바로 투표로 넘어가요."
                else:
                    hint = "💬 토론하세요. 이름을 넣어 말하면 AI 들도 반응해요."
                    btns = [("토론 끝", lambda: self.do_act("ready"), "#4a5170")]
        elif ph == "vote":
            if me.get("alive"):
                hint = ("🗳 처형할 사람의 바탕화면 캐릭터를 클릭하세요" if self.st.get("size") == "hide" else "🗳 처형할 사람을 클릭하세요") \
                    + ("  (다시 클릭하면 변경)" if me.get("picked") else "")
                btns = [("기권", lambda: self.do_act("vote", None), "#3a3f52")]
        elif ph == "selfintro":
            if me.get("myturn"):
                hint = "🙋 내 차례! 채팅으로 한 줄 자기소개 해 주세요"
            elif v.get("turn"):
                hint = f"🙋 {v['turn']}님 자기소개 중… 첫인상을 잘 봐 두세요"
            else:
                hint = "🙋 자기소개 시간"
        elif ph == "defense":
            hint = ("🎤 당신의 최후 변론! 채팅으로 살려 달라고 호소하세요" if me.get("accused")
                    else f"🎤 {v.get('accused', '')}님의 최후 변론을 들어 주세요")
        elif ph == "judge":
            if me.get("act"):
                j = me.get("judge")
                hint = f"👍👎 {v.get('accused', '')}님 처형에 " + ("찬성했어요 (바꿀 수 있어요)" if j is True else
                                                                "반대했어요 (바꿀 수 있어요)" if j is False else "찬성 / 반대?")
                btns = [("👍 찬성" + (" ✔" if j is True else ""), lambda: self.do_act("judge", True), "#b8434f"),
                        ("👎 반대" + (" ✔" if j is False else ""), lambda: self.do_act("judge", False), "#3f6fb5")]
            elif me.get("accused"):
                hint = "🙏 다들 찬반을 고르는 중… 운명을 기다리세요"
        elif ph == "end":
            hint = "수고하셨어요! 모든 직업이 공개됐어요."
            if self.host:
                btns = [("대기실로 (다시 하기)", self.host_lobby, ACCENT)]
        key = (hint, tuple(b[0] for b in btns))
        self.l_hint.configure(text=hint)
        if key == self._act_key:
            return
        self._act_key = key
        for w in self.act_fr.winfo_children():
            w.destroy()
        for text, cmd, col in btns:
            if cmd is None:
                tk.Label(self.act_fr, text=text, bg=BG, fg=TXT, font=self.f(12, True)).pack(side="left", padx=self.px(8))
            else:
                side = "right" if "시작" in text else "left"
                self.btn(self.act_fr, text, cmd, col, small=True).pack(side=side, padx=self.px(3))

    # ------------------------------------------------ 방장 조작
    def set_total(self, d):
        h = self.host
        hs = len(h.humans())
        h.total = max(core.MIN_PLAYERS, hs, min(core.MAX_PLAYERS, max(h.total, hs) + d))
        h.last_lobby = 0
        self._act_key = None

    def host_start(self):
        self.host.start()
        self._act_key = None

    def host_lobby(self):
        self.host.back_to_lobby()
        self.view, self.gid = None, ""
        self.add_line("sys", "— 대기실로 돌아왔어요 —")
        self._act_key = None

    # ------------------------------------------------ 종료
    def save_pos(self):
        try:
            self.st["x"], self.st["y"] = self.root.winfo_x(), self.root.winfo_y()
            write_json(SETTINGS, self.st)
        except Exception:
            pass

    def leave(self):
        if self.net:
            self.net.send(dict(t="bye", id=self.me))
            self.net.close()
        self.net = self.host = None
        self.lobby = self.view = None
        self.desk.clear()
        self._desk_key = None
        self.save_pos()
        self.build_start()

    def quit(self):
        if self.net:
            self.net.send(dict(t="bye", id=self.me))
            self.net.close()
        self.save_pos()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def ime_busy():
    """한글 조합 중인가 (Windows)."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        u, imm, vp = ctypes.windll.user32, ctypes.windll.imm32, ctypes.c_void_p
        u.GetFocus.restype = vp
        imm.ImmGetContext.restype, imm.ImmGetContext.argtypes = vp, [vp]
        imm.ImmGetCompositionStringW.argtypes = [vp, ctypes.c_uint32, vp, ctypes.c_uint32]
        imm.ImmReleaseContext.argtypes = [vp, vp]
        h = u.GetFocus()
        c = imm.ImmGetContext(h)
        if not c:
            return False
        n = imm.ImmGetCompositionStringW(c, 0x8, None, 0)
        imm.ImmReleaseContext(h, c)
        return n > 0
    except Exception:
        return False


def dpi_aware():
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass


if __name__ == "__main__":
    dpi_aware()
    App().run()
