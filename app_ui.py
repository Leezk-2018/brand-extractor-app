import datetime
from dataclasses import dataclass
from typing import Callable

import pandas as pd
import streamlit as st


@dataclass
class UserInputs:
    api_key: str
    search_query: str
    use_date_filter: bool
    start_date: datetime.date | None
    brands_list: list[str]
    kol_list: list[str]
    summary_display: any


def render_sidebar(log_count: int, open_log_dialog: Callable[[], None]) -> tuple[str, str, bool, datetime.date | None, list[str]]:
    with st.sidebar:
        st.header("1. API 配置")
        api_key = st.text_input(
            "YouTube Data API Key",
            type="password",
            help="请在此处输入您的 Google Cloud API Key",
        )

        st.header("2. 搜索设置")
        search_query = st.text_input(
            "搜索关键词",
            value="camera",
            help="将在频道内搜索包含该关键词的视频",
        )

        use_date_filter = st.checkbox("启用时间过滤", value=True)
        if use_date_filter:
            start_date = st.date_input(
                "仅搜索此日期之后发布的视频",
                value=datetime.date.today() - datetime.timedelta(days=365),
            )
        else:
            start_date = None

        st.header("3. 品牌词典")
        st.markdown("输入要提取的品牌，每行一个")
        brands_input = st.text_area(
            label="品牌列表",
            value="Logitech\nRazer\nElgato\nSony\nCanon\nMicrosoft\nInsta360",
            height=200,
            label_visibility="collapsed",
        )
        brands_list = [brand.strip() for brand in brands_input.split("\n") if brand.strip()]

        st.header("调试")
        st.caption(f"详细日志 {log_count} 条（弹窗内查看），中间区域仅显示统计摘要")
        if st.button("日志详情", use_container_width=True, help="在弹窗中查看 / 清空 / 下载日志"):
            open_log_dialog()

    return api_key, search_query, use_date_filter, start_date, brands_list


def render_main_inputs() -> list[str]:
    st.header("4. KOL 列表")
    kols_input = st.text_area(
        label="输入 KOL：Channel Handle（可带或不带 @）或 UC 开头的 Channel ID，每行一个",
        value="@rogerseng\n@JordanHetrick",
        height=150,
    )
    return [kol.strip() for kol in kols_input.split("\n") if kol.strip()]


def render_quota_warning(kol_count: int) -> None:
    st.warning(
        f"⚠️ API 配额提醒：您输入了 {kol_count} 个 KOL。每次搜索消耗 100 点配额，请确认 API Key 额度充足。"
    )


def render_summary_panel() -> any:
    with st.expander("运行摘要", expanded=True):
        return st.empty()


def update_summary_panel(summary_display, summary_state: dict | None) -> None:
    if not summary_state:
        summary_display.caption("暂无摘要；点击“开始提取”后在此显示任务状态。")
        return

    stats = summary_state.get("stats", {})
    meta = summary_state.get("meta", {})
    current = summary_state.get("current", {})
    events = summary_state.get("events", [])

    with summary_display.container():
        c1, c2, c3, c4, c5, c6 = st.columns(6)
        c1.metric("KOL", f'{stats.get("processed_kols", 0)}/{stats.get("total_kols", 0)}')
        c2.metric("Resolved", stats.get("resolved_kols", 0))
        c3.metric("Skipped", stats.get("skipped_kols", 0))
        c4.metric("Errors", stats.get("error_kols", 0))
        c5.metric("Candidates", stats.get("candidate_videos", 0))
        c6.metric("Matched", stats.get("matched_rows", 0))

        st.caption(
            f"Query: {meta.get('search_query', '-')} | Brands: {meta.get('brand_count', 0)} | "
            f"Date: {meta.get('published_after', 'off')}"
        )
        if current:
            st.info(
                f'当前: [{current.get("index", 0)}/{stats.get("total_kols", 0)}] '
                f'{current.get("kol", "-")} | {current.get("stage", "-")}'
            )

        if events:
            lines = [f'[{item.get("time", "--:--:--")}] {item.get("message", "")}' for item in events[-20:]]
            st.code("\n".join(lines), language=None)
        else:
            st.caption("暂无事件。")


def render_last_extract_results(rows: list[dict[str, str]] | None) -> None:
    if rows is None:
        return

    st.header("5. 提取结果")
    if not rows:
        st.info("没有找到任何符合品牌匹配条件的视频。")
        return

    df = pd.DataFrame(rows)
    sortable_columns = [
        "发布时间",
        "播放量",
        "点赞数",
        "评论数",
        "视频时长",
        "KOL 名称",
        "视频标题",
    ]
    available_sort_columns = [column for column in sortable_columns if column in df.columns]

    if available_sort_columns:
        s1, s2 = st.columns([1.6, 1.0])
        with s1:
            sort_by = st.selectbox(
                "排序字段",
                available_sort_columns,
                index=0 if "发布时间" in available_sort_columns else 0,
            )
        with s2:
            sort_direction = st.selectbox("排序方向", ["降序", "升序"], index=0)

        df = _sort_result_df(df, sort_by=sort_by, ascending=(sort_direction == "升序"))

    st.dataframe(df, use_container_width=True)
    csv_data = df.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        label="📜 一键下载为 CSV 文件",
        data=csv_data,
        file_name="youtube_brand_mentions.csv",
        mime="text/csv",
        type="primary",
        key="download_last_extract_csv",
    )


def _sort_result_df(df: pd.DataFrame, sort_by: str, ascending: bool) -> pd.DataFrame:
    sortable = df.copy()
    sort_key = f"__sort_{sort_by}"
    if sort_by in {"播放量", "点赞数", "评论数"}:
        sortable[sort_key] = sortable[sort_by].map(_parse_int_like)
    elif sort_by == "视频时长":
        sortable[sort_key] = sortable[sort_by].map(_parse_duration_like)
    elif sort_by == "发布时间":
        sortable[sort_key] = pd.to_datetime(sortable[sort_by], errors="coerce")
    else:
        sortable[sort_key] = sortable[sort_by].astype(str)

    sortable = sortable.sort_values(
        by=sort_key,
        ascending=ascending,
        na_position="last",
        kind="stable",
    ).drop(columns=[sort_key])
    return sortable.reset_index(drop=True)


def _parse_int_like(value) -> int:
    if value in ("", None):
        return -1
    try:
        return int(str(value).replace(",", "").strip())
    except Exception:
        return -1


def _parse_duration_like(value) -> int:
    if value in ("", None):
        return -1
    text = str(value).strip()
    parts = text.split(":")
    try:
        if len(parts) == 2:
            minutes, seconds = parts
            return int(minutes) * 60 + int(seconds)
        if len(parts) == 3:
            hours, minutes, seconds = parts
            return int(hours) * 3600 + int(minutes) * 60 + int(seconds)
    except Exception:
        return -1
    return -1
