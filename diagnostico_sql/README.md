# Scripts de diagnóstico SQL (solo lectura)

Estos seis archivos permiten validar manualmente, en SQL Server Management
Studio (SSMS), el esquema real de `EXACTUS` y las consultas del módulo
**Validación de Usuarios y Áreas**. No contienen credenciales ni sentencias
destructivas: solo `SELECT` y metadatos.

## Orden recomendado

| Orden | Archivo | Para qué sirve |
|---|---|---|
| 1 | `00_inspeccionar_esquema.sql` | Esquemas, tablas y columnas reales de las 18 empresas; conteo de filas por empresa. |
| 2 | `01_validar_columnas.sql` | Matriz de columnas fundamentales y opcionales; columnas que faltan por empresa. |
| 3 | `02_consultas_originales.sql` | Reproduce la consulta anterior y sus errores (`Invalid column name 'FUENTE'`, `'U_CORREO_CORP'`, etc.). |
| 4 | `03_consultas_corregidas.sql` | Versiones que omiten columnas inexistentes y normalizan los ocho campos lógicos. |
| 5 | `04_validacion_por_empresa.sql` | Una consulta por empresa para comparar esquemas y resultados. |
| 6 | `05_validar_empleados_duplicados.sql` | Coincidencias de código/nombre entre empresas y estados activo/inactivo. No fusiona ni elimina. |

## Cómo ejecutarlos en SSMS

1. Abra SSMS y conéctese con un login de **solo lectura** sobre `EXACTUS`.
2. Abra el archivo `.sql` correspondiente.
3. Ejecute con `F5`.
4. Copie el resultado o guarde el mensaje de error completo.

Cada script empieza con `USE [EXACTUS];` y `GO`, por lo que puede ejecutarse de
forma independiente.

## Cómo interpretar los resultados

- **Empresa poblada:** `EMPLEADO` tiene al menos `EMPLEADO` y `NOMBRE`, y sus
  tablas `DEPARTAMENTO`/`PUESTO` permiten resolver descripciones.
- **Empresa vacía o mínima:** `EMPLEADO` solo tiene `EMPLEADO`, `NOMBRE`,
  `ACTIVO` y columnas de auditoría. La consulta adaptativa devuelve `NULL` en
  departamento, puesto y correo, sin fallar.
- **Columna `FUENTE`:** no existe en ninguna empresa. Se genera como literal
  `'EMPLEADO'` en el `SELECT`.
- **`U_CORREO_CORP`:** solo existe en algunas empresas. Si falta, se usa
  `E_MAIL`; si tampoco existe, se devuelve `NULL`.
- **Código repetido entre empresas:** no implica la misma persona. Cada fila se
  conserva por `(empresa, fuente, código)` y la decisión es manual.

## Validación pendiente en SSMS

Los scripts están escritos para SQL Server 2016 (sin `STRING_AGG`). Cualquier
consulta que dependa de un esquema no confirmado se marca en el propio archivo
como `PENDIENTE DE VALIDACIÓN EN SSMS`. El código del repositorio no se conecta
a producción durante las pruebas automatizadas.
