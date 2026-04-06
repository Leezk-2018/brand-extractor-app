import datetime
import json
import shutil
import uuid
from pathlib import Path
from typing import Any

import pandas as pd

HISTORY_DIR = Path(__file__).with_name("history")
INDEX_PATH = HISTORY_DIR / "index.json"


def create_run_id(now: datetime.datetime | None = None) -> str:
    ts = (now or datetime.datetime.now()).strftime("%Y%m%d_%H%M%S")
    return f"{ts}_{uuid.uuid4().hex[:6]}"


def list_history_entries() -> list[dict[str, Any]]:
    return _load_index()


def load_history_detail(run_id: str) -> dict[str, Any] | None:
    run_dir = HISTORY_DIR / str(run_id)
    meta_path = run_dir / "meta.json"
    if not meta_path.exists():
        return None
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    return {
        "meta": meta,
        "run_dir": str(run_dir),
        "meta_path": str(meta_path),
        "csv_path": str(run_dir / "results.csv"),
        "log_path": str(run_dir / "debug.log"),
    }


def save_run_history(run_state: dict[str, Any], logs: list[Any]) -> dict[str, Any]:
    run_id = str(run_state.get("run_id") or "").strip()
    if not run_id:
        raise ValueError("run_state missing run_id")

    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    run_dir = HISTORY_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    csv_path = run_dir / "results.csv"
    log_path = run_dir / "debug.log"
    meta_path = run_dir / "meta.json"

    results = run_state.get("results") or []
    pd.DataFrame(results).to_csv(csv_path, index=False, encoding="utf-8-sig")
    log_path.write_text(_build_log_text(logs), encoding="utf-8")

    meta = _build_run_meta(run_state, run_id=run_id, csv_path=csv_path, log_path=log_path)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    index = [item for item in _load_index() if item.get("run_id") != run_id]
    index.insert(0, _build_index_entry(meta))
    _save_index(index)
    return meta


def delete_history(run_id: str) -> None:
    run_id = str(run_id).strip()
    if not run_id:
        return
    run_dir = HISTORY_DIR / run_id
    if run_dir.exists():
        shutil.rmtree(run_dir)
    index = [item for item in _load_index() if item.get("run_id") != run_id]
    _save_index(index)


def _build_run_meta(
    run_state: dict[str, Any],
    *,
    run_id: str,
    csv_path: Path,
    log_path: Path,
) -> dict[str, Any]:
    stats = dict(run_state.get("stats") or {})
    meta = dict(run_state.get("meta") or {})
    safe_meta = _sanitize_meta(meta)
    return {
        "run_id": run_id,
        "status": str(run_state.get("status") or "idle"),
        "started_at": run_state.get("started_at") or "",
        "finished_at": run_state.get("finished_at") or "",
        "quota_units": int(run_state.get("quota_units") or 0),
        "last_error": str(run_state.get("last_error") or ""),
        "form_data": safe_meta,
        "stats": {
            "total_kols": int(stats.get("total_kols") or 0),
            "processed_kols": int(stats.get("processed_kols") or 0),
            "resolved_kols": int(stats.get("resolved_kols") or 0),
            "skipped_kols": int(stats.get("skipped_kols") or 0),
            "error_kols": int(stats.get("error_kols") or 0),
            "candidate_videos": int(stats.get("candidate_videos") or 0),
            "matched_rows": int(stats.get("matched_rows") or 0),
        },
        "files": {
            "csv_file": _relative_path(csv_path),
            "log_file": _relative_path(log_path),
        },
    }


def _build_index_entry(meta: dict[str, Any]) -> dict[str, Any]:
    stats = meta.get("stats") or {}
    form_data = meta.get("form_data") or {}
    files = meta.get("files") or {}
    return {
        "run_id": meta.get("run_id", ""),
        "started_at": meta.get("started_at", ""),
        "finished_at": meta.get("finished_at", ""),
        "status": meta.get("status", "idle"),
        "search_query": form_data.get("search_query", ""),
        "total_kols": int(stats.get("total_kols") or 0),
        "matched_rows": int(stats.get("matched_rows") or 0),
        "quota_units": int(meta.get("quota_units") or 0),
        "csv_file": files.get("csv_file", ""),
        "log_file": files.get("log_file", ""),
    }


def _sanitize_meta(meta: dict[str, Any]) -> dict[str, Any]:
    allowed_keys = [
        "search_query",
        "published_after",
        "use_date_filter",
        "start_date",
        "brand_count",
        "brands_list",
        "kol_list",
        "enable_full_search",
        "enable_deep_search",
        "match_title",
        "match_description",
        "match_tags",
    ]
    safe_meta = {key: meta.get(key) for key in allowed_keys}
    safe_meta["brands_list"] = list(meta.get("brands_list") or [])
    safe_meta["kol_list"] = list(meta.get("kol_list") or [])
    return safe_meta


def _build_log_text(logs: list[Any]) -> str:
    lines: list[str] = []
    for raw in logs or []:
        if isinstance(raw, dict):
            lines.append(str(raw.get("entry", "")))
        else:
            lines.append(str(raw))
    return "\n".join(lines)


def _relative_path(path: Path) -> str:
    try:
        return path.relative_to(Path(__file__).resolve().parent).as_posix()
    except ValueError:
        return path.as_posix()


def _load_index() -> list[dict[str, Any]]:
    if not INDEX_PATH.exists():
        return []
    try:
        payload = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    entries = [item for item in payload if isinstance(item, dict)]
    entries.sort(key=lambda item: str(item.get("started_at") or ""), reverse=True)
    return entries


def _save_index(entries: list[dict[str, Any]]) -> None:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
