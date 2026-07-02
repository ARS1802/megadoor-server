from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated
import os
import shutil
import tempfile
import zipfile

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask


PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_SHARED_ROOT = PROJECT_DIR / "shared_files"

PathForm = Annotated[
    str,
    Form(
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


class FolderResult(BaseModel):
    status: str
    path: str
    created: bool


class UploadResult(BaseModel):
    status: str
    saved_as: str
    filename: str
    size: int


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


def safe_upload_filename(filename: str | None) -> str:
    clean_name = Path(filename or "").name.strip()

    if clean_name in {"", ".", ".."}:
        raise HTTPException(status_code=400, detail="Nome de arquivo invalido.")

    return clean_name


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


@app.post("/api/list", response_model=DirectoryListing)
def list_directory(path: PathForm = ".") -> DirectoryListing:
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


@app.post("/api/download")
def download_file(path: PathForm) -> FileResponse:
    target = resolve_safe_path(path)

    if not target.exists():
        raise HTTPException(status_code=404, detail="Arquivo nao encontrado.")

    if target.is_dir():
        raise HTTPException(
            status_code=400,
            detail="Este caminho e um diretorio. Use /api/archive para baixar um .zip.",
        )

    return FileResponse(target, filename=target.name)


@app.post("/api/archive")
def download_directory_as_zip(path: PathForm = ".") -> FileResponse:
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


@app.post("/api/folders", response_model=FolderResult)
def create_folder(path: PathForm) -> FolderResult:
    target = resolve_safe_path(path)

    if target.exists() and not target.is_dir():
        raise HTTPException(status_code=400, detail="Ja existe um arquivo com este nome.")

    created = not target.exists()
    target.mkdir(parents=True, exist_ok=True)

    return FolderResult(
        status="ok",
        path=relative_to_shared_root(target),
        created=created,
    )


@app.post("/api/upload", response_model=UploadResult)
async def upload_file(
    path: PathForm = ".",
    file: UploadFile = File(...),
) -> UploadResult:
    target_dir = resolve_safe_path(path)

    if target_dir.exists() and not target_dir.is_dir():
        raise HTTPException(status_code=400, detail="O caminho precisa ser um diretorio.")

    target_dir.mkdir(parents=True, exist_ok=True)

    filename = safe_upload_filename(file.filename)
    destination = (target_dir / filename).resolve()

    if not is_inside_shared_root(destination):
        raise HTTPException(status_code=403, detail="Nome de arquivo invalido.")

    try:
        with destination.open("wb") as output:
            shutil.copyfileobj(file.file, output)
    except PermissionError:
        raise HTTPException(status_code=403, detail="Sem permissao para salvar este arquivo.")
    finally:
        await file.close()

    return UploadResult(
        status="ok",
        saved_as=relative_to_shared_root(destination),
        filename=filename,
        size=destination.stat().st_size,
    )
