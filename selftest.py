from __future__ import annotations

import os
import sys
import threading
import time
import wave
from pathlib import Path

import numpy as np

MODEL = os.environ.get("SELFTEST_MODEL", "large-v3")
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import engine

SR = engine.SR

def read_wav_mono16k(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        sr = w.getframerate()
        ch = w.getnchannels()
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        data = data.reshape(-1, ch).mean(axis=1)
    if sr != SR:
        data = engine.resample_to_16k(data, sr)
    return data

def build_stream() -> np.ndarray:
    p = ROOT / "demo.wav"
    if not p.is_file():
        raise SystemExit("找不到 demo.wav 测试音频")
    return read_wav_mono16k(p)

class TestASR(engine.RealtimeASR):

    def __init__(self, *a, wav: np.ndarray, **kw):
        super().__init__(*a, **kw)
        self.wav = wav
        self.t0 = 0.0

    def _open_capture(self) -> None:
        self.t0 = time.time()
        threading.Thread(target=self._feed, daemon=True).start()

    def _feed(self) -> None:
        step = int(0.1 * SR)
        for i in range(0, len(self.wav), step):
            if self._stop.is_set():
                return
            target = self.t0 + i / SR
            now = time.time()
            if target > now:
                time.sleep(target - now)
            self._aq.put((self.wav[i : i + step].copy(), SR))
        time.sleep(3.0)

def main() -> None:
    wav = read_wav_mono16k(Path(sys.argv[1])) if len(sys.argv) > 1 and sys.argv[1].endswith(".wav") else build_stream()
    print(f"测试音频 {len(wav)/SR:.1f}s")

    t0 = time.time()
    results = []

    def on_partial(t: str) -> None:
        print(f"  [{time.time()-t0:5.2f}s] 临时: {t}")

    def on_final(t: str) -> None:
        results.append(t)
        print(f"  [{time.time()-t0:5.2f}s] 最终: {t}")

    def on_status(s: str) -> None:
        print(f"  [{time.time()-t0:5.2f}s] 状态: {s}")

    asr = TestASR(
        model_dir=str(ROOT / "models" / MODEL),
        device=1,
        language="zh",
        compute_device="auto",
        wav=wav,
        on_partial=on_partial,
        on_final=on_final,
        on_status=on_status,
        on_error=lambda m: print("ERROR:", m),
    )
    asr.start()
    print("加载中，设备:", asr.used_device)
    deadline = time.time() + len(wav) / SR + 12
    while time.time() < deadline:
        time.sleep(0.2)
        if asr.status == "error":
            break
    asr.stop()
    print("实际使用设备:", asr.used_device)

    print("\n最终结果:")
    for r in results:
        print("  -", r)
    if not results:
        print("  (没有识别结果！)")

if __name__ == "__main__":
    main()
