import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import llm
from app.db import connection, init_db
from app.models import (
    GuessCreate,
    GuessResponse,
    GuesserSessionResolve,
    HumanJoin,
    HumanJoinResponse,
    HumanReply,
    MessageCreate,
    MessageRow,
    HitsOnly,
    SessionCreate,
    SessionResponse,
    SessionStatus,
    StatsResponse,
)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

CHAT_DURATION_MINUTES = 2

app = FastAPI(title="Turing A/B")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


@app.on_event("startup")
def _startup() -> None:
    init_db()


def _token() -> str:
    return secrets.token_urlsafe(32)


def _correct_answer(row) -> str | None:
    """Canal correcto: humano si hay un solo modelo; en dos humanos, clave de calibración."""
    a, b = bool(row["ai_a"]), bool(row["ai_b"])
    if a and not b:
        return "B"
    if b and not a:
        return "A"
    if not a and not b:
        ck = row["calibration_key"]
        return str(ck) if ck else None
    return None


def _choice_from_user_ai_guess(row, ai_guess: str) -> str:
    """
    Convierte «creo que la IA está en X» al valor `choice` que usa la tabla guesses
    (canal del humano cuando hay un solo modelo; en otros casos, igual que la elección directa).
    """
    a, b = bool(row["ai_a"]), bool(row["ai_b"])
    if a and not b:
        return "B" if ai_guess == "A" else "A"
    if b and not a:
        return "A" if ai_guess == "B" else "B"
    return ai_guess


def _guess_count(conn, session_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM guesses WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    return int(row[0]) if row else 0


@app.post("/session", response_model=SessionResponse)
def create_session(body: SessionCreate) -> SessionResponse:
    sid = str(uuid.uuid4())
    j_a, j_b = _token(), _token()
    r_a, r_b = _token(), _token()
    g_tok = _token()
    ai_a = 1 if body.ai_a else 0
    ai_b = 1 if body.ai_b else 0
    calibration_key: str | None = None
    if ai_a == 0 and ai_b == 0:
        calibration_key = secrets.choice(["A", "B"])
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO sessions (
                id, ai_a, ai_b, join_token_a, join_token_b, reply_token_a, reply_token_b,
                guess_unlock_at, calibration_key, guesser_token
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
            """,
            (sid, ai_a, ai_b, j_a, j_b, r_a, r_b, calibration_key, g_tok),
        )
    return SessionResponse(
        session_id=sid,
        guesser_token=g_tok,
        token_a=j_a,
        token_b=j_b,
        ai_a=bool(ai_a),
        ai_b=bool(ai_b),
        guess_unlock_at=None,
    )


def _get_session(conn, session_id: str):
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="session not found")
    return row


def _hits_only(conn, where_clause: str) -> HitsOnly:
    row = conn.execute(
        f"""
        SELECT COALESCE(SUM(CASE WHEN g.was_correct = 1 THEN 1 ELSE 0 END), 0)
        FROM guesses g
        JOIN sessions s ON s.id = g.session_id
        WHERE {where_clause}
        """,
    ).fetchone()
    return HitsOnly(hits=int(row[0]))


@app.get("/stats", response_model=StatsResponse)
def get_stats() -> StatsResponse:
    with connection() as conn:
        dual_human = _hits_only(
            conn, "s.ai_a = 0 AND s.ai_b = 0 AND g.was_correct IS NOT NULL"
        )
        human_and_model = _hits_only(
            conn, "(s.ai_a + s.ai_b) = 1 AND g.was_correct IS NOT NULL"
        )
    return StatsResponse(dual_human=dual_human, human_and_model=human_and_model)


@app.get("/session/resolve-guesser", response_model=GuesserSessionResolve)
def resolve_guesser_token(token: str = Query(..., min_length=1)) -> GuesserSessionResolve:
    t = token.strip()
    with connection() as conn:
        row = conn.execute(
            "SELECT id FROM sessions WHERE guesser_token = ?",
            (t,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="enlace de adivinador no válido")
        return GuesserSessionResolve(session_id=str(row["id"]))


@app.get("/session/status", response_model=SessionStatus)
def session_status(session_id: str = Query(...)) -> SessionStatus:
    with connection() as conn:
        row = _get_session(conn, session_id)
        submitted = _guess_count(conn, session_id) > 0
        raw = row["guess_unlock_at"]
        gu = str(raw) if raw else None
    return SessionStatus(
        guess_unlock_at=gu,
        timer_started=gu is not None,
        guess_submitted=submitted,
        server_now=_iso(_utc_now()),
    )


def _turns_fully_revealed(conn, session_id: str) -> set[int]:
    rows = conn.execute(
        """
        SELECT turn_id FROM messages
        WHERE session_id = ? AND role = 'responder' AND turn_id IS NOT NULL
        GROUP BY turn_id
        HAVING COUNT(DISTINCT channel) = 2
        """,
        (session_id,),
    ).fetchall()
    return {int(r[0]) for r in rows if r[0] is not None}


@app.get("/messages", response_model=list[MessageRow])
def get_messages(
    session_id: str = Query(...),
    channel: str = Query(..., pattern="^(A|B)$"),
    since_id: int = Query(0, ge=0),
    guesser_sync: bool = Query(False, description="Oculta respuestas hasta que A y B hayan contestado ese turno"),
) -> list[MessageRow]:
    with connection() as conn:
        _get_session(conn, session_id)
        rows = conn.execute(
            """
            SELECT id, session_id, channel, role, content, created_at, turn_id
            FROM messages
            WHERE session_id = ? AND channel = ? AND id > ?
            ORDER BY id ASC
            """,
            (session_id, channel, since_id),
        ).fetchall()

        if not guesser_sync:
            return [MessageRow.model_validate(dict(r)) for r in rows]

        failed_turn_rows = conn.execute(
            "SELECT id FROM turns WHERE session_id = ? AND COALESCE(llm_failed, 0) = 1",
            (session_id,),
        ).fetchall()
        failed_turn_ids = {int(r[0]) for r in failed_turn_rows if r[0] is not None}

        revealed = _turns_fully_revealed(conn, session_id)
        out: list[MessageRow] = []
        for r in rows:
            d = dict(r)
            if d["role"] == "guesser" or d["turn_id"] is None:
                out.append(MessageRow.model_validate(d))
                continue
            if int(d["turn_id"]) in revealed:
                tid = int(d["turn_id"])
                if tid in failed_turn_ids and d["role"] == "responder":
                    d = {**d, "content": llm.GUESSER_LLM_ERROR_MESSAGE}
                out.append(MessageRow.model_validate(d))
        return out


def _can_send_message(conn, row, session_id: str) -> tuple[bool, str]:
    if _guess_count(conn, session_id) > 0:
        return False, "la sesión terminó tras registrar la adivinanza"
    raw = row["guess_unlock_at"]
    if not raw:
        return True, ""
    unlock = _parse_iso(str(raw))
    if _utc_now() >= unlock:
        return False, (
            f"se acabó el tiempo de chat ({CHAT_DURATION_MINUTES} min); "
            "envía tu adivinanza A o B"
        )
    return True, ""


def _can_submit_guess(conn, row, session_id: str) -> tuple[bool, str]:
    if _guess_count(conn, session_id) > 0:
        return False, "ya registraste una adivinanza"
    raw = row["guess_unlock_at"]
    if not raw:
        return False, "el tiempo empieza al enviar la primera pregunta; aún no ha comenzado"
    unlock = _parse_iso(str(raw))
    if _utc_now() < unlock:
        return (
            False,
            f"la adivinanza se desbloquea cuando terminen los {CHAT_DURATION_MINUTES} minutos",
        )
    return True, ""


def _insert_responder(
    conn, session_id: str, channel: str, content: str, turn_id: int
) -> None:
    conn.execute(
        """
        INSERT INTO messages (session_id, channel, role, content, turn_id)
        VALUES (?, ?, 'responder', ?, ?)
        """,
        (session_id, channel, content, turn_id),
    )


def _conversation_for_llm(conn, session_id: str, channel: str) -> list[dict[str, str]]:
    """Historial del canal para el chat API: user = adivinador, assistant = respondedor."""
    rows = conn.execute(
        """
        SELECT role, content FROM messages
        WHERE session_id = ? AND channel = ?
        ORDER BY id ASC
        """,
        (session_id, channel),
    ).fetchall()
    out: list[dict[str, str]] = []
    for r in rows:
        api_role = "user" if r["role"] == "guesser" else "assistant"
        out.append({"role": api_role, "content": str(r["content"])})
    return out


@app.post("/message")
def post_message(body: MessageCreate) -> dict:
    text = body.content.strip()
    if not text:
        raise HTTPException(status_code=400, detail="empty content")

    with connection() as conn:
        row = _get_session(conn, body.session_id)
        ok, err = _can_send_message(conn, row, body.session_id)
        if not ok:
            raise HTTPException(status_code=400, detail=err)

        conn.execute(
            """
            UPDATE sessions SET guess_unlock_at = ?
            WHERE id = ? AND guess_unlock_at IS NULL
            """,
            (_iso(_utc_now() + timedelta(minutes=CHAT_DURATION_MINUTES)), body.session_id),
        )

        cur = conn.execute(
            "INSERT INTO turns (session_id) VALUES (?)",
            (body.session_id,),
        )
        turn_id = int(cur.lastrowid)

        for ch in ("A", "B"):
            conn.execute(
                """
                INSERT INTO messages (session_id, channel, role, content, turn_id)
                VALUES (?, ?, 'guesser', ?, ?)
                """,
                (body.session_id, ch, text, turn_id),
            )

        ai_a = bool(row["ai_a"])
        ai_b = bool(row["ai_b"])
        llm_failed = False

        if ai_a:
            conv_a = _conversation_for_llm(conn, body.session_id, "A")
            reply = llm.generate_reply(conv_a)
            if llm.reply_is_mock(reply):
                llm_failed = True
                reply = llm.GUESSER_LLM_ERROR_MESSAGE
            _insert_responder(conn, body.session_id, "A", reply, turn_id)
        if ai_b:
            conv_b = _conversation_for_llm(conn, body.session_id, "B")
            reply = llm.generate_reply(conv_b)
            if llm.reply_is_mock(reply):
                llm_failed = True
                reply = llm.GUESSER_LLM_ERROR_MESSAGE
            _insert_responder(conn, body.session_id, "B", reply, turn_id)

        if llm_failed:
            conn.execute(
                "UPDATE turns SET llm_failed = 1 WHERE id = ?",
                (turn_id,),
            )

    return {"ok": True}


@app.post("/human/join", response_model=HumanJoinResponse)
def human_join(body: HumanJoin) -> HumanJoinResponse:
    tok = body.token.strip()
    with connection() as conn:
        row = conn.execute(
            """
            SELECT id, join_token_a, join_token_b, reply_token_a, reply_token_b
            FROM sessions
            WHERE join_token_a = ? OR join_token_b = ?
            """,
            (tok, tok),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="invalid token")
        sid = row["id"]
        if row["join_token_a"] == tok:
            return HumanJoinResponse(
                session_id=sid, channel="A", responder_token=row["reply_token_a"]
            )
        return HumanJoinResponse(
            session_id=sid, channel="B", responder_token=row["reply_token_b"]
        )


def _pending_turn_id(conn, session_id: str, channel: str) -> int | None:
    row = conn.execute(
        """
        SELECT t.id
        FROM turns t
        JOIN messages g ON g.turn_id = t.id AND g.session_id = t.session_id
            AND g.channel = ? AND g.role = 'guesser'
        WHERE t.session_id = ?
        AND NOT EXISTS (
            SELECT 1 FROM messages r
            WHERE r.session_id = t.session_id AND r.turn_id = t.id
            AND r.channel = ? AND r.role = 'responder'
        )
        ORDER BY t.id ASC
        LIMIT 1
        """,
        (channel, session_id, channel),
    ).fetchone()
    return int(row[0]) if row else None


@app.post("/human/reply")
def human_reply(body: HumanReply) -> dict:
    rt = body.responder_token.strip()
    content = body.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="empty content")

    try:
        with connection() as conn:
            row = conn.execute(
                """
                SELECT id, reply_token_a, reply_token_b, ai_a, ai_b
                FROM sessions
                WHERE reply_token_a = ? OR reply_token_b = ?
                """,
                (rt, rt),
            ).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="invalid responder_token")
            sid = row["id"]
            if row["reply_token_a"] == rt:
                channel = "A"
                if row["ai_a"]:
                    raise HTTPException(status_code=400, detail="channel A is AI-only")
            else:
                channel = "B"
                if row["ai_b"]:
                    raise HTTPException(status_code=400, detail="channel B is AI-only")

            turn_id = _pending_turn_id(conn, sid, channel)
            if turn_id is None:
                raise HTTPException(status_code=400, detail="no hay pregunta pendiente en tu canal")

            conn.execute(
                """
                INSERT INTO messages (session_id, channel, role, content, turn_id)
                VALUES (?, ?, 'responder', ?, ?)
                """,
                (sid, channel, content, turn_id),
            )
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=400,
            detail="ya respondiste esta pregunta (solo una respuesta por turno)",
        ) from None

    return {"ok": True}


@app.post("/guess", response_model=GuessResponse)
def post_guess(body: GuessCreate) -> GuessResponse:
    with connection() as conn:
        row = _get_session(conn, body.session_id)
        ok, err = _can_submit_guess(conn, row, body.session_id)
        if not ok:
            raise HTTPException(status_code=400, detail=err)
        if body.think_ai_in is not None:
            eff_choice = _choice_from_user_ai_guess(row, body.think_ai_in)
        else:
            eff_choice = body.choice  # type: ignore[assignment]
        correct = _correct_answer(row)
        if correct is not None:
            hit = eff_choice == correct
            conn.execute(
                """
                INSERT INTO guesses (session_id, choice, was_correct)
                VALUES (?, ?, ?)
                """,
                (body.session_id, eff_choice, 1 if hit else 0),
            )
            return GuessResponse(scored=True, was_correct=hit)
        conn.execute(
            "INSERT INTO guesses (session_id, choice, was_correct) VALUES (?, ?, NULL)",
            (body.session_id, eff_choice),
        )
    return GuessResponse(scored=False, was_correct=None)


if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def root_index():
    index = STATIC_DIR / "index.html"
    if index.is_file():
        return FileResponse(index)
    raise HTTPException(status_code=404)


@app.get("/human")
def human_page():
    path = STATIC_DIR / "human.html"
    if path.is_file():
        return FileResponse(path)
    raise HTTPException(status_code=404)


@app.get("/play")
def play_page():
    path = STATIC_DIR / "play.html"
    if path.is_file():
        return FileResponse(path)
    raise HTTPException(status_code=404)
