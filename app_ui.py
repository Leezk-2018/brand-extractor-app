import datetime
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable

import pandas as pd
import streamlit as st

try:
    from streamlit_ace import st_ace
except ImportError:
    st_ace = None

from brand_rules import build_brand_rules_payload, parse_brand_rules_json


@dataclass
class UserInputs:
    api_key: str
    search_query: str
    use_date_filter: bool
    start_date: datetime.date | None
    brands_list: list[str]
    kol_list: list[str]
    summary_display: any


@st.dialog("高级品牌规则", width="large")
def _brand_rules_dialog(brands_list: list[str]) -> None:
    _render_advanced_brand_rules_editor_content(brands_list)


def render_sidebar(
    log_count: int,
    open_log_dialog: Callable[[], None],
) -> tuple[
    str,
    str,
    bool,
    datetime.date | None,
    list[str],
    list[dict[str, Any]] | None,
    str | None,
    bool,
    bool,
    bool,
    bool,
    bool,
]:
    with st.sidebar:
        # 注入紧凑型 CSS
        st.markdown(
            """
            <style>
            /* 0. 强行压缩侧边栏顶部的巨大空白 */
            [data-testid="stSidebarHeader"] {
                height: 2.5rem !important;
                min-height: 2.5rem !important;
                padding: 0 !important;
                margin-bottom: -2rem !important;
            }
            [data-testid="stSidebarHeader"] > div {
                height: 2.5rem !important;
                min-height: 2.5rem !important;
            }
            /* 保证收缩按钮依然可见但位置紧凑 */
            [data-testid="stSidebarCollapseButton"] {
                inset-block-start: 0.5rem !important;
            }
            [data-testid="stSidebarUserContent"] {
                padding-top: 0.5rem !important;
            }
            /* 1. 减小侧边栏整体组件间隙 */
            [data-testid="stSidebar"] [data-testid="stVerticalBlock"] {
                gap: 0.5rem !important;
            }
            /* 2. 减小 container(border=True) 的内边距和内部组件间隙 */
            [data-testid="stSidebar"] [data-testid="stVerticalBlockBorderWrapper"] > div:nth-child(1) {
                padding: 0.7rem 0.6rem !important;
                gap: 0.4rem !important;
            }
            /* 3. 压低 Subheader 的外边距，让它贴近下方的卡片 */
            [data-testid="stSidebar"] h3 {
                margin-bottom: -0.5rem !important;
                margin-top: 0.5rem !important;
                font-size: 1.1rem !important;
            }
            /* 3.1 针对第一个 Subheader 的特殊对齐 */
            [data-testid="stSidebar"] [data-testid="stVerticalBlock"] > div:first-child h3 {
                margin-top: 0 !important;
            }
            /* 4. 微调 Divider(hr) 的上下边距，增加下方间距 */
            [data-testid="stSidebar"] hr {
                margin: 0.5rem 0 0.9rem 0 !important;
            }
            /* 5. 针对某些特定组件的底部间距微调 */
            [data-testid="stSidebar"] .stCheckbox, [data-testid="stSidebar"] .stWidget {
                margin-bottom: -0.3rem !important;
            }
            /* 6. 缩减侧边栏按钮高度并实现与文字对齐 */
            [data-testid="stSidebar"] button {
                height: 1.85rem !important;
                min-height: 1.85rem !important;
                padding-top: 0 !important;
                padding-bottom: 0 !important;
                line-height: 1.85rem !important;
            }
            /* 7. 让侧边栏的分列布局（如按钮+文字行）整体垂直居中 */
            [data-testid="stSidebar"] [data-testid="stHorizontalBlock"] {
                align-items: center !important;
            }
            /* 8. 移除 Caption 的默认间距，交由 Flexbox 处理居中 */
            [data-testid="stSidebar"] .stCaption {
                margin: 0 !important;
                padding: 0 !important;
            }
            /* 9. 特别处理品牌列表的 TextArea */
            div[data-testid="stSidebar"] div[data-testid="stTextArea"] {
                margin-bottom: 0 !important;
            }
            </style>
            """,
            unsafe_allow_html=True,
        )

        st.subheader("🔑 核心配置")
        with st.container(border=True):
            api_key = st.text_input(
                "YouTube API Key",
                type="password",
                help="请输入你的 Google Cloud API Key",
            )
            search_query = st.text_input(
                "搜索关键词",
                value="camera",
                help="将在频道内搜索包含该关键词的视频",
            )

        st.subheader("🏷️ 品牌词库")
        with st.container(border=True):
            st.markdown(
                """
                <style>
                div[data-testid="stSidebar"] div[data-testid="stTextArea"] {
                    margin-bottom: 0.5rem !important;
                }
                </style>
                """,
                unsafe_allow_html=True,
            )
            brands_input = st.text_area(
                label="待匹配品牌（每行一个）",
                value="Logitech\nRazer\nElgato\nSony\nCanon\nMicrosoft\nInsta360",
                height=150,
            )
            brands_list = [brand.strip() for brand in brands_input.split("\n") if brand.strip()]
            brand_rules_payload, brand_rules_error = _prepare_brand_rules_state(brands_list)

            btn_col, status_col = st.columns([1, 1.2])
            with btn_col:
                if st.button("高级配置", use_container_width=True):
                    if st.session_state.get("brand_rules_applied_text") is not None:
                        st.session_state["brand_rules_editor_text"] = st.session_state["brand_rules_applied_text"]
                    elif st.session_state.get("brand_rules_editor_text") in (None, ""):
                        st.session_state["brand_rules_editor_text"] = _format_brand_rules_json(
                            build_brand_rules_payload(brands_list)
                        )
                    st.session_state["brand_rules_editor_version"] = st.session_state.get("brand_rules_editor_version", 0) + 1
                    st.session_state["brand_rules_dialog_open"] = True
            
            with status_col:
                status_text = "⚠️ 规则有误" if brand_rules_error else f"✅ {len(brand_rules_payload or [])} 条规则"
                st.caption(status_text)

        if st.session_state.get("brand_rules_dialog_open"):
            _brand_rules_dialog(brands_list)

        if brand_rules_error:
            st.error(brand_rules_error)

        st.subheader("⚙️ 扫描选项")
        with st.container(border=True):
            use_date_filter = st.checkbox("启用日期过滤", value=True)
            if use_date_filter:
                start_date = st.date_input(
                    "起始日期",
                    value=datetime.date.today() - datetime.timedelta(days=365),
                    label_visibility="collapsed"
                )
            else:
                start_date = None

            st.divider()
            
            st.caption("搜索范围")
            enable_full_search = st.toggle(
                "全量扫描",
                value=True,
                help="自动翻页抓取全部搜索结果",
            )
            enable_deep_search = st.toggle(
                "深度解析",
                value=True,
                help="补充视频标签、时长、分类和互动数据",
            )

            st.divider()
            
            st.caption("匹配位置")
            c1, c2, c3 = st.columns(3)
            with c1:
                match_title = st.checkbox("标题", value=True)
            with c2:
                match_description = st.checkbox("简介", value=True)
            with c3:
                match_tags = st.checkbox("标签", value=True)

        st.divider()
        st.caption(f"系统日志 ({log_count} 条)")
        if st.button("📂 查看运行日志", use_container_width=True):
            open_log_dialog()

    return (
        api_key,
        search_query,
        use_date_filter,
        start_date,
        brands_list,
        brand_rules_payload,
        brand_rules_error,
        enable_full_search,
        enable_deep_search,
        match_title,
        match_description,
        match_tags,
    )



def render_main_inputs() -> list[str]:
    st.subheader("👥 KOL 列表")
    with st.container(border=True):
        kols_input = st.text_area(
            label="输入待扫描的频道：支持 Channel Handle（如 @TechSource）或 UC 开头的 Channel ID，每行一个",
            value="@rogerseng\n@JordanHetrick",
            height=150,
        )
    return [kol.strip() for kol in kols_input.split("\n") if kol.strip()]


def render_quota_warning(kol_count: int) -> None:
    if kol_count > 0:
        st.info(
            f"💡 API 配额小贴士：当前输入了 {kol_count} 个 KOL。每次普通搜索预计消耗 100 点配额（每日免费上限 10,000 点）。"
        )


def render_summary_panel() -> any:
    with st.expander("📈 实时监控面板", expanded=True):
        return st.empty()


def update_summary_panel(summary_display, summary_state: dict | None) -> None:
    if not summary_state:
        summary_display.caption("开始提取后，这里会显示进度、异常和结果摘要。")
        return

    stats = summary_state.get("stats", {})
    meta = summary_state.get("meta", {})
    current = summary_state.get("current", {})
    events = summary_state.get("events", [])
    kol_items = summary_state.get("kols", [])
    run_status = summary_state.get("status") or "idle"
    results = summary_state.get("results", [])

    with summary_display.container():
        c1, c2, c3, c4, c5, c6 = st.columns(6)
        c1.metric("KOL", f'{stats.get("processed_kols", 0)}/{stats.get("total_kols", 0)}')
        c2.metric("已解析", stats.get("resolved_kols", 0))
        c3.metric("已跳过", stats.get("skipped_kols", 0))
        c4.metric("失败", stats.get("error_kols", 0))
        c5.metric("候选视频", stats.get("candidate_videos", 0))
        c6.metric("匹配结果", stats.get("matched_rows", 0))

        st.caption(
            f"状态：{_format_run_status(run_status)} | 关键词：{meta.get('search_query', '-')} | "
            f"品牌数：{meta.get('brand_count', 0)} | 发布时间：{meta.get('published_after', 'off')} | "
            f"累计结果：{len(results)}"
        )

        if current:
            st.info(
                f'当前: [{current.get("index", 0)}/{stats.get("total_kols", 0)}] '
                f'{current.get("kol", "-")} | {current.get("stage", "-")}'
            )

        if events:
            lines = [f'[{item.get("time", "--:--:--")}] {item.get("message", "")}' for item in events[-12:]]
            st.code("\n".join(lines), language=None)
        else:
            st.caption("暂无关键事件。")

        if kol_items:
            st.markdown("**KOL 处理情况**")
            status_df = pd.DataFrame(
                [
                    {
                        "KOL": item.get("kol", ""),
                        "状态": _format_kol_status(item.get("status", "pending")),
                        "候选视频": item.get("candidate_count", 0),
                        "匹配结果": item.get("matched_count", 0),
                        "说明": item.get("message", ""),
                    }
                    for item in kol_items
                ]
            )
            st.dataframe(status_df, use_container_width=True, hide_index=True)



def render_last_extract_results(rows: list[dict[str, str]] | None) -> None:
    if rows is None:
        return

    st.subheader("📊 提取结果")
    if not rows:
        st.info("尚未发现符合条件的品牌提及视频。")
        return

    df = pd.DataFrame(rows)
    filtered_df = df.copy()

    st.markdown("**筛选结果**")
    f1, f2, f3, f4 = st.columns([1.2, 1.2, 1.6, 1.0])
    with f1:
        kol_options = sorted(str(value) for value in df.get("KOL 名称", pd.Series(dtype=str)).dropna().unique())
        selected_kols = st.multiselect("KOL 名称", kol_options)
    with f2:
        brand_options = _extract_brand_options(df)
        selected_brands = st.multiselect("品牌", brand_options)
    with f3:
        keyword = st.text_input("关键字", placeholder="搜索视频标题或品牌")
    with f4:
        category_options = sorted(str(value) for value in df.get("分类", pd.Series(dtype=str)).dropna().unique() if str(value).strip())
        selected_categories = st.multiselect("分类", category_options)

    filtered_df = _apply_result_filters(
        df,
        selected_kols=selected_kols,
        selected_brands=selected_brands,
        keyword=keyword,
        selected_categories=selected_categories,
    )

    st.caption(f"显示 {len(filtered_df)} / {len(df)} 条")

    sortable_columns = [
        "发布时间",
        "播放量",
        "点赞数",
        "评论数",
        "视频时长",
        "KOL 名称",
        "视频标题",
    ]
    available_sort_columns = [column for column in sortable_columns if column in filtered_df.columns]

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

        filtered_df = _sort_result_df(filtered_df, sort_by=sort_by, ascending=(sort_direction == "升序"))

    st.dataframe(filtered_df, use_container_width=True)
    csv_data = filtered_df.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        label="下载当前筛选结果 CSV",
        data=csv_data,
        file_name="youtube_brand_mentions.csv",
        mime="text/csv",
        type="primary",
        key="download_last_extract_csv",
    )



def _prepare_brand_rules_state(brands_list: list[str]) -> tuple[list[dict[str, Any]] | None, str | None]:
    auto_payload = build_brand_rules_payload(brands_list)
    auto_text = _format_brand_rules_json(auto_payload)

    current_text = st.session_state.get("brand_rules_editor_text")
    applied_text = st.session_state.get("brand_rules_applied_text")
    last_auto_text = st.session_state.get("brand_rules_editor_last_auto_text")
    if current_text in (None, ""):
        st.session_state["brand_rules_editor_text"] = applied_text if applied_text is not None else auto_text
    elif current_text == last_auto_text and applied_text != current_text:
        st.session_state["brand_rules_editor_text"] = auto_text
    st.session_state["brand_rules_editor_last_auto_text"] = auto_text

    editor_text = st.session_state.get("brand_rules_editor_text", auto_text)
    try:
        payload = parse_brand_rules_json(editor_text)
        st.session_state["brand_rules_payload"] = payload
        st.session_state["brand_rules_error"] = None
    except ValueError as exc:
        st.session_state["brand_rules_payload"] = None
        st.session_state["brand_rules_error"] = str(exc)

    return st.session_state.get("brand_rules_payload"), st.session_state.get("brand_rules_error")



def _render_advanced_brand_rules_editor_content(brands_list: list[str]) -> tuple[list[dict[str, Any]] | None, str | None]:
    st.caption("默认会根据上面的品牌列表生成 brands.json 风格数据；你也可以上传 JSON 后继续编辑。")

    uploaded_file = st.file_uploader(
        "上传 brands.json",
        type=["json"],
        key="brand_rules_upload_file",
        help="上传后会先解析并回填到下方编辑区。",
    )
    if uploaded_file is not None:
        upload_signature = f"{uploaded_file.name}:{hashlib.md5(uploaded_file.getvalue()).hexdigest()}"
        if st.session_state.get("brand_rules_upload_signature") != upload_signature:
            try:
                uploaded_text = uploaded_file.getvalue().decode("utf-8")
                normalized_payload = parse_brand_rules_json(uploaded_text)
                st.session_state["brand_rules_editor_text"] = _format_brand_rules_json(normalized_payload)
                st.session_state["brand_rules_upload_signature"] = upload_signature
                st.session_state["brand_rules_editor_version"] = st.session_state.get("brand_rules_editor_version", 0) + 1
                st.rerun()
            except UnicodeDecodeError:
                st.error("上传的 JSON 文件不是 UTF-8 编码。")
                return None, "上传的 JSON 文件不是 UTF-8 编码。"
            except ValueError as exc:
                st.error(str(exc))
                return None, str(exc)

    editor_text = st.session_state.get("brand_rules_editor_text", "")
    editor_version = st.session_state.get("brand_rules_editor_version", 0)
    if st_ace is not None:
        ace_value = st_ace(
            value=editor_text,
            language="json",
            theme="tomorrow_night",
            height=320,
            key=f"brand_rules_editor_ace_{editor_version}",
            wrap=True,
            auto_update=True,
            font_size=14,
            show_gutter=True,
        )
        if ace_value is not None:
            editor_text = ace_value
            st.session_state["brand_rules_editor_text"] = ace_value
    else:
        st.caption("未安装代码编辑组件，暂时使用普通文本框。")
        editor_text = st.text_area(
            "规则 JSON",
            key="brand_rules_editor_text",
            height=320,
            help="支持 name / aliases / exclude / case_sensitive 字段。aliases 和 exclude 默认留空。",
        )

    try:
        payload = parse_brand_rules_json(editor_text)
        st.caption(f"当前生效规则：{len(payload)} 条")
    except ValueError as exc:
        st.error(str(exc))
        payload = None
        exc_message = str(exc)
    else:
        exc_message = None

    left_spacer, reset_col, confirm_col, right_spacer = st.columns([2.2, 1.2, 1.2, 2.2])
    with reset_col:
        if st.button("重新加载", use_container_width=True):
            st.session_state["brand_rules_editor_text"] = _format_brand_rules_json(build_brand_rules_payload(brands_list))
            st.session_state["brand_rules_upload_signature"] = None
            st.session_state["brand_rules_editor_version"] = st.session_state.get("brand_rules_editor_version", 0) + 1
            st.rerun()
    with confirm_col:
        if st.button("确认", type="primary", use_container_width=True):
            st.session_state["brand_rules_payload"] = payload
            st.session_state["brand_rules_error"] = exc_message
            st.session_state["brand_rules_applied_text"] = _format_brand_rules_json(payload or [])
            st.session_state["brand_rules_dialog_open"] = False
            st.rerun()

    return payload, exc_message



def _format_brand_rules_json(payload: list[dict[str, Any]]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)



def _format_run_status(value: str) -> str:
    return {
        "idle": "未开始",
        "running": "进行中",
        "paused": "已中断，可恢复",
        "completed": "已完成",
        "error": "执行失败",
    }.get(value, value)



def _format_kol_status(value: str) -> str:
    return {
        "pending": "未开始",
        "running": "处理中",
        "success": "完成",
        "skipped": "已跳过",
        "error": "失败",
    }.get(value, value)



def _extract_brand_options(df: pd.DataFrame) -> list[str]:
    values: set[str] = set()
    if "提及的品牌" not in df.columns:
        return []
    for raw in df["提及的品牌"].fillna(""):
        for item in str(raw).split(","):
            brand = item.strip()
            if brand:
                values.add(brand)
    return sorted(values)



def _apply_result_filters(
    df: pd.DataFrame,
    selected_kols: list[str],
    selected_brands: list[str],
    keyword: str,
    selected_categories: list[str],
) -> pd.DataFrame:
    filtered = df.copy()

    if selected_kols and "KOL 名称" in filtered.columns:
        filtered = filtered[filtered["KOL 名称"].astype(str).isin(selected_kols)]

    if selected_brands and "提及的品牌" in filtered.columns:
        brand_set = set(selected_brands)
        filtered = filtered[
            filtered["提及的品牌"].fillna("").map(
                lambda value: bool({item.strip() for item in str(value).split(",") if item.strip()} & brand_set)
            )
        ]

    if selected_categories and "分类" in filtered.columns:
        filtered = filtered[filtered["分类"].astype(str).isin(selected_categories)]

    if keyword.strip():
        needle = keyword.strip().lower()
        title_series = filtered["视频标题"].fillna("").astype(str).str.lower() if "视频标题" in filtered.columns else ""
        brand_series = filtered["提及的品牌"].fillna("").astype(str).str.lower() if "提及的品牌" in filtered.columns else ""
        filtered = filtered[title_series.str.contains(needle, na=False) | brand_series.str.contains(needle, na=False)]

    return filtered.reset_index(drop=True)



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
