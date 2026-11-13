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
from config.sqlserver import (
    DRIVERS_CONOCIDOS,
    EMPRESAS,
    ErrorConfiguracionSQL,
    cargar_configuracion,
    guardar_configuracion,
    registrar_prueba,
    validar_configuracion,
)
from core import asignaciones as svc_asignaciones
from core import auditoria
from core import catalogo_areas
from core import diagnostico as svc_diagnostico
from core import estado as svc_estado
from core import eventos as svc_eventos
from core import procedencia
from core import revision as svc_revision
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
from database.connection import controlador_disponible, probar_conexion
from database.synchronizer import (
    Sincronizador,
    cargar_empleados,
    resumen_sincronizacion,
)
from panel_principal import PanelPrincipal, SEMANTICA
import ui_tema
from ui_tema import (
    AZUL_MARINO,
    AZUL_OSCURO as COLOR_CABECERA,
    AZUL_PRINCIPAL as COLOR_ACENTO,
    AZUL_SUAVE,
    BLANCO,
    BORDE as COLOR_BORDE,
    BotonNavegacion,
    EncabezadoDegradado,
    FONDO as COLOR_FONDO,
    NARANJA as COLOR_ALERTA,
    Pill,
    ROJO as COLOR_ERROR,
    SUPERFICIE,
    TEXTO as COLOR_TEXTO,
    TEXTO_SECUNDARIO as COLOR_TEXTO_SECUNDARIO,
    VERDE as COLOR_OK,
)

LOGGER = logging.getLogger("automatizador")

TITULO = "Sistema Integral de Tickets TI - INKACROPS"
COLOR_TABLA_CABECERA = "#E8EEF8"

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
    """Configuración de rutas locales y de la conexión corporativa SQL Server."""

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

    def __init__(self, padre: tk.Misc, seccion: str = "rutas") -> None:
        super().__init__(padre)
        self.title("Configuración")
        self.geometry("900x680")
        self.minsize(760, 560)
        self.configure(bg=COLOR_FONDO)
        self.transient(padre)
        self.grab_set()

        self._config = cfg.cargar_configuracion_local()
        self._variables: dict[str, tk.StringVar] = {}
        self._sql = cargar_configuracion()
        self._sql_vars: dict[str, tk.StringVar] = {}
        self._sql_bools: dict[str, tk.BooleanVar] = {}
        self._entries_sql: dict[str, ttk.Entry] = {}
        self._sql_ocupado = False
        self._pestanas: ttk.Notebook | None = None

        cabecera = tk.Frame(self, bg=AZUL_MARINO)
        cabecera.pack(fill="x")
        tk.Label(
            cabecera,
            text="Configuración del sistema",
            bg=AZUL_MARINO,
            fg=BLANCO,
            font=(ui_tema.FUENTE, 15, "bold"),
            padx=18,
            pady=12,
            anchor="w",
        ).pack(side="left")

        self._pestanas = ttk.Notebook(self)
        self._pestanas.pack(fill="both", expand=True, padx=14, pady=12)
        self._tab_rutas = ttk.Frame(self._pestanas, padding=14)
        self._tab_sql = ttk.Frame(self._pestanas, padding=10)
        self._pestanas.add(self._tab_rutas, text="Rutas y carpetas")
        self._pestanas.add(self._tab_sql, text="Conexión SQL Server")

        self._construir_rutas()
        self._construir_sql()
        self._cargar_formulario_sql()

        acciones = ttk.Frame(self)
        acciones.pack(fill="x", padx=14, pady=(0, 12))
        ttk.Button(
            acciones, text="Cerrar", command=self.destroy
        ).pack(side="right")
        ttk.Button(
            acciones,
            text="Guardar configuración",
            style="Principal.TButton",
            command=self._guardar,
        ).pack(side="right", padx=(0, 8))

        if str(seccion).lower() == "sql":
            self._pestanas.select(self._tab_sql)

    # ------------------------------------------------------------------
    # Pestaña de rutas
    # ------------------------------------------------------------------
    def _construir_rutas(self) -> None:
        marco = ttk.Frame(self._tab_rutas)
        marco.pack(fill="both", expand=True)

        for titulo, nombre, tipo in self._RUTAS:
            fila = ttk.Frame(marco)
            fila.pack(fill="x", pady=4)
            ttk.Label(fila, text=titulo, width=24, anchor="w").pack(side="left")
            variable = tk.StringVar(value=str(self._config.get(nombre, "")))
            self._variables[nombre] = variable
            ttk.Entry(fila, textvariable=variable).pack(
                side="left", fill="x", expand=True, padx=(8, 8)
            )
            ttk.Button(
                fila,
                text="Examinar…",
                command=lambda n=nombre, t=tipo: self._seleccionar_ruta(n, t),
            ).pack(side="left")

        ttk.Label(
            marco,
            text=(
                "Las rutas relativas se resuelven desde la carpeta del proyecto. "
                "Los archivos originales nunca se modifican."
            ),
            style="Subtitulo.TLabel",
            wraplength=780,
        ).pack(anchor="w", pady=(12, 0))

    def _seleccionar_ruta(self, nombre: str, tipo: str) -> None:
        valor = self._variables[nombre].get().strip()
        directorio_base = valor if valor else str(cfg.BASE_DIR)
        if tipo == "archivo":
            ruta = filedialog.askopenfilename(
                title="Seleccione el archivo",
                initialdir=str(
                    Path(directorio_base).resolve()
                    if Path(directorio_base).exists()
                    else cfg.BASE_DIR
                ),
            )
        else:
            ruta = filedialog.askdirectory(
                title="Seleccione la carpeta",
                initialdir=str(
                    Path(directorio_base).resolve()
                    if Path(directorio_base).exists()
                    else cfg.BASE_DIR
                ),
            )
        if ruta:
            self._variables[nombre].set(ruta)

    # ------------------------------------------------------------------
    # Pestaña SQL Server
    # ------------------------------------------------------------------
    def _campo(
        self,
        padre: tk.Misc,
        etiqueta: str,
        nombre: str,
        *,
        ancho: int = 34,
        mostrar: str = "",
    ) -> ttk.Entry:
        fila = ttk.Frame(padre)
        fila.pack(fill="x", pady=3)
        ttk.Label(fila, text=etiqueta, width=26, anchor="w").pack(side="left")
        variable = tk.StringVar()
        self._sql_vars[nombre] = variable
        entrada = ttk.Entry(
            fila, textvariable=variable, width=ancho, show=mostrar
        )
        entrada.pack(side="left", fill="x", expand=True, padx=(8, 0))
        self._entries_sql[nombre] = entrada
        return entrada

    def _construir_sql(self) -> None:
        contenedor = ttk.Frame(self._tab_sql)
        contenedor.pack(fill="both", expand=True)

        lienzo = tk.Canvas(
            contenedor, bg=COLOR_FONDO, highlightthickness=0, bd=0
        )
        barra = ttk.Scrollbar(contenedor, orient="vertical", command=lienzo.yview)
        marco = ttk.Frame(lienzo)
        lienzo.create_window((0, 0), window=marco, anchor="nw")
        lienzo.configure(yscrollcommand=barra.set)
        lienzo.pack(side="left", fill="both", expand=True)
        barra.pack(side="right", fill="y")
        marco.bind(
            "<Configure>",
            lambda _e: lienzo.configure(scrollregion=lienzo.bbox("all")),
        )

        ttk.Label(
            marco,
            text="Conexión a base de datos corporativa",
            style="Seccion.TLabel",
        ).pack(anchor="w", pady=(0, 4))
        ttk.Label(
            marco,
            text=(
                "La contraseña se cifra con la cuenta de Windows (DPAPI). "
                "La conexión es de solo lectura: el sistema nunca ejecuta "
                "INSERT, UPDATE ni DELETE sobre EXACTUS."
            ),
            style="Subtitulo.TLabel",
            wraplength=760,
        ).pack(anchor="w", pady=(0, 8))

        self._campo(marco, "Servidor / IP", "servidor")
        self._campo(marco, "Puerto", "puerto", ancho=12)
        self._campo(marco, "Base de datos", "base_datos")

        fila_auth = ttk.Frame(marco)
        fila_auth.pack(fill="x", pady=3)
        ttk.Label(
            fila_auth, text="Tipo de autenticación", width=26, anchor="w"
        ).pack(side="left")
        self._sql_vars["autenticacion"] = tk.StringVar()
        self._combo_auth = ttk.Combobox(
            fila_auth,
            textvariable=self._sql_vars["autenticacion"],
            values=("Windows (cuenta actual)", "SQL Server (usuario y contraseña)"),
            state="readonly",
        )
        self._combo_auth.pack(side="left", fill="x", expand=True, padx=(8, 0))
        self._combo_auth.bind(
            "<<ComboboxSelected>>", lambda _e: self._actualizar_auth()
        )

        self._campo(marco, "Usuario SQL", "usuario")
        self._campo(marco, "Contraseña", "contrasena", mostrar="•")

        fila_driver = ttk.Frame(marco)
        fila_driver.pack(fill="x", pady=3)
        ttk.Label(
            fila_driver, text="Controlador ODBC", width=26, anchor="w"
        ).pack(side="left")
        self._sql_vars["driver"] = tk.StringVar()
        self._combo_driver = ttk.Combobox(
            fila_driver,
            textvariable=self._sql_vars["driver"],
            values=DRIVERS_CONOCIDOS,
        )
        self._combo_driver.pack(
            side="left", fill="x", expand=True, padx=(8, 0)
        )

        self._campo(marco, "Timeout (segundos)", "timeout", ancho=10)
        self._campo(marco, "Tamaño de lote", "lote", ancho=10)

        fila_flags = ttk.Frame(marco)
        fila_flags.pack(fill="x", pady=(6, 2))
        for nombre, etiqueta in (
            ("encriptar", "Cifrar conexión"),
            ("confiar_certificado", "Confiar en el certificado"),
            ("auto_sync", "Resolver automáticamente al procesar"),
        ):
            variable = tk.BooleanVar(value=True)
            self._sql_bools[nombre] = variable
            ttk.Checkbutton(fila_flags, text=etiqueta, variable=variable).pack(
                side="left", padx=(0, 16)
            )

        ttk.Label(
            marco, text="Empresas autorizadas (18)", style="Seccion.TLabel"
        ).pack(anchor="w", pady=(10, 2))
        caja = ttk.Frame(marco)
        caja.pack(fill="x", pady=(0, 4))
        self._lista_empresas = tk.Listbox(
            caja,
            selectmode="multiple",
            height=6,
            exportselection=False,
            font=(ui_tema.FUENTE, 9),
            bg=SUPERFICIE,
            fg=COLOR_TEXTO,
            selectbackground=COLOR_ACENTO,
            selectforeground=BLANCO,
            highlightthickness=1,
            highlightbackground=COLOR_BORDE,
            bd=0,
        )
        self._lista_empresas.pack(side="left", fill="x", expand=True)
        barra_empresas = ttk.Scrollbar(
            caja, orient="vertical", command=self._lista_empresas.yview
        )
        barra_empresas.pack(side="right", fill="y")
        self._lista_empresas.configure(yscrollcommand=barra_empresas.set)
        for empresa in EMPRESAS:
            self._lista_empresas.insert("end", empresa)

        acciones = ttk.Frame(marco)
        acciones.pack(fill="x", pady=4)
        ttk.Button(
            acciones, text="Seleccionar todas", command=self._seleccionar_todas
        ).pack(side="left")
        ttk.Button(
            acciones, text="Quitar selección", command=self._quitar_seleccion
        ).pack(side="left", padx=6)

        estado = ttk.LabelFrame(marco, text="Estado de la conexión", padding=8)
        estado.pack(fill="x", pady=(10, 6))
        self._pill_sql = Pill(estado, "Desconectado", estilo="neutral", punto=True)
        self._pill_sql.pack(anchor="w", pady=(0, 6))
        self._var_estado_sql = tk.StringVar(value="Sin prueba de conexión.")
        ttk.Label(
            estado, textvariable=self._var_estado_sql, wraplength=740
        ).pack(anchor="w")
        self._var_ultima_prueba = tk.StringVar(value="")
        ttk.Label(
            estado,
            textvariable=self._var_ultima_prueba,
            style="Subtitulo.TLabel",
        ).pack(anchor="w", pady=(4, 0))

        info = ttk.LabelFrame(marco, text="Información de la caché local", padding=8)
        info.pack(fill="x", pady=(0, 6))
        self._var_info_sync = tk.StringVar(value="")
        ttk.Label(
            info, textvariable=self._var_info_sync, justify="left", wraplength=740
        ).pack(anchor="w")

        botones = ttk.Frame(marco)
        botones.pack(fill="x", pady=(8, 4))
        self._btn_probar = ttk.Button(
            botones, text="Probar conexión", command=self._probar_conexion
        )
        self._btn_probar.pack(side="left")
        self._btn_guardar_sql = ttk.Button(
            botones,
            text="Guardar conexión",
            style="Principal.TButton",
            command=self._guardar_sql,
        )
        self._btn_guardar_sql.pack(side="left", padx=6)
        self._btn_desconectar = ttk.Button(
            botones, text="Desconectar", command=self._desconectar
        )
        self._btn_desconectar.pack(side="left", padx=6)
        self._btn_sincronizar = ttk.Button(
            botones,
            text="Sincronizar ahora",
            style="Verde.TButton",
            command=self._sincronizar_ahora,
        )
        self._btn_sincronizar.pack(side="left", padx=6)

        if not controlador_disponible():
            ttk.Label(
                marco,
                text=(
                    "Aviso: el módulo pyodbc no está instalado. La aplicación "
                    "seguirá funcionando con la caché local; instale "
                    "requirements.txt para conectar con SQL Server."
                ),
                foreground=COLOR_ALERTA,
                wraplength=740,
            ).pack(anchor="w", pady=(6, 0))

    def _actualizar_auth(self) -> None:
        es_sql = self._sql_vars["autenticacion"].get().startswith("SQL")
        estado = "normal" if es_sql else "disabled"
        for nombre in ("usuario", "contrasena"):
            entrada = self._entries_sql.get(nombre)
            if entrada is not None:
                entrada.configure(state=estado)

    def _seleccionar_todas(self) -> None:
        self._lista_empresas.selection_set(0, "end")

    def _quitar_seleccion(self) -> None:
        self._lista_empresas.selection_clear(0, "end")

    def _cargar_formulario_sql(self) -> None:
        config = self._sql
        valores = {
            "servidor": config.servidor,
            "puerto": str(config.puerto),
            "base_datos": config.base_datos,
            "usuario": config.usuario,
            "contrasena": config.contrasena,
            "driver": config.driver,
            "timeout": str(config.timeout),
            "lote": str(config.lote),
        }
        for nombre, valor in valores.items():
            self._sql_vars[nombre].set(valor)
        self._sql_vars["autenticacion"].set(
            "SQL Server (usuario y contraseña)"
            if config.usa_sql_auth
            else "Windows (cuenta actual)"
        )
        self._sql_bools["encriptar"].set(bool(config.encriptar))
        self._sql_bools["confiar_certificado"].set(bool(config.confiar_certificado))
        self._sql_bools["auto_sync"].set(bool(config.auto_sync))
        seleccionadas = set(config.empresas_activas())
        for indice, empresa in enumerate(EMPRESAS):
            if empresa in seleccionadas:
                self._lista_empresas.selection_set(indice)
        self._actualizar_estado_visual(config.ultimo_estado, config.ultimo_error)
        self._var_ultima_prueba.set(
            f"Última prueba: {config.ultima_prueba}"
            if config.ultima_prueba
            else "Sin prueba de conexión registrada."
        )
        self._actualizar_info_cache()

    def _actualizar_estado_visual(self, estado: str, mensaje: str = "") -> None:
        estado = str(estado or "desconectado").lower()
        if estado == "conectado":
            self._pill_sql.actualizar("Conectado", "success")
        elif estado == "error":
            texto = "Error de conexión"
            if "autentic" in mensaje.lower():
                texto = "Error de autenticación"
            elif "tiempo" in mensaje.lower() or "timeout" in mensaje.lower():
                texto = "Tiempo de espera agotado"
            elif "servidor" in mensaje.lower():
                texto = "Servidor no disponible"
            self._pill_sql.actualizar(texto, "danger")
        else:
            self._pill_sql.actualizar("Desconectado", "neutral")
        self._var_estado_sql.set(mensaje or "Sin prueba de conexión.")

    def _actualizar_info_cache(self) -> None:
        resumen = resumen_sincronizacion()
        self._var_info_sync.set(
            f"Empresas configuradas: {len(self._sql.empresas_activas())}\n"
            f"Última sincronización: {resumen.get('fecha') or 'nunca'}\n"
            f"Empleados almacenados localmente: {resumen.get('total_empleados', 0)}\n"
            f"Errores de sincronización: {resumen.get('errores', 0)}\n"
            f"Alertas por revisar: {resumen.get('alertas', 0)}"
        )

    def _colectar_sql(self):
        from config.sqlserver import ConfiguracionSQL

        try:
            puerto = int(self._sql_vars["puerto"].get().strip() or 1433)
            timeout = int(self._sql_vars["timeout"].get().strip() or 10)
            lote = int(self._sql_vars["lote"].get().strip() or 200)
        except ValueError as exc:
            raise ErrorConfiguracionSQL(
                "Puerto, timeout y lote deben ser números enteros."
            ) from exc

        seleccion = [
            EMPRESAS[indice]
            for indice in self._lista_empresas.curselection()
        ]
        config = ConfiguracionSQL(
            habilitado=bool(self._sql_vars["servidor"].get().strip()),
            servidor=self._sql_vars["servidor"].get().strip(),
            puerto=puerto,
            base_datos=self._sql_vars["base_datos"].get().strip(),
            autenticacion=(
                "sql"
                if self._sql_vars["autenticacion"].get().startswith("SQL")
                else "windows"
            ),
            usuario=self._sql_vars["usuario"].get().strip(),
            contrasena=self._sql_vars["contrasena"].get(),
            driver=self._sql_vars["driver"].get().strip(),
            timeout=timeout,
            encriptar=bool(self._sql_bools["encriptar"].get()),
            confiar_certificado=bool(self._sql_bools["confiar_certificado"].get()),
            auto_sync=bool(self._sql_bools["auto_sync"].get()),
            lote=lote,
            empresas=seleccion or list(EMPRESAS),
            ultima_prueba=self._sql.ultima_prueba,
            ultimo_estado=self._sql.ultimo_estado,
            ultimo_error=self._sql.ultimo_error,
            ultima_sincronizacion=self._sql.ultima_sincronizacion,
        )
        return validar_configuracion(config)

    def _guardar_sql(self) -> None:
        try:
            config = self._colectar_sql()
            guardar_configuracion(config)
        except ErrorConfiguracionSQL as exc:
            messagebox.showerror("Conexión SQL inválida", str(exc))
            return
        except OSError as exc:
            messagebox.showerror("No se pudo guardar", str(exc))
            return
        self._sql = config
        messagebox.showinfo(
            "Conexión guardada",
            "La configuración se guardó. Use 'Probar conexión' para verificar "
            "el acceso real antes de sincronizar.",
        )

    def _probar_conexion(self) -> None:
        if self._sql_ocupado:
            return
        try:
            config = self._colectar_sql()
        except ErrorConfiguracionSQL as exc:
            messagebox.showerror("Conexión SQL inválida", str(exc))
            return

        self._sql_ocupado = True
        self._btn_probar.configure(state="disabled")
        self._pill_sql.actualizar("Probando…", "info")
        self._var_estado_sql.set("Comprobando el servidor y las credenciales…")

        def tarea():
            return probar_conexion(config)

        def terminar(resultado):
            self._sql_ocupado = False
            self._btn_probar.configure(state="normal")
            if isinstance(resultado, Exception):
                self._actualizar_estado_visual("error", str(resultado))
                return
            conectado = bool(resultado.get("ok"))
            actualizada = registrar_prueba(
                config,
                conectado=conectado,
                mensaje=str(resultado.get("mensaje") or ""),
            )
            try:
                guardar_configuracion(actualizada)
                self._sql = actualizada
            except OSError as exc:
                LOGGER.warning("No se pudo guardar el estado de la prueba: %s", exc)
            self._actualizar_estado_visual(
                "conectado" if conectado else "error",
                str(resultado.get("mensaje") or ""),
            )
            self._var_ultima_prueba.set(
                f"Última prueba: {actualizada.ultima_prueba}"
            )

        self._ejecutar_tarea(tarea, terminar)

    def _desconectar(self) -> None:
        config = self._sql.clonar(
            habilitado=False,
            ultimo_estado="desconectado",
            ultimo_error="",
        )
        try:
            guardar_configuracion(config)
            self._sql = config
        except (OSError, ErrorConfiguracionSQL) as exc:
            messagebox.showerror("No se pudo desconectar", str(exc))
            return
        self._actualizar_estado_visual(
            "desconectado",
            "Integración deshabilitada. La caché local sigue disponible.",
        )
        self._var_ultima_prueba.set("La configuración guardada se conserva.")

    def _sincronizar_ahora(self) -> None:
        if self._sql_ocupado:
            return
        try:
            config = self._colectar_sql()
        except ErrorConfiguracionSQL as exc:
            messagebox.showerror("Conexión SQL inválida", str(exc))
            return
        if not config.configurada:
            messagebox.showwarning(
                "Sin configuración",
                "Indique servidor y base de datos antes de sincronizar.",
            )
            return
        if not controlador_disponible():
            messagebox.showerror(
                "pyodbc no disponible",
                "Instale pyodbc y un controlador ODBC de SQL Server para "
                "sincronizar. La aplicación puede seguir usando la caché local.",
            )
            return

        self._sql_ocupado = True
        self._btn_sincronizar.configure(state="disabled")
        self._pill_sql.actualizar("Sincronizando…", "info")
        self._var_estado_sql.set(
            "Consultando las empresas configuradas. Esta operación puede tardar."
        )

        def tarea():
            return Sincronizador(config).sincronizar_completo()

        def terminar(resultado):
            self._sql_ocupado = False
            self._btn_sincronizar.configure(state="normal")
            if isinstance(resultado, Exception):
                self._actualizar_estado_visual("error", str(resultado))
                messagebox.showerror("Sincronización fallida", str(resultado))
                return
            errores = resultado.cantidad_errores
            conectado = errores == 0 and resultado.total_empleados > 0
            mensaje_estado = (
                f"Sincronización completada: {resultado.total_empleados} empleados."
                if conectado
                else (
                    "Sincronización incompleta: "
                    f"{errores} empresa(s) con error; se conservaron "
                    f"{resultado.total_empleados} empleados en caché."
                )
            )
            actualizada = registrar_prueba(
                config,
                conectado=conectado,
                mensaje=mensaje_estado,
            ).clonar(ultima_sincronizacion=resultado.fecha)
            try:
                guardar_configuracion(actualizada)
                self._sql = actualizada
            except OSError as exc:
                LOGGER.warning("No se pudo guardar la sincronización: %s", exc)
            self._actualizar_estado_visual(
                "conectado" if conectado else "error",
                mensaje_estado,
            )
            self._actualizar_info_cache()
            detalle_errores = "\n".join(
                f"  - {item.get('empresa', '?')} ({item.get('categoria', 'error')}): "
                f"{item.get('mensaje', '')}"
                for item in resultado.errores[:5]
            )
            texto = (
                f"Nuevos: {resultado.nuevos}\n"
                f"Actualizados: {resultado.actualizados}\n"
                f"Departamentos modificados: {resultado.departamentos_modificados}\n"
                f"Empleados sin departamento: {resultado.sin_departamento}\n"
                f"Registros ambiguos: {resultado.ambiguos}\n"
                f"Empresas sin datos: {len(resultado.empresas_sin_datos)}\n"
                f"Empresas sin esquema: {len(resultado.empresas_sin_esquema)}\n"
                f"Errores: {errores}\n"
                f"Alertas por conflicto con el maestro: {len(resultado.alertas)}\n"
                f"Total almacenado: {resultado.total_empleados}\n"
                f"Fecha: {resultado.fecha}"
            )
            if resultado.mensaje_cache:
                texto += f"\n{resultado.mensaje_cache}"
            if detalle_errores:
                texto += f"\n\nErrores:\n{detalle_errores}"
            if errores:
                messagebox.showwarning("Sincronización incompleta", texto)
            else:
                messagebox.showinfo("Sincronización completada", texto)

        self._ejecutar_tarea(tarea, terminar)

    def _ejecutar_tarea(self, tarea, al_terminar) -> None:
        def ejecutar():
            try:
                resultado = tarea()
            except Exception as exc:  # noqa: BLE001 - se informa en la interfaz
                resultado = exc
            try:
                self.after(0, lambda: self._finalizar_tarea(al_terminar, resultado))
            except tk.TclError:
                pass

        threading.Thread(target=ejecutar, daemon=True).start()

    def _finalizar_tarea(self, al_terminar, resultado) -> None:
        try:
            if self.winfo_exists():
                al_terminar(resultado)
        except tk.TclError:
            pass

    # ------------------------------------------------------------------
    # Guardado general
    # ------------------------------------------------------------------
    def _colectar(self) -> dict[str, str | int]:
        datos: dict[str, str | int] = {}
        for nombre, variable in self._variables.items():
            datos[nombre] = variable.get().strip()
        datos["max_backups"] = self._config.get("max_backups", 10)
        datos["log_level"] = self._config.get("log_level", "INFO")
        datos["formato_fecha_reporte"] = self._config.get(
            "formato_fecha_reporte", "DD/MM/YYYY"
        )
        return datos

    def _guardar(self) -> None:
        try:
            validada = cfg.guardar_configuracion_local(self._colectar())
            cfg.aplicar_configuracion(validada)
            if self._sql_vars:
                config = self._colectar_sql()
                guardar_configuracion(config)
                self._sql = config
        except (ValueError, ErrorConfiguracionSQL) as exc:
            messagebox.showerror("Configuración inválida", str(exc))
            return
        except OSError as exc:
            messagebox.showerror("No se pudo guardar", str(exc))
            return
        messagebox.showinfo(
            "Configuración guardada",
            "Los cambios se aplicarán en la próxima operación. La aplicación "
            "puede requerir reinicio si se usaron rutas nuevas.",
        )
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
        self.geometry("1440x900")
        self.minsize(1100, 700)
        self.configure(bg=COLOR_FONDO)

        self._estado = svc_estado.cargar_estado()
        self._maestro = svc_asignaciones.cargar_maestro()
        self._preview_df: pd.DataFrame | None = None
        self._preview_info: dict[str, Any] | None = None
        self._ruta_preview: str | None = None
        self._solicitantes_por_iid: dict[str, dict[str, Any]] = {}
        self._ultimo_procesado: str = self._estado.ultimo_archivo_procesado
        self._ultimo_manifiesto: str = self._estado.ultimo_manifiesto
        self._ruta_entrada_actual: str = ""
        self._hoja_entrada_actual: str | None = None
        self._ruta_revision_actual: str = ""
        self._revision_actual: Any = None
        self._ventana_validacion: Any = None
        self._ultima_copia_revision: str = ""
        self._estadisticas_actuales: Any = None
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
        self._suscripciones_eventos = [
            svc_eventos.suscribir(
                svc_eventos.ASIGNACION_REALIZADA, self._al_evento_datos
            ),
            svc_eventos.suscribir(
                svc_eventos.USUARIO_ACTUALIZADO, self._al_evento_datos
            ),
            svc_eventos.suscribir(svc_eventos.AREA_CREADA, self._al_evento_datos),
            svc_eventos.suscribir(
                svc_eventos.CATALOGO_AREAS_ACTUALIZADO, self._al_evento_datos
            ),
            svc_eventos.suscribir(
                svc_eventos.MAESTRO_ACTUALIZADO, self._al_evento_datos
            ),
            svc_eventos.suscribir(
                svc_eventos.PROCESAMIENTO_FINALIZADO, self._al_evento_datos
            ),
            svc_eventos.suscribir(
                svc_eventos.CONSULTA_SQL_FINALIZADA, self._al_evento_datos
            ),
        ]
        self._cola_after_id = self.after(120, self._procesar_cola)

    # ------------------------------------------------------------------
    # Actualización inmediata por eventos
    # ------------------------------------------------------------------
    def _al_evento_datos(self, evento: dict[str, Any]) -> None:
        """Programa el refresco en el hilo de Tkinter sin bloquear el evento."""

        try:
            self.after(0, self._aplicar_evento, dict(evento))
        except tk.TclError:  # la ventana ya se cerró
            pass

    def _aplicar_evento(self, evento: dict[str, Any]) -> None:
        tema = str(evento.get("tema") or "")
        try:
            if tema in {
                svc_eventos.ASIGNACION_REALIZADA,
                svc_eventos.USUARIO_ACTUALIZADO,
            }:
                self._refrescar_usuarios()
                self._refrescar_pendientes()
                self._refrescar_panel()
            elif tema == svc_eventos.AREA_CREADA:
                self._refrescar_usuarios()
                self._refrescar_maestro()
                self._refrescar_pendientes()
            elif tema == svc_eventos.CATALOGO_AREAS_ACTUALIZADO:
                self._refrescar_maestro()
            elif tema == svc_eventos.MAESTRO_ACTUALIZADO:
                self._refrescar_maestro()
                self._refrescar_usuarios()
            elif tema == svc_eventos.PROCESAMIENTO_FINALIZADO:
                self._refrescar_panel()
            elif tema == svc_eventos.CONSULTA_SQL_FINALIZADA:
                # La consulta SQL actualiza el caché de empleados y los
                # pendientes; no obliga a reprocesar el archivo.
                self._refrescar_pendientes()
                self._refrescar_panel()
        except Exception as exc:  # noqa: BLE001 - un refresco no rompe la app
            LOGGER.error("No se pudo refrescar la interfaz para '%s': %s", tema, exc)

    def destroy(self) -> None:
        for cancelar in getattr(self, "_suscripciones_eventos", []):
            try:
                cancelar()
            except Exception:  # noqa: BLE001
                pass
        self._suscripciones_eventos = []
        super().destroy()

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
        # La identidad corporativa compartida se aplica al final para que
        # prevalezca sobre los estilos heredados del diseño anterior.
        ui_tema.aplicar_estilos(self)

    def _crear_cabecera(self) -> None:
        self._encabezado = EncabezadoDegradado(self, alto=70)
        self._encabezado.pack(side="top", fill="x")
        cabecera = self._encabezado.contenido
        cabecera.configure(bg=AZUL_MARINO)

        logo = tk.Canvas(
            cabecera,
            width=40,
            height=40,
            bg=AZUL_MARINO,
            highlightthickness=0,
            bd=0,
        )
        logo.create_oval(2, 2, 38, 38, fill="#2C6FE0", outline="#7FB2FF", width=2)
        logo.create_text(
            20, 20, text="IK", fill=BLANCO, font=(ui_tema.FUENTE, 12, "bold")
        )
        logo.pack(side="left", padx=(20, 10), pady=15)

        bloque = tk.Frame(cabecera, bg=AZUL_MARINO)
        bloque.pack(side="left", pady=12)
        tk.Label(
            bloque,
            text="INKACROPS",
            bg=AZUL_MARINO,
            fg=BLANCO,
            font=(ui_tema.FUENTE, 14, "bold"),
            anchor="w",
        ).pack(anchor="w")
        tk.Label(
            bloque,
            text="Sistema Integral de Tickets TI",
            bg=AZUL_MARINO,
            fg="#BFD3F5",
            font=(ui_tema.FUENTE, 9),
            anchor="w",
        ).pack(anchor="w")

        acciones = tk.Frame(cabecera, bg=AZUL_MARINO)
        acciones.pack(side="right", padx=(0, 18))
        ttk.Button(
            acciones,
            text="Guía de uso",
            style="Cabecera.TButton",
            command=self._abrir_guia_uso,
        ).pack(side="right", padx=(0, 8))
        ttk.Button(
            acciones,
            text="Configuración",
            style="Cabecera.TButton",
            command=self._abrir_configuracion,
        ).pack(side="right")

        self._var_estado_app = tk.StringVar(value="Sistema operativo")
        self._badge_estado_app = tk.Frame(
            cabecera,
            bg="#0E2A63",
            highlightbackground="#2E5AA8",
            highlightthickness=1,
            bd=0,
        )
        self._badge_estado_app.pack(side="right", padx=(0, 18))
        self._punto_estado_app = tk.Canvas(
            self._badge_estado_app,
            width=8,
            height=8,
            bg="#0E2A63",
            highlightthickness=0,
            bd=0,
        )
        self._punto_estado_app_item = self._punto_estado_app.create_oval(
            1, 1, 7, 7, fill="#5EE2A0", outline=""
        )
        self._punto_estado_app.pack(side="left", padx=(10, 0))
        self._lbl_estado_app = tk.Label(
            self._badge_estado_app,
            textvariable=self._var_estado_app,
            bg="#0E2A63",
            fg="#A7F3D0",
            font=(ui_tema.FUENTE, 9, "bold"),
            pady=5,
        )
        self._lbl_estado_app.pack(side="left", padx=(6, 12))

    def _pintar_estado_app(self, texto: str, tipo: str = "success") -> None:
        tonos = {
            "success": "#7EF0BE",
            "warning": "#FBD38D",
            "danger": "#FCA5A5",
            "info": "#93C5FD",
            "neutral": "#C7D2E5",
        }
        color = tonos.get(tipo, tonos["neutral"])
        self._var_estado_app.set(texto)
        self._badge_estado_app.configure(bg="#0E2A63", highlightbackground=color)
        self._punto_estado_app.configure(bg="#0E2A63")
        self._punto_estado_app.itemconfigure(self._punto_estado_app_item, fill=color)
        self._lbl_estado_app.configure(bg="#0E2A63", fg=color)

    # ------------------------------------------------------------------
    # Navegación lateral
    # ------------------------------------------------------------------
    _SECCIONES = (
        ("▦", "Panel Principal"),
        ("⇩", "Cargar y Procesar"),
        ("👥", "Usuarios y Áreas"),
        ("↻", "Reimportar Correcciones"),
        ("▤", "Maestro Histórico"),
        ("▥", "Reportes"),
        ("🛡", "Auditoría"),
    )

    def _seleccionar_seccion(self, indice: int) -> None:
        if not hasattr(self, "_cuaderno"):
            return
        self._cuaderno.select(indice)
        for posicion, boton in enumerate(self._botones_nav):
            boton.marcar_activo(posicion == indice)
        if indice == 0:
            self._refrescar_panel()

    def _construir_barra_lateral(self, padre: tk.Misc) -> None:
        barra = tk.Frame(padre, bg=AZUL_MARINO, width=232)
        barra.pack(side="left", fill="y")
        barra.pack_propagate(False)

        marca = tk.Frame(barra, bg=AZUL_MARINO)
        marca.pack(fill="x", pady=(18, 14), padx=18)
        tk.Label(
            marca,
            text="MENÚ PRINCIPAL",
            bg=AZUL_MARINO,
            fg="#7FA4E0",
            font=(ui_tema.FUENTE, 8, "bold"),
            anchor="w",
        ).pack(anchor="w")

        self._botones_nav: list[BotonNavegacion] = []
        for indice, (icono, texto) in enumerate(self._SECCIONES):
            boton = BotonNavegacion(
                barra,
                icono=icono,
                texto=texto,
                comando=lambda i=indice: self._seleccionar_seccion(i),
            )
            boton.pack(fill="x", padx=8, pady=2)
            self._botones_nav.append(boton)
        self._botones_nav[0].marcar_activo(True)

        pie = tk.Frame(barra, bg=AZUL_MARINO)
        pie.pack(side="bottom", fill="x", padx=16, pady=18)
        tk.Frame(pie, bg="#2E5AA8", height=1).pack(fill="x", pady=(0, 14))
        tk.Label(
            pie,
            text="INKACROPS",
            bg=AZUL_MARINO,
            fg=BLANCO,
            font=(ui_tema.FUENTE, 15, "bold"),
        ).pack(anchor="w")
        tk.Label(
            pie,
            text="Tecnología que impulsa",
            bg=AZUL_MARINO,
            fg="#8FB4E8",
            font=(ui_tema.FUENTE, 8),
        ).pack(anchor="w", pady=(0, 8))
        ttk.Button(
            pie,
            text="Conexión SQL Server",
            style="Cabecera.TButton",
            command=self._abrir_configuracion_sql,
        ).pack(fill="x")

    def _abrir_configuracion(self) -> None:
        VentanaConfiguracion(self).wait_window()

    def _abrir_configuracion_sql(self) -> None:
        VentanaConfiguracion(self, seccion="sql").wait_window()

    def _abrir_guia_uso(self) -> None:
        VentanaGuiaUso(self).wait_window()

    def _crear_pestanas(self) -> None:
        cuerpo = tk.Frame(self, bg=COLOR_FONDO)
        cuerpo.pack(fill="both", expand=True)
        self._construir_barra_lateral(cuerpo)

        contenedor = tk.Frame(cuerpo, bg=COLOR_FONDO)
        contenedor.pack(side="left", fill="both", expand=True, padx=(14, 14), pady=(12, 12))

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
        self._cuaderno.bind("<<NotebookTabChanged>>", self._al_cambiar_pestana)

        self._construir_panel()
        self._construir_procesar()
        self._construir_usuarios()
        self._construir_reimportar()
        self._construir_maestro()
        self._construir_reportes()
        self._construir_auditoria()

    def _al_cambiar_pestana(self, _evento=None) -> None:
        try:
            indice = self._cuaderno.index(self._cuaderno.select())
        except (tk.TclError, ValueError):
            return
        for posicion, boton in enumerate(getattr(self, "_botones_nav", [])):
            boton.marcar_activo(posicion == indice)

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
            self._tab_procesar, text="2. Procesar, validar y generar el Excel", padding=10
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
            text="Procesar y validar",
            style="Principal.TButton",
            command=self._procesar,
            state="disabled",
        )
        self._btn_procesar.pack(side="left")
        self._btn_generar_normal = ttk.Button(
            botones,
            text="Generar Excel normal",
            command=lambda: self._generar(cfg.MODO_NORMAL),
            state="disabled",
        )
        self._btn_generar_normal.pack(side="left", padx=(8, 0))
        self._btn_generar_detalle = ttk.Button(
            botones,
            text="Generar Excel a detalle",
            command=lambda: self._generar(cfg.MODO_DETALLE),
            state="disabled",
        )
        self._btn_generar_detalle.pack(side="left", padx=8)
        ttk.Button(
            botones, text="Abrir resultado", command=self._abrir_procesado
        ).pack(side="left", padx=8)
        ttk.Button(
            botones, text="Abrir carpeta de salida", command=lambda: _abrir(cfg.OUTPUT_DIR)
        ).pack(side="left", padx=8)
        ttk.Button(
            botones, text="Reanudar revisión", command=self._reanudar_revision
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
        self._ruta_entrada_actual = str(ruta)
        self._hoja_entrada_actual = hoja

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
                    modo=cfg.MODO_NORMAL,
                    permitir_pendientes=True,
                    crear_revision=True,
                )

        self._ejecutar_hilo(tarea, self._fin_procesar, "procesamiento")

    def _fin_procesar(self, estadisticas: Any) -> None:
        self._barra.stop()
        self._barra.configure(mode="determinate", value=100)
        self._estadisticas_actuales = estadisticas
        self._ultimo_procesado = estadisticas.archivo_salida
        self._ultimo_manifiesto = estadisticas.archivo_manifiesto or ""
        self._ultima_copia_revision = getattr(
            estadisticas, "ruta_copia_revision", ""
        ) or ""
        ruta_revision = getattr(estadisticas, "ruta_revision", "") or ""
        self._ruta_revision_actual = ruta_revision
        for nombre in ("_btn_generar_normal", "_btn_generar_detalle"):
            boton = self.__dict__.get(nombre)
            if boton is not None:
                boton.configure(state="normal")
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
        svc_eventos.publicar_procesamiento(
            detalle="Procesamiento finalizado",
            datos={
                "archivo_salida": estadisticas.archivo_salida,
                "registros": estadisticas.registros_totales,
                "pendientes": estadisticas.registros_pendientes,
            },
        )
        self._terminar_ocupado()
        self._var_archivo.set("")
        self._limpiar_validacion_archivo(
            "Procesamiento finalizado. Seleccione otro Excel para continuar."
        )
        if ruta_revision:
            messagebox.showinfo(
                "Validación requerida",
                "Se detectaron registros que necesitan revisión:\n\n"
                f"Pendientes de área: {estadisticas.registros_pendientes}\n"
                f"Conflictos: {getattr(estadisticas, 'conflictos', 0)}\n\n"
                "Se abrirá la ventana «Validación de Usuarios y Áreas».",
            )
            self._abrir_ventana_validacion(ruta_revision)
            return
        messagebox.showinfo(
            "Procesamiento completado",
            "Se generó el Excel con las 11 columnas oficiales.\n\n"
            f"Archivo: {cfg.ruta_visible(Path(estadisticas.archivo_salida))}\n"
            f"Registros: {estadisticas.registros_totales}\n"
            "No hay pendientes de validación.\n\n"
            "Puede generar el Excel normal o a detalle desde esta pestaña.",
        )

    # ------------------------------------------------------------------
    # Validación de usuarios y áreas
    # ------------------------------------------------------------------
    def _abrir_ventana_validacion(self, ruta_revision: str) -> None:
        from core import revision as svc_revision
        from ventana_validacion import VentanaValidacion

        try:
            revision = svc_revision.Revision.cargar(ruta_revision)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(
                "No se pudo abrir la revisión",
                f"{exc}\n\nLa revisión quedó guardada en:\n{ruta_revision}",
            )
            return

        ventana = self._ventana_validacion
        if ventana is not None and ventana.winfo_exists():
            ruta_abierta = getattr(getattr(ventana, "revision", None), "ruta", None)
            if ruta_abierta is not None and str(ruta_abierta) == str(ruta_revision):
                ventana.deiconify()
                ventana.lift()
                ventana.focus_force()
                return
            ventana.destroy()

        self._revision_actual = revision
        self._ruta_revision_actual = ruta_revision
        try:
            self._ventana_validacion = VentanaValidacion(
                self,
                revision,
                self._maestro,
                al_asignar=self._asignar_desde_validacion,
                al_generar=lambda modo, permitir: self._generar_desde_validacion(
                    modo, permitir, self._ventana_validacion
                ),
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("No se pudo abrir la ventana de validación")
            messagebox.showerror("No se pudo abrir la validación", str(exc))

    def _asignar_desde_validacion(
        self, usuario: str, area: str, origen: str
    ) -> None:
        # Las áreas manuales se validan con confirmación; las que provienen de
        # SQL se normalizan sin interrumpir el procesamiento por lotes.
        area_final = self._confirmar_area(
            usuario, area, preguntar=(origen == procedencia.MANUAL)
        )
        if not area_final:
            return
        try:
            with bloqueo("actualización de usuarios y áreas"):
                svc_asignaciones.asignar_una(
                    self._maestro,
                    usuario,
                    area_final,
                    usuario_ejecutor=os.getenv("USERNAME", ""),
                )
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(
                "No se pudo guardar el área",
                f"{usuario}: {exc}",
            )
            raise
        self._refrescar_usuarios()
        self._refrescar_pendientes()
        self._refrescar_panel()
        self._refrescar_auditoria()
        self._var_info_usuario.set(
            f"Asignación guardada: {usuario} → {area_final} ({origen})"
        )

    def _reanudar_revision(self) -> None:
        from core import revision as svc_revision

        revisiones = svc_revision.listar_revisiones()
        if not revisiones:
            messagebox.showinfo(
                "Sin revisiones",
                "Todavía no hay revisiones guardadas para reanudar.",
            )
            return
        seleccion = self._elegir_revision(revisiones)
        if not seleccion:
            return
        try:
            revision = svc_revision.Revision.cargar(seleccion["ruta"])
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("No se pudo abrir la revisión", str(exc))
            return
        entrada = Path(revision.archivo_original)
        if entrada.is_file():
            self._ruta_entrada_actual = str(entrada)
            self._hoja_entrada_actual = None
        else:
            messagebox.showwarning(
                "Archivo original no disponible",
                "La revisión se abrirá, pero no se podrá regenerar el Excel "
                f"porque el archivo original ya no existe:\n{entrada}",
            )
        if revision.archivo_procesado:
            self._ultimo_procesado = revision.archivo_procesado
        self._abrir_ventana_validacion(seleccion["ruta"])

    def _elegir_revision(self, revisiones: list[dict[str, Any]]) -> dict[str, Any] | None:
        ventana = tk.Toplevel(self)
        ventana.title("Reanudar revisión")
        ventana.geometry("720x380")
        ventana.transient(self)
        ventana.grab_set()
        ventana.configure(bg=COLOR_FONDO)

        ttk.Label(
            ventana,
            text="Seleccione la revisión que desea continuar",
            style="Seccion.TLabel",
        ).pack(anchor="w", padx=16, pady=(14, 8))
        marco = ttk.Frame(ventana)
        marco.pack(fill="both", expand=True, padx=16)
        lista = tk.Listbox(marco, height=10)
        barra = ttk.Scrollbar(marco, orient="vertical", command=lista.yview)
        lista.configure(yscrollcommand=barra.set)
        lista.pack(side="left", fill="both", expand=True)
        barra.pack(side="right", fill="y")
        for item in revisiones:
            lista.insert(
                tk.END,
                f"{item['nombre_original'] or 'Sin nombre'} | "
                f"{item['actualizado'] or 'sin fecha'} | "
                f"por revisar: {item['por_revisar']}",
            )
        if revisiones:
            lista.selection_set(0)

        elegido: dict[str, Any] = {}

        def confirmar() -> None:
            seleccion = lista.curselection()
            if not seleccion:
                return
            elegido.update(revisiones[seleccion[0]])
            ventana.destroy()

        botones = ttk.Frame(ventana)
        botones.pack(fill="x", padx=16, pady=12)
        ttk.Button(botones, text="Abrir", command=confirmar).pack(side="right")
        ttk.Button(botones, text="Cancelar", command=ventana.destroy).pack(
            side="right", padx=8
        )
        lista.bind("<Double-1>", lambda _e: confirmar())
        self.wait_window(ventana)
        return elegido or None

    # ------------------------------------------------------------------
    # Generación de Excel
    # ------------------------------------------------------------------
    def _generar(self, modo: str) -> None:
        if self._ocupado:
            return
        ruta = self._ruta_entrada_actual or self._var_archivo.get().strip()
        if not ruta or not Path(ruta).is_file():
            messagebox.showwarning(
                "Sin archivo",
                "Procese y valide un archivo antes de generar el Excel.",
            )
            return
        pendientes = int(
            getattr(self._estadisticas_actuales, "registros_pendientes", 0) or 0
        )
        conflictos = int(
            getattr(self._estadisticas_actuales, "conflictos", 0) or 0
        )
        if pendientes or conflictos:
            if not messagebox.askyesno(
                "Generar Excel con pendientes",
                "No se puede generar el Excel completo:\n\n"
                f"Registros sin área: {pendientes}\n"
                f"Conflictos sin resolver: {conflictos}\n\n"
                "¿Desea generar un Excel CON PENDIENTES? La generación quedará "
                "registrada como parcial.",
            ):
                return
            self._lanzar_generacion(modo, True)
            return
        self._lanzar_generacion(modo, False)

    def _generar_desde_validacion(
        self, modo: str, permitir_pendientes: bool, ventana: Any = None
    ) -> bool:
        if self._ocupado:
            messagebox.showinfo(
                "Operación en curso",
                "Espere a que termine la operación actual.",
            )
            return False
        if not self._ruta_entrada_actual:
            messagebox.showwarning(
                "Sin archivo original",
                "No se puede regenerar el Excel porque esta revisión no tiene "
                "un archivo original asociado.",
            )
            return False
        self._lanzar_generacion(modo, permitir_pendientes, ventana)
        return True

    def _lanzar_generacion(
        self, modo: str, permitir_pendientes: bool, ventana: Any = None
    ) -> None:
        ruta = Path(self._ruta_entrada_actual)
        hoja = self._hoja_entrada_actual
        nombre = "Excel a detalle" if modo == cfg.MODO_DETALLE else "Excel normal"
        self._iniciar_ocupado(f"generando {nombre}…")
        self._barra.configure(mode="indeterminate")
        self._barra.start(12)
        self._var_etapa.set(f"Generando {nombre}…")

        def tarea() -> Any:
            try:
                with bloqueo("generación de Excel"):
                    estadisticas = procesar_archivo(
                        ruta,
                        self._maestro,
                        hoja=hoja,
                        sobrescribir=True,
                        formato_fecha=cfg.FORMATO_FECHA_PREFERIDO,
                        usuario_ejecutor=os.getenv("USERNAME", ""),
                        modo=modo,
                        permitir_pendientes=permitir_pendientes,
                        crear_revision=False,
                    )
                return True, estadisticas
            except Exception as exc:  # noqa: BLE001
                return False, exc

        def fin(resultado: Any) -> None:
            ok, carga = resultado
            if not ok:
                self._barra.stop()
                self._barra.configure(mode="determinate", value=0)
                self._terminar_ocupado()
                messagebox.showerror("No se pudo generar el Excel", str(carga))
                if ventana is not None and ventana.winfo_exists():
                    ventana.generacion_terminada(False)
                return
            self._fin_generacion(carga, ventana)

        self._ejecutar_hilo(tarea, fin, "generación de Excel")

    def _fin_generacion(self, estadisticas: Any, ventana: Any = None) -> None:
        self._barra.stop()
        self._barra.configure(mode="determinate", value=100)
        self._estadisticas_actuales = estadisticas
        self._ultimo_procesado = estadisticas.archivo_salida
        self._ultimo_manifiesto = estadisticas.archivo_manifiesto or ""
        self._ultima_copia_revision = estadisticas.ruta_copia_revision or ""
        self._ruta_revision_actual = estadisticas.ruta_revision or self._ruta_revision_actual
        svc_estado.actualizar_desde_estadisticas(
            estadisticas,
            operacion="GENERACION",
            mensajes=[
                f"{estadisticas.archivo_entrada}: {estadisticas.modo_generacion}, "
                f"{estadisticas.registros_pendientes} pendientes."
            ],
        )
        self._var_etapa.set(
            "Generación finalizada"
            + (" (con pendientes)." if estadisticas.generacion_parcial else ".")
        )
        self._refrescar_panel()
        self._refrescar_pendientes()
        self._refrescar_auditoria()
        self._terminar_ocupado()
        lineas = [
            f"Archivo: {cfg.ruta_visible(Path(estadisticas.archivo_salida))}",
        ]
        if estadisticas.archivo_detalle:
            lineas.append(
                f"Detalle: {cfg.ruta_visible(Path(estadisticas.archivo_detalle))}"
            )
        if estadisticas.ruta_copia_revision:
            lineas.append(
                "Copia de revisión: "
                f"{cfg.ruta_visible(Path(estadisticas.ruta_copia_revision))}"
            )
        if estadisticas.generacion_parcial:
            lineas.append(
                f"Generación parcial: {estadisticas.registros_pendientes} registros sin área."
            )
        messagebox.showinfo("Excel generado", "\n".join(lineas))
        if ventana is not None and ventana.winfo_exists():
            ventana.generacion_terminada(True)

    def _abrir_procesado(self) -> None:
        ruta = self._ultimo_procesado or self._estado.ultimo_archivo_procesado
        if not ruta or not Path(ruta).exists():
            messagebox.showinfo(
                "Sin resultado", "Todavía no existe un Excel procesado para abrir."
            )
            return
        try:
            copia = self._ultima_copia_revision
            if copia and Path(copia).is_file():
                destino = Path(copia)
            else:
                from core.procesado import copiar_revision

                destino = copiar_revision(Path(ruta))
                self._ultima_copia_revision = str(destino)
            _abrir(destino)
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

    def _confirmar_area(
        self, usuario: str, area: str, *, preguntar: bool = True
    ) -> str | None:
        """Valida duplicados y áreas parecidas antes de escribir.

        Devuelve el nombre canónico que debe guardarse, o ``None`` si la
        persona cancela. No modifica el maestro por sí sola.
        """

        resultado = catalogo_areas.validar_area(area, maestro=self._maestro)
        if not resultado.valido:
            messagebox.showwarning("Área no válida", resultado.mensaje)
            return None
        if resultado.existente:
            return resultado.area_reutilizable or resultado.nombre
        if not preguntar:
            return resultado.nombre

        if resultado.similares:
            similar = resultado.similares[0]
            if messagebox.askyesno(
                "Área parecida",
                f"Ya existe el área '{similar.nombre}' "
                f"({similar.similitud:.0%} de similitud).\n\n"
                f"¿Desea reutilizar '{similar.nombre}' en lugar de crear "
                f"'{resultado.nombre}'?",
            ):
                return similar.nombre
            if not messagebox.askyesno(
                "Confirmar área nueva",
                f"'{resultado.nombre}' es parecida a '{similar.nombre}', pero "
                "distinta.\n\n¿Registrarla como área nueva?",
            ):
                return None
            return resultado.nombre

        if not messagebox.askyesno(
            "Confirmar área nueva",
            f"El área '{resultado.nombre}' no existe en el maestro.\n\n"
            f"¿Registrarla y asignarla a '{usuario}'?",
        ):
            return None
        return resultado.nombre

    def _guardar_asignacion(self) -> None:
        usuario = self._var_usuario.get().strip()
        area = self._var_area.get().strip()
        if not usuario or not area:
            messagebox.showwarning(
                "Datos incompletos", "Indique el usuario y el área."
            )
            return
        area = self._confirmar_area(usuario, area) or ""
        if not area:
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
            aviso_hoja = ""
            if ruta_adicional is not None and Path(ruta_adicional).is_file():
                adicionales, _hoja, aviso_hoja = MaestroTickets.leer_hoja_detalle(
                    ruta_adicional
                )
            filas = svc_solicitantes.detectar_solicitantes(maestro, adicionales)
        except (ErrorArchivo, ErrorEstructura, OSError, ValueError) as exc:
            LOGGER.error("No se pudo auditar solicitantes: %s", exc)
            detalle = ""
            if ruta_adicional is not None:
                diagnostico = MaestroTickets.inspeccionar_hojas(ruta_adicional)
                detalle = (
                    f" Archivo: {diagnostico.get('archivo')}. "
                    f"Hojas: {', '.join(diagnostico.get('hojas') or []) or 'ninguna'}. "
                    f"Hoja esperada: {diagnostico.get('hoja_esperada')}."
                )
            self._var_info_solicitantes.set(
                f"Auditoría de solicitantes pendiente: {exc}.{detalle}"
            )
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
        mensaje = (
            f"{len(filas)} solicitantes revisados | "
            f"{pendientes} coincidencias requieren decisión | "
            f"{genericos} solicitantes genéricos protegidos. "
            "Las sugerencias no fusionan ni modifican los nombres originales."
        )
        if aviso_hoja:
            mensaje += f" {aviso_hoja}"
        self._var_info_solicitantes.set(mensaje)

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
        ttk.Button(
            barra,
            text="Diagnóstico",
            command=self._mostrar_diagnostico,
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

    def _mostrar_diagnostico(self) -> None:
        """Muestra el diagnóstico del sistema sin exponer credenciales."""

        try:
            datos = svc_diagnostico.diagnosticar()
        except Exception as exc:  # noqa: BLE001 - el diagnóstico no debe tumbar la app
            LOGGER.exception("No se pudo generar el diagnóstico")
            messagebox.showerror("No se pudo generar el diagnóstico", str(exc))
            return

        ventana = tk.Toplevel(self)
        ventana.title("Diagnóstico del sistema")
        ventana.geometry("820x560")
        ventana.minsize(620, 420)
        ventana.configure(bg=COLOR_FONDO)
        ventana.transient(self)

        cabecera = ttk.Frame(ventana, padding=(12, 10))
        cabecera.pack(fill="x")
        ttk.Label(
            cabecera,
            text="Diagnóstico del sistema",
            style="Seccion.TLabel",
        ).pack(side="left")
        ttk.Label(
            cabecera,
            text=f"Generado: {datos.fecha}",
            style="Subtitulo.TLabel",
        ).pack(side="right")

        marco = ttk.Frame(ventana, padding=(12, 0, 12, 8))
        marco.pack(fill="both", expand=True)
        texto = tk.Text(
            marco,
            wrap="word",
            font=("Consolas", 9),
            bg="white",
            fg=COLOR_TEXTO,
            relief="solid",
            borderwidth=1,
        )
        vertical = ttk.Scrollbar(marco, orient="vertical", command=texto.yview)
        texto.configure(yscrollcommand=vertical.set)
        texto.pack(side="left", fill="both", expand=True)
        vertical.pack(side="right", fill="y")
        texto.insert("1.0", datos.como_markdown())
        texto.configure(state="disabled")

        pie = ttk.Frame(ventana, padding=(12, 0, 12, 12))
        pie.pack(fill="x")

        def guardar() -> None:
            try:
                destino = svc_diagnostico.guardar_diagnostico()
            except Exception as exc:  # noqa: BLE001
                messagebox.showerror("No se pudo guardar", str(exc))
                return
            messagebox.showinfo(
                "Diagnóstico guardado",
                f"Se guardó en:\n{destino}",
            )

        ttk.Button(pie, text="Guardar informe", command=guardar).pack(
            side="left"
        )
        ttk.Button(
            pie,
            text="Abrir carpeta de reportes",
            command=lambda: _abrir(cfg.REPORTES_DIR),
        ).pack(side="left", padx=8)
        ttk.Button(pie, text="Cerrar", command=ventana.destroy).pack(
            side="right"
        )
        ventana.focus_set()

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
