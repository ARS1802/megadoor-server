from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated
import os
import tempfile
import zipfile

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask


PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_SHARED_ROOT = PROJECT_DIR / "shared_files"

PathQuery = Annotated[
    str,
    Query(
        description=(
            "Caminho relativo dentro da pasta compartilhada. "
            "Exemplos: '.', 'documentos' ou 'documentos/nota.txt'."
        )
    ),
]

app = FastAPI(
    title="Servidor de Arquivos HTTPS",
    description=(
        "API didatica para listar, baixar e compactar arquivos de uma pasta "
        "compartilhada na rede local."
    ),
    version="0.1.0",
)


class FileItem(BaseModel):
    name: str
    path: str
    type: str
    size: int
    modified_at: str


class DirectoryListing(BaseModel):
    shared_root: str
    path: str
    items: list[FileItem]


def get_shared_root() -> Path:
    root = Path(os.getenv("FILE_SERVER_ROOT", DEFAULT_SHARED_ROOT)).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def resolve_safe_path(requested_path: str) -> Path:
    root = get_shared_root()
    clean_path = (requested_path or ".").strip()

    if "\x00" in clean_path:
        raise HTTPException(status_code=400, detail="Caminho invalido.")

    if clean_path in {"", "/", "."}:
        clean_path = "."

    candidate = (root / clean_path.lstrip("/")).resolve()

    try:
        candidate.relative_to(root)
    except ValueError:
        raise HTTPException(
            status_code=403,
            detail="O caminho precisa ficar dentro da pasta compartilhada.",
        )

    return candidate


def relative_to_shared_root(path: Path) -> str:
    root = get_shared_root()
    if path == root:
        return "."
    return path.relative_to(root).as_posix()


def is_inside_shared_root(path: Path) -> bool:
    root = get_shared_root()
    try:
        path.resolve().relative_to(root)
    except ValueError:
        return False
    return True


def format_mtime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


def zip_directory(target: Path) -> Path:
    root = get_shared_root()
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
    temp_path = Path(temp_file.name)
    temp_file.close()

    with zipfile.ZipFile(temp_path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for item in sorted(target.rglob("*")):
            resolved_item = item.resolve()
            try:
                resolved_item.relative_to(root)
            except ValueError:
                continue

            if resolved_item.is_file():
                archive.write(
                    resolved_item,
                    arcname=resolved_item.relative_to(root).as_posix(),
                )

    return temp_path


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "shared_root": str(get_shared_root())}


@app.get("/api/list", response_model=DirectoryListing)
def list_directory(path: PathQuery = ".") -> DirectoryListing:
    target = resolve_safe_path(path)

    if not target.exists():
        raise HTTPException(status_code=404, detail="Caminho nao encontrado.")

    if not target.is_dir():
        raise HTTPException(status_code=400, detail="Este caminho nao e um diretorio.")

    items: list[FileItem] = []

    try:
        children = sorted(target.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
    except PermissionError:
        raise HTTPException(status_code=403, detail="Sem permissao para ler este diretorio.")

    for child in children:
        try:
            if not is_inside_shared_root(child):
                continue

            child_type = "directory" if child.is_dir() else "file"
            child_size = 0 if child.is_dir() else child.stat().st_size
            items.append(
                FileItem(
                    name=child.name,
                    path=relative_to_shared_root(child.resolve()),
                    type=child_type,
                    size=child_size,
                    modified_at=format_mtime(child),
                )
            )
        except (FileNotFoundError, PermissionError):
            continue

    return DirectoryListing(
        shared_root=str(get_shared_root()),
        path=relative_to_shared_root(target),
        items=items,
    )


@app.get("/api/download")
def download_file(path: PathQuery) -> FileResponse:
    target = resolve_safe_path(path)

    if not target.exists():
        raise HTTPException(status_code=404, detail="Arquivo nao encontrado.")

    if target.is_dir():
        raise HTTPException(
            status_code=400,
            detail="Este caminho e um diretorio. Use /api/archive para baixar um .zip.",
        )

    return FileResponse(target, filename=target.name)


@app.get("/api/archive")
def download_directory_as_zip(path: PathQuery = ".") -> FileResponse:
    target = resolve_safe_path(path)

    if not target.exists():
        raise HTTPException(status_code=404, detail="Diretorio nao encontrado.")

    if not target.is_dir():
        raise HTTPException(status_code=400, detail="Este caminho nao e um diretorio.")

    zip_path = zip_directory(target)
    filename = f"{target.name or 'arquivos'}.zip"

    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename=filename,
        background=BackgroundTask(zip_path.unlink, missing_ok=True),
    )
