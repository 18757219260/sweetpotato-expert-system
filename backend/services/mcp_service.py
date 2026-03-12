"""
services/mcp_service.py - Model Context Protocol (MCP) 工具调用服务

功能：
1. 天气查询工具（和风天气 API）
2. 工具调用管理与结果格式化
"""

import os
import json
import requests
from typing import Optional, Dict, Any, List
from datetime import datetime
from dotenv import load_dotenv
from backend.services.city_id_map import CITY_ID_MAP

load_dotenv()

# ── 配置 ─────────────────────────────────────────────────────────────────────
QWEATHER_API_KEY = os.getenv("QWEATHER_API_KEY", "")  # 和风天气 API Key

# 和风天气 API 端点（使用自定义域名）
QWEATHER_BASE_URL =os.getenv("QWEATHER_API_HOST", "")
QWEATHER_GEO_API = f"{QWEATHER_BASE_URL}/v2/city/lookup"
QWEATHER_WEATHER_NOW_API = f"{QWEATHER_BASE_URL}/v7/weather/now"
QWEATHER_WEATHER_3D_API = f"{QWEATHER_BASE_URL}/v7/weather/3d"
QWEATHER_WEATHER_7D_API = f"{QWEATHER_BASE_URL}/v7/weather/7d"



# ── 工具定义 ─────────────────────────────────────────────────────────────────
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "获取指定地点的天气信息。当用户询问天气、气温、降雨、是否适合打药等问题时使用此工具。",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "地点名称，格式为：省份+城市+区县，例如：浙江省湖州市长兴县"
                    },
                    "days": {
                        "type": "integer",
                        "description": "查询天数，1=今天，3=未来3天，7=未来7天",
                        "enum": [1, 3, 7],
                        "default": 1
                    }
                },
                "required": ["location"]
            }
        }
    }
]


# ── 工具实现 ─────────────────────────────────────────────────────────────────

def get_weather(location: str, days: int = 1) -> Dict[str, Any]:
    """
    获取天气信息（和风天气 API）

    Args:
        location: 地点名称（支持完整地址或城市名）
                 例如："浙江省湖州市长兴县" 或 "长兴"
        days: 查询天数（1/3/7）

    Returns:
        天气信息字典
    """
    if not QWEATHER_API_KEY:
        return {
            "success": False,
            "error": "和风天气 API Key 未配置",
            "message": "请在 .env 文件中配置 QWEATHER_API_KEY"
        }

    try:
        # 准备请求头（使用 Header 认证）
        headers = {"X-QW-Api-Key": QWEATHER_API_KEY}

        # 直接从映射表获取城市 ID
        location_id = CITY_ID_MAP.get(location, location)

        # 直接使用城市 ID 查询天气
        weather_params = {"location": location_id}

        if days == 1:
            # 实时天气
            weather_resp = requests.get(QWEATHER_WEATHER_NOW_API, params=weather_params, headers=headers, timeout=5)
            weather_data = weather_resp.json()

            if weather_data.get("code") != "200":
                return {
                    "success": False,
                    "error": "天气查询失败",
                    "message": weather_data.get("code", "未知错误")
                }

            now = weather_data["now"]
            return {
                "success": True,
                "location": location,
                "type": "current",
                "data": {
                    "temperature": f"{now['temp']}°C",
                    "feels_like": f"{now['feelsLike']}°C",
                    "weather": now["text"],
                    "wind": f"{now['windDir']} {now['windScale']}级",
                    "humidity": f"{now['humidity']}%",
                    "pressure": f"{now['pressure']}hPa",
                    "visibility": f"{now['vis']}km",
                    "update_time": now["obsTime"]
                }
            }

        elif days == 3:
            # 未来3天预报
            weather_resp = requests.get(QWEATHER_WEATHER_3D_API, params=weather_params, headers=headers, timeout=5)
            weather_data = weather_resp.json()

            if weather_data.get("code") != "200":
                return {
                    "success": False,
                    "error": "天气查询失败",
                    "message": weather_data.get("code", "未知错误")
                }

            daily = weather_data["daily"]
            forecast = []
            for day in daily:
                forecast.append({
                    "date": day["fxDate"],
                    "temp_max": f"{day['tempMax']}°C",
                    "temp_min": f"{day['tempMin']}°C",
                    "weather_day": day["textDay"],
                    "weather_night": day["textNight"],
                    "wind": f"{day['windDirDay']} {day['windScaleDay']}级",
                    "humidity": f"{day['humidity']}%",
                    "precip": f"{day['precip']}mm"
                })

            return {
                "success": True,
                "location": location,
                "type": "forecast_3d",
                "data": forecast
            }

        else:  # days == 7
            # 未来7天预报
            weather_resp = requests.get(QWEATHER_WEATHER_7D_API, params=weather_params, headers=headers, timeout=5)
            weather_data = weather_resp.json()

            if weather_data.get("code") != "200":
                return {
                    "success": False,
                    "error": "天气查询失败",
                    "message": weather_data.get("code", "未知错误")
                }

            daily = weather_data["daily"]
            forecast = []
            for day in daily:
                forecast.append({
                    "date": day["fxDate"],
                    "temp_max": f"{day['tempMax']}°C",
                    "temp_min": f"{day['tempMin']}°C",
                    "weather_day": day["textDay"],
                    "weather_night": day["textNight"],
                    "wind": f"{day['windDirDay']} {day['windScaleDay']}级",
                    "humidity": f"{day['humidity']}%",
                    "precip": f"{day['precip']}mm"
                })

            return {
                "success": True,
                "location": location,
                "type": "forecast_7d",
                "data": forecast
            }

    except requests.exceptions.Timeout:
        return {
            "success": False,
            "error": "请求超时",
            "message": "天气 API 请求超时，请稍后重试"
        }
    except Exception as e:
        return {
            "success": False,
            "error": "未知错误",
            "message": str(e)
        }




# ── 工具调用管理 ─────────────────────────────────────────────────────────────

def execute_tool(tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """
    执行工具调用

    Args:
        tool_name: 工具名称
        arguments: 工具参数

    Returns:
        工具执行结果
    """
    if tool_name == "get_weather":
        return get_weather(
            location=arguments.get("location", ""),
            days=arguments.get("days", 1)
        )
    else:
        return {
            "success": False,
            "error": "未知工具",
            "message": f"工具 {tool_name} 不存在"
        }


def format_tool_result(tool_name: str, result: Dict[str, Any]) -> str:
    """
    格式化工具调用结果为自然语言

    Args:
        tool_name: 工具名称
        result: 工具执行结果

    Returns:
        格式化后的文本
    """
    if not result.get("success"):
        return f"⚠️ 工具调用失败：{result.get('message', '未知错误')}"

    if tool_name == "get_weather":
        location = result.get("location", "")
        weather_type = result.get("type", "")
        data = result.get("data", {})

        if weather_type == "current":
            # 实时天气
            return f"""📍 {location} 当前天气：
🌡️ 温度：{data.get('temperature')}（体感 {data.get('feels_like')}）
☁️ 天气：{data.get('weather')}
💨 风力：{data.get('wind')}
💧 湿度：{data.get('humidity')}
🔍 能见度：{data.get('visibility')}
⏰ 更新时间：{data.get('update_time')}"""

        elif weather_type in ["forecast_3d", "forecast_7d"]:
            # 预报天气
            days_text = "未来3天" if weather_type == "forecast_3d" else "未来7天"
            forecast_list = data

            lines = [f"📍 {location} {days_text}天气预报：\n"]
            for day in forecast_list:
                lines.append(f"📅 {day['date']}")
                lines.append(f"  🌡️ {day['temp_min']} ~ {day['temp_max']}")
                lines.append(f"  ☁️ {day['weather_day']} 转 {day['weather_night']}")
                lines.append(f"  💨 {day['wind']}")
                lines.append(f"  💧 湿度 {day['humidity']}，降水 {day['precip']}")
                lines.append("")

            return "\n".join(lines)

    
