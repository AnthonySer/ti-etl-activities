# Implementación — Consulta SQL multiempresa, detección de columnas y selección manual de empleados

| Campo | Valor |
|---|---|
| Fecha | 2026-11-12 |
| Ámbito | Módulo «Validación de Usuarios y Áreas» / botón «Consultar SQL» |
| Base de datos | `EXACTUS` (solo lectura) |
| Estado | Implementación y pruebas automatizadas completadas; validación en SSMS pendiente |
| Pruebas nuevas | 24/24 correctas |
| Pruebas existentes ejecutadas | 102/102 correctas |

> Este documento no contiene credenciales, cadenas de conexión con contraseña ni datos personales de empleados. No se modificó ninguna base de datos productiva.

---

## 1. Problema original

El botón **«CONSULTAR SQL»** fallaba antes de devolver resultados porque la consulta seleccionaba columnas que no existen en todas las empresas:

- `Invalid column name 'FUENTE'` (error 207 / 42S22).
- `Invalid column name 'U_CORREO_CORP'`.
- `Invalid column name 'DEPARTAMENTO'`.
- `Statement(s) could not be prepared` (8180).

La consulta anterior asumía un esquema único para las 18 empresas. Sin embargo:

- `FUENTE` no existe en ninguna empresa.
- `U_CORREO_CORP` solo existe en FILASUR, INKA, POLYSOL, SERVIAM y TROPICAL.
- Nueve empresas tienen un esquema mínimo o están vacías: CONDOR, COYA, ESTRATEGO, PROEXPLOR, RELTEX, TANKARPATA, TARAY, TMI y YACLOT.
- Un mismo nombre puede existir en varias empresas, con códigos, departamentos, puestos y estados distintos.

Además, la auditoría de solicitantes fallaba de forma independiente con:

`ERROR: No se pudo auditar solicitantes: Worksheet named 'Tickets' not found`.

El sistema tampoco debía elegir el primer resultado ni reutilizar el departamento de otra empresa.

---

## 2. Causas encontradas

| Causa | Evidencia | Consecuencia |
|---|---|---|
| Consulta con columnas fijas | `FUENTE`, `U_CORREO_CORP`, `DEPARTAMENTO`, `PUESTO`, `ESTADO_EMPLEADO` se seleccionaban siempre | SQL Server rechaza todo el lote; una sola columna ausente bloquea la empresa |
| Esquemas heterogéneos | 9 empresas pobladas y 9 vacías/mínimas | No existe un `SELECT` único válido para todas |
| Código de empleado no único entre empresas | Mismo código en empresas distintas | No se puede usar el código solo como identidad global |
| Resolución ambigua silenciosa | Varias coincidencias por nombre | Se podía asignar un área incorrecta |
| Departamento usado como área sin control | El flujo anterior escribía el departamento del candidato | Se podía reutilizar el departamento de otra empresa |
| Persistencia incompleta | La revisión guardaba candidatos, pero no la selección ni su trazabilidad completa | No se podía auditar la decisión |
| Hoja `Tickets` asumida | `leer_excel(..., sheet_name="Tickets")` sin diagnóstico previo | Error opaco y auditoría declarada fallida sin contexto |
| Estado de sincronización engañoso | Se mostraba «conectado» aunque hubiera errores | La UI afirmaba éxito sin consulta completa |

El diagnóstico detallado está en `docs/DIAGNOSTICO_ERROR_VALIDACION_SQL.md` y la matriz real de empresas en `docs/DIAGNOSTICO_SQL_SERVER_EXACTUS.md`.

---

## 3. Archivos modificados

| Archivo | Cambio |
|---|---|
| `database/queries.py` | `SELECT` adaptativo, `EsquemaInsuficiente`, metadatos por tabla, identificadores validados y `FUENTE` como literal |
| `database/repository.py` | Caché de esquema por empresa, clasificación de errores, resultado uniforme, deduplicación por `(empresa, fuente, código)`, caché de fallos para no repetir advertencias |
| `database/resolucion.py` | `ResolverAreas.resolver_candidato` y `_resolver_empleado` para resolver solo el registro elegido |
| `database/synchronizer.py` | Conserva la caché y los empleados de empresas con error; informa `empresas_sin_datos`, `empresas_sin_esquema`, `cache_conservada` |
| `core/seleccion_empleados.py` | **Nuevo.** Formato uniforme, filtros, orden, opciones, identidad y validación de selección |
| `core/revision.py` | `registrar_seleccion`, `seleccion_guardada_valida`, campos `seleccion` y `trazabilidad`; el departamento ya no se escribe como área |
| `core/procedencia.py` | `detalle` y campos de trazabilidad opcionales; `como_dataframe` incluye EMPLEADO, NOMBRE, EMPRESA, FUENTE, DEPARTAMENTO y TIPO_RESOLUCION |
| `core/maestro_tickets.py` | `inspeccionar_hojas` y `leer_hoja_detalle` con diagnóstico de archivo, hojas y hoja esperada |
| `ventana_validacion.py` | Consulta con candidatos/trazabilidad, subventana de 8 columnas, filtros, orden y confirmación deshabilitada sin selección |
| `interfaz.py` | Auditoría de solicitantes con diagnóstico claro; sincronización informa errores reales y no declara «conectado» si falló |
| `tests/test_consulta_multiempresa.py` | **Nuevo.** 24 escenarios obligatorios con dobles de prueba |
| `diagnostico_sql/*.sql` | **Nuevo.** Seis scripts de validación manual, solo lectura |
| `diagnostico_sql/README.md` | **Nuevo.** Instrucciones de ejecución en SSMS |
| `docs/IMPLEMENTACION_CONSULTA_SQL_MULTIEMPRESA.md` | **Nuevo.** Este informe |
| `docs/CHECKPOINT_IMPLEMENTACION_SQL.md` | Actualizado al estado final |

No se agregaron dependencias nuevas. No se modificaron bases productivas ni se agregaron columnas.

---

## 4. Funciones nuevas y modificadas

### `database/queries.py`

- `validar_identificador`, `validar_esquema`: validan base, esquema, tabla y columnas antes de interpolarlas.
- `columnas_por_tabla`: agrupa filas de `INFORMATION_SCHEMA.COLUMNS`.
- `sql_empleados`: construye el `SELECT` solo con columnas existentes.
- `sql_columnas_esquema`, `sql_tablas_existen`, `sql_tablas_empresa`: consultas de metadatos.
- `EsquemaInsuficiente`: se lanza antes de ejecutar si faltan `EMPLEADO` o un nombre utilizable.

### `database/repository.py`

- `RepositorioEmpleados._leer_capacidades`: lee y cachea las columnas reales por empresa. También cachea el fallo para no repetir conexiones ni advertencias en cada lote.
- `RepositorioEmpleados._consultar`: aísla cada empresa; una falla no bloquea las demás.
- `_clasificar_error_consulta`: distingue `columna`, `tabla`, `permisos`, `sintaxis`, `campos_insuficientes`, `servidor` y errores inesperados.
- `Empleado.como_dict`: expone claves históricas y las ocho claves lógicas.
- `Empleado.desde_candidato`: reconstruye el registro elegido desde la revisión.
- `Empleado.tiene_departamento`: ignora valores nulos como `N/A`, `NULL` o espacios.

### `core/seleccion_empleados.py`

- `CAMPOS`: empleado, nombre, empresa, fuente, departamento, puesto, activo, correo.
- `valor`, `registro_uniforme`, `etiqueta_activo`, `activo_booleano`.
- `filtrar_candidatos`, `ordenar_candidatos`, `opciones`.
- `identidad`, `seleccion_valida`, `seleccion_desde_candidato`.
- `registro_trazable`: añade área, tipo de resolución y fecha.

### `core/revision.py`

- `Revision.registrar_seleccion`: guarda la selección, el área resuelta y la trazabilidad.
- `Revision.seleccionar_candidato`: se conserva por compatibilidad.
- `Revision.seleccion_guardada_valida`: revalida identidad y departamento antes de reutilizar una decisión.

### `core/procedencia.py`

- `registrar(..., detalle=..., empleado=..., nombre=..., empresa=..., fuente=..., departamento=..., tipo_resolucion=...)`: conserva la trazabilidad sin romper la firma anterior.
- `registrar_lote`: acepta la misma información por elemento.
- `como_dataframe`: agrega las columnas de trazabilidad.

### `core/maestro_tickets.py`

- `MaestroTickets.inspeccionar_hojas`: devuelve archivo, hojas, hoja esperada y problema.
- `MaestroTickets.leer_hoja_detalle`: usa la hoja esperada o una alternativa con columna `Solicitante`; nunca crea hojas vacías.

### `ventana_validacion.py`

- `VentanaValidacion._consultar_sql`: conserva candidatos, resuelve y ofrece selección manual cuando hay ambigüedad.
- `VentanaValidacion._abrir_seleccion`: abre la subventana desde el hilo principal.
- `VentanaValidacion._elegir_candidato`: resuelve el área solo con el registro elegido.
- `VentanaCoincidencias`: ocho columnas, filtros, orden, etiquetas de activo/inactivo y botón de confirmación deshabilitado sin selección.

---

## 5. Consultas SQL corregidas

El repositorio lee primero las columnas reales:

```sql
SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE
FROM [EXACTUS].INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA = ?
ORDER BY TABLE_NAME, ORDINAL_POSITION
```

Luego construye el `SELECT` solo con lo disponible. Plantilla resultante:

```sql
SELECT
    E.[EMPLEADO] AS EMPLEADO,
    E.[EMPLEADO] AS CODIGO_EMPLEADO,
    'EMPLEADO' AS FUENTE,
    E.[NOMBRE] AS NOMBRE,
    <columnas opcionales que existan> AS ...,
    E.[PUESTO] AS PUESTO_CODIGO,          -- o NULL
    E.[DEPARTAMENTO] AS DEPARTAMENTO_CODIGO, -- o NULL
    E.[ACTIVO] AS ACTIVO,                  -- o NULL
    D.[DESCRIPCION] AS DEPARTAMENTO,       -- solo si hay LEFT JOIN válido
    P.[DESCRIPCION] AS PUESTO,             -- solo si hay LEFT JOIN válido
    E.[E_MAIL] AS CORREO                   -- o E.[U_CORREO_CORP], o NULL
FROM [EXACTUS].[<EMPRESA>].[EMPLEADO] E
LEFT JOIN [EXACTUS].[<EMPRESA>].[DEPARTAMENTO] D
       ON D.[DEPARTAMENTO] = E.[DEPARTAMENTO]
LEFT JOIN [EXACTUS].[<EMPRESA>].[PUESTO] P
       ON P.[PUESTO] = E.[PUESTO]
ORDER BY E.[EMPLEADO]
```

Reglas aplicadas:

1. Si no existe `U_CORREO_CORP`, se omite y se usa `E_MAIL`; si tampoco existe, `NULL`.
2. Si no existe `PUESTO` en `EMPLEADO`, no se une la tabla `PUESTO` y se devuelve `NULL AS PUESTO`.
3. Si no existe `DEPARTAMENTO` en `EMPLEADO`, no se une la tabla `DEPARTAMENTO` y se devuelve `NULL AS DEPARTAMENTO`.
4. Si no existe `ACTIVO`, se devuelve `NULL AS ACTIVO`.
5. `FUENTE` nunca se lee de la tabla: se genera como `'EMPLEADO'`.
6. Los valores de búsqueda viajan como parámetros `?`; nunca se concatenan.
7. Si faltan `EMPLEADO` o un nombre utilizable, se lanza `EsquemaInsuficiente` antes de ejecutar.

Los scripts `diagnostico_sql/02_consultas_originales.sql` y `03_consultas_corregidas.sql` reproducen y comparan ambos comportamientos en SSMS.

---

## 6. Funcionamiento de la subventana

La subventana se abre desde el hilo principal de Tkinter con `self.after(10, ...)`, nunca desde el hilo trabajador.

Contenido mínimo:

| Columna | Origen |
|---|---|
| EMPLEADO | Código del registro |
| NOMBRE | Nombre completo |
| EMPRESA | Empresa del registro |
| FUENTE | Fuente del registro |
| DEPARTAMENTO | Departamento del registro |
| PUESTO | Puesto del registro |
| ACTIVO | Activo / Inactivo / No disponible |
| CORREO | Correo corporativo o personal disponible |

Comportamiento:

- Filtros por empleado, nombre, empresa, fuente y departamento.
- Orden ascendente/descendente por cualquier columna; los valores ausentes quedan al final.
- Filas activas, inactivas y sin dato con colores distintos.
- El botón **Confirmar selección** está deshabilitado mientras no haya una fila elegida.
- **Cancelar** cierra sin registrar selección ni modificar el área.
- La etiqueta de conteo muestra cuántas coincidencias se ven del total.
- No se elige automáticamente la primera coincidencia.

---

## 7. Criterios de identificación de empleados

La identidad lógica es:

```text
(empresa, fuente, código de empleado)
```

Consecuencias:

- El mismo código en dos empresas produce dos registros distintos.
- El mismo nombre en dos empresas produce dos candidatos; nunca se fusionan.
- Dos empleados distintos con el mismo nombre producen ambigüedad y van a selección manual.
- El mismo usuario en dos fuentes de la misma empresa conserva ambos registros.
- Activo e inactivo se muestran por separado; no se descarta el inactivo.
- El nombre normalizado se usa solo para buscar y comparar, no como identidad única.
- Una selección guardada solo se reutiliza si coinciden empresa, fuente, código y departamento.

---

## 8. Mecanismo de asignación de áreas

Flujo al confirmar:

1. Se recupera el registro completo elegido, no solo el texto visible.
2. Se obtienen empresa, fuente, código y departamento.
3. `ResolverAreas.resolver_candidato` resuelve **solo ese registro**.
4. Si el empleado está activo y tiene departamento, se usa su departamento como área base.
5. Si no tiene departamento, caso `D`: no se inventa un área.
6. Si está inactivo, caso `E`: se muestra su estado y no se asigna en silencio.
7. Si el área local del maestro o la procedencia es `MANUAL` y difiere, se reporta conflicto y se protege la asignación manual.
8. `Revision.registrar_seleccion` guarda la decisión y `procedencia.registrar(..., detalle=...)` conserva la trazabilidad.

> En el estado actual del proyecto no existe un catálogo separado `DEPARTAMENTO → ÁREA`; la lógica existente usa `DEPARTAMENTO.DESCRIPCION` como área base. La implementación no crea una segunda fuente de verdad ni modifica las equivalencias existentes.

Nunca se usa el departamento de otra empresa ni se asigna una coincidencia aproximada sin las reglas de confianza existentes.

---

## 9. Persistencia de selecciones

La revisión guarda en cada registro:

- `seleccionado`: el candidato completo elegido.
- `seleccion`: versión uniforme con empleado, nombre, empresa, fuente, departamento, puesto, activo, correo, área, fecha y tipo de resolución.
- `trazabilidad`: código, nombre, empresa, fuente, departamento, área, fecha y `tipo_resolucion` (`automatica` o `manual`).
- `historial`: acción y fecha de la decisión.

La procedencia (`data/procedencia.json` o la ruta configurada) conserva además:

- Usuario y clave normalizada.
- Área asignada y origen (`SQL_SERVER`, `MANUAL`, `LEGACY`).
- Código, nombre, empresa, fuente, departamento y tipo de resolución.
- Fecha e identificador de ejecución.

Al reabrir una revisión o reutilizar una decisión, `seleccion_guardada_valida` comprueba que la identidad y el departamento sigan siendo los mismos. Si cambiaron, la selección antigua no se aplica ciegamente.

---

## 10. Resultados de las pruebas

### Pruebas nuevas

Comando:

```powershell
python -m unittest tests.test_consulta_multiempresa -v
```

Resultado: **24 pruebas, 24 correctas, 0 fallos** en 0.12 s.

| Escenario | Prueba | Resultado |
|---|---|---|
| 1. Empresa con todas las columnas | `test_01_empresa_con_todas_las_columnas` | OK |
| 2. Sin `U_CORREO_CORP` | `test_02_empresa_sin_u_correo_corp` | OK |
| 3. Sin `PUESTO` | `test_03_empresa_sin_puesto` | OK |
| 4. Sin `ACTIVO` | `test_04_empresa_sin_activo` | OK |
| 5. Sin `DEPARTAMENTO` | `test_05_empresa_sin_departamento` | OK |
| 6. Campos insuficientes | `test_06_tabla_sin_campos_suficientes` | OK |
| 7. Tabla inexistente | `test_07_tabla_empleado_no_existe` | OK |
| 8. Error de conexión | `test_08_empresa_con_error_de_conexion` | OK |
| 9. Cero empleados | `test_09_consulta_cero_empleados_no_es_error` | OK |
| 10. Mismo empleado en dos empresas | `test_10_mismo_empleado_en_dos_empresas` | OK |
| 11. Dos empleados con el mismo nombre | `test_11_dos_empleados_distintos_mismo_nombre` | OK |
| 12. Dos fuentes de la misma empresa | `test_12_usuario_en_dos_fuentes_de_la_misma_empresa` | OK |
| 13. Activo en una empresa e inactivo en otra | `test_13_activo_en_una_empresa_e_inactivo_en_otra` | OK |
| 14. Selección de SERVIAM | `test_14_seleccion_del_registro_de_serviam` | OK |
| 15. Selección de FILASUR | `test_15_seleccion_del_registro_de_filasur` | OK |
| 16. Cancelar la subventana | `test_16_cancelar_la_subventana_no_registra_seleccion` | OK |
| 17. Usuario sin departamento | `test_17_usuario_sin_departamento` | OK |
| 18. Departamento sin equivalencia | `test_18_departamento_sin_equivalencia_no_asigna_area` | OK |
| 19. Selección conservada | `test_19_seleccion_manual_se_conserva_para_otra_ejecucion` | OK |
| 20. Selección inválida por cambio | `test_20_seleccion_guardada_deja_de_ser_valida_si_cambia_el_registro` | OK |
| 21. Una empresa falla y las demás continúan | `test_21_una_empresa_falla_y_las_demas_continuan` | OK |
| 22. Sin advertencias repetidas | `test_22_no_repite_advertencias_en_cada_lote` | OK |
| 23. Hoja `Tickets` inexistente | `test_23_hoja_tickets_no_existe` | OK |
| 24. Hoja `Tickets` existente | `test_24_hoja_tickets_existe_y_se_audita` | OK |

### Pruebas existentes

Se ejecutaron las clases relacionadas con conexión, repositorio, resolución, revisión, generación, configuración, procesamiento, solicitantes, servicios, interfaz, esquema, ETL, reimportación, consolidación, reportes e integridad.

Resultado consolidado: **102 pruebas existentes, 102 correctas, 0 fallos**.

Comandos principales:

```powershell
python -m unittest tests.test_sql_revision_generacion.PruebasConexionSQL `
  tests.test_sql_revision_generacion.PruebasRepositorioSQL `
  tests.test_sql_revision_generacion.PruebasResolucion `
  tests.test_sql_revision_generacion.PruebasRevision `
  tests.test_sql_revision_generacion.PruebasVentanaGeneracion `
  tests.test_sql_revision_generacion.PruebasConfiguracionSQL -v

python -m unittest tests.test_sistema.PruebasProcesamiento `
  tests.test_sistema.PruebasSolicitantes `
  tests.test_sistema.PruebasServicios `
  tests.test_sistema.PruebasInterfaz -v

python -m unittest tests.test_sistema.PruebasEsquema `
  tests.test_sistema.PruebasETL `
  tests.test_sistema.PruebasReimportacion `
  tests.test_sistema.PruebasConsolidacion `
  tests.test_sistema.PruebasReportes `
  tests.test_sistema.PruebasIntegridad -v
```

También se ejecutaron los scripts de generación restantes y la prueba de bloqueo con pendientes: todas correctas.

### Verificación de sintaxis

`python -m py_compile` sobre los once archivos Python modificados y el nuevo archivo de pruebas: **sin errores**.

---

## 11. Errores que continúan pendientes

| Pendiente | Motivo | Acción recomendada |
|---|---|---|
| Validación real en SSMS | El entorno de pruebas no se conecta a producción | Ejecutar los seis scripts con un login de lectura y conservar los resultados |
| Configuración guardada con autenticación Windows | `Trusted_Connection=yes` con el usuario de Windows de la sesión produce `28000/18456` | Usar autenticación SQL con la contraseña guardada o corregir el login de Windows |
| Caché local vacía o desactualizada | La caché solo se llena al sincronizar correctamente | Ejecutar «Sincronizar ahora» con la configuración SQL válida |
| Equivalencias `DEPARTAMENTO → ÁREA` | No existe un catálogo separado en el proyecto | Si negocio lo requiere, definir un catálogo canónico y validarlo antes de aplicarlo |
| Nuevas fuentes/tablas | `FUENTE` se genera como `'EMPLEADO'` | Extender `queries.sql_empleados` cuando exista otra tabla o vista autorizada |
| Diferencias de mayúsculas en nombres de hoja | Se comparan sin distinguir mayúsculas, pero se informa la hoja real | Revisar el archivo concreto si el diagnóstico indica otra hoja |
| Pruebas de interfaz en entornos sin pantalla | Tkinter puede no estar disponible | En ese caso las pruebas de UI se omiten; no es un fallo de la consulta SQL |

No se declara una consulta como exitosa si no se ejecutó. La sincronización ahora muestra «incompleta» cuando hay errores y conserva la caché anterior.

---

## 12. Instrucciones para validar en SSMS

1. Abra SQL Server Management Studio y conéctese a la instancia con un login de **solo lectura** sobre `EXACTUS`.
2. Ejecute los scripts en este orden:
   1. `diagnostico_sql/00_inspeccionar_esquema.sql`
   2. `diagnostico_sql/01_validar_columnas.sql`
   3. `diagnostico_sql/02_consultas_originales.sql`
   4. `diagnostico_sql/03_consultas_corregidas.sql`
   5. `diagnostico_sql/04_validacion_por_empresa.sql`
   6. `diagnostico_sql/05_validar_empleados_duplicados.sql`
3. En `00`, revise:
   - Los 18 esquemas existen.
   - Las tablas `EMPLEADO`, `DEPARTAMENTO` y `PUESTO` por empresa.
   - El conteo de filas: 9 empresas pobladas y 9 vacías/mínimas.
4. En `01`, confirme la matriz de columnas:
   - `FUENTE` debe dar `0` en todas las empresas.
   - `U_CORREO_CORP` solo debe dar `1` en FILASUR, INKA, POLYSOL, SERVIAM y TROPICAL.
   - Las empresas mínimas no deben tener `DEPARTAMENTO`, `PUESTO`, `ACTIVO` extendido ni correo.
5. En `02`, reproduzca los errores originales y confirme que son de columna, no de conexión.
6. En `03`, valide que las consultas corregidas devuelven los ocho campos lógicos y usan `NULL` donde falta el dato.
7. En `04`, compare una empresa poblada con una mínima; la empresa mínima no debe bloquear a las demás.
8. En `05`, revise duplicados de código y nombre. Recuerde: una coincidencia no implica la misma persona y no se debe fusionar automáticamente.
9. Guarde los resultados y adjúntelos al informe de validación. Cualquier diferencia entre el esquema real y la matriz debe corregirse en `database/queries.py`, no agregando columnas a producción.

---

## Resumen final

- La consulta SQL se adapta a las columnas reales y omite las opcionales inexistentes antes de ejecutar.
- Una empresa con error no bloquea a las demás; los errores se clasifican y se informan.
- El resultado lógico es uniforme: `empleado`, `nombre`, `empresa`, `fuente`, `departamento`, `puesto`, `activo`, `correo`.
- La selección manual muestra empresa, fuente, departamento, puesto, estado y correo, y no elige sola.
- El área se resuelve solo con el registro elegido; nunca con el departamento de otra empresa.
- La decisión y su trazabilidad se conservan en la revisión y en la procedencia.
- El error de la hoja `Tickets` ahora se diagnostica con archivo, hojas y hoja esperada.
- **24 pruebas nuevas correctas y 102 pruebas existentes sin regresiones.**
