from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


OPERATOR_UI_DIR = Path(__file__).resolve().parents[2] / "ui"
HOUSE_UI_DIR = Path(__file__).resolve().parents[2] / "house_ui"
SATELLITE_UI_DIR = Path(__file__).resolve().parents[2] / "satellite_ui"


def satellite_ui_shell() -> FileResponse:
    return FileResponse(SATELLITE_UI_DIR / "index.html", headers={"Cache-Control": "no-store, max-age=0"})


def satellite_ui_asset(asset_path: str) -> FileResponse:
    asset = (SATELLITE_UI_DIR / asset_path).resolve()
    if SATELLITE_UI_DIR.resolve() not in asset.parents or not asset.is_file():
        raise HTTPException(status_code=404, detail="Satellite UI asset not found")
    return FileResponse(asset, headers={"Cache-Control": "no-store, max-age=0"})


def register_browser_routes(app: FastAPI) -> None:
    app.get("/ui/satellite")(satellite_ui_shell)
    app.get("/ui/satellite/")(satellite_ui_shell)
    app.get("/ui/satellite/assets/{asset_path:path}")(satellite_ui_asset)

    app.mount("/ui", StaticFiles(directory=HOUSE_UI_DIR, html=True), name="oracle-ui")
    app.mount("/admin", StaticFiles(directory=OPERATOR_UI_DIR, html=True), name="oracle-admin")
