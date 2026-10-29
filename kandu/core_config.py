"""应用数据目录与设置读写（对齐 Android 版 settings.json 结构）。"""
import json
import os
from pathlib import Path


def app_dir() -> Path:
    """数据目录。测试/开发可设 KANDU_DATA_DIR 隔离，避免误动真实数据。"""
    override = os.environ.get("KANDU_DATA_DIR")
    if override:
        d = Path(override)
    else:
        base = os.environ.get("LOCALAPPDATA")
        d = (Path(base) / "KanduDesktop") if base else (Path.home() / ".kandu_desktop")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _sub(name: str) -> Path:
    d = app_dir() / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def asset_path(name: str) -> Path:
    """内置资源：开发时在包目录 assets/，PyInstaller 冻结后在 _MEIPASS。"""
    import sys
    roots = []
    if getattr(sys, "frozen", False):
        roots.append(Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)))
    roots.append(Path(__file__).resolve().parent)
    roots.append(Path(__file__).resolve().parent.parent)
    for r in roots:
        p = r / "assets" / name
        if p.exists():
            return p
    raise FileNotFoundError(name)


DEFAULTS = {
    "vocabLevel": "cet4",
    "levelChosen": False,
    "libraryDir": "",
    "readerFontSize": 13,
    "llms": [],
    "defaultLlm": "",
}


def settings_path() -> Path:
    return app_dir() / "settings.json"


def load_settings() -> dict:
    s = json.loads(json.dumps(DEFAULTS))  # deep copy
    try:
        data = json.loads(settings_path().read_text(encoding="utf-8"))
        for k, v in data.items():
            if k in ("llm", "llms", "defaultLlm"):
                continue  # LLM 配置在下方统一迁移
            s[k] = v
        # 迁移：旧版单配置 llm → 多配置 llms
        legacy = data.get("llm")
        if isinstance(legacy, dict) and legacy.get("baseUrl"):
            s["llms"] = [{"name": "默认", **legacy}]
            s["defaultLlm"] = "默认"
        if isinstance(data.get("llms"), list) and data["llms"]:
            s["llms"] = data["llms"]
            s["defaultLlm"] = data.get("defaultLlm") or data["llms"][0].get("name", "")
    except Exception:
        pass
    return s


def llm_names(settings: dict):
    return [p.get("name", "?") for p in settings.get("llms") or []]


def default_llm_name(settings: dict) -> str:
    names = llm_names(settings)
    dn = settings.get("defaultLlm")
    return dn if dn in names else (names[0] if names else "")


def get_llm(settings: dict, name: str = None) -> dict:
    """取指定名称（缺省取默认）的 LLM 配置；无任何配置时返回空字典。"""
    llms = settings.get("llms") or []
    if name:
        for p in llms:
            if p.get("name") == name:
                return p
    dn = default_llm_name(settings)
    for p in llms:
        if p.get("name") == dn:
            return p
    return llms[0] if llms else {}


def other_llms(settings: dict, exclude_name: str = None):
    dn = default_llm_name(settings)
    out = []
    for p in settings.get("llms") or []:
        if exclude_name and p.get("name") == exclude_name:
            continue
        out.append(p)
    return out


def save_settings(s: dict) -> None:
    settings_path().write_text(
        json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def ensure_wordlists() -> None:
    """首次启动把内置词表拷到内部存储，便于日后更新。"""
    targets = {"cet4": "cet4_words.txt"}
    for _level, fname in targets.items():
        dst = _sub("vocab") / fname
        if dst.exists():
            continue
        try:
            dst.write_bytes(asset_path(fname).read_bytes())
        except FileNotFoundError:
            pass


def wordset(level: str) -> set:
    fname = {"cet4": "cet4_words.txt"}.get(level)
    if not fname:
        return set()
    f = _sub("vocab") / fname
    if not f.exists():
        return set()
    out = set()
    for line in f.read_text(encoding="utf-8").splitlines():
        w = line.split("\t", 1)[0].strip().lower()
        if w:
            out.add(w)
    return out


def word_count(level: str) -> int:
    return len(wordset(level))
