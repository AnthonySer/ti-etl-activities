# Diseño técnico — Consulta SQL multiempresa y selección manual

**Fase B.** Este diseño desarrolla el plan `docs/consulta-sql-multiempresa.md` y se apoya en la auditoría `docs/DIAGNOSTICO_ERROR_VALIDACION_SQL.md`.

## 1. Detección de columnas

- Una consulta de metadatos por empresa: `sql_columnas_esquema(base, esquema)` sobre `INFORMATION_SCHEMA.COLUMNS`.
- Se agrupa en `tabla -> {COLUMN_NAME: DATA_TYPE}`.
- Se cachea en el repositorio durante toda la sesión (sincronización o búsqueda por lotes).
- Si el esquema no devuelve metadatos, se usa el constructor heredado solo como compatibilidad con dobles de prueba; en producción el constructor adaptativo es obligatorio.
- Los identificadores se validan con lista blanca de empresas y expresión regular; los valores de búsqueda siguen parametrizados con `?`.

## 2. Clasificación de campos

| Campo lógico | Fuente admitida | Obligatorio |
|---|---|---|
| `EMPLEADO` | `EMPLEADO.EMPLEADO` | Sí |
| `NOMBRE` | `EMPLEADO.NOMBRE`; si falta, `CONCAT(NOMBRE_PILA, PRIMER_APELLIDO, SEGUNDO_APELLIDO)` | Sí (alguna variante) |
| `DEPARTAMENTO` | `DEPARTAMENTO.DESCRIPCION` vía `EMPLEADO.DEPARTAMENTO` | No |
| `PUESTO` | `PUESTO.DESCRIPCION` vía `EMPLEADO.PUESTO` | No |
| `ACTIVO` | `EMPLEADO.ACTIVO`; si falta, `ESTADO_EMPLEADO` | No |
| `CORREO` | `U_CORREO_CORP`; si falta, `E_MAIL` | No |
| `FUENTE` | Literal `'EMPLEADO'` (tabla consultada) | Sí (procedencia) |

Los catálogos `DEPARTAMENTO` y `PUESTO` se unen solo si la tabla y sus columnas de unión/descripción existen. Si no, el campo queda `NULL`.

## 3. Resultado uniforme

Cada registro expone:

```text
EMPLEADO, NOMBRE, EMPRESA, FUENTE, DEPARTAMENTO, PUESTO, ACTIVO, CORREO
BASE_DATOS, ESQUEMA, TABLA
```

Además se conservan las claves históricas en mayúsculas para no romper la caché, la revisión ni la interfaz. En la UI un valor ausente se muestra como `No disponible`; en el modelo interno es cadena vacía/`None`.

## 4. Identificación y ambigüedad

- Deduplicación por `(empresa, fuente, código)`; el código **no** se considera único entre empresas.
- Coincidencia única con nombre normalizado y código/nombre suficiente: resolución automática (comportamiento actual `CASO_A`).
- Varias coincidencias: `CASO_B`, nunca se elige la primera; se abre la subventana.
- Sin coincidencias: `CASO_C`, con sugerencias.
- Sin departamento: `CASO_D`, no se inventa área.
- Inactivo: `CASO_E`, se muestra y no se valida en silencio.
- Registros de distintas empresas se mantienen separados; no se fusionan.

## 5. Flujo de selección manual

1. `CONSULTAR SQL` consulta todas las empresas activas y actualiza la caché con los encontrados.
2. `ResolverAreas` clasifica cada usuario.
3. Si hay una única coincidencia fiable, se asigna como hoy.
4. Si hay varias, se guardan los candidatos en la revisión y se abre `VentanaCoincidencias` en el hilo de Tk.
5. La subventana muestra EMPLEADO, NOMBRE, EMPRESA, FUENTE, DEPARTAMENTO, PUESTO, ACTIVO y CORREO; permite buscar, filtrar y ordenar; «Confirmar» está deshabilitado sin selección; «Cancelar» no asigna nada.
6. Al confirmar se reconstruye el `Empleado` completo del candidato y se ejecuta `ResolverAreas` con ese único registro.
7. El área sale del departamento de la empresa/fuente seleccionada. Si no hay departamento o hay conflicto con una asignación manual, no se asigna área y se informa el motivo.

## 6. Persistencia y trazabilidad

Se extiende el mecanismo existente:

- `Revision.registrar_seleccion` guarda en `seleccionado`: código, nombre, empresa, fuente, departamento, puesto, activo, correo, base, esquema, tabla, área asignada, fecha, tipo de resolución (`manual`/`automatica`) y usuario.
- `procedencia.registrar` acepta metadatos opcionales (`empleado`, `nombre`, `empresa`, `fuente`, `departamento`, `tipo_resolucion`) y los conserva en el registro y el historial.
- Antes de reutilizar una selección guardada se compara `(empresa, fuente, código, departamento)` con el registro actual. Si cambió, la selección se considera inválida y se vuelve a preguntar.
- No se guardan datos sensibles innecesarios ni contraseñas.

## 7. Errores diferenciados

`ErrorConexionSQL.categoria` distingue: `columna`, `tabla`, `permisos`, `conexion`, `autenticacion`, `timeout`, `servidor`, `base_datos`, `sintaxis`, `campos_insuficientes`, `consulta`.

`ResultadoConsulta` añade `empresas_sin_datos` y conserva `errores` con empresa, categoría y mensaje. Cero filas no es error: es `sin_datos`. Una empresa con error no impide las demás.

## 8. Hoja `Tickets`

Antes de leer un archivo adicional se inspeccionan sus hojas. Si `Tickets` no existe, se informa archivo, hoja esperada, hojas encontradas y motivo; la auditoría de solicitantes del maestro continúa si es seguro y el estado se declara parcial, nunca exitoso.

## 9. Integración con la interfaz

- La consulta sigue en un hilo trabajador y el resultado se aplica con `after(0, ...)`.
- La subventana se abre desde el hilo principal.
- Se conserva el tema, tipografía, colores y botones existentes.
- No se rediseña ninguna otra ventana.

## 10. Criterio de aceptación verificable

- Ninguna consulta incluye columnas inexistentes.
- Las 9 empresas pobladas devuelven sus empleados y las 9 vacías se reportan como `sin_datos`.
- Una empresa con error no bloquea al resto.
- Varias coincidencias exigen selección manual.
- El área corresponde al departamento de la empresa/fuente elegida.
- La decisión queda persistida con su procedencia.
- La hoja `Tickets` ausente produce un mensaje informativo, no una excepción silenciosa.
- Las pruebas automatizadas cubren los 24 escenarios y no hay regresiones.
