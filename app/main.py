from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated
import os
import secrets
import shutil
import tempfile
import zipfile

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask


PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_SHARED_ROOT = PROJECT_DIR / "shared_files"
TOKEN_ENV_NAME = "FILE_SERVER_TOKENS"

PathForm = Annotated[
    str,
    Form(
        description=(
            "Caminho relativo dentro da pasta compartilhada. "
            "Exemplos: '.', 'documentos' ou 'documentos/nota.txt'."
        )
    ),
]

SourcePathForm = Annotated[str, Form(description="Caminho relativo de origem.")]
TargetPathForm = Annotated[str, Form(description="Caminho relativo de destino.")]
NewNameForm = Annotated[str, Form(description="Novo nome, sem barras.")]
OverwriteForm = Annotated[bool, Form(description="Se true, substitui o destino se ele ja existir.")]
AuthHeader = Annotated[str | None, Header(description="Use: Bearer TOKEN")]

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


class FileOperationResult(BaseModel):
    status: str
    operation: str
    path: str | None = None
    source_path: str | None = None
    target_path: str | None = None


def get_allowed_tokens() -> set[str]:
    raw_tokens = os.getenv(TOKEN_ENV_NAME, "")
    return {token.strip() for token in raw_tokens.split(",") if token.strip()}


def require_auth(authorization: AuthHeader = None) -> None:
    allowed_tokens = get_allowed_tokens()

    if not allowed_tokens:
        raise HTTPException(
            status_code=503,
            detail="Servidor sem tokens configurados. Inicie usando START_SERVER.",
        )

    if authorization is None:
        raise HTTPException(status_code=401, detail="Token ausente.")

    prefix = "Bearer "
    if not authorization.startswith(prefix):
        raise HTTPException(status_code=401, detail="Formato de token invalido.")

    received_token = authorization.removeprefix(prefix).strip()

    if not any(secrets.compare_digest(received_token, token) for token in allowed_tokens):
        raise HTTPException(status_code=403, detail="Token nao autorizado.")


AuthDependency = Annotated[None, Depends(require_auth)]


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


def safe_entry_name(name: str) -> str:
    clean_name = name.strip()

    if clean_name in {"", ".", ".."} or "/" in clean_name or "\\" in clean_name or "\x00" in clean_name:
        raise HTTPException(status_code=400, detail="Nome invalido.")

    return clean_name


def ensure_exists(path: Path, item_name: str = "Caminho") -> None:
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"{item_name} nao encontrado.")


def ensure_not_shared_root(path: Path, operation: str) -> None:
    if path == get_shared_root():
        raise HTTPException(status_code=400, detail=f"Nao e permitido {operation} a pasta compartilhada.")


def ensure_parent_directory(path: Path) -> None:
    if not path.parent.exists():
        raise HTTPException(status_code=404, detail="Diretorio de destino nao encontrado.")

    if not path.parent.is_dir():
        raise HTTPException(status_code=400, detail="Destino pai nao e um diretorio.")


def ensure_not_inside_source(source: Path, destination: Path, operation: str) -> None:
    if not source.is_dir():
        return

    try:
        destination.relative_to(source)
    except ValueError:
        return

    if destination != source:
        raise HTTPException(
            status_code=400,
            detail=f"Nao e permitido {operation} um diretorio para dentro dele mesmo.",
        )


def resolve_final_destination(source: Path, requested_target: str) -> Path:
    target = resolve_safe_path(requested_target)

    if target.exists() and target.is_dir():
        target = (target / source.name).resolve()

        if not is_inside_shared_root(target):
            raise HTTPException(status_code=403, detail="Destino invalido.")

    return target


def remove_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def prepare_destination(source: Path, destination: Path, overwrite: bool, operation: str) -> None:
    ensure_not_shared_root(destination, operation)
    ensure_parent_directory(destination)
    ensure_not_inside_source(source, destination, operation)

    if source == destination:
        return

    if not destination.exists():
        return

    if not overwrite:
        raise HTTPException(status_code=409, detail="Destino ja existe.")

    remove_path(destination)


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
def health(_: AuthDependency) -> dict[str, str]:
    return {"status": "ok", "shared_root": str(get_shared_root())}


@app.post("/api/list", response_model=DirectoryListing)
def list_directory(_: AuthDependency, path: PathForm = ".") -> DirectoryListing:
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
def download_file(_: AuthDependency, path: PathForm) -> FileResponse:
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
def download_directory_as_zip(_: AuthDependency, path: PathForm = ".") -> FileResponse:
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
def create_folder(_: AuthDependency, path: PathForm) -> FolderResult:
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
    _: AuthDependency,
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


@app.post("/api/delete", response_model=FileOperationResult)
def delete_path(_: AuthDependency, path: PathForm) -> FileOperationResult:
    target = resolve_safe_path(path)
    ensure_exists(target)
    ensure_not_shared_root(target, "deletar")

    try:
        remove_path(target)
    except PermissionError:
        raise HTTPException(status_code=403, detail="Sem permissao para deletar este caminho.")

    return FileOperationResult(
        status="ok",
        operation="delete",
        path=path,
    )


@app.post("/api/move", response_model=FileOperationResult)
def move_path(
    _: AuthDependency,
    source_path: SourcePathForm,
    target_path: TargetPathForm,
    overwrite: OverwriteForm = False,
) -> FileOperationResult:
    source = resolve_safe_path(source_path)
    destination = resolve_final_destination(source, target_path)

    ensure_exists(source, "Origem")
    ensure_not_shared_root(source, "mover")
    prepare_destination(source, destination, overwrite, "mover")

    if source != destination:
        try:
            shutil.move(str(source), str(destination))
        except PermissionError:
            raise HTTPException(status_code=403, detail="Sem permissao para mover este caminho.")

    return FileOperationResult(
        status="ok",
        operation="move",
        source_path=source_path,
        target_path=relative_to_shared_root(destination),
    )


@app.post("/api/copy", response_model=FileOperationResult)
def copy_path(
    _: AuthDependency,
    source_path: SourcePathForm,
    target_path: TargetPathForm,
    overwrite: OverwriteForm = False,
) -> FileOperationResult:
    source = resolve_safe_path(source_path)
    destination = resolve_final_destination(source, target_path)

    ensure_exists(source, "Origem")
    ensure_not_shared_root(source, "copiar")
    prepare_destination(source, destination, overwrite, "copiar")

    if source == destination:
        raise HTTPException(status_code=400, detail="Origem e destino sao iguais.")

    try:
        if source.is_dir():
            shutil.copytree(source, destination)
        else:
            shutil.copy2(source, destination)
    except PermissionError:
        raise HTTPException(status_code=403, detail="Sem permissao para copiar este caminho.")

    return FileOperationResult(
        status="ok",
        operation="copy",
        source_path=source_path,
        target_path=relative_to_shared_root(destination),
    )


@app.post("/api/rename", response_model=FileOperationResult)
def rename_path(
    _: AuthDependency,
    source_path: SourcePathForm,
    new_name: NewNameForm,
    overwrite: OverwriteForm = False,
) -> FileOperationResult:
    source = resolve_safe_path(source_path)
    ensure_exists(source, "Origem")
    ensure_not_shared_root(source, "renomear")

    destination = (source.parent / safe_entry_name(new_name)).resolve()

    if not is_inside_shared_root(destination):
        raise HTTPException(status_code=403, detail="Novo nome invalido.")

    prepare_destination(source, destination, overwrite, "renomear")

    if source != destination:
        try:
            source.rename(destination)
        except PermissionError:
            raise HTTPException(status_code=403, detail="Sem permissao para renomear este caminho.")

    return FileOperationResult(
        status="ok",
        operation="rename",
        source_path=source_path,
        target_path=relative_to_shared_root(destination),
    )
