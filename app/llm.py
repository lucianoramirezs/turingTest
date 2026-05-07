import os
from typing import Any, Optional

from urllib.parse import urlparse, urlunparse

import httpx
from dotenv import load_dotenv

MOCK_REPLY = "[mock] No hay API configurada; esta es una respuesta de prueba."
GUESSER_LLM_ERROR_MESSAGE = "Hubo un error; espera 10 segundos."


def reply_is_mock(reply: str) -> bool:
    return (reply or "").strip() == MOCK_REPLY.strip()
SYSTEM_PROMPT = (
    "Eres Luciano. No eres un asistente virtual ni un modelo de lenguaje. Estás chateando como una persona real en una conversación casual."
    ""
    "Personalidad:"
    "Luciano es un estudiante universitario que escribe de forma relajada, directa y sin formalidad. No intenta ser especialmente útil ni educado; responde como respondería alguien en WhatsApp o Discord."
    ""
    "Estilo de escritura:"
    "- escribe principalmente en minúsculas"
    "- usa respuestas muy cortas por defecto (1 a 8 palabras)"
    "- evita signos de puntuación innecesarios"
    "- no usa estructuras formales, listas ni explicaciones largas"
    "- no saluda a menos que lo saluden primero"
    "- no se presenta como asistente ni menciona ser IA"
    ""
    "Tono:"
    "- casual, seco y a veces irónico"
    "- puede sonar indiferente o poco impresionado"
    "- es normal que responda con cosas como: no sé, nah, npi, what?, ?"
    "- puede usar spanglish de forma natural"
    ""
    "Lenguaje y muletillas comunes:"
    "- la verdad"
    "- nah"
    "- ez (nunca uses easy, ni sin pensarlo)"
    "- no sé"
    "- what?"
    "- jajaja o JAJAJA"
    ""
    ""
    "Comportamiento ante preguntas:"
    "- prefiere respuestas cortas incluso si la pregunta permitiría una explicación larga"
    "- si no sabe o no le interesa responder, dice no sé, npi o algo equivalente"
    "- si te preguntan cosas que alguien normal no sabría nada más responde *no se*"
    "- si la pregunta es confusa o sin sentido, responde ? o what?"
    "- evita ser repetitivo en tus respuestas; si una vez ya contestaste de cierta forma, para la siguiente cámbiale (no repitas palabras en un chat)"
    ""
    "Datos personales consistentes:"
    "- si le preguntan su nombre: luciano"
    "- si le preguntan si es una IA: no"
    ""
    "Imperfecciones humanas:"
    "- puede omitir acentos"
    "- puede escribir con pequeños errores"
    "- no intenta sonar perfecto ni gramaticalmente impecable"
    ""
    "Reglas estrictas:"
    "- nunca hables como servicio al cliente"
    "- nunca expliques tus respuestas a menos que te lo pidan explícitamente"
    "- prioriza sonar humano y natural sobre ser informativo o completo"
    "- no seas tibio en tus respuestas"
    "- nunca utilices palabras chilenas como fome"
    "- no debes de ser siempre políticamente correcto"
    "- si te piden una pregunta controversial da una respuesta concreta no ambigua"
    "- evita sonar demasiado confiado nunca digas obvio"
    "- nunca uses palabras de español castellano ni chileno, sólo mexicano"
    "- evita decir wey, sólo habla muy neutral"
    )

# Load .env if present (local dev convenience).
load_dotenv()

def _azure_available() -> bool:
    return all(
        os.environ.get(k, "").strip()
        for k in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_DEPLOYMENT")
    )


def _openai_public_available() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY", "").strip())


def _max_history_messages() -> int:
    raw = os.environ.get("LLM_MAX_HISTORY_MESSAGES", "48").strip()
    try:
        n = int(raw)
        return max(2, min(n, 200))
    except ValueError:
        return 48


def _trim_conversation(conv: list[dict[str, str]]) -> list[dict[str, str]]:
    """Mantiene los últimos mensajes; el tramo debe empezar por user para la API."""
    cap = _max_history_messages()
    if len(conv) <= cap:
        return conv
    trimmed = conv[-cap:]
    while trimmed and trimmed[0].get("role") != "user":
        trimmed = trimmed[1:]
    return trimmed


def _extract_content(data: dict) -> str:
    choices = data.get("choices") or []
    if not choices:
        return MOCK_REPLY
    msg = choices[0].get("message") or {}
    content: Optional[str] = msg.get("content")
    if not content:
        return MOCK_REPLY
    return content.strip() or MOCK_REPLY


def _normalize_azure_endpoint(raw: str) -> str:
    """
    Accept either:
    - base endpoint: https://<resource>.openai.azure.com
    - full chat-completions URL copied from portal
    and normalize to the base endpoint.
    """
    s = (raw or "").strip()
    if not s:
        return s

    parsed = urlparse(s)
    scheme = parsed.scheme or "https"
    netloc = parsed.netloc
    path = parsed.path or ""

    # If user pasted full path, keep only the prefix before "/openai/".
    lower_path = path.lower()
    marker = "/openai/"
    if marker in lower_path:
        idx = lower_path.find(marker)
        path = path[:idx]

    base = urlunparse((scheme, netloc, path.rstrip("/"), "", "", ""))
    return base.rstrip("/")


def _azure_reply(conversation: list[dict[str, Any]]) -> str:
    endpoint = _normalize_azure_endpoint(os.environ["AZURE_OPENAI_ENDPOINT"])
    api_key = os.environ["AZURE_OPENAI_API_KEY"].strip()
    deployment = os.environ["AZURE_OPENAI_DEPLOYMENT"].strip()
    api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-12-01-preview").strip()

    url = (
        f"{endpoint}/openai/deployments/{deployment}/chat/completions"
        f"?api-version={api_version}"
    )
    messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(conversation)
    payload = {
        "messages": messages,
        "temperature": 0.7,
    }
    headers = {"api-key": api_key, "Content-Type": "application/json"}
    with httpx.Client(timeout=60.0) as client:
        r = client.post(url, json=payload, headers=headers)
        r.raise_for_status()
        return _extract_content(r.json())


def _openai_public_reply(conversation: list[dict[str, Any]]) -> str:
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
    api_key = os.environ["OPENAI_API_KEY"].strip()
    url = "https://api.openai.com/v1/chat/completions"
    messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(conversation)
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.7,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    with httpx.Client(timeout=60.0) as client:
        r = client.post(url, json=payload, headers=headers)
        r.raise_for_status()
        return _extract_content(r.json())


def generate_reply(conversation: list[dict[str, str]]) -> str:
    """
    conversation: mensajes del canal en orden (user = adivinador, assistant = tú).
    El último mensaje debe ser user (la pregunta actual).
    """
    conv = _trim_conversation(conversation)
    if not conv or conv[-1].get("role") != "user":
        return MOCK_REPLY
    try:
        if _azure_available():
            return _azure_reply(conv)
        if _openai_public_available():
            return _openai_public_reply(conv)
        return MOCK_REPLY
    except Exception:
        return MOCK_REPLY
