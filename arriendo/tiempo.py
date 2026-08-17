"""La hora del radar, en un solo lugar.

Todo el proyecto trabaja en UTC **naive** —sin zona horaria pegada al
objeto— y eso es una decisión, no un descuido: el estado guarda fechas como
texto ISO sin zona, y mezclar datetimes con zona y sin zona revienta con
`TypeError` justo al restar, que es lo que este radar hace todo el tiempo
("¿cuántos días lleva publicado?").

Este módulo existe porque `datetime.utcnow()` —la forma clásica de obtener
eso— está deprecada desde Python 3.12 y se elimina después. La corrida en
Actions imprimía 1.966 warnings por corrida, que es la clase de ruido que
entierra al warning que sí importa.

`ahora_utc()` devuelve exactamente lo que devolvía `utcnow()`: el instante
actual en UTC, naive. Cambiar el proyecto a datetimes con zona sería el otro
camino, y sería más "moderno", pero obligaría a migrar cada fecha ya guardada
en `state/` y cada parseo — todo ese riesgo para representar la misma
información. La zona es siempre UTC acá; anotarla en cada objeto no agrega
nada.
"""

from __future__ import annotations

from datetime import date, datetime, timezone


def ahora_utc() -> datetime:
    """El instante actual en UTC, naive. Reemplazo directo de `utcnow()`."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def hoy_utc() -> date:
    """La fecha de hoy en UTC.

    En UTC y no local a propósito: el radar corre en runners de Actions que
    están en UTC, y el estado tiene que ser comparable entre corridas sin que
    importe dónde corrió cada una.
    """
    return ahora_utc().date()
