#!/usr/bin/env python3
"""Export single chat 米迷 (wxid_8137971385012) to ~/Downloads/wechat_export/html/米迷.html"""

from __future__ import annotations

import hashlib
import html
import json
import re
import sqlite3
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

try:
    import zstandard as zstd
except ImportError:
    zstd = None

WECHAT_DOMAIN = "AppDomain-com.tencent.xin"
EXPORT_ROOT = Path.home() / "Downloads" / "wechat_export"
DEFAULT_BACKUP = Path.home() / "Downloads" / "00008150-001623410A08401C"
ACCOUNT = "48d99549ba8c5780b0908193c1fab6fd"
CHAT_ID = "11d8637ec8a3730380f6691705f8a23c"
WXID = "wxid_8137971385012"

TEXT_TYPE = 1
IMAGE_TYPES = {3}
VOICE_TYPE = 34
VIDEO_TYPE = 43
SYSTEM_TYPES = {10000, 10002}
GROUP_PREFIX_RE = re.compile(
    r"^((?:wxid_[\w-]+|[\w.-]+@chatroom)):\n?(.*)$", re.DOTALL
)
REMARK_TEXT_RE = re.compile(r"[\u4e00-\u9fff\w@ .·\-]{2,60}")
TYPE_LABELS = {
    1: "文字",
    3: "图片",
    34: "语音",
    43: "视频",
    47: "表情",
    49: "链接/小程序",
    48: "位置",
    50: "视频通话",
    42: "名片",
    64: "转账",
    10000: "系统",
    10002: "撤回/系统",
}
IMAGE_MAGIC = {
    b"\xff\xd8\xff": ".jpg",
    b"\x89PNG": ".png",
    b"GIF8": ".gif",
    b"RIFF": ".webp",
}


def find_manifest(backup_root: Path) -> Path:
    for candidate in (
        backup_root / "Manifest.db",
        backup_root / "Snapshot" / "Manifest.db",
    ):
        if candidate.is_file():
            return candidate
    raise SystemExit(f"Manifest.db not found under {backup_root}")


def backup_blob_path(backup_root: Path, file_id: str) -> Path | None:
    for base in (backup_root, backup_root / "Snapshot"):
        p = base / file_id[:2] / file_id
        if p.is_file():
            return p
    return None


def parse_message_body(raw):
    if raw is None:
        return {"content": None, "sender_id": None, "is_binary": False}
    if isinstance(raw, bytes):
        return {
            "content": None,
            "sender_id": None,
            "is_binary": True,
            "binary_note": f"<二进制 {len(raw)} 字节>",
        }
    text = str(raw)
    if "\x00" in text or (
        len(text) > 0 and ord(text[0]) < 32 and not text.startswith("\n")
    ):
        return {
            "content": None,
            "sender_id": None,
            "is_binary": True,
            "binary_note": "<非文本内容>",
        }
    m = GROUP_PREFIX_RE.match(text)
    if m:
        return {
            "content": m.group(2).strip(),
            "sender_id": m.group(1),
            "is_binary": False,
        }
    return {"content": text, "sender_id": None, "is_binary": False}


def unix_to_local(ts):
    if ts is None:
        return ""
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).astimezone().strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    except (OSError, ValueError, OverflowError):
        return str(ts)


def detect_image_ext(data: bytes) -> str | None:
    for magic, ext in IMAGE_MAGIC.items():
        if data.startswith(magic):
            return ext
    if data.startswith(b"wxgf"):
        return ".wxgf"
    return None


class BackupMediaIndex:
    def __init__(self, backup_root: Path):
        self.backup_root = backup_root
        self.path_to_id: dict[str, str] = {}
        manifest = find_manifest(backup_root)
        conn = sqlite3.connect(str(manifest))
        cur = conn.cursor()
        cur.execute(
            "SELECT fileID, relativePath FROM Files WHERE domain = ?",
            (WECHAT_DOMAIN,),
        )
        for file_id, rel in cur.fetchall():
            self.path_to_id[rel] = file_id
        conn.close()

    def resolve(self, rel_path: str) -> Path | None:
        file_id = self.path_to_id.get(rel_path)
        if not file_id:
            return None
        return backup_blob_path(self.backup_root, file_id)


@dataclass
class MediaStats:
    images_embedded: int = 0
    images_thumb_only: int = 0
    images_wxgf: int = 0
    images_missing: int = 0
    voice_linked: int = 0
    voice_missing: int = 0
    video_linked: int = 0
    video_missing: int = 0


@dataclass
class AppMsgStats:
    decoded: int = 0
    decode_failed: int = 0


class AppMsgDecoder:
    """Decode Type-49 app messages with an optional WCDB zstd dictionary."""

    def __init__(self, dict_path: Path | None):
        self.dict_path = dict_path
        self.available = False
        self.error: str | None = None
        self._dctx = None
        if dict_path is None:
            self.error = "no dictionary provided"
            return
        if zstd is None:
            self.error = "zstandard not installed"
            return
        if not dict_path.is_file():
            self.error = f"dictionary not found: {dict_path}"
            return
        try:
            data = dict_path.read_bytes()
            zdict = zstd.ZstdCompressionDict(data)
            self._dctx = zstd.ZstdDecompressor(dict_data=zdict)
            self.available = True
        except Exception as exc:
            self.error = f"load dict failed: {exc}"

    def try_decode(self, payload: bytes) -> dict[str, str] | None:
        if not self.available or not self._dctx:
            return None
        xml_text = self._decompress_xml(payload)
        if not xml_text:
            return None
        return self._parse_appmsg(xml_text)

    def _decompress_xml(self, payload: bytes) -> str | None:
        for skip in (0, 4, 8, 12, 16):
            if len(payload) <= skip:
                continue
            try:
                out = self._dctx.decompress(payload[skip:], max_output_size=2_000_000)
                text = out.decode("utf-8", errors="ignore")
                if "<msg" in text and "<appmsg" in text:
                    return text
            except Exception:
                continue
        return None

    @staticmethod
    def _tag(xml_text: str, tag: str) -> str | None:
        patterns = (
            rf"<{tag}><!\[CDATA\[(.*?)\]\]></{tag}>",
            rf"<{tag}>([^<]*)</{tag}>",
        )
        for pat in patterns:
            m = re.search(pat, xml_text, re.DOTALL)
            if m:
                return m.group(1).strip()
        return None

    def _parse_appmsg(self, xml_text: str) -> dict[str, str]:
        parsed: dict[str, str] = {}
        try:
            root = ET.fromstring(xml_text)
            appmsg = root.find(".//appmsg")
            if appmsg is not None:
                for key in ("title", "des", "url", "type"):
                    node = appmsg.find(key)
                    if node is not None and node.text:
                        parsed[key] = node.text.strip()
                refer = appmsg.find(".//refermsg")
                if refer is not None:
                    for key in ("displayname", "content", "type"):
                        node = refer.find(key)
                        if node is not None and node.text:
                            parsed[f"refer_{key}"] = node.text.strip()
            return parsed
        except Exception:
            # Fallback for broken XML / edge cases
            for key in ("title", "des", "url", "type"):
                val = self._tag(xml_text, key)
                if val:
                    parsed[key] = val
            for key in ("displayname", "content", "type"):
                val = self._tag(xml_text, key)
                if val:
                    parsed[f"refer_{key}"] = val
            return parsed


class MimiExporter:
    def __init__(self, export_root: Path, backup_root: Path, dict_path: Path | None = None):
        self.export_root = export_root
        self.backup_root = backup_root
        self.html_root = export_root / "html"
        self.media_root = self.html_root / "media"
        self.html_root.mkdir(parents=True, exist_ok=True)
        self.media_root.mkdir(parents=True, exist_ok=True)
        self.idx = BackupMediaIndex(backup_root)
        self.stats = MediaStats()
        self.appmsg_stats = AppMsgStats()
        self.appmsg_decoder = AppMsgDecoder(dict_path)

    def copy_media(self, src: Path, dest_name: str):
        if not src.is_file():
            return None, None
        data = src.read_bytes()
        ext = detect_image_ext(data) or Path(src.name).suffix or ".bin"
        dest = self.media_root / f"{dest_name}{ext}"
        if not dest.exists() or dest.stat().st_size != len(data):
            dest.write_bytes(data)
        return dest, ext

    def rel_url(self, dest: Path) -> str:
        return "media/" + dest.name

    def existing_media_url(self, prefix: str) -> str | None:
        """Reuse files from a prior export when backup blobs are missing."""
        for p in sorted(self.media_root.glob(prefix + "*")):
            if p.is_file() and p.stat().st_size > 0:
                return self.rel_url(p)
        return None

    def fetch_media(self, local_id: int, msg_type: int) -> str:
        base = f"Documents/{ACCOUNT}"
        prefix = f"{ACCOUNT}_{CHAT_ID}_{local_id}"
        if msg_type in IMAGE_TYPES:
            wxgf_fallback = None
            for suffix, thum in [
                (f"Img/{CHAT_ID}/{local_id}.pic", False),
                (f"Img/{CHAT_ID}/{local_id}.pic_thum", True),
            ]:
                src = self.idx.resolve(f"{base}/{suffix}")
                tag_prefix = prefix + ("_thum" if thum else "")
                if not src:
                    cached = self.existing_media_url(tag_prefix)
                    if cached:
                        if thum:
                            self.stats.images_thumb_only += 1
                        else:
                            self.stats.images_embedded += 1
                        return (
                            f'<img src="{html.escape(cached)}" alt="图片" loading="lazy" '
                            f'class="msg-image"/>'
                        )
                    continue
                dest, ext = self.copy_media(src, prefix + ("_thum" if thum else ""))
                if not dest:
                    continue
                url = self.rel_url(dest)
                if ext == ".wxgf":
                    if not thum:
                        wxgf_fallback = (dest, url)
                        continue
                    return (
                        f'<p class="media-note">[wxgf]</p>'
                        f'<a href="{html.escape(url)}">下载</a>'
                    )
                if thum:
                    self.stats.images_thumb_only += 1
                else:
                    self.stats.images_embedded += 1
                return (
                    f'<img src="{html.escape(url)}" alt="图片" loading="lazy" '
                    f'class="msg-image"/>'
                )
            if wxgf_fallback:
                self.stats.images_wxgf += 1
                _, url = wxgf_fallback
                return f'<a href="{html.escape(url)}">下载原图</a>'
            self.stats.images_missing += 1
            return '<span class="placeholder">[图片未在备份中找到]</span>'
        if msg_type == VOICE_TYPE:
            src = self.idx.resolve(f"{base}/Audio/{CHAT_ID}/{local_id}.aud")
            if src:
                dest, _ = self.copy_media(src, prefix + "_voice")
                if dest:
                    self.stats.voice_linked += 1
                    return (
                        f'<audio controls src="{html.escape(self.rel_url(dest))}">'
                        f"</audio>"
                    )
            cached = self.existing_media_url(prefix + "_voice")
            if cached:
                self.stats.voice_linked += 1
                return f'<audio controls src="{html.escape(cached)}"></audio>'
            self.stats.voice_missing += 1
            return '<span class="placeholder">[语音未找到]</span>'
        if msg_type == VIDEO_TYPE:
            for suffix in (
                f"Video/{CHAT_ID}/{local_id}.mp4",
                f"Video/{CHAT_ID}/{local_id}.video",
                f"Video/{CHAT_ID}/{local_id}.video_thum",
            ):
                src = self.idx.resolve(f"{base}/{suffix}")
                if not src:
                    continue
                dest, ext = self.copy_media(src, prefix + "_" + Path(suffix).name)
                if dest:
                    self.stats.video_linked += 1
                    url = self.rel_url(dest)
                    if ext in (".jpg", ".jpeg", ".png", ".webp") or "thum" in suffix:
                        return f'<img src="{html.escape(url)}" class="msg-image"/>'
                    return f'<video controls src="{html.escape(url)}"></video>'
            self.stats.video_missing += 1
            return '<span class="placeholder">[视频未找到]</span>'
        return ""

    def render(self, row) -> str:
        create_time, des, message, msg_type, local_id, svr_id, status, wcdb_ct = row
        parsed = parse_message_body(message)
        time_str = unix_to_local(create_time)
        side = "right" if des == 1 else "left"
        type_label = TYPE_LABELS.get(msg_type, f"类型 {msg_type}")
        parts = []
        if parsed.get("sender_id"):
            parts.append(f'<div class="sender">{html.escape(parsed["sender_id"])}</div>')
        if msg_type == TEXT_TYPE and not parsed["is_binary"]:
            body = f'<div class="bubble"><pre>{html.escape(parsed.get("content") or "")}</pre></div>'
        elif msg_type in IMAGE_TYPES:
            body = (
                f'<div class="bubble media-bubble">'
                f"{self.fetch_media(int(local_id or 0), msg_type)}</div>"
            )
        elif msg_type in (VOICE_TYPE, VIDEO_TYPE):
            body = (
                f'<div class="bubble media-bubble">'
                f"{self.fetch_media(int(local_id or 0), msg_type)}</div>"
            )
        elif msg_type in SYSTEM_TYPES:
            text = parsed.get("content") or parsed.get("binary_note") or type_label
            return (
                f'<div class="msg system"><span class="time">{html.escape(time_str)}</span> '
                f"{html.escape(str(text))}</div>"
            )
        elif msg_type == 49 and isinstance(message, bytes):
            app = self.appmsg_decoder.try_decode(message)
            if app:
                self.appmsg_stats.decoded += 1
                app_type = app.get("type", "").strip()
                bubble = []
                if app_type == "57":
                    bubble.append('<p class="media-note">[引用回复]</p>')
                    # Most quote-reply messages keep visible text in <title>.
                    if app.get("title"):
                        bubble.append(f"<pre>{html.escape(app['title'])}</pre>")
                    if app.get("refer_displayname") and app.get("refer_content"):
                        bubble.append(
                            f'<p class="media-note">↪ {html.escape(app["refer_displayname"])}: '
                            f'{html.escape(app["refer_content"][:120])}</p>'
                        )
                else:
                    if app.get("title"):
                        bubble.append(f"<pre>{html.escape(app['title'])}</pre>")
                    if app.get("des"):
                        bubble.append(f'<p class="media-note">{html.escape(app["des"])}</p>')
                    if app.get("url"):
                        u = html.escape(app["url"])
                        bubble.append(f'<p><a href="{u}" target="_blank" rel="noreferrer">{u}</a></p>')
                if bubble:
                    body = f'<div class="bubble">{"".join(bubble)}</div>'
                else:
                    note = parsed.get("binary_note", type_label)
                    body = (
                        f'<div class="bubble"><span class="placeholder">'
                        f"[{html.escape(type_label)}] {html.escape(str(note))}</span></div>"
                    )
            else:
                self.appmsg_stats.decode_failed += 1
                note = parsed.get("binary_note", type_label)
                body = (
                    f'<div class="bubble"><span class="placeholder">'
                    f"[{html.escape(type_label)}] {html.escape(str(note))}</span></div>"
                )
        elif parsed["is_binary"]:
            note = parsed.get("binary_note", type_label)
            body = (
                f'<div class="bubble"><span class="placeholder">'
                f"[{html.escape(type_label)}] {html.escape(str(note))}</span></div>"
            )
        else:
            text = parsed.get("content") or ""
            if text:
                body = f'<div class="bubble"><pre>{html.escape(text)}</pre></div>'
            else:
                body = (
                    f'<div class="bubble"><span class="placeholder">'
                    f"[{html.escape(type_label)}]</span></div>"
                )
        return (
            f'<div class="msg {side}">'
            f'<div class="meta"><span class="time">{html.escape(time_str)}</span> '
            f'<span class="type-tag">{html.escape(type_label)}</span></div>'
            f'{"".join(parts)}{body}</div>'
        )

    def load_rows(self):
        db_dir = self.export_root / "db" / "Documents" / ACCOUNT / "DB"
        if not db_dir.is_dir():
            raise SystemExit(f"Missing account DB: {db_dir}")
        rows = []
        for db in sorted(db_dir.glob("message_*.sqlite")):
            conn = sqlite3.connect(str(db))
            cur = conn.cursor()
            table = f"Chat_{CHAT_ID}"
            cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            )
            if not cur.fetchone():
                conn.close()
                continue
            cols = [c[1] for c in cur.execute(f'PRAGMA table_info("{table}")').fetchall()]
            has_ct = "WCDB_CT_Message" in cols
            ct_expr = "WCDB_CT_Message" if has_ct else "NULL"
            cur.execute(
                f'SELECT CreateTime, Des, Message, Type, MesLocalID, MesSvrID, Status '
                f", "
                f"{ct_expr} "
                f'FROM "{table}" ORDER BY CreateTime ASC'
            )
            rows.extend(cur.fetchall())
            conn.close()
        rows.sort(key=lambda r: r[0] or 0)
        return rows

    def page(self, title: str, subtitle: str, body: str) -> str:
        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{html.escape(title)}</title>
<style>
:root {{--bg:#ebebeb;--me:#95ec69;--them:#fff;--text:#111;--muted:#666;--link:#576b95}}
body{{margin:0;font-family:-apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif;background:var(--bg);color:var(--text);line-height:1.45}}
header{{position:sticky;top:0;z-index:10;background:#2e2e2e;color:#fff;padding:12px 16px;box-shadow:0 1px 4px rgba(0,0,0,.2)}}
header h1{{margin:0;font-size:1.1rem;font-weight:600}}
header p{{margin:4px 0 0;font-size:.8rem;opacity:.85}}
.chat{{max-width:720px;margin:0 auto;padding:12px 10px 48px}}
.msg{{margin:10px 0;clear:both}}
.msg.left{{text-align:left}} .msg.right{{text-align:right}}
.msg.system{{text-align:center;font-size:.75rem;color:var(--muted);margin:16px 0}}
.meta{{font-size:.7rem;color:var(--muted);margin-bottom:2px}}
.bubble{{display:inline-block;max-width:85%;padding:8px 12px;border-radius:8px;text-align:left;word-break:break-word;box-shadow:0 1px 1px rgba(0,0,0,.06)}}
.msg.left .bubble{{background:var(--them);border-top-left-radius:2px}}
.msg.right .bubble{{background:var(--me);border-top-right-radius:2px}}
.bubble pre{{margin:0;white-space:pre-wrap;font-family:inherit;font-size:.95rem}}
.media-bubble{{padding:4px;background:var(--them)!important}}
.msg.right .media-bubble{{background:var(--them)!important}}
.msg-image{{max-width:100%;height:auto;border-radius:6px;display:block}}
.placeholder{{color:var(--muted);font-style:italic}}
.media-note{{font-size:.75rem;color:var(--muted);margin:0 0 4px}}
audio,video{{max-width:100%}}
</style>
</head>
<body>
<header>
  <h1>{html.escape(title)}</h1>
  <p>{html.escape(subtitle)}</p>
</header>
<main class="chat">
{body}
</main>
</body>
</html>
"""

    def run(self) -> dict:
        rows = self.load_rows()
        if not rows:
            raise SystemExit("未找到米迷聊天记录（请先成功提取 message_*.sqlite）")
        msgs = [self.render(r) for r in rows]
        t0, t1 = unix_to_local(rows[0][0]), unix_to_local(rows[-1][0])
        out = self.html_root / "米迷.html"
        out.write_text(
            self.page(
                "米迷",
                f"{WXID} · chat_id={CHAT_ID} · {len(rows)} 条 · {t0} → {t1}",
                "\n".join(msgs),
            ),
            encoding="utf-8",
        )
        wechat_on_disk = sum(
            1
            for _ in self.idx.path_to_id
            if backup_blob_path(self.backup_root, self.idx.path_to_id[_])
        )
        meta = {
            "exported_at": datetime.now().astimezone().isoformat(),
            "backup": str(self.backup_root),
            "messages": len(rows),
            "time_range": [t0, t1],
            "media": self.stats.__dict__,
            "appmsg_decode": {
                "decoded": self.appmsg_stats.decoded,
                "decode_failed": self.appmsg_stats.decode_failed,
                "dict_available": self.appmsg_decoder.available,
                "dict_error": self.appmsg_decoder.error,
                "dict_path": str(self.appmsg_decoder.dict_path) if self.appmsg_decoder.dict_path else None,
            },
            "output": str(out),
            "backup_wechat_blobs_on_disk_sample": wechat_on_disk,
        }
        meta_path = self.html_root / "米迷_export_meta.json"
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        return meta


def main() -> None:
    export_root = (
        Path(sys.argv[1]).expanduser().resolve()
        if len(sys.argv) > 1
        else EXPORT_ROOT
    )
    backup_root = (
        Path(sys.argv[2]).expanduser().resolve()
        if len(sys.argv) >= 3
        else DEFAULT_BACKUP
    )
    dict_path = (
        Path(sys.argv[3]).expanduser().resolve()
        if len(sys.argv) >= 4
        else None
    )
    meta = MimiExporter(export_root, backup_root, dict_path=dict_path).run()
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    if meta["media"]["images_embedded"] == 0 and meta["media"]["images_missing"] > 0:
        print(
            "\n注意：当前备份目录里没有可用的微信文件块（增量备份常见）。"
            "文字记录来自已提取的 sqlite；图片需完整未加密备份才能嵌入。",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
