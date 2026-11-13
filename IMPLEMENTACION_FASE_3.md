# IMPLEMENTACIÓN FASE 3 — Integración de coincidencias en el procesamiento Excel

## Objetivo

Automatizar la búsqueda de coincidencias para usuarios sin área, consolidando
SQL, caché local y Excel maestro, sin asignar áreas automáticamente por
similitud.

## Archivos modificados

- `database/coincidencias.py` (nuevo).
- `ventana_validacion.py`.
- `interfaz.py`.
- `database/resolucion.py`.
- `core/similitud.py` (reutilización).

## Componentes creados

- `database.coincidencias`:
  - `GrupoCoincidencias`.
  - `ConsolidadorCoincidencias`.
  - `consolidar_coincidencias`.
  - Conversión uniforme de empleados y filas del maestro a candidatos.
  - Deduplicación por origen, empresa, empleado y nombre.
  - Orden exacto primero, luego por similitud y empresa.
  - Caché SQL por ejecución e invalidación explícita.
  - `buscar_para_pendientes` para la tabla de usuarios sin área.

## Funcionalidades implementadas

- Consolidación de candidatos de SQL, caché local y maestro en una sola lista.
- Separación estricta por empresa y origen: no se mezclan resultados de
  empresas distintas.
- Deduplicación de claves de búsqueda sin eliminar registros originales.
- Reutilización de resultados SQL dentro de la misma ejecución para evitar
  consultas repetitivas.
- La ventana `VentanaCoincidencias` muestra `COINCIDENCIA` y `SIMILITUD`,
  permite ordenar por esas columnas y conserva las columnas anteriores.
- Se añadió la acción **Crear área nueva…** desde la ventana de coincidencias.
- La consulta SQL de la ventana de validación publica
  `CONSULTA_SQL_FINALIZADA` para refrescar pendientes y panel.
- Los candidatos no se asignan solos: siempre requieren revisión y confirmación.

## Pruebas ejecutadas

- `PruebasCoincidencias`:
  - consolidación de caché, SQL y maestro sin mezclar empresas;
  - nombre parecido no marcado como exacto;
  - no repetición de consulta SQL en la misma ejecución;
  - invalidación para volver a consultar;
  - conservación de filas y orden en `buscar_para_pendientes`.
- Suite completa del proyecto.

## Resultados obtenidos

- 5 pruebas específicas de coincidencias: **OK**.
- Suite completa: **212 pruebas, 4 omitidas, 0 fallos**.
- Los resultados exactos aparecen antes que los aproximados.
- La caché SQL evita repetir la misma consulta en una ejecución.

## Problemas encontrados

- Fue necesario distinguir un nombre parecido (`alta`) de uno exacto para no
  presentar coincidencias engañosas.
- La ventana de coincidencias debía seguir mostrando los campos históricos que
  usan las tablas existentes.

## Pendientes

- Ninguno crítico. La ventana puede ampliarse con filtros adicionales si el
  volumen de candidatos lo exige.

## Compatibilidad con fases anteriores

Se conservan las claves históricas de los candidatos (`empresa`, `empleado`,
`nombre`, `departamento`) y se añaden `tipo`, `similitud`, `origen` y `estado`.
La fase 1 sigue funcionando mediante los eventos existentes. La fase 2 aporta
la clasificación usada en la nueva columna.
