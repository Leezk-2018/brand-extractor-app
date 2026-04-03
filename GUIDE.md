# YouTube KOL 品牌提取工具

这是一个基于 Streamlit 和 YouTube Data API v3 的内部工具，用于按 KOL 批量搜索视频、识别标题与描述中的品牌提及，并导出结构化结果。

启动后访问 `http://localhost:8501`。

## 页面操作方法

### 1. 侧边栏配置

- `YouTube Data API Key`：填入可用的 API Key。
- `搜索关键词`：例如 `camera`、`microphone`，用于 `search.list` 查询。
- `启用时间过滤`：打开后仅保留某日期之后发布的视频。
- `品牌列表`：每行一个品牌名。这里输入的是“要启用哪些品牌规则”。

### 2. KOL 输入

在主区域的 `KOL 列表` 中，每行一个账号，支持：

- `@JordanHetrick`
- `JordanHetrick`
- `UC...` 形式的 Channel ID

### 3. 开始提取

点击 `开始提取` 后，页面会依次显示：

- `运行摘要`：当前处理到哪个 KOL、抓了多少候选视频、匹配了多少条、分页进度。
- `日志详情`：可按关键字、类型、等级筛选，支持下载日志。
- `提取结果`：支持按发布时间、播放量、点赞数、评论数、时长等排序，并导出 CSV。

## 结果字段

当前结果表包含：

- `KOL 名称`
- `视频标题`
- `视频链接`
- `提及的品牌`
- `匹配详情`
- `视频时长`
- `播放量`
- `点赞数`
- `评论数`
- `分类ID`
- `分类`
- `标签`
- `发布时间`

## brands.json 配置

`brands.json` 用来定义品牌的标准名、别名和排除词。页面里输入的品牌名会优先去这里匹配规则；如果找不到，则退化为“品牌名本身就是唯一匹配词”。

示例：

```json
[
  {
    "name": "Sony",
    "aliases": ["Sony", "Sony Alpha"],
    "exclude": []
  },
  {
    "name": "Red",
    "aliases": ["RED", "RED camera"],
    "exclude": ["credit", "reddit"]
  }
]
```

字段说明：

- `name`：结果表里展示的标准品牌名。
- `aliases`：可命中的写法列表。
- `exclude`：命中这些词时阻断该品牌匹配，适合处理歧义词。

## 接口与配额说明

- `search.list`：按频道搜索视频，单次请求成本高，并且本项目会自动翻页抓取全部结果。
- `videos.list`：补充时长、播放量、点赞数、评论数、标签等，成本相对低。
- `videoCategories.list`：把分类 ID 映射为可读分类名称。

如果某个频道搜索结果很多，分页次数会增加，运行摘要和日志中都会记录分页进度。

## 基础校验

```bash
python -m py_compile app.py app_ui.py extractor_core.py brand_rules.py
python -m unittest tests.test_extractor_core test_brand_rules test_pagination
```
