# -*- coding: utf-8 -*-
"""마피아 게임 엔진 + AI 플레이어 (화면·네트워크와 분리. 방장 PC에서만 돈다).

- Game: 직업 배분, 밤/아침/토론/투표/처형 진행, 승리 판정, 플레이어별 화면 정보(view)
- Brain: AI 한 명의 머리. '무엇을 할지'는 규칙(휴리스틱)으로 정하고,
         '어떻게 말할지'는 페르소나 문장 틀 또는 (설정 시) Claude API 가 다듬는다.
"""
import queue
import random
import re

# ---------------------------------------------------------------- 직업

ROLE_INFO = {
    "마피아": ("mafia", "밤마다 동료와 함께 한 명을 제거합니다. 마피아끼리는 서로를 알고, 밤에 마피아 채팅을 씁니다."),
    "스파이": ("mafia", "마피아 편. 밤마다 한 명의 직업을 알아냅니다. 마피아를 찾으면 접선해 마피아 채팅에 들어갑니다."),
    "경찰": ("town", "밤마다 한 명을 조사해 마피아인지 아닌지 알아냅니다. (스파이는 '마피아 아님'으로 나와요)"),
    "의사": ("town", "밤마다 한 명을 골라 마피아의 공격에서 살립니다. 자신도 고를 수 있어요."),
    "기자": ("town", "게임 중 딱 한 번, 둘째 밤부터 한 명을 취재해 다음 날 아침 그 사람의 직업을 모두에게 공개합니다."),
    "시민": ("town", "능력은 없지만 토론과 투표로 마피아를 찾아냅니다."),
}
ROLE_ICON = {"마피아": "🔪", "스파이": "🕶", "경찰": "🚓", "의사": "💉", "기자": "📰", "시민": "🙂"}
MAFIA_TEAM = ("마피아", "스파이")
MIN_PLAYERS, MAX_PLAYERS = 4, 12


def role_plan(n):
    """인원수 → 직업 목록. 마피아 > 경찰·의사 > 기자 > 스파이 순으로 필수 직업을 남긴다."""
    n = max(MIN_PLAYERS, min(MAX_PLAYERS, int(n)))
    mafia = 1 if n <= 8 else (2 if n <= 11 else 3)
    spy = 1 if n >= 7 else 0
    reporter = 1 if n >= 6 else 0
    roles = ["마피아"] * mafia + ["스파이"] * spy + ["경찰", "의사"] + ["기자"] * reporter
    return roles + ["시민"] * (n - len(roles))


def plan_text(n):
    roles = role_plan(n)
    out = []
    for r in ROLE_INFO:
        c = roles.count(r)
        if c:
            out.append(r if c == 1 else f"{r}{c}")
    return " · ".join(out)


# ---------------------------------------------------------------- 페르소나

GENERIC = {
    "open": ["첫날이라 정보가 없네요. 다들 한마디씩 해봐요.", "일단 말 많은 사람부터 지켜볼게요."],
    "accuse": ["저는 {x}님이 수상해요.", "{x}님 아까부터 좀 이상하지 않아요?"],
    "agree": ["저도 {x}님 쪽이 걸려요.", "{x}님 얘기 나온 거 동의합니다."],
    "defend": ["저 아니에요. 저 시민이에요.", "왜 저를 의심하세요? 근거가 없잖아요."],
    "claim_mafia": ["저 경찰입니다. 어젯밤 {x}님 조사했는데 마피아예요!", "경찰입니다. {x}님 마피아 확정이에요. 투표해 주세요."],
    "claim_clear": ["경찰인데요, {x}님은 마피아 아닙니다. 다른 데 보세요."],
    "counter": ["잠깐, 진짜 경찰은 저예요. {x}님 가짜 경찰입니다!"],
    "dead": ["{d}님이 당했네요...", "{d}님 잘 가요. 범인 꼭 찾을게요."],
    "quiet": ["음... 좀 더 지켜볼게요.", "아직 확신이 없네요."],
    "vote": ["저는 {x}님 찍습니다.", "{x}님에게 투표할게요."],
    "night_mafia": ["오늘은 {x} 어때요?", "{x} 처리하죠."],
    "spy_contact": ["접선 완료. 스파이입니다. 같이 가요."],
    "last_defense": ["진짜 저 아니에요! 저 죽이면 시민만 손해예요.", "제 말 한 번만 믿어 주세요. 마피아는 따로 있어요.",
                     "저를 처형하면 내일 아침에 후회하실 거예요.", "반대 눌러 주세요. 저 시민 맞습니다."],
    "judge_yes": ["찬성. 미안하지만 보내자.", "저는 찬성할게요."],
    "judge_no": ["저는 반대요. 아직 확신이 없어요.", "반대! 너무 성급해요."],
    "reply": ["음.. 그건 좀 더 지켜봐야 할 것 같아요.", "글쎄요, 저도 아직 모르겠어요.", "저한테 왜요? ㅎㅎ"],
}

# talk: 하루 발언 수, bold: 대담함(경찰 공개·가짜 경찰), trust: 남의 주장을 믿는 정도
PERSONAS = {
    "셜록": dict(desc="논리적인 분석가. 존댓말, 차분하고 단정적인 말투", talk=3, bold=.6, trust=.6, t={
        "open": ["정보가 없는 첫날입니다. 각자 발언 패턴을 기록하겠습니다.", "첫날은 투표 성향을 보는 게 중요합니다."],
        "accuse": ["정황상 {x}님이 가장 수상합니다.", "{x}님 발언에 모순이 있습니다. 설명해 주시죠.", "확률적으로 {x}님이 마피아일 가능성이 높습니다."],
        "agree": ["{x}님 의심, 논리적으로 타당합니다.", "저도 같은 결론입니다. {x}님."],
        "defend": ["저를 의심할 근거가 부족합니다. 제 발언을 다시 보시죠.", "감정적 판단은 마피아만 이롭게 합니다. 저는 시민입니다."],
        "dead": ["{d}님이 제거됐군요. 마피아가 누구를 두려워했는지가 단서입니다."],
        "vote": ["결론: {x}님에게 투표합니다."],
        "quiet": ["아직 데이터가 부족합니다."]}),
    "불도저": dict(desc="성격 급한 돌격대장. 거친 반말, 짧고 공격적", talk=3, bold=.9, trust=.5, t={
        "open": ["다 필요 없고 수상한 놈 바로 잡는다.", "첫날부터 조용한 사람이 범인이야."],
        "accuse": ["{x} 너 마피아지? 딱 보여.", "{x} 바로 투표 간다. 이견 없지?", "야 {x}, 말 돌리지 마."],
        "agree": ["ㅇㅇ {x} 맞아. 가자.", "{x} 찍자. 시간 아까워."],
        "defend": ["나? 웃기지 마. 나 시민이야.", "날 의심할 시간에 진짜 범인이나 찾아."],
        "dead": ["{d} 당했네. 마피아 이것들 각오해."],
        "vote": ["{x}. 끝."],
        "night_mafia": ["{x} 처리해. 시끄러워."]}),
    "쫄보": dict(desc="겁 많은 소심이. 말끝을 흐리고 ㅠㅠ를 자주 씀", talk=2, bold=.2, trust=.8, t={
        "open": ["저.. 저는 그냥 시민이에요.. 잘 부탁드려요ㅠ", "무서워요.. 다들 착한 사람이죠..?"],
        "accuse": ["저.. 저는 {x}님이 좀 이상한 것 같아요..", "{x}님.. 혹시.. 아니죠..?ㅠ"],
        "agree": ["저도.. {x}님이 좀..", "{x}님 쪽으로.. 따라갈게요.."],
        "defend": ["저 아니에요ㅠㅠ 진짜예요..", "왜 저예요ㅠ 저 아무것도 안 했어요.."],
        "dead": ["{d}님ㅠㅠ 너무 무서워요..", "히익.. {d}님이.."],
        "vote": ["{x}님.. 죄송해요.."]}),
    "깐족이": dict(desc="장난꾸러기. ㅋㅋ를 달고 살고 남을 놀림", talk=3, bold=.7, trust=.4, t={
        "open": ["ㅋㅋㅋ 다들 표정 관리 잘하네", "마피아 손 들어보세요~ 아 안 드네ㅋㅋ"],
        "accuse": ["{x} 방금 동공지진 온 거 나만 봄?ㅋㅋ", "{x}님 연기 좀 어색한데요?ㅋㅋ"],
        "agree": ["ㅋㅋ {x} 몰이 탑승합니다", "{x} 각이다 ㅋㅋ"],
        "defend": ["나ㅋㅋ? 나 같은 시민이 어딨다고", "ㅋㅋ 억울하다 억울해"],
        "dead": ["{d}님 RIP.. 좋은 사람이었다(?)"],
        "vote": ["{x} 픽 ㅋㅋ"]}),
    "김부장": dict(desc="자기 경험담을 늘어놓는 꼰대 부장님. '~하지', '~야' 하는 하대 말투", talk=2, bold=.7, trust=.5, t={
        "open": ["자자, 다들 집중하고. 회의 시작하지.", "내가 사회생활 30년인데, 눈빛 보면 다 알아."],
        "accuse": ["{x} 씨, 아까부터 태도가 영 별로야.", "내 경험상 {x} 같은 스타일이 꼭 일을 내."],
        "agree": ["음, {x} 씨 얘기는 일리가 있어.", "{x} 씨로 결재하지."],
        "defend": ["어허, 날 의심해? 내가 이 회사에서 몇 년인데.", "나는 아니야. 보고서로 증명할 수도 있어."],
        "dead": ["{d} 씨가... 아까운 인재였는데."],
        "vote": ["{x} 씨. 이걸로 회의 마치지."]}),
    "하늘": dict(desc="감성적이고 여린 사람. 믿음과 배신에 민감, ㅠㅠ 사용", talk=2, bold=.4, trust=.8, t={
        "open": ["다들 믿고 싶어요.. 우리 끝까지 같이 살아요ㅠ"],
        "accuse": ["{x}님 믿었는데.. 너무 수상해요ㅠㅠ", "{x}님 말할 때 마음이 불편해요.."],
        "agree": ["맞아요.. {x}님이 좀.."],
        "defend": ["제가 마피아라니.. 너무 속상해요ㅠㅠ"],
        "dead": ["{d}님.. 아직 할 말이 많았는데ㅠㅠ"],
        "vote": ["{x}님.. 미안해요ㅠ"]}),
    "무념": dict(desc="극도로 과묵함. 한두 단어로만 말함", talk=1, bold=.5, trust=.6, t={
        "open": ["...", "...지켜본다."],
        "accuse": ["...{x}.", "{x}. 수상."],
        "agree": ["{x}. 동의."],
        "defend": ["아니다."],
        "dead": ["...{d}."],
        "vote": ["{x}."],
        "quiet": ["..."]}),
    "도치": dict(desc="모두를 의심하는 의심병 환자. 반말", talk=3, bold=.6, trust=.2, t={
        "open": ["다 수상해. 진짜 다.", "아무도 안 믿어. 나 빼고."],
        "accuse": ["다 수상한데 특히 {x}.", "{x} 너무 조용해. 아니 너무 시끄러워. 아무튼 수상해."],
        "agree": ["{x}? 원래 의심하고 있었어."],
        "defend": ["날 의심하는 너가 더 수상해."],
        "dead": ["{d} 죽은 것도 연출 아냐?"],
        "vote": ["일단 {x}."]}),
    "지우": dict(desc="신조어를 쓰는 MZ 신입. 'ㄹㅇ', 'ㅇㅈ', '킹받네' 같은 말투", talk=2, bold=.6, trust=.5, t={
        "open": ["ㅎㅇㅎㅇ 저 시민임 ㄹㅇ", "다들 텐션 왜 이렇게 낮아요"],
        "accuse": ["{x}님 솔직히 킹받게 수상함", "{x}님 말투 완전 마피아 바이브인데"],
        "agree": ["{x} ㅇㅈ", "{x}님 쪽 투표 ㄱㄱ"],
        "defend": ["저요? ㄴㄴ 저 찐시민임", "억까 멈춰"],
        "dead": ["{d}님 ㅠ 너무 빨리 가셨다"],
        "vote": ["{x} ㄱ"]}),
    "평화": dict(desc="싸움을 말리는 평화주의자. 부드러운 존댓말, 근거를 강조", talk=2, bold=.3, trust=.7, t={
        "open": ["다들 진정하고 근거 중심으로 얘기해봐요."],
        "accuse": ["싸우지 말고요, 근거로 보면 {x}님이 제일 걸려요.", "차분히 보면 {x}님 쪽이 아쉬워요."],
        "agree": ["{x}님 얘기, 근거가 괜찮네요. 동의해요."],
        "defend": ["저를 의심하셔도 괜찮아요. 근데 전 시민이에요.", "근거를 듣고 싶어요. 전 아니에요."],
        "dead": ["{d}님의 희생 헛되지 않게 해요."],
        "vote": ["{x}님에게 투표할게요."]}),
}

COLORS = ["#ff7a7a", "#ffb35c", "#ffd84d", "#8fd96b", "#5cd6c0", "#5cb8ff", "#8f8cff", "#d78cff",
          "#ff8cc6", "#c9a37a", "#9aa7b8", "#6fd1ff"]

ACCUSE_W = ("마피아", "수상", "범인", "의심", "찍", "투표", "이상", "가짜", "거짓", "처형", "죽이")
DEFEND_W = ("아니", "시민", "믿", "억울", "확실", "결백")
CLAIM_RE = re.compile(r"(저|제가|나|내가|난|전)\s*(는|가|은)?\s*(진짜\s*)?경찰|경찰\s*(입니다|이에요|예요|임|이야|인데|이고)")

INTENT_DESC = {
    "open": "첫날 인사 겸 분위기 잡는 한마디",
    "accuse": "{x}을(를) 마피아로 의심한다고 말하기",
    "agree": "다른 사람이 {x}을(를) 의심한 것에 동조하기",
    "defend": "나를 의심하는 사람에게 해명하기",
    "claim_mafia": "내가 경찰이라고 밝히고 {x}이(가) 마피아라고 말하기",
    "claim_clear": "내가 경찰이라고 밝히고 {x}은(는) 마피아가 아니라고 말하기",
    "counter": "진짜 경찰은 나라며 {x}이(가) 가짜 경찰이라고 반박하기",
    "dead": "밤사이 죽은 {d}에 대한 반응",
    "quiet": "아직 판단이 안 선다는 한마디",
    "vote": "{x}에게 투표하겠다고 말하기",
    "night_mafia": "마피아 동료에게 오늘 밤 {x}을(를) 제거하자고 말하기",
    "spy_contact": "마피아에게 내가 스파이라며 접선 인사하기",
    "last_defense": "처형 직전 최후 변론: 내가 왜 처형되면 안 되는지 간절하게 호소하기 (진짜 직업을 밝힐지는 전략적으로 판단)",
    "judge_yes": "{x}의 처형에 찬성한다고 짧게 말하기",
    "judge_no": "{x}의 처형에 반대한다고 짧게 말하기",
    "reply": "방금 {x}이(가) 한 말에 대답하기: \"{q}\"",
}

PHASE_KO = {"intro": "직업 확인", "night": "밤", "morning": "아침", "day": "낮 토론", "vote": "투표",
            "defense": "최후 변론", "judge": "찬반 투표", "verdict": "결과 발표", "end": "게임 끝"}


def clean_line(line, name):
    """Claude 답에서 대사 한 줄만 깔끔하게."""
    line = (line or "").strip().split("\n")[0].strip()
    for pre in (f"{name}:", f"{name} :", f"[{name}]"):
        if line.startswith(pre):
            line = line[len(pre):].strip()
    return line.strip('"').strip("“”").strip()[:120]

# 단계별 시간(초)
TIMES = dict(intro=8, night=30, morning=7, day=100, vote=25, defense=20, judge=12, verdict=7)
FAST = dict(intro=3, night=8, morning=4, day=20, vote=10, defense=6, judge=5, verdict=4)   # 살아있는 사람이 없을 때
AI_GAP = 0.8          # AI 말이 한꺼번에 쏟아지지 않게 (초)


class Player:
    def __init__(self, pid, name, ai, persona, color, ch=None):
        self.id, self.name, self.ai, self.persona, self.color, self.ch = pid, name, ai, persona, color, ch
        self.role, self.alive, self.revealed = "시민", True, False


class Game:
    """방장 PC에서 도는 한 판. tick(now) 를 주기적으로 부르면 시간에 따라 진행된다."""

    def __init__(self, members, now=0.0, rng=None, times=None, reveal_on_death=True, stylist=None):
        """members: [{"id","name","ai":bool,"persona":이름 or None}]"""
        self.rng = rng or random.Random()
        self.times = dict(TIMES, **(times or {}))
        self.reveal_on_death = reveal_on_death
        self.stylist = stylist           # fn(request_dict, callback(text)) — 다른 스레드에서 callback 가능
        self.inbox = queue.Queue()       # stylist 결과
        self.players = {}
        for i, m in enumerate(members):
            self.players[m["id"]] = Player(m["id"], m["name"], m.get("ai", False), m.get("persona"),
                                           COLORS[i % len(COLORS)], m.get("ch"))
        self.order = [m["id"] for m in members]
        roles = role_plan(len(members))
        self.rng.shuffle(roles)
        for pid, r in zip(self.order, roles):
            self.players[pid].role = r
        self.chat, self.seq, self.version = [], 0, 0
        self.phase, self.day, self.start, self.deadline = "intro", 0, now, now
        self.night, self.mafia_pick, self.votes, self.ready = {}, None, {}, set()
        self.knowledge = {pid: {} for pid in self.order}   # 내가 아는 남의 정보 (pid → 표시 문자열)
        self.spy_contact, self.reporter_used, self.winner = False, False, None
        self.heat = {pid: 0.0 for pid in self.order}         # 공개적으로 받은 의심
        self.accused_today = {}                              # 오늘 누가 누구를 의심했나
        self.claims = []                                     # (day, 주장자, 대상, 'mafia'|'clear')
        self.pending = []                                    # (시각, 순번, fn)
        self.used = {}                                       # AI별 최근 쓴 문장 틀
        self.accused, self.judge = None, {}                  # 최후 변론 대상 / 찬반 (pid → True/False)
        self.outq, self.last_ai_post = [], 0.0               # AI 말 대기열 (간격 두고 내보냄)
        self.llm_busy = 0
        self.dead_talk = {}                                  # 날짜별 죽음 반응 수
        self._pseq = 0
        self.now = now
        self.brains = {pid: Brain(self, pid) for pid in self.order if self.players[pid].ai}
        mafia = [p for p in self.alive_list() if p.role == "마피아"]
        self.sys("🎲 게임 시작! " + plan_text(len(members)) + f" — 총 {len(members)}명")
        for p in self.players.values():
            self.sys(f"당신의 직업은 [{p.role}] 입니다. {ROLE_INFO[p.role][1]}", to=p.id)
            if p.role == "마피아" and len(mafia) > 1:
                mates = ", ".join(q.name for q in mafia if q.id != p.id)
                self.sys(f"🔪 마피아 동료: {mates}", to=p.id)
                for q in mafia:
                    if q.id != p.id:
                        self.knowledge[p.id][q.id] = "마피아"
        self._set_phase("intro", now)

    # ------------------------------------------------ 기본 도구
    def alive_list(self):
        return [self.players[i] for i in self.order if self.players[i].alive]

    def humans_alive(self):
        return [p for p in self.alive_list() if not p.ai]

    def name(self, pid):
        return self.players[pid].name if pid in self.players else "?"

    def mafia_chat_ids(self):
        ids = {p.id for p in self.players.values() if p.role == "마피아"}
        if self.spy_contact:
            ids |= {p.id for p in self.players.values() if p.role == "스파이"}
        return ids

    def team_of(self, pid):
        return ROLE_INFO[self.players[pid].role][0]

    def post(self, kind, text, who=None, scope="all"):
        """kind: sys|say|mafia|dead. scope: all|mafia|dead|to:<pid>"""
        self.seq += 1
        self.chat.append(dict(s=self.seq, k=kind, w=who, n=self.name(who) if who else "", x=text, sc=scope))
        if len(self.chat) > 400:
            self.chat = self.chat[-300:]
        self.version += 1

    def sys(self, text, to=None):
        self.post("sys", text, scope=f"to:{to}" if to else "all")

    def schedule(self, delay, fn):
        self._pseq += 1
        self.pending.append((self.now + delay, self._pseq, fn))

    def _dur(self, phase):
        d = self.times[phase]
        if phase == "defense" and self.accused and self.players[self.accused].ai:
            d = min(d, 14)
        if not self.humans_alive():
            d = min(d, FAST[phase])
        return d

    def _set_phase(self, phase, now):
        self.phase, self.start = phase, now
        self.deadline = now + self._dur(phase)
        self.version += 1

    # ------------------------------------------------ 진행
    def tick(self, now):
        self.now = now
        while True:
            try:
                self.outq.append(self.inbox.get_nowait())
            except queue.Empty:
                break
        if self.outq and now - self.last_ai_post >= AI_GAP:
            pid, text, scope, kind, ph, day = self.outq.pop(0)
            if ph == self.phase and day == self.day:
                self.last_ai_post = now
                self._ai_post(pid, text, scope, kind)
            self.outq = [o for o in self.outq if o[4] == self.phase and o[5] == self.day]
        due = sorted([x for x in self.pending if x[0] <= now])
        self.pending = [x for x in self.pending if x[0] > now]
        for _, _, fn in due:
            if self.phase != "end":
                fn()
        if self.phase != "end" and (now >= self.deadline or self._early(now)):
            self._advance(now)

    def _early(self, now):
        el = now - self.start
        if self.phase == "night":
            return el >= 6 and all(p.id in self.night or (p.role == "마피아" and self.mafia_pick is not None)
                                   for p in self.alive_list() if self._can_act(p))
        if self.phase == "day":
            hs = self.humans_alive()
            return el >= 10 and bool(hs) and all(p.id in self.ready for p in hs)
        if self.phase == "vote":
            return el >= 4 and all(p.id in self.votes for p in self.alive_list())
        if self.phase == "judge":
            return el >= 3 and all(p.id in self.judge for p in self.alive_list() if p.id != self.accused)
        return False

    def _advance(self, now):
        ph = self.phase
        if ph == "intro":
            self._begin_day(now)
        elif ph == "night":
            self._resolve_night()
            self._set_phase("morning", now)
        elif ph == "morning":
            if not self._check_win():
                self._begin_day(now)
        elif ph == "day":
            self._begin_vote(now)
        elif ph == "vote":
            if self._resolve_vote():
                self._begin_defense(now)
            else:
                self._set_phase("verdict", now)
        elif ph == "defense":
            self._begin_judge(now)
        elif ph == "judge":
            self._resolve_judge()
            self._set_phase("verdict", now)
        elif ph == "verdict":
            if not self._check_win():
                self._begin_night(now)

    def _can_act(self, p):
        if not p.alive:
            return False
        if p.role == "기자":
            return not self.reporter_used and self.day >= 2
        return p.role in ("마피아", "의사", "경찰", "스파이")

    def _begin_night(self, now):
        self.night, self.mafia_pick = {}, None
        self._set_phase("night", now)
        self.sys(f"🌙 {self.day}번째 밤이 되었습니다. 능력이 있는 사람은 대상을 고르세요.")
        for b in self._live_brains():
            b.on_night()

    def _begin_day(self, now):
        self.day += 1                                         # 낮이 오면 하루가 시작 (밤 N = 낮 N 다음 밤)
        self.ready, self.accused_today = set(), {}
        for k in self.heat:
            self.heat[k] *= 0.6
        self._set_phase("day", now)
        self.sys(f"☀ {self.day}일째 낮. 토론 시간입니다. 다 말했으면 [토론 끝] 을 누르세요.")
        for b in self._live_brains():
            b.on_day()

    def _begin_vote(self, now):
        self.votes = {}
        self._set_phase("vote", now)
        self.sys("🗳 투표 시간! 처형할 사람을 고르세요. (기권 가능)")
        for b in self._live_brains():
            b.on_vote()

    def _begin_defense(self, now):
        self._set_phase("defense", now)
        self.sys(f"🎤 {self.name(self.accused)}님의 최후 변론! ({int(self._dur('defense'))}초) — 이 시간엔 지목된 사람만 말할 수 있어요.")
        for b in self._live_brains():
            b.on_defense()

    def _begin_judge(self, now):
        self.judge = {}
        self._set_phase("judge", now)
        self.sys(f"👍👎 {self.name(self.accused)}님을 처형할까요? 찬성 / 반대를 눌러 주세요.")
        for b in self._live_brains():
            b.on_judge()

    def _resolve_judge(self):
        yes = sum(1 for v in self.judge.values() if v)
        no = sum(1 for v in self.judge.values() if v is False)
        t = self.accused
        self.sys(f"📊 찬반 결과: 찬성 {yes} · 반대 {no}")
        if yes > no:
            self._kill(t, "vote")
        else:
            self.sys(f"🙏 {self.name(t)}님이 살아남았습니다!")
            for b in self.brains.values():
                b.on_spared(t)
        self.accused = None

    def _live_brains(self):
        return [self.brains[p.id] for p in self.alive_list() if p.id in self.brains]

    def _kill(self, pid, why):
        p = self.players[pid]
        p.alive = False
        p._died, p._cause = self.day, why
        if self.reveal_on_death:
            p.revealed = True
        role = f" (직업: {p.role})" if self.reveal_on_death else ""
        if why == "mafia":
            self.sys(f"💀 밤사이 {p.name}님이 마피아에게 당했습니다.{role}")
        else:
            self.sys(f"⚖ {p.name}님이 투표로 처형되었습니다.{role}")
        if not p.ai:
            self.sys("당신은 죽었습니다. 이제부터 관전하며 죽은 사람끼리만 대화할 수 있어요.", to=pid)
        for b in self.brains.values():
            b.on_death(pid, why)

    def _resolve_night(self):
        kill = self.mafia_pick if any(p.role == "마피아" for p in self.alive_list()) else None
        heals = {t for i, t in self.night.items() if self.players[i].role == "의사" and t}
        if kill and kill in heals:
            self.sys(f"💉 {self.name(kill)}님이 마피아의 공격을 받았지만 의사의 치료로 살아났습니다!")
            kill = None
        elif kill:
            self._kill(kill, "mafia")
        else:
            self.sys("🌅 조용한 밤이었습니다. 아무도 죽지 않았어요.")
        for i, t in self.night.items():
            p = self.players[i]
            if not p.alive or not t:
                continue
            tp = self.players[t]
            if p.role == "경찰":
                res = "마피아" if tp.role == "마피아" else "마피아 아님"
                self.knowledge[i][t] = res
                self.sys(f"🚓 조사 결과: {tp.name}님은 [{res}] 입니다.", to=i)
            elif p.role == "스파이":
                self.knowledge[i][t] = tp.role
                self.sys(f"🕶 정보 입수: {tp.name}님의 직업은 [{tp.role}] 입니다.", to=i)
                if tp.role == "마피아" and not self.spy_contact:
                    self.spy_contact = True
                    for m in self.players.values():
                        if m.role == "마피아":
                            self.knowledge[i][m.id] = "마피아"
                            self.knowledge[m.id][i] = "스파이"
                    self.post("sys", f"🤝 스파이 {p.name}님이 마피아와 접선했습니다! 이제 마피아 채팅에 함께해요.", scope="mafia")
            elif p.role == "기자":
                self.reporter_used = True
                tp.revealed = True
                self.sys(f"📰 특종! 기자의 취재 결과 {tp.name}님의 직업은 [{tp.role}] 입니다!")
                for b in self.brains.values():
                    b.on_public_role(t)
        for b in self._live_brains():
            b.on_morning()

    def _resolve_vote(self):
        tally = {}
        for t in self.votes.values():
            if t:
                tally[t] = tally.get(t, 0) + 1
        if not tally:
            self.sys("모두 기권했습니다. 아무도 처형되지 않았어요.")
            return False
        top = max(tally.values())
        best = [t for t, c in tally.items() if c == top]
        summary = ", ".join(f"{self.name(t)} {c}표" for t, c in sorted(tally.items(), key=lambda x: -x[1]))
        self.sys(f"📊 투표 결과: {summary}")
        if len(best) > 1:
            self.sys("동점이라 아무도 처형되지 않았습니다.")
            return False
        self.accused = best[0]
        return True

    def _check_win(self):
        alive = self.alive_list()
        mafia = sum(p.role == "마피아" for p in alive)
        team = sum(p.role in MAFIA_TEAM for p in alive)
        if mafia == 0:
            self.winner = "시민"
        elif team >= len(alive) - team:
            self.winner = "마피아"
        if not self.winner:
            return False
        self.phase, self.deadline = "end", self.now
        self.version += 1
        self.sys(("🎉 시민 팀 승리! 마피아를 모두 찾아냈습니다." if self.winner == "시민"
                  else "😈 마피아 팀 승리! 마피아가 마을을 장악했습니다."))
        self.sys("직업 공개: " + ", ".join(f"{p.name}={p.role}" for p in self.players.values()))
        for p in self.players.values():
            p.revealed = True
        return True

    # ------------------------------------------------ 플레이어 행동
    def targets(self, pid):
        p = self.players.get(pid)
        if not p or not p.alive or self.phase not in ("night", "vote"):
            return []
        alive = [q.id for q in self.alive_list()]
        if self.phase == "vote":
            return [t for t in alive if t != pid]
        if not self._can_act(p):
            return []
        if p.role == "마피아":
            return [t for t in alive if self.players[t].role != "마피아"]
        if p.role == "의사":
            return alive
        return [t for t in alive if t != pid]

    def night_action(self, pid, target):
        """target=None 이면 이번 밤 능력 안 씀."""
        p = self.players.get(pid)
        if self.phase != "night" or not p or not self._can_act(p):
            return False
        if target is not None and target not in self.targets(pid):
            return False
        if p.role in ("경찰", "스파이", "기자") and pid in self.night:
            if not p.ai:
                self.sys("이미 대상을 골랐어요. (밤마다 한 번만)", to=pid)
            return False
        same = pid in self.night and self.night[pid] == target
        self.night[pid] = target
        self.version += 1
        if same:
            return True
        if target is None:
            if not p.ai:
                self.sys("이번 밤은 능력을 쓰지 않습니다.", to=pid)
            return True
        verb = {"마피아": "제거 대상", "의사": "치료 대상", "경찰": "조사 대상", "스파이": "정보 수집 대상", "기자": "취재 대상"}[p.role]
        if p.role == "마피아":
            self.mafia_pick = target
            self.post("sys", f"🔪 {p.name}님이 {self.name(target)}님을 제거 대상으로 골랐습니다.", scope="mafia")
        elif not p.ai:
            self.sys(f"{verb}: {self.name(target)}님 (아침에 결과가 나와요)", to=pid)
        return True

    def vote(self, pid, target):
        p = self.players.get(pid)
        if self.phase != "vote" or not p or not p.alive:
            return False
        if target is not None and target not in self.targets(pid):
            return False
        if pid in self.votes:
            prev = self.votes[pid]
            if prev:
                self.heat[prev] -= .3
        self.votes[pid] = target
        if target:
            self.heat[target] += .3
        self.version += 1
        for b in self._live_brains():
            b.on_vote_cast(pid, target)
        return True

    def judge_vote(self, pid, yes):
        p = self.players.get(pid)
        if self.phase != "judge" or not p or not p.alive or pid == self.accused:
            return False
        self.judge[pid] = bool(yes)
        self.version += 1
        return True

    def set_ready(self, pid):
        p = self.players.get(pid)
        if self.phase == "day" and p and p.alive:
            self.ready.add(pid)
            self.version += 1
            return True
        return False

    def say(self, pid, text):
        """채팅. 밤에는 마피아(접선한 스파이 포함)만, 죽은 사람은 죽은 사람끼리."""
        p = self.players.get(pid)
        text = (text or "").strip()[:200]
        if not p or not text:
            return False
        if self.phase == "end":
            self.post("say", text, pid, "all")
            return True
        if not p.alive:
            self.post("dead", text, pid, "dead")
            return True
        if self.phase == "night":
            if pid in self.mafia_chat_ids():
                self.post("mafia", text, pid, "mafia")
                for b in self._live_brains():
                    b.hear_mafia(pid, text)
                return True
            if not p.ai:
                self.sys("밤에는 말할 수 없어요. (마피아만 몰래 대화)", to=pid)
            return False
        if self.phase == "defense" and pid != self.accused:
            if not p.ai:
                self.sys("최후 변론 중에는 지목된 사람만 말할 수 있어요.", to=pid)
            return False
        self.post("say", text, pid, "all")
        self._analyze(pid, text)
        if not p.ai:
            self._call_ai(pid, text)
        return True

    def _call_ai(self, pid, text):
        """사람이 AI 이름을 부르거나 질문하면 그 AI 가 바로 대답."""
        if self.phase not in ("day", "vote", "judge", "morning", "verdict", "intro"):
            return
        who = [t for t in self.mentions(text, pid) if t in self.brains and self.players[t].alive]
        if not who and ("?" in text or "다들" in text or "누구" in text or "AI" in text.upper()):
            cands = [b.id for b in self._live_brains()]
            if cands:
                who = self.rng.sample(cands, min(len(cands), 2 if self.stylist else 1))
        for i, t in enumerate(who[:2]):
            self.brains[t].on_called(pid, text, delay=0.3 + i * 1.0)

    # ------------------------------------------------ 채팅 해석 (AI 들이 듣는 내용)
    def mentions(self, text, speaker):
        out = []
        for p in self.players.values():
            if p.id != speaker and len(p.name) >= 1 and p.name in text:
                out.append(p.id)
        return out

    def _analyze(self, pid, text):
        who = self.mentions(text, pid)
        claim = bool(CLAIM_RE.search(text))
        acc = any(w in text for w in ACCUSE_W)
        dfn = any(w in text for w in DEFEND_W)
        if claim and who:
            kind = "clear" if ("아니" in text or "시민" in text) else ("mafia" if "마피아" in text else None)
            if kind:
                self.claims.append((self.day, pid, who[0], kind))
                for b in self._live_brains():
                    b.on_claim(pid, who[0], kind)
                return
        if claim:
            for b in self._live_brains():
                b.on_claim(pid, None, None)
        if who and acc and not (dfn and not "마피아" in text):
            for t in who:
                self.heat[t] += 1.0
                self.accused_today.setdefault(pid, set()).add(t)
            for b in self._live_brains():
                b.on_accuse(pid, who)
        elif who and dfn:
            for b in self._live_brains():
                b.on_defend(pid, who)

    # ------------------------------------------------ AI 발언
    def ai_line(self, pid, intent, x=None, d=None, scope="all", q=""):
        p = self.players[pid]
        pers = PERSONAS.get(p.persona, {})
        base = intent
        if intent == "reply":                         # 대체 대사: 의심받았으면 해명, 아니면 페르소나 '모르겠다'
            base = "defend" if (p.name in q and any(w in q for w in ACCUSE_W)) else "quiet"
            if base == "quiet" and not pers.get("t", {}).get("quiet"):
                base = "reply"
        pool = pers.get("t", {}).get(base) or GENERIC.get(base) or GENERIC["quiet"]
        used = self.used.setdefault(pid, [])
        fresh = [t for t in pool if t not in used] or pool
        tmpl = self.rng.choice(fresh)
        used.append(tmpl)
        del used[:-4]
        text = tmpl.format(x=self.name(x) if x else "", d=self.name(d) if d else "")
        kind = "mafia" if scope == "mafia" else "say"
        phase, day = self.phase, self.day
        if self.stylist and self.llm_busy < 6:
            req = self.llm_request(pid, intent, x, d, scope, q, text)
            done = {"v": False}
            self.llm_busy += 1

            def cb(line):
                if done["v"]:
                    return
                done["v"] = True
                self.llm_busy = max(0, self.llm_busy - 1)
                self.inbox.put((pid, clean_line(line, p.name) or text, scope, kind, phase, day))
            self.stylist(req, cb)
            self.schedule(8, lambda: cb(text))       # 응답이 늦으면 기본 문장
            return
        self.inbox.put((pid, text, scope, kind, phase, day))

    def llm_request(self, pid, intent, x, d, scope, q, default):
        """Claude 에게 넘길 상황 정보 (그 AI 가 알 수 있는 것만)."""
        p = self.players[pid]
        pers = PERSONAS.get(p.persona, {})
        mids = self.mafia_chat_ids()
        mates = [self.name(i) for i in mids if i != pid] if pid in mids else []
        public = []
        for o in self.players.values():
            if not o.alive:
                public.append(f"{o.name}: 사망" + (f"({o.role})" if o.revealed else ""))
            elif o.revealed:
                public.append(f"{o.name}: 직업 공개됨({o.role})")
        claims = [f"{self.name(c[1])}이(가) 경찰이라며 {self.name(c[2])}을(를) {'마피아' if c[3] == 'mafia' else '시민'}라고 주장"
                  for c in self.claims[-4:]]
        recent = []
        for m in self.chat:
            if m["sc"] == "all" or (m["sc"] == "mafia" and pid in mids):
                tag = "[마피아채팅] " if m["sc"] == "mafia" else ""
                recent.append(f"{tag}{m['n'] or '[사회자]'}: {m['x']}")
        b = self.brains.get(pid)
        sus = [self.name(t) for t in sorted(b.others(), key=b.score, reverse=True)[:2]] if b else []
        fmt = dict(x=self.name(x) if x else "", d=self.name(d) if d else "", q=q[:120])
        return dict(name=p.name, persona=pers.get("desc", ""), role=p.role, team=self.team_of(pid), mates=mates,
                    intent=intent, intent_desc=INTENT_DESC.get(intent, intent).format(**fmt),
                    default=default, day=self.day, phase=PHASE_KO.get(self.phase, self.phase),
                    accused=self.name(self.accused) if self.accused else "",
                    alive=[o.name for o in self.alive_list()], public=public, claims=claims,
                    known={self.name(k): v for k, v in self.knowledge[pid].items()}, suspects=sus,
                    recent=recent[-24:], scope=scope)

    def _ai_post(self, pid, text, scope, kind):
        p = self.players[pid]
        if not p.alive or self.phase == "end":
            return
        if scope == "mafia":
            if self.phase == "night":
                self.post("mafia", text, pid, "mafia")
            return
        if self.phase in ("night",):
            return
        if self.phase == "defense" and pid != self.accused:
            return
        self.post(kind, text, pid, scope)
        self._analyze(pid, text)

    # ------------------------------------------------ 화면용 정보
    def view(self, pid, now):
        me = self.players.get(pid)
        ended = self.phase == "end"
        mine = self.knowledge.get(pid, {})
        mafia_ids = self.mafia_chat_ids()
        rows = []
        tally = {}
        if self.phase in ("vote", "defense", "judge", "verdict"):
            for t in self.votes.values():
                if t:
                    tally[t] = tally.get(t, 0) + 1
        for i in self.order:
            p = self.players[i]
            role = p.role if (ended or p.revealed or i == pid) else None
            tag = mine.get(i)
            if role is None and tag in ROLE_INFO:
                role = tag
            rows.append(dict(id=i, n=p.name, ai=p.ai, a=p.alive, c=p.color, r=role, tag=tag, ch=p.ch,
                             v=tally.get(i, 0), voted=i in (self.judge if self.phase == "judge" else self.votes),
                             rd=i in self.ready, acc=(i == self.accused),
                             mp=(self.mafia_pick == i and pid in mafia_ids and self.phase == "night")))
        vis = []
        for m in self.chat:
            sc = m["sc"]
            ok = (sc == "all" or ended or sc == f"to:{pid}"
                  or (sc == "mafia" and pid in mafia_ids)
                  or (sc == "dead" and me is not None and not me.alive))
            if ok:
                vis.append(dict(s=m["s"], k=m["k"], n=m["n"], x=m["x"], w=m["w"], me=m["w"] == pid and pid is not None,
                                p=sc.startswith("to:")))
        v = dict(phase=self.phase, day=self.day, remain=max(0, int(self.deadline - now + .99)),
                 total=int(self._dur(self.phase)) if self.phase != "end" else 0,
                 players=rows, chat=vis[-80:], winner=self.winner, plan=plan_text(len(self.order)),
                 accused=self.name(self.accused) if self.accused else "")
        if me:
            act = None
            if self.phase == "night" and self._can_act(me):
                act = {"마피아": "제거할 사람을 고르세요", "의사": "살릴 사람을 고르세요",
                       "경찰": "조사할 사람을 고르세요", "스파이": "직업을 알아낼 사람을 고르세요",
                       "기자": "취재할 사람을 고르세요 (게임 중 1번)"}[me.role]
            elif self.phase == "vote" and me.alive:
                act = "처형할 사람에게 투표하세요"
            elif self.phase == "judge" and me.alive and pid != self.accused:
                act = "찬성 / 반대를 눌러 주세요"
            v["me"] = dict(id=pid, role=me.role, desc=ROLE_INFO[me.role][1], alive=me.alive,
                           team=self.team_of(pid), act=act, targets=self.targets(pid),
                           pick=self.night.get(pid) if self.phase == "night" else self.votes.get(pid),
                           picked=(pid in self.night) if self.phase == "night" else (pid in self.votes),
                           ready=pid in self.ready, mchat=pid in mafia_ids, judge=self.judge.get(pid),
                           accused=pid == self.accused,
                           can_chat=(ended or not me.alive or ((self.phase != "night" or pid in mafia_ids)
                                                              and (self.phase != "defense" or pid == self.accused))))
        return v


# ---------------------------------------------------------------- AI 두뇌

class Brain:
    def __init__(self, game, pid):
        self.g, self.id = game, pid
        self.rng = random.Random(game.rng.random())
        self.p = game.players[pid]
        per = PERSONAS.get(self.p.persona) or {}
        self.talk, self.bold, self.trust = per.get("talk", 2), per.get("bold", .5), per.get("trust", .5)
        self.sus = {i: self.rng.uniform(0, .6) for i in game.order if i != pid}
        self.truth = {}            # 확실히 아는 직업 (pid → role)
        self.revealed_claim = False
        self.fake_claimed = False
        self.last_defend = -99
        self.spoke = 0

    # 편의
    @property
    def role(self):
        return self.p.role

    @property
    def mafia_side(self):
        return self.role in MAFIA_TEAM

    def team(self):
        g = self.g
        if self.role == "마피아":
            return {p.id for p in g.players.values() if p.role == "마피아"} | (
                {p.id for p in g.players.values() if p.role == "스파이"} if g.spy_contact else set())
        if self.role == "스파이":
            return {self.id} | ({p.id for p in g.players.values() if p.role == "마피아"} if g.spy_contact else set())
        return {self.id}

    def others(self):
        return [p.id for p in self.g.alive_list() if p.id != self.id]

    def score(self, t):
        """처형하고 싶은 정도."""
        g = self.g
        if self.mafia_side:
            if t in self.team():
                return -999
            s = g.heat[t] * 1.2 + self.rng.uniform(0, 1.2)
            if self.truth.get(t) in ("경찰", "의사", "기자"):
                s += 1.5
            return s
        s = self.sus.get(t, 0) + g.heat[t] * (0.3 + self.trust * 0.5) + self.rng.uniform(0, .8)
        r = self.truth.get(t)
        if r in MAFIA_TEAM:
            s += 100
        elif r:
            s -= 100
        return s

    def top(self, cands=None, exclude=()):
        cands = [c for c in (cands or self.others()) if c not in exclude]
        return max(cands, key=self.score) if cands else None

    def _say(self, delay, intent, **kw):
        self.g.schedule(delay, lambda: self.p.alive and self.g.ai_line(self.id, intent, **kw))

    # 밤
    def on_night(self):
        g = self.g
        if not g._can_act(self.p):
            return
        delay = self.rng.uniform(2.5, 8)
        g.schedule(delay, self._act_night)

    def _act_night(self):
        g = self.g
        if g.phase != "night" or self.id in g.night or not self.p.alive:
            return
        cands = g.targets(self.id)
        if not cands:
            return
        r = self.role
        if r == "마피아":
            if g.mafia_pick is not None:                 # 동료(사람)가 이미 골랐으면 따른다
                g.night[self.id] = g.mafia_pick
                return
            pri = []
            for c in cands:
                s = self.rng.uniform(0, 1)
                if any(cl[1] == c for cl in g.claims):  # 경찰이라고 밝힌 사람
                    s += 4
                if self.truth.get(c) in ("경찰", "의사", "기자"):
                    s += 3
                s += len([1 for a, ts in g.accused_today.items() if a == c]) * .5
                if c in self.team():
                    s -= 99
                if not g.players[c].ai and g.day <= 1 and not any(cl[1] == c for cl in g.claims):
                    s -= 1.5                              # 첫 밤엔 사람보다 AI 를 먼저 (경찰이라 밝힌 사람은 예외)
                pri.append((s, c))
            t = max(pri)[1]
            g.night_action(self.id, t)
            g.ai_line(self.id, "night_mafia", x=t, scope="mafia")
        elif r == "의사":
            claimed = [cl[1] for cl in g.claims if g.players[cl[1]].alive and cl[1] in cands]
            if claimed and self.rng.random() < .7:
                t = claimed[-1]
            elif self.rng.random() < .35:
                t = self.id
            else:
                t = self.rng.choice(cands)
            g.night_action(self.id, t)
        elif r == "경찰":
            unk = [c for c in cands if c not in self.truth] or cands
            t = max(unk, key=lambda c: self.sus.get(c, 0) + g.heat[c] * .5 + self.rng.uniform(0, 1))
            g.night_action(self.id, t)
        elif r == "스파이":
            unk = [c for c in cands if c not in self.truth] or cands
            g.night_action(self.id, self.rng.choice(unk))
        elif r == "기자":
            if self.rng.random() < .6:
                g.night_action(self.id, self.top(cands))
            else:
                g.night_action(self.id, None)

    def hear_mafia(self, pid, text):
        pass

    def on_morning(self):
        g = self.g
        for t, res in g.knowledge[self.id].items():
            if res == "마피아":
                self.truth[t] = "마피아"
            elif res == "마피아 아님":
                self.truth.setdefault(t, "town?")
            else:
                self.truth[t] = res

    # 낮
    def on_day(self):
        g = self.g
        self.spoke = 0
        n = max(2, self.talk + 1 + self.rng.choice([-1, 0, 1]))
        span = max(12, g.times["day"] * .7) if g.humans_alive() else 12
        times = sorted([self.rng.uniform(1.5, 10)] + [self.rng.uniform(8, span) for _ in range(n - 1)])
        for i, t in enumerate(times):
            g.schedule(t, lambda first=(i == 0): self._day_line(first))

    def _day_line(self, first):
        g = self.g
        if g.phase != "day" or not self.p.alive:
            return
        self.spoke += 1
        dead_today = [p.id for p in g.players.values() if not p.alive and getattr(p, "_cause", "") == "mafia"
                      and getattr(p, "_died", None) == g.day - 1]
        # 경찰: 마피아를 찾았으면 공개
        if self.role == "경찰":
            found = [t for t, r in self.truth.items() if r == "마피아" and g.players[t].alive]
            if found and (not self.revealed_claim or self.rng.random() < .5) and self.rng.random() < .5 + self.bold * .5:
                self.revealed_claim = True
                g.ai_line(self.id, "claim_mafia", x=found[0])
                return
            cleared = [t for t, r in self.truth.items() if r == "town?" and g.players[t].alive and g.heat[t] >= 1.5]
            if cleared and self.rng.random() < .5:
                self.revealed_claim = True
                g.ai_line(self.id, "claim_clear", x=cleared[0])
                return
        # 마피아/스파이: 대담하면 가짜 경찰
        if (self.mafia_side and not self.fake_claimed and g.day >= 2 and self.rng.random() < self.bold * .3
                and not any(cl[0] == g.day for cl in g.claims)):
            t = self.top()
            if t:
                self.fake_claimed = True
                g.ai_line(self.id, "claim_mafia", x=t)
                return
        if first and dead_today and g.dead_talk.get(g.day, 0) < 2 and self.rng.random() < .6:
            g.dead_talk[g.day] = g.dead_talk.get(g.day, 0) + 1
            g.ai_line(self.id, "dead", d=dead_today[0])
            return
        if g.day == 1 and first and self.rng.random() < .55:
            g.ai_line(self.id, "open")
            return
        t = self.top()
        if t is None:
            return
        if self.score(t) < -500 or (self.score(t) < .9 and self.rng.random() < .5):
            g.ai_line(self.id, "quiet")
            return
        others_accuse = any(t in ts for a, ts in g.accused_today.items() if a != self.id)
        g.ai_line(self.id, "agree" if others_accuse else "accuse", x=t)

    # 최후 변론 · 찬반
    def on_defense(self):
        g = self.g
        if g.accused != self.id:
            return
        for t in (1.2, 6.5, 11.5)[: 2 + (self.talk >= 3)]:
            g.schedule(t, lambda: g.phase == "defense" and self.p.alive and g.ai_line(self.id, "last_defense"))

    def on_judge(self):
        g = self.g
        if g.accused == self.id:
            return
        g.schedule(self.rng.uniform(2, min(8, g._dur("judge") - 1.5)), self._judge_cast)

    def _judge_cast(self):
        g = self.g
        t = g.accused
        if g.phase != "judge" or not self.p.alive or t is None:
            return
        if self.mafia_side:
            yes = t not in self.team() and self.rng.random() < .85
        else:
            r = self.truth.get(t)
            if r in MAFIA_TEAM:
                yes = True
            elif r:
                yes = False
            else:
                yes = self.score(t) + self.rng.uniform(-.6, .6) > .35
        g.judge_vote(self.id, yes)
        if self.rng.random() < .3:
            g.ai_line(self.id, "judge_yes" if yes else "judge_no", x=t)

    def on_spared(self, pid):
        if pid in self.sus and not self.mafia_side:
            self.sus[pid] -= .5

    def on_called(self, caller, text, delay=0.5):
        """사람이 내 이름을 부르거나 질문함 → 바로 대답 (Claude 가 있으면 맥락 있게)."""
        g = self.g
        if not self.p.alive or g.now - getattr(self, "last_called", -99) < 3:
            return
        self.last_called = g.now
        accused_me = self.p.name in text and any(w in text for w in ACCUSE_W)
        if g.stylist:
            g.schedule(delay, lambda: self.p.alive and g.ai_line(self.id, "reply", x=caller, q=text))
        elif accused_me:
            return                                    # on_accuse 가 해명함
        else:
            g.schedule(delay + .6, lambda: self.p.alive and g.ai_line(self.id, "reply", x=caller, q=text))

    def on_vote(self):
        g = self.g
        self.g.schedule(self.rng.uniform(3, min(14, g._dur("vote") - 2)), self._cast)

    def _cast(self):
        g = self.g
        if g.phase != "vote" or not self.p.alive:
            return
        t = self.top()
        if t is None or self.score(t) < -500 or (self.score(t) < .5 and self.rng.random() < .4):
            g.vote(self.id, None)
            return
        g.vote(self.id, t)
        if self.rng.random() < .35:
            g.ai_line(self.id, "vote", x=t)

    # 듣기
    def on_accuse(self, accuser, targets):
        g = self.g
        if accuser == self.id:
            return
        for t in targets:
            if t == self.id:
                self.sus[accuser] = self.sus.get(accuser, 0) + (.4 if self.mafia_side else .8)
                if g.now - self.last_defend > 8 and g.phase in ("day", "vote", "judge") and self.rng.random() < .85:
                    self.last_defend = g.now
                    if g.stylist and not g.players[accuser].ai:
                        continue                              # 사람이 부른 건 on_called 가 맥락 있게 대답
                    self._say(self.rng.uniform(1.0, 2.5), "defend", x=accuser)
            elif t in self.sus:
                self.sus[t] += .3 * self.trust
                if self.truth.get(t) and self.truth[t] not in MAFIA_TEAM and not self.mafia_side:
                    self.sus[accuser] = self.sus.get(accuser, 0) + .7   # 확실한 시민을 몰면 수상

    def on_defend(self, speaker, targets):
        if speaker == self.id:
            return
        for t in targets:
            if t in self.sus:
                self.sus[t] -= .2 * self.trust

    def on_claim(self, claimer, target, kind):
        g = self.g
        if claimer == self.id:
            return
        if self.role == "경찰" and not self.mafia_side:
            self.truth[claimer] = "마피아"         # 경찰은 한 명뿐 → 가짜
            self.sus[claimer] = self.sus.get(claimer, 0) + 10
            if g.phase in ("day", "vote"):
                self.revealed_claim = True
                self._say(self.rng.uniform(2, 5), "counter", x=claimer)
            return
        if self.mafia_side:
            return
        rivals = [c for c in g.claims if c[1] != claimer]
        w = self.trust * (.5 if rivals else 1)
        if target and kind == "mafia":
            self.sus[target] = self.sus.get(target, 0) + 3 * w
        elif target and kind == "clear":
            self.sus[target] = self.sus.get(target, 0) - 2 * w
        self.sus[claimer] = self.sus.get(claimer, 0) - w + (1.5 if rivals else 0)

    def on_vote_cast(self, voter, target):
        if voter == self.id or not target:
            return
        r = self.truth.get(target)
        if r and r not in MAFIA_TEAM and not self.mafia_side:
            self.sus[voter] = self.sus.get(voter, 0) + .6
        if target == self.id:
            self.sus[voter] = self.sus.get(voter, 0) + .3

    def on_death(self, pid, why):
        g = self.g
        p = g.players[pid]
        if g.reveal_on_death:
            self.on_public_role(pid)
            if why == "vote" and not self.mafia_side:
                bad = p.role not in MAFIA_TEAM
                for a, ts in g.accused_today.items():
                    if pid in ts and a in self.sus:
                        self.sus[a] += .8 if bad else -.6
        if why == "mafia" and not self.mafia_side:
            # 죽은 사람이 의심하던 사람은 덜 수상 (마피아가 죽였을 리 없으니까는 아니고, 단순 노이즈)
            for t in g.accused_today.get(pid, ()):
                if t in self.sus:
                    self.sus[t] += .2

    def on_public_role(self, pid):
        role = self.g.players[pid].role
        self.truth[pid] = role
        if not self.mafia_side and role in MAFIA_TEAM:
            for c in self.g.claims:                 # 이 사람을 감싼 경찰 주장은 가짜
                if c[2] == pid and c[3] == "clear":
                    self.sus[c[1]] = self.sus.get(c[1], 0) + 4
        if not self.mafia_side and role not in MAFIA_TEAM:
            for c in self.g.claims:
                if c[2] == pid and c[3] == "mafia" and c[1] in self.sus:
                    self.sus[c[1]] += 4              # 시민을 마피아라 한 경찰은 가짜


# ---------------------------------------------------------------- Claude API 스타일러 (선택)

def http_reason(e, model, key):
    """API 오류 → 사람이 읽을 이유 (+ 서버가 준 메시지)."""
    detail = ""
    try:
        import json
        detail = json.loads(e.read().decode("utf-8")).get("error", {}).get("message", "")[:90]
    except Exception:
        pass
    base = {401: f"API 키가 올바르지 않아요 (입력된 키: {key[:12]}…, {len(key)}자)",
            403: "API 키 권한이 없어요", 404: f"모델({model})을 찾을 수 없어요",
            429: "요청이 너무 많아요(한도)", 400: "요청 오류", 529: "Claude 서버가 바빠요"}.get(e.code, f"HTTP {e.code}")
    return base + (f" — {detail}" if detail else "")


def clean_key(k):
    """붙여넣다 섞인 공백·줄바꿈·따옴표·'x-api-key:' 같은 머리말 제거."""
    k = (k or "").strip().strip('"').strip("'").strip()
    for pre in ("x-api-key:", "ANTHROPIC_API_KEY=", "Bearer "):
        if k.lower().startswith(pre.lower()):
            k = k[len(pre):].strip()
    return "".join(k.split())


def test_key(api_key, model="claude-haiku-5-5"):
    """키가 되는지 바로 확인 → (성공?, 설명)."""
    import json
    import urllib.error
    import urllib.request
    api_key = clean_key(api_key)
    body = json.dumps({"model": model, "max_tokens": 5,
                       "messages": [{"role": "user", "content": "안녕"}]}).encode("utf-8")
    rq = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body, headers={
        "x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    try:
        with urllib.request.urlopen(rq, timeout=10):
            return True, "연결 성공! AI 들이 Claude 로 대화해요."
    except urllib.error.HTTPError as e:
        return False, http_reason(e, model, api_key)
    except Exception as e:
        return False, f"연결 실패: {type(e).__name__} (회사 네트워크에서 api.anthropic.com 이 막혔을 수 있어요)"


RULES = ("[게임 규칙] 사내 동료들과 하는 온라인 마피아 게임. 직업: 마피아(밤에 1명 제거), 스파이(마피아 편, 밤에 1명 직업 확인), "
         "경찰(밤에 1명 마피아인지 조사), 의사(밤에 1명 보호), 기자(1회 직업 공개), 시민. "
         "낮에 토론 → 투표 → 최다 득표자 최후 변론 → 찬반 투표로 처형. 마피아 팀이 나머지 이상이면 마피아 승, 마피아 전멸이면 시민 승.")


def make_claude_stylist(api_key, model="claude-haiku-5-5"):
    """AI 대사를 Claude 로 만든다 (페르소나 말투 + 게임 상황 반영). 실패하면 기본 문장.
    stylist.status 에 마지막 상태(ok/오류)를 남겨 방장 화면에 알릴 수 있다."""
    api_key = clean_key(api_key)
    import json
    import threading
    import urllib.error
    import urllib.request

    sem = threading.Semaphore(4)

    def build(req):
        team = "마피아 팀" if req["team"] == "mafia" else "시민 팀"
        strat = {
            "마피아": "정체를 숨기고 시민인 척. 다른 사람에게 의심을 돌리고, 동료 마피아는 은근히 감싸되 티 나지 않게.",
            "스파이": "마피아 편이지만 시민인 척. 시민 쪽 사람들을 의심받게 만들기.",
            "경찰": "조사 결과를 언제 밝힐지 전략적으로 판단. 밝히면 마피아에게 노려질 수 있음.",
            "의사": "정체를 숨기는 편이 유리. 꼭 필요할 때만 밝히기.",
            "기자": "정체는 숨기는 편이 유리.",
            "시민": "추리로 마피아를 찾기. 말이 앞뒤가 안 맞는 사람, 남을 몰아가는 사람을 의심.",
        }.get(req["role"], "")
        system = (f"당신은 마피아 게임 AI 플레이어 '{req['name']}'입니다.\n"
                  f"성격·말투: {req['persona']} — 이 말투를 강하게 유지하세요.\n"
                  f"비밀 정보: 당신의 진짜 직업은 '{req['role']}'({team}). {strat}\n"
                  + (f"마피아 동료: {', '.join(req['mates'])}\n" if req["mates"] else "")
                  + RULES + "\n"
                  "[출력 규칙] 단톡방 채팅처럼 짧고 자연스럽게 한 줄(최대 50자)만. 이름표·따옴표·설명·괄호 지문 금지. "
                  "다른 사람 이름을 직접 부르며 말하기. 앞에서 한 말을 반복하지 말고, 상대 말에 구체적으로 반응하기. "
                  "지시가 없는데 자기 직업을 밝히지 말 것(마피아는 거짓말 가능).")
        lines = [f"[현재] {req['day']}일째 · {req['phase']}" + (f" · 최후 변론 대상: {req['accused']}" if req["accused"] else ""),
                 f"살아있는 사람: {', '.join(req['alive'])}"]
        if req["public"]:
            lines.append("공개된 사실: " + " / ".join(req["public"]))
        if req["claims"]:
            lines.append("경찰 주장: " + " / ".join(req["claims"]))
        if req["known"]:
            lines.append("내가 몰래 알아낸 것: " + ", ".join(f"{k}={v}" for k, v in req["known"].items()))
        if req["suspects"]:
            lines.append("내가 지금 제일 의심하는 사람: " + ", ".join(req["suspects"]))
        lines.append("최근 채팅:\n" + ("\n".join(req["recent"]) or "(아직 없음)"))
        where = "마피아끼리만 보는 비밀 채팅" if req["scope"] == "mafia" else "전체 채팅"
        lines.append(f"\n지금 {where}에 할 말: {req['intent_desc']}\n(참고 예시 — 그대로 쓰지 말 것: {req['default']})")
        return system, "\n".join(lines)

    def stylist(req, cb):
        def run():
            with sem:
                try:
                    system, user = build(req)
                    body = json.dumps({"model": model, "max_tokens": 120, "system": system,
                                       "messages": [{"role": "user", "content": user}]}).encode("utf-8")
                    rq = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body, headers={
                        "x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
                    with urllib.request.urlopen(rq, timeout=8) as f:
                        out = json.loads(f.read().decode("utf-8"))
                    stylist.status = "ok"
                    cb("".join(b.get("text", "") for b in out.get("content", []) if b.get("type") == "text"))
                except urllib.error.HTTPError as e:
                    stylist.status = http_reason(e, model, api_key)
                    cb(None)
                except Exception as e:
                    stylist.status = f"연결 실패: {type(e).__name__}"
                    cb(None)
        threading.Thread(target=run, daemon=True).start()
    stylist.status = ""
    stylist.build = build
    return stylist


def pick_chars(count, chars, used=(), rng=None):
    """캐릭터 랜덤 배정: 안 쓴 캐릭터부터, 모자라면 겹쳐서."""
    rng = rng or random.Random()
    chars = list(chars or [])
    if not chars:
        return [None] * count
    out, pool = [], [c for c in chars if c not in used]
    for _ in range(count):
        if not pool:
            pool = list(chars)
        c = rng.choice(pool)
        pool.remove(c)
        out.append(c)
    return out


def ai_members(count, taken_names, rng=None, chars=None, used_chars=()):
    rng = rng or random.Random()
    names = list(PERSONAS)
    rng.shuffle(names)
    chs = pick_chars(count, chars, used_chars, rng)
    out = []
    for i in range(count):
        base = names[i % len(names)]
        n, k = base, 2
        while n in taken_names:
            n, k = f"{base}{k}", k + 1
        taken_names.add(n)
        out.append(dict(id=f"ai-{i}-{rng.randrange(1 << 30):x}", name=n, ai=True, persona=base, ch=chs[i]))
    return out
