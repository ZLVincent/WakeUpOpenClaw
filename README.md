# WakeUpOpenClaw

语音唤醒 AI 助手 — 基于 Snowboy / Porcupine 唤醒词检测 + FunASR 语音识别 + OpenClaw AI Agent + Edge TTS 语音合成。

## 功能概览

**核心语音交互**
- **语音唤醒**：支持 Snowboy 和 Picovoice Porcupine 两种引擎，通过配置文件切换
- **语音识别**：对接 FunASR Docker 服务，支持 offline / online / 2pass 模式
- **AI 对话**：调用 OpenClaw Agent CLI，支持单轮/多轮对话和自定义系统提示词
- **语音合成**：使用 Edge TTS（微软），支持代理和流式分句合成
- **语音打断**：TTS 播放期间可说唤醒词打断，直接进入新一轮对话
- **提示音**：唤醒时播放 `beep_hi.wav`，录音结束播放 `beep_lo.wav`

**本地技能**
- **关键词路由**：音量控制、停止播放、报时、新建对话等常用指令本地秒响应，不经过 AI
- **音量控制**：通过语音指令调大/调小系统音量

**Web 界面**
- **文本聊天**：浏览器聊天页面，与语音共享同一个 Agent 会话
- **实时状态**：WebSocket 推送助手状态（待唤醒/录音/思考/播放），页面顶部实时显示
- **配置管理**：在线编辑 config.yaml，枚举字段渲染为下拉框
- **OTA 更新**：检查 Git 更新、一键拉取并通过 Supervisor 重启

**数据持久化**
- **对话历史**：MySQL 存储所有语音和文本对话，支持查看历史、切换对话
- **自动归档**：对话轮次超过阈值自动归档旧对话，开启新对话
- **手动新建**：Web 按钮或语音指令"新对话"创建新会话

**全量可配**
- 所有参数通过 `config.yaml` 配置，支持 Web 页面在线修改

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
          [本地技能匹配？]
           ├─ 匹配 → 本地执行 + TTS 播报
           └─ 不匹配 ↓
          [OpenClaw Agent 处理]
               │
               ▼ AI 回复 → 保存到 MySQL
          [Edge TTS 语音合成 + 播放]
            (支持分句流式 / 唤醒词打断)
               │
               ▼
          [回到唤醒词监听]

    同时运行:
          [Web 服务 :8084]
            ├── /          聊天页面 (实时状态 + 音量控制)
            ├── /config    配置管理 + OTA 更新
            ├── /logs      日志查看 (搜索/过滤/着色)
            ├── /calendar  日程日历 (增删改查 + 7/14天视图)
            └── /ws/status WebSocket 状态推送
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
│   └── openclaw_client.py       # OpenClaw CLI 调用封装（流式读取）
├── tts/
│   └── edge_tts_engine.py       # Edge TTS 合成 + 播放 + 流式 + 打断
├── audio/
│   └── recorder.py              # PyAudio 麦克风录音
├── skills/
│   └── router.py                # 本地技能路由（关键词匹配 + 内置动作）
├── storage/
│   └── database.py              # MySQL 对话历史持久化
├── web/                         # Web 界面
│   ├── server.py                #   aiohttp 服务端（聊天/配置/OTA/WebSocket/日志/日程）
│   └── templates/
│       ├── chat.html            #   聊天页面（含实时状态面板 + 音量控制）
│       ├── config.html          #   配置管理 + 系统管理页面
│       ├── logs.html            #   日志查看页面
│       └── calendar.html        #   日程日历页面
├── mcp/                         # MCP Server（OpenClaw 工具集成）
│   └── calendar_server.py       #   日程操作 MCP Server
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
- Docker（运行 FunASR 服务和 MySQL）
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
git clone https://github.com/seasalt-ai/snowboy.git /tmp/snowboy
cd /tmp/snowboy/swig/Python3
make
cp _snowboydetect.so snowboydetect.py ~/WakeUpOpenClaw/snowboy/
```

如果使用 Porcupine 引擎，安装 `pip install pvporcupine` 并在 [Picovoice Console](https://console.picovoice.ai/) 获取 Access Key。

### 5. 部署 FunASR 服务

```bash
curl -O https://raw.githubusercontent.com/alibaba-damo-academy/FunASR/main/runtime/deploy_tools/funasr-runtime-deploy-offline-cpu-zh.sh
sudo bash funasr-runtime-deploy-offline-cpu-zh.sh install --workspace ./funasr-runtime-resources
```

### 6. 修改配置

编辑 `config.yaml`，根据实际环境调整：

```yaml
# 必须修改的配置
asr:
  server_url: "ws://localhost:10095"

tts:
  proxy: "http://127.0.0.1:7890"       # 不需要代理时设为 null

database:
  password: "your_mysql_password"

# 可选
wake_up:
  engine: "porcupine"                   # 切换唤醒词引擎
  porcupine:
    access_key: "YOUR_KEY"
```

### 7. 启动

```bash
python main.py
```

启动后：
- 对着麦克风说唤醒词（默认 "snowboy"），听到 `beep` 提示音后开始说话
- 浏览器访问 `http://<IP>:8084` 使用文本聊天和查看实时状态
- 浏览器访问 `http://<IP>:8084/config` 在线修改配置和管理系统

### 8. Supervisor 部署（可选）

```ini
[program:WakeUpOpenClaw]
command=/path/to/venv/bin/python main.py
directory=/path/to/WakeUpOpenClaw
autostart=true
autorestart=true
stderr_logfile=/var/log/wakeup-openclaw.err.log
stdout_logfile=/var/log/wakeup-openclaw.out.log
```

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

### 语音打断（Barge-in）

```yaml
conversation:
  barge_in: true     # TTS 播放期间说唤醒词可打断
```

### 流式 TTS

```yaml
conversation:
  streaming_tts: true  # 长回复分句合成，边合成边播放，降低首字延迟
```

### 免打扰时间

设定时间段内不接受语音唤醒（Web 聊天不受影响）：

```yaml
conversation:
  do_not_disturb:
    enabled: true
    start: "22:30"    # 晚上 10:30 开始
    end: "07:30"      # 早上 7:30 结束（支持跨午夜）
```

### VAD 端点检测调优

```yaml
conversation:
  vad_silence_timeout: 1.5    # 静默多久判定说完（秒）
  vad_energy_threshold: 500   # 能量阈值（安静 300~500，嘈杂 800~1500）
```

### TTS 代理

```yaml
tts:
  proxy: "http://127.0.0.1:7890"    # 或 socks5://...
```

### 本地技能

在 AI 之前匹配关键词，命中则本地执行。技能按业务分组，每个可独立开关：

```yaml
skills:
  enabled: true
  music:                                       # 本地音乐播放技能
    enabled: true
    options:
      volume_step: "10%"
    actions:
      play:
        keywords: ["播放歌曲", "播放本地歌曲", "播放"]
      volume_up:
        keywords: ["大声一点", "音量调大"]
        reply: "好的，已调大音量"
      next_track:
        keywords: ["下一首", "切歌"]
      stop:
        keywords: ["停止播放", "安静"]
  calendar:                                    # 日程管理技能
    enabled: true
    actions:
      query_today:
        keywords: ["今天有什么安排", "今天日程"]
      query_week:
        keywords: ["这周有什么安排", "本周日程"]
      query_upcoming:
        keywords: ["还有什么日程", "待完成日程"]
  conversation:                                # 对话管理技能
    enabled: true
    actions:
      new_conversation:
        keywords: ["新对话", "重新开始"]
  utility:                                     # 通用工具技能
    enabled: true
    actions:
      current_time:
        keywords: ["现在几点", "报时"]
```

### 本地音乐播放

从 MySQL `zlpi_music` 表查询歌曲，通过 mpv 播放：

```
"播放歌曲"         → 按顺序播放所有本地歌曲
"播放歌曲雨爱"     → 搜索歌名"雨爱"，单曲播放
"播放杨丞琳的歌"   → 搜索歌手"杨丞琳"，单曲播放
"播放收藏的歌"     → 播放 is_favorite=1 的歌曲
"下一首" / "上一首" → 切换曲目
"停止播放"         → 停止并清空播放列表
```

### 系统提示词

约束 AI 回复格式以适配语音播报：

```yaml
agent:
  system_prompt: >-
    你是一个语音助手...用纯文本回复，不要使用 Markdown...
```

### 对话历史

```yaml
database:
  host: "localhost"
  port: 3306
  user: "root"
  password: "your_password"
  database: "wakeup_openclaw"

conversation:
  max_history_rounds: 30    # 超过后自动归档旧对话
```

## MCP Server（OpenClaw 日程工具集成）

通过 MCP 协议让 OpenClaw Agent 直接操作日程，支持语音和微信。

### 注册（一次性）

```bash
# 安装依赖
pip install mcp httpx

# 注册 MCP Server 到 OpenClaw
openclaw mcp set calendar '{"command":"/path/to/venv/bin/python3","args":["/path/to/WakeUpOpenClaw/mcp/calendar_server.py"]}'

# 验证
openclaw mcp list
```

### 可用工具

| 工具 | 功能 | 示例指令 |
|------|------|----------|
| `create_event` | 创建日程 | "帮我添加明天下午2点的会议" |
| `update_event` | 修改日程 | "把会议改到3点" |
| `delete_event` | 删除日程 | "删除明天的会议" |
| `query_events` | 查询日期范围日程 | "下周有什么安排" |
| `query_today_events` | 查询今天日程 | "今天有什么事" |
| `query_tomorrow_events` | 查询明天日程 | "明天有什么安排" |
| `query_week_events` | 查询本周日程 | "这周日程" |

### 工作原理

```
用户 → OpenClaw Agent → MCP Server (stdio) → HTTP → localhost:8084/api/events → MySQL
```

## Web 界面

| 页面 | 地址 | 功能 |
|------|------|------|
| 聊天 | `http://<IP>:8084/` | 文本对话 + 对话历史 + 实时状态指示器 + 音量控制 |
| 配置 | `http://<IP>:8084/config` | 在线编辑 config + 检查更新 + 重启服务 |
| 日志 | `http://<IP>:8084/logs` | 查看日志文件，支持级别/模块过滤、关键词搜索、自动刷新 |
| 日程 | `http://<IP>:8084/calendar` | 日程日历，7/14天视图，日程增删改查，颜色分类 |
| 状态 | `http://<IP>:8084/status` | 系统状态 (CPU/内存/磁盘/温度/运行时间)、IP 地址、网络连通性 |

### 聊天页面

- 左侧对话列表，点击切换历史对话
- "新对话"按钮创建新会话
- 对话列表悬停显示删除按钮，确认后永久删除
- 顶部实时状态点：绿色(待唤醒) / 蓝色(录音) / 橙色(思考) / 红色(播放) / 灰色(免打扰)
- 底部音量控制滑块
- 消息标注来源（voice / web）

### 配置页面

- 按段折叠显示所有配置
- 枚举字段渲染为下拉框
- 底部系统管理：当前版本、检查更新、拉取更新并重启、仅重启

### 日志页面

- 读取 `logs/assistant.log` 文件最后 1000 行
- 按日志级别过滤（DEBUG / INFO / WARNING / ERROR / CRITICAL）
- 按模块过滤（main / wake_up / asr / agent / tts / web 等）
- 关键词搜索，实时高亮匹配文本
- 日志级别着色：DEBUG 青色、INFO 绿色、WARNING 黄色、ERROR 红色
- 切换轮转日志文件（assistant.log.1 等）
- 自动刷新（3 秒轮询，可开关）

### 日程页面

- 7天 / 14天视图切换，左右翻页，快速回到今天
- 每天一列，列头标注星期和日期，周末紫色特殊标识，今天红色高亮
- 点击空白区域新建日程，点击日程块编辑
- 日程弹窗：标题、日期、时间、全天、分类、颜色、提前提醒、备注
- 日程块悬停显示删除按钮
- 8 种预设颜色分类
- 提前提醒：默认 5 分钟，到时 TTS 语音播报（受免打扰限制）
- 可选微信提醒：通过 OpenClaw channel 发送（不受免打扰限制）
- 语音查询："今天有什么安排"、"明天有什么事"

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
