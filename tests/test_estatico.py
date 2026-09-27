"""Un análisis estático que corre como test, y lo pidió una corrida caída.

El 20-08 la corrida murió con `NameError: name 'comuna_cruda' is not
defined`: una variable que no existía en ese ámbito, en una línea que los
596 tests recorrían sin tocarla. La razón es de Python puro —
`a.comuna or comuna_cruda` evalúa de izquierda a derecha, y como los
fixtures siempre traen comuna, el segundo operando nunca se llegaba a
mirar— así que ningún test iba a cazarla nunca. En producción llegó un
aviso sin comuna y se llevó la corrida entera.

Contra eso los tests no alcanzan: hay que LEER el código, no ejecutarlo.
Estos dos tests son esa lectura, y corren en cada `pytest`.
"""

import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def _pyflakes() -> list[str]:
    salida = subprocess.run(
        [sys.executable, "-m", "pyflakes", str(RAIZ / "arriendo")],
        capture_output=True, text=True)
    return [l for l in salida.stdout.splitlines() if l.strip()]


def test_ninguna_variable_indefinida():
    """Lo que mató la corrida del 20-08. Cero tolerancia: una variable que
    no existe es una corrida caída esperando el aviso que la active."""
    graves = [l for l in _pyflakes()
              if "undefined name" in l or "local variable" in l]
    assert not graves, "nombres indefinidos:\n" + "\n".join(graves)


def test_el_codigo_no_acumula_pelusa():
    """Imports muertos y f-strings sin placeholders. No tumban nada, pero
    esconden a los que sí: un pyflakes con veinte líneas de ruido es un
    pyflakes que nadie lee, y ahí es donde se escondió el NameError."""
    ruido = _pyflakes()
    assert not ruido, "pyflakes tiene algo que decir:\n" + "\n".join(ruido)


def test_la_corrida_automatica_tiene_un_solo_camino_para_mandar_mensajes():
    """El canal manda publicaciones nuevas y NADA MÁS (pedido del 27-09).

    Esa promesa no se sostiene con un comentario: se sostiene si en todo el
    paquete hay un único punto que interrumpe al usuario sin que él lo haya
    pedido. Este test lo cuenta leyendo el código, que es lo que sobrevive a
    la próxima edición del orquestador — un `telegram.enviar(...)` nuevo
    metido en el paso 7 no rompería ningún test de comportamiento, porque
    los tests miran lo que SÍ llega, no lo que se agregó de más.

    Los dos permitidos:
      - `alertar()`, la publicación nueva
      - `Telegram.alertar` llamando a su propio `enviar`

    `probar-aviso` no cuenta: lo dispara el usuario a mano desde Actions
    para comprobar que el bot está vivo, y vive en su propio subcomando.
    """
    import re

    paquete = RAIZ / "arriendo"
    envios = []
    for archivo in sorted(paquete.rglob("*.py")):
        for n, linea in enumerate(archivo.read_text().splitlines(), 1):
            if re.search(r"\.(enviar|alertar)\(", linea):
                envios.append(f"{archivo.relative_to(RAIZ)}:{n}: {linea.strip()}")

    esperados = {
        "arriendo/cli.py": 2,            # la alerta + el probar-aviso manual
        "arriendo/alerts/telegram.py": 1,  # alertar() -> enviar()
    }
    reales: dict[str, int] = {}
    for e in envios:
        reales[e.split(":")[0]] = reales.get(e.split(":")[0], 0) + 1

    assert reales == esperados, (
        "cambió quién puede mandar mensajes; si es a propósito, actualiza "
        "este test Y la promesa del README:\n" + "\n".join(envios))


def test_el_canal_no_conserva_los_mensajes_que_se_quitaron():
    """Mientras la función exista, la próxima edición puede volver a
    llamarla sin que nadie lo note. La forma de cumplir "nada más que
    publicaciones nuevas" que no se deshace sola es que no haya qué llamar.
    """
    from arriendo.alerts import telegram

    for muerto in ("mensaje_bajas", "mensaje_sobrantes", "resumen",
                   "_latido", "_que_se_rompio", "_toca_latido",
                   "_marcar_aviso", "DIAS_ENTRE_LATIDOS"):
        assert not hasattr(telegram, muerto), \
            f"{muerto} volvió: el canal puede mandar algo que no es una alerta"
