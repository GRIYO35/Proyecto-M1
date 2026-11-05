"""CLI de triage de consultas de soporte de Nubbix.

Uso:
    python -m src.main "me cobraron dos veces la suscripción"
    python -m src.main "..." --proveedor openai --respaldo anthropic --prompt v1_zero_shot
    python src/main.py "..."      (también funciona)
"""

import argparse
import os
import sys
from pathlib import Path

# Ejecutado como script (`python src/main.py`), Python no ve el paquete `src`:
# se agrega la raíz del proyecto al path para que ambas formas funcionen.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from src.errores import ConsultaInvalidaError, ContratoInvalidoError
from src.prompts import cargar_prompt
from src.proveedores import ErrorConfiguracion, ErrorProveedor, crear_proveedor
from src.triage import procesar_consulta


def leer_argumentos() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Clasifica una consulta de soporte y propone una respuesta.")
    parser.add_argument("consulta", help="Texto libre de la consulta del cliente.")
    parser.add_argument("--proveedor", default=os.getenv("LLM_PROVEEDOR", "anthropic"))
    parser.add_argument(
        "--respaldo",
        default=os.getenv("LLM_PROVEEDOR_RESPALDO") or None,
        help="Proveedor a usar si el principal no está disponible (opcional).",
    )
    parser.add_argument("--prompt", default=os.getenv("PROMPT_VERSION", "v5_seguro"))
    return parser.parse_args()


def main() -> None:
    # La consola de Windows no usa UTF-8 por defecto y rompe con tildes.
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    load_dotenv()
    argumentos = leer_argumentos()

    try:
        nombres = [argumentos.proveedor]
        if argumentos.respaldo and argumentos.respaldo != argumentos.proveedor:
            nombres.append(argumentos.respaldo)
        proveedores = [crear_proveedor(nombre) for nombre in nombres]
        prompt = cargar_prompt(argumentos.prompt)
        resultado = procesar_consulta(argumentos.consulta, proveedores, prompt)
    except ConsultaInvalidaError as error:  # error de quien llama (≈ HTTP 400)
        print(f"Consulta inválida: {error}", file=sys.stderr)
        sys.exit(2)
    except (ErrorConfiguracion, ValueError) as error:  # falta una API key, o proveedor/prompt inexistente
        print(f"Error de configuración: {error}", file=sys.stderr)
        sys.exit(1)
    except (ContratoInvalidoError, ErrorProveedor) as error:  # falló el procesamiento (≈ HTTP 502)
        print(f"Error al procesar: {error}", file=sys.stderr)
        sys.exit(3)

    print(resultado.respuesta.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
