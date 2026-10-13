from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime
from functools import lru_cache
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

from src.config import (  # noqa: E402
    CSV_DIR,
    EXCEL_DIR,
    IMAGE_DIR,
    LOG_DIR,
    PDF_DIR,
    REPORT_DIR,
    RAW_DIR,
    VECTOR_DIR,
)


ACCOUNTS_DB = Path(os.getenv("STRIVEN_API_ACCOUNTS_DB", str(LOG_DIR / "accounts.sqlite3")))
AUDIT_LOG = Path(os.getenv("STRIVEN_API_AUDIT_LOG", str(LOG_DIR / "streamlit_audit.log")))
TOKEN_TTL_SECONDS = 12 * 60 * 60
PASSWORD_HASH_ITERATIONS = 600_000
MAX_UPLOAD_BYTES = 30 * 1024 * 1024
INDEX_FILES = ("index.faiss", "records.json", "bm25.pkl", "manifest.json")
UPLOAD_DIRS = {
    ".pdf": PDF_DIR,
    ".png": IMAGE_DIR,
    ".jpg": IMAGE_DIR,
    ".jpeg": IMAGE_DIR,
    ".webp": IMAGE_DIR,
    ".bmp": IMAGE_DIR,
    ".tif": IMAGE_DIR,
    ".tiff": IMAGE_DIR,
    ".xlsx": EXCEL_DIR,
    ".xlsm": EXCEL_DIR,
    ".xls": EXCEL_DIR,
    ".csv": CSV_DIR,
}
INDEX_LOCK = threading.Lock()
BEARER = HTTPBearer(auto_error=False)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=40)
    password: str = Field(min_length=1, max_length=256)


class PasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=12, max_length=256)


class EmployeeRequest(BaseModel):
    username: str = Field(min_length=3, max_length=40)
    password: str = Field(min_length=12, max_length=256)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)


def _audit_logger() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("document_assistant.audit")
    if not logger.handlers:
        handler = RotatingFileHandler(
            AUDIT_LOG,
            maxBytes=1_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


def audit(event: str, **details: Any) -> None:
    _audit_logger().info(
        json.dumps({"event": event, **details}, ensure_ascii=False, default=str)
    )


def _hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PASSWORD_HASH_ITERATIONS,
    )
    return salt.hex(), digest.hex()


def _initialize_accounts() -> None:
    credentials = {
        "ADMIN_USERNAME": os.getenv("ADMIN_USERNAME", "").strip(),
        "ADMIN_PASSWORD": os.getenv("ADMIN_PASSWORD", ""),
        "EMPLOYEE_USERNAME": os.getenv("EMPLOYEE_USERNAME", "").strip(),
        "EMPLOYEE_PASSWORD": os.getenv("EMPLOYEE_PASSWORD", ""),
    }
    missing = [
        name
        for name, value in credentials.items()
        if not value
        or value.casefold().startswith(("choose_", "replace_with_", "your_"))
    ]
    if missing:
        raise RuntimeError(
            "Configure valid admin and employee credentials in the project .env file: "
            + ", ".join(missing)
        )
    admin_username = credentials["ADMIN_USERNAME"]
    employee_username = credentials["EMPLOYEE_USERNAME"]
    if admin_username.casefold() == employee_username.casefold():
        raise RuntimeError("Admin and employee usernames must be different.")

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(ACCOUNTS_DB) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                username TEXT PRIMARY KEY COLLATE NOCASE,
                role TEXT NOT NULL CHECK (role IN ('admin', 'employee')),
                password_salt TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                created_by TEXT NOT NULL
            )
            """
        )
        for username, password, role in (
            (admin_username, credentials["ADMIN_PASSWORD"], "admin"),
            (employee_username, credentials["EMPLOYEE_PASSWORD"], "employee"),
        ):
            row = connection.execute(
                "SELECT role, active FROM accounts WHERE username = ? COLLATE NOCASE",
                (username,),
            ).fetchone()
            if row:
                if row[0] != role or not row[1]:
                    raise RuntimeError(
                        f"The configured {role} account conflicts with an existing account."
                    )
                continue
            salt, password_hash = _hash_password(password)
            connection.execute(
                """
                INSERT INTO accounts
                    (username, role, password_salt, password_hash, created_by)
                VALUES (?, ?, ?, ?, 'environment')
                """,
                (username, role, salt, password_hash),
            )


def _account(username: str) -> tuple[str, str, str, str, int] | None:
    with sqlite3.connect(ACCOUNTS_DB) as connection:
        row = connection.execute(
            """
            SELECT username, role, password_salt, password_hash, active
            FROM accounts
            WHERE username = ? COLLATE NOCASE
            """,
            (username,),
        ).fetchone()
    if not row:
        return None
    return row[0], row[1], row[2], row[3], row[4]


def _verify_password(username: str, password: str) -> str | None:
    account = _account(username)
    if not account or not account[4]:
        return None
    _, role, salt, stored_hash, _ = account
    _, actual_hash = _hash_password(password, bytes.fromhex(salt))
    return role if hmac.compare_digest(actual_hash, stored_hash) else None


def _token_secret() -> bytes:
    secret = os.getenv("API_SECRET_KEY", "")
    if len(secret) < 32 or secret.casefold().startswith(("choose_", "replace_with_", "your_")):
        raise RuntimeError("API_SECRET_KEY must contain at least 32 characters.")
    return secret.encode("utf-8")


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _create_token(username: str, role: str) -> str:
    payload = _b64encode(
        json.dumps(
            {"username": username, "role": role, "exp": int(time.time()) + TOKEN_TTL_SECONDS},
            separators=(",", ":"),
        ).encode("utf-8")
    )
    signature = _b64encode(
        hmac.new(_token_secret(), payload.encode("ascii"), hashlib.sha256).digest()
    )
    return f"{payload}.{signature}"


def _read_token(token: str) -> dict[str, Any]:
    try:
        payload, signature = token.split(".", maxsplit=1)
        expected = _b64encode(
            hmac.new(_token_secret(), payload.encode("ascii"), hashlib.sha256).digest()
        )
        if not hmac.compare_digest(signature, expected):
            raise ValueError("Invalid signature.")
        data = json.loads(_b64decode(payload))
        if data["exp"] < int(time.time()):
            raise ValueError("Expired token.")
        if data["role"] not in {"admin", "employee"}:
            raise ValueError("Invalid role.")
        return data
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=401, detail="Your session has expired. Sign in again.") from exc


def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(BEARER),
) -> dict[str, str]:
    if credentials is None or credentials.scheme.casefold() != "bearer":
        raise HTTPException(status_code=401, detail="Sign in to continue.")
    token_user = _read_token(credentials.credentials)
    account = _account(token_user["username"])
    if not account or not account[4] or account[1] != token_user["role"]:
        raise HTTPException(status_code=401, detail="This account is inactive. Sign in again.")
    return {"username": account[0], "role": account[1]}


def require_admin(user: dict[str, str] = Depends(current_user)) -> dict[str, str]:
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Administrator access is required.")
    return user


def _source_files() -> list[Path]:
    files = []
    for directory in set(UPLOAD_DIRS.values()):
        if directory.is_dir():
            files.extend(
                path
                for path in directory.iterdir()
                if path.is_file() and path.suffix.lower() in UPLOAD_DIRS
            )
    return sorted(files, key=lambda path: (path.name.casefold(), str(path.parent).casefold()))


def _file_label(path: Path) -> str:
    return path.relative_to(RAW_DIR).as_posix()


def _index_status() -> tuple[bool, dict[str, Any], list[dict[str, Any]]]:
    if not all((VECTOR_DIR / name).is_file() for name in INDEX_FILES):
        return False, {}, []
    manifest = json.loads((VECTOR_DIR / "manifest.json").read_text(encoding="utf-8"))
    records = json.loads((VECTOR_DIR / "records.json").read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise ValueError("The vector store records file must contain a JSON list.")
    return bool(records), manifest, records


def _rebuild_index() -> dict[str, Any]:
    with INDEX_LOCK:
        process = subprocess.run(
            [sys.executable, "-m", "src.main"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=1800,
        )
        if process.returncode:
            audit("index_rebuild_failed", return_code=process.returncode)
            output = process.stderr or process.stdout
            raise HTTPException(
                status_code=500,
                detail={
                    "message": "Indexing failed. The source files remain available.",
                    "log": output[-5000:],
                },
            )

        report_path = REPORT_DIR / "processing_report.json"
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            audit("index_report_read_failed", error=type(exc).__name__)
            raise HTTPException(
                status_code=500,
                detail="Indexing finished, but its processing report could not be read.",
            ) from exc
        if not report.get("vector_store"):
            for name in (*INDEX_FILES, "embeddings.npy"):
                (VECTOR_DIR / name).unlink(missing_ok=True)
        _get_retriever.cache_clear()
        result = {
            "vector_count": report.get("vector_store", {}).get("vector_count", 0),
            "errors": report.get("errors", []),
            "summary": process.stdout[-3000:],
        }
        audit(
            "index_rebuild_completed",
            vector_count=result["vector_count"],
            error_count=len(result["errors"]),
        )
        return result


def _format_location(metadata: dict[str, Any]) -> str:
    parts = []
    if metadata.get("page_start") is not None:
        parts.append(f"Page {metadata['page_start']}-{metadata.get('page_end') or metadata['page_start']}")
    if metadata.get("sheet"):
        parts.append(f"Sheet {metadata['sheet']}")
    if metadata.get("row_start") is not None:
        parts.append(f"Rows {metadata['row_start']}-{metadata.get('row_end') or metadata['row_start']}")
    return " · ".join(parts) or "Location not specified"


@lru_cache(maxsize=1)
def _get_retriever() -> Any:
    from src.retrieval.hybrid_retriever import HybridRetriever

    return HybridRetriever(str(VECTOR_DIR))


@asynccontextmanager
async def lifespan(_: FastAPI):
    _token_secret()
    _initialize_accounts()
    yield


app = FastAPI(title="Research Desk API", version="1.0.0", lifespan=lifespan)
origins = [
    origin.strip()
    for origin in os.getenv(
        "STRIVEN_CORS_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173,http://localhost:4173",
    ).split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/auth/login")
def login(payload: LoginRequest) -> dict[str, Any]:
    role = _verify_password(payload.username.strip(), payload.password)
    if not role:
        audit("login_failed")
        raise HTTPException(status_code=401, detail="The username or password is incorrect.")
    account = _account(payload.username.strip())
    if account is None or not account[4]:
        raise HTTPException(status_code=401, detail="This account is no longer active.")
    audit("login_succeeded", role=role, username=account[0])
    return {
        "token": _create_token(account[0], role),
        "user": {"username": account[0], "role": role},
        "expires_in": TOKEN_TTL_SECONDS,
    }


@app.get("/api/auth/me")
def who_am_i(user: dict[str, str] = Depends(current_user)) -> dict[str, str]:
    return user


@app.post("/api/auth/logout")
def logout(user: dict[str, str] = Depends(current_user)) -> dict[str, bool]:
    audit("logout", role=user["role"], username=user["username"])
    return {"ok": True}


@app.post("/api/auth/password")
def change_password(
    payload: PasswordRequest,
    user: dict[str, str] = Depends(current_user),
) -> dict[str, bool]:
    if not _verify_password(user["username"], payload.current_password):
        raise HTTPException(status_code=400, detail="The current password is incorrect.")
    salt, password_hash = _hash_password(payload.new_password)
    with sqlite3.connect(ACCOUNTS_DB) as connection:
        cursor = connection.execute(
            """
            UPDATE accounts
            SET password_salt = ?, password_hash = ?
            WHERE username = ? COLLATE NOCASE AND active = 1
            """,
            (salt, password_hash, user["username"]),
        )
        if cursor.rowcount != 1:
            raise HTTPException(status_code=401, detail="This account is no longer active.")
    audit("account_password_changed", role=user["role"], username=user["username"])
    return {"ok": True}


@app.get("/api/dashboard")
def dashboard(user: dict[str, str] = Depends(current_user)) -> dict[str, Any]:
    try:
        ready, manifest, records = _index_status()
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        audit("index_status_read_failed", error=type(exc).__name__)
        raise HTTPException(status_code=500, detail=f"Could not read the vector store: {exc}") from exc
    paths = _source_files()
    return {
        "role": user["role"],
        "index_ready": ready,
        "document_count": len(paths),
        "indexed_document_count": len(
            {
                record.get("metadata", {}).get("source")
                for record in records
                if record.get("metadata", {}).get("source")
            }
        ),
        "passage_count": manifest.get("vector_count", len(records)) if ready else 0,
    }


@app.get("/api/documents", dependencies=[Depends(current_user)])
def list_documents() -> dict[str, Any]:
    try:
        ready, _, records = _index_status()
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        audit("index_status_read_failed", error=type(exc).__name__)
        raise HTTPException(status_code=500, detail=f"Could not read the vector store: {exc}") from exc
    indexed = {
        record.get("metadata", {}).get("source")
        for record in records
        if record.get("metadata", {}).get("source")
    }
    return {
        "documents": [
            {
                "path": _file_label(path),
                "name": path.name,
                "type": path.suffix.lstrip(".").upper(),
                "size": path.stat().st_size,
                "indexed": ready and path.name in indexed,
            }
            for path in _source_files()
        ]
    }


@app.post("/api/documents/upload")
def upload_documents(
    files: list[UploadFile] = File(...),
    user: dict[str, str] = Depends(require_admin),
) -> dict[str, Any]:
    if not files:
        raise HTTPException(status_code=400, detail="Choose at least one file to upload.")
    targets: list[tuple[UploadFile, Path, bytes]] = []
    planned: set[Path] = set()
    for uploaded in files:
        safe_name = Path((uploaded.filename or "").replace("\\", "/")).name
        suffix = Path(safe_name).suffix.lower()
        target_dir = UPLOAD_DIRS.get(suffix)
        if not target_dir or safe_name in {"", ".", ".."}:
            raise HTTPException(
                status_code=400,
                detail=f"{uploaded.filename or 'File'} has an unsupported file type.",
            )
        target = target_dir / safe_name
        if target.exists() or target in planned:
            raise HTTPException(
                status_code=409,
                detail=f"A file named {safe_name} already exists. Rename it and try again.",
            )
        content = uploaded.file.read(MAX_UPLOAD_BYTES + 1)
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"{safe_name} exceeds the 30 MB per-file limit.",
            )
        planned.add(target)
        targets.append((uploaded, target, content))

    saved: list[Path] = []
    try:
        for _, target, content in targets:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            saved.append(target)
    except OSError as exc:
        for target in saved:
            target.unlink(missing_ok=True)
        audit("document_upload_failed", role=user["role"], error=type(exc).__name__)
        raise HTTPException(status_code=500, detail=f"Could not save uploaded files: {exc}") from exc

    try:
        result = _rebuild_index()
    except (OSError, subprocess.TimeoutExpired) as exc:
        audit("index_rebuild_failed_to_start", error=type(exc).__name__)
        raise HTTPException(status_code=500, detail=f"Files were saved, but indexing failed: {exc}") from exc
    audit(
        "documents_uploaded",
        role=user["role"],
        files=[_file_label(path) for path in saved],
    )
    return {"uploaded": [_file_label(path) for path in saved], **result}


@app.delete("/api/documents")
def delete_document(
    path: str = Query(min_length=1, max_length=500),
    user: dict[str, str] = Depends(require_admin),
) -> dict[str, Any]:
    target = (RAW_DIR / Path(path)).resolve()
    raw_root = RAW_DIR.resolve()
    if raw_root not in target.parents or target.suffix.lower() not in UPLOAD_DIRS:
        raise HTTPException(status_code=400, detail="Invalid document path.")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="The selected document was not found.")
    try:
        target.unlink()
    except OSError as exc:
        audit("document_delete_failed", role=user["role"], file=path, error=type(exc).__name__)
        raise HTTPException(status_code=500, detail=f"Could not delete the document: {exc}") from exc
    audit("document_deleted", role=user["role"], file=path)
    try:
        result = _rebuild_index()
    except (OSError, subprocess.TimeoutExpired) as exc:
        audit("index_rebuild_failed_to_start", action="delete", error=type(exc).__name__)
        raise HTTPException(
            status_code=500,
            detail=f"The source file was deleted, but indexing failed. Search may be stale: {exc}",
        ) from exc
    return {"deleted": path, **result}


@app.post("/api/index/rebuild", dependencies=[Depends(require_admin)])
def rebuild_index() -> dict[str, Any]:
    try:
        return _rebuild_index()
    except (OSError, subprocess.TimeoutExpired) as exc:
        audit("index_rebuild_failed_to_start", error=type(exc).__name__)
        raise HTTPException(status_code=500, detail=f"Could not start indexing: {exc}") from exc


@app.get("/api/employees", dependencies=[Depends(require_admin)])
def list_employees() -> dict[str, Any]:
    with sqlite3.connect(ACCOUNTS_DB) as connection:
        rows = connection.execute(
            """
            SELECT username, role, created_at, created_by, active
            FROM accounts
            ORDER BY role, username COLLATE NOCASE
            """
        ).fetchall()
    return {
        "employees": [
            {
                "username": row[0],
                "role": row[1],
                "created_at": row[2],
                "managed": row[3] != "environment",
                "active": bool(row[4]),
            }
            for row in rows
            if row[1] == "employee"
        ]
    }


@app.post("/api/employees")
def create_employee(
    payload: EmployeeRequest,
    user: dict[str, str] = Depends(require_admin),
) -> dict[str, str]:
    if not re.fullmatch(r"[A-Za-z0-9._-]{3,40}", payload.username):
        raise HTTPException(
            status_code=422,
            detail="Use 3–40 letters, numbers, dots, underscores, or hyphens for the username.",
        )
    salt, password_hash = _hash_password(payload.password)
    try:
        with sqlite3.connect(ACCOUNTS_DB) as connection:
            existing = connection.execute(
                """
                SELECT role, active, created_by
                FROM accounts
                WHERE username = ? COLLATE NOCASE
                """,
                (payload.username,),
            ).fetchone()
            if existing:
                if existing[0] != "employee" or existing[2] == "environment":
                    audit(
                        "employee_account_creation_rejected",
                        role=user["role"],
                        username=payload.username,
                        reason="protected_account",
                    )
                    raise HTTPException(
                        status_code=409,
                        detail="That username belongs to a configured account and cannot be recreated here.",
                    )
                if existing[1]:
                    audit(
                        "employee_account_creation_rejected",
                        role=user["role"],
                        username=payload.username,
                        reason="username_in_use",
                    )
                    raise HTTPException(
                        status_code=409,
                        detail="That username is already in use. Choose a different username.",
                    )
                connection.execute(
                    """
                    UPDATE accounts
                    SET password_salt = ?, password_hash = ?, active = 1
                    WHERE username = ? COLLATE NOCASE
                    """,
                    (salt, password_hash, payload.username),
                )
                reactivated = True
            else:
                connection.execute(
                    """
                    INSERT INTO accounts
                        (username, role, password_salt, password_hash, created_by)
                    VALUES (?, 'employee', ?, ?, ?)
                    """,
                    (payload.username, salt, password_hash, user["username"]),
                )
                reactivated = False
    except sqlite3.IntegrityError as exc:
        audit(
            "employee_account_creation_rejected",
            role=user["role"],
            username=payload.username,
            reason="username_in_use",
        )
        raise HTTPException(status_code=409, detail="That username is already in use.") from exc
    audit(
        "employee_account_reactivated" if reactivated else "employee_account_created",
        role=user["role"],
        username=payload.username,
    )
    return {
        "username": payload.username,
        "role": "employee",
        "status": "reactivated" if reactivated else "created",
    }


@app.delete("/api/employees/{username}")
def deactivate_employee(
    username: str,
    user: dict[str, str] = Depends(require_admin),
) -> dict[str, bool]:
    with sqlite3.connect(ACCOUNTS_DB) as connection:
        cursor = connection.execute(
            """
            UPDATE accounts SET active = 0
            WHERE username = ? COLLATE NOCASE
                AND role = 'employee'
                AND created_by != 'environment'
                AND active = 1
            """,
            (username,),
        )
    if cursor.rowcount != 1:
        raise HTTPException(status_code=404, detail="This employee login cannot be disabled here.")
    audit("employee_account_disabled", role=user["role"], username=username)
    return {"ok": True}


@app.get("/api/activity")
def activity(
    _: dict[str, str] = Depends(require_admin),
    limit: int = Query(default=100, ge=1, le=200),
) -> dict[str, Any]:
    if not AUDIT_LOG.is_file():
        return {"events": []}
    try:
        lines = AUDIT_LOG.read_text(encoding="utf-8").splitlines()[-limit:]
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not read the activity log: {exc}") from exc
    events = []
    for line in reversed(lines):
        parts = line.split(" ", maxsplit=2)
        if len(parts) != 3:
            continue
        date_part, time_part, payload = parts
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            continue
        timestamp = f"{date_part} {time_part}"
        try:
            timestamp = datetime.strptime(
                timestamp,
                "%Y-%m-%d %H:%M:%S,%f",
            ).isoformat()
        except ValueError:
            pass
        event["timestamp"] = timestamp
        events.append(event)
    return {"events": events}


@app.post("/api/chat")
def chat(
    payload: ChatRequest,
    user: dict[str, str] = Depends(current_user),
) -> dict[str, Any]:
    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="Enter a question about approved documents.")
    try:
        ready, _, records = _index_status()
        if not ready:
            raise HTTPException(status_code=503, detail="There are no indexed documents yet.")
        approved_names = {path.name for path in _source_files()}
        stale_sources = {
            record.get("metadata", {}).get("source")
            for record in records
            if record.get("metadata", {}).get("source")
        } - approved_names
        if user["role"] == "employee" and stale_sources:
            raise HTTPException(
                status_code=503,
                detail="Search is paused because the index references a removed file. Ask an admin to rebuild the index.",
            )
        from src.generation.groq_generator import GroqGenerator

        retrieved = _get_retriever().search(question)
        result = GroqGenerator().answer(question, retrieved)
    except HTTPException:
        raise
    except Exception as exc:
        audit("question_failed", role=user["role"], error=type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail=f"Could not answer the question ({type(exc).__name__}): {exc}",
        ) from exc
    audit(
        "question_answered",
        role=user["role"],
        username=user["username"],
        source_count=len(result["sources"]),
    )
    return {
        "answer": result["answer"],
        "sources": [
            {
                **source,
                "location": _format_location(source),
                "passage": record.get("text", ""),
            }
            for source, record in zip(result["sources"], retrieved)
        ],
    }
