# Propuesta Fase 1: esquema de datos y rutas de la API

> Estado: **aprobada por Esteban el 2026-11-20** e implementada en la Fase 1.
> Base: SPEC.md, secciones 2, 3 y 5. Los nombres de tablas, columnas y rutas van en inglés (regla 1); la explicación va en español.

## 1. Convenciones

- **Hora:** todo se guarda en hora de Costa Rica (UTC−6 fija) como texto `YYYY-MM-DDTHH:MM`. Al ser una zona fija, comparar textos equivale a comparar fechas.
- **Intervalos semiabiertos `[inicio, fin)`:** una reserva de 8:00 a 9:00 no choca con otra de 9:00 a 10:00. Dos intervalos chocan si `a.starts_at < b.ends_at AND b.starts_at < a.ends_at`.
- **Sin cruzar la medianoche:** una solicitud empieza y termina el mismo día.
- **Días de la semana:** `weekday` va de 0 (lunes) a 6 (domingo).

## 2. Tablas

Solo las del módulo 1 y las de usuarios y sesiones. **No se crea ninguna tabla de atletas**, `athletes.py` no se escribe y `ATHLETES_ENABLED` queda en `false`.

| Tabla | Columnas | Notas |
|---|---|---|
| `users` | id, username UNIQUE, password_hash, salt, role CHECK(`admin`\|`coach`), active, failed_logins, locked_until, created_at | Igual a la §5. En la fase 1 solo se usa `admin`. |
| `sessions` | token_hash PK, user_id FK, csrf_token, last_seen_at, expires_at | La cookie lleva el token, y la base guarda solo su SHA-256. La sesión expira tras 8 h sin actividad. |
| `facilities` | id, name, location, sports, capacity, rules_text, active | **Sin columna de precio ni tarifa** (regla 8). |
| `opening_hours` | id, facility_id FK, weekday, opens_at `HH:MM`, closes_at `HH:MM` | Admite varias franjas en un mismo día (p. ej., 6–12 y 14–21). |
| `blocks` | id, facility_id FK, starts_at, ends_at, reason, **public_label**, recurrence CHECK(`none`\|`weekly`), **recurrence_until**, created_by FK, created_at | Ver desviaciones 1 y 2. |
| `booking_requests` | id, facility_id FK, starts_at, ends_at, requester_name, organization, phone, email, purpose, expected_attendees, status CHECK(`pending`\|`approved`\|`rejected`\|`cancelled`), admin_note, **public_token_hash** UNIQUE, consent_at, **consent_version**, created_at, decided_by FK, decided_at, **anonymized_at** | Ver desviaciones 3 a 5. |

Índices: `booking_requests(facility_id, status, starts_at)`, `blocks(facility_id, starts_at)`, `sessions(expires_at)`.

### Desviaciones del SPEC (requieren tu visto bueno)

1. **`blocks.public_label`** (texto, opcional): el nombre que ve el público, p. ej. "Escuela de voleibol". Si queda vacío, el público solo ve "Ocupado". Así un bloqueo por mantenimiento no muestra el `reason` interno.
2. **`blocks.recurrence_until`** (fecha): el último día en que se repite un bloqueo semanal. Es obligatoria cuando `recurrence = weekly`, para que no haya bloqueos infinitos.
3. **`public_token_hash` en vez de `public_token`:** se guarda solo el SHA-256 del token. Si alguien copia la base de datos, no obtiene enlaces válidos. El token completo se le muestra **una sola vez** al solicitante. Si lo pierde, la administración puede consultar su solicitud igual.
4. **`consent_version`:** qué versión del aviso de privacidad aceptó la persona, para cuando el texto cambie.
5. **`anonymized_at`:** fecha en que se borraron los datos personales de la solicitud.

## 3. Rutas

### Páginas (HTML estático)

| Ruta | Pantalla |
|---|---|
| `GET /` | Público: calendario semanal |
| `GET /solicitar` | Público: formulario de solicitud |
| `GET /estado` | Público: estado de la solicitud (el token viaja en el `#fragmento`, ver decisión 5) |
| `GET /admin/` | Admin: SPA (inicio de sesión, bandeja, instalaciones, horarios, bloqueos) |

### API pública (sin sesión)

| Método y ruta | Qué hace | Qué devuelve |
|---|---|---|
| `GET /api/public/facilities` | Instalaciones activas | id, name, location, sports, capacity, rules_text, opening_hours |
| `GET /api/public/availability?facility_id=&week_start=YYYY-MM-DD` | Ocupación de una semana | `{opening_hours, busy:[{starts_at, ends_at, label}]}`. `label` es "Ocupado" o el `public_label`. **Sin ids de reserva, nombres, teléfonos ni organizaciones.** Las solicitudes pendientes no aparecen. |
| `POST /api/public/requests` | Envía una solicitud | 201 `{token, status_url}`. Da 422 con mensajes en español si el horario es inválido, 409 si choca con una reserva aprobada o con un bloqueo, y 429 si se pasa del límite. |
| `POST /api/public/requests/status` (cuerpo: `{token}`) | Consulta el estado | facility_name, starts_at, ends_at, status, admin_note, created_at. **Sin teléfono, correo ni id.** Si el token no existe, da 404 genérico. |

### API de administración (sesión obligatoria; token CSRF en **todo POST, PUT y DELETE**)

| Método y ruta | Qué hace |
|---|---|
| `POST /api/auth/login` | Inicia sesión. Bloquea 15 min tras 5 fallos. Da el mismo mensaje si falla el usuario o la contraseña. |
| `POST /api/auth/logout` | Cierra la sesión. |
| `GET /api/auth/me` | Devuelve el usuario y el `csrf_token`. |
| `GET /api/admin/requests?status=pending&from=&to=` | Bandeja ordenada por `starts_at`. Cada fila trae `conflicts`: con quién choca (aprobadas, bloqueos y otras pendientes). |
| `POST /api/admin/requests/{id}/approve` `{admin_note}` | Ver decisión 7. |
| `POST /api/admin/requests/{id}/reject` `{admin_note}` | Rechaza la solicitud. |
| `POST /api/admin/requests/{id}/cancel` `{admin_note}` | Cancela una solicitud aprobada o pendiente. |
| `GET /api/admin/facilities` · `POST /api/admin/facilities` · `PUT /api/admin/facilities/{id}` | Administra instalaciones. Se desactivan, nunca se borran. |
| `PUT /api/admin/facilities/{id}/hours` | Reemplaza el horario semanal completo. |
| `GET /api/admin/blocks?facility_id=` · `POST /api/admin/blocks` · `DELETE /api/admin/blocks/{id}` | Administra bloqueos. |

## 4. Decisiones (con el valor por defecto que propongo)

1. **Formato de hora:** ver §1. No se puede cruzar la medianoche.
2. **Bloqueos semanales:** se guardan como una sola fila (`recurrence = weekly` + `recurrence_until`) y el servidor calcula cada repetición al revisar choques y al armar el calendario. La revisión dentro de `BEGIN IMMEDIATE` también cubre esas repeticiones.
3. **La regla de "nunca dos cosas en el mismo espacio" vale en todos los caminos**, no solo al aprobar. Todos estos se revisan en el servidor dentro de `BEGIN IMMEDIATE`:
   - **Aprobar** una solicitud que choca con otra aprobada o con un bloqueo → 409.
   - **Crear un bloqueo** encima de una reserva aprobada → 409 con la lista de reservas afectadas. La administración las cancela primero (y llama a las personas), luego crea el bloqueo.
   - **Cambiar el horario** de forma que una reserva aprobada futura quede fuera → 409 con la lista.
4. **"Vencida"** no es un estado nuevo, sino que se calcula: una solicitud `pending` cuya hora de inicio ya pasó. La bandeja y la página de estado la muestran como "Vencida", y ya no se puede aprobar.
   - **Anonimización:** un script (y una revisión al arrancar el servidor) anonimiza las solicitudes `rejected`, `cancelled` y vencidas que tengan más de 12 meses. Borra requester_name, organization, phone, email, purpose y admin_note, y marca `anonymized_at`.
   - **Pendiente de confirmar con el CCDR:** si las `approved` pasadas también se anonimizan, y en qué plazo. Por ahora no se tocan.
5. **Token de seguimiento:** se genera con `secrets.token_urlsafe(32)` (256 bits) y se guarda solo su hash. El enlace es `/estado#<token>`: lo que va después del `#` nunca llega al servidor, así que no queda en ningún log, y el navegador lo envía por POST. Además, `Referrer-Policy: no-referrer`.
6. **Respuestas públicas:** solo los campos de las tablas de arriba. El test de privacidad carga datos personales reconocibles (p. ej., `ZZPRIVADO-Nombre`, `8888-0000`) y verifica que no aparecen en ninguna respuesta pública ni en el HTML.
7. **Aprobar:** dentro de `BEGIN IMMEDIATE` se revisa el choque y se aprueba.
   - Si choca con algo aprobado o con un bloqueo → **409** `{error, conflicts:[...]}`.
   - Si no choca → **200** `{ok:true, overlapping_pending:[{id, requester_name, phone, starts_at, ends_at}]}`. La interfaz muestra esa lista para rechazarlas a mano. **No se rechazan solas** (§3.3).
8. **Antiabuso:**
   - **Límite:** 5 solicitudes por IP por hora y 30 consultas de estado por IP por hora, en memoria (`dict` con candado).
   - **IP:** se toma de la conexión, sin confiar en `X-Forwarded-For`. Si después se pone detrás de un proxy, se configura aparte.
   - **Honeypot:** si el campo trampa viene lleno, se responde 201 con un token falso y no se guarda nada, para que el bot no se entere.
   - **Largos máximos** en todos los campos y cuerpo máximo de 16 KB.
9. **Primer usuario admin:** no hay contraseña por defecto en el repo.
   - `scripts/seed_demo.py` crea el usuario `admin` con una contraseña aleatoria y la escribe en `data/demo-credentials.txt`, que git ignora. No la imprime en el chat.
   - Para uso real habrá `scripts/create_admin.py`, que pide la contraseña con `getpass`.
10. **CSRF:** el encabezado `X-CSRF-Token` es obligatorio en todo POST, PUT y DELETE de `/api/admin/*` y `/api/auth/logout`. Además, cookie `SameSite=Strict`.
11. **Límites de reserva:** son supuestos configurables, para confirmar con el CCDR y sumar a la §9.
    - Bloques de 30 min.
    - Duración mínima de 1 h y máxima de 4 h.
    - Hasta 60 días hacia adelante.
    - Al menos 24 h de anticipación.
12. **Datos ficticios:** 4 instalaciones de ejemplo con el sufijo "(demo)" y una franja visible en todas las páginas: "Prototipo con datos ficticios". No hay ningún campo de precio, ni en la base ni en las pantallas.

## 5. Cómo se va a verificar

- **Carrera:** dos hilos aprueban a la vez dos solicitudes que se cruzan, contra el `ThreadingHTTPServer` real y el archivo SQLite. Exactamente una recibe 200 y la otra 409. También se recorre el caso con dos pestañas en el navegador.
- **Privacidad:** el test de la decisión 6, más una revisión manual del HTML y de las respuestas en el navegador.
- **Token:** se verifica la entropía, que la base no guarda el token en claro y que tokens inventados o secuenciales dan 404.
- **Navegador:** `.claude/launch.json` dentro de `ccdr-heredia` y `preview_start`, con capturas de cada pantalla, incluida la vista de celular.
