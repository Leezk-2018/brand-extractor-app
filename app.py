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
_LEE_SUMMARY_MAX_LINES = 120
_LEE_SUMMARY_EVENT_LIMIT = 80
# 仅在本轮「开始提取」运行期间指向 expander 内的 st.empty，仅刷新摘要（勿存 session_state）
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


def _new_summary_state() -> dict:
    return {
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
    }


def _summary_state() -> dict:
    state = st.session_state.get("lee_debug_summary")
    if not isinstance(state, dict):
        state = _new_summary_state()
        st.session_state["lee_debug_summary"] = state
    return state


def _refresh_summary() -> None:
    if _LIVE_LOG_EMPTY is not None:
        update_summary_panel(_LIVE_LOG_EMPTY, _summary_state())


def _summary_reset(**meta) -> None:
    state = _new_summary_state()
    state["meta"] = meta
    state["stats"]["total_kols"] = meta.get("total_kols", 0)
    st.session_state["lee_debug_summary"] = state
    _refresh_summary()


def _summary_event(message: str, level: str = "info") -> None:
    state = _summary_state()
    events = state.setdefault("events", [])
    events.append(
        {
            "time": datetime.datetime.now().strftime("%H:%M:%S"),
            "level": level,
            "message": message,
        }
    )
    if len(events) > _LEE_SUMMARY_EVENT_LIMIT:
        del events[: len(events) - _LEE_SUMMARY_EVENT_LIMIT]
    _refresh_summary()


def _summary_current(index: int, kol: str, stage: str) -> None:
    state = _summary_state()
    state["current"] = {"index": index, "kol": kol, "stage": stage}
    _refresh_summary()


def _summary_pagination(index: int, total: int, kol: str, page_number: int, total_items: int, has_next: bool) -> None:
    suffix = " | more" if has_next else " | final"
    _summary_current(index, kol, f"search_videos page={page_number} total_items={total_items}{suffix}")
    _summary_event(f"[{index}/{total}] {kol} | page {page_number} | total_items {total_items}" + (" | more" if has_next else " | final"))


def _summary_add_stats(**updates) -> None:
    stats = _summary_state().setdefault("stats", {})
    for key, value in updates.items():
        stats[key] = stats.get(key, 0) + value
    _refresh_summary()


def _log_summary(msg: str) -> None:
    _summary_event(msg)


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
if not isinstance(st.session_state.get("lee_debug_summary"), dict):
    st.session_state["lee_debug_summary"] = _new_summary_state()

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

st.title("📹 YouTube KOL 品牌提取工具")
st.markdown("该工具基于 YouTube Data API 官方接口，用于自动化提取指定博主视频中提及的品牌。")

api_key, search_query, use_date_filter, start_date, brands_list = render_sidebar(
    log_count=len(st.session_state.get("lee_debug_logs", [])),
    open_log_dialog=_log_detail_dialog,
)
kol_list = render_main_inputs()
render_quota_warning(len(kol_list))
summary_display = render_summary_panel()
update_summary_panel(summary_display, st.session_state.get("lee_debug_summary"))

# --- 核心功能函数 ---
@st.cache_resource(show_spinner=False)
def get_youtube_service(api_key):
    return build_youtube_service(api_key, logger=_LD)


# --- 执行逻辑 ---
if st.button("🚀 开始提取", type="primary"):
    try:
        st.session_state["lee_debug_summary"] = _new_summary_state()
        _LIVE_LOG_EMPTY = summary_display
        _summary_reset(
            total_kols=len(kol_list),
            brand_count=len(brands_list),
            search_query=search_query,
            published_after=build_published_after(start_date) or "off",
        )
        _summary_event(
            f"任务开始 | KOL {len(kol_list)} | brands {len(brands_list)} | date_filter {'on' if use_date_filter else 'off'}"
        )
        _log_detail(
            "run start: button clicked "
            f"api_key={_mask_api_key(api_key)} search_query={search_query!r} "
            f"use_date_filter={use_date_filter} start_date={start_date!r} "
            f"kol_count={len(kol_list)} brand_count={len(brands_list)}"
        )
        if not api_key:
            _summary_event("中止 | 未填写 API Key", level="error")
            _log_detail("run abort: missing api_key", level="ERROR")
            st.error("请在左侧侧边栏填入 API Key！")
            st.stop()

        if not kol_list:
            _summary_event("中止 | KOL 列表为空", level="error")
            _log_detail("run abort: empty kol_list", level="ERROR")
            st.error("请填入至少一个 KOL！")
            st.stop()

        _log_detail(
            f"get_youtube_service request: build(youtube, v3) api_key={_mask_api_key(api_key)}"
        )
        youtube = get_youtube_service(api_key)
        if youtube:
            _log_detail("get_youtube_service response: client built ok")
            _summary_event("YouTube API 客户端就绪")
        else:
            _log_detail(
                "get_youtube_service response: None（构建失败，详见上方摘要或终端控制台栈）",
                level="ERROR",
            )
            _summary_event("YouTube API 客户端构建失败", level="error")
        if not youtube:
            _log_detail("run abort: youtube client is None", level="ERROR")
            st.error("API 初始化失败，请检查 API Key 是否有效。")
            st.stop()

        results = []
        progress_bar = st.progress(0)
        status_text = st.empty()
        brand_rules = load_selected_brand_rules(brands_list)

        # 处理时间格式 (RFC 3339)
        published_after_str = build_published_after(start_date)

        _log_detail(f"run published_after_str={published_after_str!r}")
        _summary_event(f"发布时间下限 | {published_after_str or 'off'}")

        total_kols = len(kol_list)

        for i, kol in enumerate(kol_list):
            _log_detail(f"kol loop ({i + 1}/{total_kols}) kol={kol!r}")
            _summary_current(i + 1, kol, "resolve_channel")
            status_text.text(f"⏳ 正在处理 ({i+1}/{total_kols}): {kol} ... (解析 Channel ID)")

            try:
                kol_result = search_channel_brand_mentions(
                    youtube,
                    kol,
                    search_query,
                    brand_rules,
                    published_after_str,
                    log_detail=_log_detail,
                    log_json=_log_detail_json,
                    page_progress=lambda page_number, total_items, has_next, _i=i, _kol=kol, _total=total_kols: _summary_pagination(
                        _i + 1,
                        _total,
                        _kol,
                        page_number,
                        total_items,
                        has_next,
                    ),
                )
                if not kol_result.channel_id:
                    _summary_add_stats(processed_kols=1, skipped_kols=1)
                    _summary_event(f"[{i + 1}/{total_kols}] {kol} | skipped | channel not found", level="warn")
                    _log_detail(f"kol skip: no channel_id for kol={kol!r}", level="WARN")
                    st.warning(f"⚠️ 无法找到 {kol} 的 Channel ID，已跳过。")
                    progress_bar.progress((i + 1) / total_kols)
                    continue

                _summary_current(i + 1, kol, "search_videos")
                status_text.text(f"⏳ 正在处理 ({i+1}/{total_kols}): {kol} ... (搜索视频)")
                _summary_add_stats(
                    processed_kols=1,
                    resolved_kols=1,
                    candidate_videos=kol_result.candidate_count,
                    matched_rows=kol_result.matched_count,
                )

                if not kol_result.candidate_count:
                    _summary_event(f"[{i + 1}/{total_kols}] {kol} | ok | candidates 0 | matched 0")
                    st.info(f"ℹ️ {kol} 频道下未找到包含 '{search_query}' 的视频。")
                else:
                    _summary_event(
                        f"[{i + 1}/{total_kols}] {kol} | ok | candidates {kol_result.candidate_count} | matched {kol_result.matched_count}"
                    )

                results.extend(kol_result.rows)

            except Exception as e:
                error_msg = str(e)
                _summary_add_stats(processed_kols=1, error_kols=1)
                _summary_event(f"[{i + 1}/{total_kols}] {kol} | error | {error_msg[:120]}", level="error")
                _log_detail(f"kol loop exception kol={kol!r} err={error_msg!r}", exc_info=True, level="ERROR")
                if "quotaExceeded" in error_msg:
                    st.error(f"❌ 严重错误: YouTube API 配额已耗尽！\n本次任务被迫终止于 {kol}。请明天再试或更换 API Key。")
                    break
                else:
                    st.error(f"❌ 处理 {kol} 时发生未知错误: {e}")

            # 更新进度条
            progress_bar.progress((i + 1) / total_kols)

        _log_detail(f"run finished: results_count={len(results)}")
        _summary_current(0, "-", "completed")
        stats = _summary_state()["stats"]
        _summary_event(
            f"done | kols {stats['processed_kols']}/{stats['total_kols']} | resolved {stats['resolved_kols']} | "
            f"skipped {stats['skipped_kols']} | errors {stats['error_kols']} | "
            f"candidates {stats['candidate_videos']} | matched {stats['matched_rows']}"
        )
        status_text.success("✅ 所有 KOL 抓取完毕！")

        # 写入会话，避免点击侧边栏等控件触发重跑后「5. 提取结果」消失
        st.session_state["last_extract_results"] = results
        if results:
            _log_detail(f"results ui: dataframe shape={pd.DataFrame(results).shape}")
            df_tmp = pd.DataFrame(results)
            _log_detail(f"results export: csv_bytes={len(df_tmp.to_csv(index=False).encode('utf-8-sig'))}")
        else:
            _log_detail("results ui: empty results list")
    finally:
        _LIVE_LOG_EMPTY = None


render_last_extract_results(st.session_state.get("last_extract_results"))
