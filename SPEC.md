# CCDR Heredia — Reservas de instalaciones + Expediente de atletas

> Especificación para arrancar una sesión nueva desde cero. Leer completo antes de escribir código.
> Fecha: 2026-11-20 · Estado: **prototipo para mostrarle al CCDR**, todavía no es un sistema en producción.

## 0. Contexto y por qué

Lo que se sabe del Comité Cantonal de Deportes y Recreación de Heredia (CCDRH), según una revisión pública del 2026-11-20:

- Sede en el **Polideportivo de Fátima** (esquina suroeste de la Plaza de Fátima). Tel. 2260-5241.
- Su canal principal es Facebook (facebook.com/CCDRHeredia, ~35 mil seguidores).
- Sitio ccdrheredia.go.cr a medio construir. **No publica cómo pedir instalaciones** ni horarios.
- Participa en los Juegos Deportivos Nacionales. Según titulares de prensa que aparecieron en una búsqueda (las notas no se abrieron), ganó 12 títulos seguidos en gimnasia rítmica y oro en baloncesto en silla de ruedas en los Paranacionales 2026.

Las fuentes de cada dato están en `C:\Users\esteb\buscador-ccdr\ficha-heredia-2026-11-20.md`.

Lo que **no se sabe** (hipótesis que el CCDR debe confirmar, ver sección 9):

- Cómo reciben hoy las solicitudes de instalaciones (supuesto: teléfono y en persona).
- Cuántas instalaciones administran y con qué horarios.
- Cómo llevan hoy los expedientes de atletas (supuesto: papel o Excel).
- Qué formato exacto pide el ICODER para inscribir atletas en los Juegos.

**Objetivo:** un prototipo funcional con datos ficticios para llevarle al CCDR, validar si les sirve y ajustar con su retroalimentación.

## 1. Reglas del proyecto (obligatorias)

1. **Código 100 % en inglés**: identificadores, comentarios, nombres de tablas y columnas, logs internos y commits. **Solo el texto que ve el usuario va en español.**
2. **Solo librería estándar de Python** (`http.server`, `sqlite3`, `json`, `hashlib`, `secrets`, `hmac`, `ssl`, `unittest`). Sin pip, salvo que se apruebe explícitamente (ver 7.4).
3. **Frontend vanilla**: HTML + CSS + JS, sin bundler ni frameworks. Debe funcionar en celular, porque la mayoría de la gente entra desde ahí.
4. **Solo datos ficticios** en el prototipo. **Nunca cargar datos reales de atletas** hasta que el CCDR lo apruebe y se haga la revisión legal (sección 7).
5. **Probar antes de decir que funciona**: tests automáticos y además recorrer cada flujo en el navegador integrado (consola sin errores, capturas). Un HTTP 200 no es prueba. Lo que no se pudo probar se dice explícitamente.
6. **Revisión de seguridad** después de cada cambio: inyección SQL, XSS, autenticación y permisos, archivos subidos, secretos expuestos.
7. **Sin Claude API.** La app no la necesita. No agregar IA "porque sí".
8. **Entrega gratuita y con código abierto** (licencia MIT), con el código entregado al CCDR. El autor es regidor municipal y el CCDR es un órgano adscrito a la Municipalidad: nada de cobros ni ofertas de servicios, para evitar un posible conflicto de interés (Ley 8422; *el alcance exacto no está verificado y hay que consultarlo con la asesoría legal*).

## 2. Arquitectura

```
ccdr-heredia/
  SPEC.md
  server.py            # entry point: http.server + router
  app/
    db.py              # sqlite connection (WAL), migrations
    auth.py            # users, PBKDF2 hashing, sessions, CSRF
    bookings.py        # module 1 logic + handlers
    athletes.py        # module 2 logic + handlers
    security.py        # rate limit, headers, input validation
  static/
    public/            # public calendar + request form (no login)
    admin/             # admin SPA (login required)
  data/                # app.db + uploads/ — OUTSIDE static/, never served directly
  scripts/
    seed_demo.py       # fake data only
    backup.py
  tests/
```

- Un solo servidor con dos módulos que comparten usuarios y sesiones. **El módulo de atletas se puede apagar por configuración** (`ATHLETES_ENABLED`).
- SQLite en modo WAL, en `data/app.db`.
- El puerto va en una constante al inicio de `server.py` (p. ej., 8770).
- Para probar en el navegador integrado, arrancar el servidor con `preview_start` usando una entrada en `.claude/launch.json` y detenerlo con `preview_stop`. Ojo: en un proyecto anterior, un servidor arrancado así quedó con privilegios elevados y no se pudo detener desde PowerShell. Si vuelve a pasar, decirlo en vez de dejarlo corriendo sin avisar.
- **Python 3.12 o superior.** El módulo `cgi` ya no existe, así que las subidas de archivos se envían como **cuerpo crudo** (`fetch` con el archivo como body y los metadatos en encabezados o en la query), no como multipart.
- **Zona horaria fija UTC−6** (Costa Rica no usa horario de verano), con `datetime.timezone(timedelta(hours=-6))`. No usar `zoneinfo`: en Windows necesita el paquete `tzdata`.
- El servidor usa `ThreadingHTTPServer`, por lo que hay concurrencia real y la prueba de choques tiene sentido.
- **Primer paso: `git init`**, con un `.gitignore` que excluya `data/` y `config.local.json`.

## 3. Módulo 1 — Reserva de instalaciones

### 3.1 Usuarios

| Rol | Puede |
|---|---|
| Público (sin cuenta) | Ver el calendario de disponibilidad, enviar una solicitud, consultar el estado de su solicitud con un enlace privado |
| Administración CCDR | Aprobar, rechazar o cancelar solicitudes; crear bloqueos (mantenimiento, programas propios); administrar instalaciones y horarios |

### 3.2 Datos (tablas)

- `facilities`: id, name, location, sports, capacity, rules_text, active.
- `opening_hours`: facility_id, weekday, opens_at, closes_at.
- `blocks`: facility_id, starts_at, ends_at, reason, recurrence (opcional semanal), created_by. Sirve para mantenimiento y para las escuelas deportivas propias del CCDR.
- `booking_requests`: id, facility_id, starts_at, ends_at, requester_name, organization, phone, email, purpose, expected_attendees, status (`pending|approved|rejected|cancelled`), admin_note, public_token, consent_at, created_at, decided_by, decided_at.

### 3.3 Reglas

- Una solicitud debe caer dentro del horario de la instalación, y no puede chocar con una reserva **aprobada** ni con un bloqueo.
- Varias solicitudes **pendientes** pueden pedir el mismo espacio. Al aprobar una, el sistema advierte cuáles otras chocan.
- La verificación de choques se hace **en el servidor, dentro de una transacción `BEGIN IMMEDIATE`** (revisar el choque y aprobar en la misma transacción): no confiar en el navegador.
- **Privacidad del calendario público:** solo muestra "Disponible" u "Ocupado", sin nombres, teléfonos ni organizaciones. Los bloqueos de programas del CCDR pueden mostrar su nombre (p. ej., "Escuela de voleibol").
- El solicitante recibe un **enlace con token aleatorio** (`secrets.token_urlsafe(32)`) para ver el estado. No necesita cuenta.
- **Sin envío de correos en la v1.** La administración contacta al solicitante por teléfono o WhatsApp de forma manual, y la app muestra el teléfono con un botón para llamar. El envío automático por SMTP queda como mejora posterior.
- Antiabuso **sin captcha**: campo trampa oculto (honeypot), límite de solicitudes por IP y por hora, y largos máximos en todos los campos.
- Casilla de consentimiento obligatoria con el texto del aviso de privacidad (Ley 8968): qué datos se piden, para qué y por cuánto tiempo se guardan.
- Las solicitudes rechazadas o vencidas se **anonimizan después de 12 meses** (hay que confirmar el plazo con el CCDR).

### 3.4 Pantallas

1. **Público — Calendario**: vista semanal por instalación, cómoda en celular.
2. **Público — Solicitud**: formulario de 1 paso → pantalla de confirmación con el enlace de seguimiento.
3. **Público — Estado**: el solicitante abre su enlace y ve "Pendiente", "Aprobada" o "Rechazada", más la nota de la administración.
4. **Admin — Bandeja**: lista de pendientes ordenada por fecha de uso, con aprobar o rechazar en un clic y aviso de choques.
5. **Admin — Instalaciones, horarios y bloqueos.**

### 3.5 Criterios de aceptación

- [ ] Una persona pide una cancha desde el celular en menos de 1 minuto.
- [ ] No se pueden aprobar dos reservas que se cruzan, ni siquiera con dos pestañas de administración abiertas a la vez (probar la condición de carrera).
- [ ] El calendario público no muestra ningún dato personal (verificarlo revisando el HTML y las respuestas de la API).
- [ ] El token de seguimiento no se puede adivinar ni enumerar.
- [ ] Cada flujo se recorrió en el navegador y hay capturas de pantalla.

## 4. Módulo 2 — Expediente de atletas (Juegos Deportivos Nacionales)

> ⚠️ **Es el módulo más delicado**: guarda datos de **menores de edad** y datos de salud. Lo más probable es que la Ley 8968 los clasifique como datos sensibles (*sin verificar; se revisa en la sección 7*). El prototipo funciona **solo con datos ficticios**.

### 4.1 Usuarios

| Rol | Puede |
|---|---|
| Administración CCDR | Todo: atletas, documentos, reportes, usuarios |
| Entrenador/a | Solo ver y actualizar atletas **de su disciplina** |

El público no tiene acceso a este módulo.

### 4.2 Datos: minimizar

- `disciplines`: id, name.
- `athletes`: id, first_name, last_name, birth_date, sex, discipline_id, category, id_number (solo si los Juegos lo exigen), guardian_name, guardian_phone, status (`active|inactive`), created_at.
- `documents`: id, athlete_id, doc_type (`insurance|medical_clearance|guardian_permit|id_copy`), issued_at, expires_at, file_path, uploaded_by, uploaded_at.
- `consents`: athlete_id, guardian_name, signed_at, form_version, file_path.
- `access_log`: user_id, athlete_id, action (`view|edit|export|download`), at. **Toda consulta queda registrada.**

**Minimización:** el dictamen médico se guarda como "apto: sí/no + fechas + archivo". **No se registran diagnósticos ni detalles médicos en la base de datos.**

### 4.3 Reglas

- **Alertas**: documentos que vencen en los próximos 30 días o antes de la fecha del evento; atletas sin algún documento obligatorio.
- **Panel por disciplina**: semáforo verde/amarillo/rojo por atleta según el estado de su documentación.
- **Reporte**: exportar CSV por disciplina con el estado documental. ⚠️ **Todavía no se conoce el formato oficial del ICODER**; la v1 genera un CSV genérico, y el formato real se pide al CCDR.
- **Archivos**: solo PDF, JPG o PNG; máximo 5 MB; tipo verificado por su contenido real (no por la extensión); se guardan con un nombre aleatorio en `data/uploads/`; solo se descargan con sesión y permiso, y cada descarga queda registrada.
- **Retención**: un atleta inactivo por más de N años se anonimiza (N lo define el CCDR).

### 4.4 Criterios de aceptación

- [ ] Un entrenador **no puede** ver atletas de otra disciplina, ni cambiando el id en la URL ni llamando la API directamente (probarlo).
- [ ] Ningún archivo se puede descargar sin sesión (probar con curl y desde el navegador).
- [ ] Cada consulta, exportación y descarga aparece en `access_log`.
- [ ] Las alertas de vencimiento son correctas con fechas de prueba en los bordes (vence hoy, vence mañana, ya vencido).

## 5. Seguridad común

**Usuarios** (tablas compartidas por los dos módulos):
- `users`: id, username, password_hash, salt, role (`admin|coach`), active, failed_logins, locked_until, created_at.
- `user_disciplines`: user_id, discipline_id. Define qué disciplinas ve cada entrenador; la restricción se aplica **en cada consulta SQL**, no solo en la interfaz.
- `sessions`: token_hash, user_id, csrf_token, last_seen_at, expires_at.

- Contraseñas con `hashlib.pbkdf2_hmac('sha256', …, iterations>=600_000)` y sal aleatoria.
- Sesiones en cookie `HttpOnly`, `SameSite=Strict` y `Secure` cuando hay HTTPS. La sesión expira después de 8 horas de inactividad.
- Token CSRF en todo POST o PUT de la administración.
- **Todo SQL parametrizado.** En el frontend usar `textContent`, nunca `innerHTML`, con datos del usuario.
- Encabezados: `Content-Security-Policy` estricta, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`.
- Límite de intentos de inicio de sesión (bloqueo temporal tras 5 fallos).
- Ningún secreto va en el repositorio; la configuración va en un `config.local.json` ignorado por git.
- Respaldo diario de `data/` (script incluido). El respaldo también contiene datos sensibles, así que debe guardarse cifrado (ver 7.4).

## 6. Fases

| Fase | Entrega | Para qué |
|---|---|---|
| **1** | Módulo 1 completo con datos ficticios del Polideportivo | Mostrárselo al CCDR primero: es el de menor riesgo |
| **2** | Módulo 2 completo con atletas ficticios | Demostración, una vez que el CCDR confirme interés |
| **3** | Ajustes según la retroalimentación del CCDR | — |
| **4** | Producción con datos reales | **Solo** después de cumplir la sección 7 |

Cada fase se cierra con tests en verde, recorrido en el navegador, revisión de seguridad y un `README.md` actualizado.

## 7. Antes de usar datos reales (bloqueante para la fase 4)

1. **Aprobación del CCDR** (acuerdo de la Junta Directiva) para adoptar el sistema.
2. **Revisión legal Ley 8968**: aviso de privacidad, consentimiento informado de padres o encargados, y si la base de datos debe inscribirse ante la PRODHAB. *No verificado; consultarlo con la asesoría legal municipal.*
3. **Revisar la Ley 10626** (fortalecimiento de los CCDR, diciembre de 2024), por posibles obligaciones de publicación o de gestión. *Todavía no se ha leído.*
4. **Dónde corre** (decisión abierta):
   - Lo recomendado es separar: el módulo 1 en un servidor accesible desde internet con HTTPS, y el módulo 2 **solo en la red interna del CCDR**, o al menos detrás de VPN.
   - Cifrado: Python estándar no trae cifrado simétrico. Las opciones son cifrar el disco a nivel del sistema operativo (BitLocker en la PC del CCDR) o aprobar **una** dependencia (`cryptography`) para cifrar archivos y respaldos.
5. **Responsable designado** en el CCDR que administre usuarios y respaldos.

## 8. Fuera de alcance de la v1

Pagos o tarifas en línea, envío automático de correo, SMS o WhatsApp, integración con SICOP o con sistemas del ICODER, app nativa, y cualquier uso de IA.

## 9. Preguntas para el CCDR (llevar junto al prototipo)

1. ¿Qué instalaciones administran ustedes y cuáles la Municipalidad? (El Palacio de los Deportes, por ejemplo, lo administra la Municipalidad.)
2. ¿Cómo reciben hoy las solicitudes, y cuántas por semana?
3. ¿Cobran por el uso? ¿Tienen tarifas aprobadas?
4. ¿Cómo llevan hoy los expedientes de atletas? ¿Cuántos atletas y disciplinas tienen en los Juegos?
5. ¿Qué documentos les exige el ICODER, y en qué formato?
6. ¿Quién administraría el sistema? ¿Tienen una PC o servidor propio?
7. ¿Cuánto tiempo deben guardar los datos de solicitudes y de atletas?

## 10. Cómo arrancar la sesión nueva

Prompt sugerido:

```text
Lee C:\Users\esteb\ccdr-heredia\SPEC.md completo. Arranca la Fase 1 (módulo de reservas) siguiendo las reglas de la sección 1. Antes de escribir código, proponme el esquema de la base de datos y las rutas de la API para aprobarlas.
```
