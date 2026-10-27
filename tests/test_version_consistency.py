# -*- coding: utf-8 -*-
"""版本号一致性门禁：config.py 是唯一来源，NSI / VERSION.txt / 状态文档必须派生一致。

背景（第五轮阶段 3）：第九波只改了 NSI 外壳版本号，没同步 config.py、
也没重打 PyInstaller，造成「v1.0.7 标签 + 1.0.6 代码」。本用例把四处一致性
固化成断言，任何一处手改都会在 pytest 阶段失败。

覆盖：
1. APP_VERSION 语义化格式；BUILD_TAG = ``v{APP_VERSION}-{YYYYMMDD}``；
2. ``installer/installer.nsi``（UTF-16 LE BOM）的 APPVER / APPVER4 / OutFile 与 config 一致；
3. 部署目录 ``VERSION.txt``（存在时）首行 = ``{APP_VERSION}（{BUILD_TAG}）``；
4. ``项目状态文档.md`` 顶部（存在时）含当前 BUILD_TAG。
"""

from __future__ import annotations

import pathlib
import re

from backend.app import config

PROJECT_DIR = pathlib.Path(__file__).resolve().parents[1]
NSI_FILE = PROJECT_DIR / "installer" / "installer.nsi"
DEPLOY_VERSION = PROJECT_DIR.parent / "正式环境" / "VERSION.txt"
STATUS_DOC = PROJECT_DIR.parents[1] / "项目状态文档.md"


def _build_date() -> str:
    """从 BUILD_TAG 取出构建日期。

    Returns:
        str: ``YYYYMMDD`` 形式的日期串。
    """
    return config.BUILD_TAG.rsplit("-", 1)[-1]


def test_app_version_is_semver() -> None:
    """APP_VERSION 必须是 ``X.Y.Z`` 三段数字。"""
    assert re.fullmatch(r"\d+\.\d+\.\d+", config.APP_VERSION), config.APP_VERSION


def test_build_tag_matches_version_and_date() -> None:
    """BUILD_TAG 必须严格等于 ``v{APP_VERSION}-{YYYYMMDD}``。"""
    assert config.BUILD_TAG == f"v{config.APP_VERSION}-{_build_date()}"
    assert re.fullmatch(r"\d{8}", _build_date()), config.BUILD_TAG


def test_nsi_encoding_is_utf16le_bom() -> None:
    """installer.nsi 必须保持 UTF-16 LE with BOM（Unicode makensis 前提）。"""
    raw = NSI_FILE.read_bytes()
    assert raw[:2] == b"\xff\xfe", "NSI 必须带 UTF-16 LE BOM"


def test_nsi_versions_match_config() -> None:
    """NSI 的 APPVER / APPVER4 必须由 config 派生。"""
    text = NSI_FILE.read_text(encoding="utf-16")
    assert f'!define APPVER   "{config.APP_VERSION}"' in text
    assert f'!define APPVER4  "{config.APP_VERSION}.0"' in text


def test_nsi_outfile_name_matches_config() -> None:
    """NSI 的 OutFile 文件名必须含 ``v{APP_VERSION}_{日期}``。"""
    text = NSI_FILE.read_text(encoding="utf-16")
    expected = f"OpenBB中文投研工作台_v{config.APP_VERSION}_{_build_date()}_setup.exe"
    assert expected in text, f"OutFile 未含 {expected}"


def test_deploy_version_txt_matches_config() -> None:
    """部署目录 VERSION.txt（存在时）首行必须与 config 一致。"""
    if not DEPLOY_VERSION.exists():
        return
    first = DEPLOY_VERSION.read_text(encoding="utf-8").splitlines()[0].strip()
    assert first == f"{config.APP_VERSION}（{config.BUILD_TAG}）", first


def test_status_doc_mentions_current_build_tag() -> None:
    """项目状态文档顶部（存在时）必须含当前 BUILD_TAG。"""
    if not STATUS_DOC.exists():
        return
    head = "\n".join(STATUS_DOC.read_text(encoding="utf-8").splitlines()[:40])
    assert config.BUILD_TAG in head, f"状态文档顶部未出现 {config.BUILD_TAG}"
