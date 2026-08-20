from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

project = Path(SPECPATH)
src = project / "src"
vendor = project / "vendor"
sys.path.insert(0, str(vendor))

vgmstream_dir = vendor / "GkmasObjectManager" / "bin" / "vgmstream"
vgmstream_windows = [
    (str(path), "GkmasObjectManager/bin/vgmstream")
    for path in vgmstream_dir.iterdir()
    if path.is_file() and path.name not in {"vgmstream-linux", "vgmstream-mac"}
]
datas = [
    (str(project / "config"), "config"),
    (str(vendor / "ffmpeg" / "bin" / "ffmpeg.exe"), "ffmpeg/bin"),
    (str(vendor / "ffmpeg" / "LICENSE"), "ffmpeg"),
    (str(project / "LICENSE"), "."),
] + vgmstream_windows + collect_data_files("archspec") + collect_data_files("UnityPy")
hiddenimports = collect_submodules("GkmasObjectManager") + collect_submodules("UnityPy")

a = Analysis(
    [str(src / "main.py")],
    pathex=[str(src), str(vendor)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["flask", "tkinter", "pandas", "numpy", "pytest", "_pytest"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="GakumasMusicExtractor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="GakumasMusicExtractor",
)
