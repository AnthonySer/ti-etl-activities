# IMPLEMENTACIÓN FASE 4 — Asignación de áreas y creación de nuevas áreas

## Objetivo

Mejorar la asignación desde coincidencias y permitir crear áreas nuevas de
forma segura, sin duplicados y sin reactivar usuarios inactivos.

## Archivos modificados

- `core/catalogo_areas.py` (nuevo).
- `core/asignacion_asistida.py` (nuevo).
- `interfaz.py`.
- `ventana_validacion.py`.
- `core/asignaciones.py`.

## Componentes creados

- `core.catalogo_areas`:
  - catálogo derivado del maestro `USUARIO -> AREA_USUARIO`;
  - detección de duplicados normalizados;
  - detección de áreas parecidas;
  - `validar_area`, `validar_areas`;
  - `crear_area_para_usuario` con confirmación y reutilización.
- `core.asignacion_asistida`:
  - `preparar_asignacion`;
  - `ejecutar_asignacion`;
  - confirmación explícita;
  - advertencia y permiso expreso para usuarios inactivos.

## Funcionalidades implementadas

- La asignación desde una coincidencia muestra usuario, candidato, empresa y
  área antes de escribir.
- Toda asignación requiere confirmación.
- Si el candidato está inactivo se muestra:
  "El usuario encontrado ... figura como inactivo. ¿Está seguro de asignar un
  área a un usuario inactivo? La asignación no reactivará su cuenta."
- Asignar un área solo escribe la correspondencia en el maestro; nunca cambia
  el estado del trabajador ni lo reactiva en SQL.
- Creación de áreas desde:
  - la ventana de coincidencias;
  - el flujo de usuarios sin área.
- Validaciones antes de crear:
  - nombre no vacío;
  - limpieza de espacios;
  - duplicado por mayúsculas, tildes y espacios;
  - advertencia de área parecida;
  - opción de reutilizar el área existente;
  - confirmación explícita antes de registrar una nueva.
- El guardado del maestro genera respaldo y verifica la escritura a través de
  la capa de integridad de la fase 5.
- Los eventos `AREA_CREADA` y `CATALOGO_AREAS_ACTUALIZADO` refrescan las tablas
  afectadas.

## Pruebas ejecutadas

- `PruebasCatalogoAreas`:
  - catálogo único;
  - área vacía no válida;
  - duplicado por tildes y mayúsculas;
  - área parecida requiere confirmación;
  - creación requiere confirmación;
  - creación reutiliza un duplicado.
- `PruebasAsignacionAsistida`:
  - asignación normal requiere confirmación;
  - usuario inactivo requiere confirmación expresa.
- Suite completa del proyecto.

## Resultados obtenidos

- 8 pruebas específicas de catálogo y asignación asistida: **OK**.
- Suite completa: **212 pruebas, 4 omitidas, 0 fallos**.
- No se crean áreas duplicadas.
- Los usuarios inactivos conservan su estado.
- Las asignaciones ambiguas quedan para revisión manual.

## Problemas encontrados

- No existe una tabla separada de áreas; el catálogo se deriva del maestro. Se
  documentó que crear un área equivale a asignarla a un usuario.
- Fue necesario evitar dobles confirmaciones y dobles escrituras al integrar la
  asignación asistida con la ventana de validación.

## Pendientes

- Ninguno crítico. La lista de áreas sugeridas puede ampliarse con un catálogo
  histórico si el negocio lo solicita.

## Compatibilidad con fases anteriores

La estructura del maestro y su formato no cambian. Se conservan los flujos de
asignación manual existentes. La fase 1 recibe los eventos de área creada; la
fase 3 aporta los candidatos y la fase 5 protege la escritura.
