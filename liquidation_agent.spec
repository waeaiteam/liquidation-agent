# -*- mode: python ; coding: utf-8 -*-
# Build with: pyinstaller liquidation_agent.spec --noconfirm --clean
from PyInstaller.utils.hooks import collect_data_files, collect_submodules
import os

project_root = os.path.abspath(os.path.dirname(SPEC))
icon_path = os.path.join(project_root, "..", "liquidation_agent_electron", "build", "icon.ico")
if not os.path.exists(icon_path):
    icon_path = None  # fall back to default PyInstaller icon if not yet provided

datas = [
    (os.path.join(project_root, "templates", "index.html"), "templates"),
    (os.path.join(project_root, "templates", "echarts.min.js"), "templates"),
    (os.path.join(project_root, "templates", "app.js"), "templates"),
    (os.path.join(project_root, "static", "logos"), "static/logos"),
    (os.path.join(project_root, "agents", "x_analyst.md"), "agents"),
]
datas += collect_data_files("claw402", include_py_files=False)
datas += collect_data_files("anthropic", include_py_files=False)
datas += collect_data_files("certifi")

hiddenimports = (
    collect_submodules("claw402")
    + collect_submodules("anthropic")
    + collect_submodules("flask")
    + collect_submodules("flask_cors")
    + collect_submodules("werkzeug")
    + collect_submodules("httpx")
    + collect_submodules("tweepy")
    + [
        "providers",
        "state",
        "services.agent_chat",
        "services.claw_docs",
        "services.coinank",
        "services.evolution",
        "services.heatmap_manager",
        "services.llm",
        "services.strategy_agent",
        "services.x_sentiment",
        "services.xai_chat",
        "services.x_poster",
        "services.x_pipeline",
        "strategy.models",
        "strategy.signals",
        "trading.execution",
        "trading.risk",
        "urllib3",
        "charset_normalizer",
        "idna",
        "requests",
        "requests_oauthlib",
        "oauthlib",
        "httpcore",
        "h11",
        "sniffio",
        "anyio",
    ]
)

a = Analysis(
    ["app.py"],
    pathex=[project_root],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy.tests", "pandas.tests",
              "PyQt5", "PyQt6", "PySide2", "PySide6"],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="liquidation_agent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,            # required: Electron parses stdout for LISTENING_ON_PORT
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_path,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="liquidation_agent",
)
