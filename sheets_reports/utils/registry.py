"""
Registro genérico de piezas por clave (operadores de filtro, valores relativos, widgets).

Cada pieza es una clase con un `key` que se registra con `@REGISTRO.register`. El resto del
sistema recorre el registro (enums del JSON Schema, choices del modelo, tool de la IA) en vez
de preguntar por claves concretas: agregar una pieza es escribir su clase y registrarla.
"""
from typing import Generic, Iterator, TypeVar

T = TypeVar("T")


class UnknownKeyError(ValueError):
    """La clave no está registrada (ej. un tipo de widget que no existe)."""


class Registry(Generic[T]):
    def __init__(self, label: str, *, instantiate: bool = True):
        """`label` nombra la pieza en los mensajes ("Tipo de widget"). Con `instantiate`, se
        registra una instancia de la clase (piezas sin estado); si no, la clase misma (piezas
        que se construyen desde un spec, como las métricas)."""
        self.label = label
        self.instantiate = instantiate
        self._items: dict[str, T] = {}

    def register(self, cls):
        key = cls.key
        if key in self._items:
            raise ValueError(f"{self.label} '{key}' ya está registrado.")
        self._items[key] = cls() if self.instantiate else cls
        return cls

    def unregister(self, key: str) -> None:
        self._items.pop(key, None)

    def get(self, key) -> T:
        try:
            return self._items[key]
        except (KeyError, TypeError):
            raise UnknownKeyError(f"{self.label} desconocido: {key}") from None

    def __contains__(self, key) -> bool:
        return isinstance(key, str) and key in self._items

    def keys(self) -> list[str]:
        return list(self._items)

    def items(self):
        return self._items.items()

    def values(self) -> list[T]:
        return list(self._items.values())

    def __iter__(self) -> Iterator[T]:
        return iter(self.values())
