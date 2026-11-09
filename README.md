# Sistema Integral de Tickets TI - INKACROPS

Aplicación de escritorio y motor ETL para procesar, clasificar, corregir,
reimportar y consolidar tickets de TI. Mantiene una estructura oficial e
inalterable de **11 columnas**, asigna áreas a partir de una base persistente
`USUARIO → ÁREA`, conserva un maestro histórico y registra toda la operación en
un sistema de auditoría independiente.

La operación habitual se realiza desde la interfaz gráfica (Tkinter). La consola
queda disponible para automatizaciones y diagnósticos.

---

## Inicio rápido

```powershell
cd "script automatizador"
python -m pip install -r requirements.txt
python main.py
```

`python main.py` sin argumentos abre la interfaz gráfica. Para procesar un archivo
directamente:

```powershell
python main.py input\consulta_septiembre_2026.xlsx
```

Flujo completo desde consola (procesar + reporte + consolidar):

```powershell
python main.py input\consulta_septiembre_2026.xlsx --reporte --consolidar
```

Verificación del sistema:

```powershell
python -m unittest discover -s tests -v
python validar_septiembre.py
python auditar_sistema.py
```

---

## Estructura oficial obligatoria

Todos los archivos principales (procesado, corregido, reimportado y maestro)
conservan exactamente estas 11 columnas y en este orden:

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

- La hoja de detalle **no** admite columnas adicionales, identificadores
  internos ni estados técnicos.
- Las columnas adicionales del origen (por ejemplo `EMPRESA`) se conservan en la
  hoja auxiliar `Origen_Adicional`.
- Las observaciones, el resumen y el control se guardan en hojas auxiliares.
- Si dos columnas del origen representan el mismo campo y ambas tienen datos, la
  importación se detiene para revisión manual.
- Si falta una columna obligatoria, se informa exactamente cuál falta.

---

## Funcionalidades principales

- **Carga y validación:** selección de Excel, detección de hoja y encabezado,
  vista previa, validación estructural y confirmación. Al seleccionar un libro
  nuevo se limpian la hoja y la validación anteriores; al terminar se restablece
  el formulario sin borrar los resultados del panel ni los archivos generados.
- **ETL:** normalización de textos, fechas, horas, nulos y usuarios; estados
  internos `VÁLIDO`, `OBSERVADO`, `PENDIENTE`, `DUPLICADO` y `ERROR` fuera del
  detalle oficial.
- **Áreas:** administración persistente de usuarios y áreas, búsqueda,
  sugerencias por similitud y detección de usuarios pendientes.
- **Excel procesado:** 11 columnas, encabezados diferenciados, filtros, formatos,
  panel congelado y hojas auxiliares.
- **Reimportación:** detección de filas nuevas, modificadas, sin cambios,
  duplicadas, ambiguas y ausentes. Las filas ausentes **no se eliminan**.
- **Maestro histórico:** consolidación idempotente, control por `ID_TECNICO` y
  huella, conservación de periodos anteriores y copias de seguridad. Incluye
  auditoría asistida de solicitantes, coincidencias exactas y variantes posibles;
  las equivalencias confirmadas se guardan por separado y nunca modifican los
  nombres de origen ni fusionan tickets automáticamente.
- **Reportes:** generación de Excel con la estructura demandada por el cliente:
  exactamente cinco hojas (`RESUMEN`, `PROYECTOS`, `ACTIVIDAD`, `AREA`,
  `DETALLE`), gráficos circulares reales sin título en las cuatro hojas
  resumidas y detalle sin gráficos ni columnas extra. Las cuatro tablas
  resumidas muestran un `TOTAL GENERAL`, porcentajes almacenados como decimales
  con formato porcentual de Excel y la hoja `DETALLE` presenta fechas como
  `dd/mm/yyyy`. Los totales y porcentajes se recalculan al generar el reporte
  con el conjunto de datos vigente. También se genera el Markdown asociado.
- **Auditoría:** registro de procesamientos, reimportaciones, consolidaciones,
  asignaciones de áreas, cambios campo a campo y restauraciones.
- **Seguridad operativa:** bloqueo exclusivo, escritura temporal, reemplazo
  atómico, rollback y respaldo previo del maestro.

---

## Estructura del proyecto

```text
script automatizador/
├─ main.py                     # GUI sin argumentos; flujo de consola con archivo
├─ interfaz.py                 # Interfaz Tkinter de 7 pestañas
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
│  ├─ solicitantes.py          # Detección y equivalencias asistidas de solicitantes
│  ├─ maestro_tickets.py       # Maestro histórico y hoja CONTROL
│  ├─ consolidacion_tickets.py # Procesado → maestro
│  ├─ reimportacion.py         # Diferencias, clasificación y aplicación
│  ├─ reportes.py              # Indicadores, recurrencia y reportes
│  ├─ auditoria.py             # Eventos e historial de cambios
│  ├─ asignaciones.py          # Servicio de usuarios y áreas
│  ├─ bloqueo.py               # Bloqueo exclusivo con detección de obsoletos
│  ├─ estado.py                # Estado persistente de la interfaz
│  └─ ...                      # Módulos heredados de áreas
├─ data/                       # Maestros, historial, auditoría y respaldos
├─ input/                      # Excel de entrada (nunca se modifican)
├─ output/                     # Excel procesados y manifiestos JSON
├─ reportes/                   # Reportes Excel y Markdown
├─ logs/                       # Log técnico rotativo
└─ tests/test_sistema.py       # 65 pruebas automatizadas
```

---

## Documentación

- [Manual de instalación](MANUAL_INSTALACION.md)
- [Manual de usuario](MANUAL_USUARIO.md)
- [Documentación técnica](DOCUMENTACION_TECNICA.md)
- [Auditoría técnica final](AUDITORIA.md)

---

## Validación de referencia (septiembre 2026)

Resultado real de la última ejecución:

| Indicador | Referencia | Obtenido |
|-----------|-----------:|---------:|
| Tickets | 179 | 179 |
| Horas | 426.75 | 426.75 |
| Días hábiles | 22 | 22 |
| Tickets/día | 8.1 | 8.1 |
| Horas/día | 19.4 | 19.4 |
| Responsables | 5 | 5 |
| Capacidad nominal | 880 h | 880 h |
| Imputación | 48.5 % | 48.5 % |

Las 35 comprobaciones de `validar_septiembre.py` pasan. La distribución por
atención (110 / 41 / 28), por requerimiento y por responsable también coincide.

---

## Pruebas

```powershell
python -m unittest discover -s tests -v
```

Cobertura actual: esquema, ETL, procesamiento, reimportación, consolidación,
rollback, restauración de respaldos, reportes, servicios, integridad, validación
de septiembre y arranque de la interfaz. Resultado de la última ejecución:
**65 pruebas, 0 fallos**.

Además, la validación del reporte Excel verifica la estructura real del libro con
`openpyxl`, confirmando:

- exactamente cinco hojas;
- nombres exactos: `RESUMEN`, `PROYECTOS`, `ACTIVIDAD`, `AREA`, `DETALLE`;
- columnas y orden exactos por hoja;
- cuatro gráficos circulares reales sin título y ninguno en `DETALLE`;
- ausencia de hojas y columnas extra.

---

## Herramientas heredadas de áreas

`construir_maestro.py` y `actualizar_maestro.py` siguen disponibles para la
gestión avanzada de la base `USUARIO → AREA_USUARIO`. La interfaz y el servicio
`core/asignaciones.py` cubren la operación diaria sin necesidad de consola.
