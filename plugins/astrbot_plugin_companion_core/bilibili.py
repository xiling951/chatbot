"""Anonymous metadata only. No browser cookies, account actions or video download."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import socket
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx


ORIGIN = "https://api.bilibili.com"
VIDEO = re.compile(r"BV[0-9A-Za-z]{10}")
MAX_BODY = 256 * 1024


class ReadFailed(ValueError):
    """A bounded failure category, without raw response or request secrets."""


def video_id(value: str) -> str:
    value = value.strip()
    if VIDEO.fullmatch(value):
        return value
    url = urlsplit(value)
    if (
        url.scheme != "https"
        or url.netloc not in ("www.bilibili.com", "bilibili.com")
        or url.query
        or url.fragment
    ):
        raise ValueError(
            "请提供BV号或不带参数的 https://www.bilibili.com/video/BV号 链接。"
        )
    match = re.fullmatch(r"/video/(BV[0-9A-Za-z]{10})/?", url.path)
    if not match:
        raise ValueError("仅接受标准BV视频链接，不打开短链、文件或其他地址。")
    return match[1]


async def public_origin():
    records = await asyncio.wait_for(
        asyncio.get_running_loop().getaddrinfo(
            "api.bilibili.com", 443, type=socket.SOCK_STREAM
        ),
        timeout=3,
    )
    if not records or any(not ipaddress.ip_address(r[4][0]).is_global for r in records):
        raise ReadFailed("public_dns_required")


def limited(value, size):
    if not isinstance(value, str):
        raise ReadFailed("invalid_metadata")
    return value.strip().encode("utf-8")[:size].decode("utf-8", errors="ignore")


@dataclass(frozen=True)
class VideoMetadata:
    bvid: str
    url: str
    title: str
    author: str
    description: str
    fetched_at: str
    source_type: str = "external_metadata"
    read_scope: str = "标题、作者、简介；未读取视频正文、字幕、评论或弹幕"

    def data(self):
        return asdict(self) | {"summary": "简介节选：" + limited(self.description, 300)}


class BilibiliReader:
    def __init__(self, *, transport=None, origin_check=public_origin):
        self.transport, self.origin_check = transport, origin_check

    async def read(self, value: str) -> VideoMetadata:
        bvid = video_id(value)
        try:
            # Only a fixed public API origin is ever contacted. Response URLs,
            # redirects and fields cannot cause any further network operation.
            await self.origin_check()
            async with httpx.AsyncClient(
                transport=self.transport or httpx.AsyncHTTPTransport(retries=0),
                timeout=httpx.Timeout(10, connect=5),
                follow_redirects=False,
                trust_env=False,
                headers={
                    "User-Agent": "CompanionCore/0.5 anonymous-metadata",
                    "Accept": "application/json",
                    "Accept-Encoding": "identity",
                },
            ) as client:
                async with client.stream(
                    "GET", ORIGIN + "/x/web-interface/view", params={"bvid": bvid}
                ) as response:
                    if response.status_code != 200:
                        raise ReadFailed("http_" + str(response.status_code))
                    if (
                        response.headers.get("content-encoding", "identity")
                        != "identity"
                    ):
                        raise ReadFailed("compressed_response_refused")
                    if (
                        response.headers.get("content-type", "")
                        .split(";", 1)[0]
                        .strip()
                        != "application/json"
                    ):
                        raise ReadFailed("non_json_response")
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > MAX_BODY:
                            raise ReadFailed("response_too_large")
            payload = json.loads(body)
            if (
                not isinstance(payload, dict)
                or type(payload.get("code")) is not int
                or payload["code"] != 0
            ):
                raise ReadFailed("api_rejected")
            data = payload.get("data")
            if not isinstance(data, dict) or data.get("bvid") != bvid:
                raise ReadFailed("mismatched_video")
            owner = data.get("owner")
            if not isinstance(owner, dict):
                raise ReadFailed("missing_author")
            title, author = (
                limited(data.get("title"), 240),
                limited(owner.get("name"), 120),
            )
            if not title or not author:
                raise ReadFailed("missing_metadata")
            return VideoMetadata(
                bvid,
                f"https://www.bilibili.com/video/{bvid}",
                title,
                author,
                limited(data.get("desc", ""), 900),
                datetime.now(timezone.utc).isoformat(),
            )
        except ReadFailed:
            raise
        except (httpx.HTTPError, OSError, ValueError, TimeoutError):
            raise ReadFailed("transport_or_format_failure") from None


async def read_bounded(reader, value):
    try:
        return await asyncio.wait_for(reader.read(value), timeout=15)
    except TimeoutError:
        raise ReadFailed("total_deadline") from None
