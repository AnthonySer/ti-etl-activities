/* ============================================================================
   00_inspeccionar_esquema.sql
   Proyecto: Validación de Usuarios y Áreas / EXACTUS
   Objetivo: inspeccionar, SIN MODIFICAR NADA, los esquemas de las 18 empresas,
             sus tablas y las columnas reales de EMPLEADO, DEPARTAMENTO y PUESTO.

   Contexto confirmado en el diagnóstico (2026-11-11):
     - Base de datos: EXACTUS
     - Nomenclatura: [EXACTUS].[<EMPRESA>].[TABLA]
     - 18 esquemas de empresa: ANDINA, CALCA, CONDOR, COYA, ESTRATEGO, FILASUR,
       INKA, MUNDIAL, NK, POLYSOL, PROEXPLOR, RELTEX, SERVIAM, TANKARPATA,
       TARAY, TMI, TROPICAL, YACLOT.

   Este script es de SOLO LECTURA. No contiene credenciales ni instrucciones
   destructivas. Ejecutar en SSMS con un login de lectura.
   ============================================================================ */

USE [EXACTUS];
GO

/* 00.1 — Esquemas de empresa que existen realmente. */
SELECT
    s.name AS ESQUEMA,
    s.schema_id AS SCHEMA_ID
FROM sys.schemas AS s
WHERE s.name IN (
    'ANDINA', 'CALCA', 'CONDOR', 'COYA', 'ESTRATEGO', 'FILASUR',
    'INKA', 'MUNDIAL', 'NK', 'POLYSOL', 'PROEXPLOR', 'RELTEX',
    'SERVIAM', 'TANKARPATA', 'TARAY', 'TMI', 'TROPICAL', 'YACLOT'
)
ORDER BY s.name;
GO

/* 00.2 — Tablas y vistas de cada empresa (solo nombres, sin datos). */
SELECT
    TABLE_SCHEMA AS EMPRESA,
    TABLE_NAME AS OBJETO,
    TABLE_TYPE AS TIPO
FROM INFORMATION_SCHEMA.TABLES
WHERE TABLE_SCHEMA IN (
    'ANDINA', 'CALCA', 'CONDOR', 'COYA', 'ESTRATEGO', 'FILASUR',
    'INKA', 'MUNDIAL', 'NK', 'POLYSOL', 'PROEXPLOR', 'RELTEX',
    'SERVIAM', 'TANKARPATA', 'TARAY', 'TMI', 'TROPICAL', 'YACLOT'
)
  AND TABLE_NAME IN ('EMPLEADO', 'DEPARTAMENTO', 'PUESTO')
ORDER BY TABLE_SCHEMA, TABLE_NAME;
GO

/* 00.3 — Columnas reales de EMPLEADO, DEPARTAMENTO y PUESTO por empresa.
   Esta consulta es la fuente de verdad para construir el SELECT adaptativo:
   si una columna no aparece aquí, NO debe incluirse en la consulta. */
SELECT
    TABLE_SCHEMA AS EMPRESA,
    TABLE_NAME AS TABLA,
    ORDINAL_POSITION AS POSICION,
    COLUMN_NAME AS COLUMNA,
    DATA_TYPE AS TIPO_DATO,
    CHARACTER_MAXIMUM_LENGTH AS LONGITUD,
    IS_NULLABLE AS PERMITE_NULO
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA IN (
    'ANDINA', 'CALCA', 'CONDOR', 'COYA', 'ESTRATEGO', 'FILASUR',
    'INKA', 'MUNDIAL', 'NK', 'POLYSOL', 'PROEXPLOR', 'RELTEX',
    'SERVIAM', 'TANKARPATA', 'TARAY', 'TMI', 'TROPICAL', 'YACLOT'
)
  AND TABLE_NAME IN ('EMPLEADO', 'DEPARTAMENTO', 'PUESTO')
ORDER BY TABLE_SCHEMA, TABLE_NAME, ORDINAL_POSITION;
GO

/* 00.4 — Conteo de filas por empresa. Solo agregados; no expone empleados.
   Se usa para distinguir "empresa sin datos" de "error de esquema". */
DECLARE @sql AS nvarchar(max) = N'';

/* SQL Server 2016 no tiene STRING_AGG: se usa FOR XML PATH. */
SELECT @sql = STUFF(
    (
        SELECT N' UNION ALL SELECT ''' + TABLE_SCHEMA
             + N''' AS EMPRESA, COUNT_BIG(*) AS FILAS '
             + N'FROM [EXACTUS].[' + TABLE_SCHEMA + N'].[EMPLEADO]'
        FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_NAME = 'EMPLEADO'
          AND TABLE_SCHEMA IN (
            'ANDINA', 'CALCA', 'CONDOR', 'COYA', 'ESTRATEGO', 'FILASUR',
            'INKA', 'MUNDIAL', 'NK', 'POLYSOL', 'PROEXPLOR', 'RELTEX',
            'SERVIAM', 'TANKARPATA', 'TARAY', 'TMI', 'TROPICAL', 'YACLOT'
          )
        ORDER BY TABLE_SCHEMA
        FOR XML PATH(''), TYPE
    ).value('.', 'nvarchar(max)'),
    1,
    11,
    N''
);

IF @sql IS NOT NULL
BEGIN
    SET @sql = @sql + N' ORDER BY EMPRESA;';
    EXEC sys.sp_executesql @sql;
END
ELSE
BEGIN
    SELECT CAST('PENDIENTE DE VALIDACIÓN EN SSMS: no se encontró EMPLEADO.' AS varchar(200)) AS RESULTADO;
END
GO
