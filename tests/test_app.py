from fastapi.testclient import TestClient

from app.main import app


def test_health_uses_configured_shared_root(tmp_path, monkeypatch):
    monkeypatch.setenv("FILE_SERVER_ROOT", str(tmp_path))
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["shared_root"] == str(tmp_path.resolve())


def test_list_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("FILE_SERVER_ROOT", str(tmp_path))
    (tmp_path / "documentos").mkdir()
    (tmp_path / "documentos" / "nota.txt").write_text("ola rede", encoding="utf-8")
    client = TestClient(app)

    response = client.get("/api/list", params={"path": "documentos"})

    assert response.status_code == 200
    body = response.json()
    assert body["path"] == "documentos"
    assert body["items"][0]["name"] == "nota.txt"
    assert body["items"][0]["path"] == "documentos/nota.txt"
    assert body["items"][0]["type"] == "file"


def test_download_file(tmp_path, monkeypatch):
    monkeypatch.setenv("FILE_SERVER_ROOT", str(tmp_path))
    (tmp_path / "arquivo.txt").write_text("conteudo", encoding="utf-8")
    client = TestClient(app)

    response = client.get("/api/download", params={"path": "arquivo.txt"})

    assert response.status_code == 200
    assert response.text == "conteudo"


def test_blocks_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setenv("FILE_SERVER_ROOT", str(tmp_path))
    client = TestClient(app)

    response = client.get("/api/list", params={"path": "../"})

    assert response.status_code == 403
