import streamlit as st
import pandas as pd
from googleapiclient.discovery import build
import re
import datetime

st.set_page_config(page_title="YouTube Brand Extractor", layout="wide")

st.title("📹 YouTube KOL 品牌提取工具")
st.markdown("该工具基于 YouTube Data API 官方接口，用于自动化提取指定博主视频中提及的相机品牌。")

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

# --- 主页面配置 ---
st.header("4. KOL 列表")
kols_input = st.text_area(
    label="输入 KOL 的 Channel Handle (以 @ 开头) 或 Channel ID (每行一个)", 
    value="@TechSource\n@MKBHD", 
    height=150
)
kol_list = [k.strip() for k in kols_input.split('\n') if k.strip()]

st.warning(f"⚠️ **API 配额提醒**：您输入了 {len(kol_list)} 个 KOL。每次搜索消耗 100 点配额。请确保您的 API Key 额度充足（免费账户每日上限 10,000 点）。")

# --- 核心功能函数 ---
@st.cache_resource(show_spinner=False)
def get_youtube_service(api_key):
    try:
        return build('youtube', 'v3', developerKey=api_key)
    except Exception as e:
        return None

def resolve_channel_id(youtube, handle):
    """将 @handle 转换为 Channel ID"""
    if not handle.startswith('@'):
        return handle  # 假设没带 @ 的已经是 Channel ID 了
        
    try:
        # 通过 search 接口查找 handle 对应的 channel
        request = youtube.search().list(
            part="snippet",
            q=handle,
            type="channel",
            maxResults=1
        )
        response = request.execute()
        if response.get('items'):
            return response['items'][0]['snippet']['channelId']
        return None
    except Exception as e:
        st.error(f"解析 Handle {handle} 失败: {e}")
        return None

def extract_brands(text, brands):
    """使用正则边界匹配品牌，忽略大小写"""
    if not text:
        return []
    text = str(text)
    found_brands = set()
    for brand in brands:
        # \b 表示单词边界，防止类似 "credit" 匹配到 "red"
        pattern = r'\b' + re.escape(brand) + r'\b'
        if re.search(pattern, text, re.IGNORECASE):
            found_brands.add(brand)
    return list(found_brands)

# --- 执行逻辑 ---
if st.button("🚀 开始提取", type="primary"):
    if not api_key:
        st.error("请在左侧侧边栏填入 API Key！")
        st.stop()
        
    if not kol_list:
        st.error("请填入至少一个 KOL！")
        st.stop()
        
    youtube = get_youtube_service(api_key)
    if not youtube:
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
    
    total_kols = len(kol_list)
    
    for i, kol in enumerate(kol_list):
        status_text.text(f"⏳ 正在处理 ({i+1}/{total_kols}): {kol} ... (解析 Channel ID)")
        
        channel_id = resolve_channel_id(youtube, kol)
        if not channel_id:
            st.warning(f"⚠️ 无法找到 {kol} 的 Channel ID，已跳过。")
            progress_bar.progress((i + 1) / total_kols)
            continue
            
        status_text.text(f"⏳ 正在处理 ({i+1}/{total_kols}): {kol} ... (搜索视频)")
        
        try:
            # 调用 Search API (这里极度消耗配额，一次 100 点)
            request = youtube.search().list(
                part="snippet",
                channelId=channel_id,
                q=search_query,
                type="video",
                maxResults=50,  # 默认抓取前 50 个匹配视频，避免无限翻页耗尽配额
                publishedAfter=published_after_str
            )
            response = request.execute()
            
            items = response.get('items', [])
            if not items:
                st.info(f"ℹ️ {kol} 频道下未找到包含 '{search_query}' 的视频。")
            
            for item in items:
                title = item['snippet']['title']
                description = item['snippet']['description']
                video_id = item['id']['videoId']
                published_at = item['snippet']['publishedAt']
                video_url = f"https://youtu.be/{video_id}"
                
                # 拼接标题和简介，用于品牌匹配
                combined_text = title + " \n " + description
                mentioned_brands = extract_brands(combined_text, brands_list)
                
                # 如果匹配到了品牌，才加入结果集
                if mentioned_brands:
                    results.append({
                        "KOL 名称": kol,
                        "视频标题": title,
                        "视频链接": video_url,
                        "提及的品牌": ", ".join(mentioned_brands),
                        "发布时间": published_at[:10]  # 仅保留日期 YYYY-MM-DD
                    })
                    
        except Exception as e:
            error_msg = str(e)
            if "quotaExceeded" in error_msg:
                st.error(f"❌ 严重错误: YouTube API 配额已耗尽！\n本次任务被迫终止于 {kol}。请明天再试或更换 API Key。")
                break
            else:
                st.error(f"❌ 处理 {kol} 时发生未知错误: {e}")
                
        # 更新进度条
        progress_bar.progress((i + 1) / total_kols)
        
    status_text.success("✅ 所有 KOL 抓取完毕！")
    
    # --- 结果展示与导出 ---
    st.header("5. 提取结果")
    if results:
        df = pd.DataFrame(results)
        st.dataframe(df, use_container_width=True)
        
        # 将 DataFrame 转换为 CSV 以供下载 (支持 utf-8-sig 以兼容 Excel 乱码问题)
        csv_data = df.to_csv(index=False).encode('utf-8-sig')
        
        st.download_button(
            label="📥 一键下载为 CSV 文件 (可直接用 Excel 打开)",
            data=csv_data,
            file_name='youtube_brand_mentions.csv',
            mime='text/csv',
            type="primary"
        )
    else:
        st.info("没有找到任何符合品牌匹配条件的视频。")