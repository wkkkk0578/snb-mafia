# -*- coding: utf-8 -*-
"""S&B 마피아 — 바탕화면 레이어.

플레이어마다 먼작귀 캐릭터(chars/ 폴더)를 하나씩 바탕화면 아래쪽(작업표시줄 위)에 띄운다.
- 캐릭터·이름표·말풍선은 반투명(per-pixel alpha) Windows 창 (먼작귀펫과 같은 방식)
- 내 캐릭터는 ← → (채팅 입력칸이 비었을 때) / ↑ 점프 로 조종, 가만히 두면 혼자 돌아다님
- 방장 PC는 AI 캐릭터도 움직이고, 각 PC는 자기 캐릭터 위치를 방송한다
- 죽으면 캐릭터가 땅으로 꺼지고 'RIP' 무덤이 솟아난다
- 밤: 'Night Time', 사망: '사망' 이 화면 전체에 페이드 인 · 아웃
Windows 가 아니면(점검용) 실제 창 대신 FakeWin 에 그림과 위치만 기록한다.
"""
import hashlib
import math
import os
import random
import sys
import time

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
CHAR_DIR = os.path.join(HERE, "chars")
ALL_CHARS = ["치이카와", "하치와레", "우사기", "모몽가", "쿠리만주", "랏코", "시사"]
WIN = sys.platform == "win32"

SIZE = 64                 # 캐릭터 키 (논리 px)
FPS = 30
GRAVITY = 1.0
WALK, WALK_FAST, RUN = 2.4, 6.5, 12
GAP = SIZE * 1.35
INK = (92, 64, 51, 255)
PAPER = (255, 253, 248, 255)
PINK = (255, 222, 230, 255)
GRAY_PAPER = (226, 226, 230, 255)
MAFIA_PAPER = (255, 226, 226, 255)
GHOST_PAPER = (232, 234, 244, 255)
SC = {"v": 1.0}


def available_chars():
    return [c for c in ALL_CHARS if os.path.isfile(os.path.join(CHAR_DIR, c, "서있기.png"))]


def px(v):
    return max(1, int(round(v * SC["v"])))


# ---------------------------------------------------------------- 그리기 도구

_fonts = {}
_FONT_DIRS = [os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
              "/usr/share/fonts/opentype/noto", "/usr/share/fonts/truetype/noto"]
_FONT_NAMES = {"bold": ["malgunbd.ttf", "NotoSansCJK-Bold.ttc"],
               "reg": ["malgun.ttf", "NotoSansCJK-Regular.ttc"],
               "emoji": ["seguiemj.ttf", "NotoColorEmoji.ttf"]}


def font(size, bold=True, emoji=False):
    kind = "emoji" if emoji else ("bold" if bold else "reg")
    k = (kind, max(6, int(size)))
    if k not in _fonts:
        _fonts[k] = None
        for d in _FONT_DIRS:
            for n in _FONT_NAMES[kind]:
                p = os.path.join(d, n)
                if os.path.isfile(p):
                    try:
                        _fonts[k] = ImageFont.truetype(p, k[1])
                        break
                    except OSError:
                        pass
            if _fonts[k]:
                break
        if _fonts[k] is None:
            _fonts[k] = font(size, bold) if emoji else ImageFont.load_default()
    return _fonts[k]


def smooth(w, h, draw):
    """4배로 그린 뒤 줄여서 가장자리를 부드럽게."""
    k = 4 * SC["v"]
    big = Image.new("RGBA", (max(1, int(w * k)), max(1, int(h * k))), (0, 0, 0, 0))
    draw(ImageDraw.Draw(big), k)
    return big.resize((px(w), px(h)), Image.LANCZOS)


def soft_shadow(im, blur=4, dy=2, alpha=70):
    p = px(blur * 2)
    out = Image.new("RGBA", (im.width + 2 * p, im.height + 2 * p), (0, 0, 0, 0))
    sh = Image.new("RGBA", im.size, (40, 30, 30, 0))
    sh.putalpha(im.getchannel("A").point(lambda v: v * alpha // 255))
    layer = Image.new("RGBA", out.size, (0, 0, 0, 0))
    layer.paste(sh, (p, p + px(dy)))
    out.alpha_composite(layer.filter(ImageFilter.GaussianBlur(blur * SC["v"])))
    out.alpha_composite(im, (p, p))
    return out


def is_emoji(ch):
    o = ord(ch)
    return o >= 0x1F000 or 0x2600 <= o < 0x27C0 or 0x2B00 <= o < 0x2C00 or 0x2300 <= o < 0x2400 \
        or o in (0x200D, 0xFE0F)


_emo = {}


def emoji_img(ch, size):
    k = (ch, int(size))
    if k not in _emo:
        im = Image.new("RGBA", (int(size * 1.5), int(size * 1.5)), (0, 0, 0, 0))
        ch2 = ch.replace("\ufe0f", "") or ch
        try:
            ImageDraw.Draw(im).text((im.width / 2, im.height / 2), ch2, font=font(size, emoji=True),
                                    embedded_color=True, anchor="mm")
        except Exception:
            ImageDraw.Draw(im).text((im.width / 2, im.height / 2), ch2, font=font(size), fill=INK, anchor="mm")
        _emo[k] = im
    return _emo[k]


def runs(text):
    """보통 글자 / 이모지 덩어리로 나눔."""
    out = []
    for ch in text:
        e = is_emoji(ch)
        if out and (out[-1][1] == e or ch in "\u200d\ufe0f"):
            out[-1] = (out[-1][0] + ch, out[-1][1] or e)
        else:
            out.append((ch, e))
    return out


def text_w(t, e, f, es):
    return len([c for c in t if c not in "\u200d\ufe0f"]) * es * 1.12 if e else f.getlength(t)


def draw_runs(im, x, y, text, f, es, fill):
    d = ImageDraw.Draw(im)
    for t, e in runs(text):
        if e:
            for c in t:
                if c in "\u200d\ufe0f":
                    continue
                em = emoji_img(c, es)
                im.alpha_composite(em, (int(x + es * 0.56 - em.width / 2), int(y - em.height / 2)))
                x += es * 1.12
        else:
            d.text((x, y), t, font=f, fill=fill, anchor="lm")
            x += f.getlength(t)


def bubble_img(text, kind="say"):
    """말풍선. kind: say(흰) / mafia(붉은) / dead(회청 · 유령)."""
    f, es = font(12.5 * SC["v"], bold=False), 14 * SC["v"]
    maxw = 190 * SC["v"]
    paper = {"mafia": MAFIA_PAPER, "dead": GHOST_PAPER}.get(kind, PAPER)
    prefix = {"mafia": "🔪 ", "dead": "👻 "}.get(kind, "")
    text = prefix + text
    lines, cur, cw = [], "", 0.0
    for t, e in runs(text):
        units = [t] if e else list(t)
        for u in units:
            uw = text_w(u, e, f, es)
            if cur and cw + uw > maxw:
                lines.append(cur)
                cur, cw = ("", 0.0) if u == " " else (u, uw)
            else:
                cur, cw = cur + u, cw + uw
    lines = (lines + [cur])[:4]
    lw = [sum(text_w(t, e, f, es) for t, e in runs(l)) for l in lines]
    tw = max(lw + [0]) / SC["v"]
    lh = 18
    w, h, tail = max(tw + 26, 40), len(lines) * lh + 14, 8

    def dr(g, k):
        c, b = w * k / 2, 2.2 * k
        g.rounded_rectangle((0, 0, w * k - 1, h * k - 1), 13 * k, fill=INK)
        g.polygon([(c - 8 * k, h * k - 3 * k), (c + 8 * k, h * k - 3 * k), (c, (h + tail) * k - 1)], fill=INK)
        g.rounded_rectangle((b, b, w * k - 1 - b, h * k - 1 - b), 13 * k - b, fill=paper)
        g.polygon([(c - 8 * k + b * 1.3, h * k - 3 * k - b), (c + 8 * k - b * 1.3, h * k - 3 * k - b),
                   (c, (h + tail) * k - 1 - b * 1.8)], fill=paper)
    im = smooth(w, h + tail, dr)
    for i, l in enumerate(lines):
        draw_runs(im, px(w / 2) - lw[i] / 2, px(7 + lh / 2 + i * lh), l, f, es, INK)
    return soft_shadow(im, blur=4, dy=2, alpha=60)


def name_tag(nick, sub="", mine=False, dead=False, ai=False, mafia=False):
    """이름표: 둥근 알약 + 닉네임 (+ AI 칩, + 작은 부가 글자: 아는 직업 등)."""
    f, fs = font(11.5 * SC["v"]), font(9 * SC["v"])
    tw = f.getlength(nick) / SC["v"]
    chips = ([("AI", (205, 214, 255, 255))] if ai else []) + ([(sub, (255, 214, 214, 255) if mafia else (255, 236, 196, 255))] if sub else [])
    cws = [fs.getlength(l) / SC["v"] + 12 for l, _ in chips]
    w, h = tw + 20 + sum(c + 4 for c in cws), 22
    paper = GRAY_PAPER if dead else (PINK if mine else PAPER)

    def dr(g, k):
        g.rounded_rectangle((0, 0, w * k - 1, h * k - 1), h * k / 2, fill=(120, 120, 128, 255) if dead else INK)
        b = 2 * k
        g.rounded_rectangle((b, b, w * k - 1 - b, h * k - 1 - b), h * k / 2 - b, fill=paper)
    im = smooth(w, h, dr)
    ImageDraw.Draw(im).text((px(10), px(h / 2)), nick, font=f, fill=(110, 110, 118, 255) if dead else INK, anchor="lm")
    x = 10 + tw + 5
    for (label, col), cw in zip(chips, cws):
        chip = smooth(cw, 15, lambda g, k, col=col, cw=cw: g.rounded_rectangle((0, 0, cw * k - 1, 15 * k - 1),
                                                                               7.5 * k, fill=col))
        im.alpha_composite(chip, (px(x), px((h - 15) / 2)))
        ImageDraw.Draw(im).text((px(x + cw / 2), px(h / 2)), label, font=fs, fill=INK, anchor="mm")
        x += cw + 4
    return soft_shadow(im, blur=3, dy=1.5, alpha=55)


def tomb_img():
    """RIP 무덤 (직접 그림)."""
    w, h = SIZE * 0.95, SIZE * 1.0

    def dr(g, k):
        W, H = w * k, h * k
        sx0, sx1, top, base = W * .14, W * .86, H * .04, H * .9
        r = (sx1 - sx0) / 2
        g.ellipse((sx0, top, sx1, top + 2 * r), fill=(96, 100, 112, 255))
        g.rectangle((sx0, top + r, sx1, base), fill=(96, 100, 112, 255))
        b = 2.4 * k
        g.ellipse((sx0 + b, top + b, sx1 - b, top + 2 * r - b), fill=(178, 182, 192, 255))
        g.rectangle((sx0 + b, top + r, sx1 - b, base - b), fill=(178, 182, 192, 255))
        g.rounded_rectangle((W * .04, base - 2 * k, W * .96, H - 1), 5 * k, fill=(126, 98, 76, 255))
        for gx in (0.1, 0.27, 0.73, 0.9):                     # 풀
            cx = W * gx
            g.polygon([(cx - 4 * k, base + 1 * k), (cx + 4 * k, base + 1 * k), (cx, base - 9 * k)], fill=(104, 168, 92, 255))
    im = smooth(w, h, dr)
    ImageDraw.Draw(im).text((px(w / 2), px(h * .44)), "RIP", font=font(15 * SC["v"]), fill=(70, 72, 82, 255), anchor="mm")
    return im


def ground_shadow():
    w, h = SIZE * 0.7, 9
    return smooth(w, h, lambda g, k: g.ellipse((0, 0, w * k - 1, h * k - 1), fill=(40, 30, 30, 60)))


def banner_img(title, sub, color):
    """화면 가운데 큰 글자 (어두운 빛 번짐)."""
    ft, fs = font(96 * SC["v"]), font(26 * SC["v"])
    tw = ft.getlength(title)
    sw = fs.getlength(sub) if sub else 0
    W = int(max(tw, sw) + px(160))
    H = int(px(150) + (px(60) if sub else 0) + px(60))
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    ty = px(30) + px(75)
    gd.text((W / 2, ty), title, font=ft, fill=(0, 0, 0, 200), anchor="mm")
    if sub:
        gd.text((W / 2, ty + px(95)), sub, font=fs, fill=(0, 0, 0, 200), anchor="mm")
    im.alpha_composite(glow.filter(ImageFilter.GaussianBlur(px(14))))
    im.alpha_composite(glow.filter(ImageFilter.GaussianBlur(px(4))))
    d = ImageDraw.Draw(im)
    d.text((W / 2, ty), title, font=ft, fill=color, anchor="mm")
    if sub:
        d.text((W / 2, ty + px(95)), sub, font=fs, fill=(240, 240, 245, 255), anchor="mm")
    return im


# ---------------------------------------------------------------- 캐릭터 그림

_sprites = {}


def placeholder(ch):
    """캐릭터 그림이 없을 때: 색 동그라미."""
    hcol = int(hashlib.md5(ch.encode("utf-8")).hexdigest()[:6], 16)
    col = (120 + hcol % 120, 120 + (hcol >> 8) % 120, 120 + (hcol >> 16) % 120, 255)
    s = SIZE

    def dr(g, k):
        g.ellipse((2 * k, 6 * k, (s - 2) * k, (s - 1) * k), fill=INK)
        g.ellipse((4.5 * k, 8.5 * k, (s - 4.5) * k, (s - 3.5) * k), fill=col)
        g.ellipse((s * .32 * k, s * .42 * k, s * .4 * k, s * .52 * k), fill=INK)
        g.ellipse((s * .6 * k, s * .42 * k, s * .68 * k, s * .52 * k), fill=INK)
    im = smooth(s, s, dr)
    return {"서있기": im}


def load(ch):
    if ch not in _sprites:
        out = {}
        d = os.path.join(CHAR_DIR, ch or "")
        h = px(SIZE)
        if ch and os.path.isdir(d):
            for f in os.listdir(d):
                if f.lower().endswith(".png"):
                    try:
                        im = Image.open(os.path.join(d, f)).convert("RGBA")
                        out[f[:-4]] = im.resize((max(1, im.width * h // im.height), h), Image.LANCZOS)
                    except Exception:
                        pass
        _sprites[ch] = out if "서있기" in out else placeholder(ch or "?")
    return _sprites[ch]


def transform(im, flip=False, sx=1.0, sy=1.0, angle=0):
    if flip:
        im = im.transpose(Image.FLIP_LEFT_RIGHT)
    if (sx, sy) != (1.0, 1.0):
        im = im.resize((max(1, int(im.width * sx)), max(1, int(im.height * sy))), Image.BILINEAR)
    if angle:
        im = im.rotate(angle, expand=True, resample=Image.BICUBIC)
    return im


# ---------------------------------------------------------------- 창 (Windows 반투명 창 / 점검용 가짜)

class LayeredWin:
    """PIL RGBA 그림 한 장을 보여주는 테두리 없는 반투명 창 (먼작귀펫 Layered 와 같음)."""
    _api = None

    @classmethod
    def api(cls):
        if cls._api is None:
            import ctypes
            from ctypes import wintypes as wt
            u, g, vp = ctypes.windll.user32, ctypes.windll.gdi32, ctypes.c_void_p
            u.GetDC.restype, u.GetDC.argtypes = vp, [vp]
            u.ReleaseDC.argtypes = [vp, vp]
            u.GetAncestor.restype, u.GetAncestor.argtypes = vp, [vp, wt.UINT]
            u.GetWindowLongPtrW.restype, u.GetWindowLongPtrW.argtypes = ctypes.c_ssize_t, [vp, ctypes.c_int]
            u.SetWindowLongPtrW.restype = ctypes.c_ssize_t
            u.SetWindowLongPtrW.argtypes = [vp, ctypes.c_int, ctypes.c_ssize_t]
            u.UpdateLayeredWindow.argtypes = [vp, vp, vp, vp, vp, vp, wt.DWORD, vp, wt.DWORD]
            u.SetWindowPos.argtypes = [vp, vp, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT]
            u.IsWindowVisible.argtypes = [vp]
            g.CreateCompatibleDC.restype, g.CreateCompatibleDC.argtypes = vp, [vp]
            g.CreateDIBSection.restype = vp
            g.CreateDIBSection.argtypes = [vp, vp, wt.UINT, ctypes.POINTER(vp), vp, wt.DWORD]
            g.SelectObject.restype, g.SelectObject.argtypes = vp, [vp, vp]
            g.DeleteObject.argtypes, g.DeleteDC.argtypes = [vp], [vp]

            class BIH(ctypes.Structure):
                _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                            ("biBitCount", wt.WORD), ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                            ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                            ("biClrImportant", wt.DWORD)]

            class BLEND(ctypes.Structure):
                _fields_ = [("op", ctypes.c_ubyte), ("flags", ctypes.c_ubyte), ("alpha", ctypes.c_ubyte),
                            ("fmt", ctypes.c_ubyte)]
            cls._api = (ctypes, wt, u, g, BIH, BLEND)
        return cls._api

    def __init__(self, root, cursor="hand2", clickthrough=False):
        import tkinter as tk
        ctypes, wt, u, g, BIH, BLEND = self.api()
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.geometry("1x1+-4000+-4000")
        self.win.attributes("-topmost", True)
        if cursor:
            self.win.config(cursor=cursor)
        self.win.update_idletasks()
        self.hwnd = u.GetAncestor(self.win.winfo_id(), 2)
        ex = u.GetWindowLongPtrW(self.hwnd, -20) | 0x80000 | 0x80 | 0x08000000   # LAYERED | TOOLWINDOW | NOACTIVATE
        if clickthrough:
            ex |= 0x20
        u.SetWindowLongPtrW(self.hwnd, -20, ex)
        self.key, self.size, self.pos, self.alpha, self.im = None, (1, 1), None, 255, None
        self.show(Image.new("RGBA", (1, 1), (0, 0, 0, 0)))

    def show(self, im):
        ctypes, wt, u, g, BIH, BLEND = self.api()
        self.im = im
        w, h = im.size
        r, gg, b, a = im.convert("RGBa").split()
        data = Image.merge("RGBA", (b, gg, r, a)).tobytes()
        hdc = u.GetDC(None)
        mem = g.CreateCompatibleDC(hdc)
        bi = BIH(ctypes.sizeof(BIH), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        bits = ctypes.c_void_p()
        hb = g.CreateDIBSection(mem, ctypes.addressof(bi), 0, ctypes.byref(bits), None, 0)
        ctypes.memmove(bits, data, len(data))
        old = g.SelectObject(mem, hb)
        sz, src, bl = wt.SIZE(w, h), wt.POINT(0, 0), BLEND(0, 0, max(0, min(255, int(self.alpha))), 1)
        u.UpdateLayeredWindow(self.hwnd, hdc, None, ctypes.addressof(sz), mem, ctypes.addressof(src), 0,
                              ctypes.addressof(bl), 2)
        g.SelectObject(mem, old)
        g.DeleteObject(hb)
        g.DeleteDC(mem)
        u.ReleaseDC(None, hdc)
        self.size = (w, h)

    def image(self, key, make):
        if key != self.key:
            self.key = key
            self.show(make())

    def set_alpha(self, a):
        a = max(0, min(255, int(a)))
        if a != self.alpha and self.im is not None:
            self.alpha = a
            self.show(self.im)

    def place(self, x, y):
        pos = (int(x), int(y))
        if pos != self.pos:
            self.pos = pos
            self.api()[2].SetWindowPos(self.hwnd, None, pos[0], pos[1], 0, 0, 0x1 | 0x4 | 0x10)

    def bind(self, seq, fn):
        self.win.bind(seq, fn)

    def raise_top(self):
        try:
            ctypes, wt, u = self.api()[:3]
            if u.IsWindowVisible(self.hwnd):
                u.SetWindowPos(self.hwnd, ctypes.c_void_p(-1), 0, 0, 0, 0, 0x1 | 0x2 | 0x10)
        except Exception:
            pass

    def destroy(self):
        try:
            self.win.destroy()
        except Exception:
            pass


class FakeWin:
    """Windows 가 아닐 때(점검): 그림·위치·투명도만 기억한다. FakeWin.all 로 바탕화면을 합성해 볼 수 있다."""
    all = []

    def __init__(self, root=None, cursor="", clickthrough=False):
        self.key, self.size, self.pos, self.alpha, self.im = None, (1, 1), None, 255, None
        self.dead = False
        FakeWin.all.append(self)

    def show(self, im):
        self.im, self.size = im, im.size

    def image(self, key, make):
        if key != self.key:
            self.key = key
            self.show(make())

    def set_alpha(self, a):
        self.alpha = max(0, min(255, int(a)))

    def place(self, x, y):
        self.pos = (int(x), int(y))

    def bind(self, seq, fn):
        pass

    def raise_top(self):
        pass

    def destroy(self):
        self.dead = True
        if self in FakeWin.all:
            FakeWin.all.remove(self)


Win = LayeredWin if WIN else FakeWin


class Dim:
    """화면 전체를 살짝 어둡게 (클릭은 통과). Windows: 반투명 tk 창 / 그 외: 기록만."""

    def __init__(self, root, w, h, color):
        self.alpha, self.color, self.size = 0.0, color, (w, h)
        self.win = None
        if WIN:
            import tkinter as tk
            import ctypes
            self.win = tk.Toplevel(root)
            self.win.overrideredirect(True)
            self.win.configure(bg=color)
            self.win.geometry(f"{w}x{h}+0+0")
            self.win.attributes("-alpha", 0.0)
            self.win.attributes("-topmost", True)
            self.win.update_idletasks()
            u, vp = ctypes.windll.user32, ctypes.c_void_p
            u.GetAncestor.restype, u.GetAncestor.argtypes = vp, [vp, ctypes.c_uint]
            u.GetWindowLongPtrW.restype, u.GetWindowLongPtrW.argtypes = ctypes.c_ssize_t, [vp, ctypes.c_int]
            u.SetWindowLongPtrW.restype = ctypes.c_ssize_t
            u.SetWindowLongPtrW.argtypes = [vp, ctypes.c_int, ctypes.c_ssize_t]
            hw = u.GetAncestor(self.win.winfo_id(), 2)
            ex = u.GetWindowLongPtrW(hw, -20) | 0x80000 | 0x20 | 0x80 | 0x08000000
            u.SetWindowLongPtrW(hw, -20, ex)
            self.win.attributes("-alpha", 0.0)
        else:
            FakeWin.all.insert(0, self)

    def set_alpha(self, a):
        self.alpha = a
        if self.win:
            try:
                self.win.attributes("-alpha", max(0.0, min(1.0, a)))
            except Exception:
                pass

    def destroy(self):
        if self.win:
            try:
                self.win.destroy()
            except Exception:
                pass
        elif self in FakeWin.all:
            FakeWin.all.remove(self)


def work_bottom(default):
    if WIN:
        try:
            import ctypes
            from ctypes import wintypes
            r = wintypes.RECT()
            if ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(r), 0) and r.bottom > 0:
                return r.bottom
        except Exception:
            pass
    return default


# ---------------------------------------------------------------- 캐릭터 한 명

class Avatar:
    def __init__(self, pid, nick, ch, x, sim=False, mine=False):
        self.id, self.nick, self.ch, self.x, self.y = pid, nick, ch, x, 0.0
        self.sim, self.mine = sim, mine          # sim: 이 PC가 움직이는 캐릭터 (나 / 방장의 AI)
        self.imgs = load(ch)
        self.vx = self.vy = 0.0
        self.face = random.choice((-1, 1))      # 원본 그림은 왼쪽을 본다
        self.state, self.t, self.tick = "idle", random.randint(20, 90), 0
        self.manual = 0                          # 사람이 누르고 있는 방향
        self.run = False
        self.rest_until = 0.0
        self.rx = self.ry = None                 # 남의 캐릭터: 받은 위치
        self.moving = False
        self.die_t = None                        # 죽은 뒤 지난 프레임 (None = 살아있음)
        self.bubble, self.bubble_kind, self.bubble_until = "", "say", 0.0
        self.sub, self.ai, self.mafia = "", False, False
        self.hop_t = -99                         # 말할 때 톡 튀는 시작 tick
        self.rfx = None                          # 남의 캐릭터: 받은 위치 (무대 안 비율 0~1)
        self.grave_x = None

    @property
    def alive(self):
        return self.die_t is None

    def frames(self, prefix):
        keys = sorted(k for k in self.imgs if k.startswith(prefix))
        return keys or ["서있기"]

    def say(self, text, kind="say", sec=6.0):
        self.bubble, self.bubble_kind = text[:80], kind
        if self.alive:
            self.hop_t = self.tick
        self.bubble_until = time.time() + min(9.0, sec + len(text) * 0.05)

    def jump(self):
        if self.alive and self.y == 0 and self.state != "jump":
            self.vy, self.vx = 15, self.face * 2.5 if self.manual == 0 else self.manual * WALK_FAST
            self.state = "jump"

    def update(self, lo, hi, others, target=None, freeze=False, center=None):
        """lo~hi: 무대 안 움직일 수 있는 범위. target: 대형 자리(있으면 그리로). freeze: 밤(제자리)."""
        self.tick += 1
        if self.die_t is not None:
            self.die_t += 1
            if self.y > 0:
                self.y = max(0.0, self.y - 4)
            if self.grave_x is not None and self.die_t >= Desk.TOMB_START:
                self.x = self.grave_x                # 꺼진 뒤 무덤은 묘지로
            return
        if target is not None and not self.manual and self.state != "jump":
            return self.go_to(target, center)
        if not self.sim:
            if self.rfx is not None:
                self.rx = lo + self.rfx * (hi - lo)
            return self.follow()
        if freeze and not self.manual and self.state != "jump":
            self.state = "idle"
            return
        s = self.state
        if s == "jump":
            self.x += self.vx
            self.y += self.vy
            self.vy -= GRAVITY
            if self.y <= 0:
                self.y, self.vx, self.vy = 0.0, 0.0, 0.0
                self.state, self.t = ("walk", 10 ** 6) if self.manual else ("idle", 20)
        elif self.manual:
            self.face, self.state = self.manual, "walk"
            self.x += self.manual * (RUN if self.run else WALK_FAST)
        else:
            self.t -= 1
            if s == "walk":
                self.x += self.face * WALK
            if self.t <= 0:
                if s == "idle" and time.time() > self.rest_until:
                    nxt = random.choices(["walk", "jump", "idle"], [55, 8, 37])[0]
                    if nxt == "walk":
                        self.face = random.choice((-1, 1))
                        self.state, self.t = "walk", random.randint(60, 200)
                    elif nxt == "jump":
                        self.jump()
                    else:
                        self.t = random.randint(40, 140)
                else:
                    self.state, self.t = "idle", random.randint(40, 160)
        if self.y == 0 and self.state != "jump":
            for o in others:                     # 땅 위에서 겹치면 살짝 밀어냄
                if o is self or not o.alive or o.y > 0:
                    continue
                d = self.x - o.x
                if abs(d) < GAP:
                    side = (1 if d > 0 else -1) if d else (1 if self.id > o.id else -1)
                    self.x += side * min(3.0, (GAP - abs(d)) * 0.25)
                    if self.state == "walk" and not self.manual and self.face != side:
                        self.face = side
        if self.x < lo:
            self.x, self.face = lo, 1
            if self.manual:
                self.face = self.manual
        elif self.x > hi:
            self.x, self.face = hi, -1
            if self.manual:
                self.face = self.manual

    def go_to(self, tx, center=None):
        """대형 자리로 걸어가서 멈추고, 무대 가운데를 바라봄."""
        d = tx - self.x
        if abs(d) > 1.2:
            step = min(abs(d), WALK_FAST * 0.8)
            self.x += step if d > 0 else -step
            self.face = 1 if d > 0 else -1
            self.state, self.moving = "walk", True
        else:
            self.x, self.state, self.moving = tx, "idle", False
            if center is not None and abs(center - self.x) > 4:
                self.face = 1 if center > self.x else -1

    def follow(self):
        if self.rx is None:
            return
        d = self.rx - self.x
        if abs(d) > 400:
            self.x = self.rx
        elif abs(d) > 0.8:
            step = max(min(abs(d) * 0.2, WALK_FAST * 1.4), 0.8)
            self.x += step if d > 0 else -step
        self.moving = abs(d) > 1.5
        if self.moving and abs(d) > 4:
            self.face = 1 if d > 0 else -1
        if self.ry is not None:
            self.y += (self.ry - self.y) * 0.5
            if abs(self.y - self.ry) < 1:
                self.y = self.ry

    def pose(self):
        """→ (프레임 이름, 좌우반전, 가로배율, 세로배율, 각도, y 들썩임)"""
        k, flip = self.tick, self.face > 0
        if self.y > 0:
            return "점프", flip, 1, 1, 0, 0
        walking = (self.state == "walk") if self.sim else self.moving
        if walking:
            f = self.frames("걷기")
            if self.run and self.manual:
                return f[k // 3 % len(f)], flip, 1, 1, -9 * self.face, abs(math.sin(k / 1.8)) * 6
            fast = self.manual != 0 or not self.sim
            return f[k // (4 if fast else 6) % len(f)], flip, 1, 1, 0, abs(math.sin(k / (2.5 if fast else 3.5))) * 3
        hop = self.tick - self.hop_t
        if 0 <= hop < 12:                         # 말할 때 톡
            return "점프" if "점프" in self.imgs else "서있기", flip, 1, 1, 0, math.sin(hop / 12 * math.pi) * 14
        return "서있기", flip, 1, round(1 + 0.02 * math.sin(k / 12), 2), 0, 0

    def state_msg(self, lo, hi):
        """무대 안 비율로 보냄 (PC마다 무대 위치·폭이 달라도 같은 자리)."""
        return [self.id, round((self.x - lo) / max(1, hi - lo), 4), self.face,
                1 if (self.state == "walk" and self.y == 0) else 0, round(self.y, 1)]


# ---------------------------------------------------------------- 바탕화면 전체

class Desk:
    DIE_JUMP, DIE_SINK, TOMB_START, TOMB_RISE = 10, 32, 33, 14

    def __init__(self, root, on_click=None):
        self.root, self.on_click = root, on_click
        try:
            SC["v"] = max(1.0, root.winfo_fpixels("1i") / 96)
        except Exception:
            SC["v"] = 1.0
        self.WP, self.HP = root.winfo_screenwidth(), root.winfo_screenheight()
        self.W = self.WP / SC["v"]
        self.ground = work_bottom(self.HP - 48 * SC["v"]) / SC["v"]
        self.avs, self.views = {}, {}
        self.me, self.sims = None, set()
        self.banners, self.cur = [], None
        self.last_send, self.sent = 0.0, {}
        self.outbox = []
        self.last_top = 0.0
        self.alive_flag = True
        self.refocus = None                      # 새 창이 포커스를 가져가면 돌려받을 함수
        self.anchor = None                       # 무대 중심 x (논리 px) 를 돌려주는 함수 — 보통 채팅창 가운데
        self.mode, self.focus, self.order = "free", None, []
        self.zc = None                           # 무대 중심 (부드럽게 따라감)
        self.zone = (0.0, self.W, 0.0, self.W)   # (무대 왼쪽, 오른쪽, 생존자 구역 왼쪽, 오른쪽)
        self.dead_order = []
        self._shadow = None
        self.root.after(1000 // FPS, self.loop)

    # ------------------------------------------------ 외부에서 부르는 것
    def sync(self, rows, me, sims):
        """rows: [{id, n, ch, ai, alive, sub, mafia}] — 화면에 있어야 할 캐릭터들."""
        self.me, self.sims = me, set(sims)
        ids = set()
        for r in rows:
            pid = r["id"]
            ids.add(pid)
            a = self.avs.get(pid)
            if a is None or a.ch != r.get("ch"):
                x = a.x if a else self.spawn_x(pid)
                a = Avatar(pid, r["n"], r.get("ch"), x, sim=pid in self.sims, mine=pid == me)
                if not r.get("alive", True):
                    a.die_t = 999
                self.avs[pid] = a
            a.nick, a.sub, a.ai, a.mafia = r["n"], r.get("sub", ""), r.get("ai", False), r.get("mafia", False)
            a.sim, a.mine = pid in self.sims, pid == me
            alive = r.get("alive", True)
            if a.alive and not alive:
                a.die_t, a.manual = 0, 0
            elif not a.alive and alive:
                a.die_t, a.y = None, 0.0
        for pid in [p for p in self.avs if p not in ids]:
            self.remove(pid)

    def spawn_x(self, pid):
        h = int(hashlib.md5(str(pid).encode()).hexdigest()[:8], 16)
        _, _, lo, hi = self.zone
        return lo + SIZE / 2 + (h % 10000) / 10000 * max(1, hi - lo - SIZE)

    def set_mode(self, mode, focus=None, order=None):
        """free(자유) · line(한 줄 정렬) · stage(focus 가 가운데) · night(제자리)."""
        self.mode, self.focus = mode, focus
        if order is not None:
            self.order = list(order)

    def compute_zone(self):
        """무대: 채팅창 근처, 인원에 맞는 폭. 왼쪽 끝은 묘지."""
        avs = list(self.avs.values())
        n_alive = sum(a.alive for a in avs)
        self.dead_order = [p for p in self.dead_order if p in self.avs and not self.avs[p].alive]
        for a in avs:
            if not a.alive and a.id not in self.dead_order:
                self.dead_order.append(a.id)
        grave_w = len(self.dead_order) * SIZE * 0.95 + (SIZE * 0.4 if self.dead_order else 0)
        live_w = max(SIZE * 3, n_alive * GAP * 1.1 + SIZE)
        zw = min(self.W, grave_w + live_w)
        want = self.anchor() if self.anchor else self.W / 2
        self.zc = want if self.zc is None else self.zc + (want - self.zc) * 0.08
        x0 = min(max(0.0, self.zc - zw / 2), self.W - zw)
        self.zone = (x0, x0 + zw, x0 + grave_w, x0 + zw)
        for i, pid in enumerate(self.dead_order):
            self.avs[pid].grave_x = x0 + SIZE * 0.5 + i * SIZE * 0.95

    def targets(self):
        """대형 모드일 때 생존자별 자리."""
        if self.mode not in ("line", "stage"):
            return {}
        _, _, lo, hi = self.zone
        ids = [p for p in self.order if p in self.avs and self.avs[p].alive]
        ids += [p for p in self.avs if p not in ids and self.avs[p].alive]
        if not ids:
            return {}
        lo, hi = lo + SIZE * 0.6, hi - SIZE * 0.6
        if self.mode == "stage" and self.focus in ids:
            rest = [p for p in ids if p != self.focus]
            mid = (lo + hi) / 2
            out = {self.focus: mid}
            left, right = rest[:len(rest) // 2], rest[len(rest) // 2:]
            gap_c = GAP * 1.4                      # 가운데 사람 둘레는 넓게
            for side, grp in ((-1, left), (1, right)):
                span = (mid - lo - gap_c) if side < 0 else (hi - mid - gap_c)
                for i, p in enumerate(reversed(grp) if side < 0 else grp):
                    step = min(GAP * 1.05, span / max(1, len(grp)))
                    out[p] = mid + side * (gap_c + i * step)
            return out
        n = len(ids)
        if n == 1:
            return {ids[0]: (lo + hi) / 2}
        step = (hi - lo) / (n - 1)
        return {p: lo + i * step for i, p in enumerate(ids)}

    def remove(self, pid):
        self.avs.pop(pid, None)
        v = self.views.pop(pid, None)
        if v:
            for w in v.values():
                w.destroy()

    def on_pos(self, ps):
        for item in ps or ():
            try:
                pid, xn, face, walk, y = item[:5]
            except Exception:
                continue
            a = self.avs.get(pid)
            if a and pid not in self.sims:
                a.rfx, a.ry = min(1.0, max(0.0, float(xn))), float(y)
                if not walk:
                    a.face = 1 if face > 0 else -1

    def say(self, pid, text, kind="say"):
        a = self.avs.get(pid)
        if a and text:
            a.say(text, kind)

    def walk(self, d, run=False):
        a = self.avs.get(self.me)
        if a and a.alive:
            a.manual, a.run = d, run
            if a.state != "jump":
                a.state, a.t = "walk", 10 ** 6
            a.face = d

    def stop(self):
        a = self.avs.get(self.me)
        if a and a.manual:
            a.manual, a.run = 0, False
            if a.state == "walk":
                a.state, a.t = "idle", 60
            a.rest_until = time.time() + 20      # 조종한 뒤 잠시는 제자리

    def jump(self):
        a = self.avs.get(self.me)
        if a:
            a.jump()

    def banner(self, title, sub="", color=(255, 255, 255, 255), tint="#000814", dim=0.45):
        self.banners.append(dict(title=title, sub=sub, color=color, tint=tint, dimv=dim))

    def take_pos(self, now):
        """방송할 내 캐릭터(들) 위치 — 움직이면 0.25초, 가만히 있으면 2초마다."""
        mine = [self.avs[i] for i in self.sims if i in self.avs and self.avs[i].alive]
        if not mine:
            return None
        _, _, lo, hi = self.zone
        msgs = [a.state_msg(lo + SIZE / 2, hi - SIZE / 2) for a in mine]
        changed = any(self.sent.get(m[0]) != tuple(m[1:]) for m in msgs)
        moving = any(m[3] or m[4] for m in msgs)
        gap = 0.25 if moving else 2.0
        if (changed and now - self.last_send > 0.25) or now - self.last_send > gap * (1 if changed else 1.5):
            self.last_send = now
            for m in msgs:
                self.sent[m[0]] = tuple(m[1:])
            return msgs
        return None

    def clear(self):
        for pid in list(self.avs):
            self.remove(pid)
        if self.cur:
            self.cur["dimw"].destroy()
            self.cur["text"].destroy()
            self.cur = None
        self.banners = []

    # ------------------------------------------------ 그리기
    def loop(self):
        try:
            self.frame()
        except Exception:
            import traceback
            traceback.print_exc()
        self.root.after(1000 // FPS, self.loop)

    def frame(self, now=None):
        now = now or time.time()
        avs = list(self.avs.values())
        self.compute_zone()
        _, _, lo, hi = self.zone
        tg = self.targets()
        center = (lo + hi) / 2
        for a in avs:
            a.update(lo + SIZE / 2, hi - SIZE / 2, avs, target=tg.get(a.id), freeze=self.mode == "night",
                     center=center)
        for a in avs:
            self.draw(a, now)
        self.step_banner(now)
        if WIN and now - self.last_top > 1.5 and not self.cur:
            self.last_top = now
            for v in self.views.values():
                for w in v.values():
                    w.raise_top()

    def views_of(self, a):
        v = self.views.get(a.id)
        if v is None:
            v = self.views[a.id] = dict(shadow=Win(self.root, cursor="", clickthrough=True),
                                        body=Win(self.root), label=Win(self.root, cursor="", clickthrough=True))
            v["body"].bind("<Button-1>", lambda e, pid=a.id: self.on_click and self.on_click(pid))
            self._refocus_soon()
        return v

    def _refocus_soon(self):
        if self.refocus:
            self.root.after(30, self.refocus)
            self.root.after(250, self.refocus)

    def body_image(self, a):
        """(키, 그림 만드는 함수)"""
        if a.die_t is None:
            name, flip, sx, sy, ang, dy = a.pose()
            if name not in a.imgs:
                name = "서있기"
            return ("live", a.ch, name, flip, sx, sy, ang), (lambda: transform(a.imgs[name], flip, sx, sy, ang)), dy
        t = a.die_t
        # 1) 놀라서 톡 튀어 오름 → 2) 땅 속으로 꺼짐(세로로 줄며 흐려짐) → 3) 무덤이 솟아남
        if t < self.DIE_JUMP:
            hop = math.sin(t / self.DIE_JUMP * math.pi) * 18
        else:
            hop = 0
        sink = 0.0 if t < self.DIE_JUMP else min(1.0, (t - self.DIE_JUMP) / (self.DIE_SINK - self.DIE_JUMP))
        rise = 0.0 if t < self.TOMB_START else min(1.0, (t - self.TOMB_START) / self.TOMB_RISE)
        if rise < 1:
            rise_s = rise * 1.12 if rise < .8 else 1.12 - (rise - .8) / .2 * .12   # 살짝 튀어 오름
        else:
            rise_s = 1.0
        if t >= self.TOMB_START:
            sink = 1.0
        key = ("dead", a.ch, a.face > 0, round(sink, 2), round(rise_s, 2))

        def make():
            parts = []
            if sink < 1:
                fr = "놀람" if "놀람" in a.imgs else "서있기"
                c = transform(a.imgs[fr], a.face > 0, 1, max(0.02, 1 - sink))
                if sink > 0:
                    al = c.getchannel("A").point(lambda v: int(v * (1 - sink * 0.85)))
                    c.putalpha(al)
                parts.append(c)
            if rise_s > 0:
                tb = tomb_img()
                parts.append(tb.resize((tb.width, max(1, int(tb.height * rise_s))), Image.BILINEAR))
            if not parts:
                return Image.new("RGBA", (1, 1), (0, 0, 0, 0))
            w = max(p.width for p in parts)
            h = max(p.height for p in parts)
            out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            for p in reversed(parts):           # 무덤이 뒤, 꺼지는 캐릭터가 앞
                out.alpha_composite(p, ((w - p.width) // 2, h - p.height))
            return out
        return key, make, hop

    def draw(self, a, now):
        v = self.views_of(a)
        key, make, dy = self.body_image(a)
        v["body"].image(key, make)
        bw, bh = v["body"].size
        S = SC["v"]
        cx, bottom = a.x * S, (self.ground - a.y - dy) * S
        v["body"].place(cx - bw / 2, bottom - bh + px(2))
        if self._shadow is None:
            self._shadow = ground_shadow()
        if a.alive:
            v["shadow"].image("s", lambda: self._shadow)
            sw, sh = v["shadow"].size
            v["shadow"].place(cx - sw / 2, self.ground * S - sh / 2 - px(3))
        else:
            v["shadow"].image("none", lambda: Image.new("RGBA", (1, 1), (0, 0, 0, 0)))
        bub = a.bubble if now < a.bubble_until else ""
        lkey = (a.nick, a.sub, a.mine, a.alive, a.ai, a.mafia, bub, a.bubble_kind if bub else "")

        def mk():
            parts = ([bubble_img(bub, a.bubble_kind)] if bub else []) + \
                    [name_tag(a.nick, a.sub, a.mine, not a.alive, a.ai, a.mafia)]
            w = max(i.width for i in parts)
            h = sum(i.height for i in parts) - px(6) * (len(parts) - 1)
            out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            y = 0
            for i in parts:
                out.alpha_composite(i, ((w - i.width) // 2, y))
                y += i.height - px(6)
            return out
        v["label"].image(lkey, mk)
        lw, lh = v["label"].size
        top = (self.ground - a.y - dy) * S - (bh if a.alive else px(SIZE)) + px(14)
        v["label"].place(min(max(0, cx - lw / 2), self.WP - lw), top - lh)

    # ------------------------------------------------ 전체 화면 글자
    IN, HOLD, OUT = 0.7, 1.6, 0.8

    def step_banner(self, now):
        if self.cur is None and self.banners:
            b = self.banners.pop(0)
            text = Win(self.root, cursor="", clickthrough=True)
            text.alpha = 0
            text.show(banner_img(b["title"], b["sub"], b["color"]))
            tw, th = text.size
            text.place((self.WP - tw) / 2, (self.HP - th) / 2 - self.HP * 0.08)
            dim = Dim(self.root, self.WP, self.HP, b["tint"])
            text.raise_top()
            self._refocus_soon()
            self.cur = dict(b, text=text, dimw=dim, t0=now)
        c = self.cur
        if not c:
            return
        t = now - c["t0"]
        if t < self.IN:
            e = t / self.IN
        elif t < self.IN + self.HOLD:
            e = 1.0
        elif t < self.IN + self.HOLD + self.OUT:
            e = 1 - (t - self.IN - self.HOLD) / self.OUT
        else:
            c["text"].destroy()
            c["dimw"].destroy()
            self.cur = None
            return
        e = e * e * (3 - 2 * e)                  # 부드럽게
        c["dimw"].set_alpha(e * c["dimv"])
        c["text"].set_alpha(e * 255)


def composite(desk, bg=(58, 92, 120), w=None, h=None):
    """점검용: FakeWin 들을 바탕화면 그림 한 장으로 합친다."""
    W, H = w or desk.WP, h or desk.HP
    out = Image.new("RGBA", (W, H), bg + (255,))
    d = ImageDraw.Draw(out)
    gy = int(desk.ground * SC["v"])
    d.rectangle((0, gy, W, H), fill=(28, 30, 38, 255))          # 작업표시줄
    for w_ in FakeWin.all:
        if isinstance(w_, Dim):
            if w_.alpha > 0:
                col = tuple(int(w_.color[i:i + 2], 16) for i in (1, 3, 5))
                layer = Image.new("RGBA", (W, H), col + (int(255 * w_.alpha),))
                out.alpha_composite(layer)
            continue
        if w_.im is None or w_.pos is None:
            continue
        im = w_.im
        if w_.alpha < 255:
            im = im.copy()
            im.putalpha(im.getchannel("A").point(lambda v, a=w_.alpha: v * a // 255))
        x, y = w_.pos
        if x > W or y > H or x + im.width < 0 or y + im.height < 0:
            continue
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        layer.paste(im, (x, y))
        out.alpha_composite(layer)
    return out
