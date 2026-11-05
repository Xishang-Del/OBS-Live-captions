from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MODELS_DIR = ROOT / "models"

MODEL_REPOS = {
    "large-v3-turbo": (
        ["deepdml/faster-whisper-large-v3-turbo-ct2",
         "mobiuslabsgmbh/faster-whisper-large-v3-turbo"],
        "pengzhendong/faster-whisper-large-v3-turbo",
    ),
    "large-v3": (
        ["Systran/faster-whisper-large-v3"],
        "keepitsimple/faster-whisper-large-v3",
    ),
    "medium": (
        ["Systran/faster-whisper-medium"],
        "keepitsimple/faster-whisper-medium",
    ),
    "small": (
        ["Systran/faster-whisper-small"],
        "keepitsimple/faster-whisper-small",
    ),
}

ENDPOINTS = [
    ("HuggingFace", "https://huggingface.co"),
    ("hf-mirror", "https://hf-mirror.com"),
]

REQUIRED = ["config.json", "model.bin", "tokenizer.json", "preprocessor_config.json"]

SENSEVOICE_REPO = "csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"
SENSEVOICE_FILES = ["model.int8.onnx", "tokens.txt"]
SENSEVOICE_ENDPOINTS = ["https://hf-mirror.com", "https://huggingface.co"]

def ensure_sensevoice(quiet: bool = False) -> Path:
    target = MODELS_DIR / "sensevoice"
    ok = (target / "model.int8.onnx").is_file() and (target / "tokens.txt").is_file()
    if ok and (target / "model.int8.onnx").stat().st_size > 100 * 1024 * 1024:
        return target
    target.mkdir(parents=True, exist_ok=True)

    import requests

    last_err = None
    for endpoint in SENSEVOICE_ENDPOINTS:
        try:
            for fname in SENSEVOICE_FILES:
                url = f"{endpoint}/{SENSEVOICE_REPO}/resolve/main/{fname}"
                dest = target / fname
                tmp = dest.with_suffix(dest.suffix + ".part")
                if not quiet:
                    log(f"下载 {fname} ...")
                with requests.get(url, stream=True, timeout=60) as r:
                    r.raise_for_status()
                    total = int(r.headers.get("Content-Length") or 0)
                    done = 0
                    with open(tmp, "wb") as f:
                        for chunk in r.iter_content(chunk_size=1024 * 1024):
                            if chunk:
                                f.write(chunk)
                                done += len(chunk)
                                if not quiet and total and done % (20 * 1024 * 1024) < 1024 * 1024:
                                    log(f"  {done * 100 // total}%")
                tmp.replace(dest)
            if (target / "model.int8.onnx").stat().st_size > 100 * 1024 * 1024:
                return target
        except Exception as e:
            last_err = e
            if not quiet:
                log(f"  {endpoint} 失败: {type(e).__name__}: {e}")

    print("\nSenseVoice 模型下载失败，请手动下载：")
    print(f"  https://hf-mirror.com/{SENSEVOICE_REPO}/tree/main")
    print(f"  把 model.int8.onnx 和 tokens.txt 放进 {target}")
    print(f"  最后一次错误: {last_err}")
    raise RuntimeError("SenseVoice 模型下载失败")

def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def has_files(path: Path) -> bool:
    if not path.is_dir():
        return False
    for name in REQUIRED:
        p = path / name
        if not p.is_file() or p.stat().st_size <= 0:
            return False

    if (path / "model.bin").stat().st_size < 20 * 1024 * 1024:
        return False

    return (path / "vocabulary.txt").is_file() or (path / "vocabulary.json").is_file()

def dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())

def _download_hf(repo: str, target: Path, endpoint: str, name: str) -> bool:
    os.environ["HF_ENDPOINT"] = endpoint
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        log("未安装 huggingface_hub，尝试 pip 安装 ...")
        os.system(f'"{sys.executable}" -m pip install -q huggingface_hub')
        from huggingface_hub import snapshot_download

    log(f"尝试从 {name} 下载 {repo} ...")
    tmp = target.with_name(target.name + ".downloading")
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    try:
        snapshot_download(
            repo_id=repo,
            local_dir=str(tmp),
            endpoint=endpoint,
            allow_patterns=["*.json", "*.bin", "*.txt", "*.model"],
            max_workers=4,
        )
    except Exception as e:
        log(f"  {name} 下载失败: {type(e).__name__}: {str(e)[:300]}")
        return False

    if not has_files(tmp):
        log(f"  {name} 下载内容不完整，丢弃")
        return False
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    tmp.rename(target)
    log(f"  完成: {target} ({dir_size(target)/1024/1024:.0f} MB)")
    return True

def _download_modelscope(repo: str, target: Path) -> bool:
    try:
        from modelscope.hub.snapshot_download import snapshot_download as ms_download
    except ImportError:
        log("未安装 modelscope 包，跳过 ModelScope 源")
        return False
    log(f"尝试从 ModelScope 下载 {repo} ...")
    tmp = target.with_name(target.name + ".downloading")
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    try:
        ms_download(repo, local_dir=str(tmp))
    except Exception as e:
        log(f"  ModelScope 下载失败: {type(e).__name__}: {str(e)[:300]}")
        return False
    if not has_files(tmp):
        log("  ModelScope 下载内容不完整，丢弃")
        return False
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    tmp.rename(target)
    log(f"  完成: {target} ({dir_size(target)/1024/1024:.0f} MB)")
    return True

def ensure_model(model: str = "large-v3-turbo", quiet: bool = False) -> Path:
    if model not in MODEL_REPOS:
        raise ValueError(f"未知模型 {model}，可选: {', '.join(MODEL_REPOS)}")
    target = MODELS_DIR / model
    if has_files(target):
        return target

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    hf_repos, ms_repo = MODEL_REPOS[model]
    target.mkdir(parents=True, exist_ok=True)

    for repo in hf_repos:
        for name, endpoint in ENDPOINTS:
            if _download_hf(repo, target, endpoint, name):
                return target
    if ms_repo and _download_modelscope(ms_repo, target):
        return target

    print("\n" + "=" * 60)
    print("自动下载失败，请手动下载（任选一种）:")
    print(f"  1) HuggingFace: https://huggingface.co/{hf_repos[0]}/tree/main")
    print(f"  2) 镜像:        https://hf-mirror.com/{hf_repos[0]}/tree/main")
    print(f"  3) ModelScope:  https://www.modelscope.cn/models/{ms_repo}/files")
    print(f"  把 4 个文件(config.json / model.bin / tokenizer.json /")
    print(f"  preprocessor_config.json, 外加 vocabulary.txt) 放到:")
    print(f"  {target}")
    print("=" * 60)
    raise RuntimeError("模型下载失败")

def main() -> None:
    ap = argparse.ArgumentParser(description="下载实时字幕所需的语音模型")
    ap.add_argument("model", nargs="?", default="sensevoice")
    ap.add_argument("--list", action="store_true", help="列出可用模型")
    args = ap.parse_args()
    if args.list:
        print("sensevoice        SenseVoice 中文专精（推荐，约 230MB）")
        for k, (hf, ms) in MODEL_REPOS.items():
            print(f"{k:16s} HF: {hf[0]}")
        return
    if args.model.lower() == "sensevoice":
        p = ensure_sensevoice()
        print(f"模型就绪: {p}")
        return
    p = ensure_model(args.model)
    print(f"模型就绪: {p}")

if __name__ == "__main__":
    main()
