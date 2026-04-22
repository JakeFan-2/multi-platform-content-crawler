#!/usr/bin/env python3
"""
本地敏感信息迁移工具
在您的本地环境运行，不经过AI处理
"""

import re
import sys
import yaml
from pathlib import Path
from datetime import datetime


def migrate_credentials():
    """迁移YAML账号密码到.env（本地执行）"""
    # 保证可导入 path_helper（与 main 一致：包根目录在 path）
    pkg_root = Path(__file__).resolve().parent
    if str(pkg_root) not in sys.path:
        sys.path.insert(0, str(pkg_root))

    from utils.path_helper import ENV_PATH, PLATFORMS_DIR

    platforms_dir = PLATFORMS_DIR
    env_file = ENV_PATH
    
    if not platforms_dir.exists():
        print(f"错误: 未找到平台配置目录: {platforms_dir}")
        return
    
    # 读取现有.env内容
    existing_lines = []
    if env_file.exists():
        existing_lines = env_file.read_text(encoding="utf-8").splitlines()
    
    # 过滤掉已存在的平台配置（避免重复）
    filtered_lines = []
    skip_section = False
    for line in existing_lines:
        if "# 平台账号密码配置" in line:
            skip_section = True
        if skip_section and line.strip() and not line.startswith("#"):
            if "=" in line and any(p in line for p in ["USERNAME", "PASSWORD"]):
                continue
        filtered_lines.append(line)
    
    # 提取YAML中的凭证
    credentials = []
    
    for yaml_file in sorted(platforms_dir.glob("*.yaml")):
        platform_id = yaml_file.stem
        
        if platform_id == "template":
            continue
        
        try:
            with open(yaml_file, "r", encoding="utf-8") as f:
                content = f.read()
                config = yaml.safe_load(content)
            
            if not config:
                continue
            
            username = config.get("username", "")
            password = config.get("password", "")
            
            # 只记录有值的配置
            if username or password:
                platform_upper = platform_id.upper()
                credentials.append((platform_upper, username, password))
                print(f"找到配置: {platform_id}")
                
        except Exception as e:
            print(f"读取 {yaml_file.name} 失败: {e}")
    
    if not credentials:
        print("未找到任何平台账号配置")
        return
    
    # 生成.env内容
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    new_section = [
        "",
        "# ============================================================",
        f"# 平台账号密码配置（自动生成于 {timestamp}）",
        "# 命名规则：{PLATFORM}_USERNAME / {PLATFORM}_PASSWORD",
        "# ============================================================",
        "",
    ]
    
    for platform_upper, username, password in credentials:
        new_section.append(f"# {platform_upper}")
        new_section.append(f'{platform_upper}_USERNAME="{username}"')
        new_section.append(f'{platform_upper}_PASSWORD="{password}"')
        new_section.append("")
    
    # 写入.env
    final_content = "\n".join(filtered_lines + new_section)
    env_file.write_text(final_content, encoding="utf-8")
    
    print(f"\n迁移完成: {env_file}")
    print(f"共迁移 {len(credentials)} 个平台配置")
    print("\n请验证.env文件内容，然后手动清空YAML文件中的账号密码字段")


if __name__ == "__main__":
    migrate_credentials()