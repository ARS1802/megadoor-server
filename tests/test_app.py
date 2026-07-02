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


def test_cors_preflight_allows_browser_requests(monkeypatch):
    set_auth_tokens(monkeypatch)
    client = TestClient(app)

    response = client.options(
        "/api/list",
        headers={
            "Origin": "null",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"
    assert "POST" in response.headers["access-control-allow-methods"]
    assert "authorization" in response.headers["access-control-allow-headers"].lower()


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


def test_delete_file(tmp_path, monkeypatch):
    monkeypatch.setenv("FILE_SERVER_ROOT", str(tmp_path))
    set_auth_tokens(monkeypatch)
    (tmp_path / "arquivo.txt").write_text("conteudo", encoding="utf-8")
    client = TestClient(app)

    response = client.post("/api/delete", data={"path": "arquivo.txt"}, headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert response.json()["operation"] == "delete"
    assert not (tmp_path / "arquivo.txt").exists()


def test_delete_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("FILE_SERVER_ROOT", str(tmp_path))
    set_auth_tokens(monkeypatch)
    (tmp_path / "pasta" / "subpasta").mkdir(parents=True)
    (tmp_path / "pasta" / "subpasta" / "arquivo.txt").write_text("conteudo", encoding="utf-8")
    client = TestClient(app)

    response = client.post("/api/delete", data={"path": "pasta"}, headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert not (tmp_path / "pasta").exists()


def test_rejects_delete_shared_root(tmp_path, monkeypatch):
    monkeypatch.setenv("FILE_SERVER_ROOT", str(tmp_path))
    set_auth_tokens(monkeypatch)
    client = TestClient(app)

    response = client.post("/api/delete", data={"path": "."}, headers=AUTH_HEADERS)

    assert response.status_code == 400


def test_move_file_to_existing_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("FILE_SERVER_ROOT", str(tmp_path))
    set_auth_tokens(monkeypatch)
    (tmp_path / "arquivo.txt").write_text("conteudo", encoding="utf-8")
    (tmp_path / "documentos").mkdir()
    client = TestClient(app)

    response = client.post(
        "/api/move",
        data={"source_path": "arquivo.txt", "target_path": "documentos"},
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    assert response.json()["target_path"] == "documentos/arquivo.txt"
    assert not (tmp_path / "arquivo.txt").exists()
    assert (tmp_path / "documentos" / "arquivo.txt").read_text(encoding="utf-8") == "conteudo"


def test_move_rejects_existing_destination_without_overwrite(tmp_path, monkeypatch):
    monkeypatch.setenv("FILE_SERVER_ROOT", str(tmp_path))
    set_auth_tokens(monkeypatch)
    (tmp_path / "origem.txt").write_text("origem", encoding="utf-8")
    (tmp_path / "destino.txt").write_text("destino", encoding="utf-8")
    client = TestClient(app)

    response = client.post(
        "/api/move",
        data={"source_path": "origem.txt", "target_path": "destino.txt"},
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 409
    assert (tmp_path / "origem.txt").exists()
    assert (tmp_path / "destino.txt").read_text(encoding="utf-8") == "destino"


def test_copy_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("FILE_SERVER_ROOT", str(tmp_path))
    set_auth_tokens(monkeypatch)
    (tmp_path / "origem" / "subpasta").mkdir(parents=True)
    (tmp_path / "origem" / "subpasta" / "arquivo.txt").write_text("conteudo", encoding="utf-8")
    client = TestClient(app)

    response = client.post(
        "/api/copy",
        data={"source_path": "origem", "target_path": "copia"},
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    assert response.json()["target_path"] == "copia"
    assert (tmp_path / "origem" / "subpasta" / "arquivo.txt").exists()
    assert (tmp_path / "copia" / "subpasta" / "arquivo.txt").read_text(encoding="utf-8") == "conteudo"


def test_copy_rejects_directory_inside_itself(tmp_path, monkeypatch):
    monkeypatch.setenv("FILE_SERVER_ROOT", str(tmp_path))
    set_auth_tokens(monkeypatch)
    (tmp_path / "origem").mkdir()
    client = TestClient(app)

    response = client.post(
        "/api/copy",
        data={"source_path": "origem", "target_path": "origem/subpasta"},
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 400


def test_rename_file(tmp_path, monkeypatch):
    monkeypatch.setenv("FILE_SERVER_ROOT", str(tmp_path))
    set_auth_tokens(monkeypatch)
    (tmp_path / "antigo.txt").write_text("conteudo", encoding="utf-8")
    client = TestClient(app)

    response = client.post(
        "/api/rename",
        data={"source_path": "antigo.txt", "new_name": "novo.txt"},
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    assert response.json()["target_path"] == "novo.txt"
    assert not (tmp_path / "antigo.txt").exists()
    assert (tmp_path / "novo.txt").read_text(encoding="utf-8") == "conteudo"


def test_rename_rejects_name_with_path_separator(tmp_path, monkeypatch):
    monkeypatch.setenv("FILE_SERVER_ROOT", str(tmp_path))
    set_auth_tokens(monkeypatch)
    (tmp_path / "arquivo.txt").write_text("conteudo", encoding="utf-8")
    client = TestClient(app)

    response = client.post(
        "/api/rename",
        data={"source_path": "arquivo.txt", "new_name": "pasta/arquivo.txt"},
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 400
