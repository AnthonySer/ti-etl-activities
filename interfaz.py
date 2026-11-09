"""Interfaz gráfica del Sistema Integral de Tickets TI - INKACROPS.

Toda la operación habitual se realiza desde esta ventana: cargar y validar el
Excel, procesarlo, administrar usuarios y áreas, reimportar correcciones,
consolidar el maestro histórico, generar reportes y consultar la auditoría.

La lógica de negocio vive en ``core``; esta interfaz solo orquesta servicios y
presenta resultados. Las tareas largas se ejecutan en un hilo y se comunican
con la ventana mediante una cola, para que la interfaz no se congele.
"""

from __future__ import annotations

import logging
import os
import queue
import subprocess
import sys
import threading
import traceback
from pathlib import Path
from typing import Any, Callable

import pandas as pd
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from config import configuracion as cfg
from config.logging_config import configurar_logging
from core import asignaciones as svc_asignaciones
from core import auditoria
from core import estado as svc_estado
from core import solicitantes as svc_solicitantes
from core.archivos import ErrorArchivo, ErrorEstructura, leer_excel
from core.bloqueo import ErrorBloqueo, esta_bloqueado, bloqueo
from core.consolidacion_tickets import consolidar_procesado
from core.esquema import COLUMNAS_OFICIALES, C_FECHA, C_HORAS, C_SOLICITANTE
from core.etl import previsualizar
from core.maestro_tickets import MaestroTickets
from core.procesado import resolver_salida
from core.procesador import procesar_archivo
from core.reimportacion import (
    TIPO_AMBIGUO,
    TIPO_AUSENTE,
    TIPO_DUPLICADO,
    TIPO_ERROR,
    TIPO_MODIFICADO,
    TIPO_NUEVO,
    TIPO_SIN_CAMBIOS,
    analizar_reimportacion,
    aplicar_reimportacion,
)
from core.reportes import generar_reporte
from panel_principal import PanelPrincipal, SEMANTICA

LOGGER = logging.getLogger("automatizador")

TITULO = "Sistema Integral de Tickets TI - INKACROPS"
COLOR_FONDO = "#f8fafc"
COLOR_CABECERA = "#ffffff"
COLOR_ACENTO = "#2563eb"
COLOR_TEXTO = "#0f172a"
COLOR_TEXTO_SECUNDARIO = "#64748b"
COLOR_BORDE = "#e2e8f0"
COLOR_TABLA_CABECERA = "#f1f5f9"
COLOR_OK = "#15803d"
COLOR_ALERTA = "#b45309"
COLOR_ERROR = "#b91c1c"

_ETIQUETAS_TIPO = {
    TIPO_NUEVO: "Nuevo",
    TIPO_MODIFICADO: "Modificado",
    TIPO_SIN_CAMBIOS: "Sin cambios",
    TIPO_DUPLICADO: "Duplicado",
    TIPO_AMBIGUO: "Ambiguo",
    TIPO_AUSENTE: "Ausente",
    TIPO_ERROR: "Error",
}


def _texto(valor: Any) -> str:
    if valor is None:
        return ""
    try:
        if pd.isna(valor):
            return ""
    except (TypeError, ValueError):
        pass
    return str(valor)


class VentanaConfiguracion(tk.Toplevel):
    """Ventana ligera para ajustar rutas locales sin tocar el código fuente."""

    _RUTAS = [
        ("Archivos de entrada", "input_dir", "directorio"),
        ("Excel procesados", "output_dir", "directorio"),
        ("Copias de revisión", "revision_dir", "directorio"),
        ("Respaldos de procesados", "backups_dir", "directorio"),
        ("Datos y maestros", "data_dir", "directorio"),
        ("Reportes", "reportes_dir", "directorio"),
        ("Auditoría", "auditoria_csv", "archivo"),
        ("Logs", "logs_dir", "directorio"),
    ]

    def __init__(self, padre: tk.Misc) -> None:
        super().__init__(padre)
        self.title("Configuración")
        self.geometry("760x500")
        self.minsize(620, 420)
        self.configure(bg=COLOR_FONDO)
        self.transient(padre)
        self.grab_set()

        self._config = cfg.cargar_configuracion_local()
        self._variables: dict[str, tk.StringVar] = {}

        cabecera = ttk.Label(
            self,
            text="Configuración del sistema",
            style="Seccion.TLabel",
        )
        cabecera.pack(anchor="w", padx=18, pady=(14, 8))

        marco = ttk.Frame(self, padding=10)
        marco.pack(fill="both", expand=True, padx=12, pady=(0, 8))

        for titulo, nombre, tipo in self._RUTAS:
            fila = ttk.Frame(marco)
            fila.pack(fill="x", pady=4)
            ttk.Label(fila, text=titulo, width=22, anchor="w").pack(side="left")
            variable = tk.StringVar(value=str(self._config.get(nombre, "")))
            self._variables[nombre] = variable
            ttk.Entry(fila, textvariable=variable).pack(side="left", fill="x", expand=True, padx=(8, 8))
            ttk.Button(
                fila,
                text="Examinar…",
                command=lambda n=nombre, t=tipo: self._seleccionar_ruta(n, t),
            ).pack(side="left")

        acciones = ttk.Frame(self)
        acciones.pack(fill="x", padx=12, pady=(0, 12))
        ttk.Button(acciones, text="Guardar", style="Principal.TButton", command=self._guardar).pack(side="right")
        ttk.Button(acciones, text="Restaurar", command=self._restaurar).pack(side="right", padx=(0, 8))
        ttk.Button(acciones, text="Cancelar", command=self.destroy).pack(side="right", padx=(0, 8))

    def _seleccionar_ruta(self, nombre: str, tipo: str) -> None:
        valor = self._variables[nombre].get().strip()
        directorio_base = valor if valor else str(cfg.BASE_DIR)
        if tipo == "archivo":
            ruta = filedialog.askopenfilename(
                title="Seleccione el archivo",
                initialdir=str(Path(directorio_base).resolve() if Path(directorio_base).exists() else cfg.BASE_DIR),
            )
        else:
            ruta = filedialog.askdirectory(
                title="Seleccione la carpeta",
                initialdir=str(Path(directorio_base).resolve() if Path(directorio_base).exists() else cfg.BASE_DIR),
            )
        if ruta:
            self._variables[nombre].set(ruta)

    def _colectar(self) -> dict[str, str | int]:
        datos: dict[str, str | int] = {}
        for nombre, variable in self._variables.items():
            valor = variable.get().strip()
            if nombre == "max_backups":
                continue
            datos[nombre] = valor
        datos["max_backups"] = self._config.get("max_backups", 10)
        datos["log_level"] = self._config.get("log_level", "INFO")
        datos["formato_fecha_reporte"] = self._config.get("formato_fecha_reporte", "DD/MM/YYYY")
        return datos

    def _guardar(self) -> None:
        try:
            validada = cfg.guardar_configuracion_local(self._colectar())
            cfg.aplicar_configuracion(validada)
        except ValueError as exc:
            messagebox.showerror("Configuración inválida", str(exc))
            return
        messagebox.showinfo("Configuración guardada", "Los cambios se aplicarán en la próxima operación y la aplicación puede requerir reinicio si se usaron rutas nuevas.")
        self.destroy()

    def _restaurar(self) -> None:
        restaurada = cfg._config_por_defecto()
        for nombre, variable in self._variables.items():
            variable.set(str(restaurada.get(nombre, "")))


class VentanaGuiaUso(tk.Toplevel):
    """Guía breve en español para el flujo habitual del usuario."""

    def __init__(self, padre: tk.Misc) -> None:
        super().__init__(padre)
        self.title("Guía rápida — Cómo trabajar con los tickets")
        self.geometry("760x560")
        self.minsize(620, 420)
        self.configure(bg=COLOR_FONDO)
        self.transient(padre)
        self.grab_set()

        cabecera = ttk.Label(self, text="Guía rápida — Cómo trabajar con los tickets", style="Seccion.TLabel")
        cabecera.pack(anchor="w", padx=18, pady=(14, 8))

        contenedor = ttk.Frame(self)
        contenedor.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        lienzo = tk.Canvas(contenedor, bg=COLOR_FONDO, highlightthickness=0)
        barra = ttk.Scrollbar(contenedor, orient="vertical", command=lienzo.yview)
        marco = tk.Frame(lienzo, bg=COLOR_FONDO)

        lienzo.create_window((0, 0), window=marco, anchor="nw")
        lienzo.configure(yscrollcommand=barra.set)
        lienzo.pack(side="left", fill="both", expand=True)
        barra.pack(side="right", fill="y")

        contenido = [
            ("Paso 1. Cargar y validar el Excel", "Ir a Cargar y procesar. Seleccione el Excel y pulse Validar y previsualizar. Si aparecen errores críticos, corríjalos antes de continuar."),
            ("Paso 2. Generar el Excel", "Elija Generar Excel normal o Generar Excel a detalle. El sistema conserva un archivo canónico y genera respaldos al reemplazarlo."),
            ("Paso 3. Revisar usuarios y áreas", "Vaya a Usuarios y áreas, asigne el área a los usuarios pendientes y guarde los cambios."),
            ("Paso 4. Revisar y corregir el Excel", "Pulse Abrir resultado; el sistema abre una copia de revisión, no el archivo canónico. Corrija solo esa copia y guárdela."),
            ("Paso 5. Reimportar las correcciones", "Vaya a Reimportar correcciones y revise el resumen para resolver duplicados o errores antes de aplicar."),
            ("Paso 6. Consolidar el maestro histórico", "Desde Maestro histórico, ejecute la consolidación. La operación requiere que el maestro histórico esté actualizado antes de generar reportes definitivos."),
            ("Paso 7. Generar reportes", "Vaya a Reportes, seleccione año y mes, genere el reporte y revise las hojas RESUMEN, PROYECTOS, ACTIVIDAD, AREA y DETALLE."),
            ("Paso 8. Consultar auditoría", "Revise el historial en Auditoría para confirmar qué se ejecutó y cuándo."),
            ("Importante", "El Excel original nunca debe modificarse. Las correcciones pasan por la copia de revisión. No modificar el Excel maestro ni el archivo procesado al generar reportes. No cambiar el esquema oficial de 11 columnas."),
        ]

        for indice, (titulo, texto) in enumerate(contenido, start=1):
            bloque = tk.Frame(marco, bg=COLOR_FONDO, padx=8, pady=10)
            bloque.pack(fill="x")
            tk.Label(
                bloque,
                text=f"{indice}. {titulo}",
                bg=COLOR_FONDO,
                fg=COLOR_TEXTO,
                font=("Segoe UI", 11, "bold"),
                anchor="w",
                justify="left",
            ).pack(anchor="w")
            tk.Label(
                bloque,
                text=texto,
                bg=COLOR_FONDO,
                fg=COLOR_TEXTO_SECUNDARIO,
                font=("Segoe UI", 10),
                justify="left",
                wraplength=650,
                anchor="w",
            ).pack(anchor="w", pady=(4, 0))

        marco.update_idletasks()
        lienzo.config(scrollregion=lienzo.bbox("all"))

        ttk.Button(self, text="Cerrar", command=self.destroy).pack(anchor="e", padx=12, pady=(0, 12))


def _abrir(ruta: Path | str) -> None:
    ruta = Path(ruta)
    if not ruta.exists():
        raise ErrorArchivo(f"No existe el archivo: {ruta}")
    try:
        os.startfile(str(ruta))  # type: ignore[attr-defined]
    except AttributeError:  # pragma: no cover - otros sistemas
        subprocess.Popen(["xdg-open", str(ruta)])
    except OSError:
        subprocess.Popen(["start", "", str(ruta)], shell=True)


class AplicacionTk(tk.Tk):
    """Ventana principal del sistema."""

    def __init__(self) -> None:
        super().__init__()
        self.title(TITULO)
        self.geometry("1240x800")
        self.minsize(1080, 700)
        self.configure(bg=COLOR_FONDO)

        self._estado = svc_estado.cargar_estado()
        self._maestro = svc_asignaciones.cargar_maestro()
        self._preview_df: pd.DataFrame | None = None
        self._preview_info: dict[str, Any] | None = None
        self._ruta_preview: str | None = None
        self._solicitantes_por_iid: dict[str, dict[str, Any]] = {}
        self._ultimo_procesado: str = self._estado.ultimo_archivo_procesado
        self._ultimo_manifiesto: str = self._estado.ultimo_manifiesto
        self._reimportacion = None
        self._reporte: dict[str, Any] | None = None
        self._cola: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._cola_after_id: str | None = None
        self._ocupado = False

        self._crear_estilos()
        self._crear_cabecera()
        self._crear_pestanas()
        self._refrescar_panel()
        self._refrescar_usuarios()
        self._refrescar_pendientes()
        self._refrescar_maestro()
        self._refrescar_auditoria()
        self._cola_after_id = self.after(120, self._procesar_cola)

    # ------------------------------------------------------------------
    # Construcción visual
    # ------------------------------------------------------------------
    def _crear_estilos(self) -> None:
        estilo = ttk.Style(self)
        try:
            estilo.theme_use("clam")
        except tk.TclError:  # pragma: no cover
            pass

        estilo.configure(
            ".",
            font=("Segoe UI", 10),
            background=COLOR_FONDO,
            foreground=COLOR_TEXTO,
        )
        estilo.configure("TFrame", background=COLOR_FONDO)
        estilo.configure("TLabel", background=COLOR_FONDO, foreground=COLOR_TEXTO)
        estilo.configure(
            "Titulo.TLabel",
            background=COLOR_CABECERA,
            foreground=COLOR_TEXTO,
            font=("Segoe UI", 17, "bold"),
        )
        estilo.configure(
            "Subtitulo.TLabel",
            background=COLOR_CABECERA,
            foreground=COLOR_TEXTO_SECUNDARIO,
            font=("Segoe UI", 9),
        )
        estilo.configure(
            "Seccion.TLabel",
            background=COLOR_FONDO,
            foreground=COLOR_TEXTO,
            font=("Segoe UI", 12, "bold"),
        )
        estilo.configure(
            "Valor.TLabel",
            background=COLOR_FONDO,
            foreground=COLOR_ACENTO,
            font=("Segoe UI", 16, "bold"),
        )
        estilo.configure(
            "Clave.TLabel",
            background=COLOR_FONDO,
            foreground=COLOR_TEXTO_SECUNDARIO,
            font=("Segoe UI", 9),
        )

        estilo.configure(
            "TButton",
            font=("Segoe UI", 10),
            padding=(12, 7),
            background=COLOR_CABECERA,
            foreground=COLOR_TEXTO,
            bordercolor=COLOR_BORDE,
            lightcolor=COLOR_BORDE,
            darkcolor=COLOR_BORDE,
            focuscolor=COLOR_ACENTO,
        )
        estilo.map(
            "TButton",
            background=[("active", "#f1f5f9"), ("pressed", "#e2e8f0")],
            foreground=[("disabled", "#94a3b8")],
        )
        estilo.configure(
            "Principal.TButton",
            font=("Segoe UI", 10, "bold"),
            padding=(14, 8),
            background=COLOR_ACENTO,
            foreground="#ffffff",
            bordercolor=COLOR_ACENTO,
            lightcolor=COLOR_ACENTO,
            darkcolor=COLOR_ACENTO,
            focuscolor="#93c5fd",
        )
        estilo.map(
            "Principal.TButton",
            background=[("active", "#1d4ed8"), ("pressed", "#1e40af")],
            foreground=[("active", "#ffffff"), ("disabled", "#e2e8f0")],
        )

        estilo.configure(
            "TNotebook",
            background=COLOR_FONDO,
            borderwidth=0,
            tabmargins=(0, 6, 0, 0),
        )
        estilo.configure(
            "TNotebook.Tab",
            font=("Segoe UI", 10),
            padding=(16, 9),
            background="#eef2f7",
            foreground=COLOR_TEXTO_SECUNDARIO,
            bordercolor=COLOR_BORDE,
            lightcolor="#eef2f7",
            darkcolor="#eef2f7",
        )
        estilo.map(
            "TNotebook.Tab",
            background=[("selected", COLOR_CABECERA), ("active", "#f1f5f9")],
            foreground=[("selected", COLOR_ACENTO), ("active", COLOR_TEXTO)],
        )

        estilo.configure(
            "Treeview",
            font=("Segoe UI", 10),
            rowheight=26,
            background=COLOR_CABECERA,
            fieldbackground=COLOR_CABECERA,
            foreground=COLOR_TEXTO,
            bordercolor=COLOR_BORDE,
            lightcolor=COLOR_BORDE,
            darkcolor=COLOR_BORDE,
            borderwidth=0,
        )
        estilo.configure(
            "Treeview.Heading",
            font=("Segoe UI", 9, "bold"),
            background=COLOR_TABLA_CABECERA,
            foreground="#475569",
            relief="flat",
            padding=(8, 7),
            bordercolor=COLOR_BORDE,
            lightcolor=COLOR_BORDE,
            darkcolor=COLOR_BORDE,
        )
        estilo.map("Treeview.Heading", background=[("active", "#e2e8f0")])
        estilo.map(
            "Treeview",
            background=[("selected", "#dbeafe")],
            foreground=[("selected", COLOR_TEXTO)],
        )

        estilo.configure(
            "TLabelframe",
            background=COLOR_FONDO,
            bordercolor=COLOR_BORDE,
            lightcolor=COLOR_BORDE,
            darkcolor=COLOR_BORDE,
            relief="solid",
            borderwidth=1,
        )
        estilo.configure(
            "TLabelframe.Label",
            background=COLOR_FONDO,
            foreground=COLOR_TEXTO,
            font=("Segoe UI", 10, "bold"),
        )

        estilo.configure(
            "TEntry",
            fieldbackground=COLOR_CABECERA,
            foreground=COLOR_TEXTO,
            bordercolor=COLOR_BORDE,
            lightcolor=COLOR_BORDE,
            darkcolor=COLOR_BORDE,
            padding=6,
        )
        estilo.map(
            "TEntry",
            bordercolor=[("focus", COLOR_ACENTO)],
            lightcolor=[("focus", COLOR_ACENTO)],
            darkcolor=[("focus", COLOR_ACENTO)],
        )
        estilo.configure(
            "TCombobox",
            fieldbackground=COLOR_CABECERA,
            background=COLOR_CABECERA,
            foreground=COLOR_TEXTO,
            bordercolor=COLOR_BORDE,
            lightcolor=COLOR_BORDE,
            darkcolor=COLOR_BORDE,
            arrowcolor=COLOR_TEXTO_SECUNDARIO,
            padding=5,
        )
        estilo.map(
            "TCombobox",
            fieldbackground=[("readonly", COLOR_CABECERA)],
            bordercolor=[("focus", COLOR_ACENTO)],
            lightcolor=[("focus", COLOR_ACENTO)],
            darkcolor=[("focus", COLOR_ACENTO)],
        )

        estilo.configure(
            "Horizontal.TProgressbar",
            troughcolor="#e2e8f0",
            background=COLOR_ACENTO,
            bordercolor=COLOR_BORDE,
            lightcolor=COLOR_ACENTO,
            darkcolor=COLOR_ACENTO,
            thickness=8,
        )
        estilo.configure(
            "TCheckbutton",
            background=COLOR_FONDO,
            foreground=COLOR_TEXTO,
            font=("Segoe UI", 9),
            focuscolor=COLOR_ACENTO,
        )
        estilo.map(
            "TCheckbutton",
            background=[("active", COLOR_FONDO)],
            foreground=[("disabled", "#94a3b8")],
        )
        estilo.configure("TPanedwindow", background=COLOR_FONDO)
        estilo.configure(
            "TScrollbar",
            background="#cbd5e1",
            troughcolor=COLOR_FONDO,
            bordercolor=COLOR_FONDO,
            arrowcolor=COLOR_TEXTO_SECUNDARIO,
            relief="flat",
        )

    def _crear_cabecera(self) -> None:
        cabecera = tk.Frame(self, bg=COLOR_CABECERA, height=72)
        cabecera.pack(side="top", fill="x")
        cabecera.pack_propagate(False)

        marca = tk.Frame(cabecera, bg=COLOR_ACENTO, width=34, height=34)
        marca.pack(side="left", padx=(20, 12), pady=19)
        marca.pack_propagate(False)
        tk.Label(
            marca,
            text="TI",
            bg=COLOR_ACENTO,
            fg="#ffffff",
            font=("Segoe UI", 11, "bold"),
        ).pack(expand=True)

        bloque = tk.Frame(cabecera, bg=COLOR_CABECERA)
        bloque.pack(side="left", pady=12)
        tk.Label(
            bloque,
            text="INKACROPS",
            bg=COLOR_CABECERA,
            fg=COLOR_TEXTO,
            font=("Segoe UI", 13, "bold"),
            anchor="w",
        ).pack(anchor="w")
        tk.Label(
            bloque,
            text=TITULO,
            bg=COLOR_CABECERA,
            fg=COLOR_TEXTO_SECUNDARIO,
            font=("Segoe UI", 9),
            anchor="w",
        ).pack(anchor="w")

        acciones = tk.Frame(cabecera, bg=COLOR_CABECERA)
        acciones.pack(side="right", padx=(0, 20))
        ttk.Button(
            acciones,
            text="Guía de uso",
            command=self._abrir_guia_uso,
        ).pack(side="right", padx=(0, 8))
        ttk.Button(
            acciones,
            text="Configuración",
            command=self._abrir_configuracion,
        ).pack(side="right")

        self._var_estado_app = tk.StringVar(value="Listo")
        self._badge_estado_app = tk.Frame(
            cabecera,
            bg=SEMANTICA["success"][0],
            highlightbackground=SEMANTICA["success"][2],
            highlightthickness=1,
            bd=0,
        )
        self._badge_estado_app.pack(side="right", padx=(0, 20))
        self._punto_estado_app = tk.Canvas(
            self._badge_estado_app,
            width=8,
            height=8,
            bg=SEMANTICA["success"][0],
            highlightthickness=0,
            bd=0,
        )
        self._punto_estado_app_item = self._punto_estado_app.create_oval(
            1, 1, 7, 7, fill=SEMANTICA["success"][1], outline=""
        )
        self._punto_estado_app.pack(side="left", padx=(10, 0))
        self._lbl_estado_app = tk.Label(
            self._badge_estado_app,
            textvariable=self._var_estado_app,
            bg=SEMANTICA["success"][0],
            fg=SEMANTICA["success"][1],
            font=("Segoe UI", 9, "bold"),
            pady=5,
        )
        self._lbl_estado_app.pack(side="left", padx=(6, 12))

        tk.Frame(self, bg=COLOR_BORDE, height=1).pack(side="top", fill="x")

    def _pintar_estado_app(self, texto: str, tipo: str = "success") -> None:
        fondo, color, borde = SEMANTICA.get(tipo, SEMANTICA["neutral"])
        self._var_estado_app.set(texto)
        self._badge_estado_app.configure(bg=fondo, highlightbackground=borde)
        self._punto_estado_app.configure(bg=fondo)
        self._punto_estado_app.itemconfigure(self._punto_estado_app_item, fill=color)
        self._lbl_estado_app.configure(bg=fondo, fg=color)

    def _abrir_configuracion(self) -> None:
        VentanaConfiguracion(self).wait_window()

    def _abrir_guia_uso(self) -> None:
        VentanaGuiaUso(self).wait_window()

    def _crear_pestanas(self) -> None:
        contenedor = ttk.Frame(self, padding=(12, 8, 12, 12))
        contenedor.pack(fill="both", expand=True)

        self._cuaderno = ttk.Notebook(contenedor)
        self._cuaderno.pack(fill="both", expand=True)
        self._cuaderno.enable_traversal()

        self._tab_panel = ttk.Frame(self._cuaderno, padding=0)
        self._tab_procesar = ttk.Frame(self._cuaderno, padding=18)
        self._tab_usuarios = ttk.Frame(self._cuaderno, padding=18)
        self._tab_reimportar = ttk.Frame(self._cuaderno, padding=18)
        self._tab_maestro = ttk.Frame(self._cuaderno, padding=18)
        self._tab_reportes = ttk.Frame(self._cuaderno, padding=18)
        self._tab_auditoria = ttk.Frame(self._cuaderno, padding=18)

        self._cuaderno.add(self._tab_panel, text="Panel principal")
        self._cuaderno.add(self._tab_procesar, text="Cargar y procesar")
        self._cuaderno.add(self._tab_usuarios, text="Usuarios y áreas")
        self._cuaderno.add(self._tab_reimportar, text="Reimportar correcciones")
        self._cuaderno.add(self._tab_maestro, text="Maestro histórico")
        self._cuaderno.add(self._tab_reportes, text="Reportes")
        self._cuaderno.add(self._tab_auditoria, text="Auditoría")

        self._construir_panel()
        self._construir_procesar()
        self._construir_usuarios()
        self._construir_reimportar()
        self._construir_maestro()
        self._construir_reportes()
        self._construir_auditoria()

    # ------------------------------------------------------------------
    # Panel principal
    # ------------------------------------------------------------------
    def _construir_panel(self) -> None:
        self._panel_principal = PanelPrincipal(
            self._tab_panel,
            on_cargar=lambda: self._cuaderno.select(self._tab_procesar),
            on_abrir_procesado=self._abrir_procesado,
            on_abrir_salida=lambda: _abrir(cfg.OUTPUT_DIR),
            on_actualizar=self._refrescar_panel,
            on_auditoria=lambda: self._cuaderno.select(self._tab_auditoria),
        )
        self._panel_principal.pack(fill="both", expand=True)

    def _refrescar_panel(self) -> None:
        self._estado = svc_estado.cargar_estado()
        estado = self._estado
        panel = getattr(self, "_panel_principal", None)
        if panel is None:
            return

        if estado.registros_error:
            chip_validos = ("REVISAR", "danger")
            detalle_validos = (
                f"{estado.registros_observados} observados · "
                f"{estado.registros_error} errores"
            )
        elif estado.total_registros > 0 and estado.ultima_operacion != "CONSOLIDACION":
            porcentaje = estado.registros_validos / estado.total_registros * 100
            chip_validos = (
                f"{porcentaje:.1f} %",
                "success" if porcentaje >= 95 else "warning",
            )
            detalle_validos = (
                f"{porcentaje:.1f} % del total · "
                f"{estado.registros_observados} observados"
            )
        elif estado.registros_validos:
            chip_validos = ("VALIDADO", "success")
            detalle_validos = (
                f"{estado.registros_observados} observados · "
                f"{estado.registros_error} errores"
            )
        else:
            chip_validos = ("SIN DATOS", "neutral")
            detalle_validos = "Aún no hay una ejecución registrada."

        panel.actualizar_kpis(
            validos=estado.registros_validos,
            horas=estado.total_horas,
            pendientes=estado.registros_pendientes,
            detalle_validos=detalle_validos,
            detalle_horas=(
                f"{estado.registros_duplicados} duplicados · "
                f"{estado.registros_error} errores"
            ),
            detalle_pendientes=(
                f"{estado.registros_nuevos} nuevos · "
                f"{estado.registros_actualizados} actualizados"
            ),
            chip_validos=chip_validos,
            chip_horas=(
                ("AL DÍA", "success")
                if estado.total_horas
                else ("SIN DATOS", "neutral")
            ),
            chip_pendientes=(
                ("REVISAR", "warning")
                if estado.registros_pendientes
                else ("AL DÍA", "success")
            ),
        )

        archivo = (
            cfg.ruta_visible(Path(estado.ultimo_archivo_procesado))
            if estado.ultimo_archivo_procesado
            else "—"
        )
        aviso = (
            estado.mensajes[-1]
            if estado.mensajes
            else "Sin novedades en la última ejecución."
        )
        panel.actualizar_detalle(
            archivo=archivo,
            hoja=estado.ultima_hoja or "—",
            periodo=estado.ultimo_periodo or "—",
            id_ejecucion=estado.ultimo_id_ejecucion or "—",
            aviso=aviso,
            maestro=self._texto_maestro_corto(),
        )

        if not self._ocupado:
            if estado.registros_error:
                panel.set_estado_sistema("REVISAR EJECUCIÓN", "danger")
            else:
                panel.set_estado_sistema("SISTEMA OPERATIVO", "success")
        panel.marcar_actualizado("Ahora mismo")

    def _texto_maestro(self) -> str:
        ruta = Path(cfg.MAESTRO_TICKETS_XLSX)
        if not ruta.exists():
            return "Maestro histórico de tickets: aún no existe (se creará al consolidar)."
        try:
            maestro = MaestroTickets.cargar(ruta)
            estado = maestro.estado()
        except (ErrorArchivo, ErrorEstructura) as exc:
            return f"Maestro histórico: ERROR — {exc}"
        except Exception as exc:  # noqa: BLE001
            return f"Maestro histórico: no se pudo leer — {exc}"
        return (
            "Maestro histórico: "
            f"{estado['registros']} tickets | "
            f"{estado['ids_unicos']} IDs únicos | "
            f"{estado['responsables']} responsables | "
            f"periodo {estado['periodo'] or 'sin fechas'}"
        )

    def _texto_maestro_corto(self) -> str:
        """Versión compacta para la franja del panel principal."""

        texto = self._texto_maestro()
        prefijo = "Maestro histórico: "
        if not texto.startswith(prefijo):
            return texto
        partes = texto[len(prefijo) :].split(" | ")
        resumen = "Maestro: " + partes[0]
        if len(partes) > 1:
            resumen += " · " + partes[1]
        return resumen

    # ------------------------------------------------------------------
    # Cargar y procesar
    # ------------------------------------------------------------------
    def _construir_procesar(self) -> None:
        ttk.Label(
            self._tab_procesar,
            text="1. Seleccione y valide el Excel original",
            style="Seccion.TLabel",
        ).pack(anchor="w")

        fila = ttk.Frame(self._tab_procesar)
        fila.pack(fill="x", pady=(8, 6))
        self._var_archivo = tk.StringVar()
        ttk.Entry(fila, textvariable=self._var_archivo).pack(
            side="left", fill="x", expand=True
        )
        ttk.Button(fila, text="Examinar…", command=self._elegir_archivo).pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(
            fila, text="Validar y previsualizar", command=self._previsualizar
        ).pack(side="left", padx=(8, 0))

        opciones = ttk.Frame(self._tab_procesar)
        opciones.pack(fill="x", pady=(0, 6))
        ttk.Label(opciones, text="Hoja:").pack(side="left")
        self._var_hoja = tk.StringVar()
        self._combo_hoja = ttk.Combobox(
            opciones, textvariable=self._var_hoja, width=34, state="readonly"
        )
        self._combo_hoja.pack(side="left", padx=(6, 16))
        self._combo_hoja.bind("<<ComboboxSelected>>", lambda _e: self._previsualizar())

        self._var_info_preview = tk.StringVar(
            value="Seleccione un archivo .xlsx para ver su estructura."
        )
        ttk.Label(
            self._tab_procesar,
            textvariable=self._var_info_preview,
            foreground="#374151",
            wraplength=1100,
        ).pack(anchor="w", pady=(0, 6))

        progreso = ttk.LabelFrame(
            self._tab_procesar, text="2. Procesar y generar el Excel", padding=10
        )
        progreso.pack(side="bottom", fill="x", pady=(10, 0))
        self._var_etapa = tk.StringVar(value="Esperando archivo validado.")
        ttk.Label(progreso, textvariable=self._var_etapa).pack(anchor="w")
        self._barra = ttk.Progressbar(progreso, mode="determinate", maximum=100)
        self._barra.pack(fill="x", pady=6)
        self._var_contadores = tk.StringVar(value="")
        ttk.Label(progreso, textvariable=self._var_contadores).pack(anchor="w")

        botones = ttk.Frame(progreso)
        botones.pack(fill="x", pady=(8, 0))
        self._btn_procesar = ttk.Button(
            botones,
            text="Procesar archivo",
            style="Principal.TButton",
            command=self._procesar,
            state="disabled",
        )
        self._btn_procesar.pack(side="left")
        ttk.Button(
            botones, text="Abrir resultado", command=self._abrir_procesado
        ).pack(side="left", padx=8)
        ttk.Button(
            botones, text="Abrir carpeta de salida", command=lambda: _abrir(cfg.OUTPUT_DIR)
        ).pack(side="left", padx=8)

        marco_tabla = ttk.LabelFrame(
            self._tab_procesar, text="Vista previa (primeras filas)", padding=6
        )
        marco_tabla.pack(fill="both", expand=True)
        self._tabla_preview = self._crear_tabla(
            marco_tabla,
            columnas=list(COLUMNAS_OFICIALES),
            anchos={
                "Detalle": 320,
                "Fecha": 90,
                "Solicitante": 150,
                "Area": 140,
                "Atención": 110,
                "Requerimiento": 140,
                "Responsable": 140,
                "Subtipo": 120,
                "Proyecto": 150,
                "Horas": 60,
                "Actividad": 100,
            },
            alto=9,
        )

    def _elegir_archivo(self) -> None:
        ruta = filedialog.askopenfilename(
            title="Seleccione el Excel de tickets",
            initialdir=str(cfg.INPUT_DIR if cfg.INPUT_DIR.exists() else cfg.BASE_DIR),
            filetypes=[("Excel", "*.xlsx"), ("Todos", "*.*")],
        )
        if not ruta:
            return
        self._limpiar_validacion_archivo(
            "Nuevo archivo seleccionado; valide para cargar su vista previa."
        )
        self._var_archivo.set(ruta)
        self._previsualizar()

    def _limpiar_validacion_archivo(self, mensaje: str) -> None:
        self._preview_df = None
        self._preview_info = None
        self._ruta_preview = None
        self._var_hoja.set("")
        self._combo_hoja.configure(values=[])
        self._btn_procesar.configure(state="disabled")
        self._limpiar_tabla(self._tabla_preview)
        self._var_info_preview.set(mensaje)
        self._var_etapa.set("Esperando archivo validado.")

    def _previsualizar(self) -> None:
        ruta_texto = self._var_archivo.get().strip()
        if not ruta_texto:
            messagebox.showwarning("Sin archivo", "Seleccione primero un archivo .xlsx.")
            return
        ruta = Path(ruta_texto)
        clave_ruta = str(ruta.resolve())
        hoja_pedida = (
            self._var_hoja.get() or None
            if self._ruta_preview == clave_ruta
            else None
        )
        try:
            info = previsualizar(ruta, hoja=hoja_pedida)
        except (ErrorArchivo, ErrorEstructura) as exc:
            self._limpiar_validacion_archivo(f"Archivo no válido: {exc}")
            self._var_etapa.set("Corrija el archivo antes de procesar.")
            messagebox.showerror("Estructura no válida", str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            self._limpiar_validacion_archivo(f"No se pudo leer el archivo: {exc}")
            messagebox.showerror("Error al leer el archivo", str(exc))
            return

        self._preview_info = info
        self._preview_df = info["vista"]
        self._ruta_preview = clave_ruta
        self._combo_hoja.configure(values=info["hojas"])
        self._var_hoja.set(info["hoja"])

        reconocidas = sum(len(v) for v in info["columnas_reconocidas"].values())
        faltantes = info["faltantes"]
        detalle = (
            f"Hoja: {info['hoja']} | fila de encabezado: {info['indice_encabezado'] + 1} | "
            f"columnas reconocidas: {reconocidas}/11"
        )
        if faltantes:
            detalle += " | FALTAN: " + ", ".join(faltantes)
        self._var_info_preview.set(detalle)
        if faltantes:
            self._var_etapa.set("Faltan columnas obligatorias; no se puede procesar.")
            self._btn_procesar.configure(state="disabled")
        else:
            self._var_etapa.set("Estructura validada. Puede procesar el archivo.")
            self._btn_procesar.configure(state="normal")

        self._limpiar_tabla(self._tabla_preview)
        for _, fila in self._preview_df.head(30).iterrows():
            valores = [
                _texto(fila.get(columna)) for columna in COLUMNAS_OFICIALES
            ]
            self._tabla_preview.insert("", "end", values=valores)

    def _procesar(self) -> None:
        if self._ocupado:
            return
        ruta_texto = self._var_archivo.get().strip()
        if not ruta_texto:
            messagebox.showwarning("Sin archivo", "Seleccione un archivo .xlsx.")
            return
        clave_ruta = str(Path(ruta_texto).resolve())
        if self._preview_info is None or self._ruta_preview != clave_ruta:
            self._previsualizar()
        if self._preview_info is None:
            return
        if self._preview_info.get("faltantes"):
            messagebox.showerror(
                "Estructura no válida",
                "El archivo no contiene todas las columnas oficiales requeridas.",
            )
            return
        ruta = Path(ruta_texto)
        hoja = self._preview_info["hoja"]

        self._iniciar_ocupado("Procesando archivo…")
        self._barra.configure(mode="indeterminate")
        self._barra.start(12)
        self._var_etapa.set("Etapa 1/3: extrayendo y normalizando registros…")

        def tarea() -> Any:
            with bloqueo("procesamiento de tickets"):
                return procesar_archivo(
                    ruta,
                    self._maestro,
                    hoja=hoja,
                    sobrescribir=False,
                    formato_fecha=cfg.FORMATO_FECHA_PREFERIDO,
                    usuario_ejecutor=os.getenv("USERNAME", ""),
                )

        self._ejecutar_hilo(tarea, self._fin_procesar, "procesamiento")

    def _fin_procesar(self, estadisticas: Any) -> None:
        self._barra.stop()
        self._barra.configure(mode="determinate", value=100)
        self._ultimo_procesado = estadisticas.archivo_salida
        self._ultimo_manifiesto = estadisticas.archivo_manifiesto or ""
        svc_estado.actualizar_desde_estadisticas(
            estadisticas,
            operacion="PROCESAMIENTO",
            mensajes=[
                f"{estadisticas.archivo_entrada}: {estadisticas.registros_totales} registros, "
                f"{estadisticas.registros_validos} válidos, "
                f"{estadisticas.registros_pendientes} pendientes."
            ],
        )
        self._var_etapa.set("Procesamiento finalizado.")
        self._var_contadores.set(
            f"Leídos: {estadisticas.registros_totales} | "
            f"Válidos: {estadisticas.registros_validos} | "
            f"Observados: {estadisticas.registros_observados} | "
            f"Pendientes: {estadisticas.registros_pendientes} | "
            f"Duplicados: {estadisticas.registros_duplicados} | "
            f"Errores: {estadisticas.registros_error}"
        )
        self._refrescar_panel()
        self._refrescar_pendientes()
        self._refrescar_maestro()
        self._refrescar_solicitantes(
            ruta_adicional=Path(estadisticas.archivo_salida)
        )
        self._refrescar_auditoria()
        self._terminar_ocupado()
        self._var_archivo.set("")
        self._limpiar_validacion_archivo(
            "Procesamiento finalizado. Seleccione otro Excel para continuar."
        )
        messagebox.showinfo(
            "Procesamiento completado",
            "Se generó el Excel con las 11 columnas oficiales.\n\n"
            f"Archivo: {cfg.ruta_visible(Path(estadisticas.archivo_salida))}\n"
            f"Registros: {estadisticas.registros_totales}\n"
            f"Pendientes de área: {estadisticas.registros_pendientes}",
        )
        messagebox.showwarning(
            "Advertencia",
            "No generar los reportes definitivos antes de incorporar las correcciones manuales al maestro.",
        )

    def _abrir_procesado(self) -> None:
        ruta = self._ultimo_procesado or self._estado.ultimo_archivo_procesado
        if not ruta or not Path(ruta).exists():
            messagebox.showinfo(
                "Sin resultado", "Todavía no existe un Excel procesado para abrir."
            )
            return
        try:
            _abrir(ruta)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("No se pudo abrir", str(exc))

    # ------------------------------------------------------------------
    # Usuarios y áreas
    # ------------------------------------------------------------------
    def _construir_usuarios(self) -> None:
        self._tab_usuarios.columnconfigure(0, weight=1)
        self._tab_usuarios.rowconfigure(1, weight=3)
        self._tab_usuarios.rowconfigure(2, weight=2)

        ttk.Label(
            self._tab_usuarios,
            text="Correspondencias USUARIO → ÁREA (persistentes)",
            style="Seccion.TLabel",
        ).grid(row=0, column=0, sticky="w")

        superior = ttk.Frame(self._tab_usuarios)
        superior.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        superior.columnconfigure(0, weight=3)
        superior.columnconfigure(1, weight=2)
        superior.rowconfigure(0, weight=1)

        marco_maestro = ttk.LabelFrame(
            superior, text="Usuarios registrados", padding=6
        )
        marco_maestro.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        busqueda = ttk.Frame(marco_maestro)
        busqueda.pack(fill="x", pady=(0, 6))
        self._var_busqueda = tk.StringVar()
        ttk.Entry(busqueda, textvariable=self._var_busqueda).pack(
            side="left", fill="x", expand=True
        )
        ttk.Button(busqueda, text="Buscar", command=self._refrescar_usuarios).pack(
            side="left", padx=(6, 0)
        )
        ttk.Button(
            busqueda, text="Limpiar", command=self._limpiar_busqueda
        ).pack(side="left", padx=(6, 0))
        self._tabla_usuarios = self._crear_tabla(
            marco_maestro,
            columnas=["USUARIO", "AREA_USUARIO"],
            anchos={"USUARIO": 230, "AREA_USUARIO": 200},
            alto=10,
        )
        self._tabla_usuarios.bind("<<TreeviewSelect>>", self._seleccionar_usuario)

        marco_form = ttk.LabelFrame(
            superior, text="Registrar o modificar área", padding=8
        )
        marco_form.grid(row=0, column=1, sticky="nsew")
        ttk.Label(marco_form, text="Usuario").pack(anchor="w")
        self._var_usuario = tk.StringVar()
        ttk.Entry(marco_form, textvariable=self._var_usuario).pack(
            fill="x", pady=(2, 8)
        )
        ttk.Label(marco_form, text="Área").pack(anchor="w")
        self._var_area = tk.StringVar()
        self._combo_area = ttk.Combobox(
            marco_form, textvariable=self._var_area, state="normal"
        )
        self._combo_area.pack(fill="x", pady=(2, 8))
        self._combo_area.configure(values=self._areas_conocidas())

        ttk.Button(
            marco_form, text="Sugerir coincidencias", command=self._sugerir
        ).pack(anchor="w")
        self._lista_sugerencias = tk.Listbox(
            marco_form, height=5, font=("Segoe UI", 9), exportselection=False
        )
        self._lista_sugerencias.pack(fill="x", pady=(4, 8))
        self._lista_sugerencias.bind(
            "<<ListboxSelect>>", self._aplicar_sugerencia
        )
        ttk.Button(
            marco_form,
            text="Guardar asignación",
            style="Principal.TButton",
            command=self._guardar_asignacion,
        ).pack(anchor="w")
        self._var_info_usuario = tk.StringVar(value="")
        ttk.Label(
            marco_form,
            textvariable=self._var_info_usuario,
            foreground=COLOR_TEXTO,
            wraplength=360,
        ).pack(anchor="w", pady=(8, 0))

        marco_pend = ttk.LabelFrame(
            self._tab_usuarios,
            text="Usuarios pendientes detectados en el último procesamiento",
            padding=6,
        )
        marco_pend.grid(row=2, column=0, sticky="nsew", pady=(10, 0))
        self._tabla_pendientes = self._crear_tabla(
            marco_pend,
            columnas=["Usuario", "Filas", "Área en archivo", "Sugerencia"],
            anchos={"Usuario": 250, "Filas": 60, "Área en archivo": 180, "Sugerencia": 380},
            alto=4,
        )
        self._tabla_pendientes.bind(
            "<<TreeviewSelect>>", self._seleccionar_pendiente
        )

    def _areas_conocidas(self) -> list[str]:
        df = self._maestro.como_dataframe()
        if df.empty:
            return []
        areas = sorted(
            {
                str(valor).strip()
                for valor in df[cfg.COLUMNA_AREA].tolist()
                if str(valor).strip() and str(valor).lower() != "nan"
            }
        )
        return areas

    def _limpiar_busqueda(self) -> None:
        self._var_busqueda.set("")
        self._refrescar_usuarios()

    def _refrescar_usuarios(self) -> None:
        try:
            tabla = svc_asignaciones.tabla_usuarios(
                self._maestro, self._var_busqueda.get()
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.error("No se pudo listar usuarios: %s", exc)
            return
        self._limpiar_tabla(self._tabla_usuarios)
        for _, fila in tabla.iterrows():
            self._tabla_usuarios.insert(
                "",
                "end",
                values=(fila[cfg.COLUMNA_USUARIO], fila[cfg.COLUMNA_AREA]),
            )
        self._combo_area.configure(values=self._areas_conocidas())

    def _refrescar_pendientes(self) -> None:
        self._limpiar_tabla(self._tabla_pendientes)
        if not self._ultimo_manifiesto:
            return
        try:
            pendientes = svc_asignaciones.usuarios_pendientes(
                self._ultimo_manifiesto, self._maestro
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.error("No se pudieron leer los pendientes: %s", exc)
            return
        for item in pendientes:
            sugerencia = ""
            if item["sugerencias"]:
                mejor = item["sugerencias"][0]
                sugerencia = f"{mejor['usuario']} ({mejor['similitud']:.0%}) → {mejor['area']}"
            self._tabla_pendientes.insert(
                "",
                "end",
                values=(
                    item["usuario"],
                    item["filas"],
                    item["area_preexistente"] or "—",
                    sugerencia or "Sin sugerencia",
                ),
            )

    def _seleccionar_usuario(self, _evento=None) -> None:
        seleccion = self._tabla_usuarios.selection()
        if not seleccion:
            return
        valores = self._tabla_usuarios.item(seleccion[0], "values")
        self._var_usuario.set(valores[0])
        self._var_area.set(valores[1])

    def _seleccionar_pendiente(self, _evento=None) -> None:
        seleccion = self._tabla_pendientes.selection()
        if not seleccion:
            return
        valores = self._tabla_pendientes.item(seleccion[0], "values")
        self._var_usuario.set(valores[0])
        sugerencias = svc_asignaciones.sugerencias_usuario(
            valores[0], self._maestro
        )
        self._lista_sugerencias.delete(0, tk.END)
        for item in sugerencias:
            self._lista_sugerencias.insert(
                tk.END,
                f"{item['usuario']} — {item['area']} ({item['similitud']:.0%})",
            )
        if sugerencias:
            self._var_area.set(sugerencias[0]["area"])

    def _sugerir(self) -> None:
        usuario = self._var_usuario.get().strip()
        if not usuario:
            messagebox.showinfo("Sin usuario", "Escriba el nombre del usuario.")
            return
        sugerencias = svc_asignaciones.sugerencias_usuario(usuario, self._maestro)
        self._lista_sugerencias.delete(0, tk.END)
        for item in sugerencias:
            self._lista_sugerencias.insert(
                tk.END,
                f"{item['usuario']} — {item['area']} ({item['similitud']:.0%})",
            )
        if not sugerencias:
            self._lista_sugerencias.insert(
                tk.END, "Sin coincidencias suficientemente parecidas."
            )

    def _aplicar_sugerencia(self, _evento=None) -> None:
        seleccion = self._lista_sugerencias.curselection()
        if not seleccion:
            return
        texto = self._lista_sugerencias.get(seleccion[0])
        if "—" not in texto:
            return
        partes = texto.split("—")
        if len(partes) >= 2:
            area = partes[1].split("(")[0].strip()
            self._var_area.set(area)

    def _guardar_asignacion(self) -> None:
        usuario = self._var_usuario.get().strip()
        area = self._var_area.get().strip()
        if not usuario or not area:
            messagebox.showwarning(
                "Datos incompletos", "Indique el usuario y el área."
            )
            return
        try:
            with bloqueo("actualización de usuarios y áreas"):
                resultado = svc_asignaciones.asignar_una(
                    self._maestro,
                    usuario,
                    area,
                    usuario_ejecutor=os.getenv("USERNAME", ""),
                )
        except (ErrorArchivo, ErrorEstructura, ErrorBloqueo) as exc:
            messagebox.showerror("No se pudo guardar", str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Error inesperado", str(exc))
            return

        self._refrescar_usuarios()
        self._refrescar_pendientes()
        self._refrescar_panel()
        self._refrescar_auditoria()
        accion = resultado.asignaciones[0].accion if resultado.asignaciones else "sin_cambios"
        self._var_info_usuario.set(f"Asignación guardada ({accion}): {usuario} → {area}")

    # ------------------------------------------------------------------
    # Reimportación
    # ------------------------------------------------------------------
    def _construir_reimportar(self) -> None:
        ttk.Label(
            self._tab_reimportar,
            text="Importar el Excel procesado después de corregirlo en Excel",
            style="Seccion.TLabel",
        ).pack(anchor="w")
        ttk.Label(
            self._tab_reimportar,
            text=(
                "El archivo debe conservar exactamente las 11 columnas oficiales "
                "y su orden. Las filas ausentes no se eliminan del maestro."
            ),
            foreground="#4B5563",
        ).pack(anchor="w", pady=(2, 8))

        fila = ttk.Frame(self._tab_reimportar)
        fila.pack(fill="x")
        self._var_corregido = tk.StringVar()
        ttk.Entry(fila, textvariable=self._var_corregido).pack(
            side="left", fill="x", expand=True
        )
        ttk.Button(fila, text="Examinar…", command=self._elegir_corregido).pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(
            fila, text="Analizar diferencias", command=self._analizar_reimportacion
        ).pack(side="left", padx=(8, 0))

        self._var_resumen_reimport = tk.StringVar(
            value="Seleccione un archivo corregido para compararlo."
        )
        ttk.Label(
            self._tab_reimportar,
            textvariable=self._var_resumen_reimport,
            foreground=COLOR_TEXTO,
            font=("Segoe UI", 9, "bold"),
            wraplength=1150,
        ).pack(anchor="w", pady=(8, 6))

        botones = ttk.Frame(self._tab_reimportar)
        botones.pack(side="bottom", fill="x", pady=(8, 0))
        self._var_permitir_duplicados = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            botones,
            text="Permitir aplicar filas marcadas como duplicadas",
            variable=self._var_permitir_duplicados,
        ).pack(side="left")
        self._btn_aplicar_reimport = ttk.Button(
            botones,
            text="Aplicar cambios confirmados",
            style="Principal.TButton",
            command=self._aplicar_reimportacion,
            state="disabled",
        )
        self._btn_aplicar_reimport.pack(side="right")

        panel = ttk.Panedwindow(self._tab_reimportar, orient="vertical")
        panel.pack(fill="both", expand=True)

        marco_tabla = ttk.Frame(panel)
        self._tabla_reimport = self._crear_tabla(
            marco_tabla,
            columnas=["Tipo", "Fila corregida", "Fila original", "Campos", "Similitud", "Observación"],
            anchos={
                "Tipo": 100,
                "Fila corregida": 100,
                "Fila original": 100,
                "Campos": 260,
                "Similitud": 80,
                "Observación": 520,
            },
            alto=10,
        )
        self._tabla_reimport.bind(
            "<<TreeviewSelect>>", self._mostrar_detalle_cambio
        )
        panel.add(marco_tabla, weight=3)

        marco_detalle = ttk.LabelFrame(panel, text="Detalle del cambio", padding=6)
        self._texto_cambio = tk.Text(
            marco_detalle,
            height=8,
            font=("Consolas", 9),
            wrap="word",
            bg="white",
        )
        self._texto_cambio.pack(fill="both", expand=True)
        panel.add(marco_detalle, weight=2)

    def _elegir_corregido(self) -> None:
        ruta = filedialog.askopenfilename(
            title="Seleccione el Excel corregido",
            initialdir=str(cfg.OUTPUT_DIR if cfg.OUTPUT_DIR.exists() else cfg.BASE_DIR),
            filetypes=[("Excel", "*.xlsx")],
        )
        if ruta:
            self._var_corregido.set(ruta)

    def _analizar_reimportacion(self) -> None:
        ruta = self._var_corregido.get().strip()
        if not ruta:
            messagebox.showwarning(
                "Sin archivo", "Seleccione el Excel corregido."
            )
            return
        try:
            resultado = analizar_reimportacion(ruta)
        except (ErrorArchivo, ErrorEstructura) as exc:
            messagebox.showerror("No se pudo analizar", str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Error inesperado", str(exc))
            return

        self._reimportacion = resultado
        resumen = resultado.resumen
        self._var_resumen_reimport.set(
            " | ".join(
                f"{_ETIQUETAS_TIPO.get(tipo, tipo)}: {cantidad}"
                for tipo, cantidad in sorted(resumen.items())
            )
            + f" | Total: {len(resultado.cambios)}"
        )
        self._limpiar_tabla(self._tabla_reimport)
        orden = {
            TIPO_MODIFICADO: 0,
            TIPO_NUEVO: 1,
            TIPO_DUPLICADO: 2,
            TIPO_AMBIGUO: 3,
            TIPO_ERROR: 4,
            TIPO_AUSENTE: 5,
            TIPO_SIN_CAMBIOS: 6,
        }
        for cambio in sorted(
            resultado.cambios, key=lambda c: (orden.get(c.tipo, 9), c.fila_corregida or 0)
        ):
            self._tabla_reimport.insert(
                "",
                "end",
                iid=f"{cambio.tipo}-{cambio.fila_corregida}-{cambio.id_tecnico}",
                values=(
                    _ETIQUETAS_TIPO.get(cambio.tipo, cambio.tipo),
                    cambio.fila_corregida if cambio.fila_corregida is not None else "—",
                    cambio.fila_original if cambio.fila_original is not None else "—",
                    cambio.campos or "—",
                    f"{cambio.similitud:.0%}" if cambio.similitud else "—",
                    cambio.observacion,
                ),
            )

        ambiguos = resumen.get(TIPO_AMBIGUO, 0)
        self._btn_aplicar_reimport.configure(
            state="disabled" if ambiguos else "normal"
        )
        if ambiguos:
            self._var_resumen_reimport.set(
                self._var_resumen_reimport.get()
                + " | Resuelva las coincidencias ambiguas antes de aplicar."
            )
        if not resultado.aplicables:
            self._btn_aplicar_reimport.configure(state="disabled")

    def _mostrar_detalle_cambio(self, _evento=None) -> None:
        seleccion = self._tabla_reimport.selection()
        if not seleccion or self._reimportacion is None:
            return
        iid = seleccion[0]
        cambio = next(
            (
                item
                for item in self._reimportacion.cambios
                if f"{item.tipo}-{item.fila_corregida}-{item.id_tecnico}" == iid
            ),
            None,
        )
        if cambio is None:
            return
        lineas = [f"Tipo: {_ETIQUETAS_TIPO.get(cambio.tipo, cambio.tipo)}"]
        if cambio.campos_cambiados:
            lineas.append("Campos modificados:")
            for campo, (antes, despues) in cambio.campos_cambiados.items():
                lineas.append(f"  {campo}: {antes!r} → {despues!r}")
        if cambio.antes:
            lineas.append("\nAntes:")
            for campo in COLUMNAS_OFICIALES:
                lineas.append(f"  {campo}: {cambio.antes.get(campo, '')}")
        if cambio.despues:
            lineas.append("\nDespués:")
            for campo in COLUMNAS_OFICIALES:
                lineas.append(f"  {campo}: {cambio.despues.get(campo, '')}")
        lineas.append("\n" + cambio.observacion)
        self._texto_cambio.delete("1.0", tk.END)
        self._texto_cambio.insert("1.0", "\n".join(lineas))

    def _aplicar_reimportacion(self) -> None:
        if self._reimportacion is None or self._ocupado:
            return
        resumen = self._reimportacion.resumen
        if resumen.get(TIPO_AMBIGUO, 0):
            messagebox.showerror(
                "Coincidencias ambiguas",
                "Hay filas con más de una coincidencia posible. "
                "El sistema no las actualiza automáticamente.",
            )
            return

        cambios = sum(
            resumen.get(tipo, 0) for tipo in (TIPO_NUEVO, TIPO_MODIFICADO, TIPO_DUPLICADO)
        )
        if cambios == 0:
            messagebox.showinfo(
                "Sin cambios",
                "No hay registros nuevos ni modificados para aplicar.",
            )
            return
        if not messagebox.askyesno(
            "Confirmar reimportación",
            "Se aplicarán los cambios al maestro histórico de tickets.\n\n"
            f"Nuevos: {resumen.get(TIPO_NUEVO, 0)}\n"
            f"Modificados: {resumen.get(TIPO_MODIFICADO, 0)}\n"
            f"Duplicados: {resumen.get(TIPO_DUPLICADO, 0)}\n"
            f"Sin cambios: {resumen.get(TIPO_SIN_CAMBIOS, 0)}\n"
            f"Ausentes (se conservan): {resumen.get(TIPO_AUSENTE, 0)}\n\n"
            "¿Desea continuar?",
        ):
            return

        self._iniciar_ocupado("Aplicando reimportación…")
        permitir = bool(self._var_permitir_duplicados.get())
        resultado = self._reimportacion

        def tarea() -> Any:
            with bloqueo("reimportación de correcciones"):
                return aplicar_reimportacion(
                    resultado,
                    permitir_duplicados=permitir,
                    usuario_ejecutor=os.getenv("USERNAME", ""),
                )

        def fin(salida: Any) -> None:
            svc_estado.registrar_consolidacion(
                {
                    "nuevos": salida["nuevos"],
                    "actualizados": salida["actualizados"],
                    "duplicados": salida["duplicados"],
                    "total_maestro": salida["total_maestro"],
                    "archivo": resultado.ruta_corregido.name,
                }
            )
            self._refrescar_panel()
            self._refrescar_usuarios()
            self._refrescar_maestro()
            self._refrescar_auditoria()
            self._terminar_ocupado()
            messagebox.showinfo(
                "Reimportación aplicada",
                f"Nuevos: {salida['nuevos']}\n"
                f"Actualizados: {salida['actualizados']}\n"
                f"Sin cambios: {salida['sin_cambios']}\n"
                f"Duplicados aplicados: {salida['duplicados']}\n"
                f"Total en maestro: {salida['total_maestro']}",
            )

        self._ejecutar_hilo(tarea, fin, "reimportación")

    # ------------------------------------------------------------------
    # Maestro histórico
    # ------------------------------------------------------------------
    def _construir_maestro(self) -> None:
        ttk.Label(
            self._tab_maestro,
            text="Consolidación histórica de tickets",
            style="Seccion.TLabel",
        ).pack(anchor="w")
        self._lbl_maestro = ttk.Label(
            self._tab_maestro, text="", foreground=COLOR_TEXTO, wraplength=1150
        )
        self._lbl_maestro.pack(anchor="w", pady=(4, 10))

        botones = ttk.Frame(self._tab_maestro)
        botones.pack(fill="x")
        ttk.Button(
            botones,
            text="Consolidar último procesado",
            style="Principal.TButton",
            command=lambda: self._consolidar(None),
        ).pack(side="left")
        ttk.Button(
            botones,
            text="Seleccionar procesado y consolidar…",
            command=self._elegir_y_consolidar,
        ).pack(side="left", padx=8)
        ttk.Button(
            botones,
            text="Abrir maestro",
            command=self._abrir_maestro,
        ).pack(side="left", padx=8)

        marco_solicitantes = ttk.LabelFrame(
            self._tab_maestro,
            text="Detección y normalización de solicitantes",
            padding=8,
        )
        marco_solicitantes.pack(fill="both", expand=False, pady=(12, 0))
        self._tabla_solicitantes = self._crear_tabla(
            marco_solicitantes,
            columnas=[
                "Solicitante detectado",
                "Posible coincidencia",
                "Tickets",
                "Nivel de coincidencia",
                "Acción",
            ],
            anchos={
                "Solicitante detectado": 240,
                "Posible coincidencia": 240,
                "Tickets": 70,
                "Nivel de coincidencia": 170,
                "Acción": 140,
            },
            alto=5,
        )
        barra_solicitantes = ttk.Frame(marco_solicitantes)
        barra_solicitantes.pack(fill="x", pady=(6, 0))
        ttk.Button(
            barra_solicitantes,
            text="Auditar solicitantes",
            command=self._refrescar_solicitantes,
        ).pack(side="left")
        ttk.Button(
            barra_solicitantes,
            text="Unificar con coincidencia",
            command=self._unificar_solicitante_seleccionado,
        ).pack(side="left", padx=6)
        ttk.Button(
            barra_solicitantes,
            text="Mantener separados",
            command=self._separar_solicitante_seleccionado,
        ).pack(side="left")
        ttk.Button(
            barra_solicitantes,
            text="Dejar pendiente",
            command=self._pendiente_solicitante_seleccionado,
        ).pack(side="left", padx=6)
        ttk.Button(
            barra_solicitantes,
            text="Revertir equivalencia",
            command=self._revertir_solicitante_seleccionado,
        ).pack(side="left")
        self._var_info_solicitantes = tk.StringVar(value="")
        ttk.Label(
            marco_solicitantes,
            textvariable=self._var_info_solicitantes,
            foreground="#6B7280",
            wraplength=1100,
        ).pack(anchor="w", pady=(5, 0))

        marco_copias = ttk.LabelFrame(
            self._tab_maestro,
            text="Copias de seguridad del maestro (restauración)",
            padding=8,
        )
        marco_copias.pack(fill="both", expand=True, pady=(10, 0))
        self._lista_copias = tk.Listbox(
            marco_copias, font=("Consolas", 9), height=12
        )
        self._lista_copias.pack(side="left", fill="both", expand=True)
        barra = ttk.Scrollbar(
            marco_copias, orient="vertical", command=self._lista_copias.yview
        )
        barra.pack(side="left", fill="y")
        self._lista_copias.configure(yscrollcommand=barra.set)

        acciones = ttk.Frame(marco_copias)
        acciones.pack(side="left", fill="y", padx=(10, 0))
        ttk.Button(
            acciones, text="Actualizar lista", command=self._refrescar_copias
        ).pack(fill="x")
        ttk.Button(
            acciones,
            text="Restaurar seleccionada",
            command=self._restaurar_copia,
        ).pack(fill="x", pady=(8, 0))
        ttk.Button(
            acciones,
            text="Abrir carpeta de copias",
            command=lambda: _abrir(cfg.BACKUPS_DIR),
        ).pack(fill="x", pady=(8, 0))
        ttk.Label(
            acciones,
            text="La restauración crea primero una copia\ndel maestro actual.",
            foreground="#6B7280",
            font=("Segoe UI", 8),
        ).pack(anchor="w", pady=(12, 0))
        self._refrescar_copias()

    def _refrescar_maestro(self) -> None:
        if not hasattr(self, "_lbl_maestro"):
            return
        self._lbl_maestro.configure(text=self._texto_maestro())
        self._refrescar_copias()
        self._refrescar_solicitantes()

    def _refrescar_solicitantes(self, ruta_adicional: Path | None = None) -> None:
        if not hasattr(self, "_tabla_solicitantes"):
            return
        try:
            ruta_maestro = Path(cfg.MAESTRO_TICKETS_XLSX)
            maestro = (
                MaestroTickets.cargar(ruta_maestro).a_dataframe()
                if ruta_maestro.exists()
                else pd.DataFrame(columns=list(COLUMNAS_OFICIALES))
            )
            adicionales = None
            if ruta_adicional is not None and Path(ruta_adicional).is_file():
                try:
                    adicionales = leer_excel(
                        ruta_adicional, sheet_name=cfg.HOJA_DETALLE
                    )
                except ErrorArchivo:
                    adicionales = leer_excel(ruta_adicional)
            filas = svc_solicitantes.detectar_solicitantes(maestro, adicionales)
        except (ErrorArchivo, ErrorEstructura, OSError, ValueError) as exc:
            LOGGER.error("No se pudo auditar solicitantes: %s", exc)
            self._var_info_solicitantes.set(f"No se pudo auditar solicitantes: {exc}")
            return

        self._solicitantes_por_iid.clear()
        self._limpiar_tabla(self._tabla_solicitantes)
        pendientes = 0
        genericos = 0
        for indice, fila in enumerate(filas):
            iid = f"solicitante-{indice}"
            self._solicitantes_por_iid[iid] = fila
            self._tabla_solicitantes.insert(
                "",
                "end",
                iid=iid,
                values=(
                    fila["solicitante"],
                    fila["coincidencia"] or "—",
                    fila["tickets"],
                    fila["nivel"],
                    fila["accion"],
                ),
            )
            if fila["accion"] == "Pendiente":
                pendientes += 1
            if fila["generico"]:
                genericos += 1
        self._var_info_solicitantes.set(
            f"{len(filas)} solicitantes revisados | "
            f"{pendientes} coincidencias requieren decisión | "
            f"{genericos} solicitantes genéricos protegidos. "
            "Las sugerencias no fusionan ni modifican los nombres originales."
        )

    def _solicitante_seleccionado(self) -> dict[str, Any] | None:
        seleccion = self._tabla_solicitantes.selection()
        if not seleccion:
            messagebox.showinfo(
                "Sin selección", "Seleccione un solicitante de la tabla."
            )
            return None
        return self._solicitantes_por_iid.get(seleccion[0])

    def _unificar_solicitante_seleccionado(self) -> None:
        fila = self._solicitante_seleccionado()
        if fila is None:
            return
        candidato = fila["coincidencia"]
        if not candidato:
            messagebox.showinfo(
                "Sin coincidencia",
                "No existe una coincidencia para unificar. Revise la fila o mantenga el solicitante separado.",
            )
            return
        if fila["generico"]:
            messagebox.showwarning(
                "Solicitante genérico",
                "Los solicitantes genéricos no se pueden unificar.",
            )
            return
        if not messagebox.askyesno(
            "Confirmar equivalencia",
            f"Se reconocerá «{fila['solicitante']}» como «{candidato}» en futuras comparaciones.\n"
            "Los nombres del maestro y el historial de tickets no se modificarán.\n\n"
            "¿Desea guardar esta equivalencia?",
        ):
            return
        try:
            svc_solicitantes.unificar_solicitante(fila["solicitante"], candidato)
        except (OSError, ValueError, ErrorArchivo) as exc:
            messagebox.showerror("No se pudo guardar la equivalencia", str(exc))
            return
        self._refrescar_solicitantes()
        self._refrescar_auditoria()

    def _separar_solicitante_seleccionado(self) -> None:
        fila = self._solicitante_seleccionado()
        if fila is None:
            return
        candidato = fila["coincidencia"]
        if not candidato:
            messagebox.showinfo(
                "Sin coincidencia",
                "No hay una coincidencia que deba mantenerse separada.",
            )
            return
        try:
            svc_solicitantes.mantener_separados(fila["solicitante"], candidato)
        except (OSError, ValueError, ErrorArchivo) as exc:
            messagebox.showerror("No se pudo guardar la decisión", str(exc))
            return
        self._refrescar_solicitantes()
        self._refrescar_auditoria()

    def _pendiente_solicitante_seleccionado(self) -> None:
        fila = self._solicitante_seleccionado()
        if fila is None:
            return
        try:
            svc_solicitantes.dejar_pendiente(
                fila["solicitante"], fila["coincidencia"]
            )
        except (OSError, ValueError, ErrorArchivo) as exc:
            messagebox.showerror("No se pudo guardar la decisión", str(exc))
            return
        self._refrescar_solicitantes()
        self._refrescar_auditoria()

    def _revertir_solicitante_seleccionado(self) -> None:
        fila = self._solicitante_seleccionado()
        if fila is None:
            return
        try:
            svc_solicitantes.revertir_equivalencia(fila["solicitante"])
        except (OSError, ValueError, ErrorArchivo) as exc:
            messagebox.showerror("No se pudo revertir la equivalencia", str(exc))
            return
        self._refrescar_solicitantes()
        self._refrescar_auditoria()

    def _copias(self) -> list[Path]:
        if not cfg.BACKUPS_DIR.exists():
            return []
        return sorted(
            cfg.BACKUPS_DIR.glob("maestro_tickets_*.xlsx"),
            key=lambda ruta: ruta.stat().st_mtime,
            reverse=True,
        )

    def _refrescar_copias(self) -> None:
        if not hasattr(self, "_lista_copias"):
            return
        self._lista_copias.delete(0, tk.END)
        for ruta in self._copias():
            self._lista_copias.insert(tk.END, ruta.name)

    def _elegir_y_consolidar(self) -> None:
        ruta = filedialog.askopenfilename(
            title="Seleccione el Excel procesado a consolidar",
            initialdir=str(cfg.OUTPUT_DIR if cfg.OUTPUT_DIR.exists() else cfg.BASE_DIR),
            filetypes=[("Excel", "*.xlsx")],
        )
        if ruta:
            self._consolidar(Path(ruta))

    def _consolidar(self, ruta: Path | None) -> None:
        if self._ocupado:
            return
        if ruta is None:
            texto = self._ultimo_procesado or self._estado.ultimo_archivo_procesado
            if not texto:
                messagebox.showinfo(
                    "Sin procesado",
                    "Primero procese un archivo o seleccione uno para consolidar.",
                )
                return
            ruta = Path(texto)
        if not ruta.exists():
            messagebox.showerror("No existe", f"No se encontró: {ruta}")
            return
        if not messagebox.askyesno(
            "Confirmar consolidación",
            "Se incorporarán los tickets nuevos y se actualizarán los existentes.\n"
            "Los registros históricos ausentes se conservan y se crea una copia "
            "de seguridad antes de escribir.\n\n"
            f"Archivo: {ruta.name}\n\n¿Desea continuar?",
        ):
            return

        self._refrescar_solicitantes(ruta_adicional=ruta)
        por_revisar = [
            fila
            for fila in self._solicitantes_por_iid.values()
            if fila["accion"] == "Pendiente"
            and fila["nivel"] in {"Coincidencia exacta", "Posible coincidencia"}
        ]
        if por_revisar and messagebox.askyesno(
            "Revisar solicitantes",
            f"Hay {len(por_revisar)} solicitantes con coincidencias que aún no tienen decisión.\n"
            "¿Desea revisar la tabla del maestro histórico antes de consolidar?\n\n"
            "Si elige No, se continuará sin fusionar ningún nombre.",
        ):
            self._cuaderno.select(self._tab_maestro)
            return

        self._iniciar_ocupado("Consolidando en el maestro histórico…")

        def tarea() -> Any:
            with bloqueo("consolidación del maestro"):
                return consolidar_procesado(
                    ruta,
                    usuario_ejecutor=os.getenv("USERNAME", ""),
                )

        def fin(salida: Any) -> None:
            svc_estado.registrar_consolidacion(salida)
            self._refrescar_maestro()
            self._refrescar_panel()
            self._refrescar_auditoria()
            self._terminar_ocupado()
            messagebox.showinfo(
                "Consolidación completada",
                f"Nuevos: {salida['nuevos']}\n"
                f"Actualizados: {salida['actualizados']}\n"
                f"Sin cambios: {salida['sin_cambios']}\n"
                f"Duplicados omitidos: {salida['duplicados']}\n"
                f"Total en maestro: {salida['total_maestro']}\n\n"
                "Se generó una copia de seguridad del maestro anterior.",
            )

        self._ejecutar_hilo(tarea, fin, "consolidación")

    def _restaurar_copia(self) -> None:
        seleccion = self._lista_copias.curselection()
        if not seleccion:
            messagebox.showinfo(
                "Sin selección", "Seleccione una copia de seguridad de la lista."
            )
            return
        nombre = self._lista_copias.get(seleccion[0])
        ruta = cfg.BACKUPS_DIR / nombre
        if not messagebox.askyesno(
            "Confirmar restauración",
            f"Se restaurará el maestro desde:\n{nombre}\n\n"
            "El maestro actual se respaldará antes de reemplazarlo. "
            "¿Desea continuar?",
        ):
            return

        self._iniciar_ocupado("Restaurando copia de seguridad…")

        def tarea() -> Any:
            with bloqueo("restauración del maestro"):
                return MaestroTickets.restaurar_backup(ruta)

        def fin(_salida: Any) -> None:
            self._refrescar_maestro()
            self._refrescar_panel()
            self._refrescar_auditoria()
            self._terminar_ocupado()
            messagebox.showinfo(
                "Restauración completada",
                "El maestro histórico fue restaurado desde la copia seleccionada.",
            )

        self._ejecutar_hilo(tarea, fin, "restauración")

    def _abrir_maestro(self) -> None:
        if not Path(cfg.MAESTRO_TICKETS_XLSX).exists():
            messagebox.showinfo(
                "Sin maestro", "Todavía no existe el maestro histórico de tickets."
            )
            return
        try:
            _abrir(cfg.MAESTRO_TICKETS_XLSX)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("No se pudo abrir", str(exc))

    # ------------------------------------------------------------------
    # Reportes
    # ------------------------------------------------------------------
    def _construir_reportes(self) -> None:
        ttk.Label(
            self._tab_reportes,
            text="Indicadores y reportes de TI",
            style="Seccion.TLabel",
        ).pack(anchor="w")
        ttk.Label(
            self._tab_reportes,
            text="No modificar el Excel maestro ni el archivo procesado al generar reportes.",
            foreground=COLOR_ALERTA,
            background=COLOR_FONDO,
            wraplength=1100,
            justify="left",
            anchor="w",
        ).pack(anchor="w", pady=(8, 4))
        fila = ttk.Frame(self._tab_reportes)
        fila.pack(fill="x", pady=(8, 6))
        ttk.Label(fila, text="Origen de datos:").pack(side="left")
        self._var_origen_reporte = tk.StringVar(value="Último procesado")
        ttk.Combobox(
            fila,
            textvariable=self._var_origen_reporte,
            values=["Último procesado", "Maestro histórico", "Seleccionar archivo…"],
            state="readonly",
            width=28,
        ).pack(side="left", padx=(6, 10))

        ttk.Label(fila, text="Año:").pack(side="left", padx=(10, 4))
        self._var_anio_reporte = tk.StringVar(value=str(pd.Timestamp.now().year))
        ttk.Combobox(
            fila,
            textvariable=self._var_anio_reporte,
            values=[str(anio) for anio in sorted({pd.Timestamp.now().year, 2026, 2025, 2024})],
            state="readonly",
            width=8,
        ).pack(side="left")

        ttk.Label(fila, text="Mes:").pack(side="left", padx=(10, 4))
        self._var_mes_reporte = tk.StringVar(value="Todos")
        ttk.Combobox(
            fila,
            textvariable=self._var_mes_reporte,
            values=["Todos", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"],
            state="readonly",
            width=10,
        ).pack(side="left")

        ttk.Button(
            fila,
            text="Generar reporte",
            style="Principal.TButton",
            command=self._generar_reporte,
        ).pack(side="left", padx=(10, 0))
        ttk.Button(
            fila, text="Abrir reporte", command=self._abrir_reporte
        ).pack(side="left", padx=8)
        ttk.Button(
            fila,
            text="Abrir carpeta de reportes",
            command=lambda: _abrir(cfg.REPORTES_DIR),
        ).pack(side="left", padx=8)

        self._texto_reporte = tk.Text(
            self._tab_reportes,
            font=("Consolas", 10),
            wrap="word",
            bg="white",
            padx=10,
            pady=10,
        )
        self._texto_reporte.pack(fill="both", expand=True, pady=(8, 0))
        self._texto_reporte.insert(
            "1.0",
            "Seleccione el origen y presione «Generar reporte».\n\n"
            "El reporte incluye indicadores generales, distribuciones por atención, "
            "requerimiento, responsable, área, subtipo, proyecto y actividad, "
            "evolución semanal, esfuerzo y recurrencia. Se guarda como Excel y Markdown "
            "en la carpeta reportes.",
        )

    def _elegir_origen_reporte(self) -> Path | None:
        origen = self._var_origen_reporte.get()
        if origen == "Maestro histórico":
            return None
        if origen == "Seleccionar archivo…":
            ruta = filedialog.askopenfilename(
                title="Seleccione el Excel con los 11 campos oficiales",
                initialdir=str(cfg.OUTPUT_DIR if cfg.OUTPUT_DIR.exists() else cfg.BASE_DIR),
                filetypes=[("Excel", "*.xlsx")],
            )
            return Path(ruta) if ruta else None
        texto = self._ultimo_procesado or self._estado.ultimo_archivo_procesado
        if not texto:
            messagebox.showinfo(
                "Sin datos", "Todavía no existe un procesamiento para reportar."
            )
            return None
        return Path(texto)

    def _datos_reporte(self) -> pd.DataFrame | None:
        origen = self._var_origen_reporte.get()
        if origen == "Maestro histórico":
            if not Path(cfg.MAESTRO_TICKETS_XLSX).exists():
                messagebox.showinfo(
                    "Sin maestro", "Todavía no existe el maestro histórico."
                )
                return None
            return MaestroTickets.cargar().a_dataframe()

        ruta = self._elegir_origen_reporte()
        if ruta is None:
            return None
        if not ruta.exists():
            messagebox.showerror("No existe", f"No se encontró: {ruta}")
            return None
        try:
            return leer_excel(ruta, sheet_name=cfg.HOJA_DETALLE)
        except Exception:
            return leer_excel(ruta)

    def _generar_reporte(self) -> None:
        if self._ocupado:
            return
        try:
            df = self._datos_reporte()
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("No se pudo leer el origen", str(exc))
            return
        if df is None or df.empty:
            return
        self._iniciar_ocupado("Generando reporte…")

        def tarea() -> Any:
            marca = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
            return generar_reporte(
                df,
                ruta_salida=cfg.REPORTES_DIR / f"reporte_tickets_{marca}.xlsx",
            )

        def fin(resultado: Any) -> None:
            self._reporte = resultado
            svc_estado.registrar_reporte(resultado["xlsx"])
            self._refrescar_panel()
            self._terminar_ocupado()
            self._mostrar_reporte_en_pantalla(resultado)
            messagebox.showinfo(
                "Reporte generado",
                f"Excel: {cfg.ruta_visible(resultado['xlsx'])}\n"
                f"Markdown: {cfg.ruta_visible(resultado['markdown'])}",
            )

        self._ejecutar_hilo(tarea, fin, "reporte")

    def _mostrar_reporte_en_pantalla(self, resultado: dict[str, Any]) -> None:
        indicadores = resultado["indicadores"]
        tablas = resultado["tablas"]
        lineas = ["INDICADORES GENERALES", "=" * 40]
        for clave, valor in indicadores.items():
            if clave == "Nota":
                continue
            lineas.append(f"{clave:<38}: {valor}")
        lineas.extend(["", indicadores.get("Nota", ""), ""])
        for nombre, tabla in tablas.items():
            if tabla is None or tabla.empty:
                continue
            lineas.append(nombre.upper())
            lineas.append("-" * 40)
            lineas.append(tabla.to_string(index=False))
            lineas.append("")
        recurrencia = resultado.get("recurrencia")
        if recurrencia is not None and not recurrencia.empty:
            lineas.append("RECURRENCIA (DETALLE IDÉNTICO)")
            lineas.append("-" * 40)
            lineas.append(recurrencia.to_string(index=False))
        self._texto_reporte.delete("1.0", tk.END)
        self._texto_reporte.insert("1.0", "\n".join(lineas))

    def _abrir_reporte(self) -> None:
        if not self._reporte:
            messagebox.showinfo("Sin reporte", "Genere primero un reporte.")
            return
        try:
            _abrir(self._reporte["xlsx"])
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("No se pudo abrir", str(exc))

    # ------------------------------------------------------------------
    # Auditoría
    # ------------------------------------------------------------------
    def _construir_auditoria(self) -> None:
        ttk.Label(
            self._tab_auditoria,
            text="Historial de ejecuciones y cambios",
            style="Seccion.TLabel",
        ).pack(anchor="w")
        barra = ttk.Frame(self._tab_auditoria)
        barra.pack(fill="x", pady=(8, 6))
        ttk.Button(
            barra, text="Actualizar", command=self._refrescar_auditoria
        ).pack(side="left")
        ttk.Button(
            barra,
            text="Abrir auditoría CSV",
            command=self._abrir_auditoria,
        ).pack(side="left", padx=8)
        ttk.Button(
            barra,
            text="Abrir carpeta de datos",
            command=lambda: _abrir(cfg.DATA_DIR),
        ).pack(side="left", padx=8)

        self._tabla_auditoria = self._crear_tabla(
            self._tab_auditoria,
            columnas=[
                "FECHA_HORA",
                "TIPO",
                "PERIODO",
                "LEIDOS",
                "VALIDOS",
                "OBSERVADOS",
                "NUEVOS",
                "MODIFICADOS",
                "ESTADO",
                "ARCHIVO_ORIGINAL",
            ],
            anchos={
                "FECHA_HORA": 150,
                "TIPO": 120,
                "PERIODO": 170,
                "LEIDOS": 60,
                "VALIDOS": 60,
                "OBSERVADOS": 75,
                "NUEVOS": 60,
                "MODIFICADOS": 80,
                "ESTADO": 90,
                "ARCHIVO_ORIGINAL": 360,
            },
            alto=18,
        )

    def _refrescar_auditoria(self) -> None:
        if not hasattr(self, "_tabla_auditoria"):
            return
        self._limpiar_tabla(self._tabla_auditoria)
        try:
            historial = auditoria.cargar_auditoria()
        except Exception as exc:  # noqa: BLE001
            LOGGER.error("No se pudo leer la auditoría: %s", exc)
            return
        if historial.empty:
            return
        for _, fila in historial.tail(300).iloc[::-1].iterrows():
            self._tabla_auditoria.insert(
                "",
                "end",
                values=tuple(_texto(fila.get(columna)) for columna in (
                    "FECHA_HORA",
                    "TIPO",
                    "PERIODO",
                    "LEIDOS",
                    "VALIDOS",
                    "OBSERVADOS",
                    "NUEVOS",
                    "MODIFICADOS",
                    "ESTADO",
                    "ARCHIVO_ORIGINAL",
                )),
            )

    def _abrir_auditoria(self) -> None:
        if not Path(cfg.AUDITORIA_CSV).exists():
            messagebox.showinfo("Sin auditoría", "Todavía no hay ejecuciones registradas.")
            return
        try:
            _abrir(cfg.AUDITORIA_CSV)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("No se pudo abrir", str(exc))

    # ------------------------------------------------------------------
    # Utilidades de interfaz e hilos
    # ------------------------------------------------------------------
    def _crear_tabla(
        self,
        padre: tk.Widget,
        columnas: list[str],
        anchos: dict[str, int] | None = None,
        alto: int = 10,
    ) -> ttk.Treeview:
        marco = ttk.Frame(padre)
        marco.pack(fill="both", expand=True)
        tabla = ttk.Treeview(
            marco, columns=columnas, show="headings", height=alto
        )
        for columna in columnas:
            tabla.heading(columna, text=columna)
            tabla.column(
                columna,
                width=(anchos or {}).get(columna, 120),
                anchor="w",
                stretch=True,
            )
        vertical = ttk.Scrollbar(
            marco, orient="vertical", command=tabla.yview
        )
        horizontal = ttk.Scrollbar(
            marco, orient="horizontal", command=tabla.xview
        )
        tabla.configure(
            yscrollcommand=vertical.set, xscrollcommand=horizontal.set
        )
        tabla.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        marco.rowconfigure(0, weight=1)
        marco.columnconfigure(0, weight=1)
        return tabla

    @staticmethod
    def _limpiar_tabla(tabla: ttk.Treeview) -> None:
        for elemento in tabla.get_children():
            tabla.delete(elemento)

    def _iniciar_ocupado(self, mensaje: str) -> None:
        self._ocupado = True
        texto = f"Trabajando: {mensaje}"
        if len(texto) > 48:
            texto = texto[:45].rstrip() + "…"
        self._pintar_estado_app(texto, "warning")
        self.configure(cursor="watch")
        panel = getattr(self, "_panel_principal", None)
        if panel is not None:
            panel.set_estado_sistema("EN PROCESO", "warning")

    def _terminar_ocupado(self) -> None:
        self._ocupado = False
        self._pintar_estado_app("Listo", "success")
        self.configure(cursor="")
        panel = getattr(self, "_panel_principal", None)
        if panel is not None:
            estado = getattr(self, "_estado", None)
            if estado is not None and estado.registros_error:
                panel.set_estado_sistema("REVISAR EJECUCIÓN", "danger")
            else:
                panel.set_estado_sistema("SISTEMA OPERATIVO", "success")
        try:
            self._barra.stop()
            self._barra.configure(mode="determinate", value=0)
        except Exception:  # noqa: BLE001
            pass

    def _ejecutar_hilo(
        self,
        tarea: Callable[[], Any],
        al_terminar: Callable[[Any], None],
        descripcion: str,
    ) -> None:
        def envoltura() -> None:
            try:
                resultado = tarea()
            except Exception as exc:  # noqa: BLE001
                LOGGER.error(
                    "Error en %s: %s\n%s", descripcion, exc, traceback.format_exc()
                )
                self._cola.put(("error", (descripcion, exc)))
            else:
                self._cola.put(("ok", (al_terminar, resultado)))

        threading.Thread(target=envoltura, daemon=True).start()

    def _procesar_cola(self) -> None:
        try:
            while True:
                tipo, contenido = self._cola.get_nowait()
                if tipo == "ok":
                    callback, resultado = contenido
                    try:
                        callback(resultado)
                    except Exception as exc:  # noqa: BLE001
                        LOGGER.exception("Error al actualizar la interfaz")
                        self._terminar_ocupado()
                        panel = getattr(self, "_panel_principal", None)
                        if panel is not None:
                            panel.set_estado_sistema("REVISAR EJECUCIÓN", "danger")
                        messagebox.showerror("Error al actualizar", str(exc))
                elif tipo == "error":
                    descripcion, exc = contenido
                    self._terminar_ocupado()
                    panel = getattr(self, "_panel_principal", None)
                    if panel is not None:
                        panel.set_estado_sistema("REVISAR EJECUCIÓN", "danger")
                    if isinstance(exc, (ErrorArchivo, ErrorEstructura, ErrorBloqueo)):
                        messagebox.showerror(
                            f"No se pudo completar: {descripcion}", str(exc)
                        )
                    else:
                        messagebox.showerror(
                            "Error inesperado",
                            f"{descripcion}: {exc}",
                        )
        except queue.Empty:
            pass
        finally:
            try:
                if self.winfo_exists():
                    self._cola_after_id = self.after(120, self._procesar_cola)
            except tk.TclError:
                pass

    def destruir(self) -> None:
        if self._cola_after_id is not None:
            try:
                self.after_cancel(self._cola_after_id)
            except tk.TclError:
                pass
            self._cola_after_id = None
        try:
            self._barra.stop()
        except Exception:  # noqa: BLE001
            pass
        self.destroy()


def main(argv: list[str] | None = None) -> int:
    """Abre la interfaz gráfica."""

    configurar_logging()
    cfg.asegurar_directorios()
    try:
        app = AplicacionTk()
    except tk.TclError as exc:
        print(
            "No se pudo iniciar la interfaz gráfica (entorno sin pantalla): "
            f"{exc}",
            file=sys.stderr,
        )
        return 1
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
