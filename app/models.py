from typing import Literal

from pydantic import BaseModel, Field, model_validator


class SessionCreate(BaseModel):
    ai_a: bool = False
    ai_b: bool = False


class SessionResponse(BaseModel):
    session_id: str
    guesser_token: str
    token_a: str
    token_b: str
    ai_a: bool
    ai_b: bool
    guess_unlock_at: str | None = None


class GuesserSessionResolve(BaseModel):
    """Solo session_id: el cliente del adivinador no recibe ai_a/ai_b."""

    session_id: str


class MessageCreate(BaseModel):
    session_id: str
    content: str = Field(..., min_length=1)


class MessageRow(BaseModel):
    id: int
    session_id: str
    channel: str
    role: str
    content: str
    created_at: str
    turn_id: int | None = None


class SessionStatus(BaseModel):
    """guess_unlock_at es null hasta el primer mensaje del adivinador (ahí arranca el temporizador de chat)."""

    guess_unlock_at: str | None = None
    timer_started: bool = False
    guess_submitted: bool
    server_now: str


class HumanJoin(BaseModel):
    token: str = Field(..., min_length=1)


class HumanJoinResponse(BaseModel):
    session_id: str
    channel: str
    responder_token: str


class HumanReply(BaseModel):
    responder_token: str = Field(..., min_length=1)
    content: str = Field(..., min_length=1)


class GuessCreate(BaseModel):
    session_id: str
    choice: Literal["A", "B"] | None = None
    think_ai_in: Literal["A", "B"] | None = Field(
        default=None,
        description="Creo que la IA está en este canal (el backend traduce a choice interno).",
    )

    @model_validator(mode="after")
    def choice_xor_think_ai(self) -> "GuessCreate":
        has_c = self.choice is not None
        has_t = self.think_ai_in is not None
        if has_c == has_t:
            raise ValueError("Indica exactamente uno: choice o think_ai_in")
        return self


class GuessResponse(BaseModel):
    ok: bool = True
    scored: bool = False
    was_correct: bool | None = None


class HitsOnly(BaseModel):
    hits: int


class StatsResponse(BaseModel):
    """Solo aciertos acumulados (sin fallos); pensado para consulta fuera del navegador del adivinador."""

    dual_human: HitsOnly
    human_and_model: HitsOnly
