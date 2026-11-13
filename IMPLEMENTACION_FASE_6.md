# IMPLEMENTACIÓN FASE 6 — Historial, diagnóstico y recuperación

## Objetivo

Facilitar la supervisión del sistema, la identificación de problemas y la
recuperación de operaciones pendientes sin exponer credenciales.

## Archivos modificados

- `core/diagnostico.py` (nuevo).
- `interfaz.py` (botón **Diagnóstico** en la pestaña de auditoría).
- `core/operaciones.py` (reutilización del diario).
- `core/integridad_excel.py` (reutilización de huellas).

## Componentes creados

- `core.diagnostico.DiagnosticoSistema`:
  - fecha de generación;
  - resumen de operaciones por estado;
  - operaciones pendientes;
  - últimas operaciones;
  - última ejecución de auditoría;
  - resumen del maestro;
  - resumen de la caché de empleados;
  - alertas.
- Exportación a Markdown (`diagnostico_sistema.md`) en la carpeta de reportes.
- Funciones `historial_reciente`, `operaciones_por_usuario` y `reintentar`.

## Funcionalidades implementadas

- La pestaña de auditoría incorpora un botón **Diagnóstico** que muestra:
  - operaciones por estado;
  - alertas de errores, pendientes y revisiones;
  - últimas operaciones;
  - última importación registrada en auditoría;
  - cantidad de usuarios del maestro y estado de la caché.
- El informe puede guardarse en Markdown y abrirse desde la carpeta de
  reportes.
- El diagnóstico no incluye contraseñas, usuarios SQL, servidores ni cadenas de
  conexión.
- Las operaciones pueden filtrarse por usuario normalizado.
- El reintento reutiliza el diario idempotente de la fase 5.

## Pruebas ejecutadas

- `PruebasDiagnostico`:
  - el diagnóstico no contiene credenciales;
  - historial filtrado por usuario.
- Suite completa del proyecto.

## Resultados obtenidos

- 2 pruebas específicas de diagnóstico: **OK**.
- Suite completa: **212 pruebas, 4 omitidas, 0 fallos**.
- Cada modificación relevante queda registrada.
- Los errores y pendientes pueden identificarse y reintentarse.
- No se exponen credenciales ni información sensible.

## Problemas encontrados

- El diagnóstico debe funcionar aunque el maestro, la caché o la auditoría no
  existan todavía; todas las lecturas están protegidas y devuelven un resumen
  vacío con el error controlado.

## Pendientes

- Ninguno crítico. Puede añadirse un filtro visual por estado si el volumen de
  operaciones crece.

## Compatibilidad con fases anteriores

Se apoya en el diario y las huellas de la fase 5 y en los eventos de la fase 1.
No modifica el maestro, el Excel de tickets ni las consultas SQL. La interfaz
mantiene la pestaña de auditoría y añade una acción sin cambiar su diseño.
