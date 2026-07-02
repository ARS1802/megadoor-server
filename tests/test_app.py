from fastapi.testclient import TestClient

from app.main import app


AUTH_HEADERS = {"Authorization": "Bearer token-de-teste"}


def set_auth_tokens(monkeypatch):
    monkeypatch.setenv("FILE_SERVER_TOKENS", "token-de-teste,outro-token")


def test_health_uses_configured_shared_root(tmp_path, monkeypatch):
    monkeypatch.setenv("FILE_SERVER_ROOT", str(tmp_path))
    set_auth_tokens(monkeypatch)
    client = TestClient(app)

    response = client.get("/health", headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["shared_root"] == str(tmp_path.resolve())


def test_rejects_missing_token(tmp_path, monkeypatch):
    monkeypatch.setenv("FILE_SERVER_ROOT", str(tmp_path))
    set_auth_tokens(monkeypatch)
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 401


def test_rejects_invalid_token(tmp_path, monkeypatch):
    monkeypatch.setenv("FILE_SERVER_ROOT", str(tmp_path))
    set_auth_tokens(monkeypatch)
    client = TestClient(app)

    response = client.get("/health", headers={"Authorization": "Bearer token-errado"})

    assert response.status_code == 403


def test_list_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("FILE_SERVER_ROOT", str(tmp_path))
    set_auth_tokens(monkeypatch)
    (tmp_path / "documentos").mkdir()
    (tmp_path / "documentos" / "nota.txt").write_text("ola rede", encoding="utf-8")
    client = TestClient(app)

    response = client.post("/api/list", data={"path": "documentos"}, headers=AUTH_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["path"] == "documentos"
    assert body["items"][0]["name"] == "nota.txt"
    assert body["items"][0]["path"] == "documentos/nota.txt"
    assert body["items"][0]["type"] == "file"


def test_download_file(tmp_path, monkeypatch):
    monkeypatch.setenv("FILE_SERVER_ROOT", str(tmp_path))
    set_auth_tokens(monkeypatch)
    (tmp_path / "arquivo.txt").write_text("conteudo", encoding="utf-8")
    client = TestClient(app)

    response = client.post("/api/download", data={"path": "arquivo.txt"}, headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert response.text == "conteudo"


def test_create_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("FILE_SERVER_ROOT", str(tmp_path))
    set_auth_tokens(monkeypatch)
    client = TestClient(app)

    response = client.post("/api/folders", data={"path": "documentos/projetos"}, headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "path": "documentos/projetos",
        "created": True,
    }
    assert (tmp_path / "documentos" / "projetos").is_dir()


def test_upload_file(tmp_path, monkeypatch):
    monkeypatch.setenv("FILE_SERVER_ROOT", str(tmp_path))
    set_auth_tokens(monkeypatch)
    client = TestClient(app)

    response = client.post(
        "/api/upload",
        data={"path": "envios"},
        files={"file": ("arquivo.txt", b"conteudo enviado", "text/plain")},
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "saved_as": "envios/arquivo.txt",
        "filename": "arquivo.txt",
        "size": 16,
    }
    assert (tmp_path / "envios" / "arquivo.txt").read_text(encoding="utf-8") == "conteudo enviado"


def test_blocks_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setenv("FILE_SERVER_ROOT", str(tmp_path))
    set_auth_tokens(monkeypatch)
    client = TestClient(app)

    response = client.post("/api/list", data={"path": "../"}, headers=AUTH_HEADERS)

    assert response.status_code == 403


def test_upload_sanitizes_filename(tmp_path, monkeypatch):
    monkeypatch.setenv("FILE_SERVER_ROOT", str(tmp_path))
    set_auth_tokens(monkeypatch)
    client = TestClient(app)

    response = client.post(
        "/api/upload",
        data={"path": "."},
        files={"file": ("../arquivo.txt", b"conteudo", "text/plain")},
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    assert response.json()["saved_as"] == "arquivo.txt"
    assert (tmp_path / "arquivo.txt").exists()
