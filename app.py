from __future__ import annotations

import json
import hmac
import hashlib
import logging
import os
import re
import sqlite3
import subprocess
import sys
import secrets
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TYPE_CHECKING, Any

import streamlit as st
from dotenv import load_dotenv

from src.config import (
    CSV_DIR,
    EXCEL_DIR,
    IMAGE_DIR,
    PDF_DIR,
    LOG_DIR,
    RAW_DIR,
    VECTOR_DIR,
)
from src.generation.groq_generator import GroqGenerator

if TYPE_CHECKING:
    from src.retrieval.hybrid_retriever import HybridRetriever


ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

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
INDEX_FILES = ("index.faiss", "records.json", "bm25.pkl", "manifest.json")
SUPPORTED_SUFFIXES = frozenset(UPLOAD_DIRS)
LOGIN_LOGO = ROOT / "Image.png"
AUDIT_LOG = Path(
    os.getenv("STRIVEN_TEST_AUDIT_LOG", str(LOG_DIR / "streamlit_audit.log"))
)
ACCOUNTS_DB = Path(
    os.getenv("STRIVEN_TEST_ACCOUNTS_DB", str(LOG_DIR / "accounts.sqlite3"))
)
PASSWORD_HASH_ITERATIONS = 600_000


def get_audit_logger() -> logging.Logger:
    logger = logging.getLogger("document_assistant.audit")
    if not logger.handlers:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
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
    get_audit_logger().info(
        json.dumps({"event": event, **details}, ensure_ascii=False, default=str)
    )


def configured_secret(value: str) -> bool:
    return bool(value) and not value.casefold().startswith(
        ("choose_", "replace_with_", "your_")
    )


def hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_bytes(16)
    password_hash = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PASSWORD_HASH_ITERATIONS,
    )
    return salt.hex(), password_hash.hex()


def initialize_accounts(admin_username: str, admin_password: str,
                        employee_username: str, employee_password: str) -> None:
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
            (admin_username, admin_password, "admin"),
            (employee_username, employee_password, "employee"),
        ):
            existing = connection.execute(
                """
                SELECT role, active
                FROM accounts
                WHERE username = ? COLLATE NOCASE
                """,
                (username,),
            ).fetchone()
            if existing:
                if existing[0] != role or not existing[1]:
                    raise ValueError(
                        f"The configured {role} account conflicts with an existing account. "
                        "Resolve the account in the local account database before starting."
                    )
                continue
            salt, password_hash = hash_password(password)
            connection.execute(
                """
                INSERT INTO accounts (username, role, password_salt, password_hash, created_by)
                VALUES (?, ?, ?, ?, 'environment')
                """,
                (username, role, salt, password_hash),
            )


def authenticate(username: str, password: str) -> str | None:
    with sqlite3.connect(ACCOUNTS_DB) as connection:
        row = connection.execute(
            """
            SELECT role, password_salt, password_hash, active
            FROM accounts
            WHERE username = ? COLLATE NOCASE
            """,
            (username,),
        ).fetchone()
    if not row or not row[3]:
        return None
    salt = bytes.fromhex(row[1])
    _, password_hash = hash_password(password, salt)
    if hmac.compare_digest(password_hash, row[2]):
        return row[0]
    return None


def get_active_account_role(username: str) -> str | None:
    with sqlite3.connect(ACCOUNTS_DB) as connection:
        row = connection.execute(
            """
            SELECT role
            FROM accounts
            WHERE username = ? COLLATE NOCASE AND active = 1
            """,
            (username,),
        ).fetchone()
    return row[0] if row else None


def list_accounts() -> list[tuple[str, str, str, str]]:
    with sqlite3.connect(ACCOUNTS_DB) as connection:
        return connection.execute(
            """
            SELECT username, role, created_at, created_by
            FROM accounts
            WHERE active = 1
            ORDER BY role, username COLLATE NOCASE
            """
        ).fetchall()


def create_employee_account(username: str, password: str, created_by: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9._-]{3,40}", username):
        raise ValueError("Use 3–40 letters, numbers, dots, underscores, or hyphens for the username.")
    if len(password) < 12:
        raise ValueError(
            f"Use at least 12 characters for the temporary password "
            f"(currently {len(password)})."
        )
    salt, password_hash = hash_password(password)
    with sqlite3.connect(ACCOUNTS_DB) as connection:
        connection.execute(
            """
            INSERT INTO accounts (username, role, password_salt, password_hash, created_by)
            VALUES (?, 'employee', ?, ?, ?)
            """,
            (username, salt, password_hash, created_by),
        )


def deactivate_employee_account(username: str) -> bool:
    with sqlite3.connect(ACCOUNTS_DB) as connection:
        cursor = connection.execute(
            """
            UPDATE accounts
            SET active = 0
            WHERE username = ? COLLATE NOCASE
                AND role = 'employee'
                AND created_by != 'environment'
                AND active = 1
            """,
            (username,),
        )
        return cursor.rowcount == 1


def update_account_password(username: str, password: str) -> None:
    salt, password_hash = hash_password(password)
    with sqlite3.connect(ACCOUNTS_DB) as connection:
        cursor = connection.execute(
            """
            UPDATE accounts
            SET password_salt = ?, password_hash = ?
            WHERE username = ? COLLATE NOCASE AND active = 1
            """,
            (salt, password_hash, username),
        )
        if cursor.rowcount != 1:
            raise ValueError("The signed-in account is no longer active.")


def source_files() -> list[Path]:
    files = []
    for directory in set(UPLOAD_DIRS.values()):
        if directory.is_dir():
            files.extend(
                path
                for path in directory.iterdir()
                if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
            )
    return sorted(files, key=lambda path: (path.name.casefold(), str(path.parent).casefold()))


def clear_vector_index() -> None:
    for name in (*INDEX_FILES, "embeddings.npy"):
        path = VECTOR_DIR / name
        if path.is_file():
            path.unlink()


def file_label(path: Path) -> str:
    return str(path.relative_to(RAW_DIR))


st.set_page_config(
    page_title="Document Assistant",
    page_icon="R",
    layout="wide",
)

st.markdown(
    """
    <style>
    :root {
        color-scheme: dark;
        --ink: #f1f1f1;
        --muted: #aaaaaa;
        --green: #ff0033;
        --green-soft: #282828;
        --paper: #0f0f0f;
        --line: #303030;
    }
    html, body, [data-testid="stAppViewContainer"], [data-testid="stMain"],
    [data-testid="stSidebar"], [data-testid="stHeader"] {
        background-color: #0f0f0f !important;
        color: #f1f1f1 !important;
    }
    html, body, [class*="css"] {
        font-family: Inter, 'Segoe UI', sans-serif;
        color: #f1f1f1 !important;
    }
    .stApp, [data-testid="stMainBlockContainer"] {
        background: #0f0f0f !important;
        color: #f1f1f1 !important;
    }
    .stApp p, .stApp li, .stApp label, .stApp h1, .stApp h2,
    .stApp h3, .stApp h4, .stApp h5, .stApp h6, .stApp strong,
    .stApp [data-testid="stMarkdownContainer"],
    .stApp [data-testid="stMarkdownContainer"] p,
    .stApp [data-testid="stCaptionContainer"],
    .stApp [data-testid="stMetricLabel"],
    .stApp [data-testid="stMetricValue"],
    .stApp [data-testid="stMetricDelta"],
    .stApp [data-testid="stChatMessage"] p,
    .stApp [data-testid="stChatMessage"] li,
    .stApp [data-testid="stChatMessage"] strong,
    .stApp [data-testid="stExpander"] summary,
    .stApp [data-testid="stExpander"] p,
    .stApp [data-testid="stFileUploaderDropzone"] {
        color: #f1f1f1 !important;
        -webkit-text-fill-color: #f1f1f1;
    }
    [data-testid="stSidebar"] {
        background: #181818 !important;
        border-right: 1px solid #303030;
    }
    [data-testid="stSidebar"] > div { padding-top: 1.5rem; }
    [data-testid="stHeader"] { background: #0f0f0f !important; }
    h1, h2, h3 {
        font-family: Inter, 'Segoe UI', sans-serif;
        letter-spacing: -0.035em;
    }
    .brand {
        display: flex; align-items: center; gap: .7rem;
        font-family: Inter, 'Segoe UI', sans-serif; font-weight: 800;
        font-size: 1.1rem; color: #f1f1f1; margin-bottom: 1.8rem;
    }
    .brand-mark {
        display: grid; place-items: center; width: 2.15rem; height: 2.15rem;
        border-radius: .7rem; background: #ff0033; color: white;
        font-size: .8rem; letter-spacing: .06em;
    }
    .eyebrow {
        color: #ff0033 !important;
        text-transform: uppercase; letter-spacing: .15em;
        font-size: .72rem; font-weight: 700; margin-bottom: .55rem;
    }
    .hero-title {
        color: #f1f1f1 !important;
        font-size: clamp(2rem, 4vw, 3.25rem);
        line-height: 1.08; margin: 0;
    }
    .hero-copy {
        color: #aaaaaa !important;
        font-size: 1.03rem; max-width: 43rem; margin-top: .8rem;
    }
    .login-art {
        position: fixed;
        inset: 0;
        z-index: 0;
        width: 100vw;
        height: 100vh;
        overflow: hidden;
        border: 0;
        border-radius: 0;
        padding: 0;
        pointer-events: none;
        background:
            radial-gradient(ellipse at 35% 48%, rgba(255, 0, 51, .13), transparent 48%),
            radial-gradient(ellipse at 85% 5%, rgba(116, 20, 41, .18), transparent 38%),
            linear-gradient(145deg, #171719, #0b0b0d 76%);
    }
    .login-art::after {
        position: absolute;
        inset: 0;
        content: "";
        background:
            linear-gradient(90deg, rgba(12, 12, 14, .04) 0%, rgba(12, 12, 14, .1) 45%,
                rgba(12, 12, 14, .76) 100%);
    }
    .st-key-login-brand {
        position: fixed;
        z-index: 1;
        top: 50%;
        left: clamp(1.5rem, 7vw, 7rem);
        width: clamp(17rem, 37vw, 32rem);
        transform: translateY(-50%);
        padding: clamp(.65rem, 1.5vw, 1.2rem);
        border: 1px solid rgba(255, 255, 255, .11);
        border-radius: 1rem;
        background: rgba(255, 255, 255, .96);
        box-shadow: 0 1.5rem 5rem rgba(0, 0, 0, .32);
    }
    .st-key-login-brand [data-testid="stImage"] img {
        display: block;
        width: 100%;
        height: auto;
    }
    .st-key-login-card {
        position: fixed;
        z-index: 1;
        top: 50%;
        right: clamp(1.5rem, 7vw, 7rem);
        width: min(42vw, 42rem);
        max-width: calc(100vw - 2rem);
        box-sizing: border-box;
        margin: 0;
        padding: clamp(1.5rem, 3vw, 2.5rem);
        border: 1px solid rgba(255, 255, 255, .13);
        border-radius: 1.15rem;
        background: rgba(15, 15, 17, .88);
        box-shadow: 0 1.5rem 5rem rgba(0, 0, 0, .38);
        backdrop-filter: blur(14px);
        transform: translateY(-50%);
    }
    @media (max-width: 760px), (max-height: 600px) {
        .login-art::after {
            background: linear-gradient(90deg, rgba(12, 12, 14, .45), rgba(12, 12, 14, .78));
        }
        .st-key-login-brand {
            top: clamp(1rem, 6vh, 3rem);
            left: 50%;
            width: min(72vw, 23rem);
            transform: translateX(-50%);
            padding: .45rem;
        }
        .st-key-login-card {
            position: relative;
            top: auto;
            right: auto;
            left: auto;
            width: auto;
            max-width: none;
            margin: clamp(8rem, 25vh, 12rem) auto 0;
            padding: clamp(1.1rem, 5vw, 1.7rem);
            background: rgba(15, 15, 17, .94);
            transform: none;
        }
    }
    .pill {
        display: inline-block; border: 1px solid #383838; border-radius: 999px;
        background: #212121; padding: .38rem .72rem; margin: .7rem .35rem 0 0;
        color: #e5e5e5 !important; font-size: .8rem;
    }
    div[data-testid="stMetric"] {
        background: #181818 !important;
        border: 1px solid #303030; border-radius: 1rem;
        padding: .8rem 1rem;
    }
    div[data-testid="stChatMessage"] {
        border: 1px solid #303030; border-radius: 1rem;
        background: #181818 !important; padding: .8rem 1rem;
    }
    div[data-testid="stChatInput"] {
        background: #181818 !important; border: 1px solid #383838;
        border-radius: 1rem;
    }
    .stApp input, .stApp textarea,
    .stApp [contenteditable="true"] {
        background-color: #212121 !important;
        color: #f1f1f1 !important;
        -webkit-text-fill-color: #f1f1f1;
        caret-color: #ff0033;
    }
    .stApp input::placeholder, .stApp textarea::placeholder {
        color: #aaaaaa !important;
        -webkit-text-fill-color: #aaaaaa;
    }
    .st-key-login-card [data-testid="stForm"] {
        border: 0;
        padding: 0;
        background: transparent;
    }
    .stButton > button {
        border-radius: .7rem; border: 1px solid #383838;
        background: #212121; color: #f1f1f1 !important;
        font-weight: 600; min-height: 2.7rem;
    }
    .stButton > button[kind="primary"] {
        background: #ff0033; border-color: #ff0033;
    }
    .stButton > button:hover {
        border-color: #ff0033; color: #ffffff !important;
    }
    [class*="workspace-card"] [data-testid="stVerticalBlockBorderWrapper"] {
        height: 100%;
        border-color: #383838;
        background: linear-gradient(145deg, #191919, #131313);
        transition: border-color .15s ease, background .15s ease, transform .15s ease;
    }
    [class*="workspace-card"] [data-testid="stVerticalBlockBorderWrapper"]:hover {
        border-color: #ff0033;
        background: linear-gradient(145deg, #211619, #171315);
        transform: translateY(-2px);
    }
    [class*="workspace-card"] [data-testid="stButton"] > button {
        min-height: 3.2rem;
        justify-content: flex-start;
        font-size: 1rem;
    }
    [data-testid="stExpander"] {
        background: #181818 !important;
        border-color: #303030 !important;
    }
    [data-testid="stFileUploaderDropzone"] {
        background: #212121 !important;
        border-color: #383838 !important;
    }
    hr { border-color: #303030; }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_resource(show_spinner=False)
def get_retriever(vector_dir: str) -> HybridRetriever:
    from src.retrieval.hybrid_retriever import HybridRetriever

    return HybridRetriever(vector_dir)


def get_index_status() -> tuple[bool, dict[str, Any], list[dict[str, Any]]]:
    if not all((VECTOR_DIR / name).is_file() for name in INDEX_FILES):
        return False, {}, []

    manifest = json.loads((VECTOR_DIR / "manifest.json").read_text(encoding="utf-8"))
    records = json.loads((VECTOR_DIR / "records.json").read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise ValueError("The vector store records file must contain a JSON list.")
    return bool(records), manifest, records


def format_location(source: dict[str, Any]) -> str:
    locations = []
    if source.get("page_start") is not None:
        page_end = source.get("page_end") or source["page_start"]
        locations.append(f"Page {source['page_start']}-{page_end}")
    if source.get("sheet"):
        locations.append(f"Sheet {source['sheet']}")
    if source.get("row_start") is not None:
        row_end = source.get("row_end") or source["row_start"]
        locations.append(f"Rows {source['row_start']}-{row_end}")
    return " · ".join(locations) or "Location not specified"


def render_sources(message: dict[str, Any]) -> None:
    sources = message.get("sources", [])
    records = message.get("records", [])
    if not sources:
        return

    with st.expander(f"Sources and retrieved passages ({len(sources)})"):
        for number, (source, record) in enumerate(zip(sources, records), start=1):
            metadata = source or {}
            retrieval = record.get("retrieval", {})
            source_name = metadata.get("source") or "Unknown document"
            neighbor = " · neighboring passage" if retrieval.get("is_neighbor") else ""
            st.markdown(
                f"**{number}. {source_name}**  \n"
                f"{format_location(metadata)}{neighbor}"
            )
            score = retrieval.get("reranker_score")
            if score is not None:
                st.caption(f"Reranker score: {score:.4f}")
            st.write(record.get("text", ""))
            if number < len(sources):
                st.divider()


def render_message(message: dict[str, Any]) -> None:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        if message["role"] == "assistant":
            render_sources(message)


def save_uploads(uploaded_files: list[Any]) -> tuple[list[Path], list[str]]:
    destinations = []
    conflicts = []
    planned_targets = set()
    for uploaded_file in uploaded_files:
        safe_name = Path(uploaded_file.name.replace("\\", "/")).name
        suffix = Path(safe_name).suffix.lower()
        target_dir = UPLOAD_DIRS.get(suffix)
        if target_dir is None or safe_name in {"", ".", ".."}:
            conflicts.append(f"{uploaded_file.name}: unsupported file type")
            continue
        target = target_dir / safe_name
        if target.exists() or target in planned_targets:
            conflicts.append(f"{uploaded_file.name}: a file with this name already exists")
        planned_targets.add(target)
        destinations.append((uploaded_file, target))

    if conflicts:
        return [], conflicts

    saved = []
    try:
        for uploaded_file, target in destinations:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(uploaded_file.getvalue())
            saved.append(target)
    except OSError:
        for target in saved:
            target.unlink(missing_ok=True)
        raise
    return saved, []


def rebuild_index() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "src.main"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def show_build_result(
    process: subprocess.CompletedProcess[str],
    action: str,
) -> None:
    if process.returncode:
        audit("index_rebuild_failed", action=action, return_code=process.returncode)
        if action.startswith("Deletion of "):
            st.error(
                "The source file was removed, but rebuilding the index failed. "
                "Employee search is blocked until an admin completes a successful rebuild."
            )
        else:
            st.error("Indexing failed. The build log is shown below.")
        with st.expander("Build log"):
            output = process.stderr or process.stdout
            st.code(
                output[-5000:]
                or f"Indexer exited with status {process.returncode} and returned no output."
            )
        return

    report_path = ROOT / "reports" / "processing_report.json"
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        audit("index_report_read_failed", action=action, error=type(exc).__name__)
        st.error(f"Indexer completed, but its processing report could not be read: {exc}")
        return

    if not report.get("vector_store"):
        clear_vector_index()

    st.cache_resource.clear()
    st.session_state["index_build_notice"] = {
        "message": f"{action} finished. "
        + (
            f"{report.get('vector_store', {}).get('vector_count', 0)} searchable passages are ready."
            if report.get("vector_store")
            else "No searchable passages were produced."
        ),
        "errors": report.get("errors", []),
        "summary": process.stdout[-3000:],
    }
    audit(
        "index_rebuild_completed",
        action=action,
        vector_count=report.get("vector_store", {}).get("vector_count", 0),
        error_count=len(report.get("errors", [])),
    )
    st.rerun()


st.sidebar.markdown(
    '<div class="brand"><span class="brand-mark">R</span> Research Desk</div>',
    unsafe_allow_html=True,
)

required_credentials = {
    "ADMIN_USERNAME": os.getenv("ADMIN_USERNAME", "").strip(),
    "ADMIN_PASSWORD": os.getenv("ADMIN_PASSWORD", ""),
    "EMPLOYEE_USERNAME": os.getenv("EMPLOYEE_USERNAME", "").strip(),
    "EMPLOYEE_PASSWORD": os.getenv("EMPLOYEE_PASSWORD", ""),
}
missing_credentials = [
    name for name, value in required_credentials.items() if not configured_secret(value)
]

if missing_credentials:
    st.title("Configure sign-in")
    st.error("Sign-in is disabled until both admin and employee credentials are configured.")
    st.write("Set these variables in the private project `.env` file, then restart Streamlit:")
    st.code("\n".join(f"{name}=<choose-a-private-value>" for name in missing_credentials))
    st.stop()

if required_credentials["ADMIN_USERNAME"].casefold() == required_credentials["EMPLOYEE_USERNAME"].casefold():
    st.title("Configure sign-in")
    st.error("The admin and employee usernames must be different.")
    st.stop()

try:
    initialize_accounts(
        required_credentials["ADMIN_USERNAME"],
        required_credentials["ADMIN_PASSWORD"],
        required_credentials["EMPLOYEE_USERNAME"],
        required_credentials["EMPLOYEE_PASSWORD"],
    )
except (sqlite3.Error, ValueError) as exc:
    audit("account_database_initialization_failed", error=type(exc).__name__)
    st.error(f"Could not initialize the account database: {exc}")
    st.stop()

if st.session_state.get("role") not in {"admin", "employee"}:
    st.markdown(
        """
        <style>
        [data-testid="stSidebar"],
        [data-testid="stSidebarCollapseButton"],
        [data-testid="stExpandSidebarButton"] {
            display: none !important;
        }
        [data-testid="stMain"] {
            margin-left: 0 !important;
            width: 100% !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    art_column, login_column = st.columns([1.1, 0.9], gap="large")
    with art_column:
        st.markdown(
            '<div class="login-art" aria-hidden="true"></div>',
            unsafe_allow_html=True,
        )
        if not LOGIN_LOGO.is_file():
            st.warning(
                "Add the Striven logo to the project folder as `Image.png`."
            )
        else:
            with st.container(key="login-brand"):
                st.image(
                    str(LOGIN_LOGO),
                    width="stretch",
                )
    with login_column:
        with st.container(key="login-card"):
            st.title("Sign in to Research Desk")
            with st.form("login_form"):
                username = st.text_input("Username", autocomplete="username")
                password = st.text_input(
                    "Password",
                    type="password",
                    autocomplete="current-password",
                )
                submitted = st.form_submit_button(
                    "Sign in",
                    type="primary",
                    width="stretch",
                )
    if submitted:
        role = authenticate(username.strip(), password)
        if role:
            st.session_state["role"] = role
            st.session_state["username"] = username.strip()
            st.session_state["messages"] = []
            audit("login_succeeded", role=role, username=username.strip())
            st.rerun()
        audit("login_failed")
        st.error("The username or password is incorrect.")
    st.stop()

role = st.session_state["role"]
signed_in_username = st.session_state.get("username", "")
if get_active_account_role(signed_in_username) != role:
    audit("inactive_account_session_rejected", role=role, username=signed_in_username)
    for key in ("role", "username", "messages", "workspace"):
        st.session_state.pop(key, None)
    st.error("This account is no longer active. Sign in with an active account.")
    st.stop()

st.sidebar.caption(f"Signed in as **{role.title()}** · `{signed_in_username}`")
with st.sidebar.expander("Change my password"):
    with st.form("change_password_form"):
        current_password = st.text_input("Current password", type="password")
        new_password = st.text_input("New password (12+ characters)", type="password")
        confirm_new_password = st.text_input("Confirm new password", type="password")
        change_password = st.form_submit_button("Update password", width="stretch")
    if change_password:
        if not authenticate(signed_in_username, current_password):
            st.error("The current password is incorrect.")
        elif new_password != confirm_new_password:
            st.error("The new passwords do not match.")
        elif len(new_password) < 12:
            st.error("Choose a password with at least 12 characters.")
        else:
            try:
                update_account_password(signed_in_username, new_password)
                audit("account_password_changed", role=role, username=signed_in_username)
                st.success("Your password has been updated.")
            except (OSError, sqlite3.Error, ValueError) as exc:
                audit(
                    "account_password_change_failed",
                    role=role,
                    username=signed_in_username,
                    error=type(exc).__name__,
                )
                st.error(f"Could not update the password: {exc}")

if st.sidebar.button("Sign out", width="stretch"):
    audit("logout", role=role, username=st.session_state.get("username"))
    for key in ("role", "username", "messages", "workspace"):
        st.session_state.pop(key, None)
    st.rerun()

if role == "admin":
    if st.session_state.get("workspace") == "Manage documents":
        st.session_state["workspace"] = "Upload documents"
    workspace_options = (
        ("Ask", ":material/forum:", "Ask questions about approved documents."),
        ("Upload documents", ":material/upload_file:", "Add files and rebuild the index."),
        ("Delete document", ":material/delete:", "Remove a source file from the knowledge base."),
        ("Employee details", ":material/group:", "Create and manage employee logins."),
        ("Activity log", ":material/history:", "Review recent sign-in and admin actions."),
    )
    valid_workspaces = {
        label for label, _, _ in workspace_options
    }
    if st.session_state.get("workspace") not in valid_workspaces | {"Workspace"}:
        st.session_state["workspace"] = "Workspace"
    workspace = st.session_state.get("workspace", "Workspace")
    if workspace == "Workspace":
        st.markdown('<div class="eyebrow">Workspace</div>', unsafe_allow_html=True)
        for row_start in range(0, len(workspace_options), 3):
            workspace_row = workspace_options[row_start : row_start + 3]
            card_columns = st.columns(3)
            for column, (label, icon, description) in zip(card_columns, workspace_row):
                with column:
                    with st.container(
                        border=True,
                        key=f"workspace-card-{label.lower().replace(' ', '-')}",
                    ):
                        if st.button(
                            label,
                            icon=icon,
                            type="secondary",
                            width="stretch",
                            key=f"workspace-button-{label.lower().replace(' ', '-')}",
                        ):
                            st.session_state["workspace"] = label
                            st.rerun()
                        st.caption(description)
    elif st.button(
        "← Back to workspace",
        icon=":material/arrow_back:",
        key="back-to-workspace",
    ):
        st.session_state["workspace"] = "Workspace"
        st.rerun()
else:
    workspace = "Ask"
    st.sidebar.caption("Employee access: search and read approved documents only.")

try:
    index_ready, manifest, indexed_records = get_index_status()
except (OSError, json.JSONDecodeError, ValueError) as exc:
    audit("index_status_read_failed", error=type(exc).__name__)
    st.sidebar.error(f"Could not read the vector store: {exc}")
    index_ready, manifest, indexed_records = False, {}, []

documents = sorted(
    {
        record.get("metadata", {}).get("source")
        for record in indexed_records
        if record.get("metadata", {}).get("source")
    }
)
st.sidebar.divider()
st.sidebar.markdown("### Knowledge base")
st.sidebar.metric(
    "Indexed passages",
    manifest.get("vector_count", len(indexed_records)) if index_ready else 0,
)
st.sidebar.caption(f"{len(documents)} indexed document(s)")

build_notice = st.session_state.pop("index_build_notice", None)
if build_notice:
    st.success(build_notice["message"])
    if build_notice["errors"]:
        with st.expander(f"Document processing errors ({len(build_notice['errors'])})"):
            for item in build_notice["errors"]:
                st.write(
                    f"{item.get('file', 'Unknown file')}: "
                    f"{item.get('error', 'Unknown error')}"
                )
    if build_notice["summary"]:
        with st.expander("Build summary"):
            st.code(build_notice["summary"])

if workspace == "Upload documents":
    st.title("Admin · upload documents")
    uploaded_files = st.file_uploader(
        "Choose PDF, image, Excel, or CSV files",
        type=[suffix[1:] for suffix in UPLOAD_DIRS],
        accept_multiple_files=True,
        help="Files are added to the local approved collection and indexed.",
    )
    if st.button(
        "Upload and index",
        type="primary",
        disabled=not uploaded_files,
        width="stretch",
    ):
        try:
            saved_files, conflicts = save_uploads(uploaded_files)
            if conflicts:
                st.error("No files were saved. " + "; ".join(conflicts))
                audit("upload_rejected", role=role, conflict_count=len(conflicts))
            elif saved_files:
                audit(
                    "documents_uploaded",
                    role=role,
                    files=[file_label(path) for path in saved_files],
                )
                with st.spinner("Processing documents and rebuilding the index..."):
                    process = rebuild_index()
                show_build_result(process, f"Upload of {len(saved_files)} document(s)")
        except OSError as exc:
            audit("document_upload_failed", role=role, error=type(exc).__name__)
            st.error(f"Could not save uploaded documents: {exc}")

    if st.button("Rebuild index from all approved files", width="stretch"):
        try:
            with st.spinner("Rebuilding the index from approved source files..."):
                process = rebuild_index()
            show_build_result(process, "Full index rebuild")
        except OSError as exc:
            audit("index_rebuild_failed_to_start", role=role, error=type(exc).__name__)
            st.error(f"Could not start document indexing: {exc}")

elif workspace == "Delete document":
    st.title("Admin · delete document")
    st.subheader("Approved source files")
    existing_files = source_files()
    if existing_files:
        for source_path in existing_files:
            st.write(file_label(source_path))
        source_by_label = {file_label(path): path for path in existing_files}
        with st.form("delete_document_form"):
            selected_label = st.selectbox(
                "Document to delete",
                options=list(source_by_label),
            )
            confirmed = st.checkbox(
                "I understand this permanently removes the selected uploaded file."
            )
            delete_submitted = st.form_submit_button(
                "Delete and rebuild index",
                type="primary",
                width="stretch",
            )
        if delete_submitted:
            if not confirmed:
                st.error("Confirm the deletion before continuing.")
            else:
                target = source_by_label[selected_label]
                try:
                    target.unlink()
                    audit(
                        "document_deleted",
                        role=role,
                        file=file_label(target),
                    )
                    with st.spinner("Rebuilding the index without the deleted document..."):
                        process = rebuild_index()
                    show_build_result(process, f"Deletion of {selected_label}")
                except OSError as exc:
                    audit(
                        "document_delete_failed",
                        role=role,
                        file=selected_label,
                        error=type(exc).__name__,
                    )
                    st.error(
                        f"The source file may already have been removed, but indexing could not "
                        f"complete: {exc}. Employee search will be blocked if the index is stale."
                    )
    else:
        st.info("Upload at least one source document before deleting a file.")

elif workspace == "Employee details":
    st.title("Admin · employee details")
    st.write(
        "Create a separate employee login for each person. New passwords must be at least "
        "12 characters and are stored as salted PBKDF2 hashes."
    )
    with st.form("create_employee_account_form"):
        new_username = st.text_input("New employee username")
        new_password = st.text_input(
            "Temporary password (12+ characters)",
            type="password",
        )
        confirm_password = st.text_input(
            "Confirm temporary password",
            type="password",
        )
        create_account = st.form_submit_button(
            "Create employee login",
            type="primary",
            width="stretch",
        )
    if create_account:
        if new_password != confirm_password:
            st.error("The passwords do not match.")
        else:
            try:
                create_employee_account(
                    new_username.strip(),
                    new_password,
                    st.session_state["username"],
                )
                audit(
                    "employee_account_created",
                    role=role,
                    username=new_username.strip(),
                )
                st.success(f"Employee login created for `{new_username.strip()}`.")
            except ValueError as exc:
                st.error(str(exc))
            except sqlite3.IntegrityError:
                st.error("That username is already in use.")

    accounts = list_accounts()
    account_rows = [
        {"Username": username, "Role": account_role.title(), "Created": created_at}
        for username, account_role, created_at, _ in accounts
    ]
    st.dataframe(account_rows, hide_index=True, width="stretch")

    additional_employees = [
        username
        for username, account_role, _, created_by in accounts
        if account_role == "employee" and created_by != "environment"
    ]
    if additional_employees:
        with st.form("deactivate_employee_account_form"):
            username_to_disable = st.selectbox(
                "Employee login to disable",
                options=additional_employees,
            )
            disable_confirmed = st.checkbox(
                "I understand this will immediately prevent that employee from signing in."
            )
            disable_submitted = st.form_submit_button(
                "Disable employee login",
                width="stretch",
            )
        if disable_submitted:
            if not disable_confirmed:
                st.error("Confirm before disabling the employee login.")
            elif deactivate_employee_account(username_to_disable):
                audit(
                    "employee_account_disabled",
                    role=role,
                    username=username_to_disable,
                )
                st.success(f"Employee login `{username_to_disable}` has been disabled.")
                st.rerun()
            else:
                st.error("This account cannot be disabled here.")

elif workspace == "Activity log":
    st.title("Admin · activity log")
    st.write("Recent sign-in and document-management events. Passwords, API keys, and question text are not logged.")
    if AUDIT_LOG.is_file():
        try:
            log_lines = AUDIT_LOG.read_text(encoding="utf-8").splitlines()
            st.code("\n".join(log_lines[-200:]) or "No activity recorded yet.")
        except OSError as exc:
            st.error(f"Could not read the activity log: {exc}")
    else:
        st.info("No activity has been recorded yet.")

elif workspace == "Ask":
    approved_source_names = {path.name for path in source_files()}
    stale_sources = set(documents) - approved_source_names
    chat_index_ready = index_ready and not (role == "employee" and stale_sources)

    st.markdown('<div class="eyebrow">Grounded answers from your files</div>', unsafe_allow_html=True)
    st.markdown('<h1 class="hero-title">Ask your documents.</h1>', unsafe_allow_html=True)
    st.markdown(
        '<p class="hero-copy">Search approved documents. Answers include retrieved passages '
        'and source locations so you can verify the information.</p>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<span class="pill">Hybrid search</span><span class="pill">Reranked passages</span>'
        '<span class="pill">Cited sources</span>',
        unsafe_allow_html=True,
    )

    metric_columns = st.columns(2)
    metric_columns[0].metric("Documents", len(documents))
    metric_columns[1].metric(
        "Passages",
        manifest.get("vector_count", 0) if index_ready else 0,
    )

    if "messages" not in st.session_state:
        st.session_state.messages = []

    if role == "employee" and stale_sources:
        st.error(
            "Search is temporarily unavailable because the index still references a removed "
            "source document. Ask an admin to rebuild the index."
        )
    elif not index_ready:
        st.warning("There are no indexed documents yet. Ask an admin to upload and index approved files.")
    elif not st.session_state.messages:
        with st.container(border=True):
            st.markdown("#### Start with a question")
            st.write(
                "Ask for a summary, a specific detail, or a comparison. "
                "The assistant will say when the approved documents do not contain enough information."
            )

    for chat_message in st.session_state.messages:
        render_message(chat_message)

    question = st.chat_input(
        "Ask a question about approved documents...",
        disabled=not chat_index_ready,
    )
    if question:
        st.session_state.messages.append({"role": "user", "content": question})
        render_message(st.session_state.messages[-1])
        try:
            with st.spinner("Searching approved documents and preparing an answer..."):
                retriever = get_retriever(str(VECTOR_DIR))
                retrieved_records = retriever.search(question)
                result = GroqGenerator().answer(question, retrieved_records)
            assistant_message = {
                "role": "assistant",
                "content": result["answer"],
                "sources": result["sources"],
                "records": retrieved_records,
            }
            st.session_state.messages.append(assistant_message)
            audit(
                "question_answered",
                role=role,
                source_count=len(result["sources"]),
            )
            render_message(assistant_message)
        except Exception as exc:
            audit("question_failed", role=role, error=type(exc).__name__)
            st.error(
                f"Could not answer the question ({type(exc).__name__}): {exc}"
            )
