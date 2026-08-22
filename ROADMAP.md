# MEGA BOT LATE BETS — Roadmap y resumen de diseño

> Documento de referencia con todo lo acordado antes de escribir una sola línea de código.
> Fecha de diseño: 2026-08-22.

---

## 1. Objetivo del proyecto

Bot en Python que:

1. Scrapea **muchas casas de apuestas** (empezando por Bwin, Winamax, Betfair, Pokerstars — ampliable) y **Flashscore** como fuente de verdad, para **todos los deportes** que cada casa soporte.
2. Compara el horario de cada partido entre la casa y Flashscore mediante *fuzzy matching* de nombres de equipo (+ Ollama para casos difíciles).
3. Detecta discrepancias de horario (posibles errores de programación / "late bets") y avisa por **Telegram**, incluyendo la **liga/competición** de cada partido para poder descartar visualmente falsos positivos (mismos equipos, distinta categoría — ej. masculino vs femenino).
4. Se puede **activar/desactivar por casa y por deporte** mediante un menú sencillo (checkboxes por Telegram), sin tocar JSON a mano.
5. Corre primero en **Windows (PC de desarrollo)** para pruebas manuales, y después se traspasa sin cambios de código a una **Raspberry Pi 24/7** (WiFi + corriente, sin pantalla), donde se ejecuta cíclicamente todos los días a las 00:00.
6. Una vez en la Pi, **todo se controla remotamente desde Telegram**: hora de ejecución, casas/deportes activos, nivel de paralelismo, modo de notificación (silencioso/verboso).

---

## 2. Lecciones del proyecto anterior (`Bot_LateBets`)

Lo que se queda:
- Separación orquestador / core (auditor + normalizador) / scrapers / utils.
- Carga dinámica de scrapers por nombre (ya era, de facto, un mini sistema de plugins).
- La idea del "diccionario de alias" de nombres de equipo — se conserva, pero pasa de mantenerse a mano a **crecer asistido por IA local**.
- El filtro de deportes por configuración y el modo headless.

Lo que se corrige:
- **Secretos comiteados en git** (token de Telegram y PAT de GitHub reales, en el repo `bot-latebets` de GitHub). ⚠️ **Acción pendiente del usuario, independiente de este roadmap: revocar ambos tokens si aún no se ha hecho.** En el proyecto nuevo, secretos solo en `.env`, nunca versionado.
- Activar/desactivar casas y deportes editando JSON a mano → menú real.
- Ejecución 100% secuencial (una casa detrás de otra) → paralelismo configurable.
- Selectores CSS hardcodeados y frágiles, sin tests → estructura con fixtures + preferencia por endpoints JSON internos cuando existan.
- `chromedriver.exe` binario comiteado y rutas distintas Windows/Pi → Playwright (gestiona el binario correcto por plataforma automáticamente).
- `print()` sin logging real → logging estructurado.
- Nada de control remoto → bot-servicio con Telegram + scheduler en memoria.

---

## 3. Decisiones de arquitectura

### Stack tecnológico
- **Python** como lenguaje único, dev en Windows con VS Code + Claude Code.
- **Playwright** para scraping (sustituye Selenium/chromedriver.exe) — mismo código en Windows y en Pi (ARM64), instala el navegador correcto por plataforma con `playwright install`. Preferencia adicional: cuando una casa exponga un endpoint JSON interno, usarlo directamente en vez de renderizar la página (más rápido y más robusto a rediseños, especialmente importante en la Pi).
- **`python-telegram-bot`** en modo *long polling* (sin necesidad de IP pública ni webhook) para el panel de control por Telegram.
- **APScheduler** en el mismo proceso para disparar el ciclo de scraping a la hora configurada, y para poder reprogramarlo en caliente cuando se cambia la hora desde Telegram.
- **SQLite** como almacén único de: configuración editable (toggles, hora, paralelismo, modo verboso), histórico de discrepancias y tiempos de ejecución por casa, y diccionario de alias de equipos.
- **`thefuzz`** se mantiene como motor de matching rápido para el caso general (scores altos/bajos).
- **Ollama (local, ya instalado)** como asistente de IA para dos usos, ambos desacoplados del ciclo crítico:
  1. **Offline / asistido**: revisa periódicamente la cola de partidos que el fuzzy no pudo emparejar con confianza y propone nuevas reglas de alias (ej. "Barça" = "FC Barcelona") para que el usuario las apruebe antes de guardarlas en la base de alias.
  2. **Casos límite en caliente (a validar con datos reales)**: para scores de fuzzy en una franja intermedia (ej. 50-85), llamada síncrona a Ollama durante el propio ciclo. Se decidirá si se mantiene en caliente según lo que muestre el modo verboso (tiempo añadido real, no estimado).
- Modelo de datos **tipado** (dataclass/pydantic `Match`) en vez de dicts sueltos — incluye desde el inicio el campo `liga`/competición, capturado por cada scraper y mostrado en la notificación de Telegram (no se compara automáticamente entre casas, solo se muestra para descarte visual del usuario).

### Seguridad
- Whitelist del `user_id` de Telegram en **todos** los comandos que reconfiguran o ejecutan algo — el bot deja de ser solo emisor de avisos y pasa a aceptar comandos, así que cualquier control remoto debe estar restringido a ti.
- `.env` + `.gitignore` para credenciales, nunca JSON versionado.
- Recomendable: hook de pre-commit tipo `gitleaks`/`detect-secrets` para evitar que se repita el incidente del proyecto anterior.

### Control remoto vía Telegram (una vez en la Pi)
- Botones inline (`InlineKeyboardMarkup`) para toggles ON/OFF por casa y por deporte.
- Comandos para: hora de ejecución, nivel de paralelismo, modo silencioso/verboso.
- Aviso de "sigo vivo" (heartbeat) al final de cada ciclo, incluso sin discrepancias.
- Captura de excepciones del ciclo completo → aviso de error por Telegram (no solo log local), crítico al no haber pantalla en la Pi.

### Despliegue Windows → Raspberry Pi
- Mismo código en ambos entornos gracias a Playwright; lo único que cambia es quién supervisa el proceso:
  - **Windows (fase de pruebas)**: lanzado a mano desde terminal/VS Code, con el usuario delante.
  - **Raspberry Pi (24/7 real)**: servicio `systemd` con `Restart=on-failure` y arranque en boot.
- Traspaso vía git: `git clone`/`git pull` en la Pi; `.env` y la base SQLite se copian aparte (no van en git).
- Acceso SSH (o Tailscale) del PC a la Pi para desplegar/depurar sin necesidad de pantalla física.
- Paralelismo se espera menor en la Pi que en el PC por recursos — ajustable con datos reales gracias a la telemetría de tiempos por casa (modo verboso), guardada en SQLite para comparar ejecuciones entre máquinas.

---

## 4. Roadmap por fases

### Fase 0 — Seguridad e higiene (antes de tocar código nuevo)
- [ ] Revocar/regenerar el PAT de GitHub filtrado.
- [ ] Revocar/regenerar el token del bot de Telegram filtrado (vía @BotFather).
- [ ] Confirmar que ambos secretos nuevos solo existirán en `.env`, nunca en git.

### Fase 1 — Fundaciones del proyecto nuevo
- Estructura de carpetas y entorno virtual.
- Modelo de datos tipado (`Match`, config, etc.).
- `.env` + carga de configuración + logging estructurado.
- Base SQLite inicial (esquema: config, histórico, alias, tiempos).

### Fase 2 — Motor de scraping
- Scraper de Flashscore (fuente de verdad) con Playwright.
- Primer scraper de una casa (referencia) con la interfaz común de "plugin".
- Captura de campo `liga` desde el inicio en el modelo de datos.
- Ampliar al resto de casas actuales (Bwin, Winamax, Betfair, Pokerstars) usando la misma interfaz.

### Fase 3 — Auditor y matching
- Migrar el matching fuzzy actual (`thefuzz`) al nuevo modelo tipado.
- Cola de "no encontrados" persistida en SQLite.
- Integración de Ollama en modo offline/asistido para proponer alias.
- (Evaluar con datos reales) integración de Ollama para casos límite en caliente.

### Fase 4 — Notificaciones y panel de control Telegram
- Envío de discrepancias con liga incluida.
- Panel de botones ON/OFF por casa/deporte.
- Comandos de configuración: hora, paralelismo, modo verboso.
- Whitelist de usuario en todos los comandos.
- Heartbeat diario + aviso de errores.

### Fase 5 — Orquestación y scheduler
- APScheduler integrado en el proceso único, reprogramable en caliente.
- Paralelismo configurable en la ejecución de scrapers.
- Telemetría de tiempos por casa (modo verboso) guardada en SQLite.

### Fase 6 — Pruebas en Windows
- Ejecuciones manuales (incluida la de las 00:00) con el usuario delante.
- Validación de toggles, notificaciones, y ajuste inicial de paralelismo.
- Validación del flujo de aprobación de alias con Ollama.

### Fase 7 — Despliegue en Raspberry Pi
- Instalación de dependencias (Playwright/Chromium ARM64, Ollama si aplica).
- Servicio `systemd` con auto-restart.
- Copia de `.env` y base SQLite inicial.
- Acceso SSH configurado desde el PC.
- Comparativa de tiempos PC vs Pi y ajuste fino de paralelismo con datos reales.

### Fase 8 — Futuro / ideas abiertas (no bloqueantes)
- Uso de liga como señal de desempate en el matcher (no solo visual).
- Explorar APIs de scraping como servicio si la Pi se queda corta de recursos o hay bloqueos de IP.
- Otros usos de Ollama, además de alias (a decidir cuando surjan).
- Ampliar el auditor más allá de horarios (ej. comparación de cuotas/mercados).

---

## 5. Decisiones pendientes de afinar durante la construcción (no bloquean el arranque)

- Umbrales exactos de fuzzy score (alto / bajo / franja intermedia para Ollama).
- Modelo concreto de Ollama a usar (referencia inicial: algo pequeño tipo `qwen2.5:3b` o `llama3.2:3b`, a validar con tiempos reales).
- Lista definitiva de casas de apuestas y deportes a cubrir en el primer alcance.
- Formato exacto de los comandos/botones de Telegram.

---

## 6. Próximo paso

Con luz verde del usuario, empezar por la **Fase 0** (si no está hecha) y la **Fase 1** (estructura base del proyecto) dentro de esta misma carpeta `MEGA BOT LATE BETS`.
