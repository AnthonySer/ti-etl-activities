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
Seleccionar Excel → Validar y previsualizar → Procesar
→ Asignar áreas pendientes (si existen)
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

Si la estructura no es válida, no se procesa el archivo.

### 4.2 Procesar

1. Revise la vista previa.
2. Pulse **Procesar archivo**.
3. Espere el mensaje de finalización. La barra de progreso y los mensajes
   indican la etapa actual; la interfaz no se bloquea.

Resultado:

- `output/<nombre>_procesado.xlsx`
- `output/<nombre>_procesado.manifest.json`

Si la salida ya existe, se genera una nueva versión con marca de tiempo.

### 4.3 Hojas del Excel procesado

| Hoja | Contenido |
|------|-----------|
| `Tickets` | Detalle con exactamente las 11 columnas oficiales |
| `Observaciones` | Fila, campo, motivo, valor y acción requerida |
| `Resumen` | Totales y distribución del procesamiento |
| `Control` | Estado interno, identificadores técnicos y huellas |
| `Origen_Adicional` | Columnas extra del origen (por ejemplo `EMPRESA`) |

La hoja `Tickets` no contiene estados técnicos ni identificadores internos.

### 4.4 Estados internos

| Estado | Significado | Acción |
|--------|-------------|--------|
| `VÁLIDO` | Registro completo y consistente | Ninguna |
| `OBSERVADO` | Tiene valores atípicos o incompletos no críticos | Revisar la hoja `Observaciones` |
| `PENDIENTE` | El solicitante no tiene área asignada | Asignar el área |
| `DUPLICADO` | Coincide con otro registro | Revisar si es un duplicado real |
| `ERROR` | Falta un dato imprescindible o es inválido | Corregir antes de consolidar |

Los registros observados y pendientes **no se descartan**.

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
4. No cambie el nombre de la hoja `Tickets`.
5. Guarde el archivo (puede usar el mismo nombre o uno nuevo).

Las hojas auxiliares no son necesarias para la reimportación; el sistema lee la
hoja `Tickets`.

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
- **Abrir carpeta de copias:** abre `data/backups/`.

---

## 9. Reportes

1. Vaya a **Reportes**.
2. Elija el origen:
   - Último procesado.
   - Maestro histórico.
   - Otro archivo.
3. Pulse **Generar reporte**.
4. Pulse **Abrir reporte** para ver el Excel o el Markdown.

Indicadores incluidos:

- Total de tickets y horas.
- Promedio y mediana de horas por ticket.
- Tickets y horas por día hábil.
- Responsables, solicitantes y áreas.
- Capacidad nominal e imputación de horas.
- Distribución por atención, requerimiento, subtipo, proyecto y actividad.
- Evolución semanal.
- Carga por responsable.
- Recurrencia de detalles idénticos.

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

## 11. Preguntas frecuentes

### ¿Puedo editar el maestro directamente en Excel?

No. El maestro tiene una hoja `CONTROL` con huellas que detectan ediciones
externas. Use la interfaz para consolidar, reimportar o restaurar.

### ¿Qué pasa si un usuario no tiene área?

El registro queda `PENDIENTE` y el área se deja vacía. No se inventa un área.
Puede asignarla y volver a procesar.

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
- Reportes: `reportes/`
- Maestros y auditoría: `data/`
- Copias de seguridad: `data/backups/`
- Log técnico: `logs/sistema.log`
