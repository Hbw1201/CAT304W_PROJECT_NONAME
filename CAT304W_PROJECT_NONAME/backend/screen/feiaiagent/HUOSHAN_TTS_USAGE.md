# 火山引擎TTS使用说明

## 概述

本项目已成功从讯飞TTS切换到火山引擎TTS，所有功能保持兼容。

## 文件结构

```
feiaiagent/
├── volc_engine_config.py    # 火山引擎配置类
├── huoshan_tts.py          # 火山引擎TTS核心模块
├── test_tts_playback.py    # TTS播放测试脚本
└── static/tts/             # TTS音频文件输出目录
```

## 配置说明

### 火山引擎配置 (`volc_engine_config.py`)

```python
class VolcEngineConfig:
    APP_ID = "5788367806"
    ACCESS_TOKEN = "ERznO5yjhKwOhWBhtcPEajDQtrjna33L"
    SECRET_KEY = "7BykayuS8YK7PA9d8L_YhYPZZ4lN3mmC"
```

**重要提醒**：
- 请妥善保管这些敏感信息
- 不要提交到版本控制系统
- 建议使用环境变量或加密存储

## 使用方法

### 1. 基础TTS调用

```python
from huoshan_tts import tts_text_to_mp3
import pathlib

# 生成TTS音频
audio_file = tts_text_to_mp3(
    text="你好，这是测试文本",
    out_dir=pathlib.Path("static/tts"),
    prefix="test"
)

print(f"生成的音频文件: {audio_file}")
```

### 2. 异步TTS调用

```python
from huoshan_tts import tts_text_to_mp3_async
import pathlib

# 异步生成TTS音频
audio_file = tts_text_to_mp3_async(
    text="你好，这是异步测试",
    out_dir=pathlib.Path("static/tts"),
    prefix="async_test"
)

print(f"异步生成的音频文件: {audio_file}")
```

### 3. 配置验证

```python
from huoshan_tts import HuoShanTTSConfig

config = HuoShanTTSConfig()

# 验证配置
if config.validate_config():
    print("✅ 配置验证通过")
    config_info = config.get_config_info()
    print(f"APP ID: {config_info['app_id']}")
else:
    print("❌ 配置验证失败")
```

## Flask应用集成

### 1. TTS音频生成

```python
from app import generate_tts_audio

# 生成TTS音频并返回URL
audio_url = generate_tts_audio("测试文本", "session_123")
print(f"音频URL: {audio_url}")
```

### 2. TTS音频服务

Flask应用提供TTS音频文件服务端点：

```
GET /static/tts/<filename>
```

### 3. 前端播放

```javascript
// 设置音频源
audioEl.src = '/static/tts/session_123_abc123.mp3';

// 播放音频
audioEl.play().then(() => {
    console.log('音频播放开始');
}).catch(e => {
    console.error('音频播放失败:', e);
});
```

## 测试

### 运行TTS测试

```bash
python test_tts_playback.py
```

### 运行基础测试

```bash
python huoshan_tts.py
```

### 测试配置

```bash
python volc_engine_config.py
```

## 功能特性

- ✅ **完全兼容** - 保持原有接口不变
- ✅ **异步支持** - 支持异步TTS请求
- ✅ **线程池管理** - 支持并发TTS请求
- ✅ **缓存机制** - 避免重复生成相同文本
- ✅ **错误处理** - 完善的错误处理和回退机制
- ✅ **格式转换** - 支持PCM→WAV→MP3转换
- ✅ **配置验证** - 自动验证配置完整性

## 当前状态

### ✅ 已完成
- 火山引擎配置类创建
- TTS模块实现和集成
- Flask应用集成
- 前端播放逻辑
- 测试脚本和验证

### ⚠️ 待完善
- **真实API调用** - 当前使用模拟实现，需要根据火山引擎官方文档完善
- **音频质量优化** - 使用真实API后可获得高质量语音
- **错误处理增强** - 根据实际API响应完善错误处理

## 故障排除

### 1. 配置问题

**问题**：配置验证失败
**解决**：检查 `volc_engine_config.py` 中的配置信息

### 2. 音频生成失败

**问题**：TTS音频生成失败
**解决**：
- 检查ffmpeg是否正确安装
- 检查输出目录权限
- 查看错误日志

### 3. 音频播放问题

**问题**：前端无法播放音频
**解决**：
- 检查音频文件是否存在
- 检查Flask服务端点
- 检查浏览器音频权限

### 4. 依赖问题

**问题**：模块导入失败
**解决**：
```bash
pip install flask flask-cors websocket-client requests
```

## 下一步

1. **完善真实API** - 根据火山引擎官方文档实现真实的TTS API调用
2. **性能优化** - 优化音频生成和播放性能
3. **功能扩展** - 添加更多语音选项和参数配置
4. **监控日志** - 添加详细的日志记录和监控

## 联系支持

如有问题，请检查：
1. 配置是否正确
2. 依赖是否安装
3. 日志错误信息
4. 网络连接状态


