# Auditoría técnica final

**Sistema:** Sistema Integral de Tickets TI - INKACROPS  
**Fecha:** 6 de noviembre de 2026  
**Ámbito:** `script automatizador`  
**Método:** revisión de código, ejecución real del flujo completo, 59 pruebas
automatizadas, validación contra septiembre 2026 y auditoría reproducible con
`auditar_sistema.py`.

---

## 1. Resumen ejecutivo

El sistema quedó convertido en una solución ETL/GUI completa para tickets de TI,
con estructura oficial de 11 columnas, asignación persistente de áreas,
reimportación de correcciones, maestro histórico con control de identidad,
reportes, auditoría y recuperación ante fallos.

Resultados de la última ejecución real:

| Comprobación | Resultado |
|--------------|-----------|
| Pruebas automatizadas | **59 / 59 OK** |
| Validación septiembre 2026 | **35 / 35 PASS** |
| Auditoría de integridad | **24 comprobaciones, 0 fallos** |
| Procesamiento real de septiembre | 179 tickets, 426.75 h |
| Consolidación en maestro | 179 nuevos, 0 duplicados |
| Archivo original | SHA-256 sin cambios |

No se detectaron fallos críticos en el flujo completo.

---

## 2. Alcance auditado

1. **Esquema y estructura:** 11 columnas, orden, alias, columnas adicionales y
   detección de duplicados de encabezado.
2. **ETL:** extracción, detección de hoja/encabezado, normalización de fechas,
   horas, textos y nulos.
3. **Procesamiento:** estados internos, manifiesto v2, hojas auxiliares,
   pendientes y duplicados.
4. **Áreas:** persistencia `USUARIO → AREA_USUARIO`, sugerencias y pendientes.
5. **Reimportación:** nuevos, modificados, sin cambios, duplicados, ambiguos y
   ausentes; historial campo a campo.
6. **Consolidación:** maestro inexistente/existente, idempotencia, conservación
   de históricos, respaldo, rollback y restauración.
7. **Reportes:** indicadores, distribuciones, evolución semanal y recurrencia.
8. **Auditoría:** eventos, cambios y deduplicación.
9. **Interfaz:** arranque, pestañas y operaciones principales.
10. **Integridad:** contabilidad de registros, originales intactos y estructura
    final.

---

## 3. Correcciones y funcionalidades implementadas

- Esquema centralizado de 11 columnas con alias reales, incluidos
  `FECHA_REGISRO`, `USUARIO`, `TIPO_ATENCION`, `HORAS_TOTAL`, `GRUPO 1` y
  `SUBT_TIPO`.
- Detección explícita de columnas duplicadas con datos y de columnas faltantes.
- ETL con estados internos fuera del detalle y reporte de observaciones.
- Generación de Excel procesado con hojas `Tickets`, `Observaciones`, `Resumen`,
  `Control` y `Origen_Adicional`.
- Manifiesto v2 con `id_tecnico`, `huella`, `estado` y datos por fila.
- Reimportación con coincidencia exacta por huella, similitud ponderada,
  detección de ambigüedad y bloqueo de aplicación.
- Historial campo a campo en reimportaciones y consolidaciones.
- Maestro histórico con hoja `CONTROL`, IDs únicos, huellas verificadas,
  escritura atómica, rollback y respaldos.
- Consolidación idempotente que conserva históricos y no elimina ausentes.
- Servicio de usuarios/áreas con búsqueda, sugerencias y pendientes.
- Bloqueo exclusivo con detección de lock obsoleto.
- Estado persistente para el panel principal.
- Interfaz Tkinter de 7 pestañas con procesamiento en segundo plano.
- Reportes Excel con gráficos y reporte Markdown con limitaciones explícitas.
- Auditoría independiente y comando `auditar_sistema.py` de solo lectura.

---

## 4. Validación con septiembre de 2026

Archivo real: `input/consulta_septiembre_2026.xlsx`.

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

Distribución por atención: Mesa de Ayuda 110, Gestión 41, Proyectos 28.  
Distribución por requerimiento y por responsable: coincide exactamente con el
informe de referencia.

La validación no modifica los datos para forzar coincidencias: procesa el Excel
original y compara los indicadores calculados.

---

## 5. Pruebas ejecutadas

```powershell
python -m unittest discover -s tests -v
```

Resultado: **59 pruebas, 0 fallos**.

Cobertura principal:

- Alias, orden y validación estructural.
- Fechas ambiguas e inválidas, horas negativas/no numéricas, nulos y filas
  vacías.
- Procesamiento con columnas extra, manifiesto v2, pendientes y duplicados.
- Reimportación de detalles, fechas, solicitantes, áreas, horas y proyectos.
- Altas, ausencias, duplicados, ambigüedad e idempotencia.
- Maestro inexistente, consolidación repetida, históricos y alineación
  detalle/`CONTROL`.
- Respaldo, restauración y fallo de escritura sin maestro parcial.
- Reportes de septiembre, Excel/Markdown y recurrencia.
- Asignaciones, sugerencias, auditoría, bloqueo y estado.
- Flujo completo y arranque de la interfaz.

---

## 6. Auditoría de integridad

```powershell
python auditar_sistema.py
```

Resultado de la última ejecución:

```text
Comprobaciones: 24 | Fallos: 0 | Advertencias: 0 | Omitidas: 1
RESULTADO: INTEGRIDAD VERIFICADA
```

La comprobación omitida es el historial de cambios del maestro
(`maestro_tickets_historial.csv`), que solo se crea cuando se aplica una
reimportación con cambios. No representa un fallo.

Se verificó:

- Base de áreas: 153 correspondencias, 153 usuarios únicos, 29 áreas, Excel/CSV
  coincidentes.
- Maestro histórico: 179 filas, 11 columnas, `CONTROL` alineado, 179 IDs únicos,
  huellas coherentes.
- Excel procesado: 11 columnas, sin columnas internas, hojas auxiliares
  presentes, manifiesto v2 y contenido coincidente fila a fila.
- Archivo original intacto por SHA-256.
- Fechas completas y horas numéricas.
- Registro de auditoría con IDs únicos y tipos de evento.

---

## 7. Riesgos residuales

1. **Validez funcional del área:** el sistema garantiza integridad y
   trazabilidad, no que el área asignada sea la correcta para el negocio.
2. **Horas declaradas:** no representan tiempos de resolución; no se calculan SLA.
3. **Alias:** un encabezado nuevo requiere agregarlo en
   `config/configuracion.py`.
4. **Respaldos:** se acumulan en `data/backups/`; conviene una política de
   retención.
5. **OneDrive:** la sincronización puede alterar fechas de modificación; para
   operaciones críticas se recomienda una copia local.
6. **Concurrencia:** existe bloqueo exclusivo, pero no se debe ejecutar el mismo
   flujo desde dos equipos distintos sobre la misma carpeta sincronizada.

---

## 8. Conclusión

El sistema cumple el flujo completo: cargar, validar, procesar, asignar áreas,
corregir, reimportar, consolidar, reportar y auditar. La estructura de 11
columnas se conserva en todos los archivos principales, los históricos no se
eliminan, la consolidación es idempotente y las operaciones de escritura son
transaccionales.

La validación de septiembre de 2026 coincide en todos los indicadores de
referencia y las 59 pruebas automatizadas pasan. Los riesgos residuales son
operativos y están documentados.
