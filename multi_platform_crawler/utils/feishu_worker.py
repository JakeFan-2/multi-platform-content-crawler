"""
===============================================================
飞书导入工作线程
Feishu Import Worker
===============================================================

功能：
- 在独立线程中执行飞书导入
- 避免阻塞 GUI 主线程
- 通过信号与主窗口通信
===============================================================
"""

import asyncio
from typing import List, Dict
from PySide6.QtCore import QThread, Signal

from loguru import logger

# 模块顶层导入：保证 PyInstaller 能收集 feishu_exporter → aiohttp（勿改为 run 内延迟导入）
from utils.feishu_exporter import FeishuExporter


class FeishuImportWorker(QThread):
    """
    飞书导入工作线程
    
    在独立线程中执行数据导入，避免阻塞 GUI
    """
    
    # 信号定义
    log_signal = Signal(str)              # 日志消息信号
    finished_signal = Signal(bool, str)   # 完成信号 (成功标志, 消息)
    
    def __init__(self, data: List[Dict]):
        """
        初始化工作线程
        
        Args:
            data: 标准字段字典列表（来自 MainWindow.platform_data）
        """
        super().__init__()
        self.data = data
    
    def run(self):
        """
        线程入口：执行飞书导入
        
        在新线程中创建独立的事件循环，调用 FeishuExporter 执行导入
        """
        # 在新线程中创建独立的事件循环
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            # 发送日志
            self.log_signal.emit(f"开始导入 {len(self.data)} 条数据到飞书...")
            
            # 创建导出器并执行导出
            exporter = FeishuExporter()
            result = loop.run_until_complete(exporter.export_data(self.data))
            
            # 发送完成信号
            success = result.get("success", False)
            message = result.get("message", "")
            self.finished_signal.emit(success, message)
            
        except Exception as e:
            error_msg = f"导入过程异常: {str(e)}"
            logger.error(error_msg)
            self.log_signal.emit(f"错误: {error_msg}")
            self.finished_signal.emit(False, error_msg)
            
        finally:
            # 关闭事件循环
            loop.close()
            logger.info("飞书导入工作线程结束")
