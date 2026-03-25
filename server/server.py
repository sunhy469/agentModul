import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from docx import Document
from fastmcp import FastMCP

# 初始化 MCP 服务器
mcp = FastMCP("WeatherServer")

# OpenWeather API 配置
OPENWEATHER_API_BASE = "https://api.openweathermap.org/data/2.5/weather"
API_KEY = "b9b91b1fba4fa6a2f5675288e673f9f4"
USER_AGENT = "weather-app/1.0"

# 保存目录（建议固定目录）
BASE_DIR = os.path.join(os.getcwd(), "generated_files")
os.makedirs(BASE_DIR, exist_ok=True)
SAFE_ROOT = Path(os.getenv("SAFE_WORK_ROOT", BASE_DIR)).expanduser().resolve()
PENDING_FILE_OPERATIONS: dict[str, dict[str, Any]] = {}


async def fetch_weather(city: str) -> dict[str, Any] | None:
    """
    从 OpenWeather API 获取天气信息。
    演示模式下返回模拟数据，实际使用时连接真实API
    """
    # 演示模式：返回模拟天气数据
    demo_data = {
        "name": city.title(),
        "sys": {"country": "CN"},
        "main": {
            "temp": 15,
            "humidity": 60
        },
        "wind": {"speed": 2.5},
        "weather": [{"description": "晴朗"}]
    }

    # 如果提供了真实API密钥，则尝试连接真实API
    if API_KEY != "demo-key" and API_KEY != "b9b91b1fba4fa6a2f5675288e673f9f4":
        params = {
            "q": city,
            "appid": API_KEY,
            "units": "metric",
            "lang": "zh_cn"
        }
        headers = {"User-Agent": USER_AGENT}

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    OPENWEATHER_API_BASE,
                    params=params,
                    headers=headers,
                    timeout=30.0
                )
                response.raise_for_status()
                return response.json()

        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP 错误: {e.response.status_code}"}

        except Exception as e:
            return {"error": f"请求失败: {str(e)}"}

    return demo_data


def format_weather(data: dict[str, Any] | str) -> str:
    """
    将天气数据格式化为易读文本。
    """
    # 如果传入的是字符串，则先转换为字典
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception as e:
            return f"无法解析天气数据：{e}"

    # 如果数据中包含错误信息，直接返回错误提示
    if "error" in data:
        return f"{data['error']}"

    # 提取数据时做容错处理
    city = data.get("name", "未知")
    country = data.get("sys", {}).get("country", "未知")
    temp = data.get("main", {}).get("temp", "N/A")
    humidity = data.get("main", {}).get("humidity", "N/A")
    wind_speed = data.get("wind", {}).get("speed", "N/A")

    # weather 可能为空列表，因此用 [0] 前先提供默认字典
    weather_list = data.get("weather", [{}])
    description = weather_list[0].get("description", "未知")

    return (
        f"🌍 {city}, {country}\n"
        f"🌡️ 温度: {temp}°C\n"
        f"💧 湿度: {humidity}%\n"
        f"💨 风速: {wind_speed} m/s\n"
        f"☁️ 天气: {description}\n"
    )


@mcp.tool()
def create_word_file(filename: str, content: str) -> str:
    """
    根据文件名和内容创建 Word 文件。
    :param filename : 文件名
    :param content: 内容
    """

    try:
        # 防止路径攻击
        filename = filename.replace("/", "").replace("\\", "")

        full_path = os.path.join(BASE_DIR, f"{filename}")

        doc = Document()
        doc.add_heading(filename, level=1)

        # 按行写入
        for line in content.split("\n"):
            doc.add_paragraph(line)

        doc.save(full_path)

        return f"Word 文件已创建成功：{full_path}"

    except Exception as e:
        return f"创建文件失败: {e}"


def _safe_read_file(file_path: Path, max_chars: int = 12000) -> str:
    """读取文件内容并做基础截断，避免返回过大文本。"""
    suffix = file_path.suffix.lower()

    if suffix == ".docx":
        doc = Document(str(file_path))
        content = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    else:
        try:
            content = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return "该文件不是 UTF-8 文本，暂不支持直接解析。"
        except Exception as e:
            return f"读取文件失败: {e}"

    if not content.strip():
        return "文件内容为空。"

    if len(content) > max_chars:
        return content[:max_chars] + f"\n\n...（内容过长，已截断，原始长度 {len(content)} 字符）"

    return content


def _resolve_search_root(directory: str = "") -> Path:
    if directory.strip():
        target = Path(directory).expanduser().resolve()
    else:
        target = Path(os.getenv("LOCAL_SEARCH_DIR", BASE_DIR)).resolve()

    # 安全边界：仅允许在 SAFE_ROOT 下检索
    try:
        target.relative_to(SAFE_ROOT)
    except ValueError:
        return SAFE_ROOT

    return target


def _is_within_safe_root(target: Path) -> bool:
    try:
        target.resolve().relative_to(SAFE_ROOT)
        return True
    except Exception:
        return False


def _ensure_safe_target(file_path: str) -> tuple[Path | None, str]:
    if not file_path.strip():
        return None, "请提供文件路径。"
    target = Path(file_path).expanduser().resolve()
    if not target.exists() or not target.is_file():
        return None, f"文件不存在：{target}"
    if not _is_within_safe_root(target):
        return None, f"拒绝访问：目标超出安全目录 {SAFE_ROOT}"
    return target, ""


def _find_file_by_keyword(filename: str, directory: str = "") -> tuple[Path | None, str]:
    """按关键字查找文件并返回最佳匹配。"""
    search_root = _resolve_search_root(directory)
    if not search_root.exists() or not search_root.is_dir():
        return None, f"目录无效：{search_root}"

    keyword = filename.strip().lower()
    if not keyword:
        return None, "请提供文件名或关键字。"

    candidates = [
        p for p in search_root.rglob("*")
        if p.is_file() and keyword in p.name.lower()
    ]

    if not candidates:
        return None, f"未找到包含关键字“{filename}”的文件。搜索目录：{search_root}"

    exact = [p for p in candidates if p.name.lower() == keyword]
    target = sorted(exact or candidates, key=lambda x: (len(str(x)), str(x)))[0]
    return target, ""




@mcp.tool()
def find_and_read_local_file(filename: str, requirement: str = "", open_with_default: bool = True) -> str:
    """
    根据文件名在本地目录中查找文件，并执行“读取内容 + 默认应用打开”。
    :param filename: 待查找的文件名（支持完整文件名或关键字）
    :param requirement: 用户需求（可选），会附在返回文本中方便模型解析
    :param open_with_default: 是否按系统默认方式打开文件（默认 True）
    :return: 文件定位与内容
    """
    try:
        target, err = _find_file_by_keyword(filename)
        if err:
            return err

        if open_with_default:
            _open_with_default_app(target)

        file_content = _safe_read_file(target)
        requirement_text = requirement.strip()

        response = (
            f"已找到文件：{target}\n"
            f"文件名：{target.name}\n"
            f"文件大小：{target.stat().st_size} bytes\n"
        )

        if open_with_default:
            response += "文件已按系统默认方式打开。\n"

        if requirement_text:
            response += f"用户需求：{requirement_text}\n"

        response += f"文件内容：\n{file_content}"
        return response

    except Exception as e:
        return f"查找、读取或打开文件失败: {e}"




def _resolve_windows_app_path(app_command: str) -> str:
    """在 Windows 上尝试解析常见应用别名与安装路径。"""
    alias_map = {
        "qq": ["QQScLauncher.exe", "QQ.exe", "QQNT.exe"],
        "wechat": ["WeChat.exe"],
        "weixin": ["WeChat.exe"],
        "chrome": ["chrome.exe"],
        "edge": ["msedge.exe"],
    }

    raw = app_command.strip().strip('"')
    lower_name = raw.lower()

    # 已经是路径
    direct_path = Path(raw).expanduser()
    if direct_path.exists() and direct_path.is_file():
        return str(direct_path.resolve())

    # 先走 PATH
    which_path = shutil.which(raw)
    if which_path:
        return which_path

    candidates = []
    if lower_name in alias_map:
        candidates.extend(alias_map[lower_name])

    if lower_name.endswith('.exe'):
        candidates.append(raw)
    else:
        candidates.extend([raw, f"{raw}.exe"])

    roots = [
        Path(os.getenv("ProgramFiles", "C:/Program Files")),
        Path(os.getenv("ProgramFiles(x86)", "C:/Program Files (x86)")),
        Path(os.getenv("LOCALAPPDATA", "")),
    ]

    # 针对 QQ 的高频目录先做快速定位
    qq_fast_paths = [
        "Tencent/QQ/Bin/QQScLauncher.exe",
        "Tencent/QQ/Bin/QQ.exe",
        "Tencent/QQNT/QQ.exe",
        "Tencent/QQNT/QQNT.exe",
    ]
    if lower_name == "qq":
        for root in roots:
            for rel in qq_fast_paths:
                candidate = root / rel
                if candidate.exists():
                    return str(candidate.resolve())

    # 通用浅层搜索（限制层级，避免太慢）
    for root in roots:
        if not root.exists():
            continue
        for exe_name in candidates:
            try:
                for current_root, dirs, files in os.walk(root):
                    # 限制搜索深度
                    depth = Path(current_root).relative_to(root).parts
                    if len(depth) > 4:
                        dirs[:] = []
                        continue
                    for f in files:
                        if f.lower() == exe_name.lower():
                            return str((Path(current_root) / f).resolve())
            except Exception:
                continue

    return ""


def _build_launch_command(app_command: str, arguments: str = "") -> list[str]:
    """构造安全的启动命令，避免 shell 注入。"""
    system_name = platform.system().lower()
    raw = app_command.strip()
    if not raw:
        return []

    blocked_tokens = ["&&", "||", ";", "|", "`", "$("]
    if any(token in raw for token in blocked_tokens) or any(token in arguments for token in blocked_tokens):
        return []

    if system_name == "windows":
        executable = _resolve_windows_app_path(raw) or raw
        args = shlex.split(arguments, posix=False) if arguments.strip() else []
        return [executable, *args]

    executable = shutil.which(raw) or raw
    args = shlex.split(arguments) if arguments.strip() else []
    return [executable, *args]


@mcp.tool()
def open_local_application(app_command: str, arguments: str = "") -> str:
    """
    打开本地应用（通过命令行）。
    :param app_command: 应用启动命令，例如 qq、notepad、calc、code
    :param arguments: 可选启动参数
    """
    try:
        launch_cmd = _build_launch_command(app_command, arguments)
        if not launch_cmd:
            return "请提供应用启动命令。"

        subprocess.Popen(launch_cmd, shell=False)
        return f"已尝试启动应用：{' '.join(launch_cmd)}"
    except FileNotFoundError:
        return (
            "未找到可执行程序。可尝试：\n"
            "1) 直接传完整 exe 路径；\n"
            "2) 使用常见别名（如 qq/wechat/chrome）；\n"
            "3) 将应用目录加入系统 PATH。"
        )
    except Exception as e:
        return f"启动应用失败: {e}"


def _open_with_default_app(target: Path) -> None:
    """按系统默认方式打开文件。"""
    system_name = platform.system().lower()
    if system_name == "windows":
        os.startfile(str(target))
    elif system_name == "darwin":
        subprocess.Popen(["open", str(target)])
    else:
        subprocess.Popen(["xdg-open", str(target)])


@mcp.tool()
def read_file(file_path: str, open_with_default: bool = True) -> str:
    """
    读取并（可选）按系统默认方式打开文件。
    :param file_path: 文件完整路径
    :param open_with_default: 是否按默认应用打开（默认 True）
    """
    try:
        if not file_path.strip():
            return "请提供文件路径。"

        target = Path(file_path).expanduser().resolve()
        if not target.exists() or not target.is_file():
            return f"文件不存在：{target}"
        if not _is_within_safe_root(target):
            return f"拒绝访问：目标超出安全目录 {SAFE_ROOT}"

        if open_with_default:
            _open_with_default_app(target)
            return f"已按系统默认方式打开文件：{target}"

        # 兼容部分请求：如果不打开则返回内容摘要
        content = _safe_read_file(target)
        return f"文件路径：{target}\n文件内容：\n{content}"
    except Exception as e:
        return f"读取或打开文件失败: {e}"


@mcp.tool()
def open_path_in_file_manager(target_path: str = "") -> str:
    """
    在系统文件管理器中打开目录或文件。
    :param target_path: 目标路径，留空则打开 BASE_DIR
    """
    try:
        target = Path(target_path).expanduser().resolve() if target_path.strip() else Path(BASE_DIR).resolve()

        if not target.exists():
            return f"目标路径不存在：{target}"

        system_name = platform.system().lower()
        if system_name == "windows":
            os.startfile(str(target))
        elif system_name == "darwin":
            subprocess.Popen(["open", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target)])

        return f"已在文件管理器打开：{target}"
    except Exception as e:
        return f"打开路径失败: {e}"


@mcp.tool()
def list_local_files(directory: str = "", keyword: str = "", limit: int = 20) -> str:
    """
    列出本地文件，便于后续让模型选择要读取的文件。
    :param directory: 搜索目录，默认 LOCAL_SEARCH_DIR 或 BASE_DIR
    :param keyword: 文件名过滤关键字
    :param limit: 最多返回条数
    """
    try:
        search_root = _resolve_search_root(directory)
        if not search_root.exists() or not search_root.is_dir():
            return f"目录无效：{search_root}"

        kw = keyword.strip().lower()
        matches = []
        for p in search_root.rglob("*"):
            if not p.is_file():
                continue
            if kw and kw not in p.name.lower():
                continue
            matches.append(p)

        if not matches:
            return f"目录中未找到匹配文件。目录：{search_root}"

        limit = max(1, min(limit, 100))
        selected = sorted(matches, key=lambda x: str(x))[:limit]

        lines = [f"搜索目录：{search_root}", f"匹配数量：{len(matches)}（展示前 {len(selected)} 条）"]
        for idx, item in enumerate(selected, 1):
            lines.append(f"{idx}. {item.name} | {item}")

        return "\n".join(lines)
    except Exception as e:
        return f"列出文件失败: {e}"


@mcp.tool()
def create_pending_file_operation(
    operation: str,
    file_path: str,
    new_content: str = "",
    reason: str = "",
) -> str:
    """
    创建待确认文件操作（delete/modify），用于双重安全确认。
    """
    op = operation.strip().lower()
    if op not in {"delete", "modify"}:
        return "operation 仅支持 delete 或 modify。"

    target, err = _ensure_safe_target(file_path)
    if err:
        return err

    if op == "modify" and not new_content.strip():
        return "modify 操作必须提供 new_content。"

    operation_id = uuid.uuid4().hex[:12]
    PENDING_FILE_OPERATIONS[operation_id] = {
        "operation": op,
        "target": str(target),
        "new_content": new_content,
        "reason": reason.strip(),
        "created_at": time.time(),
    }
    return (
        f"已创建待确认操作，operation_id={operation_id}\n"
        f"类型: {op}\n目标: {target}\n"
        "请调用 confirm_file_operation(operation_id, confirm=True) 执行；"
        "若取消请传 confirm=False。"
    )


@mcp.tool()
def confirm_file_operation(operation_id: str, confirm: bool) -> str:
    """
    执行或取消待确认文件操作，防止误删误改。
    """
    payload = PENDING_FILE_OPERATIONS.pop(operation_id, None)
    if not payload:
        return f"未找到待确认操作：{operation_id}"

    if not confirm:
        return f"已取消操作：{operation_id}"

    target = Path(payload["target"]).expanduser().resolve()
    if not _is_within_safe_root(target):
        return f"拒绝执行：目标超出安全目录 {SAFE_ROOT}"

    if payload["operation"] == "delete":
        target.unlink(missing_ok=False)
        return f"已删除文件：{target}"

    target.write_text(payload["new_content"], encoding="utf-8")
    return f"已更新文件：{target}（UTF-8 覆盖写入）"


@mcp.tool()
def get_pending_operations() -> str:
    """查看尚未确认的文件操作。"""
    if not PENDING_FILE_OPERATIONS:
        return "当前没有待确认文件操作。"

    lines = [f"待确认操作数量: {len(PENDING_FILE_OPERATIONS)}"]
    for op_id, payload in PENDING_FILE_OPERATIONS.items():
        lines.append(f"- {op_id} | {payload['operation']} | {payload['target']}")
    return "\n".join(lines)


@mcp.tool()
def analyze_time_series(values: list[float], horizon: int = 3) -> str:
    """
    时间序列快速分析：给出均值、趋势与线性外推预测。
    """
    if not values or len(values) < 3:
        return "请至少提供 3 个数值。"
    horizon = max(1, min(30, int(horizon)))
    n = len(values)
    avg = sum(values) / n
    trend = (values[-1] - values[0]) / (n - 1)
    forecast = [round(values[-1] + trend * (i + 1), 4) for i in range(horizon)]
    return (
        f"样本数: {n}\n均值: {avg:.4f}\n"
        f"线性趋势(每步): {trend:.4f}\n"
        f"未来 {horizon} 步预测: {forecast}"
    )


@mcp.tool()
async def send_webhook_message(
    text: str,
    webhook_url: str = "",
    provider: str = "generic",
) -> str:
    """
    发送 webhook 消息，可用于飞书/企业微信/自定义机器人通知。
    """
    if not text.strip():
        return "text 不能为空。"

    provider_key = provider.strip().lower()
    resolved_webhook = webhook_url.strip()
    if not resolved_webhook:
        if provider_key in {"feishu", "lark"}:
            resolved_webhook = os.getenv("FEISHU_WEBHOOK_URL", "").strip()
        else:
            resolved_webhook = os.getenv("DEFAULT_WEBHOOK_URL", "").strip()
    if not resolved_webhook.startswith("http"):
        return (
            "webhook_url 无效。请在参数传入 webhook_url，"
            "或配置环境变量 FEISHU_WEBHOOK_URL / DEFAULT_WEBHOOK_URL。"
        )

    if provider_key in {"feishu", "lark"}:
        payload = {"msg_type": "text", "content": {"text": text}}
    else:
        payload = {"text": text}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(resolved_webhook, json=payload)
            content = resp.text[:300]
            return (
                f"Webhook 已发送，provider={provider_key or 'generic'}，HTTP {resp.status_code}，"
                f"响应片段：{content}"
            )
    except Exception as exc:
        return f"Webhook 发送失败: {exc}"


@mcp.tool()
def summarize_local_capabilities() -> str:
    """
    返回当前可用于本地自动化的能力说明，便于多工具编排。
    """
    return (
        "当前本地自动化能力：\n"
        "1) 文件系统：查找/读取/列举/待确认修改删除。\n"
        "2) 本地应用：按命令启动应用（可用于拉起 QQ、飞书、浏览器）。\n"
        "3) 通知消息：支持 webhook 发送（飞书机器人/自定义服务）。\n"
        "4) 桌面 IM：支持自动粘贴并发送消息（依赖 pyautogui + pyperclip）。\n"
        "5) 文档处理：可创建 Word 文件并导出。\n"
        "6) 时间序列：基础统计与趋势预测。"
    )


@mcp.tool()
def send_desktop_message(
    app_command: str,
    message: str,
    press_enter: bool = True,
    warmup_seconds: float = 1.5,
) -> str:
    """
    桌面 IM 自动发送消息（QQ/飞书/企业微信等）。
    说明：依赖 pyautogui + pyperclip，且需要桌面会话可交互。
    """
    if not app_command.strip():
        return "请提供 app_command（如 qq / feishu / wechat）。"
    if not message.strip():
        return "请提供 message 文本。"

    try:
        import pyautogui
        import pyperclip
    except Exception:
        return "缺少依赖：请安装 pyautogui 与 pyperclip 后重试。"

    launch = open_local_application(app_command, "")
    pyperclip.copy(message)
    time.sleep(max(0.5, min(warmup_seconds, 10.0)))

    if platform.system().lower() == "darwin":
        pyautogui.hotkey("command", "v")
    else:
        pyautogui.hotkey("ctrl", "v")
    if press_enter:
        pyautogui.press("enter")

    return f"{launch}\n已自动粘贴消息并{'发送' if press_enter else '停留待确认'}。"


def _detect_channel_from_request(request: str) -> str:
    text = (request or "").lower()
    if any(k in text for k in ["飞书", "feishu", "lark"]):
        return "feishu"
    if any(k in text for k in ["qq", "企鹅"]):
        return "qq"
    return "auto"


def _extract_message_from_request(request: str) -> str:
    text = (request or "").strip()
    if not text:
        return ""
    quoted = re.findall(r"[\"“”']([^\"“”']+)[\"“”']", text)
    if quoted:
        return quoted[-1].strip()

    patterns = [
        r"(?:发送|发|send)\s*(.+?)\s*(?:到|给).*(?:飞书|lark|qq)",
        r"(?:在|往).*(?:飞书|lark|qq).*(?:发送|发)\s*(.+)",
    ]
    for pattern in patterns:
        matched = re.search(pattern, text, re.IGNORECASE)
        if matched:
            return matched.group(1).strip("：: ，,。.!！")
    return text


@mcp.tool()
async def send_message_by_request(
    request: str,
    message: str = "",
    preferred_channel: str = "auto",
    qq_app_command: str = "qq",
    auto_send: bool = True,
    webhook_url: str = "",
) -> str:
    """
    根据请求自动选择飞书或 QQ 发送消息。
    - 飞书：优先参数 webhook_url，否则读 FEISHU_WEBHOOK_URL
    - QQ：调用桌面自动发送
    """
    channel = preferred_channel.strip().lower()
    if channel == "auto":
        channel = _detect_channel_from_request(request)
    final_message = message.strip() or _extract_message_from_request(request)
    if not final_message:
        return "消息内容为空，请提供 request 或 message。"

    if channel in {"feishu", "lark"}:
        return await send_webhook_message(
            webhook_url=webhook_url,
            text=final_message,
            provider="feishu",
        )
    if channel == "qq":
        return send_desktop_message(
            app_command=qq_app_command,
            message=final_message,
            press_enter=auto_send,
        )

    if os.getenv("FEISHU_WEBHOOK_URL", "").strip():
        return await send_webhook_message(
            webhook_url=webhook_url,
            text=final_message,
            provider="feishu",
        )
    return send_desktop_message(
        app_command=qq_app_command,
        message=final_message,
        press_enter=auto_send,
    )




@mcp.tool()
def execute_complex_instruction(
        instruction: str = "",
        file_keyword: str = "",
        directory: str = "",
        open_file: bool = True,
        read_content: bool = True,
        requirement: str = "",
        app_command: str = "",
        app_arguments: str = "",
        export_word_name: str = "",
        export_word_content: str = "",
) -> str:
    """
    执行复杂指令（结构化编排版）。

    设计目标：由大模型先完成语义理解，将意图映射为参数；本工具只做稳定、可控的步骤执行，
    避免在服务端做关键词匹配导致“看起来不智能”。

    参数说明：
    - instruction: 原始用户指令（用于记录）
    - file_keyword: 需要查找的文件关键字（为空则跳过文件步骤）
    - directory: 可选搜索目录
    - open_file/read_content/requirement: 文件处理行为控制
    - app_command/app_arguments: 应用启动步骤
    - export_word_name/export_word_content: 文档导出步骤
    """
    try:
        begin = time.perf_counter()
        steps: list[str] = []
        timings: list[str] = []
        if instruction.strip():
            steps.append(f"复杂指令：{instruction.strip()}")

        content_cache = ""

        # 1) 文件查找/读取/打开
        if file_keyword.strip():
            t0 = time.perf_counter()
            target, err = _find_file_by_keyword(file_keyword, directory)
            if err:
                steps.append(f"[文件步骤失败] {err}")
            else:
                steps.append(f"[文件步骤] 已定位文件：{target}")

                if open_file:
                    _open_with_default_app(target)
                    steps.append(f"[文件步骤] 已按默认应用打开：{target}")

                if read_content:
                    content_cache = _safe_read_file(target)
                    steps.append(f"[文件步骤] 已读取内容（长度 {len(content_cache)}）")

                if requirement.strip():
                    steps.append(f"[文件步骤] 解析需求：{requirement.strip()}")
            timings.append(f"[耗时] 文件阶段: {(time.perf_counter() - t0) * 1000:.2f} ms")

        # 2) 打开应用
        if app_command.strip():
            t1 = time.perf_counter()
            app_result = open_local_application(app_command, app_arguments)
            steps.append(f"[应用步骤] {app_result}")
            timings.append(f"[耗时] 应用阶段: {(time.perf_counter() - t1) * 1000:.2f} ms")

        # 3) 导出 Word
        final_export_name = export_word_name.strip()
        final_export_content = export_word_content.strip()
        if final_export_name:
            t3 = time.perf_counter()
            if not final_export_content:
                final_export_content = content_cache[:3000] if content_cache else "（未提供导出内容）"
            export_result = create_word_file(final_export_name, final_export_content)
            steps.append(f"[导出步骤] {export_result}")
            timings.append(f"[耗时] 导出阶段: {(time.perf_counter() - t3) * 1000:.2f} ms")

        if len(steps) == 0:
            return "未接收到可执行的复杂步骤参数，请由模型先解析意图后传入结构化参数。"

        steps.extend(timings)
        steps.append(f"[性能汇总] 总耗时: {(time.perf_counter() - begin) * 1000:.2f} ms")
        return "\n".join(steps)
    except Exception as e:
        return f"复杂指令执行失败: {e}"

@mcp.tool()
async def query_weather(city: str) -> str:
    """
    输入指定城市的英文名称，返回今日天气查询结果。
    :param city: 城市名称
    :return: 格式化后的天气信息
    """
    data = await fetch_weather(city)
    return format_weather(data)


@mcp.tool()
async def get_weather_forecast(city: str, days: int = 3) -> str:
    """
    获取指定城市未来几天的天气预报（演示功能）
    :param city: 城市名称
    :param days: 预报天数（默认3天）
    :return: 天气预报信息
    """
    # 演示模式的天气预报
    forecast_data = {
        "city": city.title(),
        "forecast": [
            {"day": f"{i + 1}天", "temp": f"{20 + i}℃", "weather": "晴朗"}
            for i in range(days)
        ]
    }
    result = f"{forecast_data['city']} 未来{days}天天气预报：\n"
    for day_info in forecast_data['forecast']:
        result += f"{day_info['day']}: {day_info['temp']} - {day_info['weather']}\n"

    return result


if __name__ == "__main__":
    # 以标准 I/O 方式运行 MCP 服务器
    mcp.run(transport='stdio')
