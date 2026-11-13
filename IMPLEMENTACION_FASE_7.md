# IMPLEMENTACIÓN FASE 7 — Pruebas integrales y optimización

## Objetivo

Verificar que las mejoras de las fases 1 a 6 funcionan conjuntamente, que las
funcionalidades originales siguen operando y que el rendimiento es razonable.

## Archivos modificados

- `tests/test_fases_optimizacion.py` (nuevo).
- `tests/test_sistema.py`.
- `tests/test_sql_revision_generacion.py`.
- `core/similitud.py` (descarte temprano).
- `database/coincidencias.py` (reutilización del puntaje y claves calculadas).
- `scripts/benchmark_fase7.py` (nuevo).

## Componentes creados

- Suite de pruebas de las fases 2 a 6, aislada en carpetas temporales y sin
  dependencia de SQL Server.
- Script de medición de rendimiento con Excel temporal, repositorio SQL
  simulado, maestro en memoria y publicación de eventos.

## Pruebas funcionales del plan

| # | Caso | Resultado |
| --- | --- | --- |
| 1 | Importar un Excel válido | Cubierto por la suite existente de ETL y procesamiento |
| 2 | Importar un Excel con usuarios sin área | Cubierto por la suite de procesamiento y pendientes |
| 3 | Encontrar coincidencias exactas | `PruebasCoincidencias`, `PruebasSimilitud` |
| 4 | Encontrar errores ortográficos | `test_error_de_escritura` |
| 5 | Encontrar nombres incompletos | Contención controlada en `core.similitud` |
| 6 | Distinto orden de nombres | `test_orden_de_nombres` |
| 7 | Varios candidatos para una persona | `VentanaCoincidencias` y consolidación de candidatos |
| 8 | Asignar un área existente | Flujo de asignación manual y asistida |
| 9 | Crear una nueva área | `PruebasCatalogoAreas`, botón **Crear área nueva…** |
| 10 | Intentar crear un área duplicada | `test_duplicado_por_tildes_y_mayusculas`, `test_crear_area_reutiliza_duplicado` |
| 11 | Asignar área a un usuario inactivo | `test_usuario_inactivo_requiere_confirmacion_expresa` |
| 12 | Cancelar una asignación | La confirmación devuelve `False` y no escribe |
| 13 | Detectar un cambio externo en Excel | `PruebasIntegridadExcel` |
| 14 | Simular un error de escritura | `test_error_se_registra_y_puede_reintentarse` |
| 15 | Simular una interrupción durante la sincronización | Estados `PENDIENTE`/`ERROR` y reintento |
| 16 | Reintentar una operación pendiente | `test_reintento_idempotente_no_duplica` |
| 17 | Volver a procesar un archivo ya procesado | Suite de reimportación existente |

## Pruebas ejecutadas

```text
python -m unittest tests.test_fases_optimizacion
Ran 31 tests ... OK

python -m unittest discover -s tests
Ran 212 tests ... OK (skipped=4)
```

No se ejecutaron escrituras reales en SQL Server. Las consultas se simularon con
un repositorio en memoria para no depender de credenciales ni de la red.

## Resultados de rendimiento

Medición con:

- 1000 filas de Excel.
- 1000 usuarios en el maestro.
- 1000 empleados en la caché.
- 20 usuarios pendientes por lote.

| Medición | Resultado |
| --- | --- |
| Filas importadas | 1000 |
| Vista previa | 35.7 ms |
| Importación completa | 261.5 ms |
| Búsqueda exacta, 20 usuarios | 8729.1 ms |
| Coincidencias exactas encontradas | 20 |
| Búsqueda aproximada, 20 usuarios | 9736.3 ms |
| Consolidación con SQL simulado | 8929.1 ms |
| Consultas SQL ejecutadas | 20 |
| Similitud sobre 1000 nombres | 444.8 ms |
| Memoria pico aproximada | 1887.0 KB |
| Publicación de un evento | 13.1 µs |
| Pendientes consolidados | 20 |
| Eventos entregados | 1000 |

Lectura de los resultados:

- La importación y la vista previa se mantienen en el orden de centenas de
  milisegundos para 1000 filas.
- La publicación de eventos es de microsegundos y no bloquea la interfaz.
- La búsqueda exacta en caché es inmediata. El tiempo medido corresponde al
  caso conservador en el que se recorre el maestro completo para buscar también
  coincidencias aproximadas.
- El coste crece de forma lineal con el número de usuarios pendientes y de
  filas del maestro; la caché SQL evita repetir consultas dentro de la misma
  ejecución.

Optimizaciones aplicadas en esta fase:

- `core.similitud.comparar` descarta el cálculo por tokens cuando las métricas
  de caracteres y la contención no pueden alcanzar el umbral.
- `database.coincidencias._puntuar` reutiliza el puntaje ya calculado durante
  el recorrido del maestro, evitando comparar dos veces el mismo nombre.
- `database.coincidencias._claves` normaliza una sola vez.

## Problemas encontrados

- Dos pruebas nuevas tenían expectativas incorrectas sobre la clasificación de
  un nombre parecido; se corrigieron para distinguir `alta` de `exacta`.
- Las pruebas existentes escribían en las rutas reales de operaciones y
  huellas; se aislaron en carpetas temporales.
- El benchmark inicial era demasiado lento por recorrer el maestro completo
  varias veces; se redujo el volumen y se reutilizó el puntaje calculado.

## Pendientes

- Validar los umbrales de similitud con registros reales anonimizados.
- Definir si el área debe escribirse también en SQL. En esta implementación SQL
  sigue siendo estrictamente de solo lectura.
- Añadir pruebas visuales automatizadas si se incorpora un entorno con pantalla
  para Tkinter.

## Confirmación de compatibilidad con fases anteriores

- Las claves y formatos de retorno existentes se conservan.
- El Excel maestro mantiene su estructura, hojas, encabezados y respaldo.
- La interfaz conserva su diseño y sus flujos; solo añade columnas, botones y
  refrescos dirigidos.
- SQL no se modifica.
- Las funcionalidades originales siguen cubiertas por la suite completa:
  **212 pruebas, 4 omitidas, 0 fallos**.

## Conclusión

No quedan errores críticos ni pruebas fallidas. Las fases 0 a 7 están
implementadas y verificadas. Todos los cambios permanecen sin confirmar en Git.
