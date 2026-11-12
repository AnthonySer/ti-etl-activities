# Diagnóstico — Validación de usuarios y áreas / Consulta SQL multiempresa

**Fecha:** 2026-11-11
**Alcance:** módulo «Validación de Usuarios y Áreas», botón «Consultar SQL», resolución de áreas, persistencia y auditoría de solicitantes.
**Modo:** auditoría de código y de esquema real en solo lectura. No se modificó ninguna base de datos.

> Este documento no incluye credenciales ni datos personales. Los conteos y nombres de columnas provienen del diagnóstico previo `docs/DIAGNOSTICO_SQL_SERVER_EXACTUS.md`, verificado contra `EXACTUS` con el login de aplicación.

---

## A. Archivos, clases y funciones involucrados

| Componente | Archivo | Función / clase | Líneas relevantes |
|---|---|---|---|
| Ventana de validación | `ventana_validacion.py` | `VentanaValidacion` | 64-790 |
| Botón Consultar SQL | `ventana_validacion.py` | `VentanaValidacion._consultar_sql` | 469-561 |
| Subventana de coincidencias | `ventana_validacion.py` | `VentanaCoincidencias` | 853-943 |
| Aplicar candidato | `ventana_validacion.py` | `_elegir_candidato` | 621-640 |
| Revisar coincidencias | `ventana_validacion.py` | `_revisar_coincidencias` | 602-619 |
| Consulta SQL | `database/queries.py` | `sql_empleados`, `sql_columnas_esquema`, `sql_tablas_existen` | 89-186 |
| Repositorio por empresa | `database/repository.py` | `RepositorioEmpleados._consultar` | 163-211 |
| Modelo de empleado | `database/repository.py` | `Empleado`, `ResultadoConsulta` | 43-140 |
| Resolución de áreas | `database/resolucion.py` | `ResolverAreas.resolver`, `ResultadoResolucion` | 57-309 |
| Persistencia de revisión | `core/revision.py` | `Revision.actualizar`, `Revision.seleccionar_candidato` | 243-331 |
| Procedencia | `core/procedencia.py` | `registrar`, `registrar_lote` | 88-194 |
| Equivalencias de solicitantes | `core/solicitantes.py` | `detectar_solicitantes`, `cargar_equivalencias` | 152-369 |
| Asignación local | `core/asignaciones.py` | `asignar`, `asignar_una` | 229-322 |
| Caché de empleados | `database/synchronizer.py` | `Sincronizador.sincronizar_completo`, `guardar_cache` | 179-358 |
| Conexión y clasificación | `database/connection.py` | `conectar`, `_clasificar_error`, `ejecutar_consulta` | 127-230 |
| Auditoría de solicitantes | `interfaz.py` | `_refrescar_solicitantes` | 2734-2786 |
| Carga del maestro de tickets | `core/maestro_tickets.py` | `MaestroTickets.cargar` | 184-243 |

La arquitectura ya dispone de los tres mecanismos que la solución debe reutilizar:

1. `ResolverAreas` para decidir coincidencia única, ambigua, sin departamento e inactiva.
2. `Revision` para guardar la decisión manual y reanudarla.
3. `core.procedencia` para registrar el origen del área asignada.

No es necesario crear una segunda fuente de verdad.

---

## B. Consulta SQL original completa

`database/queries.py::sql_empleados` construye, para cada empresa:

```sql
SELECT
    E.EMPLEADO AS EMPLEADO,
    E.NOMBRE AS NOMBRE,
    E.ESTADO_EMPLEADO AS ESTADO_EMPLEADO,
    E.ACTIVO AS ACTIVO,
    E.DEPARTAMENTO AS DEPARTAMENTO_CODIGO,
    E.PUESTO AS PUESTO_CODIGO,
    E.PRIMER_APELLIDO AS PRIMER_APELLIDO,
    E.SEGUNDO_APELLIDO AS SEGUNDO_APELLIDO,
    E.NOMBRE_PILA AS NOMBRE_PILA,
    E.E_MAIL AS E_MAIL,
    E.U_CORREO_CORP AS U_CORREO_CORP,
    E.FUENTE AS FUENTE,
    D.DESCRIPCION AS DEPARTAMENTO_DESCRIPCION,
    P.DESCRIPCION AS PUESTO_DESCRIPCION
FROM [EXACTUS].[<EMPRESA>].[EMPLEADO] E
LEFT JOIN [EXACTUS].[<EMPRESA>].[DEPARTAMENTO] D
       ON D.DEPARTAMENTO = E.DEPARTAMENTO
LEFT JOIN [EXACTUS].[<EMPRESA>].[PUESTO] P
       ON P.PUESTO = E.PUESTO
WHERE (
    <variante de nombre 1> IN (?, ?, ...)
    OR <variante de nombre 2> IN (?, ?, ...)
    OR <variante de nombre 3> IN (?, ?, ...)
    OR <variante de nombre 4> IN (?, ?, ...)
)
ORDER BY E.EMPLEADO
```

Las cuatro variantes de nombre se generan en `_expresion_nombres()` (líneas 66-86) combinando `NOMBRE`, `NOMBRE_PILA`, `PRIMER_APELLIDO` y `SEGUNDO_APELLIDO`.

Consultas de metadatos existentes:

```sql
-- sql_columnas_esquema
SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE
FROM [EXACTUS].INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA = ?
ORDER BY TABLE_NAME, ORDINAL_POSITION;

-- sql_tablas_existen
SELECT TABLE_NAME
FROM INFORMATION_SCHEMA.TABLES
WHERE TABLE_CATALOG = ? AND TABLE_SCHEMA = ?
  AND TABLE_NAME IN ('EMPLEADO', 'DEPARTAMENTO', 'PUESTO')
ORDER BY TABLE_NAME;
```

---

## C. Empresas y fuentes afectadas

La unidad de consulta es el esquema de empresa dentro de la base `EXACTUS`; la fuente real es la tabla `EMPLEADO` de cada esquema. No existe una columna `FUENTE` en ninguna empresa.

| Grupo | Empresas | Efecto de la consulta actual |
|---|---|---|
| Pobladas con `U_CORREO_CORP` | FILASUR, INKA, POLYSOL, SERVIAM, TROPICAL | Falla por `FUENTE` (`42S22`, error 207). |
| Pobladas sin `U_CORREO_CORP` | ANDINA, CALCA, MUNDIAL, NK | Falla por `U_CORREO_CORP` y `FUENTE`. |
| Vacías / esquema mínimo | CONDOR, COYA, ESTRATEGO, PROEXPLOR, RELTEX, TANKARPATA, TARAY, TMI, YACLOT | Falla antes por `DEPARTAMENTO`, `PUESTO`, `ESTADO_EMPLEADO`, etc. |

Consecuencia: **18 de 18 empresas fallan** aunque la conexión y los permisos sean correctos.

---

## D. Tablas y columnas: esperadas vs. reales

### D.1 Columnas esperadas por el código

`COLUMNAS_EMPLEADO` (líneas 20-33): `EMPLEADO`, `NOMBRE`, `ESTADO_EMPLEADO`, `ACTIVO`, `DEPARTAMENTO`, `PUESTO`, `PRIMER_APELLIDO`, `SEGUNDO_APELLIDO`, `NOMBRE_PILA`, `E_MAIL`, `U_CORREO_CORP`, `FUENTE`.

### D.2 Columnas reales verificadas

| Elemento | Empresas pobladas (9) | Empresas vacías (9) |
|---|---|---|
| `EMPLEADO.EMPLEADO` | Sí | Sí |
| `EMPLEADO.NOMBRE` | Sí | Sí |
| `EMPLEADO.ESTADO_EMPLEADO` | Sí | **No** |
| `EMPLEADO.ACTIVO` | Sí (`S`/`N`) | Sí |
| `EMPLEADO.DEPARTAMENTO` | Sí | **No** |
| `EMPLEADO.PUESTO` | Sí | **No** |
| `EMPLEADO.PRIMER_APELLIDO` | Sí | **No** |
| `EMPLEADO.SEGUNDO_APELLIDO` | Sí | **No** |
| `EMPLEADO.NOMBRE_PILA` | Sí | **No** |
| `EMPLEADO.E_MAIL` | Sí | **No** |
| `EMPLEADO.U_CORREO_CORP` | Solo 5 empresas | **No** |
| `EMPLEADO.FUENTE` | **No en ninguna** | **No** |
| `DEPARTAMENTO.DESCRIPCION` | Sí | Sí |
| `PUESTO` (tabla) | Sí | **No existe** |

**Hecho confirmado:** `FUENTE` no es una columna de datos; debe generarse como valor literal de procedencia (por ejemplo, la tabla consultada), nunca seleccionarse de `E`.

### D.3 Causas confirmadas

1. **Columnas opcionales tratadas como obligatorias.** `sql_empleados` incluye `E.FUENTE` y `E.U_CORREO_CORP` sin comprobar el esquema. SQL Server no ignora columnas inexistentes: falla al preparar la sentencia (`42S22` / 207 / 8180).
2. **Falta de adaptación por empresa.** El mismo `SELECT` se ejecuta contra 18 esquemas con estructuras distintas.
3. **Mezcla de ausencia de datos con error.** Las 9 empresas vacías se reportan como error de consulta, aunque su estructura mínima solo carece de columnas de negocio.
4. **Pérdida de la decisión manual.** `Revision.seleccionar_candidato` guarda el candidato, pero `_elegir_candidato` usa el departamento como área directamente y no registra empresa, fuente, fecha ni tipo de resolución de forma completa.
5. **Coincidencias no visibles.** `_consultar_sql` actualiza el estado ambiguo, pero no guarda `candidatos` en la revisión ni abre la subventana desde la consulta. `_revisar_coincidencias` suele encontrar la lista vacía.

### D.4 Causas pendientes de verificación

- Significado de negocio del código `ESTADO_EMPLEADO = '07'` y de `VACA`. No se interpreta como activo/inactivo en esta implementación.
- Existencia de otras fuentes de empleados además de `EMPLEADO` en algún esquema. No se detectó en la verificación estructural; no se inventan tablas.
- Correspondencia departamento → área distinta de la usada hoy. El proyecto usa el departamento corporativo como área y protege asignaciones manuales; no se crea un catálogo paralelo.

---

## E. Errores independientes del proceso SQL

### E.1 Hoja `Tickets` inexistente

Mensaje observado:

```
ERROR: No se pudo auditar solicitantes: Worksheet named 'Tickets' not found
```

Ruta del código: `interfaz.py::_refrescar_solicitantes` (2734-2786).

Secuencia real:

1. `MaestroTickets.cargar(ruta_maestro)` lee el maestro histórico.
2. Si se pasa un archivo adicional, se ejecuta `leer_excel(ruta_adicional, sheet_name=cfg.HOJA_DETALLE)` con `HOJA_DETALLE = "Tickets"`.
3. Si el archivo no tiene esa hoja, pandas/openpyxl lanza `ValueError: Worksheet named 'Tickets' not found`.
4. El `except ErrorArchivo` interno no captura `ValueError`; el `except` externo sí, y muestra el mensaje genérico sin indicar archivo ni hojas disponibles.

**Causa confirmada:** se asume que todo archivo adicional tiene una hoja llamada exactamente `Tickets`. No se valida el libro antes de pedirla. No es un error de SQL.

**Corrección prevista:** inspeccionar las hojas antes de leer; informar archivo, hoja esperada y hojas encontradas; continuar sin esa auditoría si es seguro, sin declararla exitosa.

### E.2 Autenticación (diagnóstico previo, independiente)

La configuración guardada usa autenticación Windows (`Trusted_Connection=yes`) con usuario SQL `APPNET`, lo que produce `28000/18456`. Con autenticación SQL el login funciona. Este defecto es independiente de las columnas y debe corregirse en la configuración, no en la consulta. La implementación de este plan no cambia credenciales ni bases de datos.

---

## F. Riesgos de modificar cada componente

| Componente | Riesgo | Mitigación |
|---|---|---|
| `database/queries.py` | Construir SQL dinámico con identificadores no validados | Lista blanca de empresas, validación regex, `QUOTENAME`/corchetes y parámetros `?` para valores. |
| `database/repository.py` | Una empresa bloquea a las demás | `try/except` por empresa; metadatos cacheados; estados separados. |
| `database/resolucion.py` | Elegir automáticamente la primera coincidencia | Mantener `CASO_B` para múltiples; selección explícita. |
| `core/revision.py` | Cambiar el formato JSON y romper revisiones previas | Campos nuevos opcionales; `cargar` ya filtra claves permitidas; no se eliminan campos. |
| `ventana_validacion.py` | Rediseñar la interfaz o bloquear el hilo principal | Mantener `Toplevel`, tema y patrón `_ejecutar`; la subventana se abre en el hilo de Tk. |
| `core/procedencia.py` | Duplicar la fuente de verdad del área | Extender el registro existente; el maestro local sigue siendo la fuente del área. |
| `interfaz.py` (Tickets) | Ocultar el error creando una hoja vacía | No crear hojas; informar y continuar de forma segura. |
| `database/synchronizer.py` | Vaciar la caché con resultados vacíos | No escribir caché vacía si hubo errores y existía caché previa. |

---

## G. Propuesta de implementación

1. **Detección de columnas por empresa:** consultar `INFORMATION_SCHEMA.COLUMNS` para el esquema y construir un mapa `tabla -> columnas` antes de ejecutar. Cachearlo por repositorio.
2. **SELECT adaptativo:** incluir solo columnas existentes. Campos fundamentales: código (`EMPLEADO`) y nombre (`NOMBRE` o composición de partes). Campos opcionales: departamento, puesto, activo, correo. `FUENTE` se genera como literal con el nombre de la tabla.
3. **Resultado uniforme:** `EMPLEADO`, `NOMBRE`, `EMPRESA`, `FUENTE`, `DEPARTAMENTO`, `PUESTO`, `ACTIVO`, `CORREO`, más base/esquema/tabla internos.
4. **Errores diferenciados:** `columna`, `tabla`, `permisos`, `conexion`, `sintaxis`, `campos_insuficientes`, `sin_datos`. Una empresa no bloquea a las demás.
5. **Identificación y ambigüedad:** deduplicar por `(empresa, fuente, código)`; no asumir que el código es único entre empresas; no autoelegir si hay varias coincidencias.
6. **Subventana de selección:** columnas EMPLEADO, NOMBRE, EMPRESA, FUENTE, DEPARTAMENTO, PUESTO, ACTIVO, CORREO; filtros, orden, activo/inactivo, confirmar deshabilitado sin selección, cancelar sin asignar.
7. **Área desde la empresa seleccionada:** reconstruir el `Empleado` del candidato y ejecutar `ResolverAreas` con ese único registro; no reutilizar departamentos de otra empresa.
8. **Persistencia y trazabilidad:** extender `Revision` y `procedencia` con código, nombre, empresa, fuente, departamento, área, fecha y tipo de resolución. Revalidar la selección guardada antes de reutilizarla.
9. **Tickets:** validar hojas antes de leer; informar archivo, hojas encontradas y hoja esperada.
10. **Pruebas:** simuladas, sin conexión real, cubriendo los 24 escenarios del plan.

---

## H. Puntos exactos de integración

- `database/queries.py`: nuevo constructor de SELECT adaptativo y consulta de columnas por tabla.
- `database/repository.py`: detección/caché de capacidades, clasificación de errores, resultado uniforme.
- `database/resolucion.py`: resolución a partir de un candidato seleccionado.
- `core/revision.py`: persistencia enriquecida de la selección.
- `core/procedencia.py`: metadatos de trazabilidad.
- `ventana_validacion.py`: consulta, subventana, aplicación del área.
- `interfaz.py`: auditoría de solicitantes y hoja `Tickets`.
- `database/synchronizer.py`: protección de la caché ante fallo total.
- `tests/` y `diagnostico_sql/`: validación y scripts SSMS.

**Estado:** Fase A completada.
