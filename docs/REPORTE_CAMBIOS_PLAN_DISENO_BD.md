# Reporte de cambios realizados

**Documento de referencia:** [DISEÑO Y BD CONEXION.md](./DISE%C3%91O%20Y%20BD%20CONEXION.md)
**Proyecto:** Sistema Integral de Tickets TI — INKACROPS
**Fecha de revisión:** 10/11/2026

## Resumen ejecutivo

Se incorporaron al proyecto componentes para configurar y consultar SQL Server, sincronizar una caché corporativa de empleados, resolver áreas y revisar pendientes desde una ventana independiente. También se añadieron opciones de generación Excel normal y a detalle, persistencia de revisiones y elementos visuales reutilizables.

La implementación es **parcial respecto a las condiciones de aceptación del plan**. La suite existente pasó, pero no se verificó una conexión real a EXACTUS ni se encontró cobertura automatizada específica para los nuevos módulos SQL y de revisión. Por tanto, este reporte distingue el código presente de lo que aún requiere integración o comprobación.

## Cambios identificados

| Área del plan | Cambios presentes en el proyecto | Estado |
|---|---|---|
| Diseño de interfaz | Se agregaron una paleta y componentes visuales reutilizables, cambios al panel principal, controles de configuración SQL y una ventana independiente `Toplevel` para validar usuarios y áreas. | Implementado parcialmente; falta evidencia de revisión visual en las resoluciones indicadas por el plan. |
| Configuración SQL | Se definieron las 18 empresas permitidas, autenticación Windows/SQL, servidor, base, controlador, timeout, parámetros de búsqueda y columnas de unión configurables. La configuración se guarda localmente. | Implementado; los nombres reales de las columnas y relaciones deben verificarse con EXACTUS. |
| Conexión y consultas | Se añadió acceso ODBC, prueba de conexión, clasificación de errores y consultas de solo lectura con valores parametrizados. Los identificadores de esquema se limitan a la lista permitida. | Implementado en código; no probado contra un servidor real. |
| Búsqueda y sincronización | Se incorporó consulta por lotes, acceso independiente por empresa, caché Excel/CSV y resumen de sincronización. La caché corporativa está separada del maestro local `USUARIO → ÁREA`. | Implementado; requiere pruebas con datos y conexión corporativos. |
| Resolución y procedencia | Se añadieron normalización de nombres, resolución por empleados, manejo de coincidencias/conflictos y registro de procedencia de asignaciones. | Implementado en código; no hay evidencia de pruebas automatizadas específicas para estas reglas. |
| Validación y reanudación | Se añadió persistencia JSON por revisión, historial, estados, filtros y acciones desde la ventana de validación. | Implementado en código; no se verificó manualmente el flujo completo de cierre y reanudación. |
| Generación de Excel | Se añadieron modos normal y a detalle, hoja canónica `Consulta`, preservación del modo legado, copia de revisión, respaldo y escritura temporal con reemplazo atómico. | Implementado en código; los nuevos modos no tienen cobertura identificada en la suite ejecutada. |
| Compatibilidad | Se mantuvo el modo legado para conservar el comportamiento previo, incluido el flujo por consola y archivos con hoja `Tickets`. | Conservado por diseño; conviene validar los flujos completos antes de producción. |

## Archivos relacionados

### Creados

- [`config/sqlserver.py`](../config/sqlserver.py): configuración SQL y empresas permitidas.
- [`core/procedencia.py`](../core/procedencia.py): origen de asignaciones.
- [`core/revision.py`](../core/revision.py): persistencia y recuperación de revisiones.
- [`core/seguridad.py`](../core/seguridad.py): protección local de credenciales.
- [`database/`](../database/): conexión, consultas, normalización, repositorio, resolución y sincronización.
- [`ui_tema.py`](../ui_tema.py): colores y componentes visuales compartidos.
- [`ventana_validacion.py`](../ventana_validacion.py): ventana de validación independiente.

### Modificados

- [`config/configuracion.py`](../config/configuracion.py): rutas y modos de generación.
- [`core/procesado.py`](../core/procesado.py): escritura de Excel, respaldos y copias de revisión.
- [`core/procesador.py`](../core/procesador.py): integración del procesamiento con resolución de áreas y modos de salida.
- [`core/reimportacion.py`](../core/reimportacion.py): compatibilidad con los nuevos formatos de salida.
- [`interfaz.py`](../interfaz.py): controles de configuración SQL, generación y validación.
- [`panel_principal.py`](../panel_principal.py): integración visual con el tema común.

## Verificación realizada

- Comando: `python -m unittest discover -s tests -v`
- Resultado observado: **65 pruebas ejecutadas, todas aprobadas (`OK`)**.
- La suite verificada cubre el comportamiento existente del sistema. No se encontraron pruebas específicas de los módulos SQL, sincronización, credenciales o ventana de revisión.
- En el entorno revisado: Python 3.15.2; `pyodbc` no está instalado. `requirements.txt` declara `pandas` y `openpyxl`, pero no `pyodbc`.

## Pendientes y límites conocidos

1. **Dependencia ODBC:** agregar/instalar `pyodbc` y un controlador Microsoft ODBC compatible antes de habilitar una conexión real.
2. **Esquema EXACTUS:** verificar las columnas y claves de relación de `EMPLEADO`, `DEPARTAMENTO` y `PUESTO` por empresa. Los valores actuales de unión son configurables, pero no se deben considerar confirmados sin esa inspección.
3. **Pruebas SQL:** agregar pruebas con mocks para conexión, errores, timeout, cierre, consultas por lotes y fallos aislados por empresa.
4. **Pruebas de resolución y revisión:** cubrir coincidencias únicas/ambiguas, empleados sin departamento, asignaciones manuales, persistencia, reanudación y generación con pendientes.
5. **Seguridad de credenciales:** en Windows se intenta usar DPAPI. El mecanismo alternativo fuera de Windows es Base64, que no cifra el secreto y no debe considerarse almacenamiento protegido.
6. **Interfaz:** revisar manualmente el comportamiento responsivo, navegación, tablas, diálogos y botones en los tamaños de ventana del plan.
7. **Aceptación integral:** ejecutar pruebas de ambos modos Excel, manifiestos, respaldos, reimportación, consolidación, auditoría y no modificación del archivo original.

**Conclusión:** hay una base de implementación para las fases de diseño, SQL, resolución, validación y generación, y las 65 pruebas existentes siguen pasando. No se declara cumplida la aceptación integral del plan hasta resolver y verificar los pendientes anteriores.
