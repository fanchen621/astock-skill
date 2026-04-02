"""
DragonFlow Skill 打包脚本
═══════════════════════════════════════════════════════════════════════════
打包内容：所有源文件 + skill.yaml + requirements.txt + templates
排除：__pycache__、*.pyc、.db、.log、data/reports/*、data/runtime/*

用法: python build_skill.py
输出: DragonFlow_v2.zip
"""
import os
import zipfile
from pathlib import Path

ROOT = Path(__file__).parent
OUTPUT = ROOT.parent / "DragonFlow_v2.zip"

EXCLUDE_DIRS = {"__pycache__", ".git", "node_modules", ".claude"}
EXCLUDE_EXTS = {".pyc", ".pyo", ".db", ".db-shm", ".db-wal", ".log"}
EXCLUDE_PATHS = {"data/reports", "data/runtime", "data/logs"}


def should_include(rel_path: str, name: str) -> bool:
    # 排除目录
    parts = rel_path.replace("\\", "/").split("/")
    for p in parts:
        if p in EXCLUDE_DIRS:
            return False
    # 排除路径前缀
    rp = rel_path.replace("\\", "/")
    for ep in EXCLUDE_PATHS:
        if rp.startswith(ep) and name != ".gitkeep":
            return False
    # 排除扩展名
    _, ext = os.path.splitext(name)
    if ext in EXCLUDE_EXTS:
        return False
    # 排除打包脚本自身的输出
    if name.endswith(".zip"):
        return False
    return True


def build():
    print(f"打包 DragonFlow Skill...")
    print(f"源目录: {ROOT}")
    print(f"输出: {OUTPUT}")

    count = 0
    with zipfile.ZipFile(OUTPUT, "w", zipfile.ZIP_DEFLATED) as zf:
        for dirpath, dirnames, filenames in os.walk(ROOT):
            # 跳过排除目录
            dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]

            for fname in filenames:
                full = os.path.join(dirpath, fname)
                rel = os.path.relpath(full, ROOT)

                if should_include(rel, fname):
                    arcname = os.path.join("DragonFlow", rel)
                    zf.write(full, arcname)
                    count += 1

    size_mb = OUTPUT.stat().st_size / 1024 / 1024
    print(f"\n{'='*50}")
    print(f"  DragonFlow_v2.zip 打包完成")
    print(f"  文件数: {count}")
    print(f"  大小: {size_mb:.2f} MB")
    print(f"  位置: {OUTPUT}")
    print(f"{'='*50}")


if __name__ == "__main__":
    build()
