"""
src/chain_codec.py

Encode one (symbol, snapshot_date) options chain as a single compressed
blob, and decode it back to a byte-identical DataFrame.

Why this exists
---------------
The chain store was a relational table: one row per contract per day, 23
typed columns, a three-column primary key and two secondary indexes. At
658MB and 2,042,262 rows it cost 322 bytes per contract-day, and 36% of
the file was index:

    options                       419.3 MB   64%
    sqlite_autoindex_options_1    104.2 MB   16%   (the primary key)
    idx_options_contract           79.6 MB   12%
    idx_options_symbol_date        54.0 MB    8%   (redundant -- the PK
                                                    already covers that
                                                    prefix)

None of which was used. Every read in the application is

    SELECT * FROM options WHERE symbol = ? AND snapshot_date = ?

straight into a DataFrame. Nothing filters by column, aggregates across
the table, or joins. It is a key-value store of (symbol, date) -> chain,
paying relational cost for an access pattern that never touches it -- and
storing `symbol`, `snapshot_date`, `option_type` and `contract_symbol`
up to three times per row across the table and its indexes.

So: one blob per slice, columnar inside. Measured over the whole real
database, 1,177 slices:

    current (rows + 3 indexes)   658.0 MB    12.2 MB/session
    this format (lzma-1)          48.6 MB     0.90 MB/session   13.5x

and it reads FASTER than the table did -- 2.8ms to decompress and rebuild
an SPX chain against 6.3ms for the SELECT, because there is no row
materialisation and no index descent.

Why not JSON, which the codebase already uses for precomputed_metrics
--------------------------------------------------------------------
Because it is lossy, and not in the way you would expect. pandas'
to_json/read_json round-trip does not merely truncate: at the default
double_precision=10 a stored 0.3 comes back as 0.30000000000000004, and 9
of the 17 numeric columns in a single SPX session come back altered.
Raising it to pandas' maximum of 15 still corrupts 7 of them. Market data
is the one thing in this project that cannot be recomputed, so an
encoding that quietly perturbs greeks is not a candidate however compact.

Measured alternatives that ARE exact, on one SPX session:

    columnar + lzma-1 (this)   30 KB
    columnar + bz2-9           39 KB
    columnar + zlib-9          44 KB
    pickle + zlib-9            54 KB
    parquet + brotli           66 KB

pickle is out regardless of size: these blobs are downloaded from a
public URL, and unpickling attacker-controlled bytes executes code.
parquet is exact and safe but 2.2x larger here and would put pyarrow --
a ~40MB wheel -- into requirements-demo.txt, which is deliberately
minimal so the demo machine cannot reach Schwab or Anthropic at all.
This format needs only numpy, already a dependency of both requirement
sets, plus stdlib lzma and json.

lzma preset 1 rather than 6: it is both smaller AND ~5x faster to encode
than zlib-9 on this data (9ms against 43ms per slice), because the
entropy coder matters more here than the dictionary size. Preset 6 buys a
few percent for several times the write cost, which is the wrong trade
for something that runs on every collection.

Layout
------
    MAGIC (4 bytes, outside the compression so a wrong or corrupt blob is
           identifiable without decompressing it)
    lzma(
        header length   uint32 little-endian
        header          JSON: row count, and per column its name, kind and
                        either its numpy dtype or its dictionary
        column sizes    uint64 little-endian, one per column
        column payloads raw little-endian numpy bytes, in column order
    )

Numeric columns are stored as their exact numpy bytes -- no decimal
representation is involved anywhere, which is what makes the round-trip
bit-exact. Text columns are dictionary-encoded to int32 codes, which is
what makes `symbol`, `snapshot_date` and `option_type` nearly free
instead of a repeated string per row. Null is code -1, so a column that
is entirely absent from a chain round-trips as None rather than as the
string "None".
"""

from __future__ import annotations

import json
import lzma
from typing import Any

import numpy as np
import pandas as pd

MAGIC = b"VDC1"
_PRESET = 1
_NULL_CODE = -1


class ChainCodecError(ValueError):
    """The bytes handed to decode() are not a chain blob this version reads."""


def _numeric_payload(array: np.ndarray) -> tuple[bytes, str]:
    """Raw little-endian bytes for a numeric column, plus its dtype string.

    Forced to little-endian so a blob written on one machine reads on
    another. Every platform this runs on today is little-endian, which is
    exactly why it would go unnoticed if it were not pinned.
    """
    little = array.dtype.newbyteorder("<")
    if array.dtype.byteorder == ">":
        array = array.astype(little)
    return np.ascontiguousarray(array).tobytes(), little.str


def _dictionary_payload(series: pd.Series) -> tuple[bytes, list[str]]:
    """int32 codes plus the distinct values, with null as _NULL_CODE."""
    values = series.to_numpy(dtype=object)
    missing = pd.isna(values)
    present = values[~missing]
    uniques, codes = np.unique(present.astype(str), return_inverse=True)
    full = np.full(len(values), _NULL_CODE, dtype="<i4")
    full[~missing] = codes
    return np.ascontiguousarray(full).tobytes(), uniques.tolist()


def encode(frame: pd.DataFrame) -> bytes:
    """One chain -> one blob. Column order and dtypes are preserved."""
    columns: list[dict[str, Any]] = []
    payloads: list[bytes] = []

    for name in frame.columns:
        series = frame[name]
        array = series.to_numpy()
        if array.dtype.kind in "fiub":
            payload, dtype = _numeric_payload(array)
            columns.append({"name": str(name), "kind": "numeric", "dtype": dtype})
        else:
            payload, uniques = _dictionary_payload(series)
            columns.append({"name": str(name), "kind": "dictionary", "values": uniques})
        payloads.append(payload)

    header = json.dumps({"rows": int(len(frame)), "columns": columns}).encode()
    body = b"".join((
        len(header).to_bytes(4, "little"),
        header,
        b"".join(len(p).to_bytes(8, "little") for p in payloads),
        b"".join(payloads),
    ))
    return MAGIC + lzma.compress(body, preset=_PRESET)


def decode(blob: bytes) -> pd.DataFrame:
    """One blob -> the chain that was encoded, byte-for-byte."""
    if not isinstance(blob, (bytes, bytearray, memoryview)):
        raise ChainCodecError(f"expected bytes, got {type(blob).__name__}")
    blob = bytes(blob)
    if not blob.startswith(MAGIC):
        raise ChainCodecError(
            f"not a chain blob: expected magic {MAGIC!r}, got {blob[:4]!r}"
        )
    try:
        body = lzma.decompress(blob[len(MAGIC):])
    except lzma.LZMAError as exc:
        raise ChainCodecError(f"chain blob is corrupt ({exc})") from exc

    header_length = int.from_bytes(body[:4], "little")
    cursor = 4 + header_length
    header = json.loads(body[4:cursor])
    rows, columns = header["rows"], header["columns"]

    sizes = [
        int.from_bytes(body[cursor + 8 * i: cursor + 8 * i + 8], "little")
        for i in range(len(columns))
    ]
    cursor += 8 * len(columns)

    rebuilt: dict[str, Any] = {}
    for column, size in zip(columns, sizes):
        chunk = body[cursor:cursor + size]
        cursor += size
        if column["kind"] == "numeric":
            rebuilt[column["name"]] = np.frombuffer(
                chunk, dtype=np.dtype(column["dtype"]), count=rows
            )
        else:
            codes = np.frombuffer(chunk, dtype="<i4", count=rows)
            # Appending None lets code -1 index it, so the null branch needs
            # no mask and no second pass.
            lookup = np.asarray(column["values"] + [None], dtype=object)
            rebuilt[column["name"]] = lookup[codes]

    return pd.DataFrame(rebuilt, columns=[c["name"] for c in columns])
