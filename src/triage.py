"""Orquesta el flujo de una consulta:

    validar entrada -> guardrail de entrada -> prompt -> LLM (con fallback)
    -> contrato (con reintento informado) -> guardrail de salida -> log de seguridad
"""

import uuid
from dataclasses import dataclass, field

from src.errores import ConsultaInvalidaError, ContratoInvalidoError
from src.esquemas import RespuestaTriage, schema_para_api
from src.guardrails import BLOQUEAR, MARCAR, PERMITIR, cargar_politica, mas_severa
from src.guardrails.entrada import revisar_entrada
from src.guardrails.salida import revisar_salida
from src.metricas import registrar_exito, registrar_fallo, registrar_seguridad
from src.prompts import Prompt
from src.proveedores import ProveedorLLM, ProveedorNoDisponible, RespuestaLLM
from src.validacion import modelo_de_salida, validar

MAX_INTENTOS_CONTRATO = 3
LARGO_MAXIMO_CONSULTA = 4000  # caracteres; un ticket real es mucho más corto
CONFIANZA_MAXIMA_SI_MARCADO = 0.5


@dataclass
class ResultadoTriage:
    respuesta: RespuestaTriage
    request_id: str = ""
    # Todas las llamadas al LLM que hicieron falta (incluye reintentos de contrato).
    llamadas: list[RespuestaLLM] = field(default_factory=list)
    accion_entrada: str = PERMITIR
    accion_salida: str | None = None
    motivos: list[str] = field(default_factory=list)
    # Sólo prompts chain-of-thought: no forma parte del contrato público, pero la
    # suite de evaluación lo usa para el LLM-as-judge.
    razonamiento: str | None = None

    @property
    def costo_usd(self) -> float:
        return sum(llamada.costo_usd for llamada in self.llamadas)

    @property
    def latencia_s(self) -> float:
        return sum(llamada.latencia_s for llamada in self.llamadas)

    @property
    def tokens_total(self) -> int:
        return sum(llamada.tokens_total for llamada in self.llamadas)


def validar_consulta(consulta: str) -> str:
    """Errores del caller: se detectan antes de gastar una sola llamada al modelo."""
    texto = consulta.strip()
    if not texto:
        raise ConsultaInvalidaError("La consulta está vacía.")
    if len(texto) > LARGO_MAXIMO_CONSULTA:
        raise ConsultaInvalidaError(
            f"La consulta tiene {len(texto)} caracteres; el máximo es {LARGO_MAXIMO_CONSULTA}."
        )
    return texto


def respuesta_bloqueo(motivos: list[str]) -> RespuestaTriage:
    """Respuesta segura y válida según el contrato: el consumidor nunca recibe un formato distinto."""
    return RespuestaTriage(
        answer=cargar_politica().respuesta_bloqueo,
        confidence=0.0,
        category="other",
        actions=[f"Revisión manual: el mensaje fue bloqueado por los controles de seguridad ({', '.join(motivos)})."],
    )


def marcar_para_revision(respuesta: RespuestaTriage, motivos: list[str]) -> RespuestaTriage:
    aviso = f"⚠ Revisar: posible intento de manipulación o contenido sensible ({', '.join(motivos)})."
    return RespuestaTriage.model_validate(
        respuesta.model_dump()
        | {
            "confidence": min(respuesta.confidence, CONFIANZA_MAXIMA_SI_MARCADO),
            "actions": [aviso, *respuesta.actions][:6],
        }
    )


def completar_con_fallback(
    proveedores: list[ProveedorLLM], prompt: Prompt, sistema: str, mensaje: str, schema: dict, intento: int, request_id: str
) -> RespuestaLLM:
    """Prueba los proveedores en orden. Sólo pasa al siguiente si el actual no está disponible
    (rate limit o caída sostenida, o su API key fue rechazada); un error definitivo (request
    inválida) se propaga, porque con otro proveedor fallaría igual o escondería un bug.
    """
    for proveedor in proveedores:
        try:
            respuesta = proveedor.completar(sistema, mensaje, schema)
        except ProveedorNoDisponible as error:
            registrar_fallo(proveedor.nombre, prompt.version, error, request_id)
            ultimo_error = error
            continue
        except Exception as error:
            registrar_fallo(proveedor.nombre, prompt.version, error, request_id)
            raise
        registrar_exito(respuesta, prompt.version, intento_contrato=intento, request_id=request_id)
        return respuesta
    raise ultimo_error


def llamar_con_contrato(
    proveedores: list[ProveedorLLM], prompt: Prompt, texto: str, resultado: ResultadoTriage
) -> RespuestaTriage:
    """LLM + validación del contrato, con reintento que le explica al modelo qué falló."""
    schema = schema_para_api(modelo_de_salida(prompt.razonamiento))
    sistema = prompt.sistema
    mensaje = prompt.armar_mensaje_usuario(texto)
    error_previo = None
    for intento in range(1, MAX_INTENTOS_CONTRATO + 1):
        if error_previo:
            # El error va al system prompt y no al mensaje del usuario: es una corrección
            # a cómo tiene que responder el modelo, no un dato nuevo del cliente.
            sistema += f"\n\n(Tu respuesta anterior fue inválida: {error_previo}. Corregila.)"
        llamada = completar_con_fallback(proveedores, prompt, sistema, mensaje, schema, intento, resultado.request_id)
        resultado.llamadas.append(llamada)
        contrato, error_previo = validar(llamada.datos, llamada.texto, prompt.razonamiento)
        if contrato is not None:
            if prompt.razonamiento and llamada.datos:
                resultado.razonamiento = llamada.datos.get("razonamiento")
            return contrato
    raise ContratoInvalidoError(f"Se agotaron los {MAX_INTENTOS_CONTRATO} intentos. Último error: {error_previo}")


def procesar_consulta(
    consulta: str, proveedores: list[ProveedorLLM], prompt: Prompt, guardrails: bool = True
) -> ResultadoTriage:
    """`guardrails=False` existe sólo para medir su efecto en la suite adversarial."""
    texto = validar_consulta(consulta)
    resultado = ResultadoTriage(respuesta=None, request_id=uuid.uuid4().hex[:12])  # type: ignore[arg-type]

    if not guardrails:
        resultado.respuesta = llamar_con_contrato(proveedores, prompt, texto, resultado)
        return resultado

    # 1. Entrada: si es un ataque evidente, ni siquiera se llama al modelo.
    entrada = revisar_entrada(texto)
    resultado.accion_entrada = entrada.decision.accion
    resultado.motivos = list(entrada.decision.codigos)
    try:
        if entrada.decision.accion == BLOQUEAR:
            resultado.respuesta = respuesta_bloqueo(resultado.motivos)
            return resultado

        # 2. LLM con el texto ya enmascarado (sin PII ni etiquetas que imiten el prompt).
        contrato = llamar_con_contrato(proveedores, prompt, entrada.texto_para_llm, resultado)

        # 3. Salida.
        contrato, salida = revisar_salida(contrato, prompt.sistema)
        resultado.accion_salida = salida.accion
        resultado.motivos += [c for c in salida.codigos if c not in resultado.motivos]
        accion_final = mas_severa(entrada.decision.accion, salida.accion)

        if accion_final == BLOQUEAR:
            resultado.respuesta = respuesta_bloqueo(resultado.motivos)
        elif accion_final == MARCAR:
            resultado.respuesta = marcar_para_revision(contrato, resultado.motivos)
        else:
            resultado.respuesta = contrato
        return resultado
    finally:
        registrar_seguridad(
            request_id=resultado.request_id,
            consulta=texto,
            version_politica=cargar_politica().version,
            version_prompt=prompt.version,
            accion_entrada=resultado.accion_entrada,
            accion_salida=resultado.accion_salida,
            motivos=resultado.motivos,
            pii_enmascarada=dict(entrada.pii),
        )
