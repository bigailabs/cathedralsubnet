TYPE_DECOMP_MAX_RECURSIVE = 9

# All Python types that ScaleType.value can hold after decoding:
#   int   — U8–U256, I8–I256, Compact
#   float — F32, F64
#   bool  — Bool
#   str   — H160/H256/H512, Bytes/Str, HexBytes, RawBytes, BitVec, AccountId, Era (immortal)
#   None  — Null, Option (absent)
#   dict  — Struct, Enum (with type_mapping)
#   list  — Vec, Set
#   tuple — Tuple (multi-element), Era (mortal: (period, phase))
from typing import Union

ScaleValue = Union[int, float, bool, str, None, dict, list, tuple]
