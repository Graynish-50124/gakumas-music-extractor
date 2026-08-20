from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Iterable

import GkmasObjectManager as gom
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from google.protobuf.message import DecodeError

from GkmasObjectManager.manifest.manifest import GkmasManifest
from GkmasObjectManager.manifest.octodb_pb2 import pdbytes2dict

from .models import ManifestCandidate, ManifestInfo


LOCAL_KEY_STRING = b"1nuv9td1bw1udefk"
LOCAL_IV_STRING = b"LvAUtf+tnz"


class ManifestError(RuntimeError):
    pass


def normalize_octo_root(path: str | Path | None) -> Path:
    if not path:
        return Path.home() / "gakumas" / "octo"
    candidate = Path(path).expanduser()
    choices = (
        candidate,
        candidate / "octo",
        candidate / "gakumas" / "octo",
    )
    for choice in choices:
        if (choice / "pdb").is_dir():
            return choice
    return candidate


def discover_manifest_candidates(octo_root: str | Path | None = None) -> list[ManifestCandidate]:
    root = normalize_octo_root(octo_root)
    if not root.is_dir():
        return []
    candidates: list[ManifestCandidate] = []
    for path in root.glob("pdb/*/*/octocacheevai"):
        try:
            stat = path.stat()
        except OSError:
            continue
        if stat.st_size <= 1:
            continue
        candidates.append(
            ManifestCandidate(
                path=path,
                modified_at=datetime.fromtimestamp(stat.st_mtime),
                size=stat.st_size,
            )
        )
    return sorted(candidates, key=lambda item: (item.modified_at, item.size), reverse=True)


def select_latest_manifest(octo_root: str | Path | None = None) -> ManifestCandidate:
    candidates = discover_manifest_candidates(octo_root)
    if not candidates:
        root = normalize_octo_root(octo_root)
        raise ManifestError(f"octocacheevai が見つかりません: {root}")
    return candidates[0]


def decrypt_local_octocache(path: str | Path) -> bytes:
    """Decrypt the PC Octo cache using the verified local-cache algorithm.

    The implementation intentionally follows get_rinami_018_local.py: the first
    management byte is excluded, the rest is AES-CBC/PKCS#7, and the first 16
    decrypted bytes are the protobuf MD5.
    """

    source = Path(path)
    try:
        encrypted_file = source.read_bytes()
    except OSError as exc:
        raise ManifestError(f"Manifestを読み込めません: {source}") from exc
    if len(encrypted_file) < 18:
        raise ManifestError("Manifestファイルが短すぎます")

    encrypted = encrypted_file[1:]
    if len(encrypted) % 16:
        raise ManifestError(
            f"ManifestのAESブロック長が不正です: {len(encrypted):,} bytes"
        )

    key = hashlib.md5(LOCAL_KEY_STRING).digest()
    iv = hashlib.md5(LOCAL_IV_STRING).digest()
    try:
        decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
        padded = decryptor.update(encrypted) + decryptor.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        decrypted = unpadder.update(padded) + unpadder.finalize()
    except ValueError as exc:
        raise ManifestError("Manifestの復号に失敗しました") from exc

    if len(decrypted) <= 16:
        raise ManifestError("復号後のManifest本体が空です")
    expected_md5, protobuf_data = decrypted[:16], decrypted[16:]
    actual_md5 = hashlib.md5(protobuf_data).digest()
    if actual_md5 != expected_md5:
        raise ManifestError("Manifestの整合性確認（MD5）に失敗しました")
    return protobuf_data


def load_local_manifest(path: str | Path) -> GkmasManifest:
    try:
        manifest_dict = pdbytes2dict(decrypt_local_octocache(path))
        return GkmasManifest(manifest_dict, 0)
    except (DecodeError, KeyError, TypeError, ValueError) as exc:
        raise ManifestError("復号したManifestの解析に失敗しました") from exc


def _revision_number(manifest: GkmasManifest) -> int:
    revision = manifest.revision.canon_repr
    if isinstance(revision, tuple):
        return int(revision[0])
    return int(revision)


def load_preferred_manifest(
    octo_root: str | Path | None = None,
    manifest_path: str | Path | None = None,
    mode: str = "local_preferred",
    online_fallback: bool = True,
) -> tuple[GkmasManifest, ManifestInfo]:
    local_error: Exception | None = None
    if mode != "online":
        try:
            chosen = (
                ManifestCandidate(
                    path=Path(manifest_path).expanduser(),
                    modified_at=datetime.fromtimestamp(Path(manifest_path).stat().st_mtime),
                    size=Path(manifest_path).stat().st_size,
                )
                if manifest_path
                else select_latest_manifest(octo_root)
            )
            manifest = load_local_manifest(chosen.path)
            root = normalize_octo_root(octo_root or _infer_octo_root(chosen.path))
            info = ManifestInfo(
                source="ローカル PC版",
                revision=_revision_number(manifest),
                updated_at=chosen.modified_at,
                manifest_path=chosen.path,
                octo_root=root,
                object_count=len(manifest),
            )
            return manifest, info
        except (ManifestError, OSError) as exc:
            local_error = exc
            if not online_fallback:
                raise ManifestError(str(exc)) from exc

    try:
        manifest = gom.fetch(pc=True)
        return manifest, ManifestInfo(
            source="オンライン PC版",
            revision=_revision_number(manifest),
            updated_at=datetime.now(),
            manifest_path=None,
            octo_root=normalize_octo_root(octo_root),
            object_count=len(manifest),
        )
    except Exception as exc:
        if local_error:
            raise ManifestError(
                f"ローカルManifestを読み込めず、オンライン取得にも失敗しました: {local_error}"
            ) from exc
        raise ManifestError("オンラインPC版Manifestの取得に失敗しました") from exc


def _infer_octo_root(manifest_path: Path) -> Path:
    parts = manifest_path.resolve().parts
    lowered = [part.casefold() for part in parts]
    if "pdb" in lowered:
        index = lowered.index("pdb")
        return Path(*parts[:index])
    return Path.home() / "gakumas" / "octo"


def candidate_summary(candidates: Iterable[ManifestCandidate]) -> list[str]:
    return [
        f"{item.path} | {item.modified_at:%Y-%m-%d %H:%M:%S} | {item.size:,} bytes"
        for item in candidates
    ]

