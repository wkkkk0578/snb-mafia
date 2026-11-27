"""AI 채팅 검증 지표 (기획안 '검증 지표' 표).
가짜 Claude 로 한 판씩 돌려 정적·길이·반복·질문 응답을 잰다.
  python test_chat.py
"""
import random
import statistics
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mafia_core as core

LEAKS = ['현우 역할로, 지훈의 "왜?"에 짧게 반응. 15~', "(웃으며) 아니에요", "저 의사인데 믿어주세요", "PASS"]


def fake(mode, rng):
    def st(req, cb):
        if mode == "fail":
            return cb(None)
        if mode == "leak" and rng.random() < .4:
            return cb(rng.choice(LEAKS))
        if mode == "long" and rng.random() < .5:
            return cb(req["default"] + " 근데 진짜로 다들 어떻게 생각하는지 궁금하네요 저는 아직 확신이 없어요")
        cb(req["default"])
    st.status, st.cool_until, st.big_off = "ok", 0, False
    return st


def run(mode, seed, n=8):
    rng = random.Random(seed)
    mem = [dict(id="h0", name="한욱", ai=False), dict(id="h1", name="민수", ai=False)]
    mem += core.ai_members(n - 2, {"한욱", "민수"}, rng)
    g = core.Game(mem, now=0.0, rng=rng, stylist=fake(mode, rng) if mode else None)
    t, asked, said = 0.0, [], {}
    while g.phase != "end" and t < 1500:
        t += .1
        g.tick(t)
        # 사람 한 명(민수)은 가끔 AI 에게 질문, 한욱은 조용
        if g.phase == "day" and g.players["h1"].alive and rng.random() < .004:
            ai = [p for p in g.alive_list() if p.ai]
            if ai:
                x = rng.choice(ai)
                g.say("h1", f"{x.name} 너 누구 의심해?")
                asked.append((t, x.id))
        me = g.view("h0", t).get("me") or {}
        for h in ("h0", "h1"):
            v = g.view(h, t)["me"]
            if g.phase == "night" and v["targets"] and h not in g.night and rng.random() < .05:
                g.night_action(h, rng.choice(v["targets"]))
            if g.phase == "vote" and v["targets"] and h not in g.votes and rng.random() < .05:
                g.vote(h, rng.choice(v["targets"]))
            if g.phase == "judge" and v.get("act") and h not in g.judge and rng.random() < .05:
                g.judge_vote(h, rng.random() < .5)
        for m in g.chat:
            if m["s"] not in said:
                said[m["s"]] = (t, m, g.phase, g.day)
    return g, said, asked


def measure(mode, seeds=6):
    lens, short, total, gaps, rep, answered, qs, leaks = [], 0, 0, [], 0, 0, 0, 0
    for sd in range(seeds):
        g, said, asked = run(mode, sd)
        ai = [(t, m, ph, d) for t, m, ph, d in sorted(said.values(), key=lambda x: x[1]["s"])
              if m["k"] == "say" and m["w"] and g.players[m["w"]].ai and ph in ("day", "vote")]
        texts = [m["x"] for _, m, _, _ in ai]
        for x in texts:
            if core.leaked(x) or x in LEAKS:
                leaks += 1
        long_ok = [x for x in texts]
        lens += [len(x) for x in long_ok]
        short += sum(len(x) <= 10 for x in long_ok)
        total += len(long_ok)
        longer = [x for x in texts if len(core.norm(x)) > 3]        # '헐' '왜요' 같은 한두 단어 리액션은 제외
        rep += len(longer) - len({core.norm(x) for x in longer})
        # 낮 토론 중 최대 정적
        day_say = {}
        for t, m, ph, d in sorted(said.values(), key=lambda x: x[0]):
            if ph == "day" and m["k"] == "say":
                day_say.setdefault(d, []).append(t)
        for d, ts in day_say.items():
            gaps += [b - a for a, b in zip(ts, ts[1:])]
        for t0, pid in asked:
            qs += 1
            if any(m["w"] == pid and t0 < t <= t0 + 6 for t, m, _, _ in said.values()):
                answered += 1
    return dict(평균길이=round(statistics.mean(lens), 1), 짧은말비율=f"{short / max(1, total):.0%}",
                최대정적=round(max(gaps or [0]), 1), 반복률=f"{rep / max(1, total):.1%}",
                질문응답률=f"{answered / max(1, qs):.0%} ({answered}/{qs})", 누출=leaks, AI발언=total)


if __name__ == "__main__":
    for mode in (None, "ok", "fail", "leak", "long"):
        print(f"{str(mode):5}", measure(mode))
