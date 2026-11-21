# CCDR Heredia: reservas de instalaciones (prototipo)

Prototipo **gratuito y de código abierto** (licencia MIT) para el Comité Cantonal de Deportes y Recreación de Heredia. Su propósito es mostrar cómo podría funcionar la solicitud de instalaciones del Polideportivo.

> ⚠️ **Usa solo datos ficticios.** No es el sistema oficial del CCDR, no tiene cobros ni tarifas, y no debe cargarse con datos reales hasta cumplir la sección 7 de [SPEC.md](SPEC.md).

**Estado:** Fase 1 (módulo de reservas). El módulo de atletas (Fase 2) **no está implementado**.

## Qué hace

**Público (sin cuenta, pensado para celular):**
- **Calendario semanal por instalación.** Cada espacio aparece como *Disponible* u *Ocupado*, o con el nombre de un programa del CCDR, como "Escuela de voleibol". **Nunca muestra datos personales.**
- **Formulario de solicitud de un paso.** Se llega con un toque desde el calendario e incluye una casilla obligatoria de consentimiento (Ley 8968).
- **Enlace privado de seguimiento** (`/estado#…`), donde la persona ve *Pendiente*, *Aprobada*, *Rechazada*, *Cancelada* o *Vencida*, más la nota de la administración.

**Administración (con inicio de sesión):**
- **Bandeja de pendientes**, ordenada por fecha de uso, con aprobar o rechazar en un clic. Cada solicitud muestra los botones *Llamar* y *WhatsApp*, y avisa con qué choca.
- **Instalaciones y horario semanal**, con varias franjas por día.
- **Bloqueos** para mantenimiento o programas propios, de una sola vez o semanales, con nombre público opcional.

## Cómo correrlo

Requiere **Python 3.12 o superior**. No usa paquetes externos.

```bash
python scripts/seed_demo.py --reset
```

```bash
python server.py
```

- **Calendario público:** <http://127.0.0.1:8770/>
- **Administración:** <http://127.0.0.1:8770/admin/>
- **Credenciales de prueba:** usuario `admin`. La contraseña es aleatoria y queda en `data/demo-credentials.txt`, que git no versiona.

### Pruebas

```bash
python -m unittest discover -s tests -t .
```

Son 59 pruebas. Entre ellas, cubren estos puntos:
- **Condición de carrera:** dos administradores aprueban a la vez solicitudes que se cruzan, 12 rondas contra el servidor real.
- **Privacidad:** se cargan datos personales reconocibles y se verifica que no aparezcan en ninguna página ni respuesta pública.
- **Token de seguimiento:** no se puede adivinar ni enumerar, y la base guarda solo su hash.
- **Acceso:** sesión, CSRF, bloqueo tras 5 intentos fallidos y recorrido de rutas.
- **Reglas de horario:** franjas, bloqueos semanales y anonimización.

### Recorrido en el navegador (2026-11-20)

Las capturas están en [docs/capturas/](docs/capturas/). Se recorrió lo siguiente:

- **Solicitud desde el celular (375 px):** en 23 segundos, desde tocar un espacio disponible del calendario hasta ver la confirmación con el enlace. Esto incluyó un intento rechazado por no marcar el consentimiento. Fue un recorrido automatizado; una persona tardará más en escribir, pero el formulario llega con la instalación, la fecha y la hora ya puestas.
- **Página de estado:** muestra "Pendiente" y luego "Aprobada" con la nota de la administración. Un enlace inventado responde "No encontramos una solicitud con ese enlace".
- **Dos pestañas de administración:** con la bandeja cargada en las dos, la pestaña 2 aprobó a Diego (10:00 a 12:00). La interfaz avisó que choca con Marco (11:00 a 1:00). Después, en la pestaña 1, que no se había recargado, se hizo clic en *Aprobar* para Marco. El servidor respondió 409 y la pantalla mostró el choque (capturas 08 y 09). Además, dos aprobaciones de un par nuevo lanzadas exactamente al mismo tiempo con `fetch` desde el navegador dieron un 200 y un 409.
- **Privacidad:** 16 páginas y respuestas públicas revisadas en el navegador, sin nombres, teléfonos, organizaciones ni correos.
- **Protecciones:** cambiar un horario o crear un bloqueo encima de reservas aprobadas se rechaza y muestra la lista de afectados.
- **Sesión:** al cerrarla, la API de administración responde 401.
- **Registro del servidor:** el token de seguimiento nunca aparece.
- **Consola:** las cuatro páginas, abiertas en una pestaña nueva después de recargar los datos, no muestran errores. Durante el recorrido solo aparecieron los 4xx que se provocaron a propósito.

Notas sobre el recorrido:
- El enlace que se ve en la captura 04 era de datos ficticios y ya no sirve, porque la base se volvió a cargar.
- El servidor se levantó en una pestaña de la terminal (`python server.py`) y no con `preview_start`. El panel del navegador solo lee el `launch.json` de la carpeta donde empezó la sesión. Igual queda [.claude/launch.json](.claude/launch.json) para la próxima sesión.
- No se probó en un celular físico; se usó la emulación de 375 px del navegador integrado.

### Otros scripts

| Script | Para qué |
|---|---|
| `scripts/create_admin.py <usuario>` | Crea un usuario administrador real. La contraseña se pide por teclado y no se guarda en texto plano. |
| `scripts/backup.py [carpeta]` | Respaldo consistente de SQLite. **No está cifrado**: guárdelo en un disco cifrado (ver SPEC 7.4). |
| `scripts/anonymize.py` | Anonimiza solicitudes viejas. El servidor también lo hace al arrancar y una vez al día. |

## Demo pública en Render

El archivo [render.yaml](render.yaml) despliega una demo con **solo datos ficticios** en el plan gratuito de Render. Un *Blueprint* es la receta que Render lee de `render.yaml` para crear el servicio. En modo demo:

- Al arrancar, y cada 24 horas, **se borra todo y se recargan los datos ficticios**. Así, lo que escriban las personas que la prueban no se acumula.
- Todas las páginas dicen **"No escriba datos personales reales"**.
- La contraseña del usuario `admin` la **genera Render**; no está en el repositorio. Se ve en el panel de Render, en *Environment*, en la variable `CCDR_DEMO_ADMIN_PASSWORD`.
- El plan gratis se apaga después de 15 minutos sin visitas. La primera carga tarda cerca de un minuto, y Render puede reiniciar el servicio en cualquier momento (lo que también recarga los datos).

**Cómo publicarla:**
1. Entre a <https://dashboard.render.com> con la cuenta de GitHub.
2. Elija **New → Blueprint** y luego el repositorio `ccdr-heredia`.
3. Pulse **Apply**.

Render crea el servicio `ccdr-heredia-demo`.

**Demo actual:** <https://ccdr-heredia-demo.onrender.com>. Se creó con la opción *Public Git Repository*, sin darle a Render acceso a la cuenta de GitHub, así que **no se vuelve a desplegar sola**. Después de cada `git push` hay que entrar al servicio en Render y elegir **Manual Deploy → Deploy latest commit**. Para que sea automático, conecte GitHub en Render.

## Configuración

Los valores por defecto están en [app/config.py](app/config.py). Se pueden cambiar de dos formas: con un `config.local.json`, que git ignora, o con variables de entorno `CCDR_<CLAVE>`, por ejemplo `CCDR_MIN_NOTICE_HOURS=48`. `HOST` y `PORT` también se leen del entorno.

```json
{ "min_notice_hours": 48, "max_minutes": 180, "secure_cookies": true }
```

Los **límites de reserva son supuestos que el CCDR debe confirmar**:
- Bloques de 30 minutos.
- Duración mínima de 1 hora y máxima de 4 horas.
- Hasta 60 días hacia adelante.
- Al menos 24 horas de anticipación.

## Reglas importantes

- **Choques:** nunca se aprueban dos reservas que se cruzan, ni una reserva sobre un bloqueo. El servidor lo revisa dentro de una transacción `BEGIN IMMEDIATE`. La misma regla aplica cuando se crea un bloqueo, se cambia un horario o se desactiva una instalación: si eso dejaría una reserva aprobada en conflicto, se rechaza con la lista de reservas afectadas.
- **Solicitudes pendientes:** varias pueden pedir el mismo espacio. Al aprobar una, el sistema **advierte** cuáles otras chocan, pero no las rechaza solas.
- **Horarios:** los intervalos son semiabiertos, así que una reserva de 8:00 a 9:00 no choca con otra de 9:00 a 10:00. Una reserva no puede cruzar la medianoche.
- **Vencida:** una solicitud pendiente cuya fecha ya pasó.
- **Retención:** 12 meses después, las solicitudes rechazadas, canceladas o vencidas pierden nombre, organización, teléfono, correo, propósito y nota. Las aprobadas todavía no se anonimizan, porque el CCDR debe definir ese plazo.

## Seguridad

- **Contraseñas:** PBKDF2-SHA256 con 600 000 iteraciones y sal aleatoria. La cuenta se bloquea 15 minutos tras 5 fallos, y hay además un límite de intentos por IP.
- **Sesiones:** cookie `HttpOnly` y `SameSite=Strict`, y `Secure` si se activa `secure_cookies`. Expiran tras 8 horas sin actividad.
- **CSRF:** token `X-CSRF-Token` en todo POST, PUT y DELETE de administración, más verificación del encabezado `Origin`.
- **Base de datos y frontend:** todo el SQL es parametrizado. El frontend inserta datos solo con `textContent`, nunca con `innerHTML`.
- **Encabezados:** CSP estricta sin `unsafe-inline`, `nosniff`, `Referrer-Policy: no-referrer` y `X-Frame-Options: DENY`.
- **Token de seguimiento:** `secrets.token_urlsafe(32)` (256 bits). La base guarda solo su SHA-256. Viaja en el `#fragmento` de la URL, que el navegador nunca envía al servidor ni queda en los registros.
- **Antiabuso sin captcha:** campo trampa oculto, límite de 5 solicitudes por IP por hora, largos máximos en todos los campos y cuerpo máximo de 16 KB.
- **Archivos:** `data/`, que contiene la base, las credenciales de prueba y los respaldos, queda fuera de `static/` y nunca se sirve.

### Limitaciones conocidas

- Los límites por IP viven en memoria: se reinician al reiniciar el servidor y no sirven si se corren varios procesos.
- No se confía en `X-Forwarded-For`. Si se publica detrás de un proxy, todo el tráfico se verá como una sola IP y habría que configurarlo aparte.
- Alguien que conozca el nombre de usuario puede bloquear esa cuenta por 15 minutos fallando 5 veces.
- Con `trust_proxy` activado (como en Render), la IP se toma de `X-Forwarded-For`, que un cliente puede falsificar para saltarse el límite por IP. En la demo se acepta ese riesgo; en producción hay que confiar solo en el proxy propio.
- En la demo pública, cualquiera que conozca la contraseña compartida de `admin` puede aprobar o borrar datos ficticios, y alguien podría bloquear la cuenta 15 minutos fallando 5 veces. Los datos se recargan solos cada día.
- El servidor escucha solo en `127.0.0.1`. Para publicarlo en internet hace falta HTTPS delante (proxy inverso) y `secure_cookies: true`.
- No envía correos ni mensajes; la administración contacta a las personas a mano (SPEC 3.3).

## Estructura

```
server.py            HTTP server + router (port 8770)
app/config.py        defaults + config.local.json
app/db.py            SQLite (WAL), migrations, BEGIN IMMEDIATE helper
app/auth.py          users, PBKDF2, sessions, CSRF, login handlers
app/bookings.py      booking rules + public/admin handlers
app/security.py      rate limiter, headers, validation
app/timeutil.py      fixed UTC-6 time helpers
static/public/       calendar, request form, status page
static/admin/        admin SPA
app/demo.py          fictitious demo dataset (seed script + demo mode)
scripts/             seed_demo, create_admin, backup, anonymize
render.yaml          Render Blueprint for the public demo
tests/               unittest suite
docs/                approved phase 1 proposal
```

## Preguntas abiertas para el CCDR

Ver la sección 9 de [SPEC.md](SPEC.md). Esta fase agrega cuatro:
- **Límites de reserva:** confirmar los supuestos de la sección *Configuración* (bloques de 30 minutos, de 1 a 4 horas, hasta 60 días adelante, 24 horas de anticipación).
- **Retención de aprobadas:** cuánto tiempo guardar los datos de las solicitudes aprobadas.
- **Cancelación por la persona:** si quien solicita debería poder cancelar desde su enlace (hoy solo puede consultar).
- **Aviso de privacidad:** el texto del formulario es preliminar y necesita revisión legal.
