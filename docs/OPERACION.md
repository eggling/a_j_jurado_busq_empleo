# Configuración y operación

## Estado actual

Implementación y pruebas locales completadas. Hay workflow manual, sin cron activo. No se ha enviado ningún correo ni desactivado la tarea local. Gmail, fuentes reales y escritura mediante la API GitHub no están verificados en esta sesión.

## Ejecutar y conservar datos

Python 3.12+ en Linux; solo biblioteca estándar. Usar el checkout existente sin crear worktrees. Desde la raíz, `python3 -m empleo init` importa `traspaso/registro-unificado.json` solo cuando no existe registro operativo. Repetirlo preserva cambios y envíos posteriores. No borrar el registro para resolver un fallo.

`python3 -m empleo run` busca y genera un informe sin enviar. Se conservan informes dentro del propio registro, además del último archivo local. El perfil completo y las aclaraciones están en `traspaso/`. No inferir horas de prácticas, años de empleo autónomo, acreditaciones, salario neto o disponibilidad no documentados.

## Conexión de Gmail

Crear o reutilizar una aplicación OAuth de Google con Gmail API habilitada. No contratar un servicio de pago. Autorizar la cuenta remitente para `https://www.googleapis.com/auth/gmail.send` y `https://www.googleapis.com/auth/gmail.readonly` con acceso offline. No usar contraseñas de Gmail ni pegar tokens en archivos, commits o conversaciones. Si la aplicación permanece en modo de prueba de Google, revisar restricciones y caducidad del refresh token antes de depender de ella diariamente.

Configurar de forma segura en el ejecutor `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET` y `GMAIL_REFRESH_TOKEN`. El programa utiliza los dos destinatarios ya autorizados de `empleo/__main__.py`, ambos en Para. Obtiene el remitente del perfil Gmail. `python3 -m empleo check` valida renovación, lectura del perfil, scopes de envío y extracción útil de al menos una oferta; no envía correo de prueba. Su éxito no acredita entrega real.

Destinos necesarios: `www.bing.com`, las webs de ofertas descubiertas, `oauth2.googleapis.com`, `gmail.googleapis.com` y, para persistencia GitHub, `api.github.com`. Las redirecciones y fuentes inaccesibles se registran como incidencias; no se desactiva TLS ni se reenvían credenciales a otros hosts.

## Estado duradero en GitHub

El workflow escribe `estado/registro.json` en `main` del mismo repositorio privado. Incluye ofertas, observaciones, informes y envíos; Git conserva versiones. La primera ejecución importa la instantánea inicial y las siguientes reutilizan el registro remoto. No se usan cachés ni artefactos temporales como autoridad para evitar duplicados.

El workflow utiliza su `GITHUB_TOKEN` con `contents: write`. Fuera de Actions, proporcionar `GITHUB_REPOSITORY=owner/repo` y un `GITHUB_TOKEN` con permisos mínimos de metadatos y lectura/escritura de contenidos. El acceso Git HTTPS de Codex no acredita automáticamente acceso a esta API.

```bash
python3 -m empleo init --store github
python3 -m empleo run --store github
```

Las escrituras usan el SHA anterior: un conflicto o fallo de persistencia detiene el envío. Si las protecciones impiden escribir en `main`, configurar una rama existente mediante `STATE_BRANCH` al ejecutar fuera del workflow, o añadir esa variable al workflow. No eludir protecciones ni utilizar varias copias independientes del registro.

Verificar persistencia ejecutando `init` y `run` sin envío con `--store github`; desde una ejecución nueva comprobar que conserva informes y envío histórico. Un archivo local no lo demuestra. El registro y el historial Git crecen: revisar tamaño y mantenimiento sin borrar reservas de envío. El cliente admite lectura de registros de más de 1 MB mediante la representación raw de Contents; limita la lectura a 50 MB y se detiene si se supera.

## Workflow manual y coste

En Settings → Secrets and variables → Actions, guardar las tres variables Gmail como secrets. GitHub proporciona su propio token. No copiar el token de esta sesión. `dry-run` no necesita Gmail.

Antes de lanzar Actions, confirmar minutos gratuitos disponibles y que la facturación no permite consumos de pago. No activar suscripciones. Después establecer `ACTIONS_FREE_QUOTA_CONFIRMED=true` como variable del repositorio. Sin ella o en un repositorio público, se omite el job.

En Actions → Búsqueda de empleo → Run workflow:

| Modo | Resultado |
| --- | --- |
| `dry-run` | Pruebas, búsqueda real e informe persistido sin envío |
| `check` | Pruebas y comprobación de Gmail y extracción, sin envío |
| `reconcile` | Conciliación positiva de Enviados; no envía |
| `send` | Búsqueda y un intento de envío tras cumplir los controles |

Comparten grupo de concurrencia y no cancelan un envío en curso. El permiso de contenidos no implica capacidad de modificar secrets o facturación.

## Activación coordinada

Mantener sin cron mientras pueda ejecutarse la tarea local:

1. Verificar Gmail, fuentes reales, privacidad del repositorio y cuota gratuita.
2. Obtener y conciliar una instantánea final del historial local sin reemplazar cambios cloud con datos antiguos.
3. Confirmar la desactivación de `empleo-de-psic-logo-en-madrid` en el PC.
4. Verificar persistencia desde una ejecución nueva y revisar el informe sin envío.
5. Establecer `CLOUD_ACTIVATION_APPROVED`, `LOCAL_TASK_DISABLED`, `FINAL_HISTORY_SYNCED` y `DURABLE_STORAGE_VERIFIED` a `true` únicamente cuando se cumpla cada condición.

Entonces habilitar el bloque `schedule` comentado del workflow: dos ticks UTC a las 07:00 y 08:00. El programa descarta el que no corresponde a la hora 09 en Europe/Madrid, ajustándose al horario de verano. Actions puede retrasar u omitir ejecuciones; no garantiza las 09:00 exactas. Si el retraso sale de la hora 09, se omite el tick. Para puntualidad garantizada haría falta otro programador.

Con un volumen persistente en un servidor alojado, el envío local exige además `ALLOW_LOCAL_SEND=true`. Un proceso en esta sesión no demuestra continuidad con el PC apagado.

## Recuperación

La reserva se guarda antes de Gmail. Un timeout deja `uncertain`; un fallo al guardar la confirmación deja `pending`. Ambos bloquean nuevos envíos para la misma fecha y destinatarios. No cambiar estados a mano para forzar reintentos.

```bash
python3 -m empleo reconcile --store github
```

Se requiere una única coincidencia en Enviados por destinatarios, fecha local, asunto y hash del contenido. Cero o varias mantienen el bloqueo. Los envíos históricos sin hash conservan su estado importado. Consultar el informe del registro ante un fallo posterior al envío, no reconstruirlo desde un archivo local diferente.

## Validación ejecutada

28 pruebas pasan: extracción RSS/JobPosting, filtros, fuentes fallidas, alias, salario, historial, CLI sin envío, reapertura, conflictos concurrentes, payload Gmail, conciliación, fallo previo, timeout y fallo posterior al envío. Utilizan fixtures y dobles de red; no equivalen a validación de las APIs reales.

La búsqueda real generó un informe parcial con cinco incidencias de DNS de Bing. Gmail falla por ausencia de las tres variables requeridas. La API GitHub tampoco es accesible desde esta sesión; Git sí funciona. No se enviaron mensajes. La puesta en servicio depende de resolver esos accesos y coordinar la tarea local.
