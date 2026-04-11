import streamlit as st
import pandas as pd
import datetime
import json
import logging
import traceback
from pathlib import Path

from app_ui import (
    render_last_extract_results,
    render_main_inputs,
    render_quota_warning,
    render_sidebar,
    render_summary_panel,
    update_summary_panel,
    _format_run_status,
)
from brand_rules import build_rules_from_payload
from extractor_core import (
    build_published_after,
    YouTubeManager,
    load_selected_brand_rules,
    search_channel_brand_mentions,
)
from history_store import delete_history, list_history_entries, load_history_detail, save_run_history, create_run_id

_LD = logging.getLogger("lee_debug")
if not _LD.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    _LD.addHandler(_h)
    _LD.setLevel(logging.INFO)
    _LD.propagate = False

_LEE_DEBUG_MAX_LINES = 4000
# 仅在本轮执行期间指向 expander 内的 st.empty，仅刷新运行状态（勿存 session_state）
_LIVE_LOG_EMPTY = None


def _log_detail(msg: str, exc_info: bool = False, level: str = "INFO") -> None:
    """完整调试日志：仅写入终端 + 弹窗用列表，不刷新页面中间区域。"""
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    level_name = level.upper()
    body = "lee-debug " + msg
    if exc_info:
        tb = traceback.format_exc()
        if tb.strip():
            body = body + "\n" + tb
    log_method = getattr(_LD, level_name.lower(), _LD.info)
    log_method("lee-debug " + msg, exc_info=exc_info)
    buf = st.session_state.setdefault("lee_debug_logs", [])
    buf.append(
        {
            "time": ts,
            "level": level_name,
            "entry": f"{ts} {body}",
        }
    )
    if len(buf) > _LEE_DEBUG_MAX_LINES:
        del buf[: len(buf) - _LEE_DEBUG_MAX_LINES]


def _new_run_state() -> dict:
    return {
        "status": "idle",
        "meta": {},
        "current": {},
        "stats": {
            "total_kols": 0,
            "processed_kols": 0,
            "resolved_kols": 0,
            "skipped_kols": 0,
            "error_kols": 0,
            "candidate_videos": 0,
            "matched_rows": 0,
        },
        "events": [],
        "kols": [],
        "results": [],
        "result_urls": [],
        "next_kol_index": 0,
        "last_error": "",
        "run_id": "",
        "started_at": "",
        "finished_at": "",
        "quota_units": 0,
    }


def _run_state() -> dict:
    state = st.session_state.get("current_run_state")
    if not isinstance(state, dict):
        state = _new_run_state()
        st.session_state["current_run_state"] = state
    return state


def _refresh_summary() -> None:
    if _LIVE_LOG_EMPTY is not None:
        update_summary_panel(_LIVE_LOG_EMPTY, _run_state())


def _set_run_status(status: str) -> None:
    _run_state()["status"] = status
    _refresh_summary()


def _run_reset(**meta) -> None:
    state = _new_run_state()
    state["status"] = "running"
    state["meta"] = meta
    state["run_id"] = create_run_id()
    state["started_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    state["quota_units"] = 0
    state["stats"]["total_kols"] = meta.get("total_kols", 0)
    state["kols"] = [
        {
            "kol": kol,
            "status": "pending",
            "candidate_count": 0,
            "matched_count": 0,
            "message": "",
        }
        for kol in meta.get("kol_list", [])
    ]
    st.session_state["current_run_state"] = state
    st.session_state["last_extract_results"] = []
    _refresh_summary()


def _run_event(message: str, level: str = "info") -> None:
    state = _run_state()
    events = state.setdefault("events", [])
    events.append(
        {
            "time": datetime.datetime.now().strftime("%H:%M:%S"),
            "level": level,
            "message": message,
        }
    )
    if len(events) > 80:
        del events[: len(events) - 80]
    _refresh_summary()


def _run_current(index: int, kol: str, stage: str) -> None:
    state = _run_state()
    state["current"] = {"index": index, "kol": kol, "stage": stage}
    _refresh_summary()


def _run_stage_pagination(index: int, kol: str, page_number: int, total_items: int, has_next: bool) -> None:
    suffix = "（还有下一页）" if has_next else "（最后一页）"
    _run_current(index, kol, f"搜索视频，第 {page_number} 页，已拿到 {total_items} 条 {suffix}")


def _run_add_stats(**updates) -> None:
    stats = _run_state().setdefault("stats", {})
    for key, value in updates.items():
        stats[key] = stats.get(key, 0) + value
    _refresh_summary()


def _run_set_kol(index: int, **updates) -> None:
    state = _run_state()
    if 0 <= index < len(state.get("kols", [])):
        state["kols"][index].update(updates)
    _refresh_summary()


def _run_append_results(rows: list[dict]) -> int:
    if not rows:
        return 0
    state = _run_state()
    result_urls = set(state.setdefault("result_urls", []))
    results = state.setdefault("results", [])
    added = 0
    for row in rows:
        video_url = row.get("视频链接", "")
        if video_url and video_url in result_urls:
            continue
        results.append(row)
        if video_url:
            result_urls.add(video_url)
        added += 1
    state["result_urls"] = list(result_urls)
    st.session_state["last_extract_results"] = list(results)
    _refresh_summary()
    return added


def _run_mark_paused(message: str) -> None:
    state = _run_state()
    state["status"] = "paused"
    state["last_error"] = message
    _run_event(message, level="error")


def _run_add_quota(api_name: str, units: int, context: dict | None = None) -> None:
    state = _run_state()
    state["quota_units"] = int(state.get("quota_units") or 0) + int(units)
    _log_detail(f"quota +{units} via {api_name}: total={state['quota_units']} context={json.dumps(context or {}, ensure_ascii=False)}")
    _refresh_summary()


def _finalize_history_snapshot() -> None:
    state = _run_state()
    if not state.get("run_id"):
        return
    if state.get("history_saved"):
        return
    if not state.get("started_at"):
        state["started_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    state["finished_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    meta = save_run_history(state, st.session_state.get("lee_debug_logs", []))
    state["history_saved"] = True
    st.session_state["last_saved_history_run_id"] = meta.get("run_id")


def _log_summary(msg: str) -> None:
    _run_event(msg)


def _mask_api_key(key: str) -> str:
    if not key:
        return "(empty)"
    if len(key) <= 8:
        return "***"
    return key[:4] + "..." + key[-4:]


def _log_detail_json(label: str, obj) -> None:
    try:
        s = json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        s = repr(obj)
    _log_detail(f"{label}: {s}", level="INFO")


def _try_parse_json_suffix_in_log_entry(entry: str) -> tuple[str | None, object | None]:
    """
    单条日志若从首个 {{ 或 [ 起可解析为完整 JSON，则返回 (前缀, 对象)；否则 (None, None)。
    JSON 后若还有非空白内容（如 Traceback），视为非纯 JSON 条目。
    """
    s = entry.rstrip("\n\r")
    if not s:
        return None, None
    idx = -1
    for ch in ("{", "["):
        p = s.find(ch)
        if p != -1 and (idx == -1 or p < idx):
            idx = p
    if idx == -1:
        return None, None
    try:
        dec = json.JSONDecoder()
        obj, end = dec.raw_decode(s, idx)
        if s[end:].strip():
            return None, None
        prefix = s[:idx].rstrip()
        return prefix, obj
    except json.JSONDecodeError:
        return None, None


def _log_entry_text(raw) -> str:
    if isinstance(raw, dict):
        return str(raw.get("entry", ""))
    return str(raw)


# 标准频道 ID：UC + 22 位（与 Data API channelId 一致）；避免把纯 handle 当成 ID
st.set_page_config(page_title="YouTube Brand Extractor", layout="wide")
st.session_state.setdefault("lee_debug_logs", [])
if not isinstance(st.session_state.get("current_run_state"), dict):
    st.session_state["current_run_state"] = _new_run_state()

# Streamlit 的 data-testid="stDialog" 在 Base Web Modal 里是「整屏遮罩 Root」，
# 若对其设置 width/max-width（如之前的 112rem），遮罩会变窄导致右侧露底、面板偏左。
# 这里只保证 Root 铺满视口，并加宽真正的对话框面板 [role="dialog"]。
st.markdown(
    """
<style>
/* 1. 缩减主内容区域顶部的巨大空白 */
.block-container {
    padding-top: 1.5rem !important;
    padding-bottom: 2rem !important;
    padding-left: 2rem !important;
    padding-right: 2rem !important;
}

/* 2. 压低主标题和副标题的间距 */
[data-testid="stHeader"] {
    background: rgba(0,0,0,0) !important;
}
h1 {
    margin-top: -1rem !important;
    padding-top: 0 !important;
    font-size: 2.2rem !important;
}
.stMarkdown p {
    margin-bottom: 0.5rem !important;
}

/* 3. 压低 Subheader 的外边距，让它贴近下方的卡片 */
h3 {
    margin-bottom: 0.2rem !important;
    margin-top: 0.8rem !important;
}

div[data-testid="stDialog"] {
  width: 100vw !important;
  max-width: 100vw !important;
  min-width: 100vw !important;
  left: 0 !important;
  right: 0 !important;
  top: 0 !important;
  bottom: 0 !important;
  margin: 0 !important;
  box-sizing: border-box !important;
}
div[data-testid="stDialog"] > div {
  width: 100% !important;
  max-width: 100% !important;
  display: flex !important;
  justify-content: center !important;
  align-items: flex-start !important;
  box-sizing: border-box !important;
}
div[data-testid="stDialog"] [role="dialog"] {
  width: 88vw !important;
  max-width: 88vw !important;
  height: 86vh !important;
  max-height: 86vh !important;
  margin-left: auto !important;
  margin-right: auto !important;
  box-sizing: border-box !important;
  overflow-y: auto !important;
  overflow-x: hidden !important;
  border-radius: 18px !important;
  padding: 0.75rem 0.9rem 1rem 0.9rem !important;
  background: linear-gradient(180deg, rgba(248,250,252,0.98), rgba(255,255,255,0.98)) !important;
}
div[data-testid="stDialog"] [data-testid="stDialogHeader"] {
  position: sticky !important;
  top: 0 !important;
  z-index: 30 !important;
  background: linear-gradient(180deg, rgba(248,250,252,0.99), rgba(248,250,252,0.97)) !important;
  border-bottom: 1px solid rgba(15,23,42,.08) !important;
}
div[data-testid="stDialog"] [role="dialog"] [data-testid="stVerticalBlockBorderWrapper"] {
  border-radius: 14px !important;
}
div[data-testid="stDialog"] [role="dialog"] p {
  margin: 0.12rem 0 !important;
  line-height: 1.4 !important;
}
div[data-testid="stDialog"] [role="dialog"] pre {
  margin: 0.15rem 0 !important;
  border-radius: 12px !important;
  border: 1px solid rgba(15,23,42,.08) !important;
  background: #f8fafc !important;
  color: #0f172a !important;
}
div[data-testid="stDialog"] [role="dialog"] pre code {
  color: #0f172a !important;
}
div[data-testid="stDialog"] [role="dialog"] [data-testid="stTextInput"] input,
div[data-testid="stDialog"] [role="dialog"] [data-testid="stSelectbox"] div[data-baseweb="select"] > div {
  min-height: 2.35rem !important;
}
div[data-testid="stDialog"] [role="dialog"] [data-testid="stJson"] {
  margin: 0.1rem 0 0.25rem 0 !important;
  border-radius: 12px !important;
  border: 1px solid rgba(15,23,42,.08) !important;
  overflow: hidden !important;
}
</style>
""",
    unsafe_allow_html=True,
)


@st.dialog("历史记录", width="large")
def _history_dialog():
    st.markdown(
        """
        <style>
        .history-list-container {
            max-height: 70vh;
            overflow-y: auto;
            padding-right: 0.6rem;
        }
        .history-card {
            padding: 0.6rem 0.8rem;
            border-radius: 8px;
            border: 1px solid rgba(15, 23, 42, 0.08);
            margin-bottom: 0.5rem;
            transition: all 0.2s;
            cursor: pointer;
            position: relative;
        }
        .history-card:hover {
            border-color: #0072ff;
            background: rgba(0, 114, 255, 0.02);
        }
        .history-card-selected {
            border-left: 5px solid #0072ff !important;
            background: rgba(0, 114, 255, 0.05) !important;
            border-color: rgba(0, 114, 255, 0.2);
        }
        .history-detail-container {
            max-height: 70vh;
            overflow-y: auto;
            padding-left: 1rem;
            border-left: 1px solid rgba(15, 23, 42, 0.08);
        }
        /* 紧凑 Metric 样式 */
        [data-testid="stMetricValue"] {
            font-size: 1.6rem !important;
        }
        </style>
        """,
        unsafe_allow_html=True
    )

    entries = list_history_entries()
    st.session_state.setdefault("history_selected_run_id", "")

    if not entries:
        st.info("暂无历史记录。")
        return

    # 默认选中第一条
    if not st.session_state["history_selected_run_id"] and entries:
        st.session_state["history_selected_run_id"] = str(entries[0].get("run_id", ""))

    list_col, detail_col = st.columns([1, 2.2])

    with list_col:
        st.markdown('<div class="history-list-container">', unsafe_allow_html=True)
        for item in entries:
            run_id = str(item.get("run_id", ""))
            is_selected = run_id == st.session_state.get("history_selected_run_id")
            
            status = item.get("status", "idle")
            status_icon = "✅" if status == "completed" else "⏳" if status == "running" else "⏸️" if status == "paused" else "❌"
            
            card_class = "history-card history-card-selected" if is_selected else "history-card"
            
            # 使用 Streamlit 容器模拟卡片
            with st.container():
                st.markdown(
                    f"""
                    <div class="{card_class}">
                        <div style="font-weight:600; font-size:0.9rem; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; line-height:1.4;">
                            {status_icon} {item.get('started_at', '-')[5:16]} | 👥 {item.get('total_kols', 0)} | 🔍 {item.get('search_query', '-')}
                        </div>
                    </div>
                    """, 
                    unsafe_allow_html=True
                )
                
                # 透明层上覆盖实际按钮
                c1, c2 = st.columns([4, 1])
                if c1.button("查看", key=f"view_{run_id}", use_container_width=True, type="primary" if is_selected else "secondary"):
                    st.session_state["history_selected_run_id"] = run_id
                    st.session_state["active_dialog"] = "history"
                    st.rerun()
                if c2.button("🗑️", key=f"del_{run_id}", use_container_width=True, help="删除此记录"):
                    delete_history(run_id)
                    if st.session_state.get("history_selected_run_id") == run_id:
                        st.session_state["history_selected_run_id"] = ""
                    st.session_state["active_dialog"] = "history"
                    st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

    with detail_col:
        st.markdown('<div class="history-detail-container">', unsafe_allow_html=True)
        selected_run_id = st.session_state.get("history_selected_run_id", "")
        if not selected_run_id:
            st.info("👈 请在左侧列表中选择一条历史记录以查看详情。")
        else:
            detail = load_history_detail(selected_run_id)
            if not detail:
                st.warning("该历史记录不存在或已被删除。")
            else:
                meta = detail.get("meta") or {}
                form_data = meta.get("form_data") or {}
                stats = meta.get("stats") or {}
                files = meta.get("files") or {}

                st.markdown(f"### 任务详情 `{meta.get('run_id', '-')[:15]}...`")
                
                tab_obs, tab_cfg, tab_log = st.tabs(["📊 结果概览", "⚙️ 原始配置", "📄 运行日志"])
                
                with tab_obs:
                    m1, m2, m3, m4 = st.columns(4)
                    m1.metric("状态", _format_run_status(meta.get("status", "-")))
                    m2.metric("额度消耗", int(meta.get("quota_units") or 0))
                    m3.metric("KOL 总数", int(stats.get("total_kols") or 0))
                    m4.metric("匹配结果", int(stats.get("matched_rows") or 0))
                    
                    st.divider()
                    
                    col_info, col_dl = st.columns([2, 1])
                    with col_info:
                        st.markdown(f"**搜索关键词**: `{form_data.get('search_query', '-')}`")
                        st.markdown(f"**品牌数量**: `{len(form_data.get('brands_list', []))}`")
                        st.caption(f"开始：{meta.get('started_at', '-')} | 结束：{meta.get('finished_at', '-')}")
                    
                    with col_dl:
                        csv_path = Path(__file__).resolve().parent / str(files.get("csv_file") or "")
                        if csv_path.exists():
                            st.download_button(
                                label="💾 下载结果 (CSV)",
                                data=csv_path.read_bytes(),
                                file_name=csv_path.name,
                                mime="text/csv",
                                use_container_width=True,
                                type="primary",
                                key=f"dl_csv_{selected_run_id}",
                            )
                        else:
                            st.error("CSV 文件已丢失")

                with tab_cfg:
                    st.markdown("**表单提交数据**")
                    st.json(form_data, expanded=False)
                    st.markdown("**详细运行统计**")
                    st.json(stats, expanded=True)

                with tab_log:
                    log_path = Path(__file__).resolve().parent / str(files.get("log_file") or "")
                    if log_path.exists():
                        log_text = log_path.read_text(encoding="utf-8")
                        st.download_button(
                            label="💾 下载完整日志",
                            data=log_text.encode("utf-8"),
                            file_name=log_path.name,
                            mime="text/plain",
                            use_container_width=True,
                            key=f"dl_log_{selected_run_id}",
                        )
                        st.markdown("**最近日志预览 (100行)**")
                        # 仅展示最后一部分日志，避免撑破页面
                        log_lines = log_text.splitlines()
                        preview_text = "\n".join(log_lines[-100:]) if len(log_lines) > 100 else log_text
                        st.code(preview_text, language=None)
                    else:
                        st.caption("日志文件不存在。")
        st.markdown('</div>', unsafe_allow_html=True)


@st.dialog("运行日志控制台", width="large")
def _log_detail_dialog():
    # 1. 注入终端风格 CSS
    st.markdown(
        """
        <style>
        /* 限制弹窗宽度 */
        div[role="dialog"] {
            max-width: 950px !important;
            margin: auto !important;
        }
        /* 紧凑统计行 */
        .log-stats-bar {
            display: flex;
            gap: 1.2rem;
            padding: 0.5rem 1rem;
            background: #f1f5f9;
            border: 1px solid #cbd5e1;
            border-radius: 8px;
            color: #334155;
            font-size: 0.85rem;
            margin-bottom: 1rem;
        }
        .stat-item { display: flex; gap: 0.4rem; align-items: center; }
        .stat-label { color: #475569; }
        .stat-value { font-weight: 800; color: #000000; }
        .stat-err { color: #dc2626 !important; }

        /* 日志条目容器 - 极致压缩 */
        .log-entry-row {
            padding: 0.2rem 0.5rem !important;
            margin-bottom: 0.15rem !important;
            border-radius: 3px !important;
            background: #ffffff !important;
            border: 1px solid #e5e7eb !important;
            display: flex;
            align-items: center;
            min-height: 1.8rem;
        }
        .log-ts { 
            color: #374151; 
            font-weight: 600; 
            font-size: 1rem; 
            font-family: 'Consolas', monospace; 
            margin-right: 0.8rem;
            white-space: nowrap;
        }
        .log-lvl { 
            font-weight: 900; 
            font-size: 0.8rem; 
            padding: 0rem 0.3rem; 
            border-radius: 2px; 
            margin-right: 0.8rem; 
            text-transform: uppercase;
            line-height: 1.2;
        }
        .lvl-info { background: #409EFF; color: #ffffff; }
        .lvl-warn { background: #E6A23C; color: #ffffff; }
        .lvl-error { background: #b91c1c; color: #ffffff; }
        
        /* 核心日志文字 - 柔和深灰 */
        .log-txt { 
            color: #374151 !important; 
            font-size: 1rem !important; 
            font-weight: 400 !important; 
            font-family: "PingFang SC", sans-serif;
            word-break: break-all;
        }
        
        /* 紧凑化 Streamlit 内置组件 */
        [data-testid="stExpander"] {
            border: none !important;
            background: transparent !important;
        }
        [data-testid="stExpander"] section {
            padding: 0 !important;
        }
        </style>
        """,
        unsafe_allow_html=True
    )

    logs = st.session_state.get("lee_debug_logs", [])
    parsed_logs = []
    json_count = 0
    level_counts = {"INFO": 0, "WARN": 0, "ERROR": 0}
    
    for i, raw in enumerate(logs):
        entry = _log_entry_text(raw)
        level = str(raw.get("level", "INFO")).upper() if isinstance(raw, dict) else ("ERROR" if "Traceback" in entry else "INFO")
        prefix, j = _try_parse_json_suffix_in_log_entry(entry)
        is_json = j is not None
        if is_json: json_count += 1
        level_counts[level] = level_counts.get(level, 0) + 1
        parsed_logs.append({
            "index": i + 1, "entry": entry, "prefix": prefix, "json": j,
            "is_json": is_json, "level": level
        })

    # 2. 统计行 (Markdown HTML)
    st.markdown(
        f"""
        <div class="log-stats-bar">
            <div class="stat-item"><span class="stat-label">Total:</span><span class="stat-value">{len(parsed_logs)}</span></div>
            <div class="stat-item"><span class="stat-label">JSON:</span><span class="stat-value">{json_count}</span></div>
            <div class="stat-item"><span class="stat-label">Text:</span><span class="stat-value">{len(parsed_logs) - json_count}</span></div>
            <div class="stat-item"><span class="stat-label">Errors:</span><span class="stat-value stat-err">{level_counts.get("ERROR", 0)}</span></div>
        </div>
        """,
        unsafe_allow_html=True
    )

    # 3. 筛选器行
    f1, f2, f3, f4 = st.columns([2.5, 1, 1, 0.8])
    keyword = f1.text_input("搜索", placeholder="过滤日志...", label_visibility="collapsed")
    view_mode = f2.selectbox("展示", ["全部", "JSON", "文本"], label_visibility="collapsed")
    level_mode = f3.selectbox("等级", ["全部", "INFO", "WARN", "ERROR"], label_visibility="collapsed")
    newest_first = f4.toggle("最新", value=True)

    # 4. 操作行
    a1, a2, a3 = st.columns([1, 1, 3])
    with a1:
        if st.button("🗑️ 清空", use_container_width=True):
            st.session_state["lee_debug_logs"] = []
            st.session_state["active_dialog"] = "logs"
            st.rerun()
    with a2:
        payload = ("\n".join(_log_entry_text(item) for item in logs) if logs else "").encode("utf-8")
        st.download_button("💾 下载", data=payload, file_name="debug.log", mime="text/plain", use_container_width=True)

    # 5. 日志列表渲染
    filtered_logs = parsed_logs
    if view_mode == "JSON": filtered_logs = [it for it in filtered_logs if it["is_json"]]
    elif view_mode == "文本": filtered_logs = [it for it in filtered_logs if not it["is_json"]]
    if level_mode != "全部": filtered_logs = [it for it in filtered_logs if it["level"] == level_mode]
    if keyword.strip():
        needle = keyword.strip().lower()
        filtered_logs = [it for it in filtered_logs if needle in it["entry"].lower()]

    if newest_first: filtered_logs = list(reversed(filtered_logs))

    st.divider()
    
    for item in filtered_logs:
        lvl_class = f"lvl-{item['level'].lower()}"
        ts = item['entry'][:19] if len(item['entry']) >= 19 else ""
        raw_content = item['entry'][20:] if len(item['entry']) > 20 else item['entry']
        
        display_content = raw_content

        with st.container():
            st.markdown(
                f"""
                <div class="log-entry-row">
                    <span class="log-ts">{ts}</span>
                    <span class="log-lvl {lvl_class}">{item['level']}</span>
                    <span class="log-txt">{item['prefix'] if item['is_json'] else display_content}</span>
                </div>
                """,
                unsafe_allow_html=True
            )
            if item["is_json"]:
                with st.expander("查看 JSON 数据", expanded=False):
                    st.json(item["json"], expanded=1)

st.markdown(
    """
    <h1 style='display: flex; align-items: center; gap: 0.8rem; margin-bottom: 0;'>
        YouTube 品牌提取助手
        <span style='
            background: linear-gradient(135deg, #00c6ff 0%, #0072ff 100%);
            color: white;
            font-size: 1.05rem;
            padding: 0.3rem 0.8rem;
            border-radius: 20px;
            font-weight: 800;
            text-transform: uppercase;
            letter-spacing: 0.8px;
            height: fit-content;
            margin-top: 0.3rem;
            box-shadow: 0 4px 15px rgba(0, 114, 255, 0.35);
        '>BETA</span>
    </h1>
    """,
    unsafe_allow_html=True
)
st.markdown("批量扫描 YouTube 频道内容，找出视频中提到的品牌，并导出结果。")

api_key, search_query, use_date_filter, start_date, brands_list, brand_rules_payload, brand_rules_error, enable_full_search, enable_deep_search, match_title, match_description, match_tags = render_sidebar(
    log_count=len(st.session_state.get("lee_debug_logs", [])),
    history_count=len(list_history_entries()),
    open_log_dialog=_log_detail_dialog,
    open_history_dialog=_history_dialog,
)
kol_list = render_main_inputs()

# --- 核心功能函数 ---
@st.cache_resource(show_spinner=False)
def get_youtube_service(api_key):
    return build_youtube_service(api_key, logger=_LD)


# --- 按钮操作区 ---

run_state = _run_state()
can_resume = run_state.get("status") == "paused" and run_state.get("next_kol_index", 0) < run_state.get("stats", {}).get("total_kols", 0)

btn_c1, btn_c2, btn_spacer = st.columns([1.2, 1.2, 2.5])
start_new_run = btn_c1.button("🚀 开始提取", type="primary", use_container_width=True)
resume_run = btn_c2.button("⏯️ 继续上次任务", disabled=not can_resume, use_container_width=True)

# 提示配额
render_quota_warning(len(kol_list))

# 监控面板
summary_display = render_summary_panel()
update_summary_panel(summary_display, _run_state())

# --- 执行逻辑 ---
if start_new_run or resume_run:
    try:
        _LIVE_LOG_EMPTY = summary_display
        resume_mode = bool(resume_run and can_resume)

        if resume_mode:
            state = _run_state()
            state["status"] = "running"
            state["last_error"] = ""
            _run_event("继续上次任务")
            _log_detail(
                f"run resume: next_kol_index={state.get('next_kol_index', 0)} total_kols={state.get('stats', {}).get('total_kols', 0)}"
            )
        else:
            _run_reset(
                total_kols=len(kol_list),
                brand_count=len(brands_list),
                search_query=search_query,
                published_after=build_published_after(start_date) or "off",
                kol_list=kol_list,
                brands_list=brands_list,
                brand_rules_payload=brand_rules_payload,
                use_date_filter=use_date_filter,
                start_date=str(start_date) if start_date else None,
                api_key=api_key,
                enable_full_search=enable_full_search,
                enable_deep_search=enable_deep_search,
                match_title=match_title,
                match_description=match_description,
                match_tags=match_tags,
            )
            _run_event(f"任务开始 | KOL {len(kol_list)} | 品牌 {len(brands_list)}")
            _log_detail(
                "run start: button clicked "
                f"api_key={_mask_api_key(api_key)} search_query={search_query!r} "
                f"use_date_filter={use_date_filter} start_date={start_date!r} "
                f"kol_count={len(kol_list)} brand_count={len(brands_list)}"
            )

            if not api_key:
                _set_run_status("error")
                _run_event("中止 | 未填写 API Key", level="error")
                _log_detail("run abort: missing api_key", level="ERROR")
                st.error("请在左侧侧边栏填入 API Key！")
                st.stop()

            if not kol_list:
                _set_run_status("error")
                _run_event("中止 | KOL 列表为空", level="error")
                _log_detail("run abort: empty kol_list", level="ERROR")
                st.error("请填入至少一个 KOL！")
                st.stop()

            if not brands_list:
                _set_run_status("error")
                _run_event("中止 | 品牌列表为空", level="error")
                _log_detail("run abort: empty brands_list", level="ERROR")
                st.error("请至少填写一个品牌！")
                st.stop()

            if brand_rules_error:
                _set_run_status("error")
                _run_event("中止 | 高级品牌规则有误", level="error")
                _log_detail(f"run abort: invalid brand_rules_payload err={brand_rules_error!r}", level="ERROR")
                st.error(brand_rules_error)
                st.stop()

        state = _run_state()
        meta = state.get("meta", {})
        api_key = api_key or meta.get("api_key", "")
        search_query = meta.get("search_query", search_query)
        enable_full_search = meta.get("enable_full_search", enable_full_search)
        enable_deep_search = meta.get("enable_deep_search", enable_deep_search)
        match_title = meta.get("match_title", match_title)
        match_description = meta.get("match_description", match_description)
        match_tags = meta.get("match_tags", match_tags)
        brands_list = meta.get("brands_list", brands_list)
        kol_list = meta.get("kol_list", kol_list)
        brand_rules_payload = meta.get("brand_rules_payload", brand_rules_payload)
        published_after_str = meta.get("published_after")
        if published_after_str == "off":
            published_after_str = None

        _log_detail(
            f"run search_mode: enable_full_search={enable_full_search} enable_deep_search={enable_deep_search}"
        )
        _log_detail(
            f"run match_scope: match_title={match_title} match_description={match_description} match_tags={match_tags}"
        )
        _log_detail(f"run published_after_str={published_after_str!r}")

        api_key_list = [k.strip() for k in api_key.split('\n') if k.strip()]
        _log_detail(
            f"get_youtube_service request: YouTubeManager api_keys_count={len(api_key_list)}"
        )
        youtube = YouTubeManager(api_keys=api_key_list, logger=_LD)
        if youtube._current_service:
            _log_detail("get_youtube_service response: client built ok")
            _run_event("YouTube API 客户端就绪")
        else:
            _log_detail(
                "get_youtube_service response: None（构建失败，详见详细日志）",
                level="ERROR",
            )
            _set_run_status("error")
            _run_event("YouTube API 客户端构建失败", level="error")
        if not youtube._current_service:
            _log_detail("run abort: youtube client is None", level="ERROR")
            st.error("API 初始化失败，请检查 API Key 是否有效。")
            st.stop()

        progress_bar = st.progress(0.0)
        status_text = st.empty()
        if brand_rules_payload is not None:
            brand_rules = build_rules_from_payload(brand_rules_payload)
            _log_detail(f"brand rules source: payload entries={len(brand_rules_payload)}")
        else:
            brand_rules = load_selected_brand_rules(brands_list)
            _log_detail(f"brand rules source: brands.json fallback entries={len(brands_list)}")
        total_kols = len(kol_list)
        start_index = int(state.get("next_kol_index", 0))
        existing_results = state.get("results", [])
        if existing_results:
            _run_append_results([])

        for i in range(start_index, total_kols):
            kol = kol_list[i]
            _log_detail(f"kol loop ({i + 1}/{total_kols}) kol={kol!r}")
            _run_current(i + 1, kol, "解析频道")
            _run_set_kol(i, status="running", message="正在解析频道")
            status_text.text(f"正在处理 ({i+1}/{total_kols}): {kol}（解析 Channel ID）")

            try:
                kol_result = search_channel_brand_mentions(
                    youtube,
                    kol,
                    search_query,
                    brand_rules,
                    published_after_str,
                    enable_full_search=enable_full_search,
                    enable_deep_search=enable_deep_search,
                    match_title=match_title,
                    match_description=match_description,
                    match_tags=match_tags,
                    log_detail=_log_detail,
                    log_json=_log_detail_json,
                    page_progress=lambda page_number, total_items, has_next, _i=i, _kol=kol: _run_stage_pagination(
                        _i + 1,
                        _kol,
                        page_number,
                        total_items,
                        has_next,
                    ),
                    quota_tracker=_run_add_quota,
                )
                state["next_kol_index"] = i + 1

                if not kol_result.channel_id:
                    _run_add_stats(processed_kols=1, skipped_kols=1)
                    _run_set_kol(i, status="skipped", message="未找到频道", candidate_count=0, matched_count=0)
                    _run_event(f"[{i + 1}/{total_kols}] {kol} | 已跳过：未找到频道", level="warn")
                    _log_detail(f"kol skip: no channel_id for kol={kol!r}", level="WARN")
                    st.warning(f"⚠️ 无法找到 {kol} 的 Channel ID，已跳过。")
                    progress_bar.progress((i + 1) / total_kols)
                    continue

                _run_current(i + 1, kol, "分析视频")
                status_text.text(f"正在处理 ({i+1}/{total_kols}): {kol}（分析视频）")
                added_rows = _run_append_results(kol_result.rows)
                _run_add_stats(
                    processed_kols=1,
                    resolved_kols=1,
                    candidate_videos=kol_result.candidate_count,
                    matched_rows=added_rows,
                )
                _run_set_kol(
                    i,
                    status="success",
                    message=f"候选 {kol_result.candidate_count}，匹配 {added_rows}",
                    candidate_count=kol_result.candidate_count,
                    matched_count=added_rows,
                )

                if not kol_result.candidate_count:
                    _run_event(f"[{i + 1}/{total_kols}] {kol} | 完成：未找到候选视频")
                    st.info(f"ℹ️ {kol} 频道下未找到包含 '{search_query}' 的视频。")
                else:
                    _run_event(
                        f"[{i + 1}/{total_kols}] {kol} | 完成：候选 {kol_result.candidate_count}，匹配 {added_rows}"
                    )

            except Exception as e:
                error_msg = str(e)
                _run_add_stats(processed_kols=1, error_kols=1)
                _run_set_kol(i, status="error", message=error_msg[:120])
                _log_detail(f"kol loop exception kol={kol!r} err={error_msg!r}", exc_info=True, level="ERROR")
                if "All API keys quotaExceeded" in error_msg or "quotaExceeded" in error_msg:
                    _run_mark_paused(f"[{i + 1}/{total_kols}] {kol} | 所有配额耗尽，任务已暂停，可稍后继续")
                    status_text.error("YouTube API 所有配额已耗尽，已保留当前进度。")
                    st.error(f"❌ 严重错误: YouTube API 所有配额已耗尽！\n本次任务已暂停在 {kol}，稍后可点击“继续上次任务”。")
                    break
                _run_event(f"[{i + 1}/{total_kols}] {kol} | 失败：{error_msg[:120]}", level="error")
                st.error(f"❌ 处理 {kol} 时发生错误: {e}")

            progress_bar.progress((i + 1) / total_kols)

        final_state = _run_state()
        if final_state.get("status") == "running":
            _set_run_status("completed")
            _run_current(0, "-", "已完成")
            stats = final_state["stats"]
            _run_event(
                f"任务完成 | KOL {stats['processed_kols']}/{stats['total_kols']} | 已解析 {stats['resolved_kols']} | "
                f"已跳过 {stats['skipped_kols']} | 失败 {stats['error_kols']} | 候选 {stats['candidate_videos']} | 匹配 {stats['matched_rows']}"
            )
            status_text.success("✅ 所有 KOL 抓取完毕！")

        results = _run_state().get("results", [])
        _log_detail(f"run finished: results_count={len(results)} status={_run_state().get('status')}")
        if results:
            _log_detail(f"results ui: dataframe shape={pd.DataFrame(results).shape}")
            df_tmp = pd.DataFrame(results)
            _log_detail(f"results export: csv_bytes={len(df_tmp.to_csv(index=False).encode('utf-8-sig'))}")
        else:
            _log_detail("results ui: empty results list")
    finally:
        try:
            _finalize_history_snapshot()
        finally:
            _LIVE_LOG_EMPTY = None


render_last_extract_results(st.session_state.get("last_extract_results"))
