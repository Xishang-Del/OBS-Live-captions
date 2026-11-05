from __future__ import annotations

import math
import os
import queue
import re
import sysconfig
import threading
import time
from collections import deque

import numpy as np

SR = 16000
FRAME_MS = 30
FRAME_SAMPLES = SR * FRAME_MS // 1000
FRAME_BYTES = FRAME_SAMPLES * 2

_CJK = r"\u3400-\u9fff\u3000-\u303f\uff00-\uffef"
_RE_SPACE_CJK = re.compile(rf"(?<=[{_CJK}])\s+(?=[{_CJK}])")
_RE_MULTI_SPACE = re.compile(r"[ \t\u3000]+")
_RE_SPACE_AFTER_PUNCT = re.compile(r"(?<=[，。！？；：、])\s+")

_JUNK_MARKERS = (
    "请不吝点赞", "明镜与点点", "字幕由amara", "amara.org", "字幕志愿者",
    "字幕提供", "中文字幕", "本字幕", "www.", ".com/", "订阅频道",
    "订阅我的频道", "请订阅",
)

_CREDIT_RE = re.compile(
    r"^\s*(?:词曲|作词|作曲|编曲|演唱|原唱|制作人|监制|出品|录音|混音|母带|"
    r"翻译|校对|字幕|统筹|策划|导演|剪辑)\s*[:：]?\s*.{0,12}\s*$"
)
_PUNCT_MAP = {",": "，", "?": "？", "!": "！", ";": "；", ":": "：", ".": "。"}
_END_PUNCT = "。！？!?…，、；：,.~～"

_CN_DIGIT = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
             "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNIT = {"十": 10, "百": 100, "千": 1000}
_NUM_RE = re.compile(r"([零〇一二两三四五六七八九十百千]{1,6})(楼|号|层|杯|帧|秒|分钟|度|℃|赫兹|次|小时)")
_PUNCT_FIX = [
    ("？，", "？"), ("，。", "。"), ("。，", "。"), ("！！", "！"),
    ("？？", "？"), ("，，", "，"), ("。。", "。"),
]
_COMPAT_PUNCT = str.maketrans({
    "﹐": "，", "﹑": "、", "﹒": "。", "﹔": "；", "﹕": "：",
    "﹖": "？", "﹗": "！", "．": "。", "・": "·", "｡": "。",
})

def _cn_to_int(s: str) -> int | None:
    if not s:
        return None
    total, section, num = 0, 0, 0
    for ch in s:
        if ch in _CN_DIGIT:
            num = _CN_DIGIT[ch]
        elif ch in _CN_UNIT:
            unit = _CN_UNIT[ch]
            if num == 0:
                num = 1
            section += num * unit
            num = 0
        else:
            return None
    return total + section + num

def normalize_numbers(text: str) -> str:
    def repl(m: re.Match) -> str:
        value = _cn_to_int(m.group(1))
        if value is None:
            return m.group(0)
        return f"{value}{m.group(2)}"

    return _NUM_RE.sub(repl, text)


def censor_text(text: str, words: list[str] | None) -> str:
    if not text or not words:
        return text
    for w in sorted({w.strip() for w in words if w and w.strip()}, key=len, reverse=True):
        if w in text:
            text = text.replace(w, "*" * len(w))
    return text

def _is_cjk(ch: str) -> bool:
    return bool(ch) and "\u3400" <= ch <= "\u9fff"

def _cjk_punct(text: str) -> str:
    out = []
    for i, ch in enumerate(text):
        if ch in _PUNCT_MAP:
            prev = text[i - 1] if i > 0 else ""
            nxt = text[i + 1] if i + 1 < len(text) else ""
            if _is_cjk(prev) and (not nxt or _is_cjk(nxt) or nxt == " "):
                ch = _PUNCT_MAP[ch]
        out.append(ch)
    return "".join(out)

def is_junk(text: str, blocklist: list[str] | None = None) -> bool:
    low = text.replace(" ", "").lower()
    if any(m in low for m in _JUNK_MARKERS):
        return True
    if _CREDIT_RE.match(text):
        return True
    for w in blocklist or ():
        w = (w or "").strip()
        if w and w.lower() in low:
            return True
    return False

def clean_text(text: str, blocklist: list[str] | None = None, numbers: bool = True) -> str:
    t = (text or "").strip()
    if not t:
        return ""
    t = _RE_MULTI_SPACE.sub(" ", t)
    t = _RE_SPACE_CJK.sub("", t)
    t = _RE_SPACE_AFTER_PUNCT.sub("", t)
    t = _cjk_punct(t)
    t = t.translate(_COMPAT_PUNCT)
    for a, b in _PUNCT_FIX:
        t = t.replace(a, b)
    if numbers:
        t = normalize_numbers(t)
    t = t.strip(" \t\r\n")
    if not t or is_junk(t, blocklist):
        return ""

    if not any(ch.isalnum() or _is_cjk(ch) for ch in t):
        return ""
    return t

def display_width(s: str) -> int:
    w = 0
    for ch in s:
        w += 2 if ord(ch) > 0x2E7F else 1
    return w

def wrap_by_width(text: str, width: int) -> list[str]:
    if width <= 4:
        width = 4
    lines: list[str] = []
    cur = ""
    cur_w = 0
    for ch in text:
        cw = 2 if ord(ch) > 0x2E7F else 1
        if cur_w + cw > width and cur:

            cut = max((cur.rfind(p) for p in "，。！？；、,.!?; "), default=-1)
            if 0 <= cut < len(cur) - 1 and display_width(cur[: cut + 1]) >= width * 0.5:
                lines.append(cur[: cut + 1])
                rest = cur[cut + 1 :]
                cur = rest
                cur_w = display_width(rest)
            else:
                lines.append(cur)
                cur = ""
                cur_w = 0
        cur += ch
        cur_w += cw
    if cur:
        lines.append(cur)
    return lines

def resample_to(x: np.ndarray, sr: int, target: int) -> np.ndarray:
    if sr == target:
        return np.asarray(x, dtype=np.float32)
    from math import gcd
    from scipy.signal import resample_poly

    g = gcd(int(sr), int(target))
    up, down = target // g, int(sr) // g
    return resample_poly(np.asarray(x, dtype=np.float32), up, down).astype(np.float32)

def resample_to_16k(x: np.ndarray, sr: int) -> np.ndarray:
    return resample_to(x, sr, SR)

class Denoiser:

    def __init__(self) -> None:
        from pyrnnoise.rnnoise import create, process_frame

        self._state = create()
        self._process = process_frame
        self._buf = np.zeros(0, np.float32)

    def process(self, x48: np.ndarray) -> np.ndarray:
        if x48.size:
            self._buf = np.concatenate([self._buf, np.asarray(x48, dtype=np.float32)])
        n = (self._buf.size // 480) * 480
        if n == 0:
            return np.zeros(0, np.float32)
        frames = self._buf[:n].reshape(-1, 480)
        out = np.empty(n, np.float32)
        for i in range(frames.shape[0]):
            denoised, _prob = self._process(self._state, frames[i])
            out[i * 480 : (i + 1) * 480] = denoised
        self._buf = self._buf[n:].copy()
        return out / 32768.0

def list_input_devices() -> list[tuple[int, str, str, int]]:
    import sounddevice as sd

    out = []
    for idx, d in enumerate(sd.query_devices()):
        if d.get("max_input_channels", 0) <= 0:
            continue
        api = sd.query_hostapis(d["hostapi"])["name"]
        out.append((idx, d["name"], api, int(d.get("default_samplerate") or 48000)))
    return out

def pick_default_input() -> int | None:
    import sounddevice as sd

    try:
        default_idx = sd.default.device[0]
    except Exception:
        default_idx = -1
    try:
        if default_idx is not None and default_idx >= 0:
            name = sd.query_devices(default_idx)["name"]
            for idx, dname, api, _ in list_input_devices():
                if dname == name and "WASAPI" in api:
                    return idx
            return default_idx
    except Exception:
        pass
    for idx, _, api, _ in list_input_devices():
        if "WASAPI" in api:
            return idx
    return None

def device_label(idx: int, name: str, api: str) -> str:
    tag = "WASAPI" if "WASAPI" in api else ("MME" if "MME" in api else api)
    return f"{name} [{tag}]"

_CUDA_HANDLES: list = []
_CUDA_READY: bool | None = None

def _cuda_dirs() -> list[str]:
    try:
        base = os.path.join(sysconfig.get_path("purelib"), "nvidia")
    except Exception:
        return []
    if not os.path.isdir(base):
        return []
    dirs = []
    for name in os.listdir(base):
        b = os.path.join(base, name, "bin")
        if os.path.isdir(b):
            dirs.append(b)
    return dirs

def cuda_available() -> bool:
    return bool(_cuda_dirs())

def prepare_cuda() -> bool:
    global _CUDA_READY
    if _CUDA_READY is not None:
        return _CUDA_READY
    dirs = _cuda_dirs()
    if not dirs:
        _CUDA_READY = False
        return False
    import ctypes

    for d in dirs:
        try:
            _CUDA_HANDLES.append(os.add_dll_directory(d))
        except Exception:
            pass
    index = {os.path.basename(os.path.dirname(d)): d for d in dirs}
    want = [
        ("cuda_runtime", "cudart64_12.dll"),
        ("cublas", "cublasLt64_12.dll"),
        ("cublas", "cublas64_12.dll"),
        ("cudnn", "cudnn64_9.dll"),
    ]
    for folder, dll in want:
        d = index.get(folder)
        if not d:
            continue
        fp = os.path.join(d, dll)
        if os.path.isfile(fp):
            try:
                _CUDA_HANDLES.append(ctypes.WinDLL(fp))
            except Exception:
                pass
    _CUDA_READY = True
    return True

def load_whisper_model(model_dir: str, compute_device: str = "auto", compute_type: str = "int8", cpu_threads: int | None = None):
    if compute_device in ("cuda", "auto"):
        prepare_cuda()
    from faster_whisper import WhisperModel

    if not os.path.isdir(model_dir):
        raise RuntimeError(f"模型目录不存在: {model_dir}")

    attempts = []
    if compute_device == "cuda" or (compute_device == "auto" and cuda_available()):
        attempts.append(("cuda", "int8_float32"))
    attempts.append(("cpu", compute_type))
    threads = cpu_threads or max(4, min(12, (os.cpu_count() or 8) // 2))
    last_err = None
    for dev, ct in attempts:
        try:
            model = WhisperModel(
                model_dir,
                device=dev,
                compute_type=ct,
                cpu_threads=threads,
                num_workers=1,
            )
            return model, dev
        except Exception as e:
            last_err = e
    raise RuntimeError(f"模型加载失败: {last_err}")

class SenseVoiceModel:

    def __init__(self, model_dir: str, language: str = "zh", num_threads: int = 4):
        import sherpa_onnx

        model_path = os.path.join(model_dir, "model.int8.onnx")
        if not os.path.isfile(model_path):
            model_path = os.path.join(model_dir, "model.onnx")
        tokens = os.path.join(model_dir, "tokens.txt")
        if not os.path.isfile(model_path) or not os.path.isfile(tokens):
            raise RuntimeError(f"找不到 SenseVoice 模型: {model_dir}")
        lang = language if language in ("zh", "en", "yue", "ja", "ko", "auto") else "zh"
        self.rec = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=model_path,
            tokens=tokens,
            num_threads=max(2, num_threads),
            use_itn=True,
            language=lang,
            debug=False,
        )

    def transcribe(self, audio: np.ndarray) -> str:
        stream = self.rec.create_stream()
        stream.accept_waveform(SR, np.asarray(audio, dtype=np.float32))
        self.rec.decode_stream(stream)
        return stream.result.text

def load_sensevoice_model(model_dir: str, language: str = "zh", num_threads: int = 4):
    model = SenseVoiceModel(model_dir, language=language, num_threads=num_threads)
    return model, "sensevoice"

class NoiseGate:
    def __init__(self, threshold_db: float = -50.0, sr: int = SR,
                 attack_ms: float = 5.0, release_ms: float = 250.0):
        self.thr_open = 10.0 ** (float(threshold_db) / 20.0)
        self.thr_close = self.thr_open * 0.5
        self.attack = math.exp(-1000.0 / max(1.0, attack_ms * sr / 1000.0))
        self.release = math.exp(-1000.0 / max(1.0, release_ms * sr / 1000.0))
        self.gain = 0.0
        self.open = False

    def process(self, x: np.ndarray) -> np.ndarray:
        block = 160
        out = np.empty_like(x)
        for i in range(0, x.size, block):
            seg = x[i : i + block]
            rms = float(np.sqrt(np.mean(seg * seg))) if seg.size else 0.0
            if self.open:
                self.open = rms >= self.thr_close
            else:
                self.open = rms >= self.thr_open
            target = 1.0 if self.open else 0.0
            coef = self.attack if target > self.gain else self.release
            end = coef * self.gain + (1.0 - coef) * target
            gains = np.linspace(self.gain, end, seg.size, dtype=np.float32)
            self.gain = end
            out[i : i + seg.size] = seg * gains
        return out

STATUS_LISTENING = "listening"
STATUS_SPEECH = "speech"
STATUS_LOADING = "loading"
STATUS_PAUSED = "paused"
STATUS_PROCESSING = "processing"
STATUS_ERROR = "error"

class RealtimeASR:
    def __init__(
        self,
        model_dir: str,
        device: int | None = None,
        language: str = "zh",
        compute_type: str = "int8",
        compute_device: str = "cpu",
        cpu_threads: int | None = None,
        on_partial=None,
        on_final=None,
        on_status=None,
        on_level=None,
        on_error=None,
        partial_interval: float = 0.7,
        max_segment: float = 12.0,
        silence_ms: int = 650,
        preroll_ms: int = 400,
        vad_aggressiveness: int = 2,
        hotwords: str = "",
        source_wav: np.ndarray | None = None,
        denoise: bool = True,
        denoise_mode: str | None = None,
        gate_db: float = -50.0,
        blocklist: list[str] | None = None,
        corrections: dict[str, str] | None = None,
        number_mode: bool = True,
        censor_enabled: bool = True,
        censor_words: list[str] | None = None,
        model=None,
        model_device: str | None = None,
        asr_backend: str = "whisper",
    ):
        self.model_dir = str(model_dir)
        self.device = device
        self.language = language
        self.compute_type = compute_type
        self.compute_device = compute_device
        self.cpu_threads = cpu_threads or max(4, min(12, (os.cpu_count() or 8) // 2))
        self.on_partial = on_partial or (lambda t: None)
        self.on_final = on_final or (lambda t: None)
        self.on_status = on_status or (lambda s: None)
        self.on_level = on_level or (lambda v: None)
        self.on_error = on_error or (lambda e: None)

        self.partial_interval = partial_interval
        self.max_segment = max_segment
        self.silence_ms = silence_ms
        self.preroll_frames = max(1, preroll_ms // FRAME_MS)
        self.vad_aggressiveness = vad_aggressiveness
        self.hotwords = hotwords
        self.source_wav = source_wav
        self.blocklist = [w for w in (blocklist or []) if w.strip()]
        self.corrections = {k: v for k, v in (corrections or {}).items() if k and v}
        self.number_mode = bool(number_mode)
        self.censor_enabled = bool(censor_enabled)
        self.censor_words = [w for w in (censor_words or []) if w.strip()]
        if denoise_mode is None:
            denoise_mode = "rnnoise_gate" if denoise else "off"
        self.denoise_mode = denoise_mode
        self.gate_db = float(gate_db)
        self.denoise_enabled = "rnnoise" in denoise_mode
        self._denoiser: Denoiser | None = None
        self._gate: NoiseGate | None = None

        self._model = None
        self._preloaded_model = model
        self._preloaded_device = model_device
        self.asr_backend = asr_backend
        self._stop = threading.Event()
        self._paused = False
        self._aq: queue.Queue = queue.Queue()
        self._final_q: queue.Queue = queue.Queue()
        self._partial_lock = threading.Lock()
        self._partial_req = None
        self._seq = 0
        self._context = ""
        self._last_final = ""
        self._threads: list[threading.Thread] = []
        self._stream = None
        self._capture_sr = 0
        self._started = False
        self._status = STATUS_LOADING
        self._level_ts = 0.0
        self._last_level = 0.0
        self.used_device: str | None = None

    @property
    def running(self) -> bool:
        return self._started

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def status(self) -> str:
        return self._status

    def start(self) -> None:
        if self._started:
            return
        self._stop.clear()
        self._paused = False
        self._started = True
        self._set_status(STATUS_LOADING)
        self._threads = [
            threading.Thread(target=self._asr_loop, name="asr", daemon=True),
            threading.Thread(target=self._vad_loop, name="vad", daemon=True),
        ]

        try:
            self._open_capture()
        except Exception:
            self._started = False
            self._stop.set()
            raise
        for t in self._threads:
            t.start()

    def stop(self) -> None:
        self._stop.set()
        self._started = False
        try:
            if self._stream is not None:
                self._stream.stop()
                self._stream.close()
        except Exception:
            pass
        self._stream = None
        for t in self._threads:
            t.join(timeout=2.0)
        self._threads = []

    def pause(self) -> None:
        self._paused = True
        self._set_status(STATUS_PAUSED)

    def resume(self) -> None:
        if self._paused:
            self._paused = False
            self._set_status(STATUS_LISTENING)

    def set_language(self, lang: str) -> None:
        self.language = lang

    def set_hotwords(self, words: str) -> None:
        self.hotwords = (words or "").strip()

    def _open_capture(self) -> None:
        import sounddevice as sd

        if self.source_wav is not None:

            threading.Thread(target=self._feed_wav, daemon=True).start()
            return

        if self.device is None:
            raise RuntimeError("没有可用的麦克风输入设备")
        info = sd.query_devices(self.device)
        sr = int(info.get("default_samplerate") or 48000)
        self._capture_sr = sr
        self._stream = sd.InputStream(
            device=self.device,
            channels=1,
            samplerate=sr,
            dtype="float32",
            blocksize=0,
            latency="low",
            callback=self._on_audio,
        )
        self._stream.start()

    def _on_audio(self, indata, frames, time_info, status) -> None:
        if self._stop.is_set():
            return
        try:
            mono = indata[:, 0].copy()
        except Exception:
            return
        self._aq.put((mono, self._capture_sr))

    def _feed_wav(self) -> None:
        wav = self.source_wav
        step = SR // 10
        t0 = time.time()
        for i in range(0, len(wav), step):
            if self._stop.is_set():
                return
            wait = t0 + i / SR - time.time()
            if wait > 0:
                time.sleep(wait)
            self._aq.put((wav[i : i + step].copy(), SR))

        time.sleep(1.2)

    def _vad_loop(self) -> None:
        import webrtcvad

        try:
            vad = webrtcvad.Vad(int(self.vad_aggressiveness))
        except Exception as e:
            self._fail(f"VAD 初始化失败: {e}")
            return

        if self.denoise_enabled:
            try:
                self._denoiser = Denoiser()
            except Exception as e:
                self._denoiser = None
                self.on_error(f"降噪不可用（pyrnnoise 加载失败）：{e}")
        if "gate" in self.denoise_mode:
            self._gate = NoiseGate(self.gate_db)

        pcm = bytearray()
        seg: list[bytes] = []
        preroll: deque[bytes] = deque(maxlen=self.preroll_frames)
        onset: list[bytes] = []
        in_speech = False
        silence = 0
        speech_run = 0
        voiced_frames = 0
        cut_pending = False
        last_partial = 0.0

        while not self._stop.is_set():
            try:
                chunk, sr = self._aq.get(timeout=0.05)
            except queue.Empty:
                continue

            x = chunk
            if self._denoiser is not None:
                x48 = resample_to(chunk, sr, 48000) if sr != 48000 else chunk
                x = resample_to(self._denoiser.process(x48), 48000, SR)
            elif sr != SR:
                x = resample_to(chunk, sr, SR)
            if x.size == 0:
                continue
            rms = float(np.sqrt(np.mean(x * x)))
            if self._gate is not None:
                x = self._gate.process(x) if x.size else 0.0
            self._emit_level(rms)

            if self._paused:
                pcm.clear()
                seg, onset = [], []
                preroll.clear()
                in_speech = False
                silence = speech_run = voiced_frames = 0
                cut_pending = False
                continue

            pcm.extend((np.clip(x, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes())

            while len(pcm) >= FRAME_BYTES:
                frame = bytes(pcm[:FRAME_BYTES])
                del pcm[:FRAME_BYTES]
                try:
                    voiced = vad.is_speech(frame, SR)
                except Exception:
                    voiced = False

                if not in_speech:
                    if voiced:
                        onset.append(frame)
                        speech_run += 1
                        if speech_run >= 3:
                            in_speech = True
                            seg = list(preroll) + onset
                            voiced_frames = len(onset)
                            onset = []
                            silence = 0
                            speech_run = 0
                            cut_pending = False
                            last_partial = 0.0
                            self._set_status(STATUS_SPEECH)
                    else:
                        onset = []
                        speech_run = 0
                        preroll.append(frame)
                    continue

                seg.append(frame)
                if voiced:
                    silence = 0
                    voiced_frames += 1
                else:
                    silence += 1

                dur = len(seg) * FRAME_MS / 1000.0
                if dur >= self.max_segment:
                    cut_pending = True

                end = silence * FRAME_MS >= self.silence_ms
                if cut_pending and (silence * FRAME_MS >= 150 or dur >= self.max_segment + 2.5):
                    end = True

                if end:
                    keep_tail = min(silence, 6) if cut_pending else silence
                    seg = self._finalize(seg, keep_tail, voiced_frames)
                    preroll = deque(seg[-self.preroll_frames :], maxlen=self.preroll_frames)
                    seg = []
                    in_speech = False
                    silence = speech_run = voiced_frames = 0
                    cut_pending = False
                    self._set_status(STATUS_LISTENING)
                elif not cut_pending and dur >= 0.8 and (time.time() - last_partial) >= self.partial_interval:
                    self._request_partial(seg)
                    last_partial = time.time()

    def _finalize(self, seg: list[bytes], trailing_silence: int, voiced_frames: int = 0) -> list[bytes]:
        frames = seg[: -max(0, trailing_silence - 2)] if trailing_silence > 2 else seg
        audio = np.frombuffer(b"".join(frames), dtype=np.int16).astype(np.float32) / 32768.0

        if audio.size < int(0.30 * SR) or voiced_frames * FRAME_MS < 300:
            return seg
        self._seq += 1
        self._final_q.put((audio, self._seq))
        self._set_status(STATUS_PROCESSING)
        return seg

    def _request_partial(self, seg: list[bytes]) -> None:
        audio = np.frombuffer(b"".join(seg), dtype=np.int16).astype(np.float32) / 32768.0
        with self._partial_lock:

            self._partial_req = (audio, self._seq)

    def _asr_loop(self) -> None:
        if self._preloaded_model is not None:
            self._model = self._preloaded_model
            self.used_device = self._preloaded_device or "cuda"
            dev = self.used_device
        else:
            if self.compute_device in ("cuda", "auto"):
                prepare_cuda()
            try:
                from faster_whisper import WhisperModel
            except Exception as e:
                self._fail(f"缺少 faster-whisper: {e}")
                return

            if not os.path.isdir(self.model_dir):
                self._fail(f"模型目录不存在: {self.model_dir}")
                return

            model = None
            last_err = None
            for dev, ct in self._device_attempts():
                try:
                    model = WhisperModel(
                        self.model_dir,
                        device=dev,
                        compute_type=ct,
                        cpu_threads=self.cpu_threads,
                        num_workers=1,
                    )
                    self.used_device = dev
                    break
                except Exception as e:
                    last_err = e
            if model is None:
                self._fail(f"模型加载失败: {last_err}")
                return
            self._model = model
            dev = self.used_device

        if dev == "cpu":
            self.partial_interval = max(self.partial_interval, 1.6)
        self._set_status(STATUS_LISTENING)

        while not self._stop.is_set():
            try:
                audio, seq = self._final_q.get_nowait()
            except queue.Empty:
                audio = None
            if audio is not None:
                self._do_final(audio, seq)
                continue

            req = None
            with self._partial_lock:
                if self._partial_req is not None:
                    req = self._partial_req
                    self._partial_req = None
            if req is not None:
                self._do_partial(*req)
                continue

            time.sleep(0.03)

    def _device_attempts(self):
        attempts = []
        if self.compute_device == "cuda" or (
            self.compute_device == "auto" and cuda_available()
        ):
            attempts.append(("cuda", "int8_float32"))
        attempts.append(("cpu", self.compute_type))
        return attempts

    def _prompt(self) -> str:
        tip = ""
        if self.language == "zh":
            tip = "以下是普通话直播的句子，使用简体中文和标点符号。"
        elif self.language == "en":
            tip = "The following is live stream speech in English with punctuation."
        parts = [tip]
        if self._context:
            parts.append(self._context[-80:])
        return " ".join(p for p in parts if p)

    def _apply_corrections(self, text: str) -> str:
        if not text or not self.corrections:
            return text
        for wrong, right in self.corrections.items():
            if wrong in text:
                text = text.replace(wrong, right)
        return text

    def _transcribe(self, audio: np.ndarray, beam: int, fallback: bool = False) -> str:
        if self.asr_backend == "sensevoice":
            return self._model.transcribe(audio)
        lang = None if self.language in ("auto", "", None) else self.language
        segments, _info = self._model.transcribe(
            audio,
            language=lang,
            beam_size=beam,
            best_of=beam,
            temperature=[0.0, 0.2, 0.4] if fallback else 0.0,
            condition_on_previous_text=False,
            initial_prompt=self._prompt(),
            hotwords=self.hotwords or None,
            vad_filter=False,
            without_timestamps=True,
            no_speech_threshold=0.6,
            log_prob_threshold=-1.0,
            compression_ratio_threshold=2.4,
        )
        return "".join(s.text for s in segments)

    def _do_partial(self, audio: np.ndarray, seq: int) -> None:
        t0 = time.time()
        try:
            text = clean_text(self._transcribe(audio, beam=1), self.blocklist, self.number_mode)
            text = self._apply_corrections(text)
            if self.censor_enabled:
                text = censor_text(text, self.censor_words)
        except Exception as e:
            self.on_error(f"识别出错: {e}")
            return
        finally:

            dt = time.time() - t0
            self.partial_interval = max(0.7, min(3.0, dt * 1.8))
        if not text:
            return
        with self._partial_lock:
            stale = seq != self._seq
        if not stale:
            self.on_partial(text)

    def _do_final(self, audio: np.ndarray, seq: int) -> None:
        try:
            text = clean_text(self._transcribe(audio, beam=5, fallback=True), self.blocklist, self.number_mode)
            text = self._apply_corrections(text)
            if self.censor_enabled:
                text = censor_text(text, self.censor_words)
        except Exception as e:
            self.on_error(f"识别出错: {e}")
            return
        if not text:
            self._set_status(STATUS_LISTENING if not self._paused else STATUS_PAUSED)
            return
        if text == self._last_final and len(text) > 4:

            self._set_status(STATUS_LISTENING if not self._paused else STATUS_PAUSED)
            return
        self._last_final = text
        self._context = (self._context + " " + text)[-120:]
        self.on_final(text)
        self._set_status(STATUS_PAUSED if self._paused else STATUS_LISTENING)

    def _set_status(self, s: str) -> None:
        if s != self._status:
            self._status = s
            try:
                self.on_status(s)
            except Exception:
                pass

    def _fail(self, msg: str) -> None:
        self._status = STATUS_ERROR
        self._started = False
        self._stop.set()
        self._set_status(STATUS_ERROR)
        self.on_error(msg)

    def _emit_level(self, rms: float) -> None:
        now = time.time()
        if now - self._level_ts < 0.05:
            return
        self._level_ts = now

        db = 20.0 * math.log10(max(rms, 1e-6))
        v = max(0.0, min(1.0, (db + 60.0) / 55.0))
        self._last_level = v
        try:
            self.on_level(v)
        except Exception:
            pass
