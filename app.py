import streamlit as st
import pandas as pd
from googleapiclient.discovery import build
import re
import datetime
import json
import logging
import traceback
import html as html_module

_LD = logging.getLogger("lee_debug")
if not _LD.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    _LD.addHandler(_h)
    _LD.setLevel(logging.INFO)
    _LD.propagate = False

_LEE_DEBUG_MAX_LINES = 4000
_LEE_SUMMARY_MAX_LINES = 120
# 仅在本轮「开始提取」运行期间指向 expander 内的 st.empty，仅刷新摘要（勿存 session_state）
_LIVE_LOG_EMPTY = None


def _log_detail(msg: str, exc_info: bool = False) -> None:
    """完整调试日志：仅写入终端 + 弹窗用列表，不刷新页面中间区域。"""
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    body = "lee-debug " + msg
    if exc_info:
        tb = traceback.format_exc()
        if tb.strip():
            body = body + "\n" + tb
    _LD.info("lee-debug " + msg, exc_info=exc_info)
    buf = st.session_state.setdefault("lee_debug_logs", [])
    buf.append(f"{ts} {body}")
    if len(buf) > _LEE_DEBUG_MAX_LINES:
        del buf[: len(buf) - _LEE_DEBUG_MAX_LINES]


def _log_summary(msg: str) -> None:
    """统计性摘要：仅显示在「开始提取」展开区，可自动换行、固定高度内滚动。"""
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    buf = st.session_state.setdefault("lee_debug_summary", [])
    buf.append(line)
    if len(buf) > _LEE_SUMMARY_MAX_LINES:
        del buf[: len(buf) - _LEE_SUMMARY_MAX_LINES]
    global _LIVE_LOG_EMPTY
    if _LIVE_LOG_EMPTY is not None:
        try:
            text = "\n".join(buf[-60:])
            esc = html_module.escape(text)
            _LIVE_LOG_EMPTY.markdown(
                f'<div style="max-height:420px;overflow-y:auto;overflow-x:auto;'
                f"white-space:pre-wrap;word-break:break-word;"
                f"font-family:ui-monospace,Consolas,monospace;font-size:13px;line-height:1.45;"
                f'padding:10px;border-radius:8px;border:1px solid rgba(128,128,128,.25);">'
                f"{esc}</div>",
                unsafe_allow_html=True,
            )
        except Exception:
            pass


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
    _log_detail(f"{label}: {s}")


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


# 标准频道 ID：UC + 22 位（与 Data API channelId 一致）；避免把纯 handle 当成 ID
_CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")


st.set_page_config(page_title="YouTube Brand Extractor", layout="wide")
st.session_state.setdefault("lee_debug_logs", [])
st.session_state.setdefault("lee_debug_summary", [])

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
  width: 80vw !important;
  max-width: 80vw !important;
  height: 80vh !important;
  max-height: 80vh !important;
  margin-left: auto !important;
  margin-right: auto !important;
  box-sizing: border-box !important;
  overflow-y: auto !important;
  overflow-x: hidden !important;
}
/* 日志详情：压缩条目间距（已去掉 st.divider） */
div[data-testid="stDialog"] [role="dialog"] p {
  margin: 0.1rem 0 !important;
  line-height: 1.35 !important;
}
div[data-testid="stDialog"] [role="dialog"] pre {
  margin: 0.15rem 0 !important;
}
div[data-testid="stDialog"] [role="dialog"] [data-testid="stJson"] {
  margin: 0.1rem 0 0.25rem 0 !important;
}
</style>
""",
    unsafe_allow_html=True,
)


@st.dialog("日志详情", width="large")
def _log_detail_dialog():
    logs = st.session_state.get("lee_debug_logs", [])
    st.caption(
        f"详细日志 {len(logs)} 条（最多保留 {_LEE_DEBUG_MAX_LINES} 条）。"
        "中间区域仅显示统计摘要；完整内容仅在此处查看。"
    )
    c1, c2 = st.columns(2)
    with c1:
        if st.button("清空详细日志", use_container_width=True):
            st.session_state["lee_debug_logs"] = []
            st.rerun()
    with c2:
        payload = ("\n".join(logs) if logs else "（尚无日志）").encode("utf-8")
        st.download_button(
            label="下载为 TXT",
            data=payload,
            file_name="lee-debug.log",
            mime="text/plain; charset=utf-8",
            use_container_width=True,
        )
    st.caption("含 JSON 的条目会拆成「前缀说明 + 下方树状结构」；纯文本/异常栈保持原样。")

    if not logs:
        st.info("（尚无日志）")
    else:
        for i, entry in enumerate(logs):
            prefix, j = _try_parse_json_suffix_in_log_entry(entry)
            if j is not None:
                st.caption(f"#{i + 1} · {prefix}")
                st.json(j, expanded=2)
            else:
                st.markdown(f"**#{i + 1}**")
                st.code(entry, language=None)

st.title("📹 YouTube KOL 品牌提取工具")
st.markdown("该工具基于 YouTube Data API 官方接口，用于自动化提取指定博主视频中提及的品牌。")

# --- 侧边栏配置 ---
with st.sidebar:
    st.header("1. API 配置")
    api_key = st.text_input("YouTube Data API Key", type="password", help="请在此处输入您的 Google Cloud API Key")
    
    st.header("2. 搜索设置")
    search_query = st.text_input("搜索关键词", value="camera", help="将在频道内搜索包含该关键词的视频")
    
    use_date_filter = st.checkbox("启用时间过滤", value=True)
    if use_date_filter:
        # 默认搜索近1年的视频
        start_date = st.date_input("仅搜索此日期之后发布的视频", value=datetime.date.today() - datetime.timedelta(days=365))
    else:
        start_date = None

    st.header("3. 品牌词典")
    st.markdown("输入要提取的品牌（每行一个）")
    brands_input = st.text_area(
        label="品牌列表", 
        value="Logitech\nRazer\nElgato\nSony\nCanon\nMicrosoft\nInsta360",
        height=200,
        label_visibility="collapsed"
    )
    brands_list = [b.strip() for b in brands_input.split('\n') if b.strip()]

    st.header("调试")
    _n = len(st.session_state.get("lee_debug_logs", []))
    st.caption(f"详细日志 {_n} 条（弹窗内查看）；中间区域仅显示统计摘要")
    if st.button("日志详情", use_container_width=True, help="在弹窗中查看 / 清空 / 下载日志"):
        _log_detail_dialog()

# --- 主页面配置 ---
st.header("4. KOL 列表")
kols_input = st.text_area(
    label="输入 KOL：Channel Handle（可带或不带 @，如 Andru 或 @Andru）或 UC 开头的 Channel ID（每行一个）",
    value="@TechSource\n@MKBHD",
    height=150,
)
kol_list = [k.strip() for k in kols_input.split('\n') if k.strip()]

st.warning(f"⚠️ **API 配额提醒**：您输入了 {len(kol_list)} 个 KOL。每次搜索消耗 100 点配额。请确保您的 API Key 额度充足（免费账户每日上限 10,000 点）。")

# 统计摘要：放在「开始提取」外，重跑（如打开日志弹窗）后仍能从 session 恢复显示
with st.expander(
    "运行摘要（统计；实时刷新，关闭弹窗后仍保留上次内容）",
    expanded=True,
):
    summary_display = st.empty()
_buf = st.session_state.get("lee_debug_summary") or []
if _buf:
    _txt = "\n".join(_buf)
    _esc = html_module.escape(_txt)
    summary_display.markdown(
        f'<div style="max-height:420px;overflow-y:auto;overflow-x:auto;'
        f"white-space:pre-wrap;word-break:break-word;"
        f"font-family:ui-monospace,Consolas,monospace;font-size:13px;line-height:1.45;"
        f'padding:10px;border-radius:8px;border:1px solid rgba(128,128,128,.25);">'
        f"{_esc}</div>",
        unsafe_allow_html=True,
    )
else:
    summary_display.caption("暂无摘要；点击「开始提取」后在此显示统计进度。")

# --- 核心功能函数 ---
@st.cache_resource(show_spinner=False)
def get_youtube_service(api_key):
    try:
        return build("youtube", "v3", developerKey=api_key)
    except Exception:
        _LD.info("lee-debug get_youtube_service build failed", exc_info=True)
        return None


def resolve_channel_id(youtube, raw: str):
    """将 Handle（可带 @）解析为 Channel ID；已是 UC… 合法 ID 则原样返回。"""
    handle = (raw or "").strip()
    if not handle:
        return None

    if _CHANNEL_ID_RE.match(handle):
        _log_detail(f"resolve_channel_id literal_uc_channel_id: {handle!r}")
        return handle

    slug = handle[1:] if handle.startswith("@") else handle
    ch_body = {"part": "id", "forHandle": slug}
    _log_detail(
        f"resolve_channel_id request channels.list: {json.dumps(ch_body, ensure_ascii=False)}"
    )
    try:
        ch_resp = youtube.channels().list(part="id", forHandle=slug).execute()
        _log_detail_json("resolve_channel_id response channels.list", ch_resp)
        ch_items = ch_resp.get("items") or []
        if ch_items:
            cid = ch_items[0]["id"]
            _log_detail(f"resolve_channel_id forHandle {handle!r} -> channelId={cid!r}")
            return cid
        _log_detail(f"resolve_channel_id channels.list empty forHandle={slug!r}, fallback search.list")
    except Exception as e:
        _log_detail(
            f"resolve_channel_id channels.list failed forHandle={slug!r} err={e!r}, fallback search.list",
            exc_info=True,
        )

    q = handle if handle.startswith("@") else f"@{slug}"
    channel_search_body = {
        "part": "snippet",
        "q": q,
        "type": "channel",
        "maxResults": 1,
    }
    _log_detail(
        f"resolve_channel_id request search.list: {json.dumps(channel_search_body, ensure_ascii=False)}"
    )

    try:
        response = youtube.search().list(
            part="snippet",
            q=q,
            type="channel",
            maxResults=1,
        ).execute()
        _log_detail_json("resolve_channel_id response search.list", response)
        if response.get("items"):
            cid = response["items"][0]["snippet"]["channelId"]
            _log_detail(f"resolve_channel_id search mapped {handle!r} -> channelId={cid!r}")
            return cid
        _log_detail(f"resolve_channel_id search empty items for {handle!r}")
        return None
    except Exception as e:
        _log_detail(f"resolve_channel_id search exception handle={handle!r} err={e!r}", exc_info=True)
        st.error(f"解析频道 {handle} 失败: {e}")
        return None


def extract_brands(text, brands):
    """使用正则边界匹配品牌，忽略大小写"""
    if not text:
        _log_detail("extract_brands: empty text matched=[]")
        return []
    text = str(text)
    found_brands = set()
    for brand in brands:
        # \b 表示单词边界，防止类似 "credit" 匹配到 "red"
        pattern = r"\b" + re.escape(brand) + r"\b"
        if re.search(pattern, text, re.IGNORECASE):
            found_brands.add(brand)
    out = list(found_brands)
    _log_detail(
        f"extract_brands: text_len={len(text)} dict_size={len(brands)} matched={out!r}"
    )
    return out


def _render_last_extract_results() -> None:
    """从会话状态渲染上次提取结果（任意重跑后仍可见，例如打开日志弹窗后）。"""
    if "last_extract_results" not in st.session_state:
        return
    rows = st.session_state["last_extract_results"]
    st.header("5. 提取结果")
    if rows:
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True)
        csv_data = df.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            label="📥 一键下载为 CSV 文件 (可直接用 Excel 打开)",
            data=csv_data,
            file_name="youtube_brand_mentions.csv",
            mime="text/csv",
            type="primary",
            key="download_last_extract_csv",
        )
    else:
        st.info("没有找到任何符合品牌匹配条件的视频。")


# --- 执行逻辑 ---
if st.button("🚀 开始提取", type="primary"):
    try:
        st.session_state["lee_debug_summary"] = []
        _LIVE_LOG_EMPTY = summary_display
        _log_summary(
            f"任务: KOL {len(kol_list)} 个 | 品牌词 {len(brands_list)} 个 | 搜索词 {search_query!r} | "
            f"日期过滤={'开' if use_date_filter else '关'}"
        )
        _log_detail(
            "run start: button clicked "
            f"api_key={_mask_api_key(api_key)} search_query={search_query!r} "
            f"use_date_filter={use_date_filter} start_date={start_date!r} "
            f"kol_count={len(kol_list)} brand_count={len(brands_list)}"
        )
        if not api_key:
            _log_summary("中止: 未填写 API Key")
            _log_detail("run abort: missing api_key")
            st.error("请在左侧侧边栏填入 API Key！")
            st.stop()

        if not kol_list:
            _log_summary("中止: KOL 列表为空")
            _log_detail("run abort: empty kol_list")
            st.error("请填入至少一个 KOL！")
            st.stop()

        _log_detail(
            f"get_youtube_service request: build(youtube, v3) api_key={_mask_api_key(api_key)}"
        )
        youtube = get_youtube_service(api_key)
        if youtube:
            _log_detail("get_youtube_service response: client built ok")
            _log_summary("YouTube API 客户端: 就绪")
        else:
            _log_detail(
                "get_youtube_service response: None（构建失败，详见上方摘要或终端控制台栈）"
            )
            _log_summary("YouTube API 客户端: 构建失败（见详细日志 / 控制台）")
        if not youtube:
            _log_detail("run abort: youtube client is None")
            st.error("API 初始化失败，请检查 API Key 是否有效。")
            st.stop()

        results = []
        progress_bar = st.progress(0)
        status_text = st.empty()

        # 处理时间格式 (RFC 3339)
        published_after_str = None
        if start_date:
            # 转为 UTC 零点
            published_after_str = start_date.strftime("%Y-%m-%dT00:00:00Z")

        _log_detail(f"run published_after_str={published_after_str!r}")
        _log_summary(f"发布时间下限: {published_after_str or '（未启用）'}")

        total_kols = len(kol_list)

        for i, kol in enumerate(kol_list):
            _log_detail(f"kol loop ({i + 1}/{total_kols}) kol={kol!r}")
            _log_summary(f"「{i + 1}/{total_kols}」{kol} → 解析频道…")
            status_text.text(f"⏳ 正在处理 ({i+1}/{total_kols}): {kol} ... (解析 Channel ID)")

            channel_id = resolve_channel_id(youtube, kol)
            if not channel_id:
                _log_summary(f"「{i + 1}/{total_kols}」{kol} → 未解析到频道，已跳过")
                _log_detail(f"kol skip: no channel_id for kol={kol!r}")
                st.warning(f"⚠️ 无法找到 {kol} 的 Channel ID，已跳过。")
                progress_bar.progress((i + 1) / total_kols)
                continue

            _log_summary(f"「{i + 1}/{total_kols}」{kol} → 频道 {channel_id}")
            status_text.text(f"⏳ 正在处理 ({i+1}/{total_kols}): {kol} ... (搜索视频)")

            try:
                video_search_body = {
                    "part": "snippet",
                    "channelId": channel_id,
                    "q": search_query,
                    "type": "video",
                    "maxResults": 50,
                    "publishedAfter": published_after_str,
                }
                _log_detail(
                    f"video search.list request: {json.dumps(video_search_body, ensure_ascii=False)}"
                )
                # 调用 Search API (这里极度消耗配额，一次 100 点)
                request = youtube.search().list(
                    part="snippet",
                    channelId=channel_id,
                    q=search_query,
                    type="video",
                    maxResults=50,  # 默认抓取前 50 个匹配视频，避免无限翻页耗尽配额
                    publishedAfter=published_after_str,
                )
                response = request.execute()
                _log_detail_json(f"video search.list response kol={kol!r}", response)

                items = response.get("items", [])
                _log_detail(f"video search parsed: items_count={len(items)} kol={kol!r}")
                if not items:
                    _log_summary(f"「{i + 1}/{total_kols}」{kol} → 无候选视频（0 条）")
                    st.info(f"ℹ️ {kol} 频道下未找到包含 '{search_query}' 的视频。")

                matched_in_kol = 0
                for item in items:
                    title = item["snippet"]["title"]
                    description = item["snippet"]["description"]
                    video_id = item["id"]["videoId"]
                    published_at = item["snippet"]["publishedAt"]
                    video_url = f"https://youtu.be/{video_id}"

                    # 拼接标题和简介，用于品牌匹配
                    combined_text = title + " \n " + description
                    mentioned_brands = extract_brands(combined_text, brands_list)

                    # 如果匹配到了品牌，才加入结果集
                    if mentioned_brands:
                        row = {
                            "KOL 名称": kol,
                            "视频标题": title,
                            "视频链接": video_url,
                            "提及的品牌": ", ".join(mentioned_brands),
                            "发布时间": published_at[:10],  # 仅保留日期 YYYY-MM-DD
                        }
                        _log_detail(
                            f"result_row append video_id={video_id!r} brands={mentioned_brands!r}"
                        )
                        matched_in_kol += 1
                        results.append(row)
                    else:
                        _log_detail(
                            f"result_row skip no_brand_match video_id={video_id!r} title={title[:80]!r}"
                        )

                if items:
                    _log_summary(
                        f"「{i + 1}/{total_kols}」{kol} → 候选 {len(items)} 条, 品牌匹配 {matched_in_kol} 行"
                    )

            except Exception as e:
                error_msg = str(e)
                _log_summary(f"「{i + 1}/{total_kols}」{kol} → 错误: {error_msg[:120]}")
                _log_detail(f"kol loop exception kol={kol!r} err={error_msg!r}", exc_info=True)
                if "quotaExceeded" in error_msg:
                    st.error(f"❌ 严重错误: YouTube API 配额已耗尽！\n本次任务被迫终止于 {kol}。请明天再试或更换 API Key。")
                    break
                else:
                    st.error(f"❌ 处理 {kol} 时发生未知错误: {e}")

            # 更新进度条
            progress_bar.progress((i + 1) / total_kols)

        _log_detail(f"run finished: results_count={len(results)}")
        _log_summary(f"全部完成: 结果表共 {len(results)} 行（含品牌匹配）")
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


_render_last_extract_results()