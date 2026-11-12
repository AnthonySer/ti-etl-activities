/* ============================================================================
   02_consultas_originales.sql
   Consulta ORIGINAL construida por database/queries.py::sql_empleados antes de
   la corrección adaptativa. Se reproduce sin alterar columnas ni uniones; solo
   se sustituye el marcador <EMPRESA> por una empresa concreta para poder
   ejecutarla y reproducir el error en SSMS.

   ADVERTENCIA: estas consultas FALLAN en las 18 empresas.
     - FUENTE no existe en ninguna empresa.
     - U_CORREO_CORP solo existe en FILASUR, INKA, POLYSOL, SERVIAM y TROPICAL.
     - Las 9 empresas vacías tampoco tienen DEPARTAMENTO, PUESTO,
       ESTADO_EMPLEADO, PRIMER_APELLIDO, SEGUNDO_APELLIDO, NOMBRE_PILA ni E_MAIL.

   Errores esperados: 42S22 / 207 "Invalid column name".
   SOLO LECTURA. No modifica datos.
   ============================================================================ */

USE [EXACTUS];
GO

/* ----------------------------------------------------------------------------
   02.1 — Original contra SERVIAM (empresa poblada, sí tiene U_CORREO_CORP).
   Falla por: Invalid column name 'FUENTE'.
   ---------------------------------------------------------------------------- */
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
FROM [EXACTUS].[SERVIAM].[EMPLEADO] E
LEFT JOIN [EXACTUS].[SERVIAM].[DEPARTAMENTO] D
       ON D.DEPARTAMENTO = E.DEPARTAMENTO
LEFT JOIN [EXACTUS].[SERVIAM].[PUESTO] P
       ON P.PUESTO = E.PUESTO
ORDER BY E.EMPLEADO;
GO

/* ----------------------------------------------------------------------------
   02.2 — Original contra ANDINA (empresa poblada, NO tiene U_CORREO_CORP).
   Falla por: Invalid column name 'U_CORREO_CORP' y 'FUENTE'.
   ---------------------------------------------------------------------------- */
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
FROM [EXACTUS].[ANDINA].[EMPLEADO] E
LEFT JOIN [EXACTUS].[ANDINA].[DEPARTAMENTO] D
       ON D.DEPARTAMENTO = E.DEPARTAMENTO
LEFT JOIN [EXACTUS].[ANDINA].[PUESTO] P
       ON P.PUESTO = E.PUESTO
ORDER BY E.EMPLEADO;
GO

/* ----------------------------------------------------------------------------
   02.3 — Original contra CONDOR (empresa vacía / esquema mínimo).
   Falla antes de unir catálogos: no existen DEPARTAMENTO, PUESTO,
   ESTADO_EMPLEADO, PRIMER_APELLIDO, SEGUNDO_APELLIDO, NOMBRE_PILA, E_MAIL,
   U_CORREO_CORP ni FUENTE en EMPLEADO. Tampoco existe la tabla PUESTO.
   PENDIENTE DE VALIDACIÓN EN SSMS: el error exacto depende del orden de
   resolución de nombres de SQL Server.
   ---------------------------------------------------------------------------- */
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
FROM [EXACTUS].[CONDOR].[EMPLEADO] E
LEFT JOIN [EXACTUS].[CONDOR].[DEPARTAMENTO] D
       ON D.DEPARTAMENTO = E.DEPARTAMENTO
LEFT JOIN [EXACTUS].[CONDOR].[PUESTO] P
       ON P.PUESTO = E.PUESTO
ORDER BY E.EMPLEADO;
GO

/* ----------------------------------------------------------------------------
   02.4 — Consulta original de búsqueda por nombre (variante parametrizada).
   Se muestra el patrón; los valores reales viajan como parámetros desde Python
   y NUNCA se concatenan. Las cuatro variantes de nombre se construían con
   NOMBRE, NOMBRE_PILA, PRIMER_APELLIDO y SEGUNDO_APELLIDO.
   PENDIENTE DE VALIDACIÓN EN SSMS: no se ejecuta aquí para no sustituir
   parámetros por valores inventados.
   ---------------------------------------------------------------------------- */
/*
SELECT ...
FROM [EXACTUS].[<EMPRESA>].[EMPLEADO] E
...
WHERE (
    <variante de nombre 1> IN (?, ?, ...)
    OR <variante de nombre 2> IN (?, ?, ...)
    OR <variante de nombre 3> IN (?, ?, ...)
    OR <variante de nombre 4> IN (?, ?, ...)
)
ORDER BY E.EMPLEADO;
*/
GO
