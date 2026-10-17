"""AstrBot Star 插件：链接下载器（astrbot_plugin_link_downloader）。

完整交互流程
------------
1. 用户在群聊发送  /download 链接 [密码:xxxx] ；
   - 私聊直接拒绝，并提示「该指令仅限群聊使用」；
2. 插件识别链接类型（蓝奏云 / 百度网盘 / 夸克网盘 / GitHub），解析并检索文件清单，
   以「1.文件名 | 大小」的序号列表发回群聊；
3. 用户回复序号（单个）、逗号分隔序号（批量）或「全部」：
   - 只有「同一群 + 同一发起人」的回复才会生效，其他人群内回复不触发；
   - 会话有超时时间（配置 session_timeout），超时自动清空并提示过期；
4. 下载选中的文件：GitHub 优先走原始地址，超时自动轮换镜像；
   网盘则带上提取码解析目录后下载；
5. 下载过程输出状态：正在解析链接 / 正在下载 xxx / 下载完成准备上传；
6. 下载完成后把文件上传到当前群聊，临时文件随后自动删除。

代码规范
--------
- 继承 Star，使用 @filter.command 注册指令；
- 全部网络请求由 aiohttp 异步完成，不阻塞 Bot 主线程；
- 网盘解析逻辑拆分到 parsers 子包中的独立模块；
- 任何解析 / 下载异常都会被捕获并转成友好提示，不会让插件崩溃。
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import shutil
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star

# 说明：AstrBot 以 data.plugins.<插件名> 包的形式加载插件，因此这里优先使用相对导入；
# 若插件被以独立脚本方式加载（例如本地单测），则把插件目录加入 sys.path 做兜底。
try:
    from .parsers import (
        BaseParser,
        HttpClient,
        LinkDownloadError,
        RemoteFile,
        build_parser,
        human_size,
        safe_filename,
        unique_path,
    )
except ImportError:  # pragma: no cover - 仅在非包加载方式下触发
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from parsers import (  # type: ignore[no-redef]
        BaseParser,
        HttpClient,
        LinkDownloadError,
        RemoteFile,
        build_parser,
        human_size,
        safe_filename,
        unique_path,
    )

# StarTools 用于获取插件数据目录（data/plugin_data/<插件名>）
try:
    from astrbot.api.star import StarTools
except Exception:  # pragma: no cover - 兼容极老版本
    StarTools = None  # type: ignore[assignment]

# MessageType 用于严格校验「仅群聊事件」
try:
    from astrbot.api.platform import MessageType
except Exception:  # pragma: no cover - 兼容性兜底
    MessageType = None  # type: ignore[assignment]


PLUGIN_NAME = "astrbot_plugin_link_downloader"

# 使用说明
USAGE = (
    "📖 用法：/download 链接 [密码:xxxx]\n"
    "支持链接：蓝奏云 / 百度网盘 / 夸克网盘 / GitHub Release·Raw\n"
    "示例：\n"
    "/download https://thutao.lanzoub.com/iOmYf4b2x8yd 密码:en15\n"
    "/download https://pan.baidu.com/s/xxxxxx 密码:1234\n"
    "/download https://pan.quark.cn/s/xxxxxx\n"
    "/download https://github.com/xxx/xxx/releases/download/v1.0/file.zip"
)

# 从消息中提取链接（兼容中英文逗号、空格分隔）
URL_RE = re.compile(r"https?://[^\s，,；;、）)】]+", re.IGNORECASE)

# 从消息中提取提取码，格式固定为 密码:xxxx
PASSWORD_RE = re.compile(
    r"(?:密码|提取码|提取密码|pwd|passcode)\s*[:：=]\s*([A-Za-z0-9]+)",
    re.IGNORECASE,
)

# 「全部下载」关键字
ALL_KEYWORDS = {"全部", "全部下载", "所有", "全选", "all"}

# 「取消任务」关键字
CANCEL_KEYWORDS = {"取消", "取消任务", "cancel", "退出", "quit"}


# ---------------------------------------------------------------------------
# 纯函数工具
# ---------------------------------------------------------------------------
def extract_url(text: str) -> str:
    """从消息文本中提取第一个链接。"""
    match = URL_RE.search(text or "")
    return match.group(0).rstrip(".") if match else ""


def extract_password(text: str) -> str:
    """从消息文本中提取提取码（没有则返回空串）。"""
    match = PASSWORD_RE.search(text or "")
    return match.group(1) if match else ""


def parse_selection(text: str, total: int) -> Optional[list[int]]:
    """解析用户的序号选择。

    Returns:
        None  ：这段文本与选择无关（应完全忽略，交给其他逻辑）；
        []    ：看起来像选择，但序号全部越界；
        list  ：合法的 0-based 索引列表。
    """
    raw = (text or "").strip()
    if not raw:
        return None
    compact = raw.replace(" ", "").replace("　", "")
    if compact.lower() in ALL_KEYWORDS:
        return list(range(total))

    cleaned = re.sub(r"[\s，、;；:：]+", ",", raw).strip(",")
    cleaned = re.sub(r",+", ",", cleaned)
    if not cleaned or not re.fullmatch(r"\d+(?:,\d+)*", cleaned):
        return None

    picked: list[int] = []
    for part in cleaned.split(","):
        try:
            index = int(part)
        except ValueError:
            continue
        if 1 <= index <= total:
            picked.append(index - 1)
    return sorted(set(picked))


def is_group_event(event: AstrMessageEvent) -> bool:
    """严格校验事件类型：只有 GROUP_MESSAGE 群聊事件才允许继续。"""
    if MessageType is not None:
        try:
            if event.get_message_type() != MessageType.GROUP_MESSAGE:
                return False
        except Exception:  # pragma: no cover - 某些适配器可能未实现
            pass
    try:
        if event.is_private_chat():
            return False
    except Exception:  # pragma: no cover
        pass
    return bool(event.get_group_id())


# ---------------------------------------------------------------------------
# 会话对象
# ---------------------------------------------------------------------------
@dataclass
class DownloadSession:
    """一次 /download 指令产生的待选择任务。"""

    key: str  # 群号:发送者ID，天然保证「同群 + 同一发起人」
    group_id: str
    platform_id: str
    sender_id: str
    sender_name: str
    umo: str  # unified_msg_origin，用于主动发送消息
    url: str
    password: str
    parser: BaseParser
    files: list[RemoteFile]
    created_at: float = field(default_factory=time.time)
    expire_task: Optional[asyncio.Task] = None

    def is_expired(self, timeout: float) -> bool:
        return (time.time() - self.created_at) > timeout

    def cancel(self) -> None:
        """取消超时任务。"""
        task = self.expire_task
        self.expire_task = None
        if task is not None and not task.done():
            task.cancel()


class _ProgressReporter:
    """下载进度上报器。

    - 已知总大小时：每前进 25% 才上报一次；
    - 未知总大小时：按时间间隔（默认 15 秒）上报；
    - 通过插件内部任务发送消息，避免阻塞下载协程。
    """

    def __init__(
        self,
        plugin: "LinkDownloaderPlugin",
        umo: str,
        name: str,
        *,
        step: int = 25,
        interval: float = 15.0,
    ) -> None:
        self._plugin = plugin
        self._umo = umo
        self._name = name
        self._step = max(1, step)
        self._interval = max(1.0, interval)
        self._next = self._step
        self._last_at = 0.0
        self._last_text = ""

    def __call__(self, downloaded: int, total: int) -> None:
        try:
            if total and total > 0:
                percent = int(downloaded * 100 / total)
                if percent < self._next:
                    return
                while self._next <= percent:
                    self._next += self._step
                text = (
                    f"📥 {self._name} 下载中：{percent}%"
                    f"（{human_size(downloaded)} / {human_size(total)}）"
                )
            else:
                now = time.monotonic()
                if now - self._last_at < self._interval:
                    return
                self._last_at = now
                text = f"📥 {self._name} 下载中：已下载 {human_size(downloaded)}"

            if text == self._last_text:
                return
            self._last_text = text
            self._plugin.spawn(self._plugin.send_text(self._umo, text))
        except Exception:  # 进度上报失败绝不能影响下载
            pass


# ---------------------------------------------------------------------------
# 插件主体
# ---------------------------------------------------------------------------
class LinkDownloaderPlugin(Star):
    """链接下载器插件。"""

    def __init__(self, context: Context, config: AstrBotConfig | None = None) -> None:
        super().__init__(context)
        self.config = config or {}

        # ------------------------- 读取配置 -------------------------
        self.network_timeout = self._cfg_int("network_timeout", 60, minimum=5)
        self.max_file_size_mb = self._cfg_int("max_file_size_mb", 200, minimum=0)
        self.session_timeout = self._cfg_int("session_timeout", 120, minimum=10)
        self.request_interval = self._cfg_float("request_interval", 1.0, minimum=0.0)
        self.max_batch_files = self._cfg_int("max_batch_files", 5, minimum=1)
        self.platform_upload_limit_mb = self._cfg_int(
            "platform_upload_limit_mb", 100, minimum=0
        )
        self.max_depth = self._cfg_int("max_depth", 3, minimum=1)
        self.github_mirrors = self._cfg_list("github_mirrors")
        self.github_token = str(self._cfg("github_token", "") or "").strip()
        self.keep_failed_temp = bool(self._cfg("keep_failed_temp", False))
        self.use_aiocqhttp_upload = bool(self._cfg("use_aiocqhttp_upload", True))

        self.max_file_size = self.max_file_size_mb * 1024 * 1024
        self.platform_upload_limit = self.platform_upload_limit_mb * 1024 * 1024

        # ------------------------- HTTP 客户端 -------------------------
        self.http = HttpClient(
            timeout=self.network_timeout,
            user_agent=str(self._cfg("user_agent", "") or "").strip(),
            request_interval=self.request_interval,
        )
        baidu_cookie = str(self._cfg("baidu_cookie", "") or "").strip()
        quark_cookie = str(self._cfg("quark_cookie", "") or "").strip()
        if baidu_cookie:
            self.http.set_domain_cookie("baidu.com", baidu_cookie)
        if quark_cookie:
            self.http.set_domain_cookie("quark.cn", quark_cookie)

        # ------------------------- 运行时状态 -------------------------
        self._sessions: dict[str, DownloadSession] = {}
        self._tasks: set[asyncio.Task] = set()
        self._download_lock = asyncio.Lock()
        self._tmp_root = self._resolve_tmp_root()
        self._cleanup_stale_tmp()

        logger.info(
            f"[link_downloader] 插件已加载：超时 {self.network_timeout}s，"
            f"单文件上限 {self.max_file_size_mb} MB，会话超时 {self.session_timeout}s，"
            f"镜像 {len(self.github_mirrors)} 个"
        )

    # ------------------------------------------------------------------ 配置
    def _cfg(self, key: str, default: Any = None) -> Any:
        try:
            value = self.config.get(key, default)
        except Exception:
            return default
        return default if value is None else value

    def _cfg_int(self, key: str, default: int, minimum: int = 0) -> int:
        try:
            value = int(float(self._cfg(key, default)))
        except (TypeError, ValueError):
            value = default
        return max(minimum, value)

    def _cfg_float(self, key: str, default: float, minimum: float = 0.0) -> float:
        try:
            value = float(self._cfg(key, default))
        except (TypeError, ValueError):
            value = default
        return max(minimum, value)

    def _cfg_list(self, key: str) -> list[str]:
        value = self._cfg(key, [])
        if isinstance(value, str):
            parts: list[str] = []
            for line in value.replace("，", "\n").replace(",", "\n").splitlines():
                line = line.strip()
                if line:
                    parts.append(line)
            return parts
        if isinstance(value, (list, tuple, set)):
            return [str(item).strip() for item in value if str(item).strip()]
        return []

    # ------------------------------------------------------------------ 临时目录
    def _resolve_tmp_root(self) -> Path:
        """临时目录：优先放在插件数据目录，其次放在插件目录 / 系统临时目录。"""
        base: Optional[Path] = None
        if StarTools is not None:
            try:
                base = Path(StarTools.get_data_dir(PLUGIN_NAME))
            except Exception as exc:
                logger.warning(f"[link_downloader] 获取插件数据目录失败，改用插件目录：{exc}")
        if base is None:
            base = Path(__file__).resolve().parent / "data"
        tmp_root = Path(base) / "tmp"
        try:
            tmp_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.error(f"[link_downloader] 创建临时目录失败，改用系统临时目录：{exc}")
            import tempfile

            tmp_root = Path(tempfile.gettempdir()) / PLUGIN_NAME / "tmp"
            tmp_root.mkdir(parents=True, exist_ok=True)
        return tmp_root

    def _cleanup_stale_tmp(self) -> None:
        """启动时清理 24 小时前的残留临时目录，防止占用磁盘。"""
        try:
            deadline = time.time() - 24 * 3600
            for entry in self._tmp_root.iterdir():
                if not entry.is_dir():
                    continue
                try:
                    if entry.stat().st_mtime < deadline:
                        shutil.rmtree(entry, ignore_errors=True)
                except OSError:
                    continue
        except Exception as exc:  # pragma: no cover
            logger.debug(f"[link_downloader] 清理残留临时目录失败：{exc}")

    # ------------------------------------------------------------------ 任务管理
    def spawn(self, coro) -> asyncio.Task:
        """创建后台任务并登记，插件卸载时统一取消。"""
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def _register_session(self, session: DownloadSession) -> None:
        old = self._sessions.get(session.key)
        if old is not None and old is not session:
            self._discard_session(old)
        self._sessions[session.key] = session
        session.expire_task = self.spawn(self._expire_session(session))

    def _discard_session(self, session: DownloadSession) -> None:
        """把会话从待选择表中移除并取消超时任务。"""
        if self._sessions.get(session.key) is session:
            self._sessions.pop(session.key, None)
        session.cancel()

    async def _expire_session(self, session: DownloadSession) -> None:
        """会话超时：自动清空并提示过期。"""
        try:
            await asyncio.sleep(self.session_timeout)
        except asyncio.CancelledError:
            return
        if self._sessions.get(session.key) is session:
            self._sessions.pop(session.key, None)
            await self.send_text(
                session.umo,
                f"⏰ 本次下载任务已过期（{self.session_timeout} 秒内未收到选择），"
                "文件清单已清空，请重新发送 /download 指令。",
            )

    # ------------------------------------------------------------------ 发送消息
    async def send_text(self, umo: str, text: str) -> None:
        """向指定会话发送一条纯文本主动消息。"""
        try:
            await self.context.send_message(
                umo, MessageChain(chain=[Comp.Plain(text)])
            )
        except Exception as exc:
            logger.warning(f"[link_downloader] 发送消息失败：{exc}")

    async def send_file(
        self,
        umo: str,
        path: Path,
        name: str,
        *,
        platform_id: str = "",
        group_id: str = "",
    ) -> bool:
        """把本地文件发送到群聊，返回是否成功。

        QQ（aiocqhttp）场景下优先调用协议端的 upload_group_file 接口，
        这样即使适配器不擅长处理 File 消息段也能稳定上传；其余平台
        统一使用 AstrBot 的 File 消息段。
        """
        if self.use_aiocqhttp_upload and platform_id and group_id:
            if await self._aiocqhttp_upload(platform_id, group_id, path, name):
                return True

        try:
            result = await self.context.send_message(
                umo, MessageChain(chain=[Comp.File(name=name, file=str(path))])
            )
            return bool(result)
        except Exception as exc:
            logger.error(f"[link_downloader] 上传文件失败：{exc}")
            return False

    async def _aiocqhttp_upload(
        self, platform_id: str, group_id: str, path: Path, name: str
    ) -> bool:
        """调用 OneBot v11 的 upload_group_file 上传群文件（仅 aiocqhttp）。"""
        try:
            platform = self.context.get_platform_inst(platform_id)
        except Exception:
            return False
        if platform is None:
            return False
        if not type(platform).__name__.lower().startswith("aiocqhttp"):
            return False

        try:
            group_value: Any = int(group_id)
        except (TypeError, ValueError):
            group_value = group_id

        try:
            client = platform.get_client()
            await client.api.call_action(
                "upload_group_file",
                group_id=group_value,
                file=str(path),
                name=name,
            )
            logger.info(f"[link_downloader] 已通过 upload_group_file 上传：{name}")
            return True
        except Exception as exc:
            logger.warning(
                f"[link_downloader] upload_group_file 上传失败，改用通用文件消息：{exc}"
            )
            return False

    # ------------------------------------------------------------------ 指令
    @filter.command("download", alias={"dl"})
    async def download_cmd(self, event: AstrMessageEvent):
        """解析网盘 / GitHub 链接并列出文件清单（仅群聊可用）"""
        # 1) 仅群聊：私聊直接拒绝并提示
        if not is_group_event(event):
            event.stop_event()
            yield event.plain_result("❌ 该指令仅限群聊使用，请在群聊中发送 /download。")
            return

        text = (event.message_str or "").strip()

        # 2) 解析链接与提取码
        url = extract_url(text)
        if not url:
            event.stop_event()
            yield event.plain_result(USAGE)
            return
        password = extract_password(text)

        # 3) 选择对应的解析器
        try:
            parser = build_parser(
                url,
                self.http,
                timeout=self.network_timeout,
                max_file_size=self.max_file_size,
                max_depth=self.max_depth,
                github_mirrors=self.github_mirrors,
                github_token=self.github_token,
            )
        except LinkDownloadError as exc:
            event.stop_event()
            yield event.plain_result(f"❌ {exc}")
            return

        # 已由插件响应，阻止后续流程（例如调用 LLM）
        event.stop_event()
        yield event.plain_result(f"🔍 正在解析链接（{parser.display_name}），请稍候…")

        # 4) 解析文件清单（限时，避免网盘卡死）
        parse_timeout = max(30.0, self.network_timeout * 2.0)
        try:
            files = await asyncio.wait_for(
                parser.list_files(url, password), timeout=parse_timeout
            )
        except asyncio.TimeoutError:
            yield event.plain_result("❌ 解析超时：目标站点响应过慢，请稍后重试。")
            return
        except LinkDownloadError as exc:
            yield event.plain_result(f"❌ {exc}")
            return
        except Exception:
            logger.exception("[link_downloader] 解析链接时出现未预期异常")
            yield event.plain_result(
                "❌ 解析失败：网页结构可能已变化或链接无效，请稍后重试。"
            )
            return

        if not files:
            yield event.plain_result("❌ 未检索到任何可下载的文件（可能是空文件夹或分享已失效）。")
            return

        # 5) 登记会话并输出清单
        group_id = event.get_group_id()
        session = DownloadSession(
            key=f"{group_id}:{event.get_sender_id()}",
            group_id=group_id,
            platform_id=event.get_platform_id(),
            sender_id=event.get_sender_id(),
            sender_name=event.get_sender_name(),
            umo=event.unified_msg_origin,
            url=url,
            password=password,
            parser=parser,
            files=files,
        )
        self._register_session(session)
        yield event.plain_result(self.render_file_list(session))

    # ------------------------------------------------------------------ 选择监听
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_group_selection(self, event: AstrMessageEvent):
        """监听群聊回复：只有「同群 + 同一发起人」的序号选择才会生效。"""
        text = (event.message_str or "").strip()
        # 指令、空消息、以及各种以 / 开头的文本一律不处理
        if not text or text.startswith("/") or text.startswith("／"):
            return

        group_id = event.get_group_id()
        if not group_id:
            return

        key = f"{group_id}:{event.get_sender_id()}"
        session = self._sessions.get(key)
        if session is None:
            # 没有属于该用户的待选择任务：直接放过，不影响其他人聊天
            return

        # 双保险：即使定时任务因异常未执行，这里也能判定过期
        if session.is_expired(self.session_timeout):
            self._discard_session(session)
            event.stop_event()
            yield event.plain_result(
                f"⏰ 本次下载任务已过期（{self.session_timeout} 秒），"
                "文件清单已清空，请重新发送 /download。"
            )
            return

        # 取消任务
        if text.lower() in CANCEL_KEYWORDS:
            self._discard_session(session)
            event.stop_event()
            yield event.plain_result("🧹 已取消本次下载任务。")
            return

        selection = parse_selection(text, len(session.files))
        if selection is None:
            # 与任务无关的聊天内容：不拦截、不回复
            return

        event.stop_event()

        if not selection:
            yield event.plain_result(
                f"⚠️ 序号超出范围，请输入 1 ~ {len(session.files)} 之间的数字。"
            )
            return

        # 「全部」不受批量上限约束（用户明确要求下载清单内所有文件）；
        # 逐个回复序号时则限制单次批量，避免误触后刷屏式下载。
        wants_all = text.replace(" ", "").replace("　", "").lower() in ALL_KEYWORDS
        if not wants_all and len(selection) > self.max_batch_files:
            yield event.plain_result(
                f"⚠️ 一次最多下载 {self.max_batch_files} 个文件，"
                f"你选择了 {len(selection)} 个，请减少选择后重试（或回复「全部」）。"
            )
            return

        # 一次性任务：取出即失效
        self._discard_session(session)
        yield event.plain_result(
            f"✅ 已接收选择：共 {len(selection)} 个文件，开始下载…"
        )
        self.spawn(self.run_download(session, selection))

    # ------------------------------------------------------------------ 清单渲染
    def render_file_list(self, session: DownloadSession) -> str:
        """把文件清单渲染成带序号的文本消息。"""
        files = session.files
        total = len(files)
        header = (
            f"📋 解析成功｜来源：{session.parser.display_name}｜共 {total} 个文件\n"
        )
        footer = [
            "",
            "请回复序号选择要下载的文件：",
            "· 下载单个：回复 2",
            "· 批量下载：回复 1,3,4",
            "· 下载全部：回复 全部",
            "· 取消任务：回复 取消",
            "",
            f"⏰ 本次任务 {self.session_timeout} 秒内有效，超时自动作废。",
            f"⚠️ 单文件上限 {self.max_file_size_mb} MB，一次最多选择 {self.max_batch_files} 个文件。",
            "⚠️ 不同平台对上传文件大小限制不同，超出限制可能上传失败。",
        ]

        show = min(total, 40)
        while True:
            lines = [header]
            for index, item in enumerate(files[:show], start=1):
                lines.append(f"{index}. {item.name} | {item.size_text}")
            if total > show:
                lines.append(
                    f"…… 还有 {total - show} 个文件未展示，回复「全部」可下载全部文件。"
                )
            text = "\n".join(lines + footer)
            if len(text) <= 1800 or show <= 5:
                return text
            show = max(5, show - 5)

    # ------------------------------------------------------------------ 下载
    async def run_download(
        self, session: DownloadSession, indexes: list[int]
    ) -> None:
        """下载选中的文件并逐个上传到群聊，结束后清理临时文件。"""
        umo = session.umo
        parser = session.parser
        targets = [
            session.files[index]
            for index in indexes
            if 0 <= index < len(session.files)
        ]
        task_dir = self._tmp_root / f"{int(time.time())}_{uuid.uuid4().hex[:8]}"
        try:
            task_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            await self.send_text(umo, f"❌ 创建临时目录失败：{exc}")
            return

        succeeded: list[str] = []
        failed: list[tuple[str, str]] = []
        skipped: list[str] = []

        try:
            async with self._download_lock:
                for order, item in enumerate(targets, start=1):
                    name = safe_filename(item.name)
                    size_text = item.size_text

                    # 体积上限预检（列表里已经知道大小的情况）
                    if self.max_file_size and item.size > self.max_file_size:
                        skipped.append(name)
                        await self.send_text(
                            umo,
                            f"⚠️ 跳过「{name}」：大小 {size_text}，"
                            f"超过配置上限 {self.max_file_size_mb} MB。",
                        )
                        continue

                    await self.send_text(
                        umo,
                        f"⬇️ 正在下载（{order}/{len(targets)}）：{name}（{size_text}）",
                    )

                    dest = unique_path(task_dir, name)
                    reporter = _ProgressReporter(self, umo, name)
                    try:
                        path = await parser.download(
                            item,
                            dest,
                            max_bytes=self.max_file_size,
                            progress=reporter,
                        )
                    except asyncio.CancelledError:
                        raise
                    except LinkDownloadError as exc:
                        failed.append((name, str(exc)))
                        await self.send_text(umo, f"❌ 「{name}」下载失败：{exc}")
                        continue
                    except Exception as exc:
                        logger.exception(f"[link_downloader] 下载「{name}」出现未预期异常")
                        failed.append((name, str(exc)))
                        await self.send_text(umo, f"❌ 「{name}」下载失败：{exc}")
                        continue

                    try:
                        actual_size = path.stat().st_size
                    except OSError:
                        actual_size = -1

                    await self.send_text(
                        umo,
                        f"✅ 下载完成，准备上传：{name}（{human_size(actual_size)}）",
                    )

                    # 适配器上传限制提示
                    if self.platform_upload_limit and actual_size > self.platform_upload_limit:
                        await self.send_text(
                            umo,
                            f"⚠️ 提示：该文件 {human_size(actual_size)} 可能超过当前适配器的"
                            f"上传限制（约 {self.platform_upload_limit_mb} MB），若上传失败请"
                            "调整平台限制或改用直链分享。",
                        )

                    ok = await self.send_file(
                        umo,
                        path,
                        name,
                        platform_id=session.platform_id,
                        group_id=session.group_id,
                    )
                    if ok:
                        succeeded.append(name)
                    else:
                        failed.append((name, "文件上传失败"))
                        await self.send_text(
                            umo,
                            f"❌ 「{name}」上传失败：可能是平台限制或适配器不支持文件消息。",
                        )

                    # 上传结束（或失败且不保留）后立即删除临时文件
                    if ok or not self.keep_failed_temp:
                        with contextlib.suppress(OSError):
                            path.unlink(missing_ok=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("[link_downloader] 下载任务出现未预期异常")
            await self.send_text(umo, f"❌ 下载任务异常终止：{exc}")
        finally:
            keep_dir = self.keep_failed_temp and bool(failed)
            if not keep_dir:
                shutil.rmtree(task_dir, ignore_errors=True)

            parts = [f"成功 {len(succeeded)} 个"]
            if failed:
                parts.append(f"失败 {len(failed)} 个")
            if skipped:
                parts.append(f"跳过 {len(skipped)} 个")
            if keep_dir:
                summary = (
                    "🏁 本次任务结束："
                    + "，".join(parts)
                    + "。上传失败的临时文件保留在："
                    + str(task_dir)
                )
            else:
                summary = "🏁 本次任务结束：" + "，".join(parts) + "。临时文件已清理。"
            with contextlib.suppress(Exception):
                await self.send_text(umo, summary)

    # ------------------------------------------------------------------ 卸载
    async def terminate(self) -> None:
        """插件卸载 / 停用 / 重载时调用：取消任务、关闭连接、清理临时文件。"""
        for session in list(self._sessions.values()):
            session.cancel()
        self._sessions.clear()

        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(BaseException):
                await task
        self._tasks.clear()

        await self.http.close()
        shutil.rmtree(self._tmp_root, ignore_errors=True)
        logger.info("[link_downloader] 插件已卸载，资源已释放")
