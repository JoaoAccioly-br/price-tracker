"""Testes para empacotamento Docker e download da extensão de navegador."""
from pathlib import Path
import io
import zipfile
import pytest
from fastapi.testclient import TestClient

from app.main import app

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_dockerignore_includes_extension():
    dockerignore_path = PROJECT_ROOT / ".dockerignore"
    assert dockerignore_path.exists(), ".dockerignore não encontrado"
    content = dockerignore_path.read_text(encoding="utf-8")
    assert "!extension/" in content, ".dockerignore deve incluir '!extension/' na whitelist"


def test_dockerfile_copies_extension():
    dockerfile_path = PROJECT_ROOT / "Dockerfile"
    assert dockerfile_path.exists(), "Dockerfile não encontrado"
    content = dockerfile_path.read_text(encoding="utf-8")
    assert "COPY extension/ ./extension/" in content, "Dockerfile deve conter 'COPY extension/ ./extension/'"


def test_api_extension_download_endpoint():
    with TestClient(app) as client:
        resp = client.get("/api/extension/download")
        assert resp.status_code == 200
        assert "application/zip" in resp.headers.get("content-type", "")
        assert 'attachment; filename="price-tracker-extension.zip"' in resp.headers.get("content-disposition", "")

        # Valida que o zip é válido e contém os arquivos essenciais
        with zipfile.ZipFile(io.BytesIO(resp.content), "r") as zf:
            namelist = zf.namelist()
            assert "price-tracker-extension/manifest.json" in namelist
            assert "price-tracker-extension/popup.js" in namelist
