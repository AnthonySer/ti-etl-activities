"""Sección «Consultas SQL» de la interfaz principal.

La sección es independiente de los módulos de tickets, revisiones, reportes y
Anexo 5. Reutiliza la conexión SQL Server existente y el mecanismo de tareas en
segundo plano de la ventana principal.
"""

from __future__ import annotations

import os
import re
import subprocess
import tkinter as tk
import uuid
from datetime import date, datetime
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any, Callable

from config import configuracion as cfg
from core import consultas_sql as svc
from ui_tema import (
    AZUL_PRINCIPAL,
    AZUL_SUAVE,
    BORDE,
    FONDO,
    FUENTE,
    FUENTE_MONO,
    MORADO,
    MORADO_SUAVE,
    NARANJA_SUAVE,
    ROJO,
    ROJO_SUAVE,
    SUPERFICIE,
    SUPERFICIE_ALT,
    TEXTO,
    TEXTO_SECUNDARIO,
    VERDE,
    VERDE_SUAVE,
)

PLACEHOLDER_GUARDADAS = "— Seleccione —"


def _abrir_archivo(ruta: str | Path) -> None:
    try:
        os.startfile(str(ruta))  # type: ignore[attr-defined]
    except AttributeError:  # pragma: no cover - otros sistemas
        subprocess.Popen(["xdg-open", str(ruta)])
    except OSError:
        subprocess.Popen(["start", "", str(ruta)], shell=True)


def _formatear_valor(valor: Any, tipo: str = "Texto") -> str:
    if valor is None:
        return ""
    if isinstance(valor, bool):
        return "Sí" if valor else "No"
    if isinstance(valor, datetime):
        if valor.hour or valor.minute or valor.second:
            return valor.strftime("%d/%m/%Y %H:%M:%S")
        return valor.strftime("%d/%m/%Y")
    if isinstance(valor, date):
        return valor.strftime("%d/%m/%Y")
    if isinstance(valor, float):
        return f"{valor:,.2f}"
    return str(valor)


class SeccionConsultasSQL(ttk.Frame):
    """Editor, ejecución, configuración de columnas y exportación a Excel."""

    _PALABRAS = (
        "SELECT", "FROM", "WHERE", "JOIN", "LEFT", "RIGHT", "INNER", "OUTER",
        "FULL", "CROSS", "ON", "GROUP", "BY", "ORDER", "HAVING", "AS", "AND",
        "OR", "NOT", "NULL", "IS", "IN", "EXISTS", "UNION", "ALL", "DISTINCT",
        "TOP", "WITH", "CASE", "WHEN", "THEN", "ELSE", "END", "COUNT", "SUM",
        "AVG", "MIN", "MAX", "CAST", "CONVERT", "DESC", "ASC", "LIKE",
        "BETWEEN", "OFFSET", "FETCH", "NEXT", "ROWS", "ONLY", "USING", "OVER",
        "PARTITION", "NULLIF", "COALESCE", "ISNULL", "GETDATE",
    )
    _PATRON_RESALTADO = re.compile(
        r"(--[^\n]*|/\*.*?\*/|'(?:''|[^'])*'|\b(?:"
        + "|".join(_PALABRAS)
        + r")\b|\b\d+(?:\.\d+)?\b)",
        re.IGNORECASE | re.DOTALL,
    )

    def __init__(self, master: tk.Misc, app: Any) -> None:
        super().__init__(master)
        self.app = app
        self._resultado: dict[str, Any] | None = None
        self._columnas: list[dict[str, Any]] = []
        self._columnas_guardadas: list[dict[str, Any]] | None = None
        self._consulta_actual_id: str | None = None
        self._nombre_actual = ""
        self._guardadas: list[dict[str, Any]] = []
        self._resaltando = False
        self._construir()
        self._cargar_guardadas()

    # ------------------------------------------------------------------
    # Construcción visual
    # ------------------------------------------------------------------
    def _construir(self) -> None:
        cabecera = ttk.Frame(self)
        cabecera.pack(fill="x")
        ttk.Label(
            cabecera, text="Consultas SQL", style="Titulo.TLabel"
        ).pack(anchor="w")
        ttk.Label(
            cabecera,
            text=(
                "Ejecute consultas SELECT de solo lectura, personalice las "
                "columnas y genere un Excel con el orden y los encabezados "
                "definidos."
            ),
            style="Subtitulo.TLabel",
        ).pack(anchor="w", pady=(2, 0))

        barra = ttk.Frame(self)
        barra.pack(fill="x", pady=(10, 4))
        self._btn_ejecutar = ttk.Button(
            barra,
            text="▶ Ejecutar consulta",
            style="Principal.TButton",
            command=self._ejecutar,
        )
        self._btn_ejecutar.pack(side="left")
        ttk.Button(barra, text="Limpiar", command=self._limpiar).pack(
            side="left", padx=(6, 0)
        )
        ttk.Button(
            barra, text="Guardar consulta", command=self._guardar_consulta
        ).pack(side="left", padx=(6, 0))

        barra_guardadas = ttk.Frame(self)
        barra_guardadas.pack(fill="x", pady=(0, 8))
        ttk.Label(
            barra_guardadas, text="Consultas guardadas:"
        ).pack(side="left")
        self._combo_guardadas = ttk.Combobox(
            barra_guardadas, state="readonly", width=34
        )
        self._combo_guardadas.pack(side="left", padx=(6, 6))
        ttk.Button(
            barra_guardadas, text="Cargar", command=self._cargar_seleccionada
        ).pack(side="left")
        ttk.Button(
            barra_guardadas,
            text="Eliminar",
            command=self._eliminar_seleccionada,
        ).pack(side="left", padx=(6, 0))
        ttk.Button(
            barra_guardadas,
            text="Historial",
            command=self._abrir_historial,
        ).pack(side="left", padx=(6, 0))

        paned = ttk.Panedwindow(self, orient="vertical")
        paned.pack(fill="both", expand=True)

        editor_marco = ttk.LabelFrame(paned, text="Editor SQL", padding=8)
        resultado_marco = ttk.Frame(paned)
        paned.add(editor_marco, weight=1)
        paned.add(resultado_marco, weight=3)

        ttk.Label(
            editor_marco,
            text=(
                "Solo SELECT y WITH … SELECT · Ctrl+Enter o F5 para ejecutar · "
                "No se permiten sentencias de escritura ni consultas múltiples."
            ),
            style="TarjetaSecundaria.TLabel",
        ).pack(anchor="w", pady=(0, 4))

        texto_marco = ttk.Frame(editor_marco)
        texto_marco.pack(fill="both", expand=True)
        self._editor = tk.Text(
            texto_marco,
            height=9,
            wrap="none",
            undo=True,
            font=(FUENTE_MONO, 10),
            bg=SUPERFICIE,
            fg=TEXTO,
            insertbackground=TEXTO,
            relief="flat",
            highlightthickness=1,
            highlightbackground=BORDE,
            highlightcolor=AZUL_PRINCIPAL,
        )
        scroll_y = ttk.Scrollbar(
            texto_marco, orient="vertical", command=self._editor.yview
        )
        scroll_x = ttk.Scrollbar(
            texto_marco, orient="horizontal", command=self._editor.xview
        )
        self._editor.configure(
            yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set
        )
        self._editor.grid(row=0, column=0, sticky="nsew")
        scroll_y.grid(row=0, column=1, sticky="ns")
        scroll_x.grid(row=1, column=0, sticky="ew")
        texto_marco.grid_rowconfigure(0, weight=1)
        texto_marco.grid_columnconfigure(0, weight=1)

        self._editor.tag_configure(
            "sql_kw",
            foreground=AZUL_PRINCIPAL,
            font=(FUENTE_MONO, 10, "bold"),
        )
        self._editor.tag_configure("sql_str", foreground="#0F7A4F")
        self._editor.tag_configure(
            "sql_com", foreground="#7C8AA5", font=(FUENTE_MONO, 10, "italic")
        )
        self._editor.tag_configure("sql_num", foreground=MORADO)
        self._editor.bind("<KeyRelease>", self._resaltar)
        self._editor.bind("<Control-Return>", self._atajo_ejecutar)
        self._editor.bind("<F5>", self._atajo_ejecutar)

        estado_marco = ttk.Frame(resultado_marco)
        estado_marco.pack(fill="x", pady=(8, 4))
        self._lbl_estado = tk.Label(
            estado_marco,
            text="Listo para ejecutar",
            bg=FONDO,
            fg=TEXTO_SECUNDARIO,
            font=(FUENTE, 10, "bold"),
            anchor="w",
        )
        self._lbl_estado.pack(side="left")
        self._lbl_metricas = tk.Label(
            estado_marco,
            text="",
            bg=FONDO,
            fg=TEXTO_SECUNDARIO,
            font=(FUENTE, 9),
            anchor="e",
        )
        self._lbl_metricas.pack(side="right")

        self._txt_mensaje = tk.Text(
            resultado_marco,
            height=3,
            wrap="word",
            font=(FUENTE, 9),
            bg=SUPERFICIE_ALT,
            fg=TEXTO_SECUNDARIO,
            relief="flat",
            bd=0,
            padx=8,
            pady=4,
            state="disabled",
        )
        self._txt_mensaje.pack(fill="x")

        previa_barra = ttk.Frame(resultado_marco)
        previa_barra.pack(fill="x", pady=(8, 4))
        ttk.Label(
            previa_barra, text="Vista previa", style="Seccion.TLabel"
        ).pack(side="left")
        self._lbl_previa_info = ttk.Label(
            previa_barra, text="", style="Subtitulo.TLabel"
        )
        self._lbl_previa_info.pack(side="left", padx=(10, 0))
        self._btn_excel = ttk.Button(
            previa_barra,
            text="Generar Excel",
            style="Verde.TButton",
            command=self._generar_excel,
            state="disabled",
        )
        self._btn_excel.pack(side="right")
        self._btn_configurar = ttk.Button(
            previa_barra,
            text="Configurar columnas",
            command=self._abrir_configuracion,
            state="disabled",
        )
        self._btn_configurar.pack(side="right", padx=(0, 6))

        marco_tabla = ttk.Frame(resultado_marco)
        marco_tabla.pack(fill="both", expand=True)
        self._tabla = ttk.Treeview(
            marco_tabla, show="headings", selectmode="browse"
        )
        tabla_y = ttk.Scrollbar(
            marco_tabla, orient="vertical", command=self._tabla.yview
        )
        tabla_x = ttk.Scrollbar(
            marco_tabla, orient="horizontal", command=self._tabla.xview
        )
        self._tabla.configure(
            yscrollcommand=tabla_y.set, xscrollcommand=tabla_x.set
        )
        self._tabla.grid(row=0, column=0, sticky="nsew")
        tabla_y.grid(row=0, column=1, sticky="ns")
        tabla_x.grid(row=1, column=0, sticky="ew")
        marco_tabla.grid_rowconfigure(0, weight=1)
        marco_tabla.grid_columnconfigure(0, weight=1)

        self._lbl_vacio = ttk.Label(
            marco_tabla,
            text="Ejecute una consulta para ver los resultados.",
            style="Subtitulo.TLabel",
        )
        self._lbl_vacio.place(relx=0.5, rely=0.4, anchor="center")

        self._mensaje(
            "Escriba una consulta SELECT y presione «Ejecutar consulta». "
            "Solo se permiten operaciones de lectura.",
            "info",
        )

    # ------------------------------------------------------------------
    # Utilidades de interfaz
    # ------------------------------------------------------------------
    def _mensaje(self, texto: str, tipo: str = "info") -> None:
        colores = {
            "info": (SUPERFICIE_ALT, TEXTO_SECUNDARIO),
            "success": (VERDE_SUAVE, "#0F7A4F"),
            "warning": (NARANJA_SUAVE, "#A96500"),
            "danger": (ROJO_SUAVE, "#B52F2F"),
            "neutral": (SUPERFICIE_ALT, TEXTO_SECUNDARIO),
        }
        fondo, color = colores.get(tipo, colores["info"])
        self._txt_mensaje.configure(state="normal", bg=fondo, fg=color)
        self._txt_mensaje.delete("1.0", "end")
        self._txt_mensaje.insert("1.0", texto)
        self._txt_mensaje.configure(state="disabled")

    def _estado(self, texto: str, tipo: str = "neutral") -> None:
        colores = {
            "success": VERDE,
            "danger": ROJO,
            "warning": "#A96500",
            "info": AZUL_PRINCIPAL,
            "neutral": TEXTO_SECUNDARIO,
        }
        self._lbl_estado.configure(
            text=texto, fg=colores.get(tipo, TEXTO_SECUNDARIO)
        )

    def _metricas(self, texto: str) -> None:
        self._lbl_metricas.configure(text=texto)

    def _iniciar_ocupado(self, mensaje: str) -> None:
        if hasattr(self.app, "_iniciar_ocupado"):
            self.app._iniciar_ocupado(mensaje)

    def _terminar_ocupado(self) -> None:
        if hasattr(self.app, "_terminar_ocupado"):
            self.app._terminar_ocupado()

    def _ejecutar_hilo(
        self,
        tarea: Callable[[], Any],
        al_terminar: Callable[[Any], None],
        descripcion: str,
    ) -> None:
        if hasattr(self.app, "_ejecutar_hilo"):
            self.app._ejecutar_hilo(tarea, al_terminar, descripcion)
        else:  # pragma: no cover - respaldo para pruebas aisladas
            try:
                resultado = tarea()
            except Exception as exc:  # noqa: BLE001
                messagebox.showerror("Error", f"{descripcion}: {exc}")
                return
            al_terminar(resultado)

    def _atajo_ejecutar(self, _evento=None) -> str:
        self._ejecutar()
        return "break"

    def _sql_editor(self) -> str:
        return self._editor.get("1.0", "end-1c").strip()

    def _nombre_para_historial(self) -> str:
        return self._nombre_actual or "(sin nombre)"

    def _registrar_historial(
        self,
        *,
        sql: str,
        total: int,
        duracion: float,
        exito: bool,
        mensaje: str = "",
    ) -> None:
        try:
            svc.registrar_historial(
                nombre=self._nombre_para_historial(),
                sql=sql,
                total=total,
                duracion=duracion,
                exito=exito,
                mensaje=mensaje,
            )
        except Exception:  # noqa: BLE001 - el historial no debe romper la consulta
            pass

    def _resaltar(self, _evento=None) -> None:
        if self._resaltando:
            return
        self._resaltando = True
        try:
            contenido = self._editor.get("1.0", "end-1c")
            for etiqueta in ("sql_kw", "sql_str", "sql_com", "sql_num"):
                self._editor.tag_remove(etiqueta, "1.0", "end")
            for coincidencia in self._PATRON_RESALTADO.finditer(contenido):
                texto = coincidencia.group(0)
                if texto.startswith("--") or texto.startswith("/*"):
                    etiqueta = "sql_com"
                elif texto.startswith("'"):
                    etiqueta = "sql_str"
                elif texto[:1].isdigit():
                    etiqueta = "sql_num"
                else:
                    etiqueta = "sql_kw"
                self._editor.tag_add(
                    etiqueta,
                    f"1.0+{coincidencia.start()}c",
                    f"1.0+{coincidencia.end()}c",
                )
        finally:
            self._resaltando = False

    # ------------------------------------------------------------------
    # Ejecución y vista previa
    # ------------------------------------------------------------------
    def _ejecutar(self) -> None:
        if getattr(self.app, "_ocupado", False):
            messagebox.showinfo(
                "Operación en curso",
                "Espere a que finalice la operación actual.",
            )
            return
        sql = self._sql_editor()
        if not sql:
            self._estado("Sin consulta", "warning")
            self._mensaje("Escriba una consulta SELECT antes de ejecutar.", "warning")
            self._editor.focus_set()
            return

        self._resultado = None
        self._columnas = []
        self._btn_configurar.configure(state="disabled")
        self._btn_excel.configure(state="disabled")
        self._btn_ejecutar.configure(state="disabled")
        self._estado("Ejecutando consulta…", "warning")
        self._metricas("")
        self._mensaje("Ejecutando consulta en segundo plano…", "info")
        self._iniciar_ocupado("Ejecutando consulta SQL…")

        def tarea() -> dict[str, Any]:
            try:
                return {"ok": True, "resultado": svc.ejecutar_consulta(sql)}
            except Exception as exc:  # noqa: BLE001 - se muestra en la sección
                return {
                    "ok": False,
                    "error": str(exc),
                    "tipo": type(exc).__name__,
                }

        def fin(respuesta: dict[str, Any]) -> None:
            self._terminar_ocupado()
            self._btn_ejecutar.configure(state="normal")
            if not respuesta.get("ok"):
                error = str(respuesta.get("error") or "Error desconocido.")
                self._estado("Error de consulta", "danger")
                self._metricas("")
                self._mensaje(error, "danger")
                self._registrar_historial(
                    sql=sql,
                    total=0,
                    duracion=0,
                    exito=False,
                    mensaje=error,
                )
                return

            resultado = respuesta["resultado"]
            self._resultado = resultado
            self._estado("Consulta ejecutada correctamente", "success")
            self._metricas(
                f"{resultado['total']} registro(s) · "
                f"{resultado['duracion']:.2f} s · "
                f"{len(resultado['columnas'])} columna(s)"
            )
            self._registrar_historial(
                sql=sql,
                total=resultado["total"],
                duracion=resultado["duracion"],
                exito=True,
                mensaje="",
            )
            if not resultado["columnas"]:
                self._mensaje(
                    "La consulta no devolvió columnas. Revise la sentencia.",
                    "warning",
                )
                return
            if resultado["total"] == 0:
                self._mensaje(
                    "La consulta se ejecutó correctamente y devolvió 0 registros. "
                    "Puede configurar las columnas y generar un Excel con encabezados.",
                    "warning",
                )
            else:
                self._mensaje(
                    "Consulta ejecutada correctamente. Configure las columnas "
                    "del Excel para continuar.",
                    "success",
                )
            self._abrir_configuracion()

        self._ejecutar_hilo(tarea, fin, "consulta SQL")

    def _abrir_configuracion(self) -> None:
        if not self._resultado:
            messagebox.showinfo(
                "Sin resultados",
                "Ejecute primero una consulta para configurar sus columnas.",
            )
            return
        disponibles = svc.columnas_desde_resultado(
            self._resultado["columnas"], self._resultado["filas"]
        )
        guardadas = self._columnas or self._columnas_guardadas
        modal = ModalConfigurarColumnas(self, disponibles, guardadas)
        self.wait_window(modal)
        if modal.resultado is None:
            self._mensaje(
                "Configuración de columnas cancelada. La vista previa no cambió.",
                "warning",
            )
            return
        self._columnas = modal.resultado
        self._mostrar_previa()

    def _mostrar_previa(self) -> None:
        if not self._resultado:
            return
        tabla = svc.construir_tabla(
            self._columnas, self._resultado["filas"]
        )
        self._tabla.delete(*self._tabla.get_children())
        identificadores = [
            f"c{indice}" for indice in range(len(tabla["encabezados"]))
        ]
        self._tabla.configure(columns=identificadores)
        for indice, (identificador, encabezado) in enumerate(
            zip(identificadores, tabla["encabezados"])
        ):
            self._tabla.heading(identificador, text=encabezado)
            ancho = max(100, min(320, len(str(encabezado)) * 8 + 28))
            for fila in tabla["filas"][:100]:
                valor = fila[indice] if indice < len(fila) else ""
                ancho = max(
                    ancho, min(320, len(_formatear_valor(valor)) * 7 + 24)
                )
            self._tabla.column(
                identificador, width=ancho, minwidth=80, stretch=True
            )

        for fila in tabla["filas"][:100]:
            valores = [
                _formatear_valor(valor, tipo)
                for valor, tipo in zip(fila, tabla["tipos"])
            ]
            self._tabla.insert("", "end", values=valores)

        total = self._resultado["total"]
        mostrados = min(100, total)
        self._lbl_previa_info.configure(
            text=(
                f"Mostrando {mostrados} de {total} registro(s) · "
                f"{len(tabla['encabezados'])} columna(s)"
            )
        )
        if total == 0:
            self._lbl_vacio.configure(
                text=(
                    "La consulta devolvió 0 registros. El Excel se generará "
                    "únicamente con los encabezados configurados."
                )
            )
            self._lbl_vacio.place(relx=0.5, rely=0.4, anchor="center")
        else:
            self._lbl_vacio.place_forget()
        self._btn_excel.configure(state="normal")
        self._btn_configurar.configure(state="normal")
        self._mensaje(
            "Vista previa actualizada. Puede generar el Excel con todos los "
            "registros recuperados.",
            "success",
        )

    def _generar_excel(self) -> None:
        if not self._resultado or not self._columnas:
            messagebox.showinfo(
                "Sin configuración",
                "Ejecute la consulta y configure las columnas antes de exportar.",
            )
            return
        marca = datetime.now().strftime("%Y%m%d_%H%M%S")
        inicial = cfg.OUTPUT_DIR if Path(cfg.OUTPUT_DIR).is_dir() else cfg.BASE_DIR
        ruta = filedialog.asksaveasfilename(
            title="Guardar Excel de la consulta",
            initialdir=str(inicial),
            initialfile=f"Consulta_SQL_{marca}.xlsx",
            defaultextension=".xlsx",
            filetypes=[("Excel", "*.xlsx")],
        )
        if not ruta:
            return

        columnas = [dict(columna) for columna in self._columnas]
        filas = self._resultado["filas"]
        self._iniciar_ocupado("Generando Excel…")
        self._btn_excel.configure(state="disabled")

        def tarea() -> dict[str, Any]:
            try:
                tabla = svc.construir_tabla(columnas, filas)
                destino = svc.exportar_excel(
                    ruta,
                    tabla["encabezados"],
                    tabla["filas"],
                    tabla["tipos"],
                )
                return {"ok": True, "ruta": str(destino)}
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": str(exc)}

        def fin(respuesta: dict[str, Any]) -> None:
            self._terminar_ocupado()
            self._btn_excel.configure(state="normal")
            if not respuesta.get("ok"):
                messagebox.showerror(
                    "No se pudo generar el Excel",
                    str(respuesta.get("error") or "Error desconocido."),
                )
                return
            destino = respuesta["ruta"]
            self._mensaje(f"Excel generado correctamente: {destino}", "success")
            if messagebox.askyesno(
                "Excel generado",
                f"Archivo guardado en:\n{destino}\n\n¿Desea abrirlo ahora?",
            ):
                try:
                    _abrir_archivo(destino)
                except Exception as exc:  # noqa: BLE001
                    messagebox.showerror("No se pudo abrir", str(exc))

        self._ejecutar_hilo(tarea, fin, "exportación Excel")

    # ------------------------------------------------------------------
    # Limpiar, guardar, cargar e historial
    # ------------------------------------------------------------------
    def _limpiar(self) -> None:
        self._editor.delete("1.0", "end")
        self._editor.focus_set()
        self._resultado = None
        self._columnas = []
        self._columnas_guardadas = None
        self._consulta_actual_id = None
        self._nombre_actual = ""
        self._tabla.delete(*self._tabla.get_children())
        self._tabla.configure(columns=())
        self._btn_configurar.configure(state="disabled")
        self._btn_excel.configure(state="disabled")
        self._lbl_previa_info.configure(text="")
        self._lbl_vacio.configure(
            text="Ejecute una consulta para ver los resultados."
        )
        self._lbl_vacio.place(relx=0.5, rely=0.4, anchor="center")
        self._combo_guardadas.set(PLACEHOLDER_GUARDADAS)
        self._estado("Listo para ejecutar", "neutral")
        self._metricas("")
        self._mensaje(
            "Editor limpio. Escriba una consulta SELECT para comenzar.", "info"
        )

    def _cargar_guardadas(self, seleccionar: str | None = None) -> None:
        datos = svc.cargar_datos()
        self._guardadas = datos["guardadas"]
        nombres = [PLACEHOLDER_GUARDADAS] + [
            str(consulta.get("nombre")) for consulta in self._guardadas
        ]
        self._combo_guardadas.configure(values=nombres)
        self._combo_guardadas.set(seleccionar or PLACEHOLDER_GUARDADAS)

    def _consulta_seleccionada(self) -> dict[str, Any] | None:
        nombre = self._combo_guardadas.get()
        if not nombre or nombre == PLACEHOLDER_GUARDADAS:
            return None
        return next(
            (
                consulta
                for consulta in self._guardadas
                if str(consulta.get("nombre")) == nombre
            ),
            None,
        )

    def _guardar_consulta(self) -> None:
        sql = self._sql_editor()
        if not sql:
            messagebox.showinfo(
                "Sin consulta", "Escriba una consulta antes de guardarla."
            )
            return
        nombre = simpledialog.askstring(
            "Guardar consulta",
            "Nombre de la consulta:",
            initialvalue=self._nombre_actual,
            parent=self,
        )
        if not nombre or not nombre.strip():
            return
        columnas = self._columnas or self._columnas_guardadas or None
        try:
            entrada = svc.guardar_consulta(
                nombre.strip(),
                sql,
                columnas,
                id=self._consulta_actual_id,
            )
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("No se pudo guardar", str(exc))
            return
        self._consulta_actual_id = entrada.get("id")
        self._nombre_actual = str(entrada.get("nombre") or nombre)
        self._columnas_guardadas = (
            svc.normalizar_columnas(entrada.get("columnas"))
            or self._columnas_guardadas
        )
        self._cargar_guardadas(seleccionar=self._nombre_actual)
        self._mensaje(
            f"Consulta guardada: {self._nombre_actual}", "success"
        )
        self._estado("Consulta guardada", "success")

    def _cargar_seleccionada(self) -> None:
        entrada = self._consulta_seleccionada()
        if entrada is None:
            messagebox.showinfo(
                "Seleccione una consulta",
                "Elija una consulta guardada en la lista.",
            )
            return
        self._editor.delete("1.0", "end")
        self._editor.insert("1.0", str(entrada.get("sql") or ""))
        self._resaltar()
        self._consulta_actual_id = entrada.get("id")
        self._nombre_actual = str(entrada.get("nombre") or "")
        self._columnas_guardadas = (
            svc.normalizar_columnas(entrada.get("columnas")) or None
        )
        self._columnas = []
        self._resultado = None
        self._tabla.delete(*self._tabla.get_children())
        self._tabla.configure(columns=())
        self._btn_configurar.configure(state="disabled")
        self._btn_excel.configure(state="disabled")
        self._lbl_previa_info.configure(text="")
        self._lbl_vacio.configure(
            text="Ejecute la consulta cargada para ver los resultados."
        )
        self._lbl_vacio.place(relx=0.5, rely=0.4, anchor="center")
        self._estado("Consulta cargada", "info")
        self._metricas("")
        if self._columnas_guardadas:
            self._mensaje(
                "Consulta cargada. Al ejecutarla se aplicará la configuración "
                "de columnas guardada.",
                "info",
            )
        else:
            self._mensaje(
                "Consulta cargada. Presione «Ejecutar consulta» para probarla.",
                "info",
            )

    def _eliminar_seleccionada(self) -> None:
        entrada = self._consulta_seleccionada()
        if entrada is None:
            messagebox.showinfo(
                "Seleccione una consulta",
                "Elija una consulta guardada en la lista.",
            )
            return
        nombre = str(entrada.get("nombre") or "")
        if not messagebox.askyesno(
            "Eliminar consulta",
            f"¿Eliminar la consulta guardada «{nombre}»?",
        ):
            return
        if svc.eliminar_consulta(str(entrada.get("id") or "")):
            if self._consulta_actual_id == entrada.get("id"):
                self._consulta_actual_id = None
                self._nombre_actual = ""
                self._columnas_guardadas = None
            self._cargar_guardadas()
            self._mensaje(f"Consulta eliminada: {nombre}", "success")
            self._estado("Consulta eliminada", "success")

    def _abrir_historial(self) -> None:
        VentanaHistorial(self, self._cargar_desde_historial)

    def _cargar_desde_historial(self, sql: str, nombre: str) -> None:
        self._editor.delete("1.0", "end")
        self._editor.insert("1.0", str(sql or ""))
        self._resaltar()
        self._consulta_actual_id = None
        self._nombre_actual = "" if nombre == "(sin nombre)" else str(nombre)
        self._columnas_guardadas = None
        self._columnas = []
        self._resultado = None
        self._tabla.delete(*self._tabla.get_children())
        self._tabla.configure(columns=())
        self._btn_configurar.configure(state="disabled")
        self._btn_excel.configure(state="disabled")
        self._lbl_previa_info.configure(text="")
        self._lbl_vacio.configure(
            text="Ejecute la consulta recuperada para ver los resultados."
        )
        self._lbl_vacio.place(relx=0.5, rely=0.4, anchor="center")
        self._combo_guardadas.set(PLACEHOLDER_GUARDADAS)
        self._estado("SQL cargado desde el historial", "info")
        self._metricas("")
        self._mensaje(
            "Sentencia recuperada del historial. Presione «Ejecutar consulta».",
            "info",
        )


class ModalConfigurarColumnas(tk.Toplevel):
    """Modal de configuración de columnas y orden del Excel."""

    def __init__(
        self,
        master: tk.Misc,
        columnas_disponibles: list[dict[str, Any]],
        columnas_guardadas: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(master)
        self.title("Configurar columnas del Excel")
        self.configure(bg=FONDO)
        self.geometry("1120x720")
        self.minsize(900, 600)
        self.transient(master.winfo_toplevel())
        self.resultado: list[dict[str, Any]] | None = None
        self._disponibles = [dict(columna) for columna in columnas_disponibles]
        self._seleccionadas = self._combinar(columnas_guardadas)
        self._construir()
        self._centrar(master)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._cancelar)
        self.bind("<Escape>", lambda _evento: self._cancelar())

    def _combinar(
        self, columnas_guardadas: list[dict[str, Any]] | None
    ) -> list[dict[str, Any]]:
        if not columnas_guardadas:
            return [self._nueva_sql(columna) for columna in self._disponibles]
        disponibles = {
            str(columna.get("campo") or ""): columna
            for columna in self._disponibles
        }
        seleccionadas: list[dict[str, Any]] = []
        for guardada in svc.normalizar_columnas(columnas_guardadas):
            if guardada["origen"] == "manual":
                seleccionadas.append(dict(guardada))
                continue
            disponible = disponibles.get(str(guardada.get("campo") or ""))
            if disponible is None:
                continue
            nueva = dict(guardada)
            nueva["id"] = disponible.get("id") or nueva.get("id")
            nueva["campo"] = disponible.get("campo")
            nueva["encabezado"] = (
                guardada.get("encabezado")
                or disponible.get("encabezado")
            )
            nueva["tipo"] = disponible.get("tipo", "Texto")
            nueva["valor"] = ""
            seleccionadas.append(nueva)
        return seleccionadas

    def _nueva_sql(self, columna: dict[str, Any]) -> dict[str, Any]:
        campo = str(columna.get("campo") or columna.get("clave") or "")
        return {
            "id": str(columna.get("id") or f"sql:{campo}"),
            "origen": "sql",
            "campo": campo,
            "encabezado": str(
                columna.get("encabezado") or columna.get("etiqueta") or campo
            ),
            "tipo": str(columna.get("tipo") or "Texto"),
            "valor": "",
        }

    def _centrar(self, master: tk.Misc) -> None:
        self.update_idletasks()
        try:
            raiz = master.winfo_toplevel()
            x = raiz.winfo_rootx() + max(
                0, (raiz.winfo_width() - self.winfo_width()) // 2
            )
            y = raiz.winfo_rooty() + max(
                0, (raiz.winfo_height() - self.winfo_height()) // 2
            )
            self.geometry(f"+{x}+{y}")
        except tk.TclError:  # pragma: no cover
            pass

    def _construir(self) -> None:
        ttk.Label(
            self, text="Configurar columnas del Excel", style="Titulo.TLabel"
        ).pack(anchor="w", padx=16, pady=(14, 0))
        ttk.Label(
            self,
            text=(
                "Seleccione las columnas, ajuste su orden y cambie el "
                "encabezado si lo necesita. Las columnas manuales no provienen "
                "de la base de datos."
            ),
            style="Subtitulo.TLabel",
            wraplength=1040,
            justify="left",
        ).pack(anchor="w", padx=16, pady=(2, 8))

        cuerpo = ttk.Frame(self)
        cuerpo.pack(fill="both", expand=True, padx=16)
        izquierda = ttk.LabelFrame(
            cuerpo, text="Columnas disponibles", padding=8
        )
        derecha = ttk.LabelFrame(
            cuerpo, text="Columnas seleccionadas (orden del Excel)", padding=8
        )
        izquierda.pack(side="left", fill="both", expand=True, padx=(0, 8))
        derecha.pack(side="left", fill="both", expand=True, padx=(8, 0))

        self._interior_disponibles = self._crear_scrollable(izquierda)
        pie_derecha = ttk.Frame(derecha)
        pie_derecha.pack(side="bottom", fill="x", pady=(6, 0))
        ttk.Button(
            pie_derecha,
            text="+ Agregar columna manual",
            command=self._agregar_manual,
        ).pack(anchor="w")
        self._interior_seleccionadas = self._crear_scrollable(derecha)

        pie = ttk.Frame(self)
        pie.pack(fill="x", padx=16, pady=12)
        ttk.Button(
            pie,
            text="Restaurar columnas originales",
            command=self._restaurar,
        ).pack(side="left")
        ttk.Button(pie, text="Cancelar", command=self._cancelar).pack(
            side="right", padx=(6, 0)
        )
        ttk.Button(
            pie,
            text="Aceptar y continuar",
            style="Principal.TButton",
            command=self._aceptar,
        ).pack(side="right")

        self._actualizar_disponibles()
        self._actualizar_seleccionadas()

    def _crear_scrollable(self, padre: tk.Misc) -> ttk.Frame:
        contenedor = ttk.Frame(padre)
        contenedor.pack(fill="both", expand=True)
        lienzo = tk.Canvas(
            contenedor, bg=SUPERFICIE, highlightthickness=0, bd=0
        )
        barra = ttk.Scrollbar(
            contenedor, orient="vertical", command=lienzo.yview
        )
        interior = ttk.Frame(lienzo, style="Superficie.TFrame")
        ventana = lienzo.create_window((0, 0), window=interior, anchor="nw")
        interior.bind(
            "<Configure>",
            lambda _evento: lienzo.configure(
                scrollregion=lienzo.bbox("all")
            ),
        )
        lienzo.bind(
            "<Configure>",
            lambda evento: lienzo.itemconfigure(
                ventana, width=evento.width
            ),
        )
        lienzo.configure(yscrollcommand=barra.set)
        lienzo.pack(side="left", fill="both", expand=True)
        barra.pack(side="right", fill="y")

        def _rueda(evento: tk.Event) -> None:
            lienzo.yview_scroll(int(-evento.delta / 120), "units")

        lienzo.bind("<Enter>", lambda _e: lienzo.bind_all("<MouseWheel>", _rueda))
        lienzo.bind("<Leave>", lambda _e: lienzo.unbind_all("<MouseWheel>"))
        return interior

    def _actualizar_disponibles(self) -> None:
        for hijo in self._interior_disponibles.winfo_children():
            hijo.destroy()
        for columna in self._disponibles:
            seleccionada = any(
                item["origen"] == "sql"
                and item.get("campo") == columna.get("campo")
                for item in self._seleccionadas
            )
            variable = tk.BooleanVar(value=seleccionada)
            fila = ttk.Frame(
                self._interior_disponibles, style="Superficie.TFrame"
            )
            fila.pack(fill="x", pady=2)
            ttk.Checkbutton(
                fila,
                text=str(
                    columna.get("encabezado")
                    or columna.get("etiqueta")
                    or columna.get("campo")
                ),
                variable=variable,
                command=lambda c=columna, v=variable: self._alternar(
                    c, bool(v.get())
                ),
            ).pack(anchor="w")
            ttk.Label(
                fila,
                text=f"SQL · {columna.get('tipo', 'Texto')}",
                style="TarjetaSecundaria.TLabel",
            ).pack(anchor="w", padx=(24, 0))

    def _actualizar_seleccionadas(self) -> None:
        for hijo in self._interior_seleccionadas.winfo_children():
            hijo.destroy()
        if not self._seleccionadas:
            ttk.Label(
                self._interior_seleccionadas,
                text=(
                    "No hay columnas seleccionadas.\n"
                    "Marque al menos una en el panel izquierdo."
                ),
                style="TarjetaSecundaria.TLabel",
                justify="left",
            ).pack(anchor="w", pady=8)
            return

        for indice, columna in enumerate(self._seleccionadas):
            fila = ttk.Frame(
                self._interior_seleccionadas, style="Superficie.TFrame"
            )
            fila.pack(fill="x", pady=3)
            ttk.Label(
                fila,
                text=str(indice + 1),
                width=3,
                style="TarjetaSecundaria.TLabel",
            ).pack(side="left")
            ttk.Button(
                fila,
                text="↑",
                width=2,
                command=lambda i=indice: self._mover(i, -1),
            ).pack(side="left")
            ttk.Button(
                fila,
                text="↓",
                width=2,
                command=lambda i=indice: self._mover(i, 1),
            ).pack(side="left", padx=(2, 0))

            variable = tk.StringVar(value=str(columna.get("encabezado") or ""))
            ttk.Entry(fila, textvariable=variable).pack(
                side="left", fill="x", expand=True, padx=6
            )
            variable.trace_add(
                "write",
                lambda *_args, c=columna, v=variable: c.__setitem__(
                    "encabezado", v.get()
                ),
            )

            if columna.get("origen") == "manual":
                tk.Label(
                    fila,
                    text="MANUAL",
                    bg=MORADO_SUAVE,
                    fg=MORADO,
                    font=(FUENTE, 8, "bold"),
                    padx=6,
                    pady=2,
                ).pack(side="left", padx=(0, 4))
                ttk.Button(
                    fila,
                    text="Editar",
                    width=6,
                    command=lambda i=indice: self._editar_manual(i),
                ).pack(side="left")
            else:
                tk.Label(
                    fila,
                    text="SQL",
                    bg=AZUL_SUAVE,
                    fg=AZUL_PRINCIPAL,
                    font=(FUENTE, 8, "bold"),
                    padx=6,
                    pady=2,
                ).pack(side="left", padx=(0, 4))

            ttk.Button(
                fila,
                text="✕",
                width=3,
                command=lambda i=indice: self._quitar(i),
            ).pack(side="left", padx=(4, 0))

    def _alternar(self, columna: dict[str, Any], activo: bool) -> None:
        campo = str(columna.get("campo") or "")
        if activo:
            if not any(
                item["origen"] == "sql" and item.get("campo") == campo
                for item in self._seleccionadas
            ):
                self._seleccionadas.append(self._nueva_sql(columna))
        else:
            self._seleccionadas = [
                item
                for item in self._seleccionadas
                if not (
                    item["origen"] == "sql"
                    and item.get("campo") == campo
                )
            ]
        self._actualizar_seleccionadas()

    def _mover(self, indice: int, delta: int) -> None:
        destino = indice + delta
        if 0 <= destino < len(self._seleccionadas):
            self._seleccionadas[indice], self._seleccionadas[destino] = (
                self._seleccionadas[destino],
                self._seleccionadas[indice],
            )
            self._actualizar_seleccionadas()

    def _quitar(self, indice: int) -> None:
        if 0 <= indice < len(self._seleccionadas):
            self._seleccionadas.pop(indice)
            self._actualizar_disponibles()
            self._actualizar_seleccionadas()

    def _agregar_manual(self) -> None:
        dialogo = DialogoColumnaManual(self)
        self.wait_window(dialogo)
        if dialogo.resultado:
            self._seleccionadas.append(dialogo.resultado)
            self._actualizar_seleccionadas()

    def _editar_manual(self, indice: int) -> None:
        if not (0 <= indice < len(self._seleccionadas)):
            return
        columna = self._seleccionadas[indice]
        if columna.get("origen") != "manual":
            return
        dialogo = DialogoColumnaManual(self, columna)
        self.wait_window(dialogo)
        if dialogo.resultado:
            self._seleccionadas[indice] = dialogo.resultado
            self._actualizar_seleccionadas()

    def _restaurar(self) -> None:
        self._seleccionadas = [
            self._nueva_sql(columna) for columna in self._disponibles
        ]
        self._actualizar_disponibles()
        self._actualizar_seleccionadas()

    def _aceptar(self) -> None:
        if not self._seleccionadas:
            messagebox.showwarning(
                "Sin columnas",
                "Seleccione al menos una columna para el Excel.",
                parent=self,
            )
            return
        vistos: dict[str, int] = {}
        for columna in self._seleccionadas:
            base = str(columna.get("encabezado") or "").strip()
            if not base:
                base = str(columna.get("campo") or "COLUMNA")
            vistos[base] = vistos.get(base, 0) + 1
            columna["encabezado"] = (
                base
                if vistos[base] == 1
                else f"{base} ({vistos[base]})"
            )
        self.resultado = [dict(columna) for columna in self._seleccionadas]
        self.destroy()

    def _cancelar(self) -> None:
        self.resultado = None
        self.destroy()


class DialogoColumnaManual(tk.Toplevel):
    """Pequeño formulario para agregar o editar una columna manual."""

    def __init__(
        self,
        master: tk.Misc,
        columna: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(master)
        self.title(
            "Editar columna manual"
            if columna
            else "Agregar columna manual"
        )
        self.configure(bg=FONDO)
        self.geometry("480x330")
        self.resizable(False, False)
        self.transient(master.winfo_toplevel())
        self.resultado: dict[str, Any] | None = None
        self._columna = columna
        self._var_nombre = tk.StringVar(
            value=str(columna.get("encabezado") or "") if columna else ""
        )
        self._var_tipo = tk.StringVar(
            value=str(columna.get("tipo") or "Texto") if columna else "Texto"
        )
        self._var_valor = tk.StringVar(
            value=str(columna.get("valor") or "") if columna else ""
        )
        self._construir()
        self._centrar(master)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._cancelar)
        self.bind("<Escape>", lambda _evento: self._cancelar())

    def _centrar(self, master: tk.Misc) -> None:
        self.update_idletasks()
        try:
            raiz = master.winfo_toplevel()
            x = raiz.winfo_rootx() + max(
                0, (raiz.winfo_width() - self.winfo_width()) // 2
            )
            y = raiz.winfo_rooty() + max(
                0, (raiz.winfo_height() - self.winfo_height()) // 2
            )
            self.geometry(f"+{x}+{y}")
        except tk.TclError:  # pragma: no cover
            pass

    def _construir(self) -> None:
        marco = ttk.Frame(self, padding=16)
        marco.pack(fill="both", expand=True)
        ttk.Label(
            marco,
            text="Columna manual",
            style="Seccion.TLabel",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))

        ttk.Label(marco, text="Nombre de la columna:").grid(
            row=1, column=0, sticky="w", pady=4
        )
        entrada_nombre = ttk.Entry(marco, textvariable=self._var_nombre)
        entrada_nombre.grid(row=1, column=1, sticky="ew", pady=4)

        ttk.Label(marco, text="Tipo de dato:").grid(
            row=2, column=0, sticky="w", pady=4
        )
        combo = ttk.Combobox(
            marco,
            textvariable=self._var_tipo,
            values=list(svc.TIPOS),
            state="readonly",
        )
        combo.grid(row=2, column=1, sticky="ew", pady=4)
        combo.bind("<<ComboboxSelected>>", lambda _e: self._actualizar_ayuda())

        ttk.Label(marco, text="Valor predeterminado:").grid(
            row=3, column=0, sticky="w", pady=4
        )
        ttk.Entry(marco, textvariable=self._var_valor).grid(
            row=3, column=1, sticky="ew", pady=4
        )

        self._lbl_ayuda = ttk.Label(
            marco,
            text="",
            style="Subtitulo.TLabel",
            wraplength=400,
            justify="left",
        )
        self._lbl_ayuda.grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(6, 0)
        )
        ttk.Label(
            marco,
            text=(
                "El valor se repetirá en todos los registros exportados. "
                "Para dejarlo vacío, no escriba nada."
            ),
            style="Subtitulo.TLabel",
            wraplength=400,
            justify="left",
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(8, 0))

        pie = ttk.Frame(marco)
        pie.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        ttk.Button(pie, text="Cancelar", command=self._cancelar).pack(
            side="right", padx=(6, 0)
        )
        ttk.Button(
            pie,
            text="Guardar columna",
            style="Principal.TButton",
            command=self._aceptar,
        ).pack(side="right")

        marco.grid_columnconfigure(1, weight=1)
        self._actualizar_ayuda()
        entrada_nombre.focus_set()

    def _actualizar_ayuda(self) -> None:
        ayudas = {
            "Texto": "Ejemplo: CONSULTA SQL, Observaciones, Origen…",
            "Número": "Use solo dígitos; por ejemplo 0, 10 o 15.50.",
            "Fecha": "Formatos admitidos: AAAA-MM-DD o DD/MM/AAAA.",
            "Booleano": "Valores: Sí/No, True/False o 1/0.",
        }
        self._lbl_ayuda.configure(
            text=ayudas.get(self._var_tipo.get(), "")
        )

    def _aceptar(self) -> None:
        nombre = self._var_nombre.get().strip()
        if not nombre:
            messagebox.showwarning(
                "Nombre requerido",
                "Escriba el nombre de la columna manual.",
                parent=self,
            )
            return
        self.resultado = {
            "id": str(
                (self._columna or {}).get("id")
                or f"manual:{uuid.uuid4().hex}"
            ),
            "origen": "manual",
            "campo": "",
            "encabezado": nombre,
            "tipo": self._var_tipo.get() or "Texto",
            "valor": self._var_valor.get(),
        }
        self.destroy()

    def _cancelar(self) -> None:
        self.resultado = None
        self.destroy()


class VentanaHistorial(tk.Toplevel):
    """Historial básico de ejecuciones con opción de recuperar el SQL."""

    def __init__(
        self,
        master: tk.Misc,
        al_cargar: Callable[[str, str], None],
    ) -> None:
        super().__init__(master)
        self.title("Historial de consultas SQL")
        self.configure(bg=FONDO)
        self.geometry("1040x560")
        self.minsize(820, 460)
        self.transient(master.winfo_toplevel())
        self._al_cargar = al_cargar
        self._registros = svc.cargar_datos()["historial"]
        self._construir()
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def _construir(self) -> None:
        ttk.Label(
            self, text="Historial de consultas ejecutadas", style="Titulo.TLabel"
        ).pack(anchor="w", padx=16, pady=(14, 0))
        ttk.Label(
            self,
            text=(
                "Se conservan las últimas ejecuciones con fecha, nombre, "
                "cantidad de registros y resultado."
            ),
            style="Subtitulo.TLabel",
        ).pack(anchor="w", padx=16, pady=(2, 8))

        marco = ttk.Frame(self)
        marco.pack(fill="both", expand=True, padx=16)
        columnas = ("fecha", "nombre", "registros", "duracion", "resultado")
        self._tabla = ttk.Treeview(
            marco, columns=columnas, show="headings", selectmode="browse"
        )
        titulos = {
            "fecha": "Fecha",
            "nombre": "Consulta",
            "registros": "Registros",
            "duracion": "Duración",
            "resultado": "Resultado",
        }
        anchos = {
            "fecha": 150,
            "nombre": 230,
            "registros": 90,
            "duracion": 90,
            "resultado": 100,
        }
        for clave in columnas:
            self._tabla.heading(clave, text=titulos[clave])
            self._tabla.column(
                clave, width=anchos[clave], minwidth=70, stretch=True
            )
        scroll_y = ttk.Scrollbar(
            marco, orient="vertical", command=self._tabla.yview
        )
        self._tabla.configure(yscrollcommand=scroll_y.set)
        self._tabla.pack(side="left", fill="both", expand=True)
        scroll_y.pack(side="right", fill="y")
        self._tabla.tag_configure("error", foreground=ROJO)
        self._tabla.bind("<<TreeviewSelect>>", self._al_seleccionar)
        self._tabla.bind("<Double-1>", lambda _e: self._cargar())

        for indice, registro in enumerate(self._registros):
            fecha = str(registro.get("fecha") or "").replace("T", " ")
            exito = bool(registro.get("exito"))
            self._tabla.insert(
                "",
                "end",
                iid=str(indice),
                values=(
                    fecha,
                    str(registro.get("nombre") or "(sin nombre)"),
                    int(registro.get("total") or 0),
                    f"{float(registro.get('duracion') or 0):.2f} s",
                    "Correcta" if exito else "Error",
                ),
                tags=() if exito else ("error",),
            )

        self._txt_sql = tk.Text(
            self,
            height=6,
            wrap="word",
            font=(FUENTE_MONO, 9),
            bg=SUPERFICIE,
            fg=TEXTO,
            relief="flat",
            highlightthickness=1,
            highlightbackground=BORDE,
            padx=8,
            pady=6,
            state="disabled",
        )
        self._txt_sql.pack(fill="x", padx=16, pady=(8, 0))

        pie = ttk.Frame(self)
        pie.pack(fill="x", padx=16, pady=12)
        ttk.Button(
            pie,
            text="Cargar SQL seleccionado",
            style="Principal.TButton",
            command=self._cargar,
        ).pack(side="left")
        ttk.Button(pie, text="Cerrar", command=self.destroy).pack(
            side="right"
        )
        if not self._registros:
            ttk.Label(
                pie,
                text="Todavía no hay ejecuciones registradas.",
                style="Subtitulo.TLabel",
            ).pack(side="left", padx=(10, 0))

    def _registro_seleccionado(self) -> dict[str, Any] | None:
        seleccion = self._tabla.selection()
        if not seleccion:
            return None
        try:
            indice = int(seleccion[0])
        except (TypeError, ValueError):
            return None
        if 0 <= indice < len(self._registros):
            return self._registros[indice]
        return None

    def _al_seleccionar(self, _evento=None) -> None:
        registro = self._registro_seleccionado()
        if registro is None:
            return
        self._txt_sql.configure(state="normal")
        self._txt_sql.delete("1.0", "end")
        self._txt_sql.insert(
            "1.0",
            str(registro.get("sql") or "")
            + (
                f"\n\nResultado: {registro.get('mensaje')}"
                if registro.get("mensaje")
                else ""
            ),
        )
        self._txt_sql.configure(state="disabled")

    def _cargar(self) -> None:
        registro = self._registro_seleccionado()
        if registro is None:
            messagebox.showinfo(
                "Seleccione una ejecución",
                "Elija una fila del historial para recuperar su SQL.",
                parent=self,
            )
            return
        self._al_cargar(
            str(registro.get("sql") or ""),
            str(registro.get("nombre") or "(sin nombre)"),
        )
        self.destroy()
