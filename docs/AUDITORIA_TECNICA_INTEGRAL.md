# Auditoría técnica integral — ETL-TI-SERVIAM

**Sistema:** Sistema Integral de Tickets TI — INKACROPS (`ti-etl-activities`)
**Fecha del informe:** 14 de noviembre de 2026
**Modalidad:** auditoría de **solo lectura** (inspección estática del código; no se ejecutaron flujos que escriban datos reales, no se modificó ningún archivo del sistema, no se instalaron dependencias ni se ejecutaron migraciones)
**Alcance:** arquitectura, flujos, rendimiento, congelamientos de interfaz, SQL, Excel/pandas, asignación de usuarios y áreas, Word, caché, concurrencia, mantenibilidad y preservación de reglas de negocio
**Único artefacto escrito:** este archivo (`docs/AUDITORIA_TECNICA_INTEGRAL.md`)

---

## Cómo leer este informe

Cada hallazgo tiene un identificador estable (`UI-01`, `ETL-03`, `SQL-02`, …) que se reutiliza en todas las secciones para no repetir evidencia.

| Marca | Significado |
|---|---|
| **CONFIRMADO** | Verificado directamente en el código fuente (archivo, función y líneas citadas). No depende de ejecución. |
| **HIPÓTESIS** | Razonamiento técnico plausible que **requiere medición** antes de afirmarse como hecho. Se listan en el Anexo B. |
| **P0** | Crítico: congelamiento perceptible del flujo normal, o riesgo de integridad del maestro / pérdida de trazabilidad. |
| **P1** | Alto: lentitud significativa o riesgo de correctitud en flujos frecuentes. |
| **P2** | Medio: costo notable en flujos específicos o deuda de mantenibilidad con impacto futuro. |
| **P3** | Bajo: higiene, optimización menor, documentación. |

> **No se inventan tiempos ni porcentajes de mejora.** Cuando el informe dice "lento" describe una **causa estructural** (número de lecturas/escrituras completas, complejidad algorítmica, trabajo en el hilo de UI). La magnitud real debe medirse con el plan de la Fase 0 y el Anexo B.

### Contexto documental previo

El repositorio ya contiene auditorías funcionales (`docs/AUDITORIA.md`, `AUDITORIA_SISTEMA_ACTUAL.md`, `docs/IMPLEMENTACION_FASE_0..7.md`). Esas auditorías concluyeron que **no hay fallos funcionales críticos** y reportaron ejecuciones reales y 59 pruebas en verde. Este informe **no contradice** esa conclusión: no se encontró evidencia de corrupción de datos ni de violación de reglas de negocio. Lo que se documenta aquí es **deuda de rendimiento, concurrencia y mantenibilidad** que las auditorías funcionales no cubrieron, más un conjunto de riesgos que hoy no se manifiestan porque los volúmenes son pequeños.

---

## 1. Resumen ejecutivo

### 1.1 Veredicto

El sistema es **funcionalmente maduro y cuidadoso con la integridad de los datos**: escrituras atómicas, guardado transaccional con rollback, detección de cambios externos por huella, SQL de solo lectura parametrizado, generación Word no destructiva con verificación de plantilla y una batería de 298 funciones de prueba en 16 módulos. Esa parte está por encima de la media de herramientas internas.

El problema principal es de **arquitectura de ejecución**, no de reglas de negocio:

1. **La interfaz ejecuta trabajo pesado en el hilo de Tkinter.** Previsualización, arranque, refrescos por evento, filtros de validación y búsqueda en el maestro leen y reconstruyen estructuras completas de forma síncrona.
2. **Cada asignación individual se persiste como una operación completa de maestro**: varias lecturas completas del `.xlsx`, dos escrituras completas, cuatro copias de respaldo/rollback, verificación posterior y varias reescrituras de JSON. Asignar en la ventana de validación repite ese costo por usuario y añade cuatro refrescos de interfaz.
3. **El costo crece de forma no lineal** en dos puntos: sugerencias de usuarios pendientes (`O(pendientes × maestro)`) y detección de solicitantes (`O(k²)` acotado). Con volúmenes pequeños es invisible; con miles de filas se convierte en la experiencia dominante.
4. **La concurrencia está resuelta a medias**: existe bloqueo cooperativo y huellas, pero la auditoría CSV y los JSON de estado/procedencia se reescriben sin candado, y el guardado del maestro tiene una ventana TOCTOU entre "comparar" y "escribir".

### 1.2 Top 10 por impacto

| # | ID | Hallazgo | Severidad | Efecto percibido |
|---|---|---|---|---|
| 1 | UI-04 / ASG-02 | Guardado del maestro **por cada asignación**, más 4 refrescos completos | P0 | Asignar varios usuarios en validación parece "colgado" |
| 2 | UI-01 | Previsualización síncrona en el hilo de UI | P0 | La ventana no responde al elegir un Excel grande |
| 3 | UI-02 | Arranque carga maestro, solicitantes, pendientes y auditoría antes del primer pintado | P0 | Ventana en blanco / "no responde" al abrir |
| 4 | UI-03 | Cada evento (`MAESTRO_ACTUALIZADO`, `AREA_CREADA`) dispara refrescos pesados síncronos | P0 | Congelamiento después de cada cambio |
| 5 | ETL-05 | `Maestro.guardar` es multifase (temporales, validación, backups, rollback, verificación) | P1 | Latencia alta por guardado, multiplicada por asignación |
| 6 | ETL-03 | Manifiesto embebe **todas** las filas y se duplica para el detalle | P1 | JSON grande, escritura y relectura dobles |
| 7 | ASG-01 | Sugerencias de pendientes recorren todo el maestro con similitud por fila | P1 | Pestaña de pendientes lenta y refrescos repetidos |
| 8 | ETL-06 | `MaestroTickets.cargar` lee xlsx + CONTROL + csv y normaliza en Python | P1 | Cada refresco de solicitantes paga el parseo triple |
| 9 | SQL-01 | `LIKE` sobre expresiones calculadas (REPLACE/UPPER) | P1 | Búsqueda SQL no usa índices; costo en servidor |
| 10 | CON-01 / CON-02 | Auditoría CSV y JSON de estado/procedencia sin candado | P1 | Riesgo de perder registros con dos escritores |

### 1.3 Lo que está bien y debe preservarse

- **SQL de solo lectura real:** `validar_solo_lectura` (`database/connection.py:195-210`) exige `SELECT`/`WITH` y bloquea `INSERT/UPDATE/DELETE/MERGE/DROP/ALTER/TRUNCATE/CREATE/EXEC/GRANT/…`; los valores viajan como `?` y los identificadores se validan contra lista blanca (`database/queries.py:19,57-76`).
- **Escrituras atómicas con reemplazo y verificación** (`core/archivos.py:449-522`), con retroalimentación clara cuando Excel tiene el archivo abierto.
- **Guardado transaccional del maestro con rollback** (`core/maestro.py:191-295`) y detección de cambios externos por SHA-256 (`core/integridad_excel.py:102-183,219-282`).
- **Reglas de negocio protegidas:** el área del maestro prevalece; la asignación manual prevalece sobre SQL/legacy (`core/procedencia.py:147-151`); los pendientes se conservan; los duplicados se marcan y no se fusionan solos; los genéricos se protegen (`core/solicitantes.py:50-53,201-203`).
- **Word no destructivo:** la plantilla nunca se sobrescribe, la salida existente se rechaza, se reserva el nombre con `O_EXCL` y se verifica el hash de la plantilla antes y después (`core/generador_docx.py:62-111`, `core/actualizador_graficos_docx.py:217-302`).
- **Patrón correcto de hilos + cola** para procesamiento, generación, reimportación, consolidación, reportes y Anexo 5 (`interfaz.py:3891-3950`), y para consultas SQL de validación (`ventana_validacion.py:1575-1593`).
- **Secretos:** la contraseña SQL se guarda cifrada con DPAPI en `contrasena_cifrada` (`config/sqlserver.py:253-256`; `core/seguridad.py`). **Este informe no reproduce credenciales.**

### 1.4 Recomendación de secuencia

No hacer una reescritura. El orden de mayor beneficio/costo es:

1. **Fase 0 (medir):** instrumentar los puntos calientes y obtener línea base. Sin esto no se puede demostrar mejora ni priorizar con datos.
2. **Fase 1 (interfaz):** mover a hilos lo que bloquea, agrupar refrescos por evento y guardar asignaciones en lote.
3. **Fase 2 (I/O del maestro):** una escritura por lote, caché por huella, evitar relecturas de verificación en salidas derivadas.
4. **Fase 3 (algoritmos):** vectorizar el ETL, indexar similitudes, reducir el manifiesto.
5. **Fase 4 (concurrencia):** un solo escritor, candados reales, diarios append-only o SQLite.
6. **Fase 5 (SQL):** optimización con el DBA mediante columna normalizada persistida e índices.
7. **Fase 6 (mantenibilidad):** dividir módulos, tipado, CI y pruebas de rendimiento.

**Riesgo de no actuar:** el sistema seguirá funcionando con los volúmenes actuales, pero el crecimiento del maestro y del histórico convierte cada clic en una espera; los usuarios empezarán a evitar la validación manual y a usar el Excel directamente, que es precisamente el escenario que el sistema busca controlar.

---

## 2. Arquitectura

### 2.1 Stack y punto de entrada

| Elemento | Valor observado |
|---|---|
| Lenguaje | Python con sintaxis moderna (`from __future__ import annotations`, uniones `X` / `Y`, `dataclass`) |
| Interfaz | `tkinter` / `ttk` (no Qt) |
| Datos | pandas 2.x, openpyxl |
| SQL | pyodbc (opcional), `sqlglot` como dependencia declarada |
| Word | python-docx, lxml |
| Dependencias | `requirements.txt`: pandas>=2.0, openpyxl>=3.1, pyodbc>=5.0, sqlglot>=26.0, python-docx>=1.2.0, lxml>=6.1.3 |
| Entrada | `main.py` → `AplicacionTk` (`interfaz.py:890`) |
| Tamaño | 82 archivos `.py`, ~31.571 líneas (incluye pruebas) |

### 2.2 Capas

```text
main.py
  └─ interfaz.py (AplicacionTk, Tkinter)          ← presentación y orquestación de UI
       ├─ panel_principal.py (dashboard)
       ├─ ventana_validacion.py (validación manual, buscadores, SQL manual, coincidencias)
       ├─ ui_tema.py (estilos)
       ├─ core/…                                   ← dominio y reglas de negocio
       │    ├─ etl.py        extracción / hoja / encabezado / normalización
       │    ├─ procesador.py orquestación del procesamiento y manifiesto
       │    ├─ archivos.py   lectura/escritura atómica, comparación, respaldos
       │    ├─ maestro.py    USUARIO → AREA_USUARIO (xlsx + csv)
       │    ├─ maestro_tickets.py maestro histórico de 11 columnas
       │    ├─ asignaciones.py servicio de asignación y sugerencias
       │    ├─ solicitantes.py auditoría de solicitantes (similitud)
       │    ├─ integridad_excel.py huellas, bloqueo de Excel, guardado seguro
       │    ├─ bloqueo.py    BloqueoSistema (lock de proceso)
       │    ├─ operaciones.py diario idempotente
       │    ├─ procedencia.py trazabilidad por usuario
       │    ├─ auditoria.py  CSV de ejecuciones
       │    ├─ revision.py   revisiones guardadas
       │    ├─ reimportacion.py correcciones
       │    ├─ reportes.py   métricas
       │    └─ *_docx.py     generación/actualización Word
       └─ database/…                               ← acceso a datos externos
            ├─ connection.py  conexión, solo lectura, consulta manual
            ├─ queries.py     SQL parametrizado y validado
            ├─ repository.py  búsqueda consolidada multiempresa
            ├─ coincidencias.py consolidación de coincidencias
            ├─ resolucion.py  resolución de áreas
            └─ synchronizer.py caché local de empleados
```

### 2.3 Artefactos persistentes

| Artefacto | Ruta por defecto | Formato | Consistencia |
|---|---|---|---|
| Maestro de áreas | `data/maestro_areas.xlsx` + `.csv` | Excel + espejo CSV | Se comparan en carga y se verifican tras guardar |
| Maestro histórico | `data/maestro_tickets.xlsx` + `.csv` (+ hoja `CONTROL`) | Excel + CSV | Triple lectura y comparación canónica |
| Manifiesto | `<salida>.manifest.json` | JSON | Embebe filas y resumen; se duplica para detalle |
| Revisiones | `output/revision/*.json` | JSON | Se reescribe completo en cada acción |
| Auditoría | `data/auditoria_ejecuciones.csv` | CSV | Read-modify-write por evento |
| Diario de operaciones | `data/operaciones.json` | JSON | Read-modify-write por operación |
| Procedencia | JSON de procedencia | JSON | Read-modify-write por asignación |
| Estado UI | `data/estado_ui.json` | JSON | Read-modify-write |
| Huellas | `data/huellas_excel.json` | JSON | SHA-256 + tamaño + mtime |
| Caché de empleados | CSV/JSON de sincronización | CSV/JSON | Generada por consulta SQL |
| Bloqueo | `data/.sistema.lock` | Texto (PID) | `O_CREAT` + `O_EXCL`, obsoleto a 30 min |

### 2.4 Modelo de ejecución

- **Hilo principal:** Tkinter, todos los widgets y callbacks `after`.
- **Hilos trabajadores:** `threading.Thread(daemon=True)` creados por `_ejecutar_hilo` (`interfaz.py:3891-3908`), con resultados entregados por `queue.Queue` y consumidos cada 120 ms (`_procesar_cola`, 3910-3950).
- **Bus de eventos síncrono** (`core/eventos.py:112-144`): publica en el hilo que llama; la interfaz se suscribe y **marshalea** con `self.after(0, …)` (`interfaz.py:955-961`), lo cual es correcto para Tkinter, pero el manejador resultante ejecuta trabajo pesado en el hilo de UI (ver UI-03).
- **Bloqueo cooperativo de escritura:** `core/bloqueo.py:92-202`.

### 2.5 Riesgos arquitectónicos de fondo

| Riesgo | Evidencia | Consecuencia |
|---|---|---|
| Módulos "god" | `interfaz.py` 3.693 líneas; `ventana_validacion.py` 3.049; `repository.py` 1.112 | Difícil razonar, probar y cambiar sin efectos colaterales |
| Dos modelos de maestro | `core/maestro.py` y `core/maestro_tickets.py` con lógicas de carga/guardado/validación paralelas | Duplicación de reglas y de costos |
| Estado en archivos JSON | operaciones, procedencia, estado, revisiones, huellas | Read-modify-write, concurrencia frágil, crecimiento sin retención |
| UI acoplada a I/O | refrescos que llaman `MaestroTickets.cargar` y `detectar_solicitantes` | Congelamientos (sección 6) |
| Dependencia de rutas fijas | `config/configuracion.py` define rutas derivadas de `BASE_DIR` | Poco testeable en paralelo; riesgo en instalaciones movidas |

---

## 3. Mapa de módulos y dependencias

### 3.1 Inventario por paquete

| Paquete | Módulos relevantes | Responsabilidad | Depende de |
|---|---|---|---|
| raíz | `main.py`, `interfaz.py`, `panel_principal.py`, `ventana_validacion.py`, `ui_tema.py`, `auditar_sistema.py` | Presentación, composición, auditoría de integridad | `core`, `database`, `config` |
| `config/` | `configuracion.py`, `sqlserver.py`, `logging_config.py`, `seguridad.py` | Rutas, parámetros, conexión, DPAPI, logging | biblioteca estándar |
| `core/` | 41 módulos (ver 3.2) | Dominio ETL, maestros, asignación, revisión, reportes, Word | `config`, `core.*`, `database.*` |
| `database/` | 7 módulos | Conexión, SQL, repositorio multiempresa, coincidencias, resolución, sincronización | `config`, `core.busqueda_solicitantes` |
| `tests/` | 16 módulos, 298 funciones `test_` | Pruebas unitarias/integración | todos |

### 3.2 Módulos `core/` y su rol

| Módulo | Rol | Acoplamiento |
|---|---|---|
| `etl.py` | Detección de hoja/encabezado, extracción, normalización de fecha/horas | Alto con `archivos` |
| `procesador.py` | Orquesta procesamiento, estados, manifiesto | Alto con `etl`, `maestro`, `revision`, `operaciones`, `auditoria` |
| `archivos.py` | I/O atómica, comparación de DataFrames, respaldos | Base de casi todo |
| `maestro.py` | Maestro USUARIO→ÁREA con xlsx+csv | `archivos`, `integridad_excel` |
| `maestro_tickets.py` | Maestro histórico 11 columnas + CONTROL | `archivos`, `etl` |
| `asignaciones.py` | Asignación, sugerencias, pendientes | `maestro`, `integridad_excel`, `operaciones`, `similitud` |
| `solicitantes.py` | Detección de variantes y equivalencias | `similitud`, JSON de equivalencias |
| `similitud.py` | `SequenceMatcher` y clasificación | Biblioteca estándar |
| `integridad_excel.py` | Huellas, bloqueo de archivo, guardado seguro | `operaciones`, `archivos` |
| `bloqueo.py` | Lock de proceso | `config` |
| `operaciones.py` | Diario idempotente | `archivos` |
| `procedencia.py` | Trazabilidad por usuario | `archivos` |
| `auditoria.py` | CSV de ejecuciones | `archivos` |
| `revision.py` | Revisiones y decisiones | `archivos` |
| `reimportacion.py` | Correcciones y emparejamiento | `archivos`, `similitud` |
| `consolidacion.py` / `consolidacion_tickets.py` | Consolidación inicial e histórica | `archivos`, `normalizador` |
| `reportes.py` | Métricas y Markdown | pandas |
| `catalogo_areas.py` | Catálogo de áreas | JSON |
| `normalizador.py` | Normalización de usuario/área | Biblioteca estándar |
| `generador_docx.py` y `actualizador_*_docx.py` / `lector_informe_docx.py` / `estadisticas_informe_docx.py` / `proyector_anexo5_docx.py` / `modelo_informe_docx.py` | Pipeline Word | python-docx, lxml, pandas |
| `eventos.py` | Bus de eventos | Biblioteca estándar |
| `estado.py`, `diagnostico.py` | Estado UI y diagnóstico | JSON, auditoría |
| `busqueda_solicitantes.py`, `seleccion_empleados.py`, `asignacion_asistida.py`, `acciones_revision.py`, `retroalimentacion.py`, `consulta_sql_manual.py`, `seguimiento_consultas.py`, `esquema.py` | Servicios de apoyo a la UI | variables |

### 3.3 Archivos más grandes (medidos)

| Archivo | Líneas | Comentario |
|---|---:|---|
| `interfaz.py` | 3.693 | Concentra 7 pestañas, hilos, eventos y persistencia |
| `ventana_validacion.py` | 3.049 | 5 ventanas secundarias en un solo módulo |
| `database/repository.py` | 1.112 | Ruta consolidada + ruta legacy |
| `panel_principal.py` | 977 | Dashboard con widgets propios |
| `core/procesado.py` | 767 | Modelo intermedio de filas procesadas |
| `core/procesador.py` | 733 | Núcleo ETL |
| `core/maestro_tickets.py` | 631 | Maestro histórico |
| `core/etl.py` | 560 | Extracción |
| `core/reimportacion.py` | 557 | Correcciones |
| `core/maestro.py` | 533 | Maestro de áreas |
| `database/coincidencias.py` | 520 | Consolidación de coincidencias |
| `database/queries.py` | ~466 | SQL |

### 3.4 Dirección de dependencias

La dirección general es correcta: `presentación → dominio → infraestructura`. No se identificaron ciclos evidentes de importación en tiempo de módulo; los pocos cruces se resuelven con importaciones locales dentro de funciones (por ejemplo `core/asignaciones.py:360`, `core/diagnostico.py:94,104`, `ventana_validacion.py:1293`). Ese patrón evita ciclos pero **oculta dependencias** y es una señal de acoplamiento alto.

Puntos de acoplamiento que más duelen:

1. `interfaz.py` conoce `core.asignaciones`, `core.maestro_tickets`, `core.solicitantes`, `core.revision`, `core.reimportacion`, `core.consolidacion*`, `core.reportes` y el pipeline DOCX. Un cambio en cualquiera puede afectar la UI.
2. `ventana_validacion.py` recibe `al_asignar` y `al_generar` como callbacks que **escriben el maestro en el hilo de UI** (UI-04).
3. `core.asignaciones` depende de `core.integridad_excel` y de `core.operaciones`, que a su vez escriben archivos; una asignación toca cinco subsistemas.
4. `database.repository` tiene dos caminos de búsqueda (`_buscar_consolidado` y `_consultar`) con garantías distintas.

---

## 4. Flujo de operaciones

### 4.1 Procesamiento de un archivo de tickets

```text
[UI] elegir archivo ─► previsualizar()  (SÍNCRONO en hilo UI · UI-01)
      │                 ├─ analizar hoja/encabezado (abre libro por hoja · ETL-08)
      │                 └─ leer vista de 11 columnas
      ▼
[UI] Procesar ─► _ejecutar_hilo(procesar_archivo)  (correcto)
      │
      ├─ _hash_archivo(entrada)                      (SHA-256 completo · ETL-09)
      ├─ extraer(): abre el libro otra vez, iterrows (· ETL-08)
      ├─ df.copy()                                   (· ETL-02)
      ├─ for fila in df.iterrows():                  (· ETL-01)
      │     ├─ normalizar usuario/área
      │     ├─ revisión guardada > maestro > área del archivo
      │     ├─ resolver SQL si está configurado
      │     └─ estado + observaciones + duplicados
      ├─ escribir Excel de salida (atómico + relectura · ETL-04)
      ├─ escribir manifiesto con TODAS las filas           (· ETL-03)
      ├─ si hay detalle: escribir un segundo manifiesto completo (· ETL-03)
      ├─ auditoría CSV (read-modify-write · ASG-04)
      └─ estado_ui.json (read-modify-write · CON-02)
      ▼
[cola] callback en hilo UI ─► refrescos de panel/pendientes/maestro
```

**Reglas preservadas en este flujo (confirmadas):**

- El archivo de entrada nunca se sobrescribe; la salida es un archivo nuevo.
- El orden de precedencia es revisión guardada → maestro → área del archivo.
- Los usuarios desconocidos quedan pendientes con su área preexistente, sin inventar área.
- Los duplicados se conservan y se marcan; no se eliminan.
- Los conflictos y ambigüedades no se aplican automáticamente.

### 4.2 Asignación de un área (flujo manual)

```text
[Validación] doble clic / botón
   └─ _asignar_area()
        ├─ _asignar_en_maestro()  ─► callback _asignar_desde_validacion() en INTERFAZ
        │     ├─ confirmar área (posible diálogo)
        │     ├─ with bloqueo("actualización de usuarios y áreas")
        │     ├─ asignar_una()  ─► asignar(guardar=True, proteger=True)
        │     │     ├─ archivo_bloqueado + RegistroHuellas.comparar()  (SHA-256)
        │     │     ├─ maestro.actualizar(df)  (memoria)
        │     │     └─ guardar_maestro_seguro()
        │     │           ├─ archivo_bloqueado + comparar() (SHA-256 otra vez · CON-03)
        │     │           ├─ maestro.guardar(): validar, copiar df, escribir temp xlsx,
        │     │           │    leer temp, escribir temp csv, leer temp, backups x2,
        │     │           │    copias rollback x2, reemplazar, releer ambos y comparar
        │     │           ├─ verificar_excel_legible() (otra lectura)
        │     │           ├─ registro.guardar() (otra lectura SHA-256)
        │     │           └─ diario.ejecutar() (escribe operaciones.json 2 veces)
        │     ├─ _registrar_en_diario() (otra reescritura de operaciones.json)
        │     ├─ _refrescar_usuarios / pendientes / panel / auditoría   (4 refrescos)
        │     └─ revision.asignar_manual + revision.guardar() (JSON completo)
        └─ _refrescar() (reconstruye toda la tabla de validación)
```

**Consecuencia estructural:** una sola asignación provoca, como mínimo, **4 lecturas completas del maestro** (dos SHA-256, una comprobación de legibilidad y un hash final), **2 lecturas de los archivos temporales** (xlsx y csv), **2 escrituras completas** (xlsx + csv), **4 copias de archivo** (2 respaldos visibles + 2 copias de rollback) y **hasta 4 reescrituras de JSON** (2-3 del diario de operaciones y 1 de procedencia), además del refresco de UI. En un lote de N usuarios, el costo se multiplica por N (ASG-02, UI-04). El conteo es estructural (derivado del código), no una medición de tiempo.

### 4.3 Validación manual

```text
abrir revisión ─► Revision.cargar (JSON completo, síncrono · UI-08)
   └─ VentanaValidacion.__init__ ─► _refrescar() (inserta todas las filas · UI-08)
        ├─ filtro: <KeyRelease> ─► _refrescar() por tecla + cargar_configuracion() (UI-06)
        ├─ consultar SQL: hilo + cola (correcto)
        ├─ revisar coincidencias: VentanaBuscarExcelMaestro / VentanaCoincidencias
        └─ generar: hilo + cola (correcto)
```

### 4.4 Reimportación, consolidación, reportes y Word

| Flujo | Ejecución | Riesgo principal |
|---|---|---|
| Reimportar correcciones (analizar) | Hilo + cola | `O(pendientes × originales)` en el emparejamiento (ETL-10) |
| Aplicar reimportación | Hilo + cola | Escritura del maestro histórico; compite con otras escrituras (CON-04) |
| Consolidación inicial | Hilo + cola | `iterrows` y normalización en Python (`consolidacion._preparar_fuente:70`) |
| Reportes | Hilo + cola | pandas; bajo riesgo |
| Anexo 5: previsualizar/generar | Hilo + cola | Word es sólido; ver sección 11 |

### 4.5 Máquina de estados del diario de operaciones

`core/operaciones.py:32-38` define `PENDIENTE`, `COMPLETADO`, `ERROR`, `REQUIERE_REVISION`. Solo los dos primeros son reintentables. La clave de idempotencia combina tipo, usuario, área, archivo y `extra` (`core/operaciones.py:56-74`). Es una buena idea; sus problemas son de **persistencia** (reescritura total, sección 14) y de **trazabilidad** (CON-06).

---

## 5. Diagnóstico de lentitud

> Todas las afirmaciones de esta sección son **CONFIRMADAS** en código. La magnitud temporal es **HIPÓTESIS** hasta la Fase 0.

### 5.1 Modelo de costo estructural

| Operación | Lecturas completas | Escrituras completas | Copias | Bucles Python sobre filas |
|---|---:|---:|---:|---|
| Previsualizar archivo | 1 por hoja analizada + 1 vista | 0 | 0 | 1 sobre vista (30 filas) |
| Procesar archivo | hash + extracción + salida (verificación) | salida + manifiesto (×2 si detalle) | backups de salida | 1 (`iterrows` del total) |
| Asignar 1 usuario | 4 del maestro (2 SHA-256, 1 legible, 1 hash final) + 2 de temporales | 2 (xlsx, csv) | 4 | 0 |
| Refrescar solicitantes | maestro tickets (hojas + principal + CONTROL + csv) | 0 | 0 | 1 + `O(k²)` acotado |
| Refrescar pendientes | manifiesto | 0 | 0 | `O(pendientes × maestro)` |
| Guardar revisión | 0 | 1 JSON completo | 0 | 0 |
| Evento de auditoría | 1 CSV completo | 1 CSV completo | 0 | 0 |

### 5.2 Puntos calientes ordenados por impacto

| Orden | ID | Punto caliente | Mecanismo | Por qué duele |
|---:|---|---|---|---|
| 1 | UI-04 / ASG-02 | Persistencia por asignación | Cada clic guarda el maestro completo y refresca 4 regiones | Costo lineal por usuario con constante muy alta |
| 2 | ETL-05 | `Maestro.cargar` + `Maestro.guardar` | Compara xlsx y csv al cargar; guarda en 4 fases con verificaciones | Multiplica el costo de cada asignación y de cada arranque |
| 3 | ETL-06 | `MaestroTickets.cargar` | xlsx + CONTROL + csv + normalización en bucles Python | Se paga en cada refresco de solicitantes y al abrir validación |
| 4 | ASG-01 | Sugerencias de pendientes | `iterrows` + `SequenceMatcher` por cada usuario pendiente | `O(pendientes × maestro)`; se ejecuta en el hilo de UI |
| 5 | ETL-01 | `procesador` | `iterrows` sobre todo el archivo con lógica por fila | Costo por fila alto; crece linealmente con tickets |
| 6 | UI-01 | Previsualización | Parseo completo del Excel en el hilo de UI | Congelamiento al elegir archivo |
| 7 | UI-02 | Arranque | 4 refrescos completos antes del primer pintado | Percepción de aplicación "colgada" |
| 8 | ETL-03 | Manifiesto | Embebe todas las filas y se duplica | JSON grande; escritura + relectura |
| 9 | SOL-01 | Solicitantes | Doble bucle sobre claves | `O(k²)` acotado, pero crece con la diversidad de nombres |
| 10 | ETL-10 | Reimportación | Emparejamiento por similitud entre pendientes y originales | `O(p × n)`; corre en hilo, pero bloquea la tarea |
| 11 | SQL-01 | Búsqueda SQL | `LIKE '%…%'` sobre expresiones calculadas | Escaneo completo por empresa |
| 12 | ASG-04 | Auditoría | CSV completo por evento | `O(N²)` de escritura en una sesión larga |

### 5.3 Análisis por dominio

#### 5.3.1 ETL y pandas

- **ETL-01 (P1) — `core/procesador.py:306-733`, bucle en `375`.** `df.iterrows()` devuelve una `Series` por fila y luego se hace `to_dict()`; el costo por fila es alto y se paga en todo el archivo. La lógica por fila incluye reglas de precedencia, normalización, resolución SQL, estados y observaciones. **No se puede vectorizar todo sin riesgo**, pero sí separar: (a) normalización vectorizada, (b) detección de excepciones, (c) bucle solo para las filas que requieren decisión.
- **ETL-02 (P2) — `core/procesador.py:341`.** `df = extraccion.df.copy()` duplica el DataFrame completo antes del bucle. Es defendible para no mutar la extracción, pero duplica memoria en el pico.
- **ETL-03 (P1) — `core/procesador.py:686-696` y `717-728`.** El manifiesto incluye `datos: fila_a_dict(fila)` por cada fila y, si hay detalle, se crea un segundo manifiesto que reutiliza la misma lista y la serializa otra vez. El archivo resultante crece con `filas × columnas` y se escribe con `indent=2`; `escribir_json_atomico` además lo relee para validarlo. Esto afecta disco, memoria y tiempo, y complica el análisis posterior.
- **ETL-04 (P2) — `core/archivos.py:449-487` y `490-522`.** Toda escritura atómica relee el archivo recién escrito para validarlo. Es una garantía valiosa para el maestro, pero costosa para salidas derivadas y JSON de bitácora.
- **ETL-08 (P2) — `core/etl.py:354-445` y `499-522`.** `_analizar_encabezado_hoja` abre el libro por hoja para puntuarla y `extraer` vuelve a abrir el archivo completo y recorre `detalle.iterrows()`. En archivos con varias hojas se paga el parseo varias veces.
- **ETL-09 (P2) — `core/procesador.py:97-102`.** El SHA-256 se calcula leyendo el archivo completo en bloques de 1 MB antes de procesar. En un `.xlsx` grande es una lectura adicional; en red, es costosa.

#### 5.3.2 Maestros

- **ETL-05 (P1) — `core/maestro.py:136-189` y `191-295`.** `cargar` lee el `.xlsx` y el `.csv` y los compara con normalización; `guardar` valida, copia, escribe temporales xlsx y csv, valida cada temporal releyéndolo, crea respaldos visibles, copia rollback, reemplaza ambos destinos y vuelve a leer ambos para comparar (`_verificar_destinos`, 341). Es un diseño **correcto** y **caro**.
- **ETL-06 (P1) — `core/maestro_tickets.py:185-243`, normalización en `83-112`.** `cargar` lista hojas, lee la hoja principal, lee `CONTROL`, lee el CSV y compara con `_canonico_df`; `_normalizar_df` recorre columnas en Python. Además `__init__` renormaliza (`150`) y `aplicar` vuelve a hacerlo (`412`, `525`).
- **ETL-07 (P2) — `core/maestro_tickets.py:400-527,532-628`.** `aplicar` usa `iterrows` (`438`) y renormaliza todo el maestro (`525`); `validar` recalcula `huella_fila` por fila (`555`, `591`, `596`); `_escribir_temporal` escribe celda por celda y `_leer_y_comparar` relee y renormaliza todo (`609-628`). El guardado (`630-711`) repite el patrón transaccional del maestro de áreas.

#### 5.3.3 Asignaciones y solicitantes

- **ASG-01 (P1) — `core/asignaciones.py:120-155,171-203`.** `sugerencias_usuario` recorre `maestro.como_dataframe()` con `iterrows` y llama `similitud.comparar` por fila; `usuarios_pendientes` la invoca por cada pendiente. Resultado: `O(pendientes × maestro)`. Se ejecuta en `_refrescar_pendientes` (`interfaz.py:2387`) y en `_seleccionar_pendiente` (`2428`).
- **ASG-02 (P1) — `core/asignaciones.py:292-451,453-477`.** `asignar` está diseñado para lote, pero la UI usa `asignar_una`, que guarda por usuario. El backend correcto (`asignar` con varias tuplas) existe y no se aprovecha.
- **ASG-03 (P1) — `core/operaciones.py:177-188` y `core/procedencia.py:111-195`.** Cada registro de operación o procedencia relee, modifica y reescribe el JSON completo; el historial de procedencia crece sin retención. En un lote de N asignaciones, el costo es `O(N²)` en bytes escritos.
- **ASG-04 (P1) — `core/auditoria.py:80-101,205-265`.** Cada evento relee el CSV completo, concatena, deduplica y reescribe (y la escritura se relee). Es `O(N²)` por sesión y además no tiene candado (CON-01).
- **SOL-01 (P2) — `core/solicitantes.py:190-245`.** El doble bucle **no** se ejecuta para todos los pares: solo para claves no genéricas, sin equivalencia canónica y con una sola variante (rama `else` en 214-228). Es una acotación importante que evita afirmar un `O(n²)` global. Aun así, con muchos nombres distintos, el subconjunto crece y cada comparación usa `SequenceMatcher`.

#### 5.3.4 Word

El pipeline Word es de los componentes mejor diseñados del sistema. No presenta un punto caliente relevante: se ejecuta en hilo, valida la plantilla, escribe en temporal, verifica el zip de salida y reserva la ruta final con `O_EXCL`. Los únicos costos son los propios de python-docx y lxml, proporcionales al tamaño del documento (sección 11).

### 5.4 Qué medir antes de optimizar (resumen; detalle en Anexo B)

1. Tiempo de previsualización, procesamiento y arranque con 1k, 10k y 50k filas.
2. Tiempo de una asignación individual y de un lote de 10/50/100.
3. Tiempo de `MaestroTickets.cargar` y de `detectar_solicitantes` con el maestro real.
4. Tamaño real del maestro, del manifiesto y de las revisiones.
5. Latencia SQL de la búsqueda consolidada y plan de ejecución del `LIKE`.

---

## 6. Diagnóstico de congelamientos

> **Hipótesis de trabajo:** Tkinter es monohilo; cualquier callback que tarde más de ~100-200 ms se percibe como congelamiento. Los puntos siguientes ejecutan trabajo de segundos en el hilo de UI cuando los archivos crecen. No se midió la duración real.

### 6.1 Inventario de bloqueos del hilo de UI

| ID | Disparador | Trabajo síncrono | Efecto |
|---|---|---|---|
| UI-01 | Elegir archivo (`_elegir_archivo:1689`) y `_procesar:1776` | `previsualizar()` completo (hojas + vista) | Ventana "no responde" |
| UI-02 | `AplicacionTk.__init__:893-950` | `_refrescar_panel`, `_refrescar_usuarios`, `_refrescar_pendientes`, `_refrescar_maestro`→`_refrescar_solicitantes`, `_refrescar_auditoria` | Arranque lento, ventana en blanco |
| UI-03 | Eventos `ASIGNACION_REALIZADA`, `AREA_CREADA`, `MAESTRO_ACTUALIZADO`, `CATALOGO_AREAS_ACTUALIZADO` (`_aplicar_evento:963-990`) | 1-4 refrescos completos, incluido `MaestroTickets.cargar` + `detectar_solicitantes` | Congelamiento tras cada cambio |
| UI-04 | Asignar en validación (`_asignar_desde_validacion:1925-1989`) | Guardado completo del maestro + 4 refrescos + guardado de revisión | Congelamiento por usuario |
| UI-05 | `_refrescar_pendientes:2387-2412` | Motor de sugerencias `O(pendientes × maestro)` | Congelamiento al refrescar pendientes |
| UI-06 | `<KeyRelease>` en filtro de validación (`ventana_validacion.py:390`) | `_refrescar` reconstruye toda la tabla (`574-604`) + `cargar_configuracion()` (`625`) | "Stutter" al escribir |
| UI-07 | `<KeyRelease>` en buscador de maestro (`1735` → `_programar_busqueda` → `_buscar:1789-1847`) | `iterrows` sobre todo el maestro + similitud por fila | "Stutter" al buscar |
| UI-08 | Abrir ventana de validación (`VentanaValidacion.__init__:288`) | Carga de revisión + construcción e inserción de todas las filas | Congelamiento al abrir |
| UI-09 | `_refrescar_usuarios:2370-2384`, `_refrescar_auditoria:3706` | `iterrows` + inserción fila a fila en Treeview | Refrescos perceptibles |
| UI-10 | `VentanaCoincidencias._refrescar:3063-3090` y `<KeyRelease>` (`2943`) | Ordena/filtra y reconstruye todas las filas | "Stutter" y congelamiento |

### 6.2 Por qué ocurre (mecanismo común)

1. **El callback de UI hace I/O.** `_refrescar_solicitantes` (`interfaz.py:2929-2992`) llama `MaestroTickets.cargar` y `detectar_solicitantes` directamente. `_previsualizar` llama `previsualizar` directamente. `_asignar_desde_validacion` llama `asignar_una` directamente.
2. **El bus de eventos es síncrono, pero eso no es el problema.** `_al_evento_datos` (`955-961`) sí marshalea con `after(0, …)`. El problema es que `_aplicar_evento` ejecuta los refrescos pesados **en el hilo de UI**.
3. **Los Treeview se reconstruyen completos.** `_limpiar_tabla` + `insert` fila a fila en cada refresco; no hay actualización incremental ni virtualización.
4. **No hay debounce en los filtros principales** (`ventana_validacion.py:390`), aunque sí lo hay en el buscador de maestro (180 ms, `1781-1787`).
5. **La configuración se relee en cada refresco de KPIs** (`_actualizar_kpis:625` → `cargar_configuracion`), que además descifra DPAPI (CAC-01).

### 6.3 Escenarios de congelamiento (HIPÓTESIS a confirmar)

| Escenario | Secuencia | Por qué se congela |
|---|---|---|
| Abrir la app con maestro grande | `__init__` → solicitantes → pendientes | Todo el trabajo ocurre antes del primer `mainloop` |
| Asignar 20 usuarios en validación | 20 × (guardado completo + 4 refrescos + revisión) | Costo multiplicado; sin barra de progreso |
| Escribir en el filtro de validación | 1 refresco por tecla | Reconstrucción completa por evento |
| Buscar un usuario en el maestro | `iterrows` completo + similitud + reconstrucción | Búsqueda lineal con costo por fila |
| Cambiar un área desde "Usuarios y áreas" | Evento → `_refrescar_maestro` + `_refrescar_usuarios` + `_refrescar_pendientes` | Tres refrescos pesados encadenados |
| Abrir una revisión de 500+ registros | `Revision.cargar` + `_refrescar` | Parseo JSON + inserción fila a fila |

### 6.4 Regla de diseño recomendada

> **Ningún callback de Tkinter debe leer un Excel, escribir un archivo, recorrer el maestro completo ni ejecutar similitud.** Esas operaciones van a un hilo con cola; el callback solo actualiza widgets con datos ya calculados y en lotes pequeños.

Esta regla ya se aplica en procesamiento, generación, reimportación, consolidación, reportes y Anexo 5 (`interfaz.py:3891-3950`). Falta aplicarla en previsualización, arranque, eventos, validación y búsqueda.

---

## 7. Problemas con archivo, función y líneas

Esta sección es el **catálogo completo**. Cada fila incluye archivo, función, líneas aproximadas, consecuencia, severidad, corrección recomendada y riesgo de la corrección. Las secciones 8-14 amplían los dominios.

### 7.1 Interfaz y congelamientos

| ID | Archivo: líneas | Función | Consecuencia | Sev | Corrección recomendada | Riesgo de la corrección |
|---|---|---|---|---|---|---|
| UI-01 | `interfaz.py:1689-1765` | `_elegir_archivo`, `_previsualizar` | Parseo completo del Excel en el hilo de UI; ventana sin responder | P0 | Ejecutar `previsualizar` en `_ejecutar_hilo`, deshabilitar controles y mostrar progreso; validar que la ruta siga siendo la misma al volver | Carrera entre selección de hoja y resultado; resolver comparando `clave_ruta` |
| UI-02 | `interfaz.py:893-950` | `AplicacionTk.__init__` | Cuatro refrescos pesados antes del primer pintado | P0 | Diferir con `after_idle` en etapas; mostrar esqueleto; cachear lo que no cambió | Primer render con tablas vacías; aceptable si hay indicador |
| UI-03 | `interfaz.py:955-990` | `_al_evento_datos`, `_aplicar_evento` | Cada evento dispara refrescos completos en UI | P0 | Marcar regiones "sucias" y agrupar en un solo `after(50)`; cargar datos en hilo | Orden de eventos; usar banderas de suciedad y refresco final |
| UI-04 | `interfaz.py:1925-1989`; `ventana_validacion.py:1239-1269` | `_asignar_desde_validacion` | Guardado completo y 4 refrescos por usuario | P0 | Encolar asignaciones y persistir en un lote al confirmar; un solo refresco; hilo con progreso | Visibilidad parcial; mostrar estado por usuario y mantener confirmaciones de conflicto |
| UI-05 | `interfaz.py:2387-2412` | `_refrescar_pendientes` | Sugerencias `O(pendientes × maestro)` en UI | P1 | Refrescar lista sin sugerencias; calcular la sugerencia al seleccionar (ya existe en 2428) o en hilo | Pérdida de vista rápida; conservar "sin sugerencia" hasta calcular |
| UI-06 | `ventana_validacion.py:383-411,574-632` | `_refrescar`, `_actualizar_kpis` | Reconstrucción por tecla y lectura de configuración | P1 | Debounce 250 ms; filtrar DataFrame y poblar en lote; cachear configuración | Latencia percibida; indicador de "filtrando" |
| UI-07 | `ventana_validacion.py:1735,1781-1847` | `_programar_busqueda`, `_buscar` | `iterrows` + similitud por fila, con debounce de 180 ms | P1 | Índice normalizado precalculado; prefijo/contains primero; similitud solo en candidatos; hilo | Cambia el orden de candidatos; preservar fórmula y desempate |
| UI-08 | `ventana_validacion.py:288,574-604` | `VentanaValidacion.__init__`, `_refrescar` | Carga y pinta todas las filas antes de mostrar | P1 | Construir ventana, mostrar, y poblar en `after_idle`; paginar si son cientos | Tabla vacía un instante; aceptable |
| UI-09 | `interfaz.py:2370-2384,3706-3733` | `_refrescar_usuarios`, `_refrescar_auditoria` | `iterrows` + inserción fila a fila (auditoría ya limitada a `tail(300)`) | P2 | `itertuples`/`to_dict('records')` e `insert` en lote; paginar usuarios | Cambios mínimos de formato; probar |
| UI-10 | `ventana_validacion.py:2943,3063-3090` | `VentanaCoincidencias._refrescar` | Reconstrucción total por tecla | P2 | Debounce + actualización incremental o paginación | Igual que UI-06 |

### 7.2 ETL, Excel y pandas

| ID | Archivo: líneas | Función | Consecuencia | Sev | Corrección recomendada | Riesgo de la corrección |
|---|---|---|---|---|---|---|
| ETL-01 | `core/procesador.py:306-733` (bucle 375) | `procesar_archivo` | `iterrows` con lógica por fila; costo alto por registro | P1 | Vectorizar normalización; iterar solo excepciones; `to_dict('records')` como paso intermedio | Alterar precedencia o estados; exige pruebas doradas |
| ETL-02 | `core/procesador.py:341` | `procesar_archivo` | Copia completa del DataFrame | P2 | Copiar solo columnas que se mutan o trabajar inmutable | Mutaciones accidentales; probar |
| ETL-03 | `core/procesador.py:686-696,717-728` | `procesar_archivo` | Manifiesto con todas las filas, duplicado para detalle | P1 | Guardar índice + huella + estado; detalle en archivo aparte o streaming; comprimir | Consumidores de `filas.datos`; mantener compatibilidad o versión de manifiesto |
| ETL-04 | `core/archivos.py:449-487,490-522` | `escribir_dataframe_atomico`, `escribir_json_atomico` | Relectura de verificación en cada escritura | P2 | Validar en memoria antes de escribir; relectura solo para maestros; opción `verificar` | Menor detección de corrupción; conservar para archivos críticos |
| ETL-05 | `core/maestro.py:136-189,191-295,341-355` | `Maestro.cargar`, `Maestro.guardar`, `_verificar_destinos` | Lecturas y escrituras múltiples por carga/guardado | P1 | Una escritura por lote; comparar por hash; CSV diferido; caché por huella | Contrato xlsx+csv; asegurar consistencia al final del lote |
| ETL-06 | `core/maestro_tickets.py:83-112,185-243` | `_normalizar_df`, `_canonico_df`, `MaestroTickets.cargar` | Triple lectura y normalización Python en cada refresco | P1 | Caché por (ruta, tamaño, mtime, hash); leer CSV solo si cambió el xlsx; vectorizar | Caché obsoleta; invalidar por huella |
| ETL-07 | `core/maestro_tickets.py:400-527,532-628,630-711` | `aplicar`, `validar`, `_escribir_temporal`, `guardar` | `iterrows`, renormalización total, escritura celda a celda, relectura | P2 | `to_excel` directo con formato controlado; normalizar una vez; huellas cacheadas | Formato/estilos de la hoja; pruebas de archivo |
| ETL-08 | `core/etl.py:354-445,499-522` | `_analizar_encabezado_hoja`, `seleccionar_hoja_y_encabezado`, `extraer` | Apertura repetida del libro y `iterrows` | P2 | Abrir una vez en modo lectura y reutilizar; leer solo el rango necesario | Matices de `read_only` en openpyxl; probar con archivos reales |
| ETL-09 | `core/procesador.py:97-102` | `_hash_archivo` | Lectura completa adicional para SHA-256 | P2 | Hashear durante la lectura de extracción o usar tamaño+mtime para prechequeo | Debilita la garantía de integridad; conservar hash para auditoría |
| ETL-10 | `core/reimportacion.py:362-470` | `analizar_reimportacion` | Emparejamiento `O(pendientes × originales)` con similitud por columna | P2 | Índice por huella (ya existe) + bloqueo por campos clave + caché de puntajes | Cambia emparejamientos; pruebas con correcciones reales |

### 7.3 Asignación de usuarios y áreas

| ID | Archivo: líneas | Función | Consecuencia | Sev | Corrección recomendada | Riesgo de la corrección |
|---|---|---|---|---|---|---|
| ASG-01 | `core/asignaciones.py:120-155,171-203` | `sugerencias_usuario`, `usuarios_pendientes` | `O(pendientes × maestro)` con `SequenceMatcher` | P1 | Índice normalizado del maestro una vez; bloqueo por prefijo/longitud; caché por versión | Menor recall; conservar umbral y fallback |
| ASG-02 | `core/asignaciones.py:292-451,453-477` | `asignar`, `asignar_una` | Persistencia completa por usuario | P1 | Usar `asignar` con lote y un solo guardado; `asignar_una` para casos individuales | Cambios en el diario y la numeración de operaciones; probar idempotencia |
| ASG-03 | `core/operaciones.py:177-188`; `core/procedencia.py:111-195` | `_guardar`, `registrar` | Reescritura completa de JSON por asignación; historial sin retención | P1 | Registro por lote (`registrar_lote` ya existe) y retención configurable; evaluar SQLite | Pérdida de historial; política de retención explícita |
| ASG-04 | `core/auditoria.py:80-101,205-265` | `registrar`, `registrar_cambios_maestro` | Read-modify-write de CSV completo por evento | P1 | Append-only con `mode='a'` y candado; compactación periódica | Deduplicación por ID; validar antes de anexar |
| ASG-05 | `core/asignaciones.py:292-451` | `asignar` | El lote existe pero la UI no lo usa | P2 | Cambiar la UI a lote; conservar confirmaciones individuales | UX de conflictos; mostrar resumen |
| ASG-06 | `core/procedencia.py:179-191` | `registrar` | `historial` crece indefinidamente | P2 | Límite por usuario o por tiempo; archivo rotado | Auditoría histórica; conservar exportación |

### 7.4 Solicitantes

| ID | Archivo: líneas | Función | Consecuencia | Sev | Corrección recomendada | Riesgo de la corrección |
|---|---|---|---|---|---|---|
| SOL-01 | `core/solicitantes.py:135-149,190-245` | `_similitud`, `detectar_solicitantes` | `O(k²)` acotado a claves de una sola variante | P2 | Índice por trigramas/prefijo; caché de pares; umbral previo por longitud | Cambian las sugerencias; pruebas con el maestro real |

### 7.5 SQL

| ID | Archivo: líneas | Función | Consecuencia | Sev | Corrección recomendada | Riesgo de la corrección |
|---|---|---|---|---|---|---|
| SQL-01 | `database/queries.py:131-142,229-279` | `sql_busqueda_consolidada`, `_limpiar`, `_limpiar_espacios` | `LIKE '%…%'` sobre `REPLACE/UPPER` no usa índices | P1 | Columna normalizada persistida + índice; o tabla de búsqueda; medir plan | Cambia semántica de acentos/puntuación; replicar reglas en la columna |
| SQL-02 | `database/repository.py:317-345,365-620` | `_obtener_departamento`, `_buscar_consolidado` | Una conexión por consulta y N+1 de departamentos en la primera búsqueda | P2 | Reutilizar conexión por búsqueda; lote `IN` de códigos (≤100); caché por ejecución | Límite de parámetros de SQL Server; chunking |
| SQL-03 | `database/repository.py:300-363` | `RepositorioEmpleados` | Metadatos y caché con alcance de instancia | P2 | Caché compartida con TTL por configuración/servidor | Metadatos obsoletos; TTL corto e invalidación manual |
| SQL-04 | `database/repository.py:780-1059` | `_consultar` | Ruta legacy empresa por empresa, con capacidades por empresa | P2 | Redirigir a `_buscar_consolidado`; marcar obsoleta y eliminar tras pruebas | Compatibilidad con llamadores; buscar referencias |
| SQL-05 | `database/connection.py:147-186` | `conectar` | Conexión nueva por consulta, sin pooling; timeout mínimo 60 s | P3 | Pool o conexión por operación; timeout configurable por tipo | Conexiones colgadas; límites del servidor |
| SQL-06 | `database/repository.py:451-455` | `_buscar_consolidado` | Cancelación solo entre lotes | P3 | Enviar cancelación al servidor o reducir lote | Complejidad; medir primero |

### 7.6 Concurrencia

| ID | Archivo: líneas | Función | Consecuencia | Sev | Corrección recomendada | Riesgo de la corrección |
|---|---|---|---|---|---|---|
| CON-01 | `core/auditoria.py:80-101` | `registrar` | Sin candado; hilos concurrentes pueden perder filas | P1 | `RLock` de módulo + lock de archivo; append-only | Interbloqueos; orden de adquisición documentado |
| CON-02 | `core/estado.py:107-118`; `core/procedencia.py:70-80` | `guardar_estado`, `guardar_procedencia` | Read-modify-write sin candado; dos instancias se pisan | P1 | Lock de archivo por recurso o SQLite | Cambio de formato; migración |
| CON-03 | `core/integridad_excel.py:219-282` | `guardar_maestro_seguro` | Ventana TOCTOU entre comparar y guardar; no toma lock propio | P1 | Tomar `BloqueoSistema` dentro del guardado (reentrante) | Reentrada; detectar PID propio |
| CON-04 | `interfaz.py:1925-1989,3891-3908` | `_asignar_desde_validacion`, `_ejecutar_hilo` | Escrituras de UI y de hilo trabajador sin cola única | P1 | Cola única de escritura; `_ocupado` para toda escritura | Serialización; puede aumentar espera percibida |
| CON-05 | `core/bloqueo.py:28,68-78` | `_obsoleto` | Lock obsoleto a los 30 min sin latido | P2 | Latido periódico durante lotes largos | Complejidad; reloj del sistema |
| CON-06 | `core/integridad_excel.py:268-281` | `guardar_maestro_seguro` | La nota de ejecutor se muta después de persistir; no queda en el diario | P2 | Pasar `usuario`/detalle al `diario.registrar` antes del `marcar` | Cambia claves de idempotencia; probar |
| CON-07 | `core/eventos.py:112-144` | `BusEventos.publicar` | Bus síncrono: un suscriptor lento bloquea al productor | P3 | Regla: suscriptores O(1); opcional cola por tema | Cambia orden de entrega; probar |

### 7.7 Caché

| ID | Archivo: líneas | Función | Consecuencia | Sev | Corrección recomendada | Riesgo de la corrección |
|---|---|---|---|---|---|---|
| CAC-01 | `config/sqlserver.py:240-261` | `cargar_configuracion` | Relee JSON y descifra DPAPI en cada llamada | P1 | Memoizar por (ruta, mtime) e invalidar al guardar | Configuración obsoleta; invalidación explícita |
| CAC-02 | `interfaz.py:2929-2945` | `_refrescar_solicitantes` | Sin caché del maestro de tickets por huella | P1 | Caché (ruta, mtime, tamaño, hash) → DataFrame | Consistencia; invalidar por huella |
| CAC-03 | `database/repository.py:300-363` | `RepositorioEmpleados` | Caché de instancia, no compartida | P2 | Caché de aplicación con TTL | Metadatos obsoletos; TTL corto |
| CAC-04 | `core/asignaciones.py:120-155` | `sugerencias_usuario` | Sin índice/caché de similitudes | P2 | Índice por versión del maestro; caché de puntajes | Memoria; límite de tamaño |
| CAC-05 | `core/solicitantes.py:59-108` | `cargar_equivalencias` | Se relee en cada detección; sin política de invalidación | P2 | Caché por mtime; invalidar al guardar | Equivalencias obsoletas; invalidación explícita |

### 7.8 Seguridad y configuración

| ID | Archivo: líneas | Función | Consecuencia | Sev | Corrección recomendada | Riesgo de la corrección |
|---|---|---|---|---|---|---|
| SEC-01 | `config/sqlserver.py:253-256` | `cargar_configuracion` | Acepta `contrasena` en texto plano si existe (clave legacy) | P2 | Rechazar la clave legacy o migrarla y borrarla al guardar | Compatibilidad con archivos antiguos; migración guiada |
| SEC-02 | `config/sqlserver.py:264-279` | `guardar_configuracion` | No verifica ACL del archivo que contiene el cifrado | P3 | Restringir permisos al usuario del proceso; documentar | Entornos no Windows; manejo de errores |

### 7.9 Word

| ID | Archivo: líneas | Función | Consecuencia | Sev | Corrección recomendada | Riesgo de la corrección |
|---|---|---|---|---|---|---|
| DOC-01 | `core/lector_informe_docx.py:176-258` | `leer_excel_informe` | `iterrows` sobre el Excel de entrada; costo por fila | P3 | `to_dict('records')` y normalización vectorizada | Cambios de tipado; pruebas con archivos reales |
| DOC-02 | `core/generador_docx.py:46-111` | `generar_anexo5_docx` | Todo el pipeline ocurre en memoria y copia; sin progreso granular | P3 | Reportar progreso por etapa al hilo | Cambios de callback; bajo riesgo |

### 7.10 Mantenibilidad

| ID | Archivo: líneas | Función | Consecuencia | Sev | Corrección recomendada | Riesgo de la corrección |
|---|---|---|---|---|---|---|
| MNT-01 | `interfaz.py:1-3693`; `ventana_validacion.py:1-3049`; `database/repository.py:1-1112` | — | Módulos god; difícil testear y cambiar | P2 | Extraer servicios y vistas por pestaña/ventana | Regresiones; hacerlo con pruebas de humo |
| MNT-02 | `core/maestro.py`, `core/maestro_tickets.py` | — | Dos maestros con lógica paralela | P2 | Base común de persistencia/validación | Diferencias sutiles; no unificar a ciegas |
| MNT-03 | `config/configuracion.py:16-72` | — | `DEFAULT_CONFIG` y constantes duplicadas; rutas derivadas | P2 | Una sola fuente y funciones de ruta | Cambios de instalación; probar |
| MNT-04 | raíz | — | Sin `pyproject.toml`/`pytest.ini`/CI/lint/tipado | P2 | Configurar pytest, ruff/mypy, CI | Ruido inicial; aplicar incremental |
| MNT-05 | `tests/` | — | 298 tests sin medición de rendimiento | P3 | Añadir harness de tiempos y presupuestos relativos | Entornos variables; usar ratios y mediana |

---

## 8. Auditoría SQL

### 8.1 Garantías confirmadas

| Garantía | Evidencia | Evaluación |
|---|---|---|
| Solo `SELECT`/`WITH` | `database/connection.py:195-210` | Correcta |
| Bloqueo de verbos peligrosos | `_SQL_PROHIBIDO` (`connection.py`) incluye INSERT, UPDATE, DELETE, MERGE, DROP, ALTER, TRUNCATE, CREATE, EXEC, GRANT, REVOKE, DENY, BACKUP, RESTORE, DBCC, SHUTDOWN, KILL, BULK | Correcta |
| Valores parametrizados | `queries.py` usa `?` en todos los predicados | Correcta |
| Identificadores validados | `_IDENTIFICADOR` + `validar_esquema` contra `EMPRESAS` (`queries.py:19,57-76`) | Correcta |
| Consulta manual acotada | 500 filas por defecto (tope 5.000) y ≤60 s (`connection.py:241-270`) | Correcta |
| Errores clasificados | `_clasificar_error_consulta`, `seguimiento` | Buena práctica |

### 8.2 Hallazgos

| ID | Hallazgo | Detalle | Severidad |
|---|---|---|---|
| SQL-01 | `LIKE` no SARGable | `sql_busqueda_consolidada` construye `REPLACE(UPPER(LTRIM(...))) LIKE ?` (o `CONCAT(...) LIKE ?`). Ningún índice tradicional resuelve eso; SQL Server escanea cada `EMPLEADO` por empresa | P1 |
| SQL-02 | Conexión por consulta y N+1 de departamentos | `_fabrica` se usa en cada operación (`repository.py:337,353,462,…`); `_obtener_departamento` consulta un código por vez, con caché de diccionario por instancia | P2 |
| SQL-03 | Metadatos por instancia | `_capacidades_consolidadas` se cachea en `self`; una nueva instancia vuelve a consultar `INFORMATION_SCHEMA` | P2 |
| SQL-04 | Ruta legacy activa | `_consultar` recorre empresa por empresa y lee capacidades por empresa; sigue disponible | P2 |
| SQL-05 | Sin pooling y timeout mínimo | `conectar` abre/cierra por consulta; `conexion.timeout = max(60, config.timeout)` | P3 |
| SQL-06 | Cancelación solo entre lotes | `_cancelado` se evalúa antes de cada lote de 100 patrones | P3 |

### 8.3 Observaciones sobre el diseño correcto

- **Batching real:** `_buscar_consolidado` calcula metadatos una vez y ejecuta `UNION ALL` por lotes de hasta 100 patrones (`repository.py:450-463`), evitando el límite de 2.100 parámetros de SQL Server. Es una decisión acertada.
- **Enriquecimiento solo de hits:** tras la búsqueda principal, consulta detalles únicamente de los códigos encontrados (`repository.py:485-620`). Correcto.
- **SERVIAM** usa la vista MDA (`queries.py:145-204`) con parámetros y sin concatenación.
- **La consulta manual** valida solo lectura y limita filas/tiempo. Correcto.

### 8.4 Recomendaciones SQL (requieren DBA)

1. **Columna normalizada persistida** en `EMPLEADO` (por ejemplo `NOMBRE_NORM`) calculada con las mismas reglas de `_limpiar`/`_limpiar_espacios`, e índice sobre ella. Sin esto, SQL-01 no se resuelve en la aplicación.
2. **Índice de cobertura** para `EMPLEADO`, `NOMBRE`, `DEPARTAMENTO`, `PUESTO`.
3. **Lote de departamentos** con `IN (?, …)` de hasta 100 códigos por consulta.
4. **Reutilizar la conexión** durante una búsqueda completa (una apertura por búsqueda, no por rama).
5. **Medir el plan real** antes de cambiar: `SET STATISTICS IO/TIME ON` en un entorno de prueba y con los 18 esquemas.
6. **Eliminar la ruta legacy** después de confirmar que ningún llamador la usa (búsqueda de referencias y pruebas).

> No se propone crear índices a ciegas: la forma correcta es medir el plan y el volumen por empresa. Eso es HIPÓTESIS hasta que el DBA lo valide.

---

## 9. Auditoría Excel / pandas

### 9.1 Patrones observados

| Patrón | Dónde | Evaluación |
|---|---|---|
| Escritura atómica con temporal + `os.replace` | `core/archivos.py:449-522` | Correcta |
| Verificación releyendo el archivo | `archivos.py:463,466,501-502` | Correcta pero costosa (ETL-04) |
| Detección de archivo abierto en Excel | `core/integridad_excel.py:77-95`; `archivos.py:469-479` | Correcta y con mensaje claro |
| Respaldo antes de sobrescribir | `core/archivos.py:426-447`; `maestro.py:244-253` | Correcta |
| Comparación xlsx vs csv con normalización | `maestro.py:136-189`; `maestro_tickets.py:224-237` | Correcta; costo alto |
| Normalización de columnas y alias | `archivos.py:38-111` | Correcta |
| `iterrows` en rutas de datos | procesador, consolidación, maestros, reimportación, lector DOCX | Deuda de rendimiento |
| `df.copy()` completo | `procesador.py:341`; `maestro.py:213` | Defensivo; duplica memoria |
| `utf-8-sig` en CSV | `archivos.py`, `auditoria.py` | Correcto para Excel en español |
| Fechas con formato explícito | `core/etl.py:175-320`; `cfg.FORMATO_FECHA_PREFERIDO` | Correcta; evita ambigüedad |
| Duplicados conservados y marcados | `procesador.py` (estados/observaciones) | Correcta (regla de negocio) |

### 9.2 Riesgos de datos

| Riesgo | Evidencia | Mitigación actual | Recomendación |
|---|---|---|---|
| NaN/NaT convertidos a texto | `core/auditoria._vacio`; `_texto` en ETL | Parcial | Pruebas con nulos en todas las columnas |
| Tipos inferidos distintos entre xlsx y csv | `comparar_dataframes` normaliza | Sí | Mantener y probar con fechas/horas |
| Fórmulas en celdas | openpyxl `data_only` en algunos lectores | Parcial | Documentar que se leen valores cacheados; validar en Excel real |
| Archivo abierto por el usuario | `archivo_bloqueado` + `PermissionError` | Sí | Mantener; añadir reintento asistido |
| Crecimiento del manifiesto | ETL-03 | Ninguna | Reducir contenido o comprimir |
| Pérdida de formato al guardar | `to_excel` sin estilos | Aceptable para datos | Probar con la hoja `CONTROL` |

### 9.3 Reglas de negocio que no deben romperse al optimizar

1. El maestro de áreas y su CSV deben quedar consistentes al final de cualquier operación.
2. La carga debe seguir detectando discrepancias entre xlsx y csv.
3. Los archivos originales nunca se modifican.
4. Las fechas ambiguas no se interpretan en silencio.
5. Los duplicados se conservan; no se eliminan para "acelerar".
6. Los usuarios sin área quedan pendientes, con su área preexistente si la había.

---

## 10. Auditoría de asignación de usuarios y áreas

### 10.1 Cadena completa

```text
sugerencias_usuario (maestro completo, similitud por fila)
   → selección manual
   → asignar_una → asignar(guardar=True, proteger=True)
        → _preparar_guardado_protegido (bloqueo Excel + huella)
        → maestro.actualizar (memoria)
        → guardar_maestro_seguro
             → comparar huella otra vez
             → maestro.guardar (temporales, validación, backups, rollback, verificación)
             → verificar_excel_legible
             → registro.guardar (SHA-256 final)
             → diario.ejecutar (idempotencia)
        → _registrar_en_diario (por asignación)
        → procedencia.registrar (por asignación)
   → refrescos de UI
```

### 10.2 Invariantes confirmadas (deben preservarse)

| Invariante | Evidencia |
|---|---|
| Un usuario no válido (vacío) no se asigna | `asignaciones.py:324-327` |
| Un área vacía no se asigna | `asignaciones.py:328-331` |
| Área del maestro prevalece ante área del archivo | `procesador.py:390-403` |
| Asignación manual prevalece sobre SQL/legacy | `procedencia.py:147-151` |
| Usuario desconocido no inventa área | `procesador.py` (pendientes) |
| Duplicados se conservan y marcan | `procesador.py` (estados/observaciones) |
| Cambio externo del Excel exige confirmación | `integridad_excel.py:248-253`; `interfaz.py:1941-1975` |
| Archivo abierto en Excel bloquea el guardado | `integridad_excel.py:242-246` |
| Reintento idempotente no duplica el diario | `operaciones.py:223-236` |
| Manual no se pisa por automático | `procedencia.py:147-151` |

### 10.3 Problemas específicos

- **ASG-02 / UI-04:** la UI usa `asignar_una` en bucle; el backend soporta lote. Es la optimización de mayor retorno y menor riesgo conceptual: **una sola escritura por lote**, manteniendo confirmaciones y detección de conflictos.
- **ASG-03:** `registrar_lote` existe (`procedencia.py:198`) pero no se usa en la ruta de asignación. Usarlo reduce `O(N²)` a `O(N)` en escritura de procedencia.
- **ASG-04 / CON-01:** la auditoría se reescribe por evento y sin candado. Un lote de 50 asignaciones produce 50 reescrituras completas y una ventana de carrera con el hilo de procesamiento.
- **CON-06:** el ejecutor se pierde en el diario porque la nota se añade después de `marcar` (que ya persistió). Es un fallo de trazabilidad, no de datos.
- **Doble diálogo:** `_asignar_desde_validacion` confirma el área y, si el maestro cambió, vuelve a preguntar. Es correcto, pero multiplica la fricción en lotes; conviene un resumen de conflictos al final.

### 10.4 Recomendación de diseño

Separar la **decisión** (en memoria, por usuario) de la **persistencia** (una vez por lote):

```text
[UI] acumular decisiones → [hilo] validar lote → [una escritura] maestro + csv
     → [una escritura] diario + procedencia + auditoría → [un refresco] UI
```

Esto conserva todas las reglas y reduce el costo de `N × (6 lecturas + 2 escrituras + 4 copias)` a `1 ×` ese costo más el trabajo en memoria.

---

## 11. Auditoría de Word

### 11.1 Pipeline

```text
Excel procesado
  → lector_informe_docx.leer_excel_informe (valida columnas, fechas, horas)
  → estadisticas_informe_docx.calcular_estadisticas_informe
  → proyector_anexo5_docx.preparar_contenido_anexo5 (Decimal)
  → generador_docx.generar_anexo5_docx
       ├─ copia la plantilla a temporal
       ├─ reemplaza marcadores conservando runs
       ├─ quita subrayado de títulos de gráficos
       ├─ actualiza 5 tablas localizadas por encabezado completo
       ├─ actualiza 4 gráficos (XML + libros embebidos) en un zip nuevo
       ├─ valida la salida (zip, charts, libros)
       └─ verifica hash de la plantilla antes y después
```

### 11.2 Garantías confirmadas

| Garantía | Evidencia |
|---|---|
| La plantilla nunca se sobrescribe | `generador_docx.py:62-69`; `actualizador_graficos_docx.py:228-235` |
| La salida existente se rechaza | `generador_docx.py:68-69`; `actualizador_graficos_docx.py:234-235` |
| Reserva exclusiva de la ruta final | `actualizador_graficos_docx.py:285-293` (`O_CREAT` + `O_EXCL`) |
| Detección de cambio de plantilla durante la generación | `generador_docx.py:84,101-104`; `actualizador_graficos_docx.py:246,283-284` |
| Localización de tablas por encabezado completo | `actualizador_tablas_docx.py:47-63` |
| Reemplazo de texto conservando runs | `actualizador_textos_docx.py:59-102` |
| Validación de la salida emitida | `actualizador_graficos_docx.py:171-214` |
| Cálculos con `Decimal` | `proyector_anexo5_docx.py`, `estadisticas_informe_docx.py` |
| Se ejecuta en hilo | `interfaz.py:3478-3516,3574-3620` |

### 11.3 Hallazgos

Solo dos menores:

- **DOC-01 (P3):** `core/lector_informe_docx.py:176-258` recorre con `iterrows` y normaliza por fila. Para el tamaño típico de un informe es aceptable; si el Excel crece, conviene vectorizar.
- **DOC-02 (P3):** no hay progreso granular por etapa; el usuario ve "generando" sin saber si está en tablas o gráficos. Mejora de UX, no de correctitud.

### 11.4 Riesgos residuales

| Riesgo | Impacto | Mitigación |
|---|---|---|
| Plantilla oficial modificada por un tercero | Generación abortada o informe inconsistente | Hash antes/después (ya implementado) |
| Estilos personalizados en tablas | Se conservan al clonar filas; probar con la plantilla real | Pruebas de plantilla (`test_plantilla_docx.py`) |
| Gráficos con más de 4 series o nombres distintos | Error controlado | Mensaje claro; documentar contrato |
| Documento abierto en Word al escribir | `PermissionError` | Mensaje claro; no sobrescribe por diseño |

**Conclusión:** el pipeline Word es la parte más sólida del sistema en cuanto a integridad. No requiere cambios de arquitectura; solo las mejoras menores indicadas.

---

## 12. Auditoría de interfaz

### 12.1 Estructura

| Componente | Evidencia | Observación |
|---|---|---|
| Ventana principal | `AplicacionTk` (`interfaz.py:890`) | 7 pestañas: Panel, Cargar y procesar, Usuarios y áreas, Reimportar, Maestro histórico, Reportes, Auditoría (`1422-1428`) |
| Panel | `panel_principal.PanelPrincipal` | KPIs, sparklines, acciones; se actualiza con `actualizar_kpis` |
| Validación | `ventana_validacion.VentanaValidacion` | KPIs, filtros, tabla, acciones por fila, menú contextual |
| Ventanas secundarias | `VentanaDetalleRegistro`, `VentanaBuscarExcelMaestro`, `VentanaConsultaSqlManual`, `VentanaCoincidencias`, `VentanaSeguimientoConsulta` | Todas en `ventana_validacion.py` |
| Cola de trabajos | `_cola`, `_procesar_cola` (`3910-3950`) | 120 ms; patrón correcto |
| Eventos | `core/eventos.py` + `_al_evento_datos` | Marshal correcto con `after(0)` |

### 12.2 Problemas de experiencia y rendimiento

| ID | Problema | Evidencia | Efecto |
|---|---|---|---|
| UI-01 | Sin indicador de progreso en previsualización | `1714-1765` | El usuario cree que la app se colgó y vuelve a hacer clic |
| UI-02 | Arranque sin esqueleto ni progreso | `893-950` | Ventana en blanco |
| UI-03 | Refrescos completos tras cada evento | `963-990` | Parpadeo y congelamiento |
| UI-04 | Sin lote ni progreso en asignación múltiple | `1925-1989` | Sensación de bloqueo |
| UI-06 | Filtro sin debounce | `ventana_validacion.py:390` | "Stutter" al teclear |
| UI-09 | Tablas reconstruidas fila a fila | `2378-2384` | Parpadeo y costo |
| — | Sin paginación/virtualización | Treeviews | Con cientos/miles de filas, el widget es el cuello de botella |
| — | Diálogos modales en medio de un lote | `_asignar_desde_validacion` | Interrumpe el flujo y multiplica la percepción de lentitud |
| — | `_ocupado` no cubre todas las escrituras | `1925` no consulta `_ocupado` | Posible solapamiento con procesamiento |
| — | Mensajes de error técnicos | Varios `messagebox.showerror` | Aceptable para usuarios técnicos; documentar |

### 12.3 Lo que está bien

- **Hilos + cola** en todos los flujos largos excepto los listados.
- **Estado de "ocupado"** y barra de progreso en procesamiento/generación.
- **Confirmaciones** para cambios de área y conflictos.
- **Mensajes claros** cuando Excel tiene el archivo abierto.
- **Separación visual** por pestañas y panel de KPIs.
- **Uso de `after`** para volver al hilo de UI desde eventos.

### 12.4 Recomendaciones de UX inmediatas (sin cambiar reglas)

1. Barra de progreso indeterminada + texto ("Leyendo archivo…", "Guardando maestro…") en previsualización, asignación y búsqueda.
2. Deshabilitar botones mientras hay una operación en curso (un único `_ocupado` global para escrituras).
3. Debounce de 250 ms en filtros.
4. Resultado de búsqueda con "mostrando N de M" y botón "ver más".
5. Resumen al final de un lote: "12 asignadas, 1 omitida, 1 conflicto".

---

## 13. Caché

### 13.1 Inventario actual

| Caché | Dónde | Alcance | Invalidación | Evaluación |
|---|---|---|---|---|
| Departamentos por (empresa, código) | `repository._cache_departamentos:315,326-344` | Instancia del repositorio | Ninguna dentro de la instancia | Útil; se pierde al crear otra instancia |
| Metadatos de 18 empresas | `repository._capacidades_consolidadas:314,350-362` | Instancia | Ninguna | Útil; repetido por instancia |
| Empleados sincronizados | `database/synchronizer.py` (CSV/JSON) | Global | Sincronización manual/consulta | Correcta, pero sin TTL explícito |
| Configuración SQL | `config/sqlserver.py:240` | **Ninguna** | — | Se relee y descifra en cada llamada (CAC-01) |
| Maestro de tickets | — | **Ninguna** | — | Se relee en cada refresco (CAC-02) |
| Equivalencias de solicitantes | `core/solicitantes.py:59` | **Ninguna** | — | Se relee en cada detección (CAC-05) |
| Sugerencias de similitud | — | **Ninguna** | — | Se recalcula por usuario (CAC-04) |
| Índice del maestro | — | **Ninguna** | — | Se recorre con `iterrows` (ASG-01) |

### 13.2 Diseño de caché propuesto

| Caché | Clave | Valor | Invalidación | Riesgo |
|---|---|---|---|---|
| Configuración SQL | `(ruta, mtime, tamaño)` | `ConfiguracionSQL` | Al guardar configuración; cambio de mtime | Bajo |
| Maestro de tickets | `(ruta, mtime, tamaño, SHA-256 opcional)` | DataFrame normalizado + control | Cambio de huella; tras guardar | Medio: archivo cambiado sin mtime (raro) |
| Índice de usuarios | `(versión del maestro)` | DataFrame normalizado + columna `clave` | Cambio de versión | Bajo |
| Sugerencias | `(versión, usuario, umbral)` | Lista top-N | Cambio de versión o umbral | Bajo |
| Equivalencias | `(ruta, mtime)` | Dict | Al guardar equivalencias | Bajo |
| Similitudes de solicitantes | `(clave_a, clave_b, versión)` | Puntaje | Cambio del maestro | Medio: memoria; limitar tamaño |
| Metadatos SQL | `(servidor, base, esquema)` | Columnas/tablas | TTL 10-30 min o botón "refrescar" | Bajo |

### 13.3 Reglas de oro

1. **Nunca cachear el maestro para escribir.** El guardado debe partir del archivo verificado por huella; la caché es solo para lectura/UI.
2. **Invalidar por huella, no solo por tiempo.** El usuario puede editar el Excel fuera de la app.
3. **La caché no puede cambiar reglas de negocio.** Si una decisión depende del maestro, debe leer la versión vigente.
4. **Medir aciertos.** Un caché sin métricas es una fuente de bugs silenciosos.

---

## 14. Concurrencia

### 14.1 Primitivas existentes

| Primitiva | Dónde | Alcance | Evaluación |
|---|---|---|---|
| `BloqueoSistema` | `core/bloqueo.py:92-202` | Proceso/máquina (archivo `O_EXCL`, PID, stale 30 min) | Buena base; falta latido y uso sistemático |
| `RegistroHuellas` | `core/integridad_excel.py:102-183` | Archivo (SHA-256+tamaño+mtime) | Correcta para detectar cambios externos |
| `archivo_bloqueado` | `core/integridad_excel.py:77-95` | Archivo abierto en Excel | Correcta |
| `_CANDADO` | `core/operaciones.py:40,177-188` | Hilo (RLock) | Correcta dentro del proceso; no entre procesos |
| Cola de UI | `interfaz.py:917,3910-3950` | Hilo | Correcta |
| Bus de eventos | `core/eventos.py:75-147` | Hilo | Correcto para Tkinter vía `after`; síncrono |
| Idempotencia | `core/operaciones.py:56-74,223-236` | Operación | Correcta; ver CON-06 |

### 14.2 Escenarios de carrera

| Escenario | Ventana | Consecuencia | ID |
|---|---|---|---|
| Hilo de procesamiento y UI escriben auditoría a la vez | Read-modify-write del CSV | Se pierde una fila | CON-01 |
| Dos instancias escriben `estado_ui.json`/procedencia | Read-modify-write JSON | Última escritura gana; se pierde historial | CON-02 |
| Otra app modifica el maestro entre `comparar` y `guardar` | Milisegundos | Se sobrescribe un cambio externo sin confirmación | CON-03 |
| Asignación en UI mientras se aplica una reimportación | Segundos | Dos escrituras del maestro; una puede fallar o pisar | CON-04 |
| Lote de más de 30 min sin actualizar el lock | 30 min | Otro proceso lo considera obsoleto y escribe | CON-05 |
| Fila de auditoría con mismo ID desde dos hilos | Milisegundos | Deduplicación `keep=last` puede eliminar la primera | CON-01 |

### 14.3 Reglas propuestas

1. **Un solo escritor por recurso.** Toda escritura del maestro pasa por una cola única en la aplicación.
2. **Lock jerárquico y documentado:** `sistema.lock` → maestro → csv → diarios → auditoría. Siempre en ese orden, para evitar interbloqueos.
3. **`guardar_maestro_seguro` toma el lock él mismo** (reentrante si el PID es el mismo), no confía en el llamador.
4. **Diarios append-only** o SQLite con transacción; nunca read-modify-write de JSON completo en rutas concurrentes.
5. **Latido del lock** durante lotes largos.
6. **Prohibido tocar Tkinter desde un hilo trabajador.** Ya se cumple; mantener y documentar.
7. **Los suscriptores de eventos deben ser O(1).** Si necesitan trabajo, encolan.

---

## 15. Riesgos técnicos

| # | Riesgo | Probabilidad | Impacto | Evidencia | Mitigación |
|---|---|---|---|---|---|
| R-01 | Congelamiento de UI por archivos grandes | Alta | Alto | UI-01..UI-10 | Fase 1 |
| R-02 | Maestro inconsistente por escritura concurrente | Media | Alto | CON-03, CON-04 | Fase 4 |
| R-03 | Pérdida de filas de auditoría | Media | Medio | CON-01 | Fase 4 |
| R-04 | Área incorrecta aplicada por sugerencia | Baja | Alto | ASG-01, SOL-01 | Sugerencias nunca aplican solas (ya se cumple); pruebas |
| R-05 | Crecimiento sin control de JSON/CSV | Alta | Medio | ASG-03, ASG-04, ETL-03 | Retención + append-only |
| R-06 | Lentitud SQL por `LIKE` no indexable | Media | Alto | SQL-01 | Fase 5 con DBA |
| R-07 | Duplicación de reglas entre maestros | Alta | Medio | MNT-02 | Fase 6 |
| R-08 | Regresión al refactorizar ETL | Media | Alto | ETL-01 | Pruebas doradas y comparación de salidas |
| R-09 | Dependencia de versiones no fijadas | Media | Medio | `requirements.txt` con `>=` | Fijar versiones en un lock file |
| R-10 | Sin CI: una regresión llega a producción | Media | Alto | MNT-04 | Fase 6 |
| R-11 | Contraseña legacy en texto plano | Baja | Alto | SEC-01 | Migrar y rechazar la clave |
| R-12 | Archivo de configuración legible por otros usuarios | Baja | Medio | SEC-02 | ACL + documentación |

---

## 16. Matriz de prioridades

**P0** = hacer primero; **P1** = siguiente; **P2** = planificar; **P3** = cuando se toque el módulo.
**Esfuerzo:** S (≤1 día), M (2-5 días), L (>1 semana).

| ID | Hallazgo | P | Fase | Esfuerzo | Depende de |
|---|---|---|---|---|---|
| UI-01 | Previsualización síncrona | P0 | 1 | S | — |
| UI-02 | Arranque síncrono | P0 | 1 | S | — |
| UI-03 | Refrescos por evento | P0 | 1 | M | — |
| UI-04 | Asignación por usuario | P0 | 1 | M | ASG-02 |
| UI-05 | Sugerencias en UI | P1 | 3 | M | ASG-01 |
| UI-06 | Filtro por tecla | P1 | 1 | S | CAC-01 |
| UI-07 | Buscador de maestro | P1 | 3 | M | ASG-01 |
| UI-08 | Apertura de validación | P1 | 1 | S | — |
| UI-09 | Tablas reconstruidas | P2 | 1 | S | — |
| UI-10 | Coincidencias por tecla | P2 | 3 | S | — |
| ETL-01 | `iterrows` del procesador | P1 | 3 | L | Pruebas doradas |
| ETL-02 | `df.copy()` | P2 | 3 | S | — |
| ETL-03 | Manifiesto duplicado | P1 | 3 | M | Compatibilidad |
| ETL-04 | Relectura de verificación | P2 | 2 | S | — |
| ETL-05 | Maestro multifase | P1 | 2 | L | ASG-02 |
| ETL-06 | Carga triple de tickets | P1 | 2 | M | — |
| ETL-07 | `aplicar`/`validar` de tickets | P2 | 3 | M | — |
| ETL-08 | Aperturas repetidas del libro | P2 | 3 | M | — |
| ETL-09 | SHA-256 completo | P2 | 2 | S | — |
| ETL-10 | Reimportación `O(p×n)` | P2 | 3 | M | — |
| ASG-01 | Sugerencias `O(pendientes×maestro)` | P1 | 3 | M | — |
| ASG-02 | Persistencia por asignación | P1 | 1 | M | — |
| ASG-03 | JSON por asignación | P1 | 4 | M | — |
| ASG-04 | Auditoría CSV por evento | P1 | 4 | S | — |
| ASG-05 | Lote no usado | P2 | 1 | S | ASG-02 |
| ASG-06 | Historial sin retención | P2 | 4 | S | — |
| SOL-01 | Detección `O(k²)` | P2 | 3 | M | — |
| SQL-01 | `LIKE` no SARGable | P1 | 5 | L | DBA |
| SQL-02 | Conexión/N+1 | P2 | 5 | M | DBA |
| SQL-03 | Metadatos por instancia | P2 | 5 | S | — |
| SQL-04 | Ruta legacy | P2 | 5 | M | Pruebas |
| SQL-05 | Pooling/timeout | P3 | 5 | M | — |
| SQL-06 | Cancelación | P3 | 5 | S | — |
| CON-01 | Auditoría sin candado | P1 | 4 | S | — |
| CON-02 | Estado/procedencia sin candado | P1 | 4 | M | — |
| CON-03 | TOCTOU del maestro | P1 | 4 | S | — |
| CON-04 | Escrituras UI/hilo | P1 | 4 | M | Cola única |
| CON-05 | Lock sin latido | P2 | 4 | S | — |
| CON-06 | Ejecutor no persistido | P2 | 4 | S | — |
| CON-07 | Bus síncrono | P3 | 4 | S | — |
| CAC-01 | Config sin caché | P1 | 2 | S | — |
| CAC-02 | Tickets sin caché | P1 | 2 | M | — |
| CAC-03 | Metadatos sin caché compartida | P2 | 5 | S | — |
| CAC-04 | Similitudes sin caché | P2 | 3 | M | ASG-01 |
| CAC-05 | Equivalencias sin caché | P2 | 3 | S | — |
| SEC-01 | Contraseña legacy | P2 | 4 | S | — |
| SEC-02 | ACL de configuración | P3 | 4 | S | — |
| DOC-01 | Lector DOCX `iterrows` | P3 | 6 | S | — |
| DOC-02 | Progreso DOCX | P3 | 6 | S | — |
| MNT-01 | Módulos god | P2 | 6 | L | — |
| MNT-02 | Dos maestros | P2 | 6 | L | — |
| MNT-03 | Config duplicada | P2 | 6 | S | — |
| MNT-04 | Sin CI/lint | P2 | 6 | M | — |
| MNT-05 | Sin pruebas de rendimiento | P3 | 0 | M | — |

---

## 17. Plan por fases

> Todas las fases respetan la restricción de este informe: **no se ejecutaron cambios**. El plan es una propuesta para implementar después, con validación y reversión.

### Fase 0 — Medición y línea base (1 semana)

**Objetivo:** convertir las hipótesis en datos y evitar optimizar a ciegas.

- Instrumentar con `time.perf_counter` y logs estructurados: `previsualizar`, `procesar_archivo`, `Maestro.cargar`, `Maestro.guardar`, `MaestroTickets.cargar`, `detectar_solicitantes`, `usuarios_pendientes`, `asignar_una`, `_buscar`.
- Añadir contadores: filas, lecturas, escrituras, tamaño de archivos, duración.
- Ejecutar `auditar_sistema.py` (ya existe, solo lectura) para el estado de integridad.
- Crear un banco de pruebas sintético: 1k, 10k, 50k filas; maestro de 100, 1k, 5k usuarios; 10, 50, 200 pendientes.
- **Criterio de salida:** tabla de tiempos por operación y escenario, con mediana y percentil 95, en la máquina objetivo.

**Riesgo:** instrumentar mal y medir el ruido. Usar repeticiones y mediana; no comparar máquinas distintas.

### Fase 1 — Interfaz responsiva (1-2 semanas)

**Objetivo:** eliminar los congelamientos percibidos sin cambiar reglas.

- UI-01: previsualización en hilo con progreso y cancelación cooperativa.
- UI-02: arranque por etapas con `after_idle` y esqueleto.
- UI-03: banderas de suciedad + un solo refresco agrupado.
- UI-04/ASG-02/ASG-05: encolar asignaciones y persistir en lote con progreso.
- UI-06/UI-08/UI-09: debounce, poblar en lote, construir después de mostrar.
- **Criterio de salida:** la UI responde a eventos mientras se ejecutan las operaciones; ningún callback de Tkinter lee Excel ni recorre el maestro.
- **Reversión:** cada cambio detrás de una bandera de configuración o con commits pequeños y pruebas de humo.

**Reglas preservadas:** confirmaciones de área, detección de conflicto, pendientes, duplicados.

### Fase 2 — I/O del maestro y caché (2-3 semanas)

**Objetivo:** reducir el número de lecturas/escrituras por operación.

- ETL-05: una escritura por lote; CSV diferido; comparación por hash.
- ETL-06/CAC-02: caché del maestro de tickets por huella.
- CAC-01: memoizar configuración con invalidación al guardar.
- ETL-04/ETL-09: relectura de verificación solo para archivos críticos; hash durante la lectura.
- **Criterio de salida:** una asignación individual cuesta una fracción de las lecturas actuales; un lote de N cuesta O(1) escrituras.
- **Riesgo:** romper el contrato xlsx+csv. Pruebas de consistencia después de cada operación.

### Fase 3 — Algoritmos y datos (2-4 semanas)

**Objetivo:** quitar los costos no lineales.

- ASG-01/CAC-04: índice normalizado + bloqueo por prefijo/longitud + caché de puntajes.
- UI-05/UI-07: sugerencias perezosas y búsqueda con índice.
- SOL-01: bloqueo por trigramas y caché de pares.
- ETL-01: vectorizar normalización y aislar el bucle de decisiones.
- ETL-03: manifiesto compacto con versión compatible.
- ETL-10: índice y bloqueo en reimportación.
- **Criterio de salida:** tiempos que crecen de forma aproximadamente lineal con el volumen; mismas decisiones que antes en el banco de pruebas.

### Fase 4 — Concurrencia y trazabilidad (2-3 semanas)

**Objetivo:** hacer robusto el sistema ante dos escritores y ante fallos.

- CON-01/ASG-04: auditoría append-only con candado.
- CON-02/ASG-03: procedencia y estado con lock o SQLite.
- CON-03: lock dentro de `guardar_maestro_seguro`.
- CON-04: cola única de escritura.
- CON-05: latido del lock.
- CON-06: persistir el ejecutor.
- SEC-01/SEC-02: migrar contraseña y restringir ACL.
- **Criterio de salida:** prueba de estrés con dos hilos/instancias sin pérdida de filas ni sobrescrituras silenciosas.

### Fase 5 — SQL (2-3 semanas, con DBA)

**Objetivo:** que la búsqueda multiempresa escale sin degradar el servidor.

- Medir planes y volúmenes por empresa.
- Columna normalizada persistida + índice; o tabla de búsqueda mantenida por proceso.
- Lote de departamentos; conexión por búsqueda; caché de metadatos con TTL.
- Retirar la ruta legacy tras pruebas.
- **Criterio de salida:** mismo conjunto de resultados (pruebas de equivalencia) con menor costo de servidor.
- **Riesgo:** cambiar la semántica de acentos/puntuación. Replicar exactamente las reglas y comparar resultados.

### Fase 6 — Mantenibilidad (paralela, continua)

- Dividir `interfaz.py` y `ventana_validacion.py` por pestaña/ventana.
- Unificar la persistencia de ambos maestros.
- `pyproject.toml`, pytest, ruff/mypy, CI.
- Pruebas de rendimiento con presupuestos relativos.
- **Criterio de salida:** cualquier cambio en un módulo se puede probar sin abrir la app.

---

## 18. Pruebas de no-regresión

### 18.1 Inventario existente (medido)

| Módulo | Funciones `test_` | Cubre |
|---|---:|---|
| `test_sistema.py` | 65 | ETL, maestros, esquema, flujo general |
| `test_plantilla_docx.py` | 40 | Plantilla y estructura Word |
| `test_sql_revision_generacion.py` | 37 | SQL, revisión, generación |
| `test_fases_optimizacion.py` | 32 | Optimizaciones previas |
| `test_consulta_multiempresa.py` | 29 | Búsqueda consolidada |
| `test_busqueda_solicitantes.py` | 18 | Similitud y solicitantes |
| `test_seguimiento_consultas.py` | 15 | Seguimiento SQL |
| `test_acciones_revision.py` | 13 | Acciones de validación |
| `test_integracion_mejoras_consulta.py` | 11 | Integración de consultas |
| `test_actualizadores_docx_fase2.py` | 10 | Tablas/textos Word |
| `test_informe_docx_fase1.py` | 8 | Lector de informe |
| `test_autocompletado_y_pendientes.py` | 7 | Pendientes y autocompletado |
| `test_proyector_anexo5_docx.py` | 5 | Proyección Anexo 5 |
| `test_consulta_sql_manual.py` | 4 | Consulta manual |
| `test_graficos_docx_fase3.py` | 3 | Gráficos |
| `test_generador_docx.py` | 1 | Generador |
| **Total** | **298** | |

### 18.2 Matriz de cobertura de riesgos

| Riesgo / cambio | Pruebas existentes | Pruebas nuevas necesarias |
|---|---|---|
| Precedencia revisión > maestro > archivo | `test_sistema`, `test_acciones_revision` | Doradas con los tres casos en el mismo archivo |
| Pendientes conservados | `test_autocompletado_y_pendientes` | Caso con área preexistente y sin ella |
| Duplicados conservados | `test_sistema` | Conteo exacto antes/después de optimizar |
| Manual prevalece sobre SQL | `test_acciones_revision` | Prueba explícita de `procedencia` |
| xlsx/csv consistentes | `test_sistema` | Comparación byte a byte de contenido normalizado |
| Idempotencia del diario | — | Doble asignación → un registro, un guardado |
| Concurrencia | — | Estrés de dos hilos/instancias |
| SQL solo lectura | `test_consulta_sql_manual` | Intentos de `INSERT/EXEC/WITH … DELETE` |
| Word no destructivo | `test_plantilla_docx`, `test_generador_docx` | Hash de plantilla, salida existente, O_EXCL |
| Rendimiento | — | Harness con ratios |

### 18.3 Pruebas nuevas propuestas

| ID | Prueba | Tipo | Criterio |
|---|---|---|---|
| T-01 | Dorada de procesamiento | Integración | Salida idéntica (filas, estados, áreas, manifiesto normalizado) antes/después de ETL-01 |
| T-02 | Precedencia triple | Unitaria | Revisión gana; maestro siguiente; archivo al final |
| T-03 | Pendientes y duplicados | Unitaria | Mismos conteos y mismas marcas |
| T-04 | Idempotencia | Unitaria | Repetir asignación no duplica diario ni guardado |
| T-05 | Lote vs individual | Integración | Mismo resultado final; menos escrituras (contador instrumentado) |
| T-06 | Concurrencia de auditoría | Estrés | 1.000 registros desde 2 hilos; ninguno perdido |
| T-07 | Concurrencia de maestro | Estrés | Dos escritores serializados; sin corrupción; error claro |
| T-08 | TOCTOU | Integración | Modificación externa entre comparar y guardar → excepción |
| T-09 | Caché de tickets | Unitaria | Cambio de archivo invalida; sin cambio no relee |
| T-10 | Configuración memoizada | Unitaria | Una lectura por proceso; guardar invalida |
| T-11 | SQL: solo lectura | Unitaria | Todos los verbos prohibidos rechazados; parámetros contados |
| T-12 | SQL: equivalencia de resultados | Integración | Ruta nueva = ruta vieja en 18 empresas (entorno de prueba) |
| T-13 | Word: plantilla intacta | Integración | SHA-256 de plantilla igual; salida válida |
| T-14 | Word: salida existente | Unitaria | `FileExistsError`; no sobrescribe |
| T-15 | Búsqueda de maestro indexada | Unitaria | Mismos candidatos y mismo orden que la versión actual |
| T-16 | Sugerencias indexadas | Unitaria | Mismo top-N con el maestro real |
| T-17 | Manifiesto compatible | Integración | Consumidores actuales leen la versión nueva |
| T-18 | Rendimiento relativo | Benchmark | Mediana ≤ línea base en el mismo equipo; sin umbrales absolutos |

### 18.4 Cómo medir sin inventar cifras

1. **Mismo equipo, mismo archivo, misma versión de Python.** Guardar la línea base en `docs/` con fecha.
2. **Repetir 5 veces y usar mediana + p95.** Descartar la primera ejecución (caché de disco).
3. **Instrumentar contadores, no solo tiempo:** lecturas, escrituras, filas recorridas, llamadas a similitud. Los contadores son reproducibles; los tiempos varían.
4. **Comparar resultados, no solo velocidad.** Una optimización que cambia decisiones es un fallo, no una mejora.
5. **Nunca usar datos reales en pruebas de escritura.** Copiar a un directorio temporal y verificar que el original no cambió (SHA-256).

### 18.5 Puertas de aceptación por fase

| Fase | Puerta |
|---|---|
| 0 | Línea base documentada y reproducible |
| 1 | La UI responde durante previsualización, asignación y refrescos; sin regresiones funcionales |
| 2 | Menos lecturas/escrituras por operación (contadores); xlsx/csv consistentes |
| 3 | Tiempos aproximadamente lineales; mismas decisiones en pruebas doradas |
| 4 | Estrés de concurrencia sin pérdidas; auditoría completa |
| 5 | Resultados SQL equivalentes; plan validado por DBA |
| 6 | CI verde; módulos probables sin abrir la app |

---

## Anexo A — Índice de evidencia

| Archivo | Líneas clave | Qué demuestra |
|---|---|---|
| `interfaz.py` | 890-950 | Construcción y refrescos de arranque |
| `interfaz.py` | 955-990 | Eventos y refrescos síncronos |
| `interfaz.py` | 1689-1765 | Previsualización síncrona |
| `interfaz.py` | 1925-1989 | Asignación por usuario y 4 refrescos |
| `interfaz.py` | 2370-2412 | Refresco de usuarios y pendientes |
| `interfaz.py` | 2922-2992 | Refresco de maestro y solicitantes |
| `interfaz.py` | 3828-3865 | Helper de Treeview y limpieza |
| `interfaz.py` | 3891-3950 | Hilos y cola |
| `ventana_validacion.py` | 288, 383-411, 574-632 | Apertura, filtros, refresco, KPIs |
| `ventana_validacion.py` | 744-1065 | Consulta SQL en hilo |
| `ventana_validacion.py` | 1239-1269 | Callback de asignación |
| `ventana_validacion.py` | 1575-1593 | Ejecución en hilo |
| `ventana_validacion.py` | 1781-1847 | Buscador de maestro |
| `ventana_validacion.py` | 3063-3090 | Refresco de coincidencias |
| `core/procesador.py` | 97-102, 306-733 | Hash, bucle, manifiesto |
| `core/procesador.py` | 341, 375, 686-696, 717-728 | Copia, iterrows, manifiesto duplicado |
| `core/archivos.py` | 426-522 | Respaldos y escrituras atómicas con relectura |
| `core/maestro.py` | 136-189, 191-295, 341-355 | Carga doble y guardado multifase |
| `core/maestro_tickets.py` | 83-112, 185-243, 400-527, 532-711 | Normalización, carga triple, aplicar/guardar |
| `core/asignaciones.py` | 73-107, 120-155, 171-203, 292-477 | Carga, sugerencias, pendientes, asignación |
| `core/solicitantes.py` | 59-108, 135-149, 152-245 | Equivalencias, similitud, detección |
| `core/integridad_excel.py` | 77-95, 102-183, 219-282 | Bloqueo, huellas, guardado seguro |
| `core/bloqueo.py` | 28, 68-78, 92-202 | Lock cooperativo y obsolescencia |
| `core/operaciones.py` | 32-74, 177-188, 223-236, 268-281 | Estados, idempotencia, reescritura |
| `core/procedencia.py` | 111-195, 198 | Registro y lote |
| `core/auditoria.py` | 80-101, 205-265 | CSV read-modify-write |
| `core/eventos.py` | 75-147 | Bus síncrono |
| `core/etl.py` | 354-445, 499-522 | Aperturas y extracción |
| `core/reimportacion.py` | 362-470 | Emparejamiento |
| `core/lector_informe_docx.py` | 176-258 | Lectura del informe |
| `core/generador_docx.py` | 46-111 | Generación segura |
| `core/actualizador_graficos_docx.py` | 171-302 | Zip, validación y O_EXCL |
| `database/connection.py` | 147-210, 241-270 | Conexión, solo lectura, consulta manual |
| `database/queries.py` | 19-76, 79-142, 229-279, 317-502 | Validación e SQL |
| `database/repository.py` | 300-363, 365-620, 780-1059 | Caché, búsqueda consolidada, legacy |
| `config/sqlserver.py` | 240-261 | Configuración sin caché y DPAPI |
| `config/configuracion.py` | 16-72 | Rutas y parámetros |
| `tests/` | 16 módulos | 298 pruebas |

---

## Anexo B — Hipótesis a medir

| ID | Hipótesis | Cómo medir | Decisión que desbloquea |
|---|---|---|---|
| H-01 | La previsualización tarda > 1 s en archivos reales | Cronometrar `previsualizar` con 1k/10k/50k filas | Prioridad de UI-01 |
| H-02 | El maestro real tiene cientos/miles de usuarios | Contar filas de `maestro_areas.xlsx` | Peso de ASG-01/ETL-05 |
| H-03 | Hay decenas de pendientes por ejecución | Contar `usuarios_pendientes` del manifiesto | Peso de UI-05 |
| H-04 | El maestro de tickets supera varios miles de filas | Contar filas | Peso de ETL-06/UI-02 |
| H-05 | El `LIKE` calculado consume CPU significativa en SQL | Plan + `STATISTICS TIME` en prueba | Prioridad de SQL-01 |
| H-06 | Faltan índices en `EMPLEADO`/`NOMBRE` | Consultar índices con el DBA | Diseño de SQL-01 |
| H-07 | La relectura de verificación es un porcentaje relevante del guardado | Cronometrar escritura vs relectura | Alcance de ETL-04 |
| H-08 | Se usa más de una instancia de la app | Entrevistas + logs de lock | Prioridad de CON-02/CON-04 |
| H-09 | El manifiesto y las revisiones son grandes | Medir tamaño de archivos | Diseño de ETL-03 |
| H-10 | Los usuarios dejan Excel abierto con frecuencia | Frecuencia de `PermissionError` en logs | UX de guardado |
| H-11 | El arranque se percibe lento | Cronometrar desde `main` hasta primer `mainloop` | Prioridad de UI-02 |
| H-12 | El pico de memoria es alto al procesar | Medir RSS durante el procesamiento | Necesidad de streaming |

> Ninguna de estas hipótesis se afirma como hecho en este informe.

---

## Anexo C — Glosario

| Término | Significado |
|---|---|
| Maestro de áreas | Correspondencia USUARIO → AREA_USUARIO en `data/maestro_areas.xlsx` + CSV |
| Maestro histórico | Excel de 11 columnas con todos los tickets y hoja `CONTROL` |
| Manifiesto | JSON con el resumen y las filas de una ejecución |
| Revisión | JSON con las decisiones de validación de una ejecución |
| Pendiente | Usuario sin área resuelta; se conserva para decisión manual |
| Genérico | Solicitante tipo "SOPORTE" o "TI" que no debe fusionarse |
| Huella | SHA-256 + tamaño + mtime de un archivo |
| TOCTOU | Time-of-check to time-of-use: la ventana entre verificar y escribir |
| SARGable | Predicado que puede usar un índice |
| Idempotencia | Repetir la misma operación no crea duplicados |

---

## Anexo D — Declaración de modo solo lectura

- No se modificó ningún archivo del sistema, Excel, base de datos ni configuración.
- No se ejecutaron flujos que escriban datos reales.
- No se instalaron dependencias.
- No se ejecutaron migraciones.
- El único archivo creado es este informe: `docs/AUDITORIA_TECNICA_INTEGRAL.md`.
- Las credenciales no se reproducen; solo se indica su ubicación (`config/sqlserver.json`, campo `contrasena_cifrada`, cifrado DPAPI) y el mecanismo.

**Fin del informe.**
