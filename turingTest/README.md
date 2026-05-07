# Turing Test A/B — FastAPI + estático

Aplicación en un solo árbol: API en `app/`, HTML en `static/`, SQLite en la raíz del repo.

## Estructura

| Ruta | Contenido |
|------|-----------|
| `app/main.py` | FastAPI: sesiones, mensajes, humanos, adivinanza |
| `app/llm.py` | Llamadas a Azure/OpenAI y prompt del modelo |
| `app/db.py` | SQLite (`turing.db` en la raíz del proyecto) |
| `app/models.py` | Modelos Pydantic |
| `static/index.html` | Panel del anfitrión: crea sesión y muestra enlaces |
| `static/play.html` | Vista solo adivinador (`/play?t=…`, sin configuración) |
| `static/human.html` | Vista del respondedor humano (`/human?token=…`) |
| `requirements.txt` | Dependencias Python |

## Requisitos

- Python 3.11+ (recomendado)
- Opcional: credenciales Azure OpenAI u OpenAI para respuestas reales; si no, el canal IA usa texto mock

## Instalación y arranque

Desde la **raíz del repo** (no hay carpeta `backend/`):

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Variables de entorno (no commitear secretos):

```bash
cp .env.example .env
# Edita .env: Azure y/o OPENAI_API_KEY según corresponda
```

`python-dotenv` carga `.env` al usar el LLM. Arranca el servidor:

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

- **Anfitrión (tú):** [http://127.0.0.1:8000/](http://127.0.0.1:8000/) — creas la sesión y copias enlaces
- **Adivinador:** enlace `http://127.0.0.1:8000/play?t=…` (lo genera el panel; quien recibe solo abre y chatea)
- **Humano (respondedor):** `http://127.0.0.1:8000/human?token=…` (abre en tu PC el del canal que no es IA)
- **OpenAPI:** [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

No hace falta un `python -m http.server` aparte: el mismo proceso sirve API y estáticos.

## Flujo resumido

1. **Anfitrión:** en `/` marcas IA en A y/o B y pulsas «Nueva sesión».
2. Envías al **adivinador** solo el enlace **`/play?t=<guesser_token>`** (no necesita instalar ni configurar nada).
3. En **tu computadora** abres el enlace **`/human?token=…`** del canal humano (el que no es IA); ves las preguntas y respondes.
4. `POST /message` replica la pregunta en A y B; la IA responde sola en su canal; tú respondes con `POST /human/reply`.
5. `GET /messages` con `guesser_sync=true` oculta respuestas al adivinador hasta que A y B han contestado ese turno.
6. Tras el temporizador, el adivinador elige canal; `POST /guess` acepta `think_ai_in` (desde `/play`) o `choice` (API legacy).

## Endpoints útiles

- `POST /session` — crea sesión; devuelve `session_id`, `guesser_token`, `token_a`, `token_b`, `ai_a` / `ai_b`
- `GET /session/resolve-guesser?token=…` — resuelve el token del enlace `/play` → `{ session_id }` (sin revelar quién es IA)
- `GET /session/status` — temporizador y estado de adivinanza
- `POST /message` — mensaje del adivinador (se envía a ambos canales)
- `GET /messages` — polling por `session_id`, `channel`, `since_id`; opcional `guesser_sync`
- `POST /human/join` — body `{ "token": "<join_token del enlace>" }`; devuelve `channel`, `responder_token`
- `POST /human/reply` — body `{ "responder_token", "content" }`
- `POST /guess` — body `{ "session_id", "think_ai_in": "A" | "B" }` **o** `{ "session_id", "choice": "A" | "B" }` (exactamente uno de los dos)
- `GET /stats` — agregados de aciertos (consulta administrativa)

## Base de datos

Archivo **`turing.db`** en la raíz del proyecto (misma carpeta que `app/`). Se crea al arrancar la app (`init_db`).

## Variables de entorno (LLM)

Ver `.env.example`. Opcional:

- `LLM_MAX_HISTORY_MESSAGES` — tope de mensajes enviados al modelo por canal (por defecto `48`).

## Exponer en internet

Misma idea en todos los casos: el túnel debe apuntar al puerto donde corre FastAPI (`8000`). Luego usa esa URL pública en **URL base para los enlaces** (setup del adivinador) y, si hace falta, abre el adivinador también contra esa base para que `fetch` llegue al mismo backend. La app ya permite CORS amplio.

### Cloudflare Tunnel (`cloudflared`)

[Túneles rápidos](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/do-more-with-tunnels/trycloudflare/) (sin cuenta, URL temporal `*.trycloudflare.com`):

1. Instala el binario **cloudflared** ([descargas](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/) o, en el dev container de este repo, se instala en `postCreateCommand`).
2. Arranca la API: `uvicorn app.main:app --host 0.0.0.0 --port 8000`
3. En otra terminal:

```bash
cloudflared tunnel --url http://127.0.0.1:8000
```

4. Copia la URL `https://xxxx.trycloudflare.com` que imprime la herramienta y úsala como URL base (sin barra final).

Para un dominio fijo y producción, Cloudflare ofrece túneles con nombre y cuenta ([documentación](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/)).

### ngrok

1. `uvicorn app.main:app --host 0.0.0.0 --port 8000`
2. `ngrok http 8000` → URL tipo `https://xxxx.ngrok-free.app`
3. Pégala en **URL base para los enlaces** y abre el adivinador contra esa misma base si procede.

Si sirves solo el HTML en otro hosting, usa la URL del túnel como API base o confía en el CORS del backend.

## Credenciales

- No las pongas en el código.
- Usa `.env` local y mantenlo fuera de git (`.gitignore`).
