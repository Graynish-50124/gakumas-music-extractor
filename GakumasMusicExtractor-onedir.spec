from pathlib import Path
import os
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
unitypy_data = [
    (source, destination)
    for source, destination in collect_data_files("UnityPy")
    if "fmod" not in source.casefold()
]
datas = [
    (str(project / "config"), "config"),
    (str(vendor / "ffmpeg" / "bin" / "ffmpeg.exe"), "ffmpeg/bin"),
    (str(vendor / "ffmpeg" / "LICENSE"), "ffmpeg"),
    (str(project / "LICENSE"), "."),
] + vgmstream_windows + collect_data_files("archspec") + unitypy_data
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
    excludes=[
        "flask", "tkinter", "pandas", "numpy", "pytest", "_pytest",
        "pyfmodex", "fmod_toolkit",
    ],
    noarchive=False,
    optimize=1,
)

# PyInstaller searches PATH while resolving native DLLs.  A build shell may
# contain unrelated tools (Poppler, ImageMagick, etc.) with DLL names that also
# exist in Windows, such as icuuc.dll.  Bundling one of those files makes Qt
# load the wrong ABI and QtWidgets fails with Windows error 127.  All legitimate
# native dependencies for this standalone build must originate from the
# project/venv, the base Python installation, or Windows itself.
allowed_binary_roots = [
    project.resolve(),
    Path(sys.prefix).resolve(),
    Path(sys.base_prefix).resolve(),
    Path(os.environ.get("SystemRoot", r"C:\Windows")).resolve(),
]
unexpected_binaries = [
    (destination, source)
    for destination, source, _kind in a.binaries
    if not any(Path(source).resolve().is_relative_to(root) for root in allowed_binary_roots)
]
if unexpected_binaries:
    details = "\n".join(f"  {destination}: {source}" for destination, source in unexpected_binaries)
    raise RuntimeError(f"PATH由来の外部DLLを検出しました。build.ps1を使用してください:\n{details}")

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
