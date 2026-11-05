"""Errores tipados del servicio de triage.

Cada uno indica de quién es la responsabilidad y si tiene sentido reintentar.
El código de salida de la CLI sigue la misma lógica que un código HTTP:
"""


class ConsultaInvalidaError(Exception):
    """La consulta no se puede procesar (vacía o demasiado larga).

    Es un error de quien llama: no se reintenta y no se gasta ninguna llamada al modelo.
    Equivale a HTTP 400. Código de salida de la CLI: 2.
    """


class ContratoInvalidoError(Exception):
    """El modelo no devolvió un contrato válido después de todos los reintentos.

    La consulta era válida pero el sistema no pudo procesarla.
    Equivale a HTTP 502. Código de salida de la CLI: 3.
    """
