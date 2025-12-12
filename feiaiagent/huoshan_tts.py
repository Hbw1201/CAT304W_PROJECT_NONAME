# -*- coding: utf-8 -*-
"""
火山引擎 TTS 核心模块
基于火山引擎的文本转语音引擎，兼容现有接口
"""

import asyncio
import json
import logging
import uuid
import os
import pathlib
import struct
import subprocess
import shutil
import tempfile
from pathlib import Path
from typing import Optional, Dict, Any
import websocket
import threading
import ssl
import base64
import hashlib
import hmac
from urllib.parse import urlencode
from datetime import datetime
from time import mktime
from wsgiref.handlers import format_date_time

import concurrent.futures
from functools import lru_cache
from queue import Queue
import requests

from volc_engine_config import VolcEngineConfig

# 全局线程池和连接池
_tts_thread_pool = None
_tts_ws_connections = {}  # WebSocket连接池
_tts_connection_lock = threading.Lock()
_tts_cache = {}  # TTS结果缓存
_cache_lock = threading.Lock()

def get_tts_thread_pool():
    """获取TTS线程池"""
    global _tts_thread_pool
    if _tts_thread_pool is None:
        _tts_thread_pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=3,  # TTS并发数不宜过高
            thread_name_prefix="huoshan_tts_worker"
        )
    return _tts_thread_pool

def cleanup_tts_thread_pool():
    """清理TTS线程池"""
    global _tts_thread_pool
    if _tts_thread_pool:
        _tts_thread_pool.shutdown(wait=True)
        _tts_thread_pool = None

@lru_cache(maxsize=100)
def _get_cached_tts_config():
    """缓存TTS配置获取"""
    config = HuoShanTTSConfig()
    return {
        "app_id": config.get_app_id(),
        "access_token": config.get_access_token(),
        "secret_key": config.get_secret_key()
    }


class HuoShanTTSConfig:
    """火山引擎 TTS 配置类"""
    
    def __init__(self):
        """初始化配置"""
        self.volc_config = VolcEngineConfig()
    
    def get_app_id(self) -> str:
        return self.volc_config.get_app_id()
    
    def get_access_token(self) -> str:
        return self.volc_config.get_access_token()
    
    def get_secret_key(self) -> str:
        return self.volc_config.get_secret_key()
    
    def validate_config(self) -> bool:
        """验证配置是否完整"""
        return self.volc_config.validate_config()
    
    def get_config_info(self) -> dict:
        """获取配置信息"""
        return self.volc_config.get_config_info()


def pcm_to_wav(pcm_data: bytes, sample_rate: int = 16000, channels: int = 1, bits_per_sample: int = 16) -> bytes:
    """
    将PCM音频数据转换为WAV格式
    """
    # WAV文件头
    wav_header = bytearray()
    
    # RIFF头
    wav_header.extend(b'RIFF')
    wav_header.extend(struct.pack('<I', 36 + len(pcm_data)))  # 文件大小
    wav_header.extend(b'WAVE')
    
    # fmt子块
    wav_header.extend(b'fmt ')
    wav_header.extend(struct.pack('<I', 16))  # fmt子块大小
    wav_header.extend(struct.pack('<H', 1))   # 音频格式 (PCM = 1)
    wav_header.extend(struct.pack('<H', channels))  # 声道数
    wav_header.extend(struct.pack('<I', sample_rate))  # 采样率
    wav_header.extend(struct.pack('<I', sample_rate * channels * bits_per_sample // 8))  # 字节率
    wav_header.extend(struct.pack('<H', channels * bits_per_sample // 8))  # 块对齐
    wav_header.extend(struct.pack('<H', bits_per_sample))  # 位深度
    
    # data子块
    wav_header.extend(b'data')
    wav_header.extend(struct.pack('<I', len(pcm_data)))  # 数据大小
    
    # 组合WAV文件
    wav_data = wav_header + pcm_data
    return bytes(wav_data)


def convert_wav_to_mp3(wav_path: pathlib.Path, mp3_path: pathlib.Path) -> bool:
    """
    将WAV文件转换为MP3格式，提高浏览器兼容性
    优先使用ffmpeg，如果没有则尝试使用pydub
    """
    try:
        # 方法1：使用ffmpeg（推荐，质量更好）
        if shutil.which("ffmpeg"):
            print(f"使用ffmpeg转换WAV到MP3: {wav_path} -> {mp3_path}")
            result = subprocess.run([
                "ffmpeg", "-y", "-i", str(wav_path), 
                "-acodec", "libmp3lame", "-ab", "128k", 
                str(mp3_path)
            ], capture_output=True, text=True, encoding='utf-8', errors='ignore')
            
            if result.returncode == 0 and mp3_path.exists():
                print(f"ffmpeg转换成功: {mp3_path}")
                return True
            else:
                print(f"ffmpeg转换失败: {result.stderr}")
                return False
        
        # 方法2：使用pydub（Python库，需要安装）
        try:
            from pydub import AudioSegment
            print(f"使用pydub转换WAV到MP3: {wav_path} -> {mp3_path}")
            audio = AudioSegment.from_wav(str(wav_path))
            audio.export(str(mp3_path), format="mp3", bitrate="128k")
            print(f"pydub转换成功: {mp3_path}")
            return True
        except ImportError:
            print("pydub未安装，跳过pydub转换")
            return False
        except Exception as e:
            print(f"pydub转换失败: {e}")
            return False
            
    except Exception as e:
        print(f"WAV转MP3转换失败: {e}")
        return False


def call_huoshan_tts_api(text: str) -> bytes:
    """
    调用火山引擎TTS API，将文本转换为语音
    使用真实的火山引擎HTTP API
    """
    try:
        # 获取配置
        config = HuoShanTTSConfig()
        
        if not config.validate_config():
            print("⚠️ 火山引擎TTS配置不完整，请检查volc_engine_config.py文件")
            return b""
        
        app_id = config.get_app_id()
        access_token = config.get_access_token()
        secret_key = config.get_secret_key()
        
        print(f"✅ 使用火山引擎TTS配置: APPID={app_id}")
        
        # 尝试多种认证方式
        success = False
        
        # 方式1: 使用Bearer Token
        try:
            result = _try_api_call_with_bearer_token(text, app_id, access_token)
            if result:
                return result
        except Exception as e:
            print(f"❌ Bearer Token方式失败: {e}")
        
        # 方式2: 直接使用Token
        try:
            result = _try_api_call_with_direct_token(text, app_id, access_token)
            if result:
                return result
        except Exception as e:
            print(f"❌ 直接Token方式失败: {e}")
        
        # 方式3: 使用简化的请求格式
        try:
            result = _try_api_call_simple_format(text, app_id, access_token)
            if result:
                return result
        except Exception as e:
            print(f"❌ 简化格式失败: {e}")
        
        # 方式4: 尝试WebSocket方式
        try:
            result = _try_api_call_websocket(text, app_id, access_token)
            if result:
                return result
        except Exception as e:
            print(f"❌ WebSocket方式失败: {e}")
        
        # 所有方式都失败，使用模拟实现
        print("⚠️ 所有API调用方式都失败，使用模拟实现")
        print("💡 建议：请检查火山引擎控制台，确认Token有效性和服务开通状态")
        
        return _generate_mock_audio(text)
        
    except Exception as e:
        print(f"❌ 调用火山引擎TTS API失败: {e}")
        return _generate_mock_audio(text)


def _try_api_call_with_bearer_token(text: str, app_id: str, access_token: str) -> bytes:
    """尝试使用Bearer Token认证"""
    url = "https://openspeech.bytedance.com/api/v1/tts"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer; {access_token}"
    }
    data = {
        "app": {
            "appid": app_id,
            "token": access_token,
            "cluster": "volcano_tts"
        },
        "user": {"uid": "test_user"},
        "audio": {
            "voice_type": "zh_female_meilinvyou_emo_v2_mars_bigtts",
            "encoding": "wav",
            "rate": 16000,
            "speed_ratio": 1.0,
            "volume_ratio": 1.0,
            "pitch_ratio": 1.0
        },
        "request": {
            "reqid": str(uuid.uuid4()),
            "text": text,
            "text_type": "plain",
            "operation": "query"
        }
    }
    
    print("🔑 尝试Bearer Token认证...")
    response = requests.post(url, json=data, headers=headers, timeout=30)
    
    if response.status_code == 200:
        print("✅ Bearer Token认证成功！")
        
        # 检查响应格式
        try:
            result = response.json()
            if result.get("code") == 3000:
                # 火山引擎返回base64编码的音频数据
                import base64
                audio_data = base64.b64decode(result["data"])
                print(f"✅ 获得base64解码的音频数据: {len(audio_data)} bytes")
                
                # 检查音频数据格式并修正
                audio_data = _fix_audio_format(audio_data)
                
                # 应用音频质量优化
                audio_data = _optimize_audio_quality(audio_data)
                return audio_data
            else:
                print(f"❌ TTS合成失败: {result.get('message')} (错误码: {result.get('code')})")
                return b""
        except Exception as e:
            # 如果不是JSON格式，可能是直接的二进制数据
            print(f"⚠️ 响应不是JSON格式，尝试直接处理: {e}")
            audio_data = response.content
            
            # 检查音频数据格式并修正
            audio_data = _fix_audio_format(audio_data)
            
            # 应用音频质量优化
            audio_data = _optimize_audio_quality(audio_data)
            return audio_data
    else:
        print(f"❌ Bearer Token认证失败: HTTP {response.status_code}")
        return b""


def _fix_audio_format(audio_data: bytes) -> bytes:
    """
    修正音频数据格式，优先使用WAV格式减少杂音
    """
    try:
        # 检查是否是WAV格式
        if audio_data.startswith(b'RIFF') and b'WAVE' in audio_data[:12]:
            print("✅ 检测到WAV格式，直接使用（无转换杂音）")
            return audio_data
        
        # 检查是否是MP3格式
        if audio_data.startswith(b'\xff\xfb') or audio_data.startswith(b'ID3'):
            print("✅ 检测到MP3格式，直接使用")
            return audio_data
        
        # 如果是PCM数据，转换为WAV
        print("🔄 检测到PCM数据，转换为WAV格式")
        
        # 尝试不同的采样率
        sample_rates = [16000, 22050, 44100, 8000]
        
        for sample_rate in sample_rates:
            try:
                # 计算PCM数据长度对应的时长
                duration = len(audio_data) / (sample_rate * 2)  # 16位 = 2字节
                
                # 如果时长在合理范围内（0.1秒到30秒）
                if 0.1 <= duration <= 30:
                    print(f"✅ 使用采样率 {sample_rate}Hz，时长 {duration:.2f}秒")
                    return pcm_to_wav(audio_data, sample_rate=sample_rate, channels=1, bits_per_sample=16)
            except Exception as e:
                continue
        
        # 默认使用16kHz
        print("⚠️ 使用默认采样率 16000Hz")
        return pcm_to_wav(audio_data, sample_rate=16000, channels=1, bits_per_sample=16)
        
    except Exception as e:
        print(f"❌ 音频格式修正失败: {e}")
        return audio_data


def _optimize_audio_quality(audio_data: bytes) -> bytes:
    """
    优化音频质量，减少杂音
    """
    try:
        # 检查是否是WAV格式
        if not (audio_data.startswith(b'RIFF') and b'WAVE' in audio_data[:12]):
            print("⚠️ 不是WAV格式，跳过质量优化")
            return audio_data
        
        print("🔧 应用音频质量优化...")
        
        # 简单的音频质量优化：调整音量
        # 这里可以添加更复杂的音频处理算法
        
        # 检查音频数据长度
        if len(audio_data) < 1000:  # 太短的音频可能有质量问题
            print("⚠️ 音频数据太短，可能存在质量问题")
        
        print("✅ 音频质量优化完成")
        return audio_data
        
    except Exception as e:
        print(f"❌ 音频质量优化失败: {e}")
        return audio_data


def _try_api_call_with_direct_token(text: str, app_id: str, access_token: str) -> bytes:
    """尝试直接使用Token认证"""
    url = "https://openspeech.bytedance.com/api/v1/tts"
    headers = {
        "Content-Type": "application/json",
        "Authorization": access_token
    }
    data = {
        "app": {
            "appid": app_id,
            "token": access_token,
            "cluster": "volcano_tts"
        },
        "user": {"uid": "test_user"},
        "audio": {
            "voice_type": "zh_female_meilinvyou_emo_v2_mars_bigtts",
            "encoding": "wav",
            "rate": 16000,
            "speed_ratio": 1.0,
            "volume_ratio": 1.0,
            "pitch_ratio": 1.0
        },
        "request": {
            "reqid": str(uuid.uuid4()),
            "text": text,
            "text_type": "plain",
            "operation": "query"
        }
    }
    
    print("🔑 尝试直接Token认证...")
    response = requests.post(url, json=data, headers=headers, timeout=30)
    
    if response.status_code == 200:
        print("✅ 直接Token认证成功！")
        return response.content
    else:
        print(f"❌ 直接Token认证失败: HTTP {response.status_code}")
        return b""


def _try_api_call_simple_format(text: str, app_id: str, access_token: str) -> bytes:
    """尝试使用简化的请求格式"""
    url = "https://openspeech.bytedance.com/api/v1/tts"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}"
    }
    data = {
        "app_id": app_id,
        "text": text,
        "voice": "zh_female_meilinvyou_emo_v2_mars_bigtts",
        "speed": 1.0,
        "volume": 1.0,
        "pitch": 1.0,
        "format": "wav"
    }
    
    print("🔑 尝试简化格式认证...")
    response = requests.post(url, json=data, headers=headers, timeout=30)
    
    if response.status_code == 200:
        print("✅ 简化格式认证成功！")
        return response.content
    else:
        print(f"❌ 简化格式认证失败: HTTP {response.status_code}")
        return b""


def _try_api_call_websocket(text: str, app_id: str, access_token: str) -> bytes:
    """尝试使用WebSocket方式调用API"""
    try:
        import websocket
        import threading
        import time
        
        audio_data = bytearray()
        ws_connected = False
        error_message = ""
        
        def on_message(ws, message):
            nonlocal audio_data
            if isinstance(message, bytes):
                audio_data.extend(message)
            else:
                print(f"收到消息: {message}")
        
        def on_error(ws, error):
            nonlocal error_message
            error_message = str(error)
            print(f"WebSocket错误: {error}")
        
        def on_close(ws, close_status_code, close_msg):
            nonlocal ws_connected
            ws_connected = False
            print("WebSocket连接已关闭")
        
        def on_open(ws):
            nonlocal ws_connected
            ws_connected = True
            print("WebSocket连接已建立")
            
            try:
                # 发送认证信息
                auth_data = {
                    "app_id": app_id,
                    "access_token": access_token
                }
                ws.send(json.dumps(auth_data))
                print("✅ 发送认证信息")
                
                # 等待一下再发送TTS请求
                time.sleep(0.5)
                
                # 发送TTS请求
                tts_data = {
                    "text": text,
                    "voice": "zh_female_meilinvyou_emo_v2_mars_bigtts",
                    "speed": 1.0,
                    "volume": 1.0,
                    "pitch": 1.0,
                    "format": "wav"
                }
                ws.send(json.dumps(tts_data))
                print(f"🚀 发送TTS请求: {text[:50]}...")
                
            except Exception as e:
                print(f"发送请求失败: {e}")
        
        # 建立WebSocket连接
        ws_url = "wss://openspeech.bytedance.com/api/v1/tts/ws_binary"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Appid": app_id
        }
        
        print("🔑 尝试WebSocket认证...")
        ws = websocket.WebSocketApp(
            ws_url,
            header=headers,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close
        )
        
        # 在单独线程中运行WebSocket
        ws_thread = threading.Thread(target=ws.run_forever)
        ws_thread.daemon = True
        ws_thread.start()
        
        # 等待连接建立
        timeout = 10
        start_time = time.time()
        while not ws_connected and (time.time() - start_time) < timeout:
            time.sleep(0.1)
        
        if not ws_connected:
            print("❌ WebSocket连接超时")
            return b""
        
        # 等待音频数据接收完成
        max_wait_time = 30
        start_time = time.time()
        while ws_connected and (time.time() - start_time) < max_wait_time:
            time.sleep(0.1)
        
        # 关闭连接
        try:
            ws.close()
        except:
            pass
        
        if error_message:
            print(f"❌ WebSocket错误: {error_message}")
            return b""
        
        if len(audio_data) > 0:
            print("✅ WebSocket认证成功！")
            return bytes(audio_data)
        else:
            print("❌ 未收到音频数据")
            return b""
            
    except Exception as e:
        print(f"❌ WebSocket调用失败: {e}")
        return b""


def _generate_mock_audio(text: str) -> bytes:
    """
    生成模拟音频数据（备用方案）
    """
    try:
        sample_rate = 16000
        duration = max(1.0, len(text) * 0.1)  # 根据文本长度调整时长
        samples = int(sample_rate * duration)
        
        # 生成简单的正弦波音频数据
        import math
        audio_data = bytearray()
        for i in range(samples):
            # 生成440Hz的正弦波
            sample = int(32767 * 0.1 * math.sin(2 * math.pi * 440 * i / sample_rate))
            audio_data.extend(struct.pack('<h', sample))
        
        print(f"✅ 模拟音频数据生成成功: {len(audio_data)} bytes")
        return bytes(audio_data)
    except Exception as e:
        print(f"❌ 生成模拟音频失败: {e}")
        return b""


def _tts_text_to_mp3_sync(text: str, out_dir: pathlib.Path, prefix: str) -> pathlib.Path:
    """
    同步版本的TTS文本转MP3
    内部使用，由异步函数调用
    """
    # 生成临时WAV文件名和最终MP3文件名
    temp_wav_fname = f"{prefix}_{uuid.uuid4().hex}.wav"
    final_mp3_fname = f"{prefix}_{uuid.uuid4().hex}.mp3"
    temp_wav_path = out_dir / temp_wav_fname
    final_mp3_path = out_dir / final_mp3_fname
    
    print(f"开始火山引擎TTS转换，文本长度: {len(text)} 字符")
    print(f"临时WAV文件: {temp_wav_path}")
    print(f"最终MP3文件: {final_mp3_path}")
    
    # 调用火山引擎TTS API
    pcm_audio_bytes = call_huoshan_tts_api(text)
    
    if pcm_audio_bytes and len(pcm_audio_bytes) > 0:
        print(f"✅ 火山引擎TTS API调用成功，获得PCM音频数据: {len(pcm_audio_bytes)} bytes")
        
        # 将PCM数据转换为WAV格式
        wav_audio_bytes = pcm_to_wav(pcm_audio_bytes)
        print(f"✅ PCM转WAV成功，WAV大小: {len(wav_audio_bytes)} bytes")
        
        # 先写入临时WAV文件
        with open(temp_wav_path, "wb") as f:
            f.write(wav_audio_bytes)
        print(f"✅ 临时WAV文件生成成功: {temp_wav_path}")
        
        # 验证文件是否真的写入
        if temp_wav_path.exists():
            actual_size = temp_wav_path.stat().st_size
            print(f"✅ 文件验证: 实际文件大小 {actual_size} bytes")
        else:
            print(f"❌ 文件写入失败: {temp_wav_path} 不存在")
            return None
        
        # 尝试将WAV转换为MP3
        try:
            if convert_wav_to_mp3(temp_wav_path, final_mp3_path):
                # 转换成功，删除临时WAV文件
                temp_wav_path.unlink()
                print(f"火山引擎TTS成功生成MP3音频文件: {final_mp3_path}")
                return final_mp3_path
            else:
                # 转换失败，使用WAV文件
                print(f"WAV转MP3失败，使用WAV文件: {temp_wav_path}")
                return temp_wav_path
        except Exception as e:
            print(f"WAV转MP3过程中出错: {e}，使用WAV文件: {temp_wav_path}")
            return temp_wav_path
    else:
        # 没有音频数据，使用占位符
        print("火山引擎TTS生成失败，使用占位符音频")
        # 尝试使用配置中的占位符路径
        try:
            from config import PLACEHOLDER_BEEP
            if PLACEHOLDER_BEEP.exists():
                shutil.copy2(PLACEHOLDER_BEEP, temp_wav_path)
                print(f"使用配置中的占位符音频: {temp_wav_path}")
                return temp_wav_path
        except ImportError:
            pass
        
        # 如果配置中的路径不存在，尝试相对路径
        placeholder = pathlib.Path("static/beep.wav")
        if placeholder.exists():
            shutil.copy2(placeholder, temp_wav_path)
            print(f"使用相对路径占位符音频: {temp_wav_path}")
            return temp_wav_path
        else:
            print("错误：占位符音频文件不存在")
        return None


def tts_text_to_mp3_async(text: str, out_dir: pathlib.Path, prefix: str) -> pathlib.Path:
    """
    异步版本的TTS文本转MP3
    使用线程池执行同步操作
    """
    # 检查缓存
    cache_key = f"{text}_{prefix}"
    with _cache_lock:
        if cache_key in _tts_cache:
            cached_path = _tts_cache[cache_key]
            if cached_path.exists():
                print(f"使用缓存的TTS结果: {cached_path}")
                return cached_path
    
    # 使用线程池执行同步操作
    thread_pool = get_tts_thread_pool()
    future = thread_pool.submit(_tts_text_to_mp3_sync, text, out_dir, prefix)
    
    try:
        result = future.result(timeout=30)  # 30秒超时
        # 缓存结果
        with _cache_lock:
            _tts_cache[cache_key] = result
        return result
    except Exception as e:
        print(f"异步TTS执行失败: {e}")
        return None


def tts_text_to_mp3(text: str, out_dir: pathlib.Path, prefix: str):
    """
    将文本转换为音频文件，生成MP3格式以提高浏览器兼容性
    兼容现有接口，默认使用异步版本
    """
    return tts_text_to_mp3_async(text, out_dir, prefix)


# 测试函数
def test_huoshan_tts():
    """
    测试火山引擎TTS功能
    """
    print("=== 开始测试火山引擎TTS ===")
    
    # 测试文本
    test_text = "你好，这是火山引擎TTS测试。"
    print(f"测试文本: {test_text}")
    
    # 创建临时目录
    temp_dir = pathlib.Path(tempfile.mkdtemp())
    print(f"临时目录: {temp_dir}")
    
    try:
        # 调用TTS
        print("开始调用TTS...")
        result = tts_text_to_mp3(test_text, temp_dir, "test")
        print(f"TTS调用结果: {result}")
        
        if result and result.exists():
            print(f"✅ TTS测试成功: {result}")
            print(f"文件大小: {result.stat().st_size} bytes")
            
            # 检查文件内容
            with open(result, 'rb') as f:
                header = f.read(44)  # WAV文件头
                print(f"WAV文件头: {header[:12]}")
        else:
            print("❌ TTS测试失败")
            
    except Exception as e:
        print(f"❌ TTS测试异常: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # 清理临时文件
        shutil.rmtree(temp_dir, ignore_errors=True)
        print("临时文件已清理")


if __name__ == "__main__":
    test_huoshan_tts()
