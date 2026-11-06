# Manual de instalación

Sistema Integral de Tickets TI - INKACROPS

---

## 1. Requisitos

| Componente | Requisito |
|------------|-----------|
| Sistema operativo | Windows 10/11 (probado en Windows) |
| Python | 3.10 o superior (validado con Python 3.15.2) |
| Librerías | `pandas`, `openpyxl` |
| Interfaz | Tkinter (incluido en la instalación estándar de Python en Windows) |
| Excel | Microsoft Excel o LibreOffice para revisar los archivos generados |

Compruebe la versión instalada:

```powershell
python --version
python -m pip --version
```

Si `python` no se reconoce, instale Python desde
<https://www.python.org/downloads/> y marque la opción **Add Python to PATH**.

---

## 2. Instalación

1. Copie la carpeta del proyecto a una ubicación local o de OneDrive.
2. Abra PowerShell en la carpeta `script automatizador`.
3. Instale las dependencias:

```powershell
cd "C:\ruta\a\script automatizador"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Contenido esperado de `requirements.txt`:

```text
pandas>=2.0
openpyxl>=3.1
```

Verifique que Tkinter esté disponible:

```powershell
python -c "import tkinter; print('Tkinter OK', tkinter.TkVersion)"
```

Si aparece un error de Tkinter, reinstale Python marcando la opción **tcl/tk and
IDLE** en el instalador.

---

## 3. Primer arranque

Desde la carpeta del proyecto:

```powershell
python main.py
```

Se abre la interfaz gráfica. La primera vez se crean automáticamente:

```text
input/     output/     data/     data/backups/
logs/      reportes/
```

La base de áreas debe existir en `data/maestro_areas.xlsx`. Si el proyecto se
entrega con esa base, no es necesario reconstruirla. Si no existe, use:

```powershell
python construir_maestro.py
```

Este comando genera `data/maestro_areas.xlsx` y su espejo
`data/maestro_areas.csv` a partir de los archivos históricos disponibles.

---

## 4. Ejecución

### 4.1 Interfaz gráfica (recomendado)

```powershell
python main.py
```

También puede abrirla explícitamente:

```powershell
python main.py --interfaz
```

### 4.2 Procesamiento por consola

```powershell
python main.py input\consulta_octubre_2026.xlsx
```

Opciones:

```powershell
python main.py archivo.xlsx --hoja "consulta_octubre_2026"
python main.py archivo.xlsx --salida output\personalizado.xlsx
python main.py archivo.xlsx --maestro data\maestro_areas.xlsx
python main.py archivo.xlsx --maestro-tickets data\maestro_tickets.xlsx
python main.py archivo.xlsx --formato-fecha DD/MM/YYYY
python main.py archivo.xlsx --reporte --consolidar
python main.py archivo.xlsx --sobrescribir
python main.py archivo.xlsx --usuario "Nombre del operador"
```

Sin `--sobrescribir`, si la salida ya existe se genera una versión con marca de
tiempo para no perder el resultado anterior.

### 4.3 Verificaciones

```powershell
python validar_septiembre.py
python auditar_sistema.py
python -m unittest discover -s tests -v
```

---

## 5. Estructura de datos

| Ruta | Contenido |
|------|-----------|
| `data/maestro_areas.xlsx` | Correspondencias `USUARIO → AREA_USUARIO` |
| `data/maestro_areas.csv` | Espejo CSV de la base de áreas |
| `data/maestro_tickets.xlsx` | Maestro histórico de tickets (11 columnas + `CONTROL`) |
| `data/maestro_tickets.csv` | Espejo CSV del detalle histórico |
| `data/maestro_tickets_historial.csv` | Cambios campo a campo del maestro |
| `data/auditoria_ejecuciones.csv` | Eventos de procesamiento, reimportación y consolidación |
| `data/maestro_historial.csv` | Historial de correcciones de áreas |
| `data/backups/` | Copias de seguridad automáticas |
| `data/estado_ui.json` | Último estado mostrado por la interfaz |
| `logs/sistema.log` | Log técnico rotativo (5 archivos de 2 MB) |

No edite manualmente los archivos del maestro. Use la interfaz o los comandos del
sistema para preservar la hoja `CONTROL`, las huellas y la trazabilidad.

---

## 6. Copias de seguridad y recuperación

- Antes de reemplazar el maestro histórico se crea una copia en
  `data/backups/maestro_tickets_AAAAMMDD_HHMMSS.xlsx`.
- La escritura usa archivos temporales y reemplazo atómico; si falla, se
  restaura la versión anterior.
- Para recuperar una copia:
  1. Abra la pestaña **Maestro histórico**.
  2. Pulse **Actualizar lista**.
  3. Seleccione la copia y pulse **Restaurar seleccionada**.
  4. Confirme la operación.
- La restauración crea primero una copia del maestro actual antes de
  reemplazarlo.

También puede restaurar desde consola:

```python
from core.maestro_tickets import MaestroTickets
MaestroTickets.restaurar_backup("data/backups/maestro_tickets_AAAAMMDD_HHMMSS.xlsx")
```

---

## 7. Solución de problemas

### «No se pudo guardar el maestro: verifique que los archivos no estén abiertos»

Cierre el archivo en Excel y vuelva a intentar. El sistema no fuerza la escritura
sobre archivos bloqueados.

### «El maestro fue editado fuera del sistema»

La hoja `CONTROL` no coincide con el detalle. Restaure una copia de seguridad
desde la pestaña **Maestro histórico** o vuelva a consolidar desde un procesado
válido.

### «Faltan columnas obligatorias» o «columnas duplicadas»

Revise el Excel de origen. Debe contener las 11 columnas oficiales (o sus alias)
y no debe tener dos columnas con datos para el mismo campo. El sistema no
descarta información automáticamente.

### «El archivo está siendo usado por otro proceso»

Existe un bloqueo exclusivo en `data/.sistema.lock`. Si el proceso anterior
terminó de forma anormal, el sistema detecta el bloqueo obsoleto y lo libera. Si
persiste, cierre las instancias abiertas y elimine el archivo solo cuando esté
seguro de que no hay procesos activos.

### Fechas interpretadas al revés

Configure explícitamente el formato:

```powershell
python main.py archivo.xlsx --formato-fecha MM/DD/YYYY
```

o seleccione el formato en la pestaña **Cargar y procesar**. El sistema nunca
interpreta una fecha ambigua en silencio.

### Los acentos se ven incorrectos en la consola

Los archivos se guardan en UTF-8. Use Windows Terminal o PowerShell actualizado.
Esto no afecta a los datos ni a los Excel generados.

---

## 8. Actualización del sistema

1. Realice una copia de `data/`.
2. Reemplace los archivos de código (`*.py`, `core/`, `config/`, `tests/`).
3. Ejecute:

```powershell
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python auditar_sistema.py
```

4. Confirme que la auditoría termina con `RESULTADO: INTEGRIDAD VERIFICADA`.
