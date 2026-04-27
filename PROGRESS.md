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
| 免打扰时间 | done | 配置时间段内暂停语音唤醒（跨午夜支持），Web 聊天不受影响，状态面板显示灰色"免打扰中" |

### 第三阶段：扩展功能（已完成）

| 功能 | 状态 | 说明 |
|------|------|------|
| 对话历史持久化 (MySQL) | done | aiomysql 异步连接池，自动建库建表，conversations + messages 两表 |
| 对话自动归档 | done | 超过 max_history_rounds 轮自动归档旧对话，新建新会话 |
| 手动新建对话 | done | Web 按钮 / 语音指令"新对话" |
| 本地技能路由 | done | 关键词匹配→本地执行，不经过 AI，毫秒级响应 |
| 技能独立配置 | done | 按业务分组（music/calendar/conversation/utility），每组独立开关+options，Web 卡片式 toggle + 标签式关键词编辑 |
| 音量控制 | done | amixer set Master 10%+/- |
| 停止播放 | done | pkill mpv |
| 报时 | done | 本地 datetime 格式化 |
| 系统重启 | done | 双 action 二次确认：reboot(提示) + confirm_reboot(执行 sudo reboot) |
| 系统状态查询 | done | psutil 读取 CPU 使用率/温度、内存、磁盘、运行时间，语音口语化播报 |
| IP 地址查询 | done | 读取局域网 IP 和主机名 |
| 网络状态检测 | done | ping 百度和谷歌，显示延迟或不通状态 |
| 晨间简报 | done | "早上好"触发：wttr.in 获取天气 + AI 生成今日头条/财经/娱乐/笑话 |

### 第八阶段：定时器（已完成）

| 功能 | 状态 | 说明 |
|------|------|------|
| 定时器设定 | done | "5分钟后提醒我关火" 解析时长+标签，asyncio 倒计时 |
| 定时器查询 | done | "还剩多少时间" 查看所有活跃定时器剩余时间 |
| 定时器取消 | done | "取消定时器" 取消最近的/全部定时器 |
| 到期提醒 | done | TTS 语音播报（不受免打扰限制）+ 可选微信推送 |
| 时长解析 | done | 支持 N分钟/N小时/N秒/半小时/N个半小时 等格式 |
| 语音打断 (Barge-in) | done | TTS 播放期间后台运行唤醒词检测，检测到则 kill 播放进程 |
| 流式 TTS | done | 按标点分句，asyncio.Queue 生产者-消费者，边合成边播放 |

### 第四阶段：Web 界面（已完成）

| 功能 | 状态 | 说明 |
|------|------|------|
| Web 聊天页面 | done | aiohttp，暗色主题，对话列表侧边栏，消息标注来源(voice/web)，删除对话 |
| Web 配置管理 | done | 在线编辑 config.yaml，枚举字段下拉框，保存后部分需重启 |
| 实时状态面板 | done | WebSocket 推送，聊天页顶部彩色状态点 (IDLE/LISTENING/THINKING/SPEAKING) |
| OTA 更新 | done | git fetch→检查→git pull→supervisorctl restart WakeUpOpenClaw |
| 服务重启 | done | supervisorctl restart WakeUpOpenClaw |
| Web 音量控制 | done | 聊天页侧边栏底部滑块，amixer 控制系统音量 |
| Web 日志查看 | done | /logs 页面，读取日志文件，级别/模块过滤，关键词搜索高亮，自动刷新 |
| Web 日程日历 | done | /calendar 页面，7/14天列表视图，日程增删改查，颜色分类，周末高亮 |
| Web 系统监控 | done | /status 页面，系统状态/IP/网络连通性，资源警报阈值颜色提示，手动刷新 |

### 第五阶段：安全与运维（已完成）

| 功能 | 状态 | 说明 |
|------|------|------|
| 配置敏感值加密 | done | `${ENV_VAR}` 语法引用环境变量，密码不入 git |
| Web 配置安全 | done | 页面显示 `${VAR}` 原始文本，不暴露实际密码 |
| session-id 竞态修复 | done | send_message 传入 session_id 参数，不再修改共享状态 |
| barge-in 线程泄漏修复 | done | 取消 Future 后调用 detector.stop() 中断阻塞线程 |
| asyncio 事件循环安全 | done | get_event_loop → get_running_loop，异步清理移到 main() finally |
| skills 标点匹配修复 | done | match() 去除标点后匹配，避免 FunASR 识别标点干扰 |

### 第六阶段：日程管理（已完成）

| 功能 | 状态 | 说明 |
|------|------|------|
| events 数据表 | done | MySQL 自动建表，title/date/time/color/category/remind 等字段 |
| 日程 CRUD API | done | GET/POST/PUT/DELETE /api/events |
| 日程日历页面 | done | 7/14 天视图，左右翻页，周末紫色，今天红色，点击新建/编辑 |
| 日程颜色分类 | done | 8 种预设颜色，日程块左边框着色 |
| 语音日程查询 | done | 今天/明天/本周/下周/本周剩余 日程查询，多行句号分隔播报，时间口语化 |
| 日程提醒 (TTS) | done | 后台每 60s 检查，提前 N 分钟 TTS 语音播报，受免打扰限制 |
| 日程提醒 (微信) | done | 通过 openclaw message send --target --message 发送，不受免打扰限制 |
| MCP 日程工具 | done | MCP Server 暴露 7 个日程工具给 OpenClaw Agent，支持语音和微信操作日程 |

### 第七阶段：本地音乐播放（已完成）

| 功能 | 状态 | 说明 |
|------|------|------|
| 音乐数据库查询 | done | 读取 zlpi_music 表，精确/模糊搜索歌名、歌手，随机/收藏过滤 |
| MusicPlayer 播放器 | done | 播放列表管理、后台 mpv 播放循环、上一首/下一首/停止 |
| 语音播放控制 | done | "播放歌曲XX"单曲、"播放歌曲"列表、"下一首"/"上一首"/"停止播放" |
| 关键词智能提取 | done | 从"播放歌曲雨爱"中提取搜索词"雨爱"，去除连接词 |
| 收藏歌曲播放 | done | "播放收藏的歌"只播放 is_favorite=1 |

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
│   ├── router.py                # 本地技能路由 (关键词匹配 + 内置动作)
│   ├── music_player.py          # 本地音乐播放器 (播放列表 + mpv 管理)
│   └── timer.py                 # 定时器管理 (asyncio 倒计时 + 到期回调)
│
├── mcp/
│   └── calendar_server.py       # MCP Server (日程工具，供 OpenClaw Agent 调用)
│
├── storage/
│   └── database.py              # MySQL 对话历史 (aiomysql 异步)
│
├── web/
│   ├── server.py                # aiohttp 服务端 (聊天/配置/OTA/WebSocket/日志/音量/状态)
│   └── templates/
│       ├── chat.html            #   聊天页面 (对话列表 + 实时状态 + 音量控制)
│       ├── config.html          #   配置管理 + 系统管理页面
│       ├── logs.html            #   日志查看页面
│       ├── calendar.html        #   日程日历页面
│       └── status.html          #   系统状态监控页面
│
├── utils/
│   ├── logger.py                # 彩色日志 (终端 + 文件轮转)
│   ├── config_resolver.py       # ${ENV_VAR} 配置值解析器
│   └── system_info.py           # 系统状态收集 (CPU/IP/网络)
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
| `conversation` | mode, silence_timeout, vad_*, barge_in, streaming_tts, max_history_rounds, do_not_disturb | 对话行为参数 |
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
