import json
import os
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
    输入指定城市的英文名称，返回今日天气查询结果。
    :filename : 文件名
    :content: 内容
    """

    try:
        # 防止路径攻击
        filename = filename.replace("/", "").replace("\\", "")

        full_path = os.path.join(BASE_DIR, f"{filename}.docx")

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


@mcp.tool()
def find_and_read_local_file(filename: str, requirement: str = "") -> str:
    """
    根据文件名在本地目录中查找文件并读取内容。
    :param filename: 待查找的文件名（支持完整文件名或关键字）
    :param requirement: 用户需求（可选），会附在返回文本中方便模型解析
    :return: 文件定位与内容
    """
    try:
        search_root = Path(os.getenv("LOCAL_SEARCH_DIR", BASE_DIR)).resolve()

        if not search_root.exists() or not search_root.is_dir():
            return f"本地搜索目录无效：{search_root}"

        keyword = filename.strip().lower()
        if not keyword:
            return "请提供要查找的文件名或关键字。"

        candidates: list[Path] = []
        for path in search_root.rglob("*"):
            if path.is_file() and keyword in path.name.lower():
                candidates.append(path)

        if not candidates:
            return f"未找到包含关键字“{filename}”的文件。搜索目录：{search_root}"

        # 优先匹配完整文件名，其次取最短路径的候选
        exact = [p for p in candidates if p.name.lower() == keyword]
        target = sorted(exact or candidates, key=lambda x: (len(str(x)), str(x)))[0]

        file_content = _safe_read_file(target)
        requirement_text = requirement.strip()

        response = (
            f"已找到文件：{target}\n"
            f"文件名：{target.name}\n"
            f"文件大小：{target.stat().st_size} bytes\n"
        )

        if requirement_text:
            response += f"用户需求：{requirement_text}\n"

        response += f"文件内容：\n{file_content}"
        return response

    except Exception as e:
        return f"查找或读取文件失败: {e}"


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
