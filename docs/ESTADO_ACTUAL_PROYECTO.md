# Estado actual del proyecto

**Proyecto:** Sistema Integral de Tickets TI — INKACROPS / SERVIAM  
**Fecha de revisión:** 13/11/2026  
**Alcance:** revisión estática del código, documentación, estructura del repositorio, archivos de operación presentes y estado de Git.

> Este informe distingue entre funcionalidades presentes en el código, resultados de pruebas documentados anteriormente y verificaciones realizadas en esta revisión. No se ejecutaron pruebas, no se abrió la interfaz, no se conectó a SQL Server y no se modificaron datos de operación.

## Resumen ejecutivo

El repositorio contiene una aplicación de escritorio en Python/Tkinter y un flujo ETL para importar, validar, normalizar y procesar tickets de TI desde Excel. También incluye resolución asistida de áreas, consulta de empleados a SQL Server en modo de solo lectura, reimportación de correcciones, consolidación histórica, reportes, auditoría y controles de integridad.

Las mejoras descritas en las fases 0 a 7 están presentes en el código y se integran con la interfaz en varios puntos. El documento de la fase 7 registra históricamente 212 pruebas aprobadas y 4 omitidas, además de un benchmark; esos resultados no se volvieron a ejecutar durante esta revisión y no deben interpretarse como una verificación actual de todo el árbol de trabajo.

El frente de generación de informes Word está en preparación: existe un plan detallado y hay dos plantillas DOCX y una prueba de plantilla sin seguimiento en Git. En el inventario actual no aparecen el lector, el cálculo de estadísticas, el generador DOCX, sus integraciones en la interfaz ni la dependencia `python-docx`. Por ello el informe Word todavía no se considera una función implementada. La plantilla ya presente también hace que la nota del plan que dice que falta recibirla necesite actualizarse, previa validación de que corresponda a la plantilla oficial.

## Qué está implementado

| Área | Estado observado | Evidencia / límite |
|---|---|---|
| Aplicación y consola | Implementadas | `main.py` inicia la GUI sin archivo y procesa un Excel desde consola; `interfaz.py` y `ventana_validacion.py` contienen la interfaz. |
| Importación y ETL | Implementados | `core/archivos.py`, `core/etl.py` y `core/procesador.py` soportan detección/validación de estructura, normalización y generación del resultado. |
| Esquema de tickets | Implementado | El contrato del proyecto conserva 11 columnas oficiales; las hojas auxiliares y los estados técnicos se mantienen fuera del detalle. |
| Usuarios y áreas | Implementados | Maestro persistente, asignación manual/asistida, sugerencias por similitud, catálogo de áreas y revisión de pendientes. La similitud propone candidatos, no confirma identidades automáticamente. |
| SQL Server | Implementación de solo lectura | Hay configuración, repositorio, consultas parametrizadas, aislamiento por empresa y sincronización/caché. El informe de auditoría del 12/11 registra SQL no configurado ni habilitado; este análisis no probó una conexión real. |
| Revisión y reimportación | Implementadas | Revisión persistente, clasificación de diferencias y conservación de registros ausentes en vez de borrarlos automáticamente. |
| Maestro histórico | Implementado | Consolidación idempotente, huellas/identificadores, control y respaldos. |
| Reportes | Implementados | Generación de reporte Excel y Markdown; se describe un libro con hojas de resumen y detalle. Hay un reporte generado en `reportes/`. |
| Auditoría y resiliencia | Implementadas | CSV de auditoría, bloqueo cooperativo, escritura protegida, huellas de Excel, diario de operaciones, diagnóstico e historial. |
| Optimizaciones fases 0–7 | Código presente e integrado | Se observan bus de eventos y suscripciones en la interfaz, uso de asignación protegida, módulos de similitud/coincidencias y diagnóstico. La cobertura funcional real debe confirmarse ejecutando la suite y revisando flujos manuales. |

## Estado de ejecución y datos locales

La última auditoría técnica versionada, `AUDITORIA_SISTEMA_ACTUAL.md`, fue generada el **12/11/2026**. Reporta que en esa ejecución no estaban creados el maestro de áreas, el maestro histórico ni la caché de empleados, y que SQL estaba deshabilitado/no configurado. En la carpeta `data/` observada el 13/11 sí hay archivos locales de auditoría, estado de interfaz y procedencia, pero no se observa un maestro de áreas o maestro histórico. La auditoría también registra una ejecución de procesamiento finalizada el 10/11/2026.

En el árbol de trabajo se encuentran un Excel procesado con manifiesto en `output/`, un reporte Excel/Markdown en `reportes/` y un log. `input/` no contiene un libro de entrada, por lo que este análisis no pudo corroborar una ejecución nueva con datos de negocio.

## Trabajo en curso: informes Word (DOCX)

`docs/PLAN_IMPLEMENTACION_DOCX.md` describe el objetivo de generar un informe mensual desde una sola fuente, usando una plantilla, estadísticas compartidas, cinco tablas y cuatro gráficos nativos, con vista previa e integración en Tkinter.

Estado contrastado con el repositorio:

- Está presente el plan y el análisis de requisitos.
- Existen `plantillas/anexo_5_plantilla.docx` y `plantillas/Anexo_5_Servicio_de_Tecnologías_de_la_Información_MES_2026_PLANTILLA.docx`; ambos aparecen como no rastreados por Git al momento de la revisión. Su presencia no demuestra por sí sola que sean la plantilla oficial aprobada ni que sean idénticos.
- Existe `tests/test_plantilla_docx.py`, también no rastreado.
- No aparecen módulos de producción para leer la fuente, calcular las estadísticas del informe, editar el DOCX o actualizar sus gráficos.
- No se identifica integración del flujo DOCX en `interfaz.py` ni dependencia `python-docx` en `requirements.txt`.
- La fase 1 del plan dice que está bloqueada por falta de plantilla, estado que debe revisarse ante los archivos ahora presentes.

**Conclusión:** el generador DOCX está planificado y parcialmente preparado, pero no funcional como flujo de extremo a extremo según el inventario actual.

## Pendientes recomendados

### Prioridad alta: cerrar y verificar el estado base

1. Ejecutar la suite automatizada completa en un entorno controlado y registrar el resultado actual. La cifra de 212 pruebas/4 omitidas proviene del informe histórico de la fase 7; el árbol ahora contiene archivos adicionales no rastreados.
2. Ejecutar la auditoría de solo lectura y actualizar sus métricas con fecha, separando estado del repositorio y estado de datos locales.
3. Corregir referencias de documentación obsoletas: `README.md` indica una carpeta de inicio distinta a la raíz observada y enlaza manuales en la raíz aunque están dentro de `docs/`; el plan DOCX todavía marca la plantilla como faltante.
4. Decidir si los archivos no rastreados (plantillas y prueba DOCX, además de `temp_check_deps.py` y `test_output.txt`) son entregables intencionales, artefactos temporales o archivos locales que deben mantenerse fuera del repositorio. No se modificaron.

### Prioridad alta: completar generación DOCX

1. Confirmar la identidad y versión de la plantilla oficial; comparar las dos copias y validar su estructura y elementos editables.
2. Actualizar el estado de las fases del plan conforme a la plantilla disponible.
3. Implementar lector/normalizador de fuentes y modelo único de estadísticas con reglas de clasificación documentadas.
4. Implementar actualización transaccional de textos, tablas y gráficos en una copia de la plantilla.
5. Integrar selección de fuente, vista previa, generación y apertura del informe en la pestaña Reportes.
6. Añadir la dependencia necesaria y pruebas aisladas de lectura, cálculos, tablas, gráficos, errores, regresión y preservación de la plantilla.
7. Validar visualmente los DOCX generados con casos de distinto volumen y conservar evidencia del resultado.

### Prioridad media: operación y rendimiento

- Configurar SQL Server en el entorno objetivo y realizar una verificación de solo lectura, autorizada y con datos de prueba apropiados. La conexión no fue probada en esta revisión.
- Si los tiempos del benchmark de la fase 7 siguen siendo representativos, optimizar y medir de nuevo la búsqueda de coincidencias: el informe registra aproximadamente 8.7–9.7 segundos para 20 usuarios y un maestro de 1,000 filas.
- Revisar umbrales de similitud con datos anonimizados y medir falsos positivos/falsos negativos antes de ajustar valores.
- Realizar validación manual de la interfaz: el plan de auditoría posterior enumera como pendiente confirmar actualización visual, respuesta de la GUI y comportamiento de botones/confirmaciones.
- Definir si las asignaciones deben continuar guardándose solo en el maestro local. Actualmente SQL se mantiene como solo lectura; no hay escritura de áreas a la base corporativa.

## Riesgos y límites actuales

- No se verificó la ejecución actual de la suite ni la GUI; el estado de pruebas de fases anteriores es evidencia histórica, no un resultado de esta revisión.
- No hay datos de entrada en `input/` ni maestro local visible, así que falta corroborar el flujo con un archivo real representativo en el entorno de operación.
- La integración SQL depende de configuración, drivers ODBC, conectividad y esquema corporativo disponibles fuera del repositorio.
- El benchmark previo muestra el mayor costo en la búsqueda aproximada de coincidencias; conviene medir con cargas reales anonimizadas.
- La plantilla DOCX local aún requiere validación de origen y estructura antes de tratarla como plantilla oficial.
- El estado de Git incluye archivos sin seguimiento; no se hizo commit ni limpieza.

## Referencias del repositorio

- `README.md`: descripción y uso general del sistema.
- `AUDITORIA_SISTEMA_ACTUAL.md`: auditoría de entorno generada el 12/11/2026.
- `IMPLEMENTACION_FASE_0.md` a `IMPLEMENTACION_FASE_7.md`: alcance y resultados históricos de las optimizaciones.
- `docs/PLAN_IMPLEMENTACION_DOCX.md`: plan por fases del generador Word.
- `docs/AUDITORÍA FINAL POSTIMPLEMENTACIÓN-pendiente.md`: alcance de la auditoría técnica pendiente.

