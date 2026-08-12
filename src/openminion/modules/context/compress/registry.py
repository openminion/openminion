from dataclasses import dataclass
from typing import Literal


MethodKind = Literal["main", "prepass"]


@dataclass
class MethodDescriptor:
    method_id: str
    kind: MethodKind
    optional: bool = True
    available: bool = True

    def is_available(self) -> bool:
        return self.available


class MethodRegistry:
    BASELINE_METHOD_ID = "extractive.v1"

    def __init__(self) -> None:
        self._main_methods: dict[str, MethodDescriptor] = {}
        self._prepass_methods: dict[str, MethodDescriptor] = {}
        self.register_main(self.BASELINE_METHOD_ID, optional=False, available=True)

    def register_main(
        self,
        method_id: str,
        *,
        available: bool = True,
        optional: bool = True,
    ) -> None:
        self._main_methods[method_id] = MethodDescriptor(
            method_id=method_id,
            kind="main",
            optional=optional,
            available=available,
        )

    def register_prepass(
        self,
        method_id: str,
        *,
        available: bool = True,
        optional: bool = True,
    ) -> None:
        self._prepass_methods[method_id] = MethodDescriptor(
            method_id=method_id,
            kind="prepass",
            optional=optional,
            available=available,
        )

    def set_availability(self, method_id: str, available: bool) -> None:
        descriptor = self._main_methods.get(method_id) or self._prepass_methods.get(
            method_id
        )
        if descriptor is None:
            raise KeyError(f"method not registered: {method_id}")
        descriptor.available = available

    def get_descriptor(self, method_id: str) -> MethodDescriptor | None:
        return self._main_methods.get(method_id) or self._prepass_methods.get(method_id)

    def get_main(self, method_id: str | None) -> MethodDescriptor | None:
        return self._main_methods.get(method_id) if method_id else None

    def get_prepass(self, method_id: str | None) -> MethodDescriptor | None:
        return self._prepass_methods.get(method_id) if method_id else None

    def is_main_available(self, method_id: str | None) -> bool:
        if method_id == self.BASELINE_METHOD_ID:
            return True
        descriptor = self.get_main(method_id)
        return bool(descriptor and descriptor.is_available())

    def is_prepass_available(self, method_id: str | None) -> bool:
        descriptor = self.get_prepass(method_id)
        return bool(descriptor and descriptor.is_available())

    @property
    def baseline_method_id(self) -> str:
        return self.BASELINE_METHOD_ID
