import streamlit as st
import pandas as pd
import datetime
import json
import logging
import traceback

from app_ui import (
    render_last_extract_results,
    render_main_inputs,
    render_quota_warning,
    render_sidebar,
    render_summary_panel,
    update_summary_panel,
)
from brand_rules import build_rules_from_payload
from extractor_core import (
    build_published_after,
    get_youtube_service as build_youtube_service,
    load_selected_brand_rules,
    search_channel_brand_mentions,
)

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


@st.dialog("日志详情", width="large")
def _log_detail_dialog():
    logs = st.session_state.get("lee_debug_logs", [])
    parsed_logs = []
    json_count = 0
    level_counts = {"INFO": 0, "WARN": 0, "ERROR": 0}
    for i, raw in enumerate(logs):
        if isinstance(raw, dict):
            entry = _log_entry_text(raw)
            level = str(raw.get("level", "INFO")).upper()
        else:
            entry = _log_entry_text(raw)
            level = "ERROR" if "Traceback" in entry or " err=" in entry else "INFO"
        prefix, j = _try_parse_json_suffix_in_log_entry(entry)
        is_json = j is not None
        if is_json:
            json_count += 1
        if level not in level_counts:
            level_counts[level] = 0
        level_counts[level] += 1
        parsed_logs.append(
            {
                "index": i + 1,
                "entry": entry,
                "prefix": prefix,
                "json": j,
                "is_json": is_json,
                "level": level,
            }
        )

    st.markdown("### 日志控制台")
    st.caption("保留完整调试日志。支持筛选、倒序查看、结构化 JSON 浏览和下载。")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("总条目", len(parsed_logs))
    m2.metric("JSON", json_count)
    m3.metric("文本", len(parsed_logs) - json_count)
    m4.metric("ERROR", level_counts.get("ERROR", 0))

    t1, t2, t3, t4 = st.columns([2.8, 1.1, 1.1, 0.9])
    with t1:
        keyword = st.text_input(
            "筛选",
            placeholder="搜索关键字、KOL、video id、error",
            label_visibility="collapsed",
        )
    with t2:
        view_mode = st.selectbox(
            "类型",
            ["全部", "JSON", "文本"],
            label_visibility="collapsed",
        )
    with t3:
        level_mode = st.selectbox(
            "等级",
            ["全部", "INFO", "WARN", "ERROR"],
            label_visibility="collapsed",
        )
    with t4:
        newest_first = st.toggle("最新在前", value=True)

    a1, a2 = st.columns(2)
    with a1:
        if st.button("清空日志", use_container_width=True):
            st.session_state["lee_debug_logs"] = []
            st.rerun()
    with a2:
        payload = ("\n".join(_log_entry_text(item) for item in logs) if logs else "（尚无日志）").encode("utf-8")
        st.download_button(
            label="下载 TXT",
            data=payload,
            file_name="lee-debug.log",
            mime="text/plain; charset=utf-8",
            use_container_width=True,
        )

    filtered_logs = parsed_logs
    if view_mode == "JSON":
        filtered_logs = [item for item in filtered_logs if item["is_json"]]
    elif view_mode == "文本":
        filtered_logs = [item for item in filtered_logs if not item["is_json"]]

    if level_mode != "全部":
        filtered_logs = [item for item in filtered_logs if item["level"] == level_mode]

    if keyword.strip():
        needle = keyword.strip().lower()
        filtered_logs = [item for item in filtered_logs if needle in item["entry"].lower()]

    if newest_first:
        filtered_logs = list(reversed(filtered_logs))

    st.caption(f"显示 {len(filtered_logs)} / {len(parsed_logs)} 条")

    if not filtered_logs:
        st.info("没有匹配当前筛选条件的日志。")
    else:
        for item in filtered_logs:
            with st.container(border=True):
                if item["is_json"]:
                    st.caption(f"#{item['index']} · {item['level']} · JSON")
                    if item["prefix"]:
                        st.markdown(f"**{item['prefix']}**")
                else:
                    st.caption(f"#{item['index']} · {item['level']} · TEXT")

                if item["is_json"]:
                    st.json(item["json"], expanded=1)
                else:
                    st.code(item["entry"], language=None)

st.title("YouTube 品牌提取助手")
st.markdown("批量扫描 YouTube 频道内容，找出视频中提到的品牌，并导出结果。")

api_key, search_query, use_date_filter, start_date, brands_list, brand_rules_payload, brand_rules_error, enable_full_search, enable_deep_search, match_title, match_description, match_tags = render_sidebar(
    log_count=len(st.session_state.get("lee_debug_logs", [])),
    open_log_dialog=_log_detail_dialog,
)
kol_list = render_main_inputs()
render_quota_warning(len(kol_list))
summary_display = render_summary_panel()
update_summary_panel(summary_display, _run_state())

# --- 核心功能函数 ---
@st.cache_resource(show_spinner=False)
def get_youtube_service(api_key):
    return build_youtube_service(api_key, logger=_LD)


# --- 执行逻辑 ---
run_state = _run_state()
can_resume = run_state.get("status") == "paused" and run_state.get("next_kol_index", 0) < run_state.get("stats", {}).get("total_kols", 0)

start_col, resume_col = st.columns([1.2, 1.0])
start_new_run = start_col.button("开始提取", type="primary", use_container_width=True)
resume_run = resume_col.button("继续上次任务", disabled=not can_resume, use_container_width=True)

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

        _log_detail(
            f"get_youtube_service request: build(youtube, v3) api_key={_mask_api_key(api_key)}"
        )
        youtube = get_youtube_service(api_key)
        if youtube:
            _log_detail("get_youtube_service response: client built ok")
            _run_event("YouTube API 客户端就绪")
        else:
            _log_detail(
                "get_youtube_service response: None（构建失败，详见详细日志）",
                level="ERROR",
            )
            _set_run_status("error")
            _run_event("YouTube API 客户端构建失败", level="error")
        if not youtube:
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
                if "quotaExceeded" in error_msg:
                    _run_mark_paused(f"[{i + 1}/{total_kols}] {kol} | 配额耗尽，任务已暂停，可稍后继续")
                    status_text.error("YouTube API 配额已耗尽，已保留当前进度。")
                    st.error(f"❌ 严重错误: YouTube API 配额已耗尽！\n本次任务已暂停在 {kol}，稍后可点击“继续上次任务”。")
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
        _LIVE_LOG_EMPTY = None


render_last_extract_results(st.session_state.get("last_extract_results"))
