# Estado actual del proyecto y último plan solicitado

**Sistema Integral de Tickets TI — INKACROPS**

- **Fecha del documento:** 09/11/2026
- **Alcance:** estado implementado del sistema, flujo operativo vigente y plan propuesto para la generación de Excel.
- **Importante:** el plan descrito en la sección 4 **no está implementado**. Este documento solo lo documenta para su revisión y aprobación.

---

## 1. Resumen ejecutivo

El sistema es una aplicación de escritorio en Python (Tkinter/ttk) con un motor ETL que procesa tickets de TI, asigna áreas a partir de una base persistente `USUARIO → ÁREA`, genera un Excel procesado con un esquema oficial e inalterable de **11 columnas**, permite reimportar correcciones, consolidar un maestro histórico y registrar toda la operación en una auditoría independiente.

El estado actual funciona y está validado:

- Interfaz gráfica de 7 pestañas con panel principal tipo dashboard.
- Procesamiento ETL con detección de hoja, encabezado, alias de columnas y validación estructural.
- Generación de Excel procesado con hoja de detalle más hojas auxiliares.
- Manifiesto JSON por cada procesamiento para trazabilidad y reimportación.
- Reimportación con clasificación de cambios y protección contra duplicados.
- Consolidación idempotente en el maestro histórico.
- Reportes en Excel y Markdown.
- Auditoría en CSV independiente del Excel principal.
- Bloqueo cooperativo, escritura temporal, reemplazo atómico y copias de seguridad.
- **59 pruebas automatizadas aprobadas** (`python -m unittest discover -s tests`).

El último plan solicitado busca simplificar la salida normal a **una sola hoja (`Consulta`)**, separar la generación normal de la generación a detalle, sobrescribir el archivo canónico con respaldo automático y abrir una **copia de revisión** en lugar del archivo canónico.

---

## 2. Estado actual del proyecto

### 2.1 Funcionalidades implementadas

| Área | Estado actual |
|------|---------------|
| Carga y validación de Excel | Implementado: selección de archivo, detección de hoja y fila de encabezado, vista previa y validación de las 11 columnas. |
| ETL y normalización | Implementado: limpieza de textos, fechas, horas, nulos y usuarios. |
| Asignación de áreas | Implementado: base persistente `USUARIO → ÁREA`, búsqueda, sugerencias por similitud y detección de pendientes. |
| Excel procesado | Implementado: modo `normal` con una hoja `Consulta` y modo `detalle` con cinco hojas; respaldo previo y manifiesto. |
| Manifiesto de control | Implementado: `.manifest.json` junto a cada salida, con modo, pendientes, resoluciones y conflictos. |
| Integración SQL Server | Implementado (solo lectura): conexión ODBC, consultas parametrizadas por empresa y por lotes, caché local y alertas de cambios. |
| Validación de áreas | Implementado: ventana dedicada con KPIs, filtros, asignación manual, homónimos, validación masiva y reanudación de revisiones. |
| Reimportación | Implementado: filas nuevas, modificadas, sin cambios, duplicadas, ambiguas, ausentes y con error. |
| Maestro histórico | Implementado: consolidación idempotente, control por `ID_TECNICO` y huella, conservación de periodos y respaldos. |
| Reportes | Implementado: Excel con gráficos y Markdown. |
| Auditoría | Implementado: procesamientos, reimportaciones, consolidaciones, asignaciones, cambios y restauraciones. |
| Interfaz gráfica | Implementado: 7 pestañas, panel principal con KPIs y acciones rápidas. |
| Consola | Implementado: `python main.py <archivo>` con `--modo normal|detalle|legacy` y `--permitir-pendientes`. |
| Pruebas | Implementado: 100 pruebas unitarias/integración aprobadas (4 omitidas). |

### 2.2 Estructura del proyecto

```text
script automatizador/
├─ main.py                     # GUI sin argumentos; flujo de consola con archivo
├─ interfaz.py                 # Interfaz Tkinter de 7 pestañas
├─ panel_principal.py          # Dashboard del panel principal (solo UI)
├─ auditar_sistema.py          # Auditoría técnica de integridad
├─ validar_septiembre.py       # Validación contra la referencia de septiembre 2026
├─ construir_maestro.py        # Herramienta avanzada: base inicial de áreas
├─ actualizar_maestro.py       # Herramienta avanzada: correcciones manuales de áreas
├─ config/
│  ├─ configuracion.py         # Rutas, alias, umbrales y parámetros
│  └─ logging_config.py        # Logs rotativos
├─ core/
│  ├─ esquema.py               # Esquema de 11 columnas, alias y huellas
│  ├─ etl.py                   # Extracción y normalización
│  ├─ procesador.py            # Orquestación del procesamiento y manifiesto
│  ├─ procesado.py             # Escritura del Excel procesado
│  ├─ reimportacion.py         # Comparación y aplicación de correcciones
│  ├─ consolidacion_tickets.py # Procesado → maestro histórico
│  ├─ maestro_tickets.py       # Maestro histórico y hoja CONTROL
│  ├─ maestro.py               # Base USUARIO → ÁREA
│  ├─ estado.py                # Estado persistente de la interfaz
│  ├─ auditoria.py             # Registro de ejecuciones y cambios
│  ├─ reportes.py              # Reportes Excel/Markdown
│  ├─ bloqueo.py               # Bloqueo cooperativo
│  └─ archivos.py              # Lectura/escritura atómica y errores
├─ tests/
│  └─ test_sistema.py          # 59 pruebas automatizadas
├─ input/                      # Excel de origen (nunca se modifican)
├─ output/                     # Excel procesados y manifiestos
├─ data/                       # Maestros, estado, auditoría y respaldos
├─ reportes/                   # Reportes generados
└─ logs/                       # Log técnico rotativo
```

### 2.3 Flujo operativo vigente

```text
Seleccionar Excel
  → Validar y previsualizar
  → Procesar y validar
  → Resolver pendientes en la ventana de validación (si existen)
  → Generar Excel normal o a detalle
  → Revisar/corregir el Excel procesado
  → Reimportar correcciones
  → Consolidar en el maestro histórico
  → Generar reportes
```

Reglas vigentes:

1. El archivo original de `input/` **nunca se modifica**.
2. Todo el trabajo se realiza sobre copias procesadas en `output/`.
3. Cada procesamiento genera un Excel procesado, su manifiesto JSON y una copia de revisión.
4. El archivo canónico se sobrescribe con respaldo previo; el modo `legacy` conserva el comportamiento de crear una versión nueva.
5. La reimportación y la consolidación requieren el manifiesto del procesamiento original.
6. Las filas ausentes en una corrección **no eliminan** registros del maestro.
7. La consolidación es idempotente: aplicar dos veces no duplica tickets.
8. La auditoría se guarda fuera del Excel principal y no agrega columnas al detalle oficial.
9. La generación completa se bloquea si hay registros sin área o conflictos, salvo confirmación de generación parcial.

### 2.4 Interfaz gráfica actual

La ventana principal tiene 7 pestañas:

1. **Panel principal**
2. **Cargar y procesar**
3. **Usuarios y áreas**
4. **Reimportar correcciones**
5. **Maestro histórico**
6. **Reportes**
7. **Auditoría**

El **Panel principal** muestra:

- Estado general del sistema.
- Último procesamiento: archivo, hoja, periodo, ejecución y fecha.
- KPIs: registros leídos, válidos, observados, pendientes, duplicados y errores.
- Horas registradas.
- Estado del maestro histórico y cantidad de tickets.
- Últimos mensajes del sistema.
- Acciones rápidas: cargar archivo, abrir Excel procesado, abrir carpeta de salida y actualizar estado.

En la pestaña **Cargar y procesar**, los controles actuales son:

- **Examinar…**
- **Validar y previsualizar**
- **Procesar y validar**
- **Generar Excel normal**
- **Generar Excel a detalle**
- **Abrir resultado** (abre la copia de revisión)
- **Abrir carpeta de salida**
- **Reanudar revisión**

La **ventana de validación de usuarios y áreas** se abre automáticamente cuando
hay pendientes o conflictos y ofrece KPIs, filtros, asignación manual, consulta
SQL, revisión de homónimos, validación masiva y generación completa o parcial.

### 2.5 Esquema oficial de 11 columnas

Todos los archivos principales conservan exactamente estas columnas y en este orden:

| # | Columna | Alias de origen reconocidos |
|---|---------|-----------------------------|
| 1 | `Detalle` | `DETALLE`, `DETALLE_TICKET`, `DESCRIPCION`, `DETALLE_INCIDENCIA` |
| 2 | `Fecha` | `FECHA`, `FECHA_REGISRO`, `FECHA_REGISTRO`, `FECHA_REGISTR0`, `FECHA_SOLICITUD` |
| 3 | `Solicitante` | `SOLICITANTE`, `USUARIO`, `USUARIO_SOLICITANTE`, `NOMBRE_SOLICITANTE` |
| 4 | `Area` | `AREA`, `AREA_USUARIO`, `ÁREA` |
| 5 | `Atención` | `ATENCION`, `TIPO_ATENCION`, `TIPO DE ATENCIÓN` |
| 6 | `Requerimiento` | `REQUERIMIENTO`, `TIPO_REQUERIMIENTO` |
| 7 | `Responsable` | `RESPONSABLE`, `TECNICO`, `ASIGNADO` |
| 8 | `Subtipo` | `SUBTIPO`, `SUB_TIPO`, `SUBT_TIPO`, `SUBTIPO_TICKET` |
| 9 | `Proyecto` | `PROYECTO`, `NOMBRE_PROYECTO` |
| 10 | `Horas` | `HORAS`, `HORAS_TOTAL`, `HORAS_TOTALES`, `TIEMPO`, `TIEMPO_H` |
| 11 | `Actividad` | `ACTIVIDAD`, `GRUPO_1`, `GRUPO1`, `TIPO_ACTIVIDAD` |

Reglas estructurales:

- La hoja de detalle no admite columnas adicionales, identificadores internos ni estados técnicos.
- Las columnas adicionales del origen (por ejemplo `EMPRESA`) se conservan en una hoja auxiliar.
- Si dos columnas del origen representan el mismo campo y ambas tienen datos, la importación se detiene para revisión manual.
- Si falta una columna obligatoria, se informa exactamente cuál falta.

### 2.6 Hojas del Excel procesado actual

| Hoja | Contenido |
|------|-----------|
| `Consulta` | Detalle con exactamente las 11 columnas oficiales (modo normal y canónico del detalle). |
| `Observaciones` | Fila, severidad, campo, motivo, valor y acción requerida (solo detalle). |
| `Resumen` | Totales, indicadores y distribuciones del procesamiento (solo detalle). |
| `Control` | Metadatos de ejecución, estructura oficial e identificadores (solo detalle). |
| `Origen_Adicional` | Columnas extra del origen (solo detalle). |

El modo `legacy` conserva la hoja `Tickets` para archivos históricos.

La hoja `Tickets` no contiene estados técnicos ni identificadores internos.

### 2.7 Estados internos

| Estado | Significado | Acción |
|--------|-------------|--------|
| `VÁLIDO` | Registro completo y consistente. | Ninguna. |
| `OBSERVADO` | Tiene valores atípicos o incompletos no críticos. | Revisar `Observaciones`. |
| `PENDIENTE` | El solicitante no tiene área asignada. | Asignar el área. |
| `DUPLICADO` | Coincide con otro registro. | Revisar si es un duplicado real. |
| `ERROR` | Falta un dato imprescindible o es inválido. | Corregir antes de consolidar. |

Los registros observados y pendientes **no se descartan**.

### 2.8 Persistencia y trazabilidad

| Artefacto | Ubicación actual | Uso |
|-----------|------------------|-----|
| Excel procesado | `output/<stem>_procesado.xlsx` | Resultado del procesamiento. |
| Manifiesto | `output/<stem>_procesado.manifest.json` | IDs técnicos, huellas, observaciones y resumen. |
| Base de áreas | `data/maestro_areas.xlsx` y `.csv` | Correspondencias `USUARIO → ÁREA`. |
| Maestro histórico | `data/maestro_tickets.xlsx` y `.csv` | Consolidado histórico de tickets. |
| Historial de cambios | `data/maestro_tickets_historial.csv` | Cambios campo a campo. |
| Auditoría | `data/auditoria_ejecuciones.csv` | Eventos del sistema. |
| Estado de la interfaz | `data/estado_ui.json` | Último procesamiento, rutas y KPIs. |
| Revisiones | `output/revision/revision_<id>.json` | Sesiones de validación reanudables. |
| Copia de revisión | `output/revision/<stem>_procesado_revision.xlsx` | Copia estable que se abre para corregir. |
| Caché de empleados | `data/cache_empleados.xlsx` y `.csv` | Fotografía corporativa para la resolución. |
| Sincronización | `data/sincronizacion.json` | Resultado, errores y alertas de cambios. |
| Respaldos | `data/backups/` | Copias del maestro histórico. |
| Reportes | `reportes/` | Excel y Markdown. |
| Log | `logs/sistema.log` | Trazabilidad técnica rotativa. |

### 2.9 Comportamiento actual de salida y apertura

- `procesar_archivo(..., modo="normal")` escribe `output/<stem>_procesado.xlsx` con una sola hoja `Consulta`.
- El modo `detalle` escribe el canónico anterior y además `output/<stem>_procesado_detalle.xlsx` con cinco hojas.
- El archivo canónico se reemplaza de forma atómica con respaldo automático previo en `output/respaldos/` (últimas 10 copias por archivo lógico).
- El modo `legacy` conserva la hoja `Tickets` y el comportamiento histórico de no sobrescribir.
- **Abrir resultado** crea o reemplaza `output/revision/<stem>_procesado_revision.xlsx` y abre esa copia, nunca el canónico.
- El manifiesto se escribe junto al archivo de salida y es obligatorio para reimportar y consolidar.
- La lectura de reimportación, consolidación y reportes busca `Consulta`, luego `Tickets`, luego la primera hoja.
- Si quedan registros sin área o conflictos, la generación completa se bloquea salvo confirmación de generación parcial.

### 2.10 Verificación actual

```powershell
python -m unittest discover -s tests
# Ran 100 tests ... OK (skipped=4)

python validar_septiembre.py
python auditar_sistema.py
```

---

## 3. Puntos de atención y cómo quedaron atendidos

| Punto detectado | Solución implementada |
|-----------------|------------------------|
| Salida con múltiples hojas | Modo `normal` con una sola hoja `Consulta`; el detalle es un artefacto separado. |
| Versiones con marca de tiempo | El canónico se sobrescribe de forma atómica con respaldo previo; ya no se dispersa la fuente. |
| Abrir resultado sobre el archivo canónico | Se abre una copia estable en `output/revision/` con su manifiesto. |
| Sin separación normal/detalle | Dos botones y dos modos explícitos (`normal`, `detalle`) más `legacy` para compatibilidad. |
| Respaldo de salidas | `output/respaldos/` con retención de las últimas 10 copias por archivo lógico. |
| Pendientes sin control de generación | Bloqueo de la generación completa con `permitir_pendientes=False` y confirmación explícita para la parcial. |
| Asignación de áreas sin apoyo corporativo | Integración SQL Server de solo lectura, caché local y alertas de cambios. |
| Falta de reanudación | Revisiones persistentes y botón **Reanudar revisión**. |

---

## 4. Plan implementado (2026-11-10)

> Sección conservada como registro del diseño. **La implementación está realizada y verificada con 100 pruebas automatizadas.**

### 4.1 Objetivo

Separar la generación de Excel en dos modos claros, dejar un archivo normal canónico de una sola hoja para reimportación, consolidación y reportes, y abrir una copia de revisión en lugar del archivo canónico.

### 4.2 Decisiones acordadas

| Tema | Decisión |
|------|----------|
| Interfaz | Mantener Tkinter/ttk puro; no agregar dependencias de UI. |
| Acciones de procesamiento | Dos botones: **Generar Excel normal** y **Generar Excel a detalle**. |
| Archivo normal | `output/<stem>_procesado.xlsx` con una sola hoja: `Consulta`. |
| Archivo a detalle | `output/<stem>_procesado_detalle.xlsx` con cinco hojas estándar. |
| Archivo canónico | El Excel normal es la fuente canónica para reimportar, consolidar y reportar. |
| Reprocesamiento | Sobrescribir el mismo archivo canónico, con respaldo automático previo. |
| Respaldo | `output/respaldos/`, conservando las últimas 10 copias por archivo. |
| Abrir resultado | Crear una copia fija de revisión y abrir esa copia, no el archivo canónico. |
| Copia de revisión | `output/revision/<stem>_procesado_revision.xlsx` y su manifiesto. |
| Hoja de detalle | Cambiar `HOJA_DETALLE` de `"Tickets"` a `"Consulta"`, con compatibilidad temporal para `"Tickets"`. |
| Hojas manuales | Ya no se preservan en el archivo normal; el normal es estrictamente de una hoja. |
| Manifiesto | El Excel normal sigue generando `.manifest.json` para reimportación y consolidación. |
| Consola | Mantener compatibilidad hacia atrás en `main.py` y sus valores por defecto. |
| Detalle obsoleto | El procesamiento normal no elimina un detalle existente; su vigencia se refleja en la hoja `Control`. |

### 4.3 Acción: Generar Excel normal

**Salida:** `output/<stem>_procesado.xlsx`

- Una única hoja: `Consulta`.
- Exactamente las 11 columnas oficiales, sin columnas técnicas ni estados internos.
- Sin hojas `Observaciones`, `Resumen`, `Control` ni `Origen_Adicional`.
- Es el archivo canónico para:
  - reimportación de correcciones;
  - consolidación en el maestro histórico;
  - generación de reportes.
- Genera también `output/<stem>_procesado.manifest.json`.
- Si el archivo ya existe:
  1. se copia a `output/respaldos/` con marca de tiempo;
  2. se conservan las últimas 10 copias por archivo;
  3. se sobrescribe el archivo canónico de forma atómica.
- El archivo original de `input/` nunca se modifica.

### 4.4 Acción: Generar Excel a detalle

**Salida:** `output/<stem>_procesado_detalle.xlsx`

Incluye cinco hojas:

| Hoja | Contenido |
|------|-----------|
| `Consulta` | Detalle con las 11 columnas oficiales. |
| `Observaciones` | Fila, severidad, campo, motivo, valor y acción requerida. |
| `Resumen` | Totales, indicadores y distribuciones. |
| `Control` | Metadatos de ejecución, estructura y vigencia. |
| `Origen_Adicional` | Columnas extra del origen; puede quedar vacía si no existen. |

Reglas:

- Procesa el mismo archivo de entrada seleccionado.
- Además de generar el detalle, **actualiza el archivo normal canónico** para que la reimportación y la consolidación sigan usando una fuente única.
- El detalle es un artefacto de análisis y revisión, no la fuente canónica.
- Si el detalle ya existe, se reemplaza con respaldo automático en `output/respaldos/`.
- La hoja `Control` debe permitir identificar la ejecución, el archivo de origen y la fecha de generación para detectar si el detalle está desactualizado.
- Un procesamiento normal no elimina el detalle existente.

### 4.5 Acción: Abrir resultado

Comportamiento propuesto:

1. Tomar el archivo canónico `output/<stem>_procesado.xlsx`.
2. Crear una copia fija de revisión:
   - `output/revision/<stem>_procesado_revision.xlsx`
   - `output/revision/<stem>_procesado_revision.manifest.json`
3. Abrir la copia de revisión con la aplicación asociada del sistema.
4. No abrir ni modificar el archivo canónico.

Objetivos:

- Evitar que el usuario edite accidentalmente la fuente canónica.
- Permitir corregir y reimportar la copia de revisión.
- Mantener un manifiesto asociado a la copia para que la reimportación encuentre los identificadores técnicos.
- La copia de revisión se reemplaza en cada apertura; no se acumulan revisiones ilimitadas.

### 4.6 Compatibilidad de lectura

Para no romper archivos existentes:

- `HOJA_DETALLE` pasará a ser `"Consulta"`.
- La lectura de reimportación y consolidación intentará primero `"Consulta"`.
- Si no existe, usará `"Tickets"` como compatibilidad temporal.
- Si tampoco existe, usará la primera hoja del libro, como ya ocurre hoy.
- El manifiesto sigue siendo obligatorio para reimportar y consolidar.
- El manifiesto del archivo normal debe seguir conteniendo IDs técnicos, huellas, observaciones y resumen.

### 4.7 Respaldo y retención

- Carpeta: `output/respaldos/`.
- Nombre propuesto: `<stem>_procesado_YYYYMMDD_HHMMSS.xlsx` y equivalentes para el detalle.
- Retención: últimas **10** copias por archivo lógico.
- La limpieza debe ser determinista y no eliminar respaldos de otros archivos.
- El respaldo se crea antes de reemplazar el archivo canónico o el detalle.
- Si el respaldo falla, no se debe sobrescribir el archivo original.

### 4.8 Archivos impactados por la implementación futura

| Archivo | Cambio previsto |
|---------|-----------------|
| `config/configuracion.py` | `HOJA_DETALLE = "Consulta"`, rutas de revisión y respaldos, retención. |
| `core/procesado.py` | Escritura de modo normal (una hoja) y modo detalle (cinco hojas); respaldo previo. |
| `core/procesador.py` | Parámetro o función para elegir modo normal/detalle; metadatos de vigencia. |
| `core/reimportacion.py` | Búsqueda de `Consulta` con fallback a `Tickets` y primera hoja. |
| `core/consolidacion_tickets.py` | Lectura compatible con `Consulta`. |
| `core/estado.py` | Registrar ruta canónica y ruta de detalle por separado si aplica. |
| `interfaz.py` | Dos botones de generación; `Abrir resultado` abre la copia de revisión. |
| `main.py` | Mantener compatibilidad; opción explícita de modo si se requiere. |
| `auditar_sistema.py` | Validar el archivo normal de una sola hoja y el detalle de cinco hojas. |
| `tests/test_sistema.py` | Nuevas pruebas para ambos modos, respaldos, retención y copia de revisión. |
| `MANUAL_USUARIO.md` | Actualizar flujo, botones, hojas y ubicación de respaldos/revisiones. |

### 4.9 Pruebas que deberá cubrir la implementación

1. El Excel normal contiene exactamente una hoja llamada `Consulta`.
2. El Excel normal contiene exactamente las 11 columnas oficiales.
3. El Excel normal no contiene hojas auxiliares.
4. El Excel a detalle contiene las cinco hojas esperadas.
5. El detalle actualiza también el archivo normal canónico.
6. Reprocesar sobrescribe el canónico y crea un respaldo.
7. La retención conserva solo las últimas 10 copias por archivo.
8. `Abrir resultado` crea y abre la copia de revisión, no el canónico.
9. La copia de revisión tiene su manifiesto asociado.
10. La reimportación funciona con `Consulta`.
11. La reimportación sigue funcionando con archivos legacy que usan `Tickets`.
12. La consolidación funciona con `Consulta` y con `Tickets`.
13. Un fallo de respaldo no destruye el archivo existente.
14. La consola mantiene su comportamiento compatible.
15. Las 59 pruebas existentes siguen aprobando.

### 4.10 Fuera de alcance del plan

- Cambiar el esquema oficial de 11 columnas.
- Agregar dependencias nuevas de interfaz.
- Modificar la lógica de negocio de asignación de áreas.
- Cambiar el formato del manifiesto más allá de lo necesario para la copia de revisión.
- Eliminar la compatibilidad con archivos históricos.
- Implementar el plan sin aprobación explícita.

---

## 5. Comparación: estado anterior vs. implementación actual

| Aspecto | Estado anterior | Implementación actual |
|---------|-----------------|-----------------------|
| Botón de procesamiento | `Procesar archivo` | `Procesar y validar` + `Generar Excel normal` + `Generar Excel a detalle` |
| Hoja de detalle | `Tickets` | `Consulta` (con fallback a `Tickets`) |
| Excel normal | 5 hojas (según datos) | Solo `Consulta` |
| Excel a detalle | No existía como salida separada | 5 hojas estándar |
| Archivo canónico | Versión con marca de tiempo si ya existía | `<stem>_procesado.xlsx` sobrescrito con respaldo |
| Respaldo de salidas | No específico | `output/respaldos/`, últimas 10 copias |
| Abrir resultado | Abría el archivo procesado | Abre copia en `output/revision/` |
| Manifiesto | Sí | Sí, también para la copia y el detalle |
| Reimportación | Lee `Tickets` o primera hoja | Lee `Consulta`, luego `Tickets`, luego primera hoja |
| Consolidación | Lee `Tickets` o primera hoja | Lee `Consulta`, luego `Tickets`, luego primera hoja |
| Reportes | Leen el procesado | Siguen usando el normal canónico |
| Consola | Comportamiento actual | `--modo normal|detalle|legacy` y `--permitir-pendientes` |
| Áreas | Solo base local | Base local + SQL Server solo lectura + caché + alertas |
| Validación | Pestaña de usuarios y áreas | Ventana dedicada con KPIs, filtros, homónimos y reanudación |

---

## 6. Riesgos y consideraciones (con mitigación aplicada)

1. **Archivos legacy:** el fallback a `Tickets` se mantiene y está cubierto por la suite.
2. **Manifiesto y copia de revisión:** la copia se genera junto con su `.manifest.json` de forma atómica.
3. **Bloqueo de archivos en Excel:** la escritura informa el bloqueo, conserva el respaldo y no destruye el canónico.
4. **Retención de respaldos:** la rotación agrupa por archivo lógico y conserva 10 copias.
5. **Detalle desactualizado:** la hoja `Control`, el manifiesto y la fecha de ejecución permiten detectarlo.
6. **Doble escritura:** el detalle se escribe después del canónico; si falla, se informa que el canónico sí se generó.
7. **Pruebas:** `tests/test_sql_revision_generacion.py` cubre SQL, resolución, revisiones, modos, respaldos, retención, alertas y consola.
8. **Documentación:** `README.md`, `MANUAL_USUARIO.md` y `DOCUMENTACION_TECNICA.md` reflejan el flujo implementado.
9. **Esquema EXACTUS:** las consultas son configurables, pero el esquema real debe validarse en el entorno corporativo antes de producción.
10. **Generación parcial:** el manifiesto marca `generacion_parcial` y la interfaz/consola lo advierten antes de continuar.

---

## 7. Próximos pasos

1. Validar el esquema y las credenciales reales de EXACTUS en el entorno corporativo.
2. Verificar visualmente la interfaz (dos botones, ventana de validación y reanudación) en una sesión de escritorio.
3. Ejecutar `validar_septiembre.py` y `auditar_sistema.py` con los datos reales del mes.
4. Revisar la política de retención de respaldos con el área de operaciones.
5. Programar la sincronización periódica de la caché de empleados.

> **Estado:** implementado y verificado con 100 pruebas automatizadas (4 omitidas).
> Las verificaciones que dependen de EXACTUS y de una sesión gráfica real quedan
> pendientes del entorno del usuario.
