"""
scxfyy's music bar (Windows)

A borderless, always-on-top now-playing widget. It reads Windows' system-wide
media controls, so it works with Spotify, Apple Music, YouTube in a browser, etc.

Controls:
  - Drag anywhere on the bar to move it
  - Click the lock icon (top right) to LOCK it. While locked, your mouse passes
    straight through the bar, so it never gets in your way.
  - Press Ctrl+Alt+L anywhere to lock / unlock
  - Click the X to close it
  - The bar never steals focus, so clicking it won't pull you out of your game/app

Install:
    python -m pip install winrt-runtime winrt-Windows.Media.Control winrt-Windows.Storage.Streams winrt-Windows.Foundation winrt-Windows.Foundation.Collections pillow

Run:
    python now_playing.py
"""
import asyncio
import ctypes
import io
import random
import threading
from ctypes import wintypes
from datetime import datetime, timezone
import tkinter as tk

from PIL import Image, ImageDraw, ImageFont, ImageTk
from winrt.windows.media.control import (
    GlobalSystemMediaTransportControlsSessionManager as Manager,
    GlobalSystemMediaTransportControlsSessionPlaybackStatus as Status,
)
from winrt.windows.storage.streams import DataReader

APP_NAME = "scxfyy's music bar"

# ---- look & feel (orange + bold) --------------------------------------------
W, H = 470, 175
ART = 110
KEY = "#010101"        # this colour is made see-through (gives rounded corners)
BG = "#140d06"
ORANGE = "#ff7a00"
ORANGE2 = "#ffb020"
TEXT = "#ffffff"
MUTED = "#d9a066"
TRACK = "#3d2a17"
DIM = "#7a4a14"        # outline colour while locked
X0, X1, PY = 150, 440, 108   # progress bar left, right, y

# ---- Windows plumbing -------------------------------------------------------
user32 = ctypes.windll.user32
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_long
user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
user32.SetWindowLongW.restype = ctypes.c_long
user32.GetParent.argtypes = [wintypes.HWND]
user32.GetParent.restype = wintypes.HWND
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                               wintypes.UINT, wintypes.UINT]

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020   # mouse clicks pass through
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000     # show a taskbar button
WS_EX_NOACTIVATE = 0x08000000    # never take focus

hotkey_event = threading.Event()   # set when Ctrl+Alt+L is pressed
hotkey_ok = {"ok": False}


def hotkey_thread():
    """Listen for Ctrl+Alt+L system-wide."""
    MOD_ALT, MOD_CONTROL, MOD_NOREPEAT, VK_L, WM_HOTKEY = 0x1, 0x2, 0x4000, 0x4C, 0x0312
    if not user32.RegisterHotKey(None, 1, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_L):
        print("Could not register Ctrl+Alt+L (another app may be using it)")
        return
    hotkey_ok["ok"] = True
    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
        if msg.message == WM_HOTKEY:
            hotkey_event.set()


# ---- taskbar / window icon: orange music note that says "scxfyy" -------------
def make_icon(size):
    s = size * 4  # draw big, then shrink for smooth edges
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    orange = (255, 122, 0, 255)
    d.rounded_rectangle((0, 0, s - 1, s - 1), radius=s // 4, fill=(20, 13, 6, 255),
                        outline=orange, width=max(2, s // 40))

    def p(x, y):
        return (x * s, y * s)

    # music note: head, stem, flag
    d.ellipse([p(0.20, 0.36), p(0.46, 0.55)], fill=orange)
    d.rectangle([p(0.41, 0.10), p(0.48, 0.46)], fill=orange)
    d.polygon([p(0.48, 0.10), p(0.76, 0.22), p(0.76, 0.36), p(0.48, 0.24)], fill=orange)

    # the word scxfyy, as big as fits
    px = int(s * 0.26)
    font = None
    for name in ("segoeuib.ttf", "arialbd.ttf", "arial.ttf"):
        try:
            while px > 8:
                font = ImageFont.truetype(name, px)
                if d.textlength("scxfyy", font=font) <= s * 0.86:
                    break
                px -= 2
            break
        except Exception:
            font = None
    if font is None:
        font = ImageFont.load_default()
    d.text((s / 2, s * 0.78), "scxfyy", font=font, fill=(255, 255, 255, 255), anchor="mm")
    return img.resize((size, size), Image.LANCZOS)


# ---- shared state (media thread writes, UI thread reads) --------------------
state = {
    "active": False, "title": "Nothing playing", "artist": "",
    "playing": False, "position": 0.0, "duration": 0.0,
    "art": None, "art_id": 0,
}

loop = asyncio.new_event_loop()
manager = None


async def read_art(props):
    """Return album art as raw bytes (or None)."""
    try:
        ref = props.thumbnail
        if ref is None:
            return None
        stream = await ref.open_read_async()
        reader = DataReader(stream.get_input_stream_at(0))
        await reader.load_async(stream.size)
        buf = bytearray(stream.size)
        reader.read_bytes(buf)
        return bytes(buf)
    except Exception:
        return None


async def poll():
    global manager
    manager = await Manager.request_async()
    last_key = None
    while True:
        try:
            s = manager.get_current_session()
            if s is None:
                state.update(active=False, title="Nothing playing", artist="",
                             playing=False, position=0, duration=0, art=None)
                if last_key is not None:
                    state["art_id"] += 1
                last_key = None
            else:
                props = await s.try_get_media_properties_async()
                key = (props.title, props.artist, s.source_app_user_model_id)
                if key != last_key:
                    last_key = key
                    state["art"] = await read_art(props)
                    state["art_id"] += 1
                info = s.get_playback_info()
                tl = s.get_timeline_properties()
                playing = info.playback_status == Status.PLAYING
                duration = (tl.end_time - tl.start_time).total_seconds()
                pos = (tl.position - tl.start_time).total_seconds()
                if playing:  # estimate movement since the last update
                    pos += (datetime.now(timezone.utc) - tl.last_updated_time).total_seconds()
                pos = max(0.0, min(pos, duration)) if duration > 0 else max(0.0, pos)
                state.update(active=True, title=props.title or "Unknown title",
                             artist=props.artist or "", playing=playing,
                             position=pos, duration=duration)
        except Exception as e:
            print("poll error:", e)
        await asyncio.sleep(0.5)


def send(action):
    """Send 'prev', 'toggle' or 'next' to the current media session."""
    async def run():
        s = manager.get_current_session() if manager else None
        if s is None:
            return
        if action == "prev":
            await s.try_skip_previous_async()
        elif action == "next":
            await s.try_skip_next_async()
        else:
            await s.try_toggle_play_pause_async()
    asyncio.run_coroutine_threadsafe(run(), loop)


def fmt(sec):
    sec = int(sec)
    return f"{sec // 60}:{sec % 60:02d}"


def trunc(text, n):
    return text if len(text) <= n else text[: n - 1] + "…"


class MusicBar(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.overrideredirect(True)            # removes the minimize/close bar
        self.attributes("-topmost", True)
        self.configure(bg=KEY)
        self.attributes("-transparentcolor", KEY)
        sw = self.winfo_screenwidth()
        self.geometry(f"{W}x{H}+{sw - W - 40}+40")

        # taskbar / window icon
        self.icons = [ImageTk.PhotoImage(make_icon(n)) for n in (256, 64, 32)]
        self.iconphoto(True, *self.icons)

        self.locked = False
        self.cv = c = tk.Canvas(self, width=W, height=H, bg=KEY,
                                highlightthickness=0, bd=0)
        c.pack()
        self.seen_art_id = -1
        self.photo = None
        self._dx = self._dy = 0

        # card
        self.card = self.round_rect(2, 2, W - 2, H - 2, 24, fill=BG, outline=ORANGE, width=3)

        # header: name + hint + equalizer
        c.create_text(20, 22, text=APP_NAME, anchor="w", fill=ORANGE,
                      font=("Segoe UI", 11, "bold"))
        self.hint_t = c.create_text(172, 23, text="", anchor="w", fill=ORANGE2,
                                    font=("Segoe UI", 8, "bold"))
        self.eq = []
        for i in range(5):
            bx = W - 126 + i * 9
            self.eq.append(c.create_rectangle(bx, 26, bx + 5, 29, fill=ORANGE2, outline=""))

        # lock button
        c.create_rectangle(W - 72, 8, W - 40, 36, fill=BG, outline="", tags="lock")
        self.lock_t = c.create_text(W - 56, 22, text="🔓", fill=MUTED,
                                    font=("Segoe UI Symbol", 12), tags="lock")
        c.tag_bind("lock", "<Button-1>", lambda e: self.toggle_lock())
        c.tag_bind("lock", "<Enter>", lambda e: c.itemconfig(self.lock_t, fill=ORANGE2))
        c.tag_bind("lock", "<Leave>", lambda e: c.itemconfig(self.lock_t, fill=MUTED))

        # close button
        c.create_rectangle(W - 40, 8, W - 8, 36, fill=BG, outline="", tags="close")
        self.close_t = c.create_text(W - 24, 22, text="✕", fill=MUTED,
                                     font=("Segoe UI Symbol", 12, "bold"), tags="close")
        c.tag_bind("close", "<Button-1>", lambda e: self.destroy())
        c.tag_bind("close", "<Enter>", lambda e: c.itemconfig(self.close_t, fill=ORANGE2))
        c.tag_bind("close", "<Leave>", lambda e: c.itemconfig(self.close_t, fill=MUTED))

        # album art (placeholder + real image)
        self.art_ph = self.round_rect(20, 40, 20 + ART, 40 + ART, 14,
                                      fill="#22160b", outline=ORANGE, width=2)
        self.art_note = c.create_text(20 + ART // 2, 40 + ART // 2, text="♪",
                                      fill=ORANGE, font=("Segoe UI Symbol", 36, "bold"))
        self.art_img = c.create_image(20, 40, anchor="nw", state="hidden")

        # text
        self.title_t = c.create_text(X0, 60, anchor="w", fill=TEXT,
                                     font=("Segoe UI", 14, "bold"))
        self.artist_t = c.create_text(X0, 84, anchor="w", fill=MUTED,
                                      font=("Segoe UI", 11, "bold"))

        # progress bar
        c.create_line(X0, PY, X1, PY, width=8, capstyle="round", fill=TRACK)
        self.fill_line = c.create_line(X0, PY, X0, PY, width=8, capstyle="round", fill=ORANGE)
        self.knob = c.create_oval(X0 - 8, PY - 8, X0 + 8, PY + 8, fill=ORANGE2, outline="")
        self.t_left = c.create_text(X0, 127, anchor="w", fill=MUTED, font=("Segoe UI", 10, "bold"))
        self.t_right = c.create_text(X1, 127, anchor="e", fill=MUTED, font=("Segoe UI", 10, "bold"))

        # buttons
        cy = 153
        self.icon_button("prev", 245, cy, "⏮", lambda: send("prev"))
        self.icon_button("next", 335, cy, "⏭", lambda: send("next"))
        self.play_c = c.create_oval(290 - 19, cy - 19, 290 + 19, cy + 19,
                                    fill=ORANGE, outline="", tags="play")
        self.play_t = c.create_text(290, cy, text="▶", fill="#140d06",
                                    font=("Segoe UI Symbol", 14, "bold"), tags="play")
        c.tag_bind("play", "<Button-1>", lambda e: send("toggle"))
        c.tag_bind("play", "<Enter>", lambda e: c.itemconfig(self.play_c, fill=ORANGE2))
        c.tag_bind("play", "<Leave>", lambda e: c.itemconfig(self.play_c, fill=ORANGE))

        # drag the bar around
        c.bind("<ButtonPress-1>", self.start_drag)
        c.bind("<B1-Motion>", self.do_drag)

        # window styles: no focus stealing + taskbar button
        self.update_idletasks()
        self.hwnd = user32.GetParent(self.winfo_id()) or self.winfo_id()
        self.withdraw()
        self.apply_styles()
        self.after(100, self.deiconify)

        self.refresh()

    # ---- window styles / lock ------------------------------------------------
    def apply_styles(self):
        ex = user32.GetWindowLongW(self.hwnd, GWL_EXSTYLE)
        ex |= WS_EX_NOACTIVATE | WS_EX_APPWINDOW
        ex &= ~WS_EX_TOOLWINDOW
        if self.locked:
            ex |= WS_EX_TRANSPARENT      # mouse passes through the bar
        else:
            ex &= ~WS_EX_TRANSPARENT
        user32.SetWindowLongW(self.hwnd, GWL_EXSTYLE, ex)

    def toggle_lock(self):
        if not self.locked and not hotkey_ok["ok"]:
            self.flash_hint("Ctrl+Alt+L is taken, can't lock")
            return
        self.locked = not self.locked
        self.apply_styles()
        self.update_lock_visuals()

    def update_lock_visuals(self):
        c = self.cv
        c.itemconfig(self.lock_t, text="🔒" if self.locked else "🔓")
        c.itemconfig(self.card, outline=DIM if self.locked else ORANGE)
        c.itemconfig(self.hint_t, text="LOCKED · Ctrl+Alt+L to unlock" if self.locked else "")

    def flash_hint(self, text):
        self.cv.itemconfig(self.hint_t, text=text)
        self.after(3000, self.update_lock_visuals)

    # ---- helpers ------------------------------------------------------------
    def round_rect(self, x1, y1, x2, y2, r, **kw):
        pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
               x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        return self.cv.create_polygon(pts, smooth=True, **kw)

    def icon_button(self, tag, cx, cy, symbol, cmd):
        c = self.cv
        c.create_rectangle(cx - 22, cy - 18, cx + 22, cy + 18, fill=BG, outline="", tags=tag)
        t = c.create_text(cx, cy, text=symbol, fill=TEXT,
                          font=("Segoe UI Symbol", 17, "bold"), tags=tag)
        c.tag_bind(tag, "<Button-1>", lambda e: cmd())
        c.tag_bind(tag, "<Enter>", lambda e: c.itemconfig(t, fill=ORANGE2))
        c.tag_bind(tag, "<Leave>", lambda e: c.itemconfig(t, fill=TEXT))
        return t

    def start_drag(self, e):
        self._dx, self._dy = e.x, e.y

    def do_drag(self, e):
        x = self.winfo_x() + e.x - self._dx
        y = self.winfo_y() + e.y - self._dy
        self.geometry(f"+{x}+{y}")

    # ---- live updates ---------------------------------------------------------
    def refresh(self):
        c = self.cv

        if hotkey_event.is_set():       # Ctrl+Alt+L was pressed
            hotkey_event.clear()
            self.toggle_lock()

        playing = state["playing"]
        c.itemconfig(self.title_t, text=trunc(state["title"], 27))
        c.itemconfig(self.artist_t,
                     text=trunc(state["artist"], 38) if state["active"]
                     else "play something on your PC")
        c.itemconfig(self.play_t, text="⏸" if playing else "▶")

        dur, pos = state["duration"], state["position"]
        frac = pos / dur if dur else 0
        x = X0 + (X1 - X0) * frac
        c.coords(self.fill_line, X0, PY, max(x, X0 + 0.1), PY)
        c.coords(self.knob, x - 8, PY - 8, x + 8, PY + 8)
        c.itemconfig(self.t_left, text=fmt(pos))
        c.itemconfig(self.t_right, text=fmt(dur))

        # equalizer bounces while music plays
        for i, bar in enumerate(self.eq):
            h = random.randint(4, 18) if playing else 3
            bx = W - 126 + i * 9
            c.coords(bar, bx, 29 - h, bx + 5, 29)

        # new track -> update artwork
        if state["art_id"] != self.seen_art_id:
            self.seen_art_id = state["art_id"]
            shown = False
            if state["art"]:
                try:
                    img = Image.open(io.BytesIO(state["art"])).convert("RGB")
                    img = img.resize((ART, ART))
                    self.photo = ImageTk.PhotoImage(img)
                    c.itemconfig(self.art_img, image=self.photo, state="normal")
                    shown = True
                except Exception:
                    pass
            if shown:
                c.itemconfig(self.art_ph, state="hidden")
                c.itemconfig(self.art_note, state="hidden")
            else:
                c.itemconfig(self.art_img, state="hidden")
                c.itemconfig(self.art_ph, state="normal")
                c.itemconfig(self.art_note, state="normal")

        self.after(150, self.refresh)


def media_thread():
    asyncio.set_event_loop(loop)
    loop.create_task(poll())
    loop.run_forever()


if __name__ == "__main__":
    try:  # gives the app its own taskbar identity (so it shows our icon, not Python's)
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("scxfyy.musicbar")
    except Exception:
        pass
    threading.Thread(target=media_thread, daemon=True).start()
    threading.Thread(target=hotkey_thread, daemon=True).start()
    MusicBar().mainloop()
