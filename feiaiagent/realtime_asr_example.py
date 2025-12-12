#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
火山ASR实时流式识别示例
演示如何使用实时识别功能，只保留最后结果
"""

import asyncio
import logging
from huoshan_asr import start_realtime_asr

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def demo_realtime_asr():
    """演示实时ASR功能"""
    print("=== 火山ASR实时流式识别演示 ===")
    print("只保留最后的识别结果")
    print()
    
    # 创建音频队列
    audio_queue = asyncio.Queue()
    
    # 存储最新的识别结果
    latest_result = ""
    
    def result_callback(text):
        """识别结果回调函数"""
        nonlocal latest_result
        latest_result = text
        print(f"最新识别结果: {text}")
    
    # 启动实时ASR任务
    asr_task = asyncio.create_task(
        start_realtime_asr(audio_queue, result_callback)
    )
    
    try:
        # 模拟音频数据输入
        print("开始模拟音频数据输入...")
        
        # 创建一些模拟的音频数据
        sample_rate = 16000
        chunk_size = 1024
        
        for i in range(20):  # 发送20个音频块
            # 创建模拟音频数据（简单的正弦波）
            import math
            samples = []
            for j in range(chunk_size):
                sample = int(32767 * 0.1 * math.sin(2 * math.pi * 440 * j / sample_rate))
                samples.append(sample)
            
            # 转换为字节
            audio_data = bytes()
            for sample in samples:
                audio_data += sample.to_bytes(2, byteorder='little', signed=True)
            
            # 放入队列
            await audio_queue.put(audio_data)
            print(f"发送音频块 {i+1}/20")
            
            # 等待一段时间
            await asyncio.sleep(0.1)
        
        # 等待ASR处理
        await asyncio.sleep(2)
        
        print(f"\n最终识别结果: '{latest_result}'")
        
    except Exception as e:
        logger.error(f"演示失败: {e}")
    finally:
        # 取消ASR任务
        asr_task.cancel()
        try:
            await asr_task
        except asyncio.CancelledError:
            pass

async def demo_with_manual_audio():
    """演示手动控制音频输入"""
    print("\n=== 手动音频输入演示 ===")
    
    audio_queue = asyncio.Queue()
    latest_result = ""
    
    def result_callback(text):
        nonlocal latest_result
        latest_result = text
        print(f"识别结果: {text}")
    
    # 启动ASR
    asr_task = asyncio.create_task(
        start_realtime_asr(audio_queue, result_callback)
    )
    
    try:
        print("请输入音频数据（按Ctrl+C停止）...")
        
        # 模拟用户输入音频数据
        for i in range(10):
            # 模拟音频数据
            audio_data = b'\x00\x01' * 512  # 简单的音频数据
            await audio_queue.put(audio_data)
            print(f"输入音频数据 {i+1}")
            await asyncio.sleep(0.5)
        
        print(f"\n最终结果: {latest_result}")
        
    except KeyboardInterrupt:
        print("\n用户中断")
    finally:
        asr_task.cancel()
        try:
            await asr_task
        except asyncio.CancelledError:
            pass

def main():
    """主函数"""
    print("火山ASR实时流式识别测试")
    print("=" * 50)
    
    try:
        # 运行演示
        asyncio.run(demo_realtime_asr())
        asyncio.run(demo_with_manual_audio())
        
    except Exception as e:
        logger.error(f"测试失败: {e}")
    
    print("\n测试完成")

if __name__ == "__main__":
    main()

