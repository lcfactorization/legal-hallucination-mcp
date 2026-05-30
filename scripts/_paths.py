"""公共路径解析 — 从环境变量或默认值获取项目路径。

所有脚本统一使用此模块获取路径，避免硬编码绝对路径。
环境变量优先，回退到项目相对路径。
"""

import os
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent


def get_vault_root() -> str:
    return os.environ.get(
        "VAULT_ROOT",
        str(_PROJECT_ROOT),
    )


def get_manifest_path() -> str:
    return os.environ.get(
        "EVIDENCE_MANIFEST_PATH",
        os.path.join(get_vault_root(), ".trae", "evidence_manifest.md"),
    )


def get_output_dir() -> str:
    return os.environ.get(
        "OUTPUT_DIR",
        str(_SCRIPT_DIR / "output"),
    )


def get_law_dir() -> str:
    return os.environ.get(
        "LOCAL_LAW_DIR",
        os.path.join(get_vault_root(), "案件", "法律法规"),
    )


def get_doc_path(version: str) -> str:
    return os.environ.get(
        f"DOC_PATH_{version}",
        os.path.join(get_vault_root(), f"{version}_模拟二审判决书_苏06民终6271号劳动争议"),
    )
