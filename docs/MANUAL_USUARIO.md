# Manual de usuario

Sistema Integral de Tickets TI - INKACROPS

Este manual describe la operación diaria desde la interfaz gráfica. No requiere
conocimientos de programación ni editar archivos internos.

---

## 1. Abrir la aplicación

1. Abra PowerShell en la carpeta `script automatizador`.
2. Ejecute:

```powershell
python main.py
```

La ventana principal muestra el estado del sistema y siete pestañas:

1. **Panel principal**
2. **Cargar y procesar**
3. **Usuarios y áreas**
4. **Reimportar correcciones**
5. **Maestro histórico**
6. **Reportes**
7. **Auditoría**

---

## 2. Flujo completo recomendado

```text
Seleccionar Excel → Validar y previsualizar → Procesar y validar
→ Resolver pendientes en la ventana de validación (si existen)
→ Generar Excel normal o a detalle
→ Revisar/corregir el Excel procesado
→ Reimportar correcciones
→ Consolidar en el maestro histórico
→ Generar reportes
```

El archivo original de `input/` **nunca se modifica**. Todo el trabajo se realiza
sobre copias procesadas en `output/`.

---

## 3. Panel principal

Muestra:

- Estado general de la aplicación.
- Último procesamiento: archivo, hoja, periodo, ejecución y fecha.
- Registros leídos, válidos, observados, pendientes, duplicados y errores.
- Horas registradas.
- Estado del maestro histórico y cantidad de tickets.
- Últimos mensajes del sistema.

Botones disponibles:

- **Cargar archivo Excel:** lleva a la pestaña de procesamiento.
- **Abrir Excel procesado:** abre el último resultado.
- **Abrir carpeta de salida:** abre `output/`.
- **Actualizar estado:** relee el estado persistente.

---

## 4. Cargar y procesar un archivo

### 4.1 Seleccionar el archivo

1. Vaya a **Cargar y procesar**.
2. Pulse **Examinar…** y seleccione un archivo `.xlsx`.
3. Si el libro tiene varias hojas, elija la hoja correcta en el desplegable.
4. Seleccione el formato de fecha si el origen usa fechas ambiguas
   (`DD/MM/YYYY` o `MM/DD/YYYY`).
5. Pulse **Validar y previsualizar**.

El sistema:

- Valida la extensión y la legibilidad del archivo.
- Detecta la hoja y la fila de encabezado.
- Reconoce las 11 columnas oficiales y sus alias.
- Muestra una vista previa de los primeros registros.
- Informa si falta una columna o si hay columnas duplicadas con datos.
- Al elegir otro Excel limpia la hoja, la vista previa y la validación del libro
  anterior. Si cancela el diálogo, conserva la selección actual.

Si la estructura no es válida, no se procesa el archivo.

### 4.2 Procesar y validar

1. Revise la vista previa.
2. Pulse **Procesar y validar**.
3. Espere el mensaje de finalización. La barra de progreso y los mensajes
   indican la etapa actual; la interfaz no se bloquea.

Resultado:

- `output/<nombre>_procesado.xlsx` (archivo canónico, hoja `Consulta`)
- `output/<nombre>_procesado.manifest.json`
- `output/revision/<nombre>_procesado_revision.xlsx` (copia de revisión)

El archivo canónico se reemplaza de forma atómica y siempre se conserva una copia
en `data/backups/` antes de sobrescribirlo. El original de `input/` no se toca.

Si existen registros sin área o conflictos de área, al finalizar se abre
automáticamente la **ventana de validación de usuarios y áreas**. El resto del
flujo se explica en la sección 4.5.

Al finalizar, el formulario de carga queda limpio para seleccionar otro archivo;
el resultado del último procesamiento sigue disponible en el panel principal.

### 4.3 Hojas del Excel procesado

| Hoja | Contenido |
|------|-----------|
| `Consulta` | Detalle con exactamente las 11 columnas oficiales |
| `Observaciones` | Fila, campo, motivo, valor y acción requerida |
| `Resumen` | Totales y distribución del procesamiento |
| `Control` | Estado interno, identificadores técnicos y huellas |
| `Origen_Adicional` | Columnas extra del origen (por ejemplo `EMPRESA`) |

La generación **normal** produce un libro de una sola hoja (`Consulta`). La
generación **a detalle** produce, además, `<nombre>_procesado_detalle.xlsx` con
las cinco hojas de la tabla anterior y actualiza el archivo canónico. En el modo
heredado la hoja principal se llama `Tickets`.

La hoja de detalle no contiene estados técnicos ni identificadores internos.

### 4.4 Estados internos

| Estado | Significado | Acción |
|--------|-------------|--------|
| `VÁLIDO` | Registro completo y consistente | Ninguna |
| `OBSERVADO` | Tiene valores atípicos o incompletos no críticos | Revisar la hoja `Observaciones` |
| `PENDIENTE` | El solicitante no tiene área asignada | Asignar el área |
| `DUPLICADO` | Coincide con otro registro | Revisar si es un duplicado real |
| `ERROR` | Falta un dato imprescindible o es inválido | Corregir antes de consolidar |

Los registros observados y pendientes **no se descartan**.

### 4.5 Ventana de validación de usuarios y áreas

La ventana se abre automáticamente cuando el procesamiento deja registros por
revisar. También puede abrirse con **Reanudar revisión** desde la pestaña
**Cargar y procesar**.

La ventana muestra tarjetas con el total de registros, áreas validadas y
pendientes, filtros por estado y búsqueda por nombre. Acciones principales:

- **Asignar área:** escribe el área en el maestro persistente y en la revisión.
- **Consultar SQL:** busca al empleado en la caché corporativa o en SQL Server
  (solo lectura) y propone áreas.
- **Revisar coincidencias:** muestra candidatos cuando hay homónimos.
- **Validar seleccionados:** marca registros que ya tienen área.
- **Generar Excel completo:** solo se habilita cuando no quedan pendientes.
- **Generar Excel con pendientes:** genera un archivo parcial y lo registra como
  tal en el manifiesto.

La ventana se cierra únicamente cuando la generación termina correctamente. Si
falla, la revisión queda disponible para reintentar.

### 4.6 Generar el Excel definitivo

En la pestaña **Cargar y procesar** dispone de dos botones:

- **Generar Excel normal:** libro de una hoja `Consulta` con las 11 columnas.
- **Generar Excel a detalle:** actualiza el canónico y crea además el libro de
  cinco hojas.

Si hay pendientes o conflictos, el sistema pide confirmación y, si acepta,
genera un archivo **parcial**. Sin confirmación, la generación se cancela y no
sobrescribe resultados anteriores. Toda generación conserva respaldo y deja su
manifiesto JSON con el detalle de pendientes y resoluciones.

**Abrir resultado** abre siempre una copia de revisión (`output/revision/`), de
modo que el archivo canónico no queda bloqueado mientras se revisa en Excel.

---

## 5. Asignar áreas a usuarios pendientes

1. Vaya a **Usuarios y áreas**.
2. En la tabla inferior aparecen los usuarios pendientes del último
   procesamiento, con cantidad de filas, área que traía el archivo y una
   sugerencia.
3. Seleccione un usuario pendiente: se copia al formulario.
4. Escriba o seleccione el **Área**.
5. Pulse **Sugerir coincidencias** para ver usuarios parecidos ya registrados.
6. Pulse **Guardar asignación**.

La asignación queda persistida en `data/maestro_areas.xlsx`. En el siguiente
procesamiento ese usuario ya no aparecerá como pendiente.

También puede:

- Buscar usuarios por nombre.
- Seleccionar un usuario existente y modificar su área.
- Consultar todas las áreas conocidas en el desplegable.

> El sistema no inventa áreas ni fusiona identidades automáticamente. Las
> coincidencias solo se proponen.

---

## 6. Corregir el Excel procesado

1. Abra el archivo procesado desde **Cargar y procesar → Abrir resultado**.
2. Corrija en Excel únicamente los valores de las 11 columnas.
3. No agregue, elimine ni reordene columnas.
4. No cambie el nombre de la hoja `Consulta` (o `Tickets` si usa el modo
   heredado).
5. Guarde el archivo (puede usar el mismo nombre o uno nuevo).

Las hojas auxiliares no son necesarias para la reimportación; el sistema lee la
hoja canónica.

---

## 7. Reimportar correcciones

1. Vaya a **Reimportar correcciones**.
2. Pulse **Examinar…** y seleccione el Excel corregido.
3. Pulse **Analizar diferencias**.
4. Revise la tabla de cambios:

| Tipo | Significado |
|------|-------------|
| `NUEVO` | Fila que no existía en el procesamiento original |
| `SIN_CAMBIOS` | Fila idéntica |
| `MODIFICADO` | Fila existente con campos cambiados |
| `DUPLICADO` | Fila repetida dentro del archivo corregido |
| `AMBIGUO` | No se puede determinar con certeza a qué fila original corresponde |
| `AUSENTE` | Fila del procesamiento original que no aparece en el corregido |
| `ERROR` | Fila sin solicitante u otro dato imprescindible |

5. Seleccione cada cambio para ver el detalle campo a campo.
6. Si desea aplicar filas marcadas como duplicadas, marque la casilla
   **Permitir aplicar filas marcadas como duplicadas**.
7. Pulse **Aplicar cambios confirmados** y confirme.

Reglas importantes:

- Las filas `AMBIGUO` bloquean la aplicación hasta resolverlas manualmente.
- Las filas `AUSENTE` **no eliminan** registros del maestro.
- La reimportación es idempotente: aplicarla dos veces no duplica tickets.
- Cada cambio campo a campo queda en
  `data/maestro_tickets_historial.csv`.

---

## 8. Consolidar en el maestro histórico

1. Vaya a **Maestro histórico**.
2. Puede consolidar el último procesado con **Consolidar último procesado**, o
   seleccionar otro archivo con **Seleccionar procesado y consolidar…**.
3. Confirme la operación.

El sistema:

- Lee el maestro existente o lo crea si no existe.
- Valida las 11 columnas y la hoja `CONTROL`.
- Agrega tickets nuevos.
- Actualiza tickets existentes sin duplicarlos.
- Conserva los tickets históricos que no aparecen en el archivo actual.
- Crea una copia de seguridad antes de reemplazar el maestro.
- Escribe de forma temporal y atómica; si falla, deja intacta la versión anterior.

Botones adicionales:

- **Abrir maestro:** abre `data/maestro_tickets.xlsx`.
- **Actualizar lista:** refresca las copias disponibles.
- **Restaurar seleccionada:** recupera una copia de seguridad.
- **Abrir carpeta de copias:** abre `output/respaldos/`.

### 8.1 Detección y normalización de solicitantes

En esta pestaña, la tabla **Detección y normalización de solicitantes** permite
auditar los nombres del maestro y revisar candidatos detectados en el último
procesamiento. Muestra el número de tickets por nombre y distingue coincidencias
exactas, posibles coincidencias y nombres sin coincidencia.

- **Unificar con coincidencia:** guarda una equivalencia para futuras revisiones.
- **Mantener separados:** registra que los nombres no deben sugerirse como una
  coincidencia entre sí.
- **Dejar pendiente:** conserva la revisión como pendiente y la registra en la
  auditoría.
- **Revertir equivalencia:** elimina la equivalencia seleccionada.

Las sugerencias no prueban identidad ni fusionan tickets. Los solicitantes
genéricos están protegidos contra unificación. El valor original del maestro y
los identificadores de tickets no se modifican; las equivalencias se guardan en
`data/solicitantes_equivalencias.json`. La detección se actualiza después del
procesamiento, la reimportación y la consolidación, y puede ejecutarse con
**Auditar solicitantes**.

---

## 9. Reportes

1. Vaya a **Reportes**.
2. Elija el origen:
   - Último procesado.
   - Maestro histórico.
   - Otro archivo.
3. Pulse **Generar reporte**.
4. Pulse **Abrir reporte** para ver el Excel o el Markdown.

El Excel generado contiene exactamente cinco hojas, en este orden:

1. `RESUMEN`: horas por Incidencia, Proyecto y Solicitud.
2. `PROYECTOS`: horas por proyecto asignado.
3. `ACTIVIDAD`: tickets por requerimiento, sin incluir proyectos.
4. `AREA`: tickets por área.
5. `DETALLE`: todos los registros y las seis columnas solicitadas.

Las cuatro primeras hojas incluyen un gráfico circular real sin título y una
fila `TOTAL GENERAL`; sus porcentajes se guardan como fracciones y se muestran
con formato porcentual de Excel. Las fechas de `DETALLE` se presentan como
`dd/mm/yyyy`. El Markdown asociado conserva los indicadores generales y las
distribuciones analíticas.

Los reportes distinguen indicadores reales de estimaciones. No calculan SLA ni
tiempos de resolución porque los campos de origen no los contienen.

---

## 10. Auditoría

La pestaña **Auditoría** muestra:

- Historial de ejecuciones.
- Procesamientos, reimportaciones y consolidaciones.
- Asignaciones de áreas.
- Restauraciones de copias.
- Cambios en el maestro.

Botones:

- **Actualizar:** relee el historial.
- **Abrir auditoría CSV:** abre `data/auditoria_ejecuciones.csv`.
- **Abrir carpeta de datos:** abre `data/`.

La auditoría es independiente del Excel principal y no agrega columnas al
detalle oficial.

---

## 11. Conexión SQL Server (solo lectura)

La aplicación puede consultar la base corporativa EXACTUS para obtener el
departamento de cada empleado. La conexión es **opcional** y de **solo lectura**:
solo se ejecutan `SELECT` parametrizados; cualquier sentencia de escritura se
rechaza antes de enviarse.

1. Abra **Configuración → Conexión SQL Server**.
2. Complete servidor, puerto, base de datos y autenticación (Windows o SQL).
3. Marque las empresas autorizadas que desea consultar.
4. Pulse **Probar conexión** y luego **Guardar**.
5. Pulse **Sincronizar ahora** para actualizar la caché local de empleados.

La sincronización **nunca** sobrescribe el maestro `USUARIO → ÁREA`. Si el
departamento corporativo cambió y difiere del área asignada localmente, se
registra una **alerta** que aparece en el resumen de configuración para revisión.

Si `pyodbc` o el controlador ODBC no están disponibles, la aplicación sigue
funcionando con la caché local y muestra un mensaje comprensible.

---

## 12. Preguntas frecuentes

### ¿Puedo editar el maestro directamente en Excel?

No. El maestro tiene una hoja `CONTROL` con huellas que detectan ediciones
externas. Use la interfaz para consolidar, reimportar o restaurar.

### ¿Qué pasa si un usuario no tiene área?

El registro queda `PENDIENTE` y el área se deja vacía. No se inventa un área.
La ventana de validación permite asignarla; mientras existan pendientes, el
sistema no genera el Excel **completo**, salvo que usted confirme explícitamente
una generación **parcial**.

### ¿Se pierden los tickets de meses anteriores?

No. La consolidación conserva los registros históricos aunque no aparezcan en el
archivo del mes actual.

### ¿Qué pasa si me equivoco al reimportar?

La reimportación solo modifica los registros identificados y registra el cambio
campo a campo. Si necesita revertir, restaure una copia desde **Maestro
histórico**.

### ¿Puedo procesar el mismo archivo dos veces?

Sí. El sistema detecta duplicados y la consolidación es idempotente: no se crean
tickets repetidos.

### ¿Dónde están los archivos generados?

- Excel procesados: `output/`
- Copias de revisión: `output/revision/`
- Respaldos automáticos: `output/respaldos/`
- Reportes: `reportes/`
- Maestros y auditoría: `data/`
- Log técnico: `logs/sistema.log`
