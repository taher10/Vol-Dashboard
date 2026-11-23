"""
Tests for src/chain_codec.py.

The contract is exactness, and it is not a formality. The chain archive is
the one thing in this project that cannot be recomputed -- Schwab serves
current chains only -- so an encoding that perturbs a greek in the fourth
decimal place is a silent, permanent corruption of the only copy.

The first test below is the reason this module exists at all rather than
reusing the JSON encoding already in src/schwab_database.py's
_encode_frame. pandas' to_json/read_json round-trip is lossy in a way that
does not look like truncation: at its default double_precision=10 a stored
0.3 returns as 0.30000000000000004, and 9 of the 17 numeric columns in a
real SPX session come back altered. Raising it to pandas' maximum of 15
still corrupts 7.

Verified beyond these unit tests: every one of the 1,177 (symbol, date)
slices in the real 658MB archive -- 2,042,262 rows -- round-trips under
pandas.testing.assert_frame_equal with check_exact=True and
check_dtype=True, and encodes to 48.7MB, 13.5x smaller.
"""

import lzma

import numpy as np
import pandas as pd
import pytest

from src.chain_codec import MAGIC, ChainCodecError, decode, encode


def _chain(rows: int = 40) -> pd.DataFrame:
    """Shaped like what SchwabDatabase hands over: a few low-cardinality
    text columns and a lot of float64 greeks."""
    rng = np.random.default_rng(0)
    return pd.DataFrame({
        "symbol": ["SPX"] * rows,
        "snapshot_date": ["2026-11-17"] * rows,
        "contract_symbol": [f"SPX   261219C0{6000 + i * 25:07d}" for i in range(rows)],
        "option_type": ["CALL" if i % 2 else "PUT" for i in range(rows)],
        "expiration": ["2026-12-19"] * rows,
        "dte": np.arange(rows, dtype="int64"),
        "strike_price": np.arange(6000.0, 6000.0 + rows * 25, 25.0),
        "bid": rng.random(rows) * 100,
        "implied_volatility": rng.random(rows),
        "delta": rng.standard_normal(rows),
        "volume": rng.integers(0, 50_000, rows),
        "in_the_money": rng.integers(0, 2, rows),
    })


class TestExactness:
    def test_the_floats_json_corrupts_survive(self):
        """The regression that chose this format over JSON. 0.3, 0.1 and
        0.7 are not representable in binary floating point, so any encoder
        that goes through a decimal representation and back can return a
        different float64 than it was given -- and pandas' JSON reader
        returns 0.30000000000000004 for a stored 0.3."""
        hostile = pd.DataFrame({
            "bid": [0.3, 0.1, 0.7, 1.0 / 3.0, 2.0 / 3.0],
            "implied_volatility": [0.07, 0.123456789012345, 1e-18, 1e18, np.pi],
        })

        back = decode(encode(hostile))

        pd.testing.assert_frame_equal(hostile, back, check_exact=True)
        # Spelled out, because assert_frame_equal passing is the whole claim.
        assert back["bid"].iloc[0] == 0.3
        assert back["bid"].iloc[0].hex() == (0.3).hex()

    def test_nan_and_inf_round_trip(self):
        """A thin chain leaves greeks missing, and a zero-vega contract can
        produce an infinity upstream. Neither may become a different number
        or a string."""
        edge = pd.DataFrame({"delta": [np.nan, np.inf, -np.inf, 0.0, -0.0]})

        back = decode(encode(edge))

        assert np.isnan(back["delta"].iloc[0])
        assert back["delta"].iloc[1] == np.inf
        assert back["delta"].iloc[2] == -np.inf
        # -0.0 == 0.0 is True, so compare the bits.
        assert np.signbit(back["delta"].iloc[4])

    def test_column_order_and_dtypes_are_preserved(self):
        frame = _chain()

        back = decode(encode(frame))

        assert list(back.columns) == list(frame.columns)
        pd.testing.assert_frame_equal(frame, back, check_exact=True, check_dtype=True)

    def test_decoded_frames_are_writable(self):
        """np.frombuffer returns a read-only view. Consumers rename columns
        and assign into these frames -- adapt_db_chain does both -- so a
        read-only result would fail at the call site, not here."""
        back = decode(encode(_chain()))

        back.loc[0, "bid"] = 1.23

        assert back["bid"].iloc[0] == 1.23


class TestNulls:
    def test_a_missing_text_value_stays_missing(self):
        """astype(str) on a text column turns a missing value into the
        STRING "None" or "nan", which then reads back as data rather than
        as absence -- a contract that silently has option_type "nan".

        Asserted as isna() rather than against None, because pandas
        normalises None to nan inside a str-dtype column on construction;
        the frame handed to encode() therefore already holds nan, and what
        has to survive is the missingness, not the particular sentinel."""
        frame = pd.DataFrame({"option_type": ["CALL", None, "PUT"]})

        back = decode(encode(frame))

        assert back["option_type"].isna().tolist() == [False, True, False]
        assert back["option_type"].iloc[0] == "CALL"
        assert back["option_type"].iloc[2] == "PUT"
        assert not (back["option_type"].astype(str) == "None").any()
        pd.testing.assert_frame_equal(frame, back, check_exact=True, check_dtype=True)

    def test_an_entirely_absent_column_round_trips(self):
        """append_options_snapshot fills columns the chain did not provide
        with None, so an all-null column is a real case, not a contrived
        one."""
        frame = pd.DataFrame({"contract_symbol": [None, None, None]})

        back = decode(encode(frame))

        assert back["contract_symbol"].isna().all()

    def test_an_empty_chain_round_trips(self):
        frame = _chain(0)

        back = decode(encode(frame))

        assert len(back) == 0
        assert list(back.columns) == list(frame.columns)


class TestItActuallyCompresses:
    def test_a_realistic_chain_shrinks_substantially(self):
        """Guards against a change that silently stops compressing -- the
        round-trip tests would all still pass on an encoder that stored
        everything verbatim."""
        frame = _chain(2000)
        raw = frame.memory_usage(deep=True).sum()

        assert len(encode(frame)) < raw / 5

    def test_repeated_text_is_nearly_free(self):
        """symbol, snapshot_date and option_type are one value repeated per
        contract. Dictionary encoding is what stops them costing a string
        per row, and it is where most of the 13.5x comes from."""
        rows = 5000
        constant = pd.DataFrame({"symbol": ["SPX"] * rows})

        assert len(encode(constant)) < 400


class TestRejectsWhatItCannotRead:
    def test_bytes_without_the_magic_are_refused(self):
        """A 404 page or a truncated download must not be mistaken for a
        chain and half-decoded into something plausible."""
        with pytest.raises(ChainCodecError, match="not a chain blob"):
            decode(b"<!DOCTYPE html><title>Not Found</title>")

    def test_a_corrupt_body_is_refused(self):
        with pytest.raises(ChainCodecError, match="corrupt"):
            decode(MAGIC + b"not lzma at all")

    def test_a_non_bytes_argument_says_so(self):
        with pytest.raises(ChainCodecError, match="expected bytes"):
            decode("a string, not bytes")

    def test_the_magic_is_outside_the_compression(self):
        """So a wrong blob is identifiable without spending a decompression
        on it, and so a future format change is detectable rather than
        producing nonsense."""
        blob = encode(_chain())

        assert blob.startswith(MAGIC)
        lzma.decompress(blob[len(MAGIC):])  # the remainder is the whole stream
