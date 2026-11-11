# CONTINUACIÓN DEL PLAN — INTEGRACIÓN SQL SERVER, VALIDACIÓN DE ÁREAS Y CIERRE DEL PROYECTO

## Sistema Integral de Tickets TI — INKACROPS

**Documento rector:** [`DISEÑO Y BD CONEXION.md`](./DISE%C3%91O%20Y%20BD%20CONEXION.md)
**Reporte del avance anterior:** [`REPORTE_CAMBIOS_PLAN_DISENO_BD.md`](./REPORTE_CAMBIOS_PLAN_DISENO_BD.md)
**Fecha de esta continuación:** 10/11/2026
**Estado de partida:** implementación **parcial y no verificada de extremo a extremo**
**Estado actual:** implementado y verificado con **98 pruebas automatizadas (4 omitidas)**. Ver REPORTE_FINAL_IMPLEMENTACION.md.

---

# 1. CONTEXTO Y OBJETIVO

Tomás el relevo de una implementación que ya incorporó al proyecto la mayor parte de la infraestructura del plan `DISEÑO Y BD CONEXION.md`: configuración SQL Server, acceso ODBC de solo lectura, caché corporativa de empleados, normalización y resolución de nombres, registro de procedencia, persistencia de revisiones, ventana independiente de validación y los tres modos de escritura de Excel en la capa de datos.

Esa implementación **no está terminada**. Existe código correcto que todavía no está conectado a la interfaz, existen requisitos del plan que se cumplieron solo en el backend, y no hay evidencia de pruebas de los módulos nuevos.

Tu responsabilidad es:

1. Verificar el estado real del código antes de modificarlo.
2. Completar la integración entre la ventana de validación y el flujo de la interfaz.
3. Completar y corregir la generación de Excel (modos, pendientes, copias de revisión).
4. Alinear el flujo por consola con las reglas del plan.
5. Añadir las pruebas que faltan y ejecutarlas realmente.
6. Actualizar la documentación.
7. Entregar un informe final con evidencia, no con afirmaciones.

No es una reescritura. No es una aplicación nueva. No se debe tocar el esquema oficial de 11 columnas.

El objetivo funcional completo es el flujo descrito en la sección 9 del documento rector:

Seleccionar Excel → validar estructura → ETL y normalización → maestro local → SQL Server por lotes → resolución de coincidencias → **ventana de validación si hay pendientes o conflictos** → guardar estado → validar → elegir generación completa o con pendientes → elegir Excel normal o a detalle → generar Excel + manifiesto + auditoría → reimportar / consolidar / reportar.

---

# 2. DOCUMENTACIÓN OBLIGATORIA

Antes de programar, leé y contrastá:

### Documento rector

`docs/DISEÑO Y BD CONEXION.md`

Contiene las 15 secciones del plan, las 20 condiciones de aceptación y las restricciones. Es la referencia principal; no lo reemplaces por interpretaciones propias.

### Reporte del agente anterior

`docs/REPORTE_CAMBIOS_PLAN_DISENO_BD.md`

Extraé de él:

- Qué módulos se crearon y qué archivos se modificaron.
- Qué se declaró implementado.
- Qué se declaró pendiente.
- La advertencia explícita de que la aceptación integral no estaba cumplida.
- El resultado de pruebas reportado: **65 pruebas aprobadas**, sin cobertura de los módulos SQL ni de la ventana de revisión.

### Estado y arquitectura

- `docs/ESTADO_PROYECTO_Y_PLAN.md`
- `docs/DOCUMENTACION_TECNICA.md`
- `docs/INFORME_ACTUAL_PROYECTO.md`
- `README.md`
- `docs/MANUAL_USUARIO.md`

### Continuidad anterior (plan de Excel ya cerrado)

`docs/continuacion_plan_excel_2026-11-10.md`

Es la versión previa de `continuacion.md`, correspondiente al plan de modos de generación de Excel, respaldos y copia de revisión. Se conservó como archivo separado al escribir este documento. Sus requisitos siguen vigentes y están integrados en las fases 3 y 7.

**Regla:** no inventes estados de avance. Todo lo que afirmes debe poder señalarse en el código o en una ejecución real.

---

# 3. ESTADO REAL DE PARTIDA

## 3.1 Lo que ya existe y es reutilizable

| Componente | Archivo | Estado observado |
|---|---|---|
| Configuración SQL y 18 empresas permitidas | `config/sqlserver.py` | Implementado; contraseña protegida con DPAPI en Windows; guardado en `config/sqlserver.json` |
| Conexión ODBC y clasificación de errores | `database/connection.py` | Implementado; no probado contra EXACTUS |
| Consultas de solo lectura y búsqueda por lotes | `database/queries.py`, `database/repository.py` | Implementado; identificadores de esquema limitados a la lista permitida |
| Normalización de nombres | `database/normalizador.py`, `core/normalizador.py` | Implementado |
| Resolución de coincidencias, conflictos y protección manual | `database/resolucion.py`, `core/procedencia.py` | Implementado; estados `AMBIGUO`, `SIN_COINCIDENCIAS`, `SIN_DEPARTAMENTO`, `CONFLICTO`, `MANUAL_PROTEGIDO`, `VALIDADO` |
| Sincronización y caché | `database/synchronizer.py` | Implementado; respaldo de caché con retención, detección de departamentos modificados |
| Persistencia de revisiones | `core/revision.py` | Implementado; guardar, cargar, reabrir, historial, validación masiva, listado |
| Ventana de validación | `ventana_validacion.py` | **Construida y completa en sí misma**, pero nunca instanciada por la aplicación |
| Escritura de Excel normal / detalle / legado | `core/procesado.py` | Implementado en la capa de datos |
| Integración de resolución en el ETL | `core/procesador.py` | Implementada: consulta por lotes, conflictos, manifiesto, copia de revisión |
| Modos de generación en configuración | `config/configuracion.py` | Implementado: `MODO_NORMAL`, `MODO_DETALLE`, `MODO_LEGACY` |
| Panel SQL dentro de Configuración | `interfaz.py` (`VentanaConfiguracion`) | Implementado: servidor, puerto, base, autenticación, usuario, contraseña, driver, timeout, lote, probar, guardar, sincronizar, desconectar, indicadores |
| Tema visual y componentes | `ui_tema.py`, `panel_principal.py` | Implementado parcialmente; falta verificación visual en las resoluciones exigidas |
| Suite existente | `tests/test_sistema.py` | **65 casos definidos**; ninguno cubre SQL, resolución, revisión ni los modos normal/detalle |

## 3.2 Brechas confirmadas (lo que falta)

Estas brechas se verificaron en el código, no se deducen del reporte.

### A. La ventana de validación no se abre nunca

- `ventana_validacion.py` define `VentanaValidacion`, con KPIs, buscador, filtro por estado, tabla, menú contextual, acciones Consultar SQL / Asignar área / Revisar coincidencias / Ver detalles, botones de generación completa y parcial, y guardado automático.
- En `interfaz.py`, el atributo `_ventana_validacion` se declara y no se utiliza.
- `core/revision` se importa como `svc_revision` y no se usa.
- `_fin_procesar` muestra un mensaje de finalización y termina; no evalúa `estadisticas.ruta_revision` ni abre la ventana.
- Consecuencia: el flujo exigido en las secciones 7 y 9 del documento rector no existe para el usuario.

### B. La generación a detalle no actualiza el Excel canónico

- `core/procesador.py` resuelve `output/<stem>_procesado_detalle.xlsx` cuando el modo es `detalle` y no se indicó salida.
- `core/procesado.py` escribe **un solo archivo** con cinco hojas en ese destino.
- No se escribe `output/<stem>_procesado.xlsx`.
- Consecuencia: se incumple el requisito de que el modo a detalle también actualice el archivo normal canónico, y reimportación/consolidación pueden quedar apuntando a un archivo distinto del esperado.

### C. No hay decisiones de generación en la interfaz

- `interfaz.py` ejecuta siempre `procesar_archivo(..., modo=cfg.MODO_NORMAL, permitir_pendientes=True, crear_revision=True)`.
- No existe botón ni acción para `MODO_DETALLE`.
- No existe diálogo de confirmación para generar con pendientes.
- No se bloquea la generación completa cuando hay pendientes.
- El botón visible se llama "Procesar y generar el Excel": procesamiento y generación están fusionados, en contra de la separación exigida entre validación y formato de salida.

### D. "Abrir resultado" abre el archivo canónico

- `_abrir_procesado` abre `_ultimo_procesado`, es decir `output/<stem>_procesado.xlsx`.
- Debe abrir la copia de revisión y conservar su manifiesto asociado.
- La copia de revisión se genera, pero no se recuerda ni se expone.

### E. La copia de revisión no cumple el nombre ni la rotación

- `core/procesado.copiar_revision` genera `<stem>_revision_<AAAAMMDD_HHMMSS>.xlsx`.
- El plan exige `output/revision/<stem>_procesado_revision.xlsx` y **reemplazar** la copia anterior.
- El nombre actual acumula copias y no permite localizar una copia estable desde la interfaz.
- El manifiesto sí se copia junto al archivo; eso debe conservarse.

### F. El flujo por consola no respeta las reglas

- `main.ejecutar_consola` llama a `procesar_archivo` sin `modo` ni `permitir_pendientes`; se usa el modo legado y se permite generar con pendientes.
- No existen argumentos `--modo`, `--permitir-pendientes` ni equivalentes.
- No se informa ni se cancela cuando se pide un Excel completo con áreas pendientes.
- No se abre ninguna ventana en consola (correcto), pero el resultado puede incumplir la modalidad solicitada.

### G. Dependencias y entorno

- `requirements.txt` declara `pandas` y `openpyxl`, pero **no declara `pyodbc`**, aunque `database/connection.py` lo requiere para conectar.
- En el entorno auditado (Python 3.14) **no están instalados `pandas` ni `pyodbc`**; la suite no puede ejecutarse tal cual. El reporte anterior declara 65 pruebas aprobadas en otro entorno, pero eso debe reproducirse antes de continuar.
- `config/sqlserver.json` no está en `.gitignore`; contiene el secreto cifrado y no debe versionarse.

### H. Cobertura de pruebas inexistente para lo nuevo

La suite actual prueba esquema, ETL, procesamiento legado, reimportación, consolidación, reportes, solicitantes, servicios e interfaz básica. No hay pruebas de:

- conexión, errores, timeout, cierre, esquema inexistente;
- búsqueda por lotes y aislamiento de fallos por empresa;
- coincidencia única, ambigua, sin coincidencias, sin departamento, inactivo;
- protección de asignaciones manuales;
- persistencia y reanudación de revisiones;
- bloqueo de generación completa con pendientes;
- generación parcial con confirmación;
- Excel normal de una hoja, Excel a detalle de cinco hojas y actualización del canónico;
- respaldos, retención y fallo de respaldo;
- apertura de la copia de revisión.

### I. Documentación desactualizada

`README.md`, `docs/MANUAL_USUARIO.md`, `docs/DOCUMENTACION_TECNICA.md` y `docs/ESTADO_PROYECTO_Y_PLAN.md` no describen la integración SQL, la ventana de validación, los modos de generación ni los pendientes reales.

### J. Alertas de sincronización

`database/synchronizer.py` detecta y cuenta departamentos modificados y guarda el detalle en el resumen de sincronización, pero no genera una alerta persistente para revisión ni la cruza con las asignaciones manuales del maestro local. El plan lo exige en la sección 5.2.

---

# 4. REGLAS DE TRABAJO

1. No reescribas módulos que ya funcionan; intégralos.
2. No elimines funcionalidades existentes ni el modo legado.
3. No modifiques el esquema oficial de 11 columnas ni su orden.
4. No modifiques nunca el Excel original de `input/`.
5. No ejecutes `INSERT`, `UPDATE` ni `DELETE` contra la base corporativa.
6. No inventes nombres de columnas, claves ni relaciones de EXACTUS.
7. No interpoles entradas del usuario como identificadores SQL.
8. No guardes contraseñas en claro ni las registres en logs.
9. No marques una prueba como aprobada sin ejecutar el comando.
10. No declares implementada una función solo porque el código "parece" correcto.
11. No dejes botones decorativos ni acciones sin efecto.
12. No cambies la lógica de negocio para acomodar el diseño visual.
13. Hacé cambios pequeños, controlados y verificables, con una ejecución de pruebas después de cada fase.
14. Si encontrás una contradicción entre el reporte anterior y el plan, resolvé a favor de la integridad de datos y dejalo documentado.
15. `docs/` está en `.gitignore`: los documentos y capturas deben entregarse como archivos físicos, no cuentan como "versionados".

---

# 5. FASE 1 — ENTORNO Y LÍNEA BASE

## Objetivo

Tener una línea base reproducible antes de tocar código.

## Tareas

1. Instalar en el entorno de trabajo las dependencias reales del proyecto:
   - `pandas`, `openpyxl`;
   - `pyodbc` y un controlador ODBC de SQL Server compatible.
2. Agregar `pyodbc>=5.0` a `requirements.txt`, con una nota de que el controlador ODBC del sistema se instala aparte.
3. Agregar `config/sqlserver.json` y `config/sqlserver.json.*` a `.gitignore`.
4. Ejecutar y guardar la salida literal de:
   - `python -m unittest discover -s tests -v`
   - `python validar_septiembre.py`
   - `python auditar_sistema.py`
5. Registrar en el informe: versión de Python, dependencias instaladas, cantidad de pruebas ejecutadas, aprobadas y fallidas.

## Criterio de cierre

Los 65 casos existentes vuelven a ejecutarse en este entorno, sin fallos nuevos, y la salida queda anexada al informe final. Si algún caso falla por el entorno y no por el código, debe demostrarse y corregirse la causa, no omitirse.

---

# 6. FASE 2 — CONECTAR LA VENTANA DE VALIDACIÓN

Es la brecha de mayor impacto funcional. La ventana ya está construida; falta el cableado.

## 6.1 Apertura desde el procesamiento

En `interfaz.py`:

1. `_fin_procesar` debe leer `estadisticas.ruta_revision`.
2. Si existe, cargar la revisión con `svc_revision.Revision.cargar(...)` y abrir `VentanaValidacion` como `Toplevel` hijo de la ventana principal.
3. Si no existe (sin pendientes ni conflictos), informar que la validación está completa y dejar continuar con la generación, sin abrir ventanas innecesarias.
4. Evitar ventanas duplicadas: si ya hay una abierta para la misma revisión, enfocarla en lugar de crear otra.
5. No abrir la ventana en modo consola.

## 6.2 Callback de asignación

`VentanaValidacion` recibe `al_asignar(usuario, area, origen)`. Al conectarlo:

1. Actualizar el maestro local `USUARIO → ÁREA` por la vía existente (`core.asignaciones` / `core.maestro`), sin escribir directamente el Excel del maestro.
2. Registrar la procedencia `MANUAL` mediante `core.procedencia`.
3. Refrescar en la ventana principal: pendientes, maestro, panel, preview de solicitantes.
4. No sobrescribir una asignación manual previa con datos de SQL Server.
5. No cerrar la ventana ni perder la revisión si la actualización falla; informar el error.

## 6.3 Callback de generación

`VentanaValidacion` ya expone `al_generar(modo, permitir_pendientes)` y ya bloquea el modo completo cuando hay pendientes. Al conectarlo:

1. Resolver el área validada de cada registro desde la revisión persistida antes de escribir.
2. Ejecutar la generación respetando `modo` y `permitir_pendientes`.
3. Si es parcial: registrar en manifiesto y auditoría la cantidad y la lista de usuarios sin área; no inventar áreas.
4. Si es completa: verificar que no queden pendientes ni conflictos de identidad sin resolver.
5. Refrescar el panel principal, la tabla de pendientes y el estado persistente.
6. Mantener la ventana abierta hasta que la generación termine correctamente; si falla, no destruirla.

## 6.4 Reanudación

1. Añadir en "Usuarios y áreas" la acción **Reanudar revisión**.
2. Listar revisiones con `svc_revision.listar_revisiones`, mostrando archivo original, fecha, pendientes y estado.
3. Abrir la seleccionada en `VentanaValidacion`.
4. Reabrir una revisión no debe duplicar registros ni perder correcciones ya guardadas.
5. La ventana debe guardar al cerrar con la X, con Escape y con "Guardar y cerrar".

## 6.5 Estados y mensajes

La ventana debe mostrar y respetar los estados del plan: área vacía, ambiguo, sin departamento, en revisión, sin coincidencias, conflicto, asignación manual protegida, validado. Los filtros deben cubrirlos todos y los KPIs deben recalcularse tras cada acción.

## Criterio de cierre

- Procesar un archivo con pendientes abre la ventana automáticamente.
- Procesar un archivo sin pendientes no la abre.
- Asignar un área desde la ventana actualiza maestro, revisión y panel principal.
- Cerrar y reabrir la aplicación permite continuar la misma revisión.
- Ninguna acción deja la interfaz bloqueada ni pierde datos.

---

# 7. FASE 3 — GENERACIÓN DE EXCEL, PENDIENTES Y COPIAS

## 7.1 Separar validación de generación

En "Cargar y procesar":

1. Mantener la acción de procesar/validar, que produce el Excel canónico y la revisión.
2. Añadir la elección explícita de formato: **Excel normal** y **Excel a detalle**, después de la validación.
3. No multiplicar botones sin función: cada botón debe ejecutar un modo real y mostrar el resultado.
4. Habilitar "Generar Excel completo" solo cuando no haya pendientes ni conflictos sin resolver.
5. Añadir "Generar Excel con pendientes" con advertencia, cantidad de pendientes y confirmación explícita.

## 7.2 Corregir el modo a detalle

En `core/procesador.py` y `core/procesado.py`:

1. En `MODO_DETALLE`, escribir primero el archivo canónico `output/<stem>_procesado.xlsx` con una sola hoja `Consulta` y sus 11 columnas.
2. Luego escribir `output/<stem>_procesado_detalle.xlsx` con las cinco hojas: `Consulta`, `Observaciones`, `Resumen`, `Control`, `Origen_Adicional`.
3. Cada archivo debe tener su manifiesto propio y coherente con su ruta.
4. El canónico debe poder usarse para reimportación y consolidación sin depender del detalle.
5. Si falla la escritura del detalle, el canónico no debe quedar en estado inconsistente: escribir en temporal, verificar y reemplazar de forma atómica; informar el error.
6. No duplicar la lógica de escritura: reutilizar `escribir_procesado` con el modo correspondiente.

## 7.3 Copia de revisión

1. Cambiar el nombre a `output/revision/<stem>_procesado_revision.xlsx`.
2. Reemplazar la copia anterior al volver a solicitarla, conservando el manifiesto asociado.
3. Si el archivo está bloqueado por Excel, informar y no sobrescribir a medias.
4. Registrar en el manifiesto de la copia el identificador de ejecución y el archivo canónico de origen.

## 7.4 "Abrir resultado"

1. Localizar el canónico.
2. Generar o reutilizar la copia de revisión de forma controlada.
3. Abrir siempre la copia de revisión, nunca el canónico.
4. Mantener el manifiesto junto a la copia.
5. Si no hay copia posible, informar el motivo; no abrir el canónico como sustituto silencioso.

## 7.5 Respaldos

Verificar y probar:

- respaldo antes de sobrescribir;
- retención de las últimas 10 copias por archivo lógico;
- fallo de respaldo cancela la sobrescritura;
- archivo bloqueado produce un mensaje comprensible;
- respaldos de otros procesamientos no se eliminan;
- el Excel original permanece intacto (hash antes/después).

## Criterio de cierre

- Excel normal: una hoja `Consulta`, 11 columnas, sin hojas técnicas.
- Excel a detalle: cinco hojas y actualización simultánea del canónico.
- Generación completa bloqueada con pendientes; parcial solo con confirmación y registro.
- "Abrir resultado" abre la copia de revisión con su manifiesto.
- Respaldos y retención demostrados con una prueba real.

---

# 8. FASE 4 — FLUJO POR CONSOLA

## Tareas

1. Añadir argumentos explícitos:
   - `--modo {normal,detalle,legacy}`, con valor por defecto documentado;
   - `--permitir-pendientes` (por defecto, deshabilitado);
   - conservar `--salida`, `--sobrescribir`, `--reporte`, `--consolidar`, `--usuario`.
2. Si se solicita generación completa y hay pendientes: informar por consola, no escribir el resultado incompleto y terminar con código de error controlado.
3. Si se permite parcial: escribir, registrar en manifiesto y auditoría, e imprimir la cantidad y los usuarios sin área.
4. No abrir ninguna ventana en consola, ni siquiera cuando falten áreas.
5. Decidir y documentar el comportamiento por defecto. Recomendación: mantener `legacy` solo cuando se solicite explícitamente, y usar `normal` como flujo por defecto, verificando antes qué pruebas y consumidores dependen del formato legado.
6. Mantener el resumen de consola y agregar modo, parcialidad y rutas de copia de revisión.

## Criterio de cierre

- `python main.py archivo.xlsx` con pendientes no genera un "completo" silencioso.
- `--modo detalle` produce canónico y detalle.
- `--permitir-pendientes` exige intención explícita.
- Los comandos históricos siguen documentados y probados.

---

# 9. FASE 5 — SQL SERVER: VERIFICACIÓN REAL Y ROBUSTEZ

## 9.1 Verificar la estructura EXACTUS

Antes de dar por válidos los JOIN:

1. Confirmar si `[EXACTUS].[<ESQUEMA>].[EMPLEADO]`, `[PUESTO]` y `[DEPARTAMENTO]` existen para las 18 empresas.
2. Confirmar las columnas reales: `EMPLEADO`, `NOMBRE`, `ESTADO_EMPLEADO`, `ACTIVO`, `DEPARTAMENTO`, `PUESTO`, `PRIMER_APELLIDO`, `SEGUNDO_APELLIDO`, `NOMBRE_PILA`, `E_MAIL`, `U_CORREO_CORP`, `FUENTE`.
3. Confirmar las columnas de descripción de los catálogos.
4. Confirmar la relación entre `EMPLEADO.DEPARTAMENTO` y `DEPARTAMENTO`.
5. Si algún dato no puede verificarse, no lo inventes: dejalo configurable y documentá el bloqueo en el informe final.

## 9.2 Comportamiento obligatorio

1. Una empresa que falla no bloquea a las demás; se registra el error y se continúa.
2. Timeout, credenciales incorrectas, servidor no disponible y esquema inexistente deben clasificarse y mostrarse en lenguaje comprensible.
3. Las conexiones y cursores se cierran siempre.
4. La búsqueda es por lotes; nunca una consulta por ticket.
5. Solo `SELECT`.
6. Los valores van parametrizados; los esquemas salen de la lista permitida.
7. Sin SQL Server disponible: el ETL continúa con la caché local, los usuarios sin área quedan pendientes, se muestra advertencia y se puede reintentar.
8. La sincronización manual informa: nuevos, actualizados, departamentos modificados, sin departamento, ambiguos, errores y fecha.
9. Antes de modificar la caché se crea respaldo (ya existe) y no se sobrescriben correcciones manuales.
10. Si el departamento corporativo cambia respecto de una asignación manual, se registra una alerta persistente para revisión y se muestra en la interfaz.

## 9.3 Seguridad

1. No mostrar la contraseña ni incluirla en logs, manifiestos o auditoría.
2. Confirmar que en Windows se usa DPAPI; si se usa el respaldo Base64, advertir explícitamente que no es cifrado real y no presentarlo como almacenamiento protegido.
3. No versionar `config/sqlserver.json`.
4. Validar parámetros antes de conectar (puerto, timeout, lote, driver, autenticación).

## 9.4 Criterio de cierre

- Prueba de conexión real contra EXACTUS (o contra una base de pruebas equivalente) documentada.
- Búsqueda por lotes devuelve el departamento correcto para al menos un empleado por empresa verificada.
- Fallos por empresa quedan aislados y visibles.
- Sincronización manual con contadores correctos y respaldo previo.

---

# 10. FASE 6 — PRUEBAS OBLIGATORIAS

Conservar los 65 casos existentes y agregar módulos nuevos. Usar `unittest`, `mock` y archivos temporales; nunca la base corporativa real.

## 10.1 SQL (con `pyodbc` simulado)

- conexión correcta;
- servidor no disponible;
- credenciales incorrectas;
- timeout;
- cierre de conexión y de cursor;
- esquema inexistente;
- catálogo sin departamento;
- búsqueda por lotes;
- error de una empresa sin bloquear las demás;
- validación de esquemas fuera de la lista permitida.

## 10.2 Resolución

- coincidencia única;
- coincidencia múltiple;
- sin coincidencias;
- empleado sin departamento;
- empleado inactivo;
- tildes, espacios duplicados y puntuación;
- cambio de departamento;
- protección de asignación manual (conflicto, no sobrescritura);
- procedencia registrada (`SQL_SERVER`, `MANUAL`, `LEGACY`).

## 10.3 Revisión y validación

- no abrir ventana si todo está validado;
- abrir si hay áreas vacías, ambigüedades o conflictos;
- resolver un pendiente y una ambigüedad;
- asignación manual;
- persistencia del progreso;
- recuperación tras cerrar y reabrir;
- bloqueo de generación completa con pendientes;
- generación parcial con confirmación;
- registro en auditoría.

## 10.4 Excel

- 11 columnas exactas y en orden;
- hoja `Consulta`;
- normal de una sola hoja;
- detalle de cinco hojas;
- actualización del canónico al generar detalle;
- manifiesto por archivo;
- compatibilidad con `Tickets`;
- reimportación y consolidación de ambos formatos;
- respaldo antes de sobrescribir;
- retención de 10 copias;
- fallo de respaldo sin sobrescritura;
- archivo bloqueado;
- integridad del original (hash).

## 10.5 Interfaz

- las siete secciones abren;
- navegación funcional;
- redimensionamiento en 1366×768, 1440×900, 1600×900, 1920×1080 y ventana reducida;
- tablas con desplazamiento;
- botones con efecto real;
- ventanas secundarias y diálogos;
- ninguna operación pesada bloquea el hilo principal.

## Ejecución

```
python -m unittest discover -s tests -v
python validar_septiembre.py
python auditar_sistema.py
```

Reportar cantidad ejecutada, aprobada y fallida. Si algo falla, corregir y repetir. No declarar éxito sin salida real.

---

# 11. FASE 7 — INTERFAZ Y RESPONSIVE

1. Revisar visualmente cada sección y la ventana de validación en las cinco resoluciones del plan.
2. Verificar que no se corten botones, textos ni tablas; agregar scroll horizontal/vertical donde corresponda.
3. Confirmar que los KPIs de la ventana de validación se recalculan y que los estados tienen color y texto coherentes.
4. Confirmar que la ventana de configuración SQL muestra estado real de conexión, última prueba, empresas activas, última sincronización, empleados en caché y errores.
5. Sustituir `len(EMPRESAS)` por las empresas realmente activas en la configuración cuando corresponda.
6. Revisar que "Sincronizar ahora" y "Probar conexión" se ejecuten fuera del hilo principal y no congelen la interfaz.
7. No introducir dependencias gráficas nuevas.
8. Guardar capturas como evidencia en el informe final.

---

# 12. FASE 8 — DOCUMENTACIÓN Y ENTREGA

Actualizar:

- `README.md`: requisitos, `pyodbc`, configuración SQL, flujo de validación, modos de generación, comandos.
- `docs/MANUAL_USUARIO.md`: cómo conectar, sincronizar, validar áreas, reanudar una revisión, generar completo/parcial y abrir resultados.
- `docs/DOCUMENTACION_TECNICA.md`: arquitectura de `database/`, resolución, procedencia, revisiones, manifiestos, respaldos y seguridad.
- `docs/ESTADO_PROYECTO_Y_PLAN.md`: estado real por funcionalidad y pendientes verificados.

Generar `docs/REPORTE_FINAL_IMPLEMENTACION.md` con:

1. Resumen ejecutivo y estado final.
2. Continuidad: qué estaba bien, qué estaba incompleto y qué se corrigió.
3. Archivos creados, modificados y no modificados, con justificación.
4. Funcionalidades terminadas y cómo se verificaron.
5. Pruebas ejecutadas: comandos, cantidades, resultados literales.
6. Validación de integridad: Excel, manifiestos, respaldos, maestro histórico, original intacto.
7. Configuración necesaria para SQL Server (servidor, puerto, base, autenticación, driver, esquemas verificados).
8. Limitaciones y datos que requieren confirmación del área de TI.
9. Capturas de las pantallas principales.
10. Instrucciones para ejecutar y probar.

No declares cumplida una condición de aceptación que no puedas demostrar.

---

# 13. MATRIZ DE ACEPTACIÓN

| # | Condición del plan | Evidencia exigida |
|---|---|---|
| 1 | Identidad visual coherente | Capturas de las 7 secciones y de la ventana de validación |
| 2 | Interfaz responsiva | Capturas en 1366×768, 1440×900, 1600×900, 1920×1080 y ventana reducida |
| 3 | Las 7 secciones funcionan | Recorrido manual + pruebas de interfaz |
| 4 | SQL configurable desde Configuración | Captura del panel y archivo `config/sqlserver.json` sin secreto en claro |
| 5 | Consulta de las 18 empresas | Registro de sincronización con empresas consultadas y errores por empresa |
| 6 | Sincronización automática y manual | Ejecución real de ambas, con contadores |
| 7 | Sin asignaciones ambiguas automáticas | Pruebas de resolución + revisión en ventana |
| 8 | Áreas desde `DEPARTAMENTO.DESCRIPCION` | Consulta verificada y valor escrito en Excel de prueba |
| 9 | Ventana independiente funcional | Captura + prueba de apertura, asignación y cierre |
| 10 | Guardar y reanudar revisión | Cierre de app y reapertura con el mismo estado |
| 11 | No generar completo con pendientes | Prueba automatizada + intento manual bloqueado |
| 12 | Generar parcial con confirmación | Prueba + manifiesto que declare parcialidad y usuarios sin área |
| 13 | Modos normal y detalle | Archivos generados con 1 y 5 hojas, canónico actualizado |
| 14 | Esquema de 11 columnas | Prueba de columnas y orden |
| 15 | Reimportación y consolidación | Pruebas ejecutadas sobre ambos formatos |
| 16 | Manifiestos y auditoría íntegros | Verificación de manifiestos y CSV de auditoría |
| 17 | Original nunca modificado | Hash antes/después |
| 18 | Pruebas actuales y nuevas aprobadas | Salida literal de la suite |
| 19 | Sin botones sin implementar | Revisión de cada acción de la interfaz |
| 20 | Documentación actualizada | Documentos modificados y reporte final |

---

# 14. CRITERIOS DE BLOQUEO

Si encontrás alguna de estas situaciones, detené esa parte y documentá el bloqueo en el informe final:

1. No se puede verificar el esquema real de EXACTUS (columnas, relaciones o esquemas por empresa).
2. No hay controlador ODBC ni forma de probar conexión en el entorno.
3. Un requisito del plan contradice una regla de integridad de datos.
4. Una corrección exige modificar el esquema oficial o el Excel original.
5. Una prueba no puede ejecutarse por el entorno y no hay forma de aislar la causa.

En esos casos: implementá lo configurable, no inventes datos, no simules resultados como reales y explicá exactamente qué falta y quién debe confirmarlo.

---

# 15. ORDEN DE TRABAJO RECOMENDADO

1. Fase 1: entorno, dependencias y línea base de pruebas.
2. Fase 3.2 y 3.3: corregir el modo a detalle, la copia de revisión y "Abrir resultado" (backend verificable con pruebas).
3. Fase 2: conectar la ventana de validación con callbacks reales.
4. Fase 3.1: decidir y construir la generación completa/parcial en la interfaz.
5. Fase 4: alinear consola.
6. Fase 5: verificar SQL real y alertas de sincronización.
7. Fase 6: completar la suite y ejecutarla.
8. Fase 7: revisión visual y responsiva.
9. Fase 8: documentación y reporte final.

Este orden permite probar la capa de datos antes de cablear la interfaz y reduce el riesgo de arrastrar errores a la ventana de validación.

---

# 16. INSTRUCCIÓN FINAL

No empieces a programar sin haber ejecutado la línea base y sin haber leído el documento rector y el reporte anterior.

No te limites a describir lo que falta: **implementá y verificá**.

Después de cada fase:

- ejecutá las pruebas correspondientes;
- corregí los errores;
- comprobá que el flujo anterior sigue funcionando;
- registrá el resultado real.

Al terminar, entregá `docs/REPORTE_FINAL_IMPLEMENTACION.md` con evidencia verificable y un veredicto honesto: el sistema está listo para usarse, o sigue teniendo limitaciones concretas y enumeradas.

**Prioridad máxima: integridad de datos, compatibilidad, seguridad, trazabilidad y una experiencia de validación que no pierda el trabajo del usuario.**
