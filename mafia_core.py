# -*- coding: utf-8 -*-
"""마피아 게임 엔진 + AI 플레이어 (화면·네트워크와 분리. 방장 PC에서만 돈다).

- Game: 직업 배분, 밤/아침/토론/투표/처형 진행, 승리 판정, 플레이어별 화면 정보(view)
- Brain: AI 한 명의 머리. '무엇을 할지'는 규칙(휴리스틱)으로 정하고,
         '어떻게 말할지'는 페르소나 문장 틀 또는 (설정 시) Claude API 가 다듬는다.
"""
import queue
import time
import random
import re

# ---------------------------------------------------------------- 직업

ROLE_INFO = {
    "마피아": ("mafia", "밤마다 동료와 함께 한 명을 제거합니다. 마피아끼리는 서로를 알고, 밤에 마피아 채팅을 씁니다."),
    "스파이": ("mafia", "마피아 편. 밤마다 한 명의 직업을 알아냅니다. 마피아를 찾으면 접선해 마피아 채팅에 들어갑니다."),
    "경찰": ("town", "밤마다 한 명을 조사해 마피아인지 아닌지 알아냅니다. (스파이는 '마피아 아님'으로 나와요)"),
    "의사": ("town", "밤마다 한 명을 골라 마피아의 공격에서 살립니다. 자신도 고를 수 있어요."),
    "기자": ("town", "게임 중 딱 한 번, 둘째 밤부터 한 명을 취재해 다음 날 아침 그 사람의 직업을 모두에게 공개합니다."),
    "군인": ("town", "마피아의 공격을 한 번 버텨 냅니다. 버티면 군인임이 모두에게 공개돼요."),
    "정치인": ("town", "투표할 때 2표로 계산되고, 투표로는 처형되지 않습니다. (처형될 상황이면 정치인임이 공개돼요)"),
    "영매": ("town", "밤마다 유령 채팅을 보고 죽은 사람들과 대화할 수 있습니다. (낮에는 유령 채팅을 읽기만)"),
    "마담": ("mafia", "마피아 편. 낮에 한 명을 유혹하면 그 사람은 그날 밤 능력을 못 씁니다. 처음부터 마피아와 서로 알아요."),
    "시민": ("town", "능력은 없지만 토론과 투표로 마피아를 찾아냅니다."),
}
ROLE_ICON = {"마피아": "🔪", "스파이": "🕶", "마담": "💋", "경찰": "🚓", "의사": "💉", "기자": "📰",
             "군인": "🪖", "정치인": "🏛", "영매": "🔮", "시민": "🙂"}
MAFIA_TEAM = ("마피아", "스파이", "마담")
MIN_PLAYERS, MAX_PLAYERS = 4, 12
# 직업 특성 태그 (직업판 · 팝업에 표시)
ROLE_TRAITS = {
    "마피아": ["🌙 밤마다 1명 제거", "🤝 동료 마피아·마담을 앎", "💬 밤에 마피아 채팅"],
    "스파이": ["🌙 밤마다 직업 1명 확인", "🤝 마피아를 찾으면 접선", "🚓 경찰 조사엔 '마피아 아님'"],
    "마담": ["☀ 낮에 1명 유혹 (하루 1번)", "🔒 유혹당한 사람은 그날 밤 능력 못 씀", "🤝 처음부터 마피아와 서로 앎"],
    "경찰": ["🌙 밤마다 1명 조사", "결과: 마피아 / 마피아 아님", "⚠ 밝히면 마피아의 표적"],
    "의사": ["🌙 밤마다 1명 보호", "🙋 자기 자신도 가능", "💉 살리면 그 사람 이름 공개"],
    "기자": ["📰 게임 중 1번", "🌙 둘째 밤부터", "📢 다음 날 아침 직업 전체 공개"],
    "군인": ["🛡 마피아 공격 1번 버팀", "📢 버티면 군인 공개", "능력 사용 없음 (자동)"],
    "정치인": ["🗳 투표 2표", "⚖ 투표로 처형 안 됨", "📢 처형될 상황이면 공개"],
    "영매": ["🌙 밤에 유령 채팅 대화", "👻 낮엔 유령 채팅 읽기만", "죽은 사람 정보를 들을 수 있음"],
    "시민": ["능력 없음", "🗣 토론과 투표로 추리", "다수가 힘"],
}
WIN_TEXT = {"mafia": "마피아 팀 수가 나머지 이상이 되면 승리", "town": "마피아를 모두 처형하면 승리"}

# 켜고 끌 수 있는 직업 → 들어가는 최소 인원 (넣는 순서대로)
OPTIONAL = {"기자": 6, "스파이": 7, "군인": 8, "마담": 10, "정치인": 10, "영매": 11}
BOARD_ORDER = ["마피아", "스파이", "마담", "경찰", "의사", "기자", "군인", "정치인", "영매", "시민"]


def team_size(n):
    """마피아 팀 인원 (마피아 + 스파이 + 마담)."""
    return 1 if n <= 6 else (2 if n <= 9 else (3 if n <= 11 else 4))


def role_plan(n, off=()):
    """인원수 → 직업 목록. off: 끈 직업. 끈 시민 팀 직업 자리는 시민, 마피아 팀 자리는 마피아."""
    n = max(MIN_PLAYERS, min(MAX_PLAYERS, int(n)))
    on = lambda r: r not in off and n >= OPTIONAL[r]   # noqa: E731
    T = team_size(n)
    team = [r for r in ("스파이", "마담") if on(r)]
    mafia = max(2 if n >= 12 else 1, T - len(team))
    team = team[:max(0, T - mafia)]
    town = ["경찰", "의사"] + [r for r in ("기자", "군인", "정치인", "영매") if on(r)]
    roles = ["마피아"] * mafia + team + town
    return roles + ["시민"] * (n - len(roles))


def plan_text(n, off=()):
    roles = role_plan(n, off)
    out = []
    for r in BOARD_ORDER:
        c = roles.count(r)
        if c:
            out.append(r if c == 1 else f"{r}{c}")
    return " · ".join(out)


# ---------------------------------------------------------------- 페르소나

GENERIC = {
    "open": ["첫날이라 감이 없네요", "다들 한마디씩 해봐요", "누구부터 얘기할까요"],
    "accuse": ["{x}님이 좀 걸려요", "{x}님 아까 말이 애매했어요", "{x}님 쪽이 신경 쓰여요"],
    "agree": ["저도 {x}님", "{x}님 얘기 일리 있어요", "ㅇㅇ {x}님 좀"],
    "ask": ["{x}님은 누구 의심해요?", "{x}님 생각은요?", "{x}님 아까 그건 왜요?"],
    "short_ask": ["{x}님은요?", "{x}님?", "{x}님 생각은요?", "{x}님 의견도요"],
    "why": ["왜요?", "근거는요?", "진짜요?", "누구요?", "헐 왜요?"],
    "react": ["헐", "음", "흠..", "?", "오", "아하", "그쵸", "그러게요"],
    "defend": ["저 아니에요", "왜 저요? 근거 있어요?", "저 시민이에요 진짜", "제가요? 어제 제 투표 보세요", "아니 저 진짜 억울해요", "저 말고 다른 사람 봐주세요"],
    "claim_mafia": ["저 경찰이에요. {x}님 마피아 나왔어요", "경찰입니다. {x}님 마피아예요"],
    "claim_clear": ["저 경찰인데 {x}님은 마피아 아니에요"],
    "counter": ["제가 진짜 경찰이에요. {x}님 거짓말이에요"],
    "dead": ["{d}님 당했네요..", "아 {d}님 ㅠ", "{d}님 왜 죽었지"],
    "quiet": ["아직 모르겠네요", "좀 더 들어볼게요", "음 잘 모르겠어요"],
    "vote": ["저는 {x}님이요", "{x}님으로 갈게요"],
    "self_intro": ["안녕하세요 잘 부탁드려요", "반가워요 재밌게 해봐요"],
    "intro_react": ["{x}님 반가워요 ㅎㅎ", "{x}님 기억해 둘게요"],
    "night_mafia": ["오늘은 {x} 어때요?", "{x} 가죠"],
    "spy_contact": ["저 스파이예요. 같이 가요"],
    "last_defense": ["저 진짜 아니에요. 반대 눌러 주세요", "저 시민이에요. 저 죽으면 손해예요", "마피아는 따로 있어요. 믿어 주세요"],
    "judge_yes": ["찬성할게요", "아쉽지만 찬성"],
    "judge_no": ["저는 반대요", "아직 애매해서 반대"],
    "reply": ["음 아직 잘 모르겠어요", "글쎄요 좀 더 볼게요"],
    "accept": ["아 그럼 이해돼요", "그건 말 되네요", "오케이 일단 빼볼게요"],
    "press": ["그건 좀 약한데요?", "그럼 어제 투표는요?", "근거가 그거 하나예요?"],
    "doubt": ["다들 {x}님만 보는데 근거 있어요?", "{x}님 몰이 좀 급한 것 같은데요"],
    "change_mind": ["어제는 {y}님 봤는데 지금은 {x}님이요", "{y}님보다 {x}님이 더 걸려요"],
    "nudge": ["{x}님 답 좀 해주세요", "{x}님 아까 질문 답은요?"],
    "end_react": ["와 {x}님 마피아였어요?", "아 {x}님이었구나", "수고하셨어요 ㅋㅋ", "한 판 더 해요"],
    "ghost": ["어서 와요 ㅠ", "저도 당했어요 ㅋㅋ", "누가 마피아 같아요?", "이제 구경이나 해요"],
    "accuse_reason": ["{x}님 어제 투표가 이상했어요", "{x}님 어제 왜 그 사람 찍었어요?"],
}

GENERIC_B = {
    "open": ["첫날이라 감 없다", "다들 한마디씩 해봐", "누구부터 얘기할래"],
    "accuse": ["{x} 좀 걸려", "{x} 아까 말 애매했어", "{x} 쪽이 신경 쓰여"],
    "agree": ["나도 {x}", "{x} 얘기 일리 있어", "ㅇㅇ {x} 좀"],
    "ask": ["{x}는 누구 의심해?", "{x} 생각은?", "{x} 아까 그건 왜?"],
    "short_ask": ["{x}는?", "{x}?", "{x} 생각은?", "{x} 의견도"],
    "why": ["왜?", "근거는?", "ㄹㅇ?", "누구?", "헐 왜?"],
    "react": ["헐", "ㅇㅇ", "ㄹㅇ?", "?", "ㅋㅋㅋ", "오", "그치", "음.."],
    "defend": ["나 아닌데?", "왜 나야? 근거 있어?", "나 시민이야 진짜", "내가? 어제 내 투표 봐", "아니 나 진짜 억울해", "나 말고 딴 사람 봐줘"],
    "claim_mafia": ["나 경찰임. {x} 마피아 나왔어", "경찰이야. {x} 마피아야"],
    "claim_clear": ["나 경찰인데 {x}는 마피아 아니야"],
    "counter": ["내가 진짜 경찰이야. {x} 거짓말이야"],
    "dead": ["{d} 당했네..", "아 {d} ㅠ", "{d} 왜 죽었지"],
    "quiet": ["아직 모르겠다", "좀 더 들어볼게", "음 잘 모르겠어"],
    "vote": ["난 {x}", "{x}로 갈게"],
    "self_intro": ["잘 부탁해~", "반가워 재밌게 하자"],
    "intro_react": ["{x} 반가워 ㅎㅎ", "{x} 기억해 둘게"],
    "night_mafia": ["오늘 {x} 어때?", "{x} 가자"],
    "spy_contact": ["나 스파이야. 같이 가자"],
    "last_defense": ["나 진짜 아니야. 반대 눌러줘", "나 시민이야. 나 죽으면 손해야", "마피아 따로 있어. 믿어줘"],
    "judge_yes": ["찬성", "아쉽지만 찬성"],
    "judge_no": ["난 반대", "아직 애매해서 반대"],
    "reply": ["음 아직 잘 모르겠어", "글쎄 좀 더 볼게"],
    "accept": ["아 그럼 이해돼", "그건 말 되네", "ㅇㅋ 일단 빼볼게"],
    "press": ["그건 좀 약한데?", "그럼 어제 투표는?", "근거가 그거 하나야?"],
    "doubt": ["다들 {x}만 보는데 근거 있어?", "{x} 몰이 좀 급한 듯"],
    "change_mind": ["어제는 {y} 봤는데 지금은 {x}", "{y}보다 {x}가 더 걸려"],
    "nudge": ["{x} 답 좀 해줘", "{x} 아까 질문 답은?"],
    "end_react": ["와 {x} 마피아였어?", "아 {x}였구나", "수고 ㅋㅋ", "한 판 더 하자"],
    "ghost": ["어서 와 ㅠ", "나도 당했어 ㅋㅋ", "누가 마피아 같아?", "이제 구경이나 하자"],
    "accuse_reason": ["{x} 어제 투표 이상했어", "{x} 어제 왜 그 사람 찍었어?"],
}

# 평범한 회사 동료들. 성격은 살짝만 (과장된 캐릭터 연기 X)
# speech: jon(존댓말) / ban(반말) / mix(존댓말로 시작, 상대가 반말하면 따라서 말 놓음)
# t: 자기 기본 말투 대사, tj: 반말 캐릭터가 '반말 끄기'일 때 쓰는 존댓말, tb: mix 가 말 놓았을 때 반말
# talk: 하루 잡담 수, bold: 대담함(경찰 공개·가짜 경찰), trust: 남의 주장을 믿는 정도
PERSONAS = {
    "지훈": dict(desc="말이 짧고 직설적인 편. 담백한 존댓말", speech="jon", talk=2, bold=.8, trust=.5, t={
        "self_intro": ["지훈입니다", "지훈이요. 빨리 잡아봅시다"],
        "intro_react": ["{x}님 소개 짧네요 ㅋㅋ"],
        "accuse": ["{x}님 좀 이상한데요", "전 {x}님이요. 말이 걸려요", "{x}님 말 바꿨죠?", "{x}님 투표가 좀 그래요"],
        "agree": ["저도 {x}님", "{x}님 쪽 동의요", "ㅇㅇ {x}님"],
        "ask": ["{x}님은 누구요?", "{x}님 근거는요?", "{x}님 생각은?"],
        "defend": ["저 아닌데요?", "왜 저요? 근거 있어요?", "저 시민이에요. 어제 투표 보세요"],
        "quiet": ["아직 모르겠네요", "더 봐야겠어요"],
        "react": ["음", "흠", "그렇네요", "?"],
        "vote": ["{x}님 찍어요"], "dead": ["{d}님 갔네요.."]}),
    "서연": dict(desc="차분하고 근거를 따지는 편. 부드러운 존댓말", speech="jon", talk=2, bold=.5, trust=.6, t={
        "self_intro": ["서연이에요. 차분하게 해봐요", "안녕하세요 서연입니다"],
        "intro_react": ["{x}님 반가워요"],
        "accuse": ["{x}님이 아까 말 바꾼 게 걸려요", "{x}님 쪽이 좀 신경 쓰여요", "{x}님 투표 이유가 궁금해요"],
        "agree": ["{x}님 얘기 일리 있어요", "저도 그렇게 봤어요"],
        "ask": ["{x}님 생각은 어때요?", "{x}님은 왜 그렇게 보세요?", "근거가 뭐예요?"],
        "defend": ["저 시민이에요. 뭐가 걸리셨어요?", "그건 오해예요", "저 아니에요. 어제 제 투표 보시면 알아요"],
        "quiet": ["판단 보류할게요", "좀 더 들어볼게요"],
        "react": ["아..", "그렇군요", "음 일리 있네요"],
        "vote": ["저는 {x}님이요"], "dead": ["{d}님을 왜 노렸을까요"]}),
    "민재": dict(desc="장난기 있고 ㅋㅋ를 자주 씀. 반말", speech="ban", talk=2, bold=.6, trust=.4, t={
        "self_intro": ["민재 ㅎㅇ", "민재임 ㅋㅋ 잘 부탁"],
        "intro_react": ["{x} 소개 무난하네 ㅋㅋ", "{x} 너무 짧은데?ㅋㅋ"],
        "accuse": ["{x} 왜케 조용함 ㅋㅋ", "{x} 좀 수상한데", "{x} 아까 말 이상했어"],
        "agree": ["ㅋㅋ 나도 {x}", "{x} ㅇㅈ"],
        "ask": ["{x} 누구 찍을 거?", "{x}는 어케 봄?", "{x} 왜?"],
        "defend": ["나? ㅋㅋ 아닌데", "에이 나 아님", "아니 왜 나야 ㅋㅋ"],
        "quiet": ["흠 모르겠다", "아직 감 없음"],
        "react": ["ㅋㅋㅋ", "ㄹㅇ?", "헐", "오"],
        "vote": ["{x} 간다"], "dead": ["헐 {d} ㅠ"]}, tj={
        "self_intro": ["민재요 ㅋㅋ 잘 부탁드려요"], "intro_react": ["{x}님 소개 무난하네요 ㅋㅋ"],
        "accuse": ["{x}님 왜케 조용하세요 ㅋㅋ", "{x}님 좀 수상한데요"], "agree": ["ㅋㅋ 저도 {x}님"],
        "ask": ["{x}님 누구 찍을 거예요?"], "defend": ["저요?ㅋㅋ 아니에요", "에이 저 아님"],
        "quiet": ["흠 모르겠네요"], "vote": ["{x}님 갑니다"], "dead": ["헐 {d}님 ㅠ"]}),
    "수빈": dict(desc="조심스럽고 말수가 적은 편", speech="jon", talk=1, bold=.2, trust=.8, t={
        "self_intro": ["수빈이에요.. 잘 부탁드려요"],
        "intro_react": ["{x}님 반가워요"],
        "accuse": ["음.. {x}님이 좀 걸려요", "{x}님이 조금 이상해요"],
        "agree": ["저도 {x}님 생각했어요"],
        "ask": ["{x}님은 어떻게 보세요?", "{x}님은요?"],
        "defend": ["저 진짜 아니에요..", "저 시민이에요"],
        "quiet": ["잘 모르겠어요..", "음.."],
        "react": ["아..", "헉", "그렇구나.."],
        "vote": ["{x}님으로 할게요"], "dead": ["{d}님 ㅠㅠ"]}),
    "현우": dict(desc="의견을 정리하고 이끄는 편", speech="mix", talk=2, bold=.7, trust=.5, t={
        "self_intro": ["현우입니다. 의견 모아서 해봐요"],
        "intro_react": ["{x}님 잘 부탁해요"],
        "accuse": ["정리하면 {x}님이 제일 애매해요", "{x}님 쪽으로 모아볼까요"],
        "agree": ["{x}님으로 모아볼까요?"],
        "ask": ["{x}님 의견은요?", "{x}님은 누가 제일 수상해요?"],
        "defend": ["저 아니에요. 다른 분은요?"],
        "quiet": ["다들 한마디씩 해봐요", "일단 정리해보죠"],
        "react": ["오케이", "그렇네요", "음"],
        "vote": ["{x}님으로 갈게요"], "dead": ["{d}님 죽어서 누가 이득일까요"]}, tb={
        "accuse": ["정리하면 {x}가 제일 애매해", "{x} 쪽으로 모아볼까"], "agree": ["{x}로 모으자"],
        "ask": ["{x}는 어떻게 봐?", "{x} 의견은?"], "defend": ["나 아니야. 다른 사람은?"],
        "quiet": ["다들 한마디씩 해봐", "일단 정리하자"], "react": ["오케이", "그치", "음"],
        "vote": ["{x}로 갈게"], "dead": ["{d} 죽어서 누가 이득이지"]}),
    "지은": dict(desc="친근하고 리액션이 큰 편", speech="mix", talk=2, bold=.4, trust=.7, t={
        "self_intro": ["지은이에요~ 재밌게 해요!"],
        "intro_react": ["{x}님 반가워요~"],
        "accuse": ["{x}님 좀 수상해요ㅠ", "{x}님 왜 말이 없어요?"],
        "agree": ["맞아요 {x}님 좀..", "헐 저도요"],
        "ask": ["{x}님 진짜 시민이에요?", "{x}님 어떻게 생각해요?"],
        "defend": ["저 아니라구요ㅠ", "헐 저요?"],
        "quiet": ["다 착해 보여서 모르겠어요", "모르겠어요ㅠ"],
        "react": ["헐", "헐 대박", "ㅠㅠ", "오오"],
        "vote": ["{x}님.. 미안해요"], "dead": ["아 {d}님 ㅠㅠ"]}, tb={
        "accuse": ["{x} 좀 수상해ㅠ", "{x} 왜 말이 없어?"], "agree": ["맞아 {x} 좀..", "헐 나도"],
        "ask": ["{x} 진짜 시민이야?", "{x}는 어떻게 생각해?"], "defend": ["나 아니라구ㅠ", "헐 나?"],
        "quiet": ["다 착해 보여ㅠ", "모르겠어ㅠ"], "react": ["헐", "헐 대박", "ㅠㅠ", "오오"],
        "vote": ["{x}.. 미안"], "dead": ["아 {d} ㅠㅠ"]}),
    "도윤": dict(desc="말이 아주 짧고 과묵한 편", speech="jon", talk=1, bold=.5, trust=.6, t={
        "self_intro": ["도윤입니다"],
        "intro_react": ["반가워요"],
        "accuse": ["{x}님 수상해요", "{x}님"],
        "agree": ["동의요", "ㅇㅇ"],
        "ask": ["{x}님 근거는요?", "왜요?"],
        "defend": ["아니에요", "저 아님"],
        "quiet": ["좀 더 볼게요", "..."],
        "react": ["음", "ㅇㅇ", "?"],
        "vote": ["{x}님"], "dead": ["{d}님.."]}),
    "하린": dict(desc="눈치가 빠르고 의심이 많은 편. 반말", speech="ban", talk=2, bold=.6, trust=.3, t={
        "self_intro": ["하린. 다 의심할 거야 ㅎㅎ"],
        "intro_react": ["{x} 소개 너무 매끄러운데?"],
        "accuse": ["{x} 수상해", "{x} 아까부터 말 돌리던데", "{x} 투표 왜 그렇게 했어?"],
        "agree": ["그치 {x} 이상해", "나도 {x}"],
        "ask": ["{x} 근거 뭔데?", "{x} 왜 갑자기 조용해?"],
        "defend": ["나 아니거든?", "나 의심할 시간에 {x}나 봐"],
        "quiet": ["다 수상해 ㅋㅋ", "아직 몰라"],
        "react": ["흠", "그래?", "?", "의심스럽다"],
        "vote": ["{x}"], "dead": ["{d} 죽은 거 좀 이상하네"]}, tj={
        "self_intro": ["하린이요. 다 의심할 거예요 ㅎㅎ"], "intro_react": ["{x}님 소개 너무 매끄러운데요?"],
        "accuse": ["{x}님 수상해요", "{x}님 아까부터 말 돌려요"], "agree": ["저도 {x}님"],
        "ask": ["{x}님 근거가 뭐예요?"], "defend": ["저 아니거든요?"], "quiet": ["다 수상해요 ㅋㅋ"],
        "vote": ["{x}님이요"], "dead": ["{d}님 죽은 게 좀 이상하네요"]}),
    "태호": dict(desc="넉살 좋고 분위기를 푸는 편. 반말", speech="ban", talk=2, bold=.6, trust=.5, t={
        "self_intro": ["태호~ 살살 해줘"],
        "intro_react": ["{x} 반가워 ㅎㅎ"],
        "accuse": ["{x} 좀 흔들리는 것 같은데?", "{x} 땀 흘리는 거 보여 ㅋㅋ"],
        "agree": ["{x}에 한 표", "ㅇㅋ {x}"],
        "ask": ["{x} 한마디 해봐", "{x}는 누구 같아?"],
        "defend": ["아이고 나 아니야", "나 억울해 ㅋㅋ"],
        "quiet": ["다 비슷해 보이는데", "몰라 ㅋㅋ"],
        "react": ["ㅋㅋㅋ", "오~", "아이고", "ㅇㅋ"],
        "vote": ["{x} 간다~"], "dead": ["{d} 잘 가.."]}, tj={
        "self_intro": ["태호입니다~ 살살 해주세요"], "intro_react": ["{x}님 반가워요 ㅎㅎ"],
        "accuse": ["{x}님 좀 흔들리는 것 같은데요?"], "agree": ["{x}님 쪽에 한 표"],
        "ask": ["{x}님 한마디 해보세요"], "defend": ["아이고 저 아니에요"], "quiet": ["다 비슷해 보여요"],
        "vote": ["{x}님 갑니다"], "dead": ["{d}님 잘 가요.."]}),
    "유나": dict(desc="밝은 신입. 줄임말(ㅇㅈ, ㄹㅇ) 씀. 반말", speech="ban", talk=2, bold=.5, trust=.6, t={
        "self_intro": ["유나! 잘 부탁해ㅎㅎ"],
        "intro_react": ["{x} 반가워ㅎㅎ"],
        "accuse": ["{x} 좀 수상한데?", "{x} 말투 바뀜"],
        "agree": ["{x} ㅇㅈ", "ㄹㅇ {x}"],
        "ask": ["{x}는 누구 의심해?", "{x} ㄹㅇ 시민?"],
        "defend": ["나? 나 시민이야ㅠ", "ㄴㄴ 나 아님"],
        "quiet": ["감이 안 와ㅠ", "ㅁㄹ겠다"],
        "react": ["ㄹㅇ?", "ㅇㅈ", "헐", "ㅋㅋㅋ"],
        "vote": ["{x} 할게"], "dead": ["{d} ㅠ"]}, tj={
        "self_intro": ["유나예요! 잘 부탁드립니다ㅎㅎ"], "intro_react": ["{x}님 반가워요ㅎㅎ"],
        "accuse": ["{x}님 좀 수상하신데요?"], "agree": ["{x}님 ㅇㅈ"], "ask": ["{x}님 누구 의심하세요?"],
        "defend": ["저요? 저 시민이에요ㅠ"], "quiet": ["감이 안 와요ㅠ"], "vote": ["{x}님 할게요"], "dead": ["{d}님 ㅠ"]}),
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
    "reply": "방금 {x}이(가) 나한테 한 말에 대답하기: \"{q}\"",
    "chat": "지금 대화 흐름에 자연스럽게 한마디 (질문에 답하기 / 누군가의 말에 동의·반박 / 근거를 대고 의심 / 궁금한 걸 질문)",
    "ask": "{x}에게 의견이나 근거를 물어보기",
    "self_intro": "사회자가 부른 내 자기소개 차례: 내 캐릭터답게 가볍게 한 줄 자기소개 (직업은 절대 밝히지 말 것, 마피아면 시민인 척)",
    "intro_react": "방금 {x}이(가) 한 자기소개를 장난스럽게 살짝 의심하거나 놀리는 한마디",
    "accept": "{x}의 해명을 듣고 납득했다는 짧은 한마디",
    "press": "{x}의 해명이 약하다며 구체적인 허점을 짧게 물어보기",
    "doubt": "다들 {x}만 몰아가는데 근거가 약하다며 제동 걸기",
    "change_mind": "어제는 {y}을(를) 의심했지만 지금은 {x}이(가) 더 수상하다고 생각이 바뀐 걸 말하기",
    "accuse_reason": "{x}의 투표나 말 바꿈을 근거로 짧게 의심하기",
    "ghost": "죽은 사람끼리만 보는 유령 채팅에서 {x}의 말에 편하게 대답하기: \"{q}\" (이제 죽었으니 내 직업을 말해도 됨)",
}

PHASE_KO = {"intro": "직업 확인", "selfintro": "자기소개", "night": "밤", "morning": "아침", "day": "낮 토론", "vote": "투표",
            "defense": "최후 변론", "judge": "찬반 투표", "verdict": "결과 발표", "end": "게임 끝"}


LEAK_PAT = re.compile(r"역할로|역할\s*:|에\s*(짧게\s*)?(반응|대답|답변)\s*[.:]|반응\.|\d+\s*[~∼-]\s*\d*\s*자|글자|출력|대사\s*:|"
                      r"의도\s*:|지시|프롬프트|PASS|\bAI\b|[→\[\]]|^\(|\)$|인 척|캐릭터")


# 상황별 길이 (글자 수 상한). 평소엔 짧게, 해명·변론 때만 길게.
LIMITS = dict(react=8, short_ask=12, why=8, nudge=16, quiet=16, agree=16, ask=18, vote=16, judge_yes=14, judge_no=14,
              accept=16, press=18, doubt=22, dead=18, open=18, chat=22, reply=24, accuse=24, accuse_reason=24,
              change_mind=26, intro_react=20, self_intro=24, ghost=22, end_react=20, night_mafia=20,
              spy_contact=24, claim_mafia=30, claim_clear=30, counter=30, defend=40, last_defense=50)
LONG_INTENTS = ("defend", "last_defense")          # 길면 두 마디로 끊어 보냄
TEMPLATE_ONLY = ("react", "short_ask", "why", "nudge", "end_react", "night_mafia", "spy_contact")   # Claude 없이 바로
BIG_INTENTS = ("defend", "last_defense", "counter", "claim_mafia", "claim_clear")             # 판을 바꾸는 말 → 큰 모델
LEN_HINT = {"react": "감탄이나 한두 단어", "short_ask": "몇 단어짜리 짧은 질문", "defend": "해명이니 한두 문장, 조금 길어도 됨",
            "last_defense": "최후 변론이니 두세 문장으로 절박하게", "claim_mafia": "한 문장", "claim_clear": "한 문장",
            "counter": "한 문장"}
ROLE_WORDS = "의사|경찰|기자|군인|정치인|영매|스파이|마담|마피아"
SELF_ROLE_RE = re.compile(rf"(저|나|제가|내가|난|전)\s*(는|가|은)?\s*(진짜|사실)?\s*({ROLE_WORDS})(이|예|입|임|야|라|인데|이야|이에)")
BANMAL_END = re.compile(r"(야|냐|니|해|어|지|자|라|래|네|걸|거|임|음|함|됨|듯|ㅋ|ㅎ|ㅠ|\?|!|\.|~)\s*[?!.~ㅋㅎㅠ]*\s*$")


def has_batchim(word):
    c = (word or " ")[-1]
    return '가' <= c <= '힣' and (ord(c) - 0xAC00) % 28 != 0


def fix_josa(text, names):
    """'한욱는' → '한욱은' 처럼 이름 뒤 조사를 받침에 맞춤 (반말 호칭용)."""
    for n in sorted(set(names), key=len, reverse=True):
        if not n or n not in text:
            continue
        b = has_batchim(n)
        last_l = b and (ord(n[-1]) - 0xAC00) % 28 == 8          # ㄹ 받침
        pairs = [("는", "은"), ("가", "이"), ("를", "을"), ("야", "아"), ("랑", "이랑"), ("와", "과"), ("로", "으로")]
        for nb, wb in pairs:
            want = wb if b else nb
            if (nb, wb) == ("로", "으로") and last_l:
                want = "로"
            for cur in (nb, wb):
                text = re.sub(re.escape(n) + cur + r"(?![가-힣])" if cur in ("야", "아") else re.escape(n) + cur + r"(?=[\s?!.~,ㅋㅎㅠ]|$)",
                              n + want, text)
    return text


def norm(t):
    return re.sub(r"[\s.,!?~…ㅋㅎㅠㅜ'\"]+", "", t or "")


def is_banmal(text):
    """존댓말 표지(요/니다/세요) 없이 끝나는 말이면 반말로 본다."""
    t = (text or "").strip()
    if len(norm(t)) < 2 or re.search(r"(요|니다|세요|죠|십시오|까요)\s*[?!.~ㅋㅎㅠ]*\s*$", t):
        return False
    return bool(BANMAL_END.search(t)) or not re.search(r"요", t)


def hangul_ok(t):
    letters = [c for c in t if c.isalpha()]
    if not letters:
        return True
    return sum('가' <= c <= '힣' or 'ㄱ' <= c <= 'ㅣ' for c in letters) / len(letters) >= .6


def split_two(t, at=22):
    """긴 해명은 두 마디로: 문장 끝(. ! ? ~ ㅋ ㅠ)에서 나눔."""
    if len(t) <= at:
        return [t]
    cuts = [m.end() for m in re.finditer(r"[.!?~…]+|ㅋ+|ㅠ+", t) if 6 <= m.end() <= len(t) - 5]
    if not cuts:
        sp = [m.start() for m in re.finditer(" ", t) if 8 <= m.start() <= len(t) - 6]
        if not sp:
            return [t]
        c = min(sp, key=lambda i: abs(i - len(t) / 2))
        return [t[:c].strip(), t[c:].strip()]
    c = min(cuts, key=lambda i: abs(i - len(t) / 2))
    return [t[:c].strip(), t[c:].strip()]


def leaked(line, name=""):
    """Claude 가 대사 대신 지시문·메모를 내보냈는지 (예: '현우 역할로, ○○의 "…"에 짧게 반응. 15~')."""
    if not line:
        return False
    if LEAK_PAT.search(line):
        return True
    if name and re.match(rf"^{re.escape(name)}\s*(역할|으로|로서|입장)", line):
        return True
    if re.search(r"[0-9]+\s*[~∼]\s*$", line):    # 끝이 '15~' 처럼 잘린 숫자
        return True
    return False


def clean_line(line, name, limit=None):
    """Claude 답에서 대사 한 줄만 깔끔하게. 'PASS'(할 말 없음)면 빈 문자열."""
    line = (line or "").strip().split("\n")[0].strip()
    if line.upper().strip(" .\"'") in ("PASS", "(PASS)", "패스"):
        return ""
    for pre in (f"{name}:", f"{name} :", f"[{name}]"):
        if line.startswith(pre):
            line = line[len(pre):].strip()
    line = line.strip('"').strip("“”").strip()
    if leaked(line, name):                        # 지시문·메모가 그대로 나온 경우 → 기본 대사로
        return ""
    if not hangul_ok(line):
        return ""
    lim = limit or MAX_LEN
    if len(line) > lim:                           # 길면 문장 끝(. ! ? ~ …)이나 띄어쓰기에서 자름
        lo = max(2, lim // 3)
        cut = max((m.end() for m in re.finditer(r"[.!?~…ㅋㅠ]+|(요|다|죠|야|지|어|네)(?=\s)", line[:lim + 1])), default=0)
        if cut < lo:
            return ""                             # 자르면 어색해짐 → 기본 대사
        line = line[:cut].rstrip(" ,")
    return line

# 단계별 시간(초)
TIMES = dict(intro=8, selfintro=90, night=30, morning=7, day=100, vote=25, defense=20, judge=12, verdict=7)
FAST = dict(intro=3, selfintro=12, night=8, morning=4, day=20, vote=10, defense=6, judge=5, verdict=4)   # 살아있는 사람이 없을 때
MAX_LEN = 32          # AI 한 마디 최대 글자 수
DIRECT = ("reply", "defend", "last_defense", "counter", "claim_mafia", "claim_clear", "self_intro")   # 먼저 내보낼 말


def assign_roles(order, roles, humans, history, rng):
    """직업 배정. 사람은 시민이 덜 나오고, 지난 판과 같은 직업이 덜 나오게 가중치를 줌.
    history: pid → 최근 직업 목록(최신이 끝)."""
    pool = list(roles)
    out = {}
    hs = [p for p in order if p in humans]
    rng.shuffle(hs)
    for pid in hs:
        last = (history.get(pid) or [])[-3:]
        def w(r):
            x = 0.55 if r == "시민" else 1.0              # 사람은 시민 덜 나오게
            if last and r == last[-1]:
                x *= 0.35                                  # 지난 판과 같은 직업은 덜
            x *= 0.8 ** sum(1 for h in last[:-1] if h == r)
            if r == "시민" and last.count("시민") >= 2:
                x *= 0.5                                   # 최근 시민만 했으면 더 줄임
            return x
        kinds = sorted(set(pool))
        ws = [w(k) * pool.count(k) for k in kinds]
        pick = rng.choices(kinds, weights=ws)[0]
        pool.remove(pick)
        out[pid] = pick
    rest = [p for p in order if p not in out]
    rng.shuffle(pool)
    out.update(zip(rest, pool))
    return out


class Player:
    def __init__(self, pid, name, ai, persona, color, ch=None):
        self.id, self.name, self.ai, self.persona, self.color, self.ch = pid, name, ai, persona, color, ch
        self.role, self.alive, self.revealed = "시민", True, False


class Game:
    """방장 PC에서 도는 한 판. tick(now) 를 주기적으로 부르면 시간에 따라 진행된다."""

    def __init__(self, members, now=0.0, rng=None, times=None, reveal_on_death=True, stylist=None, off=(),
                 banmal=True, history=None):
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
        self.off = tuple(off)
        self.banmal = banmal                 # AI 반말 허용 (방장이 끌 수 있음)
        for pid, r in assign_roles(self.order, role_plan(len(members), self.off),
                                   {m["id"] for m in members if not m.get("ai")}, history or {}, self.rng).items():
            self.players[pid].role = r
        self.chat, self.seq, self.version = [], 0, 0
        self.phase, self.day, self.start, self.deadline = "intro", 0, now, now
        self.night, self.mafia_pick, self.votes, self.ready = {}, None, {}, set()
        self.ext_used = set()
        self.knowledge = {pid: {} for pid in self.order}   # 내가 아는 남의 정보 (pid → 표시 문자열)
        self.spy_contact, self.reporter_used, self.winner = False, False, None
        self.heat = {pid: 0.0 for pid in self.order}         # 공개적으로 받은 의심
        self.accused_today = {}                              # 오늘 누가 누구를 의심했나
        self.claims = []                                     # (day, 주장자, 대상, 'mafia'|'clear')
        self.pending = []                                    # (시각, 순번, fn)
        self.used = {}                                       # AI별 최근 쓴 문장 틀
        self.intro_order, self.intro_i, self.intro_tok, self.intro_cur = [], -1, 0, None   # 자기소개 순서
        self.accused, self.judge = None, {}                  # 최후 변론 대상 / 찬반 (pid → True/False)
        self.vote_log, self.ai_chain, self.day_seq = [], 0, 0
        self.shield = {p.id for p in self.players.values() if p.role == "군인"}   # 군인 방패 (1회)
        self.seduced, self.seduced_by = None, None                              # 마담이 오늘 유혹한 사람                 # 공개된 투표 기록 / AI 끼리 연달아 주고받은 횟수
        self.outq, self.next_ai_slot, self.ai_last, self.ai_post_t = [], 0.0, {}, 0.0   # AI 말 대기열 · 다음 말 가능 시각 · AI별 마지막 말
        self.llm_busy = 0
        self.dead_talk = {}                                  # 날짜별 죽음 반응 수
        self.recent_ai = []                                  # 최근 AI 말 (pid, 원문) — 비슷한 말 막기
        self.questions = []                                  # 열린 질문 dict(a=묻는 사람, t=받는 사람, q, at, done)
        self.talk_log = []                                   # (day, 'acc'|'def', 누가, 누구) — 의심·해명 기록
        self.ai_streak = 0                                   # 사람 말 없이 AI 만 연달아 한 말 수
        self.ai_log = []                                     # 판 기록 (AI 말 출처 포함)
        self.end_lines = []                                  # 게임 끝 감상 (시각, pid, 문장)
        self.last_react = -99
        self.pester = {}                                     # (day, 사람) → 재촉·질문 받은 수
        self._pseq = 0
        self.now = now
        self.brains = {pid: Brain(self, pid) for pid in self.order if self.players[pid].ai}
        self.sys("🎲 게임 시작! " + plan_text(len(members), self.off) + f" — 총 {len(members)}명")
        for p in self.players.values():
            self.sys(f"당신의 직업은 [{p.role}] 입니다. {ROLE_INFO[p.role][1]}", to=p.id)
            team = [q for q in self.players.values() if q.role in ("마피아", "마담") and q.id != p.id]
            if p.role in ("마피아", "마담") and team:
                self.sys("🔪 마피아 동료: " + ", ".join(f"{q.name}({q.role})" for q in team), to=p.id)
                for q in team:
                    self.knowledge[p.id][q.id] = q.role
        self._set_phase("intro", now)

    # ------------------------------------------------ 기본 도구
    def alive_list(self):
        return [self.players[i] for i in self.order if self.players[i].alive]

    def humans_alive(self):
        return [p for p in self.alive_list() if not p.ai]

    def name(self, pid):
        return self.players[pid].name if pid in self.players else "?"

    def mafia_chat_ids(self):
        ids = {p.id for p in self.players.values() if p.role in ("마피아", "마담")}
        if self.spy_contact:
            ids |= {p.id for p in self.players.values() if p.role == "스파이"}
        return ids

    def team_of(self, pid):
        return ROLE_INFO[self.players[pid].role][0]

    def post(self, kind, text, who=None, scope="all"):
        """kind: sys|say|mafia|dead. scope: all|mafia|dead|to:<pid>"""
        self.seq += 1
        self.chat.append(dict(s=self.seq, k=kind, w=who, n=self.name(who) if who else "", x=text, sc=scope))
        if kind == "say" and scope == "all":
            self._last_say = self.now
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
                pid, text, scope, kind, ph, day, intent = self.inbox.get_nowait()
            except queue.Empty:
                break
            prio = 0 if intent in DIRECT else 1
            at = now
            for i, part in enumerate(text.split("\n")):            # 긴 해명은 두 마디로 나눠 보냄
                at += min(2.0, 0.4 + len(part) * 0.04) if prio == 0 else min(3.0, 0.6 + len(part) * 0.06)  # 타자 시간
                self.outq.append(dict(pid=pid, text=part, scope=scope, kind=kind, ph=ph, day=day,
                                      prio=prio, ready=at, born=now, part=i, intent=intent))
        if self.phase == "end":
            for x in [e for e in self.end_lines if e[0] <= now]:
                self.end_lines.remove(x)
                self.post("say", x[2], x[1], "all")
            return
        self._pump_ai(now)
        self._dead_air(now)
        self._open_questions(now)
        due = sorted([x for x in self.pending if x[0] <= now])
        self.pending = [x for x in self.pending if x[0] > now]
        for _, _, fn in due:
            if self.phase != "end":
                fn()
        if self.phase != "end" and (now >= self.deadline or self._early(now)):
            self._advance(now)

    def _open_questions(self, now):
        """답이 없는 질문: AI 가 받은 건 10초 뒤 대답 시키고, 사람이 받은 건 15초 뒤 다른 AI 가 재촉."""
        if self.phase not in ("day", "vote"):
            return
        for q in self.questions:
            if q["done"] or q["day"] != self.day:
                continue
            t = q["t"]
            if not self.players[t].alive:
                q["done"] = True
            elif t in self.brains and now - q["at"] > 10:
                q["done"] = True
                b = self.brains[t]
                b.last_called = -99
                b.on_called(q["a"], q["q"], delay=0.3)
            elif t not in self.brains and now - q["at"] > 15:
                q["done"] = True
                bs = [b for b in self._live_brains() if b.id != q["a"]]
                if bs and self.pester_ok(t):
                    self.pester[(self.day, t)] = self.pester.get((self.day, t), 0) + 1
                    self.ai_line(self.rng.choice(bs).id, "nudge", x=t)

    def pester_ok(self, h):
        """조용한 사람을 하루 두 번 넘게 찌르지 않기 (자리 비웠을 수도)."""
        return self.pester.get((self.day, h), 0) < 2

    def _dead_air(self, now):
        """토론 중 12초 넘게 아무 말이 없으면 AI 한 명이 먼저 말을 꺼냄."""
        if self.phase != "day" or not self.humans_alive() or self.outq:
            return
        last = max([self.start] + [getattr(self, "_last_say", 0)])
        if now - last < 12 or now - getattr(self, "_last_nudge", 0) < 6:
            return
        bs = [b for b in self.brains.values() if self.players[b.id].alive]
        bs = [b for b in bs if now - self.ai_last.get(b.id, -99) > 9] or bs
        if bs:
            self._last_nudge = now
            random.choice(bs)._day_line(False)

    def _early(self, now):
        el = now - self.start
        if self.phase == "night":
            return False                       # 밤은 항상 정해진 시간만큼 (일찍 끝나면 누가 능력자인지 티 남)
        if self.phase == "day":
            hs = self.humans_alive()
            return el >= 10 and bool(hs) and all(p.id in self.ready for p in hs)
        if self.phase == "vote":
            return el >= 4 and all(p.id in self.votes for p in self.alive_list())
        if self.phase == "judge":
            return el >= 3 and all(p.id in self.judge for p in self.alive_list() if p.id != self.accused)
        return False

    def _pump_ai(self, now):
        """AI 말을 사람 대화 속도로 하나씩 내보낸다."""
        q = [o for o in self.outq if o["ph"] == self.phase and o["day"] == self.day
             and now - o["born"] < (20 if o["prio"] == 0 else 14)]
        # 같은 AI 의 잡담이 쌓이면 최신 1개만, 전체 잡담은 3개까지만
        seen, keep = set(), []
        for o in reversed(q):
            if o["prio"] == 1 and o["pid"] in seen:
                continue
            seen.add(o["pid"])
            keep.append(o)
        keep.reverse()
        chat = [o for o in keep if o["prio"] == 1]
        if len(chat) > 3:
            drop = {id(o) for o in chat[:-3]}
            keep = [o for o in keep if id(o) not in drop]
        self.outq = keep
        ready = [o for o in self.outq if o["ready"] <= now]
        if not ready:
            return
        o = min(ready, key=lambda o: (o["prio"], o["born"]))
        # 대답·해명은 1.2초만 띄우면 끼어들 수 있고, 잡담은 정해진 순번(2.5~4초 간격)을 기다림
        if now < (self.ai_post_t + 1.2 if o["prio"] == 0 else self.next_ai_slot):
            return
        self.outq.remove(o)
        silent = now - max(getattr(self, "_last_say", 0), self.start) > 9
        if o["prio"] == 1 and o.get("part", 0) == 0 and not silent:
            if now - self.ai_last.get(o["pid"], -99) < 9:
                return                                          # 같은 AI 잡담은 9초에 한 번
            if self.too_similar(o["text"], o["pid"]):
                return                                          # 방금 나온 말과 비슷하면 버림
        self.ai_last[o["pid"]] = now
        self.ai_post_t = now
        # 분위기: 첫날은 탐색이라 느긋하게, 사망 뒤엔 조금 빠르게, 짧은 리액션 뒤엔 바로 다음 말
        mood = 1.25 if self.day <= 1 else (0.9 if any(not p.alive for p in self.players.values()) else 1.0)
        gap = (2.2 + len(o["text"]) * 0.04 + self.rng.uniform(0, 1.6)) * mood
        if len(o["text"]) <= 6:
            gap *= .7
        self.next_ai_slot = now + gap
        self._ai_post(o["pid"], o["text"], o["scope"], o["kind"])

    def _advance(self, now):
        ph = self.phase
        if ph == "intro":
            self._begin_selfintro(now)
        elif ph == "selfintro":
            self.sys("🗣 자기소개 끝! 이제 소개를 곱씹으며 서로 의심해 보세요.")
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
        if self.phase == "night" and p.id == self.seduced:
            return False
        if p.role == "기자":
            return not self.reporter_used and self.day >= 2
        return p.role in ("마피아", "의사", "경찰", "스파이")

    def _begin_night(self, now):
        self.night, self.mafia_pick = {}, None
        if self.seduced and self.players[self.seduced].alive:
            self.sys("💋 마담에게 유혹당해 오늘 밤은 능력을 쓸 수 없어요…", to=self.seduced)
        self._set_phase("night", now)
        self.sys(f"🌙 {self.day}번째 밤이 되었습니다. 능력이 있는 사람은 대상을 고르세요.")
        for b in self._live_brains():
            b.on_night()

    def seduce(self, pid, target):
        """마담: 낮(토론·투표)에 한 명 유혹 → 그날 밤 능력 봉인. 하루 한 번."""
        p = self.players.get(pid)
        if (self.phase not in ("day", "vote") or not p or not p.alive or p.role != "마담"
                or self.seduced_by == pid or target not in self.targets(pid)):
            return False
        self.seduced, self.seduced_by = target, pid
        self.version += 1
        self.post("sys", f"💋 {p.name}님이 {self.name(target)}님을 유혹했습니다. 오늘 밤 그 사람은 능력을 못 써요.",
                  scope="mafia")
        return True

    def _begin_day(self, now):
        self.day += 1                                         # 낮이 오면 하루가 시작 (밤 N = 낮 N 다음 밤)
        self.seduced, self.seduced_by = None, None
        self.day_seq = self.seq
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

    def _begin_selfintro(self, now):
        self.intro_order = [p.id for p in self.alive_list()]
        self.rng.shuffle(self.intro_order)
        self.intro_i = -1
        self._set_phase("selfintro", now)
        self.deadline = now + min(self._dur("selfintro"), 4 + 10 * len(self.intro_order))
        self.sys("🙋 사회자: 본격적으로 시작하기 전에, 한 명씩 돌아가며 자기소개 해 볼까요? 이름 불리면 한 줄로!")
        self.schedule(2.5, self._next_intro)

    def _next_intro(self):
        """사회자가 다음 사람을 부름."""
        if self.phase != "selfintro":
            return
        self.intro_i += 1
        self.intro_tok += 1
        tok = self.intro_tok
        if self.intro_i >= len(self.intro_order):
            self.intro_cur = None
            self.deadline = min(self.deadline, self.now + 2)
            return
        pid = self.intro_order[self.intro_i]
        self.intro_cur = pid
        p = self.players[pid]
        self.sys(f"👉 {p.name}님, 자기소개 부탁해요!")
        if p.ai:
            self.schedule(self.rng.uniform(1.2, 2.2), lambda: self.phase == "selfintro" and self.ai_line(pid, "self_intro"))
            self.schedule(6.5, lambda: self.intro_tok == tok and self._after_intro(pid))
        else:
            self.schedule(12, lambda: self.intro_tok == tok and self._intro_timeout(pid))
        self.version += 1

    def _intro_timeout(self, pid):
        if self.phase == "selfintro":
            self.sys(f"⏱ {self.name(pid)}님은 수줍음이 많으시네요~ 다음 분!")
            self._next_intro()

    def _after_intro(self, pid):
        """소개가 끝나면 가끔 AI 하나가 살짝 의심 → 다음 사람."""
        if self.phase != "selfintro":
            return
        p = self.players[pid]
        react = [b for b in self._live_brains() if b.id != pid]
        if react and self.rng.random() < (0.5 if not p.ai else 0.25):
            b = self.rng.choice(react)
            self.ai_line(b.id, "intro_react", x=pid)
            self.schedule(3.5, self._next_intro)
        else:
            self.schedule(0.8, self._next_intro)

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
        ys = ", ".join(self.name(i) for i, v in self.judge.items() if v)
        ns = ", ".join(self.name(i) for i, v in self.judge.items() if v is False)
        self.sys(f"📊 찬반 결과: 찬성 {yes}" + (f"({ys})" if ys else "") + f" · 반대 {no}" + (f"({ns})" if ns else ""))
        if yes > no and self.players[t].role == "정치인":
            self.players[t].revealed = True
            self.sys(f"🏛 {self.name(t)}님은 정치인이라 투표로 처형되지 않습니다!")
            for b in self.brains.values():
                b.on_public_role(t)
        elif yes > no:
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
        elif kill and kill in self.shield:
            self.shield.discard(kill)
            kp = self.players[kill]
            kp.revealed = True
            self.sys(f"🪖 {kp.name}님이 마피아의 공격을 버텨 냈습니다! {kp.name}님은 군인입니다.")
            for b in self.brains.values():
                b.on_public_role(kill)
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
        for v, t in self.votes.items():
            if t:
                tally[t] = tally.get(t, 0) + self.vote_weight(v)
        if not tally:
            self.sys("모두 기권했습니다. 아무도 처형되지 않았어요.")
            return False
        top = max(tally.values())
        best = [t for t, c in tally.items() if c == top]
        voters = {}
        for v, t in self.votes.items():
            voters.setdefault(t, []).append(self.name(v) + ("×2" if self.vote_weight(v) == 2 and t else ""))
            self.vote_log.append((self.day, v, t))
            if t:
                self.heat[t] += .15
            for b in self._live_brains():
                b.on_vote_cast(v, t)
        summary = " / ".join(f"{self.name(t)} {c}표({', '.join(voters[t])})"
                             for t, c in sorted(tally.items(), key=lambda x: -x[1]))
        abst = voters.get(None, [])
        self.sys(f"📊 투표 결과: {summary}" + (f" / 기권: {', '.join(abst)}" if abst else ""))
        if len(best) > 1:
            self.sys("동점이라 아무도 처형되지 않았습니다.")
            return False
        self.accused = best[0]
        return True

    def vote_weight(self, pid):
        return 2 if self.players[pid].role == "정치인" else 1

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
        maf = [p for p in self.players.values() if p.role in MAFIA_TEAM]
        talkers = list(self.brains)
        self.rng.shuffle(talkers)
        for i, pid in enumerate(talkers[:2]):
            m = self.rng.choice([p for p in maf if p.id != pid] or maf)
            text = self.pick_text(pid, "end_react", dict(x=m.name, d="", y=""))
            self.end_lines.append((self.now + 2.5 + i * 3.5, pid, text))
        return True

    # ------------------------------------------------ 플레이어 행동
    def targets(self, pid):
        p = self.players.get(pid)
        if not p or not p.alive or self.phase not in ("night", "vote", "day"):
            return []
        alive = [q.id for q in self.alive_list()]
        if self.phase == "day":
            if p.role == "마담" and self.seduced_by != pid:
                return [t for t in alive if t != pid and self.players[t].role not in ("마피아", "마담")]
            return []
        if self.phase == "vote" and not (p.role == "마담" and self.seduced_by != pid and self._seduce_mode(pid)):
            return [t for t in alive if t != pid]
        if not self._can_act(p):
            return []
        if p.role == "마피아":
            return [t for t in alive if self.players[t].role != "마피아"]
        if p.role == "의사":
            return alive
        return [t for t in alive if t != pid]

    def _seduce_mode(self, pid):
        return False                                   # 투표 시간엔 클릭 = 투표 (유혹은 토론 때)

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
        self.votes[pid] = target
        self.version += 1
        return True

    def judge_vote(self, pid, yes):
        p = self.players.get(pid)
        if self.phase != "judge" or not p or not p.alive or pid == self.accused:
            return False
        self.judge[pid] = bool(yes)
        self.version += 1
        return True

    def extend(self, pid, sec):
        """낮 토론 시간 늘리기(+) / 줄이기(-). 살아있는 사람 한 명당 하루 한 번."""
        p = self.players.get(pid)
        if self.phase != "day" or not p or not p.alive or p.ai or (pid, self.day) in self.ext_used:
            return False
        self.ext_used.add((pid, self.day))
        if sec > 0:
            self.deadline += sec
            self.sys(f"⏱ {p.name}님이 토론 시간을 {sec}초 늘렸어요.")
        else:
            self.deadline = max(self.now + 10, self.deadline + sec)
            self.sys(f"⏩ {p.name}님이 토론 시간을 {-sec}초 줄였어요.")
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
            if not p.ai:
                self._ghost_reply(pid, text)
            return True
        if self.phase == "night" and p.role == "영매":
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
        self._track_q(pid, text)
        if not p.ai:
            self.next_ai_slot = max(self.next_ai_slot, self.now + 2.5)   # 사람 말 뒤엔 잠깐 숨 고르기
            self.ai_chain = 0
            self.ai_streak = 0
            self.ai_log.append(dict(t=round(self.now, 1), day=self.day, ph=self.phase, who=p.name, x=text, src="human"))
            if is_banmal(text):                                        # 반말로 부르면 mix 캐릭터는 말 놓음
                for t in self.mentions(text, pid):
                    if t in self.brains:
                        self.brains[t].loose = True
            if self.phase == "selfintro":
                if pid == self.intro_cur:
                    self.intro_tok += 1
                    tok = self.intro_tok
                    self.schedule(2.0, lambda: self.intro_tok == tok and self._after_intro(pid))
                return
            self._call_ai(pid, text)
        return True

    def _track_q(self, pid, text):
        """질문 기록: 이름을 넣어 물으면 그 사람에게 열린 질문. 받은 사람이 말하면 답한 걸로."""
        for q in self.questions:
            if not q["done"] and q["t"] == pid:
                q["done"] = True
        if "?" in text and self.phase in ("day", "vote"):
            for t in self.mentions(text, pid)[:1]:
                self.questions.append(dict(a=pid, t=t, q=text[:120], at=self.now, day=self.day, done=False))
        del self.questions[:-20]

    def _ghost_reply(self, pid, text):
        """죽은 사람이 유령 채팅에 말하면, 죽은 AI 가 대답 (영매는 듣기만)."""
        ghosts = [i for i, b in self.brains.items() if not self.players[i].alive]
        if not ghosts or self.now - getattr(self, "_ghost_t", -99) < 4:
            return
        named = [t for t in self.mentions(text, pid) if t in ghosts]
        self._ghost_t = self.now
        for i, t in enumerate((named or [self.rng.choice(ghosts)])[:1 + (len(ghosts) > 1 and self.rng.random() < .3)]):
            self.schedule(self.rng.uniform(1.5, 3) + i * 3,
                          lambda t=t: self.ai_line(t, "ghost", x=pid, q=text, scope="dead"))

    def _call_ai(self, pid, text):
        """사람이 AI 이름을 부르거나 질문하면 그 AI 가 바로 대답."""
        if self.phase not in ("day", "vote", "judge", "morning", "verdict", "intro"):
            return
        who = [t for t in self.mentions(text, pid) if t in self.brains and self.players[t].alive]
        if not who and ("?" in text or "다들" in text or "누구" in text or "AI" in text.upper()):
            cands = [b.id for b in self._live_brains()]
            if cands:
                who = [self.rng.choice(cands)]
        for i, t in enumerate(who[:2]):
            self.brains[t].on_called(pid, text, delay=self.rng.uniform(0.8, 1.6) + i * 3)

    # ------------------------------------------------ 채팅 해석 (AI 들이 듣는 내용)
    def mentions(self, text, speaker):
        out = []
        for p in self.players.values():
            if p.id != speaker and len(p.name) >= 1 and p.name in text:
                out.append(p.id)
        return out

    def _analyze(self, pid, text):
        if self.phase == "selfintro":
            return
        who = self.mentions(text, pid)
        claim = bool(CLAIM_RE.search(text))
        acc = any(w in text for w in ACCUSE_W)
        if re.search(r"누구\s*(를|을)?\s*(의심|찍|수상|투표)|누가\s*(수상|마피아|의심)", text):
            acc = False                                   # '누구 의심해?' 는 질문이지 의심이 아님
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
        accused_me = any(pid in ts for a, ts in self.accused_today.items() if a != pid)
        if accused_me and dfn and not (who and acc):          # 의심받던 사람의 해명
            self.talk_log.append((self.day, "def", pid, None, text[:60]))
            for b in self._live_brains():
                b.on_self_defense(pid, text)
        if who and acc and not (dfn and not "마피아" in text):
            for t in who:
                self.heat[t] += 1.0
                self.accused_today.setdefault(pid, set()).add(t)
                self.talk_log.append((self.day, "acc", pid, t, text[:60]))
            for b in self._live_brains():
                b.on_accuse(pid, who)
        elif who and dfn:
            for b in self._live_brains():
                b.on_defend(pid, who)

    # ------------------------------------------------ AI 발언
    # 말투·비슷한 말
    def speech(self, pid):
        """'ban' 반말 / 'jon' 존댓말."""
        b = self.brains.get(pid)
        sp = PERSONAS.get(self.players[pid].persona, {}).get("speech", "jon")
        if not self.banmal:
            return "jon"
        if sp == "mix":
            return "ban" if (b and b.loose) else "jon"
        return sp

    def templates(self, pid, intent):
        pers = PERSONAS.get(self.players[pid].persona, {})
        sp, own = self.speech(pid), pers.get("speech", "jon")
        if sp == "ban":
            mine = pers.get("t" if own == "ban" else "tb", {})
            gen = GENERIC_B
        else:
            mine = pers.get("tj" if own == "ban" else "t", {})
            gen = GENERIC
        own_l, gen_l = mine.get(intent) or [], gen.get(intent) or []
        if own_l and intent in ("defend", "accuse", "ask", "quiet", "agree", "react", "dead", "vote"):
            return own_l + [t for t in gen_l if t not in own_l]   # 캐릭터 대사 + 공통 대사 (반복 줄이기)
        return own_l or gen_l or gen["quiet"]

    def too_similar(self, text, pid=None):
        """최근 AI 말과 같은 말 / 거의 같은 말 / 같은 말끝 연속 / 같은 시작 단어면 True."""
        import difflib
        n = norm(text)
        if not n:
            return False
        rec = self.recent_ai[-10:]
        for _, t in rec:
            m = norm(t)
            if m == n or (len(n) >= 6 and difflib.SequenceMatcher(None, m, n).ratio() > .75):
                return True
        if len(n) >= 5:
            ends = [norm(t)[-2:] for _, t in rec[-2:] if len(norm(t)) >= 5]
            if len(ends) == 2 and all(e == n[-2:] for e in ends):
                return True                     # 세 마디 연속 같은 말끝
        if rec:
            w0 = (text.split() or [""])[0]
            p0 = (rec[-1][1].split() or [""])[0]
            if len(w0) >= 2 and w0 == p0:
                return True                     # 앞 사람과 같은 단어로 시작
        return False

    def pick_text(self, pid, intent, fmt):
        """템플릿 고르기: 내가 최근 쓴 것·이번 판에 나온 것·방금 나온 말과 비슷한 것은 피함."""
        pool = list(self.templates(pid, intent))
        used = self.used.setdefault(pid, [])
        seen = self.used.setdefault("*", [])
        self.rng.shuffle(pool)
        pool.sort(key=lambda t: (t in used, seen.count(t)))
        best = None
        for tmpl in pool:
            try:
                text = fix_josa(tmpl.format(**fmt), [v for v in fmt.values() if v])
            except (KeyError, IndexError):
                continue
            if best is None:
                best = (tmpl, text)
            if not self.too_similar(text, pid):
                best = (tmpl, text)
                break
        if best is None:
            t = self.rng.choice(self.templates(pid, "quiet"))
            best = (t, t.format(**fmt))
        used.append(best[0])
        del used[:-6]
        seen.append(best[0])
        return best[1]

    def ai_line(self, pid, intent, x=None, d=None, scope="all", q="", fb=None, y=None):
        p = self.players[pid]
        base = fb if (intent == "chat" and fb) else intent
        if intent == "reply":                         # 대체 대사: 의심받았으면 해명, 아니면 '모르겠다'
            base = "defend" if (p.name in q and any(w in q for w in ACCUSE_W)) else "quiet"
        if intent == "ghost":
            base = "ghost"
        fmt = dict(x=self.name(x) if x else "", d=self.name(d) if d else "", y=self.name(y) if y else "")
        text = self.pick_text(pid, base, fmt)
        if base in LONG_INTENTS:
            text = "\n".join(split_two(text))
        kind = {"mafia": "mafia", "dead": "dead"}.get(scope, "say")
        phase, day = self.phase, self.day
        cool = getattr(self.stylist, "cool_until", 0) if self.stylist else 0
        use_llm = (self.stylist and self.llm_busy < 6 and intent not in TEMPLATE_ONLY and time.time() >= cool)
        if use_llm:
            req = self.llm_request(pid, intent, x, d, scope, q, text.replace("\n", " "), y=y)
            done = {"v": False}
            self.llm_busy += 1
            big = intent in BIG_INTENTS

            def cb(line):
                if done["v"]:
                    return
                done["v"] = True
                self.llm_busy = max(0, self.llm_busy - 1)
                said = clean_line(line, p.name, LIMITS.get(intent, MAX_LEN)) if line is not None else ""
                why = "claude" if said else ("timeout/error" if line is None else "filtered")
                if said and intent not in ("claim_mafia", "claim_clear", "counter", "last_defense", "ghost") \
                        and SELF_ROLE_RE.search(said) and not re.search(r"시민(이|예|입|임|야)", said):
                    said, why = "", "role-leak"               # 시키지 않았는데 자기 직업을 흘림
                if said and intent in ("claim_mafia", "claim_clear", "counter") and "경찰" not in said:
                    said, why = "", "claim-mismatch"          # 경찰 주장인데 경찰 얘기가 없음
                if said and intent in ("claim_mafia", "claim_clear", "counter") and x and self.name(x) not in said:
                    said, why = "", "claim-mismatch"
                if said and p.role in MAFIA_TEAM and scope == "all" and re.search(r"(우리|동료|같은 편)", said):
                    said, why = "", "team-leak"
                if said and intent not in DIRECT and self.too_similar(said, pid):
                    said, why = "", "similar"
                if said and intent in LONG_INTENTS:
                    said = "\n".join(split_two(said))
                self.ai_log.append(dict(t=round(self.now, 1), day=day, ph=phase, who=p.name, intent=intent,
                                        src=why, x=(said or text).replace("\n", " / ")))
                # 실패·늦음·걸러짐 → 기본 대사로 (조용해지지 않게)
                self.inbox.put((pid, said or text, scope, kind, phase, day, intent))
            self.stylist(req, cb)
            self.schedule(11 if big else 8, lambda: cb(None))
            return
        self.ai_log.append(dict(t=round(self.now, 1), day=day, ph=phase, who=p.name, intent=intent, src="template",
                                x=text.replace("\n", " / ")))
        self.inbox.put((pid, text, scope, kind, phase, day, intent))

    def llm_request(self, pid, intent, x, d, scope, q, default, y=None):
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
            if m["sc"] == "all" or (m["sc"] == "mafia" and pid in mids) or (m["sc"] == "dead" and not p.alive):
                tag = "[마피아채팅] " if m["sc"] == "mafia" else ""
                recent.append(f"{tag}{m['n'] or '[사회자]'}: {m['x']}")
        b = self.brains.get(pid)
        sus = [self.name(t) for t in sorted(b.others(), key=b.score, reverse=True)[:2]] if b else []
        votes = [f"{d}일 투표: {self.name(v)}→{self.name(t) if t else '기권'}" for d, v, t in self.vote_log[-16:]]
        fmt = dict(x=self.name(x) if x else "", d=self.name(d) if d else "", q=q[:120], y=self.name(y) if y else "")
        openq = [f"{self.name(o['a'])}→{self.name(o['t'])}: {o['q']}" for o in self.questions
                 if not o["done"] and o["day"] == self.day][-4:]
        mine_q = [f"{self.name(o['a'])}이(가) 나에게: {o['q']}" for o in self.questions
                  if not o["done"] and o["t"] == pid][-2:]
        tlog = [(f"{self.name(a)}→{self.name(t)} 의심" if k == "acc" else f"{self.name(a)} 해명: {x_}")
                for dd, k, a, t, x_ in self.talk_log if dd == self.day][-8:]
        my_past = [f"{dd}일 {self.name(t)} 의심" for dd, k, a, t, _ in self.talk_log if k == "acc" and a == pid and dd < self.day][-3:]
        my_past += [f"{dd}일 투표 {self.name(t) if t else '기권'}" for dd, v, t in self.vote_log if v == pid][-3:]
        ends = [norm(t)[-3:] for _, t in self.recent_ai[-5:] if norm(t)]
        return dict(name=p.name, persona=pers.get("desc", ""), role=p.role, team=self.team_of(pid), mates=mates,
                    intent=intent, intent_desc=INTENT_DESC.get(intent, intent).format(**fmt),
                    default=default, day=self.day, phase=PHASE_KO.get(self.phase, self.phase),
                    accused=self.name(self.accused) if self.accused else "",
                    alive=[o.name for o in self.alive_list()], public=public, claims=claims,
                    known={self.name(k): v for k, v in self.knowledge[pid].items()}, suspects=sus,
                    recent=recent[-20:], scope=scope, votes=votes,
                    speech=self.speech(pid), length=LEN_HINT.get(intent, "아주 짧게, 보통 한두 마디. 근거를 댈 때만 한 문장"),
                    open_q=openq, my_q=mine_q, talk_log=tlog, my_past=my_past, ends=ends,
                    big=intent in BIG_INTENTS, dead=not p.alive)

    def _ai_post(self, pid, text, scope, kind):
        p = self.players[pid]
        if scope == "dead":
            if not p.alive and self.phase != "end":
                self.post("dead", text, pid, "dead")
            return
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
        self.recent_ai.append((pid, text))
        del self.recent_ai[:-12]
        self.ai_streak += 1
        self._analyze(pid, text)
        self._track_q(pid, text)
        # 리액션: 의심·경찰 주장 같은 큰 말에는 다른 AI 한 명이 짧게 반응 (템플릿, 4초에 한 번)
        big = CLAIM_RE.search(text) or (self.mentions(text, pid) and any(w in text for w in ACCUSE_W))
        if big and self.phase == "day" and self.now - self.last_react > 4 and self.rng.random() < .45:
            bs = [b for b in self._live_brains() if b.id != pid]
            if bs:
                self.last_react = self.now
                rb = self.rng.choice(bs)
                kind = self.rng.choice(["react", "why"])
                self.schedule(self.rng.uniform(0.8, 1.8), lambda: self.phase == "day" and rb.p.alive
                              and self.ai_line(rb.id, kind))
        if self.stylist and self.phase in ("day", "vote") and self.ai_chain < 3:
            for t in self.mentions(text, pid):
                if t in self.brains and self.players[t].alive and self.rng.random() < .5:
                    self.ai_chain += 1
                    self.brains[t].on_called(pid, text, delay=self.rng.uniform(1.0, 2.0))
                    break

    # ------------------------------------------------ 화면용 정보
    def view(self, pid, now):
        me = self.players.get(pid)
        ended = self.phase == "end"
        mine = self.knowledge.get(pid, {})
        mafia_ids = self.mafia_chat_ids()
        rows = []
        tally = {}
        if self.phase in ("defense", "judge", "verdict"):       # 투표 중에는 몇 표인지 비공개 (소신 투표)
            for v, t in self.votes.items():
                if t:
                    tally[t] = tally.get(t, 0) + self.vote_weight(v)
        for i in self.order:
            p = self.players[i]
            role = p.role if (ended or p.revealed or i == pid) else None
            tag = mine.get(i)
            if role is None and tag in ROLE_INFO:
                role = tag
            rows.append(dict(id=i, n=p.name, ai=p.ai, a=p.alive, c=p.color, r=role, tag=tag, ch=p.ch,
                             v=tally.get(i, 0), voted=i in (self.judge if self.phase == "judge" else self.votes),
                             rd=i in self.ready, acc=(i == self.accused),
                             turn=(self.phase == "selfintro" and i == self.intro_cur),
                             mp=(self.mafia_pick == i and pid in mafia_ids and self.phase == "night")))
        vis = []
        for m in self.chat:
            sc = m["sc"]
            ok = (sc == "all" or ended or sc == f"to:{pid}"
                  or (sc == "mafia" and pid in mafia_ids)
                  or (sc == "dead" and me is not None and (not me.alive or me.role == "영매")))
            if ok:
                vis.append(dict(s=m["s"], k=m["k"], n=m["n"], x=m["x"], w=m["w"], me=m["w"] == pid and pid is not None,
                                p=sc.startswith("to:")))
        v = dict(phase=self.phase, day=self.day, remain=max(0, int(self.deadline - now + .99)),
                 total=max(int(self._dur(self.phase)), int(self.deadline - self.start)) if self.phase != "end" else 0,
                 players=rows, chat=vis[-80:], winner=self.winner, plan=plan_text(len(self.order), self.off),
                 accused=self.name(self.accused) if self.accused else "",
                 turn=self.name(self.intro_cur) if self.phase == "selfintro" and self.intro_cur else "")
        if me:
            act = None
            if self.phase == "night" and self._can_act(me):
                act = {"마피아": "제거할 사람을 고르세요", "의사": "살릴 사람을 고르세요",
                       "경찰": "조사할 사람을 고르세요", "스파이": "직업을 알아낼 사람을 고르세요",
                       "기자": "취재할 사람을 고르세요 (게임 중 1번)"}[me.role]
            elif self.phase == "day" and me.alive and me.role == "마담" and self.seduced_by != pid:
                act = "💋 유혹할 사람을 고르세요 (그 사람은 오늘 밤 능력을 못 써요)"
            elif self.phase == "vote" and me.alive:
                act = "처형할 사람에게 투표하세요"
            elif self.phase == "judge" and me.alive and pid != self.accused:
                act = "찬성 / 반대를 눌러 주세요"
            v["me"] = dict(id=pid, role=me.role, desc=ROLE_INFO[me.role][1], alive=me.alive,
                           team=self.team_of(pid), act=act, targets=self.targets(pid),
                           pick=(self.night.get(pid) if self.phase == "night" else
                                 self.seduced if (self.phase == "day" and self.seduced_by == pid) else self.votes.get(pid)),
                           picked=(pid in self.night) if self.phase == "night" else (pid in self.votes),
                           ready=pid in self.ready, mchat=pid in mafia_ids,
                           ext=(self.phase == "day" and me.alive and not me.ai and (pid, self.day) not in self.ext_used), judge=self.judge.get(pid),
                           accused=pid == self.accused, myturn=(self.phase == "selfintro" and pid == self.intro_cur),
                           medium=me.role == "영매",
                           can_chat=(ended or not me.alive or ((self.phase != "night" or pid in mafia_ids or me.role == "영매")
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
        self.loose = False          # mix 캐릭터: 상대가 반말하면 말 놓음
        self.hist = {}              # day → 그날 내가 가장 의심한 사람
        self.changed = set()

    # 편의
    @property
    def role(self):
        return self.p.role

    @property
    def mafia_side(self):
        return self.role in MAFIA_TEAM

    def team(self):
        g = self.g
        if self.role in ("마피아", "마담"):
            return {p.id for p in g.players.values() if p.role in ("마피아", "마담")} | (
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
        s = self.sus.get(t, 0) + g.heat[t] * (0.15 + self.trust * 0.25) + self.rng.uniform(0, 1.0)
        r = self.truth.get(t)
        if r in MAFIA_TEAM:
            s += 100
        elif r:
            s -= 100
        if g.players[t].revealed and g.players[t].role == "정치인":
            s -= 50
        return s

    def conf(self, t):
        """무작위 없이 '이 사람이 마피아라고 얼마나 확신하나' (시민 AI 용)."""
        r = self.truth.get(t)
        if r in MAFIA_TEAM:
            return 100
        if r:
            return -100
        return self.sus.get(t, 0) + self.g.heat[t] * (0.15 + self.trust * 0.25)

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
        if self.role == "마담":                       # 토론 중에 한 명 유혹: 경찰이라 밝힌 사람 > 수상한 정보직
            g.schedule(self.rng.uniform(12, 35), self._seduce)
        self.spoke = 0
        if g.day >= 2:                                       # 생각이 바뀌었으면 한 번 말하기
            y = self.hist.get(g.day - 1)
            t = self.top()
            if (y and t and y != t and g.players[y].alive and not self.mafia_side and self.conf(t) > self.conf(y) + .5
                    and self.rng.random() < .35):
                g.schedule(self.rng.uniform(15, 40), lambda: g.phase == "day" and self.p.alive
                           and g.ai_line(self.id, "change_mind", x=t, y=y))
        n = max(1, self.talk + self.rng.choice([-1, 0, 0]))
        span = max(12, g.times["day"] * .7) if g.humans_alive() else 12
        times = sorted([self.rng.uniform(3, 12)] + [self.rng.uniform(15, span) for _ in range(n - 1)])
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
        if g.day == 1 and first and not g.stylist and self.rng.random() < .55:
            g.ai_line(self.id, "open")
            return
        hs = [p.id for p in g.alive_list() if not p.ai]
        hs = [h for h in hs if g.pester_ok(h)]
        if g.ai_streak >= 5 and hs:                          # AI 끼리만 떠들면 사람에게 말 돌리기
            quiet_h = min(hs, key=lambda h: sum(1 for m in g.chat if m["w"] == h and m["s"] > g.day_seq))
            g.pester[(g.day, quiet_h)] = g.pester.get((g.day, quiet_h), 0) + 1
            g.ai_streak = 0
            g.ai_line(self.id, self.rng.choice(["ask", "short_ask"]), x=quiet_h)
            return
        last = next((m for m in reversed(g.chat) if m["k"] == "say" and m["sc"] == "all"), None)
        if last and last["s"] > g.day_seq and last["w"] and last["w"] != self.id and g.players[last["w"]].alive \
                and g.now - getattr(g, "_last_say", 0) < 8 and self.rng.random() < .35:
            hot = bool(CLAIM_RE.search(last["x"])) or (bool(g.mentions(last["x"], last["w"]))
                                                       and any(w in last["x"] for w in ACCUSE_W))
            k = self.rng.random()
            if hot and k < .5:
                g.ai_line(self.id, "why")                     # 의심·주장에 '왜?' '근거는?'
            elif k < .75:
                g.ai_line(self.id, "react")                   # 짧게 받아치기 (주고받는 느낌)
            else:
                g.ai_line(self.id, "short_ask", x=last["w"])
            return
        fb, fx = self.pick_line()
        if fb in ("accuse", "agree") and fx:
            self.hist[g.day] = fx
        if g.stylist:
            g.ai_line(self.id, "chat", x=fx, fb=fb)
            return
        if fb:
            g.ai_line(self.id, fb, x=fx)

    def pick_line(self):
        """규칙으로 고른 '할 말' → (의도, 대상). Claude 가 없거나 실패할 때 씀."""
        g = self.g
        t = self.top()
        if t is None:
            return "quiet", None
        others_accuse = any(t in ts for a, ts in g.accused_today.items() if a != self.id)
        already = t in g.accused_today.get(self.id, set())
        sc = self.score(t)
        cf = self.conf(t) if not self.mafia_side else sc
        if sc > -500 and not already and (cf >= 1.5 or (others_accuse and cf >= 1.1)):
            n_acc = sum(1 for a, ts in g.accused_today.items() if t in ts and a != self.id)
            if n_acc >= 3 and cf < 2.5 and not self.mafia_side and self.rng.random() < .5:
                return "doubt", t                             # 다들 한 사람만 몰면 제동
            voted_y = any(v == t and x for d, v, x in g.vote_log if d == g.day - 1)
            if not others_accuse and voted_y and self.rng.random() < .35:
                return "accuse_reason", t                     # 어제 투표를 근거로
            return ("agree" if others_accuse else "accuse"), t
        hot = [x for x in self.others() if sum(1 for a, ts in g.accused_today.items() if x in ts) >= 3]
        if hot and not self.mafia_side and self.conf(hot[0]) < 1.2 and self.rng.random() < .4:
            return "doubt", hot[0]
        accusers = [a for a in g.accused_today if a != self.id and g.players[a].alive]
        talkers = [m["w"] for m in g.chat if m["s"] > g.day_seq and m["w"] and m["w"] != self.id
                   and m["k"] == "say" and g.players[m["w"]].alive]
        pool = accusers or talkers or [o for o in self.others() if not g.players[o].ai and g.pester_ok(o)]
        if pool and self.rng.random() < .75:
            return "ask", self.rng.choice(pool)
        return "quiet", None

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
        if self.rng.random() < .15:
            g.ai_line(self.id, "judge_yes" if yes else "judge_no", x=t)

    def on_spared(self, pid):
        if pid in self.sus and not self.mafia_side:
            self.sus[pid] -= .5

    def on_called(self, caller, text, delay=0.5):
        """사람이 내 이름을 부르거나 질문함 → 바로 대답 (Claude 가 있으면 맥락 있게)."""
        g = self.g
        if not self.p.alive or g.now - getattr(self, "last_called", -99) < 6:
            return
        self.last_called = g.now
        accused_me = (self.p.name in text and any(w in text for w in ACCUSE_W)
                      and not re.search(r"누구\s*(를|을)?\s*(의심|찍|수상|투표)|누가\s*(수상|마피아|의심)", text))
        if g.stylist:
            g.schedule(delay, lambda: self.p.alive and g.ai_line(self.id, "reply", x=caller, q=text))
        elif accused_me:
            return                                    # on_accuse 가 해명함
        else:
            g.schedule(delay + .6, lambda: self.p.alive and g.ai_line(self.id, "reply", x=caller, q=text))

    def _seduce(self):
        g = self.g
        c = g.targets(self.id)
        if g.phase != "day" or not c:
            return
        claimed = [t for t in c if any(cl[1] == t for cl in g.claims)]
        t = claimed[0] if claimed else max(c, key=lambda x: (self.truth.get(x) in ("경찰", "의사"), self.rng.random()))
        g.seduce(self.id, t)

    def on_vote(self):
        g = self.g
        self.g.schedule(self.rng.uniform(3, min(14, g._dur("vote") - 2)), self._cast)

    def _cast(self):
        g = self.g
        if g.phase != "vote" or not self.p.alive:
            return
        t = self.top()
        sc = self.score(t) if t else -999
        if t is not None and not self.mafia_side and self.conf(t) < 1.3:
            # 확신 없음 → 반은 기권, 반은 남 말 말고 내 의심(sus)대로
            if self.rng.random() < .5:
                g.vote(self.id, None)
                if self.rng.random() < .25:
                    g.ai_line(self.id, "quiet")
                return
            own = [c for c in self.others() if self.truth.get(c) not in ("town?",) and self.truth.get(c) not in ROLE_INFO
                   or self.truth.get(c) in MAFIA_TEAM]
            t = max(own or self.others(), key=lambda c: self.sus.get(c, 0) + self.rng.uniform(0, .8))
        elif self.mafia_side and t is not None and g.heat[t] < 1.0:
            t = self.rng.choice([c for c in self.others() if c not in self.team()] or [t])   # 묻어가기: 흐름 없으면 아무나
        if t is None or sc < -500:
            g.vote(self.id, None)
            return
        g.vote(self.id, t)
        if self.rng.random() < .2:
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
                    self._say(self.rng.uniform(2.0, 4.0), "defend", x=accuser)
            elif t in self.sus:
                self.sus[t] += .3 * self.trust
                if self.truth.get(t) and self.truth[t] not in MAFIA_TEAM and not self.mafia_side:
                    self.sus[accuser] = self.sus.get(accuser, 0) + .7   # 확실한 시민을 몰면 수상

    def on_self_defense(self, speaker, text):
        """나에게 의심받던 사람이 해명함 → 근거가 있으면 의심을 낮추고 말로 반응."""
        g = self.g
        if speaker == self.id or speaker not in g.accused_today.get(self.id, set()):
            return
        reasoned = len(text) >= 12 or any(w in text for w in ("어제", "투표", "아까", "왜냐", "경찰", "봤", "찍었"))
        if not self.mafia_side:
            self.sus[speaker] = self.sus.get(speaker, 0) - (.4 if reasoned else .1) * (.5 + self.trust)
        if self.rng.random() < .6 and g.phase in ("day", "vote"):
            keep = self.mafia_side or self.truth.get(speaker) in MAFIA_TEAM or self.conf(speaker) > 2.2 or not reasoned
            self._say(self.rng.uniform(1.5, 3.0), "press" if keep else "accept", x=speaker)

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


def make_claude_stylist(api_key, model="claude-haiku-5-5", model_big=None):
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
            "군인": "마피아 공격을 한 번 버팀. 정체는 숨겨서 마피아가 헛수고하게 하는 게 유리.",
            "정치인": "투표 2표, 투표로 처형 안 됨. 투표 흐름을 이끌 수 있음.",
            "영매": "밤에 유령 채팅으로 죽은 사람 정보를 들음. 출처를 너무 드러내면 마피아에게 노려짐.",
            "마담": "마피아 편이지만 시민인 척. 마피아 동료를 은근히 감싸기.",
            "시민": "추리로 마피아를 찾기. 말이 앞뒤가 안 맞는 사람, 남을 몰아가는 사람을 의심.",
        }.get(req["role"], "")
        ban = req.get("speech") == "ban"
        tone = ("반말. 친구한테 치듯이. 호칭은 이름만(예: 민재, 민재야). '~요' 쓰지 말 것"
                if ban else "존댓말. 호칭은 '○○님'")
        good = ("민재 어제 기권했던데 왜? / ㄹㅇ? / 그건 좀 억지 아님? / 아 그럼 서연은 빼자 / 나 아니야 ㅋㅋ 어제 지훈 찍었잖아"
                if ban else "민수님 어제 기권하셨던데 왜요? / 헐 / 그건 좀 억지 같은데요 / 아 그럼 서연님은 빼죠 / 저 아니에요 ㅋㅋ")
        system = (f"당신은 회사 동료들과 단톡방에서 마피아 게임 중인 '{req['name']}'입니다.\n"
                  f"성격: {req['persona']}. 캐릭터 연기를 과장하지 말고 평범한 직장인처럼.\n"
                  f"말투: {tone}\n"
                  f"비밀 정보: 당신의 진짜 직업은 '{req['role']}'({team}). {strat}\n"
                  + (f"마피아 동료: {', '.join(req['mates'])}\n" if req["mates"] else "")
                  + RULES + "\n"
                  "[출력] 채팅창에 그대로 올라갈 대사만 출력. 상황 설명·메모·'○○ 역할로' 같은 지시문·글자 수 표기 금지.\n"
                  "[말하는 법]\n"
                  "- 단톡처럼 주고받기. 보통은 아주 짧게(한두 마디, 감탄·되묻기도 OK). 길게 말하는 건 해명할 때뿐.\n"
                  "- 방금 나온 말, 특히 나한테 온 질문에 먼저 반응. 혼자 딴 얘기 금지.\n"
                  "- 의심하려면 근거(투표 기록, 말 바꿈, 경찰 주장) 하나. 근거 없으면 질문.\n"
                  "- 누가 해명하면 납득하거나 허점을 구체적으로 묻기. 같은 의심 고집 금지.\n"
                  "- 다른 사람과 같은 말끝·같은 시작으로 말하지 않기. 앞에서 한 말 반복 금지.\n"
                  "- 시키지 않았는데 자기 직업 밝히지 않기(마피아는 거짓말 가능).\n"
                  f"[좋은 예] {good}\n"
                  "[나쁜 예] 제가 사회생활 30년인데 눈빛을 보면 다 압니다 / 확률적으로 분석한 결과 마피아일 가능성이 높습니다")
        lines = [f"[현재] {req['day']}일째 · {req['phase']}" + (f" · 최후 변론 대상: {req['accused']}" if req["accused"] else ""),
                 f"살아있는 사람: {', '.join(req['alive'])}"]
        if req.get("dead"):
            lines.append("(나는 이미 죽었음. 유령 채팅은 죽은 사람끼리만 봄)")
        if req["public"]:
            lines.append("공개된 사실: " + " / ".join(req["public"]))
        if req["claims"]:
            lines.append("경찰 주장: " + " / ".join(req["claims"]))
        if req["known"]:
            lines.append("내가 몰래 알아낸 것: " + ", ".join(f"{k}={v}" for k, v in req["known"].items()))
        if req.get("votes"):
            lines.append("지난 투표 기록: " + " / ".join(req["votes"]))
        if req.get("talk_log"):
            lines.append("오늘 의심·해명: " + " / ".join(req["talk_log"]))
        if req.get("my_past"):
            lines.append("내가 전에 한 것: " + ", ".join(req["my_past"]))
        if req["suspects"]:
            lines.append("(속으로 의심 중: " + ", ".join(req["suspects"]) + " — 근거가 있을 때만 말하기)")
        if req.get("my_q"):
            lines.append("나한테 온 답 안 한 질문: " + " / ".join(req["my_q"]))
        elif req.get("open_q"):
            lines.append("답 안 나온 질문: " + " / ".join(req["open_q"]))
        lines.append("최근 채팅:\n" + ("\n".join(req["recent"]) or "(아직 없음)"))
        if req.get("ends"):
            lines.append("(최근 말끝: " + ", ".join(req["ends"]) + " — 이것과 다르게)")
        where = {"mafia": "마피아끼리만 보는 비밀 채팅", "dead": "유령 채팅"}.get(req["scope"], "전체 채팅")
        lines.append(f"\n지금 {where}에 할 말: {req['intent_desc']}  (길이: {req.get('length', '짧게')})")
        return system, "\n".join(lines)

    def stylist(req, cb):
        def run():
            with sem:
                try:
                    system, user = build(req)
                    use = model_big if (req.get("big") and model_big and not stylist.big_off) else model
                    body = json.dumps({"model": use, "max_tokens": 90 if req.get("big") else 60, "system": system,
                                       "messages": [{"role": "user", "content": user}]}).encode("utf-8")
                    rq = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body, headers={
                        "x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
                    with urllib.request.urlopen(rq, timeout=10 if use != model else 8) as f:
                        out = json.loads(f.read().decode("utf-8"))
                    stylist.status = "ok"
                    cb("".join(b.get("text", "") for b in out.get("content", []) if b.get("type") == "text"))
                except urllib.error.HTTPError as e:
                    if e.code in (429, 503, 529):                 # 과부하 → 30초 동안 기본 대사 위주
                        stylist.cool_until = time.time() + 30
                    elif use != model and e.code in (400, 403, 404):
                        stylist.big_off = True                    # 큰 모델을 못 쓰면 작은 모델로만
                        cb(None)
                        return
                    stylist.status = http_reason(e, use, api_key)
                    cb(None)
                except Exception as e:
                    stylist.status = f"연결 실패: {type(e).__name__}"
                    cb(None)
        threading.Thread(target=run, daemon=True).start()
    stylist.status = ""
    stylist.cool_until = 0
    stylist.big_off = False
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
