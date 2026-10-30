"""Bound the pinned framework's image preprocessing for the owned QQ C2C bot.

Explicit host opt-in only. Other platforms keep their original preprocessing.
QQ refusals never fall back to the original unbounded network downloader.
"""

import asyncio
import json
import ssl
import sqlite3
import uuid
from contextlib import closing, suppress
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

from qq_transport import resolve_proxy

MAX_BYTES = 4 * 1024 * 1024
TOTAL_SECONDS = 15
MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}


class MediaRefused(ValueError):
    """Public-safe category; never carry a signed URL or provider exception."""


def image_url(value):
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 8192
        or any(c.isspace() or ord(c) < 32 for c in value)
    ):
        raise MediaRefused("unsupported_qq_image_source")
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "multimedia.nt.qq.com.cn"
            or parsed.port not in (None, 443)
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or parsed.path != "/download"
        ):
            raise ValueError()
    except ValueError:
        raise MediaRefused("unsupported_qq_image_source") from None
    return value


class QQImages:
    def __init__(self, home, data, temp_root, original):
        self.home, self.data, self.temp_root = Path(home), Path(data), Path(temp_root)
        self.original = original
        self.busy = False

    def revisions(self):
        # Metadata only. Capture before waiting on the network so a deletion
        # cannot turn an older queued image into a newly authorized ticket.
        try:
            with closing(
                sqlite3.connect(
                    (self.home / "deletions.sqlite").as_uri() + "?mode=ro",
                    uri=True,
                    timeout=0.2,
                )
            ) as ledger:
                return dict(ledger.execute("SELECT scope,revision FROM revisions"))
        except (OSError, ValueError, sqlite3.Error):
            raise MediaRefused("qq_image_ledger_unavailable") from None

    def settings(self, event):
        """Re-read the actual configured account/owner, not message-body claims."""
        import botpy

        try:
            settings = json.loads(
                (self.home / "settings.local.json").read_text(encoding="utf-8")
            )
            config = json.loads(
                (self.data / "cmd_config.json").read_text(encoding="utf-8-sig")
            )
            platform = str(event.get_platform_id())
            raw = event.message_obj.raw_message
            channels = [p for p in config["platform"] if p.get("id") == platform]
            if (
                event.get_platform_name() != "qq_official"
                or event.get_group_id()
                or settings.get("qq_bounded_images") is not True
                or settings.get("vision_enabled") is not True
                or settings.get("model_enabled") is not True
                or (self.home / "STOP").exists()
                or len(channels) != 1
                or channels[0].get("enable") is not True
                or channels[0].get("type") != "qq_official"
                or str(channels[0].get("appid", ""))
                != settings.get("bot_account_ids", {}).get(platform)
                or settings.get("allowed_senders", {}).get(platform)
                != [event.get_sender_id()]
                or not isinstance(raw, botpy.message.C2CMessage)
                or raw.author.user_openid != event.get_sender_id()
                or str(raw.id) != str(event.message_obj.message_id)
                or not isinstance(raw.attachments, list)
                or len(raw.attachments) != 1
            ):
                return None
            return settings
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return None

    def source(self, event, component):
        if not any(part is component for part in event.get_messages()):
            raise MediaRefused("not_current_qq_attachment")
        attachment = event.message_obj.raw_message.attachments[0]
        if isinstance(attachment, dict):
            mime = attachment.get("content_type") or attachment.get("contentType") or ""
            url = attachment.get("url")
        else:
            mime = getattr(attachment, "content_type", "") or ""
            url = getattr(attachment, "url", None)
        if str(mime).lower() not in MIME_TYPES or not isinstance(url, str):
            raise MediaRefused("unsupported_qq_image_type")
        # Match the pinned adapter's scheme completion, then require HTTPS.
        url = url if url.startswith(("http://", "https://")) else "https://" + url
        if (component.url or component.file) != url:
            raise MediaRefused("qq_image_reference_mismatch")
        return image_url(url)

    async def download(self, url, proxy, permitted):
        import aiohttp

        if not permitted():
            raise MediaRefused("qq_image_authorization_changed")
        # Only the owner's existing loopback CONNECT gateway resolves the fixed
        # remote hostname. No environment proxy, cookies, redirects or TLS retry.
        async with aiohttp.ClientSession(
            trust_env=False,
            auto_decompress=False,
            cookie_jar=aiohttp.DummyCookieJar(),
            timeout=aiohttp.ClientTimeout(total=12, connect=5, sock_read=5),
            headers={
                "Accept": "image/jpeg,image/png,image/webp",
                "Accept-Encoding": "identity",
            },
        ) as client:
            if not permitted():
                raise MediaRefused("qq_image_authorization_changed")
            async with client.get(
                image_url(url),
                proxy=proxy,
                ssl=ssl.create_default_context(),
                allow_redirects=False,
            ) as response:
                if response.status != 200:
                    raise MediaRefused("qq_image_http_failure")
                if (
                    response.headers.get("Content-Encoding", "identity").lower()
                    != "identity"
                ):
                    raise MediaRefused("compressed_qq_image_refused")
                mime = (
                    response.headers.get("Content-Type", "")
                    .split(";", 1)[0]
                    .strip()
                    .lower()
                )
                if mime not in MIME_TYPES | {"application/octet-stream"}:
                    raise MediaRefused("unsupported_qq_image_type")
                if (
                    response.content_length is not None
                    and not 0 <= response.content_length <= MAX_BYTES
                ):
                    raise MediaRefused("qq_image_too_large")
                body = bytearray()
                async for chunk in response.content.iter_chunked(16384):
                    if len(body) + len(chunk) > MAX_BYTES:
                        raise MediaRefused("qq_image_too_large")
                    body.extend(chunk)
                if not body:
                    raise MediaRefused("empty_qq_image")
                if not permitted():
                    raise MediaRefused("qq_image_authorization_changed")
                return bytes(body)

    async def normalize(self, stage, event, component):
        if event.get_platform_name() != "qq_official":
            return await self.original(stage, event, component)
        if self.busy:
            event.set_extra("companion_qq_image_error", "qq_image_busy")
            return
        settings = self.settings(event)
        if settings is None:
            event.set_extra("companion_qq_image_error", "qq_image_not_authorized")
            return
        self.busy = True
        created = None
        retained = False
        try:
            url = self.source(event, component)
            proxy = resolve_proxy(settings.get("qq_loopback_proxy"))
            revisions = self.revisions()

            # Account/owner/settings changes invalidate the in-flight result.
            def permitted():
                return (
                    self.settings(event) == settings and self.revisions() == revisions
                )

            raw = await asyncio.wait_for(
                self.download(url, proxy, permitted), TOTAL_SECONDS
            )
            if not permitted():
                raise MediaRefused("qq_image_authorization_changed")
            self.temp_root.mkdir(exist_ok=True)
            root = self.temp_root.resolve(strict=True)
            destination = root / ("companion_qq_img_" + uuid.uuid4().hex + "_image.jpg")
            with destination.open("xb") as stream:
                created = destination
                stream.write(raw)
            # The pinned outer event_context cleans this one owned file, even
            # after cancellation/error. Never untrack it as a retained upload.
            event.track_temporary_local_file(str(created))
            component.file = component.url = component.path = str(created)
            retained = True
            event.set_extra("companion_qq_image_source", "bounded_owned_download")
            event.set_extra("companion_qq_image_revisions", revisions)
        except asyncio.CancelledError:
            raise
        except MediaRefused as exc:
            event.set_extra("companion_qq_image_error", str(exc))
        except TimeoutError:
            event.set_extra("companion_qq_image_error", "qq_image_timeout")
        except Exception:
            # No raw aiohttp exception/URL/query in logs or model messages, and
            # no call of the framework's long-wait or unverified-TLS fallback.
            event.set_extra("companion_qq_image_error", "qq_image_download_failed")
        finally:
            if created is not None and not retained:
                with suppress(OSError):
                    created.unlink(missing_ok=True)
            self.busy = False


def install(home, data, temp_root):
    from astrbot.core.pipeline.preprocess_stage.stage import PreProcessStage

    previous = getattr(PreProcessStage, "_companion_qq_image_hook", None)
    if previous is not None:
        return previous
    normalizer = QQImages(
        home, data, temp_root, PreProcessStage._normalize_image_component
    )

    async def normalize(stage, event, component):
        return await normalizer.normalize(stage, event, component)

    installation = SimpleNamespace(
        original=PreProcessStage._normalize_image_component,
        replacement=normalize,
        normalizer=normalizer,
    )
    PreProcessStage._normalize_image_component = normalize
    PreProcessStage._companion_qq_image_hook = installation
    return installation


def uninstall(installation):
    from astrbot.core.pipeline.preprocess_stage.stage import PreProcessStage

    if (
        getattr(PreProcessStage, "_companion_qq_image_hook", None) is installation
        and PreProcessStage._normalize_image_component is installation.replacement
    ):
        PreProcessStage._normalize_image_component = installation.original
        del PreProcessStage._companion_qq_image_hook
