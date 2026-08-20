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
