from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from agent import Agent
from local_eval import CHANNELS, LocalEnv, dataset_summary, evaluate_campaigns, make_env


ROOT = Path(__file__).parent
STATIC_DIR = ROOT / "frontend"
SUBMISSION_PATH = ROOT / "submission.csv"
profile_rows: List[Dict[str, Any]] | None = None

app = FastAPI(title="Beeline Campaign Agent", version="1.0.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _run_agent() -> Dict[str, Any]:
    env = make_env() if profile_rows is None else LocalEnv(profile_rows)
    campaigns = Agent().act(env)
    summary = evaluate_campaigns(campaigns, env)
    _write_submission(campaigns)
    return {
        "campaigns": campaigns,
        "summary": summary,
        "pilots_used": 20 - int(getattr(env, "pilots_left", 20)),
        "profile_rows": len(env.customer_profile),
        "data_source": "demo" if profile_rows is None else "uploaded_csv",
    }


def _write_submission(campaigns: List[Dict[str, Any]]) -> None:
    fields = [
        "campaign_name",
        "filter_arpu_segment",
        "filter_data_segment",
        "filter_call_segment",
        "filter_current_tariff",
        "target_tariff",
        "channel",
    ]
    with SUBMISSION_PATH.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for campaign in campaigns[:10]:
            writer.writerow({field: campaign.get(field, "") for field in fields})


@app.get("/", response_class=FileResponse)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/status")
def status() -> Dict[str, Any]:
    return {
        "service": "ok",
        "submission_exists": SUBMISSION_PATH.exists(),
        "official_judge": False,
        "profile_rows": None if profile_rows is None else len(profile_rows),
        "data_source": "demo" if profile_rows is None else "uploaded_csv",
        "message": (
            "Используется загруженный профиль клиентов"
            if profile_rows is not None
            else "Используется локальное демонстрационное окружение"
        ),
    }


@app.get("/api/dataset")
def dataset() -> Dict[str, Any]:
    return {
        "tables": dataset_summary(),
        "tariffs": [f"tariff_{index}" for index in range(1, 22)],
        "channels": [
            {"name": channel, "cost": cost, "effectiveness": effect}
            for channel, cost, effect in (
                ("push", 0, 0.50),
                ("sms", 4, 0.65),
                ("digital_ads", 22, 0.85),
                ("call", 160, 1.20),
            )
        ],
        "segments": {
            "arpu": ["LOW", "MID", "HIGH"],
            "data": ["NON_USER", "LITE", "HEAVY"],
            "calls": ["LOW", "MEDIUM", "HIGH"],
        },
    }


@app.post("/api/run")
def run_campaigns() -> Dict[str, Any]:
    try:
        return _run_agent()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Не удалось запустить агента: {exc}") from exc


@app.get("/api/submission")
def submission() -> Dict[str, Any]:
    if not SUBMISSION_PATH.exists():
        return {"campaigns": [], "summary": None}
    with SUBMISSION_PATH.open(encoding="utf-8", newline="") as stream:
        campaigns = list(csv.DictReader(stream))
    return {"campaigns": campaigns, "summary": None}


@app.post("/api/profile")
async def upload_profile(file: UploadFile) -> Dict[str, Any]:
    global profile_rows
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Загрузите CSV-файл профиля клиентов")
    content = await file.read()
    try:
        rows = list(csv.DictReader(io.StringIO(content.decode("utf-8-sig"))))
    except (UnicodeDecodeError, csv.Error) as exc:
        raise HTTPException(status_code=400, detail="Не удалось прочитать CSV") from exc
    required = {
        "current_tariff",
        "arpu_segment",
        "data_segment",
        "call_segment",
        "predicted_arpu",
    }
    missing = required - set(rows[0]) if rows else required
    if missing:
        raise HTTPException(status_code=400, detail=f"В CSV не хватает колонок: {', '.join(sorted(missing))}")
    profile_rows = rows
    return {"rows": len(rows), "message": "Профиль загружен и будет использован при следующем запуске"}
