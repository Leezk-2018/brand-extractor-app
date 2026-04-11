import datetime
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from brand_rules import (
    BrandMatchDetail,
    BrandRule,
    build_rules_for_names,
    evaluate_brand_matches,
    load_brand_rules,
    match_brands,
)

LogFn = Callable[[str], None]
LogJsonFn = Callable[[str, Any], None]
PageProgressFn = Callable[[int, int, bool], None]
QuotaTrackerFn = Callable[[str, int, dict[str, Any]], None]
DEFAULT_BRAND_RULES_PATH = Path(__file__).with_name("brands.json")
CHANNEL_ID_RE_PATTERN = r"^UC[A-Za-z0-9_-]{22}$"
CHANNEL_ID_RE = re.compile(CHANNEL_ID_RE_PATTERN)


@dataclass
class KolProcessingResult:
    kol: str
    channel_id: str | None = None
    candidate_count: int = 0
    matched_count: int = 0
    rows: list[dict[str, str]] = field(default_factory=list)


def get_youtube_service(api_key: str, logger: logging.Logger | None = None):
    try:
        from googleapiclient.discovery import build

        return build("youtube", "v3", developerKey=api_key)
    except Exception:
        if logger is not None:
            logger.info("lee-debug get_youtube_service build failed", exc_info=True)
        return None


class YouTubeManager:
    def __init__(self, api_keys: list[str], logger: logging.Logger | None = None):
        self.api_keys = [k.strip() for k in api_keys if k.strip()]
        self.current_index = 0
        self.logger = logger
        self._current_service = None
        self._find_next_valid_service()
        
    def _build(self, index: int):
        if not self.api_keys or index >= len(self.api_keys):
            return None
        return get_youtube_service(self.api_keys[index], self.logger)

    def _find_next_valid_service(self):
        """寻找下一个能够成功初始化的服务对象"""
        while self.current_index < len(self.api_keys):
            service = self._build(self.current_index)
            if service:
                self._current_service = service
                return
            
            if self.logger:
                self.logger.info(f"lee-debug API key #{self.current_index + 1} initialization failed, skipping...")
            self.current_index += 1
        
        self._current_service = None

    def execute(self, build_request_fn: Callable[[Any], Any]) -> Any:
        while True:
            if not self._current_service:
                raise Exception("所有提供的 API Key 均无效或配额已耗尽 (quotaExceeded)")
            
            try:
                request = build_request_fn(self._current_service)
                return request.execute()
            except Exception as e:
                error_msg = str(e) or ""
                is_skippable = False
                reason = "unknown"
                
                # 1. 检查异常消息字符串中的关键词
                # 包含配额耗尽、无效 Key、已过期、被封禁等情况
                keywords = ["quotaExceeded", "API key not valid", "keyInvalid", "API key expired", "missing a valid API key"]
                for kw in keywords:
                    if kw.lower() in error_msg.lower():
                        is_skippable = True
                        reason = kw
                        break
                
                # 2. 检查 HttpError 的具体状态码和内容
                if not is_skippable:
                    try:
                        from googleapiclient.errors import HttpError
                        if isinstance(e, HttpError):
                            # 400: Invalid Key, 403: Quota/Forbidden, 429: Rate Limit
                            if e.resp.status in (400, 403, 429):
                                is_skippable = True
                                reason = f"HTTP {e.resp.status}"
                    except:
                        pass
                
                if is_skippable:
                    if self.logger:
                        self.logger.info(f"lee-debug Key #{self.current_index + 1} failed ({reason}), trying next...")
                    
                    self.current_index += 1
                    self._find_next_valid_service()
                    continue
                
                # 非跳过类错误，重新抛出
                final_msg = error_msg or repr(e)
                if self.logger:
                    self.logger.info(f"lee-debug execution failed: {final_msg}")
                raise Exception(final_msg) from e


def build_published_after(start_date: datetime.date | None) -> str | None:
    if not start_date:
        return None
    return start_date.strftime("%Y-%m-%dT00:00:00Z")


def load_selected_brand_rules(
    brand_names: list[str],
    rules_path: str | Path | None = None,
) -> list[BrandRule]:
    path = Path(rules_path) if rules_path is not None else DEFAULT_BRAND_RULES_PATH
    rule_map = load_brand_rules(path) if path.exists() else {}
    return build_rules_for_names(brand_names, rule_map=rule_map)


def _parse_kol_input(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    
    if text.startswith("http://") or text.startswith("https://"):
        parsed = urlparse(text)
        netloc = parsed.netloc.lower()
        if not ("youtube.com" in netloc or "youtu.be" in netloc):
            raise ValueError(f"不支持非 YouTube 链接: {text}")
        
        path = parsed.path.strip("/")
        parts = path.split("/")
        
        if parts:
            if parts[0].startswith("@"):
                return parts[0]
            elif parts[0] in ("c", "channel", "user") and len(parts) > 1:
                return parts[1]
            else:
                return parts[0]
        else:
            raise ValueError(f"无法从链接中解析出频道信息: {text}")
            
    return text


def resolve_channel_id(
    youtube_manager,
    raw: str,
    log_detail: LogFn | None = None,
    log_json: LogJsonFn | None = None,
    quota_tracker: QuotaTrackerFn | None = None,
) -> str | None:
    try:
        handle = _parse_kol_input(raw)
    except ValueError as e:
        _log(log_detail, f"resolve_channel_id input error: {e}")
        raise

    if not handle:
        return None

    if CHANNEL_ID_RE.match(handle):
        _log(log_detail, f"resolve_channel_id literal_uc_channel_id: {handle!r}")
        return handle

    slug = handle[1:] if handle.startswith("@") else handle
    ch_body = {"part": "id", "forHandle": slug}
    _log(log_detail, f"resolve_channel_id request channels.list: {json.dumps(ch_body, ensure_ascii=False)}")
    try:
        _track_quota(quota_tracker, "channels.list", 1, {"handle": slug})
        ch_resp = youtube_manager.execute(lambda yt: yt.channels().list(part="id", forHandle=slug))
        _log_json(log_json, "resolve_channel_id response channels.list", ch_resp)
        ch_items = ch_resp.get("items") or []
        if ch_items:
            channel_id = ch_items[0]["id"]
            _log(log_detail, f"resolve_channel_id forHandle {handle!r} -> channelId={channel_id!r}")
            return channel_id
        _log(log_detail, f"resolve_channel_id channels.list empty forHandle={slug!r}, fallback search.list")
    except Exception as exc:
        _log(
            log_detail,
            f"resolve_channel_id channels.list failed forHandle={slug!r} err={exc!r}, fallback search.list",
        )

    query = handle if handle.startswith("@") else f"@{slug}"
    channel_search_body = {
        "part": "snippet",
        "q": query,
        "type": "channel",
        "maxResults": 1,
    }
    _log(
        log_detail,
        f"resolve_channel_id request search.list: {json.dumps(channel_search_body, ensure_ascii=False)}",
    )

    _track_quota(quota_tracker, "search.list.channel", 100, {"handle": handle, "query": query})
    response = youtube_manager.execute(lambda yt: yt.search().list(
        part="snippet",
        q=query,
        type="channel",
        maxResults=1,
    ))
    _log_json(log_json, "resolve_channel_id response search.list", response)
    if response.get("items"):
        channel_id = response["items"][0]["snippet"]["channelId"]
        _log(log_detail, f"resolve_channel_id search mapped {handle!r} -> channelId={channel_id!r}")
        return channel_id

    _log(log_detail, f"resolve_channel_id search empty items for {handle!r}")
    return None


def extract_brands(
    text: str,
    brands: list[BrandRule],
    log_detail: LogFn | None = None,
) -> list[str]:
    if not text:
        _log(log_detail, "extract_brands: empty text matched=[]")
        return []

    normalized_text = str(text)
    rules = _ensure_brand_rules(brands)
    matches = match_brands(normalized_text, rules)
    _log(log_detail, f"extract_brands: text_len={len(normalized_text)} dict_size={len(rules)} matched={matches!r}")
    return matches


def explain_brand_matches_for_video(
    title: str,
    description: str,
    tags: list[str] | None,
    brands: list[BrandRule],
    match_title: bool = True,
    match_description: bool = True,
    match_tags: bool = True,
    log_detail: LogFn | None = None,
) -> list[BrandMatchDetail]:
    rules = _ensure_brand_rules(brands)
    evaluations: list[BrandMatchDetail] = []
    if match_title:
        evaluations.extend(evaluate_brand_matches(title or "", rules, source="title"))
    if match_description:
        evaluations.extend(evaluate_brand_matches(description or "", rules, source="description"))
    if match_tags:
        evaluations.extend(evaluate_brand_matches(" ".join(tags or []), rules, source="tags"))
    matched = _dedupe_match_details([item for item in evaluations if item.excluded_by is None])
    blocked = _dedupe_match_details([item for item in evaluations if item.excluded_by is not None])
    _log(
        log_detail,
        f"explain_brand_matches_for_video: matched={[_detail_to_log_payload(item) for item in matched]!r} blocked={[_detail_to_log_payload(item) for item in blocked]!r}",
    )
    return matched


def search_channel_brand_mentions(
    youtube_manager,
    kol: str,
    search_query: str,
    brands: list[BrandRule],
    published_after: str | None,
    enable_full_search: bool = False,
    enable_deep_search: bool = False,
    match_title: bool = True,
    match_description: bool = True,
    match_tags: bool = True,
    log_detail: LogFn | None = None,
    log_json: LogJsonFn | None = None,
    page_progress: PageProgressFn | None = None,
    quota_tracker: QuotaTrackerFn | None = None,
) -> KolProcessingResult:
    # 提取核心标识（处理 URL 情况），使结果表中使用提取后的 Handle/ID
    try:
        kol = _parse_kol_input(kol)
    except ValueError:
        # 如果解析失败，这里先不做处理，留给 resolve_channel_id 统一抛出或处理
        pass

    result = KolProcessingResult(kol=kol)
    rules = _ensure_brand_rules(brands)
    result.channel_id = resolve_channel_id(
        youtube_manager,
        kol,
        log_detail=log_detail,
        log_json=log_json,
        quota_tracker=quota_tracker,
    )
    if not result.channel_id:
        return result

    video_search_body = {
        "part": "snippet",
        "channelId": result.channel_id,
        "q": search_query,
        "type": "video",
        "maxResults": 50,
        "publishedAfter": published_after,
    }
    items = _fetch_all_search_video_items(
        youtube_manager,
        kol=kol,
        request_body=video_search_body,
        enable_full_search=enable_full_search,
        log_detail=log_detail,
        log_json=log_json,
        page_progress=page_progress,
        quota_tracker=quota_tracker,
    )
    enriched_by_video_id: dict[str, dict[str, Any]] = {}
    category_map: dict[str, str] = {}
    if enable_deep_search:
        enriched_by_video_id = _fetch_video_details_map(
            youtube_manager,
            kol=kol,
            items=items,
            log_detail=log_detail,
            log_json=log_json,
            quota_tracker=quota_tracker,
        )
        category_map = _fetch_video_category_map(
            youtube_manager,
            kol=kol,
            video_details=enriched_by_video_id,
            log_detail=log_detail,
            log_json=log_json,
            quota_tracker=quota_tracker,
        )
    else:
        _log(log_detail, f"videos.list skipped kol={kol!r} enable_deep_search=False")
    result.candidate_count = len(items)
    _log(log_detail, f"video search parsed: items_count={result.candidate_count} kol={kol!r}")

    for item in items:
        row = _build_result_row(
            kol,
            item,
            rules,
            video_detail=enriched_by_video_id.get(item["id"]["videoId"]),
            category_map=category_map,
            match_title=match_title,
            match_description=match_description,
            match_tags=match_tags,
            log_detail=log_detail,
        )
        if row is None:
            continue
        result.matched_count += 1
        result.rows.append(row)

    return result


def _fetch_all_search_video_items(
    youtube_manager,
    kol: str,
    request_body: dict[str, Any],
    enable_full_search: bool = False,
    log_detail: LogFn | None = None,
    log_json: LogJsonFn | None = None,
    page_progress: PageProgressFn | None = None,
    quota_tracker: QuotaTrackerFn | None = None,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    page_token: str | None = None
    page_number = 1

    while True:
        request_payload = dict(request_body)
        if page_token:
            request_payload["pageToken"] = page_token

        _log(
            log_detail,
            f"video search.list request page={page_number} kol={kol!r}: {json.dumps(request_payload, ensure_ascii=False)}",
        )
        _track_quota(quota_tracker, "search.list.video", 100, {"kol": kol, "page": page_number})
        
        response = youtube_manager.execute(lambda yt: yt.search().list(**request_payload))
        _log_json(log_json, f"video search.list response kol={kol!r} page={page_number}", response)

        page_items = response.get("items", [])
        items.extend(page_items)
        next_page_token = response.get("nextPageToken")
        _log(
            log_detail,
            f"video search.list page={page_number} kol={kol!r} fetched={len(page_items)} total={len(items)} nextPageToken={next_page_token!r}",
        )
        if page_progress is not None:
            page_progress(page_number, len(items), bool(next_page_token))

        if not next_page_token:
            break
        if not enable_full_search:
            _log(
                log_detail,
                f"video search.list stop after first page kol={kol!r} enable_full_search=False nextPageToken={next_page_token!r}",
            )
            break

        page_token = next_page_token
        page_number += 1

    return items


def _fetch_video_details_map(
    youtube_manager,
    kol: str,
    items: list[dict[str, Any]],
    log_detail: LogFn | None = None,
    log_json: LogJsonFn | None = None,
    quota_tracker: QuotaTrackerFn | None = None,
) -> dict[str, dict[str, Any]]:
    video_ids = [item["id"]["videoId"] for item in items if item.get("id", {}).get("videoId")]
    if not video_ids:
        return {}

    details_by_id: dict[str, dict[str, Any]] = {}
    batch_size = 50
    for offset in range(0, len(video_ids), batch_size):
        batch_ids = video_ids[offset : offset + batch_size]
        request_payload = {
            "part": "snippet,contentDetails,statistics",
            "id": ",".join(batch_ids),
            "maxResults": len(batch_ids),
        }
        batch_number = offset // batch_size + 1
        _log(
            log_detail,
            f"videos.list request batch={batch_number} kol={kol!r} ids={len(batch_ids)}",
        )
        _track_quota(quota_tracker, "videos.list", 1, {"kol": kol, "batch": batch_number, "count": len(batch_ids)})
        
        response = youtube_manager.execute(lambda yt: yt.videos().list(**request_payload))
        _log_json(log_json, f"videos.list response kol={kol!r} batch={batch_number}", response)
        for detail in response.get("items", []):
            details_by_id[detail["id"]] = detail
        _log(
            log_detail,
            f"videos.list batch={batch_number} kol={kol!r} fetched={len(response.get('items', []))} total={len(details_by_id)}",
        )

    return details_by_id


def _fetch_video_category_map(
    youtube_manager,
    kol: str,
    video_details: dict[str, dict[str, Any]],
    log_detail: LogFn | None = None,
    log_json: LogJsonFn | None = None,
    quota_tracker: QuotaTrackerFn | None = None,
) -> dict[str, str]:
    category_ids = sorted(
        {
            detail.get("snippet", {}).get("categoryId", "")
            for detail in video_details.values()
            if detail.get("snippet", {}).get("categoryId")
        }
    )
    if not category_ids:
        return {}

    request_payload = {
        "part": "snippet",
        "id": ",".join(category_ids),
    }
    _log(
        log_detail,
        f"videoCategories.list request kol={kol!r} ids={category_ids!r}",
    )
    _track_quota(quota_tracker, "videoCategories.list", 1, {"kol": kol, "count": len(category_ids)})
    
    response = youtube_manager.execute(lambda yt: yt.videoCategories().list(**request_payload))
    _log_json(log_json, f"videoCategories.list response kol={kol!r}", response)
    mapping = {
        item["id"]: item.get("snippet", {}).get("title", "")
        for item in response.get("items", [])
    }
    _log(
        log_detail,
        f"videoCategories.list kol={kol!r} fetched={len(mapping)}",
    )
    return mapping


def _build_result_row(
    kol: str,
    item: dict[str, Any],
    brands: list[BrandRule],
    video_detail: dict[str, Any] | None = None,
    category_map: dict[str, str] | None = None,
    match_title: bool = True,
    match_description: bool = True,
    match_tags: bool = True,
    log_detail: LogFn | None = None,
) -> dict[str, str] | None:
    snippet = item["snippet"]
    title = snippet["title"]
    description = snippet["description"]
    video_id = _extract_video_id(item)
    published_at = snippet["publishedAt"]
    if not video_id:
        _log(log_detail, f"result_row skip missing_video_id title={title[:80]!r}",)
        return None
    video_url = f"https://www.youtube.com/watch?v={video_id}"
    detail_snippet = (video_detail or {}).get("snippet", {})
    detail_content = (video_detail or {}).get("contentDetails", {})
    detail_stats = (video_detail or {}).get("statistics", {})
    detail_tags = detail_snippet.get("tags", [])
    match_details = explain_brand_matches_for_video(
        title,
        description,
        detail_tags,
        brands,
        match_title=match_title,
        match_description=match_description,
        match_tags=match_tags,
        log_detail=log_detail,
    )
    mentioned_brands = _dedupe_brand_names([detail.name for detail in match_details])
    category_id = detail_snippet.get("categoryId", "")

    if not mentioned_brands:
        _log(log_detail, f"result_row skip no_brand_match video_id={video_id!r} title={title[:80]!r}")
        return None

    _log(
        log_detail,
        f"result_row append video_id={video_id!r} brands={mentioned_brands!r} details={[_detail_to_log_payload(item) for item in match_details]!r}",
    )
    return {
        "KOL 名称": kol,
        "视频标题": title,
        "视频链接": video_url,
        "提及的品牌": ", ".join(mentioned_brands),
        "匹配详情": "; ".join(_format_match_detail(detail) for detail in match_details),
        "视频时长": _format_duration(detail_content.get("duration", "")),
        "播放量": _format_count(detail_stats.get("viewCount", "")),
        "点赞数": _format_count(detail_stats.get("likeCount", "")),
        "评论数": _format_count(detail_stats.get("commentCount", "")),
        "分类ID": category_id,
        "分类": (category_map or {}).get(category_id, ""),
        "标签": ", ".join(detail_snippet.get("tags", [])),
        "发布时间": published_at[:10],
    }


def _log(log_fn: LogFn | None, message: str) -> None:
    if log_fn is not None:
        log_fn(message)


def _log_json(log_json_fn: LogJsonFn | None, label: str, payload: Any) -> None:
    if log_json_fn is not None:
        log_json_fn(label, payload)


def _track_quota(quota_tracker: QuotaTrackerFn | None, api_name: str, units: int, context: dict[str, Any]) -> None:
    if quota_tracker is not None:
        quota_tracker(api_name, units, context)


def _ensure_brand_rules(brands: list[BrandRule]) -> list[BrandRule]:
    if not brands:
        return []
    if not isinstance(brands[0], BrandRule):
        raise TypeError("brands must be preloaded BrandRule objects")
    return list(brands)


def _dedupe_match_details(details: list[BrandMatchDetail]) -> list[BrandMatchDetail]:
    seen: set[tuple[str, str, str]] = set()
    deduped: list[BrandMatchDetail] = []
    for detail in details:
        key = (detail.name, detail.alias, detail.source)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(detail)
    return deduped


def _dedupe_brand_names(names: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        deduped.append(name)
    return deduped


def _detail_to_log_payload(detail: BrandMatchDetail) -> dict[str, str]:
    payload = {
        "name": detail.name,
        "alias": detail.alias,
        "source": detail.source,
    }
    if detail.excluded_by:
        payload["excluded_by"] = detail.excluded_by
    return payload


def _format_match_detail(detail: BrandMatchDetail) -> str:
    source_label_map = {
        "title": "标题",
        "description": "描述",
        "tags": "标签",
    }
    source_label = source_label_map.get(detail.source, detail.source)
    alias = detail.alias or detail.name
    return f"{detail.name}：命中{source_label}（{alias}）"


def _extract_video_id(item: dict[str, Any]) -> str:
    raw_id = item.get("id")
    if isinstance(raw_id, dict):
        return str(raw_id.get("videoId", "") or "")
    return str(raw_id or "")


def _format_duration(value: str) -> str:
    if not value:
        return ""
    match = re.fullmatch(
        r"P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?",
        value,
    )
    if not match:
        return value
    days = int(match.group("days") or 0)
    hours = int(match.group("hours") or 0)
    minutes = int(match.group("minutes") or 0)
    seconds = int(match.group("seconds") or 0)
    total_seconds = days * 86400 + hours * 3600 + minutes * 60 + seconds
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def _format_count(value: str | int) -> str:
    if value in ("", None):
        return ""
    try:
        return f"{int(value):,}"
    except Exception:
        return str(value)
