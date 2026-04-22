"""
===============================================================
GUI界面模块
多平台内容采集系统 - PySide6实现
8模块上下分行结构
===============================================================
"""

from typing import Dict, List

from utils.path_helper import DATA_DIR
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QCheckBox, QPushButton, QLineEdit, QLabel,
    QTextBrowser, QTableWidget, QTableWidgetItem,
    QGroupBox, QScrollArea, QMessageBox, QFileDialog,
    QButtonGroup, QProgressBar, QListWidget, QListWidgetItem,
    QSizePolicy,
)
from PySide6.QtCore import Signal, Qt, QTimer
from PySide6.QtGui import QColor, QFont

# 最终补采模块：置顶三项（问题较多平台优先展示）；其余平台在注册表顺序中列出
_PINNED_MANUAL_COLLECT = (
    ("wechat", "微信公众号"),
    ("qq", "企鹅号"),
    ("xueqiu", "雪球号"),
)

# 平台信息配置
PLATFORMS_INFO = {
    "weibo": "微博",
    "toutiao": "头条号",
    "zhihu": "知乎号",
    "baijiahao": "百家号",
    "netease": "网易号",
    "qq": "企鹅号",
    "yidian": "一点资讯号",
    "xueqiu": "雪球号",
    "zaker": "ZAKER号",
}

# 标准字段（按COLLECTOR_STRATEGY.md标准顺序）
STANDARD_COLUMNS = [
    "发布日期",
    "文章标题",
    "发布平台",
    "发布链接",
    "曝光量",
    "阅读量",
    "推荐",
    "评论量",
    "点赞量",
    "转发量",
    "收藏量",
]


class MainWindow(QWidget):
    """
    主窗口
    8模块上下分行结构
    """

    # 信号定义
    start_signal = Signal(list, list, list)  # 启动信号 (platforms, targets, keywords)
    pause_signal = Signal()           # 暂停信号
    resume_signal = Signal()           # 恢复信号
    stop_signal = Signal()             # 停止信号
    manual_login_signal = Signal(str) # 手动登录信号 (platform)
    export_signal = Signal()           # 导出信号
    def __init__(self):
        super().__init__()
        self.setWindowTitle("多平台内容采集系统")
        self.setMinimumSize(1200, 800)

        # 状态标志（必须在_init_ui之前初始化）
        self.is_running = False
        self.is_paused = False
        # 数据存储
        self.platform_data: List[Dict] = []
        self.unmatched_by_platform: Dict[str, List[str]] = {}  # {平台名: [标题列表]}

        # 初始化UI
        self._init_ui()

    def _init_ui(self):
        """初始化UI布局"""
        # 创建滚动区域作为主容器
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)  # 禁用水平滚动
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)     # 垂直滚动按需显示
        scroll_area.setFrameShape(QScrollArea.NoFrame)                   # 无边框

        # 创建滚动内容容器
        scroll_content = QWidget()
        main_layout = QVBoxLayout(scroll_content)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(10, 10, 10, 10)

        # 模块1: 平台选择模块
        self._create_platform_selector(main_layout)

        # 模块2: 标题/关键词输入模块
        self._create_input_module(main_layout)

        # 模块3: 执行控制模块
        self._create_control_module(main_layout)

        # 模块4: 手动登录平台（自动登录失败队列）
        self._create_manual_login_module(main_layout)

        # 模块5: 最终补采（可折叠，有头浏览器兜底）
        self._create_manual_collect_module(main_layout)

        # 模块6: 实时日志模块
        self._create_log_module(main_layout)

        # 模块7: 采集数据结果模块
        self._create_data_module(main_layout)

        # 模块8: 工具模块
        self._create_tool_module(main_layout)

        # 添加弹性空间，让内容顶部对齐
        main_layout.addStretch()

        # 设置滚动内容
        scroll_area.setWidget(scroll_content)

        # 将滚动区域设置为主窗口的中心部件
        self_layout = QVBoxLayout(self)
        self_layout.setContentsMargins(0, 0, 0, 0)
        self_layout.addWidget(scroll_area)

        # 初始化启动按钮状态（所有模块创建完成后）
        self._update_start_button_state()

    def _create_platform_selector(self, parent_layout):
        """模块1: 平台选择模块（动态加载）"""
        group = QGroupBox("1. 采集平台选择")
        group.setStyleSheet("""
            QGroupBox {
                border: 1px solid #a0c4e8;
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 10px;
                font-weight: bold;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 8px;
                background-color: #e6f2ff;
                border-radius: 4px;
                color: #1a3a5c;
            }
        """)
        layout = QVBoxLayout()

        # 平台选择区域（滚动）
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(80)

        scroll_widget = QWidget()
        scroll_layout = QHBoxLayout(scroll_widget)
        scroll_layout.setSpacing(15)

        # ========== 核心修改：从注册中心动态加载平台 ==========
        from utils.platform_registry import get_platform_registry
        registry = get_platform_registry()

        # 获取所有稳定平台（按顺序）
        stable_platforms = registry.get_stable_platforms()

        # 创建平台复选框
        self.platform_checkboxes: Dict[str, QCheckBox] = {}
        self.platform_group = QButtonGroup(self)
        self.platform_group.setExclusive(False)

        for platform_id in stable_platforms:
            # 获取平台显示名称
            display_name = registry.get_platform_display_name(platform_id)

            checkbox = QCheckBox(display_name)
            checkbox.setObjectName(platform_id)
            checkbox.setToolTip(f"平台ID: {platform_id}")

            self.platform_checkboxes[platform_id] = checkbox
            self.platform_group.addButton(checkbox)
            scroll_layout.addWidget(checkbox)

            # 连接信号（在此处连接，避免在_input_module中连接时checkbox已被回收）
            checkbox.stateChanged.connect(self._on_platform_selection_changed)

        # 设置滚动区域的内容
        scroll.setWidget(scroll_widget)

        # 初始化启动按钮状态（无平台选中时禁用）
        self._update_start_button_state()

        # 全选/全不选按钮
        btn_layout = QHBoxLayout()
        select_all_btn = QPushButton("全选")
        select_all_btn.clicked.connect(self._select_all_platforms)
        deselect_all_btn = QPushButton("全不选")
        deselect_all_btn.clicked.connect(self._deselect_all_platforms)
        btn_layout.addWidget(select_all_btn)
        btn_layout.addWidget(deselect_all_btn)
        btn_layout.addStretch()

        # 显示已注册平台数量
        platform_count_label = QLabel(f"已注册稳定平台: {len(stable_platforms)} 个")
        platform_count_label.setStyleSheet("color: #666; font-size: 11px;")
        btn_layout.addWidget(platform_count_label)

        layout.addWidget(scroll)
        layout.addLayout(btn_layout)
        group.setLayout(layout)
        parent_layout.addWidget(group)

    def _create_input_module(self, parent_layout):
        """模块2: 标题/关键词输入模块"""
        group = QGroupBox("2. 标题/关键词输入")
        group.setStyleSheet("""
            QGroupBox {
                border: 1px solid #a0c4e8;
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 10px;
                font-weight: bold;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 8px;
                background-color: #e6f2ff;
                border-radius: 4px;
                color: #1a3a5c;
            }
        """)
        layout = QGridLayout()
        layout.setSpacing(5)

        # 标题输入（5个），每个标题栏下方独立显示专属关键词输入框
        self.title_labels: List[QLabel] = []
        self.title_inputs: List[QLineEdit] = []
        self.keyword_inputs: List[QLineEdit] = []  # 每个标题对应的专属关键词输入框
        self.keyword_widgets: List[QWidget] = []   # 关键词输入框容器（用于显示/隐藏）

        layout.addWidget(QLabel("目标标题 (最多5个):"), 0, 0)

        for i in range(5):
            row = i * 2  # 每个标题占2行：标题行 + 关键词行

            # 标题输入行
            h_layout = QHBoxLayout()
            line_edit = QLineEdit()
            line_edit.setPlaceholderText(f"标题 {i+1}")
            line_edit.textChanged.connect(self._on_title_changed)
            line_edit.setObjectName(f"title_{i}")
            line_edit.setReadOnly(False)
            line_edit.setEnabled(True)
            self.title_inputs.append(line_edit)
            h_layout.addWidget(line_edit)

            # 字符数提示标签
            char_label = QLabel("0字")
            char_label.setStyleSheet("color: #999; font-size: 11px;")
            char_label.setMinimumWidth(40)
            self.title_labels.append(char_label)
            h_layout.addWidget(char_label)
            layout.addLayout(h_layout, row, 1)

            # 专属关键词输入框（默认隐藏，仅当标题>30字且选中微博/头条时显示）
            keyword_widget = QWidget()
            keyword_layout = QHBoxLayout()
            keyword_layout.setContentsMargins(20, 0, 0, 0)  # 左侧缩进，与标题对齐

            keyword_label = QLabel(f"关键词{i+1}:")
            keyword_label.setStyleSheet("color: #e65100; font-size: 11px;")
            keyword_layout.addWidget(keyword_label)

            keyword_input = QLineEdit()
            keyword_input.setPlaceholderText(f"标题{i+1}专属关键词（多个用空格分隔）")
            keyword_input.setReadOnly(False)
            keyword_input.setEnabled(True)
            self.keyword_inputs.append(keyword_input)
            keyword_layout.addWidget(keyword_input)

            keyword_hint = QLabel("(微博/头条检索用)")
            keyword_hint.setStyleSheet("color: #999; font-size: 10px;")
            keyword_layout.addWidget(keyword_hint)

            keyword_widget.setLayout(keyword_layout)
            keyword_widget.setVisible(False)  # 默认隐藏
            self.keyword_widgets.append(keyword_widget)
            layout.addWidget(keyword_widget, row + 1, 1)

        # 平台选择提示（用于检测是否需要显示关键词）
        self.platform_selection_label = QLabel("")
        self.platform_selection_label.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(self.platform_selection_label, 10, 0, 1, 8)

        group.setLayout(layout)
        parent_layout.addWidget(group)

    def _create_control_module(self, parent_layout):
        """模块3: 执行控制模块"""
        group = QGroupBox("3. 执行控制")
        group.setStyleSheet("""
            QGroupBox {
                border: 1px solid #a0c4e8;
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 10px;
                font-weight: bold;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 8px;
                background-color: #e6f2ff;
                border-radius: 4px;
                color: #1a3a5c;
            }
        """)
        layout = QHBoxLayout()

        self.start_btn = QPushButton("启动采集")
        self.start_btn.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                padding: 10px 20px;
                font-size: 14px;
                font-weight: bold;
                border: none;
                border-radius: 4px;
            }
            QPushButton:hover { background-color: #45a049; }
            QPushButton:disabled { background-color: #cccccc; }
        """)
        self.start_btn.clicked.connect(self._on_start_clicked)

        self.pause_btn = QPushButton("暂停")
        self.pause_btn.setStyleSheet("""
            QPushButton {
                background-color: #FF9800;
                color: white;
                padding: 10px 20px;
                font-size: 14px;
                border: none;
                border-radius: 4px;
            }
            QPushButton:hover { background-color: #e68a00; }
            QPushButton:disabled { background-color: #cccccc; }
        """)
        self.pause_btn.clicked.connect(self._on_pause_clicked)
        self.pause_btn.setEnabled(False)

        self.stop_btn = QPushButton("停止")
        self.stop_btn.setStyleSheet("""
            QPushButton {
                background-color: #f44336;
                color: white;
                padding: 10px 20px;
                font-size: 14px;
                border: none;
                border-radius: 4px;
            }
            QPushButton:hover { background-color: #da190b; }
            QPushButton:disabled { background-color: #cccccc; }
        """)
        self.stop_btn.clicked.connect(self._on_stop_clicked)
        self.stop_btn.setEnabled(False)

        # 进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximumWidth(300)
        self.progress_bar.setFormat("%v/%m (%p%)")

        layout.addWidget(self.start_btn)
        layout.addWidget(self.pause_btn)
        layout.addWidget(self.stop_btn)
        layout.addWidget(QLabel("进度:"))
        layout.addWidget(self.progress_bar)
        layout.addStretch()

        group.setLayout(layout)
        parent_layout.addWidget(group)

    def _create_manual_login_module(self, parent_layout):
        """模块4: 自动采集失败后的手动登录补采队列"""
        group = QGroupBox("4. 手动登录平台")
        group.setStyleSheet("""
            QGroupBox {
                border: 1px solid #a0c4e8;
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 10px;
                font-weight: bold;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 8px;
                background-color: #e6f2ff;
                border-radius: 4px;
                color: #1a3a5c;
            }
        """)
        layout = QVBoxLayout()

        manual_login_hint = QLabel(
            "正常自动采集未登录成功的平台会出现在下方。请单选一个平台，点击【手动登录采集】，"
            "弹出浏览器后于5分钟内完成登录，采集完成后浏览器将自动关闭，数据会写入下方主表格。"
        )
        manual_login_hint.setWordWrap(True)
        hint_font = QFont(manual_login_hint.font())
        if hint_font.pointSize() > 0:
            hint_font.setPointSize(max(hint_font.pointSize() - 1, 8))
        else:
            hint_font.setPixelSize(max(hint_font.pixelSize() - 1, 11))
        manual_login_hint.setFont(hint_font)
        manual_login_hint.setStyleSheet("color: #757575;")
        layout.addWidget(manual_login_hint)

        row = QHBoxLayout()
        left_layout = QVBoxLayout()
        self.manual_login_list = QListWidget()
        self.manual_login_list.setMinimumHeight(130)
        self.manual_login_list.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding
        )
        self.manual_login_list.setSelectionMode(QListWidget.SingleSelection)
        self.manual_login_list.setStyleSheet("""
            QListWidget {
                background-color: #ffe6e6;
                font-weight: bold;
                color: black;
            }
            QListWidget::item {
                background-color: #ffe6e6;
                color: black;
                font-weight: bold;
            }
            QListWidget::item:selected {
                background-color: #ffb3b3;
                color: black;
            }
        """)
        left_layout.addWidget(self.manual_login_list, stretch=1)

        btn_layout = QVBoxLayout()
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        self.manual_login_btn = QPushButton("手动登录采集")
        self.manual_login_btn.setMinimumSize(120, 32)
        self.manual_login_btn.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                font-weight: bold;
                border-radius: 5px;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
            QPushButton:disabled {
                background-color: #cccccc;
            }
        """)
        self.manual_login_btn.clicked.connect(self._on_manual_login_clicked)

        self.remove_manual_btn = QPushButton("撤销该平台")
        self.remove_manual_btn.setMinimumSize(120, 32)
        self.remove_manual_btn.setStyleSheet("""
            QPushButton {
                background-color: #f44336;
                color: white;
                font-weight: bold;
                border-radius: 3px;
            }
            QPushButton:hover {
                background-color: #da190b;
            }
        """)
        self.remove_manual_btn.clicked.connect(self._on_remove_manual_clicked)

        btn_row.addWidget(self.manual_login_btn)
        btn_row.addWidget(self.remove_manual_btn)
        btn_layout.addLayout(btn_row)
        btn_layout.addStretch()

        row.addLayout(left_layout, stretch=1)
        row.addLayout(btn_layout)
        layout.addLayout(row)

        group.setLayout(layout)
        parent_layout.addWidget(group)

    def _create_manual_collect_module(self, parent_layout):
        """模块5: 最终补采（可折叠，有头浏览器）"""
        group = QGroupBox("5. 最终补采【手动登录，启动浏览器界面】")
        group.setCheckable(True)
        group.setChecked(False)  # 默认折叠
        group.setStyleSheet("""
            QGroupBox {
                font-weight: bold;
                border: 2px solid #4CAF50;
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
                color: #4CAF50;
                background-color: #e6f9f0;
                border-radius: 4px;
            }
            QGroupBox::indicator {
                width: 13px;
                height: 13px;
            }
        """)

        layout = QVBoxLayout()
        layout.setSpacing(10)

        # 说明标签
        hint_label = QLabel(
            "本模块用于最终主动补采。当自动采集时某平台登录失败且未出现在左侧「4. 手动登录平台」列表中（极少数情况），"
            "可在此处主动选择平台并以有头浏览器模式运行。仅微信公众号、企鹅号（QQ号）、雪球号需要手动扫码登录；"
            "其余8个平台亦可在此兜底补采。总之，此模块为采集流程的最终兜底保障。"
        )
        hint_label.setWordWrap(True)
        hint_label.setStyleSheet("color: #666; font-size: 12px;")
        layout.addWidget(hint_label)

        # 平台列表（单选）
        platform_layout = QHBoxLayout()

        # 左侧：平台选择列表
        left_layout = QVBoxLayout()
        left_layout.addWidget(QLabel("选择平台:"))

        self.manual_collect_list = QListWidget()
        self.manual_collect_list.setMaximumHeight(150)
        self.manual_collect_list.setSelectionMode(QListWidget.SingleSelection)

        # 按置顶顺序 + 注册顺序添加平台
        self._populate_manual_collect_platforms()

        left_layout.addWidget(self.manual_collect_list)
        platform_layout.addLayout(left_layout)

        # 右侧：操作按钮
        right_layout = QVBoxLayout()
        right_layout.addStretch()

        self.manual_collect_btn = QPushButton("手动登录并采集")
        self.manual_collect_btn.setMinimumHeight(50)
        self.manual_collect_btn.setStyleSheet("""
            QPushButton {
                background-color: #FF9800;
                color: white;
                font-weight: bold;
                font-size: 14px;
                border-radius: 5px;
                padding: 10px 20px;
            }
            QPushButton:hover {
                background-color: #e68a00;
            }
            QPushButton:disabled {
                background-color: #cccccc;
            }
        """)
        self.manual_collect_btn.clicked.connect(self._on_manual_collect_clicked)

        right_layout.addWidget(self.manual_collect_btn)
        right_layout.addStretch()

        platform_layout.addLayout(right_layout)
        layout.addLayout(platform_layout)

        # 超时提示
        timeout_label = QLabel(
            "须至少一条目标标题。整任务自动采集中会先暂停队列，本操作结束后再恢复。"
        )
        timeout_label.setWordWrap(True)
        timeout_label.setStyleSheet("color: #999; font-size: 11px;")
        layout.addWidget(timeout_label)

        group.setLayout(layout)
        parent_layout.addWidget(group)

    def _populate_manual_collect_platforms(self):
        """置顶微信/企鹅/雪球，其余平台按注册表补全。"""
        from utils.platform_registry import get_platform_registry

        registry = get_platform_registry()
        all_platforms = registry.get_all_platforms()
        added: set = set()

        for platform_id, display_name in _PINNED_MANUAL_COLLECT:
            if platform_id not in all_platforms:
                continue
            item = QListWidgetItem(f"[置顶] {display_name}")
            item.setData(Qt.UserRole, platform_id)
            item.setForeground(QColor("#FF9800"))
            font = item.font()
            font.setBold(True)
            item.setFont(font)
            self.manual_collect_list.addItem(item)
            added.add(platform_id)

        if self.manual_collect_list.count() > 0:
            separator = QListWidgetItem("─" * 30)
            separator.setFlags(Qt.NoItemFlags)
            separator.setForeground(QColor("#ccc"))
            self.manual_collect_list.addItem(separator)

        for platform_id, info in all_platforms.items():
            if platform_id not in added:
                item = QListWidgetItem(info.display_name)
                item.setData(Qt.UserRole, platform_id)
                self.manual_collect_list.addItem(item)

    def _create_log_module(self, parent_layout):
        """模块6: 实时日志模块"""
        group = QGroupBox("6. 实时日志")
        group.setStyleSheet("""
            QGroupBox {
                border: 1px solid #a0c4e8;
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 10px;
                font-weight: bold;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 8px;
                background-color: #e6f2ff;
                border-radius: 4px;
                color: #1a3a5c;
            }
        """)
        layout = QVBoxLayout()

        self.log_browser = QTextBrowser()
        self.log_browser.setMaximumHeight(150)
        self.log_browser.setStyleSheet("""
            QTextBrowser {
                background-color: #1e1e1e;
                color: #00ff00;
                font-family: Consolas, monospace;
                font-size: 11px;
            }
        """)

        # 清空日志按钮
        clear_log_btn = QPushButton("清空日志")
        clear_log_btn.clicked.connect(self.log_browser.clear)

        btn_layout = QHBoxLayout()
        btn_layout.addWidget(clear_log_btn)
        btn_layout.addStretch()

        layout.addWidget(self.log_browser)
        layout.addLayout(btn_layout)

        group.setLayout(layout)
        parent_layout.addWidget(group)

    def _create_data_module(self, parent_layout):
        """模块7: 采集数据结果模块"""
        group = QGroupBox("7. 采集数据结果")
        group.setStyleSheet("""
            QGroupBox {
                border: 1px solid #a0c4e8;
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 10px;
                font-weight: bold;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 8px;
                background-color: #e6f2ff;
                border-radius: 4px;
                color: #1a3a5c;
            }
        """)
        layout = QVBoxLayout()

        # 数据表格
        self.data_table = QTableWidget()
        self.data_table.setColumnCount(len(STANDARD_COLUMNS))
        self.data_table.setHorizontalHeaderLabels(STANDARD_COLUMNS)
        self.data_table.setMinimumHeight(250)  # 设置最小高度，使表格更大
        self.data_table.setStyleSheet("""
            QTableWidget {
                gridline-color: #d0d0d0;
                background-color: #d1e7fe;
                alternate-background-color: #1e3a5f;
            }
            QTableWidget::item {
                color: black;
            }
            QTableWidget::item:alternate {
                background-color: #1e3a5f;
                color: white;
            }
            QHeaderView::section {
                background-color: #f0f0f0;
                color: black;
                padding: 5px;
                border: 1px solid #d0d0d0;
                font-weight: bold;
            }
            QTableWidget::item:selected {
                background-color: #0078d4;
                color: white;
            }
        """)
        self.data_table.setAlternatingRowColors(True)
        self.data_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.data_table.setSelectionBehavior(QTableWidget.SelectRows)

        layout.addWidget(self.data_table)

        # 未匹配数据列表
        unmatched_layout = QHBoxLayout()
        unmatched_layout.addWidget(QLabel("未匹配标题:"))
        self.unmatched_list = QListWidget()
        self.unmatched_list.setMaximumHeight(60)
        unmatched_layout.addWidget(self.unmatched_list)

        layout.addLayout(unmatched_layout)

        group.setLayout(layout)
        parent_layout.addWidget(group)

    def _create_tool_module(self, parent_layout):
        """模块8: 工具模块"""
        group = QGroupBox("8. 工具")
        group.setStyleSheet("""
            QGroupBox {
                border: 1px solid #a0c4e8;
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 10px;
                font-weight: bold;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 8px;
                background-color: #e6f2ff;
                border-radius: 4px;
                color: #1a3a5c;
            }
        """)
        layout = QHBoxLayout()

        self.export_btn = QPushButton("导出CSV (UTF-8 BOM)")
        self.export_btn.setStyleSheet("""
            QPushButton {
                background-color: #2196F3;
                color: white;
                padding: 8px 16px;
                border: none;
                border-radius: 4px;
            }
            QPushButton:hover { background-color: #0b7dda; }
            QPushButton:disabled { background-color: #cccccc; }
        """)
        self.export_btn.clicked.connect(self._on_export_clicked)

        # 导入飞书按钮（始终启用，程序启动后即可点击）
        self.import_feishu_btn = QPushButton("导入飞书")
        self.import_feishu_btn.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                padding: 8px 16px;
                border: none;
                border-radius: 4px;
            }
            QPushButton:hover { background-color: #45a049; }
            QPushButton:disabled { background-color: #cccccc; }
        """)
        self.import_feishu_btn.clicked.connect(self._on_import_feishu_clicked)

        self.clear_btn = QPushButton("清空数据")
        self.clear_btn.clicked.connect(self._on_clear_clicked)

        layout.addWidget(self.export_btn)
        layout.addWidget(self.import_feishu_btn)
        layout.addWidget(self.clear_btn)
        layout.addStretch()

        group.setLayout(layout)
        parent_layout.addWidget(group)

    # ==================== 事件处理 ====================

    def _select_all_platforms(self):
        """全选平台"""
        for checkbox in self.platform_checkboxes.values():
            checkbox.setChecked(True)

    def _deselect_all_platforms(self):
        """全不选平台"""
        for checkbox in self.platform_checkboxes.values():
            checkbox.setChecked(False)

    def _on_title_changed(self):
        """标题输入变化时更新字符数显示"""
        self._check_and_show_keyword_area()

    def _on_platform_selection_changed(self):
        """平台选择变化时检查是否需要显示关键词区域"""
        self._check_and_show_keyword_area()
        self._update_start_button_state()

    def _update_start_button_state(self):
        """更新启动按钮状态：至少选中一个平台才启用"""
        # 如果start_btn尚未创建，跳过
        if not hasattr(self, 'start_btn'):
            return
        has_selection = any(cb.isChecked() for cb in self.platform_checkboxes.values())
        # 仅在非运行状态时更新启动按钮
        if not self.is_running:
            self.start_btn.setEnabled(has_selection)

    def _check_and_show_keyword_area(self):
        """
        检查是否需要显示关键词输入区域
        规则：
        1. 仅当勾选【微博】或【头条】时触发字数校验
        2. 哪个标题栏>30字，就在该标题栏正下方显示专属关键词输入框
        3. 多个标题栏超字数：每个超字数栏下方独立显示对应的专属关键词输入框
        """
        # 检查是否选择了头条号或微博
        selected_toutiao_weibo = [
            pid for pid, cb in self.platform_checkboxes.items()
            if cb.isChecked() and pid in ["toutiao", "weibo"]
        ]
        has_toutiao_weibo = len(selected_toutiao_weibo) > 0

        # 逐个检查标题栏，显示/隐藏对应的专属关键词输入框
        long_title_count = 0
        long_title_indices = []

        for i, title_input in enumerate(self.title_inputs):
            text = title_input.text().strip()
            char_count = len(text)

            # 更新字符数显示
            if i < len(self.title_labels):
                if char_count > 30:
                    self.title_labels[i].setText(f"{char_count}字⚠️")
                    self.title_labels[i].setStyleSheet("color: #f44336; font-size: 11px; font-weight: bold;")
                    long_title_count += 1
                    long_title_indices.append(i)
                elif char_count > 0:
                    self.title_labels[i].setText(f"{char_count}字")
                    self.title_labels[i].setStyleSheet("color: #4CAF50; font-size: 11px;")
                else:
                    self.title_labels[i].setText("0字")
                    self.title_labels[i].setStyleSheet("color: #999; font-size: 11px;")

            # 显示/隐藏对应的专属关键词输入框
            # 条件：选中微博/头条 且 当前标题>30字
            if i < len(self.keyword_widgets):
                should_show = has_toutiao_weibo and char_count > 30
                self.keyword_widgets[i].setVisible(should_show)

        # 更新提示信息
        if has_toutiao_weibo:
            if long_title_count > 0:
                self.platform_selection_label.setText(
                    f"检测到 {long_title_count} 个标题超过30字，请在对应标题下方输入专属关键词（微博/头条检索用）"
                )
                self.platform_selection_label.setStyleSheet("color: #e65100; font-weight: bold; font-size: 12px;")
            else:
                self.platform_selection_label.setText(
                    "当前微博/头条标题均未超过30字，无需关键词"
                )
                self.platform_selection_label.setStyleSheet("color: #999; font-size: 11px;")
        else:
            self.platform_selection_label.setText(
                "未选择微博或头条平台，跳过字数校验"
            )
            self.platform_selection_label.setStyleSheet("color: #999; font-size: 11px;")

    def _validate_title_input(self):
        """校验标题输入（兼容旧版本）"""
        self._check_and_show_keyword_area()

    def _on_start_clicked(self):
        """启动按钮点击"""
        # 获取选中的平台
        selected_platforms = [
            platform_id for platform_id, checkbox in self.platform_checkboxes.items()
            if checkbox.isChecked()
        ]

        if not selected_platforms:
            QMessageBox.warning(self, "警告", "请至少选择一个平台")
            return

        # 检查是否选择了头条号或微博
        has_toutiao_weibo = any(
            pid in ["toutiao", "weibo"]
            for pid in selected_platforms
        )

        # 获取标题列表（保留位置信息），并绑定对应的专属关键词
        titles_with_index = []
        for i, input_widget in enumerate(self.title_inputs):
            text = input_widget.text().strip()
            if text:
                is_long_title = len(text) > 30

                # 获取该标题对应的专属关键词
                keyword_text = ""
                if i < len(self.keyword_inputs):
                    keyword_text = self.keyword_inputs[i].text().strip()

                # 如果是超字数标题且选择了微博/头条，必须有专属关键词
                if has_toutiao_weibo and is_long_title and not keyword_text:
                    QMessageBox.warning(
                        self, "警告",
                        f"标题{i+1}超过30字，请输入专属关键词用于微博/头条检索"
                    )
                    return

                titles_with_index.append({
                    "index": i,
                    "title": text,  # 原标题全程保留
                    "char_count": len(text),
                    "use_keyword": is_long_title and has_toutiao_weibo,
                    "keyword": keyword_text  # 该标题的专属关键词
                })

        if not titles_with_index:
            QMessageBox.warning(self, "警告", "请至少输入一个目标标题")
            return

        # 更新状态
        self.is_running = True
        self.is_paused = False
        self._update_button_state()

        # 发送启动信号：传递标题列表（每个标题已绑定专属关键词）
        # 格式: (platforms, titles_with_index, [])  # keywords参数保留但不再使用
        self.start_signal.emit(selected_platforms, titles_with_index, [])
        self.append_log(f"启动采集任务: 平台={selected_platforms}, 标题={len(titles_with_index)}个")

        # 日志显示超字数标题的关键词绑定信息
        for item in titles_with_index:
            if item["use_keyword"]:
                self.append_log(f"[关键词绑定] 标题{item['index']+1}: \"{item['title'][:20]}...\" → 关键词: \"{item['keyword']}\"")

    def _on_pause_clicked(self):
        """暂停/恢复按钮点击"""
        if self.is_paused:
            self.resume_signal.emit()
            self.pause_btn.setText("暂停")
            self.is_paused = False
            self.append_log("任务已恢复")
        else:
            self.pause_signal.emit()
            self.pause_btn.setText("恢复")
            self.is_paused = True
            self.append_log("任务已暂停")

    def _on_stop_clicked(self):
        """停止按钮点击"""
        reply = QMessageBox.question(
            self, "确认", "确定要停止采集任务吗?",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self.stop_signal.emit()
            self.is_running = False
            self._update_button_state()
            self.append_log("任务已停止")

    def _on_export_clicked(self):
        """导出CSV"""
        if not self.platform_data:
            QMessageBox.warning(self, "警告", "没有数据可导出")
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self, "导出CSV", str(DATA_DIR / "采集数据.csv"), "CSV Files (*.csv)"
        )

        if file_path:
            try:
                import csv
                # UTF-8 BOM
                with open(file_path, 'w', newline='', encoding='utf-8-sig') as f:
                    writer = csv.DictWriter(f, fieldnames=STANDARD_COLUMNS)
                    writer.writeheader()
                    for row in self.platform_data:
                        # 字段名映射：旧字段名 -> 新字段名
                        def get_field(field_name):
                            value = row.get(field_name)
                            if value is not None and value != "":
                                return value
                            old_name_map = {
                                'read': 'read_count',
                                'like': 'like_count',
                                'comment': 'comment_count',
                                'forward': 'share_count',
                                'collect': 'collect_count',
                                'exposure': 'exposure_count',
                            }
                            old_name = old_name_map.get(field_name)
                            if old_name:
                                return row.get(old_name, "/")
                            return "/"

                        # 映射字段（按标准字段顺序）
                        row_data = {
                            "发布日期": row.get("publish_time", "/"),
                            "文章标题": row.get("title", "/"),
                            "发布平台": row.get("platform", "/"),
                            "发布链接": row.get("url", "/"),
                            "曝光量": get_field("exposure"),
                            "阅读量": get_field("read"),
                            "推荐": "/",  # 固定填充
                            "评论量": get_field("comment"),
                            "点赞量": get_field("like"),
                            "转发量": get_field("forward"),
                            "收藏量": get_field("collect"),
                        }
                        writer.writerow(row_data)

                QMessageBox.information(self, "成功", f"数据已导出到:\n{file_path}")
                self.append_log(f"数据已导出: {file_path}")
            except Exception as e:
                QMessageBox.critical(self, "错误", f"导出失败: {str(e)}")

    def _on_clear_clicked(self):
        """清空数据"""
        reply = QMessageBox.question(
            self, "确认", "确定要清空所有数据吗?",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self.platform_data.clear()
            self.unmatched_by_platform.clear()
            self.data_table.setRowCount(0)
            self.unmatched_list.clear()
            self.append_log("数据已清空")

    def _on_import_feishu_clicked(self):
        """点击导入飞书按钮后的处理"""
        if not self.platform_data:
            QMessageBox.information(self, "提示", "没有数据可导入")
            return

        reply = QMessageBox.question(
            self, "确认导入",
            f"即将导入 {len(self.platform_data)} 条记录到飞书表格，是否继续？\n\n温馨提示：请确认您已完成所有平台的数据采集，本次将导入当前已采集的全部数据",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        # 禁用按钮，防止重复点击
        self.import_feishu_btn.setEnabled(False)

        # 启动独立工作线程
        from utils.feishu_worker import FeishuImportWorker
        self.feishu_worker = FeishuImportWorker(self.platform_data)
        self.feishu_worker.log_signal.connect(self.append_log)
        self.feishu_worker.finished_signal.connect(self._on_import_finished)
        self.feishu_worker.start()

    def _on_import_finished(self, success: bool, message: str):
        """导入完成回调（主线程）"""
        self.import_feishu_btn.setEnabled(True)
        if success:
            QMessageBox.information(self, "完成", message)
        else:
            QMessageBox.critical(self, "导入失败", f"错误：{message}")
        self.append_log(f"飞书导入结果: {message}")

    def _update_button_state(self):
        """更新按钮状态"""
        self.start_btn.setEnabled(not self.is_running)
        self.pause_btn.setEnabled(self.is_running)
        self.stop_btn.setEnabled(self.is_running)

    # ==================== 公共接口 ====================

    def append_log(self, message: str):
        """
        添加日志消息

        Args:
            message: 日志消息
        """
        self.log_browser.append(message)
        # 自动滚动到底部
        self.log_browser.verticalScrollBar().setValue(
            self.log_browser.verticalScrollBar().maximum()
        )

    def add_data(self, data: Dict):
        """
        添加单条数据

        Args:
            data: 数据字典
        """
        self.platform_data.append(data)

        # 添加到表格（按标准字段顺序）
        row = self.data_table.rowCount()
        self.data_table.insertRow(row)

        # 字段名映射：旧字段名 -> 新字段名
        def get_field(field_name):
            # 尝试新字段名
            value = data.get(field_name)
            if value is not None and value != "":
                return value
            # 尝试旧字段名映射
            old_name_map = {
                'read': 'read_count',
                'like': 'like_count',
                'comment': 'comment_count',
                'forward': 'share_count',
                'collect': 'collect_count',
                'exposure': 'exposure_count',
            }
            old_name = old_name_map.get(field_name)
            if old_name:
                return data.get(old_name, "/")
            return "/"

        values = [
            data.get("publish_time", "/"),
            data.get("title", "/"),
            data.get("platform", "/"),
            data.get("url", "/"),
            get_field("exposure"),
            get_field("read"),
            "/",  # recommend 固定填充
            get_field("comment"),
            get_field("like"),
            get_field("forward"),
            get_field("collect"),
        ]

        for col, value in enumerate(values):
            item = QTableWidgetItem(str(value))
            self.data_table.setItem(row, col, item)

    def add_unmatched(self, platform: str, title: str):
        """
        按平台添加未匹配标题

        Args:
            platform: 平台标识
            title: 未匹配的标题
        """
        # 获取平台显示名称
        from utils.platform_registry import get_platform_registry
        registry = get_platform_registry()
        platform_name = registry.get_platform_display_name(platform)

        if platform_name not in self.unmatched_by_platform:
            self.unmatched_by_platform[platform_name] = []
        if title not in self.unmatched_by_platform[platform_name]:
            self.unmatched_by_platform[platform_name].append(title)
            # 列表与主日志：企鹅号/微信公众号用用户习惯的简称，其余用注册表显示名
            list_label = (
                "QQ号"
                if platform == "qq"
                else "微信号"
                if platform == "wechat"
                else platform_name
            )
            self.unmatched_list.addItem(f"{list_label}: {title}")
            self.append_log(f"{list_label}：{title}（未采集到的）")

    def update_progress(self, current: int, total: int, platform: str):
        """
        更新进度

        Args:
            current: 当前进度
            total: 总数
            platform: 当前平台
        """
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)
        self.progress_bar.setFormat(f"{platform}: {current}/{total} ({int(current/total*100)}%)")

    def set_manual_login_platforms(self, platforms: List[str]):
        """
        设置需要手动登录的平台列表

        Args:
            platforms: 平台标识列表
        """
        self.manual_login_list.clear()
        # 使用注册中心获取平台名称
        from utils.platform_registry import get_platform_registry
        registry = get_platform_registry()
        for platform_id in platforms:
            name = registry.get_platform_display_name(platform_id)
            item = QListWidgetItem(name)
            # 添加手动登录按钮
            self.manual_login_list.addItem(item)

    def add_manual_login_platform(self, platform_id: str):
        """
        添加需要手动登录的平台

        Args:
            platform_id: 平台标识
        """
        # 使用注册中心获取平台名称
        from utils.platform_registry import get_platform_registry
        registry = get_platform_registry()
        name = registry.get_platform_display_name(platform_id)

        # 检查是否已在列表中
        for i in range(self.manual_login_list.count()):
            if self.manual_login_list.item(i).text() == name:
                return

        self.manual_login_list.addItem(name)
        self.append_log(f"需要手动登录: {name}")

    def _on_manual_login_clicked(self):
        """手动登录采集按钮点击处理"""
        # 获取选中的平台
        current_item = self.manual_login_list.currentItem()
        if not current_item:
            QMessageBox.warning(self, "提示", "请先选择一个需要手动登录的平台")
            return

        platform_name = current_item.text()

        # 获取平台ID
        from utils.platform_registry import get_platform_registry
        registry = get_platform_registry()
        platform_id = None
        for pid, info in registry.get_all_platforms().items():
            if info.display_name == platform_name:
                platform_id = pid
                break

        if not platform_id:
            self.append_log(f"无法找到平台ID: {platform_name}")
            return

        self.append_log(f"开始手动登录采集: {platform_name}")

        # 禁用两个手动入口，防止同一事件循环上并行多路有头采集
        self.manual_login_btn.setEnabled(False)
        self.manual_collect_btn.setEnabled(False)

        # 发出手动登录采集信号
        self.manual_login_signal.emit(platform_id)

    def _on_remove_manual_clicked(self):
        """撤销该平台：从失败队列移除当前选中项"""
        current_row = self.manual_login_list.currentRow()
        if current_row < 0:
            QMessageBox.warning(self, "提示", "请先选择一个需要撤销的平台")
            return

        platform_name = self.manual_login_list.takeItem(current_row).text()
        self.append_log(f"已从失败队列撤销: {platform_name}")

    def _on_manual_collect_clicked(self):
        """
        与「需要手动登录的平台」模块相同链路：manual_login_signal → CrawlThread → execute_manual_crawl。
        """
        current_item = self.manual_collect_list.currentItem()
        if not current_item:
            QMessageBox.warning(self, "提示", "请先选择一个平台")
            return

        platform_id = current_item.data(Qt.UserRole)
        if not platform_id:
            QMessageBox.warning(self, "提示", "无法获取平台ID（勿选分隔线）")
            return

        has_title = any(w.text().strip() for w in self.title_inputs)
        if not has_title:
            QMessageBox.warning(self, "提示", "请至少输入一个目标标题（与主界面标题输入框相同）")
            return

        self.manual_login_btn.setEnabled(False)
        self.manual_collect_btn.setEnabled(False)
        self.manual_login_signal.emit(platform_id)

    def task_finished(self):
        """
        任务完成回调
        """
        self.is_running = False
        self.is_paused = False
        self._update_button_state()
        self.append_log("=" * 50)
        self.append_log("采集任务完成!")
        self.append_log(f"成功数据: {len(self.platform_data)} 条")
        total_unmatched = sum(len(titles) for titles in self.unmatched_by_platform.values())
        self.append_log(f"未匹配标题: {total_unmatched} 条")

    def show_error(self, message: str):
        """
        显示错误消息

        Args:
            message: 错误消息
        """
        self.append_log(f"[错误] {message}")
        QMessageBox.critical(self, "错误", message)


class ManualLoginDialog(QWidget):
    """
    手动登录对话框
    """

    login_completed = Signal(str)  # 登录完成信号

    def __init__(self, platform_name: str, parent=None):
        super().__init__(parent)
        self.platform_name = platform_name
        self.setWindowTitle(f"手动登录 - {platform_name}")
        self.setMinimumSize(600, 400)

        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # 说明
        layout.addWidget(QLabel(f"请在打开的浏览器中完成 {self.platform_name} 的登录操作"))
        layout.addWidget(QLabel("登录完成后，点击下方按钮确认"))

        # 状态显示
        self.status_label = QLabel("等待登录...")
        self.status_label.setStyleSheet("color: blue; font-size: 14px;")
        layout.addWidget(self.status_label)

        layout.addStretch()

        # 按钮
        btn_layout = QHBoxLayout()
        confirm_btn = QPushButton("登录完成")
        confirm_btn.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                padding: 10px 20px;
                border: none;
                border-radius: 4px;
            }
        """)
        confirm_btn.clicked.connect(self._on_confirm_clicked)

        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.close)

        btn_layout.addWidget(confirm_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

    def _on_confirm_clicked(self):
        """确认登录完成"""
        self.login_completed.emit(self.platform_name)
        self.close()
