# -*- coding: utf-8 -*-
"""S&B 마피아 실행기 — 켤 때마다 새 버전을 확인해 받고, 게임을 연다.

config.json 의 update_url (GitHub Gist raw 주소) 에서 version.json 을 읽어
지금보다 새 버전이면 게임 파일을 받아 바꾼다. 인터넷이 안 되거나 실패하면 그냥 지금 버전으로 켠다.
"""
import hashlib
import json
import os
import runpy
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
FILES = {"mafia.py", "mafia_core.py", "desk.py", "launcher.py", "안내.txt"}   # 받을 수 있는 파일 (이 이름만)


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def local_version():
    return int(read_json(os.path.join(HERE, "version.json"), {}).get("version", 0))


def fetch(url, timeout=6):
    sep = "&" if "?" in url else "?"
    req = urllib.request.Request(f"{url}{sep}t={int(time.time())}", headers={"Cache-Control": "no-cache",
                                                                         "User-Agent": "SnB-Mafia"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def remote_manifest(cfg):
    base = (cfg.get("update_url") or "").strip()
    if not base:
        return None, None
    base = base.rstrip("/") + "/"
    return base, json.loads(fetch(base + "version.json").decode("utf-8"))


def update(cfg):
    """새 버전이 있으면 받아서 바꾼다 → 바뀐 버전 번호 / None."""
    base, man = remote_manifest(cfg)
    if not man or int(man.get("version", 0)) <= local_version():
        return None
    got = {}
    for name, digest in man.get("files", {}).items():
        if name not in FILES:
            continue
        data = fetch(base + urllib.parse.quote(name))
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError(f"{name} 확인 실패")          # 받다 깨지면 아무것도 안 바꾼다
        got[name] = data
    for name, data in got.items():
        tmp = os.path.join(HERE, name + ".new")
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, os.path.join(HERE, name))
    with open(os.path.join(HERE, "version.json"), "w", encoding="utf-8") as f:
        json.dump({"version": int(man["version"]), "note": man.get("note", "")}, f, ensure_ascii=False)
    return int(man["version"])


def main():
    cfg = read_json(os.path.join(HERE, "config.json"), {})
    try:
        v = update(cfg)
        if v:
            os.environ["SNB_UPDATED"] = str(v)
    except Exception as e:                                   # 인터넷 문제 등 → 지금 버전으로
        os.environ["SNB_UPDATE_ERR"] = str(e)[:80]
    sys.path.insert(0, HERE)
    runpy.run_path(os.path.join(HERE, "mafia.py"), run_name="__main__")


if __name__ == "__main__":
    main()
