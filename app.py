from __future__ import annotations

import ctypes
import gc
import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import engine
import web_source
from engine import RealtimeASR

CONFIG_PATH = ROOT / "config.json"
DEFAULT_OUTPUT = ROOT / "output" / "obs_subtitle.txt"

MODEL_CHOICES = ["large-v3-turbo", "large-v3", "medium", "small"]
LANG_CHOICES = {"中文": "zh", "粤语（粤语口音）": "yue", "English": "en", "自动识别": "auto"}
DENOISE_CHOICES = {
    "关闭": "off",
    "RNNoise": "rnnoise",
    "RNNoise + 噪声门（推荐）": "rnnoise_gate",
    "仅噪声门": "gate",
}
BG_CHOICES = {"黑色": "#000000", "深灰": "#16181d", "绿幕": "#00b140", "白色": "#ffffff"}

BG = "#0d0f14"
CARD = "#161a22"
CARD2 = "#1d222c"
INPUT = "#11151d"
TEXT = "#e9eef7"
MUTED = "#8b93a7"
ACCENT = "#5b8cff"
ACCENT_HOVER = "#4a7bf0"
GREEN = "#35d07f"
AMBER = "#f0b429"
RED = "#ff5f5f"

DEFAULTS = {
    "device": "",
    "asr_backend": "sensevoice",
    "model": "large-v3",
    "language": "zh",
    "compute_device": "auto",
    "denoise_mode": "rnnoise_gate",
    "gate_db": -50,
    "vad": 2,
    "max_segment": 12,
    "silence_ms": 800,
    "hotwords": "恐鬼症,英雄联盟,异环,灵球,通灵盒,EMF,暗影,御灵,赤鬼,双洞门,掉帧,帧数,显卡,内存,散热,CPU,GPU,OBS",
    "corrections": "丢帧=掉帧",
    "number_mode": True,
    "blocklist": "词曲\n作词\n作曲\n编曲\n字幕\nAmara\n明镜与点点",
    "censor_enabled": True,
    "censor_words": "傻逼\n傻B\n沙比\n煞笔\n妈的\n他妈\n你妈\n尼玛\n卧槽\n我操\n操你\n草泥马\n贱人\n婊子\n王八蛋\n畜生\n狗东西\n去死\n强奸\n自杀\n毒品\n吸毒\n赌博\n枪支\n炸弹\n恐怖袭击\n色情\n裸聊\n卖淫\n嫖娼",
    "obs_enabled": False,
    "obs_file": str(DEFAULT_OUTPUT),
    "obs_keep_lines": 1,
    "obs_width": 40,
    "obs_show_partial": True,
    "web_enabled": True,
    "web_port": 8765,
    "subtitle_hold": 4.0,
    "overlay_enabled": False,
    "overlay_font_size": 34,
    "overlay_bg": "#000000",
    "overlay_alpha": 0.85,
    "overlay_geometry": "",
    "overlay_clickthrough": False,
    "font_size": 17,
}

def load_config() -> dict:
    cfg = dict(DEFAULTS)
    try:
        if CONFIG_PATH.is_file():
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                cfg.update({k: v for k, v in data.items() if k in DEFAULTS})
    except Exception:
        pass
    return cfg

def save_config(cfg: dict) -> None:
    try:
        CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

def set_click_through(win: tk.Toplevel, enable: bool) -> None:
    try:
        GWL_EXSTYLE = -20
        WS_EX_TRANSPARENT = 0x00000020
        WS_EX_LAYERED = 0x00080000
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id()) or win.winfo_id()
        style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        if enable:
            style |= WS_EX_TRANSPARENT | WS_EX_LAYERED
        else:
            style &= ~WS_EX_TRANSPARENT
        ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
    except Exception:
        pass

class Overlay:

    def __init__(self, app: "App"):
        self.app = app
        self.win: tk.Toplevel | None = None
        self.label: tk.Label | None = None
        self._drag = None
        self.text = ""
        self.center: tuple[float, float] | None = None

    def show(self) -> None:
        if self.win is not None:
            return
        cfg = self.app.cfg
        w = tk.Toplevel(self.app.root)
        self.win = w
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        try:
            w.attributes("-alpha", float(cfg["overlay_alpha"]))
        except Exception:
            pass
        w.configure(bg=cfg["overlay_bg"])
        sw = self.app.root.winfo_screenwidth()
        self.label = tk.Label(
            w,
            text="",
            bg=cfg["overlay_bg"],
            fg="#ffffff" if cfg["overlay_bg"] != "#ffffff" else "#111111",
            font=("Microsoft YaHei UI", int(cfg["overlay_font_size"]), "bold"),
            justify="center",
            wraplength=int(sw * 0.78),
            padx=26,
            pady=14,
        )
        self.label.pack(fill="both", expand=True)
        self.label.bind("<Button-1>", self._press)
        self.label.bind("<B1-Motion>", self._move)
        self.label.bind("<ButtonRelease-1>", self._release)

        geo = cfg.get("overlay_geometry") or ""
        try:
            if "," in geo:
                cx, cy = geo.split(",")[:2]
                self.center = (float(cx), float(cy))
        except Exception:
            self.center = None
        set_click_through(w, bool(cfg["overlay_clickthrough"]))
        self.set_text(self.text)
        self._apply_position()

    def _apply_position(self) -> None:
        if self.win is None:
            return
        self.win.update_idletasks()
        w = max(1, self.win.winfo_width())
        h = max(1, self.win.winfo_height())
        if self.center is None:
            sw = self.win.winfo_screenwidth()
            sh = self.win.winfo_screenheight()
            self.center = (sw / 2, sh * 0.82)
        cx, cy = self.center
        self.win.geometry(f"+{int(cx - w / 2)}+{int(cy - h / 2)}")

    def _press(self, e) -> None:
        self._drag = (e.x_root, e.y_root, self.win.winfo_x(), self.win.winfo_y())

    def _move(self, e) -> None:
        if not self._drag:
            return
        x0, y0, wx, wy = self._drag
        self.win.geometry(f"+{wx + e.x_root - x0}+{wy + e.y_root - y0}")

    def _release(self, _e) -> None:
        self._drag = None
        try:
            w = self.win.winfo_width()
            h = self.win.winfo_height()
            self.center = (self.win.winfo_x() + w / 2, self.win.winfo_y() + h / 2)
            self.app.cfg["overlay_geometry"] = f"{int(self.center[0])},{int(self.center[1])}"
            save_config(self.app.cfg)
        except Exception:
            pass

    def set_text(self, text: str) -> None:
        self.text = text
        if self.win is not None and self.label is not None:
            self.label.configure(text=text)
            self._apply_position()

    def close(self) -> None:
        if self.win is not None:
            self.win.destroy()
            self.win = None
            self.label = None
            self.center = None

class App:
    def __init__(self, root: ctk.CTk):
        self.root = root
        self.cfg = load_config()
        self.ui_q: queue.Queue = queue.Queue()
        self.asr: RealtimeASR | None = None
        self.finals: list[str] = []
        self.partial = ""
        self.device_map: dict[str, int] = {}
        self.overlay = Overlay(self)
        self.recfile = None
        self._starting = False
        self._paused = False
        self._last_level_ts = 0.0
        self.model = None
        self.model_device = None
        self._model_loading = False
        self._pending_start = False

        self.server = web_source.SubtitleServer(port=int(self.cfg["web_port"]))
        self.server.hold = float(self.cfg.get("subtitle_hold", 4))
        self.web_ok = False

        self._build_ui()
        self._load_devices()
        save_config(self.cfg)
        self._start_web()
        self._apply_overlay_visibility()
        self._preload_model()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(50, self._drain)
        self.root.after(300, self._auto_start_check)
        if "--autostart" in sys.argv:
            self.root.after(1500, self.start)
        if "--settings" in sys.argv:
            self.root.after(800, self._open_settings)

    def _font(self, size: int, weight: str = "normal") -> ctk.CTkFont:
        return ctk.CTkFont(family="Microsoft YaHei UI", size=size, weight=weight)

    def _build_ui(self) -> None:
        r = self.root
        r.title("实时字幕 · OBS")
        r.geometry("1060x700")
        r.minsize(900, 600)
        r.configure(fg_color=BG)

        header = ctk.CTkFrame(r, fg_color="transparent", height=72)
        header.pack(fill="x", padx=26, pady=(20, 10))

        left = ctk.CTkFrame(header, fg_color="transparent")
        left.pack(side="left")
        title_row = ctk.CTkFrame(left, fg_color="transparent")
        title_row.pack(anchor="w")
        ctk.CTkLabel(title_row, text="实时字幕", font=self._font(26, "bold"), text_color=TEXT).pack(side="left")
        ctk.CTkLabel(
            title_row, text="  LOCAL ASR", font=self._font(11, "bold"), text_color="#3f4759"
        ).pack(side="left", pady=(8, 0))
        ctk.CTkLabel(
            left, text="麦克风 → 实时文字 → OBS 浏览器源", font=self._font(12), text_color=MUTED
        ).pack(anchor="w", pady=(2, 0))

        right = ctk.CTkFrame(header, fg_color="transparent")
        right.pack(side="right")
        self.dev_cb = ctk.CTkComboBox(
            right,
            width=300,
            height=40,
            values=["(未找到麦克风)"],
            font=self._font(13),
            dropdown_font=self._font(13),
            fg_color=CARD2,
            border_width=0,
            button_color=CARD2,
            button_hover_color="#2a3040",
            text_color=TEXT,
            dropdown_fg_color=CARD2,
            dropdown_text_color=TEXT,
            dropdown_hover_color="#2a3040",
            command=lambda _v: self._on_device_change(),
        )
        self.dev_cb.pack(side="left", padx=(0, 10))
        self.btn_start = ctk.CTkButton(
            right,
            text="开始识别",
            width=132,
            height=42,
            corner_radius=12,
            font=self._font(15, "bold"),
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            command=self.toggle,
        )
        self.btn_start.pack(side="left")

        body = ctk.CTkFrame(r, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=26, pady=(4, 0))
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, minsize=340)
        body.grid_rowconfigure(0, weight=1)

        rec = ctk.CTkFrame(body, fg_color=CARD, corner_radius=18)
        rec.grid(row=0, column=0, sticky="nsew", padx=(0, 16))
        rec.grid_rowconfigure(1, weight=1)
        rec.grid_columnconfigure(0, weight=1)

        rec_head = ctk.CTkFrame(rec, fg_color="transparent")
        rec_head.grid(row=0, column=0, sticky="ew", padx=20, pady=(16, 8))
        ctk.CTkLabel(rec_head, text="识别记录", font=self._font(14, "bold"), text_color=TEXT).pack(side="left")
        self.count_lbl = ctk.CTkLabel(rec_head, text="0 句", font=self._font(12), text_color="#525b70")
        self.count_lbl.pack(side="left", padx=10)
        ctk.CTkButton(
            rec_head, text="清空", width=56, height=26, corner_radius=8, font=self._font(12),
            fg_color="transparent", hover_color="#232936", text_color=MUTED, command=self.clear,
        ).pack(side="right")

        self.txt = ctk.CTkTextbox(
            rec,
            fg_color=INPUT,
            corner_radius=12,
            border_width=0,
            font=self._font(int(self.cfg["font_size"])),
            text_color="#eef2f8",
            wrap="word",
        )
        self.txt.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 12))
        self.txt.configure(state="disabled")

        self.partial_lbl = ctk.CTkLabel(
            rec, text="", font=self._font(15), text_color="#7fb2ff", anchor="w", justify="left", wraplength=560
        )
        self.partial_lbl.grid(row=2, column=0, sticky="ew", padx=22, pady=(0, 14))
        rec.bind("<Configure>", lambda e: self.partial_lbl.configure(wraplength=max(240, e.width - 60)))

        side = ctk.CTkFrame(body, fg_color="transparent")
        side.grid(row=0, column=1, sticky="nsew")
        side.grid_columnconfigure(0, weight=1)

        obs = ctk.CTkFrame(side, fg_color=CARD, corner_radius=18)
        obs.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        ctk.CTkLabel(obs, text="OBS 浏览器源", font=self._font(14, "bold"), text_color=TEXT).pack(
            anchor="w", padx=18, pady=(14, 2)
        )
        ctk.CTkLabel(
            obs, text="OBS 里添加「浏览器」源，地址填下面这个", font=self._font(11), text_color=MUTED
        ).pack(anchor="w", padx=18)
        self.url_var = tk.StringVar(value="http://127.0.0.1:8765")
        url_entry = ctk.CTkEntry(
            obs,
            textvariable=self.url_var,
            state="readonly",
            height=36,
            corner_radius=10,
            fg_color=INPUT,
            border_width=0,
            font=self._font(13, "bold"),
            text_color="#9ec1ff",
        )
        url_entry.pack(fill="x", padx=18, pady=(10, 8))
        row = ctk.CTkFrame(obs, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=(0, 16))
        ctk.CTkButton(
            row, text="复制地址", height=32, corner_radius=9, font=self._font(12, "bold"),
            fg_color=ACCENT, hover_color=ACCENT_HOVER, command=self.copy_url,
        ).pack(side="left", expand=True, fill="x", padx=(0, 6))
        ctk.CTkButton(
            row, text="浏览器预览", height=32, corner_radius=9, font=self._font(12),
            fg_color=CARD2, hover_color="#2a3040", text_color=TEXT, command=self.preview_url,
        ).pack(side="left", expand=True, fill="x")

        status = ctk.CTkFrame(side, fg_color=CARD, corner_radius=18)
        status.grid(row=1, column=0, sticky="ew", pady=(0, 14))
        st_row = ctk.CTkFrame(status, fg_color="transparent")
        st_row.pack(fill="x", padx=18, pady=(14, 6))
        self.dot = tk.Canvas(st_row, width=16, height=16, highlightthickness=0, bg=CARD)
        self.dot.pack(side="left")
        self._dot = self.dot.create_oval(4, 4, 12, 12, fill="#5a6172", outline="")
        self.status_lbl = ctk.CTkLabel(st_row, text="未开始", font=self._font(13, "bold"), text_color=TEXT)
        self.status_lbl.pack(side="left", padx=8)
        self.device_lbl = ctk.CTkLabel(status, text="", font=self._font(11), text_color="#5b6478")
        self.device_lbl.pack(anchor="w", padx=18)
        self.level = ctk.CTkProgressBar(status, height=6, corner_radius=3, fg_color="#0b0e13", progress_color=GREEN)
        self.level.set(0)
        self.level.pack(fill="x", padx=18, pady=(10, 4))
        self.db_lbl = ctk.CTkLabel(status, text="噪音 -- dB", font=self._font(10), text_color="#4c556a")
        self.db_lbl.pack(anchor="e", padx=18, pady=(0, 14))

        ctrl = ctk.CTkFrame(side, fg_color=CARD, corner_radius=18)
        ctrl.grid(row=2, column=0, sticky="ew")
        self.overlay_sw = ctk.CTkSwitch(
            ctrl, text="悬浮字幕窗（截图用）", width=250, font=self._font(12), text_color=TEXT,
            progress_color=ACCENT, command=self._toggle_overlay,
        )
        self.overlay_sw.pack(anchor="w", padx=18, pady=(16, 8))
        self.file_sw = ctk.CTkSwitch(
            ctrl, text="同时写入文本文件", width=250, font=self._font(12), text_color=TEXT,
            progress_color=ACCENT, command=self._toggle_file,
        )
        self.file_sw.pack(anchor="w", padx=18, pady=(0, 8))
        if self.cfg["obs_enabled"]:
            self.file_sw.select()
        if self.cfg["overlay_enabled"]:
            self.overlay_sw.select()

        footer = ctk.CTkFrame(r, fg_color="transparent")
        footer.pack(fill="x", padx=26, pady=(12, 16))
        self.btn_pause = ctk.CTkButton(
            footer, text="暂停", width=84, height=34, corner_radius=10, font=self._font(12, "bold"),
            fg_color=CARD2, hover_color="#2a3040", text_color=TEXT, command=self.toggle_pause, state="disabled",
        )
        self.btn_pause.pack(side="left")
        ctk.CTkButton(
            footer, text="设置", width=84, height=34, corner_radius=10, font=self._font(12),
            fg_color=CARD2, hover_color="#2a3040", text_color=TEXT, command=self._open_settings,
        ).pack(side="left", padx=8)
        ctk.CTkButton(
            footer, text="输出目录", width=96, height=34, corner_radius=10, font=self._font(12),
            fg_color="transparent", hover_color="#1a1f29", text_color=MUTED, command=self.open_output,
        ).pack(side="left")
        self.hint_lbl = ctk.CTkLabel(footer, text="", font=self._font(11), text_color=MUTED)
        self.hint_lbl.pack(side="right")

    def _load_devices(self) -> None:
        try:
            devs = engine.list_input_devices()
        except Exception as e:
            messagebox.showerror("错误", f"读取音频设备失败：{e}")
            return
        self.device_map = {}
        labels = []
        for idx, name, api, _sr in devs:
            label = engine.device_label(idx, name, api)
            if label in self.device_map:
                continue
            self.device_map[label] = idx
            labels.append(label)
        if not labels:
            labels = ["(未找到麦克风)"]
        self.dev_cb.configure(values=labels)
        saved = self.cfg.get("device") or ""
        if saved in self.device_map:
            self.dev_cb.set(saved)
        else:
            default_idx = engine.pick_default_input()
            match = next((l for l, i in self.device_map.items() if i == default_idx), None)
            self.dev_cb.set(match or labels[0])
            if match:
                self.cfg["device"] = match

    def _on_device_change(self) -> None:
        self.cfg["device"] = self.dev_cb.get()
        save_config(self.cfg)
        if self.asr and self.asr.running:
            self.hint_lbl.configure(text="已更换麦克风，请停止后重新开始")

    def _start_web(self) -> None:
        if not self.cfg["web_enabled"]:
            self.url_var.set("网页源已关闭")
            return
        self.server.port = int(self.cfg["web_port"])
        ok = self.server.start()
        self.web_ok = ok
        if ok:
            self.url_var.set(self.server.url)
            self.cfg["web_port"] = self.server.port
        else:
            self.url_var.set("端口被占用，网页源启动失败")
            self.hint_lbl.configure(text=f"网页源启动失败：{getattr(self.server, '_last_error', '')}")

    def copy_url(self) -> None:
        if not self.web_ok:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self.server.url)
        self.hint_lbl.configure(text="地址已复制，粘贴到 OBS 浏览器源里")

    def preview_url(self) -> None:
        if not self.web_ok:
            return
        webbrowser.open(self.server.url + "?preview=1")

    def _auto_start_check(self) -> None:
        if self.cfg.get("asr_backend", "whisper") == "sensevoice":
            ok = (ROOT / "models" / "sensevoice" / "model.int8.onnx").is_file()
            if not ok:
                if messagebox.askyesno(
                    "缺少模型",
                    "还没有 SenseVoice 中文模型（约 230MB）。\n是否现在自动下载？",
                ):
                    self._download_model_async()
            return
        model_dir = ROOT / "models" / self.cfg["model"]
        if not (model_dir / "model.bin").is_file():
            if messagebox.askyesno(
                "缺少模型",
                f"还没有语音模型 {self.cfg['model']}（约 3GB）。\n是否现在自动下载？",
            ):
                self._download_model_async()

    def _download_model_async(self) -> None:
        backend = self.cfg.get("asr_backend", "whisper")
        name = self.cfg["model"] if backend != "sensevoice" else "SenseVoice"
        self._set_status("loading", "正在下载模型…")

        def work() -> None:
            try:
                import model_download

                if backend == "sensevoice":
                    model_download.ensure_sensevoice()
                else:
                    model_download.ensure_model(name)
                self.ui_q.put(("info", "模型下载完成"))
                self.ui_q.put(("model_downloaded", name))
                self.ui_q.put(("status", "paused"))
            except Exception as e:
                self.ui_q.put(("error", f"模型下载失败：{e}"))
            finally:
                self.ui_q.put(("status", "paused"))

        threading.Thread(target=work, daemon=True).start()

    def toggle(self) -> None:
        if self.asr and self.asr.running:
            self.stop()
        else:
            self.start()

    def _preload_model(self) -> None:
        if self._model_loading:
            return
        backend = self.cfg.get("asr_backend", "whisper")
        if backend == "sensevoice":
            model_dir = ROOT / "models" / "sensevoice"
            if not (model_dir / "model.int8.onnx").is_file():
                self.device_lbl.configure(text="模型：SenseVoice 未下载")
                return
        else:
            model_dir = ROOT / "models" / self.cfg["model"]
            if not (model_dir / "model.bin").is_file():
                self.device_lbl.configure(text="模型：未下载")
                return
        self._model_loading = True
        self.device_lbl.configure(text="模型：正在载入…")

        def work() -> None:
            try:
                if backend == "sensevoice":
                    model, dev = engine.load_sensevoice_model(
                        str(model_dir), str(self.cfg.get("language", "zh"))
                    )
                else:
                    model, dev = engine.load_whisper_model(str(model_dir), str(self.cfg["compute_device"]))
                self.ui_q.put(("model_ready", (model, dev)))
            except Exception as e:
                self.ui_q.put(("model_error", str(e)))

        threading.Thread(target=work, daemon=True).start()

    def reload_model(self) -> None:
        if self.asr:
            self.stop()
        self.model = None
        self.model_device = None
        self._model_loading = False
        gc.collect()
        self._preload_model()

    def start(self) -> None:
        if self._starting:
            return
        demo = "--demo" in sys.argv
        source = None
        if demo:
            source = load_demo_audio()
            if source is None:
                messagebox.showerror("演示模式", "找不到 demo.wav")
                return
        else:
            backend = self.cfg.get("asr_backend", "whisper")
            if backend == "sensevoice":
                ok = (ROOT / "models" / "sensevoice" / "model.int8.onnx").is_file()
                name = "SenseVoice"
            else:
                ok = (ROOT / "models" / self.cfg["model"] / "model.bin").is_file()
                name = self.cfg["model"]
            if not ok:
                if messagebox.askyesno("缺少模型", f"模型 {name} 还没下载，现在下载吗？"):
                    self._download_model_async()
                return
            idx = self.device_map.get(self.dev_cb.get())
            if idx is None:
                messagebox.showwarning("提示", "请选择麦克风")
                return
        idx = self.device_map.get(self.dev_cb.get()) if not demo else None

        if self.model is None:
            self._pending_start = True
            if self._model_loading:
                self.hint_lbl.configure(text="模型正在载入，稍后自动开始")
            else:
                self.hint_lbl.configure(text="正在载入模型…")
                self._preload_model()
            return

        self._starting = True
        self._set_status("loading", "正在启动…")
        self.btn_start.configure(text="停止识别", fg_color=RED, hover_color="#e05050", state="disabled")
        self.root.update_idletasks()

        backend = self.cfg.get("asr_backend", "whisper")
        model_dir = ROOT / "models" / ("sensevoice" if backend == "sensevoice" else self.cfg["model"])
        self.asr = RealtimeASR(
            model_dir=str(model_dir),
            model=self.model,
            asr_backend=backend,
            model_device=self.model_device,
            device=idx,
            language=self.cfg["language"],
            compute_device=self.cfg["compute_device"],
            denoise_mode=self.cfg["denoise_mode"],
            gate_db=float(self.cfg["gate_db"]),
            corrections=self._parse_corrections(self.cfg.get("corrections", "")),
            number_mode=bool(self.cfg.get("number_mode", True)),
            blocklist=[w for w in str(self.cfg["blocklist"]).splitlines() if w.strip()],
            censor_enabled=bool(self.cfg.get("censor_enabled", True)),
            censor_words=[w for w in str(self.cfg.get("censor_words", "")).splitlines() if w.strip()],
            on_partial=lambda t: self.ui_q.put(("partial", t)),
            on_final=lambda t: self.ui_q.put(("final", t)),
            on_status=lambda s: self.ui_q.put(("status", s)),
            on_level=lambda v: self.ui_q.put(("level", v)),
            on_error=lambda m: self.ui_q.put(("error", m)),
            silence_ms=int(self.cfg["silence_ms"]),
            max_segment=float(self.cfg["max_segment"]),
            vad_aggressiveness=int(self.cfg["vad"]),
            hotwords=self.cfg["hotwords"],
            source_wav=source,
        )
        try:
            self.asr.start()
        except Exception as e:
            self.asr = None
            self._starting = False
            self.btn_start.configure(text="开始识别", fg_color=ACCENT, hover_color=ACCENT_HOVER, state="normal")
            messagebox.showerror("无法启动", f"打开麦克风失败：{e}")
            self._set_status("error", "启动失败")
            return
        self._starting = False
        self.btn_start.configure(state="normal")
        self.btn_pause.configure(state="normal", text="暂停")
        self._open_record_file()
        self._refresh_views()
        if source is not None:
            self.hint_lbl.configure(text="演示模式：播放测试语音")
            self.root.after(4000, lambda: self.hint_lbl.configure(text=""))

    def stop(self) -> None:
        if self.asr:
            asr, self.asr = self.asr, None
            try:
                asr.stop()
            except Exception:
                pass
        self.btn_start.configure(text="开始识别", fg_color=ACCENT, hover_color=ACCENT_HOVER)
        self.btn_pause.configure(state="disabled", text="暂停")
        self._set_status("paused", "已停止")
        self.partial = ""
        self._refresh_views()
        self._close_record_file()

    def toggle_pause(self) -> None:
        if not self.asr:
            return
        if self.asr.paused:
            self.asr.resume()
            self.btn_pause.configure(text="暂停")
        else:
            self.asr.pause()
            self.btn_pause.configure(text="继续")

    def _on_final(self, text: str) -> None:
        self.finals.append(text)
        if len(self.finals) > 500:
            self.finals = self.finals[-500:]
        self.partial = ""
        self.partial_lbl.configure(text="")
        self._insert_final(text)
        self._write_record(text)
        self.count_lbl.configure(text=f"{len(self.finals)} 句")
        self._refresh_views()

    def _insert_final(self, text: str) -> None:
        self.txt.configure(state="normal")
        self.txt.insert("end", text + "\n")
        self.txt.see("end")
        self.txt.configure(state="disabled")

    def _on_partial(self, text: str) -> None:
        self.partial = text
        self.partial_lbl.configure(text=text)
        self._refresh_views()

    def _refresh_views(self) -> None:
        cfg = self.cfg
        keep = max(1, int(cfg["obs_keep_lines"]))
        lines = list(self.finals[-keep:])
        wrapped: list[str] = []
        for s in lines:
            wrapped.extend(engine.wrap_by_width(s, int(cfg["obs_width"])))

        if self.overlay.win is not None:
            shown = list(wrapped)
            if self.partial and cfg["obs_show_partial"] and len(self.partial) >= 3:
                shown += engine.wrap_by_width(self.partial, int(cfg["obs_width"]))
            self.overlay.set_text("\n".join(shown[-keep:]) if shown else "")

        if self.web_ok:
            self.server.hold = float(cfg.get("subtitle_hold", 4))
            self.server.update(list(self.finals[-1:]), self.partial)

        if cfg["obs_enabled"]:
            out = "\n".join(wrapped[-keep:]) if wrapped else ""
            self._write_obs_file(out)

    def _write_obs_file(self, text: str) -> None:
        path = Path(self.cfg["obs_file"])
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        except Exception as e:
            self.hint_lbl.configure(text=f"写文件失败: {e}")

    def _open_record_file(self) -> None:
        try:
            outdir = ROOT / "output"
            outdir.mkdir(parents=True, exist_ok=True)
            path = outdir / f"record-{time.strftime('%Y%m%d-%H%M%S')}.txt"
            self.recfile = open(path, "a", encoding="utf-8", buffering=1)
        except Exception:
            self.recfile = None

    def _write_record(self, text: str) -> None:
        if self.recfile:
            try:
                self.recfile.write(f"[{time.strftime('%H:%M:%S')}] {text}\n")
            except Exception:
                pass

    def _close_record_file(self) -> None:
        if self.recfile:
            try:
                self.recfile.close()
            except Exception:
                pass
            self.recfile = None

    def _toggle_overlay(self) -> None:
        if self.overlay_sw.get():
            self.overlay.show()
            self.cfg["overlay_enabled"] = True
        else:
            self.overlay.close()
            self.cfg["overlay_enabled"] = False
        save_config(self.cfg)
        self._refresh_views()

    def _apply_overlay_visibility(self) -> None:
        if self.cfg["overlay_enabled"]:
            self.root.after(200, self.overlay.show)

    def _toggle_file(self) -> None:
        self.cfg["obs_enabled"] = bool(self.file_sw.get())
        save_config(self.cfg)
        self._refresh_views()

    def clear(self) -> None:
        self.finals = []
        self.partial = ""
        self.partial_lbl.configure(text="")
        self.count_lbl.configure(text="0 句")
        self.txt.configure(state="normal")
        self.txt.delete("1.0", "end")
        self.txt.configure(state="disabled")
        self._refresh_views()

    def open_output(self) -> None:
        outdir = Path(self.cfg["obs_file"]).parent
        outdir.mkdir(parents=True, exist_ok=True)
        os.startfile(str(outdir))

    def _set_status(self, status: str, text: str | None = None) -> None:
        colors = {
            "listening": GREEN, "speech": AMBER, "loading": ACCENT,
            "processing": ACCENT, "paused": "#5a6172", "error": RED,
        }
        labels = {
            "listening": "聆听中", "speech": "正在说话…", "loading": "加载中…",
            "processing": "识别中…", "paused": "已暂停", "error": "出错",
        }
        self.dot.itemconfigure(self._dot, fill=colors.get(status, "#5a6172"))
        self.status_lbl.configure(text=text or labels.get(status, status))
        if hasattr(self, "device_lbl") and self.asr is not None and self.asr.used_device:
            if self.asr.used_device == "sensevoice":
                self.device_lbl.configure(text="推理：SenseVoice · CPU")
            else:
                dev = {"cuda": "NVIDIA CUDA", "cpu": "CPU"}.get(self.asr.used_device, self.asr.used_device)
                self.device_lbl.configure(text=f"推理：{dev} · 模型常驻显存")

    def _drain(self) -> None:
        try:
            while True:
                kind, payload = self.ui_q.get_nowait()
                if kind == "final":
                    self._on_final(payload)
                elif kind == "partial":
                    self._on_partial(payload)
                elif kind == "level":
                    v = float(payload)
                    self.level.set(v)
                    self.db_lbl.configure(text=f"噪音 {v*55-60:.0f} dB")
                elif kind == "status":
                    self._set_status(payload)
                    if payload == "error":
                        self.btn_start.configure(text="开始识别", fg_color=ACCENT, hover_color=ACCENT_HOVER)
                        self.btn_pause.configure(state="disabled")
                elif kind == "error":
                    self._set_status("error", "出错")
                    messagebox.showerror("识别错误", payload)
                    if self.asr is not None and not self.asr.running:
                        self.stop()
                elif kind == "info":
                    self.hint_lbl.configure(text=payload)
                    self.root.after(4000, lambda: self.hint_lbl.configure(text=""))
                elif kind == "model_ready":
                    self.model, self.model_device = payload
                    self._model_loading = False
                    if self.model_device == "sensevoice":
                        self.device_lbl.configure(text="模型：已常驻内存（SenseVoice）")
                        self.hint_lbl.configure(text="SenseVoice 就绪（CPU，不占显存），可以随时开始识别")
                    else:
                        dev = {"cuda": "NVIDIA CUDA", "cpu": "CPU"}.get(self.model_device, self.model_device)
                        if self.model_device == "cuda":
                            self.device_lbl.configure(text=f"模型：已常驻显存（{dev}）")
                        else:
                            self.device_lbl.configure(text=f"模型：已载入内存（{dev}）")
                        self.hint_lbl.configure(text=f"模型就绪（{dev}），可以随时开始识别")
                    self.root.after(4000, lambda: self.hint_lbl.configure(text=""))
                    if self._pending_start:
                        self._pending_start = False
                        self.start()
                elif kind == "model_error":
                    self._model_loading = False
                    self.device_lbl.configure(text="模型：加载失败")
                    self.hint_lbl.configure(text=f"模型加载失败：{payload}")
                    if self._pending_start:
                        self._pending_start = False
                        messagebox.showerror("模型加载失败", payload)
                elif kind == "model_downloaded":
                    self._preload_model()
        except queue.Empty:
            pass
        self.root.after(50, self._drain)

    def _on_close(self) -> None:
        try:
            if self.asr:
                self.asr.stop()
        except Exception:
            pass
        try:
            self.server.stop()
        except Exception:
            pass
        self._close_record_file()
        save_config(self.cfg)
        self.root.destroy()

    def _open_settings(self) -> None:
        SettingsDialog(self)

    @staticmethod
    def _parse_corrections(text: str) -> dict[str, str]:
        rules: dict[str, str] = {}
        for line in str(text or "").splitlines():
            if "=" in line:
                wrong, right = line.split("=", 1)
                if wrong.strip() and right.strip():
                    rules[wrong.strip()] = right.strip()
        return rules

class SettingsDialog:
    def __init__(self, app: App):
        self.app = app
        self.cfg = dict(app.cfg)
        self.top = ctk.CTkToplevel(app.root)
        self.top.title("设置")
        self.top.geometry("680x780")
        self.top.configure(fg_color=BG)
        self.top.transient(app.root)
        self.top.grab_set()
        self._dl_state = {"done": True, "msg": ""}

        head = ctk.CTkFrame(self.top, fg_color="transparent")
        head.pack(fill="x", padx=24, pady=(20, 6))
        ctk.CTkLabel(head, text="设置", font=app._font(20, "bold"), text_color=TEXT).pack(side="left")

        self.scroll = ctk.CTkScrollableFrame(self.top, fg_color="transparent")
        self.scroll.pack(fill="both", expand=True, padx=16, pady=(0, 6))

        self._section("识别")
        self.engine_cb = self._combo(
            "识别引擎",
            ["SenseVoice（中文专精，推荐）", "Whisper（多语言）"],
            "Whisper（多语言）" if self.cfg.get("asr_backend") == "whisper" else "SenseVoice（中文专精，推荐）",
        )
        self.engine_cb.configure(command=lambda _v: self._check_model())
        self.model_cb = self._combo("Whisper 模型", MODEL_CHOICES, self.cfg["model"])
        self.model_state = ctk.CTkLabel(self.scroll, text="", font=app._font(11), text_color=MUTED)
        self.model_state.pack(anchor="w", padx=8, pady=(0, 2))
        drow = ctk.CTkFrame(self.scroll, fg_color="transparent")
        drow.pack(fill="x", padx=8, pady=(0, 8))
        ctk.CTkButton(
            drow, text="下载模型", width=100, height=30, corner_radius=8, font=app._font(12),
            fg_color=CARD2, hover_color="#2a3040", text_color=TEXT, command=self._download,
        ).pack(side="left")
        ctk.CTkButton(
            drow, text="手动下载说明", width=120, height=30, corner_radius=8, font=app._font(12),
            fg_color="transparent", hover_color="#1a1f29", text_color=MUTED, command=self._manual_help,
        ).pack(side="left", padx=6)

        self.device_cb = self._combo(
            "推理设备",
            ["自动（优先 NVIDIA）", "NVIDIA 显卡 (CUDA)", "CPU"],
            {"auto": "自动（优先 NVIDIA）", "cuda": "NVIDIA 显卡 (CUDA)",
             "cpu": "CPU"}.get(self.cfg["compute_device"], "自动（优先 NVIDIA）"),
        )
        self.lang_cb = self._combo("语言", list(LANG_CHOICES), next(
            (k for k, v in LANG_CHOICES.items() if v == self.cfg["language"]), "中文"))
        self.hot_entry = self._entry("热词（逗号分隔）", self.cfg["hotwords"], "游戏名、人名，提高识别率")

        self._section("降噪（CPU，和 OBS 同款）")
        self.denoise_cb = self._combo(
            "降噪模式",
            [v for v in DENOISE_CHOICES],
            next((k for k, v in DENOISE_CHOICES.items() if v == self.cfg["denoise_mode"]), "RNNoise + 噪声门"),
        )
        self.gate = self._slider_range("噪声门阈值", -60, -20, float(self.cfg["gate_db"]), "dB")
        ctk.CTkLabel(
            self.scroll,
            text="RNNoise 负责压掉持续底噪；噪声门把更小的杂音直接静音（OBS 的「噪音阈值」）。"
                 "阈值建议比你的环境噪音高 3~6 dB。",
            font=app._font(10), text_color="#4c556a", wraplength=560, justify="left",
        ).pack(anchor="w", padx=8, pady=(2, 6))

        self._section("断句")
        self.silence = self._spin("静音断句 (ms)", 300, 2000, 50, int(self.cfg["silence_ms"]))
        self.maxseg = self._spin("最长一句 (秒)", 4, 20, 1, int(self.cfg["max_segment"]))
        self.vad = self._spin("VAD 灵敏度（0 灵敏 ~ 3 抗噪）", 0, 3, 1, int(self.cfg["vad"]))

        self._section("字幕")
        self.number_sw = self._switch("数字转阿拉伯数字", bool(self.cfg.get("number_mode", True)), "如「十号房」→「10号房」")
        self.hold = self._slider_range("字幕停留（秒）", 1.5, 12, float(self.cfg.get("subtitle_hold", 4)), "s")
        self.width = self._spin("每行宽度", 16, 80, 2, int(self.cfg["obs_width"]))
        self.keep = self._spin("显示行数", 1, 3, 1, int(self.cfg["obs_keep_lines"]))
        self.show_partial = self._switch("显示「正在识别」的内容", bool(self.cfg["obs_show_partial"]), "更及时，但文字会跳动")
        self.ofs = self._spin("悬浮窗字号", 14, 90, 2, int(self.cfg["overlay_font_size"]))
        self.bg_cb = self._combo("悬浮窗背景", list(BG_CHOICES), next(
            (k for k, v in BG_CHOICES.items() if v == self.cfg["overlay_bg"]), "黑色"))
        self.alpha = self._slider("悬浮窗透明度", float(self.cfg["overlay_alpha"]))
        self.through = self._switch("悬浮窗鼠标穿透", bool(self.cfg["overlay_clickthrough"]), "不挡操作")

        self._section("文件输出（可选）")
        self.obs_on = self._switch("写入文本文件", bool(self.cfg["obs_enabled"]), "给 OBS「文本(GDI+)」源用")
        self.obs_path = self._entry("文件路径", self.cfg["obs_file"])
        ctk.CTkButton(
            self.scroll, text="选择路径…", width=110, height=28, corner_radius=8, font=app._font(11),
            fg_color="transparent", hover_color="#1a1f29", text_color=MUTED, command=self._browse,
        ).pack(anchor="w", padx=8, pady=(0, 8))

        self._section("屏蔽词（每行一个，命中整句丢弃）")
        self.block = ctk.CTkTextbox(
            self.scroll, height=110, corner_radius=10, fg_color=INPUT, border_width=0,
            font=app._font(12), text_color=TEXT, wrap="word",
        )
        self.block.pack(fill="x", padx=8, pady=(0, 12))
        self.block.insert("1.0", self.cfg["blocklist"])

        self._section("直播安全（违禁词替换为 *）")
        self.censor_sw = self._switch(
            "启用违禁词过滤", bool(self.cfg.get("censor_enabled", True)), "命中后每个字变成 *"
        )
        self.censor_box = ctk.CTkTextbox(
            self.scroll, height=110, corner_radius=10, fg_color=INPUT, border_width=0,
            font=app._font(12), text_color=TEXT, wrap="word",
        )
        self.censor_box.pack(fill="x", padx=8, pady=(0, 4))
        self.censor_box.insert("1.0", self.cfg.get("censor_words", ""))
        ctk.CTkLabel(
            self.scroll,
            text="每行一个词，按平台要求自行增删；只做替换，不会阻断识别",
            font=app._font(10), text_color="#4c556a",
        ).pack(anchor="w", padx=8, pady=(0, 10))

        self._section("纠错规则（每行：识别错的=正确写法）")
        self.corr = ctk.CTkTextbox(
            self.scroll, height=80, corner_radius=10, fg_color=INPUT, border_width=0,
            font=app._font(12), text_color=TEXT, wrap="word",
        )
        self.corr.pack(fill="x", padx=8, pady=(0, 4))
        self.corr.insert("1.0", self.cfg.get("corrections", ""))
        ctk.CTkLabel(
            self.scroll,
            text="例：安逸=暗影（只替换这几个字，别写常用词，否则会误伤正常说话）",
            font=app._font(10), text_color="#4c556a",
        ).pack(anchor="w", padx=8, pady=(0, 10))

        btns = ctk.CTkFrame(self.top, fg_color="transparent")
        btns.pack(fill="x", padx=24, pady=(4, 18))
        ctk.CTkButton(
            btns, text="保存", width=110, height=38, corner_radius=10, font=app._font(13, "bold"),
            fg_color=ACCENT, hover_color=ACCENT_HOVER, command=self._save,
        ).pack(side="right")
        ctk.CTkButton(
            btns, text="取消", width=90, height=38, corner_radius=10, font=app._font(13),
            fg_color=CARD2, hover_color="#2a3040", text_color=TEXT, command=self.top.destroy,
        ).pack(side="right", padx=8)

        self._check_model()
        self.top.after(300, self._poll_download)

    def _section(self, title: str) -> None:
        ctk.CTkLabel(
            self.scroll, text=title, font=self.app._font(12, "bold"), text_color="#5b6478"
        ).pack(anchor="w", padx=8, pady=(14, 4))

    def _row(self) -> ctk.CTkFrame:
        f = ctk.CTkFrame(self.scroll, fg_color="transparent")
        f.pack(fill="x", padx=8, pady=4)
        return f

    def _label(self, row: ctk.CTkFrame, text: str) -> None:
        ctk.CTkLabel(row, text=text, font=self.app._font(12), text_color=TEXT, width=170, anchor="w").pack(side="left")

    def _combo(self, label: str, values: list[str], value: str) -> ctk.CTkComboBox:
        row = self._row()
        self._label(row, label)
        cb = ctk.CTkComboBox(
            row, values=values, width=260, height=32, font=self.app._font(12),
            dropdown_font=self.app._font(12), fg_color=INPUT, border_width=0,
            button_color=INPUT, button_hover_color="#2a3040", text_color=TEXT,
            dropdown_fg_color=CARD2, dropdown_text_color=TEXT, dropdown_hover_color="#2a3040",
        )
        cb.set(value)
        cb.pack(side="left")
        return cb

    def _entry(self, label: str, value: str, hint: str = "") -> ctk.CTkEntry:
        row = self._row()
        self._label(row, label)
        e = ctk.CTkEntry(
            row, height=32, corner_radius=8, fg_color=INPUT, border_width=0,
            font=self.app._font(12), text_color=TEXT,
        )
        e.insert(0, value)
        e.pack(side="left", fill="x", expand=True)
        if hint:
            ctk.CTkLabel(self.scroll, text=hint, font=self.app._font(10), text_color="#4c556a").pack(
                anchor="w", padx=(190, 8), pady=(0, 2)
            )
        return e

    def _spin(self, label: str, lo: int, hi: int, step: int, value: int) -> ctk.CTkEntry:
        row = self._row()
        self._label(row, label)
        var = tk.StringVar(value=str(value))

        def bump(d: int) -> None:
            try:
                v = int(var.get()) + d * step
            except ValueError:
                v = value
            var.set(str(max(lo, min(hi, v))))

        ctk.CTkButton(
            row, text="-", width=28, height=28, corner_radius=8, font=self.app._font(14),
            fg_color=CARD2, hover_color="#2a3040", text_color=TEXT, command=lambda: bump(-1),
        ).pack(side="left")
        e = ctk.CTkEntry(
            row, textvariable=var, width=70, height=30, corner_radius=8, fg_color=INPUT,
            border_width=0, font=self.app._font(12), text_color=TEXT, justify="center",
        )
        e.pack(side="left", padx=6)
        ctk.CTkButton(
            row, text="+", width=28, height=28, corner_radius=8, font=self.app._font(14),
            fg_color=CARD2, hover_color="#2a3040", text_color=TEXT, command=lambda: bump(1),
        ).pack(side="left")
        return e

    def _switch(self, label: str, value: bool, hint: str = "") -> ctk.CTkSwitch:
        row = self._row()
        sw = ctk.CTkSwitch(
            row, text=label, width=250, font=self.app._font(12), text_color=TEXT,
            progress_color=ACCENT,
        )
        sw.pack(side="left")
        if value:
            sw.select()
        if hint:
            ctk.CTkLabel(row, text=hint, font=self.app._font(10), text_color="#4c556a").pack(side="left", padx=10)
        return sw

    def _slider(self, label: str, value: float) -> ctk.CTkSlider:
        row = self._row()
        self._label(row, label)
        s = ctk.CTkSlider(
            row, from_=0.3, to=1.0, width=200, height=16, fg_color=CARD2,
            progress_color=ACCENT, button_color=ACCENT, button_hover_color=ACCENT_HOVER,
        )
        s.set(value)
        s.pack(side="left")
        return s

    def _slider_range(self, label: str, lo: float, hi: float, value: float, unit: str = "") -> ctk.CTkSlider:
        row = self._row()
        self._label(row, label)
        val_lbl = ctk.CTkLabel(row, text=f"{value:.0f}{unit}", font=self.app._font(12), text_color=MUTED, width=56)
        s = ctk.CTkSlider(
            row, from_=lo, to=hi, width=220, height=16, fg_color=CARD2,
            progress_color=ACCENT, button_color=ACCENT, button_hover_color=ACCENT_HOVER,
            command=lambda v: val_lbl.configure(text=f"{float(v):.0f}{unit}"),
        )
        s.set(value)
        s.pack(side="left")
        val_lbl.pack(side="left", padx=10)
        return s

    def _check_model(self) -> None:
        if self.engine_cb.get().startswith("SenseVoice"):
            ok = (ROOT / "models" / "sensevoice" / "model.int8.onnx").is_file()
            self.model_state.configure(text="SenseVoice 已下载" if ok else "SenseVoice 未下载（约 230MB）")
            return
        name = self.model_cb.get()
        ok = (ROOT / "models" / name / "model.bin").is_file()
        self.model_state.configure(text="已下载" if ok else "未下载")

    def _download(self) -> None:
        self._dl_state = {"done": False, "msg": "下载中…"}
        self.model_state.configure(text="下载中…")
        sense = self.engine_cb.get().startswith("SenseVoice")
        name = self.model_cb.get()

        def work() -> None:
            try:
                import model_download

                if sense:
                    model_download.ensure_sensevoice()
                else:
                    model_download.ensure_model(name)
                self._dl_state = {"done": True, "msg": "已下载"}
            except Exception as e:
                self._dl_state = {"done": True, "msg": f"下载失败: {e}"}

        threading.Thread(target=work, daemon=True).start()

    def _poll_download(self) -> None:
        if self._dl_state.get("done"):
            self.model_state.configure(text=self._dl_state["msg"])
        else:
            self.top.after(600, self._poll_download)

    def _manual_help(self) -> None:
        if self.engine_cb.get().startswith("SenseVoice"):
            messagebox.showinfo(
                "手动下载 SenseVoice",
                "1. 浏览器打开 https://hf-mirror.com/csukuangfj/"
                "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/tree/main\n\n"
                "2. 下载 model.int8.onnx 和 tokens.txt\n\n"
                f"3. 放进：\n   {ROOT / 'models' / 'sensevoice'}",
            )
            return
        name = self.model_cb.get()
        hf = {
            "large-v3-turbo": "deepdml/faster-whisper-large-v3-turbo-ct2",
            "large-v3": "Systran/faster-whisper-large-v3",
            "medium": "Systran/faster-whisper-medium",
            "small": "Systran/faster-whisper-small",
        }.get(name, "Systran/faster-whisper-small")
        messagebox.showinfo(
            "手动下载模型",
            f"1. 浏览器打开 https://hf-mirror.com/{hf}/tree/main\n\n"
            f"2. 下载 config.json、model.bin、tokenizer.json、\n"
            f"   preprocessor_config.json、vocabulary.txt\n\n"
            f"3. 全部放进：\n   {ROOT / 'models' / name}",
        )

    def _browse(self) -> None:
        p = filedialog.asksaveasfilename(
            title="选择文本文件", defaultextension=".txt", initialfile="obs_subtitle.txt",
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")],
        )
        if p:
            self.obs_path.delete(0, "end")
            self.obs_path.insert(0, p)

    def _save(self) -> None:
        cfg = self.app.cfg
        before = (cfg["model"], cfg["compute_device"], cfg.get("asr_backend"))
        cfg["asr_backend"] = "sensevoice" if self.engine_cb.get().startswith("SenseVoice") else "whisper"
        cfg["model"] = self.model_cb.get()
        sel = self.device_cb.get()
        cfg["compute_device"] = (
            "cuda" if sel.startswith("NVIDIA") else "cpu" if sel == "CPU" else "auto"
        )
        cfg["denoise_mode"] = DENOISE_CHOICES.get(self.denoise_cb.get(), "rnnoise_gate")
        cfg["gate_db"] = round(float(self.gate.get()))
        cfg["language"] = LANG_CHOICES.get(self.lang_cb.get(), "zh")
        cfg["hotwords"] = self.hot_entry.get().strip()
        cfg["silence_ms"] = self._int(self.silence, cfg["silence_ms"])
        cfg["max_segment"] = self._int(self.maxseg, cfg["max_segment"])
        cfg["vad"] = self._int(self.vad, cfg["vad"])
        cfg["obs_width"] = self._int(self.width, cfg["obs_width"])
        cfg["number_mode"] = bool(self.number_sw.get())
        cfg["subtitle_hold"] = round(float(self.hold.get()), 1)
        cfg["obs_keep_lines"] = self._int(self.keep, cfg["obs_keep_lines"])
        cfg["obs_show_partial"] = bool(self.show_partial.get())
        cfg["overlay_font_size"] = self._int(self.ofs, cfg["overlay_font_size"])
        cfg["overlay_bg"] = BG_CHOICES.get(self.bg_cb.get(), "#000000")
        cfg["overlay_alpha"] = round(float(self.alpha.get()), 2)
        cfg["overlay_clickthrough"] = bool(self.through.get())
        cfg["obs_enabled"] = bool(self.obs_on.get())
        cfg["obs_file"] = self.obs_path.get().strip() or str(DEFAULT_OUTPUT)
        cfg["blocklist"] = self.block.get("1.0", "end").strip()
        cfg["censor_enabled"] = bool(self.censor_sw.get())
        cfg["censor_words"] = self.censor_box.get("1.0", "end").strip()
        cfg["corrections"] = self.corr.get("1.0", "end").strip()
        save_config(cfg)

        if self.app.overlay.win is not None:
            self.app.overlay.close()
            self.app.overlay.show()
        if self.app.asr:
            self.app.asr.set_hotwords(cfg["hotwords"])
        self.app.file_sw.select() if cfg["obs_enabled"] else self.app.file_sw.deselect()
        self.app._refresh_views()

        changed = before != (cfg["model"], cfg["compute_device"], cfg.get("asr_backend"))
        if changed:
            self.app.reload_model()

        self.top.destroy()
        if changed:
            self.app.hint_lbl.configure(text="模型已切换，正在重新载入显存…")
        else:
            self.app.hint_lbl.configure(text="设置已保存（麦克风/断句改动在下次开始时生效）")
        self.app.root.after(4000, lambda: self.app.hint_lbl.configure(text=""))

    @staticmethod
    def _int(entry: ctk.CTkEntry, default: int) -> int:
        try:
            return int(entry.get())
        except Exception:
            return default

def load_demo_audio():
    import wave

    import numpy as np

    p = ROOT / "demo.wav"
    if not p.is_file():
        return None
    with wave.open(str(p), "rb") as w:
        sr = w.getframerate()
        ch = w.getnchannels()
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        data = data.reshape(-1, ch).mean(axis=1)
    if sr != engine.SR:
        data = engine.resample_to_16k(data, sr)
    return data

def cli_file(path: str, model: str, language: str) -> None:
    import wave

    import numpy as np

    with wave.open(path, "rb") as w:
        sr = w.getframerate()
        ch = w.getnchannels()
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        data = data.reshape(-1, ch).mean(axis=1)
    data = engine.resample_to_16k(data, sr) if sr != engine.SR else data

    from faster_whisper import WhisperModel

    t0 = time.time()
    m = WhisperModel(str(ROOT / "models" / model), device="cpu", compute_type="int8")
    print(f"模型加载 {time.time()-t0:.2f}s")
    t0 = time.time()
    segments, _info = m.transcribe(
        data, language=None if language == "auto" else language, beam_size=5,
        vad_filter=True, condition_on_previous_text=False,
    )
    text = "".join(s.text for s in segments)
    dt = time.time() - t0
    print(f"音频 {len(data)/engine.SR:.1f}s，耗时 {dt:.2f}s，速度 {len(data)/engine.SR/dt:.2f}x")
    print("结果:", text.strip())

def main() -> None:
    if "--file" in sys.argv:
        i = sys.argv.index("--file")
        cli_file(sys.argv[i + 1], "large-v3-turbo", "zh")
        return
    if "--devices" in sys.argv:
        for idx, name, api, sr in engine.list_input_devices():
            print(f"{idx:3d}  {api:14s} {sr:6d}  {name}")
        return

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    ctk.set_appearance_mode("dark")
    root = ctk.CTk()
    app = App(root)
    root.mainloop()

if __name__ == "__main__":
    main()
