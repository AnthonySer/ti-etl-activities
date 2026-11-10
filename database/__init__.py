"""Capa de acceso a la base corporativa EXACTUS (SQL Server)."""

from __future__ import annotations

from .connection import (
    ErrorConexionSQL,
    conectar,
    construir_cadena_conexion,
    ejecutar_consulta,
    probar_conexion,
)
from .repository import Empleado, RepositorioEmpleados, ResultadoConsulta

__all__ = [
    "Empleado",
    "ErrorConexionSQL",
    "RepositorioEmpleados",
    "ResultadoConsulta",
    "conectar",
    "construir_cadena_conexion",
    "ejecutar_consulta",
    "probar_conexion",
]
