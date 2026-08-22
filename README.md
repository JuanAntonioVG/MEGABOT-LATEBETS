# MEGA BOT LATE BETS

Bot que scrapea Flashscore (fuente de verdad) y varias casas de apuestas,
compara horarios de partidos, y avisa por Telegram cuando hay una
discrepancia — con panel de control remoto por Telegram (qué casas y
deportes están activos, hora de ejecución, paralelismo...).

Ver [ROADMAP.md](ROADMAP.md) para el diseño completo y el porqué de cada
decisión. Este README es solo la guía de puesta en marcha.

## Requisitos

- Python 3.12+
- [Ollama](https://ollama.com) instalado y corriendo localmente, con un
  modelo descargado (`ollama pull llama3.2`) — solo hace falta para
  `/alias revisar`, el resto del bot funciona sin él.

## Puesta en marcha (Windows, PC de pruebas)

```powershell
# 1. Entorno virtual (si no existe ya)
python -m venv .venv
.venv\Scripts\Activate.ps1

# 2. Dependencias (usa requirements-dev.txt si vas a tocar código —
#    incluye ruff/pre-commit/detect-secrets además de lo que necesita el bot)
pip install -r requirements-dev.txt
playwright install chromium

# 3. Configuracion
copy .env.example .env
# Edita .env: token del bot (@BotFather) y tu TELEGRAM_ADMIN_ID
# (pideselo a @userinfobot en Telegram, es tu user_id numerico).

# 4. Hook de seguridad (evita que un secreto real vuelva a colarse en un commit)
pre-commit install
```

## Uso

**Probar un ciclo ahora mismo, sin Telegram** (recomendado para empezar):

```powershell
python run.py --once
```

Scrapea y audita todo lo que esté activo (por defecto, todo lo del
catálogo) y lo imprime en la terminal — nada de esto toca Telegram.

**Arrancar el bot completo** (panel de control + ejecución programada a
diario):

```powershell
python run.py
```

Déjalo corriendo en una terminal. Te escribe `/start` al bot en Telegram
para ver los comandos disponibles: `/panel` (Mini App visual — ver abajo),
`/menu` (activar/desactivar casas y deportes con botones de chat), `/hora`,
`/paralelismo`, `/verbose`, `/ejecutar` (lanza un ciclo ya mismo), `/alias`,
`/estado`.

### Activar el panel visual (Mini App de Telegram) — un paso, una sola vez

`/panel` y el botón junto al campo de texto abren una pantalla de verdad
dentro de Telegram (`docs/panel.html`), no botones de chat, con cuatro
pestañas — pensada para poder controlar y diagnosticar TODO el bot sin
pantalla delante (la Raspberry Pi no tiene una):

- **🏠 Casas** — encender/apagar cada casa+deporte, con "activar/desactivar todo" por casa.
- **📋 Reportes** — las últimas discrepancias encontradas con todo el detalle
  (equipos, hora y liga de cada lado) y las últimas ejecuciones (duración,
  partidos, errores) — esto es lo que abre el botón "Ver detalle" de cada
  notificación, en vez de mandar el detalle como texto de chat.
- **🧠 Alias** — aprobar o rechazar las propuestas de Ollama, borrar un alias
  aprobado que resultó un error, añadir uno a mano, y un botón para pedirle
  a Ollama que revise la cola ahora mismo (antes solo por `/alias revisar`).
- **🛠 Ajustes** — paralelismo, hora diaria, pausar la ejecución programada
  sin perder la hora configurada, modo verboso, y un botón para lanzar un
  ciclo ya mismo.

Todo lo que se marca en el panel se aplica de golpe al pulsar "Guardar" (el
botón nativo de Telegram, abajo) — incluidas las acciones (lanzar un ciclo,
pedirle a Ollama que revise), que corren en segundo plano y avisan por
Telegram aparte cuando terminan, para no bloquear el guardado.

Esa página vive en GitHub Pages, así que hay que activarlo una vez en el repo:

1. En GitHub → tu repo → **Settings → Pages**.
2. **Source**: "Deploy from a branch" → **Branch**: `main` → **Folder**: `/docs` → **Save**.
3. Espera 1-2 minutos a que GitHub lo publique en
   `https://<tu-usuario>.github.io/MEGABOT-LATEBETS/panel.html`.

Si tu usuario de GitHub no es `JuanAntonioVG`, actualiza `URL_BASE_PANEL`
en `telegram_bot/miniapp.py`. El bot no necesita ningún servidor propio
para esto — la página es estática y habla con el bot a través del propio
Telegram (`sendData`), igual de local y privado que el resto del bot.

## Tests

```powershell
pytest tests/ -v
ruff check .
ruff format .
```

## Estructura

```
config/          Ajustes de entorno (.env) y catálogo estático de casas/deportes
core/            Modelos, base de datos SQLite, matcher, orquestador, scheduler, Ollama
scrapers/        Un fichero por casa (+ Flashscore), todos con la misma interfaz
telegram_bot/    Comandos, teclados inline, formateo de notificaciones
tests/           Tests unitarios (matcher y base de datos)
datos/           SQLite y datos locales — NUNCA se sube a git
run.py           Punto de entrada único
```

Añadir una casa de apuestas nueva = un fichero nuevo en `scrapers/`
(implementando `ScraperBase.extraer`) + una entrada en
`config/catalogo_casas.py`. El orquestador no necesita ningún cambio.

## Notas sobre los scrapers

Todos verificados en vivo el 2026-08-22 contra las webs reales (no son
una copia del bot anterior — varias webs habían cambiado por completo,
ver comentarios al principio de cada fichero en `scrapers/`). El más
frágil es `scrapers/winamax.py`: su web usa clases CSS generadas sin
nombre semántico, así que es el primer sitio a revisar si un día deja de
devolver partidos. El resto usa selectores con `data-testid` o clases
semánticas, más resistentes a rediseños.

## Despliegue en Raspberry Pi

Pendiente para cuando el comportamiento en Windows esté validado (ver
Fase 7 del [ROADMAP.md](ROADMAP.md)): mismo código, `git pull` +
`.env`/base de datos copiados a mano, y un servicio `systemd` en vez de
lanzarlo manualmente.
