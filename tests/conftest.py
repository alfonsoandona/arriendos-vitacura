"""Configuración común de los tests.

Lo único que hay acá es una red cortada, y vale la pena explicar por qué.

Un test que se escapa a internet no es solo lento: es un test que puede pasar
o fallar según el día, y sobre todo es la señal de que el código bajo prueba
está haciendo algo que nadie le pidió.

Pasó de verdad mientras se escribía esto. Un bug de argparse hacía que
`arriendo --fuentes f.yml run` ignorara el archivo y cargara el catálogo de
verdad, así que un test que creía estar probando la validación de un YAML
inválido salió a consultar los diecisiete portales reales. Tardó 84 segundos y
el motivo verdadero quedó escondido detrás del ruido.

Con la red cortada ese mismo bug habría fallado en un segundo y con un mensaje
que apunta al lugar exacto.
"""

import socket

import pytest


class RedProhibida(RuntimeError):
    pass


@pytest.fixture(autouse=True)
def sin_red(monkeypatch):
    """Corta la red en todos los tests, sin excepciones.

    Las fuentes se prueban con fixtures de HTML y con dobles del `Fetcher`;
    ninguna parte de la suite necesita salir de verdad.
    """
    def prohibido(*args, **kwargs):
        raise RedProhibida(
            "Un test intentó abrir una conexión de red. Los tests corren "
            "offline: usa un fixture de tests/fixtures/ o un doble de "
            "`barrer`/`Fetcher`. Si el test no pretendía salir a la red, "
            "esto es el síntoma de un bug en el código que está probando."
        )

    # `create_connection` cubre a requests/urllib3, y el connect del socket
    # cubre todo lo demás, incluido el navegador.
    monkeypatch.setattr(socket, "create_connection", prohibido)
    monkeypatch.setattr(socket.socket, "connect", prohibido)
    monkeypatch.setattr(socket.socket, "connect_ex", prohibido)


@pytest.fixture(autouse=True)
def sin_geocode(monkeypatch):
    """Salta el segundo de cortesía de Nominatim en los tests.

    La red ya está cortada (sin_red), así que ningún geocode resuelve; pero
    `geo.geocode` espera 1 segundo ENTRE llamadas por respeto al servicio, y
    esa espera corre aunque la llamada vaya a fallar. Con el tope de 25 por
    corrida, cada test del pipeline pagaba ~25 segundos de sleep por nada:
    la suite entera pasó de 5 a 89 segundos el día que entró el geocoding.

    Los tests del propio geocoding parchan `geo.geocode` con un doble.
    """
    from arriendo import geo
    monkeypatch.setattr(geo, "_dormir", lambda s: None)
