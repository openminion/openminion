from dataclasses import dataclass
from typing import Dict, Literal, Optional


MethodKind = Literal["main", "prepass"]


@dataclass
class MethodDescriptor:
    """Metadata describing a registered method."""

    method_id: str
    kind: MethodKind
    optional: bool = True
    available: bool = True

    def is_available(self) -> bool:
        return self.available


class MethodRegistry:
    """Tracks available compression methods for prepass and main roles."""

    BASELINE_METHOD_ID = "extractive.v1"

    def __init__(self) -> None:
        self._main_methods: Dict[str, MethodDescriptor] = {}
        self._prepass_methods: Dict[str, MethodDescriptor] = {}
        # Baseline extractive path is always available.
        self.register_main(self.BASELINE_METHOD_ID, optional=False, available=True)

    # Registration helpers -------------------------------------------------
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

    # Availability controls ------------------------------------------------
    def set_availability(self, method_id: str, available: bool) -> None:
        descriptor = self._main_methods.get(method_id) or self._prepass_methods.get(
            method_id
        )
        if descriptor is None:
            raise KeyError(f"method not registered: {method_id}")
        descriptor.available = available

    # Query helpers --------------------------------------------------------
    def get_descriptor(self, method_id: str) -> Optional[MethodDescriptor]:
        return self._main_methods.get(method_id) or self._prepass_methods.get(method_id)

    def get_main(self, method_id: Optional[str]) -> Optional[MethodDescriptor]:
        if not method_id:
            return None
        return self._main_methods.get(method_id)

    def get_prepass(self, method_id: Optional[str]) -> Optional[MethodDescriptor]:
        if not method_id:
            return None
        return self._prepass_methods.get(method_id)

    def is_main_available(self, method_id: Optional[str]) -> bool:
        if not method_id:
            return False
        if method_id == self.BASELINE_METHOD_ID:
            return True
        descriptor = self._main_methods.get(method_id)
        return bool(descriptor and descriptor.is_available())

    def is_prepass_available(self, method_id: Optional[str]) -> bool:
        if not method_id:
            return False
        descriptor = self._prepass_methods.get(method_id)
        return bool(descriptor and descriptor.is_available())

    @property
    def baseline_method_id(self) -> str:
        return self.BASELINE_METHOD_ID
