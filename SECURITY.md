# Seguridad

## Alcance

El servidor es de solo lectura, público y sin autenticación: no guarda datos de quienes lo usan, no recibe
archivos y no tiene credenciales propias. Las entradas de cada herramienta se validan con esquemas y las
respuestas tienen un tamaño máximo.

## Cómo reportar una vulnerabilidad

Usar el reporte privado de GitHub en la pestaña **Security** del repositorio («Report a vulnerability»).
No abrir un *issue* público con el detalle. Se acusará recibo dentro de 7 días.

## Prácticas del proyecto

- Todo cambio entra a `main` por *pull request* con integración continua: revisión de tipos, pruebas
  unitarias, prueba de las herramientas contra una muestra de datos, análisis estático (CodeQL y ruff) y
  revisión de vulnerabilidades en dependencias.
- Dependabot mantiene actualizadas las dependencias de npm, Python y GitHub Actions; las Actions se fijan
  por su SHA.
- El escaneo de secretos con bloqueo al subir está activo.
- Los datos procesados se publican como Release con huella SHA-256 registrada en
  `parametros/datos_release.json`; el despliegue verifica esa huella antes de publicar.
- Cada despliegue se prueba primero en una versión de vista previa y pasa a producción solo con aprobación
  manual. La clave de Cloudflare vive como secreto de GitHub y solo permite editar Workers.
- Una prueba diaria verifica que el servicio público responda correctamente.
