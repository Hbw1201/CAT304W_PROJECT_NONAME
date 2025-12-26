# 火山ASR实时流式识别使用说明

## 功能概述

火山ASR现在支持实时流式识别，**只保留最后的识别结果**，避免中间结果的干扰。

## 主要特性

### 1. 实时流式识别
- **实时性**: 从第一秒开始识别，1000ms音频块发送
- **只保留最后结果**: 每次只返回最新的完整识别文本
- **高准确性**: 结合累积音频包提高识别准确性

### 2. 智能结果处理
- **结果更新**: 每次收到新的识别结果时更新
- **避免重复**: 不会重复输出相同的识别结果
- **最终结果**: 只保留和返回最新的完整识别文本

## 使用方法

### 1. 基本用法

```python
import asyncio
from huoshan_asr import start_realtime_asr

async def main():
    # 创建音频队列
    audio_queue = asyncio.Queue()
    
    # 存储最新结果
    latest_result = ""
    
    def result_callback(text):
        """识别结果回调函数"""
        nonlocal latest_result
        latest_result = text
        print(f"最新识别结果: {text}")
    
    # 启动实时ASR
    await start_realtime_asr(audio_queue, result_callback)
```

### 2. 音频数据输入

```python
# 模拟音频数据输入
sample_rate = 16000
chunk_size = 1024

for i in range(10):
    # 创建音频数据
    audio_data = create_audio_data()  # 您的音频数据创建函数
    
    # 放入队列
    await audio_queue.put(audio_data)
    
    # 等待处理
    await asyncio.sleep(0.1)
```

### 3. 完整示例

```python
import asyncio
import logging
from huoshan_asr import start_realtime_asr

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def realtime_recognition():
    """实时识别示例"""
    audio_queue = asyncio.Queue()
    latest_result = ""
    
    def result_callback(text):
        nonlocal latest_result
        latest_result = text
        print(f"识别结果: {text}")
    
    # 启动ASR任务
    asr_task = asyncio.create_task(
        start_realtime_asr(audio_queue, result_callback)
    )
    
    try:
        # 模拟音频输入
        for i in range(20):
            audio_data = create_test_audio()  # 创建测试音频
            await audio_queue.put(audio_data)
            await asyncio.sleep(0.1)
        
        # 等待处理完成
        await asyncio.sleep(2)
        
        print(f"最终结果: {latest_result}")
        
    finally:
        asr_task.cancel()
        try:
            await asr_task
        except asyncio.CancelledError:
            pass

# 运行示例
asyncio.run(realtime_recognition())
```

## API参考

### `start_realtime_asr(audio_queue, result_callback)`

启动实时火山ASR识别。

**参数:**
- `audio_queue` (asyncio.Queue): 音频数据队列
- `result_callback` (callable): 识别结果回调函数

**回调函数:**
```python
def result_callback(text: str):
    """
    识别结果回调函数
    
    参数:
        text: 最新的识别文本
    """
    print(f"识别结果: {text}")
```

### `AsrWsClient.start_realtime_stream(audio_queue)`

启动实时音频流处理。

**参数:**
- `audio_queue` (asyncio.Queue): 音频数据队列

**返回:**
- `AsyncGenerator[str, None]`: 异步生成器，产生最新的识别文本

## 技术细节

### 1. 音频处理
- **采样率**: 16kHz
- **位深度**: 16bit
- **声道**: 单声道
- **格式**: PCM

### 2. 流式处理
- **分段大小**: 1000ms音频块
- **累积包**: 每3个包发送一次累积音频
- **压缩**: GZIP压缩传输

### 3. 结果处理
- **只保留最后结果**: 每次只返回最新的完整识别文本
- **避免重复**: 不会重复输出相同的识别结果
- **实时更新**: 每次收到新结果时立即更新

## 配置说明

### 1. 服务地址
```python
# 在 huoshan_asr.py 中配置
self.huosan_asr_url = "wss://您的火山ASR服务地址/api/v3/sauc/bigmodel"
```

### 2. 认证信息
```python
self.auth = {
    "app_key": "5788367806",
    "access_key": "ERznO5yjhKwOhWBhtcPEajDQtrjna33L"
}
```

## 测试方法

### 1. 运行测试示例
```bash
python realtime_asr_example.py
```

### 2. 测试内容
- 模拟音频数据输入
- 实时识别结果输出
- 最终结果验证

## 注意事项

### 1. 音频格式
- 确保音频数据格式正确
- 采样率必须是16kHz
- 位深度必须是16bit

### 2. 网络连接
- 确保网络连接稳定
- 检查防火墙设置
- 验证服务地址正确

### 3. 资源管理
- 及时取消异步任务
- 清理音频队列
- 释放网络连接

## 故障排除

### 1. 常见问题
- **连接失败**: 检查网络和服务地址
- **识别为空**: 检查音频质量和格式
- **延迟过高**: 调整分段大小

### 2. 调试方法
- 查看日志输出
- 检查音频数据格式
- 验证网络连接状态

## 总结

火山ASR实时流式识别功能提供了：

✅ **实时性**: 从第一秒开始识别
✅ **准确性**: 只保留最后的识别结果
✅ **易用性**: 简单的API接口
✅ **稳定性**: 完善的错误处理

通过这个功能，您可以实现真正的实时语音识别，只获取最终的识别结果，避免中间结果的干扰。

