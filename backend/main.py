import asyncio
import logging
from typing import List, Optional
from pathlib import Path

from fastapi import FastAPI, File, UploadFile, HTTPException, BackgroundTasks, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from pydantic import BaseModel

from backend.telegram_checker import (
    TelegramContactChecker,
    ConfigError,
    CriticalTelegramError,
    parse_phone_numbers,
    parse_excel_bytes,
    logger
)
from backend.ai_extractor import extract_and_normalize_phones
from telethon.errors import FloodWaitError, SessionPasswordNeededError

app = FastAPI(title="Telegram Phone Checker", version="1.0.0")

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception on {request.url.path}: {exc}")
    return JSONResponse(
        status_code=500,
        content={"detail": f"Ошибка сервера ({exc.__class__.__name__}): {str(exc)}"}
    )

# Setup paths
BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"

# Mount static files (CSS/JS)
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

# Shared Application State
checker = TelegramContactChecker()

import os
import openpyxl

# Setup export directory
if os.getenv("VERCEL"):
    EXPORTS_DIR = Path("/tmp/exports")
else:
    EXPORTS_DIR = BASE_DIR / "backend" / "exports"

try:
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
except Exception as e:
    logger.warning(f"Could not create exports dir at {EXPORTS_DIR}: {e}")
    EXPORTS_DIR = Path("/tmp/exports")
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)

def save_server_excel(file_path: Path, items: List[dict]):
    """Generates server-side Excel (.xlsx) file with columns: Имя, Фамилия, Номер телефона, Username, Дата рождения."""
    try:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Контакты Telegram"

        headers = ["Имя", "Фамилия", "Номер телефона", "Username", "Дата рождения"]
        ws.append(headers)

        for item in items:
            fn = (item.get("first_name") or "").strip()
            ln = (item.get("last_name") or "").strip()
            phone = (item.get("phone") or "").strip()
            un = (item.get("username") or "").strip()
            if un and un != "—" and not un.startswith("@"):
                un = "@" + un
            if un == "—":
                un = ""
            bd = (item.get("birthday") or "").strip()

            ws.append([fn, ln, phone, un, bd])

        wb.save(file_path)
        logger.info(f"Saved server-side Excel file with {len(items)} items to {file_path}")
    except Exception as e:
        logger.error(f"Failed to save server Excel file: {e}")

class GlobalState:
    def __init__(self):
        self.status = "idle"  # idle, running, completed, critical_error, stopped
        self.total = 0
        self.processed = 0
        self.found = 0
        self.not_found = 0
        self.error = 0
        self.flood_wait_seconds = 0
        self.critical_error_msg: Optional[str] = None
        self.results: List[dict] = []
        self.contacts: List[dict] = []
        self.excel_path: Path = EXPORTS_DIR / "telegram_contacts.xlsx"
        self._cancel_requested = False
        self._task: Optional[asyncio.Task] = None

    def reset(self, contacts: List[dict]):
        self.status = "idle"
        self.contacts = contacts
        self.total = len(contacts)
        self.processed = 0
        self.found = 0
        self.not_found = 0
        self.error = 0
        self.flood_wait_seconds = 0
        self.critical_error_msg = None
        self.results = []
        self._cancel_requested = False

        # Initialize server-side Excel file immediately after extraction
        initial_results = []
        for c in contacts:
            initial_results.append({
                "phone": c.get("phone", ""),
                "first_name": c.get("first_name", ""),
                "last_name": c.get("last_name", ""),
                "username": c.get("username", ""),
                "birthday": c.get("birthday", ""),
                "status": "PENDING",
                "error": None
            })
        save_server_excel(self.excel_path, initial_results)

state = GlobalState()

# Pydantic Request Models
class SendCodeRequest(BaseModel):
    phone: str

class LoginRequest(BaseModel):
    phone: str
    code: str
    password: Optional[str] = None
    phone_code_hash: Optional[str] = None

class StartCheckRequest(BaseModel):
    batch_size: Optional[int] = 20

class AIExtractRequest(BaseModel):
    text: str
    openai_key: Optional[str] = None

@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Shutting down application, closing Telegram client...")
    await checker.disconnect()

@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    index_file = FRONTEND_DIR / "index.html"
    if not index_file.exists():
        return HTMLResponse("<h1>Frontend index.html not found!</h1>", status_code=404)
    return FileResponse(index_file)

@app.get("/api/config")
async def check_config():
    """Verifies .env file and credentials validity."""
    try:
        checker.initialize_config()
        return {
            "valid": True,
            "api_id": checker.api_id,
            "message": "Configuration loaded successfully."
        }
    except ConfigError as e:
        return {
            "valid": False,
            "api_id": None,
            "message": str(e)
        }

@app.get("/api/auth/status")
async def auth_status():
    """Checks if Telegram client is currently authorized."""
    try:
        is_auth = await checker.check_authorization()
        return {"authorized": is_auth, "error": None}
    except ConfigError as e:
        return {"authorized": False, "error": str(e)}
    except CriticalTelegramError as e:
        return {"authorized": False, "error": str(e)}
    except Exception as e:
        return {"authorized": False, "error": f"Unexpected error: {str(e)}"}

@app.post("/api/auth/send-code")
async def send_code(req: SendCodeRequest):
    """Sends Telegram verification code to user phone."""
    try:
        phone_code_hash = await checker.send_auth_code(req.phone)
        return {"success": True, "phone_code_hash": phone_code_hash}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/auth/login")
async def login(req: LoginRequest):
    """Signs in user using code and optional 2FA password."""
    try:
        await checker.sign_in_with_code(req.phone, req.code, req.password, req.phone_code_hash)
        return {"success": True, "message": "Successfully authorized with Telegram!"}
    except SessionPasswordNeededError:
        raise HTTPException(status_code=401, detail="2FA_PASSWORD_REQUIRED")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    """Receives uploaded numbers text/Excel file and parses unique numbers."""
    try:
        content_bytes = await file.read()
        filename_lower = file.filename.lower() if file.filename else ""
        
        if filename_lower.endswith(".xlsx") or filename_lower.endswith(".xls"):
            phones = parse_excel_bytes(content_bytes)
            contacts = [{"phone": p, "first_name": "", "last_name": "", "username": "", "birthday": ""} for p in phones]
        else:
            raw_text = content_bytes.decode("utf-8", errors="ignore")
            contacts = extract_and_normalize_phones(raw_text)
        
        if not contacts:
            raise HTTPException(status_code=400, detail="Файл не содержит корректных номеров.")
            
        state.reset(contacts)
        return {
            "success": True,
            "filename": file.filename,
            "count": len(contacts),
            "contacts": contacts,
            "preview": contacts[:10]
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Ошибка парсинга файла: {str(e)}")

@app.post("/api/ai-extract")
async def ai_extract(req: AIExtractRequest):
    """Extracts structured contact records from text and normalizes Moldovan numbers via AI / Regex."""
    if not req.text or not req.text.strip():
        raise HTTPException(status_code=400, detail="Введите или вставьте текст для обработки.")

    contacts = extract_and_normalize_phones(req.text, api_key=req.openai_key)
    
    if not contacts:
        raise HTTPException(status_code=400, detail="В предоставленном тексте не найдено телефонных номеров.")

    state.reset(contacts)
    return {
        "success": True,
        "count": len(contacts),
        "contacts": contacts,
        "preview": contacts[:10]
    }

async def run_checking_process(batch_size: int = 20):
    """Background task running batch checking against Telegram API."""
    state.status = "running"
    state._cancel_requested = False
    logger.info(f"Starting phone numbers check: total={state.total}, batch_size={batch_size}")

    # Ensure authorized before starting
    try:
        is_auth = await checker.check_authorization()
        if not is_auth:
            state.status = "critical_error"
            state.critical_error_msg = "Telegram client is not authorized. Please complete authorization."
            return
    except Exception as e:
        state.status = "critical_error"
        state.critical_error_msg = f"Telegram connection error: {str(e)}"
        return

    client_id_counter = 1000

    for i in range(0, len(state.contacts), batch_size):
        if state._cancel_requested:
            logger.info("Check process cancelled by user.")
            state.status = "stopped"
            return

        batch = state.contacts[i : i + batch_size]
        success = False

        while not success and not state._cancel_requested:
            try:
                batch_results = await checker.check_batch(batch, client_id_counter)
                client_id_counter += len(batch)
                
                # Append results and update statistics
                for res in batch_results:
                    state.results.append(res)
                    state.processed += 1
                    if res["status"] == "FOUND":
                        state.found += 1
                    elif res["status"] == "NOT_FOUND":
                        state.not_found += 1
                    else:
                        state.error += 1
                
                # Update server-side Excel file with enriched results
                save_server_excel(state.excel_path, state.results)

                success = True
                state.flood_wait_seconds = 0
                
                # Small polite delay between batches to respect rate limits
                await asyncio.sleep(1.5)

            except FloodWaitError as e:
                logger.warning(f"FloodWaitError: Sleeping for {e.seconds} seconds...")
                state.flood_wait_seconds = e.seconds
                
                # Wait second by second to allow clean cancellation
                for s in range(e.seconds, 0, -1):
                    if state._cancel_requested:
                        state.status = "stopped"
                        return
                    state.flood_wait_seconds = s
                    await asyncio.sleep(1)
                
                state.flood_wait_seconds = 0

            except CriticalTelegramError as e:
                logger.error(f"Critical Telegram Error: {e}")
                state.status = "critical_error"
                state.critical_error_msg = f"CRITICAL ERROR: Telegram API connection failed.\n{str(e)}"
                return

            except Exception as e:
                logger.error(f"Batch execution exception: {e}")
                state.status = "critical_error"
                state.critical_error_msg = f"CRITICAL ERROR: Unexpected error during API check: {str(e)}"
                return

    if not state._cancel_requested and state.status == "running":
        state.status = "completed"
        logger.info("Phone checking process completed successfully.")

@app.post("/api/start")
async def start_check(req: StartCheckRequest):
    """Initiates phone check background task."""
    if not state.contacts:
        raise HTTPException(status_code=400, detail="Сначала извлеките или загрузите контакты.")
    
    if state.status == "running":
        raise HTTPException(status_code=400, detail="Проверка уже выполняется.")

    state.status = "running"
    state._task = asyncio.create_task(run_checking_process(batch_size=req.batch_size or 20))
    return {"success": True, "message": "Check process started."}

@app.post("/api/stop")
async def stop_check():
    """Cancels active phone check task."""
    if state.status == "running":
        state._cancel_requested = True
        state.status = "stopped"
        return {"success": True, "message": "Stopping check process..."}
    return {"success": True, "message": "Process is not running."}

@app.get("/api/status")
async def get_status(offset: int = 0):
    """Returns current status, counters, floodwait state, and new results."""
    new_results = state.results[offset:]
    return {
        "status": state.status,
        "total": state.total,
        "processed": state.processed,
        "found": state.found,
        "not_found": state.not_found,
        "error": state.error,
        "flood_wait_seconds": state.flood_wait_seconds,
        "critical_error_msg": state.critical_error_msg,
        "next_offset": len(state.results),
        "results": new_results
    }

@app.get("/api/download-excel")
async def download_excel():
    """Streams server-side Excel file containing final or current results."""
    if not state.excel_path.exists():
        if state.results:
            save_server_excel(state.excel_path, state.results)
        else:
            raise HTTPException(status_code=400, detail="Нет данных для скачивания.")
    
    return FileResponse(
        path=state.excel_path,
        filename="telegram_contacts.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
