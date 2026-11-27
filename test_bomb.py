"""💣 폭탄 이벤트 규칙 테스트 (화면 없이). python test_bomb.py"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mafia_core as core

REACH = 70.0
OK = []


def check(name, cond):
    OK.append((name, bool(cond)))
    print(("  ✅ " if cond else "  ❌ ") + name)


class Sim:
    """간단한 1차원 무대: AI 는 ai_moves 대로 움직이고, 사람은 테스트가 옮김."""
    def __init__(self, n=8, seed=1, test=None, humans=1, roles=None):
        self.rng = random.Random(seed)
        random.seed(seed)
        mem = [dict(id=f"h{i}", name=["한욱", "민수", "지수"][i], ai=False) for i in range(humans)]
        mem += core.ai_members(n - humans, {m["name"] for m in mem}, self.rng)
        self.g = core.Game(mem, now=0.0, rng=self.rng, test=test or {})
        if roles:
            for pid, r in roles.items():
                self.g.players[pid].role = r
        self.t = 0.0
        self.x = {p: self.rng.uniform(0, 800) for p in self.g.players}
        self.said = []

    def step(self, n=1):
        g = self.g
        for _ in range(n):
            self.t += .1
            for pid, (kind, tgt, _) in list(g.ai_moves.items()):
                if pid not in self.x or tgt not in self.x:
                    continue
                d = self.x[tgt] - self.x[pid]
                sp = 9 if kind == "chase" else 7
                if kind == "chase" or kind == "approach":
                    self.x[pid] += max(-sp, min(sp, d))
                elif kind == "flee":
                    self.x[pid] -= sp if d > 0 else -sp
                self.x[pid] = max(0, min(800, self.x[pid]))
            g.update_pos(self.x, REACH)
            seen = len(self.said)
            g.tick(self.t)
            for m in g.chat[seen:]:
                pass
        self.said = list(g.chat)

    def until(self, cond, limit=600):
        for _ in range(int(limit * 10)):
            if cond():
                return True
            self.step()
        return False

    def to_day(self, day):
        g = self.g
        def auto():
            v = g.view("h0", self.t)["me"]
            if g.phase == "vote" and "h0" not in g.votes and v["targets"]:
                g.vote("h0", None)
            if g.phase == "judge" and v.get("act") and "h0" not in g.judge:
                g.judge_vote("h0", False)
            return g.phase == "day" and g.day >= day
        return self.until(auto)


def lines(g, since=0):
    return [m for m in g.chat if m["s"] > since]


def main():
    print("1) 폭탄 도착 · 거리 · 되돌려주기 금지 · 1.5초")
    s = Sim(test=dict(bomb_force=True, penalty="novote"))
    g = s.g
    s.to_day(2)
    check("둘째 날 폭탄 도착", s.until(lambda: g.bomb is not None, 40))
    # 사람에게 폭탄 쥐여주기
    g.bomb.update(holder="h0", giver=None, got=g.now - 2)
    others = [p for p in g.alive_list() if p.id != "h0"]
    far = others[0].id
    s.x["h0"], s.x[far] = 100, 500
    for o in others[1:]:
        s.x[o.id] = 760
    g.update_pos(s.x, REACH)
    check("멀면 안 넘어감", not g.bomb_pass("h0", far))
    check("'토론 끝' 막힘", not g.set_ready("h0"))
    s.x["h0"] = 450
    g.update_pos(s.x, REACH)
    check("가까우면 넘어감", g.bomb_pass("h0", far) and g.bomb["holder"] == far)
    g.bomb.update(holder="h0", giver=far, got=g.now)
    check("받은 직후 1.5초 못 넘김", not g.bomb_pass("h0", far))
    g.bomb["got"] = g.now - 2
    check("준 사람에게 곧장 못 돌려줌", far not in g.bomb_targets("h0") and not g.bomb_pass("h0", far))
    check("채팅에 '💣 A → B' 기록", any("💣" in m["x"] and "→" in m["x"] for m in g.chat))

    print("2) AI 가 폭탄 들고 쫓아가서 넘기기")
    g.bomb.update(holder=others[2].id, giver=None, got=g.now)
    g.brains[others[2].id].on_bomb_got(None)
    n0 = g.bomb["n"]
    ok = s.until(lambda: g.bomb is None or g.bomb["n"] > n0, 30)
    check("AI 가 다가가서 건넴", ok and (g.bomb is None or g.bomb["holder"] != others[2].id))
    check("폭탄 반응 대사 (받음/넘김/도망)", any(m["w"] in g.brains and m["k"] == "say" for m in g.chat[-30:]))

    print("3) 폭발 → 패널티 4종 + 룰렛 5칸")
    for pen in ("jail", "novote", "noability"):
        s = Sim(n=10, seed=3, test=dict(bomb_force=True, penalty=pen))
        g = s.g
        s.to_day(2)
        s.until(lambda: g.bomb is not None, 40)
        victim = g.bomb["holder"]
        g.bomb["end"] = g.now + .2
        s.step(5)
        ann = [m["x"] for m in g.chat if g.name(victim) in m["x"] and "—" in m["x"]]
        check(f"패널티 {pen} 발표", any(core.PEN_LABEL[pen] in a for a in ann))
        s.step(60)
        boom = max(m["s"] for m in g.chat if "💥" in m["x"])
        check(f"패널티 {pen}: 반응 대사", any(m["k"] == "say" and m["w"] in g.brains and m["s"] > boom for m in g.chat))
        if pen == "jail":
            check("감옥: 채팅 막힘", not g.say(victim, "안녕"))
            check("감옥: 투표 대상 제외", all(victim not in g.targets(p.id) for p in g.alive_list() if p.id != victim)
                  or g.phase != "vote")
            if victim in g.brains:
                s0 = g.seq
                check("감옥: 갇히는 순간 한마디", any(m["w"] == victim and m["k"] == "say" for m in g.chat[-12:]))
                s.step(150)
                check("감옥: 그 뒤 AI 대사 없음", not any(m["w"] == victim and m["k"] == "say" and m["s"] > s0 for m in g.chat))
            s.until(lambda: g.phase == "vote", 200)
            check("감옥: 투표 대상 제외 (투표 시간)", all(victim not in g.targets(p.id) for p in g.alive_list() if p.id != victim))
            check("감옥: 본인 투표 불가", not g.vote(victim, None) and not g.targets(victim))
            s.until(lambda: g.phase == "night", 200)
            maf = [p.id for p in g.alive_list() if p.role == "마피아" and p.id != victim]
            check("감옥: 마피아 공격 대상 아님", all(victim not in g.targets(m) for m in maf))
            s.until(lambda: g.phase in ("morning", "end"), 60)
            check("다음 날 아침 석방" + (" (게임이 먼저 끝남)" if g.phase == "end" else ""),
                  victim not in g.jailed or g.phase == "end")
        if pen == "novote":
            s.until(lambda: g.phase == "vote", 200)
            check("투표권 없음: 투표 못 함", not g.vote(victim, None) and not g.targets(victim))
        if pen == "noability":
            s.until(lambda: g.phase == "night", 300)
            check("능력 사용 불가: 그날 밤 봉인", not g._can_act(g.players[victim]))
    for res in ("pass", "novote", "noability", "jail", "lost"):
        s = Sim(seed=5, test=dict(bomb_force=True, penalty="roulette", roulette=res))
        g = s.g
        s.to_day(2)
        s.until(lambda: g.bomb is not None, 40)
        victim = g.bomb["holder"]
        g.bomb["end"] = g.now + .2
        s.step(5)
        check(f"룰렛 시작 ({res})", g.roulette is not None and g.roul_view(g.now)["slots"])
        s.until(lambda: g.roulette is None, 15)
        check(f"룰렛 결과 {res} 적용·발표", any(core.PEN_LABEL[res] in m["x"] for m in g.chat))
        s.step(40)
        rs = max(m["s"] for m in g.chat if core.PEN_LABEL[res] in m["x"])
        check(f"룰렛 {res}: 반응 대사", any(m["k"] == "say" and m["w"] in g.brains and m["s"] > rs for m in g.chat))

    print("4) 마피아 1명 판에서 마피아 능력 소멸 → 다음 날 밤 정상 제거")
    s = Sim(n=6, seed=8, test=dict(bomb_force=True, penalty="roulette", roulette="lost"))
    g = s.g
    s.to_day(2)
    maf = next(p.id for p in g.players.values() if p.role == "마피아")
    s.until(lambda: g.bomb is not None, 40)
    if g.bomb:
        g.bomb.update(holder=maf, got=g.now)
        g.bomb["end"] = g.now + .2
    s.step(5)
    s.until(lambda: g.roulette is None, 15)
    check("마피아: 능력 소멸 발표는 똑같음", any(g.name(maf) in m["x"] and "능력 소멸" in m["x"] for m in g.chat))
    s.until(lambda: g.phase == "night", 300)
    check("마피아: 그날 밤은 봉인", not g._can_act(g.players[maf]) or not g.players[maf].alive)
    g.test["bomb_force"] = False
    s.until(lambda: g.phase == "night" and g.day >= 3 or g.phase == "end", 600)
    check("마피아: 다음 날 밤 다시 제거 가능", g.phase == "end" or g._can_act(g.players[maf]))

    print("5) 이벤트 꺼짐이면 폭탄 없음")
    s = Sim(seed=9, test=dict(bomb_force=True))
    s.g.bomb_on = False
    s.to_day(3)
    check("폭탄 안 옴", s.g.bomb is None and s.g.bomb_n == 0)

    bad = [n for n, ok in OK if not ok]
    print(f"\n{len(OK) - len(bad)}/{len(OK)} 통과" + (f" — 실패: {bad}" if bad else ""))


if __name__ == "__main__":
    main()
