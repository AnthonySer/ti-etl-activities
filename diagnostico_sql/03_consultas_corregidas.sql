/* ============================================================================
   03_consultas_corregidas.sql
   Versiones propuestas que OMITEN antes de ejecutar las columnas opcionales
   inexistentes y normalizan el resultado a los ocho campos lógicos:

       empleado, nombre, empresa, fuente, departamento, puesto, activo, correo

   Reglas aplicadas:
     - No se selecciona FUENTE de la tabla: se genera 'EMPLEADO' como literal.
     - U_CORREO_CORP solo se usa si la empresa la tiene.
     - DEPARTAMENTO y PUESTO se resuelven por LEFT JOIN al catálogo cuando
       existen las columnas de unión y DESCRIPCION.
     - Si falta un campo opcional se usa NULL (la interfaz mostrará
       "No disponible"), nunca una columna inexistente.
     - Los valores de búsqueda se parametrizan; aquí se muestran consultas de
       inspección sin filtros para validar columnas y resultados.

   SOLO LECTURA. No modifica datos ni estructuras.
   ============================================================================ */

USE [EXACTUS];
GO

/* ----------------------------------------------------------------------------
   03.1 — SERVIAM: empresa poblada CON U_CORREO_CORP.
   ---------------------------------------------------------------------------- */
SELECT
    E.EMPLEADO                                   AS EMPLEADO,
    E.NOMBRE                                     AS NOMBRE,
    'SERVIAM'                                    AS EMPRESA,
    'EMPLEADO'                                   AS FUENTE,
    COALESCE(D.DESCRIPCION, E.DEPARTAMENTO)      AS DEPARTAMENTO,
    COALESCE(P.DESCRIPCION, E.PUESTO)            AS PUESTO,
    E.ACTIVO                                     AS ACTIVO,
    COALESCE(NULLIF(LTRIM(RTRIM(E.E_MAIL)), ''),
             NULLIF(LTRIM(RTRIM(E.U_CORREO_CORP)), '')) AS CORREO,
    E.ESTADO_EMPLEADO                            AS ESTADO_EMPLEADO,
    E.DEPARTAMENTO                               AS DEPARTAMENTO_CODIGO,
    E.PUESTO                                     AS PUESTO_CODIGO,
    E.U_CORREO_CORP                              AS U_CORREO_CORP
FROM [EXACTUS].[SERVIAM].[EMPLEADO] E
LEFT JOIN [EXACTUS].[SERVIAM].[DEPARTAMENTO] D
       ON D.DEPARTAMENTO = E.DEPARTAMENTO
LEFT JOIN [EXACTUS].[SERVIAM].[PUESTO] P
       ON P.PUESTO = E.PUESTO
ORDER BY E.EMPLEADO;
GO

/* ----------------------------------------------------------------------------
   03.2 — ANDINA: empresa poblada SIN U_CORREO_CORP.
   Se omite la columna y se usa E_MAIL; si faltara, se devolvería NULL.
   ---------------------------------------------------------------------------- */
SELECT
    E.EMPLEADO                                   AS EMPLEADO,
    E.NOMBRE                                     AS NOMBRE,
    'ANDINA'                                     AS EMPRESA,
    'EMPLEADO'                                   AS FUENTE,
    COALESCE(D.DESCRIPCION, E.DEPARTAMENTO)      AS DEPARTAMENTO,
    COALESCE(P.DESCRIPCION, E.PUESTO)            AS PUESTO,
    E.ACTIVO                                     AS ACTIVO,
    NULLIF(LTRIM(RTRIM(E.E_MAIL)), '')           AS CORREO,
    E.ESTADO_EMPLEADO                            AS ESTADO_EMPLEADO,
    E.DEPARTAMENTO                               AS DEPARTAMENTO_CODIGO,
    E.PUESTO                                     AS PUESTO_CODIGO
FROM [EXACTUS].[ANDINA].[EMPLEADO] E
LEFT JOIN [EXACTUS].[ANDINA].[DEPARTAMENTO] D
       ON D.DEPARTAMENTO = E.DEPARTAMENTO
LEFT JOIN [EXACTUS].[ANDINA].[PUESTO] P
       ON P.PUESTO = E.PUESTO
ORDER BY E.EMPLEADO;
GO

/* ----------------------------------------------------------------------------
   03.3 — CONDOR: empresa vacía / esquema mínimo.
   Columnas reales de EMPLEADO: EMPLEADO, NOMBRE, NIT, ACTIVO y auditoría.
   No existe DEPARTAMENTO ni PUESTO en EMPLEADO, ni la tabla PUESTO.
   Se devuelven NULL en los campos ausentes; la empresa no bloquea a las demás.
   ---------------------------------------------------------------------------- */
SELECT
    E.EMPLEADO                    AS EMPLEADO,
    E.NOMBRE                      AS NOMBRE,
    'CONDOR'                      AS EMPRESA,
    'EMPLEADO'                    AS FUENTE,
    CAST(NULL AS varchar(255))    AS DEPARTAMENTO,
    CAST(NULL AS varchar(255))    AS PUESTO,
    E.ACTIVO                      AS ACTIVO,
    CAST(NULL AS varchar(255))    AS CORREO,
    CAST(NULL AS varchar(50))     AS ESTADO_EMPLEADO,
    CAST(NULL AS varchar(50))     AS DEPARTAMENTO_CODIGO,
    CAST(NULL AS varchar(50))     AS PUESTO_CODIGO
FROM [EXACTUS].[CONDOR].[EMPLEADO] E
ORDER BY E.EMPLEADO;
GO

/* ----------------------------------------------------------------------------
   03.4 — Plantilla del SELECT adaptativo que construye el repositorio.
   El código decide ANTES de ejecutar qué líneas incluir. Esta plantilla no se
   ejecuta: documenta la regla de omisión.

   Seudocódigo (implementado en database/queries.py::sql_empleados):
     columnas = leer INFORMATION_SCHEMA.COLUMNS de [EXACTUS].[<EMPRESA>]
     si no existe EMPLEADO.EMPLEADO -> EsquemaInsuficiente
     si no existe NOMBRE ni partes de nombre -> EsquemaInsuficiente
     SELECT
         E.[EMPLEADO] AS EMPLEADO,
         <NOMBRE o CONCAT de partes> AS NOMBRE,
         '<EMPRESA>' AS EMPRESA,
         'EMPLEADO' AS FUENTE,
         <D.DESCRIPCION o E.DEPARTAMENTO o NULL> AS DEPARTAMENTO,
         <P.DESCRIPCION o E.PUESTO o NULL> AS PUESTO,
         <E.ACTIVO o NULL> AS ACTIVO,
         <E.E_MAIL o E.U_CORREO_CORP o NULL> AS CORREO
     FROM [EXACTUS].[<EMPRESA>].[EMPLEADO] E
     [LEFT JOIN DEPARTAMENTO solo si existen columnas de unión y DESCRIPCION]
     [LEFT JOIN PUESTO       solo si existen columnas de unión y DESCRIPCION]
     ORDER BY E.EMPLEADO

   PENDIENTE DE VALIDACIÓN EN SSMS: la plantilla se valida ejecutando 03.1,
   03.2 y 03.3, que representan los tres esquemas reales.
   ---------------------------------------------------------------------------- */
GO
