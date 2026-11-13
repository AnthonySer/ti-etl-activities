# IMPLEMENTACIÓN FASE 5 — Sincronización segura entre Excel y SQL

## Objetivo

Evitar inconsistencias y pérdida de información al guardar el Excel maestro,
detectar cambios externos y registrar las operaciones para poder recuperarlas.

## Archivos modificados

- `core/integridad_excel.py` (nuevo).
- `core/operaciones.py` (nuevo).
- `core/asignaciones.py`.
- `config/configuracion.py`.
- `interfaz.py`.
- `tests/test_sistema.py`.
- `tests/test_sql_revision_generacion.py`.

## Componentes creados

- `core.integridad_excel`:
  - huella SHA-256 del archivo;
  - `RegistroHuellas` persistente;
  - detección de cambios externos;
  - detección de archivo abierto en Excel;
  - `guardar_maestro_seguro`;
  - verificación de que el Excel generado puede abrirse;
  - idempotencia por contenido y por huella externa.
- `core.operaciones`:
  - `DiarioOperaciones`;
  - estados `PENDIENTE`, `COMPLETADO`, `ERROR`, `REQUIERE_REVISION`;
  - claves de idempotencia;
  - `ejecutar`, `reintentar`, historial y resumen;
  - persistencia JSON atómica.

## Funcionalidades implementadas

- Antes de modificar el maestro se comprueba:
  - que el archivo exista;
  - que no esté abierto en Excel;
  - que no haya cambiado fuera de la aplicación.
- Si hay un cambio externo, el guardado se bloquea hasta que la persona lo
  confirme expresamente.
- El guardado conserva el respaldo y la escritura transaccional existente.
- Después de guardar se verifica que el archivo pueda abrirse.
- Cada asignación relevante queda en el diario con usuario, área anterior,
  área nueva, origen, archivo, estado y error.
- Los reintentos son idempotentes: una operación `COMPLETADO` con el mismo
  contenido no se vuelve a ejecutar; `PENDIENTE` y `ERROR` pueden reintentarse.
- `REQUIERE_REVISION` no se reintenta automáticamente.
- El diario no almacena contraseñas ni credenciales.
- Se conservan las operaciones en memoria cuando el guardado está desactivado
  (`guardar=False`).

## Pruebas ejecutadas

- `PruebasIntegridadExcel`:
  - huella estable;
  - detección de cambio externo;
  - bloqueo sin confirmación;
  - guardado confirmado y actualización de huella.
- `PruebasOperaciones`:
  - error registrado y reintentable;
  - reintento idempotente sin duplicados;
  - `REQUIERE_REVISION` no se reintenta;
  - el diario no guarda credenciales.
- Suite completa del proyecto.

## Resultados obtenidos

- 8 pruebas específicas de integridad y operaciones: **OK**.
- Suite completa: **212 pruebas, 4 omitidas, 0 fallos**.
- El Excel maestro no pierde información ni estructura.
- Los errores de escritura quedan registrados y pueden reintentarse.
- Los cambios externos no se sobrescriben sin confirmación.

## Problemas encontrados

- La escritura y la consulta SQL no comparten una transacción; se resolvió con
  el diario de operaciones y los estados explícitos.
- Fue necesario aislar `OPERACIONES_JSON` y `HUELLAS_EXCEL_JSON` en las pruebas
  para no tocar los datos reales.

## Pendientes

- Si el negocio autoriza escribir áreas en SQL, deberá definirse la tabla y la
  regla de actualización. En esta implementación SQL sigue siendo de solo
  lectura.

## Compatibilidad con fases anteriores

`Maestro.guardar` conserva su comportamiento transaccional y de respaldo. La
capa nueva se añade alrededor sin cambiar el formato del Excel. Las fases 1, 3 y
4 siguen funcionando con las mismas claves y eventos.
