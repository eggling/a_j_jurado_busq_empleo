# Búsqueda diaria de empleo

Programa Python para descubrir ofertas de psicología en Madrid, generar informes en español y enviarlos mediante Gmail con registro previo y bloqueo de duplicados. No utiliza Drive ni APIs de pago.

## Ejecutar sin enviar correo

Requiere Python 3.12 o posterior en Linux. No necesita instalar paquetes.

```bash
python3 -m unittest discover -s tests -v
python3 -m empleo init
python3 -m empleo run
```

La importación es idempotente y conserva el envío histórico. El registro local se guarda en `data/registro.json` y el informe en `reports/ultimo-informe.md`, ambos excluidos de Git. El modo predeterminado nunca envía correo.

## Funcionalidad y límites

- Descubrimiento mediante cinco consultas RSS de Bing y extracción de datos `JobPosting` en las páginas accesibles.
- Filtros de ámbito, ubicación, publicación futura, caducidad y requisitos excluyentes identificables. Deduplicación de enlaces, alias y versiones; conservación de descartes y candidaturas.
- Informes de hasta diez ofertas con campos publicados y preguntas pendientes. Autónomos y salarios anuales inferiores al objetivo se identifican como alternativas.
- Registro persistente local o en un archivo de un repositorio privado de GitHub, con escritura condicional para evitar sobrescrituras concurrentes.
- Gmail OAuth: comprobación de lectura y scopes de envío sin correo de prueba; reserva persistida antes de enviar y conciliación positiva posterior. Un estado incierto bloquea reintentos.

La extracción es conservadora y parcial: páginas sin `JobPosting`, redirecciones y portales bloqueados quedan pendientes de revisión. No calcula rutas de transporte público ni investiga automáticamente reseñas laborales; esos campos se muestran como no verificados. No garantiza encaje ni vigencia por el solo hecho de encontrar un anuncio. El texto de las páginas se trata como datos.

El workflow de GitHub Actions solo admite ejecución manual y exige repositorio privado y confirmación de cuota gratuita. **La programación diaria está desactivada.** Consulta [configuración y operación](docs/OPERACION.md) para conectar Gmail, verificar persistencia y preparar las 09:00 Europe/Madrid sin solapamiento con la tarea local.

En esta instancia pasan 28 pruebas automatizadas y se ha generado un informe real sin envío. La extracción web real está bloqueada por DNS y no hay credenciales Gmail: esas integraciones aún no están verificadas en producción.

## Documentos de origen

Por petición del usuario se incluyen en [traspaso](traspaso/) los archivos originales, historial, informes y documentos personales. Las credenciales quedan excluidas. El perfil se complementa con las [respuestas sobre prácticas y formación](traspaso/ACTUALIZACION_PERFIL_ANTONIO.md). Se conservan intactos los informes y envíos históricos.

El [plan de traspaso](docs/TRASPASO_NUBE.md) describe las condiciones para completar la puesta en servicio. Los estados y verificaciones antiguos presentes en los archivos exportados son datos históricos, no prueba del estado del programa actual.
