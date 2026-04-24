<!-- 本 README 由 AI 辅助生成，基于项目使用说明文档 -->

# 多平台内容采集系统

![Python Version](https://img.shields.io/badge/Python-3.11-blue)
![License](https://img.shields.io/badge/License-MIT-green)
![Platform Support](https://img.shields.io/badge/Platform-Windows-brightgreen)
![Build Status](https://img.shields.io/badge/Build-Passing-success)

## 📖 项目概要

**多平台内容采集系统·极简使用说明**

- **定位**：面向运营/运维人员的一键式多平台文章数据采集工具，无需代码、无需安装环境，双击即用。
- **功能**：自动从 10 个主流内容平台（微信公众号、微博、头条、知乎、百家号等）抓取文章阅读、点赞、评论、转发等运营数据，支持导出 CSV 与一键同步飞书多维表格。总运行时长大概在 5 分钟左右。
- **总运行时长**：约 5 分钟

### 🚫 能力边界

- 仅采集大约两周以内发布的文章，不支持历史旧文采集
- 平台页面改版仅需改配置文件【结合 AI 编程工具自动分析】，无需动代码
- 单平台异常不影响其他平台，故障自动隔离
- 不支持批量无限爬取，单次最多采集 5 篇指定标题文章

## 📋 支持平台列表

| 平台标识 | 显示名称 | 登录方式 | 状态 |
|----------|----------|----------|------|
| `wechat` | 微信公众号 | 扫码登录 | ✅ 稳定 |
| `weibo` | 微博 | 自动填表 + 扫码兜底 | ✅ 稳定 |
| `toutiao` | 头条号 | 自动填表 + 扫码兜底 | ✅ 稳定 |
| `zhihu` | 知乎 | 自动填表 + 扫码兜底 | ✅ 稳定 |
| `baijiahao` | 百家号 | 自动填表 + 扫码兜底 | ✅ 稳定 |
| `netease` | 网易号 | 自动填表 + 扫码兜底 | ✅ 稳定 |
| `qq` | 企鹅号 | 扫码登录 | ✅ 稳定 |
| `yidian` | 一点资讯 | 自动填表 + 扫码兜底 | ✅ 稳定 |
| `zaker` | ZAKER | 自动填表 + 扫码兜底 | ✅ 稳定 |
| `xueqiu` | 雪球 | 扫码登录 | ✅ 稳定 |

> 注：微信公众号、企鹅号、雪球仅支持扫码登录，需手动扫码完成登录流程。

## 🏗️ 系统架构

### 1. 项目总体架构图

```mermaid
flowchart TB
    subgraph UI["接入层 · Qt 主线程"]
        GUI["GUI 模块<br/>MainWindow<br/>平台选择/标题输入<br/>手动登录/日志/数据表格"]
    end

    subgraph THREAD["应用服务层 · 子线程"]
        direction TB
        CT["采集线程<br/>CrawlThread<br/>QThread+asyncio"]
        CS["调度控制器<br/>CrawlScheduler<br/>平台调度/登录控制<br/>数据归一化/快照"]
    end

    subgraph COLLECTORS["核心业务层 · 采集器集群"]
        PLATS["平台采集器<br/>10个平台<br/>遵循统一模板"]
    end

    subgraph UTILS["基础设施层 · 工具支撑"]
        direction TB
        REG["平台注册器<br/>PlatformRegistry"]
        EXP["曝光加载器<br/>ExposureLoader"]
        MAT["标题匹配器<br/>TitleMatcher"]
        DM["数据模型<br/>DataModel<br/>11字段"]
        OPS["运维工具<br/>ops<br/>日志轮转/快照清理"]
        FW["飞书导入服务<br/>FeishuImportWorker<br/>导入与导出"]
    end

    subgraph STORAGE["数据持久层 · 本地文件"]
        direction TB
        SECRETS["加密存储<br/>.encryption.key<br/>cookies/*.enc"]
        CONFIGS["配置文件<br/>platforms/*.yaml<br/>exposure.yaml"]
        DATA_CSV["采集数据<br/>data/*.csv"]
        LOGS["运行日志<br/>logs/*.log"]
        SNAP["异常快照<br/>snapshots/"]
    end

    EXTERNAL{"外部依赖<br/>飞书多维表格"}

    GUI --> CT
    CT --> CS
    CS --> PLATS
    CT -.->|"日志信号"| GUI
    CS -.->|"登录失败通知"| GUI
    GUI -->|"启动"| FW
    FW --> DATA_CSV
    FW --> EXTERNAL
    PLATS --> SECRETS
    PLATS --> CONFIGS
    OPS --> LOGS
    OPS --> SNAP
    REG --> CONFIGS
    EXP --> CONFIGS
    DM --> DATA_CSV
    CS --> REG
    CS --> DM
    CS --> MAT
    CS --> EXP

    classDef external fill:#f9f0ff,stroke:#a0a,stroke-width:2px
    style UI fill:#f0f4ff,stroke:#4a6fa5,stroke-width:2px
    style THREAD fill:#f0fff4,stroke:#4a9e5c,stroke-width:2px
    style COLLECTORS fill:#fff4f0,stroke:#c07a4b,stroke-width:2px
    style UTILS fill:#f4f0ff,stroke:#7a4ba0,stroke-width:2px
    style STORAGE fill:#f4fff0,stroke:#5c9e4a,stroke-width:2px
```

### 2. GUI 架构图
为你梳理了一份综合性 GUI 架构图，它融合了所有区域、控件以及跨线程的信号连接关系，便于开发与 AI 精确解析。

```text
┌─────────────────────────────────────────────────────────────────────┐ 
│                 多平台内容采集系统 v3.3 (GUI 架构)                  │ 
├─────────────────────────────────────────────────────────────────────┤ 
│  ┌─ 区域1: 采集平台选择 ────────────────────────────────────┐      │ 
│  │  - 10 个 QCheckBox (微信公众号，微博，... 雪球)           │      │ 
│  │  - QPushButton [全选] [全不选]                           │      │ 
│  │  - 启用条件: 至少选中 1 个平台才能启动采集               │      │ 
│  ├──────────────────────────────────────────────────────────┤      │ 
│  ├─ 区域2: 标题/关键词输入 ─────────────────────────────────┤      │ 
│  │  - 最多 5 个 QLineEdit 标题输入框                         │      │ 
│  │  - 动态关键词输入框 (微博/头条号标题 > 30字时展示)       │      │ 
│  ├──────────────────────────────────────────────────────────┤      │ 
│  ├─ 区域3: 执行控制 ────────────────────────────────────────┤      │ 
│  │  - QPushButton [启动采集] [暂停] [停止]                  │      │ 
│  │  - QLabel 显示运行状态                                    │      │ 
│  ├──────────────────────────────────────────────────────────┤      │ 
│  ├─ 区域4: 手动登录队列 ────────────────────────────────────┤      │ 
│  │  - QListWidget 展示自动登录失败的平台                     │      │ 
│  │  - QPushButton [手动登录采集]                            │      │ 
│  ├──────────────────────────────────────────────────────────┤      │ 
│  ├─ 区域5: 手动登录采集模块 ────────────────────────────────┤      │ 
│  │  - QListWidget (微信/企鹅/雪球置顶)                       │      │ 
│  │  - QPushButton [手动登录并采集] (有头浏览器模式)         │      │ 
│  ├──────────────────────────────────────────────────────────┤      │ 
│  ├─ 区域6: 实时日志 ────────────────────────────────────────┤      │ 
│  │  - QTextBrowser (接收 CrawlThread log_signal 信号)       │      │ 
│  ├──────────────────────────────────────────────────────────┤      │ 
│  ├─ 区域7: 数据结果 ────────────────────────────────────────┤      │ 
│  │  - QTableWidget (11 个标准字段: 发布日期，阅读，点赞等)  │      │ 
│  │  - 未匹配标题列表 (Widget)                                │      │ 
│  ├──────────────────────────────────────────────────────────┤      │ 
│  └─ 区域8: 工具集 ──────────────────────────────────────────┘      │ 
│     - QPushButton [导出 CSV] [导入飞书] [清空数据]                │ 
└─────────────────────────────────────────────────────────────────────┘
```

### 3. 项目核心执行流程图
清晰展示从调度到采集、数据回传的完整逻辑链，包含手动登录两条路径。

```mermaid
graph TD
    A[用户操作] -->|点击启动采集| B[CrawlThread 启动]
    A -->|点击手动登录采集| C[手动登录流程]
    
    B --> D[CrawlScheduler 调度]
    D --> E{自动登录}
    E -->|成功| F[execute_platform 自动采集]
    E -->|失败| G[加入手动登录队列]
    
    G -->|用户选择| C
    C --> H[启动有头浏览器]
    H --> I[用户完成登录]
    I --> J[execute_manual_crawl 手动采集]
    
    F --> K[BaseCollector 处理]
    J --> K
    K --> L[ensure_login 登录验证]
    L --> M[crawl 采集执行]
    M --> N[result_callback 结果处理]
    
    N --> O[data_signal 数据信号]
    N --> P[log_signal 日志信号]
    O --> Q[GUI 主线程更新]
    P --> Q
    Q --> R[数据表格显示]
    Q --> S[日志输出]
    Q --> T[飞书导入]

    style A fill:#e6f7ff,stroke:#1890ff,stroke-width:2px
    style B fill:#f0f2ff,stroke:#597ef7,stroke-width:2px
    style C fill:#fef0f0,stroke:#f5222d,stroke-width:2px
    style D fill:#f0f2ff,stroke:#597ef7,stroke-width:2px
    style E fill:#fff2e8,stroke:#fa8c16,stroke-width:2px
    style F fill:#f0f2ff,stroke:#597ef7,stroke-width:2px
    style G fill:#fef0f0,stroke:#f5222d,stroke-width:2px
    style H fill:#fef0f0,stroke:#f5222d,stroke-width:2px
    style I fill:#fef0f0,stroke:#f5222d,stroke-width:2px
    style J fill:#fef0f0,stroke:#f5222d,stroke-width:2px
    style K fill:#f0f2ff,stroke:#597ef7,stroke-width:2px
    style L fill:#f0f2ff,stroke:#597ef7,stroke-width:2px
    style M fill:#f0f2ff,stroke:#597ef7,stroke-width:2px
    style N fill:#f0f2ff,stroke:#597ef7,stroke-width:2px
    style O fill:#f0f2ff,stroke:#597ef7,stroke-width:2px
    style P fill:#f0f2ff,stroke:#597ef7,stroke-width:2px
    style Q fill:#e6f7ff,stroke:#1890ff,stroke-width:2px
    style R fill:#e6f7ff,stroke:#1890ff,stroke-width:2px
    style S fill:#e6f7ff,stroke:#1890ff,stroke-width:2px
    style T fill:#f6ffed,stroke:#52c41a,stroke-width:2px
```

**执行流程说明：**
1. **用户操作**：点击启动采集或手动登录采集
2. **调度流程**：CrawlScheduler 负责平台调度和登录控制
3. **自动采集**：自动登录成功后执行无头浏览器采集
4. **手动登录**：自动登录失败或用户直接选择手动登录
5. **采集执行**：BaseCollector 处理登录验证和采集逻辑
6. **结果处理**：将采集结果通过信号传递给主线程
7. **数据展示**：在GUI界面显示采集数据和日志信息
8. **数据导出**：支持导出CSV或导入飞书


### 4. 手动登录采集流程图（双路径兼容）
清晰展示手动登录采集的完整流程，包含自动登录失败和用户直接手动采集两条路径。

```mermaid
flowchart TB
    classDef fail fill:#fff1f0,stroke:#f5222d,stroke-width:1px
    classDef manual fill:#f9f0ff,stroke:#722ed1,stroke-width:1px
    classDef exec fill:#f0fff4,stroke:#52c41a,stroke-width:1px

    P1[自动采集Cookie失效]:::fail
    P2[加入手动登录队列<br/>GUI提示]:::manual
    P3[用户触发手动登录]:::manual
    P4[暂停自动任务<br/>启动有头浏览器]:::exec
    P5[扫码/自动填表登录]:::exec
    P6[执行数据采集]:::exec
    P7[恢复自动任务]:::exec

    Q1[用户直接手动采集]:::manual
    Q2[校验标题+启动有头模式]:::exec

    P1-->P2-->P3-->P4-->P5-->P6-->P7
    Q1-->Q2-->P5
```

**手动登录流程说明：**
1. **路径一（自动登录失败）**：当自动采集Cookie失效时，系统会将该平台加入手动登录队列并在GUI界面提示用户
2. **路径二（用户直接操作）**：用户可以直接选择手动登录采集，跳过自动登录步骤
3. **执行过程**：无论是哪种路径，都会启动有头浏览器，用户完成登录后执行数据采集
4. **任务管理**：手动采集过程中会暂停自动任务，采集完成后恢复自动任务的执行

### 5. 打包后项目目录架构图

![打包后项目目录架构图](./images/packaged_directory.png)

### 6. 打包版使用流程图（用户操作流程）

```mermaid
flowchart TB 
    Start[下载压缩包] --> Step1[解压到本地文件夹] 
    Step1 --> Step2[阅读首次配置说明\n部署ms-playwright] 
    Step2 --> Step3[重命名.env.example为.env\n填写密钥/账号] 
    Step3 --> Step4[双击exe启动程序] 
    Step4 --> Step5[选择平台+输入文章标题] 
    Step5 --> Step6[点击启动采集] 
    Step6 --> Step7{自动登录成功?} 
    Step7 -->|是| Step8[继续自动采集] 
    Step7 -->|否| Step9[自动登录失败\n平台进入手动登录队列] 
    Step9 --> Step10[用户选择失败平台\n点击手动登录采集] 
    Step10 --> Step11[启动有头浏览器] 
    Step11 --> Step12[等待用户5分钟\n完成登录操作] 
    Step12 --> Step13{登录成功?} 
    Step13 -->|是| Step14[继续采集] 
    Step13 -->|否| Step15[登录超时\n请重新尝试] 
    Step14 --> Step16[数据汇总] 
    Step8 --> Step16 
    Step16 --> Step17{采集完成} 
    Step17 -->|导出数据| Step18[保存CSV/导入飞书] 
    Step18 --> End[使用完成] 
    Step15 --> Step10 

    %% 配色 
    style Start fill:#e6f7ff,stroke:#1890ff 
    style End fill:#f6ffed,stroke:#52c41a 
    style Step11 fill:#fff2e8,stroke:#fa8c16 
    style Step12 fill:#fff2e8,stroke:#fa8c16 
    style Step16 fill:#f6ffed,stroke:#52c41a 
```

### 7. 系统总技术架构图（核心分层）
该图展示从用户交互到底层存储的完整技术分层，标明了各层核心模块及关键通信方式。

```
┌────────────────────────────────────────────────────────────────────────────────┐
│                     【表现层 · Qt 主线程 (GUI 事件循环)】                         │
├────────────────────────────────────────────────────────────────────────────────┤
│  📁 gui.py : MainWindow (QScrollArea)                                          │
│  ├─ 平台选择        (10个复选框 + 全选/全不选)                                 │
│  ├─ 标题/关键词输入  (5个标题槽 + 动态关键词槽)                                 │
│  ├─ 手动登录/采集   (自动登录失败队列 + 手动登录并采集模块)                     │
│  ├─ 实时日志         (QTextBrowser, 跨线程 log_signal 追加)                     │
│  ├─ 数据结果         (QTableWidget, 11标准字段)                                │
│  └─ 工具模块         (导出CSV, 导入飞书, 清空数据)                             │
└───────────────────────────┬────────────────────────────────────────────────────┘
                            │  Qt 信号/槽 (跨线程安全通信)
                            ▼
┌────────────────────────────────────────────────────────────────────────────────┐
│                   【调度与采集层 · 独立子线程 (asyncio 循环)】                    │
├────────────────────────────────────────────────────────────────────────────────┤
│  📁 thread.py : CrawlThread (QThread)                                           │
│  📁 controller.py : CrawlScheduler (调度中心)                                    │
│  ├─ 核心属性: platform_queue, manual_login_queue, is_paused, is_running        │
│  ├─ 核心功能:                                                                     │
│  │   ├─ 平台 FIFO 串行调度                                                       │
│  │   ├─ 双模式登录控制 (自动登录优先 → 失败则进入手动队列)                      │
│  │   ├─ 浏览器模式控制 (自动采集无头；手动采集强制有头)                         │
│  │   ├─ 策略一采集执行 (调用采集器 crawl 接口)                                  │
│  │   ├─ 数据归一化处理 (11字段映射 + 静态曝光量注入)                            │
│  │   └─ 异常处理与快照记录                                                        │
└───────────────────────────┬────────────────────────────────────────────────────┘
                            │  动态导入 collectors/{platform}.py
                            ▼
┌────────────────────────────────────────────────────────────────────────────────┐
│               【采集器层 · 各平台采集器 (均继承 BaseCollector)】                  │
├────────────────────────────────────────────────────────────────────────────────┤
│  📂 collectors/                                                                 │
│  ├─ template_collector.py (BaseCollector 基类)                                  │
│  ├─ wechat.py / weibo.py / toutiao.py / ... / xueqiu.py (10个稳定平台)          │
│  │                                                                              │
│  └─ 每个采集器内部标准模块:                                                       │
│      ├─ ConfigLoader       (加载 platforms/{platform}.yaml)                      │
│      ├─ AntiSpiderHelper   (随机UA、人类输入模拟)                               │
│      ├─ RetryManager       (可重试异常自动重试)                                 │
│      ├─ LoginManager       (Cookie管理、自动/手动登录)                          │
│      ├─ NavigationManager  (导航至文章列表页)                                   │
│      ├─ TitleMatcher       (三级标题匹配算法)                                   │
│      └─ ArticleListExtractor (策略一提取、翻页控制)                             │
└───────────────────────────┬────────────────────────────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────────────────────────────┐
│                      【工具层 · 线程安全与数据支撑】                               │
├────────────────────────────────────────────────────────────────────────────────┤
│  📂 utils/                                                                      │
│  ├─ platform_registry.py  - 平台注册与查询 (单例)                               │
│  ├─ data_model.py         - 11字段定义与映射                                    │
│  ├─ title_matcher.py      - 三级匹配算法 (精确/清理/标准化)                     │
│  ├─ exposure_loader.py    - 曝光量加载器 (单例)                                 │
│  ├─ feishu_worker.py      - 飞书导入工作线程 (独立 QThread)                    │
│  ├─ feishu_exporter.py    - 飞书 API 封装 (批量写入)                             │
│  └─ ops.py                - 日志轮转、健康检查、快照清理                         │
└───────────────────────────┬────────────────────────────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────────────────────────────┐
│                    【数据存储层 · 本地文件系统 (无外部依赖)】                     │
├────────────────────────────────────────────────────────────────────────────────┤
│  ├─ platforms/*.yaml     (核心配置：选择器、URL、超时，改版只需修改此处)        │
│  ├─ config/exposure.yaml (静态曝光量配置，支持实时修改并生效)                   │
│  ├─ .encryption.key      (Fernet 对称加密密钥，自动生成)                         │
│  ├─ cookies/*.enc        (AES 加密的 Cookie 文件，保存登录态)                   │
│  ├─ data/*.csv           (导出的采集数据，UTF-8-BOM 编码)                        │
│  ├─ logs/*.log           (loguru 日志，按天轮转，保留30天)                       │
│  └─ snapshots/{plat}_ts/ (异常快照：截图+DOM+堆栈，保留7天)                    │
└────────────────────────────────────────────────────────────────────────────────┘
```

## � 项目源代码目录结构

### 1. 原代码目录结构（打包前）

```
multi_platform_crawler/
├── main.py                 # 程序入口
├── gui.py                  # PySide6 主界面
├── controller.py           # 调度中心 CrawlScheduler
├── thread.py               # 采集子线程 CrawlThread
├── collectors/             # 各平台采集器（继承 BaseCollector）
│   ├── __init__.py
│   ├── template_collector.py
│   ├── wechat.py
│   ├── weibo.py
│   ├── toutiao.py
│   ├── zhihu.py
│   ├── baijiahao.py
│   ├── netease.py
│   ├── qq.py
│   ├── yidian.py
│   ├── zaker.py
│   └── xueqiu.py
├── platforms/              # YAML 配置文件（驱动采集逻辑）
│   ├── wechat.yaml
│   ├── weibo.yaml
│   ├── toutiao.yaml
│   ├── zhihu.yaml
│   ├── baijiahao.yaml
│   ├── netease.yaml
│   ├── qq.yaml
│   ├── yidian.yaml
│   ├── zaker.yaml
│   └── xueqiu.yaml
├── config/                 # 静态配置
│   └── exposure.yaml       # 静态曝光量配置
├── utils/                  # 工具模块
│   ├── __init__.py
│   ├── platform_registry.py
│   ├── data_model.py
│   ├── title_matcher.py
│   ├── exposure_loader.py
│   ├── ops.py
│   ├── feishu_worker.py
│   ├── feishu_exporter.py
│   ├── security.py
│   └── path_helper.py
├── docs/                   # 项目文档
├── cookies/                # 加密存储的登录态（运行时生成）
├── data/                   # 导出 CSV 数据（运行时生成）
├── logs/                   # 日志文件（运行时生成）
├── snapshots/              # 异常快照（运行时生成）
├── .env.example            # 环境变量配置模板
├── .gitignore              # Git 忽略文件
├── build_exe.bat           # 打包脚本
├── check_before_pack.py    # 打包前检查脚本
├── requirements.txt        # 依赖文件
├── test_migration.py       # 迁移测试脚本
└── 首次配置说明.txt         # 首次使用配置说明
```

### 2. 打包后目录结构

```
Crawler_Portable_vX.X.X/      # 交付文件夹
├── MultiPlatformCrawler.exe  # 主程序（可重命名为「多平台内容采集系统.exe」）
├── .env                     # 环境变量配置（用户创建）
├── collectors/              # 外置采集器
├── platforms/               # 外置 YAML 配置文件
├── config/                  # 系统配置
│   └── exposure.yaml        # 曝光量配置
├── ms-playwright/           # 浏览器内核
├── cookies/                 # 自动生成，存放加密 Cookie
├── data/                    # 自动生成，存放导出的 CSV
├── logs/                    # 自动生成，存放日志
├── snapshots/               # 自动生成，存放异常快照
└── 首次配置说明.txt          # 浏览器内核部署步骤
```

## � 核心使用步骤

1. **解压程序包**，按说明复制浏览器缓存到电脑指定目录
2. **双击 exe** 启动程序，勾选要采集的平台
3. **输入最多 5 篇**近两周内发布的目标文章标题
4. **点击【启动采集】**，等待自动完成
5. **查看表格结果**，导出 CSV 或导入飞书
6. **登录失败时**使用【手动登录采集】补采即可

## 📥 安装步骤

### 1. 下载与解压

1. 下载项目压缩包到电脑本地，推荐下载到桌面
2. 右键压缩包选择**全部解压缩**
3. 解压后的文件夹可以保存在系统任意磁盘，推荐保存在桌面

### 2. 部署浏览器内核

1. 右键压缩文件：`ms-playwright.zip` 文件，选择**全部解压缩**
2. 复制解压后的 `ms-playwright` 文件夹
3. 打开文件资源管理器，在顶部搜索框粘贴：`%LOCALAPPDATA%` 并回车
4. 将 `ms-playwright` 文件夹粘贴到 `C:\Users\【你的用户名】\AppData\Local\` 目录下

> 注：如果输入命令没有跳转到指定目录，也可以依据目录路径模板手动查找到该目录

## 🖥️ 工具使用指导

### 1. 启动程序

1. 打开项目文件夹，双击 `MultiPlatformCrawler.exe` 文件
2. 稍等 5~10 秒，会弹出交互界面

![启动界面](images/startup.png)

### 2. 开始采集

1. **选择平台**：单个勾选或者全选要采集的平台
2. **输入标题**：输入最多 5 篇近两周内发布的目标文章标题
3. **点击启动**：点击【启动采集】按钮，等待采集进度条到达 100%
4. **查看结果**：观察'未匹配标题'，分析是否需要补采
5. **导出数据**：点击"导入飞书"或"导出 CSV"

![采集界面](images/collect.gif)

### 3. 关键词输入

当选择了微博或头条平台，且输入的标题字符数大于 30 字时，会弹出关键词输入框。需要按照标题的关键词顺序输入，每个关键词之间空一个空格。

![关键词输入](images/keywords.gif)

### 4. 手动登录与补采

#### 入口一：「需要登录平台」按钮

- **适用场景**：自动采集结束后，列表中出现登录失败平台时的快速补采
- **操作步骤**：
  1. 在列表中单选需补采的平台
  2. 点击 「手动登录采集」
  3. 弹出谷歌浏览器窗口，等待约 10 秒，页面稳定后手动完成扫码登录
  4. 切勿关闭浏览器，程序会自动完成剩余采集并关闭窗口
  5. 采集完毕后，浏览器页面会自动关闭，可选择点击"撤销该平台"

#### 入口二：「最终补采【手动登录，启动浏览器界面】」

- **位置**：位于"4. 手动登录平台"下方，是一个可折叠的独立分组
- **特点**：包含全部 10 个平台，其中微信公众号、企鹅号、雪球号置顶显示
- **适用场景**：
  - 自动采集卡住时，重启程序后单独对某平台进行有头补采
  - 需要观察浏览器操作过程时使用
- **使用步骤**：
  1. 确保主界面标题区至少填写了 1 个目标标题
  2. 在模块列表中单选一个平台
  3. 点击 「手动登录并采集」
  4. 系统以有头浏览器执行登录与采集（便于观察调试）
  5. 数据同样写入主数据表格

![手动登录](images/manual_login.png)

### 5. 共同注意事项

- 一次只能操作一个平台，多个平台需逐个处理
- 系统设置了 5 分钟登录等待时限，请在规定时间内完成扫码
- 登录成功后切勿关闭浏览器窗口，程序会自动处理后续流程
- 过程中不要重复点击按钮，待当前流程结束后再操作下一个

### 6. 分析数据汇总

- 采集到的数据会呈现到：6. 采集数据结果
- 重点关注：未匹配标题，分析是否需要补采

![数据结果](images/results.png)

### 7. 导入飞书

1. 点击"导入飞书"按钮
2. 等待导入成功的信息弹窗
3. 显示导入成功后前往飞书多维表格核查

> 若因网络波动导致导入飞书失败，可点击"导出 CSV"，打开文件后全选数据，复制粘贴至飞书多维表格。


## ❓ 常见问题处理

### 1. 数据采集不完整（平台部分文章未采集到）

- **现象**：系统展示未成功采集的文章标题及对应平台
- **处理方法**：
  1. 单独勾选对应平台 + 输入未采集到的标题，重新发起采集
  2. 若重新采集仍失败：
     - 先手动核实该平台是否已发布对应文章
     - 未发布文章：完成发布后，将文章链接填入链接收集表格
     - 已发布但仍未采集到：直接将文章链接、数据填入链接收集表格

### 2. 平台完全无采集数据（登录态失效）

- **现象**：微信号、雪球号、企鹅号（QQ）完全无数据
- **处理方法**：
  1. 打开界面中的【手动登录采集模块】
  2. 选择无数据的平台（三类平台已在列表置顶），一次仅选一个
  3. 点击【手动登录并采集】，程序会自动弹出对应平台浏览器窗口
  4. 完成登录：
     - 微信号：等待页面跳转至扫码界面，自行扫码登录
     - 雪球号、企鹅号（QQ）：联系管理人协助扫码登录

### 3. 导入飞书失败

- **现象**：点击"导入飞书"后提示失败
- **处理方法**：
  1. 检查网络连接是否稳定
  2. 重新点击"导入飞书"按钮
  3. 若仍失败，点击"导出 CSV"，打开文件后全选数据，复制粘贴至飞书多维表格

## 🧹 日志和数据定期清理

- `\data\` 和 `\logs\` 文件夹内仅存放单次运行的数据与日志，可定期清理（直接全选文件夹内内容删除即可）
- **注意**：切勿误删其他文件，若不慎误删，可在回收站中还原

## 🔧 配置更新

### 平台账号密码更换、飞书 API 更新

1. 在项目文件夹中找到 `.env` 文件
2. 用记事本打开文件
3. 更新对应配置值（变量名不能修改，只能修改等号后面的值）
4. 保存文件并重启程序

### 页面定位/判断专用配置

【初次使用必配】页面判断专用账号名称配置：需严格按照账号实际页面的信息填写账号名称；10 个平台的配置信息需独立区分，禁止重叠混用，避免采集定位混乱。

**配置位置**：在 `.env` 文件的页面定位/判断专用配置模块中填写

**配置项**：
- `{PLATFORM}_PLATFORM_NAME`：平台名称（页面定位用）
- `{PLATFORM}_ACCOUNT_NICKNAME`：账号昵称（侧边栏/页面判断用，需与账号页面实际显示的名称完全一致）

**注意事项**：10个平台的配置信息需独立区分，禁止重叠混用，避免采集定位混乱

### .env 文件配置提醒（新用户必看）

**配置步骤**：
1. 用户下载代码后，在项目目录中找到 `.env.example` 文件（这是模板文件，公开无害）
2. 在同一目录下，新建一个空白文件，命名为 `.env`
3. 打开 `.env.example`，全选复制所有内容
4. 粘贴到新建的 `.env` 文件里
5. 用户把自己的隐私数据（如账号密码、API 密钥等）填进 `.env` 即可

**运行机制**：程序运行时，会自动读取 `.env` 文件，完美运行

### 飞书多维表格 API 配置指南

#### 1. 从链接提取 app_token & table_id

从多维表格链接中直接提取：
- **app_token**：wiki/或base/后至?前的字符串
- **table_id**：table=后至&前的字符串

#### 2. 权限配置

1. 飞书开放平台 → 自建应用 → 权限管理，勾选并开通：
   - 查看、评论、编辑和管理多维表格
   - 查看、评论、编辑和管理云空间中所有文件

2. 多维表格右上角「…」→ 更多 → 添加文档应用，搜索并添加自建应用

#### 3. .env 配置填写

```yaml
# 飞书多维表格配置
FEISHU_APP_ID=cli_xxxxxxxxxxxxxxxx
FEISHU_APP_SECRET=xxxxxxxxxxxxxxxx
FEISHU_APP_TOKEN=xxxxxxxxxxxxxxxx
FEISHU_TABLE_ID=xxxxxxxxxxxxxxxx
```

#### 4. 配置更新规则

| 场景 | 操作 | 是否重启程序 |
|------|------|--------------|
| 换表格/账号/密钥轮换 | 修改.env对应项 | 是 |
| 增删表格字段 | 确保字段与程序匹配 | 否 |

> ⚠️ 注意：
> - .env 修改后需重启程序生效
> - 表格需提前建好 11 个字段：发布日期、题目、发布平台、发布链接、曝光量、阅读、推荐、评论、点赞、转发、收藏

## 🔄 日常运维与平台改版应对

### 日常运维

查阅 `docs/` 目录下的《运维使用手册.md》，文档内包含详细的运维全流程说明及相关注意事项。

### 平台页面改版应对

查阅 `docs/` 目录下的《应对平台改版策略.md》，可获取平台页面改版后的具体应对方法及操作指引。

### 利用 AI 编程工具修复

1. 将《项目总技术文档.md》与《应对平台改版策略.md》提交给 AI
2. 在项目文件的 `\logs\` 目录中，找到对应待修复采集器的日志文件（日志以平台名称命名）
3. 将该日志内容复制粘贴给 AI，并说明这是对应失效平台的日志

> 注：更新频率较高的平台：微信公众号、微博号、百家号



## 📦 模块架构与功能详解

### 1. 核心模块

#### 1.1 调度中心 (CrawlScheduler)
- **文件**：`controller.py`
- **功能**：负责平台调度、登录控制、数据归一化和异常处理
- **核心方法**：
  - `run()`: 主循环，逐平台执行采集
  - `execute_platform()`: 单平台采集执行
  - `execute_manual_crawl()`: 手动有头采集
  - `normalize_data()`: 数据归一化和曝光量注入
- **信号**：平台开始/结束、登录请求、数据就绪、任务完成等

#### 1.2 采集器模块 (collectors/)
- **文件**：`collectors/*.py`
- **功能**：实现各平台的具体采集逻辑
- **核心类**：`BaseCollector` (模板类)
- **标准接口**：
  - `crawl()`: 执行策略一采集，返回成功数据和未匹配标题
- **内置管理器**：
  - `ConfigLoader`: 加载 YAML 配置
  - `AntiSpiderHelper`: 反检测处理
  - `RetryManager`: 自动重试机制
  - `LoginManager`: Cookie 管理和登录处理
  - `NavigationManager`: 页面导航
  - `TitleMatcher`: 标题匹配算法
  - `ArticleListExtractor`: 文章列表提取

#### 1.3 工具模块 (utils/)
- **文件**：`utils/*.py`
- **功能**：提供通用工具和服务，为整个系统提供技术支撑
- **核心模块**：
  - `platform_registry.py`: 平台注册与管理（单例），负责管理所有采集平台的注册、查询和状态管理
  - `data_model.py`: 标准字段定义和映射，定义了11个标准数据字段，并提供字段映射和归一化功能
  - `title_matcher.py`: 三级标题匹配算法，实现了精确匹配、清理匹配和标准化匹配三种匹配策略
  - `exposure_loader.py`: 静态曝光量加载（单例），从配置文件加载各平台的静态曝光量值
  - `feishu_worker.py`: 飞书导入独立线程，在后台异步执行飞书导入操作，避免阻塞主线程
  - `feishu_exporter.py`: 飞书 API 封装，提供飞书多维表格的批量写入和数据处理功能
  - `security.py`: 敏感数据加密存储，使用 Fernet 对称加密算法加密存储 Cookie 和其他敏感信息
  - `ops.py`: 日志轮转、健康检查、快照清理，负责系统运维相关的功能，如日志管理和异常快照处理
  - `path_helper.py`: 路径管理工具，提供项目路径的统一管理和路径生成功能

#### 1.4 GUI 模块 (gui.py)
- **文件**：`gui.py`
- **功能**：用户界面和交互逻辑
- **核心区域**：
  - 平台选择模块（10个平台复选框）
  - 标题/关键词输入模块（最多5条标题）
  - 执行控制模块（启动/暂停/停止）
  - 手动登录队列模块（登录失败平台）
  - 手动登录采集模块（有头浏览器模式）
  - 实时日志模块（QTextBrowser）
  - 数据结果模块（QTableWidget，11个标准字段）
  - 工具模块（导出CSV、导入飞书、清空数据）

#### 1.5 线程模块 (thread.py)
- **文件**：`thread.py`
- **功能**：管理采集子线程和 asyncio 事件循环
- **核心类**：`CrawlThread` (继承 QThread)
- **职责**：
  - 接收主线程信号
  - 在子线程中执行调度中心逻辑
  - 发射信号更新 GUI

### 2. 配置体系

#### 2.1 平台配置文件 (platforms/)
- **文件**：`platforms/{platform}.yaml`
- **功能**：定义各平台的选择器、URL、超时、重试等配置
- **核心配置**：
  - 登录选择器（username、password、login_button）
  - 文章列表选择器（table、row、field）
  - 分页配置（next_button、max_pages）
  - 超时和重试设置

#### 2.2 静态曝光量配置 (config/)
- **文件**：`config/exposure.yaml`
- **功能**：定义各平台的静态曝光量值
- **特点**：支持实时修改，重启程序生效

#### 2.3 环境变量 (.env)
- **文件**：`.env`
- **功能**：存储敏感信息，如飞书 API 凭证和平台账号密码
- **注意**：该文件不提交到代码仓库，使用 `.env.example` 作为模板

### 3. 数据存储

#### 3.1 加密存储 (cookies/)
- **文件**：`cookies/*.enc`
- **功能**：存储加密后的 Cookie 登录态
- **加密方式**：AES-128 + HMAC（Fernet 对称加密）

#### 3.2 数据导出 (data/)
- **文件**：`data/*.csv`
- **功能**：存储导出的 CSV 数据
- **格式**：UTF-8-BOM，包含 11 个标准字段

#### 3.3 日志和快照
- **文件**：`logs/*.log` 和 `snapshots/{platform}_时间戳/`
- **功能**：记录程序运行日志和异常快照
- **特点**：
  - 日志按天轮转，保留 30 天
  - 异常快照包含截图、DOM 和堆栈，保留 7 天

### 4. 文档说明

#### 4.1 技术文档
- **文件**：`docs/项目总技术文档.md`
- **内容**：项目架构、核心模块、执行流程、配置体系等详细技术说明

#### 4.2 运维文档
- **文件**：`docs/运维使用手册.md`
- **内容**：日常运维流程、平台改版应对策略、常见故障处理等

#### 4.3 平台改版应对
- **文件**：`docs/应对平台改版策略.md`
- **内容**：平台页面改版后的具体应对方法和操作指引

#### 4.4 打包交付文档
- **文件**：`docs/交付、打包、迁移、稳定运维 & 平台改版应对方案.md`
- **内容**：打包流程、交付目录结构、迁移方案等

#### 4.5 首次配置说明
- **文件**：`首次配置说明.txt`
- **内容**：浏览器内核部署步骤和首次使用配置指南

## ⚠️ 免责声明

**免责声明**：本项目仅供学习、技术研究使用，请勿用于商业用途或非法采集他人数据。使用者应遵守各平台用户协议及相关法律法规，一切违法违规后果由使用者自行承担。

## 📦 打包策略与技术实现

### 打包流程图

```mermaid
flowchart TB
    subgraph 接入层[接入层]
        Dev[开发环境<br/>源代码仓库]
        Build[构建环境<br/>虚拟环境]
    end

    subgraph 应用服务层[应用服务层]
        Check[打包前检查<br/>check_before_pack.py]
        Pack[打包执行<br/>build_exe.bat]
        Verify[产物验证<br/>功能测试]
    end

    subgraph 核心业务层[核心业务层]
        Core[核心模块打包<br/>main.py/gui.py/controller.py]
        Utils[工具模块打包<br/>utils/]
        External[外置模块分离<br/>collectors/platforms/config]
    end

    subgraph 数据持久层[数据持久层]
        Config[配置文件<br/>.env/exposure.yaml]
        Cookie[登录态存储<br/>cookies/]
        Data[数据导出<br/>data/]
        Log[日志记录<br/>logs/]
    end

    subgraph 基础设施层[基础设施层]
        Runtime[运行时环境<br/>Windows系统]
        Browser[浏览器内核<br/>ms-playwright]
    end

    subgraph 外部依赖层[外部依赖层]
        PyInstaller[PyInstaller<br/>打包工具]
        Playwright[Playwright<br/>浏览器自动化]
        PySide6[PySide6<br/>GUI框架]
    end

    Dev --> Build
    Build --> Check
    Check --> Pack
    Pack --> Core
    Pack --> Utils
    Pack --> External
    Core --> Verify
    Utils --> Verify
    External --> Verify
    Verify --> Runtime
    Runtime --> Browser
    Runtime --> Config
    Runtime --> Cookie
    Runtime --> Data
    Runtime --> Log

    Pack -.-> PyInstaller
    Core -.-> Playwright
    Core -.-> PySide6

    style Dev fill:#e6f7ff,stroke:#1890ff
    style Build fill:#e6f7ff,stroke:#1890ff
    style Check fill:#f0f2ff,stroke:#597ef7
    style Pack fill:#f0f2ff,stroke:#597ef7
    style Verify fill:#f0f2ff,stroke:#597ef7
    style Core fill:#fff1eb,stroke:#fa8c16
    style Utils fill:#fff1eb,stroke:#fa8c16
    style External fill:#fff1eb,stroke:#fa8c16
    style Config fill:#f6ffed,stroke:#52c41a
    style Cookie fill:#f6ffed,stroke:#52c41a
    style Data fill:#f6ffed,stroke:#52c41a
    style Log fill:#f6ffed,stroke:#52c41a
    style Runtime fill:#fff7e6,stroke:#faad14
    style Browser fill:#fff7e6,stroke:#faad14
    style PyInstaller fill:#fef0f0,stroke:#f5222d
    style Playwright fill:#fef0f0,stroke:#f5222d
    style PySide6 fill:#fef0f0,stroke:#f5222d
```

### 1. 打包的意义

#### 业务场景意义
- **降低使用门槛**：非技术人员无需安装 Python 环境和依赖，双击即可运行
- **简化部署流程**：避免了复杂的环境配置和依赖安装步骤
- **提高稳定性**：打包后环境隔离，减少外部因素干扰
- **便于分发**：可作为独立交付物分发给不同用户使用

#### 技术解决思路
- **路径动态化**：使用动态路径处理，避免硬编码路径问题
- **模块分离**：将核心代码与易变配置分离，提高维护效率
- **运行时自适应**：自动检测运行环境，适配开发和打包两种模式

### 2. 打包策略

#### 核心原则
- **完整保留原始目录结构**：不拆分、不移动、不重命名任何文件夹
- **动态根目录**：全局使用 `PROJECT_ROOT` 动态确定根目录
- **硬编码禁止**：代码中不得出现任何绝对路径
- **固定代码全打包**：核心模块打包进 exe，避免频繁修改
- **易变模块外置**：采集器代码、平台配置、账号密码配置外置

#### 具体实现
- **动态路径处理**：在 `utils/path_helper.py` 中实现 `get_project_root()` 函数，根据运行环境动态确定根目录
- **模块分离策略**：
  - 打包进 exe：`main.py`, `gui.py`, `controller.py`, `thread.py`, `utils/` 等核心模块
  - 外置文件：`collectors/`（采集器代码）、`platforms/`（YAML 配置）、`.env`（账号密码配置）、`config/exposure.yaml`（曝光量配置）
- **运行时目录自动创建**：程序启动时自动创建 `cookies/`, `data/`, `logs/`, `snapshots/` 等运行时目录

### 3. 打包效果与优点

#### 运行效果
- **整文件夹迁移**：可复制整个交付文件夹到任意位置（桌面、D盘、U盘等），双击 exe 即可运行
- **零配置启动**：无需额外配置，程序自动读取外置文件和创建必要目录
- **跨环境兼容**：在不同 Windows 系统上表现一致，不受环境变量影响
- **登录态保留**：Cookie 存储在 `cookies/` 目录，随文件夹一起迁移，无需重新登录

#### 技术优点
- **平台改版快速响应**：仅需修改外置的 YAML 配置文件，无需重新打包
- **运维成本降低**：非技术人员可通过修改外置文件应对平台改版
- **故障排查便捷**：日志按天分割，异常自动保存快照（截图+DOM+堆栈）
- **安全性提升**：账号密码等敏感信息存储在外置 `.env` 文件，不随 exe 打包

### 4. 关键技术实现

#### 动态路径处理
```python
# utils/path_helper.py
def get_project_root() -> Path:
    """返回项目根目录（开发环境返回当前工作目录，打包环境返回exe所在目录）"""
    if getattr(sys, 'frozen', False):
        # 打包后运行：sys.executable 是 exe 的完整路径
        return Path(sys.executable).parent
    else:
        # 开发环境：返回当前工作目录（通常是项目根目录）
        return Path.cwd()

# 全局常量，供其他模块导入
PROJECT_ROOT = get_project_root()
```

#### 外置模块动态导入
```python
# utils/module_loader.py
def load_collector(platform_name: str):
    """从外置 collectors 目录动态加载采集器类"""
    collector_path = PROJECT_ROOT / "collectors" / f"{platform_name}.py"
    if not collector_path.exists():
        raise FileNotFoundError(f"Collector not found: {collector_path}")
    
    spec = importlib.util.spec_from_file_location(platform_name, collector_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[platform_name] = module
    spec.loader.exec_module(module)
    
    # 约定采集器类名为 {PlatformName}Collector，首字母大写驼峰
    class_name = ''.join(part.capitalize() for part in platform_name.split('_')) + "Collector"
    collector_class = getattr(module, class_name)
    return collector_class()
```

## 📖 使用方式

### 1. 下载与安装
- **打包版本**：
  - 在 GitHub 仓库页面点击 "Releases" 下载最新版本的完整压缩包
  - 压缩包包含完整的 `crawler_system/` 目录结构
  - 无需安装 Python 环境，解压后双击 `MultiPlatformCrawler.exe` 即可运行

- **浏览器内核**：
  - 在 GitHub 仓库 "Releases" 页面下载 `ms-playwright` 浏览器压缩包
  - 若目标机器没有 Python 环境，需下载并解压 `ms-playwright` 浏览器压缩包
  - 将解压后的 `ms-playwright` 文件夹复制到 `%LOCALAPPDATA%\ms-playwright\` 目录

### 2. 目录结构（打包版本）
```
crawler_system/                     # 根文件夹（可重命名，可任意放置）
├─ .env.example                          # 账号密码配置文件【配置时候需要修改为：.env文件】
├─ MultiPlatformCrawler.exe       # 主程序
├─ collectors/                    # 采集器代码
├─ platforms/                     # 平台配置文件
├─ config/                        # 运维配置
│   └─ exposure.yaml              # 曝光量配置
├─ cookies/                       # 运行时生成（登录态）
├─ data/                          # 运行时生成（CSV导出）
├─ logs/                          # 运行时生成（日志）
└─ snapshots/                     # 运行时生成（异常快照）
```

### 3. 首次配置
1. 下载并解压完整的 `crawler_system.zip` 压缩包
2. 复制 `crawler_system/` 文件夹到任意位置
3. 打开 `crawler_system/` 文件夹，将 `.env.example` 复制并重命名为 `.env` 文件
4. 填写 `.env` 文件中的账号密码等配置
5. 双击 `MultiPlatformCrawler.exe` 启动程序

### 4. 运行流程
1. **平台选择**：在左侧勾选需要采集的平台（至少选择 1 个）
2. **标题输入**：在标题输入区域填写需要采集的文章标题（最多 5 条）
3. **开始采集**：点击 "启动采集" 按钮
4. **查看结果**：采集完成后，结果会显示在数据表格中
5. **数据导出**：可选择 "导出 CSV" 或 "导入飞书"

### 5. 常见问题
- **启动失败**：检查 `ms-playwright` 浏览器内核是否正确配置
- **登录失败**：检查账号密码是否正确，或使用手动登录模式
- **采集无数据**：检查平台选择器配置是否需要更新（修改 `platforms/*.yaml`）

## �📦 打包与部署

### 打包步骤
1. **准备环境**：确保已安装 Python 3.11 和所有依赖（`pip install -r requirements.txt`）
2. **执行打包脚本**：运行 `build_exe.bat` 脚本
3. **生成位置**：打包后的文件将生成在 `D:\crawling_github\PythonProject5\multi_platform_crawler\dist` 目录
4. **交付准备**：将 `dist` 目录中的文件按照打包后目录结构组织成交付包

### 详细打包指南
项目提供了详细的打包步骤指导文档，包含 PowerShell 和 CMD 两套可执行的命令序列：
- **文件**：`docs/项目打包步骤.md`
- **内容**：包含环境说明、删除旧文件、激活虚拟环境、打包前检查、执行打包、确认产物位置、交付目录要求等完整步骤
- **适用场景**：适用于需要详细打包操作指导的用户

### 配置修改与重新打包
- **不需要重新打包的情况**：
  - 修改采集器配置文件（`platforms/*.yaml`）
  - 修改曝光量配置（`config/exposure.yaml`）
  - 修改环境变量（`.env`）
  - 更新采集器代码（`collectors/*.py`）
- **需要重新打包的情况**：
  - 修改核心模块代码（如 `main.py`、`gui.py`、`controller.py` 等）
  - 添加新的依赖包
  - 修改打包脚本或配置

## �� 许可证

[MIT](LICENSE)

---

**感谢使用多平台内容采集系统！** 🎉  
如有问题或建议，欢迎联系项目维护人员。