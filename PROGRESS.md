# WakeUpOpenClaw 项目进度记录

> 最后更新: 2026-04-14

---

## 项目概述

基于树莓派 5 的语音唤醒 AI 助手，通过唤醒词激活后进行语音对话，同时提供 Web 界面作为备用交互方式。

**技术栈**: Python 3.9+ / Snowboy (Porcupine) / FunASR / OpenClaw / Edge TTS / MySQL / aiohttp

---

## 已完成功能

### 第一阶段：核心语音交互（已完成）

| 功能 | 状态 | 说明 |
|------|------|------|
| 语音唤醒 — Snowboy | done | seasalt-ai fork，需从源码编译 |
| 语音唤醒 — Porcupine | done | pvporcupine SDK，需 Access Key |
| 唤醒词引擎可切换 | done | config.yaml `wake_up.engine` 切换，工厂模式，延迟导入 |
| FunASR 语音识别 | done | WebSocket 客户端，支持 offline/online/2pass，SSL 可选 |
| OpenClaw Agent 调用 | done | CLI 方式，流式读取 stdout（不等进程退出），JSON 解析兼容 Gateway/Embedded 两种格式 |
| Edge TTS 语音合成 | done | 支持 HTTP/SOCKS 代理，Markdown 符号自动清理 |
| 提示音播放 | done | beep_hi.wav（开始说话）、beep_lo.wav（录音结束） |
| 彩色日志系统 | done | 终端彩色 + 文件轮转，各模块独立 logger |

### 第二阶段：体验优化（已完成）

| 功能 | 状态 | 说明 |
|------|------|------|
| 单轮/多轮对话模式 | done | config `conversation.mode`: single / multi |
| VAD 端点检测 | done | 能量阈值 + 静默超时，参数可配置 |
| 系统提示词 | done | 约束 AI 回复为纯文本、口语化、无 Markdown/emoji |
| TTS 文本清理 | done | 合成前自动去除 `**`、`*`、`#`、emoji、HTML 标签等 |
| 多轮对话退出优化 | done | AI 回复后等待用户开口（5s），无声音则退出回 IDLE |
| OpenClaw 超时恢复 | done | 超时后播提示音直接回唤醒词监听，不尝试 TTS |
| OpenClaw 响应加速 | done | 流式读取 stdout，JSON 完整即返回，不等进程退出（节省 ~15s） |
| TTS 临时文件清理 | done | speak() try/finally 确保清理 + 定期清理过期文件 + 启动时清理残留目录 |

### 第三阶段：扩展功能（已完成）

| 功能 | 状态 | 说明 |
|------|------|------|
| 对话历史持久化 (MySQL) | done | aiomysql 异步连接池，自动建库建表，conversations + messages 两表 |
| 对话自动归档 | done | 超过 max_history_rounds 轮自动归档旧对话，新建新会话 |
| 手动新建对话 | done | Web 按钮 / 语音指令"新对话" |
| 本地技能路由 | done | 关键词匹配→本地执行，不经过 AI，毫秒级响应 |
| 音量控制 | done | amixer set Master 10%+/- |
| 停止播放 | done | pkill mpv |
| 报时 | done | 本地 datetime 格式化 |
| 语音打断 (Barge-in) | done | TTS 播放期间后台运行唤醒词检测，检测到则 kill 播放进程 |
| 流式 TTS | done | 按标点分句，asyncio.Queue 生产者-消费者，边合成边播放 |

### 第四阶段：Web 界面（已完成）

| 功能 | 状态 | 说明 |
|------|------|------|
| Web 聊天页面 | done | aiohttp，暗色主题，对话列表侧边栏，消息标注来源(voice/web) |
| Web 配置管理 | done | 在线编辑 config.yaml，枚举字段下拉框，保存后部分需重启 |
| 实时状态面板 | done | WebSocket 推送，聊天页顶部彩色状态点 (IDLE/LISTENING/THINKING/SPEAKING) |
| OTA 更新 | done | git fetch→检查→git pull→supervisorctl restart WakeUpOpenClaw |
| 服务重启 | done | supervisorctl restart WakeUpOpenClaw |

### 第五阶段：安全与运维（已完成）

| 功能 | 状态 | 说明 |
|------|------|------|
| 配置敏感值加密 | done | `${ENV_VAR}` 语法引用环境变量，密码不入 git |
| Web 配置安全 | done | 页面显示 `${VAR}` 原始文本，不暴露实际密码 |

---

## 项目文件清单

```
WakeUpOpenClaw/
├── main.py                      # 主程序入口，异步状态机
├── config.yaml                  # 完整配置文件
├── requirements.txt             # Python 依赖
├── README.md                    # 项目说明文档
├── PROGRESS.md                  # 本文件 — 项目进度记录
│
├── wake_up/                     # 唤醒词检测模块
│   ├── base.py                  #   抽象基类 BaseWakeWordDetector
│   ├── factory.py               #   工厂函数 create_detector()
│   ├── snowboy_detector.py      #   Snowboy 实现
│   └── porcupine_detector.py    #   Porcupine 实现
│
├── asr/
│   └── funasr_client.py         # FunASR WebSocket 客户端 (SSL 可选)
│
├── agent/
│   └── openclaw_client.py       # OpenClaw CLI 封装 (流式 stdout 读取)
│
├── tts/
│   └── edge_tts_engine.py       # Edge TTS: 合成/播放/流式/打断/代理/清理
│
├── audio/
│   └── recorder.py              # PyAudio 麦克风录音
│
├── skills/
│   └── router.py                # 本地技能路由 (关键词匹配 + 内置动作)
│
├── storage/
│   └── database.py              # MySQL 对话历史 (aiomysql 异步)
│
├── web/
│   ├── server.py                # aiohttp 服务端 (聊天/配置/OTA/WebSocket)
│   └── templates/
│       ├── chat.html            #   聊天页面 (对话列表 + 实时状态)
│       └── config.html          #   配置管理 + 系统管理页面
│
├── utils/
│   ├── logger.py                # 彩色日志 (终端 + 文件轮转)
│   └── config_resolver.py       # ${ENV_VAR} 配置值解析器
│
├── static/                      # 提示音文件
│   ├── beep_hi.wav
│   └── beep_lo.wav
│
├── snowboy/                     # Snowboy 运行时 (编译产物 + 模型)
├── models/                      # Porcupine 唤醒词模型目录
└── logs/                        # 日志输出目录
```

---

## 配置项总览

| 配置段 | 关键配置 | 说明 |
|--------|---------|------|
| `wake_up` | engine, snowboy.*, porcupine.* | 唤醒词引擎选择和参数 |
| `asr` | server_url, ssl_enabled, mode | FunASR 服务地址和识别模式 |
| `agent` | method, session_id, thinking, timeout, local, system_prompt | OpenClaw 调用参数 |
| `tts` | voice, rate, volume, player, proxy | Edge TTS 语音合成参数 |
| `audio` | sample_rate, channels, chunk_size, input_device_index | 音频采集参数 |
| `conversation` | mode, silence_timeout, vad_*, barge_in, streaming_tts, max_history_rounds | 对话行为参数 |
| `skills` | enabled, commands[] | 本地技能指令列表 |
| `web` | enabled, host, port, tts_on_web | Web 服务参数 |
| `database` | host, port, user, password, database, pool_size | MySQL 连接参数 |
| `logging` | level, console, console_color, file, max_file_size, backup_count | 日志参数 |

---

## 待实现 / 未来规划

| 功能 | 优先级 | 说明 |
|------|--------|------|
| 连接状态监控和自动重连 | 中 | FunASR/OpenClaw/TTS 后台健康检查，异常时自动重连 |
| 定时任务 / 闹钟 | 低 | 语音设置闹钟，asyncio 定时器，到时播放提示音 |
| Web 音量控制滑块 | 低 | 聊天页面加音量滑块，调用 amixer |
| 多语言 TTS 自动切换 | 低 | 根据识别文本语言自动选择 TTS voice |
| 更多内置技能 | 低 | 天气查询（本地API）、音乐播放控制等 |

---

## 部署信息

| 项目 | 值 |
|------|-----|
| 运行平台 | 树莓派 5 (Linux ARM64) |
| 进程管理 | Supervisor (`program:WakeUpOpenClaw`) |
| FunASR | Docker, ws://localhost:10095, offline 模式 |
| MySQL | Docker, localhost:3306, 数据库 ZLPI, 用户 zlpi |
| Web 端口 | 8084 |
| TTS 代理 | http://127.0.0.1:7890 |
| 唤醒词引擎 | Snowboy (默认) |
| 敏感配置 | 通过 `${ENV_VAR}` 引用环境变量，supervisor environment 设置 |

---

## Git 提交历史

共 34 个提交，按功能分组：

**核心实现 (v1)**
- `d126f99` 初始语音助手实现
- `773adaf` Snowboy 可插拔唤醒词架构
- `463c1b7` 提示音播放

**FunASR 连接修复**
- `e1d7b46` SSL 支持
- `67f1ef9` 切换到 offline 模式 (端口 10095)
- `b76bc19` offline 模式 10s 延迟修复

**OpenClaw 调用优化**
- `ce66ad4` JSON/纯文本双格式解析
- `6395b4f` Gateway JSON result.payloads 嵌套结构修复
- `5c68aee` 流式 stdout 读取（节省 ~15s）
- `8d130a5` 超时后直接回唤醒词

**对话体验优化**
- `edc3441` 单轮/多轮模式
- `c51023e` AI 回复后不自动播放 beep
- `4fed983` VAD 参数可配 + --local 标志
- `2ac3834` 系统提示词 + Markdown 清理

**TTS 优化**
- `9937905` 代理支持
- `6ecbb26` 临时文件三层清理
- `2957833` 流式分句合成

**Web 界面**
- `6c4cf50` 聊天页面
- `a05a286` 配置管理页面
- `a596a45` 枚举字段下拉框
- `a310a6b` WebSocket 实时状态面板

**扩展功能**
- `ed191a4` MySQL 对话历史持久化
- `a9aa851` 本地技能路由
- `f93a0c6` OTA 更新
- `0664cf0` 语音打断 (Barge-in)

**安全**
- `7066744` ${ENV_VAR} 配置值加密
- `69ba9e3` Porcupine access_key 环境变量化
