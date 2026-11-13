# Auditoría del sistema actual

Generado: 2026-11-12T21:10:12-05:00

> Informe de solo lectura. No se modificó ningún Excel, base de datos ni archivo de configuración.

## 1. Arquitectura observada

- Raíz del proyecto: `C:\Users\antho\Documents\ETL-TI-SERVIAM\ti-etl-activities`
- Python: 3.14.7
- Aplicación de escritorio Tkinter con motor ETL en Python.
- Módulos de dominio en `core/`, acceso a SQL en `database/`, configuración en `config/`.
- El Excel original nunca se sobrescribe: se genera una salida de 11 columnas.
- Maestro persistente `USUARIO -> AREA_USUARIO` en `data/maestro_areas.xlsx` con espejo CSV.

### Dependencias

| Dependencia | Estado |
| --- | --- |
| pandas | disponible |
| openpyxl | disponible |
| pyodbc | disponible |
| tkinter | disponible |

## 2. Flujo actual

```text
Excel de tickets
  -> validación de estructura (11 columnas oficiales)
  -> extracción y normalización
  -> búsqueda en maestro USUARIO -> ÁREA
  -> resolución contra caché local / SQL Server (solo lectura)
  -> pendientes y conflictos a revisión
  -> generación de Excel de salida + manifiesto
  -> auditoría CSV y estado de la interfaz
  -> validación manual y actualización del maestro
```

## 3. Maestro de áreas

| Métrica | Valor |
| --- | --- |
| Archivo existe | no |
| Usuarios | 0 |
| Áreas únicas | 0 |
| Áreas vacías | 0 |
| Usuarios duplicados | 0 |
| Columnas | no disponibles |
| Hojas | no disponibles |

- Espejo CSV: ausente (0 filas).
- Maestro histórico de tickets: ausente (0 filas).
- Caché de empleados: ausente (0 filas).

## 4. Conexión SQL

- Configurada: no
- Habilitada: no
- Servidor: ``
- Base de datos: `EXACTUS`
- Autenticación: `windows`
- Empresas autorizadas: 18
- La contraseña no se muestra en este informe.
- Todas las consultas son de solo lectura; ``database.connection`` rechaza escrituras.

## 5. Auditoría y operaciones

- Registros de auditoría: 1.
- Última ejecución: PROCESAMIENTO 2026-11-10T21:11:32-05:00 (FINALIZADO).
- Diario de operaciones:
  - PENDIENTE: 0
  - COMPLETADO: 0
  - ERROR: 0
  - REQUIERE_REVISION: 0
  - TOTAL: 0

## 6. Módulos existentes

| Carpeta | Archivos | Líneas |
| --- | --- | --- |
| core | 33 | |
| database | 8 | |
| config | 4 | |
| tests | 7 | |
| scripts | 1 | |

## 7. Funcionalidades que deben conservarse

- Procesamiento del Excel de 11 columnas y generación del archivo de salida.
- Validación de estructura, fechas, horas, duplicados y observaciones.
- Maestro `USUARIO -> AREA_USUARIO` con guardado transaccional y respaldos.
- Resolución de casos A–E y protección de asignaciones manuales.
- Consulta SQL de solo lectura por empresa, con aislamiento de errores.
- Revisión persistente, reimportación, consolidación y reportes.
- Auditoría CSV independiente y estado de la interfaz.

## 8. Problemas y riesgos detectados

- La búsqueda de sugerencias usaba ``difflib`` como único criterio; no cubría bien orden de nombres ni errores combinados.
- No existía un mecanismo de eventos para refrescar solo la región afectada de la interfaz.
- No había un diario único de operaciones con estados PENDIENTE/COMPLETADO/ERROR/REQUIERE_REVISION.
- No se detectaban cambios externos del Excel maestro antes de sobrescribirlo.
- El catálogo de áreas no tenía validación centralizada de duplicados y nombres parecidos.
- Los reintentos de sincronización no contaban con una clave de idempotencia.
- Persisten riesgos propios de Windows/Excel: archivo abierto, permisos y bloqueo de escritura.

## 9. Mejoras implementadas en las fases siguientes

- `core/similitud.py`: Jaro-Winkler, Levenshtein y comparación por tokens.
- `core/eventos.py`: bus de eventos para actualización inmediata por componente.
- `database/coincidencias.py`: consolidación de caché, SQL y maestro con caché por ejecución.
- `core/catalogo_areas.py`: validación y creación segura de áreas.
- `core/asignacion_asistida.py`: confirmación, advertencia de inactivos y asignación desde coincidencias.
- `core/operaciones.py`: diario con estados e idempotencia.
- `core/integridad_excel.py`: huellas, detección de cambios externos y escritura protegida.
- `core/diagnostico.py`: historial, alertas y recuperación.

## 10. Archivos que probablemente deben modificarse

- `core/asignaciones.py` (integrado con similitud y eventos).
- `database/resolucion.py` (sugerencias con el nuevo motor).
- `interfaz.py` (suscripción a eventos de actualización).
- `config/configuracion.py` (rutas y umbrales configurables).
- `tests/` (pruebas de las nuevas fases).

## 11. Propuesta de arquitectura objetivo

```text
Interfaz Tkinter
  -> Bus de eventos (core.eventos)
  -> Servicios de asignación y catálogo
  -> Motor de similitud (core.similitud)
  -> Consolidador de coincidencias (database.coincidencias)
  -> Maestro Excel + caché local + SQL de solo lectura
  -> Diario de operaciones + integridad de Excel
  -> Auditoría y diagnóstico
```

Regla de responsabilidades: identidad y estado provienen de SQL; el catálogo y la asignación de áreas se administran en el Excel maestro; las coincidencias solo proponen, nunca asignan solas.
