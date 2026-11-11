# Documentación técnica

Sistema Integral de Tickets TI - INKACROPS

---

## 1. Arquitectura general

La aplicación mantiene una separación estricta entre interfaz, servicios de
negocio, motor ETL, persistencia y auditoría. La interfaz no contiene reglas de
negocio ni escribe archivos directamente.

```text
interfaz.py (Tkinter)
    │
    ├─ ventana_validacion.py (revisión de usuarios y áreas)
    │
    ├─ core/procesador.py ─── core/etl.py ─── core/esquema.py
    │      │                       │
    │      │                       └── core/procesado.py (Excel de salida)
    │      └── database/ (SQL Server, solo lectura) ─── core/revision.py
    │
    ├─ core/reimportacion.py ─── core/maestro_tickets.py
    ├─ core/consolidacion_tickets.py ─── core/maestro_tickets.py
    ├─ core/reportes.py
    ├─ core/asignaciones.py ─── core/maestro.py
    ├─ core/auditoria.py
    ├─ core/bloqueo.py
    └─ core/estado.py
```

Principios:

- **Esquema centralizado:** `core/esquema.py` es la única fuente de verdad de los
  nombres oficiales, alias, orden y huellas.
- **Persistencia desacoplada:** el maestro histórico y la base de áreas son
  archivos Excel/CSV independientes.
- **Trazabilidad fuera del detalle:** estados, IDs y huellas viven en hojas o
  archivos auxiliares.
- **Escritura segura:** archivo temporal + validación + reemplazo atómico +
  rollback.
- **Idempotencia:** consolidar o reimportar dos veces no duplica tickets.

### Módulos principales

| Módulo | Responsabilidad |
|--------|-----------------|
| `config/configuracion.py` | Rutas, alias, formatos, umbrales y parámetros |
| `core/esquema.py` | 11 columnas, alias, normalización canónica y huellas |
| `core/archivos.py` | Lectura/escritura, validación de archivos y errores |
| `core/normalizador.py` | Limpieza de usuarios, áreas y valores |
| `core/etl.py` | Extracción, detección de hoja/encabezado y normalización |
| `core/procesado.py` | Generación del Excel procesado y sus hojas |
| `core/procesador.py` | Orquestación, estados, manifiesto y auditoría |
| `core/maestro_tickets.py` | Maestro histórico, `CONTROL`, atomicidad y respaldos |
| `core/consolidacion_tickets.py` | Procesado → maestro histórico |
| `core/reimportacion.py` | Diferencias, clasificación y aplicación de correcciones |
| `core/reportes.py` | Indicadores, distribuciones, recurrencia y reportes |
| `core/auditoria.py` | Eventos, historial campo a campo y deduplicación |
| `core/asignaciones.py` | CRUD de usuarios/áreas, sugerencias y pendientes |
| `core/bloqueo.py` | Bloqueo exclusivo con detección de lock obsoleto |
| `core/estado.py` | Estado persistente para el panel principal |
| `core/revision.py` | Estado persistente de una sesión de validación |
| `config/sqlserver.py` | Configuración cifrada de la conexión corporativa |
| `database/connection.py` | Conexión ODBC y rechazo de sentencias de escritura |
| `database/repository.py` | Consultas por empresa y por lotes de empleados |
| `database/queries.py` | SQL parametrizado de `EMPLEADO`/`DEPARTAMENTO`/`PUESTO` |
| `database/normalizador.py` | Normalización de nombres y áreas |
| `database/resolucion.py` | Casos A–E, conflictos y sugerencias |
| `database/synchronizer.py` | Caché local de empleados y alertas de cambios |
| `ventana_validacion.py` | Ventana de validación y generación asíncrona |
| `interfaz.py` | GUI Tkinter de 7 pestañas, hilos y cola de eventos |

---

## 2. Modelo de datos

### 2.1 Detalle oficial (11 columnas)

| Columna | Tipo lógico | Regla |
|---------|-------------|-------|
| `Detalle` | Texto | Se conserva el contenido normalizado; no se recorta el significado |
| `Fecha` | Fecha | Se normaliza a fecha; las ambiguas usan el formato configurado |
| `Solicitante` | Texto | Se limpia y se conserva la escritura original normalizada |
| `Area` | Texto | Se asigna desde `maestro_areas`; vacía si el usuario es pendiente |
| `Atención` | Texto | Se conserva/normaliza |
| `Requerimiento` | Texto | Se conserva/normaliza |
| `Responsable` | Texto | Se conserva/normaliza |
| `Subtipo` | Texto | Se conserva/normaliza |
| `Proyecto` | Texto | Se conserva/normaliza |
| `Horas` | Numérico | Valores inválidos no se convierten en cero; quedan observados |
| `Actividad` | Texto | Se conserva/normaliza |

### 2.2 Hojas auxiliares del Excel procesado

| Hoja | Campos |
|------|--------|
| `Consulta` | Detalle con las 11 columnas oficiales (modo normal) |
| `Observaciones` | Fila, campo, motivo, valor encontrado, acción, severidad |
| `Resumen` | Totales, distribución por atención, periodos y control de ejecución |
| `Control` | Ejecución, archivo, hoja, periodo, huellas y conteos |
| `Origen_Adicional` | Columnas extra del origen y número de fila |

En el modo `normal` el libro contiene solo `Consulta`. En el modo `detalle` se
escriben dos archivos: el canónico de una hoja y `<stem>_detalle.xlsx` con las
cinco hojas anteriores. El modo `legacy` conserva la hoja `Tickets` y las cinco
hojas históricas.

### 2.3 Hoja `CONTROL` del maestro histórico

| Columna | Contenido |
|---------|-----------|
| `ID_TECNICO` | UUID interno estable del ticket |
| `HUELLA` | SHA-256 de los 11 valores canónicos |
| `ORIGEN` | Archivo o tipo de operación que originó el registro |
| `EJECUCION_ALTA` | ID de la ejecución que dio de alta |
| `FECHA_ALTA` | Fecha/hora de alta |
| `EJECUCION_ACTUALIZACION` | ID de la última actualización |
| `FECHA_ACTUALIZACION` | Fecha/hora de la última actualización |
| `ESTADO` | `ACTIVO` (los históricos no se eliminan) |

### 2.4 Manifiesto de procesamiento (v2)

```json
{
  "version": 2,
  "id_ejecucion": "20261106_154835_bd65d3",
  "periodo": "2026-09-01/2026-09-30",
  "filas": [
    {
      "fila_origen": 2,
      "id_tecnico": "uuid",
      "huella": "sha256",
      "estado": "VALIDO",
      "datos": { "Detalle": "...", "Fecha": "..." }
    }
  ]
}
```

El manifiesto permite reconstruir la correspondencia entre el Excel procesado y
las filas originales, incluso después de una corrección manual.

### 2.5 Auditoría

`data/auditoria_ejecuciones.csv`:

```text
ID_EJECUCION, FECHA_HORA, TIPO, ARCHIVO_ORIGINAL, ARCHIVO_SALIDA,
PERIODO, LEIDOS, VALIDOS, OBSERVADOS, PENDIENTES, DUPLICADOS, ERRORES,
NUEVOS, MODIFICADOS, USUARIO_EJECUTOR, ESTADO, MENSAJES
```

`data/maestro_tickets_historial.csv` registra un cambio por fila:

```text
ID_EJECUCION, FECHA_HORA, TIPO, ARCHIVO_FUENTE, ID_TECNICO,
CAMPO, VALOR_ANTERIOR, VALOR_NUEVO
```

---

## 3. ETL

### 3.1 Extracción

1. Valida extensión `.xlsx`, existencia y legibilidad.
2. Detecta la hoja: usa la indicada, `Tickets` si existe, o la primera hoja con
   estructura compatible.
3. Localiza la fila de encabezado dentro de las primeras 30 filas.
4. Mapea encabezados de origen a los 11 nombres oficiales mediante alias.
5. Detecta columnas adicionales y las separa.
6. Si falta una columna obligatoria o hay dos columnas con datos para el mismo
   campo, detiene la importación con un error explícito.
7. Omite filas totalmente vacías y conserva el número de fila Excel.

### 3.2 Transformación

- **Texto:** recorta extremos, colapsa espacios y normaliza valores nulos
  (`""`, `NULL`, `N/A`, `NaN`).
- **Fechas:** reconoce fechas de Excel, ISO y formatos con separadores. Las
  fechas ambiguas se interpretan con `FORMATO_FECHA_PREFERIDO`
  (`DD/MM/YYYY` por defecto) y generan una observación. Una fecha inválida no se
  convierte en nula: se conserva y se marca como error.
- **Horas:** convierte números y texto numérico. Horas negativas y no numéricas
  generan observación; no se reemplazan por cero.
- **Usuarios:** normaliza mayúsculas/espacios para comparar, conservando el valor
  legible.
- **Áreas:** se consulta la base `USUARIO → AREA_USUARIO`; si no existe
  correspondencia, el área queda vacía y el registro pasa a `PENDIENTE`.

### 3.3 Validación y estados

| Estado | Condición |
|--------|-----------|
| `VALIDO` | Sin observaciones ni errores |
| `OBSERVADO` | Observaciones no críticas (fechas ambiguas, horas atípicas, etc.) |
| `PENDIENTE` | Solicitante sin área asignada |
| `DUPLICADO` | Huella repetida dentro del archivo |
| `ERROR` | Falta solicitante, fecha u horas, o hay un valor inválido |

Los estados se guardan en el manifiesto y en la hoja `Control`; nunca en el
detalle oficial. Los registros observados, pendientes y duplicados se conservan.

---

## 4. Identidad, huellas y reimportación

### 4.1 Huella canónica

`huella_fila` calcula SHA-256 sobre la concatenación de los 11 valores
canónicos. No incluye estado, área técnica ni metadatos. Es estable entre tipos
(`fecha` vs `texto ISO`, `2.5` vs `"2,5"`).

### 4.2 Correspondencia en reimportación

1. Se busca primero una coincidencia exacta de huella con una fila original no
   utilizada.
2. Si no existe, se calcula una similitud ponderada:

| Campo | Peso |
|-------|-----:|
| Detalle | 0.40 |
| Fecha | 0.16 |
| Solicitante | 0.16 |
| Horas | 0.06 |
| Requerimiento | 0.05 |
| Responsable | 0.05 |
| Área | 0.04 |
| Atención | 0.03 |
| Subtipo | 0.02 |
| Proyecto | 0.02 |
| Actividad | 0.01 |

3. Se descartan candidatos evidentemente distintos (solicitante distinto, fecha
   a más de un día y detalle con similitud baja).
4. Si la mejor similitud supera `UMBRAL_COINCIDENCIA = 0.78`, se propone como
   modificación.
5. Si la diferencia con el segundo candidato es menor que
   `MARGEN_COINCIDENCIA = 0.06`, se marca `AMBIGUO` y se bloquea la aplicación.
6. Las filas sin coincidencia se clasifican como `NUEVO` (o `DUPLICADO` si
   repiten una huella dentro del archivo corregido).
7. Las filas originales no utilizadas se clasifican como `AUSENTE`; **no se
   eliminan**.

### 4.3 Aplicación

- `AMBIGUO` bloquea toda la aplicación.
- `AUSENTE` nunca genera eliminación.
- `DUPLICADO` solo se aplica si el operador lo autoriza.
- `ERROR` no se aplica.
- Los cambios campo a campo se registran en el historial.
- La operación es idempotente.

---

## 5. Consolidación y maestro histórico

### 5.1 Flujo

1. Carga el maestro existente o crea uno vacío.
2. Valida las 11 columnas y la alineación detalle/`CONTROL`.
3. Aplica las filas del procesado usando `ID_TECNICO` del manifiesto.
4. Actualiza las filas existentes y agrega las nuevas.
5. Omite duplicados no autorizados.
6. Conserva todos los registros históricos.
7. Guarda con respaldo, archivo temporal, validación y reemplazo atómico.

### 5.2 Reglas de integridad

- `ID_TECNICO` único y no vacío.
- Una fila de `CONTROL` por cada fila del detalle.
- La huella registrada debe coincidir con la calculada desde el detalle.
- Si el maestro fue editado fuera del sistema, la validación falla y no se
  sobrescribe.
- Si falla cualquier escritura, se restaura la versión anterior.

### 5.3 Respaldos

Cada guardado con `crear_backup=True` copia el archivo existente a
`data/backups/`. La restauración:

1. Carga la copia sin exigir consistencia (puede ser una versión histórica).
2. Crea un respaldo del maestro actual.
3. Guarda con el mismo flujo transaccional.

---

## 6. Reglas de asignación de áreas

1. La clave de comparación es el usuario normalizado (mayúsculas, sin espacios
   redundantes, sin tildes para sugerencias).
2. Si existe una correspondencia válida, se asigna el área persistida.
3. Si el usuario no existe, el área queda vacía y el registro es `PENDIENTE`.
4. Si el archivo de origen trae un área para un usuario desconocido, se conserva
   como valor de origen; no se inventa ni se sobrescribe la base.
5. Las sugerencias usan similitud de nombres y solo se proponen; nunca se aplican
   automáticamente.
6. Las asignaciones se guardan en `data/maestro_areas.xlsx` y su espejo CSV.

### 6.1 Resolución con SQL Server (solo lectura)

`database/resolucion.py` clasifica cada solicitante en uno de cinco casos:

| Caso | Situación | Acción |
|------|-----------|--------|
| `A` | Coincidencia única en la caché corporativa | Asigna el departamento |
| `B` | Varios empleados con el mismo nombre | No asigna; requiere revisión |
| `C` | Sin coincidencias | Pendiente; sugiere nombres parecidos |
| `D` | Empleado sin departamento | Pendiente; no inventa área |
| `E` | Empleado inactivo o cesado | No asigna; requiere revisión |

Reglas:

- La conexión usa `pyodbc` con `SELECT` parametrizado. `validar_solo_lectura`
  rechaza `INSERT`, `UPDATE`, `DELETE`, `MERGE`, `DROP`, `ALTER`, `TRUNCATE` y
  bloques que intenten modificar datos.
- Cada empresa se consulta por separado; un error en una empresa no bloquea a
  las demás y queda en `errores_sql` del manifiesto.
- Las consultas se agrupan por lotes (`lote`) para no saturar la red.
- El maestro local **siempre** tiene prioridad. Si el área local difiere del
  departamento corporativo, se marca conflicto y no se sobrescribe solo.
- La sincronización de la caché no modifica las áreas locales; solo actualiza la
  fotografía de empleados. Los cambios que difieren del maestro generan alertas
  persistentes en `data/sincronizacion.json`.
- El esquema real de EXACTUS debe validarse en el entorno corporativo antes de
  habilitar la integración en producción. `database/queries.py` concentra las
  columnas y tablas para ajustarlas sin tocar la lógica.

### 6.2 Revisión y generación

- `core/revision.py` persiste una sesión de validación en
  `output/revision/revision_<id>.json`.
- La ventana de validación escribe cada asignación manual en el maestro
  (`svc_asignaciones.asignar_una`) y en la revisión.
- La generación se ejecuta en un hilo y la ventana se cierra solo cuando la
  interfaz principal confirma el éxito (`generacion_terminada(True)`).
- Modos de generación:
  - `normal`: una hoja `Consulta`.
  - `detalle`: canónico + libro de cinco hojas.
  - `legacy`: formato histórico, sin sobrescritura automática.
- `permitir_pendientes=False` bloquea la generación si hay registros sin área o
  conflictos. La consola devuelve código 2 y el mensaje `GENERACIÓN CANCELADA`.
- El archivo canónico se reemplaza de forma atómica con respaldo previo; la copia
  de revisión `<stem>_procesado_revision.xlsx` también se reemplaza de forma
  atómica para que "Abrir resultado" siempre apunte a la última versión.

---

## 7. Reportes

`core/reportes.py` calcula:

- Totales, promedios, mediana y distribución de horas.
- Tickets y horas por solicitante, responsable, área, atención, requerimiento,
  subtipo, proyecto y actividad.
- Evolución semanal por semana ISO.
- Carga por responsable y porcentaje de esfuerzo.
- Capacidad nominal = días hábiles × horas de jornada × responsables.
- Imputación = horas registradas / capacidad nominal.
- Recurrencia por detalle idéntico normalizado, con mínimo configurable.
- Recurrencia por combinación requerimiento/subtipo/actividad.

El reporte Excel incluye una hoja `Resumen`, una hoja por distribución con
gráfico de barras y una hoja `Recurrencia`. El reporte Markdown contiene las
mismas tablas y una nota explícita de limitaciones: las horas son esfuerzo
declarado, no tiempos de resolución, y no se calculan SLA.

---

## 8. Auditoría y trazabilidad

Se registran:

- Procesamientos (`PROCESAMIENTO`).
- Reimportaciones (`REIMPORTACION`).
- Consolidaciones (`CONSOLIDACION`).
- Asignaciones de áreas (`ASIGNACION_AREA`).
- Restauraciones de copias.
- Cambios campo a campo del maestro.

La auditoría es independiente del Excel principal, se deduplica por
`ID_EJECUCION` y nunca agrega columnas al detalle oficial.

---

## 9. Seguridad y recuperación

- **Validación de archivos:** extensión, existencia, legibilidad y estructura.
- **Bloqueo exclusivo:** `data/.sistema.lock` evita escrituras simultáneas;
  detecta procesos obsoletos.
- **Escritura atómica:** se escribe en un temporal, se valida y se reemplaza con
  `os.replace`.
- **Rollback:** si falla el reemplazo, se restaura la copia previa.
- **Respaldos:** antes de reemplazar el maestro.
- **Originales intactos:** el archivo de entrada solo se lee; su hash se verifica
  en la auditoría.
- **Archivos bloqueados:** se informa al usuario y no se fuerza la escritura.

---

## 10. Pruebas

Archivos: `tests/test_sistema.py` y `tests/test_sql_revision_generacion.py`.

Categorías:

1. **Esquema:** alias, orden, columnas faltantes/duplicadas, huellas.
2. **ETL:** normalización, fechas ambiguas, horas inválidas, filas vacías,
   archivos inválidos y referencia de septiembre.
3. **Procesamiento:** 11 columnas, hojas auxiliares, manifiesto v2, pendientes,
   duplicados, errores, no sobrescritura, original intacto.
4. **Reimportación:** sin cambios, modificaciones, nuevos, ausentes, duplicados,
   ambiguos, historial e idempotencia.
5. **Consolidación:** maestro inexistente, idempotencia, históricos, alineación
   detalle/`CONTROL`, respaldo/restauración y fallo de escritura sin maestro
   parcial.
6. **Reportes:** indicadores de septiembre, reporte Excel/Markdown y recurrencia.
7. **Servicios:** asignaciones, sugerencias, auditoría, bloqueo y estado.
8. **Integridad:** contabilidad de estados, flujo completo y validación de
   septiembre.
9. **Interfaz:** arranque de la ventana principal.
10. **SQL/revisión/generación:** clasificación de errores de conexión, rechazo de
    escrituras, consultas por empresa y por lotes, casos A–E, conflictos,
    revisiones persistentes, modos normal/detalle, gating de pendientes,
    respaldos, rotación, copia de revisión estable y alertas de sincronización.
11. **Consola:** modo normal por defecto, bloqueo con pendientes (código 2),
    `--permitir-pendientes` y `--modo detalle`.

Comando:

```powershell
python -m unittest discover -s tests -v
```

Resultado de la última ejecución real: **98 pruebas, 0 fallos (4 omitidas)**.

Validación de referencia:

```powershell
python validar_septiembre.py
```

Resultado: **35/35 comprobaciones PASS**, incluyendo 179 tickets, 426.75 horas,
22 días hábiles, 8.1 tickets/día, 19.4 horas/día, 5 responsables, 880 horas de
capacidad, 48.5 % de imputación, distribución de atención 110/41/28 y las tablas
exactas de requerimiento y responsable.

Auditoría técnica:

```powershell
python auditar_sistema.py
```

Resultado: **24 comprobaciones, 0 fallos, 0 advertencias, 1 omitida**
(`maestro_tickets_historial.csv` aún no existe si no se ha realizado una
reimportación con cambios).

---

## 11. Limitaciones conocidas

- Las horas representan esfuerzo declarado; no permiten calcular SLA ni tiempos
  de resolución.
- La detección de recurrencia agrupa solo detalles idénticos normalizados; no
  infiere causas por palabras genéricas.
- La validez funcional del área asignada es responsabilidad del negocio; el
  sistema garantiza integridad y trazabilidad, no la corrección semántica.
- Los alias de encabezados son configurables en `config/configuracion.py`; un
  encabezado nuevo debe agregarse allí.
- Los respaldos se acumulan en `data/backups/`; se recomienda una política de
  retención.
- OneDrive puede cambiar fechas de modificación; para operaciones críticas se
  recomienda trabajar con una copia local y sincronizar después.
- La integración con EXACTUS depende de que el controlador ODBC y el esquema de
  cada empresa estén disponibles. El repositorio es configurable, pero el
  esquema real debe validarse en el entorno corporativo antes de producción.
- La copia de revisión estable conserva solo la última generación; el historial
  completo queda en los respaldos de `data/backups/`.
