from __future__ import annotations

import json
import copy
import os
import hashlib
import re
import shutil
import subprocess
import uuid
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

BASE = Path(__file__).resolve().parent
PROJECTS = Path(os.getenv("PROJECTS_DIR", str(BASE / "projects"))).expanduser().resolve()
STATIC = BASE / "static"
PROJECTS.mkdir(parents=True, exist_ok=True)
MAX_UPLOAD_MB = max(10, int(os.getenv("MAX_UPLOAD_MB", "500")))
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")

GEMINI_API_KEY = (os.getenv("GEMINI_API_KEY", "") or os.getenv("GOOGLE_API_KEY", "")).strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash"
GEMINI_TRANSCRIBE_MODEL = os.getenv("GEMINI_TRANSCRIBE_MODEL", "gemini-3.5-transcribe").strip() or "gemini-3.5-transcribe"
GEMINI_BASE_URL = os.getenv("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta").rstrip("/")

FONT_CANDIDATES = [
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"),
]
FONT_PATH = next((p for p in FONT_CANDIDATES if p.exists()), None)
SCRIPT_FONTS = {
    "arabic": Path("/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf"),
    "devanagari": Path("/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf"),
    "cjk": Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
}
SUPPORTED_LOCALES = {
    "en": "English", "es": "Español", "pt": "Português", "fr": "Français",
    "ar": "العربية", "de": "Deutsch", "hi": "हिन्दी", "id": "Bahasa Indonesia",
    "tr": "Türkçe", "ja": "日本語", "ko": "한국어", "zh": "中文",
    "it": "Italiano", "ru": "Русский",
}

SEO_META = {
    "en": ("Chat Video Editor — Edit videos by chatting", "Edit videos by chatting. Trim, resize, caption, add text, logos, music and more with natural-language commands."),
    "es": ("Editor de video por chat — Edita videos hablando", "Edita videos con lenguaje natural: corta, redimensiona, añade subtítulos, texto, logos y música."),
    "pt": ("Editor de vídeo por chat — Edite vídeos conversando", "Edite vídeos com linguagem natural: corte, redimensione, adicione legendas, texto, logos e música."),
    "fr": ("Éditeur vidéo par chat — Modifiez vos vidéos en parlant", "Modifiez des vidéos en langage naturel : coupez, redimensionnez, ajoutez sous-titres, texte, logos et musique."),
    "ar": ("محرر فيديو بالمحادثة — عدّل الفيديو بالكلام", "عدّل الفيديو بأوامر طبيعية: قص، تغيير المقاس، ترجمة، نصوص، شعارات وموسيقى."),
    "de": ("Video-Editor per Chat — Videos mit Textbefehlen bearbeiten", "Videos mit natürlicher Sprache bearbeiten: schneiden, skalieren, Untertitel, Text, Logos und Musik hinzufügen."),
    "hi": ("चैट वीडियो एडिटर — लिखकर वीडियो एडिट करें", "प्राकृतिक भाषा से वीडियो काटें, आकार बदलें, सबटाइटल, टेक्स्ट, लोगो और संगीत जोड़ें।"),
    "id": ("Editor Video lewat Chat — Edit video dengan perintah teks", "Edit video dengan bahasa alami: potong, ubah ukuran, tambahkan subtitle, teks, logo, dan musik."),
    "tr": ("Sohbet Video Editörü — Yazarak video düzenleyin", "Doğal dille video düzenleyin: kesin, yeniden boyutlandırın, altyazı, metin, logo ve müzik ekleyin."),
    "ja": ("チャット動画編集 — 文字で動画を編集", "自然言語で動画をトリミング、サイズ変更、字幕、テキスト、ロゴ、音楽追加。"),
    "ko": ("채팅 비디오 편집기 — 말하듯 영상 편집", "자연어로 영상 자르기, 크기 변경, 자막, 텍스트, 로고, 음악 추가."),
    "zh": ("聊天视频编辑器 — 用文字直接剪视频", "用自然语言编辑视频：裁剪、改比例、自动字幕、文字、Logo、音乐等。"),
    "it": ("Editor video via chat — Modifica video scrivendo", "Modifica video con linguaggio naturale: taglia, ridimensiona, aggiungi sottotitoli, testo, logo e musica."),
    "ru": ("Видеоредактор через чат — Редактируйте видео командами", "Редактируйте видео естественным языком: обрезка, формат, субтитры, текст, логотип и музыка."),
}

def font_for_text(text: str) -> Path | None:
    # Use script-aware Noto fonts when available so overlays work beyond Latin/Arabic.
    for ch in str(text):
        cp = ord(ch)
        if 0x0600 <= cp <= 0x06FF or 0x0750 <= cp <= 0x077F or 0x08A0 <= cp <= 0x08FF:
            f = SCRIPT_FONTS["arabic"]
            return f if f.exists() else FONT_PATH
        if 0x0900 <= cp <= 0x097F:
            f = SCRIPT_FONTS["devanagari"]
            return f if f.exists() else FONT_PATH
        if (0x3040 <= cp <= 0x30FF) or (0x3400 <= cp <= 0x9FFF) or (0xAC00 <= cp <= 0xD7AF):
            f = SCRIPT_FONTS["cjk"]
            return f if f.exists() else FONT_PATH
    return FONT_PATH

def ass_font_name_for_text(text: str) -> str:
    for ch in str(text):
        cp = ord(ch)
        if 0x0600 <= cp <= 0x06FF or 0x0750 <= cp <= 0x077F or 0x08A0 <= cp <= 0x08FF:
            return "Noto Sans Arabic"
        if 0x0900 <= cp <= 0x097F:
            return "Noto Sans Devanagari"
        if (0x3040 <= cp <= 0x30FF) or (0x3400 <= cp <= 0x9FFF) or (0xAC00 <= cp <= 0xD7AF):
            return "Noto Sans CJK SC"
    return "DejaVu Sans"

app = FastAPI(title="EditByChat", version="1.0-alpha")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


class EditRequest(BaseModel):
    project_id: str
    command: str


class ProjectRequest(BaseModel):
    project_id: str


class RenameProjectRequest(BaseModel):
    name: str


class ExportRequest(BaseModel):
    project_id: str
    resolution: str = "source"  # source | 720p | 1080p
    fps: str = "source"         # source | 30 | 60
    quality: str = "balanced"   # draft | balanced | high


def run(cmd: list[str]) -> None:
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr[-5000:])




def _friendly_media_error(value: object) -> str:
    text = str(value or "")
    low = text.lower()
    if "no space left on device" in low or "disk quota exceeded" in low:
        return "Storage is full. Old temporary renders were cleaned up; please try the edit again."
    if "conversion failed" in low:
        return "The video render failed. Please try again; if it repeats, use a shorter/smaller video or remove the last layer."
    if "invalid data found" in low or "moov atom not found" in low:
        return "This media file could not be read. Try exporting it as a standard MP4 (H.264/AAC) and upload it again."
    if "permission denied" in low:
        return "The server could not write the rendered video. Please try again."
    return "The video render failed. Please try the edit again."


def cleanup_project_storage(project_dir: Path, meta: dict | None = None, keep_exports: int = 1) -> int:
    """Remove obsolete previews/failed temp renders while preserving source, current preview, assets and caches."""
    try:
        if meta is None:
            mp = project_dir / "project.json"
            if not mp.exists():
                return 0
            meta = json.loads(mp.read_text(encoding="utf-8"))
        keep = {"project.json", str(meta.get("base_render", "")), str(meta.get("current", ""))}
        for a in meta.get("assets", []):
            if a.get("stored"):
                keep.add(str(a["stored"]))
        exports = sorted(project_dir.glob("export_*.mp4"), key=lambda x: x.stat().st_mtime if x.exists() else 0, reverse=True)
        keep.update(x.name for x in exports[:max(0, keep_exports)])
        removed = 0
        for f in project_dir.iterdir():
            if not f.is_file() or f.name in keep:
                continue
            if (f.name.startswith(("edit_", "layer_tmp_", "splice_", "export_", "original"))
                    and f.suffix.lower() in {".mp4", ".mov", ".m4v", ".webm", ".mkv"}):
                try:
                    removed += f.stat().st_size
                    f.unlink(missing_ok=True)
                except OSError:
                    pass
        return removed
    except Exception:
        return 0


def cleanup_all_projects() -> int:
    total = 0
    try:
        for d in PROJECTS.iterdir():
            if d.is_dir():
                total += cleanup_project_storage(d)
    except Exception:
        pass
    return total

def probe_duration(path: Path) -> float:
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        return round(float(p.stdout.strip()), 2)
    except Exception:
        return 0.0


def probe_has_audio(path: Path) -> bool:
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return bool(p.stdout.strip())


def meta_path(project_dir: Path) -> Path:
    return project_dir / "project.json"


def load_meta(project_dir: Path) -> dict:
    try:
        return json.loads(meta_path(project_dir).read_text())
    except Exception:
        raise HTTPException(404, "Project not found")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def save_meta(project_dir: Path, meta: dict) -> None:
    meta["updated_at"] = utc_now()
    meta.setdefault("created_at", meta["updated_at"])
    meta_path(project_dir).write_text(json.dumps(meta, indent=2, ensure_ascii=False))


def current_path(project_dir: Path, meta: dict) -> Path:
    return project_dir / meta["current"]


def next_output(project_dir: Path, meta: dict) -> Path:
    idx = len(meta.get("history", []))
    return project_dir / f"edit_{idx:03d}.mp4"



LAYER_LABELS = {
    "trim_start": "Cut", "remove_segment": "Cut", "aspect": "Format",
    "volume": "Volume", "speed": "Speed", "text_overlay": "Text",
    "captions": "Captions", "image_overlay": "Logo", "music": "Music",
    "zoom_segment": "Zoom", "fade": "Fade", "insert_clip": "Clip",
    "replace_segment": "Clip",
}


def ensure_layer_state(meta: dict) -> None:
    """Initialize the v0.8 non-destructive logical layer state."""
    meta.setdefault("base_render", (meta.get("history") or [meta.get("current")])[0])
    meta.setdefault("layers", [])
    meta.setdefault("layer_snapshots", [])
    meta.setdefault("last_layer_id", None)
    meta.setdefault("layer_counters", {})
    if int(meta.get("schema_version", 2)) < 3:
        # Best-effort migration for old MVP projects. We only bootstrap direct edits;
        # revisions remain represented by their latest old render and future edits use layers.
        # New v0.8 projects are native schema 3 from upload time.
        meta["schema_version"] = 3


def _layer_base_name(edit_type: str) -> str:
    return LAYER_LABELS.get(edit_type, "Edit")


def _new_layer(meta: dict, edit: dict, command: str = "") -> dict:
    base = _layer_base_name(edit.get("type", ""))
    counters = meta.setdefault("layer_counters", {})
    counters[base] = int(counters.get(base, 0)) + 1
    n = counters[base]
    return {
        "id": f"{base.lower()}_{n}_{uuid.uuid4().hex[:6]}",
        "label": f"{base} {n}",
        "type": edit.get("type"),
        "edit": copy.deepcopy(edit),
        "active": True,
        "order": len(meta.get("layers", [])),
        "created_by": command,
    }


def _public_layer(layer: dict) -> dict:
    edit = copy.deepcopy(layer.get("edit", {}))
    edit.pop("asset_path", None)
    return {
        "id": layer.get("id"), "label": layer.get("label"), "type": layer.get("type"),
        "active": bool(layer.get("active", True)), "order": layer.get("order", 0), "edit": edit,
    }


def push_layer_snapshot(meta: dict) -> None:
    snaps = meta.setdefault("layer_snapshots", [])
    snaps.append(copy.deepcopy(meta.get("layers", [])))
    # Keep the MVP metadata bounded.
    if len(snaps) > 60:
        del snaps[:-60]


def _layer_by_id(meta: dict, layer_id: str | None) -> Optional[dict]:
    if not layer_id:
        return None
    return next((x for x in meta.get("layers", []) if x.get("id") == layer_id), None)


def _matching_layers(meta: dict, family: str, active: Optional[bool] = None) -> list[dict]:
    families = {
        "text": {"text_overlay"}, "captions": {"captions"}, "logo": {"image_overlay"},
        "music": {"music"}, "zoom": {"zoom_segment"}, "clip": {"insert_clip", "replace_segment"},
        "fade": {"fade"}, "cut": {"trim_start", "remove_segment"}, "format": {"aspect"},
        "volume": {"volume"}, "speed": {"speed"},
    }
    types = families.get(family, {family})
    out = [x for x in meta.get("layers", []) if x.get("type") in types]
    if active is not None:
        out = [x for x in out if bool(x.get("active", True)) is active]
    return sorted(out, key=lambda x: x.get("order", 0))


def _ordinal_index(text: str) -> Optional[int]:
    c = text.lower()
    word_map = {
        1: ["first", "1st", "الأول", "الاول", "الأولى", "الاولى", "primero", "primera", "premier", "première", "erste", "ersten", "primeiro", "primeira", "primo", "prima", "перв", "ilk", "pertama", "पहला", "पहली", "最初", "첫", "第一"],
        2: ["second", "2nd", "الثاني", "الثانية", "segundo", "segunda", "deuxième", "second", "zweite", "segundo", "secondo", "втор", "ikinci", "kedua", "दूसरा", "दूसरी", "2番", "두 번째", "第二"],
        3: ["third", "3rd", "الثالث", "الثالثة", "tercero", "terceira", "troisième", "dritte", "terceiro", "terzo", "трет", "üçüncü", "ketiga", "तीसरा", "3番", "세 번째", "第三"],
    }
    for idx, words in word_map.items():
        if any(w in c for w in words):
            return idx - 1
    m = re.search(r"(?:text|logo|music|zoom|clip|caption|layer|النص|اللوغو|الموسيقى|الزوم|الكابشن)\s*#?\s*(\d+)", c)
    if m:
        return max(0, int(m.group(1)) - 1)
    return None


def _layer_family_from_command(command: str) -> Optional[str]:
    c = command.lower()
    groups = [
        ("captions", ["captions", "caption layer", "subtitles", "subtítulos", "legendas", "sous-titres", "untertitel", "كابشن", "الترجمة", "字幕", "자막", "субтит"]),
        ("logo", ["logo", "image layer", "اللوغو", "الشعار", "imagen", "imagem", "bild", "immagine", "ロゴ", "로고", "徽标", "логотип"]),
        ("music", ["music", "background track", "الموسيقى", "موسيقى", "música", "musique", "musik", "musica", "音楽", "음악", "音乐", "музык"]),
        ("zoom", ["zoom", "الزوم", "زووم", "ズーム", "줌", "缩放", "зум"]),
        ("text", ["text", "title", "النص", "الكتابة", "texto", "texte", "testo", "текст", "テキスト", "텍스트", "文字"]),
        ("clip", ["clip", "video clip", "الفيديو", "الكليب", "vídeo", "vidéo", "videoclip", "クリップ", "클립", "视频片段", "клип"]),
        ("fade", ["fade", "تلاشي", "fundido", "fondu", "überblend", "dissolvenza", "затух"]),
        ("format", ["format layer", "aspect", "ratio", "المقاس", "النسبة"]),
        ("speed", ["speed layer", "السرعة"]),
        ("volume", ["volume layer", "الصوت"]),
    ]
    for family, words in groups:
        if any(w in c for w in words):
            return family
    return None


def _is_disable_layer_command(command: str) -> bool:
    c = command.lower()
    return any(x in c for x in [
        "remove", "delete", "hide", "disable", "turn off", "حيد", "مسح", "حذف", "عطل",
        "supprime", "retire", "elimina", "quita", "remova", "entferne", "lösche", "rimuovi",
        "удал", "убери", "kaldır", "hapus", "हटा", "削除", "消して", "삭제", "删除",
    ])


def _is_restore_layer_command(command: str) -> bool:
    c = command.lower()
    return any(x in c for x in [
        "restore", "bring back", "enable", "turn back on", "رجع", "رجعو", "استرجع",
        "restaura", "restaurer", "rétabl", "wiederher", "ripristina", "восстанов", "geri getir",
        "kembalikan", "वापस", "戻して", "복원", "恢复",
    ])


def _pick_layer(meta: dict, family: str, command: str, for_restore: bool = False) -> Optional[dict]:
    matches = _matching_layers(meta, family, active=False if for_restore else True)
    if not matches:
        return None
    idx = _ordinal_index(command)
    if idx is not None and idx < len(matches):
        return matches[idx]
    return matches[-1]


def _context_revision_for_layer(command: str, meta: dict, layer: dict, duration: float) -> Optional[dict]:
    pseudo = {
        "commands": [{"edits": [copy.deepcopy(layer.get("edit", {}))], "base": meta.get("base_render")}],
        "assets": meta.get("assets", []),
    }
    rev = parse_context_revision(command, pseudo, duration)
    if rev and rev.get("edits"):
        return rev["edits"][-1]
    if GEMINI_API_KEY:
        rev = parse_context_with_gemini(command, pseudo, duration)
        if rev and rev.get("edits"):
            return rev["edits"][-1]
    return None


def parse_layer_reference_command(command: str, meta: dict, duration: float) -> Optional[dict]:
    """Resolve commands that refer to a specific existing project layer."""
    family = _layer_family_from_command(command)
    if not family:
        return None
    if _is_restore_layer_command(command):
        layer = _pick_layer(meta, family, command, for_restore=True)
        if not layer:
            return None
        return {"action": "enable", "layer_id": layer["id"], "summary": f"Restored {layer['label']}."}
    if _is_disable_layer_command(command):
        layer = _pick_layer(meta, family, command, for_restore=False)
        if not layer:
            return None
        return {"action": "disable", "layer_id": layer["id"], "summary": f"Removed {layer['label']} from the render."}

    layer = _pick_layer(meta, family, command, for_restore=False)
    if not layer:
        return None

    # Asset swaps for named layers.
    c = command.lower()
    if family == "logo" and any(x in c for x in ["replace", "swap", "بدل", "change logo", "new logo", "هاد اللوغو", "this logo"]):
        latest = _latest_asset_id(meta, "image")
        if latest:
            edit = copy.deepcopy(layer["edit"]); edit["asset_id"] = latest
            return {"action": "update", "layer_id": layer["id"], "edit": edit, "summary": f"Replaced the asset used by {layer['label']}."}
    if family == "music" and any(x in c for x in ["replace", "swap", "بدل", "change music", "new music", "هاد الموسيقى", "this music"]):
        latest = _latest_asset_id(meta, "audio")
        if latest:
            edit = copy.deepcopy(layer["edit"]); edit["asset_id"] = latest
            return {"action": "update", "layer_id": layer["id"], "edit": edit, "summary": f"Replaced the track used by {layer['label']}."}

    revised = _context_revision_for_layer(command, meta, layer, duration)
    if revised:
        return {"action": "update", "layer_id": layer["id"], "edit": revised, "summary": f"Updated {layer['label']}."}
    return None


def parse_layer_reference_with_gemini(command: str, meta: dict, duration: float) -> Optional[dict]:
    if not GEMINI_API_KEY or not meta.get("layers"):
        return None
    layers = [_public_layer(x) for x in meta.get("layers", [])]
    instructions = f"""You resolve references to existing layers in a chat-first video editor.
Current project duration: {duration:.2f}s.
Layers: {json.dumps(layers, ensure_ascii=False)}
Understand English first, plus Spanish, Portuguese, French, Arabic/Moroccan Darija, German, Hindi, Indonesian, Turkish, Japanese, Korean, Chinese, Italian and Russian.
Return JSON only. If the user refers to an EXISTING layer, return one of:
{{"action":"disable","layer_id":"...","summary":"..."}}
{{"action":"enable","layer_id":"...","summary":"..."}}
{{"action":"update","layer_id":"...","edit":{{complete revised edit object}},"summary":"..."}}
If the user is asking for a NEW edit rather than referring to an existing layer, return {{"action":"none"}}.
Never change edit type. Preserve fields the user did not ask to change. Reply summary in the user's language."""
    try:
        obj = gemini_json(instructions, command, 600)
        if obj.get("action") not in {"disable", "enable", "update"}:
            return None
        layer = _layer_by_id(meta, obj.get("layer_id"))
        if not layer:
            return None
        if obj["action"] == "update":
            candidate = obj.get("edit") or {}
            if candidate.get("type") != layer.get("type"):
                return None
            normalized = normalize_ai_plan({"edits": [candidate], "summary": ""}, duration).get("edits", [])
            if not normalized:
                return None
            obj["edit"] = normalized[0]
        return obj
    except Exception as exc:
        print("Layer Gemini resolver failed:", repr(exc))
        return None


JOBS: dict[str, dict] = {}
JOB_LOCK = threading.Lock()
JOB_CONTEXT = threading.local()
PROJECT_LOCKS: dict[str, threading.Lock] = {}
PROJECT_LOCKS_GUARD = threading.Lock()


def _project_lock(project_id: str) -> threading.Lock:
    with PROJECT_LOCKS_GUARD:
        return PROJECT_LOCKS.setdefault(project_id, threading.Lock())


def update_job_progress(percent: int, stage: str) -> None:
    job_id = getattr(JOB_CONTEXT, "job_id", None)
    if not job_id:
        return
    with JOB_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return
        job["progress"] = max(0, min(100, int(percent)))
        job["stage"] = stage
        job["updated_at"] = utc_now()


def render_layer_stack(project_dir: Path, meta: dict, dst: Path) -> None:
    ensure_layer_state(meta)
    base = project_dir / meta["base_render"]
    if not base.exists():
        raise RuntimeError("Base render is missing")
    active = [x for x in sorted(meta.get("layers", []), key=lambda z: z.get("order", 0)) if x.get("active", True)]
    if not active:
        update_job_progress(82, "Preparing preview")
        shutil.copy2(base, dst)
        return
    current = base
    temps: list[Path] = []
    try:
        for i, layer in enumerate(active):
            pct = 30 + int((i / max(1, len(active))) * 58)
            update_job_progress(pct, f"Rendering {layer.get('label', 'edit')}")
            edit = copy.deepcopy(layer.get("edit", {}))
            resolve_asset_for_edit(project_dir, meta, edit)
            is_last = i == len(active) - 1
            out = dst if is_last else project_dir / f"layer_tmp_{uuid.uuid4().hex[:8]}.mp4"
            apply_edit(current, out, edit, project_dir)
            update_job_progress(30 + int(((i + 1) / max(1, len(active))) * 58), f"Rendered {layer.get('label', 'edit')}")
            if current != base and current.name.startswith("layer_tmp_"):
                temps.append(current)
            current = out
    finally:
        for tmp in temps:
            tmp.unlink(missing_ok=True)


def rerender_layers(project_dir: Path, meta: dict) -> Path:
    # Free stale previews before a new render; important on small persistent volumes.
    cleanup_project_storage(project_dir, meta)
    dst = next_output(project_dir, meta)
    render_layer_stack(project_dir, meta, dst)
    meta["current"] = dst.name
    meta.setdefault("history", []).append(dst.name)
    cleanup_project_storage(project_dir, meta)
    return dst

def parse_number(text: str) -> Optional[float]:
    m = re.search(r"(\d+(?:\.\d+)?)", text)
    return float(m.group(1)) if m else None


def _summary_for(edit: dict) -> str:
    kind = edit.get("type")
    if kind == "trim_start":
        return f"Removed the first {edit['seconds']:g} seconds."
    if kind == "remove_segment":
        return f"Removed {edit['start']:g}s–{edit['end']:g}s."
    if kind == "aspect":
        return f"Changed framing to {edit['ratio']}."
    if kind == "volume":
        pct = round(float(edit["factor"]) * 100)
        return "Muted the video." if pct == 0 else f"Set volume to {pct}%."
    if kind == "speed":
        return f"Changed speed to {edit['speed']:g}×."
    if kind == "text_overlay":
        return f"Added “{edit['text']}” to the video."
    if kind == "captions":
        return "Added automatic captions."
    if kind == "image_overlay":
        return f"Added the latest image/logo at {edit.get('position', 'top-right')}."
    if kind == "music":
        pct = round(float(edit.get("factor", 0.2)) * 100)
        return f"Added the latest music track at {pct}% volume."
    if kind == "zoom_segment":
        return f"Applied {edit.get('zoom', 1.25):g}× zoom from {edit.get('start', 0):g}s to {edit.get('end', 0):g}s ({edit.get('focus', 'center')})."
    if kind == "fade":
        return f"Added a {edit.get('direction', 'in')} fade ({edit.get('duration', 0.6):g}s)."
    if kind == "insert_clip":
        extra = " with fade" if edit.get("transition") == "fade" else ""
        return f"Inserted the latest video clip at {edit.get('at', 0):g}s{extra}."
    if kind == "replace_segment":
        extra = " with fade" if edit.get("transition") == "fade" else ""
        return f"Replaced {edit.get('start', 0):g}s–{edit.get('end', 0):g}s with the latest clip{extra}."
    return "Applied edit."




def is_undo_command(command: str) -> bool:
    c = command.strip().lower()
    phrases = [
        "undo", "undo that", "undo last", "revert", "revert that", "go back",
        "رجع", "رجعو", "رجعها", "رجع آخر تعديل", "رجع اخر تعديل", "لا رجعو كيف كان",
        "rje3", "rj3", "rj3ha", "rje3ha", "rj3 kif kan",
    ]
    return any(p in c for p in phrases)


def _last_command(meta: dict):
    commands = meta.get("commands", [])
    for i in range(len(commands) - 1, -1, -1):
        item = commands[i]
        if item.get("edits"):
            return i, item
    return None, None


def _latest_asset_id(meta: dict, kind: str) -> Optional[str]:
    for asset in reversed(meta.get("assets", [])):
        if asset.get("kind") == kind:
            return asset.get("id")
    return None


def _horizontal_position(pos: str, side: str) -> str:
    if side == "left":
        return "bottom-left" if pos.startswith("bottom") else "top-left"
    return "bottom-right" if pos.startswith("bottom") else "top-right"


def _vertical_position(pos: str, side: str) -> str:
    if side == "top":
        return "top-left" if pos.endswith("left") else "top-right"
    return "bottom-left" if pos.endswith("left") else "bottom-right"


def parse_context_revision(command: str, meta: dict, base_duration: float) -> Optional[dict]:
    """Revise the latest logical edit instead of stacking a duplicate on top of it."""
    idx, last = _last_command(meta)
    if last is None:
        return None
    edits = copy.deepcopy(last.get("edits", []))
    if not edits:
        return None
    c = command.strip().lower()
    changed = False
    note = "Updated the previous edit."

    edit = edits[-1]
    kind = edit.get("type")
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", c)]

    if kind == "zoom_segment":
        if any(x in c for x in ["زيد شوية", "زيد فالzoom", "زيد فالزووم", "كبر شوية", "more zoom", "zoom more", "increase zoom", "closer"]):
            edit["zoom"] = min(2.0, round(float(edit.get("zoom", 1.25)) + (nums[0] if nums and nums[0] < 1 else 0.1), 2))
            changed = True
        elif any(x in c for x in ["نقص شوية", "نقص فالzoom", "بعد شوية", "less zoom", "zoom less", "decrease zoom", "zoom out a bit"]):
            edit["zoom"] = max(1.05, round(float(edit.get("zoom", 1.25)) - (nums[0] if nums and nums[0] < 1 else 0.1), 2))
            changed = True
        focus = None
        if any(x in c for x in ["لليسار", "left", "lisar"]): focus = "left"
        elif any(x in c for x in ["لليمين", "right", "limn"]): focus = "right"
        elif any(x in c for x in ["لفوق", "فوق", "top", "fo9"]): focus = "top"
        elif any(x in c for x in ["لتحت", "تحت", "bottom", "ta7t"]): focus = "bottom"
        elif any(x in c for x in ["للوسط", "وسط", "center", "middle"]): focus = "center"
        if focus and any(x in c for x in ["حرك", "خليه", "ركز", "focus", "move", "zoom"]):
            edit["focus"] = focus
            changed = True
        if changed:
            note = f"Updated the previous zoom to {float(edit.get('zoom', 1.25)):g}× ({edit.get('focus', 'center')})."

    elif kind == "image_overlay":
        pos = edit.get("position", "top-right")
        if any(x in c for x in ["حرك", "move", "خليه", "ديرو", "ديرها"]):
            if any(x in c for x in ["لليسار", "left", "lisar"]): pos = _horizontal_position(pos, "left"); changed = True
            elif any(x in c for x in ["لليمين", "right", "limn"]): pos = _horizontal_position(pos, "right"); changed = True
            if any(x in c for x in ["لفوق", "فوق", "top", "fo9"]): pos = _vertical_position(pos, "top"); changed = True
            elif any(x in c for x in ["لتحت", "تحت", "bottom", "ta7t"]): pos = _vertical_position(pos, "bottom"); changed = True
            if any(x in c for x in ["للوسط", "وسط", "center", "middle"]): pos = "center"; changed = True
            edit["position"] = pos
        if any(x in c for x in ["كبر", "bigger", "larger", "increase size"]):
            edit["scale"] = min(0.6, round(float(edit.get("scale", 0.18)) * 1.2, 3)); changed = True
        elif any(x in c for x in ["صغر", "smaller", "decrease size"]):
            edit["scale"] = max(0.05, round(float(edit.get("scale", 0.18)) / 1.2, 3)); changed = True
        if changed:
            note = f"Updated the logo/image: {edit.get('position', 'top-right')}, size {round(float(edit.get('scale', .18))*100)}%."

    elif kind == "text_overlay":
        shift = nums[0] if nums else 1.0
        if any(x in c for x in ["بكري", "earlier", "before"]):
            edit["start"] = max(0.0, float(edit.get("start", 0)) - shift)
            edit["end"] = max(edit["start"] + 0.1, float(edit.get("end", base_duration)) - shift)
            changed = True
        elif any(x in c for x in ["متأخر", "مورا", "later", "after"]):
            edit["start"] = min(base_duration - 0.1, float(edit.get("start", 0)) + shift)
            edit["end"] = min(base_duration, float(edit.get("end", base_duration)) + shift)
            changed = True
        if any(x in c for x in ["فوق", "top"]): edit["position"] = "top"; changed = True
        elif any(x in c for x in ["وسط", "center", "middle"]): edit["position"] = "center"; changed = True
        elif any(x in c for x in ["تحت", "bottom"]): edit["position"] = "bottom"; changed = True
        quoted = re.search(r'[“\"]([^”\"]{1,120})[”\"]', command)
        if quoted and any(x in c for x in ["بدل", "change", "replace text", "خلي النص"]):
            edit["text"] = quoted.group(1).strip(); changed = True
        if changed:
            note = f"Updated the previous text overlay from {float(edit.get('start',0)):g}s to {float(edit.get('end',base_duration)):g}s ({edit.get('position','bottom')})."

    elif kind == "music":
        if "%" in c and nums:
            edit["factor"] = max(0.0, min(1.5, nums[0] / 100.0)); changed = True
        elif any(x in c for x in ["طلع", "زيد الصوت", "louder", "more volume"]):
            edit["factor"] = min(1.5, round(float(edit.get("factor", .2)) + .1, 2)); changed = True
        elif any(x in c for x in ["نقص", "هابط", "quieter", "lower", "less volume"]):
            edit["factor"] = max(0.0, round(float(edit.get("factor", .2)) - .1, 2)); changed = True
        if changed: note = f"Updated background music volume to {round(float(edit.get('factor',.2))*100)}%."

    elif kind == "volume":
        if "%" in c and nums:
            edit["factor"] = max(0.0, min(3.0, nums[0] / 100.0)); changed = True
        elif any(x in c for x in ["طلع", "louder", "more volume"]):
            edit["factor"] = min(3.0, round(float(edit.get("factor", 1.0)) + .1, 2)); changed = True
        elif any(x in c for x in ["نقص", "quieter", "lower", "less volume"]):
            edit["factor"] = max(0.0, round(float(edit.get("factor", 1.0)) - .1, 2)); changed = True
        if changed: note = f"Updated video volume to {round(float(edit.get('factor',1))*100)}%."

    elif kind == "speed":
        if any(x in c for x in ["سرع كثر", "زيد السرعة", "faster", "speed it up more"]):
            edit["speed"] = min(4.0, round(float(edit.get("speed",1.0)) + .25, 2)); changed = True
        elif any(x in c for x in ["بطئ", "بطّ", "slower", "slow it down"]):
            edit["speed"] = max(.25, round(float(edit.get("speed",1.0)) - .25, 2)); changed = True
        if changed: note = f"Updated speed to {float(edit.get('speed',1)):g}×."

    elif kind in {"insert_clip", "replace_segment"}:
        if any(x in c for x in ["بهاد واحد", "بهذا", "بهدا", "latest clip", "new clip", "هاد الفيديو", "this clip"]):
            latest = _latest_asset_id(meta, "video")
            if latest:
                edit["asset_id"] = latest
                changed = True
                note = "Replaced the clip used by the previous edit with the latest attached video."

    if not changed:
        return None
    return {
        "edits": edits,
        "summary": note,
        "revision_of": idx,
        "base": last.get("base"),
        "parser": "context-local",
    }


def parse_context_with_gemini(command: str, meta: dict, base_duration: float) -> Optional[dict]:
    if not GEMINI_API_KEY:
        return None
    idx, last = _last_command(meta)
    if last is None or not last.get("edits"):
        return None
    instructions = (
        "You are resolving a conversational follow-up in a video editor.\n"
        f"The user may be referring to the immediately previous logical edit. Base video duration: {base_duration:.2f}s.\n"
        f"Previous edit plan: {json.dumps(last.get('edits'), ensure_ascii=False)}\n"
        "If the new message CLEARLY asks to modify that previous edit (for example: bigger, smaller, move it left, start it earlier, use this new clip), "
        "return the complete REVISED previous plan as JSON: {\"edits\":[...],\"summary\":\"...\"}. "
        "If it is a new independent edit, return {\"edits\":[],\"summary\":\"not a revision\"}. "
        "Use only the same supported edit schemas as the previous plan. Preserve fields the user did not ask to change. Return the summary in the same language as the user's new message. Do not invent edits."
    )
    try:
        plan = normalize_ai_plan(gemini_json(instructions, command, 600), base_duration)
        if not plan["edits"]:
            return None
        return {**plan, "revision_of": idx, "base": last.get("base"), "parser": "context-gemini"}
    except Exception as exc:
        print("Context Gemini parser failed:", repr(exc))
        return None

def parse_single_deterministic(command: str, duration: float) -> Optional[dict]:
    raw = command.strip()
    c = raw.lower()

    beginning_markers = [
        "remove first", "trim first", "delete first", "cut first",
        "حيد أول", "حيد الاول", "حيد اللول", "قطع أول", "قطع اللول",
        "7id lwal", "7yed lwal", "7id awal",
        "elimina los primeros", "recorta los primeros", "quita los primeros",
        "remova os primeiros", "corte os primeiros", "retire os primeiros",
        "supprime les premières", "coupe les premières", "retire les premières",
        "entferne die ersten", "schneide die ersten",
        "पहले", "hapus", "potong", "ilk", "kes",
        "最初の", "처음", "前", "удали первые", "обрежь первые", "rimuovi i primi",
    ]
    if any(m.lower() in c for m in beginning_markers):
        n = parse_number(c)
        if n is not None:
            return {"type": "trim_start", "seconds": n}

    segment_markers = ["remove from", "delete from", "cut from", "حيد من", "قطع من", "7id mn", "7yed mn"]
    if any(m in c for m in segment_markers):
        nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", c)]
        if len(nums) >= 2 and nums[1] > nums[0]:
            return {"type": "remove_segment", "start": nums[0], "end": nums[1]}

    for ratio in ("9:16", "1:1", "16:9"):
        if ratio in c:
            return {"type": "aspect", "ratio": ratio}

    if any(x in c for x in ["mute", "no sound", "حيد الصوت", "سكت الصوت", "bla sot", "7id sot",
        "silenciar", "sin sonido", "mudo", "sem som", "coupe le son", "sans son",
        "stummschalten", "ohne ton", "आवाज़ बंद", "म्यूट", "matikan suara", "sessize al",
        "ミュート", "音を消", "음소거", "静音", "关闭声音", "без звука", "выключи звук", "senza audio"]):
        return {"type": "volume", "factor": 0.0}

    if any(x in c for x in ["volume", "sound", "الصوت", "sot"]):
        m = re.search(r"(\d+(?:\.\d+)?)\s*%", c)
        if m:
            pct = max(0.0, min(300.0, float(m.group(1))))
            return {"type": "volume", "factor": pct / 100.0}

    if any(x in c for x in ["speed", "faster", "slower", "سرع", "سرعة", "بطّ", "bt2", "sre3"]):
        m = re.search(r"(0\.\d+|[1-4](?:\.\d+)?)\s*x", c) or re.search(r"x\s*(0\.\d+|[1-4](?:\.\d+)?)", c)
        if m:
            speed = float(m.group(1))
            if 0.25 <= speed <= 4.0:
                return {"type": "speed", "speed": speed}

    # Zoom a precise time range. MVP uses deterministic crop+scale with a chosen focus.
    zoom_markers = ["zoom", "zoom in", "كبر", "قرب", "زووم"]
    if any(x in c for x in zoom_markers):
        zoom = 1.25
        zm = re.search(r"(1(?:\.\d+)?|2(?:\.0)?)\s*x", c) or re.search(r"x\s*(1(?:\.\d+)?|2(?:\.0)?)", c)
        if zm:
            zoom = max(1.05, min(2.0, float(zm.group(1))))
        nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", c)]
        if zm:
            try:
                znum = float(zm.group(1))
                removed = False
                kept=[]
                for n in nums:
                    if not removed and abs(n-znum) < 1e-9:
                        removed=True
                    else:
                        kept.append(n)
                nums=kept
            except Exception:
                pass
        start, end = 0.0, duration
        if len(nums) >= 2 and any(x in c for x in ["from", "to", "من", "حتى", "mn"]):
            start, end = nums[0], nums[1]
        focus = "center"
        if any(x in c for x in ["left", "اليسار", "lisar"]):
            focus = "left"
        elif any(x in c for x in ["right", "اليمين", "limn"]):
            focus = "right"
        elif any(x in c for x in ["top", "فوق", "fo9"]):
            focus = "top"
        elif any(x in c for x in ["bottom", "تحت", "lta7t"]):
            focus = "bottom"
        if 0 <= start < end <= duration:
            return {"type": "zoom_segment", "start": start, "end": end, "zoom": zoom, "focus": focus}

    # Simple cinematic fades.
    if "fade in" in c or "فيد ان" in c or "دخول تدريجي" in c:
        n = parse_number(c) or 0.6
        return {"type": "fade", "direction": "in", "duration": max(0.1, min(float(n), duration))}
    if "fade out" in c or "فيد اوت" in c or "خروج تدريجي" in c:
        n = parse_number(c) or 0.6
        return {"type": "fade", "direction": "out", "duration": max(0.1, min(float(n), duration))}

    # Insert/replace using the latest attached video clip.
    replace_markers = ["replace from", "replace clip from", "بدل من", "عوّض من", "عوض من", "bdel mn"]
    if any(x in c for x in replace_markers):
        nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", c)]
        if len(nums) >= 2 and 0 <= nums[0] < nums[1] <= duration:
            return {"type": "replace_segment", "start": nums[0], "end": nums[1], "transition": "fade" if "fade" in c or "فيد" in c else "cut"}

    insert_markers = ["insert clip", "insert video", "add clip at", "add video at", "دخل الفيديو", "زيد الفيديو", "حط الفيديو", "zid video", "7et video"]
    if any(x in c for x in insert_markers):
        nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", c)]
        at = nums[0] if nums else duration
        if 0 <= at <= duration:
            return {"type": "insert_clip", "at": at, "transition": "fade" if "fade" in c or "فيد" in c else "cut"}

    # Latest uploaded image/logo overlay. Asset path is resolved per project at execution time.
    image_markers = [
        "add logo", "put logo", "add image", "overlay image",
        "زيد اللوغو", "حط اللوغو", "زيد الصورة", "حط الصورة",
        "zid logo", "7et logo", "dir logo",
        "añade logo", "agrega logo", "adicionar logo", "ajoute le logo", "logo hinzufügen",
        "लोगो जोड़", "tambahkan logo", "logo ekle", "ロゴを追加", "로고 추가", "添加徽标",
        "добавь логотип", "aggiungi logo",
    ]
    if any(x in c for x in image_markers):
        position = "top-right"
        if any(x in c for x in ["top left", "فوق على اليسار", "fo9 lisar"]):
            position = "top-left"
        elif any(x in c for x in ["bottom right", "لتحت على اليمين", "تحت على اليمين", "lta7t limn"]):
            position = "bottom-right"
        elif any(x in c for x in ["bottom left", "لتحت على اليسار", "تحت على اليسار", "lta7t lisar"]):
            position = "bottom-left"
        elif any(x in c for x in ["center", "middle", "وسط", "lwest"]):
            position = "center"
        scale = 0.18
        m = re.search(r"(\d+(?:\.\d+)?)\s*%", c)
        if m and any(x in c for x in ["size", "حجم", "كبر", "صغر"]):
            scale = max(0.05, min(0.6, float(m.group(1)) / 100.0))
        return {"type": "image_overlay", "position": position, "scale": scale}

    # Latest uploaded audio as background music. Keep original speech/audio and mix by default.
    music_markers = [
        "add music", "background music", "put music",
        "زيد الموسيقى", "حط الموسيقى", "موسيقى فالخلفية",
        "zid music", "7et music",
        "añade música", "agrega música", "adicionar música", "coloque música",
        "ajoute de la musique", "mets de la musique", "musik hinzufügen",
        "संगीत जोड़", "tambahkan musik", "müzik ekle", "音楽を追加", "음악 추가",
        "添加音乐", "добавь музыку", "aggiungi musica",
    ]
    if any(x in c for x in music_markers):
        factor = 0.2
        m = re.search(r"(\d+(?:\.\d+)?)\s*%", c)
        if m:
            factor = max(0.0, min(1.5, float(m.group(1)) / 100.0))
        return {"type": "music", "factor": factor}

    caption_markers = [
        "add captions", "add subtitles", "auto captions", "automatic captions",
        "caption this", "subtitle this", "زيد الكابشن", "زيد الكابتشن", "زيد subtitles",
        "دير كابشن", "دير subtitles", "دير الترجمة", "zid captions", "dir captions",
        "añade subtítulos", "agrega subtítulos", "legendas automáticas", "adicionar legendas",
        "ajoute des sous-titres", "sous-titres automatiques", "untertitel hinzufügen", "automatische untertitel",
        "सबटाइटल", "कैप्शन", "tambahkan subtitle", "subtitle otomatis", "altyazı ekle",
        "字幕を追加", "自動字幕", "자막 추가", "자동 자막", "添加字幕", "自动字幕",
        "добавь субтитры", "автосубтитры", "aggiungi sottotitoli", "sottotitoli automatici",
    ]
    if any(x in c for x in caption_markers):
        position = "bottom"
        if any(x in c for x in ["top", "فوق", "lfou9"]):
            position = "top"
        elif any(x in c for x in ["center", "middle", "وسط", "lwest"]):
            position = "center"
        return {"type": "captions", "position": position}

    # Text overlay: quote text where possible.
    text_markers = ["add text", "put text", "write", "زيد", "كتب", "ktb", "zid"]
    if any(x in c for x in text_markers):
        quoted = re.search(r'[“\"]([^”\"]{1,120})[”\"]', raw)
        text = quoted.group(1).strip() if quoted else ""
        if not text:
            # English "add text Order Now ..."
            m = re.search(r"(?:add text|put text|write)\s+(.+?)(?:\s+(?:from|at|for|on|in)\b|$)", raw, re.I)
            if m:
                text = m.group(1).strip(" :,-")
        if not text:
            # Darija/Arabic: take the portion after marker until timing/location hint.
            m = re.search(r"(?:زيد|كتب|zid|ktb)\s+(.+?)(?:\s+(?:من|فال|في|لمدة|from|at|for)\b|$)", raw, re.I)
            if m:
                text = m.group(1).strip(" :,-")
        if text:
            position = "bottom"
            if any(x in c for x in ["top", "فوق", "lfou9"]):
                position = "top"
            elif any(x in c for x in ["center", "middle", "وسط", "lwest"]):
                position = "center"

            nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", c)]
            start, end = 0.0, duration
            if "last" in c or "end" in c or "الآخر" in c or "فالخر" in c or "f lekher" in c:
                n = nums[-1] if nums else 3.0
                start, end = max(0.0, duration - n), duration
            elif len(nums) >= 2 and any(x in c for x in ["from", "من", "mn"]):
                start, end = nums[-2], nums[-1]
            elif nums and any(x in c for x in ["for", "لمدة"]):
                end = min(duration, nums[-1])
            return {"type": "text_overlay", "text": text[:120], "start": start, "end": max(start + 0.1, end), "position": position}

    return None


def parse_deterministic(command: str, duration: float) -> dict:
    # Support simple multi-step prompts split by conjunctions. Quoted text stays intact well enough for MVP.
    parts = re.split(r"\s+(?:and then|then|and|y luego|y|e depois|e|puis|et|und dann|und|और फिर|और|lalu|dan|sonra|ve|そして|それから|그리고|然后|и потом|и|e poi|و من بعد|ومن بعد|و)\s+", command, flags=re.I)
    edits: list[dict] = []
    for part in parts:
        edit = parse_single_deterministic(part.strip(), duration)
        if edit:
            edits.append(edit)
    if not edits:
        one = parse_single_deterministic(command, duration)
        if one:
            edits = [one]
    return {"edits": edits, "summary": " ".join(_summary_for(e) for e in edits)}


def _extract_response_text(data: dict) -> str:
    for item in data.get("output", []):
        if item.get("type") == "message":
            for content in item.get("content", []):
                if content.get("type") == "output_text" and content.get("text"):
                    return content["text"]
    return ""


def _extract_gemini_text(data: dict) -> str:
    try:
        parts = data.get("candidates", [])[0].get("content", {}).get("parts", [])
        return "".join(str(p.get("text", "")) for p in parts if p.get("text"))
    except Exception:
        return ""


def gemini_json(instructions: str, user_text: str, max_output_tokens: int = 700) -> dict:
    if not GEMINI_API_KEY:
        raise RuntimeError("Gemini is not configured")
    payload = {
        "system_instruction": {"parts": [{"text": instructions}]},
        "contents": [{"role": "user", "parts": [{"text": user_text}]}],
        "generationConfig": {
            "temperature": 0,
            "maxOutputTokens": max_output_tokens,
            "responseMimeType": "application/json",
        },
    }
    url = f"{GEMINI_BASE_URL}/models/{GEMINI_MODEL}:generateContent"
    with httpx.Client(timeout=45.0) as client:
        r = client.post(url, headers={"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"}, json=payload)
        if r.status_code >= 400:
            raise RuntimeError(f"Gemini request failed ({r.status_code}): {r.text[-1200:]}")
        text = _extract_gemini_text(r.json()).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S).strip()
    if not text:
        raise RuntimeError("Gemini returned an empty response")
    return json.loads(text)


def normalize_ai_plan(plan: Any, duration: float) -> dict:
    if not isinstance(plan, dict) or not isinstance(plan.get("edits"), list):
        raise ValueError("AI returned an invalid edit plan")
    allowed = {"trim_start", "remove_segment", "aspect", "volume", "speed", "text_overlay", "captions", "image_overlay", "music", "zoom_segment", "fade", "insert_clip", "replace_segment"}
    clean: list[dict] = []
    for edit in plan["edits"][:8]:
        if not isinstance(edit, dict) or edit.get("type") not in allowed:
            continue
        kind = edit["type"]
        if kind == "trim_start":
            sec = float(edit.get("seconds", 0))
            if 0 < sec < duration:
                clean.append({"type": kind, "seconds": sec})
        elif kind == "remove_segment":
            s, e = float(edit.get("start", 0)), float(edit.get("end", 0))
            if 0 <= s < e <= duration:
                clean.append({"type": kind, "start": s, "end": e})
        elif kind == "aspect" and edit.get("ratio") in {"9:16", "1:1", "16:9"}:
            clean.append({"type": kind, "ratio": edit["ratio"]})
        elif kind == "volume":
            factor = max(0.0, min(3.0, float(edit.get("factor", 1.0))))
            clean.append({"type": kind, "factor": factor})
        elif kind == "speed":
            speed = float(edit.get("speed", 1.0))
            if 0.25 <= speed <= 4.0:
                clean.append({"type": kind, "speed": speed})
        elif kind == "captions":
            pos = edit.get("position", "bottom")
            if pos not in {"top", "center", "bottom"}:
                pos = "bottom"
            clean.append({"type": kind, "position": pos})
        elif kind == "image_overlay":
            pos = edit.get("position", "top-right")
            if pos not in {"top-left", "top-right", "bottom-left", "bottom-right", "center"}:
                pos = "top-right"
            scale = max(0.05, min(0.6, float(edit.get("scale", 0.18))))
            clean.append({"type": kind, "position": pos, "scale": scale, **({"asset_id": str(edit["asset_id"])} if edit.get("asset_id") else {})})
        elif kind == "music":
            factor = max(0.0, min(1.5, float(edit.get("factor", 0.2))))
            clean.append({"type": kind, "factor": factor, **({"asset_id": str(edit["asset_id"])} if edit.get("asset_id") else {})})
        elif kind == "zoom_segment":
            start = max(0.0, float(edit.get("start", 0)))
            end = min(duration, float(edit.get("end", duration)))
            zoom = max(1.05, min(2.0, float(edit.get("zoom", 1.25))))
            focus = edit.get("focus", "center")
            if focus not in {"center", "left", "right", "top", "bottom"}:
                focus = "center"
            if start < end:
                clean.append({"type": kind, "start": start, "end": end, "zoom": zoom, "focus": focus})
        elif kind == "fade":
            direction = edit.get("direction", "in")
            if direction not in {"in", "out"}:
                direction = "in"
            dur = max(0.1, min(duration, float(edit.get("duration", 0.6))))
            clean.append({"type": kind, "direction": direction, "duration": dur})
        elif kind == "insert_clip":
            at = max(0.0, min(duration, float(edit.get("at", duration))))
            transition = "fade" if edit.get("transition") == "fade" else "cut"
            clean.append({"type": kind, "at": at, "transition": transition, **({"asset_id": str(edit["asset_id"])} if edit.get("asset_id") else {})})
        elif kind == "replace_segment":
            start = max(0.0, float(edit.get("start", 0)))
            end = min(duration, float(edit.get("end", duration)))
            transition = "fade" if edit.get("transition") == "fade" else "cut"
            if start < end:
                clean.append({"type": kind, "start": start, "end": end, "transition": transition, **({"asset_id": str(edit["asset_id"])} if edit.get("asset_id") else {})})
        elif kind == "text_overlay":
            text = str(edit.get("text", "")).strip()[:120]
            start = max(0.0, float(edit.get("start", 0)))
            end = min(duration, float(edit.get("end", duration)))
            pos = edit.get("position", "bottom")
            if pos not in {"top", "center", "bottom"}:
                pos = "bottom"
            if text and start < end:
                clean.append({"type": kind, "text": text, "start": start, "end": end, "position": pos})
    return {"edits": clean, "summary": " ".join(_summary_for(e) for e in clean)}


def parse_with_gemini(command: str, duration: float) -> dict:
    if not GEMINI_API_KEY:
        raise RuntimeError("Gemini is not configured")

    instructions = f"""You are the command parser for a video editor. Convert the user's editing request into JSON only.
Current video duration is {duration:.2f} seconds.
Supported edit types ONLY:
- trim_start: {{"type":"trim_start","seconds":number}}
- remove_segment: {{"type":"remove_segment","start":number,"end":number}}
- aspect: {{"type":"aspect","ratio":"9:16"|"1:1"|"16:9"}}
- volume: {{"type":"volume","factor":number}} where 1 is 100%, 0 mute
- speed: {{"type":"speed","speed":number}} from 0.25 to 4
- text_overlay: {{"type":"text_overlay","text":string,"start":number,"end":number,"position":"top"|"center"|"bottom"}}
- captions: {{"type":"captions","position":"top"|"center"|"bottom"}} for automatic speech captions
- zoom_segment: {{"type":"zoom_segment","start":number,"end":number,"zoom":1.05..2.0,"focus":"center"|"left"|"right"|"top"|"bottom"}}
- fade: {{"type":"fade","direction":"in"|"out","duration":number}}
- insert_clip: {{"type":"insert_clip","at":number,"transition":"cut"|"fade"}} using the latest attached video clip
- replace_segment: {{"type":"replace_segment","start":number,"end":number,"transition":"cut"|"fade"}} using the latest attached video clip
Return exactly: {{"edits":[...],"summary":"short human summary"}}.
Understand the user's language directly. Strongly support English, Spanish, Portuguese, French, Arabic and Moroccan Darija (Arabic or Latin script), German, Hindi, Indonesian, Turkish, Japanese, Korean, Chinese, Italian, and Russian. Return the summary in the same language as the user's command. Never invent an edit the user did not ask for. If the request is unsupported, return an empty edits array. For 'at the end for N seconds', calculate start = duration-N and end = duration. For multi-step requests, return edits in the requested order."""
    return normalize_ai_plan(gemini_json(instructions, command, 800), duration)

def parse_command(command: str, duration: float) -> tuple[dict, str]:
    # Deterministic parser is first: cheap, fast and predictable for common commands.
    local = parse_deterministic(command, duration)
    if local["edits"]:
        return local, "local"
    if GEMINI_API_KEY:
        try:
            plan = parse_with_gemini(command, duration)
            if plan["edits"]:
                return plan, "ai"
        except Exception as exc:
            print("AI parser failed:", repr(exc))
    return {"edits": [], "summary": "I couldn't map that request to a supported edit yet."}, "none"


def probe_video_size(path: Path) -> tuple[int, int]:
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        w, h = p.stdout.strip().split("x", 1)
        return int(w), int(h)
    except Exception:
        return 1280, 720


def _ass_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _ass_escape(text: str) -> str:
    # ASS control characters must be escaped; preserve UTF-8 for Arabic/Darija.
    return str(text).replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}").replace("\n", r"\N")


def words_to_caption_cues(words: list[dict]) -> list[dict]:
    """Group word timestamps into readable 1–2 line caption cues."""
    cues: list[dict] = []
    group: list[dict] = []
    max_words = 7
    max_duration = 2.8

    def flush() -> None:
        nonlocal group
        if not group:
            return
        text = " ".join(str(w.get("word", "")).strip() for w in group).strip()
        # Clean spacing before common punctuation.
        text = re.sub(r"\s+([,.!?؛،:])", r"\1", text)
        if text:
            cues.append({
                "start": float(group[0].get("start", 0.0)),
                "end": max(float(group[-1].get("end", 0.0)), float(group[0].get("start", 0.0)) + 0.18),
                "text": text,
            })
        group = []

    for word in words:
        token = str(word.get("word", "")).strip()
        if not token:
            continue
        try:
            start = float(word.get("start", 0.0))
            end = float(word.get("end", start + 0.2))
        except Exception:
            continue
        if group:
            group_start = float(group[0].get("start", start))
            gap = start - float(group[-1].get("end", start))
            if len(group) >= max_words or end - group_start > max_duration or gap > 0.75:
                flush()
        group.append({"word": token, "start": start, "end": end})
        if re.search(r"[.!?؟]$", token) and len(group) >= 3:
            flush()
    flush()
    return cues


def segments_to_caption_cues(segments: list[dict]) -> list[dict]:
    cues: list[dict] = []
    for seg in segments:
        text = str(seg.get("text", "")).strip()
        if not text:
            continue
        try:
            start = float(seg.get("start", 0.0))
            end = float(seg.get("end", start + 1.0))
        except Exception:
            continue
        # Split very long segments roughly by words while preserving their segment time.
        tokens = text.split()
        if len(tokens) <= 10:
            cues.append({"start": start, "end": max(end, start + 0.2), "text": text})
            continue
        chunks = [tokens[i:i+8] for i in range(0, len(tokens), 8)]
        span = max(end - start, 0.4)
        for i, chunk in enumerate(chunks):
            cs = start + span * i / len(chunks)
            ce = start + span * (i + 1) / len(chunks)
            cues.append({"start": cs, "end": max(ce, cs + 0.2), "text": " ".join(chunk)})
    return cues


def _caption_cache_path(work_dir: Path, audio_hash: str) -> Path:
    cache_dir = work_dir / "caption_cache"
    cache_dir.mkdir(exist_ok=True)
    model_key = re.sub(r"[^A-Za-z0-9_.-]+", "_", GEMINI_TRANSCRIBE_MODEL)[:60]
    return cache_dir / f"{model_key}_{audio_hash}.json"


def _offset_seconds(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if text.endswith("s"):
        text = text[:-1]
    try:
        return float(text)
    except Exception:
        return 0.0


def _upload_gemini_file(path: Path, mime_type: str) -> tuple[str, str | None]:
    size = path.stat().st_size
    start_url = f"{GEMINI_BASE_URL.replace('/v1beta','')}/upload/v1beta/files"
    headers = {
        "x-goog-api-key": GEMINI_API_KEY,
        "X-Goog-Upload-Protocol": "resumable",
        "X-Goog-Upload-Command": "start",
        "X-Goog-Upload-Header-Content-Length": str(size),
        "X-Goog-Upload-Header-Content-Type": mime_type,
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=120.0) as client:
        r = client.post(start_url, headers=headers, json={"file": {"display_name": path.name}})
        if r.status_code >= 400:
            raise RuntimeError(f"Gemini file upload start failed ({r.status_code}): {r.text[-1200:]}")
        upload_url = r.headers.get("x-goog-upload-url")
        if not upload_url:
            raise RuntimeError("Gemini did not return a resumable upload URL")
        with path.open("rb") as fh:
            r2 = client.post(
                upload_url,
                headers={
                    "Content-Length": str(size),
                    "X-Goog-Upload-Offset": "0",
                    "X-Goog-Upload-Command": "upload, finalize",
                    "Content-Type": mime_type,
                },
                content=fh.read(),
            )
        if r2.status_code >= 400:
            raise RuntimeError(f"Gemini file upload failed ({r2.status_code}): {r2.text[-1200:]}")
        info = r2.json().get("file", {})
        uri = info.get("uri")
        if not uri:
            raise RuntimeError("Gemini upload response did not contain a file URI")
        return str(uri), info.get("name")


def _delete_gemini_file(name: str | None) -> None:
    if not name:
        return
    try:
        with httpx.Client(timeout=15.0) as client:
            client.delete(f"{GEMINI_BASE_URL}/{name}", headers={"x-goog-api-key": GEMINI_API_KEY})
    except Exception:
        pass


def transcribe_caption_cues(src: Path, work_dir: Path) -> tuple[list[dict], str]:
    if not GEMINI_API_KEY:
        raise RuntimeError("Auto captions require GEMINI_API_KEY")
    if not probe_has_audio(src):
        raise RuntimeError("This video has no audio track to caption")

    audio = work_dir / f"caption_audio_{uuid.uuid4().hex[:8]}.mp3"
    run(["ffmpeg", "-y", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", "-b:a", "64k", str(audio)])
    audio_hash = hashlib.sha256(audio.read_bytes()).hexdigest()[:32]
    cache_path = _caption_cache_path(work_dir, audio_hash)
    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            cues = cached.get("cues") or []
            if cues:
                update_job_progress(45, "Using cached captions")
                audio.unlink(missing_ok=True)
                return cues, str(cached.get("transcript", ""))
        except Exception:
            cache_path.unlink(missing_ok=True)

    update_job_progress(40, "Transcribing captions with Gemini")
    file_name = None
    try:
        file_uri, file_name = _upload_gemini_file(audio, "audio/mpeg")
        payload = {
            "model": GEMINI_TRANSCRIBE_MODEL,
            "input": [{"type": "audio", "uri": file_uri, "mime_type": "audio/mpeg"}],
            "generation_config": {
                "transcription_config": {
                    "language_codes": [],
                    "mode": {"type": "verbatim", "timestamp_granularities": ["word"]},
                }
            },
        }
        with httpx.Client(timeout=180.0) as client:
            r = client.post(
                f"{GEMINI_BASE_URL}/interactions",
                headers={"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"},
                json=payload,
            )
            if r.status_code >= 400:
                raise RuntimeError(f"Gemini transcription failed ({r.status_code}): {r.text[-1200:]}")
            data = r.json()

        transcript = ""
        words: list[dict] = []
        for step in data.get("steps", []) or []:
            for content in step.get("content", []) or []:
                if content.get("type") == "text" and content.get("text"):
                    if not transcript:
                        transcript = str(content.get("text", "")).strip()
                for annotation in content.get("annotations", []) or []:
                    if annotation.get("type") != "word_info":
                        continue
                    token = str(annotation.get("text", "")).strip()
                    if not token:
                        continue
                    words.append({
                        "word": token,
                        "start": _offset_seconds(annotation.get("start_offset")),
                        "end": _offset_seconds(annotation.get("end_offset")),
                    })
        # Some Interactions responses expose output text separately.
        if not transcript:
            transcript = str(data.get("output_text", "") or "").strip()
        cues = words_to_caption_cues(words)
        if not cues:
            raise RuntimeError("Gemini transcription returned no timestamped speech")
    finally:
        audio.unlink(missing_ok=True)
        _delete_gemini_file(file_name)

    try:
        cache_path.write_text(json.dumps({"cues": cues, "transcript": transcript, "model": GEMINI_TRANSCRIBE_MODEL}, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return cues, transcript

def write_ass_captions(path: Path, cues: list[dict], video_size: tuple[int, int], position: str = "bottom") -> None:
    w, h = video_size
    font_size = max(24, int(min(w, h) * 0.055))
    margin_v = max(28, int(h * 0.06))
    alignment = {"bottom": 2, "center": 5, "top": 8}.get(position, 2)
    all_text = " ".join(str(c.get("text", "")) for c in cues)
    ass_font = ass_font_name_for_text(all_text)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Default,{ass_font},{font_size},&H00FFFFFF,&H000000FF,&H00101010,&H70000000,-1,0,0,0,100,100,0,0,1,3,1,{alignment},60,60,{margin_v},1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""
    lines = [header]
    for cue in cues:
        text = _ass_escape(cue["text"])
        lines.append(f"Dialogue: 0,{_ass_time(cue['start'])},{_ass_time(cue['end'])},Default,,0,0,0,,{text}\n")
    path.write_text("".join(lines), encoding="utf-8")


def burn_caption_cues(src: Path, dst: Path, cues: list[dict], work_dir: Path, position: str = "bottom") -> None:
    ass = work_dir / f"captions_{uuid.uuid4().hex[:8]}.ass"
    write_ass_captions(ass, cues, probe_video_size(src), position)
    # ASS/libass handles Unicode shaping and styling more reliably than drawtext for timed captions.
    vf = f"ass='{ass}'"
    cmd = ["ffmpeg", "-y", "-i", str(src), "-vf", vf, "-c:v", "libx264", "-preset", "veryfast"]
    cmd += ["-c:a", "aac"] if probe_has_audio(src) else ["-an"]
    try:
        run(cmd + ["-movflags", "+faststart", str(dst)])
    finally:
        ass.unlink(missing_ok=True)


def _even(n: float) -> int:
    i = max(2, int(round(n)))
    return i if i % 2 == 0 else i - 1


def normalize_clip_av(src: Path, dst: Path, width: int, height: int) -> None:
    """Normalize a clip to H.264/AAC with stereo audio for reliable concatenation."""
    vf = f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},fps=30,format=yuv420p"
    if probe_has_audio(src):
        run(["ffmpeg", "-y", "-i", str(src), "-vf", vf, "-af", "aresample=44100", "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", "-ar", "44100", "-ac", "2", "-movflags", "+faststart", str(dst)])
    else:
        dur = max(0.1, probe_duration(src))
        run(["ffmpeg", "-y", "-i", str(src), "-f", "lavfi", "-t", str(dur), "-i", "anullsrc=channel_layout=stereo:sample_rate=44100", "-vf", vf, "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", "-ar", "44100", "-ac", "2", "-shortest", "-movflags", "+faststart", str(dst)])


def _splice_clip_cut(main: Path, clip: Path, dst: Path, start: float, end: float, total: float) -> None:
    parts=[]
    concat_inputs=[]
    if start > 0.001:
        parts += [f"[0:v]trim=0:{start},setpts=PTS-STARTPTS,fps=30,settb=AVTB[vpre]", f"[0:a]atrim=0:{start},asetpts=PTS-STARTPTS[apre]"]
        concat_inputs += ["[vpre][apre]"]
    parts += ["[1:v]setpts=PTS-STARTPTS,fps=30,settb=AVTB[vclip]", "[1:a]asetpts=PTS-STARTPTS[aclip]"]
    concat_inputs += ["[vclip][aclip]"]
    if end < total - 0.001:
        parts += [f"[0:v]trim=start={end},setpts=PTS-STARTPTS,fps=30,settb=AVTB[vpost]", f"[0:a]atrim=start={end},asetpts=PTS-STARTPTS[apost]"]
        concat_inputs += ["[vpost][apost]"]
    parts.append("".join(concat_inputs)+f"concat=n={len(concat_inputs)}:v=1:a=1[v][a]")
    run(["ffmpeg", "-y", "-i", str(main), "-i", str(clip), "-filter_complex", ";".join(parts), "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", "-movflags", "+faststart", str(dst)])


def _splice_clip_crossfade(main: Path, clip: Path, dst: Path, start: float, end: float, total: float, clip_dur: float) -> None:
    pre_dur = max(0.0, start)
    post_dur = max(0.0, total - end)
    boundary_durs = [clip_dur / 3]
    if pre_dur > 0.001:
        boundary_durs.append(pre_dur / 2)
    if post_dur > 0.001:
        boundary_durs.append(post_dur / 2)
    fd = min([0.40] + boundary_durs)
    if fd < 0.08 or (pre_dur <= 0.001 and post_dur <= 0.001):
        _splice_clip_cut(main, clip, dst, start, end, total)
        return

    filters=[]
    if pre_dur > 0.001:
        filters += [f"[0:v]trim=0:{start},setpts=PTS-STARTPTS,fps=30,settb=AVTB[vpre]", f"[0:a]atrim=0:{start},asetpts=PTS-STARTPTS[apre]"]
    filters += ["[1:v]setpts=PTS-STARTPTS,fps=30,settb=AVTB[vclip]", "[1:a]asetpts=PTS-STARTPTS[aclip]"]
    if post_dur > 0.001:
        filters += [f"[0:v]trim=start={end},setpts=PTS-STARTPTS,fps=30,settb=AVTB[vpost]", f"[0:a]atrim=start={end},asetpts=PTS-STARTPTS[apost]"]

    if pre_dur > 0.001 and post_dur > 0.001:
        off1=max(0.0, pre_dur-fd)
        filters += [
            f"[vpre][vclip]xfade=transition=fade:duration={fd}:offset={off1}[vpc]",
            f"[apre][aclip]acrossfade=d={fd}:c1=tri:c2=tri[apc]",
        ]
        first_dur=pre_dur+clip_dur-fd
        off2=max(0.0, first_dur-fd)
        filters += [
            f"[vpc][vpost]xfade=transition=fade:duration={fd}:offset={off2}[v]",
            f"[apc][apost]acrossfade=d={fd}:c1=tri:c2=tri[a]",
        ]
    elif pre_dur > 0.001:
        off=max(0.0, pre_dur-fd)
        filters += [
            f"[vpre][vclip]xfade=transition=fade:duration={fd}:offset={off}[v]",
            f"[apre][aclip]acrossfade=d={fd}:c1=tri:c2=tri[a]",
        ]
    else:
        off=max(0.0, clip_dur-fd)
        filters += [
            f"[vclip][vpost]xfade=transition=fade:duration={fd}:offset={off}[v]",
            f"[aclip][apost]acrossfade=d={fd}:c1=tri:c2=tri[a]",
        ]
    run(["ffmpeg", "-y", "-i", str(main), "-i", str(clip), "-filter_complex", ";".join(filters), "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", "-movflags", "+faststart", str(dst)])


def splice_clip(src: Path, asset: Path, dst: Path, work_dir: Path, start: float, end: float, transition: str = "cut") -> None:
    """Replace [start,end] with asset. Insert is represented by start == end.

    transition='fade' now performs a real overlapping video xfade + audio acrossfade.
    """
    width, height = probe_video_size(src)
    main = work_dir / f"norm_main_{uuid.uuid4().hex[:8]}.mp4"
    clip = work_dir / f"norm_clip_{uuid.uuid4().hex[:8]}.mp4"
    try:
        normalize_clip_av(src, main, width, height)
        normalize_clip_av(asset, clip, width, height)
        total = probe_duration(main)
        clip_dur = probe_duration(clip)
        start = max(0.0, min(float(start), total))
        end = max(start, min(float(end), total))
        if transition == "fade":
            _splice_clip_crossfade(main, clip, dst, start, end, total, clip_dur)
        else:
            _splice_clip_cut(main, clip, dst, start, end, total)
    finally:
        main.unlink(missing_ok=True)
        clip.unlink(missing_ok=True)


def apply_edit(src: Path, dst: Path, edit: dict, work_dir: Path) -> None:
    kind = edit["type"]
    has_audio = probe_has_audio(src)

    if kind == "trim_start":
        sec = edit["seconds"]
        cmd = ["ffmpeg", "-y", "-ss", str(sec), "-i", str(src), "-c:v", "libx264", "-preset", "veryfast"]
        cmd += ["-c:a", "aac"] if has_audio else ["-an"]
        run(cmd + ["-movflags", "+faststart", str(dst)])
        return

    if kind == "remove_segment":
        s, e = edit["start"], edit["end"]
        if has_audio:
            filt = (
                f"[0:v]trim=0:{s},setpts=PTS-STARTPTS[v0];"
                f"[0:a]atrim=0:{s},asetpts=PTS-STARTPTS[a0];"
                f"[0:v]trim=start={e},setpts=PTS-STARTPTS[v1];"
                f"[0:a]atrim=start={e},asetpts=PTS-STARTPTS[a1];"
                "[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]"
            )
            run(["ffmpeg", "-y", "-i", str(src), "-filter_complex", filt, "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", "-movflags", "+faststart", str(dst)])
        else:
            filt = (
                f"[0:v]trim=0:{s},setpts=PTS-STARTPTS[v0];"
                f"[0:v]trim=start={e},setpts=PTS-STARTPTS[v1];"
                "[v0][v1]concat=n=2:v=1:a=0[v]"
            )
            run(["ffmpeg", "-y", "-i", str(src), "-filter_complex", filt, "-map", "[v]", "-c:v", "libx264", "-preset", "veryfast", "-an", "-movflags", "+faststart", str(dst)])
        return

    if kind == "aspect":
        targets = {"9:16": (720, 1280), "1:1": (1080, 1080), "16:9": (1280, 720)}
        w, h = targets[edit["ratio"]]
        vf = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"
        cmd = ["ffmpeg", "-y", "-i", str(src), "-vf", vf, "-c:v", "libx264", "-preset", "veryfast"]
        cmd += ["-c:a", "aac"] if has_audio else ["-an"]
        run(cmd + ["-movflags", "+faststart", str(dst)])
        return

    if kind == "volume":
        if not has_audio:
            shutil.copy2(src, dst)
            return
        run(["ffmpeg", "-y", "-i", str(src), "-filter:a", f"volume={edit['factor']}", "-c:v", "copy", "-c:a", "aac", "-movflags", "+faststart", str(dst)])
        return

    if kind == "speed":
        speed = edit["speed"]
        vf = f"setpts=PTS/{speed}"
        if has_audio:
            tempo = speed
            stages = []
            while tempo > 2.0:
                stages.append("atempo=2.0")
                tempo /= 2.0
            while tempo < 0.5:
                stages.append("atempo=0.5")
                tempo /= 0.5
            stages.append(f"atempo={tempo}")
            af = ",".join(stages)
            run(["ffmpeg", "-y", "-i", str(src), "-filter_complex", f"[0:v]{vf}[v];[0:a]{af}[a]", "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", "-movflags", "+faststart", str(dst)])
        else:
            run(["ffmpeg", "-y", "-i", str(src), "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-an", "-movflags", "+faststart", str(dst)])
        return

    if kind == "zoom_segment":
        s0, e0 = float(edit["start"]), float(edit["end"])
        zoom = float(edit.get("zoom", 1.25))
        w, h = probe_video_size(src)
        cw, ch = _even(w / zoom), _even(h / zoom)
        focus = edit.get("focus", "center")
        coords = {
            "center": (f"(iw-{cw})/2", f"(ih-{ch})/2"),
            "left": ("0", f"(ih-{ch})/2"),
            "right": (f"iw-{cw}", f"(ih-{ch})/2"),
            "top": (f"(iw-{cw})/2", "0"),
            "bottom": (f"(iw-{cw})/2", f"ih-{ch}"),
        }
        x, y = coords.get(focus, coords["center"])
        vparts=[]; concat_v=[]; aparts=[]; concat_a=[]
        if s0 > 0.001:
            vparts.append(f"[0:v]trim=0:{s0},setpts=PTS-STARTPTS[v0]"); concat_v.append("[v0]")
            if has_audio:
                aparts.append(f"[0:a]atrim=0:{s0},asetpts=PTS-STARTPTS[a0]"); concat_a.append("[a0]")
        vparts.append(f"[0:v]trim=start={s0}:end={e0},setpts=PTS-STARTPTS,crop={cw}:{ch}:{x}:{y},scale={w}:{h},setsar=1[vz]"); concat_v.append("[vz]")
        if has_audio:
            aparts.append(f"[0:a]atrim=start={s0}:end={e0},asetpts=PTS-STARTPTS[az]"); concat_a.append("[az]")
        dur=probe_duration(src)
        if e0 < dur - 0.001:
            vparts.append(f"[0:v]trim=start={e0},setpts=PTS-STARTPTS[v2]"); concat_v.append("[v2]")
            if has_audio:
                aparts.append(f"[0:a]atrim=start={e0},asetpts=PTS-STARTPTS[a2]"); concat_a.append("[a2]")
        if has_audio:
            filt=";".join(vparts+aparts+["".join(concat_v)+f"concat=n={len(concat_v)}:v=1:a=0[v]", "".join(concat_a)+f"concat=n={len(concat_a)}:v=0:a=1[a]"])
            run(["ffmpeg","-y","-i",str(src),"-filter_complex",filt,"-map","[v]","-map","[a]","-c:v","libx264","-preset","veryfast","-c:a","aac","-movflags","+faststart",str(dst)])
        else:
            filt=";".join(vparts+["".join(concat_v)+f"concat=n={len(concat_v)}:v=1:a=0[v]"])
            run(["ffmpeg","-y","-i",str(src),"-filter_complex",filt,"-map","[v]","-c:v","libx264","-preset","veryfast","-an","-movflags","+faststart",str(dst)])
        return

    if kind == "fade":
        fd = float(edit.get("duration", 0.6))
        direction = edit.get("direction", "in")
        dur = probe_duration(src)
        st = 0.0 if direction == "in" else max(0.0, dur - fd)
        vf = f"fade=t={direction}:st={st}:d={fd}"
        cmd=["ffmpeg","-y","-i",str(src),"-vf",vf,"-c:v","libx264","-preset","veryfast"]
        if has_audio:
            cmd += ["-af", f"afade=t={direction}:st={st}:d={fd}", "-c:a", "aac"]
        else:
            cmd += ["-an"]
        run(cmd+["-movflags","+faststart",str(dst)])
        return

    if kind in {"insert_clip", "replace_segment"}:
        asset = Path(edit["asset_path"])
        if not asset.exists():
            raise RuntimeError("Uploaded video clip is missing")
        if kind == "insert_clip":
            start=end=float(edit.get("at", probe_duration(src)))
        else:
            start=float(edit.get("start",0)); end=float(edit.get("end",start))
        splice_clip(src, asset, dst, work_dir, start, end, edit.get("transition","cut"))
        return

    if kind == "image_overlay":
        asset = Path(edit["asset_path"])
        if not asset.exists():
            raise RuntimeError("Uploaded image/logo asset is missing")
        vw, vh = probe_video_size(src)
        iw, ih = probe_video_size(asset)
        target_w = max(24, int(vw * float(edit.get("scale", 0.18))))
        target_h = max(24, int(target_w * ih / max(iw, 1)))
        margin = max(18, int(min(vw, vh) * 0.035))
        pos = edit.get("position", "top-right")
        xy = {
            "top-left": (str(margin), str(margin)),
            "top-right": (f"W-w-{margin}", str(margin)),
            "bottom-left": (str(margin), f"H-h-{margin}"),
            "bottom-right": (f"W-w-{margin}", f"H-h-{margin}"),
            "center": ("(W-w)/2", "(H-h)/2"),
        }
        x, y = xy.get(pos, xy["top-right"])
        filt = f"[1:v]scale={target_w}:{target_h}:force_original_aspect_ratio=decrease[ol];[0:v][ol]overlay={x}:{y}:format=auto[v]"
        cmd = ["ffmpeg", "-y", "-i", str(src), "-i", str(asset), "-filter_complex", filt, "-map", "[v]"]
        if has_audio:
            cmd += ["-map", "0:a:0", "-c:a", "aac"]
        else:
            cmd += ["-an"]
        run(cmd + ["-c:v", "libx264", "-preset", "veryfast", "-movflags", "+faststart", str(dst)])
        return

    if kind == "music":
        asset = Path(edit["asset_path"])
        if not asset.exists():
            raise RuntimeError("Uploaded music asset is missing")
        factor = float(edit.get("factor", 0.2))
        dur = probe_duration(src)
        if has_audio:
            filt = f"[0:a]volume=1[a0];[1:a]volume={factor},atrim=0:{dur},asetpts=PTS-STARTPTS[a1];[a0][a1]amix=inputs=2:duration=first:dropout_transition=2[a]"
            run(["ffmpeg", "-y", "-i", str(src), "-stream_loop", "-1", "-i", str(asset), "-filter_complex", filt, "-map", "0:v:0", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-movflags", "+faststart", str(dst)])
        else:
            filt = f"[1:a]volume={factor},atrim=0:{dur},asetpts=PTS-STARTPTS[a]"
            run(["ffmpeg", "-y", "-i", str(src), "-stream_loop", "-1", "-i", str(asset), "-filter_complex", filt, "-map", "0:v:0", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-shortest", "-movflags", "+faststart", str(dst)])
        return

    if kind == "captions":
        cues, transcript = transcribe_caption_cues(src, work_dir)
        edit["cue_count"] = len(cues)
        edit["transcript"] = transcript[:600]
        burn_caption_cues(src, dst, cues, work_dir, edit.get("position", "bottom"))
        return

    if kind == "text_overlay":
        overlay_font = font_for_text(edit.get("text", ""))
        if overlay_font is None:
            raise RuntimeError("No suitable font installed for text overlays")
        text_file = work_dir / f"overlay_{uuid.uuid4().hex[:8]}.txt"
        text_file.write_text(edit["text"], encoding="utf-8")
        # FFmpeg filter parsing needs escaped path separators/colons. Unix path is simple here.
        position = edit.get("position", "bottom")
        y = {"top": "60", "center": "(h-text_h)/2", "bottom": "h-text_h-70"}[position]
        start, end = edit.get("start", 0), edit.get("end", probe_duration(src))
        vf = (
            f"drawtext=fontfile='{overlay_font}':textfile='{text_file}':"
            f"fontcolor=white:fontsize=48:borderw=2:bordercolor=black@0.7:"
            f"box=1:boxcolor=black@0.35:boxborderw=18:"
            f"x=(w-text_w)/2:y={y}:enable='between(t,{start},{end})'"
        )
        cmd = ["ffmpeg", "-y", "-i", str(src), "-vf", vf, "-c:v", "libx264", "-preset", "veryfast"]
        cmd += ["-c:a", "aac"] if has_audio else ["-an"]
        try:
            run(cmd + ["-movflags", "+faststart", str(dst)])
        finally:
            text_file.unlink(missing_ok=True)
        return

    raise ValueError("Unsupported edit")


def apply_plan(src: Path, dst: Path, edits: list[dict], project_dir: Path) -> None:
    if not edits:
        raise ValueError("No edits")
    current = src
    temps: list[Path] = []
    for i, edit in enumerate(edits):
        is_last = i == len(edits) - 1
        out = dst if is_last else project_dir / f"tmp_{uuid.uuid4().hex[:8]}.mp4"
        apply_edit(current, out, edit, project_dir)
        if current != src and current.name.startswith("tmp_"):
            temps.append(current)
        current = out
    for p in temps:
        p.unlink(missing_ok=True)


def _export_dimensions(src: Path, resolution: str) -> tuple[int, int] | None:
    if resolution == "source":
        return None
    w, h = probe_video_size(src)
    long_target = 1280 if resolution == "720p" else 1920
    long_current = max(w, h)
    if long_current <= long_target:
        return None
    scale = long_target / long_current
    return _even(w * scale), _even(h * scale)


def export_project_file(project_dir: Path, meta: dict, req: ExportRequest) -> Path:
    resolution = req.resolution if req.resolution in {"source", "720p", "1080p"} else "source"
    fps = req.fps if req.fps in {"source", "30", "60"} else "source"
    quality = req.quality if req.quality in {"draft", "balanced", "high"} else "balanced"
    src = current_path(project_dir, meta)
    cleanup_project_storage(project_dir, meta, keep_exports=0)
    dims = _export_dimensions(src, resolution)
    qmap = {"draft": (28, "veryfast"), "balanced": (23, "medium"), "high": (18, "slow")}
    crf, preset = qmap[quality]
    out = project_dir / f"export_{uuid.uuid4().hex[:10]}.mp4"
    vf=[]
    if dims:
        vf.append(f"scale={dims[0]}:{dims[1]}")
    if fps != "source":
        vf.append(f"fps={fps}")
    cmd=["ffmpeg","-y","-i",str(src)]
    if vf:
        cmd += ["-vf", ",".join(vf)]
    cmd += ["-c:v","libx264","-preset",preset,"-crf",str(crf),"-pix_fmt","yuv420p"]
    if probe_has_audio(src):
        cmd += ["-c:a","aac","-b:a","192k"]
    else:
        cmd += ["-an"]
    update_job_progress(35, "Encoding export")
    run(cmd+["-movflags","+faststart",str(out)])
    update_job_progress(92, "Preparing download")
    return out


def render_localized_editor(lang: str) -> HTMLResponse:
    if lang not in SUPPORTED_LOCALES:
        raise HTTPException(404, "Unsupported language")
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    title, description = SEO_META.get(lang, SEO_META["en"])
    html = re.sub(r'<html lang="[^"]+"(?: dir="[^"]+")?>', f'<html lang="{lang}"' + (' dir="rtl"' if lang == 'ar' else '') + '>', html, count=1)
    html = re.sub(r'<title>.*?</title>', f'<title>{title}</title>', html, count=1, flags=re.S)
    html = re.sub(r'<meta name="description" content="[^"]*"\s*/?>', f'<meta name="description" content="{description}" />', html, count=1)
    canonical_path = "/editor" if lang == "en" else f"/editor/l/{lang}"
    canonical = f"{PUBLIC_BASE_URL}{canonical_path}" if PUBLIC_BASE_URL else canonical_path
    html = re.sub(r'<link rel="canonical" href="[^"]*"\s*/?>', f'<link rel="canonical" href="{canonical}">', html, count=1)
    if '<meta name="robots"' not in html:
        html = html.replace('</head>', '  <meta name="robots" content="noindex,nofollow" />\n</head>', 1)
    return HTMLResponse(html, headers={"Content-Language": lang})


@app.get("/editor")
def editor_home():
    return render_localized_editor("en")


@app.get("/editor/l/{lang}")
def localized_editor(lang: str):
    return render_localized_editor(lang)


LANDING_COPY = {
    "en": {"eyebrow":"AI video editing, without the editing jargon","title":"Edit videos by simply telling it what to do.","sub":"Upload your footage, describe the change in plain language, preview it, refine it, and export. No timeline skills required.","cta":"Try the free alpha","secondary":"See how it works","demo":"Try commands like","features":"Why EditByChat","f1":"Chat-first editing","f1s":"Say what you want changed instead of hunting through menus and keyframes.","f2":"You stay in control","f2s":"Every change becomes a visible, reversible edit you can refine or undo.","f3":"Built for the world","f3s":"Use the interface and editing commands across 14 major languages.","alpha":"Free Alpha · No credit card · No watermark","faq":"Frequently asked questions"},
    "es": {"eyebrow":"Edición de video con IA, sin jerga","title":"Edita videos simplemente diciendo qué quieres cambiar.","sub":"Sube tu video, describe el cambio con lenguaje natural, revísalo, ajústalo y exporta.","cta":"Probar alpha gratis","secondary":"Cómo funciona","demo":"Prueba comandos como","features":"Por qué EditByChat","f1":"Edición por chat","f1s":"Describe el cambio sin buscar menús ni keyframes.","f2":"Tú mantienes el control","f2s":"Cada cambio es visible, reversible y se puede ajustar.","f3":"Hecho para el mundo","f3s":"Interfaz y comandos en 14 idiomas principales.","alpha":"Alpha gratis · Sin tarjeta · Sin marca de agua","faq":"Preguntas frecuentes"},
    "fr": {"eyebrow":"Montage vidéo IA, sans jargon","title":"Montez vos vidéos en disant simplement quoi changer.","sub":"Importez votre vidéo, décrivez la modification, prévisualisez, affinez et exportez.","cta":"Essayer l’alpha gratuite","secondary":"Voir comment ça marche","demo":"Essayez des commandes comme","features":"Pourquoi EditByChat","f1":"Montage par chat","f1s":"Décrivez le changement sans chercher dans des menus complexes.","f2":"Vous gardez le contrôle","f2s":"Chaque modification reste visible, réversible et ajustable.","f3":"Conçu pour le monde","f3s":"Interface et commandes dans 14 langues majeures.","alpha":"Alpha gratuite · Sans carte · Sans filigrane","faq":"Questions fréquentes"},
    "ar": {"eyebrow":"مونتاج فيديو بالذكاء الاصطناعي بلا تعقيد","title":"عدّل الفيديو غير بقولك شنو بغيتي يتبدل.","sub":"ارفع الفيديو، اكتب التعديل بلغتك، شوف النتيجة، زيد صححها، وصدّر الفيديو.","cta":"جرّب النسخة Alpha مجاناً","secondary":"كيفاش كتخدم","demo":"جرّب أوامر بحال","features":"علاش EditByChat","f1":"المونتاج بالشات","f1s":"قول شنو بغيتي بلا ما تقلب فالقوائم والـkeyframes.","f2":"التحكم ديالك يبقى عندك","f2s":"كل تعديل واضح، قابل للتراجع، وتقدر تصلحو بالشات.","f3":"مصنوع للعالم كامل","f3s":"واجهة وأوامر مونتاج بـ14 لغة مهمة.","alpha":"Alpha مجانية · بلا بطاقة · بلا watermark","faq":"أسئلة شائعة"},
    "pt": {"eyebrow":"Edição de vídeo com IA, sem complicação","title":"Edite vídeos simplesmente dizendo o que quer mudar.","sub":"Envie seu vídeo, descreva a alteração, visualize, refine e exporte.","cta":"Testar alpha grátis","secondary":"Como funciona","demo":"Experimente comandos como","features":"Por que EditByChat","f1":"Edição por chat","f1s":"Descreva a mudança sem procurar menus ou keyframes.","f2":"Você mantém o controle","f2s":"Cada edição fica visível, reversível e fácil de ajustar.","f3":"Feito para o mundo","f3s":"Interface e comandos em 14 idiomas importantes.","alpha":"Alpha grátis · Sem cartão · Sem marca d’água","faq":"Perguntas frequentes"},
    "de": {"eyebrow":"KI-Videobearbeitung ohne Fachchinesisch","title":"Bearbeite Videos, indem du einfach sagst, was geändert werden soll.","sub":"Video hochladen, Änderung beschreiben, Vorschau ansehen, verfeinern und exportieren.","cta":"Kostenlose Alpha testen","secondary":"So funktioniert es","demo":"Probiere Befehle wie","features":"Warum EditByChat","f1":"Bearbeiten per Chat","f1s":"Beschreibe die Änderung statt Menüs und Keyframes zu durchsuchen.","f2":"Du behältst die Kontrolle","f2s":"Jede Änderung ist sichtbar, rückgängig und weiter anpassbar.","f3":"Für die Welt gebaut","f3s":"Oberfläche und Bearbeitungsbefehle in 14 wichtigen Sprachen.","alpha":"Kostenlose Alpha · Keine Karte · Kein Wasserzeichen","faq":"Häufige Fragen"},
    "hi": {"eyebrow":"AI वीडियो एडिटिंग, बिना जटिल टूल्स के","title":"बस बताइए क्या बदलना है और वीडियो एडिट हो जाएगा।","sub":"वीडियो अपलोड करें, बदलाव लिखें, प्रीव्यू देखें, सुधारें और एक्सपोर्ट करें।","cta":"फ्री अल्फा आज़माएँ","secondary":"यह कैसे काम करता है","demo":"ऐसे कमांड आज़माएँ","features":"EditByChat क्यों","f1":"चैट से एडिटिंग","f1s":"मेनू और कीफ्रेम खोजने के बजाय बदलाव लिखें।","f2":"कंट्रोल आपके हाथ में","f2s":"हर बदलाव दिखाई देता है, वापस किया जा सकता है और सुधारा जा सकता है।","f3":"दुनिया के लिए बनाया गया","f3s":"14 प्रमुख भाषाओं में इंटरफ़ेस और एडिटिंग कमांड।","alpha":"फ्री Alpha · कार्ड नहीं · वॉटरमार्क नहीं","faq":"अक्सर पूछे जाने वाले सवाल"},
    "id": {"eyebrow":"Edit video AI tanpa istilah rumit","title":"Edit video cukup dengan mengatakan apa yang ingin diubah.","sub":"Unggah video, jelaskan perubahan, lihat pratinjau, perbaiki, lalu ekspor.","cta":"Coba alpha gratis","secondary":"Cara kerjanya","demo":"Coba perintah seperti","features":"Mengapa EditByChat","f1":"Edit lewat chat","f1s":"Jelaskan perubahan tanpa mencari menu dan keyframe.","f2":"Kontrol tetap di tangan Anda","f2s":"Setiap edit terlihat, bisa dibatalkan, dan bisa disempurnakan.","f3":"Dibuat untuk dunia","f3s":"Antarmuka dan perintah edit dalam 14 bahasa utama.","alpha":"Alpha gratis · Tanpa kartu · Tanpa watermark","faq":"Pertanyaan umum"},
    "tr": {"eyebrow":"Karmaşık araçlar olmadan yapay zekâ video düzenleme","title":"Videoyu yalnızca neyi değiştirmek istediğini söyleyerek düzenle.","sub":"Videonu yükle, değişikliği yaz, önizle, düzelt ve dışa aktar.","cta":"Ücretsiz alpha’yı dene","secondary":"Nasıl çalışır","demo":"Şu komutları dene","features":"Neden EditByChat","f1":"Sohbetle düzenleme","f1s":"Menü ve keyframe aramak yerine istediğin değişikliği yaz.","f2":"Kontrol sende kalır","f2s":"Her değişiklik görünür, geri alınabilir ve yeniden düzenlenebilir.","f3":"Dünya için tasarlandı","f3s":"14 önemli dilde arayüz ve düzenleme komutları.","alpha":"Ücretsiz Alpha · Kart yok · Filigran yok","faq":"Sık sorulan sorular"},
    "ja": {"eyebrow":"難しい操作なしのAI動画編集","title":"変えたいことを伝えるだけで動画を編集。","sub":"動画をアップロードし、変更内容を入力、プレビューして調整し、そのまま書き出せます。","cta":"無料Alphaを試す","secondary":"使い方を見る","demo":"こんな指示が使えます","features":"EditByChatを選ぶ理由","f1":"チャットで編集","f1s":"メニューやキーフレームを探さず、変更内容をそのまま入力。","f2":"最後まで自分でコントロール","f2s":"すべての変更は見えて、戻せて、さらに調整できます。","f3":"世界向けに設計","f3s":"14の主要言語でUIと編集コマンドに対応。","alpha":"無料Alpha · カード不要 · ウォーターマークなし","faq":"よくある質問"},
    "ko": {"eyebrow":"복잡한 편집 용어 없는 AI 영상 편집","title":"바꾸고 싶은 것을 말하기만 하면 영상을 편집합니다.","sub":"영상을 올리고 변경 내용을 입력한 뒤 미리 보고 수정하고 내보내세요.","cta":"무료 알파 사용하기","secondary":"작동 방식 보기","demo":"이런 명령을 사용해 보세요","features":"왜 EditByChat인가","f1":"채팅으로 편집","f1s":"메뉴와 키프레임을 찾는 대신 원하는 변경을 말하세요.","f2":"통제권은 사용자에게","f2s":"모든 변경은 보이고, 되돌릴 수 있고, 다시 조정할 수 있습니다.","f3":"전 세계를 위해 설계","f3s":"14개 주요 언어의 UI와 편집 명령을 지원합니다.","alpha":"무료 Alpha · 카드 불필요 · 워터마크 없음","faq":"자주 묻는 질문"},
    "zh": {"eyebrow":"不用学复杂术语的 AI 视频剪辑","title":"直接说你想怎么改，视频就会跟着改。","sub":"上传视频，用自然语言描述修改，预览、继续调整，然后导出。","cta":"免费体验 Alpha","secondary":"查看工作方式","demo":"可以这样说","features":"为什么选择 EditByChat","f1":"聊天式剪辑","f1s":"不用找菜单和关键帧，直接描述你想要的修改。","f2":"控制权一直在你手上","f2s":"每个修改都可见、可撤销，也可以继续细调。","f3":"面向全球","f3s":"支持 14 种主要语言的界面和剪辑指令。","alpha":"免费 Alpha · 无需信用卡 · 无水印","faq":"常见问题"},
    "it": {"eyebrow":"Video editing con IA, senza gergo tecnico","title":"Modifica i video dicendo semplicemente cosa vuoi cambiare.","sub":"Carica il video, descrivi la modifica, guarda l’anteprima, perfeziona ed esporta.","cta":"Prova l’alpha gratis","secondary":"Come funziona","demo":"Prova comandi come","features":"Perché EditByChat","f1":"Editing via chat","f1s":"Descrivi il cambiamento senza cercare menu e keyframe.","f2":"Mantieni il controllo","f2s":"Ogni modifica è visibile, reversibile e perfezionabile.","f3":"Creato per il mondo","f3s":"Interfaccia e comandi di editing in 14 lingue principali.","alpha":"Alpha gratis · Nessuna carta · Nessuna filigrana","faq":"Domande frequenti"},
    "ru": {"eyebrow":"ИИ-монтаж без сложных терминов","title":"Редактируйте видео, просто говоря, что нужно изменить.","sub":"Загрузите видео, опишите правку, посмотрите результат, уточните и экспортируйте.","cta":"Попробовать бесплатную альфу","secondary":"Как это работает","demo":"Попробуйте команды","features":"Почему EditByChat","f1":"Монтаж через чат","f1s":"Опишите изменение вместо поиска меню и ключевых кадров.","f2":"Контроль остаётся у вас","f2s":"Каждая правка видима, отменяема и может быть уточнена.","f3":"Для пользователей по всему миру","f3s":"Интерфейс и команды монтажа на 14 основных языках.","alpha":"Бесплатная Alpha · Без карты · Без водяного знака","faq":"Частые вопросы"},
}

def landing_copy(lang: str) -> dict:
    return LANDING_COPY.get(lang, LANDING_COPY["en"])

def render_landing(lang: str) -> HTMLResponse:
    if lang not in SUPPORTED_LOCALES:
        raise HTTPException(404, "Unsupported language")
    html=(STATIC / "landing.html").read_text(encoding="utf-8")
    title, description = SEO_META.get(lang, SEO_META["en"])
    c=landing_copy(lang)
    replacements={
        "{{LANG}}":lang,"{{DIR}}":"rtl" if lang=="ar" else "ltr",
        "{{TITLE}}":title.replace("Chat Video Editor", "EditByChat"),"{{DESCRIPTION}}":description,
        "{{EYEBROW}}":c["eyebrow"],"{{HERO}}":c["title"],"{{SUB}}":c["sub"],
        "{{CTA}}":c["cta"],"{{SECONDARY}}":c["secondary"],"{{DEMO}}":c["demo"],
        "{{FEATURES}}":c["features"],"{{F1}}":c["f1"],"{{F1S}}":c["f1s"],
        "{{F2}}":c["f2"],"{{F2S}}":c["f2s"],"{{F3}}":c["f3"],"{{F3S}}":c["f3s"],
        "{{ALPHA}}":c["alpha"],"{{FAQ}}":c["faq"],
    }
    path="/" if lang=="en" else f"/l/{lang}"
    canonical=f"{PUBLIC_BASE_URL}{path}" if PUBLIC_BASE_URL else path
    replacements["{{CANONICAL}}"] = canonical
    replacements["{{EDITOR_URL}}"] = "/editor" if lang=="en" else f"/editor/l/{lang}"
    for k,v in replacements.items():
        html=html.replace(k,str(v))
    return HTMLResponse(html, headers={"Content-Language":lang})

@app.get("/")
def home():
    return render_landing("en")

@app.get("/l/{lang}")
def localized_home(lang: str):
    return render_landing(lang)

@app.get("/robots.txt", response_class=HTMLResponse)
def robots():
    base=PUBLIC_BASE_URL or ""
    return HTMLResponse(f"User-agent: *\nAllow: /\nDisallow: /editor\nDisallow: /api/\nSitemap: {base}/sitemap.xml\n", media_type="text/plain")

@app.get("/sitemap.xml", response_class=HTMLResponse)
def sitemap():
    base=PUBLIC_BASE_URL or ""
    urls=["/"]+[f"/l/{x}" for x in SUPPORTED_LOCALES if x!="en"]
    body="".join(f"<url><loc>{base}{u}</loc></url>" for u in urls)
    return HTMLResponse(f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>', media_type="application/xml")


@app.get("/api/status")
def status():
    return {
        "version": "1.0-alpha-gemini",
        "ai_enabled": bool(GEMINI_API_KEY),
        "ai_model": GEMINI_MODEL if GEMINI_API_KEY else None,
        "caption_model": GEMINI_TRANSCRIBE_MODEL if GEMINI_API_KEY else None,
        "locales": SUPPORTED_LOCALES,
        "features": ["trim", "cut", "aspect", "volume", "speed", "text_overlay", "captions", "image_overlay", "music", "zoom_segment", "fade", "insert_clip", "replace_segment", "undo", "multi_step", "chat_context", "revise_last_edit", "multilingual_ui", "multilingual_commands", "localized_seo", "project_layers", "named_layer_references", "restore_layer", "remove_layer", "full_stack_rerender", "project_persistence", "recent_projects", "timeline_view", "background_render_jobs", "render_progress", "caption_cache", "true_crossfade", "export_settings", "first_run_onboarding", "healthcheck"],
    }


@app.on_event("startup")
def _startup_storage_cleanup() -> None:
    cleanup_all_projects()


@app.get("/healthz")
def healthz():
    return {"ok": True, "version": "1.0-alpha-gemini"}


@app.post("/api/upload")
async def upload_video(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(400, "Missing filename")
    suffix = Path(file.filename).suffix.lower()
    if suffix not in {".mp4", ".mov", ".m4v", ".webm", ".mkv"}:
        raise HTTPException(400, "Please upload a video file")

    project_id = uuid.uuid4().hex[:12]
    project_dir = PROJECTS / project_id
    project_dir.mkdir()
    original = project_dir / f"original{suffix}"
    with original.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    if original.stat().st_size > MAX_UPLOAD_MB * 1024 * 1024:
        shutil.rmtree(project_dir, ignore_errors=True)
        raise HTTPException(413, f"Video exceeds the {MAX_UPLOAD_MB} MB upload limit")

    normalized = project_dir / "edit_000.mp4"
    try:
        has_audio = probe_has_audio(original)
        cmd = ["ffmpeg", "-y", "-i", str(original), "-c:v", "libx264", "-preset", "veryfast"]
        cmd += ["-c:a", "aac"] if has_audio else ["-an"]
        run(cmd + ["-movflags", "+faststart", str(normalized)])
    except RuntimeError as e:
        shutil.rmtree(project_dir, ignore_errors=True)
        raise HTTPException(400, f"Could not read this video: {str(e)[-500:]}")

    # The normalized H.264/AAC file becomes the project source; the raw upload is no longer needed.
    original.unlink(missing_ok=True)

    now = utc_now()
    project_name = Path(file.filename).stem[:80] or "Untitled video"
    meta = {"project_id": project_id, "name": project_name, "source_name": file.filename, "current": normalized.name, "base_render": normalized.name, "history": [normalized.name], "commands": [], "chat_log": [], "assets": [], "layers": [], "layer_snapshots": [], "last_layer_id": None, "layer_counters": {}, "schema_version": 4, "created_at": now, "updated_at": now}
    save_meta(project_dir, meta)
    return {"project_id": project_id, "name": project_name, "video_url": f"/media/{project_id}/{normalized.name}", "duration": probe_duration(normalized)}


@app.post("/api/assets/{project_id}")
async def upload_asset(project_id: str, file: UploadFile = File(...)):
    if not re.fullmatch(r"[a-f0-9]{12}", project_id):
        raise HTTPException(400, "Invalid project")
    project_dir = PROJECTS / project_id
    meta = load_meta(project_dir)
    if not file.filename:
        raise HTTPException(400, "Missing filename")
    suffix = Path(file.filename).suffix.lower()
    image_exts = {".png", ".jpg", ".jpeg", ".webp"}
    audio_exts = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}
    video_exts = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
    if suffix in image_exts:
        kind = "image"
    elif suffix in audio_exts:
        kind = "audio"
    elif suffix in video_exts:
        kind = "video"
    else:
        raise HTTPException(400, "Please upload an image/logo, audio/music, or video clip")
    asset_id = uuid.uuid4().hex[:10]
    safe_name = f"asset_{asset_id}{suffix}"
    path = project_dir / safe_name
    with path.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    asset = {"id": asset_id, "kind": kind, "filename": file.filename, "stored": safe_name}
    meta.setdefault("assets", []).append(asset)
    save_meta(project_dir, meta)
    return {"ok": True, "asset": asset}


def resolve_asset_for_edit(project_dir: Path, meta: dict, edit: dict) -> None:
    et = edit.get("type")
    kind = "image" if et == "image_overlay" else "audio" if et == "music" else "video" if et in {"insert_clip", "replace_segment"} else None
    if not kind:
        return
    matches = [a for a in meta.get("assets", []) if a.get("kind") == kind]
    if not matches:
        label = "image/logo" if kind == "image" else "music/audio" if kind == "audio" else "video clip"
        article = "an" if kind in {"image", "audio"} else "a"
        raise HTTPException(400, f"Upload {article} {label} file first, then tell me where/how to add it.")
    asset = None
    wanted = edit.get("asset_id")
    if wanted:
        asset = next((a for a in matches if a.get("id") == wanted), None)
    if asset is None:
        asset = matches[-1]
    edit["asset_id"] = asset.get("id")
    edit["asset_path"] = str(project_dir / asset["stored"])
    edit["asset_name"] = asset.get("filename", asset["stored"])



@app.get("/api/project/{project_id}/layers")
def project_layers(project_id: str):
    if not re.fullmatch(r"[a-f0-9]{12}", project_id):
        raise HTTPException(400, "Invalid project")
    project_dir = PROJECTS / project_id
    meta = load_meta(project_dir)
    ensure_layer_state(meta)
    save_meta(project_dir, meta)
    return {
        "ok": True,
        "layers": [_public_layer(x) for x in sorted(meta.get("layers", []), key=lambda z: z.get("order", 0))],
        "last_layer_id": meta.get("last_layer_id"),
    }

def record_chat(meta: dict, user_text: str, assistant_text: str) -> None:
    log = meta.setdefault("chat_log", [])
    log.append({"role": "user", "text": user_text, "at": utc_now()})
    log.append({"role": "assistant", "text": assistant_text, "at": utc_now()})
    if len(log) > 200:
        del log[:-200]


def edit_video_core(req: EditRequest):
    update_job_progress(8, "Understanding your edit")
    project_dir = PROJECTS / req.project_id
    meta = load_meta(project_dir)
    ensure_layer_state(meta)

    if is_undo_command(req.command) and _layer_family_from_command(req.command) is None:
        result = undo_project(project_dir, meta)
        result["parser"] = "layer-undo"
        return result

    current_src = current_path(project_dir, meta)
    current_duration = probe_duration(current_src)
    base_duration = probe_duration(project_dir / meta["base_render"])

    # 1) Explicit reference to any existing layer.
    update_job_progress(16, "Resolving project layers")
    layer_action = parse_layer_reference_command(req.command, meta, current_duration)
    if layer_action is None and GEMINI_API_KEY:
        layer_action = parse_layer_reference_with_gemini(req.command, meta, current_duration)
    if layer_action:
        layer = _layer_by_id(meta, layer_action.get("layer_id"))
        if not layer:
            raise HTTPException(400, "Referenced layer no longer exists")
        push_layer_snapshot(meta)
        if layer_action["action"] == "disable":
            layer["active"] = False
        elif layer_action["action"] == "enable":
            layer["active"] = True
        elif layer_action["action"] == "update":
            layer["edit"] = copy.deepcopy(layer_action["edit"])
            layer["active"] = True
        meta["last_layer_id"] = layer["id"]
        try:
            dst = rerender_layers(project_dir, meta)
        except RuntimeError as e:
            meta["layers"] = meta["layer_snapshots"].pop()
            raise HTTPException(500, f"FFmpeg error: {str(e)[-1600:]}")
        summary = layer_action.get("summary", f"Updated {layer['label']}.")
        meta.setdefault("commands", []).append({"command": req.command, "kind": f"layer-{layer_action['action']}", "layer_ids": [layer["id"]], "summary": summary})
        record_chat(meta, req.command, summary)
        save_meta(project_dir, meta)
        return {"ok": True, "message": summary, "video_url": f"/media/{req.project_id}/{dst.name}", "duration": probe_duration(dst), "parser": "layer-reference", "kind": layer_action["action"], "last_layer_id": meta.get("last_layer_id"), "layers": [_public_layer(x) for x in meta["layers"]]}

    update_job_progress(22, "Planning the edit")
    # 2) Parse a NEW edit first. This prevents e.g. "Add logo top right" from
    # being mistaken for a follow-up that moves the previous text to the top.
    plan, parser = parse_command(req.command, current_duration)
    if plan["edits"]:
        if any(e.get("type") == "captions" for e in plan["edits"]) and not GEMINI_API_KEY:
            raise HTTPException(400, "Auto captions need GEMINI_API_KEY. Other editing commands still work without it.")
        for edit in plan["edits"]:
            probe_edit = copy.deepcopy(edit)
            resolve_asset_for_edit(project_dir, meta, probe_edit)
            if probe_edit.get("asset_id"):
                edit["asset_id"] = probe_edit["asset_id"]
        push_layer_snapshot(meta)
        added = []
        for edit in plan["edits"]:
            layer = _new_layer(meta, edit, req.command)
            meta.setdefault("layers", []).append(layer)
            added.append(layer)
        if added:
            meta["last_layer_id"] = added[-1]["id"]
        try:
            dst = rerender_layers(project_dir, meta)
        except RuntimeError as e:
            meta["layers"] = meta["layer_snapshots"].pop()
            raise HTTPException(500, f"FFmpeg error: {str(e)[-1600:]}")
        meta.setdefault("commands", []).append({"command": req.command, "kind": "layer-add", "layer_ids": [x["id"] for x in added], "parser": parser, "summary": plan["summary"]})
        record_chat(meta, req.command, plan["summary"])
        save_meta(project_dir, meta)
        return {
            "ok": True, "message": plan["summary"], "video_url": f"/media/{req.project_id}/{dst.name}",
            "duration": probe_duration(dst), "parser": parser, "kind": "layer-add", "last_layer_id": meta.get("last_layer_id"),
            "edits": [copy.deepcopy(x["edit"]) for x in added], "layers": [_public_layer(x) for x in meta["layers"]],
        }

    # 3) Only when the message is not a clear new edit, treat it as a follow-up
    # to the most recently touched layer.
    target = _layer_by_id(meta, meta.get("last_layer_id"))
    if target and target.get("active", True):
        revised = _context_revision_for_layer(req.command, meta, target, base_duration)
        if revised:
            push_layer_snapshot(meta)
            target["edit"] = revised
            try:
                dst = rerender_layers(project_dir, meta)
            except RuntimeError as e:
                meta["layers"] = meta["layer_snapshots"].pop()
                raise HTTPException(500, f"FFmpeg error: {str(e)[-1600:]}")
            summary = f"Updated {target['label']}."
            meta.setdefault("commands", []).append({"command": req.command, "kind": "layer-revision", "layer_ids": [target["id"]], "summary": summary})
            record_chat(meta, req.command, summary)
            save_meta(project_dir, meta)
            return {"ok": True, "message": summary, "video_url": f"/media/{req.project_id}/{dst.name}", "duration": probe_duration(dst), "parser": "layer-context", "kind": "revision", "last_layer_id": meta.get("last_layer_id"), "layers": [_public_layer(x) for x in meta["layers"]]}

    return {
        "ok": False,
        "message": plan["summary"],
        "supported": [
            "Remove the first 4 seconds", "Make it 9:16", "Zoom 1.3x from 4 to 7 seconds",
            "Move Logo 1 left", "Remove Text 1", "Restore Zoom 1", "Make Logo 1 bigger",
            "Add text “Order Now” at the end for 3 seconds", "Add captions", "Add logo top right",
            "Add music at 20%", "Insert clip at 5 seconds with fade", "Undo that",
        ],
        "parser": parser,
    }

def undo_project(project_dir: Path, meta: dict) -> dict:
    ensure_layer_state(meta)
    snaps = meta.get("layer_snapshots", [])
    if not snaps:
        return {"ok": False, "message": "Nothing to undo."}
    previous = snaps.pop()
    meta["layers"] = previous
    active = [x for x in meta.get("layers", []) if x.get("active", True)]
    meta["last_layer_id"] = active[-1]["id"] if active else None
    try:
        dst = rerender_layers(project_dir, meta)
    except RuntimeError as e:
        raise HTTPException(500, f"FFmpeg error while undoing: {str(e)[-1200:]}")
    if meta.get("commands"):
        meta["commands"].pop()
    meta.setdefault("chat_log", []).append({"role": "assistant", "text": "Undid the last project-layer change.", "at": utc_now()})
    save_meta(project_dir, meta)
    return {
        "ok": True, "message": "Undid the last project-layer change.",
        "video_url": f"/media/{meta['project_id']}/{dst.name}", "duration": probe_duration(dst),
        "layers": [_public_layer(x) for x in meta.get("layers", [])],
    }


@app.get("/api/project/{project_id}")
def project_state(project_id: str):
    if not re.fullmatch(r"[a-f0-9]{12}", project_id):
        raise HTTPException(400, "Invalid project")
    project_dir = PROJECTS / project_id
    meta = load_meta(project_dir)
    ensure_layer_state(meta)
    current = current_path(project_dir, meta)
    return {
        "ok": True, "project_id": project_id, "name": meta.get("name") or meta.get("source_name") or "Untitled video",
        "video_url": f"/media/{project_id}/{current.name}", "duration": probe_duration(current),
        "created_at": meta.get("created_at"), "updated_at": meta.get("updated_at"),
        "layers": [_public_layer(x) for x in sorted(meta.get("layers", []), key=lambda z: z.get("order", 0))],
        "last_layer_id": meta.get("last_layer_id"), "assets": meta.get("assets", []),
        "chat_log": meta.get("chat_log", []),
    }


@app.post("/api/project/{project_id}/rename")
def rename_project(project_id: str, req: RenameProjectRequest):
    if not re.fullmatch(r"[a-f0-9]{12}", project_id):
        raise HTTPException(400, "Invalid project")
    name = re.sub(r"\s+", " ", req.name).strip()[:80]
    if not name:
        raise HTTPException(400, "Project name cannot be empty")
    project_dir = PROJECTS / project_id
    meta = load_meta(project_dir)
    meta["name"] = name
    save_meta(project_dir, meta)
    return {"ok": True, "name": name}


def _run_edit_job(job_id: str, req: EditRequest) -> None:
    JOB_CONTEXT.job_id = job_id
    with JOB_LOCK:
        if job_id in JOBS:
            JOBS[job_id]["status"] = "running"
    update_job_progress(3, "Starting render")
    lock = _project_lock(req.project_id)
    try:
        with lock:
            result = edit_video_core(req)
        update_job_progress(96, "Finalizing preview")
        with JOB_LOCK:
            JOBS[job_id].update({"status": "done", "progress": 100, "stage": "Done", "result": result, "updated_at": utc_now()})
    except HTTPException as exc:
        with JOB_LOCK:
            JOBS[job_id].update({"status": "error", "progress": 100, "stage": "Error", "error": _friendly_media_error(exc.detail) if "ffmpeg" in str(exc.detail).lower() or "conversion failed" in str(exc.detail).lower() else str(exc.detail), "updated_at": utc_now()})
    except Exception as exc:
        with JOB_LOCK:
            JOBS[job_id].update({"status": "error", "progress": 100, "stage": "Error", "error": _friendly_media_error(exc), "updated_at": utc_now()})
    finally:
        JOB_CONTEXT.job_id = None


@app.post("/api/edit")
def edit_video(req: EditRequest):
    # Synchronous compatibility endpoint for scripts/tests.
    return edit_video_core(req)


@app.post("/api/edit/start")
def start_edit_job(req: EditRequest):
    if not re.fullmatch(r"[a-f0-9]{12}", req.project_id):
        raise HTTPException(400, "Invalid project")
    if not (PROJECTS / req.project_id / "project.json").exists():
        raise HTTPException(404, "Project not found")
    job_id = uuid.uuid4().hex[:16]
    now = utc_now()
    with JOB_LOCK:
        JOBS[job_id] = {"job_id": job_id, "project_id": req.project_id, "status": "queued", "progress": 0, "stage": "Queued", "created_at": now, "updated_at": now}
    threading.Thread(target=_run_edit_job, args=(job_id, req), daemon=True).start()
    return {"ok": True, "job_id": job_id}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    with JOB_LOCK:
        job = copy.deepcopy(JOBS.get(job_id))
    if not job:
        raise HTTPException(404, "Render job not found")
    return job


def _run_export_job(job_id: str, req: ExportRequest) -> None:
    JOB_CONTEXT.job_id = job_id
    with JOB_LOCK:
        if job_id in JOBS:
            JOBS[job_id]["status"] = "running"
    update_job_progress(5, "Preparing export")
    lock = _project_lock(req.project_id)
    try:
        with lock:
            project_dir = PROJECTS / req.project_id
            meta = load_meta(project_dir)
            out = export_project_file(project_dir, meta, req)
        result = {
            "ok": True,
            "download_url": f"/media/{req.project_id}/{out.name}",
            "filename": f"{(meta.get('name') or 'video')[:60]}.mp4",
            "resolution": req.resolution, "fps": req.fps, "quality": req.quality,
        }
        with JOB_LOCK:
            JOBS[job_id].update({"status":"done","progress":100,"stage":"Done","result":result,"updated_at":utc_now()})
    except HTTPException as exc:
        with JOB_LOCK:
            JOBS[job_id].update({"status":"error","progress":100,"stage":"Error","error":str(exc.detail),"updated_at":utc_now()})
    except Exception as exc:
        with JOB_LOCK:
            JOBS[job_id].update({"status":"error","progress":100,"stage":"Error","error":str(exc)[-1800:],"updated_at":utc_now()})
    finally:
        JOB_CONTEXT.job_id = None


@app.post("/api/export/start")
def start_export_job(req: ExportRequest):
    if not re.fullmatch(r"[a-f0-9]{12}", req.project_id):
        raise HTTPException(400, "Invalid project")
    if not (PROJECTS / req.project_id / "project.json").exists():
        raise HTTPException(404, "Project not found")
    if req.resolution not in {"source", "720p", "1080p"}:
        raise HTTPException(400, "Unsupported resolution")
    if req.fps not in {"source", "30", "60"}:
        raise HTTPException(400, "Unsupported frame rate")
    if req.quality not in {"draft", "balanced", "high"}:
        raise HTTPException(400, "Unsupported quality")
    job_id = uuid.uuid4().hex[:16]
    now = utc_now()
    with JOB_LOCK:
        JOBS[job_id] = {"job_id":job_id,"project_id":req.project_id,"status":"queued","progress":0,"stage":"Queued","created_at":now,"updated_at":now,"kind":"export"}
    threading.Thread(target=_run_export_job, args=(job_id, req), daemon=True).start()
    return {"ok": True, "job_id": job_id}


@app.post("/api/undo")
def undo(req: ProjectRequest):
    project_dir = PROJECTS / req.project_id
    meta = load_meta(project_dir)
    return undo_project(project_dir, meta)


@app.get("/media/{project_id}/{filename}")
def media(project_id: str, filename: str):
    if not re.fullmatch(r"[a-f0-9]{12}", project_id):
        raise HTTPException(400, "Invalid project")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", filename):
        raise HTTPException(400, "Invalid filename")
    path = PROJECTS / project_id / filename
    if not path.exists():
        raise HTTPException(404, "File not found")
    return FileResponse(path, media_type="video/mp4", filename=filename)
