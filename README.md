# YouTube KOL Brand Extractor 📹

一款基于 **YouTube Data API v3** 和 **Streamlit** 构建的自动化品牌提及提取工具。专门为市场运营和竞品分析人员设计，用于快速从指定 KOL 频道中识别并整理特定品类（如 Camera、Microphone 等）的品牌提及情况。

![Python](https://img.shields.io/badge/Python-3.8+-blue.svg)
![Streamlit](https://img.shields.io/badge/Streamlit-1.32+-red.svg)
![API](https://img.shields.io/badge/YouTube%20API-v3-green.svg)

---

## ✨ 核心功能

*   **🔍 定向频道搜索**：支持通过博主 Handle（如 `@TechSource`）或 Channel ID 锁定特定 KOL。
*   **📅 智能时间过滤**：可自定义搜索起始日期，只分析近期视频，排除过时信息。
*   **🏷️ 精准品牌识别**：基于正则表达式（Word Boundary）进行品牌词匹配，有效区分易混淆词汇（如匹配 `Red` 而非 `Credit`）。
*   **动态品牌词典**：在 UI 界面实时修改和增加待提取的品牌列表。
*   **📊 结构化导出**：一键生成包含 `KOL名称`、`视频标题`、`链接`、`品牌` 和 `发布日期` 的 CSV 表格。
*   **🍎 现代化交互**：基于 Streamlit 的极简 Web 界面，带实时进度条和数据预览。

---

## 🚀 快速上手

### 1. 克隆项目
```bash
git clone https://github.com/your-username/youtube-brand-extractor.git
cd youtube-brand-extractor
```

### 2. 环境配置
建议使用虚拟环境：
```bash
# 创建虚拟环境
python -m venv venv

# 激活虚拟环境 (Windows)
.\venv\Scripts\activate

# 激活虚拟环境 (Mac/Linux)
source venv/bin/activate

# 安装依赖
pip install -r requirements.txt
```

### 3. 获取 API Key
1. 前往 [Google Cloud Console](https://console.cloud.google.com/)。
2. 创建一个新项目并启用 **YouTube Data API v3**。
3. 在“凭据”页面创建一个 **API 密钥 (API Key)**。

### 4. 运行应用
```bash
streamlit run app.py
```
启动后，浏览器会自动打开 `http://localhost:8501`。

---

## 🛠️ 技术选型

*   **Frontend**: [Streamlit](https://streamlit.io/) - 极速构建交互式数据应用。
*   **Data Source**: [Official YouTube Data API](https://developers.google.com/youtube/v3) - 官方接口，稳定可靠。
*   **Processing**: [Pandas](https://pandas.pydata.org/) - 数据清洗与结构化导出。
*   **Regex**: 实现忽略大小写及边界感知的精准品牌提取。

---

## ⚠️ 重要说明：API 配额限制 (Quota)

本工具使用的是 YouTube 官方的 `search.list` 接口。
*   **消耗消耗**：针对每个 KOL 的一次搜索请求会消耗 **100 点配额**。
*   **免费上限**：Google 免费账户每日默认配额为 **10,000 点**。
*   **建议**：如果你有大量的 KOL 列表（超过 100 个），建议分批次运行或准备多个 API Key。

---

## 📄 开源协议

本项目采用 [MIT License](LICENSE) 协议。

---

## 🤝 贡献与反馈

欢迎提交 Issue 或 Pull Request 来完善这个工具！如果你觉得好用，请给个 ⭐ Star。
