from abc import ABC
from re import Pattern
from typing import Any, Generic, Optional, Type, TypeVar, Union, overload
from typing import Literal

from scalecodec.constants import ScaleValue as ScaleValue

from scalecodec._scale_bytes import ScaleBytes

T_co = TypeVar("T_co", covariant=True)

class ScaleDecoder(ABC, Generic[T_co]):
    runtime_config: Optional["RuntimeConfigurationObject"]
    data: Optional[ScaleBytes]
    value: T_co
    decoded: bool
    value_object: T_co
    value_serialized: T_co
    data_start_offset: Optional[int]
    data_end_offset: Optional[int]
    type_string: Optional[str]
    type_mapping: Any
    sub_type: Optional[str]

    def process(self) -> T_co: ...
    def process_encode(self, value: Any) -> ScaleBytes: ...
    def decode(
        self, data: Optional[ScaleBytes] = None, check_remaining: bool = True
    ) -> T_co: ...
    def encode(self, value: Any) -> ScaleBytes: ...
    def process_type(
        self, type_string: Union[str, dict], **kwargs: Any
    ) -> "ScaleType[Any]": ...
    def serialize(self) -> T_co: ...

class ScaleType(ScaleDecoder[T_co], ABC, Generic[T_co]):
    scale_info_type: Any
    metadata: Any
    meta_info: dict
    elements: list[Any]

    def __init__(
        self,
        data: Optional[ScaleBytes] = None,
        sub_type: Optional[str] = None,
        metadata: Optional[Any] = None,
        runtime_config: Optional["RuntimeConfigurationObject"] = None,
        **kwargs: Any,
    ) -> None: ...
    def __getitem__(self, item: Any) -> Any: ...
    def __iter__(self) -> Any: ...
    def __len__(self) -> int: ...
    @classmethod
    def generate_type_decomposition(
        cls, _recursion_level: int = 0, max_recursion: int = ...
    ) -> Any: ...

class RuntimeConfigurationObject:
    config_id: Optional[str]
    ss58_format: Optional[int]
    implements_scale_info: bool
    type_registry: dict
    _initial_state: bool
    _dynamic_class_cache: dict
    bracket_match_re: Pattern[str]
    arrow_match_re: Pattern[str]
    active_spec_version_id: Optional[int]
    chain_id: Optional[str]

    def __init__(
        self,
        config_id: Optional[str] = None,
        ss58_format: Optional[int] = None,
        only_primitives_on_init: bool = False,
        implements_scale_info: bool = False,
    ) -> None: ...
    def get_decoder_class(
        self, type_string: Union[str, dict]
    ) -> Optional[Type[ScaleType[Any]]]: ...
    def _require_decoder_class(self, type_string: str) -> Type[ScaleType[Any]]: ...
    def get_decoder_class_for_scale_info_definition(
        self, type_string: str, scale_info_type: Any, prefix: str
    ) -> Optional[Type[ScaleType[Any]]]: ...
    def batch_decode(
        self, type_strings: list[str], data_list: list[bytes]
    ) -> list[Any]: ...
    def update_type_registry(self, type_registry: dict) -> None: ...
    def update_type_registry_types(self, types_dict: dict) -> None: ...
    def update_from_scale_info_types(
        self, scale_info_types: list, prefix: Optional[str] = None
    ) -> None: ...
    def clear_type_registry(self) -> None: ...
    def set_active_spec_version_id(self, spec_version_id: int) -> None: ...
    def get_runtime_id_from_upgrades(self, block_number: int) -> Optional[int]: ...
    def set_runtime_upgrades_head(self, block_number: int) -> None: ...
    def add_portable_registry(self, metadata: Any) -> None: ...
    def add_contract_metadata_dict_to_type_registry(
        self, metadata_dict: dict
    ) -> None: ...
    @overload
    def create_scale_object(
        self,
        type_string: Literal["MetadataVersioned"],
        data: Optional[ScaleBytes] = None,
        **kwargs: Any,
    ) -> "GenericMetadataVersioned": ...
    @overload
    def create_scale_object(
        self,
        type_string: Literal["MetadataV14", "MetadataV15"],
        data: Optional[ScaleBytes] = None,
        **kwargs: Any,
    ) -> "GenericMetadataAll": ...
    @overload
    def create_scale_object(
        self,
        type_string: Literal["Era"],
        data: Optional[ScaleBytes] = None,
        **kwargs: Any,
    ) -> "Era": ...
    @overload
    def create_scale_object(
        self,
        type_string: Literal["GenericCall", "Call"],
        data: Optional[ScaleBytes] = None,
        **kwargs: Any,
    ) -> "GenericCall": ...
    @overload
    def create_scale_object(
        self,
        type_string: Literal["Extrinsic", "GenericExtrinsic"],
        data: Optional[ScaleBytes] = None,
        **kwargs: Any,
    ) -> "GenericExtrinsic": ...
    @overload
    def create_scale_object(
        self,
        type_string: Union[str, dict],
        data: Optional[ScaleBytes] = None,
        **kwargs: Any,
    ) -> "ScaleType[Any]": ...
    @classmethod
    def convert_type_string(cls, name: str) -> str: ...
    @classmethod
    def all_subclasses(cls, class_: type) -> set: ...

class RuntimeConfiguration(RuntimeConfigurationObject): ...
class ScalePrimitive(ScaleType[Any], ABC): ...

# Imported here so callers only need `from scalecodec.base import ...`
from scalecodec.types import GenericMetadataVersioned as GenericMetadataVersioned
from scalecodec.types import GenericMetadataAll as GenericMetadataAll
