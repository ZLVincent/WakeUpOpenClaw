# WakeUpOpenClaw

语音唤醒 AI 助手 — 基于 Snowboy / Porcupine 唤醒词检测 + FunASR 语音识别 + OpenClaw AI Agent + Edge TTS 语音合成。

## 功能概览

- **语音唤醒**：支持 Snowboy 和 Picovoice Porcupine 两种引擎，通过配置文件切换
- **语音识别**：对接 FunASR Docker 服务，支持 offline / online / 2pass 模式
- **AI 对话**：调用 OpenClaw Agent CLI，支持多轮对话和自定义系统提示词
- **语音合成**：使用 Edge TTS（微软），中文效果好，支持代理
- **提示音**：唤醒时播放 `beep_hi.wav`，录音结束播放 `beep_lo.wav`
- **Web 界面**：提供浏览器聊天页面和配置管理页面，作为语音交互的备用方式
- **全量可配**：所有参数通过 `config.yaml` 配置，支持 Web 页面在线修改

## 系统架构

```
[麦克风] → [Snowboy/Porcupine 唤醒词检测]
               │
               ▼ 检测到唤醒词
          [播放 beep_hi.wav]
               │
               ▼
          [录音 + FunASR 语音识别]
               │
               ▼ 识别结果
          [播放 beep_lo.wav]
               │
               ▼
          [OpenClaw Agent 处理]
               │
               ▼ AI 回复
          [Edge TTS 语音合成 + 播放]
               │
               ▼
          [回到唤醒词监听]

    同时运行:
          [Web 服务 :8084]
            ├── /       聊天页面
            └── /config 配置管理
```

## 项目结构

```
WakeUpOpenClaw/
├── main.py                      # 主程序入口，状态机
├── config.yaml                  # 完整配置文件
├── requirements.txt             # Python 依赖
├── wake_up/                     # 唤醒词检测模块
│   ├── base.py                  #   抽象基类
│   ├── factory.py               #   工厂函数（根据配置选择引擎）
│   ├── snowboy_detector.py      #   Snowboy 实现
│   └── porcupine_detector.py    #   Porcupine 实现
├── asr/
│   └── funasr_client.py         # FunASR WebSocket 客户端
├── agent/
│   └── openclaw_client.py       # OpenClaw CLI 调用封装
├── tts/
│   └── edge_tts_engine.py       # Edge TTS 语音合成 + 播放
├── audio/
│   └── recorder.py              # PyAudio 麦克风录音
├── web/                         # Web 界面
│   ├── server.py                #   aiohttp 服务端
│   └── templates/
│       ├── chat.html            #   聊天页面
│       └── config.html          #   配置管理页面
├── utils/
│   └── logger.py                # 彩色日志（终端 + 文件轮转）
├── static/                      # 提示音文件
│   ├── beep_hi.wav
│   └── beep_lo.wav
├── snowboy/                     # Snowboy 运行时文件
│   ├── resources/common.res
│   └── models/snowboy.umdl
├── models/                      # Porcupine 唤醒词模型目录
└── logs/                        # 日志输出目录
```

## 环境要求

- 树莓派 5（或其他 Linux ARM64 / x86_64 设备）
- Python 3.9+
- 麦克风 + 扬声器
- Docker（运行 FunASR 服务）
- OpenClaw 已安装并配置

## 快速部署

### 1. 安装系统依赖

```bash
sudo apt update
sudo apt install portaudio19-dev python3-pyaudio mpv
# Snowboy 编译依赖
sudo apt install swig libatlas-base-dev sox
```

### 2. 克隆项目

```bash
git clone https://github.com/ZLVincent/WakeUpOpenClaw.git
cd WakeUpOpenClaw
```

### 3. 安装 Python 依赖

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 4. 编译 Snowboy（如果使用 Snowboy 引擎）

```bash
# 克隆 Snowboy 源码
git clone https://github.com/seasalt-ai/snowboy.git /tmp/snowboy
cd /tmp/snowboy/swig/Python3
make

# 将编译产物复制到项目目录
cp _snowboydetect.so snowboydetect.py ~/WakeUpOpenClaw/snowboy/
```

如果使用 Porcupine 引擎，安装 `pip install pvporcupine` 并在 [Picovoice Console](https://console.picovoice.ai/) 获取 Access Key。

### 5. 部署 FunASR 服务

```bash
# 离线识别服务（端口 10095）
curl -O https://raw.githubusercontent.com/alibaba-damo-academy/FunASR/main/runtime/deploy_tools/funasr-runtime-deploy-offline-cpu-zh.sh
sudo bash funasr-runtime-deploy-offline-cpu-zh.sh install --workspace ./funasr-runtime-resources
```

### 6. 修改配置

编辑 `config.yaml`，根据实际环境调整：

```yaml
# 必须修改的配置
asr:
  server_url: "ws://localhost:10095"    # FunASR 地址

tts:
  proxy: "http://127.0.0.1:7890"       # 代理地址（不需要时设为 null）

# 可选：如果使用 Porcupine
wake_up:
  engine: "porcupine"                   # 切换为 porcupine
  porcupine:
    access_key: "YOUR_KEY"
```

### 7. 启动

```bash
python main.py
```

启动后：
- 对着麦克风说唤醒词（默认 "snowboy"），听到 `beep` 提示音后开始说话
- 浏览器访问 `http://<IP>:8084` 使用文本聊天
- 浏览器访问 `http://<IP>:8084/config` 在线修改配置

## 配置说明

所有配置集中在 `config.yaml` 中，也可通过 Web 配置页面修改。

### 唤醒词引擎切换

```yaml
wake_up:
  engine: "snowboy"      # 或 "porcupine"
```

两个引擎的依赖相互独立，用 Snowboy 不需要安装 Porcupine，反之亦然。

### 对话模式

```yaml
conversation:
  mode: "single"   # 唤醒 → 说话 → AI回复 → 回到待唤醒
  # mode: "multi"  # 唤醒 → 说话 → AI回复 → 等待继续 → ... → 超时回到待唤醒
```

### VAD 端点检测调优

```yaml
conversation:
  vad_silence_timeout: 1.5    # 静默多久判定说完（秒）
  vad_energy_threshold: 500   # 能量阈值（安静 300~500，嘈杂 800~1500）
```

### TTS 代理

Edge TTS 需要访问 `speech.platform.bing.com`，如需代理：

```yaml
tts:
  proxy: "http://127.0.0.1:7890"    # 或 socks5://...
```

### 系统提示词

约束 AI 回复格式以适配语音播报：

```yaml
agent:
  system_prompt: >-
    你是一个语音助手...用纯文本回复，不要使用 Markdown...
```

## Web 界面

| 页面 | 地址 | 功能 |
|------|------|------|
| 聊天 | `http://<IP>:8084/` | 文本对话，与语音共享同一个 Agent 会话 |
| 配置 | `http://<IP>:8084/config` | 在线编辑 config.yaml，保存后部分配置需重启生效 |

## 日志

- 终端输出彩色日志，不同模块不同颜色
- 文件日志写入 `logs/assistant.log`，自动轮转（10MB x 5 个文件）
- 通过 `config.yaml` 中 `logging.level` 控制日志级别

```
2026-04-13 21:27:53 [INFO    ] [wake_up   ] 检测到唤醒词!
2026-04-13 21:27:53 [INFO    ] [main      ] 开始录音，请说话...
2026-04-13 21:27:57 [INFO    ] [asr       ] FunASR 离线结果: 上海明天天气怎么样
2026-04-13 21:28:07 [INFO    ] [agent     ] OpenClaw 回复 (9.81s): 明天上海有阵雨...
2026-04-13 21:28:09 [INFO    ] [tts       ] 语音合成完成 (1.52s, 45.2 KB)
2026-04-13 21:28:15 [INFO    ] [tts       ] 语音播放完成
2026-04-13 21:28:15 [INFO    ] [wake_up   ] 开始监听唤醒词...
```

## 许可证

MIT
